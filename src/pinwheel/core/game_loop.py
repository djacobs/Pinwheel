"""Game loop — autonomous round cycle for Pinwheel Fates.

Each round:
1. Simulate all games for the round
2. Store results
3. Close any open governance windows, enact rule changes
4. Generate mirrors (simulation, governance, private)
5. Publish events to EventBus for SSE clients
6. Advance to next round

The game loop is designed to be called by a scheduler (APScheduler, cron, etc.)
or stepped manually for testing.
"""

from __future__ import annotations

import logging
import time
import uuid

from pinwheel.ai.mirror import (
    generate_governance_mirror,
    generate_governance_mirror_mock,
    generate_private_mirror,
    generate_private_mirror_mock,
    generate_simulation_mirror,
    generate_simulation_mirror_mock,
)
from pinwheel.core.event_bus import EventBus
from pinwheel.core.governance import close_governance_window
from pinwheel.core.simulation import simulate_game
from pinwheel.db.repository import Repository
from pinwheel.models.governance import GovernanceWindow, Proposal, Vote, VoteTally
from pinwheel.models.mirror import Mirror
from pinwheel.models.rules import RuleSet
from pinwheel.models.team import Agent, PlayerAttributes, Team, Venue

logger = logging.getLogger(__name__)


def _row_to_team(team_row: object) -> Team:
    """Convert a TeamRow + AgentRows to domain Team model."""
    agents = []
    for a in team_row.agents:  # type: ignore[attr-defined]
        attrs = PlayerAttributes(**a.attributes)  # type: ignore[attr-defined]
        agents.append(
            Agent(
                id=a.id,  # type: ignore[attr-defined]
                name=a.name,  # type: ignore[attr-defined]
                team_id=a.team_id,  # type: ignore[attr-defined]
                archetype=a.archetype,  # type: ignore[attr-defined]
                attributes=attrs,
                moves=[],
            )
        )

    venue_data = team_row.venue  # type: ignore[attr-defined]
    venue = Venue(**(venue_data or {"name": "Default Arena"}))

    return Team(
        id=team_row.id,  # type: ignore[attr-defined]
        name=team_row.name,  # type: ignore[attr-defined]
        venue=venue,
        agents=agents,
    )


async def step_round(
    repo: Repository,
    season_id: str,
    round_number: int,
    event_bus: EventBus | None = None,
    api_key: str = "",
) -> RoundResult:
    """Execute one complete round of the game loop.

    Returns a RoundResult with game results, governance outcomes, and mirrors.
    """
    start = time.monotonic()
    logger.info("round_start season=%s round=%d", season_id, round_number)

    # 1. Get season + ruleset
    season = await repo.get_season(season_id)
    if not season:
        raise ValueError(f"Season {season_id} not found")
    ruleset = RuleSet(**(season.current_ruleset or {}))

    # 2. Get schedule for this round
    schedule = await repo.get_schedule_for_round(season_id, round_number)
    if not schedule:
        logger.warning("No games scheduled for round %d", round_number)
        return RoundResult(round_number=round_number, games=[], mirrors=[], tallies=[])

    # 3. Load teams
    teams_cache: dict[str, Team] = {}
    for entry in schedule:
        for tid in (entry.home_team_id, entry.away_team_id):
            if tid not in teams_cache:
                row = await repo.get_team(tid)
                if row:
                    teams_cache[tid] = _row_to_team(row)

    # 4. Simulate games
    game_summaries = []
    for entry in schedule:
        home = teams_cache.get(entry.home_team_id)
        away = teams_cache.get(entry.away_team_id)
        if not home or not away:
            logger.error(
                "Missing team for matchup %s vs %s",
                entry.home_team_id,
                entry.away_team_id,
            )
            continue

        seed = int(uuid.uuid4().int % (2**31))
        game_id = f"g-{round_number}-{entry.matchup_index}"
        result = simulate_game(home, away, ruleset, seed, game_id=game_id)

        # Store result
        game_row = await repo.store_game_result(
            season_id=season_id,
            round_number=round_number,
            matchup_index=entry.matchup_index,
            home_team_id=home.id,
            away_team_id=away.id,
            home_score=result.home_score,
            away_score=result.away_score,
            winner_team_id=result.winner_team_id,
            seed=seed,
            total_possessions=result.total_possessions,
            ruleset_snapshot=ruleset.model_dump(),
            quarter_scores=[qs.model_dump() for qs in result.quarter_scores],
            elam_target=result.elam_target_score,
            play_by_play=[p.model_dump() for p in result.possession_log[:50]],
        )

        # Store box scores
        for bs in result.box_scores:
            await repo.store_box_score(
                game_id=game_row.id,
                agent_id=bs.agent_id,
                team_id=bs.team_id,
                points=bs.points,
                field_goals_made=bs.field_goals_made,
                field_goals_attempted=bs.field_goals_attempted,
                three_pointers_made=bs.three_pointers_made,
                three_pointers_attempted=bs.three_pointers_attempted,
                free_throws_made=bs.free_throws_made,
                free_throws_attempted=bs.free_throws_attempted,
                assists=bs.assists,
                steals=bs.steals,
                turnovers=bs.turnovers,
            )

        summary = {
            "game_id": game_id,
            "home_team": home.name,
            "away_team": away.name,
            "home_score": result.home_score,
            "away_score": result.away_score,
            "winner_team_id": result.winner_team_id,
            "elam_activated": result.elam_activated,
            "total_possessions": result.total_possessions,
        }
        game_summaries.append(summary)

        if event_bus:
            await event_bus.publish("game.completed", summary)

    # 5. Governance — close window if one is open
    tallies: list[VoteTally] = []
    governance_data: dict = {"proposals": [], "votes": [], "rules_changed": []}

    gov_events = await repo.get_events_by_type(
        season_id=season_id,
        event_types=["window.opened"],
    )

    open_window = None
    for evt in reversed(gov_events):
        payload = evt.payload
        if payload.get("status") == "open":
            open_window = GovernanceWindow(**payload)
            break

    if open_window and open_window.round_number < round_number:
        # Gather proposals and votes for this window
        proposal_events = await repo.get_events_by_type(
            season_id=season_id,
            event_types=["proposal.submitted", "proposal.confirmed"],
        )
        vote_events = await repo.get_events_by_type(
            season_id=season_id,
            event_types=["vote.cast"],
        )

        proposals = []
        for pe in proposal_events:
            p_data = pe.payload
            if p_data.get("window_id") == open_window.id and p_data.get("status") in (
                "confirmed",
                "submitted",
            ):
                proposals.append(Proposal(**p_data))

        votes_by_proposal: dict[str, list[Vote]] = {}
        for ve in vote_events:
            v_data = ve.payload
            pid = v_data.get("proposal_id", "")
            if pid:
                votes_by_proposal.setdefault(pid, []).append(Vote(**v_data))

        new_ruleset, round_tallies = await close_governance_window(
            repo=repo,
            window=open_window,
            proposals=proposals,
            votes_by_proposal=votes_by_proposal,
            ruleset=ruleset,
            round_number=round_number,
        )
        tallies = round_tallies

        if new_ruleset != ruleset:
            await repo.update_season_ruleset(season_id, new_ruleset.model_dump())
            ruleset = new_ruleset

        governance_data["proposals"] = [p.model_dump(mode="json") for p in proposals]
        governance_data["votes"] = [
            v.model_dump(mode="json") for vs in votes_by_proposal.values() for v in vs
        ]
        governance_data["rules_changed"] = [
            t.model_dump(mode="json") for t in tallies if t.passed
        ]

        if event_bus:
            await event_bus.publish(
                "governance.window_closed",
                {
                    "round": round_number,
                    "proposals_count": len(proposals),
                    "rules_changed": len([t for t in tallies if t.passed]),
                },
            )

    # 6. Generate mirrors
    mirrors: list[Mirror] = []
    round_data = {"round_number": round_number, "games": game_summaries}

    if api_key:
        sim_mirror = await generate_simulation_mirror(
            round_data, season_id, round_number, api_key
        )
    else:
        sim_mirror = generate_simulation_mirror_mock(round_data, season_id, round_number)

    await repo.store_mirror(
        season_id=season_id,
        mirror_type="simulation",
        round_number=round_number,
        content=sim_mirror.content,
    )
    mirrors.append(sim_mirror)

    if event_bus:
        await event_bus.publish(
            "mirror.generated",
            {
                "mirror_type": "simulation",
                "round": round_number,
                "excerpt": sim_mirror.content[:200],
            },
        )

    # Governance mirror (even if no activity — silence is a pattern)
    if api_key:
        gov_mirror = await generate_governance_mirror(
            governance_data, season_id, round_number, api_key
        )
    else:
        gov_mirror = generate_governance_mirror_mock(governance_data, season_id, round_number)

    await repo.store_mirror(
        season_id=season_id,
        mirror_type="governance",
        round_number=round_number,
        content=gov_mirror.content,
    )
    mirrors.append(gov_mirror)

    if event_bus:
        await event_bus.publish(
            "mirror.generated",
            {
                "mirror_type": "governance",
                "round": round_number,
                "excerpt": gov_mirror.content[:200],
            },
        )

    # Private mirrors for active governors
    active_governor_events = await repo.get_events_by_type(
        season_id=season_id,
        event_types=["proposal.submitted", "vote.cast"],
    )
    governor_ids = {e.governor_id for e in active_governor_events if e.governor_id}

    for gov_id in governor_ids:
        gov_proposals = [
            e for e in active_governor_events
            if e.governor_id == gov_id and e.event_type == "proposal.submitted"
        ]
        gov_votes = [
            e for e in active_governor_events
            if e.governor_id == gov_id and e.event_type == "vote.cast"
        ]
        governor_data = {
            "governor_id": gov_id,
            "proposals_submitted": len(gov_proposals),
            "votes_cast": len(gov_votes),
            "tokens_spent": len(gov_proposals),  # 1 propose token per proposal
        }

        if api_key:
            priv_mirror = await generate_private_mirror(
                governor_data, gov_id, season_id, round_number, api_key
            )
        else:
            priv_mirror = generate_private_mirror_mock(
                governor_data, gov_id, season_id, round_number
            )

        await repo.store_mirror(
            season_id=season_id,
            mirror_type="private",
            round_number=round_number,
            content=priv_mirror.content,
            governor_id=gov_id,
        )
        mirrors.append(priv_mirror)

    # 7. Publish round complete
    elapsed = time.monotonic() - start
    logger.info(
        "round_complete season=%s round=%d games=%d mirrors=%d elapsed_ms=%.1f",
        season_id,
        round_number,
        len(game_summaries),
        len(mirrors),
        elapsed * 1000,
    )

    if event_bus:
        await event_bus.publish(
            "round.completed",
            {
                "round": round_number,
                "games": len(game_summaries),
                "mirrors": len(mirrors),
                "elapsed_ms": round(elapsed * 1000, 1),
            },
        )

    return RoundResult(
        round_number=round_number,
        games=game_summaries,
        mirrors=mirrors,
        tallies=tallies,
    )


class RoundResult:
    """Result of a single round step."""

    def __init__(
        self,
        round_number: int,
        games: list[dict],
        mirrors: list[Mirror],
        tallies: list[VoteTally],
    ) -> None:
        self.round_number = round_number
        self.games = games
        self.mirrors = mirrors
        self.tallies = tallies

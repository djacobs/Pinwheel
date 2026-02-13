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

from pinwheel.ai.commentary import (
    generate_game_commentary,
    generate_game_commentary_mock,
    generate_highlight_reel,
    generate_highlight_reel_mock,
)
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
from pinwheel.models.game import GameResult
from pinwheel.models.governance import GovernanceWindow, Proposal, Vote, VoteTally
from pinwheel.models.mirror import Mirror
from pinwheel.models.rules import RuleSet
from pinwheel.models.team import Hooper, PlayerAttributes, Team, Venue

logger = logging.getLogger(__name__)


def _row_to_team(team_row: object) -> Team:
    """Convert a TeamRow + HooperRows to domain Team model."""
    hoopers = []
    for a in team_row.hoopers:  # type: ignore[attr-defined]
        attrs = PlayerAttributes(**a.attributes)  # type: ignore[attr-defined]
        hoopers.append(
            Hooper(
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
        hoopers=hoopers,
    )


async def _run_evals(
    repo: Repository,
    season_id: str,
    round_number: int,
    mirrors: list[Mirror],
    game_summaries: list[dict],
    teams_cache: dict,
) -> None:
    """Run automated evals after mirror generation. Non-blocking."""
    from pinwheel.evals.behavioral import compute_mirror_impact_rate
    from pinwheel.evals.grounding import GroundingContext, check_grounding
    from pinwheel.evals.prescriptive import scan_prescriptive

    # Build grounding context
    team_data = [{"name": t.name} for t in teams_cache.values()]
    hooper_data = []
    for t in teams_cache.values():
        for h in t.hoopers:
            hooper_data.append({"name": h.name})
    season = await repo.get_season(season_id)
    ruleset_dict = season.current_ruleset if season else {}
    context = GroundingContext(
        team_names=[d["name"] for d in team_data],
        agent_names=[d["name"] for d in hooper_data],
        rule_params=list((ruleset_dict or {}).keys()),
    )

    for mirror in mirrors:
        # Prescriptive scan
        presc = scan_prescriptive(mirror.content, mirror.id, mirror.mirror_type)
        await repo.store_eval_result(
            season_id=season_id,
            round_number=round_number,
            eval_type="prescriptive",
            eval_subtype=mirror.mirror_type,
            score=float(presc.prescriptive_count),
            details_json={
                "mirror_id": mirror.id,
                "mirror_type": mirror.mirror_type,
                "count": presc.prescriptive_count,
                "flagged": presc.flagged,
            },
        )

        # Grounding check
        grounding = check_grounding(mirror.content, context, mirror.id, mirror.mirror_type)
        await repo.store_eval_result(
            season_id=season_id,
            round_number=round_number,
            eval_type="grounding",
            eval_subtype=mirror.mirror_type,
            score=float(grounding.entities_found),
            details_json={
                "mirror_id": mirror.id,
                "mirror_type": mirror.mirror_type,
                "entities_expected": grounding.entities_expected,
                "entities_found": grounding.entities_found,
                "grounded": grounding.grounded,
            },
        )

    # Behavioral shift (Mirror Impact Rate)
    impact_rate = await compute_mirror_impact_rate(repo, season_id, round_number)
    await repo.store_eval_result(
        season_id=season_id,
        round_number=round_number,
        eval_type="behavioral",
        eval_subtype="mirror_impact_rate",
        score=impact_rate,
        details_json={"mirror_impact_rate": impact_rate},
    )

    # Scenario flags
    try:
        from pinwheel.evals.flags import detect_all_flags

        flags = await detect_all_flags(repo, season_id, round_number, game_summaries)
        for flag in flags:
            await repo.store_eval_result(
                season_id=season_id,
                round_number=round_number,
                eval_type="flag",
                eval_subtype=flag.flag_type,
                score=1.0 if flag.severity == "critical" else 0.5,
                details_json=flag.model_dump(mode="json"),
            )
    except Exception:
        logger.exception("flag_detection_failed season=%s round=%d", season_id, round_number)

    logger.info("evals_complete season=%s round=%d", season_id, round_number)


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
    game_results: list[GameResult] = []
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
            play_by_play=[p.model_dump() for p in result.possession_log],
        )

        # Store box scores
        for bs in result.box_scores:
            await repo.store_box_score(
                game_id=game_row.id,
                hooper_id=bs.hooper_id,
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

        game_results.append(result)

        summary = {
            "game_id": game_id,
            "home_team": home.name,
            "away_team": away.name,
            "home_team_id": home.id,
            "away_team_id": away.id,
            "home_score": result.home_score,
            "away_score": result.away_score,
            "winner_team_id": result.winner_team_id,
            "elam_activated": result.elam_activated,
            "total_possessions": result.total_possessions,
        }

        # Generate commentary (non-blocking — never break the game loop)
        try:
            if api_key:
                commentary = await generate_game_commentary(
                    result, home, away, ruleset, api_key,
                )
            else:
                commentary = generate_game_commentary_mock(result, home, away)
            summary["commentary"] = commentary
        except Exception:
            logger.exception(
                "commentary_failed game=%s season=%s round=%d",
                game_id, season_id, round_number,
            )

        game_summaries.append(summary)

        if event_bus:
            await event_bus.publish("game.completed", summary)

    # Generate highlight reel for the round (non-blocking)
    highlight_reel = ""
    if game_summaries:
        try:
            if api_key:
                highlight_reel = await generate_highlight_reel(
                    game_summaries, round_number, api_key,
                )
            else:
                highlight_reel = generate_highlight_reel_mock(
                    game_summaries, round_number,
                )
        except Exception:
            logger.exception(
                "highlight_reel_failed season=%s round=%d",
                season_id, round_number,
            )

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

        # Enrich rules_changed with actual parameter change details
        rule_enacted_events = await repo.get_events_by_type(
            season_id=season_id,
            event_types=["rule.enacted"],
        )
        # Filter to this round's changes and merge into governance_data
        for rc_event in rule_enacted_events:
            if rc_event.payload.get("round_enacted") == round_number:
                # Find matching tally entry and enrich it
                for rc in governance_data["rules_changed"]:
                    if rc.get("proposal_id") == rc_event.payload.get("source_proposal_id"):
                        rc["parameter"] = rc_event.payload.get("parameter")
                        rc["old_value"] = rc_event.payload.get("old_value")
                        rc["new_value"] = rc_event.payload.get("new_value")

        # Add governance window timing info
        from pinwheel.config import Settings as _Settings

        _gov_settings = _Settings()
        governance_data["governance_window_minutes"] = _gov_settings.pinwheel_gov_window // 60

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

        mirror_row = await repo.store_mirror(
            season_id=season_id,
            mirror_type="private",
            round_number=round_number,
            content=priv_mirror.content,
            governor_id=gov_id,
        )
        mirrors.append(priv_mirror)

        if event_bus:
            await event_bus.publish(
                "mirror.generated",
                {
                    "mirror_type": "private",
                    "round": round_number,
                    "governor_id": gov_id,
                    "mirror_id": mirror_row.id,
                    "excerpt": priv_mirror.content[:200],
                },
            )

    # 7. Run evals (non-blocking — failures here never break the game loop)
    try:
        from pinwheel.config import Settings

        eval_settings = Settings()
        if eval_settings.pinwheel_evals_enabled:
            await _run_evals(repo, season_id, round_number, mirrors, game_summaries, teams_cache)
    except Exception:
        logger.exception("eval_step_failed season=%s round=%d", season_id, round_number)

    # 8. Publish round complete
    elapsed = time.monotonic() - start
    logger.info(
        "round_complete season=%s round=%d games=%d mirrors=%d elapsed_ms=%.1f",
        season_id,
        round_number,
        len(game_summaries),
        len(mirrors),
        elapsed * 1000,
    )

    round_completed_data: dict = {
        "round": round_number,
        "games": len(game_summaries),
        "mirrors": len(mirrors),
        "elapsed_ms": round(elapsed * 1000, 1),
    }
    if highlight_reel:
        round_completed_data["highlight_reel"] = highlight_reel

    if event_bus:
        await event_bus.publish("round.completed", round_completed_data)

    return RoundResult(
        round_number=round_number,
        games=game_summaries,
        mirrors=mirrors,
        tallies=tallies,
        game_results=game_results,
    )


class RoundResult:
    """Result of a single round step."""

    def __init__(
        self,
        round_number: int,
        games: list[dict],
        mirrors: list[Mirror],
        tallies: list[VoteTally],
        game_results: list[GameResult] | None = None,
    ) -> None:
        self.round_number = round_number
        self.games = games
        self.mirrors = mirrors
        self.tallies = tallies
        self.game_results = game_results or []

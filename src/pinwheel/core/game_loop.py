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
from pinwheel.core.governance import tally_governance
from pinwheel.core.scheduler import compute_standings
from pinwheel.core.simulation import simulate_game
from pinwheel.core.tokens import regenerate_tokens
from pinwheel.db.repository import Repository
from pinwheel.models.game import GameResult
from pinwheel.models.governance import Proposal, Vote, VoteTally
from pinwheel.models.mirror import Mirror
from pinwheel.models.rules import RuleSet
from pinwheel.models.team import Hooper, PlayerAttributes, Team, Venue

logger = logging.getLogger(__name__)


def _row_to_team(team_row: object) -> Team:
    """Convert a TeamRow + HooperRows to domain Team model."""
    hoopers = []
    for idx, a in enumerate(team_row.hoopers):  # type: ignore[attr-defined]
        attrs = PlayerAttributes(**a.attributes)  # type: ignore[attr-defined]
        hoopers.append(
            Hooper(
                id=a.id,  # type: ignore[attr-defined]
                name=a.name,  # type: ignore[attr-defined]
                team_id=a.team_id,  # type: ignore[attr-defined]
                archetype=a.archetype,  # type: ignore[attr-defined]
                attributes=attrs,
                moves=[],
                is_starter=idx < 3,
            )
        )

    venue_data = team_row.venue  # type: ignore[attr-defined]
    venue = Venue(**(venue_data or {"name": "Default Arena"}))

    return Team(
        id=team_row.id,  # type: ignore[attr-defined]
        name=team_row.name,  # type: ignore[attr-defined]
        color=getattr(team_row, "color", "#000000") or "#000000",
        color_secondary=getattr(team_row, "color_secondary", "#ffffff") or "#ffffff",
        venue=venue,
        hoopers=hoopers,
    )


async def _check_season_complete(repo: Repository, season_id: str) -> bool:
    """Check if all scheduled regular-season games have been played.

    Compares the set of round numbers in the regular-season schedule against
    the set of round numbers that have game results stored.  Returns True only
    when every scheduled round has at least one played game.
    """
    schedule = await repo.get_full_schedule(season_id, phase="regular")
    if not schedule:
        return False
    games = await repo.get_all_games(season_id)
    played_rounds = {g.round_number for g in games}
    scheduled_rounds = {s.round_number for s in schedule}
    return scheduled_rounds.issubset(played_rounds)


async def compute_standings_from_repo(repo: Repository, season_id: str) -> list[dict]:
    """Compute W-L standings from game results stored in the database.

    Reuses the existing ``compute_standings`` function from ``scheduler.py``,
    enriching each entry with the team name.  Results are sorted by wins
    descending, then point differential descending.
    """
    games = await repo.get_all_games(season_id)
    results: list[dict] = []
    for g in games:
        results.append({
            "home_team_id": g.home_team_id,
            "away_team_id": g.away_team_id,
            "home_score": g.home_score,
            "away_score": g.away_score,
            "winner_team_id": g.winner_team_id,
        })
    standings = compute_standings(results)
    # Enrich with team names
    for s in standings:
        team = await repo.get_team(s["team_id"])
        if team:
            s["team_name"] = team.name
    return standings


async def generate_playoff_bracket(
    repo: Repository,
    season_id: str,
    num_playoff_teams: int = 4,
) -> list[dict]:
    """Generate playoff matchups from final standings.

    Standard bracket: #1 vs #4, #2 vs #3 (semis), winners play finals.
    Stores playoff schedule entries in the database and returns the bracket
    as a list of matchup dicts.

    If fewer teams than ``num_playoff_teams`` exist, the bracket shrinks
    accordingly.  Returns an empty list if fewer than 2 teams qualify.
    """
    standings = await compute_standings_from_repo(repo, season_id)
    playoff_teams = standings[:num_playoff_teams]

    if len(playoff_teams) < 2:
        return []

    # Determine first available round number for playoffs
    full_schedule = await repo.get_full_schedule(season_id)
    max_round = max((s.round_number for s in full_schedule), default=0) if full_schedule else 0
    games = await repo.get_all_games(season_id)
    max_played = max((g.round_number for g in games), default=0) if games else 0
    playoff_round_start = max(max_round, max_played) + 1

    bracket: list[dict] = []

    if len(playoff_teams) >= 4:
        # Standard 4-team bracket: semis then finals
        semi_matchups = [
            (playoff_teams[0], playoff_teams[3]),  # #1 vs #4
            (playoff_teams[1], playoff_teams[2]),  # #2 vs #3
        ]
        for idx, (higher_seed, lower_seed) in enumerate(semi_matchups):
            matchup = {
                "playoff_round": "semifinal",
                "matchup_index": idx,
                "round_number": playoff_round_start,
                "home_team_id": higher_seed["team_id"],
                "away_team_id": lower_seed["team_id"],
                "home_seed": idx * 3 + 1 if idx == 0 else 2,
                "away_seed": 4 if idx == 0 else 3,
            }
            bracket.append(matchup)
            await repo.create_schedule_entry(
                season_id=season_id,
                round_number=playoff_round_start,
                matchup_index=idx,
                home_team_id=higher_seed["team_id"],
                away_team_id=lower_seed["team_id"],
                phase="playoff",
            )
        # Finals placeholder (round_number = semis + 1)
        bracket.append({
            "playoff_round": "finals",
            "matchup_index": 0,
            "round_number": playoff_round_start + 1,
            "home_team_id": "TBD",
            "away_team_id": "TBD",
            "home_seed": None,
            "away_seed": None,
        })
    elif len(playoff_teams) >= 2:
        # 2-team bracket: direct finals
        matchup = {
            "playoff_round": "finals",
            "matchup_index": 0,
            "round_number": playoff_round_start,
            "home_team_id": playoff_teams[0]["team_id"],
            "away_team_id": playoff_teams[1]["team_id"],
            "home_seed": 1,
            "away_seed": 2,
        }
        bracket.append(matchup)
        await repo.create_schedule_entry(
            season_id=season_id,
            round_number=playoff_round_start,
            matchup_index=0,
            home_team_id=playoff_teams[0]["team_id"],
            away_team_id=playoff_teams[1]["team_id"],
            phase="playoff",
        )

    return bracket


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
    governance_interval: int = 3,
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
    game_row_ids: list[str] = []
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

        game_row_ids.append(game_row.id)

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

    # 5. Governance — tally every Nth round (interval-based)
    tallies: list[VoteTally] = []
    governance_data: dict = {"proposals": [], "votes": [], "rules_changed": []}
    governance_summary: dict | None = None

    if governance_interval > 0 and round_number % governance_interval == 0:
        # Gather confirmed proposals that haven't been resolved yet
        confirmed_events = await repo.get_events_by_type(
            season_id=season_id,
            event_types=["proposal.confirmed"],
        )
        resolved_events = await repo.get_events_by_type(
            season_id=season_id,
            event_types=["proposal.passed", "proposal.failed"],
        )
        resolved_ids = {e.aggregate_id for e in resolved_events}

        # Deduplicate: use proposal_id from payload, falling back to aggregate_id
        pending_proposal_ids: list[str] = []
        seen_ids: set[str] = set()
        for ce in confirmed_events:
            pid = ce.payload.get("proposal_id", ce.aggregate_id)
            if pid not in resolved_ids and pid not in seen_ids:
                pending_proposal_ids.append(pid)
                seen_ids.add(pid)

        if pending_proposal_ids:
            # Reconstruct proposals from submitted events
            submitted_events = await repo.get_events_by_type(
                season_id=season_id,
                event_types=["proposal.submitted"],
            )
            proposals: list[Proposal] = []
            for se in submitted_events:
                p_data = se.payload
                pid = p_data.get("id", se.aggregate_id)
                if pid in seen_ids:
                    # Mark as confirmed since we found it via confirmed events
                    p_data_copy = dict(p_data)
                    if p_data_copy.get("status") == "submitted":
                        p_data_copy["status"] = "confirmed"
                    proposals.append(Proposal(**p_data_copy))

            # Gather votes for pending proposals
            vote_events = await repo.get_events_by_type(
                season_id=season_id,
                event_types=["vote.cast"],
            )
            votes_by_proposal: dict[str, list[Vote]] = {}
            for ve in vote_events:
                v_data = ve.payload
                pid = v_data.get("proposal_id", "")
                if pid in seen_ids:
                    votes_by_proposal.setdefault(pid, []).append(Vote(**v_data))

            new_ruleset, round_tallies = await tally_governance(
                repo=repo,
                season_id=season_id,
                proposals=proposals,
                votes_by_proposal=votes_by_proposal,
                current_ruleset=ruleset,
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
            for rc_event in rule_enacted_events:
                if rc_event.payload.get("round_enacted") == round_number:
                    for rc in governance_data["rules_changed"]:
                        if rc.get("proposal_id") == rc_event.payload.get("source_proposal_id"):
                            rc["parameter"] = rc_event.payload.get("parameter")
                            rc["old_value"] = rc_event.payload.get("old_value")
                            rc["new_value"] = rc_event.payload.get("new_value")

        # Build per-proposal tally data for Discord notifications
        proposal_tallies = []
        for tally in tallies:
            tally_data = tally.model_dump(mode="json")
            # Find the proposal text for this tally
            for p in proposals:
                if p.id == tally.proposal_id:
                    tally_data["proposal_text"] = p.raw_text
                    break
            proposal_tallies.append(tally_data)

        governance_summary = {
            "round": round_number,
            "proposals_count": len(pending_proposal_ids),
            "rules_changed": len([t for t in tallies if t.passed]),
            "tallies": proposal_tallies,
        }

        # Regenerate tokens for all enrolled governors
        regen_count = 0
        for team in teams_cache.values():
            governors = await repo.get_governors_for_team(team.id, season_id)
            for gov in governors:
                await regenerate_tokens(repo, gov.id, team.id, season_id)
                regen_count += 1
        if regen_count > 0:
            logger.info(
                "tokens_regenerated season=%s round=%d governors=%d",
                season_id, round_number, regen_count,
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

    # 8. Check if the regular season is complete
    season_complete = False
    final_standings: list[dict] | None = None
    playoff_bracket: list[dict] | None = None

    # Re-read season to get current status
    season = await repo.get_season(season_id)
    if season and season.status not in ("regular_season_complete", "playoffs", "completed"):
        try:
            season_complete = await _check_season_complete(repo, season_id)
        except Exception:
            logger.exception(
                "season_complete_check_failed season=%s round=%d",
                season_id, round_number,
            )

        if season_complete:
            await repo.update_season_status(season_id, "regular_season_complete")
            final_standings = await compute_standings_from_repo(repo, season_id)
            logger.info(
                "regular_season_complete season=%s round=%d teams=%d",
                season_id, round_number, len(final_standings),
            )

            # Generate playoff bracket
            try:
                playoff_bracket = await generate_playoff_bracket(repo, season_id)
                logger.info(
                    "playoff_bracket_generated season=%s matchups=%d",
                    season_id, len(playoff_bracket),
                )
            except Exception:
                logger.exception(
                    "playoff_bracket_failed season=%s", season_id,
                )

            # Publish season.regular_season_complete event
            if event_bus:
                await event_bus.publish(
                    "season.regular_season_complete",
                    {
                        "season_id": season_id,
                        "final_round": round_number,
                        "standings": final_standings,
                        "playoff_bracket": playoff_bracket,
                    },
                )

    # 9. Publish round complete
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
    if season_complete:
        round_completed_data["season_complete"] = True

    if event_bus:
        await event_bus.publish("round.completed", round_completed_data)

    return RoundResult(
        round_number=round_number,
        games=game_summaries,
        mirrors=mirrors,
        tallies=tallies,
        game_results=game_results,
        game_row_ids=game_row_ids,
        teams_cache=teams_cache,
        governance_summary=governance_summary,
        season_complete=season_complete,
        final_standings=final_standings,
        playoff_bracket=playoff_bracket,
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
        game_row_ids: list[str] | None = None,
        teams_cache: dict | None = None,
        governance_summary: dict | None = None,
        season_complete: bool = False,
        final_standings: list[dict] | None = None,
        playoff_bracket: list[dict] | None = None,
    ) -> None:
        self.round_number = round_number
        self.games = games
        self.mirrors = mirrors
        self.tallies = tallies
        self.game_results = game_results or []
        self.game_row_ids = game_row_ids or []
        self.teams_cache = teams_cache or {}
        self.governance_summary = governance_summary
        self.season_complete = season_complete
        self.final_standings = final_standings
        self.playoff_bracket = playoff_bracket

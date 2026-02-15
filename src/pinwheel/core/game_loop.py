"""Game loop — autonomous round cycle for Pinwheel Fates.

Each round:
1. Simulate all games for the round
2. Store results
3. Close any open governance windows, enact rule changes
4. Generate reports (simulation, governance, private)
5. Publish events to EventBus for SSE clients
6. Advance to next round

The game loop is designed to be called by a scheduler (APScheduler, cron, etc.)
or stepped manually for testing.
"""

from __future__ import annotations

import dataclasses
import logging
import time
import uuid

from pinwheel.ai.commentary import (
    generate_game_commentary,
    generate_game_commentary_mock,
    generate_highlight_reel,
    generate_highlight_reel_mock,
)
from pinwheel.ai.report import (
    generate_governance_report,
    generate_governance_report_mock,
    generate_private_report,
    generate_private_report_mock,
    generate_simulation_report,
    generate_simulation_report_mock,
)
from pinwheel.core.event_bus import EventBus
from pinwheel.core.governance import tally_governance
from pinwheel.core.scheduler import compute_standings
from pinwheel.core.simulation import simulate_game
from pinwheel.core.tokens import regenerate_tokens
from pinwheel.db.repository import Repository
from pinwheel.models.game import GameResult
from pinwheel.models.governance import Proposal, Vote, VoteTally
from pinwheel.models.report import Report
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


async def _determine_semifinal_winners(
    repo: Repository, season_id: str, semi_round_number: int
) -> list[str]:
    """Return winner team IDs from the semifinal round, ordered by matchup_index."""
    games = await repo.get_games_for_round(season_id, semi_round_number)
    games_sorted = sorted(games, key=lambda g: g.matchup_index)
    return [g.winner_team_id for g in games_sorted]


async def _create_finals_entry(
    repo: Repository,
    season_id: str,
    semi_round_number: int,
    winner_team_ids: list[str],
) -> dict | None:
    """Persist a finals schedule entry from semifinal winners. Returns the matchup dict."""
    if len(winner_team_ids) < 2:
        return None
    finals_round = semi_round_number + 1
    home_team_id = winner_team_ids[0]  # winner of higher-seed semi (#1v#4)
    away_team_id = winner_team_ids[1]  # winner of lower-seed semi (#2v#3)
    await repo.create_schedule_entry(
        season_id=season_id,
        round_number=finals_round,
        matchup_index=0,
        home_team_id=home_team_id,
        away_team_id=away_team_id,
        phase="playoff",
    )
    return {
        "playoff_round": "finals",
        "matchup_index": 0,
        "round_number": finals_round,
        "home_team_id": home_team_id,
        "away_team_id": away_team_id,
    }


async def _check_all_playoffs_complete(repo: Repository, season_id: str) -> bool:
    """Check if every scheduled playoff game has been played."""
    playoff_schedule = await repo.get_full_schedule(season_id, phase="playoff")
    if not playoff_schedule:
        return False
    games = await repo.get_all_games(season_id)
    played = {(g.round_number, g.matchup_index) for g in games}
    scheduled = {(s.round_number, s.matchup_index) for s in playoff_schedule}
    return scheduled.issubset(played)


async def compute_standings_from_repo(repo: Repository, season_id: str) -> list[dict]:
    """Compute W-L standings from game results stored in the database.

    Reuses the existing ``compute_standings`` function from ``scheduler.py``,
    enriching each entry with the team name.  Results are sorted by wins
    descending, then point differential descending.
    """
    games = await repo.get_all_games(season_id)
    results: list[dict] = []
    for g in games:
        results.append(
            {
                "home_team_id": g.home_team_id,
                "away_team_id": g.away_team_id,
                "home_score": g.home_score,
                "away_score": g.away_score,
                "winner_team_id": g.winner_team_id,
            }
        )
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
        bracket.append(
            {
                "playoff_round": "finals",
                "matchup_index": 0,
                "round_number": playoff_round_start + 1,
                "home_team_id": "TBD",
                "away_team_id": "TBD",
                "home_seed": None,
                "away_seed": None,
            }
        )
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
    reports: list[Report],
    game_summaries: list[dict],
    teams_cache: dict,
    api_key: str = "",
) -> None:
    """Run automated evals after report generation. Non-blocking."""
    from pinwheel.evals.behavioral import compute_report_impact_rate
    from pinwheel.evals.grounding import GroundingContext, check_grounding
    from pinwheel.evals.prescriptive import scan_prescriptive

    # Build grounding context
    team_data = [{"name": t.name} for t in teams_cache.values()]
    hooper_data = []
    for t in teams_cache.values():
        for h in t.hoopers:
            hooper_data.append({"name": h.name})
    season = await repo.get_season(season_id)
    ruleset_dict = (season.current_ruleset if season else None) or {}
    context = GroundingContext(
        team_names=[d["name"] for d in team_data],
        agent_names=[d["name"] for d in hooper_data],
        rule_params=list((ruleset_dict or {}).keys()),
    )

    for report in reports:
        # Prescriptive scan
        presc = scan_prescriptive(report.content, report.id, report.report_type)
        await repo.store_eval_result(
            season_id=season_id,
            round_number=round_number,
            eval_type="prescriptive",
            eval_subtype=report.report_type,
            score=float(presc.prescriptive_count),
            details_json={
                "report_id": report.id,
                "report_type": report.report_type,
                "count": presc.prescriptive_count,
                "flagged": presc.flagged,
            },
        )

        # Grounding check
        grounding = check_grounding(report.content, context, report.id, report.report_type)
        await repo.store_eval_result(
            season_id=season_id,
            round_number=round_number,
            eval_type="grounding",
            eval_subtype=report.report_type,
            score=float(grounding.entities_found),
            details_json={
                "report_id": report.id,
                "report_type": report.report_type,
                "entities_expected": grounding.entities_expected,
                "entities_found": grounding.entities_found,
                "grounded": grounding.grounded,
            },
        )

    # Behavioral shift (Report Impact Rate)
    impact_rate = await compute_report_impact_rate(repo, season_id, round_number)
    await repo.store_eval_result(
        season_id=season_id,
        round_number=round_number,
        eval_type="behavioral",
        eval_subtype="report_impact_rate",
        score=impact_rate,
        details_json={"report_impact_rate": impact_rate},
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

    # GQI (Governance Quality Index)
    try:
        from pinwheel.evals.gqi import compute_gqi, store_gqi

        gqi_result = await compute_gqi(repo, season_id, round_number)
        await store_gqi(repo, season_id, round_number, gqi_result)
        logger.info(
            "gqi_computed season=%s round=%d composite=%.3f",
            season_id,
            round_number,
            gqi_result.composite,
        )
    except Exception:
        logger.exception("gqi_computation_failed season=%s round=%d", season_id, round_number)

    # Rule Evaluator (Opus-powered admin analysis)
    try:
        from pinwheel.evals.rule_evaluator import evaluate_rules, store_rule_evaluation

        rule_eval = await evaluate_rules(repo, season_id, round_number, api_key=api_key)
        await store_rule_evaluation(repo, season_id, round_number, rule_eval)
        logger.info(
            "rule_evaluation_complete season=%s round=%d experiments=%d",
            season_id,
            round_number,
            len(rule_eval.suggested_experiments),
        )
    except Exception:
        logger.exception(
            "rule_evaluation_failed season=%s round=%d", season_id, round_number
        )

    logger.info("evals_complete season=%s round=%d", season_id, round_number)


async def tally_pending_governance(
    repo: Repository,
    season_id: str,
    round_number: int,
    ruleset: RuleSet,
    event_bus: EventBus | None = None,
) -> tuple[RuleSet, list[VoteTally], dict]:
    """Tally all pending proposals and enact passing rule changes.

    Standalone function — can run with or without game simulation.
    Returns (updated_ruleset, tallies, governance_data).
    """
    governance_data: dict = {"proposals": [], "votes": [], "rules_changed": []}

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

    # Exclude vetoed proposals from tally
    vetoed_events = await repo.get_events_by_type(
        season_id=season_id,
        event_types=["proposal.vetoed"],
    )
    vetoed_ids = {e.aggregate_id for e in vetoed_events}

    # Deduplicate: use proposal_id from payload, falling back to aggregate_id
    pending_proposal_ids: list[str] = []
    seen_ids: set[str] = set()
    for ce in confirmed_events:
        pid = ce.payload.get("proposal_id", ce.aggregate_id)
        if pid not in resolved_ids and pid not in seen_ids and pid not in vetoed_ids:
            pending_proposal_ids.append(pid)
            seen_ids.add(pid)

    tallies: list[VoteTally] = []
    proposals: list[Proposal] = []

    if pending_proposal_ids:
        # Reconstruct proposals from submitted events
        submitted_events = await repo.get_events_by_type(
            season_id=season_id,
            event_types=["proposal.submitted"],
        )
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

    return ruleset, tallies, governance_data


# ---------------------------------------------------------------------------
# Phase functions — extracted from step_round for multi-session orchestration
# ---------------------------------------------------------------------------


async def _phase_simulate_and_govern(
    repo: Repository,
    season_id: str,
    round_number: int,
    event_bus: EventBus | None = None,
    governance_interval: int = 1,
    suppress_spoiler_events: bool = False,
) -> _SimPhaseResult | None:
    """Phase 1: Simulate games, store results, tally governance.

    Fast DB reads/writes only — no AI calls. Returns None if no games are
    scheduled for this round.
    """
    # 1. Get season + ruleset
    season = await repo.get_season(season_id)
    if not season:
        raise ValueError(f"Season {season_id} not found")
    ruleset = RuleSet(**(season.current_ruleset or {}))

    # 2. Get schedule for this round
    schedule = await repo.get_schedule_for_round(season_id, round_number)
    if not schedule:
        logger.warning("No games scheduled for round %d", round_number)
        return None

    # 3. Load teams
    #
    # Team rosters are read from the DB at the start of each round. Any hooper
    # trade executed (via Discord) after this point will be committed in a
    # separate DB session and will NOT be visible to the current session thanks
    # to transaction-level snapshot isolation (SQLite WAL).
    # This means trades accepted mid-round automatically take effect at the
    # next round — exactly the intended behavior.
    teams_cache: dict[str, Team] = {}
    for entry in schedule:
        for tid in (entry.home_team_id, entry.away_team_id):
            if tid not in teams_cache:
                row = await repo.get_team(tid)
                if row:
                    teams_cache[tid] = _row_to_team(row)

    # 3b. Load team strategies
    from pinwheel.models.team import TeamStrategy

    strategies: dict[str, TeamStrategy] = {}
    for tid in teams_cache:
        strat_events = await repo.get_events_by_type(
            season_id=season_id,
            event_types=["strategy.interpreted"],
        )
        for evt in reversed(strat_events):
            if evt.payload.get("team_id") == tid:
                try:
                    strategies[tid] = TeamStrategy(**evt.payload.get("strategy", {}))
                except Exception:
                    logger.warning("invalid_strategy_payload team=%s", tid)
                break

    # 4. Simulate games
    playoff_context: str | None = None
    if schedule and schedule[0].phase == "playoff":
        playoff_context = "semifinal" if len(schedule) >= 2 else "finals"

    game_summaries: list[dict] = []
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
        result = simulate_game(
            home,
            away,
            ruleset,
            seed,
            game_id=game_id,
            home_strategy=strategies.get(home.id),
            away_strategy=strategies.get(away.id),
        )

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

        game_summaries.append(summary)

        # Publish game.completed without commentary (commentary added in AI phase)
        if event_bus and not suppress_spoiler_events:
            await event_bus.publish("game.completed", summary)

    # 5. Governance — tally every Nth round (interval-based)
    tallies: list[VoteTally] = []
    governance_data: dict = {"proposals": [], "votes": [], "rules_changed": []}
    governance_summary: dict | None = None

    if governance_interval > 0 and round_number % governance_interval == 0:
        ruleset, tallies, governance_data = await tally_pending_governance(
            repo=repo,
            season_id=season_id,
            round_number=round_number,
            ruleset=ruleset,
            event_bus=event_bus,
        )

        # Build per-proposal tally data for Discord notifications
        proposals_data = governance_data.get("proposals", [])
        proposal_tallies = []
        for tally in tallies:
            tally_data = tally.model_dump(mode="json")
            for p_data in proposals_data:
                if p_data.get("id") == tally.proposal_id:
                    tally_data["proposal_text"] = p_data.get("raw_text", "")
                    break
            proposal_tallies.append(tally_data)

        governance_summary = {
            "round": round_number,
            "proposals_count": len(tallies),
            "rules_changed": len([t for t in tallies if t.passed]),
            "tallies": proposal_tallies,
        }

        # Regenerate tokens for all enrolled governors
        regen_count = 0
        for team in teams_cache.values():
            governors = await repo.get_governors_for_team(team.id, season_id)
            for gov in governors:
                await regenerate_tokens(repo, gov.id, team.id, season_id, boost_amount=0)
                regen_count += 1
        if regen_count > 0:
            logger.info(
                "tokens_regenerated season=%s round=%d governors=%d",
                season_id,
                round_number,
                regen_count,
            )

    # Query governor activity for private reports
    active_governor_events = await repo.get_events_by_type(
        season_id=season_id,
        event_types=["proposal.submitted", "vote.cast"],
    )
    active_governor_ids = {e.governor_id for e in active_governor_events if e.governor_id}

    governor_activity: dict[str, dict] = {}
    for gov_id in active_governor_ids:
        gov_proposals = [
            e
            for e in active_governor_events
            if e.governor_id == gov_id and e.event_type == "proposal.submitted"
        ]
        gov_votes = [
            e
            for e in active_governor_events
            if e.governor_id == gov_id and e.event_type == "vote.cast"
        ]
        governor_activity[gov_id] = {
            "governor_id": gov_id,
            "proposals_submitted": len(gov_proposals),
            "votes_cast": len(gov_votes),
            "tokens_spent": len(gov_proposals),
        }

    return _SimPhaseResult(
        season_id=season_id,
        round_number=round_number,
        ruleset=ruleset,
        teams_cache=teams_cache,
        game_results=game_results,
        game_row_ids=game_row_ids,
        game_summaries=game_summaries,
        playoff_context=playoff_context,
        tallies=tallies,
        governance_data=governance_data,
        governance_summary=governance_summary,
        governor_activity=governor_activity,
        active_governor_ids=active_governor_ids,
    )


async def _phase_ai(
    sim: _SimPhaseResult,
    api_key: str = "",
) -> _AIPhaseResult:
    """Phase 2: Generate all AI content. No DB access needed.

    Makes all AI calls: commentary per game, highlight reel, simulation report,
    governance report, and private reports per active governor.
    """
    commentaries: dict[str, str] = {}
    round_data = {"round_number": sim.round_number, "games": sim.game_summaries}

    # Commentary per game
    for i, result in enumerate(sim.game_results):
        if i >= len(sim.game_summaries):
            break
        game_id = sim.game_summaries[i].get("game_id", "")

        # Find teams from cache
        home = sim.teams_cache.get(result.home_team_id)
        away = sim.teams_cache.get(result.away_team_id)
        if not home or not away:
            continue

        try:
            if api_key:
                commentary = await generate_game_commentary(
                    result,
                    home,
                    away,
                    sim.ruleset,
                    api_key,
                    playoff_context=sim.playoff_context,
                )
            else:
                commentary = generate_game_commentary_mock(
                    result, home, away, playoff_context=sim.playoff_context
                )
            commentaries[game_id] = commentary
        except Exception:
            logger.exception(
                "commentary_failed game=%s season=%s round=%d",
                game_id,
                sim.season_id,
                sim.round_number,
            )

    # Highlight reel
    highlight_reel = ""
    if sim.game_summaries:
        try:
            if api_key:
                highlight_reel = await generate_highlight_reel(
                    sim.game_summaries,
                    sim.round_number,
                    api_key,
                    playoff_context=sim.playoff_context,
                )
            else:
                highlight_reel = generate_highlight_reel_mock(
                    sim.game_summaries,
                    sim.round_number,
                    playoff_context=sim.playoff_context,
                )
        except Exception:
            logger.exception(
                "highlight_reel_failed season=%s round=%d",
                sim.season_id,
                sim.round_number,
            )

    # Simulation report
    if api_key:
        sim_report = await generate_simulation_report(
            round_data, sim.season_id, sim.round_number, api_key
        )
    else:
        sim_report = generate_simulation_report_mock(
            round_data, sim.season_id, sim.round_number
        )

    # Governance report
    if api_key:
        gov_report = await generate_governance_report(
            sim.governance_data, sim.season_id, sim.round_number, api_key
        )
    else:
        gov_report = generate_governance_report_mock(
            sim.governance_data, sim.season_id, sim.round_number
        )

    # Private reports for active governors
    private_reports: list[tuple[str, Report]] = []
    for gov_id in sim.active_governor_ids:
        governor_data = sim.governor_activity.get(gov_id, {})
        if api_key:
            priv_report = await generate_private_report(
                governor_data, gov_id, sim.season_id, sim.round_number, api_key
            )
        else:
            priv_report = generate_private_report_mock(
                governor_data, gov_id, sim.season_id, sim.round_number
            )
        private_reports.append((gov_id, priv_report))

    return _AIPhaseResult(
        commentaries=commentaries,
        highlight_reel=highlight_reel,
        sim_report=sim_report,
        gov_report=gov_report,
        private_reports=private_reports,
    )


async def _phase_persist_and_finalize(
    repo: Repository,
    sim: _SimPhaseResult,
    ai: _AIPhaseResult,
    event_bus: EventBus | None = None,
    suppress_spoiler_events: bool = False,
    start_time: float | None = None,
    api_key: str = "",
) -> RoundResult:
    """Phase 3: Store AI outputs, run evals, handle season progression.

    Fast DB writes only — all slow AI calls already completed in Phase 2.
    """
    reports: list[Report] = []
    deferred_report_events: list[dict] = []

    # Attach commentary to game_summaries
    for summary in sim.game_summaries:
        game_id = summary.get("game_id", "")
        if game_id in ai.commentaries:
            summary["commentary"] = ai.commentaries[game_id]

    # Store simulation report
    await repo.store_report(
        season_id=sim.season_id,
        report_type="simulation",
        round_number=sim.round_number,
        content=ai.sim_report.content,
    )
    reports.append(ai.sim_report)
    deferred_report_events.append(
        {
            "report_type": "simulation",
            "round": sim.round_number,
            "excerpt": ai.sim_report.content[:200],
        },
    )

    # Store governance report
    await repo.store_report(
        season_id=sim.season_id,
        report_type="governance",
        round_number=sim.round_number,
        content=ai.gov_report.content,
    )
    reports.append(ai.gov_report)
    deferred_report_events.append(
        {
            "report_type": "governance",
            "round": sim.round_number,
            "excerpt": ai.gov_report.content[:200],
        },
    )

    # Store private reports
    for gov_id, priv_report in ai.private_reports:
        report_row = await repo.store_report(
            season_id=sim.season_id,
            report_type="private",
            round_number=sim.round_number,
            content=priv_report.content,
            governor_id=gov_id,
        )
        reports.append(priv_report)
        deferred_report_events.append(
            {
                "report_type": "private",
                "round": sim.round_number,
                "governor_id": gov_id,
                "report_id": report_row.id,
                "excerpt": priv_report.content[:200],
            },
        )

    # Run evals
    try:
        from pinwheel.config import Settings

        eval_settings = Settings()
        if eval_settings.pinwheel_evals_enabled:
            await _run_evals(
                repo,
                sim.season_id,
                sim.round_number,
                reports,
                sim.game_summaries,
                sim.teams_cache,
                api_key=api_key,
            )
    except Exception:
        logger.exception(
            "eval_step_failed season=%s round=%d", sim.season_id, sim.round_number
        )

    # Season progression checks
    season_complete = False
    final_standings: list[dict] | None = None
    playoff_bracket: list[dict] | None = None
    playoffs_complete = False
    finals_matchup: dict | None = None

    season = await repo.get_season(sim.season_id)
    if season and season.status not in (
        "regular_season_complete",
        "playoffs",
        "completed",
        "complete",
        "championship",
        "offseason",
        "tiebreaker_check",
        "tiebreakers",
    ):
        try:
            season_complete = await _check_season_complete(repo, sim.season_id)
        except Exception:
            logger.exception(
                "season_complete_check_failed season=%s round=%d",
                sim.season_id,
                sim.round_number,
            )

        if season_complete:
            final_standings = await compute_standings_from_repo(repo, sim.season_id)
            logger.info(
                "regular_season_complete season=%s round=%d teams=%d",
                sim.season_id,
                sim.round_number,
                len(final_standings),
            )

            try:
                from pinwheel.core.season import check_and_handle_tiebreakers

                result_phase = await check_and_handle_tiebreakers(
                    repo,
                    sim.season_id,
                    event_bus=event_bus,
                )
            except Exception:
                logger.exception(
                    "tiebreaker_check_failed season=%s",
                    sim.season_id,
                )
                await repo.update_season_status(sim.season_id, "regular_season_complete")
                result_phase = None

            if result_phase == "playoffs":
                try:
                    playoff_bracket = await generate_playoff_bracket(repo, sim.season_id)
                    logger.info(
                        "playoff_bracket_generated season=%s matchups=%d",
                        sim.season_id,
                        len(playoff_bracket),
                    )
                except Exception:
                    logger.exception(
                        "playoff_bracket_failed season=%s",
                        sim.season_id,
                    )

            if event_bus:
                await event_bus.publish(
                    "season.regular_season_complete",
                    {
                        "season_id": sim.season_id,
                        "final_round": sim.round_number,
                        "standings": final_standings,
                        "playoff_bracket": playoff_bracket,
                        "tiebreakers_needed": result_phase == "tiebreakers",
                    },
                )

    elif season and season.status == "tiebreakers":
        tb_schedule = await repo.get_full_schedule(sim.season_id, phase="tiebreaker")
        if tb_schedule:
            games = await repo.get_all_games(sim.season_id)
            played = {(g.round_number, g.matchup_index) for g in games}
            tb_scheduled = {(s.round_number, s.matchup_index) for s in tb_schedule}
            if tb_scheduled.issubset(played):
                from pinwheel.core.season import SeasonPhase, transition_season

                await transition_season(
                    repo,
                    sim.season_id,
                    SeasonPhase.PLAYOFFS,
                    event_bus=event_bus,
                )
                try:
                    playoff_bracket = await generate_playoff_bracket(repo, sim.season_id)
                    logger.info(
                        "tiebreaker_resolved season=%s -> playoffs matchups=%d",
                        sim.season_id,
                        len(playoff_bracket),
                    )
                except Exception:
                    logger.exception(
                        "playoff_bracket_failed_after_tiebreaker season=%s",
                        sim.season_id,
                    )

    elif season and season.status in ("regular_season_complete", "playoffs"):
        playoff_schedule = await repo.get_full_schedule(sim.season_id, phase="playoff")
        created_finals = False

        if playoff_schedule:
            playoff_rounds = sorted({s.round_number for s in playoff_schedule})

            if len(playoff_rounds) == 1:
                semi_round_num = playoff_rounds[0]
                semi_entries = [
                    s for s in playoff_schedule if s.round_number == semi_round_num
                ]
                if len(semi_entries) == 2:
                    semi_games = await repo.get_games_for_round(
                        sim.season_id, semi_round_num
                    )
                    if len(semi_games) == 2:
                        winners = await _determine_semifinal_winners(
                            repo, sim.season_id, semi_round_num
                        )
                        finals_matchup = await _create_finals_entry(
                            repo, sim.season_id, semi_round_num, winners
                        )
                        created_finals = True
                        await repo.update_season_status(sim.season_id, "playoffs")
                        logger.info(
                            "semifinals_complete season=%s finals=%s",
                            sim.season_id,
                            finals_matchup,
                        )
                        if event_bus:
                            await event_bus.publish(
                                "season.semifinals_complete",
                                {
                                    "season_id": sim.season_id,
                                    "semifinal_winners": winners,
                                    "finals_matchup": finals_matchup,
                                },
                            )

        if not created_finals:
            try:
                playoffs_complete = await _check_all_playoffs_complete(
                    repo, sim.season_id
                )
            except Exception:
                logger.exception(
                    "playoffs_complete_check_failed season=%s round=%d",
                    sim.season_id,
                    sim.round_number,
                )

            if playoffs_complete:
                if not playoff_schedule:
                    playoff_schedule = await repo.get_full_schedule(
                        sim.season_id, phase="playoff"
                    )
                finals_round_num = max(s.round_number for s in playoff_schedule)
                finals_games = await repo.get_games_for_round(
                    sim.season_id, finals_round_num
                )
                champion_team_id = (
                    finals_games[0].winner_team_id if finals_games else None
                )
                logger.info(
                    "season_playoffs_complete season=%s champion=%s",
                    sim.season_id,
                    champion_team_id,
                )

                if champion_team_id:
                    from pinwheel.core.season import enter_championship

                    try:
                        await enter_championship(
                            repo,
                            sim.season_id,
                            champion_team_id,
                            event_bus=event_bus,
                        )
                    except Exception:
                        logger.exception(
                            "enter_championship_failed season=%s",
                            sim.season_id,
                        )
                        await repo.update_season_status(sim.season_id, "completed")
                else:
                    await repo.update_season_status(sim.season_id, "completed")

                if event_bus:
                    await event_bus.publish(
                        "season.playoffs_complete",
                        {
                            "season_id": sim.season_id,
                            "champion_team_id": champion_team_id,
                        },
                    )

    # Publish round complete
    elapsed = (time.monotonic() - start_time) if start_time is not None else 0.0
    logger.info(
        "round_complete season=%s round=%d games=%d reports=%d elapsed_ms=%.1f",
        sim.season_id,
        sim.round_number,
        len(sim.game_summaries),
        len(reports),
        elapsed * 1000,
    )

    round_completed_data: dict = {
        "round": sim.round_number,
        "games": len(sim.game_summaries),
        "reports": len(reports),
        "elapsed_ms": round(elapsed * 1000, 1),
    }
    if ai.highlight_reel:
        round_completed_data["highlight_reel"] = ai.highlight_reel
    if season_complete:
        round_completed_data["season_complete"] = True
    if playoffs_complete:
        round_completed_data["playoffs_complete"] = True
    if finals_matchup:
        round_completed_data["finals_matchup"] = finals_matchup

    if event_bus and not suppress_spoiler_events:
        await event_bus.publish("round.completed", round_completed_data)

    return RoundResult(
        round_number=sim.round_number,
        games=sim.game_summaries,
        reports=reports,
        tallies=sim.tallies,
        game_results=sim.game_results,
        game_row_ids=sim.game_row_ids,
        teams_cache=sim.teams_cache,
        governance_summary=sim.governance_summary,
        season_complete=season_complete,
        final_standings=final_standings,
        playoff_bracket=playoff_bracket,
        playoffs_complete=playoffs_complete,
        finals_matchup=finals_matchup,
        report_events=deferred_report_events,
    )


async def step_round(
    repo: Repository,
    season_id: str,
    round_number: int,
    event_bus: EventBus | None = None,
    api_key: str = "",
    governance_interval: int = 1,
    suppress_spoiler_events: bool = False,
) -> RoundResult:
    """Execute one complete round of the game loop.

    Returns a RoundResult with game results, governance outcomes, and reports.
    Delegates to the three phase functions but keeps everything in one session
    (backward-compatible single-session behavior).
    """
    start = time.monotonic()
    logger.info("round_start season=%s round=%d", season_id, round_number)

    sim = await _phase_simulate_and_govern(
        repo,
        season_id,
        round_number,
        event_bus=event_bus,
        governance_interval=governance_interval,
        suppress_spoiler_events=suppress_spoiler_events,
    )
    if sim is None:
        return RoundResult(round_number=round_number, games=[], reports=[], tallies=[])

    ai = await _phase_ai(sim, api_key)

    return await _phase_persist_and_finalize(
        repo,
        sim,
        ai,
        event_bus=event_bus,
        suppress_spoiler_events=suppress_spoiler_events,
        start_time=start,
        api_key=api_key,
    )


async def step_round_multisession(
    engine: object,
    season_id: str,
    round_number: int,
    event_bus: EventBus | None = None,
    api_key: str = "",
    governance_interval: int = 1,
    suppress_spoiler_events: bool = False,
) -> RoundResult:
    """Execute one round with separate DB sessions per phase.

    Releases the SQLite write lock between phases so Discord commands
    (/join, /propose, /vote) can write freely during slow AI calls.

    Lock timeline:
        Session 1 (~2-3s): simulate games, store results, tally governance
           [LOCK RELEASED]
        AI calls (~30-90s): commentary, highlights, reports (NO session open)
           [LOCK RELEASED]
        Session 2 (~1-2s): store reports, run evals, season progression
           [LOCK RELEASED]

    The ``engine`` parameter is typed as ``object`` to avoid importing
    AsyncEngine at module level; callers pass an ``AsyncEngine`` instance.
    """
    from pinwheel.db.engine import get_session as _get_session
    from pinwheel.db.repository import Repository as _Repository

    start = time.monotonic()
    logger.info("round_start_multisession season=%s round=%d", season_id, round_number)

    # Session 1: simulate + govern (fast)
    async with _get_session(engine) as session:
        repo = _Repository(session)
        sim = await _phase_simulate_and_govern(
            repo,
            season_id,
            round_number,
            event_bus=event_bus,
            governance_interval=governance_interval,
            suppress_spoiler_events=suppress_spoiler_events,
        )
    # Session closed — lock released

    if sim is None:
        return RoundResult(round_number=round_number, games=[], reports=[], tallies=[])

    # NO SESSION: AI calls (slow, 30-90s)
    ai = await _phase_ai(sim, api_key)

    # Session 2: persist + finalize (fast)
    async with _get_session(engine) as session:
        repo = _Repository(session)
        return await _phase_persist_and_finalize(
            repo,
            sim,
            ai,
            event_bus=event_bus,
            suppress_spoiler_events=suppress_spoiler_events,
            start_time=start,
            api_key=api_key,
        )
    # Session closed


class RoundResult:
    """Result of a single round step."""

    def __init__(
        self,
        round_number: int,
        games: list[dict],
        reports: list[Report],
        tallies: list[VoteTally],
        game_results: list[GameResult] | None = None,
        game_row_ids: list[str] | None = None,
        teams_cache: dict | None = None,
        governance_summary: dict | None = None,
        season_complete: bool = False,
        final_standings: list[dict] | None = None,
        playoff_bracket: list[dict] | None = None,
        playoffs_complete: bool = False,
        finals_matchup: dict | None = None,
        report_events: list[dict] | None = None,
    ) -> None:
        self.round_number = round_number
        self.games = games
        self.reports = reports
        self.tallies = tallies
        self.game_results = game_results or []
        self.game_row_ids = game_row_ids or []
        self.teams_cache = teams_cache or {}
        self.governance_summary = governance_summary
        self.season_complete = season_complete
        self.final_standings = final_standings
        self.playoff_bracket = playoff_bracket
        self.playoffs_complete = playoffs_complete
        self.finals_matchup = finals_matchup
        self.report_events = report_events or []


# ---------------------------------------------------------------------------
# Phase dataclasses — intermediate results passed between multi-session phases
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class _SimPhaseResult:
    """Data from the simulate-and-store phase (Session 1)."""

    season_id: str
    round_number: int
    ruleset: RuleSet
    teams_cache: dict[str, Team]
    game_results: list[GameResult]
    game_row_ids: list[str]
    game_summaries: list[dict]  # without commentary yet
    playoff_context: str | None
    tallies: list[VoteTally]
    governance_data: dict
    governance_summary: dict | None
    governor_activity: dict[str, dict]  # gov_id -> {proposals, votes, etc.}
    active_governor_ids: set[str]


@dataclasses.dataclass
class _AIPhaseResult:
    """AI-generated content (no DB access needed)."""

    commentaries: dict[str, str]  # game_id -> commentary text
    highlight_reel: str
    sim_report: Report
    gov_report: Report
    private_reports: list[tuple[str, Report]]  # (governor_id, report)

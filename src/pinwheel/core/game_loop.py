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

import asyncio
import dataclasses
import logging
import time
import uuid

from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from pinwheel.ai.commentary import (
    generate_game_commentary,
    generate_game_commentary_mock,
    generate_highlight_reel,
    generate_highlight_reel_mock,
)
from pinwheel.ai.insights import (
    compute_behavioral_profile,
    compute_governor_leverage,
    compute_impact_validation,
    generate_behavioral_report,
    generate_behavioral_report_mock,
    generate_impact_validation,
    generate_impact_validation_mock,
    generate_leverage_report,
    generate_leverage_report_mock,
)
from pinwheel.ai.report import (
    compute_private_report_context,
    generate_governance_report,
    generate_governance_report_mock,
    generate_private_report,
    generate_private_report_mock,
    generate_series_report,
    generate_series_report_mock,
    generate_simulation_report,
    generate_simulation_report_mock,
)
from pinwheel.core.drama import DramaAnnotation, annotate_drama, get_drama_summary
from pinwheel.core.effects import EffectRegistry, load_effect_registry, persist_expired_effects
from pinwheel.core.event_bus import EventBus
from pinwheel.core.governance import (
    get_proposal_effects_v2,
    tally_governance,
    tally_governance_with_effects,
)
from pinwheel.core.hooks import HookContext, fire_effects
from pinwheel.core.meta import MetaStore
from pinwheel.core.milestones import check_milestones
from pinwheel.core.narrative import NarrativeContext, compute_narrative_context
from pinwheel.core.scheduler import compute_standings
from pinwheel.core.simulation import simulate_game
from pinwheel.core.tokens import regenerate_tokens
from pinwheel.db.models import TeamRow
from pinwheel.db.repository import Repository
from pinwheel.models.game import GameResult
from pinwheel.models.governance import EffectSpec, Proposal, Vote, VoteTally
from pinwheel.models.report import Report
from pinwheel.models.rules import RuleSet
from pinwheel.models.team import Hooper, Move, PlayerAttributes, Team, Venue

logger = logging.getLogger(__name__)


def _row_to_team(team_row: TeamRow) -> Team:
    """Convert a TeamRow + HooperRows to domain Team model."""
    hoopers = []
    for idx, a in enumerate(team_row.hoopers):
        attrs = PlayerAttributes(**a.attributes)
        raw_moves = a.moves if hasattr(a, "moves") and a.moves else []
        moves = [Move(**m) if isinstance(m, dict) else m for m in raw_moves]
        hoopers.append(
            Hooper(
                id=a.id,
                name=a.name,
                team_id=a.team_id,
                archetype=a.archetype,
                attributes=attrs,
                moves=moves,
                is_starter=idx < 3,
            )
        )

    venue_data = team_row.venue
    venue = Venue(**(venue_data or {"name": "Default Arena"}))

    return Team(
        id=team_row.id,
        name=team_row.name,
        color=team_row.color or "#000000",
        color_secondary=team_row.color_secondary or "#ffffff",
        venue=venue,
        hoopers=hoopers,
    )


async def _check_earned_moves(
    repo: Repository,
    season_id: str,
    round_number: int,
    teams_cache: dict[str, Team],
    event_bus: EventBus | None = None,
) -> list[dict]:
    """Check all hoopers for newly earned moves via milestone thresholds.

    Iterates every hooper in teams_cache, aggregates their season stats,
    and grants any moves whose milestone thresholds have been crossed.
    Returns a list of grant dicts for narrative integration.
    """
    grants: list[dict] = []
    for team in teams_cache.values():
        for hooper in team.hoopers:
            season_stats = await repo.get_hooper_season_stats(hooper.id, season_id)
            existing_move_names = {m.name for m in hooper.moves}

            new_moves = check_milestones(season_stats, existing_move_names)
            for move in new_moves:
                await repo.add_hooper_move(hooper.id, move.model_dump())
                grant = {
                    "hooper_id": hooper.id,
                    "hooper_name": hooper.name,
                    "team_id": team.id,
                    "team_name": team.name,
                    "move_name": move.name,
                    "source": "earned",
                }
                grants.append(grant)
                logger.info(
                    "hooper_earned_move hooper=%s move=%s season=%s round=%d",
                    hooper.name,
                    move.name,
                    season_id,
                    round_number,
                )

                if event_bus:
                    await event_bus.publish(
                        "hooper.milestone_reached",
                        {
                            "hooper_id": hooper.id,
                            "hooper_name": hooper.name,
                            "team_id": team.id,
                            "team_name": team.name,
                            "move_name": move.name,
                            "round_number": round_number,
                            "season_id": season_id,
                        },
                    )

    if grants:
        logger.info(
            "milestones_granted season=%s round=%d count=%d",
            season_id,
            round_number,
            len(grants),
        )
    return grants


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


def _series_wins_needed(best_of: int) -> int:
    """Number of wins needed to clinch a best-of-N series."""
    return (best_of + 1) // 2


async def _get_playoff_series_record(
    repo: Repository,
    season_id: str,
    team_a_id: str,
    team_b_id: str,
    before_round: int | None = None,
) -> tuple[int, int, int]:
    """Get win counts for a playoff series between two teams.

    Returns (team_a_wins, team_b_wins, games_played).
    Counts games where this specific team pair was scheduled to play,
    regardless of round number — so manually-inserted or late-committed
    schedule entries are always included.

    Args:
        before_round: If set, only count games with round_number < before_round.
            Used by the display layer to show the pre-game series state for
            each historical game rather than the current overall series record.
    """
    playoff_schedule = await repo.get_full_schedule(season_id, phase="playoff")
    pair = frozenset({team_a_id, team_b_id})
    # Rounds where this specific matchup was scheduled (not all playoff rounds)
    scheduled_rounds = {
        s.round_number
        for s in playoff_schedule
        if frozenset({s.home_team_id, s.away_team_id}) == pair
    }
    all_games = await repo.get_all_games(season_id)

    a_wins = 0
    b_wins = 0
    games = 0
    for g in all_games:
        if frozenset({g.home_team_id, g.away_team_id}) != pair:
            continue
        if g.round_number not in scheduled_rounds:
            continue
        if before_round is not None and g.round_number >= before_round:
            continue
        games += 1
        if g.winner_team_id == team_a_id:
            a_wins += 1
        elif g.winner_team_id == team_b_id:
            b_wins += 1
    return a_wins, b_wins, games


async def _schedule_next_series_game(
    repo: Repository,
    season_id: str,
    higher_seed_id: str,
    lower_seed_id: str,
    games_played: int,
    round_number: int,
    matchup_index: int,
    phase: str = "semifinal",
) -> None:
    """Schedule the next game in a playoff series with alternating home court.

    Higher seed has home court in odd-numbered games (1, 3, 5, ...).

    Args:
        phase: Precise playoff phase — ``"semifinal"`` or ``"finals"``.
    """
    if games_played % 2 == 0:
        home_team_id = higher_seed_id
        away_team_id = lower_seed_id
    else:
        home_team_id = lower_seed_id
        away_team_id = higher_seed_id

    try:
        await repo.create_schedule_entry(
            season_id=season_id,
            round_number=round_number,
            matchup_index=matchup_index,
            home_team_id=home_team_id,
            away_team_id=away_team_id,
            phase=phase,
        )
    except IntegrityError:
        # Entry already exists (unique constraint on season/round/matchup_index).
        # This can happen if _advance_playoff_series is called twice (e.g., after
        # a retry or a double-tick). Safe to ignore — the existing entry stands.
        logger.warning(
            "schedule_entry_already_exists season=%s round=%d idx=%d phase=%s",
            season_id,
            round_number,
            matchup_index,
            phase,
        )


async def _advance_playoff_series(
    repo: Repository,
    season_id: str,
    ruleset: RuleSet,
    sim_round_number: int,
    event_bus: EventBus | None = None,
    suppress_spoiler_events: bool = False,
) -> tuple[bool, dict | None, list[tuple[str, dict]]]:
    """Advance playoff series after a round of games.

    Checks each active series (semis and finals) for completion, schedules
    next games as needed, creates finals when semis are decided, and enters
    championship when finals are decided.

    Returns (playoffs_complete, finals_matchup_info, deferred_events).
    """
    deferred_events: list[tuple[str, dict]] = []
    playoffs_complete = False
    finals_matchup: dict | None = None

    playoff_schedule = await repo.get_full_schedule(season_id, phase="playoff")
    if not playoff_schedule:
        return False, None, deferred_events

    # Identify initial semi matchups from the earliest playoff round
    playoff_rounds_sorted = sorted({s.round_number for s in playoff_schedule})
    first_playoff_round = playoff_rounds_sorted[0]
    initial_entries = [
        s for s in playoff_schedule if s.round_number == first_playoff_round
    ]
    initial_entries.sort(key=lambda s: s.matchup_index)

    # Determine if we have semi series or direct finals
    initial_pairs = [
        frozenset({s.home_team_id, s.away_team_id}) for s in initial_entries
    ]

    # Finals entries are playoff schedule entries with a team pair NOT in the initial bracket
    finals_entries = [
        s
        for s in playoff_schedule
        if frozenset({s.home_team_id, s.away_team_id}) not in initial_pairs
    ]
    has_finals = len(finals_entries) > 0

    # Check if this is a 2-team bracket (direct finals, no semis)
    is_direct_finals = len(initial_entries) == 1

    next_round = sim_round_number + 1

    if is_direct_finals:
        # --- 2-team bracket: only finals ---
        fe = initial_entries[0]
        higher_seed_id = fe.home_team_id
        lower_seed_id = fe.away_team_id
        best_of = ruleset.playoff_finals_best_of
        wins_needed = _series_wins_needed(best_of)

        h_wins, l_wins, games_played = await _get_playoff_series_record(
            repo, season_id, higher_seed_id, lower_seed_id
        )

        if h_wins >= wins_needed or l_wins >= wins_needed:
            champion_id = higher_seed_id if h_wins >= wins_needed else lower_seed_id
            playoffs_complete = True

            from pinwheel.core.season import enter_championship

            try:
                eb = None if suppress_spoiler_events else event_bus
                champ_config = await enter_championship(
                    repo, season_id, champion_id, event_bus=eb,
                )
                if suppress_spoiler_events:
                    deferred_events.append(
                        (
                            "season.championship_started",
                            {
                                "season_id": season_id,
                                "champion_team_id": champion_id,
                                "champion_team_name": champ_config.get(
                                    "champion_team_name", ""
                                ),
                                "awards": champ_config.get("awards", []),
                                "championship_ends_at": champ_config.get(
                                    "championship_ends_at", ""
                                ),
                            },
                        )
                    )
            except SQLAlchemyError:
                logger.exception(
                    "enter_championship_failed season=%s", season_id
                )
                await repo.update_season_status(season_id, "completed")

            event_data = {
                "season_id": season_id,
                "champion_team_id": champion_id,
                "finals_record": f"{max(h_wins, l_wins)}-{min(h_wins, l_wins)}",
            }
            if suppress_spoiler_events:
                deferred_events.append(("season.playoffs_complete", event_data))
            elif event_bus:
                await event_bus.publish("season.playoffs_complete", event_data)
        else:
            await _schedule_next_series_game(
                repo,
                season_id,
                higher_seed_id,
                lower_seed_id,
                games_played,
                next_round,
                0,
                phase="finals",
            )

        return playoffs_complete, finals_matchup, deferred_events

    # --- 4-team bracket: semis then finals ---

    # Check semi series state
    semi_winners: list[str] = []
    semi_series_info: list[dict] = []
    needs_more_semi_games = False

    for semi_idx, se in enumerate(initial_entries):
        higher_seed_id = se.home_team_id
        lower_seed_id = se.away_team_id
        best_of = ruleset.playoff_semis_best_of
        wins_needed = _series_wins_needed(best_of)

        h_wins, l_wins, games_played = await _get_playoff_series_record(
            repo, season_id, higher_seed_id, lower_seed_id
        )

        if h_wins >= wins_needed:
            semi_winners.append(higher_seed_id)
            semi_series_info.append(
                {
                    "matchup_index": semi_idx,
                    "winner_id": higher_seed_id,
                    "loser_id": lower_seed_id,
                    "winner_wins": h_wins,
                    "loser_wins": l_wins,
                }
            )
        elif l_wins >= wins_needed:
            semi_winners.append(lower_seed_id)
            semi_series_info.append(
                {
                    "matchup_index": semi_idx,
                    "winner_id": lower_seed_id,
                    "loser_id": higher_seed_id,
                    "winner_wins": l_wins,
                    "loser_wins": h_wins,
                }
            )
        else:
            # Series not decided — schedule next game
            await _schedule_next_series_game(
                repo,
                season_id,
                higher_seed_id,
                lower_seed_id,
                games_played,
                next_round,
                semi_idx,
                phase="semifinal",
            )
            needs_more_semi_games = True

    # Transition to finals when both semis are decided
    if (
        len(semi_winners) == len(initial_entries)
        and not has_finals
        and not needs_more_semi_games
    ):
        await repo.update_season_status(season_id, "playoffs")

        home_team_id = semi_winners[0]  # winner of higher-seed semi (#1v#4)
        away_team_id = semi_winners[1]  # winner of lower-seed semi (#2v#3)

        await repo.create_schedule_entry(
            season_id=season_id,
            round_number=next_round,
            matchup_index=0,
            home_team_id=home_team_id,
            away_team_id=away_team_id,
            phase="finals",
        )

        finals_matchup = {
            "playoff_round": "finals",
            "matchup_index": 0,
            "round_number": next_round,
            "home_team_id": home_team_id,
            "away_team_id": away_team_id,
        }

        event_data = {
            "season_id": season_id,
            "semifinal_winners": semi_winners,
            "finals_matchup": finals_matchup,
            "semi_series": semi_series_info,
        }
        if suppress_spoiler_events:
            deferred_events.append(("season.semifinals_complete", event_data))
        elif event_bus:
            await event_bus.publish("season.semifinals_complete", event_data)

        logger.info(
            "semifinals_complete season=%s finals=%s",
            season_id,
            finals_matchup,
        )

    # Handle finals series
    if has_finals:
        fe = finals_entries[0]
        higher_seed_id = fe.home_team_id
        lower_seed_id = fe.away_team_id
        best_of = ruleset.playoff_finals_best_of
        wins_needed = _series_wins_needed(best_of)

        h_wins, l_wins, games_played = await _get_playoff_series_record(
            repo, season_id, higher_seed_id, lower_seed_id
        )

        if h_wins >= wins_needed or l_wins >= wins_needed:
            champion_id = higher_seed_id if h_wins >= wins_needed else lower_seed_id
            playoffs_complete = True

            from pinwheel.core.season import enter_championship

            try:
                eb = None if suppress_spoiler_events else event_bus
                champ_config = await enter_championship(
                    repo, season_id, champion_id, event_bus=eb,
                )
                if suppress_spoiler_events:
                    deferred_events.append(
                        (
                            "season.championship_started",
                            {
                                "season_id": season_id,
                                "champion_team_id": champion_id,
                                "champion_team_name": champ_config.get(
                                    "champion_team_name", ""
                                ),
                                "awards": champ_config.get("awards", []),
                                "championship_ends_at": champ_config.get(
                                    "championship_ends_at", ""
                                ),
                            },
                        )
                    )
            except SQLAlchemyError:
                logger.exception(
                    "enter_championship_failed season=%s", season_id
                )
                await repo.update_season_status(season_id, "completed")

            event_data = {
                "season_id": season_id,
                "champion_team_id": champion_id,
                "finals_record": f"{max(h_wins, l_wins)}-{min(h_wins, l_wins)}",
            }
            if suppress_spoiler_events:
                deferred_events.append(("season.playoffs_complete", event_data))
            elif event_bus:
                await event_bus.publish("season.playoffs_complete", event_data)

            logger.info(
                "season_playoffs_complete season=%s champion=%s record=%s",
                season_id,
                champion_id,
                f"{max(h_wins, l_wins)}-{min(h_wins, l_wins)}",
            )
        else:
            await _schedule_next_series_game(
                repo,
                season_id,
                higher_seed_id,
                lower_seed_id,
                games_played,
                next_round,
                0,
                phase="finals",
            )

    return playoffs_complete, finals_matchup, deferred_events


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
                phase="semifinal",
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
            phase="finals",
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
    except SQLAlchemyError:
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
    except (SQLAlchemyError, ValueError, TypeError):
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
    except Exception:  # Last-resort handler — AI (Anthropic) and DB errors
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
    effect_registry: EffectRegistry | None = None,
    meta_store: MetaStore | None = None,
    skip_deferral: bool = False,
) -> tuple[RuleSet, list[VoteTally], dict]:
    """Tally all pending proposals and enact passing rule changes.

    Standalone function — can run with or without game simulation.
    When ``effect_registry`` is provided, passing proposals that contain
    v2 effects (meta_mutation, hook_callback, narrative) will have those
    effects registered in the registry and persisted to the event store.
    When ``meta_store`` is provided, fires ``gov.pre`` and ``gov.post``
    hooks around the governance tally for any registered effects.
    When ``skip_deferral`` is True, the minimum voting period is bypassed
    (used for season-close catch-up tallies).
    Returns (updated_ruleset, tallies, governance_data).
    """
    governance_data: dict = {"proposals": [], "votes": [], "rules_changed": []}

    # Fire gov.pre hooks
    if effect_registry and meta_store:
        _gov_pre_effects = effect_registry.get_effects_for_hook("gov.pre")
        if _gov_pre_effects:
            gov_pre_ctx = HookContext(
                round_number=round_number,
                season_id=season_id,
                meta_store=meta_store,
                rules=ruleset,
            )
            fire_effects("gov.pre", gov_pre_ctx, _gov_pre_effects)

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

    # --- Minimum voting period deferral ---
    # Every proposal must sit for at least one full tally cycle before being
    # tallied. On a proposal's first tally encounter we emit a
    # ``proposal.first_tally_seen`` event and defer it to the next cycle.
    # Skipped when ``skip_deferral`` is True (season-close catch-up).
    if pending_proposal_ids and not skip_deferral:
        first_seen_events = await repo.get_events_by_type(
            season_id=season_id,
            event_types=["proposal.first_tally_seen"],
        )
        already_seen_ids = {e.aggregate_id for e in first_seen_events}

        deferred_ids: list[str] = []
        ready_ids: list[str] = []
        for pid in pending_proposal_ids:
            if pid not in already_seen_ids:
                deferred_ids.append(pid)
            else:
                ready_ids.append(pid)

        # Emit first_tally_seen for newly encountered proposals
        for pid in deferred_ids:
            await repo.append_event(
                event_type="proposal.first_tally_seen",
                aggregate_id=pid,
                aggregate_type="proposal",
                season_id=season_id,
                payload={"proposal_id": pid, "round_number": round_number},
            )
            logger.info(
                "proposal_deferred pid=%s round=%d season=%s",
                pid,
                round_number,
                season_id,
            )

        # Only tally proposals that have been seen in a prior cycle
        pending_proposal_ids = ready_ids
        seen_ids = set(ready_ids)

    tallies: list[VoteTally] = []
    proposals: list[Proposal] = []

    if pending_proposal_ids:
        # Reconstruct proposals from submitted events
        submitted_events = await repo.get_events_by_type(
            season_id=season_id,
            event_types=["proposal.submitted"],
        )
        effects_v2_by_proposal: dict[str, list[EffectSpec]] = {}
        for se in submitted_events:
            p_data = se.payload
            pid = p_data.get("id", se.aggregate_id)
            if pid in seen_ids:
                # Extract v2 effects from the event payload before
                # stripping unknown fields for Proposal construction
                v2_effects = get_proposal_effects_v2(p_data)
                if v2_effects:
                    effects_v2_by_proposal[pid] = v2_effects
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

        if effect_registry is not None:
            new_ruleset, round_tallies = await tally_governance_with_effects(
                repo=repo,
                season_id=season_id,
                proposals=proposals,
                votes_by_proposal=votes_by_proposal,
                current_ruleset=ruleset,
                round_number=round_number,
                effect_registry=effect_registry,
                effects_v2_by_proposal=effects_v2_by_proposal,
            )
        else:
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

    # Fire gov.post hooks
    if effect_registry and meta_store:
        _gov_post_effects = effect_registry.get_effects_for_hook("gov.post")
        if _gov_post_effects:
            gov_post_ctx = HookContext(
                round_number=round_number,
                season_id=season_id,
                meta_store=meta_store,
                rules=ruleset,
                tally=tallies,
            )
            fire_effects("gov.post", gov_post_ctx, _gov_post_effects)

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

    # 3b. Load effect registry and meta store
    effect_registry: EffectRegistry | None = None
    meta_store: MetaStore | None = None
    try:
        effect_registry = await load_effect_registry(repo, season_id)
        if effect_registry.count > 0:
            meta_store = MetaStore()
            # Load team meta from DB
            for tid in teams_cache:
                team_meta = await repo.load_team_meta(tid)
                if team_meta:
                    meta_store.load_entity("team", tid, team_meta)
            # Load hooper meta from DB
            hooper_meta = await repo.load_hoopers_meta_for_teams(
                list(teams_cache.keys()),
            )
            for hooper_id, h_meta in hooper_meta.items():
                if h_meta:
                    meta_store.load_entity("hooper", hooper_id, h_meta)
            logger.info(
                "effects_loaded season=%s effects=%d",
                season_id,
                effect_registry.count,
            )
    except SQLAlchemyError:
        logger.exception("effect_registry_load_failed season=%s", season_id)
        effect_registry = None
        meta_store = None

    # 3c. Load team strategies
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
                except (ValueError, TypeError):
                    logger.warning("invalid_strategy_payload team=%s", tid)
                break

    # 4. Simulate games
    _PLAYOFF_PHASES = ("playoff", "semifinal", "finals")
    playoff_context: str | None = None
    if schedule and schedule[0].phase in _PLAYOFF_PHASES:
        # Use precise phase if stored; fall back to inference for legacy entries.
        if schedule[0].phase in ("semifinal", "finals"):
            playoff_context = schedule[0].phase
        else:
            # Legacy: determine semi vs finals by checking the bracket.
            full_playoff_schedule = await repo.get_full_schedule(season_id, phase="playoff")
            if full_playoff_schedule:
                earliest_round = min(s.round_number for s in full_playoff_schedule)
                initial_pairs = [
                    frozenset({s.home_team_id, s.away_team_id})
                    for s in full_playoff_schedule
                    if s.round_number == earliest_round
                ]
                current_pairs = [
                    frozenset({s.home_team_id, s.away_team_id}) for s in schedule
                ]
                if len(initial_pairs) >= 2 and all(
                    p in initial_pairs for p in current_pairs
                ):
                    playoff_context = "semifinal"
                else:
                    playoff_context = "finals"
            else:
                playoff_context = "semifinal" if len(schedule) >= 2 else "finals"

    # Fire round.pre effects
    _round_effects = (
        effect_registry.get_effects_for_hook("round.pre") if effect_registry else []
    )
    if _round_effects and meta_store:
        round_ctx = HookContext(
            round_number=round_number,
            season_id=season_id,
            meta_store=meta_store,
            teams={tid: t for tid, t in teams_cache.items()},
        )
        fire_effects("round.pre", round_ctx, _round_effects)

    # Pre-compute series records for playoff games (before this round's games)
    _pre_round_series: dict[str, tuple[int, int]] = {}  # "teamA:teamB" -> (a_wins, b_wins)
    if playoff_context:
        for entry in schedule:
            sorted_ids = sorted([entry.home_team_id, entry.away_team_id])
            pair_key = ":".join(sorted_ids)
            if pair_key not in _pre_round_series:
                a_wins, b_wins, _ = await _get_playoff_series_record(
                    repo, season_id, sorted_ids[0], sorted_ids[1]
                )
                _pre_round_series[pair_key] = (a_wins, b_wins)

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

        # Skip unnecessary playoff games — if the series is already clinched,
        # this game doesn't need to be played.
        if playoff_context and entry.phase in _PLAYOFF_PHASES:
            pair_key = ":".join(sorted([home.id, away.id]))
            pre_wins = _pre_round_series.get(pair_key, (0, 0))
            _best_of_check = (
                ruleset.playoff_finals_best_of
                if playoff_context == "finals"
                else ruleset.playoff_semis_best_of
            )
            _wins_needed = _series_wins_needed(_best_of_check)
            sorted_ids = sorted([home.id, away.id])
            if sorted_ids[0] == home.id:
                _h_pre, _a_pre = pre_wins
            else:
                _a_pre, _h_pre = pre_wins
            if _h_pre >= _wins_needed or _a_pre >= _wins_needed:
                logger.info(
                    "skipping_unnecessary_game round=%d matchup=%d %s vs %s "
                    "series_already_clinched=%d-%d",
                    round_number, entry.matchup_index, home.name, away.name,
                    _h_pre, _a_pre,
                )
                continue

        seed = int(uuid.uuid4().int % (2**31))
        game_id = f"g-{round_number}-{entry.matchup_index}"

        # Fire round.game.pre effects
        _game_pre_effects = (
            effect_registry.get_effects_for_hook("round.game.pre")
            if effect_registry
            else []
        )
        if _game_pre_effects and meta_store:
            game_pre_ctx = HookContext(
                round_number=round_number,
                season_id=season_id,
                meta_store=meta_store,
                home_team_id=home.id,
                away_team_id=away.id,
                teams=teams_cache,
            )
            fire_effects("round.game.pre", game_pre_ctx, _game_pre_effects)

        # Get sim-level effects for the effect_registry
        _sim_effects = (
            effect_registry.get_all_active() if effect_registry else None
        )

        result = simulate_game(
            home,
            away,
            ruleset,
            seed,
            game_id=game_id,
            home_strategy=strategies.get(home.id),
            away_strategy=strategies.get(away.id),
            effect_registry=_sim_effects,
            meta_store=meta_store,
        )

        # Store result — carry the precise phase from the schedule entry
        _game_phase: str | None = None
        if entry.phase in _PLAYOFF_PHASES:
            _game_phase = entry.phase if entry.phase != "playoff" else playoff_context
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
            phase=_game_phase,
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

        # Fire round.game.post effects
        _game_post_effects = (
            effect_registry.get_effects_for_hook("round.game.post")
            if effect_registry
            else []
        )
        if _game_post_effects and meta_store:
            margin = abs(result.home_score - result.away_score)
            game_post_ctx = HookContext(
                round_number=round_number,
                season_id=season_id,
                meta_store=meta_store,
                home_team_id=home.id,
                away_team_id=away.id,
                winner_team_id=result.winner_team_id,
                margin=margin,
                teams=teams_cache,
            )
            fire_effects("round.game.post", game_post_ctx, _game_post_effects)

        # Build series_context for playoff games (pre-round record)
        _series_ctx: dict | None = None
        if playoff_context:
            pair_key = ":".join(sorted([home.id, away.id]))
            pre_wins = _pre_round_series.get(pair_key, (0, 0))
            # Map pre-round wins to home/away order
            if pair_key == ":".join(sorted([home.id, away.id])):
                # Determine which is which based on sort order
                sorted_ids = sorted([home.id, away.id])
                if sorted_ids[0] == home.id:
                    h_wins, a_wins = pre_wins
                else:
                    a_wins, h_wins = pre_wins
            else:
                h_wins, a_wins = 0, 0
            best_of = (
                ruleset.playoff_finals_best_of
                if playoff_context == "finals"
                else ruleset.playoff_semis_best_of
            )
            wins_needed = _series_wins_needed(best_of)
            if playoff_context == "finals":
                phase_label = "CHAMPIONSHIP FINALS"
                clinch_text = f"First to {wins_needed} wins is champion"
            else:
                phase_label = "SEMIFINAL SERIES"
                clinch_text = f"First to {wins_needed} wins advances"
            if h_wins == a_wins:
                record_text = f"Series tied {h_wins}-{a_wins}"
            elif h_wins > a_wins:
                record_text = f"{home.name} lead {h_wins}-{a_wins}"
            else:
                record_text = f"{away.name} lead {a_wins}-{h_wins}"

            _series_ctx = {
                "phase": playoff_context,
                "phase_label": phase_label,
                "home_wins": h_wins,
                "away_wins": a_wins,
                "best_of": best_of,
                "wins_needed": wins_needed,
                "description": f"{phase_label} \u00b7 {record_text} \u00b7 {clinch_text}",
            }

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
            "playoff_context": playoff_context,
            "series_context": _series_ctx,
        }

        game_summaries.append(summary)

        # Publish game.completed without commentary (commentary added in AI phase)
        if event_bus and not suppress_spoiler_events:
            await event_bus.publish("game.completed", summary)

    # Fire round.post effects (after all games, before governance)
    _round_post_effects = (
        effect_registry.get_effects_for_hook("round.post") if effect_registry else []
    )
    if _round_post_effects and meta_store:
        round_post_ctx = HookContext(
            round_number=round_number,
            season_id=season_id,
            meta_store=meta_store,
            game_results=game_results,
            teams=teams_cache,
        )
        fire_effects("round.post", round_post_ctx, _round_post_effects)

    # Flush meta store changes to DB
    if meta_store:
        try:
            dirty = meta_store.get_dirty_entities()
            if dirty:
                await repo.flush_meta_store(dirty)
                logger.info(
                    "meta_flushed season=%s round=%d entities=%d",
                    season_id,
                    round_number,
                    len(dirty),
                )
        except SQLAlchemyError:
            logger.exception(
                "meta_flush_failed season=%s round=%d", season_id, round_number
            )

    # Tick effect lifetimes and persist expirations
    if effect_registry:
        expired_ids = effect_registry.tick_round(round_number)
        if expired_ids:
            try:
                await persist_expired_effects(repo, season_id, expired_ids)
            except SQLAlchemyError:
                logger.exception(
                    "effect_expiration_persist_failed season=%s round=%d",
                    season_id,
                    round_number,
                )

    # 4b. Milestone checks — earned moves for hoopers who hit stat thresholds
    milestone_grants: list[dict] = []
    try:
        milestone_grants = await _check_earned_moves(
            repo, season_id, round_number, teams_cache, event_bus,
        )
    except SQLAlchemyError:
        logger.exception(
            "milestone_check_failed season=%s round=%d", season_id, round_number
        )

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
            effect_registry=effect_registry,
            meta_store=meta_store,
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

    # Query governor activity for private reports — enriched with blind spots,
    # voting outcomes, and swing vote data from compute_private_report_context.
    active_governor_events = await repo.get_events_by_type(
        season_id=season_id,
        event_types=["proposal.submitted", "vote.cast"],
    )
    active_governor_ids = {e.governor_id for e in active_governor_events if e.governor_id}

    governor_activity: dict[str, dict] = {}
    for gov_id in active_governor_ids:
        try:
            enriched = await compute_private_report_context(
                repo, gov_id, season_id, round_number,
            )
            governor_activity[gov_id] = enriched
        except SQLAlchemyError:
            logger.exception(
                "private_report_context_failed gov=%s season=%s round=%d",
                gov_id, season_id, round_number,
            )
            # Fallback to basic counts so private report still generates
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

    # Compute narrative context for output systems
    narrative_ctx: NarrativeContext | None = None
    try:
        narrative_ctx = await compute_narrative_context(
            repo, season_id, round_number, governance_interval,
            ruleset=ruleset,
        )
    except SQLAlchemyError:
        logger.exception(
            "narrative_context_failed season=%s round=%d", season_id, round_number
        )

    # Build effects summary for report context and inject into narrative
    effects_summary = ""
    if effect_registry and effect_registry.count > 0:
        effects_summary = effect_registry.build_effects_summary()
        if narrative_ctx is not None:
            narrative_ctx.effects_narrative = effects_summary

    # Build meta_store snapshot for AI phase (deep copy, safe to pass without DB)
    meta_store_snapshot = meta_store.snapshot() if meta_store else None

    # Classify drama for each game (pure computation, sub-millisecond)
    drama_map: dict[str, list[DramaAnnotation]] = {}
    for i, result in enumerate(game_results):
        gid = (
            game_summaries[i].get("game_id", result.game_id)
            if i < len(game_summaries)
            else result.game_id
        )
        game_drama = annotate_drama(result)
        drama_map[gid] = game_drama
        drama_counts = get_drama_summary(game_drama)
        logger.info(
            "drama_classified game=%s routine=%d elevated=%d high=%d peak=%d",
            gid,
            drama_counts.get("routine", 0),
            drama_counts.get("elevated", 0),
            drama_counts.get("high", 0),
            drama_counts.get("peak", 0),
        )

    # Pre-compute insight data for Phase 2 AI generation
    impact_validation_data: list[dict] = []
    governor_leverage_data: dict[str, dict] = {}
    governor_behavioral_data: dict[str, dict] = {}
    insight_interval = 3  # generate leverage/behavioral every N rounds

    try:
        impact_validation_data = await compute_impact_validation(
            repo, season_id, round_number, governance_data,
        )
    except SQLAlchemyError:
        logger.exception(
            "insight_compute_impact_failed season=%s round=%d", season_id, round_number,
        )

    # Leverage + behavioral only every N rounds and when governors exist
    if round_number % insight_interval == 0 and active_governor_ids:
        for gov_id in active_governor_ids:
            try:
                governor_leverage_data[gov_id] = await compute_governor_leverage(
                    repo, gov_id, season_id,
                )
            except SQLAlchemyError:
                logger.exception(
                    "insight_compute_leverage_failed gov=%s season=%s", gov_id, season_id,
                )
            try:
                governor_behavioral_data[gov_id] = await compute_behavioral_profile(
                    repo, gov_id, season_id,
                )
            except SQLAlchemyError:
                logger.exception(
                    "insight_compute_behavioral_failed gov=%s season=%s", gov_id, season_id,
                )

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
        narrative_context=narrative_ctx,
        effects_summary=effects_summary,
        milestone_grants=milestone_grants,
        effect_registry=effect_registry,
        meta_store_snapshot=meta_store_snapshot,
        drama_annotations=drama_map,
        impact_validation_data=impact_validation_data,
        governor_leverage_data=governor_leverage_data,
        governor_behavioral_data=governor_behavioral_data,
        insight_interval=insight_interval,
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
    round_data: dict[str, object] = {
        "round_number": sim.round_number,
        "games": sim.game_summaries,
    }
    # Inject rule changes so the simulation report can correlate rules to outcomes
    rules_changed = sim.governance_data.get("rules_changed", [])
    if rules_changed:
        round_data["rule_changes"] = rules_changed
    narrative = sim.narrative_context

    # Fire report hooks using the effect_registry snapshot from Phase 1.
    # These hooks inject narrative context from effects into the AI prompt.
    _report_narratives: list[str] = []
    if sim.effect_registry and sim.effect_registry.count > 0:
        # Reconstruct a read-only MetaStore from the Phase 1 snapshot
        _meta_snapshot_store: MetaStore | None = None
        if sim.meta_store_snapshot:
            _meta_snapshot_store = MetaStore()
            for etype, entities in sim.meta_store_snapshot.items():
                for eid, fields in entities.items():
                    _meta_snapshot_store.load_entity(etype, eid, dict(fields))

        # report.commentary.pre — fires before game commentary generation
        _commentary_pre_effects = sim.effect_registry.get_effects_for_hook(
            "report.commentary.pre",
        )
        if _commentary_pre_effects:
            commentary_pre_ctx = HookContext(
                round_number=sim.round_number,
                season_id=sim.season_id,
                meta_store=_meta_snapshot_store,
                report_data={"games": sim.game_summaries},
                rules=sim.ruleset,
            )
            commentary_results = fire_effects(
                "report.commentary.pre", commentary_pre_ctx, _commentary_pre_effects,
            )
            for hr in commentary_results:
                if hr.narrative:
                    _report_narratives.append(hr.narrative)

        # report.simulation.pre — fires before simulation report generation
        _sim_pre_effects = sim.effect_registry.get_effects_for_hook(
            "report.simulation.pre",
        )
        if _sim_pre_effects:
            sim_pre_ctx = HookContext(
                round_number=sim.round_number,
                season_id=sim.season_id,
                meta_store=_meta_snapshot_store,
                report_data=round_data,
                rules=sim.ruleset,
            )
            sim_results = fire_effects(
                "report.simulation.pre", sim_pre_ctx, _sim_pre_effects,
            )
            for hr in sim_results:
                if hr.narrative:
                    _report_narratives.append(hr.narrative)

    # Inject effect-produced narratives into the narrative context
    if _report_narratives and narrative is not None:
        combined = "\n".join(_report_narratives)
        if narrative.effects_narrative:
            narrative.effects_narrative += "\n" + combined
        else:
            narrative.effects_narrative = combined

    # --- All AI calls below are independent and can run concurrently ---
    # We build coroutines for each call, then execute them via asyncio.gather().
    # Mock (sync) calls are wrapped in trivial coroutines for uniformity.

    # -- Commentary per game --
    commentary_game_ids: list[str] = []
    commentary_coros: list[asyncio.Future[str]] = []
    for i, result in enumerate(sim.game_results):
        if i >= len(sim.game_summaries):
            break
        game_id = sim.game_summaries[i].get("game_id", "")
        home = sim.teams_cache.get(result.home_team_id)
        away = sim.teams_cache.get(result.away_team_id)
        if not home or not away:
            continue
        commentary_game_ids.append(game_id)
        if api_key:
            commentary_coros.append(
                generate_game_commentary(
                    result, home, away, sim.ruleset, api_key,
                    playoff_context=sim.playoff_context,
                    narrative=narrative,
                )
            )
        else:
            # Wrap sync mock in a coroutine
            _mock_commentary = generate_game_commentary_mock(
                result, home, away,
                playoff_context=sim.playoff_context,
                narrative=narrative,
            )

            async def _wrap_commentary(c: str = _mock_commentary) -> str:
                return c

            commentary_coros.append(_wrap_commentary())

    # -- Highlight reel --
    async def _gen_highlight() -> str:
        if not sim.game_summaries:
            return ""
        if api_key:
            return await generate_highlight_reel(
                sim.game_summaries, sim.round_number, api_key,
                playoff_context=sim.playoff_context,
                narrative=narrative,
            )
        return generate_highlight_reel_mock(
            sim.game_summaries, sim.round_number,
            playoff_context=sim.playoff_context,
            narrative=narrative,
        )

    # -- Simulation report --
    async def _gen_sim_report() -> Report:
        if api_key:
            return await generate_simulation_report(
                round_data, sim.season_id, sim.round_number, api_key,
                narrative=narrative,
            )
        return generate_simulation_report_mock(
            round_data, sim.season_id, sim.round_number,
            narrative=narrative,
        )

    # -- Governance report --
    async def _gen_gov_report() -> Report:
        if api_key:
            return await generate_governance_report(
                sim.governance_data, sim.season_id, sim.round_number, api_key,
                narrative=narrative,
            )
        return generate_governance_report_mock(
            sim.governance_data, sim.season_id, sim.round_number,
            narrative=narrative,
        )

    # -- Private reports per governor --
    private_gov_ids: list[str] = list(sim.active_governor_ids)
    private_coros: list[asyncio.Future[Report]] = []
    for gov_id in private_gov_ids:
        governor_data = sim.governor_activity.get(gov_id, {})
        if api_key:
            private_coros.append(
                generate_private_report(
                    governor_data, gov_id, sim.season_id, sim.round_number, api_key,
                )
            )
        else:
            _mock_priv = generate_private_report_mock(
                governor_data, gov_id, sim.season_id, sim.round_number,
            )

            async def _wrap_priv(r: Report = _mock_priv) -> Report:
                return r

            private_coros.append(_wrap_priv())

    # -- Impact validation report (only when rules changed) --
    async def _gen_impact() -> Report | None:
        if not sim.impact_validation_data:
            return None
        if api_key:
            return await generate_impact_validation(
                sim.impact_validation_data, sim.season_id, sim.round_number, api_key,
            )
        return generate_impact_validation_mock(
            sim.impact_validation_data, sim.season_id, sim.round_number,
        )

    # -- Leverage reports per governor --
    leverage_gov_ids: list[str] = list(sim.governor_leverage_data.keys())
    leverage_coros: list[asyncio.Future[Report]] = []
    for gov_id in leverage_gov_ids:
        lev_data = sim.governor_leverage_data[gov_id]
        if api_key:
            leverage_coros.append(
                generate_leverage_report(
                    lev_data, gov_id, sim.season_id, sim.round_number, api_key,
                )
            )
        else:
            _mock_lev = generate_leverage_report_mock(
                lev_data, gov_id, sim.season_id, sim.round_number,
            )

            async def _wrap_lev(r: Report = _mock_lev) -> Report:
                return r

            leverage_coros.append(_wrap_lev())

    # -- Behavioral reports per governor --
    behavioral_gov_ids: list[str] = list(sim.governor_behavioral_data.keys())
    behavioral_coros: list[asyncio.Future[Report]] = []
    for gov_id in behavioral_gov_ids:
        beh_data = sim.governor_behavioral_data[gov_id]
        if api_key:
            behavioral_coros.append(
                generate_behavioral_report(
                    beh_data, gov_id, sim.season_id, sim.round_number, api_key,
                )
            )
        else:
            _mock_beh = generate_behavioral_report_mock(
                beh_data, gov_id, sim.season_id, sim.round_number,
            )

            async def _wrap_beh(r: Report = _mock_beh) -> Report:
                return r

            behavioral_coros.append(_wrap_beh())

    # --- Execute all AI calls concurrently ---
    # Gather commentary, highlight, sim_report, gov_report, impact, and all
    # per-governor reports (private, leverage, behavioral) in a single gather.
    # Each call is independent — they read shared data but don't write to it.

    all_coros: list[asyncio.Future[str | Report | None]] = []  # type: ignore[type-arg]
    # Track offsets into the results list for each category
    _commentary_start = len(all_coros)
    all_coros.extend(commentary_coros)  # type: ignore[arg-type]
    _commentary_end = len(all_coros)

    _highlight_idx = len(all_coros)
    all_coros.append(_gen_highlight())  # type: ignore[arg-type]

    _sim_report_idx = len(all_coros)
    all_coros.append(_gen_sim_report())  # type: ignore[arg-type]

    _gov_report_idx = len(all_coros)
    all_coros.append(_gen_gov_report())  # type: ignore[arg-type]

    _private_start = len(all_coros)
    all_coros.extend(private_coros)  # type: ignore[arg-type]
    _private_end = len(all_coros)

    _impact_idx = len(all_coros)
    all_coros.append(_gen_impact())  # type: ignore[arg-type]

    _leverage_start = len(all_coros)
    all_coros.extend(leverage_coros)  # type: ignore[arg-type]
    _leverage_end = len(all_coros)

    _behavioral_start = len(all_coros)
    all_coros.extend(behavioral_coros)  # type: ignore[arg-type]
    _behavioral_end = len(all_coros)

    # return_exceptions=True so one failure doesn't cancel the rest
    all_results = await asyncio.gather(*all_coros, return_exceptions=True)

    # --- Unpack results ---

    # Commentary per game
    for idx_c, game_id in enumerate(commentary_game_ids):
        res = all_results[_commentary_start + idx_c]
        if isinstance(res, BaseException):
            logger.exception(
                "commentary_failed game=%s season=%s round=%d",
                game_id, sim.season_id, sim.round_number,
                exc_info=res,
            )
        else:
            commentaries[game_id] = res  # type: ignore[assignment]

    # Highlight reel
    highlight_reel = ""
    res_hl = all_results[_highlight_idx]
    if isinstance(res_hl, BaseException):
        logger.exception(
            "highlight_reel_failed season=%s round=%d",
            sim.season_id, sim.round_number,
            exc_info=res_hl,
        )
    else:
        highlight_reel = res_hl  # type: ignore[assignment]

    # Simulation report
    res_sim = all_results[_sim_report_idx]
    if isinstance(res_sim, BaseException):
        logger.exception(
            "sim_report_failed season=%s round=%d",
            sim.season_id, sim.round_number,
            exc_info=res_sim,
        )
        # Fallback to mock so we always have a report
        sim_report = generate_simulation_report_mock(
            round_data, sim.season_id, sim.round_number,
            narrative=narrative,
        )
    else:
        sim_report = res_sim  # type: ignore[assignment]

    # Governance report
    res_gov = all_results[_gov_report_idx]
    if isinstance(res_gov, BaseException):
        logger.exception(
            "gov_report_failed season=%s round=%d",
            sim.season_id, sim.round_number,
            exc_info=res_gov,
        )
        gov_report = generate_governance_report_mock(
            sim.governance_data, sim.season_id, sim.round_number,
            narrative=narrative,
        )
    else:
        gov_report = res_gov  # type: ignore[assignment]

    # Private reports per governor
    private_reports: list[tuple[str, Report]] = []
    for idx_p, gov_id in enumerate(private_gov_ids):
        res_p = all_results[_private_start + idx_p]
        if isinstance(res_p, BaseException):
            logger.exception(
                "private_report_failed gov=%s season=%s round=%d",
                gov_id, sim.season_id, sim.round_number,
                exc_info=res_p,
            )
        else:
            private_reports.append((gov_id, res_p))  # type: ignore[arg-type]

    # Impact validation report
    impact_report: Report | None = None
    res_impact = all_results[_impact_idx]
    if isinstance(res_impact, BaseException):
        logger.exception(
            "impact_validation_failed season=%s round=%d",
            sim.season_id, sim.round_number,
            exc_info=res_impact,
        )
    else:
        impact_report = res_impact  # type: ignore[assignment]

    # Leverage reports per governor
    leverage_reports: list[tuple[str, Report]] = []
    for idx_l, gov_id in enumerate(leverage_gov_ids):
        res_l = all_results[_leverage_start + idx_l]
        if isinstance(res_l, BaseException):
            logger.exception(
                "leverage_report_failed gov=%s season=%s round=%d",
                gov_id, sim.season_id, sim.round_number,
                exc_info=res_l,
            )
        else:
            leverage_reports.append((gov_id, res_l))  # type: ignore[arg-type]

    # Behavioral reports per governor
    behavioral_reports: list[tuple[str, Report]] = []
    for idx_b, gov_id in enumerate(behavioral_gov_ids):
        res_b = all_results[_behavioral_start + idx_b]
        if isinstance(res_b, BaseException):
            logger.exception(
                "behavioral_report_failed gov=%s season=%s round=%d",
                gov_id, sim.season_id, sim.round_number,
                exc_info=res_b,
            )
        else:
            behavioral_reports.append((gov_id, res_b))  # type: ignore[arg-type]

    return _AIPhaseResult(
        commentaries=commentaries,
        highlight_reel=highlight_reel,
        sim_report=sim_report,
        gov_report=gov_report,
        private_reports=private_reports,
        impact_report=impact_report,
        leverage_reports=leverage_reports,
        behavioral_reports=behavioral_reports,
    )


async def _get_series_games(
    repo: Repository,
    season_id: str,
    team_a_id: str,
    team_b_id: str,
) -> list[dict]:
    """Get all playoff games between two teams, ordered chronologically.

    Returns a list of dicts with home/away team ids, scores, round number.
    """
    playoff_schedule = await repo.get_full_schedule(season_id, phase="playoff")
    playoff_rounds = {s.round_number for s in playoff_schedule}
    all_games = await repo.get_all_games(season_id)

    pair = frozenset({team_a_id, team_b_id})
    series_games: list[dict] = []
    for g in sorted(all_games, key=lambda g: (g.round_number, g.matchup_index)):
        if g.round_number not in playoff_rounds:
            continue
        if frozenset({g.home_team_id, g.away_team_id}) == pair:
            series_games.append(
                {
                    "round_number": g.round_number,
                    "home_team_id": g.home_team_id,
                    "away_team_id": g.away_team_id,
                    "home_score": g.home_score,
                    "away_score": g.away_score,
                    "winner_team_id": g.winner_team_id,
                }
            )
    return series_games


async def _generate_series_reports(
    repo: Repository,
    season_id: str,
    deferred_events: list[tuple[str, dict]],
    teams_cache: dict[str, Team],
    api_key: str = "",
) -> list[dict]:
    """Generate series reports for any completed series in deferred events.

    Scans deferred_events for ``season.semifinals_complete`` and
    ``season.playoffs_complete`` events, gathers game data for each
    completed series, generates an AI recap, and stores it.

    Returns list of report event dicts for downstream publishing.
    """
    report_events: list[dict] = []

    for event_type, event_data in deferred_events:
        if event_type == "season.semifinals_complete":
            # Each semi series that just completed
            for semi in event_data.get("semi_series", []):
                winner_id = semi.get("winner_id", "")
                loser_id = semi.get("loser_id", "")
                if not winner_id or not loser_id:
                    continue

                winner_name = teams_cache[winner_id].name if winner_id in teams_cache else winner_id
                loser_name = teams_cache[loser_id].name if loser_id in teams_cache else loser_id
                w_wins = semi.get("winner_wins", 0)
                l_wins = semi.get("loser_wins", 0)

                games = await _get_series_games(repo, season_id, winner_id, loser_id)
                # Enrich game dicts with team names
                for g in games:
                    g["home_team_name"] = (
                        teams_cache[g["home_team_id"]].name
                        if g["home_team_id"] in teams_cache
                        else g["home_team_id"]
                    )
                    g["away_team_name"] = (
                        teams_cache[g["away_team_id"]].name
                        if g["away_team_id"] in teams_cache
                        else g["away_team_id"]
                    )

                series_data = {
                    "series_type": "semifinal",
                    "winner_name": winner_name,
                    "loser_name": loser_name,
                    "record": f"{w_wins}-{l_wins}",
                    "games": games,
                }

                try:
                    if api_key:
                        report = await generate_series_report(series_data, season_id, api_key)
                    else:
                        report = generate_series_report_mock(series_data)

                    await repo.store_report(
                        season_id=season_id,
                        report_type="series",
                        round_number=0,
                        content=report.content,
                        team_id=winner_id,
                        metadata_json={
                            "series_type": "semifinal",
                            "winner_id": winner_id,
                            "loser_id": loser_id,
                            "record": f"{w_wins}-{l_wins}",
                        },
                    )
                    report_events.append(
                        {
                            "report_type": "series",
                            "series_type": "semifinal",
                            "winner_name": winner_name,
                            "loser_name": loser_name,
                            "excerpt": report.content[:200],
                        },
                    )
                    logger.info(
                        "series_report_generated season=%s type=semifinal winner=%s",
                        season_id,
                        winner_name,
                    )
                except Exception:  # Last-resort handler — AI (Anthropic) and DB errors
                    logger.exception(
                        "series_report_failed season=%s type=semifinal winner=%s",
                        season_id,
                        winner_name,
                    )

        elif event_type == "season.playoffs_complete":
            champion_id = event_data.get("champion_team_id", "")
            record = event_data.get("finals_record", "")
            if not champion_id:
                continue

            # Determine the loser by finding teams in the finals
            playoff_schedule = await repo.get_full_schedule(season_id, phase="playoff")
            initial_round = min((s.round_number for s in playoff_schedule), default=0)
            initial_pairs = [
                frozenset({s.home_team_id, s.away_team_id})
                for s in playoff_schedule
                if s.round_number == initial_round
            ]
            # Finals entries are pairs NOT in the initial bracket
            finals_teams: set[str] = set()
            for s in playoff_schedule:
                pair = frozenset({s.home_team_id, s.away_team_id})
                if pair not in initial_pairs:
                    finals_teams.update(pair)

            # For direct finals (2-team bracket), use initial pair
            if not finals_teams:
                for s in playoff_schedule:
                    if s.round_number == initial_round:
                        finals_teams.add(s.home_team_id)
                        finals_teams.add(s.away_team_id)

            loser_id = ""
            for tid in finals_teams:
                if tid != champion_id:
                    loser_id = tid
                    break

            if not loser_id:
                continue

            winner_name = (
                teams_cache[champion_id].name if champion_id in teams_cache else champion_id
            )
            loser_name = teams_cache[loser_id].name if loser_id in teams_cache else loser_id

            games = await _get_series_games(repo, season_id, champion_id, loser_id)
            for g in games:
                g["home_team_name"] = (
                    teams_cache[g["home_team_id"]].name
                    if g["home_team_id"] in teams_cache
                    else g["home_team_id"]
                )
                g["away_team_name"] = (
                    teams_cache[g["away_team_id"]].name
                    if g["away_team_id"] in teams_cache
                    else g["away_team_id"]
                )

            series_data = {
                "series_type": "finals",
                "winner_name": winner_name,
                "loser_name": loser_name,
                "record": record,
                "games": games,
            }

            try:
                if api_key:
                    report = await generate_series_report(series_data, season_id, api_key)
                else:
                    report = generate_series_report_mock(series_data)

                await repo.store_report(
                    season_id=season_id,
                    report_type="series",
                    round_number=0,
                    content=report.content,
                    team_id=champion_id,
                    metadata_json={
                        "series_type": "finals",
                        "winner_id": champion_id,
                        "loser_id": loser_id,
                        "record": record,
                    },
                )
                report_events.append(
                    {
                        "report_type": "series",
                        "series_type": "finals",
                        "winner_name": winner_name,
                        "loser_name": loser_name,
                        "excerpt": report.content[:200],
                    },
                )
                logger.info(
                    "series_report_generated season=%s type=finals winner=%s",
                    season_id,
                    winner_name,
                )
            except Exception:  # Last-resort handler — AI (Anthropic) and DB errors
                logger.exception(
                    "series_report_failed season=%s type=finals winner=%s",
                    season_id,
                    winner_name,
                )

    return report_events


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

    # Store impact validation report
    if ai.impact_report:
        await repo.store_report(
            season_id=sim.season_id,
            report_type="impact_validation",
            round_number=sim.round_number,
            content=ai.impact_report.content,
        )
        reports.append(ai.impact_report)
        deferred_report_events.append(
            {
                "report_type": "impact_validation",
                "round": sim.round_number,
                "excerpt": ai.impact_report.content[:200],
            },
        )

    # Store leverage reports (private)
    for gov_id, lev_report in ai.leverage_reports:
        await repo.store_report(
            season_id=sim.season_id,
            report_type="leverage",
            round_number=sim.round_number,
            content=lev_report.content,
            governor_id=gov_id,
        )
        reports.append(lev_report)
        deferred_report_events.append(
            {
                "report_type": "leverage",
                "round": sim.round_number,
                "governor_id": gov_id,
                "excerpt": lev_report.content[:200],
            },
        )

    # Store behavioral reports (private)
    for gov_id, beh_report in ai.behavioral_reports:
        await repo.store_report(
            season_id=sim.season_id,
            report_type="behavioral",
            round_number=sim.round_number,
            content=beh_report.content,
            governor_id=gov_id,
        )
        reports.append(beh_report)
        deferred_report_events.append(
            {
                "report_type": "behavioral",
                "round": sim.round_number,
                "governor_id": gov_id,
                "excerpt": beh_report.content[:200],
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
    except Exception:  # Last-resort handler — AI (Anthropic) and DB errors
        logger.exception(
            "eval_step_failed season=%s round=%d", sim.season_id, sim.round_number
        )

    # Season progression checks
    season_complete = False
    final_standings: list[dict] | None = None
    playoff_bracket: list[dict] | None = None
    playoffs_complete = False
    finals_matchup: dict | None = None
    deferred_season_events: list[tuple[str, dict]] = []

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
        except SQLAlchemyError:
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
            except SQLAlchemyError:
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
                except SQLAlchemyError:
                    logger.exception(
                        "playoff_bracket_failed season=%s",
                        sim.season_id,
                    )

            season_event_data = {
                "season_id": sim.season_id,
                "final_round": sim.round_number,
                "standings": final_standings,
                "playoff_bracket": playoff_bracket,
                "tiebreakers_needed": result_phase == "tiebreakers",
            }
            if suppress_spoiler_events:
                deferred_season_events.append(
                    ("season.regular_season_complete", season_event_data)
                )
            elif event_bus:
                await event_bus.publish(
                    "season.regular_season_complete", season_event_data
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
                except SQLAlchemyError:
                    logger.exception(
                        "playoff_bracket_failed_after_tiebreaker season=%s",
                        sim.season_id,
                    )

    elif season and season.status in ("regular_season_complete", "playoffs"):
        ruleset = RuleSet(**(season.current_ruleset or {}))
        try:
            playoffs_complete, finals_matchup, series_events = (
                await _advance_playoff_series(
                    repo,
                    sim.season_id,
                    ruleset,
                    sim.round_number,
                    event_bus=event_bus,
                    suppress_spoiler_events=suppress_spoiler_events,
                )
            )
            deferred_season_events.extend(series_events)

            # Generate series reports for any completed series
            if series_events:
                try:
                    series_report_events = await _generate_series_reports(
                        repo,
                        sim.season_id,
                        series_events,
                        sim.teams_cache,
                        api_key=api_key,
                    )
                    deferred_report_events.extend(series_report_events)
                except Exception:  # Last-resort handler — AI (Anthropic) and DB errors
                    logger.exception(
                        "series_reports_failed season=%s round=%d",
                        sim.season_id,
                        sim.round_number,
                    )
        except Exception:  # Last-resort handler — playoff logic, DB, and event-bus errors
            logger.exception(
                "playoff_series_advance_failed season=%s round=%d",
                sim.season_id,
                sim.round_number,
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

    # Compute aggregate drama level for the round
    round_drama_level = "routine"
    if sim.drama_annotations:
        all_levels = [
            a.level for anns in sim.drama_annotations.values() for a in anns
        ]
        if any(lv == "peak" for lv in all_levels):
            round_drama_level = "peak"
        elif any(lv == "high" for lv in all_levels):
            round_drama_level = "high"
        elif any(lv == "elevated" for lv in all_levels):
            round_drama_level = "elevated"

    round_completed_data: dict = {
        "round": sim.round_number,
        "games": len(sim.game_summaries),
        "reports": len(reports),
        "elapsed_ms": round(elapsed * 1000, 1),
        "drama_level": round_drama_level,
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
        deferred_season_events=deferred_season_events,
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
        deferred_season_events: list[tuple[str, dict]] | None = None,
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
        self.deferred_season_events = deferred_season_events or []


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
    narrative_context: NarrativeContext | None = None
    effects_summary: str = ""
    effect_registry: EffectRegistry | None = None
    meta_store_snapshot: dict[str, dict[str, dict[str, object]]] | None = None
    milestone_grants: list[dict] = dataclasses.field(default_factory=list)
    drama_annotations: dict[str, list[DramaAnnotation]] = dataclasses.field(
        default_factory=dict
    )  # game_id -> annotations
    # Pre-computed insight data (assembled in Phase 1 for Phase 2 AI calls)
    impact_validation_data: list[dict] = dataclasses.field(default_factory=list)
    governor_leverage_data: dict[str, dict] = dataclasses.field(
        default_factory=dict
    )  # gov_id -> leverage dict
    governor_behavioral_data: dict[str, dict] = dataclasses.field(
        default_factory=dict
    )  # gov_id -> profile dict
    insight_interval: int = 3  # generate leverage/behavioral every N rounds


@dataclasses.dataclass
class _AIPhaseResult:
    """AI-generated content (no DB access needed)."""

    commentaries: dict[str, str]  # game_id -> commentary text
    highlight_reel: str
    sim_report: Report
    gov_report: Report
    private_reports: list[tuple[str, Report]]  # (governor_id, report)
    impact_report: Report | None = None
    leverage_reports: list[tuple[str, Report]] = dataclasses.field(
        default_factory=list
    )  # (governor_id, report)
    behavioral_reports: list[tuple[str, Report]] = dataclasses.field(
        default_factory=list
    )  # (governor_id, report)

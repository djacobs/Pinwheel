"""New player onboarding -- league context data gathering.

Provides a structured snapshot of the current league state for use in
onboarding embeds and the /status command. All data comes from existing
repository queries -- no AI calls, no new DB methods.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pinwheel.core.scheduler import compute_standings
from pinwheel.core.season import SeasonPhase, normalize_phase

if TYPE_CHECKING:
    from pinwheel.db.repository import Repository

logger = logging.getLogger(__name__)

# Maximum number of active proposals to include in the league context.
MAX_ACTIVE_PROPOSALS = 5

# Maximum number of recent rule changes to surface.
MAX_RECENT_RULE_CHANGES = 3


@dataclass
class LeagueContext:
    """Structured snapshot of the current state of the league.

    Gathered from existing repository data. All fields are plain Python
    types (not ORM objects) so the data remains usable after the DB
    session closes.
    """

    season_name: str
    season_phase: SeasonPhase
    current_round: int
    total_rounds: int
    standings: list[dict[str, object]] = field(default_factory=list)
    active_proposals: list[dict[str, object]] = field(default_factory=list)
    active_proposals_total: int = 0
    recent_rule_changes: list[dict[str, object]] = field(default_factory=list)
    governor_count: int = 0
    team_governor_counts: dict[str, int] = field(default_factory=dict)
    governance_interval: int = 1
    games_played: int = 0


async def build_league_context(
    repo: Repository,
    season_id: str,
    season_name: str,
    season_status: str,
    governance_interval: int = 1,
) -> LeagueContext:
    """Gather a snapshot of the current league state.

    All data comes from existing repository queries. This function is
    safe to call from any context (Discord bot, web handler, tests)
    and does not make AI calls.

    Args:
        repo: An active Repository instance (session must be open).
        season_id: The season to gather context for.
        season_name: Display name for the season.
        season_status: Raw status string from the SeasonRow.
        governance_interval: Governance tally interval (from settings).

    Returns:
        A LeagueContext with all fields populated from the database.
    """
    phase = normalize_phase(season_status)

    # --- Standings ---
    games = await repo.get_all_games(season_id)
    game_dicts = [
        {
            "home_team_id": g.home_team_id,
            "away_team_id": g.away_team_id,
            "home_score": g.home_score,
            "away_score": g.away_score,
            "winner_team_id": g.winner_team_id,
        }
        for g in games
    ]
    standings = compute_standings(game_dicts)

    # Enrich standings with team names
    for s in standings:
        team = await repo.get_team(s["team_id"])
        if team:
            s["team_name"] = team.name

    # --- Current round and total rounds ---
    current_round = 0
    if games:
        current_round = max(g.round_number for g in games)

    total_rounds = 0
    schedule = await repo.get_full_schedule(season_id, phase="regular")
    if schedule:
        total_rounds = max(s.round_number for s in schedule)

    # --- Active proposals ---
    all_proposals = await repo.get_all_proposals(season_id)
    active_statuses = {"confirmed", "amended", "flagged_for_review"}
    active_proposals = [
        p for p in all_proposals if p.get("status") in active_statuses
    ]
    active_proposals_total = len(active_proposals)

    # Cap the list for display
    active_proposals_display = active_proposals[:MAX_ACTIVE_PROPOSALS]

    # --- Recent rule changes ---
    rule_events = await repo.get_events_by_type(
        season_id, ["rule.enacted"]
    )
    recent_rule_changes: list[dict[str, object]] = []
    for evt in rule_events[-MAX_RECENT_RULE_CHANGES:]:
        payload = evt.payload or {}
        recent_rule_changes.append(
            {
                "parameter": payload.get("parameter", "unknown"),
                "old_value": payload.get("old_value"),
                "new_value": payload.get("new_value"),
                "round_number": evt.round_number,
            }
        )

    # --- Governor counts ---
    governors = await repo.get_all_governors_for_season(season_id)
    governor_count = len(governors)

    governor_counts_raw = await repo.get_governor_counts_by_team(season_id)
    # Map team_id -> team_name for display
    team_governor_counts: dict[str, int] = {}
    teams = await repo.get_teams_for_season(season_id)
    team_name_map = {t.id: t.name for t in teams}
    for team_id, count in governor_counts_raw.items():
        name = team_name_map.get(team_id, team_id)
        team_governor_counts[name] = count

    return LeagueContext(
        season_name=season_name,
        season_phase=phase,
        current_round=current_round,
        total_rounds=total_rounds,
        standings=standings,
        active_proposals=active_proposals_display,
        active_proposals_total=active_proposals_total,
        recent_rule_changes=recent_rule_changes,
        governor_count=governor_count,
        team_governor_counts=team_governor_counts,
        governance_interval=governance_interval,
        games_played=len(games),
    )

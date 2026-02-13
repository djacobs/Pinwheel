"""Season management -- creating new seasons, carrying over teams and rules.

Supports the "Start Season 2" flow: after a season is archived, the admin
starts a fresh season with either default rules or carried-forward rules.
Teams, hoopers, and governor enrollments are carried over; tokens are regenerated.

Also handles season archiving: when a season is completed, archive_season()
creates a frozen snapshot capturing final standings, rule change history,
champion info, and aggregate statistics.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from pinwheel.core.scheduler import compute_standings, generate_round_robin
from pinwheel.core.tokens import regenerate_tokens
from pinwheel.db.models import SeasonArchiveRow
from pinwheel.models.rules import DEFAULT_RULESET, RuleSet

if TYPE_CHECKING:
    from pinwheel.db.models import SeasonRow
    from pinwheel.db.repository import Repository

logger = logging.getLogger(__name__)


async def start_new_season(
    repo: Repository,
    league_id: str,
    season_name: str,
    carry_forward_rules: bool = False,
    previous_season_id: str | None = None,
) -> SeasonRow:
    """Start a new season in the league.

    Args:
        repo: Database repository.
        league_id: The league to create the season in.
        season_name: Display name for the new season.
        carry_forward_rules: If True, use previous season's final ruleset.
                           If False, reset to defaults.
        previous_season_id: Season to carry rules from (uses latest completed if None).

    Returns:
        The newly created SeasonRow.
    """
    # 1. Verify the league exists
    league = await repo.get_league(league_id)
    if league is None:
        msg = f"League {league_id} not found"
        raise ValueError(msg)

    # 2. Determine the ruleset
    ruleset_data: dict | None = None
    source_season_id: str | None = previous_season_id

    if carry_forward_rules:
        if source_season_id is not None:
            # Use the specified previous season's ruleset
            prev_season = await repo.get_season(source_season_id)
            if prev_season is None:
                msg = f"Previous season {source_season_id} not found"
                raise ValueError(msg)
            ruleset_data = prev_season.current_ruleset
        else:
            # Find the latest completed season in this league
            prev_season = await repo.get_latest_completed_season(league_id)
            if prev_season is not None:
                ruleset_data = prev_season.current_ruleset
                source_season_id = prev_season.id

    if ruleset_data is None:
        ruleset_data = DEFAULT_RULESET.model_dump()

    # 3. Create the new season
    new_season = await repo.create_season(
        league_id=league_id,
        name=season_name,
        starting_ruleset=ruleset_data,
    )

    # 4. Carry over teams if there is a source season
    if source_season_id is None:
        # Try to find any previous season to carry teams from
        prev = await repo.get_latest_completed_season(league_id)
        if prev is not None:
            source_season_id = prev.id

    if source_season_id is not None:
        await carry_over_teams(repo, source_season_id, new_season.id)

    # 5. Generate schedule for new season
    teams = await repo.get_teams_for_season(new_season.id)
    if len(teams) >= 2:
        team_ids = [t.id for t in teams]
        ruleset = RuleSet(**ruleset_data)
        matchups = generate_round_robin(
            team_ids,
            num_cycles=ruleset.round_robins_per_season,
        )
        for m in matchups:
            await repo.create_schedule_entry(
                season_id=new_season.id,
                round_number=m.round_number,
                matchup_index=m.matchup_index,
                home_team_id=m.home_team_id,
                away_team_id=m.away_team_id,
                phase=m.phase,
            )

    # 6. Regenerate tokens for all governors
    await regenerate_all_governor_tokens(repo, new_season.id)

    # 7. Mark season as active
    new_season.status = "active"
    await repo.session.flush()

    logger.info(
        "new_season_created season_id=%s name=%s league_id=%s carry_rules=%s",
        new_season.id,
        season_name,
        league_id,
        carry_forward_rules,
    )

    return new_season


async def carry_over_teams(
    repo: Repository,
    from_season_id: str,
    to_season_id: str,
) -> list[str]:
    """Copy teams and their hoopers from one season to the next.

    Creates new team records linked to the new season.
    Hoopers get fresh stats but keep names, archetypes, attributes.
    Governor enrollments are carried over.

    Returns:
        List of new team IDs created in the target season.
    """
    old_teams = await repo.get_teams_for_season(from_season_id)
    new_team_ids: list[str] = []

    for old_team in old_teams:
        # Create new team for the new season
        new_team = await repo.create_team(
            season_id=to_season_id,
            name=old_team.name,
            color=old_team.color,
            color_secondary=old_team.color_secondary,
            motto=old_team.motto,
            venue=old_team.venue,
        )
        new_team_ids.append(new_team.id)

        # Copy hoopers with fresh records (same name, archetype, attributes)
        for hooper in old_team.hoopers:
            await repo.create_hooper(
                team_id=new_team.id,
                season_id=to_season_id,
                name=hooper.name,
                archetype=hooper.archetype,
                attributes=hooper.attributes,
                moves=hooper.moves,
                is_active=hooper.is_active,
            )

        # Carry over governor enrollments
        governors = await repo.get_governors_for_team(
            old_team.id,
            from_season_id,
        )
        for governor in governors:
            await repo.enroll_player(governor.id, new_team.id, to_season_id)

    logger.info(
        "teams_carried_over from=%s to=%s team_count=%d",
        from_season_id,
        to_season_id,
        len(new_team_ids),
    )

    return new_team_ids


async def regenerate_all_governor_tokens(
    repo: Repository,
    season_id: str,
) -> int:
    """Regenerate tokens for all governors enrolled in a season.

    Returns:
        Number of governors who received tokens.
    """
    teams = await repo.get_teams_for_season(season_id)
    governor_count = 0

    for team in teams:
        governors = await repo.get_governors_for_team(team.id, season_id)
        for governor in governors:
            await regenerate_tokens(
                repo=repo,
                governor_id=governor.id,
                team_id=team.id,
                season_id=season_id,
            )
            governor_count += 1

    logger.info(
        "tokens_regenerated season_id=%s governor_count=%d",
        season_id,
        governor_count,
    )

    return governor_count


async def archive_season(repo: Repository, season_id: str) -> SeasonArchiveRow:
    """Create an archive snapshot of a completed season.

    Gathers final standings, rule change history, game counts, proposal
    counts, and governor participation into an immutable archive row.
    Marks the season as completed.

    Args:
        repo: Repository bound to an active session.
        season_id: The season to archive.

    Returns:
        The created SeasonArchiveRow.

    Raises:
        ValueError: If the season does not exist.
    """
    season = await repo.get_season(season_id)
    if not season:
        raise ValueError(f"Season {season_id} not found")

    # Gather all game results and compute standings
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

    # Determine champion (top of standings)
    champion_team_id = None
    champion_team_name = None
    if standings:
        champion_team_id = standings[0]["team_id"]
        champion_team_name = standings[0].get("team_name", str(champion_team_id))

    # Get rule change history from events
    rule_events = await repo.get_events_by_type(season_id=season_id, event_types=["rule.enacted"])
    rule_changes = [e.payload for e in rule_events]

    # Count proposals
    proposal_events = await repo.get_events_by_type(
        season_id=season_id, event_types=["proposal.submitted"]
    )

    # Count governors
    governors = await repo.get_all_governors_for_season(season_id)

    # Mark season complete
    await repo.update_season_status(season_id, "completed")
    season_row = await repo.get_season_row(season_id)
    if season_row:
        season_row.completed_at = datetime.now(UTC)

    # Create archive
    archive = SeasonArchiveRow(
        season_id=season_id,
        season_name=season.name,
        final_standings=standings,
        final_ruleset=season.current_ruleset or {},
        rule_change_history=rule_changes,
        champion_team_id=champion_team_id,
        champion_team_name=champion_team_name,
        total_games=len(games),
        total_proposals=len(proposal_events),
        total_rule_changes=len(rule_changes),
        governor_count=len(governors),
    )
    await repo.store_season_archive(archive)

    logger.info(
        "season_archived season=%s games=%d proposals=%d rule_changes=%d governors=%d",
        season_id,
        len(games),
        len(proposal_events),
        len(rule_changes),
        len(governors),
    )

    return archive

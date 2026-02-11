"""Repository pattern for database access.

Wraps SQLAlchemy async sessions. Governance events are append-only.
Game results and box scores are stored directly (immutable outputs).
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from pinwheel.db.models import (
    AgentRow,
    BoxScoreRow,
    GameResultRow,
    GovernanceEventRow,
    LeagueRow,
    ScheduleRow,
    SeasonRow,
    TeamRow,
)


class Repository:
    """Async repository for all database operations."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # --- League / Season ---

    async def create_league(self, name: str) -> LeagueRow:
        row = LeagueRow(name=name)
        self.session.add(row)
        await self.session.flush()
        return row

    async def create_season(
        self,
        league_id: str,
        name: str,
        starting_ruleset: dict | None = None,
    ) -> SeasonRow:
        row = SeasonRow(
            league_id=league_id,
            name=name,
            starting_ruleset=starting_ruleset,
            current_ruleset=starting_ruleset,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def get_season(self, season_id: str) -> SeasonRow | None:
        return await self.session.get(SeasonRow, season_id)

    # --- Teams / Agents ---

    async def create_team(
        self,
        season_id: str,
        name: str,
        color: str = "#000000",
        motto: str = "",
        venue: dict | None = None,
    ) -> TeamRow:
        row = TeamRow(
            season_id=season_id,
            name=name,
            color=color,
            motto=motto,
            venue=venue,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def get_team(self, team_id: str) -> TeamRow | None:
        stmt = select(TeamRow).where(TeamRow.id == team_id).options(selectinload(TeamRow.agents))
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_teams_for_season(self, season_id: str) -> list[TeamRow]:
        stmt = (
            select(TeamRow)
            .where(TeamRow.season_id == season_id)
            .options(selectinload(TeamRow.agents))
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def create_agent(
        self,
        team_id: str,
        season_id: str,
        name: str,
        archetype: str,
        attributes: dict,
        moves: list | None = None,
        is_active: bool = True,
    ) -> AgentRow:
        row = AgentRow(
            team_id=team_id,
            season_id=season_id,
            name=name,
            archetype=archetype,
            attributes=attributes,
            moves=moves or [],
            is_active=is_active,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def get_agent(self, agent_id: str) -> AgentRow | None:
        return await self.session.get(AgentRow, agent_id)

    # --- Game Results ---

    async def store_game_result(
        self,
        season_id: str,
        round_number: int,
        matchup_index: int,
        home_team_id: str,
        away_team_id: str,
        home_score: int,
        away_score: int,
        winner_team_id: str,
        seed: int,
        total_possessions: int,
        ruleset_snapshot: dict | None = None,
        quarter_scores: list | None = None,
        elam_target: int | None = None,
        play_by_play: list | None = None,
    ) -> GameResultRow:
        row = GameResultRow(
            season_id=season_id,
            round_number=round_number,
            matchup_index=matchup_index,
            home_team_id=home_team_id,
            away_team_id=away_team_id,
            home_score=home_score,
            away_score=away_score,
            winner_team_id=winner_team_id,
            seed=seed,
            total_possessions=total_possessions,
            ruleset_snapshot=ruleset_snapshot,
            quarter_scores=quarter_scores,
            elam_target=elam_target,
            play_by_play=play_by_play,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def store_box_score(
        self,
        game_id: str,
        agent_id: str,
        team_id: str,
        **stats: int | float,
    ) -> BoxScoreRow:
        row = BoxScoreRow(game_id=game_id, agent_id=agent_id, team_id=team_id, **stats)
        self.session.add(row)
        await self.session.flush()
        return row

    async def get_game_result(self, game_id: str) -> GameResultRow | None:
        stmt = (
            select(GameResultRow)
            .where(GameResultRow.id == game_id)
            .options(selectinload(GameResultRow.box_scores))
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_games_for_round(self, season_id: str, round_number: int) -> list[GameResultRow]:
        stmt = (
            select(GameResultRow)
            .where(
                GameResultRow.season_id == season_id,
                GameResultRow.round_number == round_number,
            )
            .options(selectinload(GameResultRow.box_scores))
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    # --- Governance Events (append-only) ---

    async def append_event(
        self,
        event_type: str,
        aggregate_id: str,
        aggregate_type: str,
        season_id: str,
        payload: dict,
        round_number: int | None = None,
        governor_id: str | None = None,
        team_id: str | None = None,
    ) -> GovernanceEventRow:
        # Atomic sequence assignment: SELECT FOR UPDATE prevents concurrent
        # writers from getting the same sequence number on PostgreSQL.
        # SQLite ignores FOR UPDATE (single-writer is inherently safe).
        stmt = select(
            func.coalesce(func.max(GovernanceEventRow.sequence_number), 0)
        ).with_for_update()
        result = await self.session.execute(stmt)
        seq = result.scalar_one() + 1

        row = GovernanceEventRow(
            event_type=event_type,
            aggregate_id=aggregate_id,
            aggregate_type=aggregate_type,
            season_id=season_id,
            payload=payload,
            round_number=round_number,
            governor_id=governor_id,
            team_id=team_id,
            sequence_number=seq,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def get_events_for_aggregate(
        self, aggregate_type: str, aggregate_id: str
    ) -> list[GovernanceEventRow]:
        stmt = (
            select(GovernanceEventRow)
            .where(
                GovernanceEventRow.aggregate_type == aggregate_type,
                GovernanceEventRow.aggregate_id == aggregate_id,
            )
            .order_by(GovernanceEventRow.sequence_number)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_events_by_type_and_governor(
        self,
        season_id: str,
        governor_id: str,
        event_types: list[str],
    ) -> list[GovernanceEventRow]:
        """Get events of specific types for a governor in a season."""
        stmt = (
            select(GovernanceEventRow)
            .where(
                GovernanceEventRow.season_id == season_id,
                GovernanceEventRow.governor_id == governor_id,
                GovernanceEventRow.event_type.in_(event_types),
            )
            .order_by(GovernanceEventRow.sequence_number)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_events_by_type(
        self,
        season_id: str,
        event_types: list[str],
    ) -> list[GovernanceEventRow]:
        """Get all events of specific types in a season."""
        stmt = (
            select(GovernanceEventRow)
            .where(
                GovernanceEventRow.season_id == season_id,
                GovernanceEventRow.event_type.in_(event_types),
            )
            .order_by(GovernanceEventRow.sequence_number)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def update_season_ruleset(self, season_id: str, ruleset_data: dict) -> None:
        """Update the cached current_ruleset on a season."""
        season = await self.get_season(season_id)
        if season:
            season.current_ruleset = ruleset_data
            await self.session.flush()

    # --- Schedule ---

    async def create_schedule_entry(
        self,
        season_id: str,
        round_number: int,
        matchup_index: int,
        home_team_id: str,
        away_team_id: str,
        phase: str = "regular",
    ) -> ScheduleRow:
        row = ScheduleRow(
            season_id=season_id,
            round_number=round_number,
            matchup_index=matchup_index,
            home_team_id=home_team_id,
            away_team_id=away_team_id,
            phase=phase,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def get_schedule_for_round(self, season_id: str, round_number: int) -> list[ScheduleRow]:
        stmt = (
            select(ScheduleRow)
            .where(
                ScheduleRow.season_id == season_id,
                ScheduleRow.round_number == round_number,
            )
            .order_by(ScheduleRow.matchup_index)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

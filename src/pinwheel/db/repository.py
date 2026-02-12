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
    BotStateRow,
    BoxScoreRow,
    EvalResultRow,
    GameResultRow,
    GovernanceEventRow,
    LeagueRow,
    MirrorRow,
    PlayerRow,
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

    # --- Mirrors ---

    async def store_mirror(
        self,
        season_id: str,
        mirror_type: str,
        round_number: int,
        content: str,
        team_id: str = "",
        governor_id: str = "",
        metadata_json: dict | None = None,
    ) -> MirrorRow:
        row = MirrorRow(
            season_id=season_id,
            mirror_type=mirror_type,
            round_number=round_number,
            content=content,
            team_id=team_id,
            governor_id=governor_id,
            metadata_json=metadata_json,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def get_mirrors_for_round(
        self,
        season_id: str,
        round_number: int,
        mirror_type: str | None = None,
    ) -> list[MirrorRow]:
        stmt = select(MirrorRow).where(
            MirrorRow.season_id == season_id,
            MirrorRow.round_number == round_number,
        )
        if mirror_type:
            stmt = stmt.where(MirrorRow.mirror_type == mirror_type)
        stmt = stmt.order_by(MirrorRow.created_at)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_private_mirrors(
        self,
        season_id: str,
        governor_id: str,
        round_number: int | None = None,
    ) -> list[MirrorRow]:
        """Get private mirrors for a specific governor."""
        stmt = select(MirrorRow).where(
            MirrorRow.season_id == season_id,
            MirrorRow.governor_id == governor_id,
            MirrorRow.mirror_type == "private",
        )
        if round_number is not None:
            stmt = stmt.where(MirrorRow.round_number == round_number)
        stmt = stmt.order_by(MirrorRow.created_at.desc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_latest_mirror(
        self,
        season_id: str,
        mirror_type: str,
    ) -> MirrorRow | None:
        stmt = (
            select(MirrorRow)
            .where(
                MirrorRow.season_id == season_id,
                MirrorRow.mirror_type == mirror_type,
            )
            .order_by(MirrorRow.created_at.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_all_game_results_for_season(self, season_id: str) -> list[GameResultRow]:
        """Get all game results for a season, ordered by round."""
        stmt = (
            select(GameResultRow)
            .where(GameResultRow.season_id == season_id)
            .options(selectinload(GameResultRow.box_scores))
            .order_by(GameResultRow.round_number, GameResultRow.matchup_index)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    # --- Players (Discord OAuth) ---

    async def get_player_by_discord_id(self, discord_id: str) -> PlayerRow | None:
        """Look up a player by their Discord user ID."""
        stmt = select(PlayerRow).where(PlayerRow.discord_id == discord_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_or_create_player(
        self,
        discord_id: str,
        username: str,
        avatar_url: str = "",
    ) -> PlayerRow:
        """Find an existing player by Discord ID, or create a new one.

        If the player already exists, update their username, avatar, and
        last_login timestamp.
        """
        from datetime import UTC, datetime

        player = await self.get_player_by_discord_id(discord_id)
        if player is not None:
            player.username = username
            player.avatar_url = avatar_url
            player.last_login = datetime.now(UTC)
            await self.session.flush()
            return player

        player = PlayerRow(
            discord_id=discord_id,
            username=username,
            avatar_url=avatar_url,
        )
        self.session.add(player)
        await self.session.flush()
        return player

    # --- Player Enrollment ---

    async def enroll_player(
        self, player_id: str, team_id: str, season_id: str
    ) -> PlayerRow:
        """Set a player's team enrollment for a season.

        Raises ValueError if the player is already enrolled on a different
        team this season (season-lock).
        """
        player = await self.session.get(PlayerRow, player_id)
        if player is None:
            msg = f"Player {player_id} not found"
            raise ValueError(msg)

        if (
            player.enrolled_season_id == season_id
            and player.team_id is not None
            and player.team_id != team_id
        ):
            msg = f"Player already enrolled on team {player.team_id} for season {season_id}"
            raise ValueError(msg)

        player.team_id = team_id
        player.enrolled_season_id = season_id
        await self.session.flush()
        return player

    async def get_players_for_team(self, team_id: str) -> list[PlayerRow]:
        """Return all players enrolled on a team."""
        stmt = select(PlayerRow).where(PlayerRow.team_id == team_id)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def swap_agent_team(self, agent_id: str, new_team_id: str) -> None:
        """Move an agent to a different team."""
        from pinwheel.db.models import AgentRow

        agent = await self.session.get(AgentRow, agent_id)
        if agent is None:
            msg = f"Agent {agent_id} not found"
            raise ValueError(msg)
        agent.team_id = new_team_id
        await self.session.flush()

    async def get_governors_for_team(
        self, team_id: str, season_id: str,
    ) -> list[PlayerRow]:
        """Return all enrolled governors on a team for a given season."""
        stmt = select(PlayerRow).where(
            PlayerRow.team_id == team_id,
            PlayerRow.enrolled_season_id == season_id,
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_governor_counts_by_team(self, season_id: str) -> dict[str, int]:
        """Return {team_id: governor_count} for all teams in a season."""
        from sqlalchemy import func as sa_func

        stmt = (
            select(PlayerRow.team_id, sa_func.count(PlayerRow.id))
            .where(
                PlayerRow.enrolled_season_id == season_id,
                PlayerRow.team_id.isnot(None),
            )
            .group_by(PlayerRow.team_id)
        )
        result = await self.session.execute(stmt)
        return dict(result.all())

    async def get_player_enrollment(
        self, discord_id: str, season_id: str
    ) -> tuple[str, str] | None:
        """Return (team_id, team_name) if the player is enrolled this season, else None."""
        stmt = select(PlayerRow).where(
            PlayerRow.discord_id == discord_id,
            PlayerRow.enrolled_season_id == season_id,
            PlayerRow.team_id.isnot(None),
        )
        result = await self.session.execute(stmt)
        player = result.scalar_one_or_none()
        if player is None or player.team_id is None:
            return None

        team = await self.get_team(player.team_id)
        team_name = team.name if team else player.team_id
        return (player.team_id, team_name)

    # --- Agent Box Scores & League Averages ---

    async def get_box_scores_for_agent(
        self, agent_id: str
    ) -> list[tuple[BoxScoreRow, GameResultRow]]:
        """Get all box scores for an agent joined with their game results.

        Returns list of (BoxScoreRow, GameResultRow) tuples ordered by round_number.
        """
        stmt = (
            select(BoxScoreRow, GameResultRow)
            .join(GameResultRow, BoxScoreRow.game_id == GameResultRow.id)
            .where(BoxScoreRow.agent_id == agent_id)
            .order_by(GameResultRow.round_number)
        )
        result = await self.session.execute(stmt)
        return list(result.tuples().all())

    async def get_league_attribute_averages(self, season_id: str) -> dict[str, float]:
        """Average each of the 9 attributes across all agents in a season."""
        stmt = select(AgentRow.attributes).where(
            AgentRow.season_id == season_id,
            AgentRow.is_active.is_(True),
        )
        result = await self.session.execute(stmt)
        all_attrs = list(result.scalars().all())

        if not all_attrs:
            return {}

        from pinwheel.api.charts import ATTRIBUTE_ORDER

        totals: dict[str, float] = {a: 0.0 for a in ATTRIBUTE_ORDER}
        count = len(all_attrs)
        for attrs in all_attrs:
            for a in ATTRIBUTE_ORDER:
                totals[a] += float(attrs.get(a, 0))

        return {a: round(totals[a] / count, 1) for a in ATTRIBUTE_ORDER}

    async def update_agent_backstory(self, agent_id: str, backstory: str) -> AgentRow | None:
        """Update an agent's backstory text."""
        agent = await self.get_agent(agent_id)
        if agent:
            agent.backstory = backstory
            await self.session.flush()
        return agent

    # --- Eval Results ---

    async def store_eval_result(
        self,
        season_id: str,
        round_number: int,
        eval_type: str,
        score: float = 0.0,
        eval_subtype: str = "",
        details_json: dict | None = None,
    ) -> EvalResultRow:
        """Store an eval result. Never stores private mirror content."""
        row = EvalResultRow(
            season_id=season_id,
            round_number=round_number,
            eval_type=eval_type,
            eval_subtype=eval_subtype,
            score=score,
            details_json=details_json,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def get_eval_results(
        self,
        season_id: str,
        eval_type: str | None = None,
        round_number: int | None = None,
    ) -> list[EvalResultRow]:
        """Get eval results, optionally filtered by type and round."""
        stmt = select(EvalResultRow).where(EvalResultRow.season_id == season_id)
        if eval_type:
            stmt = stmt.where(EvalResultRow.eval_type == eval_type)
        if round_number is not None:
            stmt = stmt.where(EvalResultRow.round_number == round_number)
        stmt = stmt.order_by(EvalResultRow.created_at.desc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    # --- Bot State (key-value persistence) ---

    async def get_bot_state(self, key: str) -> str | None:
        """Retrieve a bot state value by key, or None if not set."""
        row = await self.session.get(BotStateRow, key)
        return row.value if row else None

    async def set_bot_state(self, key: str, value: str) -> None:
        """Upsert a bot state key-value pair."""
        row = await self.session.get(BotStateRow, key)
        if row is not None:
            row.value = value
        else:
            row = BotStateRow(key=key, value=value)
            self.session.add(row)
        await self.session.flush()

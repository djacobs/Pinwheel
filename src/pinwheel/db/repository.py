"""Repository pattern for database access.

Wraps SQLAlchemy async sessions. Governance events are append-only.
Game results and box scores are stored directly (immutable outputs).
"""

from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from pinwheel.db.models import (
    BotStateRow,
    BoxScoreRow,
    EvalResultRow,
    GameResultRow,
    GovernanceEventRow,
    HooperRow,
    LeagueRow,
    MirrorRow,
    PlayerRow,
    ScheduleRow,
    SeasonArchiveRow,
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

    async def get_league(self, league_id: str) -> LeagueRow | None:
        """Get a league by ID."""
        return await self.session.get(LeagueRow, league_id)

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

    async def get_latest_completed_season(self, league_id: str) -> SeasonRow | None:
        """Get the most recently completed season in a league."""
        stmt = (
            select(SeasonRow)
            .where(
                SeasonRow.league_id == league_id,
                SeasonRow.status.in_(["completed", "archived"]),
            )
            .order_by(SeasonRow.completed_at.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_players_for_season(self, season_id: str) -> list[PlayerRow]:
        """Return all players enrolled in a season."""
        stmt = select(PlayerRow).where(
            PlayerRow.enrolled_season_id == season_id,
            PlayerRow.team_id.isnot(None),
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    # --- Teams / Agents ---

    async def create_team(
        self,
        season_id: str,
        name: str,
        color: str = "#000000",
        color_secondary: str = "#ffffff",
        motto: str = "",
        venue: dict | None = None,
    ) -> TeamRow:
        row = TeamRow(
            season_id=season_id,
            name=name,
            color=color,
            color_secondary=color_secondary,
            motto=motto,
            venue=venue,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def get_team(self, team_id: str) -> TeamRow | None:
        stmt = select(TeamRow).where(TeamRow.id == team_id).options(selectinload(TeamRow.hoopers))
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_teams_for_season(self, season_id: str) -> list[TeamRow]:
        stmt = (
            select(TeamRow)
            .where(TeamRow.season_id == season_id)
            .options(selectinload(TeamRow.hoopers))
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def create_hooper(
        self,
        team_id: str,
        season_id: str,
        name: str,
        archetype: str,
        attributes: dict,
        moves: list | None = None,
        is_active: bool = True,
    ) -> HooperRow:
        row = HooperRow(
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

    # Backward-compatible alias
    create_agent = create_hooper

    async def get_hooper(self, hooper_id: str) -> HooperRow | None:
        return await self.session.get(HooperRow, hooper_id)

    # Backward-compatible alias
    async def get_agent(self, agent_id: str) -> HooperRow | None:
        return await self.get_hooper(agent_id)

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
        hooper_id: str = "",
        team_id: str = "",
        agent_id: str = "",
        **stats: int | float,
    ) -> BoxScoreRow:
        # Support both hooper_id and legacy agent_id parameter
        _hooper_id = hooper_id or agent_id
        row = BoxScoreRow(game_id=game_id, hooper_id=_hooper_id, team_id=team_id, **stats)
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

    async def get_games_for_round(
        self, season_id: str, round_number: int, *, presented_only: bool = False,
    ) -> list[GameResultRow]:
        stmt = (
            select(GameResultRow)
            .where(
                GameResultRow.season_id == season_id,
                GameResultRow.round_number == round_number,
            )
            .options(selectinload(GameResultRow.box_scores))
        )
        if presented_only:
            stmt = stmt.where(
                or_(
                    GameResultRow.presented.is_(True),
                    GameResultRow.presented.is_(None),
                )
            )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def mark_game_presented(self, game_id: str) -> None:
        """Mark a game result as presented (visible to players)."""
        game = await self.session.get(GameResultRow, game_id)
        if game:
            game.presented = True
            await self.session.flush()

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

    async def get_events_by_governor(
        self,
        season_id: str,
        governor_id: str,
    ) -> list[GovernanceEventRow]:
        """Get all governance events for a governor in a season."""
        stmt = (
            select(GovernanceEventRow)
            .where(
                GovernanceEventRow.season_id == season_id,
                GovernanceEventRow.governor_id == governor_id,
            )
            .order_by(GovernanceEventRow.sequence_number)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_governor_activity(self, governor_id: str, season_id: str) -> dict:
        """Get a governor's governance activity summary.

        Returns a dict with:
        - proposals_submitted: int
        - proposals_passed: int
        - proposals_failed: int
        - votes_cast: int
        - proposal_list: list of dicts with proposal details + outcomes
        - token_balance: TokenBalance (propose, amend, boost)
        """
        from pinwheel.core.tokens import get_token_balance

        # Get proposals submitted by this governor
        submitted_events = await self.get_events_by_type_and_governor(
            season_id=season_id,
            governor_id=governor_id,
            event_types=["proposal.submitted"],
        )

        # Get votes cast by this governor
        vote_events = await self.get_events_by_type_and_governor(
            season_id=season_id,
            governor_id=governor_id,
            event_types=["vote.cast"],
        )

        # Get all outcome events to determine pass/fail for proposals
        outcome_events = await self.get_events_by_type(
            season_id=season_id,
            event_types=["proposal.passed", "proposal.failed"],
        )
        outcomes: dict[str, str] = {}
        for e in outcome_events:
            pid = e.payload.get("proposal_id", e.aggregate_id)
            outcomes[pid] = "passed" if e.event_type == "proposal.passed" else "failed"

        # Get confirmed proposals to identify pending ones
        confirmed_events = await self.get_events_by_type(
            season_id=season_id,
            event_types=["proposal.confirmed"],
        )
        confirmed_ids: set[str] = set()
        for e in confirmed_events:
            pid = e.payload.get("proposal_id", e.aggregate_id)
            confirmed_ids.add(pid)

        # Build proposal list with outcomes
        proposals_passed = 0
        proposals_failed = 0
        proposal_list: list[dict] = []

        for evt in submitted_events:
            p_data = evt.payload
            if "id" not in p_data or "raw_text" not in p_data:
                continue
            pid = p_data["id"]
            status = outcomes.get(pid, "pending")
            if pid in confirmed_ids and pid not in outcomes:
                status = "confirmed"
            if status == "passed":
                proposals_passed += 1
            elif status == "failed":
                proposals_failed += 1

            interp = p_data.get("interpretation")
            parameter = None
            if interp and isinstance(interp, dict):
                parameter = interp.get("parameter")

            proposal_list.append({
                "id": pid,
                "raw_text": p_data.get("raw_text", ""),
                "status": status,
                "parameter": parameter,
                "round_number": evt.round_number,
                "tier": p_data.get("tier", 1),
            })

        balance = await get_token_balance(self, governor_id, season_id)

        return {
            "proposals_submitted": len(proposal_list),
            "proposals_passed": proposals_passed,
            "proposals_failed": proposals_failed,
            "votes_cast": len(vote_events),
            "proposal_list": proposal_list,
            "token_balance": balance,
        }

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

    async def get_full_schedule(
        self, season_id: str, phase: str | None = None,
    ) -> list[ScheduleRow]:
        """Get all schedule entries for a season, optionally filtered by phase.

        Args:
            season_id: The season to query.
            phase: Filter by phase (e.g. "regular", "playoff"). None returns all.

        Returns:
            Schedule entries ordered by round_number and matchup_index.
        """
        stmt = select(ScheduleRow).where(ScheduleRow.season_id == season_id)
        if phase:
            stmt = stmt.where(ScheduleRow.phase == phase)
        stmt = stmt.order_by(ScheduleRow.round_number, ScheduleRow.matchup_index)
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

    async def get_player(self, player_id: str) -> PlayerRow | None:
        """Look up a player by their internal ID."""
        return await self.session.get(PlayerRow, player_id)

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

    async def swap_hooper_team(self, hooper_id: str, new_team_id: str) -> None:
        """Move a hooper to a different team."""
        hooper = await self.session.get(HooperRow, hooper_id)
        if hooper is None:
            msg = f"Hooper {hooper_id} not found"
            raise ValueError(msg)
        hooper.team_id = new_team_id
        await self.session.flush()

    # Backward-compatible alias
    swap_agent_team = swap_hooper_team

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

    # --- Hooper Box Scores & League Averages ---

    async def get_box_scores_for_hooper(
        self, hooper_id: str
    ) -> list[tuple[BoxScoreRow, GameResultRow]]:
        """Get all box scores for a hooper joined with their game results.

        Returns list of (BoxScoreRow, GameResultRow) tuples ordered by round_number.
        """
        stmt = (
            select(BoxScoreRow, GameResultRow)
            .join(GameResultRow, BoxScoreRow.game_id == GameResultRow.id)
            .where(BoxScoreRow.hooper_id == hooper_id)
            .order_by(GameResultRow.round_number)
        )
        result = await self.session.execute(stmt)
        return list(result.tuples().all())

    # Backward-compatible alias
    get_box_scores_for_agent = get_box_scores_for_hooper

    async def get_league_attribute_averages(self, season_id: str) -> dict[str, float]:
        """Average each of the 9 attributes across all hoopers in a season."""
        stmt = select(HooperRow.attributes).where(
            HooperRow.season_id == season_id,
            HooperRow.is_active.is_(True),
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

    async def update_hooper_backstory(self, hooper_id: str, backstory: str) -> HooperRow | None:
        """Update a hooper's backstory text."""
        hooper = await self.get_hooper(hooper_id)
        if hooper:
            hooper.backstory = backstory
            await self.session.flush()
        return hooper

    # Backward-compatible alias
    update_agent_backstory = update_hooper_backstory

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

    # --- Season Archives ---

    async def get_all_games(self, season_id: str) -> list[GameResultRow]:
        """Get all game results for a season."""
        return await self.get_all_game_results_for_season(season_id)

    async def get_all_governors_for_season(self, season_id: str) -> list[PlayerRow]:
        """Return all players enrolled in a season (regardless of team)."""
        stmt = select(PlayerRow).where(
            PlayerRow.enrolled_season_id == season_id,
            PlayerRow.team_id.isnot(None),
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def update_season_status(self, season_id: str, status: str) -> None:
        """Update season status (setup, active, regular_season_complete, playoffs, completed)."""
        season = await self.get_season(season_id)
        if season:
            season.status = status
            if status == "completed":
                from datetime import UTC, datetime

                season.completed_at = datetime.now(UTC)
            await self.session.flush()

    async def get_season_row(self, season_id: str) -> SeasonRow | None:
        """Get the raw season row. Alias for get_season."""
        return await self.get_season(season_id)

    async def store_season_archive(self, archive: SeasonArchiveRow) -> SeasonArchiveRow:
        """Persist a season archive row."""
        self.session.add(archive)
        await self.session.flush()
        return archive

    async def get_season_archive(self, season_id: str) -> SeasonArchiveRow | None:
        """Retrieve the archive for a given season."""
        stmt = select(SeasonArchiveRow).where(
            SeasonArchiveRow.season_id == season_id,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_all_archives(self) -> list[SeasonArchiveRow]:
        """List all archived seasons, newest first."""
        stmt = (
            select(SeasonArchiveRow)
            .order_by(SeasonArchiveRow.created_at.desc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

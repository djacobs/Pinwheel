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
    PlayerRow,
    ReportRow,
    ScheduleRow,
    SeasonArchiveRow,
    SeasonRow,
    TeamRow,
)
from pinwheel.models.tokens import TokenBalance


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

    async def get_active_season(self) -> SeasonRow | None:
        """Get the current active season (most recent non-terminal).

        Returns the season with the most recent ``created_at`` whose status
        is *not* a terminal state (``completed``, ``complete``, ``archived``,
        or ``setup``).  Falls back to the most recently created season of
        any status if no active one exists.

        Includes all lifecycle phases: active, playoffs, championship,
        offseason, tiebreaker_check, tiebreakers, regular_season_complete.
        """
        stmt = (
            select(SeasonRow)
            .where(
                SeasonRow.status.not_in(
                    ["completed", "complete", "archived", "setup"]
                )
            )
            .order_by(SeasonRow.created_at.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        row = result.scalar_one_or_none()
        if row is not None:
            return row
        # Fallback: return the most recent season of any status
        stmt = select(SeasonRow).order_by(SeasonRow.created_at.desc()).limit(1)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

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

    async def get_all_seasons(self) -> list[SeasonRow]:
        """Return all seasons, most recent first."""
        stmt = select(SeasonRow).order_by(SeasonRow.created_at.desc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_all_players(self) -> list[PlayerRow]:
        """Return all players regardless of season or team."""
        stmt = select(PlayerRow)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

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
        backstory: str = "",
    ) -> HooperRow:
        row = HooperRow(
            team_id=team_id,
            season_id=season_id,
            name=name,
            archetype=archetype,
            attributes=attributes,
            moves=moves or [],
            is_active=is_active,
            backstory=backstory,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def get_hooper(self, hooper_id: str) -> HooperRow | None:
        return await self.session.get(HooperRow, hooper_id)

    async def get_hoopers_by_name(self, name: str) -> list[HooperRow]:
        """Return all hooper records across all seasons with this exact name.

        Since carry_over_teams creates a new HooperRow (new ID) each season,
        name is the only stable identifier linking a player across seasons.
        """
        stmt = select(HooperRow).where(HooperRow.name == name)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_hoopers_for_team(self, team_id: str) -> list[HooperRow]:
        """Return all hoopers currently assigned to a team."""
        stmt = select(HooperRow).where(HooperRow.team_id == team_id)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

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
        phase: str | None = None,
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
            phase=phase,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def store_box_score(
        self,
        game_id: str,
        hooper_id: str = "",
        team_id: str = "",
        **stats: int | float,
    ) -> BoxScoreRow:
        row = BoxScoreRow(game_id=game_id, hooper_id=hooper_id, team_id=team_id, **stats)
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
        self,
        season_id: str,
        round_number: int,
        *,
        presented_only: bool = False,
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

    async def get_latest_round_number(self, season_id: str) -> int | None:
        """Get the highest round number with completed game results.

        Returns None if no games have been played yet.  This is O(1) via
        ``SELECT MAX(round_number)`` — use it instead of scanning rounds
        in a loop.
        """
        stmt = select(func.max(GameResultRow.round_number)).where(
            GameResultRow.season_id == season_id,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def mark_game_presented(self, game_id: str) -> None:
        """Mark a game result as presented (visible to players)."""
        game = await self.session.get(GameResultRow, game_id)
        if game:
            game.presented = True
            await self.session.flush()

    async def get_game_stats_for_rounds(
        self,
        season_id: str,
        round_start: int,
        round_end: int,
    ) -> dict:
        """Compute aggregate game stats for a range of rounds.

        Returns a dict with: game_count, avg_score, avg_margin,
        three_point_pct, two_point_pct, avg_possessions, elam_activation_rate.
        """
        stmt = select(GameResultRow).where(
            GameResultRow.season_id == season_id,
            GameResultRow.round_number >= round_start,
            GameResultRow.round_number <= round_end,
        )
        result = await self.session.execute(stmt)
        games = list(result.scalars().all())

        if not games:
            return {"game_count": 0}

        total_scores: list[int] = []
        margins: list[int] = []
        possessions: list[int] = []
        elam_count = 0

        for g in games:
            total_scores.append(g.home_score)
            total_scores.append(g.away_score)
            margins.append(abs(g.home_score - g.away_score))
            possessions.append(g.total_possessions)
            if g.elam_target is not None:
                elam_count += 1

        # Box score aggregates for shooting percentages
        box_stmt = (
            select(BoxScoreRow)
            .join(GameResultRow, BoxScoreRow.game_id == GameResultRow.id)
            .where(
                GameResultRow.season_id == season_id,
                GameResultRow.round_number >= round_start,
                GameResultRow.round_number <= round_end,
            )
        )
        box_result = await self.session.execute(box_stmt)
        box_scores = list(box_result.scalars().all())

        total_3pa = sum(getattr(bs, "three_pointers_attempted", 0) or 0 for bs in box_scores)
        total_3pm = sum(getattr(bs, "three_pointers_made", 0) or 0 for bs in box_scores)
        total_fga = sum(getattr(bs, "field_goals_attempted", 0) or 0 for bs in box_scores)
        total_fgm = sum(getattr(bs, "field_goals_made", 0) or 0 for bs in box_scores)

        game_count = len(games)
        return {
            "game_count": game_count,
            "avg_score": sum(total_scores) / len(total_scores) if total_scores else 0,
            "avg_margin": sum(margins) / len(margins) if margins else 0,
            "three_point_pct": (total_3pm / total_3pa * 100) if total_3pa > 0 else 0,
            "field_goal_pct": (total_fgm / total_fga * 100) if total_fga > 0 else 0,
            "avg_possessions": sum(possessions) / len(possessions) if possessions else 0,
            "elam_activation_rate": elam_count / game_count if game_count > 0 else 0,
        }

    async def get_avg_total_game_score_for_rounds(
        self,
        season_id: str,
        round_start: int,
        round_end: int,
    ) -> tuple[float, int]:
        """Compute average total game score (home + away) for a range of rounds.

        Returns (avg_total_score, game_count).  If no games exist in the
        range, returns (0.0, 0).
        """
        stmt = select(
            func.count(GameResultRow.id),
            func.avg(GameResultRow.home_score + GameResultRow.away_score),
        ).where(
            GameResultRow.season_id == season_id,
            GameResultRow.round_number >= round_start,
            GameResultRow.round_number <= round_end,
        )
        result = await self.session.execute(stmt)
        row = result.one()
        count: int = row[0] or 0
        avg: float = float(row[1]) if row[1] is not None else 0.0
        return avg, count

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
        # SQLite is single-writer, so concurrent sequence assignment is safe.
        stmt = select(
            func.coalesce(func.max(GovernanceEventRow.sequence_number), 0)
        )
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

        # Get pending_review, rejected, and vetoed proposals
        review_events = await self.get_events_by_type(
            season_id=season_id,
            event_types=["proposal.pending_review", "proposal.rejected", "proposal.vetoed"],
        )
        pending_review_ids: set[str] = set()
        rejected_ids: set[str] = set()
        vetoed_ids: set[str] = set()
        for e in review_events:
            pid = e.payload.get("id", e.aggregate_id)
            if e.event_type == "proposal.pending_review":
                pending_review_ids.add(pid)
            elif e.event_type == "proposal.vetoed":
                vetoed_ids.add(pid)
            else:
                rejected_ids.add(pid)

        # Build proposal list with outcomes
        proposals_passed = 0
        proposals_failed = 0
        proposal_list: list[dict] = []

        for evt in submitted_events:
            p_data = evt.payload
            if "id" not in p_data or "raw_text" not in p_data:
                continue
            pid = p_data["id"]
            # Determine status from lifecycle events (most specific wins)
            if pid in outcomes:
                status = outcomes[pid]
            elif pid in vetoed_ids:
                status = "vetoed"
            elif pid in rejected_ids:
                status = "rejected"
            elif pid in confirmed_ids:
                status = "confirmed"
            elif pid in pending_review_ids:
                status = "pending_review"
            else:
                status = "pending"
            if status == "passed":
                proposals_passed += 1
            elif status == "failed":
                proposals_failed += 1

            interp = p_data.get("interpretation")
            parameter = None
            if interp and isinstance(interp, dict):
                parameter = interp.get("parameter")

            proposal_list.append(
                {
                    "id": pid,
                    "raw_text": p_data.get("raw_text", ""),
                    "status": status,
                    "parameter": parameter,
                    "round_number": evt.round_number,
                    "tier": p_data.get("tier", 1),
                }
            )

        # Derive token balance from event log (same logic as core/tokens.get_token_balance,
        # inlined here to avoid a db → core layer violation).
        token_events = await self.get_events_by_type_and_governor(
            season_id=season_id,
            governor_id=governor_id,
            event_types=["token.regenerated", "token.spent"],
        )
        balance = TokenBalance(
            governor_id=governor_id, season_id=season_id, propose=0, amend=0, boost=0
        )
        for tok_evt in token_events:
            tok_payload = tok_evt.payload
            tok_type = tok_payload.get("token_type", "")
            tok_amount = tok_payload.get("amount", 0)
            if tok_evt.event_type == "token.regenerated":
                if tok_type == "propose":
                    balance.propose += tok_amount
                elif tok_type == "amend":
                    balance.amend += tok_amount
                elif tok_type == "boost":
                    balance.boost += tok_amount
            elif tok_evt.event_type == "token.spent":
                if tok_type == "propose":
                    balance.propose -= tok_amount
                elif tok_type == "amend":
                    balance.amend -= tok_amount
                elif tok_type == "boost":
                    balance.boost -= tok_amount

        return {
            "proposals_submitted": len(proposal_list),
            "proposals_passed": proposals_passed,
            "proposals_failed": proposals_failed,
            "votes_cast": len(vote_events),
            "proposal_list": proposal_list,
            "token_balance": balance,
        }

    async def get_all_proposals(self, season_id: str) -> list[dict]:
        """Get all proposals in a season with full lifecycle status.

        Returns a list of dicts with id, raw_text, status, governor_id,
        team_id, parameter, tier, round_number.
        """
        submitted_events = await self.get_events_by_type(
            season_id=season_id,
            event_types=["proposal.submitted"],
        )
        outcome_events = await self.get_events_by_type(
            season_id=season_id,
            event_types=["proposal.passed", "proposal.failed"],
        )
        outcomes: dict[str, str] = {}
        for e in outcome_events:
            pid = e.payload.get("proposal_id", e.aggregate_id)
            outcomes[pid] = "passed" if e.event_type == "proposal.passed" else "failed"

        confirmed_events = await self.get_events_by_type(
            season_id=season_id,
            event_types=["proposal.confirmed"],
        )
        confirmed_ids = {
            e.payload.get("proposal_id", e.aggregate_id) for e in confirmed_events
        }

        review_events = await self.get_events_by_type(
            season_id=season_id,
            event_types=["proposal.pending_review", "proposal.rejected", "proposal.vetoed"],
        )
        pending_review_ids: set[str] = set()
        rejected_ids: set[str] = set()
        vetoed_ids: set[str] = set()
        for e in review_events:
            pid = e.payload.get("id", e.aggregate_id)
            if e.event_type == "proposal.pending_review":
                pending_review_ids.add(pid)
            elif e.event_type == "proposal.vetoed":
                vetoed_ids.add(pid)
            else:
                rejected_ids.add(pid)

        proposals: list[dict] = []
        for evt in submitted_events:
            p_data = evt.payload
            if "id" not in p_data:
                continue
            pid = p_data["id"]
            if pid in outcomes:
                status = outcomes[pid]
            elif pid in vetoed_ids:
                status = "vetoed"
            elif pid in rejected_ids:
                status = "rejected"
            elif pid in confirmed_ids:
                status = "confirmed"
            elif pid in pending_review_ids:
                status = "pending_review"
            else:
                status = "pending"

            interp = p_data.get("interpretation")
            parameter = None
            if interp and isinstance(interp, dict):
                parameter = interp.get("parameter")

            proposals.append(
                {
                    "id": pid,
                    "raw_text": p_data.get("raw_text", ""),
                    "status": status,
                    "governor_id": evt.governor_id,
                    "team_id": evt.team_id,
                    "parameter": parameter,
                    "tier": p_data.get("tier", 1),
                    "round_number": evt.round_number,
                }
            )
        return proposals

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

    # Phases that count as "playoff" for backward-compatible queries.
    _PLAYOFF_PHASES = ("playoff", "semifinal", "finals")

    async def get_full_schedule(
        self,
        season_id: str,
        phase: str | None = None,
    ) -> list[ScheduleRow]:
        """Get all schedule entries for a season, optionally filtered by phase.

        Args:
            season_id: The season to query.
            phase: Filter by phase (e.g. "regular", "playoff"). When
                ``"playoff"`` is passed, matches entries with phase
                ``"playoff"``, ``"semifinal"``, or ``"finals"`` for
                backward compatibility.  None returns all.

        Returns:
            Schedule entries ordered by round_number and matchup_index.
        """
        stmt = select(ScheduleRow).where(ScheduleRow.season_id == season_id)
        if phase:
            if phase == "playoff":
                stmt = stmt.where(ScheduleRow.phase.in_(self._PLAYOFF_PHASES))
            else:
                stmt = stmt.where(ScheduleRow.phase == phase)
        stmt = stmt.order_by(ScheduleRow.round_number, ScheduleRow.matchup_index)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    # --- Reports ---

    async def store_report(
        self,
        season_id: str,
        report_type: str,
        round_number: int,
        content: str,
        team_id: str = "",
        governor_id: str = "",
        metadata_json: dict | None = None,
    ) -> ReportRow:
        row = ReportRow(
            season_id=season_id,
            report_type=report_type,
            round_number=round_number,
            content=content,
            team_id=team_id,
            governor_id=governor_id,
            metadata_json=metadata_json,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def get_reports_for_round(
        self,
        season_id: str,
        round_number: int,
        report_type: str | None = None,
    ) -> list[ReportRow]:
        stmt = select(ReportRow).where(
            ReportRow.season_id == season_id,
            ReportRow.round_number == round_number,
        )
        if report_type:
            stmt = stmt.where(ReportRow.report_type == report_type)
        stmt = stmt.order_by(ReportRow.created_at.desc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_private_reports(
        self,
        season_id: str,
        governor_id: str,
        round_number: int | None = None,
    ) -> list[ReportRow]:
        """Get private reports for a specific governor."""
        stmt = select(ReportRow).where(
            ReportRow.season_id == season_id,
            ReportRow.governor_id == governor_id,
            ReportRow.report_type == "private",
        )
        if round_number is not None:
            stmt = stmt.where(ReportRow.round_number == round_number)
        stmt = stmt.order_by(ReportRow.created_at.desc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def update_report_content(
        self,
        report_id: str,
        content: str,
    ) -> ReportRow | None:
        """Update the content of an existing report.

        Returns the updated row, or None if not found.
        """
        row = await self.session.get(ReportRow, report_id)
        if row is not None:
            row.content = content
            await self.session.flush()
        return row

    async def get_series_reports(
        self,
        season_id: str,
    ) -> list[ReportRow]:
        """Get all series reports for a season, newest first."""
        stmt = (
            select(ReportRow)
            .where(
                ReportRow.season_id == season_id,
                ReportRow.report_type == "series",
            )
            .order_by(ReportRow.created_at.desc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_latest_report(
        self,
        season_id: str,
        report_type: str,
    ) -> ReportRow | None:
        stmt = (
            select(ReportRow)
            .where(
                ReportRow.season_id == season_id,
                ReportRow.report_type == report_type,
            )
            .order_by(ReportRow.created_at.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_public_reports_for_season(
        self,
        season_id: str,
    ) -> list[ReportRow]:
        """Get all public reports (simulation, governance, series) for a season."""
        stmt = (
            select(ReportRow)
            .where(
                ReportRow.season_id == season_id,
                ReportRow.report_type.in_(["simulation", "governance", "series"]),
            )
            .order_by(ReportRow.round_number.asc(), ReportRow.report_type.asc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

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

    async def enroll_player(self, player_id: str, team_id: str, season_id: str) -> PlayerRow:
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

    async def get_governors_for_team(
        self,
        team_id: str,
        season_id: str,
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

    # --- Search / Stats Queries ---

    async def get_stat_leaders(
        self,
        season_id: str,
        stat: str,
        limit: int = 10,
    ) -> list[dict]:
        """Aggregate box score stats across the season, return top N hoopers.

        Args:
            season_id: Season to query.
            stat: Column name on BoxScoreRow (e.g. "points", "assists", "steals").
            limit: Number of leaders to return.

        Returns:
            List of dicts with hooper_id and total.
        """
        stat_col = getattr(BoxScoreRow, stat, None)
        if stat_col is None:
            return []

        stmt = (
            select(
                BoxScoreRow.hooper_id,
                func.sum(stat_col).label("total"),
            )
            .join(GameResultRow, BoxScoreRow.game_id == GameResultRow.id)
            .where(GameResultRow.season_id == season_id)
            .group_by(BoxScoreRow.hooper_id)
            .order_by(func.sum(stat_col).desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return [
            {"hooper_id": row[0], "total": row[1]}
            for row in result.all()
        ]

    async def get_head_to_head(
        self,
        season_id: str,
        team_a_id: str,
        team_b_id: str,
    ) -> list[GameResultRow]:
        """Get all games between two teams in a season.

        Returns games ordered by round_number.
        """
        stmt = (
            select(GameResultRow)
            .where(
                GameResultRow.season_id == season_id,
                or_(
                    (
                        (GameResultRow.home_team_id == team_a_id)
                        & (GameResultRow.away_team_id == team_b_id)
                    ),
                    (
                        (GameResultRow.home_team_id == team_b_id)
                        & (GameResultRow.away_team_id == team_a_id)
                    ),
                ),
            )
            .options(selectinload(GameResultRow.box_scores))
            .order_by(GameResultRow.round_number)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_games_for_team(
        self,
        season_id: str,
        team_id: str,
    ) -> list[GameResultRow]:
        """Get all games involving a specific team in a season.

        Returns games ordered by round_number.
        """
        stmt = (
            select(GameResultRow)
            .where(
                GameResultRow.season_id == season_id,
                or_(
                    GameResultRow.home_team_id == team_id,
                    GameResultRow.away_team_id == team_id,
                ),
            )
            .options(selectinload(GameResultRow.box_scores))
            .order_by(GameResultRow.round_number)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_team_game_results(
        self,
        team_id: str,
        season_id: str,
    ) -> list[dict]:
        """Get round-by-round game results for a team.

        Returns a list of dicts with: round_number, opponent_team_id,
        opponent_team_name, team_score, opponent_score, won (bool), margin.
        Used for trajectory analysis on team pages.

        Uses a single batch query for opponent team names instead of one
        query per game (avoids N+1).
        """
        games = await self.get_games_for_team(season_id, team_id)
        if not games:
            return []

        # Collect all unique opponent IDs, then batch-fetch their names
        opponent_ids = {
            (game.away_team_id if game.home_team_id == team_id else game.home_team_id)
            for game in games
        }
        opponent_stmt = select(TeamRow.id, TeamRow.name).where(
            TeamRow.id.in_(opponent_ids)
        )
        opponent_result = await self.session.execute(opponent_stmt)
        team_name_map: dict[str, str] = {
            row[0]: row[1] for row in opponent_result.all()
        }

        results: list[dict] = []
        for game in games:
            is_home = game.home_team_id == team_id
            team_score = game.home_score if is_home else game.away_score
            opponent_team_id = game.away_team_id if is_home else game.home_team_id
            opponent_score = game.away_score if is_home else game.home_score
            won = game.winner_team_id == team_id
            margin = team_score - opponent_score
            opponent_team_name = team_name_map.get(opponent_team_id, opponent_team_id)

            results.append({
                "round_number": game.round_number,
                "opponent_team_id": opponent_team_id,
                "opponent_team_name": opponent_team_name,
                "team_score": team_score,
                "opponent_score": opponent_score,
                "won": won,
                "margin": margin,
                "is_home": is_home,
            })

        return results

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

        from pinwheel.models.constants import ATTRIBUTE_ORDER

        totals: dict[str, float] = {a: 0.0 for a in ATTRIBUTE_ORDER}
        count = len(all_attrs)
        for attrs in all_attrs:
            for a in ATTRIBUTE_ORDER:
                totals[a] += float(attrs.get(a, 0))

        return {a: round(totals[a] / count, 1) for a in ATTRIBUTE_ORDER}

    async def get_league_season_highs(self, season_id: str) -> dict[str, int]:
        """Get the single-game high for each core stat across all hoopers in a season.

        Returns a dict mapping stat name to the maximum value achieved in any single game.
        Used to identify and bold league-high performances on the hooper page.
        """
        stmt = (
            select(
                func.max(BoxScoreRow.points).label("points"),
                func.max(BoxScoreRow.assists).label("assists"),
                func.max(BoxScoreRow.steals).label("steals"),
            )
            .join(GameResultRow, BoxScoreRow.game_id == GameResultRow.id)
            .where(GameResultRow.season_id == season_id)
        )
        result = await self.session.execute(stmt)
        row = result.one_or_none()
        if not row:
            return {}
        return {
            "points": row.points or 0,
            "assists": row.assists or 0,
            "steals": row.steals or 0,
        }

    async def update_hooper_backstory(self, hooper_id: str, backstory: str) -> HooperRow | None:
        """Update a hooper's backstory text."""
        hooper = await self.get_hooper(hooper_id)
        if hooper:
            hooper.backstory = backstory
            await self.session.flush()
        return hooper

    async def get_hooper_season_stats(
        self, hooper_id: str, season_id: str
    ) -> dict[str, int]:
        """Sum box score stats for a hooper across all games in a season.

        Returns a dict mapping stat name to cumulative total, e.g.:
            {"points": 120, "assists": 34, "steals": 12, ...}
        """
        stat_columns = {
            "points": BoxScoreRow.points,
            "field_goals_made": BoxScoreRow.field_goals_made,
            "field_goals_attempted": BoxScoreRow.field_goals_attempted,
            "three_pointers_made": BoxScoreRow.three_pointers_made,
            "three_pointers_attempted": BoxScoreRow.three_pointers_attempted,
            "free_throws_made": BoxScoreRow.free_throws_made,
            "free_throws_attempted": BoxScoreRow.free_throws_attempted,
            "assists": BoxScoreRow.assists,
            "steals": BoxScoreRow.steals,
            "turnovers": BoxScoreRow.turnovers,
        }
        aggregates = [
            func.coalesce(func.sum(col), 0).label(name)
            for name, col in stat_columns.items()
        ]
        stmt = (
            select(*aggregates)
            .join(GameResultRow, BoxScoreRow.game_id == GameResultRow.id)
            .where(
                BoxScoreRow.hooper_id == hooper_id,
                GameResultRow.season_id == season_id,
            )
        )
        result = await self.session.execute(stmt)
        row = result.one_or_none()
        if row is None:
            return {name: 0 for name in stat_columns}
        return dict(zip(stat_columns.keys(), row, strict=True))

    async def add_hooper_move(self, hooper_id: str, move_data: dict) -> None:
        """Append a move to a hooper's moves JSON array.

        Loads the current moves list, appends the new move, and writes back.
        No-op if the hooper is not found.
        """
        hooper = await self.session.get(HooperRow, hooper_id)
        if hooper is None:
            return
        current_moves: list[dict] = list(hooper.moves or [])
        current_moves.append(move_data)
        hooper.moves = current_moves
        await self.session.flush()

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
        """Store an eval result. Never stores private report content."""
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
        """Update season status.

        Accepts any lifecycle phase value (setup, active, playoffs,
        championship, offseason, complete) as well as legacy values
        (completed, archived, regular_season_complete).

        Automatically sets ``completed_at`` when transitioning to a
        terminal state.
        """
        season = await self.get_season(season_id)
        if season:
            season.status = status
            if status in ("completed", "complete"):
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
        stmt = select(SeasonArchiveRow).order_by(SeasonArchiveRow.created_at.desc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    # --- Meta Column Helpers ---

    async def update_team_meta(self, team_id: str, meta: dict) -> None:
        """Merge meta fields onto a team's meta JSON column."""
        team = await self.session.get(TeamRow, team_id)
        if team:
            current = dict(team.meta or {})
            current.update(meta)
            team.meta = current
            await self.session.flush()

    async def update_hooper_meta(self, hooper_id: str, meta: dict) -> None:
        """Merge meta fields onto a hooper's meta JSON column."""
        hooper = await self.session.get(HooperRow, hooper_id)
        if hooper:
            current = dict(hooper.meta or {})
            current.update(meta)
            hooper.meta = current
            await self.session.flush()

    async def update_season_meta(self, season_id: str, meta: dict) -> None:
        """Merge meta fields onto a season's meta JSON column."""
        season = await self.session.get(SeasonRow, season_id)
        if season:
            current = dict(season.meta or {})
            current.update(meta)
            season.meta = current
            await self.session.flush()

    async def update_game_result_meta(self, game_id: str, meta: dict) -> None:
        """Merge meta fields onto a game result's meta JSON column."""
        game = await self.session.get(GameResultRow, game_id)
        if game:
            current = dict(game.meta or {})
            current.update(meta)
            game.meta = current
            await self.session.flush()

    async def update_player_meta(self, player_id: str, meta: dict) -> None:
        """Merge meta fields onto a player's meta JSON column."""
        player = await self.session.get(PlayerRow, player_id)
        if player:
            current = dict(player.meta or {})
            current.update(meta)
            player.meta = current
            await self.session.flush()

    async def flush_meta_store(
        self,
        dirty_entities: list[tuple[str, str, dict]],
    ) -> None:
        """Flush dirty MetaStore entries back to database meta columns.

        Each entry is (entity_type, entity_id, meta_dict).
        Routes to the appropriate update_*_meta method.
        """
        type_map = {
            "team": self.update_team_meta,
            "hooper": self.update_hooper_meta,
            "season": self.update_season_meta,
            "game": self.update_game_result_meta,
            "player": self.update_player_meta,
        }
        for entity_type, entity_id, meta in dirty_entities:
            updater = type_map.get(entity_type)
            if updater and entity_id:
                await updater(entity_id, meta)

    async def get_rule_change_timeline(self, season_id: str) -> list[dict]:
        """Get the timeline of rule changes for a season.

        Returns a list of dicts, each with:
        - parameter: str
        - old_value: int | float | bool
        - new_value: int | float | bool
        - round_enacted: int
        - proposal_id: str
        - governor_id: str | None (from linked proposal.submitted event)
        - governor_name: str | None (resolved username)
        - raw_text: str | None (original proposal text)
        - vote_yes: float (weighted yes votes)
        - vote_no: float (weighted no votes)
        - vote_margin: str | None (human-readable margin, e.g. "3-1")

        Ordered by round_enacted, then sequence_number.
        """
        rule_events = await self.get_events_by_type(
            season_id=season_id,
            event_types=["rule.enacted"],
        )

        # Build maps from submitted events
        submitted_events = await self.get_events_by_type(
            season_id=season_id,
            event_types=["proposal.submitted"],
        )
        proposal_governors: dict[str, str | None] = {}
        proposal_raw_text: dict[str, str] = {}
        for e in submitted_events:
            pid = e.payload.get("id", "")
            if pid:
                proposal_governors[pid] = e.governor_id
                proposal_raw_text[pid] = e.payload.get("raw_text", "")

        # Build vote tally map from outcome events
        outcome_events = await self.get_events_by_type(
            season_id=season_id,
            event_types=["proposal.passed", "proposal.failed"],
        )
        vote_tallies: dict[str, dict[str, float | int]] = {}
        for e in outcome_events:
            pid = e.payload.get("proposal_id", e.aggregate_id)
            vote_tallies[pid] = {
                "yes": float(e.payload.get("weighted_yes", 0)),
                "no": float(e.payload.get("weighted_no", 0)),
                "yes_count": int(e.payload.get("yes_count", 0)),
                "no_count": int(e.payload.get("no_count", 0)),
            }

        # Resolve governor usernames in bulk
        governor_ids = {
            gid for gid in proposal_governors.values() if gid
        }
        governor_names: dict[str, str] = {}
        for gid in governor_ids:
            player = await self.get_player(gid)
            if player:
                governor_names[gid] = player.username

        timeline: list[dict] = []
        for evt in rule_events:
            payload = evt.payload
            proposal_id = payload.get("source_proposal_id", "")
            governor_id = proposal_governors.get(proposal_id)
            tally = vote_tallies.get(proposal_id, {})
            yes_count = int(tally.get("yes_count", 0))
            no_count = int(tally.get("no_count", 0))
            vote_margin = (
                f"{yes_count}\u2013{no_count}"
                if yes_count or no_count
                else None
            )
            timeline.append({
                "parameter": payload.get("parameter", ""),
                "old_value": payload.get("old_value"),
                "new_value": payload.get("new_value"),
                "round_enacted": payload.get("round_enacted", 0),
                "proposal_id": proposal_id,
                "governor_id": governor_id,
                "governor_name": governor_names.get(governor_id or ""),
                "raw_text": proposal_raw_text.get(proposal_id),
                "vote_yes": float(tally.get("yes", 0)),
                "vote_no": float(tally.get("no", 0)),
                "vote_margin": vote_margin,
            })

        # Sort by round_enacted ascending
        timeline.sort(key=lambda x: x["round_enacted"])
        return timeline

    async def load_team_meta(self, team_id: str) -> dict:
        """Load meta dict for a team."""
        team = await self.session.get(TeamRow, team_id)
        if team and team.meta:
            return dict(team.meta)
        return {}

    async def load_all_team_meta(self, season_id: str) -> dict[str, dict]:
        """Load meta for all teams in a season. Returns {team_id: meta_dict}."""
        teams = await self.get_teams_for_season(season_id)
        result: dict[str, dict] = {}
        for team in teams:
            result[team.id] = dict(team.meta or {})
        return result

    async def load_hooper_meta(self, hooper_id: str) -> dict:
        """Load meta dict for a hooper."""
        hooper = await self.session.get(HooperRow, hooper_id)
        if hooper and hooper.meta:
            return dict(hooper.meta)
        return {}

    async def load_hoopers_meta_for_teams(
        self, team_ids: list[str],
    ) -> dict[str, dict]:
        """Load meta for all hoopers on the given teams.

        Returns {hooper_id: meta_dict} for hoopers that have non-empty meta.
        """
        result: dict[str, dict] = {}
        for tid in team_ids:
            hoopers = await self.get_hoopers_for_team(tid)
            for hooper in hoopers:
                meta = dict(hooper.meta or {})
                result[hooper.id] = meta
        return result

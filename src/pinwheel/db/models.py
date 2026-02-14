"""SQLAlchemy ORM models for the Pinwheel database.

Day 1 tables: leagues, seasons, teams, hoopers, game_results, box_scores,
governance_events, schedule. Additional tables (reports, commentary,
governors) added as needed.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _uuid() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    """Base class for all ORM models."""


class LeagueRow(Base):
    __tablename__ = "leagues"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))

    seasons: Mapped[list[SeasonRow]] = relationship(back_populates="league")


class SeasonRow(Base):
    __tablename__ = "seasons"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    league_id: Mapped[str] = mapped_column(ForeignKey("leagues.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="setup")
    starting_ruleset: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    current_ruleset: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    league: Mapped[LeagueRow] = relationship(back_populates="seasons")
    teams: Mapped[list[TeamRow]] = relationship(back_populates="season")


class TeamRow(Base):
    __tablename__ = "teams"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    season_id: Mapped[str] = mapped_column(ForeignKey("seasons.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    color: Mapped[str] = mapped_column(String(7), default="#000000")
    color_secondary: Mapped[str] = mapped_column(String(7), default="#ffffff")
    motto: Mapped[str] = mapped_column(Text, default="")
    venue: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))

    season: Mapped[SeasonRow] = relationship(back_populates="teams")
    hoopers: Mapped[list[HooperRow]] = relationship(back_populates="team")

    @property
    def agents(self) -> list[HooperRow]:
        """Backward-compatible alias for hoopers."""
        return self.hoopers


# Backward-compatible alias
AgentRow = None  # Defined after HooperRow


class HooperRow(Base):
    __tablename__ = "hoopers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"), nullable=False)
    season_id: Mapped[str] = mapped_column(ForeignKey("seasons.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    archetype: Mapped[str] = mapped_column(String(30), nullable=False)
    backstory: Mapped[str] = mapped_column(Text, default="")
    attributes: Mapped[dict] = mapped_column(JSON, nullable=False)
    moves: Mapped[list] = mapped_column(JSON, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))

    team: Mapped[TeamRow] = relationship(back_populates="hoopers")

    __table_args__ = (Index("ix_hoopers_team_id", "team_id"),)


AgentRow = HooperRow  # type: ignore[assignment]


class GameResultRow(Base):
    __tablename__ = "game_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    season_id: Mapped[str] = mapped_column(ForeignKey("seasons.id"), nullable=False)
    round_number: Mapped[int] = mapped_column(Integer, nullable=False)
    matchup_index: Mapped[int] = mapped_column(Integer, nullable=False)
    home_team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"), nullable=False)
    away_team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"), nullable=False)
    home_score: Mapped[int] = mapped_column(Integer, nullable=False)
    away_score: Mapped[int] = mapped_column(Integer, nullable=False)
    winner_team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"), nullable=False)
    seed: Mapped[int] = mapped_column(BigInteger, nullable=False)
    ruleset_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    quarter_scores: Mapped[list | None] = mapped_column(JSON, nullable=True)
    elam_target: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_possessions: Mapped[int] = mapped_column(Integer, nullable=False)
    play_by_play: Mapped[list | None] = mapped_column(JSON, nullable=True)
    presented: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))

    box_scores: Mapped[list[BoxScoreRow]] = relationship(back_populates="game")

    __table_args__ = (
        Index("ix_game_results_season_round", "season_id", "round_number"),
        UniqueConstraint("season_id", "round_number", "matchup_index", name="uq_game_matchup"),
    )


class BoxScoreRow(Base):
    __tablename__ = "box_scores"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    game_id: Mapped[str] = mapped_column(ForeignKey("game_results.id"), nullable=False)
    hooper_id: Mapped[str] = mapped_column(ForeignKey("hoopers.id"), nullable=False)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"), nullable=False)
    points: Mapped[int] = mapped_column(Integer, default=0)
    field_goals_made: Mapped[int] = mapped_column(Integer, default=0)
    field_goals_attempted: Mapped[int] = mapped_column(Integer, default=0)
    three_pointers_made: Mapped[int] = mapped_column(Integer, default=0)
    three_pointers_attempted: Mapped[int] = mapped_column(Integer, default=0)
    free_throws_made: Mapped[int] = mapped_column(Integer, default=0)
    free_throws_attempted: Mapped[int] = mapped_column(Integer, default=0)
    assists: Mapped[int] = mapped_column(Integer, default=0)
    steals: Mapped[int] = mapped_column(Integer, default=0)
    turnovers: Mapped[int] = mapped_column(Integer, default=0)
    minutes: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))

    game: Mapped[GameResultRow] = relationship(back_populates="box_scores")

    @property
    def agent_id(self) -> str:
        """Backward-compatible alias for hooper_id."""
        return self.hooper_id

    __table_args__ = (
        Index("ix_box_scores_game_id", "game_id"),
        Index("ix_box_scores_hooper_id", "hooper_id"),
    )


class GovernanceEventRow(Base):
    """Append-only governance event store. Source of truth for governance state."""

    __tablename__ = "governance_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(100), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(50), nullable=False)
    season_id: Mapped[str] = mapped_column(ForeignKey("seasons.id"), nullable=False)
    round_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    governor_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    team_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        Index("ix_gov_events_aggregate", "aggregate_type", "aggregate_id"),
        Index("ix_gov_events_season_round", "season_id", "round_number"),
        Index("ix_gov_events_type", "event_type"),
    )


class ReportRow(Base):
    """Stored AI-generated reports."""

    __tablename__ = "reports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    season_id: Mapped[str] = mapped_column(ForeignKey("seasons.id"), nullable=False)
    report_type: Mapped[str] = mapped_column(String(30), nullable=False)
    round_number: Mapped[int] = mapped_column(Integer, default=0)
    team_id: Mapped[str] = mapped_column(String(36), default="")
    governor_id: Mapped[str] = mapped_column(String(36), default="")
    content: Mapped[str] = mapped_column(Text, default="")
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))

    __table_args__ = (
        Index("ix_reports_season_round", "season_id", "round_number"),
        Index("ix_reports_type", "report_type"),
        Index("ix_reports_governor", "governor_id"),
    )


class PlayerRow(Base):
    """Discord-authenticated player (governor) identity."""

    __tablename__ = "players"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    discord_id: Mapped[str] = mapped_column(String(30), nullable=False, unique=True)
    username: Mapped[str] = mapped_column(String(100), nullable=False)
    avatar_url: Mapped[str] = mapped_column(String(512), default="")
    team_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    enrolled_season_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    last_login: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))

    __table_args__ = (Index("ix_players_discord_id", "discord_id"),)


class ScheduleRow(Base):
    __tablename__ = "schedule"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    season_id: Mapped[str] = mapped_column(ForeignKey("seasons.id"), nullable=False)
    round_number: Mapped[int] = mapped_column(Integer, nullable=False)
    matchup_index: Mapped[int] = mapped_column(Integer, nullable=False)
    home_team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"), nullable=False)
    away_team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"), nullable=False)
    phase: Mapped[str] = mapped_column(String(20), default="regular")
    status: Mapped[str] = mapped_column(String(20), default="scheduled")
    game_result_id: Mapped[str | None] = mapped_column(ForeignKey("game_results.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))

    __table_args__ = (
        Index("ix_schedule_season_round", "season_id", "round_number"),
        UniqueConstraint("season_id", "round_number", "matchup_index", name="uq_schedule_matchup"),
    )


class BotStateRow(Base):
    """Key-value store for Discord bot state (channel IDs, role IDs, etc).

    Persisted across restarts so the bot can recover channel/role mappings
    without recreating them.
    """

    __tablename__ = "bot_state"

    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    value: Mapped[str] = mapped_column(String(500), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class SeasonArchiveRow(Base):
    """Frozen snapshot of a completed season."""

    __tablename__ = "season_archives"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    season_id: Mapped[str] = mapped_column(ForeignKey("seasons.id"), nullable=False)
    season_name: Mapped[str] = mapped_column(String(100), nullable=False)
    final_standings: Mapped[dict] = mapped_column(JSON, nullable=False)
    final_ruleset: Mapped[dict] = mapped_column(JSON, nullable=False)
    rule_change_history: Mapped[list] = mapped_column(JSON, default=list)
    champion_team_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    champion_team_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    total_games: Mapped[int] = mapped_column(Integer, default=0)
    total_proposals: Mapped[int] = mapped_column(Integer, default=0)
    total_rule_changes: Mapped[int] = mapped_column(Integer, default=0)
    governor_count: Mapped[int] = mapped_column(Integer, default=0)
    memorial: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))


class EvalResultRow(Base):
    """Stored eval results. Never contains private report content."""

    __tablename__ = "eval_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    season_id: Mapped[str] = mapped_column(ForeignKey("seasons.id"), nullable=False)
    round_number: Mapped[int] = mapped_column(Integer, default=0)
    eval_type: Mapped[str] = mapped_column(String(50), nullable=False)
    eval_subtype: Mapped[str] = mapped_column(String(50), default="")
    score: Mapped[float] = mapped_column(Float, default=0.0)
    details_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))

    __table_args__ = (
        Index("ix_eval_results_season_round", "season_id", "round_number"),
        Index("ix_eval_results_type", "eval_type"),
    )

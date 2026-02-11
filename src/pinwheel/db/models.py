"""SQLAlchemy ORM models for the Pinwheel database.

Day 1 tables: leagues, seasons, teams, agents, game_results, box_scores,
governance_events, schedule. Additional tables (mirrors, commentary,
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
    motto: Mapped[str] = mapped_column(Text, default="")
    venue: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))

    season: Mapped[SeasonRow] = relationship(back_populates="teams")
    agents: Mapped[list[AgentRow]] = relationship(back_populates="team")


class AgentRow(Base):
    __tablename__ = "agents"

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

    team: Mapped[TeamRow] = relationship(back_populates="agents")

    __table_args__ = (Index("ix_agents_team_id", "team_id"),)


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
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), nullable=False)
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

    __table_args__ = (
        Index("ix_box_scores_game_id", "game_id"),
        Index("ix_box_scores_agent_id", "agent_id"),
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

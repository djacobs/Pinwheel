"""Mutable game state for the simulation engine.

GameState, HooperState — the working memory of a game in progress.
These are internal to the simulation; GameResult is the immutable output.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pinwheel.models.team import Hooper, PlayerAttributes, TeamStrategy


@dataclass
class PossessionContext:
    """Effect-derived modifiers applied to this possession.

    Built from HookResult accumulation at sim.possession.pre,
    consumed by resolve_possession(). Ephemeral — one per possession.
    """

    shot_probability_modifier: float = 0.0
    shot_value_modifier: int = 0
    extra_stamina_drain: float = 0.0
    at_rim_bias: float = 0.0
    mid_range_bias: float = 0.0
    three_point_bias: float = 0.0
    turnover_modifier: float = 0.0
    random_ejection_probability: float = 0.0
    bonus_pass_count: int = 0
    narrative_tags: list[str] = field(default_factory=list)


@dataclass
class HooperState:
    """Mutable state of a Hooper during a game."""

    hooper: Hooper
    on_court: bool = True
    current_stamina: float = 1.0
    fouls: int = 0
    ejected: bool = False
    points: int = 0
    field_goals_made: int = 0
    field_goals_attempted: int = 0
    three_pointers_made: int = 0
    three_pointers_attempted: int = 0
    free_throws_made: int = 0
    free_throws_attempted: int = 0
    rebounds: int = 0
    assists: int = 0
    steals: int = 0
    blocks: int = 0
    turnovers: int = 0
    minutes: float = 0.0
    moves_activated: list[str] = field(default_factory=list)

    # Cache for current_attributes — invalidated when current_stamina or any
    # stamina-scaled base attribute changes.  In normal gameplay base attributes
    # are fixed at construction, so this comparison is essentially free.
    # Not part of the public interface; excluded from __init__ via field(init=False).
    _cached_stamina: float = field(default=-1.0, init=False, repr=False, compare=False)
    _cached_base_key: tuple[int, int, int, int] = field(
        default=(-1, -1, -1, -1), init=False, repr=False, compare=False
    )
    _cached_attributes: PlayerAttributes | None = field(
        default=None, init=False, repr=False, compare=False
    )

    @property
    def current_attributes(self) -> PlayerAttributes:
        """Attributes scaled by current stamina.

        Result is cached and reused as long as current_stamina and the
        stamina-scaled base attributes (scoring, passing, defense, speed)
        have not changed since the last computation.  This avoids a Pydantic
        model allocation on every access — called 20-40 times per possession,
        100+ possessions per game.

        In normal gameplay base attributes are set at Hooper construction and
        never mutated, so the base-key comparison is a cheap tuple equality
        check that almost always hits the cache.
        """
        base = self.hooper.attributes
        base_key = (base.scoring, base.passing, base.defense, base.speed)
        if (
            self._cached_attributes is None
            or self._cached_stamina != self.current_stamina
            or self._cached_base_key != base_key
        ):
            s = self.current_stamina
            self._cached_attributes = PlayerAttributes(
                scoring=max(1, int(base.scoring * s)),
                passing=max(1, int(base.passing * s)),
                defense=max(1, int(base.defense * s)),
                speed=max(1, int(base.speed * s)),
                stamina=base.stamina,
                iq=base.iq,
                ego=base.ego,
                chaotic_alignment=base.chaotic_alignment,
                fate=base.fate,
            )
            self._cached_stamina = self.current_stamina
            self._cached_base_key = base_key
        return self._cached_attributes


@dataclass
class GameState:
    """Mutable state of a game in progress."""

    home_agents: list[HooperState]
    away_agents: list[HooperState]
    home_score: int = 0
    away_score: int = 0
    quarter: int = 1
    possession_number: int = 0
    total_possessions: int = 0
    game_clock_seconds: float = 0.0
    home_has_ball: bool = True
    elam_activated: bool = False
    elam_target_score: int | None = None
    game_over: bool = False
    home_strategy: TeamStrategy | None = None
    away_strategy: TeamStrategy | None = None

    # Cross-possession tracking (for condition evaluation)
    last_action: str = ""
    last_result: str = ""
    consecutive_makes: int = 0
    consecutive_misses: int = 0

    @property
    def home_active(self) -> list[HooperState]:
        """Home players currently on the court and not ejected."""
        return [a for a in self.home_agents if a.on_court and not a.ejected]

    @property
    def away_active(self) -> list[HooperState]:
        """Away players currently on the court and not ejected."""
        return [a for a in self.away_agents if a.on_court and not a.ejected]

    @property
    def home_bench(self) -> list[HooperState]:
        """Home players on the bench (not on court) and not ejected."""
        return [a for a in self.home_agents if not a.on_court and not a.ejected]

    @property
    def away_bench(self) -> list[HooperState]:
        """Away players on the bench (not on court) and not ejected."""
        return [a for a in self.away_agents if not a.on_court and not a.ejected]

    # Keep backward-compatible aliases
    @property
    def home_starters(self) -> list[HooperState]:
        return self.home_active

    @property
    def away_starters(self) -> list[HooperState]:
        return self.away_active

    @property
    def offense(self) -> list[HooperState]:
        return self.home_active if self.home_has_ball else self.away_active

    @property
    def defense(self) -> list[HooperState]:
        return self.away_active if self.home_has_ball else self.home_active

    @property
    def offense_strategy(self) -> TeamStrategy | None:
        """Strategy for the team currently on offense."""
        return self.home_strategy if self.home_has_ball else self.away_strategy

    @property
    def defense_strategy(self) -> TeamStrategy | None:
        """Strategy for the team currently on defense."""
        return self.away_strategy if self.home_has_ball else self.home_strategy

    @property
    def score_diff(self) -> int:
        """Positive = home leading."""
        return self.home_score - self.away_score

    def substitute(self, out: HooperState, in_: HooperState) -> None:
        """Swap a player out for a bench player."""
        out.on_court = False
        in_.on_court = True

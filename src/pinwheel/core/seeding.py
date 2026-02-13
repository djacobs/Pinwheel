"""League seeding — YAML config loading and league generation.

Supports two flows:
1. Load from YAML (hand-authored or AI-generated)
2. Generate programmatically from archetypes (no AI needed for Day 1)
"""

from __future__ import annotations

import uuid
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from pinwheel.core.archetypes import ARCHETYPE_MOVES, ARCHETYPES, apply_variance
from pinwheel.models.team import Hooper, Team, Venue


class LeagueConfig(BaseModel):
    """Configuration for an entire league."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str = "Pinwheel Fates"
    teams: list[Team] = Field(default_factory=list)


def generate_league(
    num_teams: int = 8,
    hoopers_per_team: int = 4,
    seed: int = 42,
) -> LeagueConfig:
    """Generate a league programmatically from archetypes.

    Each team gets hoopers with diverse archetypes. Attributes get variance.
    """
    archetype_names = list(ARCHETYPES.keys())
    teams: list[Team] = []

    # Portland-inspired team names
    team_data = [
        ("Rose City Thorns", "#CC0000", "Bloom Where They Plant You", "The Thorn Garden", 18000),
        ("Burnside Breakers", "#0066CC", "Break the Pattern", "Breaker Bay Arena", 6200),
        ("Steel Bridge Iron Horses", "#4A4A4A", "Forged in Fire", "The Foundry", 12000),
        ("Hawthorne Wolves", "#333366", "Run Together", "The Den", 8500),
        ("Alberta Monarchs", "#FFD700", "Crown the Moment", "Monarch Court", 7800),
        ("Mississippi Foxes", "#FF6633", "Quick and Clever", "The Fox Hole", 5500),
        ("St. Johns Ravens", "#1A1A2E", "Wisdom from Above", "Raven's Roost", 9200),
        ("Sellwood Drift", "#88BBDD", "Go with the Flow", "Drift Arena", 4800),
    ]

    for team_idx in range(min(num_teams, len(team_data))):
        tname, color, motto, vname, cap = team_data[team_idx]
        team_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"team-{seed}-{team_idx}"))

        hoopers = []
        for hooper_idx in range(hoopers_per_team):
            idx = (team_idx * hoopers_per_team + hooper_idx) % len(archetype_names)
            arch_name = archetype_names[idx]
            base_attrs = ARCHETYPES[arch_name]
            hooper_seed = seed * 1000 + team_idx * 100 + hooper_idx
            attrs = apply_variance(base_attrs, hooper_seed, variance=8)
            moves = ARCHETYPE_MOVES.get(arch_name, [])

            ns = f"hooper-{seed}-{team_idx}-{hooper_idx}"
            hooper_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, ns))
            hoopers.append(
                Hooper(
                    id=hooper_id,
                    name=f"Hooper-{team_idx}-{hooper_idx}",
                    team_id=team_id,
                    archetype=arch_name,
                    attributes=attrs,
                    moves=moves,
                    is_starter=hooper_idx < 3,
                )
            )

        teams.append(
            Team(
                id=team_id,
                name=tname,
                color=color,
                motto=motto,
                venue=Venue(name=vname, capacity=cap),
                hoopers=hoopers,
            )
        )

    return LeagueConfig(teams=teams)


def save_league_yaml(config: LeagueConfig, path: Path) -> None:
    """Save league config to YAML."""
    data = config.model_dump()
    with open(path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)


def load_league_yaml(path: Path) -> LeagueConfig:
    """Load league config from YAML."""
    with open(path) as f:
        data = yaml.safe_load(f)
    return LeagueConfig.model_validate(data)

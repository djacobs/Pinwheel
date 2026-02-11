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
from pinwheel.models.team import Agent, Team, Venue


class LeagueConfig(BaseModel):
    """Configuration for an entire league."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str = "Pinwheel Fates"
    teams: list[Team] = Field(default_factory=list)


def generate_league(
    num_teams: int = 8,
    agents_per_team: int = 4,
    seed: int = 42,
) -> LeagueConfig:
    """Generate a league programmatically from archetypes.

    Each team gets agents with diverse archetypes. Attributes get variance.
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

        agents = []
        for agent_idx in range(agents_per_team):
            idx = (team_idx * agents_per_team + agent_idx) % len(archetype_names)
            arch_name = archetype_names[idx]
            base_attrs = ARCHETYPES[arch_name]
            agent_seed = seed * 1000 + team_idx * 100 + agent_idx
            attrs = apply_variance(base_attrs, agent_seed, variance=8)
            moves = ARCHETYPE_MOVES.get(arch_name, [])

            agent_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"agent-{seed}-{team_idx}-{agent_idx}"))
            agents.append(
                Agent(
                    id=agent_id,
                    name=f"Agent-{team_idx}-{agent_idx}",
                    team_id=team_id,
                    archetype=arch_name,
                    attributes=attrs,
                    moves=moves,
                    is_starter=agent_idx < 3,
                )
            )

        teams.append(
            Team(
                id=team_id,
                name=tname,
                color=color,
                motto=motto,
                venue=Venue(name=vname, capacity=cap),
                agents=agents,
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

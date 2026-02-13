"""Tests for league seeding and archetype templates."""

import tempfile
from pathlib import Path

from pinwheel.core.archetypes import ARCHETYPE_MOVES, ARCHETYPES, apply_variance
from pinwheel.core.seeding import generate_league, load_league_yaml, save_league_yaml


class TestArchetypes:
    def test_all_archetypes_360_budget(self):
        for name, attrs in ARCHETYPES.items():
            total = attrs.total()
            assert total == 360, f"{name} has {total} points, expected 360"

    def test_all_archetypes_have_moves(self):
        for name in ARCHETYPES:
            assert name in ARCHETYPE_MOVES, f"{name} missing from ARCHETYPE_MOVES"
            assert len(ARCHETYPE_MOVES[name]) >= 1

    def test_variance_stays_in_bounds(self):
        for name, base in ARCHETYPES.items():
            varied = apply_variance(base, rng_seed=42, variance=10)
            data = varied.model_dump()
            for attr, val in data.items():
                assert 1 <= val <= 100, f"{name}.{attr}={val} out of bounds"

    def test_variance_produces_different_results(self):
        base = ARCHETYPES["sharpshooter"]
        v1 = apply_variance(base, rng_seed=1)
        v2 = apply_variance(base, rng_seed=2)
        assert v1 != v2


class TestLeagueGeneration:
    def test_generates_8_teams(self):
        league = generate_league(num_teams=8, seed=42)
        assert len(league.teams) == 8

    def test_each_team_has_4_hoopers(self):
        league = generate_league(seed=42)
        for team in league.teams:
            assert len(team.hoopers) == 4

    def test_3_starters_1_bench(self):
        league = generate_league(seed=42)
        for team in league.teams:
            starters = [h for h in team.hoopers if h.is_starter]
            bench = [h for h in team.hoopers if not h.is_starter]
            assert len(starters) == 3
            assert len(bench) == 1

    def test_deterministic(self):
        l1 = generate_league(seed=42)
        l2 = generate_league(seed=42)
        assert l1.teams[0].name == l2.teams[0].name
        assert l1.teams[0].hoopers[0].attributes == l2.teams[0].hoopers[0].attributes


class TestYAMLRoundTrip:
    def test_save_and_load(self):
        league = generate_league(num_teams=4, seed=42)
        with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False) as f:
            path = Path(f.name)
        save_league_yaml(league, path)
        loaded = load_league_yaml(path)
        assert len(loaded.teams) == 4
        assert loaded.teams[0].name == league.teams[0].name
        assert loaded.teams[0].hoopers[0].id == league.teams[0].hoopers[0].id
        path.unlink()

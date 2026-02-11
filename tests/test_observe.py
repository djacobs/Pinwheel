"""Phase 7: Observability — 1000-game batch statistics and distribution validation.

Verifies that the simulation engine produces basketball-like distributions
across a large sample of games with diverse team compositions.
"""

from pinwheel.core.archetypes import ARCHETYPES, apply_variance
from pinwheel.core.simulation import simulate_game
from pinwheel.models.rules import DEFAULT_RULESET
from pinwheel.models.team import Agent, PlayerAttributes, Team, Venue


def _make_varied_team(
    team_id: str,
    seed_base: int,
    archetype_offset: int = 0,
) -> Team:
    """Create a team with varied archetype agents for realistic distributions."""
    arch_names = list(ARCHETYPES.keys())
    agents = []
    for i in range(4):
        arch = arch_names[(archetype_offset + i) % len(arch_names)]
        attrs = apply_variance(ARCHETYPES[arch], rng_seed=seed_base + i, variance=8)
        agents.append(
            Agent(
                id=f"{team_id}-a{i}",
                name=f"Agent-{i}",
                team_id=team_id,
                archetype=arch,
                attributes=attrs,
                is_starter=i < 3,
            )
        )
    return Team(
        id=team_id,
        name=f"Team-{team_id}",
        venue=Venue(name="Court", capacity=5000),
        agents=agents,
    )


class TestThousandGameDistributions:
    """Run 1000 games with varied teams and verify distributions."""

    def test_1000_games_score_distribution(self):
        """Total scores should cluster in a basketball-like range."""
        scores = []
        for seed in range(1000):
            home = _make_varied_team("h", seed * 10, archetype_offset=seed % 9)
            away = _make_varied_team("a", seed * 10 + 100, archetype_offset=(seed + 3) % 9)
            result = simulate_game(home, away, DEFAULT_RULESET, seed=seed)
            scores.append(result.home_score + result.away_score)

        avg = sum(scores) / len(scores)
        min_score = min(scores)
        max_score = max(scores)

        # 3v3 with Elam ending: expect average total 60-160
        assert 40 < avg < 180, f"avg total score {avg:.1f} out of basketball range"
        assert min_score > 10, f"min total score {min_score} too low"
        assert max_score < 400, f"max total score {max_score} too high"

    def test_1000_games_possession_distribution(self):
        """Possession counts should be reasonable."""
        possessions = []
        for seed in range(1000):
            home = _make_varied_team("h", seed * 10)
            away = _make_varied_team("a", seed * 10 + 100, archetype_offset=4)
            result = simulate_game(home, away, DEFAULT_RULESET, seed=seed)
            possessions.append(result.total_possessions)

        avg = sum(possessions) / len(possessions)
        assert 45 < avg < 200, f"avg possessions {avg:.1f} out of range"

    def test_1000_games_win_balance(self):
        """With varied but roughly equal teams, wins should be balanced."""
        home_wins = 0
        for seed in range(1000):
            home = _make_varied_team("h", seed * 10)
            away = _make_varied_team("a", seed * 10 + 100, archetype_offset=4)
            result = simulate_game(home, away, DEFAULT_RULESET, seed=seed)
            if result.winner_team_id == "h":
                home_wins += 1

        # Should be roughly 50/50 (allow 35-65% range)
        assert 350 < home_wins < 650, f"home wins {home_wins}/1000 too skewed"

    def test_1000_games_elam_activates(self):
        """Elam ending should activate in most games."""
        elam_count = 0
        for seed in range(1000):
            home = _make_varied_team("h", seed * 10)
            away = _make_varied_team("a", seed * 10 + 100, archetype_offset=4)
            result = simulate_game(home, away, DEFAULT_RULESET, seed=seed)
            if result.elam_activated:
                elam_count += 1

        # Elam should activate in nearly all games
        assert elam_count > 950, f"Elam activated in only {elam_count}/1000 games"

    def test_box_score_integrity(self):
        """Box scores should sum to team totals across 100 games."""
        for seed in range(100):
            home = _make_varied_team("h", seed * 10)
            away = _make_varied_team("a", seed * 10 + 100, archetype_offset=4)
            result = simulate_game(home, away, DEFAULT_RULESET, seed=seed)

            home_pts = sum(bs.points for bs in result.box_scores if bs.team_id == "h")
            away_pts = sum(bs.points for bs in result.box_scores if bs.team_id == "a")
            assert home_pts == result.home_score, (
                f"seed={seed}: home box {home_pts} != {result.home_score}"
            )
            assert away_pts == result.away_score, (
                f"seed={seed}: away box {away_pts} != {result.away_score}"
            )

    def test_fg_percentages_reasonable(self):
        """Field goal percentages should be in basketball-like range."""
        all_fga = 0
        all_fgm = 0
        all_3pa = 0
        all_3pm = 0
        for seed in range(500):
            home = _make_varied_team("h", seed * 10)
            away = _make_varied_team("a", seed * 10 + 100, archetype_offset=4)
            result = simulate_game(home, away, DEFAULT_RULESET, seed=seed)
            for bs in result.box_scores:
                all_fga += bs.field_goals_attempted
                all_fgm += bs.field_goals_made
                all_3pa += bs.three_pointers_attempted
                all_3pm += bs.three_pointers_made

        fg_pct = all_fgm / all_fga if all_fga > 0 else 0
        three_pct = all_3pm / all_3pa if all_3pa > 0 else 0

        # 3v3 with contested shots and diverse archetypes: FG% ~20-50%, 3P% ~10-45%
        assert 0.15 < fg_pct < 0.60, f"FG% {fg_pct:.3f} out of range"
        assert 0.10 < three_pct < 0.50, f"3P% {three_pct:.3f} out of range"

    def test_high_scorer_archetype_advantage(self):
        """Sharpshooter teams should outscore lockdown teams on average."""
        sharp_scores = []
        lock_scores = []

        sharp_arch = ARCHETYPES["sharpshooter"]
        lock_arch = ARCHETYPES["lockdown"]

        for seed in range(200):
            sharp_team = _make_team_from_attrs(
                "sharp", apply_variance(sharp_arch, seed, variance=5)
            )
            lock_team = _make_team_from_attrs(
                "lock", apply_variance(lock_arch, seed + 1000, variance=5)
            )
            result = simulate_game(sharp_team, lock_team, DEFAULT_RULESET, seed=seed)
            sharp_scores.append(result.home_score)
            lock_scores.append(result.away_score)

        avg_sharp = sum(sharp_scores) / len(sharp_scores)
        avg_lock = sum(lock_scores) / len(lock_scores)
        # Sharpshooters should generally outscore lockdowns
        assert avg_sharp > avg_lock, (
            f"Sharpshooter avg {avg_sharp:.1f} <= Lockdown avg {avg_lock:.1f}"
        )


def _make_team_from_attrs(team_id: str, attrs: PlayerAttributes) -> Team:
    """Helper: make a team where all agents share the same attributes."""
    agents = [
        Agent(
            id=f"{team_id}-a{i}",
            name=f"Agent-{i}",
            team_id=team_id,
            archetype="generic",
            attributes=attrs,
            is_starter=i < 3,
        )
        for i in range(4)
    ]
    return Team(
        id=team_id,
        name=f"Team-{team_id}",
        venue=Venue(name="Court", capacity=5000),
        agents=agents,
    )

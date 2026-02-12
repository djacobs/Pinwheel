"""Tests for mirror generation (mock) and mirror models."""


from pinwheel.ai.mirror import (
    generate_governance_mirror_mock,
    generate_private_mirror_mock,
    generate_simulation_mirror_mock,
)
from pinwheel.models.mirror import Mirror, MirrorUpdate


class TestSimulationMirrorMock:
    def test_basic_generation(self):
        data = {
            "round_number": 5,
            "games": [
                {
                    "game_id": "g-5-0",
                    "home_team": "Thorns",
                    "away_team": "Voids",
                    "home_score": 55,
                    "away_score": 48,
                    "elam_activated": False,
                    "total_possessions": 60,
                },
            ],
        }
        mirror = generate_simulation_mirror_mock(data, "s-1", 5)
        assert mirror.mirror_type == "simulation"
        assert mirror.round_number == 5
        # Narrative mirrors reference team names, not generic stats
        assert "Thorns" in mirror.content or "Voids" in mirror.content
        assert len(mirror.content) > 20

    def test_close_game_narrative(self):
        data = {
            "round_number": 3,
            "games": [
                {
                    "game_id": "g-3-0",
                    "home_team": "Herons",
                    "away_team": "Hammers",
                    "home_score": 30,
                    "away_score": 27,
                    "elam_activated": True,
                    "total_possessions": 70,
                },
            ],
        }
        mirror = generate_simulation_mirror_mock(data, "s-1", 3)
        # Close games (margin <= 4) should reference the winner
        assert "Herons" in mirror.content or "Hammers" in mirror.content

    def test_no_games(self):
        mirror = generate_simulation_mirror_mock({"games": []}, "s-1", 1)
        assert mirror.mirror_type == "simulation"
        # Empty rounds get a terse message, not "0 games with 0 points"
        assert len(mirror.content) > 0

    def test_blowout_narrative(self):
        data = {
            "games": [
                {
                    "home_team": "Breakers",
                    "away_team": "Thorns",
                    "home_score": 45,
                    "away_score": 30,
                    "elam_activated": False,
                },
                {
                    "home_team": "Herons",
                    "away_team": "Hammers",
                    "home_score": 25,
                    "away_score": 27,
                    "elam_activated": True,
                },
            ]
        }
        mirror = generate_simulation_mirror_mock(data, "s-1", 2)
        # Should mention at least one team name
        content = mirror.content
        has_team = any(
            name in content
            for name in ["Breakers", "Thorns", "Herons", "Hammers"]
        )
        assert has_team


class TestGovernanceMirrorMock:
    def test_with_proposals(self):
        data = {
            "proposals": [{"id": "p-1", "raw_text": "increase 3pt"}],
            "votes": [
                {"vote": "yes"},
                {"vote": "yes"},
                {"vote": "no"},
            ],
            "rules_changed": [],
        }
        mirror = generate_governance_mirror_mock(data, "s-1", 4)
        assert mirror.mirror_type == "governance"
        assert "1 proposal" in mirror.content
        assert "3 votes" in mirror.content
        assert "2 yes" in mirror.content
        assert "1 no" in mirror.content

    def test_no_activity(self):
        data = {"proposals": [], "votes": [], "rules_changed": []}
        mirror = generate_governance_mirror_mock(data, "s-1", 1)
        assert "quiet" in mirror.content.lower() or "no proposals" in mirror.content.lower()

    def test_with_rule_changes(self):
        data = {
            "proposals": [{"id": "p-1"}],
            "votes": [],
            "rules_changed": [{"parameter": "three_point_value"}],
        }
        mirror = generate_governance_mirror_mock(data, "s-1", 5)
        assert "three_point_value" in mirror.content

    def test_id_format(self):
        data = {"proposals": [], "votes": [], "rules_changed": []}
        mirror = generate_governance_mirror_mock(data, "s-1", 7)
        assert mirror.id.startswith("m-gov-7-")


class TestPrivateMirrorMock:
    def test_active_governor(self):
        data = {"proposals_submitted": 2, "votes_cast": 3, "tokens_spent": 2}
        mirror = generate_private_mirror_mock(data, "gov-1", "s-1", 4)
        assert mirror.mirror_type == "private"
        assert mirror.governor_id == "gov-1"
        assert "2 proposal" in mirror.content
        assert "3 vote" in mirror.content

    def test_inactive_governor(self):
        data = {"proposals_submitted": 0, "votes_cast": 0, "tokens_spent": 0}
        mirror = generate_private_mirror_mock(data, "gov-2", "s-1", 4)
        assert "quiet" in mirror.content.lower() or "absence" in mirror.content.lower()

    def test_private_mirror_id(self):
        data = {"proposals_submitted": 1, "votes_cast": 0, "tokens_spent": 1}
        mirror = generate_private_mirror_mock(data, "gov-abc123", "s-1", 3)
        assert "gov-abc1" in mirror.id


class TestMirrorModels:
    def test_mirror_defaults(self):
        m = Mirror(id="m-1", mirror_type="simulation", round_number=5)
        assert m.content == ""
        assert m.team_id == ""
        assert m.governor_id == ""

    def test_private_mirror(self):
        m = Mirror(
            id="m-2",
            mirror_type="private",
            governor_id="gov-1",
            round_number=3,
            content="Reflection text.",
        )
        assert m.governor_id == "gov-1"
        assert m.content == "Reflection text."

    def test_mirror_update(self):
        mu = MirrorUpdate(mirror_id="m-1", mirror_type="governance", round_number=5)
        assert mu.excerpt == ""

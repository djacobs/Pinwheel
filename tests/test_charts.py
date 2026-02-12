"""Unit tests for spider chart geometry and season averages."""

import math

import pytest

from pinwheel.api.charts import (
    ATTRIBUTE_ORDER,
    compute_grid_rings,
    compute_season_averages,
    polygon_points,
    spider_chart_data,
)


class TestSpiderChartData:
    """Tests for spider_chart_data()."""

    def test_basic_all_50(self):
        """All attributes at 50 → points equidistant at half-radius."""
        attrs = {a: 50 for a in ATTRIBUTE_ORDER}
        data = spider_chart_data(attrs, center=150, max_radius=120)

        assert len(data) == 9
        for point in data:
            # Each point should be at radius 60 (50% of 120) from center
            dx = point["x"] - 150
            dy = point["y"] - 150
            dist = math.sqrt(dx**2 + dy**2)
            assert abs(dist - 60) < 0.5, f"{point['attr']} distance {dist} != 60"
            assert point["value"] == 50

    def test_max_all_100(self):
        """All attributes at 100 → points at max radius."""
        attrs = {a: 100 for a in ATTRIBUTE_ORDER}
        data = spider_chart_data(attrs, center=150, max_radius=120)

        for point in data:
            dx = point["x"] - 150
            dy = point["y"] - 150
            dist = math.sqrt(dx**2 + dy**2)
            assert abs(dist - 120) < 0.5, f"{point['attr']} distance {dist} != 120"
            assert point["value"] == 100

    def test_asymmetric(self):
        """One attribute at 100, rest at 10 → one far point, others near center."""
        attrs = {a: 10 for a in ATTRIBUTE_ORDER}
        attrs["scoring"] = 100
        data = spider_chart_data(attrs, center=150, max_radius=120)

        scoring_pt = next(p for p in data if p["attr"] == "scoring")
        dx = scoring_pt["x"] - 150
        dy = scoring_pt["y"] - 150
        scoring_dist = math.sqrt(dx**2 + dy**2)
        assert abs(scoring_dist - 120) < 0.5

        for point in data:
            if point["attr"] != "scoring":
                dx = point["x"] - 150
                dy = point["y"] - 150
                dist = math.sqrt(dx**2 + dy**2)
                assert abs(dist - 12) < 0.5  # 10% of 120

    def test_zero_values(self):
        """All attributes at 0 → all points at center."""
        attrs = {a: 0 for a in ATTRIBUTE_ORDER}
        data = spider_chart_data(attrs, center=150, max_radius=120)

        for point in data:
            assert abs(point["x"] - 150) < 0.01
            assert abs(point["y"] - 150) < 0.01

    def test_missing_attributes(self):
        """Missing attributes default to 0."""
        data = spider_chart_data({}, center=150, max_radius=120)
        for point in data:
            assert point["value"] == 0

    def test_clamping(self):
        """Values above 100 or below 0 are clamped."""
        attrs = {"scoring": 150, "passing": -20}
        data = spider_chart_data(attrs, center=150, max_radius=120)
        scoring_pt = next(p for p in data if p["attr"] == "scoring")
        passing_pt = next(p for p in data if p["attr"] == "passing")
        assert scoring_pt["value"] == 100
        assert passing_pt["value"] == 0

    def test_has_color_and_label(self):
        """Each point has the correct color and label."""
        attrs = {a: 50 for a in ATTRIBUTE_ORDER}
        data = spider_chart_data(attrs)
        for point in data:
            assert len(point["color"]) == 7 and point["color"].startswith("#")
            assert len(point["label"]) <= 3

    def test_first_point_at_top(self):
        """First axis (scoring) should be at the top of the chart (angle = -90°)."""
        attrs = {a: 100 for a in ATTRIBUTE_ORDER}
        data = spider_chart_data(attrs, center=150, max_radius=120)
        scoring_pt = data[0]
        assert scoring_pt["attr"] == "scoring"
        # At angle -90° → x=center, y=center-radius
        assert abs(scoring_pt["x"] - 150) < 0.5
        assert abs(scoring_pt["y"] - 30) < 0.5

    def test_polygon_points_string(self):
        """polygon_points() produces a valid SVG points string."""
        attrs = {a: 50 for a in ATTRIBUTE_ORDER}
        data = spider_chart_data(attrs)
        pts = polygon_points(data)
        # Should have 9 coordinate pairs
        pairs = pts.split(" ")
        assert len(pairs) == 9
        for pair in pairs:
            x, y = pair.split(",")
            float(x)
            float(y)


class TestGridRings:
    """Tests for compute_grid_rings()."""

    def test_four_rings(self):
        rings = compute_grid_rings()
        assert len(rings) == 4

    def test_ring_format(self):
        """Each ring is a space-separated string of 9 coordinate pairs."""
        rings = compute_grid_rings(center=150, max_radius=120)
        for ring in rings:
            pairs = ring.split(" ")
            assert len(pairs) == 9
            for pair in pairs:
                x, y = pair.split(",")
                float(x)
                float(y)

    def test_ring_radii_increase(self):
        """Outer rings should be farther from center than inner rings."""
        rings = compute_grid_rings(center=150, max_radius=120)
        # Check first point of each ring
        distances = []
        for ring in rings:
            first_pair = ring.split(" ")[0]
            x, y = (float(v) for v in first_pair.split(","))
            dist = math.sqrt((x - 150) ** 2 + (y - 150) ** 2)
            distances.append(dist)
        # 25%, 50%, 75%, 100% → strictly increasing
        for i in range(len(distances) - 1):
            assert distances[i] < distances[i + 1]


class TestSeasonAverages:
    """Tests for compute_season_averages()."""

    def test_known_values(self):
        """Two games with known stats → correct per-game averages."""
        box_scores = [
            {
                "points": 20,
                "assists": 4,
                "steals": 2,
                "turnovers": 3,
                "field_goals_made": 8,
                "field_goals_attempted": 16,
                "three_pointers_made": 2,
                "three_pointers_attempted": 5,
                "free_throws_made": 2,
                "free_throws_attempted": 2,
            },
            {
                "points": 30,
                "assists": 6,
                "steals": 0,
                "turnovers": 1,
                "field_goals_made": 12,
                "field_goals_attempted": 20,
                "three_pointers_made": 3,
                "three_pointers_attempted": 7,
                "free_throws_made": 3,
                "free_throws_attempted": 4,
            },
        ]
        avgs = compute_season_averages(box_scores)
        assert avgs["games_played"] == 2
        assert avgs["ppg"] == 25.0
        assert avgs["apg"] == 5.0
        assert avgs["spg"] == 1.0
        assert avgs["topg"] == 2.0
        # FG%: 20/36 ≈ 55.6
        assert avgs["fg_pct"] == pytest.approx(55.6, abs=0.1)
        # 3P%: 5/12 ≈ 41.7
        assert avgs["three_pct"] == pytest.approx(41.7, abs=0.1)
        # FT%: 5/6 ≈ 83.3
        assert avgs["ft_pct"] == pytest.approx(83.3, abs=0.1)

    def test_empty_returns_empty(self):
        """No box scores → empty dict."""
        assert compute_season_averages([]) == {}

    def test_zero_attempts_no_division_error(self):
        """Zero FG/3P/FT attempts → percentages are 0.0, not ZeroDivisionError."""
        box_scores = [
            {
                "points": 0,
                "assists": 0,
                "steals": 0,
                "turnovers": 0,
                "field_goals_made": 0,
                "field_goals_attempted": 0,
                "three_pointers_made": 0,
                "three_pointers_attempted": 0,
                "free_throws_made": 0,
                "free_throws_attempted": 0,
            }
        ]
        avgs = compute_season_averages(box_scores)
        assert avgs["fg_pct"] == 0.0
        assert avgs["three_pct"] == 0.0
        assert avgs["ft_pct"] == 0.0
        assert avgs["games_played"] == 1

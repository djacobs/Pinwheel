"""Spider chart geometry and season average helpers.

Pure functions — no side effects, no database access.
"""

from __future__ import annotations

import math

from pinwheel.models.constants import ATTRIBUTE_ORDER  # noqa: F401 — re-exported for callers

__all__ = ["ATTRIBUTE_ORDER"]

ATTRIBUTE_LABELS: dict[str, str] = {
    "scoring": "SCO",
    "passing": "PAS",
    "defense": "DEF",
    "speed": "SPD",
    "stamina": "STA",
    "iq": "IQ",
    "ego": "EGO",
    "chaotic_alignment": "CHA",
    "fate": "FAT",
}

ATTRIBUTE_COLORS: dict[str, str] = {
    "scoring": "#e94560",
    "passing": "#53d8fb",
    "defense": "#48bb78",
    "speed": "#f0c040",
    "stamina": "#e67e22",
    "iq": "#b794f4",
    "ego": "#fc5c65",
    "chaotic_alignment": "#9b59b6",
    "fate": "#555577",
}

NUM_AXES = len(ATTRIBUTE_ORDER)
ANGLE_STEP = 360 / NUM_AXES  # 40 degrees


def _point(center: float, radius: float, index: int) -> tuple[float, float]:
    """Compute (x, y) for the given axis index at the given radius."""
    angle_deg = index * ANGLE_STEP - 90  # start from top
    angle_rad = math.radians(angle_deg)
    x = center + radius * math.cos(angle_rad)
    y = center + radius * math.sin(angle_rad)
    return (round(x, 2), round(y, 2))


def spider_chart_data(
    attributes: dict[str, float],
    center: float = 150,
    max_radius: float = 120,
) -> list[dict]:
    """Compute SVG coordinates for each attribute vertex.

    Returns a list of 9 dicts with keys:
        x, y       — vertex position
        lx, ly     — label position (slightly beyond vertex)
        attr       — attribute name
        label      — 3-char abbreviation
        value      — numeric value (0–100)
        color      — hex color for this attribute
    """
    points = []
    for i, attr in enumerate(ATTRIBUTE_ORDER):
        value = max(0, min(100, float(attributes.get(attr, 0))))
        r = (value / 100) * max_radius
        x, y = _point(center, r, i)
        # Label position: push out beyond the max radius
        lx, ly = _point(center, max_radius + 18, i)
        points.append(
            {
                "x": x,
                "y": y,
                "lx": lx,
                "ly": ly,
                "attr": attr,
                "label": ATTRIBUTE_LABELS[attr],
                "value": int(value),
                "color": ATTRIBUTE_COLORS[attr],
            }
        )
    return points


def compute_grid_rings(
    center: float = 150,
    max_radius: float = 120,
) -> list[str]:
    """Compute SVG polygon point-strings for 4 concentric grid rings (25/50/75/100%)."""
    rings = []
    for pct in (0.25, 0.50, 0.75, 1.0):
        r = max_radius * pct
        coords = []
        for i in range(NUM_AXES):
            x, y = _point(center, r, i)
            coords.append(f"{x},{y}")
        rings.append(" ".join(coords))
    return rings


def polygon_points(data: list[dict]) -> str:
    """Convert spider_chart_data output to an SVG polygon points string."""
    return " ".join(f"{p['x']},{p['y']}" for p in data)


def axis_lines(
    center: float = 150,
    max_radius: float = 120,
) -> list[dict]:
    """Compute axis line endpoints from center to outer ring."""
    lines = []
    for i in range(NUM_AXES):
        x, y = _point(center, max_radius, i)
        lines.append({"x1": center, "y1": center, "x2": x, "y2": y})
    return lines


def compute_season_averages(
    box_scores: list[dict],
) -> dict[str, float | int]:
    """Compute season averages from a list of box score dicts.

    Each dict should have keys matching BoxScoreRow fields:
        points, assists, steals, turnovers,
        field_goals_made, field_goals_attempted,
        three_pointers_made, three_pointers_attempted,
        free_throws_made, free_throws_attempted

    Returns dict with ppg, apg, spg, topg, fg_pct, three_pct, ft_pct, games_played.
    Returns empty dict if no box scores.
    """
    if not box_scores:
        return {}

    games = len(box_scores)
    total_pts = sum(bs.get("points", 0) for bs in box_scores)
    total_ast = sum(bs.get("assists", 0) for bs in box_scores)
    total_stl = sum(bs.get("steals", 0) for bs in box_scores)
    total_to = sum(bs.get("turnovers", 0) for bs in box_scores)

    total_fgm = sum(bs.get("field_goals_made", 0) for bs in box_scores)
    total_fga = sum(bs.get("field_goals_attempted", 0) for bs in box_scores)
    total_3pm = sum(bs.get("three_pointers_made", 0) for bs in box_scores)
    total_3pa = sum(bs.get("three_pointers_attempted", 0) for bs in box_scores)
    total_ftm = sum(bs.get("free_throws_made", 0) for bs in box_scores)
    total_fta = sum(bs.get("free_throws_attempted", 0) for bs in box_scores)

    return {
        "ppg": round(total_pts / games, 1),
        "apg": round(total_ast / games, 1),
        "spg": round(total_stl / games, 1),
        "topg": round(total_to / games, 1),
        "fg_pct": round(100 * total_fgm / total_fga, 1) if total_fga else 0.0,
        "three_pct": round(100 * total_3pm / total_3pa, 1) if total_3pa else 0.0,
        "ft_pct": round(100 * total_ftm / total_fta, 1) if total_fta else 0.0,
        "games_played": games,
    }

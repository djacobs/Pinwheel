"""Shared constants for Pinwheel models.

Placed here so both the API layer (charts.py) and the database layer
(repository.py) can import them without creating a layer violation.
"""

from __future__ import annotations

ATTRIBUTE_ORDER: list[str] = [
    "scoring",
    "passing",
    "defense",
    "speed",
    "stamina",
    "iq",
    "ego",
    "chaotic_alignment",
    "fate",
]

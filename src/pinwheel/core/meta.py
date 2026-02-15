"""MetaStore — in-memory read/write interface for entity metadata.

Loaded from DB at round start, effects read/write during the round,
flushed to DB at round end. Provides a clean API for effects to
attach arbitrary state to teams, hoopers, games, and seasons without
schema migrations.
"""

from __future__ import annotations

import copy
import logging
from collections import defaultdict

logger = logging.getLogger(__name__)

# Allowed meta value types (matches MetaValue in models/governance.py)
MetaValueType = int | float | str | bool | None


class MetaStore:
    """In-memory metadata cache for game entities.

    Keys are (entity_type, entity_id, field). Values are JSON-safe primitives.
    The store tracks which entries were modified for efficient DB flushing.

    Usage:
        store = MetaStore()
        store.set("team", "team-123", "swagger", 3)
        val = store.get("team", "team-123", "swagger", default=0)
        store.increment("team", "team-123", "swagger", 1)
    """

    def __init__(self) -> None:
        # {entity_type: {entity_id: {field: value}}}
        self._data: dict[str, dict[str, dict[str, MetaValueType]]] = defaultdict(
            lambda: defaultdict(dict)
        )
        self._dirty: set[tuple[str, str]] = set()

    def get(
        self,
        entity_type: str,
        entity_id: str,
        field: str,
        default: MetaValueType = None,
    ) -> MetaValueType:
        """Read a meta value. Returns default if not set."""
        return self._data[entity_type][entity_id].get(field, default)

    def set(
        self,
        entity_type: str,
        entity_id: str,
        field: str,
        value: MetaValueType,
    ) -> None:
        """Write a meta value. Marks the entity as dirty for DB flush."""
        self._data[entity_type][entity_id][field] = value
        self._dirty.add((entity_type, entity_id))

    def increment(
        self,
        entity_type: str,
        entity_id: str,
        field: str,
        amount: int | float = 1,
    ) -> MetaValueType:
        """Increment a numeric meta value. Initializes to 0 if not set."""
        current = self.get(entity_type, entity_id, field, default=0)
        if not isinstance(current, (int, float)):
            current = 0
        new_val = current + amount
        self.set(entity_type, entity_id, field, new_val)
        return new_val

    def decrement(
        self,
        entity_type: str,
        entity_id: str,
        field: str,
        amount: int | float = 1,
    ) -> MetaValueType:
        """Decrement a numeric meta value. Initializes to 0 if not set."""
        return self.increment(entity_type, entity_id, field, -amount)

    def toggle(
        self,
        entity_type: str,
        entity_id: str,
        field: str,
    ) -> bool:
        """Toggle a boolean meta value. Initializes to False if not set."""
        current = self.get(entity_type, entity_id, field, default=False)
        new_val = not bool(current)
        self.set(entity_type, entity_id, field, new_val)
        return new_val

    def get_all(
        self,
        entity_type: str,
        entity_id: str,
    ) -> dict[str, MetaValueType]:
        """Get all meta fields for an entity."""
        return dict(self._data[entity_type][entity_id])

    def get_dirty_entities(self) -> list[tuple[str, str, dict[str, MetaValueType]]]:
        """Return all modified entities and their current meta state.

        Returns list of (entity_type, entity_id, meta_dict) tuples.
        Clears the dirty set after reading.
        """
        result: list[tuple[str, str, dict[str, MetaValueType]]] = []
        for entity_type, entity_id in self._dirty:
            meta = dict(self._data[entity_type][entity_id])
            result.append((entity_type, entity_id, meta))
        self._dirty.clear()
        return result

    def load_entity(
        self,
        entity_type: str,
        entity_id: str,
        meta: dict[str, MetaValueType],
    ) -> None:
        """Load meta from DB into the store. Does NOT mark as dirty."""
        self._data[entity_type][entity_id] = dict(meta)

    def snapshot(self) -> dict[str, dict[str, dict[str, MetaValueType]]]:
        """Return a deep copy of all meta state. Useful for report context."""
        return copy.deepcopy(dict(self._data))

    def entity_count(self) -> int:
        """Total number of entities with meta data."""
        return sum(
            len(entities) for entities in self._data.values()
        )

    def __repr__(self) -> str:
        count = self.entity_count()
        dirty = len(self._dirty)
        return f"MetaStore(entities={count}, dirty={dirty})"

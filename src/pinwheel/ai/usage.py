"""AI usage tracking — record token counts and costs for every API call.

Provides ``record_ai_usage()`` which inserts an ``AIUsageLogRow`` into the
database. All AI call sites (report, commentary, interpreter, classifier)
call this after each Anthropic API response.

Also provides helpers for Messages API features:
- ``cacheable_system()`` — wrap a system prompt for prompt caching
- ``pydantic_to_response_format()`` — convert a Pydantic model to a
  ``response_format`` dict for structured output

Pricing constants live here and should be updated when Anthropic changes
its rates.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from pinwheel.db.models import AIUsageLogRow

if TYPE_CHECKING:
    from pydantic import BaseModel

logger = logging.getLogger(__name__)

# Pricing per million tokens (USD). Update when prices change.
# cache_write_per_mtok is the 25% premium for creating a cache entry.
PRICING: dict[str, dict[str, float]] = {
    "claude-opus-4-6": {
        "input_per_mtok": 15.00,
        "output_per_mtok": 75.00,
        "cache_read_per_mtok": 1.50,
        "cache_write_per_mtok": 18.75,
    },
    "claude-sonnet-4-5-20250929": {
        "input_per_mtok": 3.00,
        "output_per_mtok": 15.00,
        "cache_read_per_mtok": 0.30,
        "cache_write_per_mtok": 3.75,
    },
    "claude-haiku-4-5-20251001": {
        "input_per_mtok": 0.80,
        "output_per_mtok": 4.00,
        "cache_read_per_mtok": 0.08,
        "cache_write_per_mtok": 1.00,
    },
}

# Fallback pricing for unknown models
_DEFAULT_PRICING = {
    "input_per_mtok": 3.00,
    "output_per_mtok": 15.00,
    "cache_read_per_mtok": 0.30,
    "cache_write_per_mtok": 3.75,
}


def compute_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
) -> float:
    """Compute estimated cost in USD for a single API call.

    Includes cache creation tokens (25% premium on first cache write)
    and cache read tokens (90% discount on subsequent reads).
    """
    rates = PRICING.get(model, _DEFAULT_PRICING)
    cost = (
        input_tokens * rates["input_per_mtok"]
        + output_tokens * rates["output_per_mtok"]
        + cache_read_tokens * rates["cache_read_per_mtok"]
        + cache_creation_tokens * rates["cache_write_per_mtok"]
    ) / 1_000_000
    return round(cost, 8)


async def record_ai_usage(
    *,
    session: object,
    call_type: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
    latency_ms: float = 0.0,
    season_id: str = "",
    round_number: int | None = None,
) -> AIUsageLogRow:
    """Record an AI API call to the usage log.

    Parameters
    ----------
    session : AsyncSession
        The SQLAlchemy async session to use for the insert.
    call_type : str
        Identifier for the call site, e.g. "report.simulation",
        "commentary.game", "interpreter.v2", "classifier".
    model : str
        The model name, e.g. "claude-sonnet-4-5-20250929".
    input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens : int
        Token counts from the API response.
    latency_ms : float
        Wall-clock time of the API call in milliseconds.
    season_id : str
        Current season ID (empty string if unavailable).
    round_number : int or None
        Current round number (None if not applicable).

    Returns
    -------
    AIUsageLogRow
        The inserted row.
    """
    cost = compute_cost(
        model, input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens
    )
    row = AIUsageLogRow(
        call_type=call_type,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_creation_tokens=cache_creation_tokens,
        latency_ms=latency_ms,
        cost_usd=cost,
        season_id=season_id,
        round_number=round_number,
    )
    session.add(row)  # type: ignore[union-attr]
    try:
        await session.flush()  # type: ignore[union-attr]
    except Exception:
        # Usage logging should never break the caller.
        logger.warning("Failed to flush AI usage log row", exc_info=True)
    return row


@asynccontextmanager
async def track_latency() -> AsyncGenerator[dict[str, float], None]:
    """Context manager that yields a dict; after exit, 'latency_ms' is set.

    Usage::

        async with track_latency() as timing:
            response = await client.messages.create(...)
        latency = timing["latency_ms"]
    """
    timing: dict[str, float] = {"latency_ms": 0.0}
    start = time.monotonic()
    try:
        yield timing
    finally:
        timing["latency_ms"] = (time.monotonic() - start) * 1000


def extract_usage(response: object) -> tuple[int, int, int, int]:
    """Extract token counts from an API response.

    Returns (input_tokens, output_tokens, cache_read_tokens,
    cache_creation_tokens). Works with the Anthropic SDK ``Message``
    objects.

    ``cache_creation_input_tokens`` is populated on the first request
    that creates a cache entry (charged at a 25% premium). Subsequent
    requests return ``cache_read_input_tokens`` instead (90% discount).
    """
    usage = getattr(response, "usage", None)
    if usage is None:
        return (0, 0, 0, 0)
    input_tokens = getattr(usage, "input_tokens", 0) or 0
    output_tokens = getattr(usage, "output_tokens", 0) or 0
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    cache_creation = getattr(usage, "cache_creation_input_tokens", 0) or 0
    return (input_tokens, output_tokens, cache_read, cache_creation)


# ---------------------------------------------------------------------------
# Messages API helpers
# ---------------------------------------------------------------------------


def cacheable_system(text: str) -> list[dict[str, object]]:
    """Wrap a system prompt string as a cacheable content block.

    The Messages API accepts ``system`` as either a string or a list of
    content blocks. To enable prompt caching, we use the block format
    with ``cache_control: {"type": "ephemeral"}``. Cached content costs
    90% less on subsequent reads within a 5-minute TTL.

    Prompts below the 1,024-token minimum cacheable size will simply
    not be cached (no error).
    """
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]


def pydantic_to_response_format(
    model_class: type[BaseModel], name: str
) -> dict[str, object]:
    """Convert a Pydantic model to a Messages API ``output_config`` dict.

    Uses ``anthropic.transform_schema()`` to sanitize Pydantic's JSON schema
    (adds ``additionalProperties: false``, strips unsupported constraints like
    ``minimum``/``maximum``, etc.). The API guarantees the response conforms
    to the schema, eliminating JSON parsing failures.

    Returns a dict suitable for the ``output_config`` parameter
    (SDK v0.79+ uses ``output_config.format``).
    """
    from anthropic import transform_schema

    return {
        "format": {
            "type": "json_schema",
            "schema": transform_schema(model_class),
        },
    }

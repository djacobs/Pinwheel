"""AI usage tracking — record token counts and costs for every API call.

Provides ``record_ai_usage()`` which inserts an ``AIUsageLogRow`` into the
database. All AI call sites (report, commentary, interpreter, classifier)
call this after each Anthropic API response.

Pricing constants live here and should be updated when Anthropic changes
its rates.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from pinwheel.db.models import AIUsageLogRow

logger = logging.getLogger(__name__)

# Pricing per million tokens (USD). Update when prices change.
PRICING: dict[str, dict[str, float]] = {
    "claude-sonnet-4-5-20250929": {
        "input_per_mtok": 3.00,
        "output_per_mtok": 15.00,
        "cache_read_per_mtok": 0.30,
    },
    "claude-haiku-4-5-20251001": {
        "input_per_mtok": 0.80,
        "output_per_mtok": 4.00,
        "cache_read_per_mtok": 0.08,
    },
}

# Fallback pricing for unknown models
_DEFAULT_PRICING = {
    "input_per_mtok": 3.00,
    "output_per_mtok": 15.00,
    "cache_read_per_mtok": 0.30,
}


def compute_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
) -> float:
    """Compute estimated cost in USD for a single API call."""
    rates = PRICING.get(model, _DEFAULT_PRICING)
    cost = (
        input_tokens * rates["input_per_mtok"]
        + output_tokens * rates["output_per_mtok"]
        + cache_read_tokens * rates["cache_read_per_mtok"]
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
    input_tokens, output_tokens, cache_read_tokens : int
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
    cost = compute_cost(model, input_tokens, output_tokens, cache_read_tokens)
    row = AIUsageLogRow(
        call_type=call_type,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
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


def extract_usage(response: object) -> tuple[int, int, int]:
    """Extract (input_tokens, output_tokens, cache_read_tokens) from an API response.

    Works with the Anthropic SDK ``Message`` objects.
    """
    usage = getattr(response, "usage", None)
    if usage is None:
        return (0, 0, 0)
    input_tokens = getattr(usage, "input_tokens", 0) or 0
    output_tokens = getattr(usage, "output_tokens", 0) or 0
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    return (input_tokens, output_tokens, cache_read)

"""Token economy — balances derived from events, trading between governors.

Token balances are never stored as mutable state. They are computed from
the append-only governance event store on read.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from pinwheel.models.tokens import TokenBalance, Trade

if TYPE_CHECKING:
    from pinwheel.db.repository import Repository

# Default token allocation per governance window
DEFAULT_PROPOSE_PER_WINDOW = 2
DEFAULT_AMEND_PER_WINDOW = 2
DEFAULT_BOOST_PER_WINDOW = 2


async def get_token_balance(repo: Repository, governor_id: str, season_id: str) -> TokenBalance:
    """Derive current token balance from event log.

    Sums: token.regenerated (+), token.spent (-).
    """
    events = await repo.get_events_by_type_and_governor(
        season_id=season_id,
        governor_id=governor_id,
        event_types=["token.regenerated", "token.spent"],
    )

    balance = TokenBalance(
        governor_id=governor_id,
        season_id=season_id,
        propose=0,
        amend=0,
        boost=0,
    )

    for event in events:
        payload = event.payload
        token_type = payload.get("token_type", "")
        amount = payload.get("amount", 0)

        if event.event_type == "token.regenerated":
            if token_type == "propose":
                balance.propose += amount
            elif token_type == "amend":
                balance.amend += amount
            elif token_type == "boost":
                balance.boost += amount
        elif event.event_type == "token.spent":
            if token_type == "propose":
                balance.propose -= amount
            elif token_type == "amend":
                balance.amend -= amount
            elif token_type == "boost":
                balance.boost -= amount

    return balance


async def regenerate_tokens(
    repo: Repository,
    governor_id: str,
    team_id: str,
    season_id: str,
    propose_amount: int = DEFAULT_PROPOSE_PER_WINDOW,
    amend_amount: int = DEFAULT_AMEND_PER_WINDOW,
    boost_amount: int = DEFAULT_BOOST_PER_WINDOW,
) -> None:
    """Grant tokens to a governor at the start of a governance window."""
    for token_type, amount in [
        ("propose", propose_amount),
        ("amend", amend_amount),
        ("boost", boost_amount),
    ]:
        await repo.append_event(
            event_type="token.regenerated",
            aggregate_id=governor_id,
            aggregate_type="token",
            season_id=season_id,
            governor_id=governor_id,
            team_id=team_id,
            payload={"token_type": token_type, "amount": amount, "reason": "window_regen"},
        )


async def has_token(
    repo: Repository, governor_id: str, season_id: str, token_type: str
) -> bool:
    """Check if governor has at least 1 token of the given type."""
    balance = await get_token_balance(repo, governor_id, season_id)
    return getattr(balance, token_type, 0) > 0


# --- Trading ---


async def offer_trade(
    repo: Repository,
    from_governor: str,
    from_team_id: str,
    to_governor: str,
    to_team_id: str,
    season_id: str,
    offered_type: str,
    offered_amount: int,
    requested_type: str,
    requested_amount: int,
) -> Trade:
    """Create a trade offer between two governors."""
    trade_id = str(uuid.uuid4())
    trade = Trade(
        id=trade_id,
        from_governor=from_governor,
        to_governor=to_governor,
        from_team_id=from_team_id,
        to_team_id=to_team_id,
        offered_type=offered_type,  # type: ignore[arg-type]
        offered_amount=offered_amount,
        requested_type=requested_type,  # type: ignore[arg-type]
        requested_amount=requested_amount,
    )

    await repo.append_event(
        event_type="trade.offered",
        aggregate_id=trade_id,
        aggregate_type="trade",
        season_id=season_id,
        governor_id=from_governor,
        team_id=from_team_id,
        payload=trade.model_dump(mode="json"),
    )

    return trade


async def accept_trade(
    repo: Repository,
    trade: Trade,
    season_id: str,
) -> Trade:
    """Accept a trade. Transfer tokens between governors via events."""
    # Debit offerer
    await repo.append_event(
        event_type="token.spent",
        aggregate_id=trade.from_governor,
        aggregate_type="token",
        season_id=season_id,
        governor_id=trade.from_governor,
        payload={
            "token_type": trade.offered_type,
            "amount": trade.offered_amount,
            "reason": f"trade:{trade.id}:sent",
        },
    )
    # Credit receiver
    await repo.append_event(
        event_type="token.regenerated",
        aggregate_id=trade.to_governor,
        aggregate_type="token",
        season_id=season_id,
        governor_id=trade.to_governor,
        payload={
            "token_type": trade.offered_type,
            "amount": trade.offered_amount,
            "reason": f"trade:{trade.id}:received",
        },
    )
    # Debit receiver (what they gave)
    await repo.append_event(
        event_type="token.spent",
        aggregate_id=trade.to_governor,
        aggregate_type="token",
        season_id=season_id,
        governor_id=trade.to_governor,
        payload={
            "token_type": trade.requested_type,
            "amount": trade.requested_amount,
            "reason": f"trade:{trade.id}:sent",
        },
    )
    # Credit offerer (what they got)
    await repo.append_event(
        event_type="token.regenerated",
        aggregate_id=trade.from_governor,
        aggregate_type="token",
        season_id=season_id,
        governor_id=trade.from_governor,
        payload={
            "token_type": trade.requested_type,
            "amount": trade.requested_amount,
            "reason": f"trade:{trade.id}:received",
        },
    )

    await repo.append_event(
        event_type="trade.accepted",
        aggregate_id=trade.id,
        aggregate_type="trade",
        season_id=season_id,
        governor_id=trade.to_governor,
        payload={"trade_id": trade.id},
    )

    trade.status = "accepted"
    return trade

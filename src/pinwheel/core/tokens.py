"""Token economy — balances derived from events, trading between governors.

Token balances are never stored as mutable state. They are computed from
the append-only governance event store on read.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from pinwheel.models.tokens import HooperTrade, TokenBalance, Trade

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


# --- Hooper Trading ---


async def propose_hooper_trade(
    repo: Repository,
    proposer_id: str,
    from_team_id: str,
    to_team_id: str,
    offered_hooper_ids: list[str],
    requested_hooper_ids: list[str],
    offered_hooper_names: list[str],
    requested_hooper_names: list[str],
    from_team_name: str,
    to_team_name: str,
    required_voters: list[str],
    season_id: str,
    from_team_voters: list[str] | None = None,
    to_team_voters: list[str] | None = None,
) -> HooperTrade:
    """Create an agent trade proposal between two teams."""
    trade_id = str(uuid.uuid4())
    trade = HooperTrade(
        id=trade_id,
        from_team_id=from_team_id,
        to_team_id=to_team_id,
        offered_hooper_ids=offered_hooper_ids,
        requested_hooper_ids=requested_hooper_ids,
        offered_hooper_names=offered_hooper_names,
        requested_hooper_names=requested_hooper_names,
        proposed_by=proposer_id,
        required_voters=required_voters,
        from_team_voters=from_team_voters or [],
        to_team_voters=to_team_voters or [],
        from_team_name=from_team_name,
        to_team_name=to_team_name,
    )
    await repo.append_event(
        event_type="hooper_trade.proposed",
        aggregate_id=trade_id,
        aggregate_type="hooper_trade",
        season_id=season_id,
        governor_id=proposer_id,
        team_id=from_team_id,
        payload=trade.model_dump(mode="json"),
    )
    return trade


def vote_hooper_trade(trade: HooperTrade, governor_id: str, vote: str) -> HooperTrade:
    """Record a governor's vote on an agent trade. Returns updated trade.

    Does NOT check authorization — caller must verify governor is in required_voters.
    """
    trade.votes[governor_id] = vote
    return trade


def tally_hooper_trade(trade: HooperTrade) -> tuple[bool, bool, bool]:
    """Tally votes for an agent trade.

    Returns (all_voted, from_team_approved, to_team_approved).
    Approval requires majority-yes among each team's governors independently.
    A team cannot be forced into a trade they unanimously reject.
    """
    all_voted = len(trade.votes) >= len(trade.required_voters)
    if not all_voted:
        return False, False, False

    from_yes = sum(1 for vid in trade.from_team_voters if trade.votes.get(vid) == "yes")
    from_no = sum(1 for vid in trade.from_team_voters if trade.votes.get(vid) == "no")
    to_yes = sum(1 for vid in trade.to_team_voters if trade.votes.get(vid) == "yes")
    to_no = sum(1 for vid in trade.to_team_voters if trade.votes.get(vid) == "no")

    from_ok = from_yes > from_no
    to_ok = to_yes > to_no
    return True, from_ok, to_ok


async def execute_hooper_trade(
    repo: Repository,
    trade: HooperTrade,
    season_id: str,
) -> None:
    """Execute an approved hooper trade — swap hoopers between teams."""
    for hooper_id in trade.offered_hooper_ids:
        await repo.swap_hooper_team(hooper_id, trade.to_team_id)
    for hooper_id in trade.requested_hooper_ids:
        await repo.swap_hooper_team(hooper_id, trade.from_team_id)
    trade.status = "approved"
    await repo.append_event(
        event_type="hooper_trade.executed",
        aggregate_id=trade.id,
        aggregate_type="hooper_trade",
        season_id=season_id,
        governor_id=trade.proposed_by,
        team_id=trade.from_team_id,
        payload=trade.model_dump(mode="json"),
    )

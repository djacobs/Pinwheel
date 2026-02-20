"""Refund tokens for proposals stuck in pending_interpretation state.

Finds all proposal.pending_interpretation events that have no corresponding
interpretation_ready or interpretation_expired event, then:
1. Appends a proposal.interpretation_expired event for each
2. Appends a token.regenerated event to refund the PROPOSE token

Safe to run multiple times (idempotent — skips already-resolved proposals).

Usage:
    # Dry run (default — shows what would change, changes nothing):
    python scripts/refund_stuck_proposals.py

    # Apply changes:
    python scripts/refund_stuck_proposals.py --apply

    # On Fly.io production:
    flyctl ssh console -C "python scripts/refund_stuck_proposals.py --apply"
"""

from __future__ import annotations

import asyncio
import os
import sys

from pinwheel.db.engine import create_engine, get_session
from pinwheel.db.models import Base
from pinwheel.db.repository import Repository


async def main(apply: bool = False) -> None:
    """Find and refund stuck pending interpretations."""
    db_url = os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///pinwheel.db")
    engine = create_engine(db_url)

    async with get_session(engine) as session:
        repo = Repository(session)

        all_seasons = await repo.get_all_seasons()
        if not all_seasons:
            print("No seasons found.")
            return

        total_found = 0
        total_refunded = 0

        for season in all_seasons:
            sid = season.id
            pending_events = await repo.get_events_by_type(
                season_id=sid,
                event_types=["proposal.pending_interpretation"],
            )
            ready_events = await repo.get_events_by_type(
                season_id=sid,
                event_types=["proposal.interpretation_ready"],
            )
            expired_events = await repo.get_events_by_type(
                season_id=sid,
                event_types=["proposal.interpretation_expired"],
            )

            resolved_ids = {e.aggregate_id for e in ready_events} | {
                e.aggregate_id for e in expired_events
            }

            stuck = [ev for ev in pending_events if ev.aggregate_id not in resolved_ids]
            if not stuck:
                continue

            total_found += len(stuck)
            print(f"\nSeason: {season.name} ({sid})")
            print(f"  Stuck pending interpretations: {len(stuck)}")

            for ev in stuck:
                raw_text = ev.payload.get("raw_text", "<no text>")
                token_cost = ev.payload.get("token_cost", 1)
                print(f"  - [{ev.aggregate_id[:8]}] governor={ev.governor_id} "
                      f"cost={token_cost} text={raw_text[:60]!r}")

                if apply:
                    # 1. Expire the pending interpretation
                    await repo.append_event(
                        event_type="proposal.interpretation_expired",
                        aggregate_id=ev.aggregate_id,
                        aggregate_type="proposal",
                        season_id=sid,
                        governor_id=ev.governor_id,
                        payload={
                            "reason": "manual_refund_script",
                            "raw_text": raw_text,
                        },
                    )
                    # 2. Refund the token
                    if isinstance(token_cost, (int, float)):
                        await repo.append_event(
                            event_type="token.regenerated",
                            aggregate_id=ev.governor_id,
                            aggregate_type="token",
                            season_id=sid,
                            governor_id=ev.governor_id,
                            payload={
                                "token_type": "propose",
                                "amount": int(token_cost),
                                "reason": "manual_refund_stuck_proposal",
                            },
                        )
                    total_refunded += 1
                    print(f"    -> REFUNDED (token_cost={token_cost})")

        if apply:
            await session.commit()

        print(f"\n{'='*50}")
        print(f"Total stuck proposals found: {total_found}")
        if apply:
            print(f"Total refunded: {total_refunded}")
        else:
            print("DRY RUN — no changes made. Pass --apply to refund.")

    await engine.dispose()


if __name__ == "__main__":
    apply = "--apply" in sys.argv
    asyncio.run(main(apply=apply))

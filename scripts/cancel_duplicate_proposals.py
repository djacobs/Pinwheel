"""Cancel duplicate proposals, keeping only the specified ones.

Usage:
    python scripts/cancel_duplicate_proposals.py          # dry-run
    python scripts/cancel_duplicate_proposals.py --apply  # write to DB
"""

import asyncio
import sys

# IDs to KEEP (batch 3, 03:07-03:09 Feb 20 — real AI interpretation, high confidence)
KEEP_IDS = {
    "27093edb",  # "la pelota es lava"
    "cabc90e9",  # "baskets made from inside the key score 0 points"
    "dcc9cb1d",  # "the more baskets a hooper scores, the more their ability scores go up."
    "e3806c1d",  # "no one can hold the ball for more than 4 seconds"
    "5fbbf870",  # "no one can hold the ball longer than 3 seconds"
}


async def main(apply: bool) -> None:
    import os

    from sqlalchemy.ext.asyncio import create_async_engine

    from pinwheel.db.engine import get_session
    from pinwheel.db.repository import Repository

    db_url = os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///pinwheel.db")
    engine = create_async_engine(db_url)

    async with get_session(engine) as session:
        repo = Repository(session)

        # Find all submitted proposals in the active season
        active_season = await repo.get_active_season()
        if not active_season:
            print("No active season found.")
            return

        submitted = await repo.get_events_by_type(
            season_id=active_season.id,
            event_types=["proposal.submitted"],
        )
        # Also get cancelled events to avoid double-cancelling
        already_cancelled = await repo.get_events_by_type(
            season_id=active_season.id,
            event_types=["proposal.cancelled"],
        )
        already_cancelled_ids = {
            e.payload.get("proposal_id", e.aggregate_id)
            for e in already_cancelled
        }

        to_cancel = []
        for e in submitted:
            p_data = e.payload
            pid = p_data.get("id", "")
            if not pid:
                continue
            # Check if this proposal's short ID matches any KEEP_IDS prefix
            if any(pid.startswith(keep) for keep in KEEP_IDS):
                print(f"  KEEP  {pid[:8]} | \"{p_data.get('raw_text', '')[:60]}\"")
                continue
            if pid in already_cancelled_ids or any(
                pid.startswith(c) for c in already_cancelled_ids
            ):
                print(f"  SKIP  {pid[:8]} | already cancelled")
                continue
            text = p_data.get("raw_text", p_data.get("text", "?"))
            print(f"  CANCEL {pid[:8]} | \"{text[:60]}\"")
            to_cancel.append(pid)

        print(f"\n{len(to_cancel)} proposals to cancel, {len(KEEP_IDS)} to keep.")

        if not to_cancel:
            print("Nothing to cancel.")
            return

        if not apply:
            print("\nDry-run — pass --apply to write changes.")
            return

        for pid in to_cancel:
            await repo.append_event(
                event_type="proposal.cancelled",
                aggregate_id=pid,
                aggregate_type="proposal",
                season_id=active_season.id,
                payload={
                    "proposal_id": pid,
                    "reason": "duplicate_resubmission",
                },
            )
        await session.commit()
        print(f"\nCancelled {len(to_cancel)} proposals.")

    await engine.dispose()


if __name__ == "__main__":
    apply = "--apply" in sys.argv
    asyncio.run(main(apply))

"""Resubmit 5 proposals that never took effect, with real AI interpretation.

Proposals are resubmitted gratis (no token debit) into the current season.
Each gets a fresh interpret_proposal_v2 call so effects_v2 is persisted.
Proposals are auto-confirmed and ready for voting.

Also refunds tokens from the original submissions that never took effect.

Usage:
    # Dry run (shows what would happen):
    python scripts/resubmit_proposals.py

    # Apply:
    python scripts/resubmit_proposals.py --apply

    # On Fly.io production:
    flyctl ssh console -C "python scripts/resubmit_proposals.py --apply"
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# The 5 proposals to resubmit, with their original metadata.
PROPOSALS = [
    {
        "label": "#8 Adriana 'la pelota es lava'",
        "original_id": "25893d00-a445-4654-aa5d-5f8ed16d02b5",
        "original_season_id": "ee0c7acb-f8e2-41e6-8269-a3ef2655cf04",
        "governor_username": "Adriana",
        "raw_text": "la pelota es lava",
        "original_cost": 1,
    },
    {
        "label": "#9 Rob Drimmie 'baskets from inside the key score 0 points'",
        "original_id": "2de65e4e-aeae-412f-8d7d-c45063937594",
        "original_season_id": "52a26c1a-6606-4ee1-bb9e-afe11e3c4117",
        "governor_username": "Rob Drimmie",
        "raw_text": "baskets made from inside the key score 0 points",
        "original_cost": 1,
    },
    {
        "label": "#10 .djacobs 'the more baskets...'",
        "original_id": "045459b8-f8df-42de-96a4-69d5234b7e55",
        "original_season_id": "52a26c1a-6606-4ee1-bb9e-afe11e3c4117",
        "governor_username": ".djacobs",
        "raw_text": "the more baskets a hooper scores, the more their ability scores go up.",
        "original_cost": 1,
    },
    {
        "label": "#14 JudgeJedd 'no hold > 4 sec'",
        "original_id": "5f2bac5a-67a9-4d66-bb20-436047536b15",
        "original_season_id": "58fa5666-8f8d-40ee-bfbb-fdeee4e86009",
        "governor_username": "JudgeJedd",
        "raw_text": "no one can hold the ball for more than 4 seconds",
        "original_cost": 1,
    },
    {
        "label": "#15 JudgeJedd 'no hold > 3 sec'",
        "original_id": "fc74171a-1291-4319-8d86-21fc4ed2900a",
        "original_season_id": "ab5505f2-136c-411a-8a8f-305d286ae0d7",
        "governor_username": "JudgeJedd",
        "raw_text": "no one can hold the ball longer than 3 seconds",
        "original_cost": 1,
    },
]


async def main(apply: bool = False) -> None:
    """Resubmit proposals with real AI interpretation."""
    from pinwheel.ai.interpreter import interpret_proposal_v2
    from pinwheel.core.governance import confirm_proposal, submit_proposal
    from pinwheel.db.engine import create_engine, get_session
    from pinwheel.db.repository import Repository
    from pinwheel.models.rules import RuleSet

    db_url = os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///pinwheel.db")
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    engine = create_engine(db_url)

    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set. Cannot interpret proposals.")
        return

    async with get_session(engine) as session:
        repo = Repository(session)

        # Get current season
        active_season = await repo.get_active_season()
        if not active_season:
            print("ERROR: No active season found.")
            return

        season_id = active_season.id
        print(f"Current season: {active_season.name} ({season_id})")

        # Get current ruleset
        ruleset_data = active_season.current_ruleset or active_season.starting_ruleset
        ruleset = RuleSet(**ruleset_data) if isinstance(ruleset_data, dict) else RuleSet()

        # Build governor lookup: username -> (player_id, team_id)
        all_players = await repo.get_all_players()
        governor_map: dict[str, tuple[str, str]] = {}
        for p in all_players:
            governor_map[p.username] = (p.id, p.team_id or "")

        print(f"\nGovernors found: {list(governor_map.keys())}")
        print()

        for entry in PROPOSALS:
            label = entry["label"]
            raw_text = entry["raw_text"]
            username = entry["governor_username"]
            original_cost = entry["original_cost"]
            original_season_id = entry["original_season_id"]

            print("=" * 60)
            print(f"  {label}")
            print(f"  Text: {raw_text}")

            if username not in governor_map:
                print(f"  SKIP: Governor '{username}' not found in player roster")
                continue

            governor_id, team_id = governor_map[username]
            print(f"  Governor: {username} ({governor_id[:12]}...)")
            print(f"  Team ID:  {team_id[:12]}...")

            if not apply:
                print("  [DRY RUN] Would interpret + submit + confirm")
                print()
                continue

            # Step 1: Refund original token cost
            print(f"  Refunding original cost ({original_cost} PROPOSE)...")
            await repo.append_event(
                event_type="token.regenerated",
                aggregate_id=governor_id,
                aggregate_type="token",
                season_id=original_season_id,
                governor_id=governor_id,
                payload={
                    "token_type": "propose",
                    "amount": original_cost,
                    "reason": f"resubmit_refund:{entry['original_id']}",
                },
            )

            # Step 2: Interpret with real AI
            print("  Calling interpret_proposal_v2...")
            try:
                interpretation_v2 = await interpret_proposal_v2(
                    raw_text=raw_text,
                    ruleset=ruleset,
                    api_key=api_key,
                    season_id=season_id,
                )
            except Exception as exc:
                print(f"  ERROR: AI interpretation failed: {exc}")
                continue

            interpretation_v1 = interpretation_v2.to_rule_interpretation()

            print(f"  Confidence: {interpretation_v2.confidence}")
            print(f"  Effects: {len(interpretation_v2.effects)}")
            for eff in interpretation_v2.effects:
                print(f"    - {eff.effect_type}: {eff.description or ''}")
            print(f"  Impact: {interpretation_v2.impact_analysis[:80]}...")
            if interpretation_v2.injection_flagged:
                print("  WARNING: injection_flagged=True")
            if interpretation_v2.rejection_reason:
                print(f"  WARNING: rejection_reason={interpretation_v2.rejection_reason}")

            # Step 3: Submit (gratis — token_already_spent=True)
            print("  Submitting proposal (gratis)...")
            proposal = await submit_proposal(
                repo=repo,
                governor_id=governor_id,
                team_id=team_id,
                season_id=season_id,
                window_id="resubmit",
                raw_text=raw_text,
                interpretation=interpretation_v1,
                ruleset=ruleset,
                token_already_spent=True,
                interpretation_v2=interpretation_v2,
            )
            print(f"  New proposal ID: {proposal.id}")
            print(f"  Tier: {proposal.tier}")

            # Step 4: Confirm (open for voting)
            print("  Confirming (open for voting)...")
            proposal = await confirm_proposal(
                repo, proposal, interpretation_v2=interpretation_v2,
            )
            print(f"  Status: {proposal.status}")
            print()

        if apply:
            await session.commit()
            print("=" * 60)
            print("DONE — all proposals resubmitted and confirmed.")
            print("Governors can now vote on them via /vote in Discord.")
        else:
            print("=" * 60)
            print("DRY RUN — no changes made. Pass --apply to execute.")

    await engine.dispose()


if __name__ == "__main__":
    apply_flag = "--apply" in sys.argv
    asyncio.run(main(apply=apply_flag))

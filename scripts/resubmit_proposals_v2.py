"""Resubmit 5 proposals with structured output interpreter fix.

Same 5 proposals as before, but this time the interpreter uses
output_config for guaranteed valid JSON. No token refund needed
(already refunded in Session 113).

Usage:
    # Dry run:
    python scripts/resubmit_proposals_v2.py

    # Apply:
    python scripts/resubmit_proposals_v2.py --apply

    # On Fly.io production:
    flyctl ssh console -C "python scripts/resubmit_proposals_v2.py --apply"
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

PROPOSALS = [
    {
        "label": "#8 Adriana 'la pelota es lava'",
        "governor_username": "Adriana",
        "raw_text": "la pelota es lava",
    },
    {
        "label": "#9 Rob Drimmie 'baskets from inside the key score 0 points'",
        "governor_username": "Rob Drimmie",
        "raw_text": "baskets made from inside the key score 0 points",
    },
    {
        "label": "#10 .djacobs 'the more baskets...'",
        "governor_username": ".djacobs",
        "raw_text": "the more baskets a hooper scores, the more their ability scores go up.",
    },
    {
        "label": "#14 JudgeJedd 'no hold > 4 sec'",
        "governor_username": "JudgeJedd",
        "raw_text": "no one can hold the ball for more than 4 seconds",
    },
    {
        "label": "#15 JudgeJedd 'no hold > 3 sec'",
        "governor_username": "JudgeJedd",
        "raw_text": "no one can hold the ball longer than 3 seconds",
    },
]


async def main(apply: bool = False) -> None:
    """Resubmit proposals with structured output interpreter."""
    from pinwheel.ai.interpreter import interpret_proposal_v2
    from pinwheel.core.governance import confirm_proposal, submit_proposal
    from pinwheel.db.engine import create_engine, get_session
    from pinwheel.db.repository import Repository
    from pinwheel.models.rules import RuleSet

    db_url = os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///pinwheel.db")
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    engine = create_engine(db_url)

    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set.")
        return

    async with get_session(engine) as session:
        repo = Repository(session)

        active_season = await repo.get_active_season()
        if not active_season:
            print("ERROR: No active season found.")
            return

        season_id = active_season.id
        print(f"Current season: {active_season.name} ({season_id})")

        ruleset_data = active_season.current_ruleset or active_season.starting_ruleset
        ruleset = RuleSet(**ruleset_data) if isinstance(ruleset_data, dict) else RuleSet()

        all_players = await repo.get_all_players()
        governor_map: dict[str, tuple[str, str]] = {}
        for p in all_players:
            governor_map[p.username] = (p.id, p.team_id or "")

        print(f"Governors: {list(governor_map.keys())}\n")

        successes = 0
        mock_fallbacks = 0

        for entry in PROPOSALS:
            label = entry["label"]
            raw_text = entry["raw_text"]
            username = entry["governor_username"]

            print("=" * 60)
            print(f"  {label}")
            print(f"  Text: {raw_text}")

            if username not in governor_map:
                print(f"  SKIP: Governor '{username}' not found")
                continue

            governor_id, team_id = governor_map[username]

            if not apply:
                print("  [DRY RUN] Would interpret + submit + confirm")
                print()
                continue

            # Interpret with fixed structured output
            print("  Calling interpret_proposal_v2 (structured output)...")
            try:
                interpretation_v2 = await interpret_proposal_v2(
                    raw_text=raw_text,
                    ruleset=ruleset,
                    api_key=api_key,
                    season_id=season_id,
                    db_session=session,
                )
            except Exception as exc:
                print(f"  ERROR: AI interpretation failed: {exc}")
                continue

            is_mock = getattr(interpretation_v2, "is_mock_fallback", False)
            interpretation_v1 = interpretation_v2.to_rule_interpretation()

            print(f"  Confidence: {interpretation_v2.confidence}")
            print(f"  Mock fallback: {is_mock}")
            print(f"  Effects: {len(interpretation_v2.effects)}")
            for eff in interpretation_v2.effects:
                print(f"    - {eff.effect_type}: {eff.description or ''}")
            print(f"  Impact: {interpretation_v2.impact_analysis[:120]}")

            if is_mock:
                mock_fallbacks += 1
                print("  WARNING: Still fell back to mock!")
            else:
                successes += 1

            # Submit gratis (no token debit — already refunded)
            print("  Submitting (gratis)...")
            proposal = await submit_proposal(
                repo=repo,
                governor_id=governor_id,
                team_id=team_id,
                season_id=season_id,
                window_id="resubmit-v2",
                raw_text=raw_text,
                interpretation=interpretation_v1,
                ruleset=ruleset,
                token_already_spent=True,
                interpretation_v2=interpretation_v2,
            )
            print(f"  New proposal ID: {proposal.id}")

            # Confirm (open for voting)
            proposal = await confirm_proposal(
                repo, proposal, interpretation_v2=interpretation_v2,
            )
            print(f"  Status: {proposal.status}\n")

        if apply:
            await session.commit()
            print("=" * 60)
            print(f"DONE — {successes} real AI, {mock_fallbacks} mock fallback")
        else:
            print("=" * 60)
            print("DRY RUN — pass --apply to execute.")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main(apply="--apply" in sys.argv))

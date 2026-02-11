"""AI mirror generation — Claude-powered reflections on gameplay and governance.

Three mirror types for Day 3:
- Simulation mirror: reflects on game results, statistical patterns, emergent behavior
- Governance mirror: reflects on proposal patterns, voting dynamics, rule evolution
- Private mirror: reflects on a single governor's behavior (visible only to them)

All mirrors follow the same constraint: they DESCRIBE patterns, never PRESCRIBE actions.
The AI observes; humans decide.
"""

from __future__ import annotations

import json
import logging
import uuid

import anthropic

from pinwheel.models.mirror import Mirror

logger = logging.getLogger(__name__)


SIMULATION_MIRROR_PROMPT = """\
You are the Social Mirror for Pinwheel Fates, a 3v3 basketball governance game.

Your job: reflect on the round's game results. Describe patterns, surprises, and emergent behavior.

## Rules
1. You DESCRIBE. You never PRESCRIBE. Never say "players should" or "the league needs to."
2. You are observing a simulated basketball league. The games are auto-simulated; no humans play.
3. Human "governors" control the RULES of the game. Your job is to make patterns visible.
4. Be concise (2-4 paragraphs). Be vivid. Channel a sports journalist who sees the deeper story.
5. Note any statistical anomalies, streaks, or effects of recent rule changes.
6. If the Elam Ending activated, comment on how it shaped the game's outcome.

## Current Round Data

{round_data}
"""

GOVERNANCE_MIRROR_PROMPT = """\
You are the Governance Mirror for Pinwheel Fates, a 3v3 basketball governance game.

Your job: reflect on governance activity this round. Describe voting patterns, proposal themes, \
and how the rule space is evolving.

## Rules
1. You DESCRIBE. You never PRESCRIBE. Never say "governors should" or "the league needs to."
2. Be concise (2-3 paragraphs). Note trends — are proposals getting bolder? Is consensus forming?
3. If rules changed this round, reflect on what the change reveals about the community's values.
4. If proposals failed, note what that tells us about disagreement or shared priorities.

## Governance Activity

{governance_data}
"""

PRIVATE_MIRROR_PROMPT = """\
You are generating a Private Mirror for governor "{governor_id}" in Pinwheel Fates.

A private mirror reflects a governor's OWN behavior back to them. Only they see this.
It helps them understand their patterns without telling them what to do.

## Rules
1. You DESCRIBE their behavior patterns. You never PRESCRIBE actions.
2. Be concise (1-2 paragraphs). Be specific to THIS governor's actions.
3. Note: voting patterns, proposal themes, token usage, consistency of philosophy.
4. Never compare them to other specific governors. Reflect, don't rank.
5. If they haven't been active, note the absence without judgment.

## Governor Activity

{governor_data}
"""


async def generate_simulation_mirror(
    round_data: dict,
    season_id: str,
    round_number: int,
    api_key: str,
) -> Mirror:
    """Generate a simulation mirror using Claude."""
    content = await _call_claude(
        system=SIMULATION_MIRROR_PROMPT.format(round_data=json.dumps(round_data, indent=2)),
        user_message="Generate a simulation mirror for this round.",
        api_key=api_key,
    )
    return Mirror(
        id=f"m-sim-{round_number}-{uuid.uuid4().hex[:8]}",
        mirror_type="simulation",
        round_number=round_number,
        content=content,
    )


async def generate_governance_mirror(
    governance_data: dict,
    season_id: str,
    round_number: int,
    api_key: str,
) -> Mirror:
    """Generate a governance mirror using Claude."""
    content = await _call_claude(
        system=GOVERNANCE_MIRROR_PROMPT.format(
            governance_data=json.dumps(governance_data, indent=2)
        ),
        user_message="Generate a governance mirror for this round.",
        api_key=api_key,
    )
    return Mirror(
        id=f"m-gov-{round_number}-{uuid.uuid4().hex[:8]}",
        mirror_type="governance",
        round_number=round_number,
        content=content,
    )


async def generate_private_mirror(
    governor_data: dict,
    governor_id: str,
    season_id: str,
    round_number: int,
    api_key: str,
) -> Mirror:
    """Generate a private mirror for a specific governor."""
    content = await _call_claude(
        system=PRIVATE_MIRROR_PROMPT.format(
            governor_id=governor_id,
            governor_data=json.dumps(governor_data, indent=2),
        ),
        user_message=f"Generate a private mirror for governor {governor_id}.",
        api_key=api_key,
    )
    return Mirror(
        id=f"m-priv-{round_number}-{uuid.uuid4().hex[:8]}",
        mirror_type="private",
        round_number=round_number,
        governor_id=governor_id,
        content=content,
    )


async def _call_claude(system: str, user_message: str, api_key: str) -> str:
    """Make a Claude API call for mirror generation."""
    try:
        client = anthropic.AsyncAnthropic(api_key=api_key)
        response = await client.messages.create(
            model="claude-sonnet-4-5-20250929",
            max_tokens=800,
            system=system,
            messages=[{"role": "user", "content": user_message}],
        )
        return response.content[0].text
    except anthropic.APIError as e:
        logger.error("Mirror generation API error: %s", e)
        return f"[Mirror generation failed: {e}]"


# --- Mock implementations for testing ---


def generate_simulation_mirror_mock(
    round_data: dict,
    season_id: str,
    round_number: int,
) -> Mirror:
    """Mock simulation mirror for testing without API key."""
    games = round_data.get("games", [])
    num_games = len(games)
    total_points = sum(g.get("home_score", 0) + g.get("away_score", 0) for g in games)
    elam_count = sum(1 for g in games if g.get("elam_activated", False))

    lines = [f"Round {round_number} delivered {num_games} games with {total_points} total points."]
    if elam_count:
        lines.append(
            f"The Elam Ending activated in {elam_count} game(s), "
            "adding dramatic tension to otherwise conventional contests."
        )
    if games:
        high_scorer = max(games, key=lambda g: max(g.get("home_score", 0), g.get("away_score", 0)))
        top_score = max(high_scorer.get("home_score", 0), high_scorer.get("away_score", 0))
        lines.append(f"The highest score this round was {top_score}.")

    return Mirror(
        id=f"m-sim-{round_number}-mock",
        mirror_type="simulation",
        round_number=round_number,
        content=" ".join(lines),
    )


def generate_governance_mirror_mock(
    governance_data: dict,
    season_id: str,
    round_number: int,
) -> Mirror:
    """Mock governance mirror for testing."""
    proposals = governance_data.get("proposals", [])
    votes = governance_data.get("votes", [])
    rules_changed = governance_data.get("rules_changed", [])

    lines = []
    if proposals:
        lines.append(
            f"Round {round_number} saw {len(proposals)} proposal(s) enter the governance arena."
        )
    else:
        lines.append(
            f"Round {round_number} was quiet on the governance front — no proposals filed."
        )

    if votes:
        yes_count = sum(1 for v in votes if v.get("vote") == "yes")
        no_count = sum(1 for v in votes if v.get("vote") == "no")
        lines.append(f"Governors cast {len(votes)} votes ({yes_count} yes, {no_count} no).")

    if rules_changed:
        params = [rc.get("parameter", "?") for rc in rules_changed]
        lines.append(f"Rule changes enacted: {', '.join(params)}.")

    return Mirror(
        id=f"m-gov-{round_number}-mock",
        mirror_type="governance",
        round_number=round_number,
        content=" ".join(lines) if lines else "Governance was silent this round.",
    )


def generate_private_mirror_mock(
    governor_data: dict,
    governor_id: str,
    season_id: str,
    round_number: int,
) -> Mirror:
    """Mock private mirror for testing."""
    proposals = governor_data.get("proposals_submitted", 0)
    votes = governor_data.get("votes_cast", 0)
    tokens_spent = governor_data.get("tokens_spent", 0)

    if proposals == 0 and votes == 0:
        content = (
            f"Governor {governor_id} was quiet this round. "
            "Sometimes the most revealing pattern is the absence of action."
        )
    else:
        parts = []
        if proposals:
            parts.append(f"submitted {proposals} proposal(s)")
        if votes:
            parts.append(f"cast {votes} vote(s)")
        if tokens_spent:
            parts.append(f"spent {tokens_spent} token(s)")
        content = f"Governor {governor_id} {', '.join(parts)} this round."

    return Mirror(
        id=f"m-priv-{round_number}-{governor_id[:8]}-mock",
        mirror_type="private",
        round_number=round_number,
        governor_id=governor_id,
        content=content,
    )

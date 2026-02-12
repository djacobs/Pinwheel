"""AI commentary engine — play-by-play color commentary for games.

Generates broadcaster-style commentary for individual games and round highlight reels.
Tone: energetic sports broadcaster meets Blaseball weirdness — fun, dramatic, slightly absurd.

Two modes:
- AI-powered (Claude Sonnet): rich, contextual commentary referencing agents and gameplay moments.
- Mock: template-based fallback when no API key is set. Still references real names and scores.
"""

from __future__ import annotations

import logging

import anthropic

from pinwheel.models.game import GameResult
from pinwheel.models.rules import RuleSet
from pinwheel.models.team import Team

logger = logging.getLogger(__name__)

COMMENTARY_SYSTEM_PROMPT = """\
You are the color commentator for Pinwheel Fates, a chaotic 3v3 basketball league.

Write 2-3 short paragraphs of play-by-play commentary for this game. Your style:
- Energetic, dramatic, slightly absurd — think sports broadcaster who has seen too much.
- Reference specific agents by name when describing key plays.
- If the Elam Ending activated, treat it as a dramatic narrative pivot.
- Note any dominant performances, big scoring runs, or statistical oddities.
- Keep it fun. This is Blaseball energy — the weird is the point.

Be concise. No headers. No bullet points. Just vivid prose."""

HIGHLIGHT_REEL_SYSTEM_PROMPT = """\
You are the highlight desk anchor for Pinwheel Fates, a chaotic 3v3 basketball league.

Write a round highlights summary. For each game, one punchy sentence. Then 1-2 sentences \
of overall round narrative — trends, surprises, the vibe. Keep it brisk and entertaining.

No headers. No bullet points. Just vivid, concise prose."""


def _build_game_context(
    game_result: GameResult,
    home_team: Team,
    away_team: Team,
    ruleset: RuleSet,
) -> str:
    """Build a concise context string for the AI from game data."""
    lines = [
        f"{home_team.name} (home) vs {away_team.name} (away)",
        f"Final: {game_result.home_score}-{game_result.away_score}",
        f"Total possessions: {game_result.total_possessions}",
    ]

    if game_result.elam_activated:
        lines.append(f"ELAM ENDING activated! Target score: {game_result.elam_target_score}")

    if ruleset.three_point_value != 3:
        lines.append(f"Three-pointers worth {ruleset.three_point_value} (rule change!)")

    # Box scores — top performers
    lines.append("\nBox scores:")
    for bs in sorted(game_result.box_scores, key=lambda b: b.points, reverse=True):
        team_name = home_team.name if bs.team_id == home_team.id else away_team.name
        lines.append(
            f"  {bs.agent_name} ({team_name}): "
            f"{bs.points}pts {bs.assists}ast {bs.steals}stl {bs.turnovers}to"
        )

    # Key moments from possession log (sample up to 8 notable plays)
    notable = [
        p for p in game_result.possession_log
        if p.points_scored >= 3 or p.result == "turnover" or p.move_activated
    ][:8]
    if notable:
        lines.append("\nKey plays:")
        for p in notable:
            lines.append(f"  Q{p.quarter} #{p.possession_number}: {p.action} -> {p.result}"
                         f" ({p.points_scored}pts)")

    return "\n".join(lines)


async def generate_game_commentary(
    game_result: GameResult,
    home_team: Team,
    away_team: Team,
    ruleset: RuleSet,
    api_key: str,
) -> str:
    """Generate AI-powered broadcaster commentary for a completed game.

    Uses Claude Sonnet for cost-effective high-volume generation.
    Falls back to a bracketed error message on API failure.
    """
    context = _build_game_context(game_result, home_team, away_team, ruleset)

    try:
        client = anthropic.AsyncAnthropic(api_key=api_key)
        response = await client.messages.create(
            model="claude-sonnet-4-5-20250929",
            max_tokens=400,
            system=COMMENTARY_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": f"Call this game:\n\n{context}"}],
        )
        return response.content[0].text
    except anthropic.APIError as e:
        logger.error("Commentary generation API error: %s", e)
        return f"[Commentary generation failed: {e}]"


def generate_game_commentary_mock(
    game_result: GameResult,
    home_team: Team,
    away_team: Team,
) -> str:
    """Template-based fallback commentary when no API key is set.

    References real team/agent names and scores for demo-quality output.
    """
    winner_name = home_team.name if game_result.winner_team_id == home_team.id else away_team.name
    loser_name = away_team.name if game_result.winner_team_id == home_team.id else home_team.name
    winner_score = max(game_result.home_score, game_result.away_score)
    loser_score = min(game_result.home_score, game_result.away_score)
    margin = winner_score - loser_score

    # Find top scorer
    top_scorer = (
        max(game_result.box_scores, key=lambda b: b.points)
        if game_result.box_scores
        else None
    )

    # Find top assist leader
    top_assist = max(
        game_result.box_scores, key=lambda b: b.assists
    ) if game_result.box_scores else None

    paragraphs = []

    # Opening paragraph — the result
    if margin <= 3:
        opener = (
            f"What a nail-biter at the buzzer! The {winner_name} edged out the {loser_name} "
            f"{winner_score}-{loser_score} in a game that could have gone either way. "
            f"The crowd is still vibrating."
        )
    elif margin >= 15:
        opener = (
            f"A statement game from the {winner_name}, who absolutely dismantled the "
            f"{loser_name} {winner_score}-{loser_score}. "
            f"That was less a basketball game and more a public demonstration."
        )
    else:
        opener = (
            f"The {winner_name} take this one {winner_score}-{loser_score} over the "
            f"{loser_name} in a hard-fought {game_result.total_possessions}-possession battle."
        )
    paragraphs.append(opener)

    # Elam paragraph
    if game_result.elam_activated:
        paragraphs.append(
            f"The Elam Ending activated with a target score of {game_result.elam_target_score}, "
            f"and everything changed. Suddenly every possession was sudden death. "
            f"The {winner_name} found a way to cross the finish line first."
        )

    # Star performer paragraph
    if top_scorer:
        scorer_team = (
            home_team.name if top_scorer.team_id == home_team.id else away_team.name
        )
        star_line = (
            f"{top_scorer.agent_name} of the {scorer_team} led all scorers with "
            f"{top_scorer.points} points"
        )
        if top_assist and top_assist.agent_id != top_scorer.agent_id and top_assist.assists > 0:
            assist_team = (
                home_team.name if top_assist.team_id == home_team.id else away_team.name
            )
            star_line += (
                f", while {top_assist.agent_name} ({assist_team}) orchestrated the offense "
                f"with {top_assist.assists} assists"
            )
        star_line += "."
        paragraphs.append(star_line)

    return "\n\n".join(paragraphs)


async def generate_highlight_reel(
    game_summaries: list[dict],
    round_number: int,
    api_key: str,
) -> str:
    """Generate an AI-powered highlights summary for all games in a round.

    One punchy sentence per game, plus overall round narrative.
    """
    if not game_summaries:
        return (
            f"Round {round_number} was eerily quiet. "
            "No games were played. The silence is deafening."
        )

    lines = [f"Round {round_number} results:"]
    for g in game_summaries:
        home = g.get("home_team", "Home")
        away = g.get("away_team", "Away")
        hs = g.get("home_score", 0)
        aws = g.get("away_score", 0)
        elam = " [ELAM]" if g.get("elam_activated") else ""
        lines.append(f"  {home} {hs} - {aws} {away}{elam}")

    context = "\n".join(lines)

    try:
        client = anthropic.AsyncAnthropic(api_key=api_key)
        response = await client.messages.create(
            model="claude-sonnet-4-5-20250929",
            max_tokens=300,
            system=HIGHLIGHT_REEL_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": f"Highlights:\n\n{context}"}],
        )
        return response.content[0].text
    except anthropic.APIError as e:
        logger.error("Highlight reel generation API error: %s", e)
        return f"[Highlight reel generation failed: {e}]"


def generate_highlight_reel_mock(
    game_summaries: list[dict],
    round_number: int,
) -> str:
    """Template-based fallback highlight reel when no API key is set.

    Still references real team names and scores.
    """
    if not game_summaries:
        return f"Round {round_number}: No games scheduled. The league rests."

    lines = []
    for g in game_summaries:
        home = g.get("home_team", "Home")
        away = g.get("away_team", "Away")
        hs = g.get("home_score", 0)
        aws = g.get("away_score", 0)
        winner = home if hs > aws else away
        margin = abs(hs - aws)

        if g.get("elam_activated"):
            lines.append(
                f"The {winner} survived an Elam Ending thriller against "
                f"the {away if winner == home else home}, {max(hs, aws)}-{min(hs, aws)}."
            )
        elif margin >= 15:
            lines.append(
                f"The {winner} blew out the {away if winner == home else home} "
                f"by {margin} in a game that was never close."
            )
        elif margin <= 3:
            lines.append(
                f"A razor-thin finish: {home} {hs}, {away} {aws}. "
                f"Every possession mattered."
            )
        else:
            lines.append(f"The {winner} handled the {away if winner == home else home} "
                         f"{max(hs, aws)}-{min(hs, aws)}.")

    total_points = sum(g.get("home_score", 0) + g.get("away_score", 0) for g in game_summaries)
    elam_count = sum(1 for g in game_summaries if g.get("elam_activated"))

    summary = (
        f"Round {round_number} delivered {len(game_summaries)} games "
        f"and {total_points} total points."
    )
    if elam_count:
        summary += (
            f" The Elam Ending made its presence felt in {elam_count} contest"
            f"{'s' if elam_count > 1 else ''} — chaos remains undefeated."
        )

    lines.append(summary)

    return " ".join(lines)

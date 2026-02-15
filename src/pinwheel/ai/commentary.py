"""AI commentary engine — play-by-play color commentary for games.

Generates broadcaster-style commentary for individual games and round highlight reels.
Tone: energetic sports broadcaster — fun, dramatic, slightly absurd.

Two modes:
- AI-powered (Claude Sonnet): rich, contextual commentary referencing agents and gameplay moments.
- Mock: template-based fallback when no API key is set. Still references real names and scores.
"""

from __future__ import annotations

import logging

import anthropic

from pinwheel.core.narrative import NarrativeContext, format_narrative_for_prompt
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
- Keep it fun. The weird is the point — players change the rules, and the game gets stranger.

{playoff_instructions}

System-level awareness:
- If rule changes are recent, mention this is "the first game under [new rule]" or note its impact
- If a team is on a win/loss streak (3+), weave that into the narrative naturally
- For playoff/championship games, treat every possession with elevated stakes
- If this is late in the season, reference playoff positioning or championship implications
- End with ONE sentence connecting this game to the broader league context — standings impact, \
streak continuation, rule evolution, or what it means for the playoff race

Be concise. No headers. No bullet points. Just vivid prose that shows you know the league \
inside out."""

_PLAYOFF_COMMENTARY_INSTRUCTIONS = {
    "semifinal": (
        "THIS IS A SEMIFINAL PLAYOFF GAME. The stakes are enormous — lose and you go home. "
        "Treat every possession like it matters more than usual. Reference the playoff context "
        "explicitly: 'semifinal,' 'win or go home,' 'season on the line.' The intensity should "
        "come through in every sentence."
    ),
    "finals": (
        "THIS IS THE CHAMPIONSHIP FINALS. The biggest game of the season. Two teams, one title. "
        "Treat this with maximum drama — 'championship,' 'for all the marbles,' 'legacy game.' "
        "Every basket, every stop, every turnover carries the weight of the entire season. "
        "This is the moment everyone has been playing for."
    ),
}

HIGHLIGHT_REEL_SYSTEM_PROMPT = """\
You are the highlight desk anchor for Pinwheel Fates, a chaotic 3v3 basketball league.

Write a round highlights summary. For each game, one punchy sentence. Then 1-2 sentences \
of overall round narrative — trends, surprises, the vibe. Keep it brisk and entertaining.

{playoff_instructions}

System-level awareness:
- Reference rule changes if they affected gameplay this round
- Note win/loss streaks when relevant (3+ games)
- For late-season rounds, frame action in terms of playoff positioning
- Connect individual games to the broader season arc

No headers. No bullet points. Just vivid, concise prose that shows you know the league."""

_PLAYOFF_HIGHLIGHT_INSTRUCTIONS = {
    "semifinal": (
        "These are SEMIFINAL PLAYOFF games. Frame the entire summary around the "
        "elimination stakes. Who survived? Who goes home? Build the narrative "
        "toward the upcoming finals."
    ),
    "finals": (
        "This is THE CHAMPIONSHIP FINALS. The culmination of the entire season. "
        "Frame everything as historic — a champion is crowned tonight."
    ),
}


def _build_game_context(
    game_result: GameResult,
    home_team: Team,
    away_team: Team,
    ruleset: RuleSet,
    playoff_context: str | None = None,
    narrative: NarrativeContext | None = None,
) -> str:
    """Build a concise context string for the AI from game data.

    When a NarrativeContext is provided, includes standings, streaks,
    head-to-head history, rule changes, and other dramatic context.
    """
    lines = []

    if playoff_context:
        label = "SEMIFINAL" if playoff_context == "semifinal" else "CHAMPIONSHIP FINALS"
        lines.append(f"*** {label} PLAYOFF GAME ***")

    lines.extend([
        f"{home_team.name} (home) vs {away_team.name} (away)",
        f"Final: {game_result.home_score}-{game_result.away_score}",
        f"Total possessions: {game_result.total_possessions}",
    ])

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
            f"{bs.points}pts {bs.rebounds}reb {bs.assists}ast {bs.steals}stl {bs.turnovers}to"
        )

    # Key moments from possession log (sample up to 8 notable plays)
    notable = [
        p
        for p in game_result.possession_log
        if p.points_scored >= 3 or p.result == "turnover" or p.move_activated
    ][:8]
    if notable:
        lines.append("\nKey plays:")
        for p in notable:
            move_tag = f" [MOVE: {p.move_activated}]" if p.move_activated else ""
            lines.append(
                f"  Q{p.quarter} #{p.possession_number}: {p.action} -> {p.result}"
                f" ({p.points_scored}pts){move_tag}"
            )

    # Named moves used during the game — context for richer commentary
    moves_used: set[str] = set()
    for p in game_result.possession_log:
        if p.move_activated:
            moves_used.add(p.move_activated)
    if moves_used:
        lines.append(f"\nSignature moves activated: {', '.join(sorted(moves_used))}")

    # Narrative context — standings, streaks, head-to-head, rule changes
    if narrative:
        narrative_block = format_narrative_for_prompt(narrative)
        if narrative_block:
            lines.append(f"\n--- Dramatic Context ---\n{narrative_block}")

    return "\n".join(lines)


async def generate_game_commentary(
    game_result: GameResult,
    home_team: Team,
    away_team: Team,
    ruleset: RuleSet,
    api_key: str,
    playoff_context: str | None = None,
    narrative: NarrativeContext | None = None,
    season_id: str = "",
    round_number: int | None = None,
    db_session: object | None = None,
) -> str:
    """Generate AI-powered broadcaster commentary for a completed game.

    Uses Claude Sonnet for cost-effective high-volume generation.
    Falls back to a bracketed error message on API failure.
    """
    from pinwheel.ai.usage import extract_usage, record_ai_usage, track_latency

    context = _build_game_context(
        game_result, home_team, away_team, ruleset, playoff_context,
        narrative=narrative,
    )
    playoff_instructions = _PLAYOFF_COMMENTARY_INSTRUCTIONS.get(
        playoff_context or "", ""
    )
    system = COMMENTARY_SYSTEM_PROMPT.format(playoff_instructions=playoff_instructions)

    model = "claude-sonnet-4-5-20250929"
    try:
        client = anthropic.AsyncAnthropic(api_key=api_key)
        async with track_latency() as timing:
            response = await client.messages.create(
                model=model,
                max_tokens=400,
                system=system,
                messages=[{"role": "user", "content": f"Call this game:\n\n{context}"}],
            )
        text = response.content[0].text

        if db_session is not None:
            input_tok, output_tok, cache_tok = extract_usage(response)
            await record_ai_usage(
                session=db_session,
                call_type="commentary.game",
                model=model,
                input_tokens=input_tok,
                output_tokens=output_tok,
                cache_read_tokens=cache_tok,
                latency_ms=timing["latency_ms"],
                season_id=season_id,
                round_number=round_number,
            )

        return text
    except anthropic.APIError as e:
        logger.error("Commentary generation API error: %s", e)
        return f"[Commentary generation failed: {e}]"


def generate_game_commentary_mock(
    game_result: GameResult,
    home_team: Team,
    away_team: Team,
    playoff_context: str | None = None,
    narrative: NarrativeContext | None = None,
) -> str:
    """Template-based fallback commentary when no API key is set.

    References real team/agent names and scores for demo-quality output.
    Includes streak and rule change context when narrative is provided.
    """
    winner_name = home_team.name if game_result.winner_team_id == home_team.id else away_team.name
    loser_name = away_team.name if game_result.winner_team_id == home_team.id else home_team.name
    winner_score = max(game_result.home_score, game_result.away_score)
    loser_score = min(game_result.home_score, game_result.away_score)
    margin = winner_score - loser_score

    # Find top scorer
    top_scorer = (
        max(game_result.box_scores, key=lambda b: b.points) if game_result.box_scores else None
    )

    # Find top assist leader
    top_assist = (
        max(game_result.box_scores, key=lambda b: b.assists) if game_result.box_scores else None
    )

    paragraphs = []

    # Playoff context — dramatic opener
    if playoff_context == "finals":
        paragraphs.append(
            "THE CHAMPIONSHIP IS ON THE LINE. This is what the entire season has been building "
            "toward — two teams, one title, and a crowd that can barely breathe."
        )
    elif playoff_context == "semifinal":
        paragraphs.append(
            "Win or go home. The playoff pressure is suffocating, and every possession "
            "carries the weight of a full season's worth of work."
        )

    # Opening paragraph — the result
    if margin <= 3:
        if playoff_context:
            opener = (
                f"And it came down to the wire! The {winner_name} survived against the "
                f"{loser_name} {winner_score}-{loser_score} in a {_playoff_label(playoff_context)}"
                f" classic that will be talked about for seasons to come."
            )
        else:
            opener = (
                f"What a nail-biter at the buzzer! The {winner_name} edged out the {loser_name} "
                f"{winner_score}-{loser_score} in a game that could have gone either way. "
                f"The crowd is still vibrating."
            )
    elif margin >= 15:
        if playoff_context:
            opener = (
                f"A dominant {_playoff_label(playoff_context)} performance from the "
                f"{winner_name}, who dismantled the {loser_name} {winner_score}-{loser_score}. "
                f"The {loser_name}'s season ends not with a bang, but a whimper."
            )
        else:
            opener = (
                f"A statement game from the {winner_name}, who absolutely dismantled the "
                f"{loser_name} {winner_score}-{loser_score}. "
                f"That was less a basketball game and more a public demonstration."
            )
    else:
        if playoff_context:
            opener = (
                f"The {winner_name} advance with a {winner_score}-{loser_score} "
                f"{_playoff_label(playoff_context)} victory over the {loser_name}. "
                f"{game_result.total_possessions} possessions of pure playoff intensity."
            )
        else:
            opener = (
                f"The {winner_name} take this one {winner_score}-{loser_score} over the "
                f"{loser_name} in a hard-fought "
                f"{game_result.total_possessions}-possession battle."
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
        scorer_team = home_team.name if top_scorer.team_id == home_team.id else away_team.name
        star_line = (
            f"{top_scorer.agent_name} of the {scorer_team} led all scorers with "
            f"{top_scorer.points} points"
        )
        if top_assist and top_assist.agent_id != top_scorer.agent_id and top_assist.assists > 0:
            assist_team = home_team.name if top_assist.team_id == home_team.id else away_team.name
            star_line += (
                f", while {top_assist.agent_name} ({assist_team}) orchestrated the offense "
                f"with {top_assist.assists} assists"
            )
        star_line += "."
        paragraphs.append(star_line)

    # Narrative enrichment — streaks, rule changes, and system-level context
    if narrative:
        winner_id = game_result.winner_team_id
        winner_streak = narrative.streaks.get(winner_id, 0)
        if winner_streak >= 3:
            paragraphs.append(
                f"That's {winner_streak} straight wins for the {winner_name}. "
                f"The streak is real."
            )

        loser_id = (
            home_team.id if winner_id == away_team.id else away_team.id
        )
        loser_streak = narrative.streaks.get(loser_id, 0)
        if loser_streak <= -3:
            paragraphs.append(
                f"The {loser_name} have now dropped {abs(loser_streak)} in a row. "
                f"The skid continues."
            )

        # Rule change context — "first game under new X"
        if narrative.active_rule_changes:
            recent_changes = [
                rc for rc in narrative.active_rule_changes
                if rc.get("round_enacted") == narrative.round_number
            ]
            if recent_changes:
                change = recent_changes[0]
                param = str(change.get("parameter", "")).replace("_", " ")
                new_val = change.get("new_value")
                paragraphs.append(
                    f"This is the first game under the new {param} ({new_val}). "
                    f"The governors have spoken, and the court has changed."
                )
            elif narrative.rules_narrative:
                paragraphs.append(
                    f"Rules in effect: {narrative.rules_narrative}."
                )
        elif narrative.rules_narrative:
            # No active_rule_changes list, but rules_narrative is set
            paragraphs.append(
                f"Rules in effect: {narrative.rules_narrative}."
            )

        # Season arc awareness — late season urgency
        if narrative.season_arc == "late" and not playoff_context:
            paragraphs.append(
                f"Round {narrative.round_number} of {narrative.total_rounds} — "
                f"the regular season is winding down, and every game matters for "
                f"playoff positioning."
            )

    # Playoff closing
    if playoff_context == "finals":
        paragraphs.append(
            f"The {winner_name} are your CHAMPIONS. Confetti is falling. "
            f"The {loser_name} gave everything they had, but tonight belongs to the {winner_name}."
        )
    elif playoff_context == "semifinal":
        paragraphs.append(
            f"The {winner_name} punch their ticket to the finals. "
            f"The {loser_name}'s season is over."
        )
    else:
        # System-level closing — connect to league context
        if narrative and narrative.standings:
            # Find winner and loser in standings
            winner_standing = next(
                (s for s in narrative.standings if s.get("team_id") == winner_id),
                None,
            )
            loser_standing = next(
                (s for s in narrative.standings if s.get("team_id") == loser_id),
                None,
            )

            if winner_standing and loser_standing:
                winner_record = (
                    f"{winner_standing.get('wins', 0)}-"
                    f"{winner_standing.get('losses', 0)}"
                )

                # Choose closing based on context
                if narrative.season_arc == "late":
                    paragraphs.append(
                        f"With this win, the {winner_name} improve to "
                        f"{winner_record} — crucial positioning as playoff seeding "
                        f"comes into focus."
                    )
                elif winner_streak >= 5:
                    paragraphs.append(
                        f"The {winner_name} ({winner_record}) continue their "
                        f"remarkable run, climbing the standings and making a "
                        f"statement to the rest of the league."
                    )
                elif loser_streak <= -3:
                    paragraphs.append(
                        f"The {loser_name}'s rough patch continues — they'll need "
                        f"to turn things around quickly or risk falling out of "
                        f"playoff contention."
                    )
                else:
                    paragraphs.append(
                        f"The {winner_name} move to {winner_record}, maintaining "
                        f"their position in the standings while the {loser_name} "
                        f"look to regroup."
                    )

    return "\n\n".join(paragraphs)


def _playoff_label(playoff_context: str | None) -> str:
    """Return a human-readable label for the playoff round."""
    if playoff_context == "finals":
        return "championship"
    if playoff_context == "semifinal":
        return "semifinal"
    return ""


async def generate_highlight_reel(
    game_summaries: list[dict],
    round_number: int,
    api_key: str,
    playoff_context: str | None = None,
    narrative: NarrativeContext | None = None,
    season_id: str = "",
    db_session: object | None = None,
) -> str:
    """Generate an AI-powered highlights summary for all games in a round.

    One punchy sentence per game, plus overall round narrative.
    """
    from pinwheel.ai.usage import extract_usage, record_ai_usage, track_latency

    if not game_summaries:
        return (
            f"Round {round_number} was eerily quiet. "
            "No games were played. The silence is deafening."
        )

    if playoff_context:
        label = "SEMIFINAL" if playoff_context == "semifinal" else "CHAMPIONSHIP FINALS"
        lines = [f"Round {round_number} — {label} PLAYOFFS:"]
    else:
        lines = [f"Round {round_number} results:"]
    for g in game_summaries:
        home = g.get("home_team", "Home")
        away = g.get("away_team", "Away")
        hs = g.get("home_score", 0)
        aws = g.get("away_score", 0)
        elam = " [ELAM]" if g.get("elam_activated") else ""
        lines.append(f"  {home} {hs} - {aws} {away}{elam}")

    context = "\n".join(lines)

    # Add narrative context if available
    if narrative:
        narrative_block = format_narrative_for_prompt(narrative)
        if narrative_block:
            context += f"\n\n--- Dramatic Context ---\n{narrative_block}"

    playoff_instructions = _PLAYOFF_HIGHLIGHT_INSTRUCTIONS.get(
        playoff_context or "", ""
    )
    system = HIGHLIGHT_REEL_SYSTEM_PROMPT.format(
        playoff_instructions=playoff_instructions
    )

    model = "claude-sonnet-4-5-20250929"
    try:
        client = anthropic.AsyncAnthropic(api_key=api_key)
        async with track_latency() as timing:
            response = await client.messages.create(
                model=model,
                max_tokens=300,
                system=system,
                messages=[{"role": "user", "content": f"Highlights:\n\n{context}"}],
            )
        text = response.content[0].text

        if db_session is not None:
            input_tok, output_tok, cache_tok = extract_usage(response)
            await record_ai_usage(
                session=db_session,
                call_type="commentary.highlight_reel",
                model=model,
                input_tokens=input_tok,
                output_tokens=output_tok,
                cache_read_tokens=cache_tok,
                latency_ms=timing["latency_ms"],
                season_id=season_id,
                round_number=round_number,
            )

        return text
    except anthropic.APIError as e:
        logger.error("Highlight reel generation API error: %s", e)
        return f"[Highlight reel generation failed: {e}]"


def generate_highlight_reel_mock(
    game_summaries: list[dict],
    round_number: int,
    playoff_context: str | None = None,
    narrative: NarrativeContext | None = None,
) -> str:
    """Template-based fallback highlight reel when no API key is set.

    Still references real team names and scores. Includes narrative
    context (streaks, rule changes) when available.
    """
    if not game_summaries:
        return f"Round {round_number}: No games scheduled. The league rests."

    lines = []

    # Playoff header
    if playoff_context == "finals":
        lines.append(
            "THE CHAMPIONSHIP FINALS ARE HERE. One game to decide it all."
        )
    elif playoff_context == "semifinal":
        lines.append(
            "SEMIFINAL PLAYOFF ACTION — win or go home."
        )

    for g in game_summaries:
        home = g.get("home_team", "Home")
        away = g.get("away_team", "Away")
        hs = g.get("home_score", 0)
        aws = g.get("away_score", 0)
        winner = home if hs > aws else away
        loser = away if winner == home else home
        margin = abs(hs - aws)

        if g.get("elam_activated"):
            if playoff_context:
                lines.append(
                    f"The {winner} survived an Elam Ending "
                    f"{_playoff_label(playoff_context)} thriller against "
                    f"the {loser}, {max(hs, aws)}-{min(hs, aws)}."
                )
            else:
                lines.append(
                    f"The {winner} survived an Elam Ending thriller against "
                    f"the {loser}, {max(hs, aws)}-{min(hs, aws)}."
                )
        elif margin >= 15:
            if playoff_context:
                lines.append(
                    f"The {winner} dominated the {loser} {max(hs, aws)}-{min(hs, aws)} "
                    f"in a {_playoff_label(playoff_context)} blowout. "
                    f"The {loser}'s season is over."
                )
            else:
                lines.append(
                    f"The {winner} blew out the {loser} "
                    f"by {margin} in a game that was never close."
                )
        elif margin <= 3:
            if playoff_context:
                lines.append(
                    f"A {_playoff_label(playoff_context)} instant classic: "
                    f"{home} {hs}, {away} {aws}. Absolute agony for the {loser}."
                )
            else:
                lines.append(
                    f"A razor-thin finish: {home} {hs}, {away} {aws}. "
                    f"Every possession mattered."
                )
        else:
            if playoff_context:
                lines.append(
                    f"The {winner} eliminate the {loser} "
                    f"{max(hs, aws)}-{min(hs, aws)} in the {_playoff_label(playoff_context)}."
                )
            else:
                lines.append(
                    f"The {winner} handled the {loser} "
                    f"{max(hs, aws)}-{min(hs, aws)}."
                )

    total_points = sum(
        g.get("home_score", 0) + g.get("away_score", 0)
        for g in game_summaries
    )
    elam_count = sum(1 for g in game_summaries if g.get("elam_activated"))

    if playoff_context == "finals":
        winners = []
        for g in game_summaries:
            home = g.get("home_team", "Home")
            away = g.get("away_team", "Away")
            hs = g.get("home_score", 0)
            aws = g.get("away_score", 0)
            winners.append(home if hs > aws else away)
        summary = (
            f"Your champion: the {winners[0]}. "
            f"What a season. What a game. {total_points} points in the finale."
        )
    elif playoff_context == "semifinal":
        summary = (
            f"The semifinal round is in the books — "
            f"{total_points} total points across {len(game_summaries)} elimination games. "
            f"The finals await."
        )
    else:
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

    # Narrative enrichment — rule changes, streaks, season arc
    if narrative:
        # First-round-under-new-rule callout
        if narrative.active_rule_changes:
            recent_changes = [
                rc for rc in narrative.active_rule_changes
                if rc.get("round_enacted") == round_number
            ]
            if recent_changes:
                change = recent_changes[0]
                param = str(change.get("parameter", "")).replace("_", " ")
                new_val = change.get("new_value")
                lines.append(
                    f"The new {param} ({new_val}) made its debut — "
                    f"the governors' will is now law on the court."
                )
            elif narrative.rules_narrative:
                lines.append(f"Rules in effect: {narrative.rules_narrative}.")
        elif narrative.rules_narrative:
            # No active_rule_changes list, but rules_narrative is set
            lines.append(f"Rules in effect: {narrative.rules_narrative}.")

        # Late season arc awareness
        if narrative.season_arc == "late" and not playoff_context:
            lines.append(
                f"Round {round_number} of {narrative.total_rounds} — "
                f"the playoff race is heating up as the regular season winds down."
            )

        # Notable streaks across the league
        if narrative.streaks:
            long_streaks = [
                (tid, s) for tid, s in narrative.streaks.items()
                if abs(s) >= 5
            ]
            if long_streaks:
                # Find team name from standings
                streak_notes = []
                for tid, streak_val in long_streaks[:2]:  # max 2 streak mentions
                    team_standing = next(
                        (st for st in narrative.standings if st.get("team_id") == tid),
                        None,
                    )
                    if team_standing:
                        team_name = team_standing.get("team_name", tid)
                        if streak_val > 0:
                            streak_notes.append(f"{team_name} ({streak_val} straight)")
                        else:
                            streak_notes.append(f"{team_name} ({abs(streak_val)}-game skid)")
                if streak_notes:
                    lines.append(f"Streak watch: {', '.join(streak_notes)}.")

    return " ".join(lines)

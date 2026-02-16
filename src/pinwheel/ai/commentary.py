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

System-level awareness (IMPORTANT — check the "System-Level Notes" section in the game context):
- If this is the FIRST GAME under a new rule, that is the lead story. Open with it. \
"This is the first game under the new three-point value, and it showed."
- If a stat comparison to pre-rule-change averages is provided, cite specific numbers: \
"Scoring is up 12 points per game since the governors changed the three-point value."
- If a game-count milestone is noted (50th game, 100th game), mention it naturally.
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
- If new rules debuted this round, lead with that: "The governors' new three-point value made \
its presence felt."
- If pre-rule-change stat averages are provided, cite the comparison explicitly.
- If a game-count milestone is noted (50th game, etc.), mention it in the wrap-up.
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


def _is_numeric(val: object) -> bool:
    """Type guard for int/float, excluding bool."""
    return isinstance(val, (int, float)) and not isinstance(val, bool)


_SCORING_PARAMS = {"three_point_value", "two_point_value", "free_throw_value"}


def _rule_change_stat_comparison(
    active_rule_changes: list[dict[str, object]],
    round_number: int,
    total_game_score: int,
) -> str:
    """Generate stat-comparison callout for recent (but not brand-new) rule changes.

    Targets rule changes enacted 1-3 rounds ago (not the current round,
    which gets the 'first game under' callout instead).
    """
    for rc in active_rule_changes:
        enacted = rc.get("round_enacted")
        if not _is_numeric(enacted):
            continue
        enacted_int = int(enacted)  # type: ignore[arg-type]
        rounds_since = round_number - enacted_int
        if rounds_since < 1 or rounds_since > 3:
            continue

        param = str(rc.get("parameter", "")).replace("_", " ")
        old_val = rc.get("old_value")
        new_val = rc.get("new_value")
        param_key = str(rc.get("parameter", ""))

        if param_key in _SCORING_PARAMS and _is_numeric(old_val) and _is_numeric(new_val):
            direction = "up" if float(new_val) > float(old_val) else "down"  # type: ignore[arg-type]
            return (
                f"Scoring is {direction} since the {param} changed "
                f"from {old_val} to {new_val} — the governors' impact is real."
            )
        if param_key == "shot_clock_seconds":
            return (
                f"The pace has shifted since the {param} changed "
                f"from {old_val} to {new_val} — the governors set the tempo."
            )
        if param_key == "quarter_minutes":
            return (
                f"The rhythm is different since the {param} changed "
                f"from {old_val} to {new_val} — the governors' vision takes shape."
            )
        return (
            f"The {param} changed from {old_val} to {new_val} — "
            f"and the governors' fingerprints are all over this game."
        )
    return ""


def _check_clinch(
    standings: list[dict[str, object]],
    round_number: int,
    total_rounds: int,
) -> str:
    """Detect if first place has mathematically clinched the top seed."""
    if len(standings) < 2:
        return ""
    first = standings[0]
    second = standings[1]
    first_wins = int(first.get("wins", 0))
    second_wins = int(second.get("wins", 0))
    remaining = total_rounds - round_number
    if remaining >= 0 and first_wins > second_wins + remaining:
        team_name = str(first.get("team_name", "First place"))
        return (
            f"The {team_name} have clinched the top seed "
            f"— no one can catch them now."
        )
    return ""


def _season_milestone_callout(
    round_number: int,
    total_rounds: int,
    standings: list[dict[str, object]] | None = None,
    playoff_context: str | None = None,
) -> str:
    """Generate milestone callouts based on round position in the season."""
    if playoff_context:
        return ""
    if total_rounds <= 0:
        return ""

    # Check clinch first
    if standings:
        clinch = _check_clinch(standings, round_number, total_rounds)
        if clinch:
            return clinch

    # Final round
    if round_number == total_rounds:
        return (
            "This is it — the final round of the regular season. "
            "Playoff seeds are on the line."
        )

    # Down the stretch (2 rounds left)
    if total_rounds - round_number == 2:
        return (
            "Just 2 rounds left in the regular season. "
            "Down the stretch they come."
        )

    # Halfway point
    halfway = total_rounds // 2
    if round_number == halfway and total_rounds >= 4:
        return (
            f"We've reached the halfway point of the season — "
            f"Round {round_number} of {total_rounds}. The second half starts now."
        )

    return ""


# Milestone thresholds for "Nth game of the season" callouts.
_GAME_COUNT_MILESTONES: list[int] = [10, 25, 50, 75, 100, 150, 200]


def _game_count_milestone(
    season_game_number: int,
    games_this_round: int,
) -> str:
    """Generate a callout when the season hits a round game-count milestone.

    The ``season_game_number`` is the count of games played *before* this round.
    ``games_this_round`` is how many games will be played in this round.
    If the range [season_game_number+1 .. season_game_number+games_this_round]
    includes a milestone number, we callout.

    Returns an empty string if no milestone is hit.
    """
    if season_game_number < 0 or games_this_round <= 0:
        return ""

    start = season_game_number + 1
    end = season_game_number + games_this_round

    for milestone in _GAME_COUNT_MILESTONES:
        if start <= milestone <= end:
            return (
                f"Game #{milestone} of the season — a milestone that marks how far "
                f"this league has come."
            )
    return ""


def _stat_comparison_with_average(
    active_rule_changes: list[dict[str, object]],
    round_number: int,
    total_game_score: int,
    pre_rule_avg_score: float,
) -> str:
    """Generate a stat-comparison callout using pre-rule historical averages.

    Enhanced version of ``_rule_change_stat_comparison`` that incorporates the
    actual historical average total game score from before the rule change.
    Falls back to the generic callout if no average is available.
    """
    for rc in active_rule_changes:
        enacted = rc.get("round_enacted")
        if not _is_numeric(enacted):
            continue
        enacted_int = int(enacted)  # type: ignore[arg-type]
        rounds_since = round_number - enacted_int
        if rounds_since < 1 or rounds_since > 3:
            continue

        param_key = str(rc.get("parameter", ""))
        param = param_key.replace("_", " ")
        old_val = rc.get("old_value")
        new_val = rc.get("new_value")

        if param_key in _SCORING_PARAMS and pre_rule_avg_score > 0:
            diff = total_game_score - pre_rule_avg_score
            if abs(diff) >= 2:
                direction = "up" if diff > 0 else "down"
                return (
                    f"Scoring is {direction} since the {param} changed from "
                    f"{old_val} to {new_val} — this game's {total_game_score} total "
                    f"points vs. the pre-change average of {pre_rule_avg_score:.0f}."
                )
            return (
                f"Under the new {param} ({new_val}), scoring is holding steady "
                f"— {total_game_score} total points vs. {pre_rule_avg_score:.0f} "
                f"before the change."
            )
    # No matching scoring rule change with historical average — fall through
    return ""


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

        # System-level threading — explicit callouts for AI to incorporate
        system_notes: list[str] = []

        # First game under new rule
        if narrative.active_rule_changes:
            recent_changes = [
                rc for rc in narrative.active_rule_changes
                if rc.get("round_enacted") == narrative.round_number
            ]
            if recent_changes:
                for change in recent_changes:
                    param = str(change.get("parameter", "")).replace("_", " ")
                    old_val = change.get("old_value")
                    new_val = change.get("new_value")
                    system_notes.append(
                        f"FIRST GAME under new {param}: changed from {old_val} to {new_val}. "
                        f"Weave this into the commentary — how did the new rule show up?"
                    )
            else:
                # Stat comparison with historical average
                game_total = game_result.home_score + game_result.away_score
                if narrative.pre_rule_avg_score > 0:
                    stat_cmp = _stat_comparison_with_average(
                        narrative.active_rule_changes,
                        narrative.round_number,
                        game_total,
                        narrative.pre_rule_avg_score,
                    )
                    if stat_cmp:
                        system_notes.append(stat_cmp)
                else:
                    stat_cmp = _rule_change_stat_comparison(
                        narrative.active_rule_changes,
                        narrative.round_number,
                        game_total,
                    )
                    if stat_cmp:
                        system_notes.append(stat_cmp)

        # Game count milestone
        milestone = _game_count_milestone(
            narrative.season_game_number, games_this_round=1,
        )
        if milestone:
            system_notes.append(milestone)

        if system_notes:
            lines.append(
                "\n--- System-Level Notes (thread these into the narrative) ---"
            )
            for note in system_notes:
                lines.append(f"  - {note}")

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
    from pinwheel.ai.usage import (
        cacheable_system,
        extract_usage,
        record_ai_usage,
        track_latency,
    )

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
                system=cacheable_system(system),
                messages=[{"role": "user", "content": f"Call this game:\n\n{context}"}],
            )
        text = response.content[0].text

        if db_session is not None:
            input_tok, output_tok, cache_tok, cache_create_tok = extract_usage(response)
            await record_ai_usage(
                session=db_session,
                call_type="commentary.game",
                model=model,
                input_tokens=input_tok,
                output_tokens=output_tok,
                cache_read_tokens=cache_tok,
                cache_creation_tokens=cache_create_tok,
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
            else:
                # Not the first game — check for stat comparison callout
                game_total = game_result.home_score + game_result.away_score
                # Prefer comparison with historical averages when available
                stat_cmp = ""
                if narrative.pre_rule_avg_score > 0:
                    stat_cmp = _stat_comparison_with_average(
                        narrative.active_rule_changes,
                        narrative.round_number,
                        game_total,
                        narrative.pre_rule_avg_score,
                    )
                if not stat_cmp:
                    stat_cmp = _rule_change_stat_comparison(
                        narrative.active_rule_changes,
                        narrative.round_number,
                        game_total,
                    )
                if stat_cmp:
                    paragraphs.append(stat_cmp)
                elif narrative.rules_narrative:
                    paragraphs.append(
                        f"Rules in effect: {narrative.rules_narrative}."
                    )
        elif narrative.rules_narrative:
            # No active_rule_changes list, but rules_narrative is set
            paragraphs.append(
                f"Rules in effect: {narrative.rules_narrative}."
            )

        # Season milestone callouts (round position)
        milestone = _season_milestone_callout(
            narrative.round_number,
            narrative.total_rounds,
            narrative.standings,
            playoff_context,
        )
        if milestone:
            paragraphs.append(milestone)

        # Game count milestone callouts (e.g., "50th game of the season")
        game_milestone = _game_count_milestone(
            narrative.season_game_number, games_this_round=1,
        )
        if game_milestone:
            paragraphs.append(game_milestone)

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
    from pinwheel.ai.usage import (
        cacheable_system,
        extract_usage,
        record_ai_usage,
        track_latency,
    )

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
                system=cacheable_system(system),
                messages=[{"role": "user", "content": f"Highlights:\n\n{context}"}],
            )
        text = response.content[0].text

        if db_session is not None:
            input_tok, output_tok, cache_tok, cache_create_tok = extract_usage(response)
            await record_ai_usage(
                session=db_session,
                call_type="commentary.highlight_reel",
                model=model,
                input_tokens=input_tok,
                output_tokens=output_tok,
                cache_read_tokens=cache_tok,
                cache_creation_tokens=cache_create_tok,
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
            else:
                # Not the first round — check for stat comparison
                stat_cmp = ""
                if narrative.pre_rule_avg_score > 0:
                    stat_cmp = _stat_comparison_with_average(
                        narrative.active_rule_changes,
                        round_number,
                        total_points,
                        narrative.pre_rule_avg_score,
                    )
                if not stat_cmp:
                    stat_cmp = _rule_change_stat_comparison(
                        narrative.active_rule_changes,
                        round_number,
                        total_points,
                    )
                if stat_cmp:
                    lines.append(stat_cmp)
                elif narrative.rules_narrative:
                    lines.append(f"Rules in effect: {narrative.rules_narrative}.")
        elif narrative.rules_narrative:
            # No active_rule_changes list, but rules_narrative is set
            lines.append(f"Rules in effect: {narrative.rules_narrative}.")

        # Season milestone callouts (round position)
        milestone = _season_milestone_callout(
            round_number,
            narrative.total_rounds,
            narrative.standings,
            playoff_context,
        )
        if milestone:
            lines.append(milestone)

        # Game count milestone callouts
        game_milestone = _game_count_milestone(
            narrative.season_game_number, games_this_round=len(game_summaries),
        )
        if game_milestone:
            lines.append(game_milestone)

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

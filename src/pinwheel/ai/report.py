"""AI report generation — Claude-powered reports on gameplay and governance.

Three report types for Day 3:
- Simulation report: reflects on game results, statistical patterns, emergent behavior
- Governance report: reflects on proposal patterns, voting dynamics, rule evolution
- Private report: reflects on a single governor's behavior (visible only to them)

All reports follow the same constraint: they DESCRIBE patterns, never PRESCRIBE actions.
The AI observes; humans decide.
"""

from __future__ import annotations

import json
import logging
import uuid

import anthropic

from pinwheel.core.narrative import NarrativeContext, format_narrative_for_prompt
from pinwheel.models.report import Report

logger = logging.getLogger(__name__)


SIMULATION_REPORT_PROMPT = """\
You are the Social Mirror for Pinwheel Fates, a 3v3 basketball governance game.

Your job: reflect on the round's game results. Describe patterns, surprises, and emergent behavior.

## Rules
1. You DESCRIBE. You never PRESCRIBE. Never say "players should" or "the league needs to."
2. You are observing a simulated basketball league. The games are auto-simulated; no humans play.
3. Human "governors" control the RULES of the game. Your job is to make patterns visible.
4. Be vivid and thorough (3-5 paragraphs). Channel a sports journalist who sees the deeper story.
5. Note any statistical anomalies, streaks, or effects of recent rule changes.
6. If the Elam Ending activated, comment on how it shaped the game's outcome.
7. If rules changed recently, analyze how the new parameters affected this round's outcomes. \
Reference specific changes (e.g., "With three-pointers now worth 4, perimeter shooting dominated").
8. Mention the next governance window — what patterns should governors pay attention to?

## Current Round Data

{round_data}
"""

GOVERNANCE_REPORT_PROMPT = """\
You are the Governance Mirror for Pinwheel Fates, a 3v3 basketball governance game.

Your job: reflect on governance activity this round. Describe voting patterns, proposal themes, \
and how the rule space is evolving.

## Rules
1. You DESCRIBE. You never PRESCRIBE. Never say "governors should" or "the league needs to."
2. Be thorough (3-5 paragraphs). Note trends — are proposals getting bolder? Is consensus forming?
3. If rules changed this round, reflect on what the change reveals about the community's values.
4. If proposals failed, note what that tells us about disagreement or shared priorities.
5. For each rule that changed, state the parameter name, old value, and new value \
explicitly (e.g., "three_point_value changed from 3 to 4").
6. Summarize the governance window outcome: how many proposals filed, passed, and failed.
7. If governance window timing is available, mention when the next window opens.

## Governance Activity

{governance_data}
"""

PRIVATE_REPORT_PROMPT = """\
You are generating a Private Mirror for governor "{governor_id}" in Pinwheel Fates.

A private mirror reflects a governor's OWN behavior back to them. Only they see this.
It helps them understand their patterns without telling them what to do.

## Rules
1. You DESCRIBE their behavior patterns. You never PRESCRIBE actions.
2. Write 2-3 paragraphs. Be specific to THIS governor's actions.
3. Note: voting patterns, proposal themes, token usage, consistency of philosophy.
4. Never compare them to other specific governors. Reflect, don't rank.
5. If they haven't been active, note the absence without judgment.

## Governor Activity

{governor_data}
"""


# --- Variant B prompts for A/B comparison (M.2) ---

SIMULATION_REPORT_PROMPT_B = """\
You are a keen-eyed sports analyst for Pinwheel Fates, a 3v3 basketball governance game.

Reflect on this round's results. Focus on what the numbers reveal about the current meta.

## Constraints
1. OBSERVE only. Never recommend, suggest, or advise.
2. Be terse — one paragraph. Data-driven.
3. If Elam triggered, note the score dynamics it created.
4. Mention specific teams and agents by name when relevant.

## Round Data

{round_data}
"""

GOVERNANCE_REPORT_PROMPT_B = """\
You are a governance analyst for Pinwheel Fates, a 3v3 basketball governance game.

Analyze this round's governance activity. Focus on coalition dynamics and power shifts.

## Constraints
1. OBSERVE only. Never say what governors "should" do.
2. One paragraph. Be precise about vote counts and proposal patterns.
3. If consensus formed, note what that reveals. If it fractured, note the fault lines.

## Governance Activity

{governance_data}
"""

PRIVATE_REPORT_PROMPT_B = """\
You are writing a behavioral snapshot for governor "{governor_id}" in Pinwheel Fates.

This is private — only they see it. Show them their pattern.

## Constraints
1. DESCRIBE only. Zero advice.
2. One paragraph. Specific to their actions.
3. Note: frequency, consistency, token economy, risk appetite.
4. Never mention other governors by name.

## Governor Activity

{governor_data}
"""


async def generate_report_with_prompt(
    prompt_template: str,
    data: dict,
    format_kwargs: dict,
    report_type: str,
    report_id_prefix: str,
    round_number: int,
    api_key: str,
    governor_id: str = "",
    season_id: str = "",
    db_session: object | None = None,
) -> Report:
    """Generate a report using a specific prompt template (for A/B testing)."""
    formatted = prompt_template.format(**format_kwargs)
    content = await _call_claude(
        system=formatted,
        user_message=f"Generate a {report_type} report for this round.",
        api_key=api_key,
        call_type=f"report.{report_type}.ab",
        season_id=season_id,
        round_number=round_number,
        db_session=db_session,
    )
    return Report(
        id=f"{report_id_prefix}-{round_number}-{uuid.uuid4().hex[:8]}",
        report_type=report_type,
        round_number=round_number,
        governor_id=governor_id,
        content=content,
    )


async def generate_simulation_report(
    round_data: dict,
    season_id: str,
    round_number: int,
    api_key: str,
    narrative: NarrativeContext | None = None,
    db_session: object | None = None,
) -> Report:
    """Generate a simulation report using Claude."""
    data_str = json.dumps(round_data, indent=2)
    if narrative:
        narrative_block = format_narrative_for_prompt(narrative)
        data_str += f"\n\n--- Dramatic Context ---\n{narrative_block}"
    content = await _call_claude(
        system=SIMULATION_REPORT_PROMPT.format(round_data=data_str),
        user_message="Generate a simulation report for this round.",
        api_key=api_key,
        call_type="report.simulation",
        season_id=season_id,
        round_number=round_number,
        db_session=db_session,
    )
    return Report(
        id=f"r-sim-{round_number}-{uuid.uuid4().hex[:8]}",
        report_type="simulation",
        round_number=round_number,
        content=content,
    )


async def generate_governance_report(
    governance_data: dict,
    season_id: str,
    round_number: int,
    api_key: str,
    narrative: NarrativeContext | None = None,
    db_session: object | None = None,
) -> Report:
    """Generate a governance report using Claude."""
    data_str = json.dumps(governance_data, indent=2)
    if narrative:
        narrative_block = format_narrative_for_prompt(narrative)
        data_str += f"\n\n--- Dramatic Context ---\n{narrative_block}"
    content = await _call_claude(
        system=GOVERNANCE_REPORT_PROMPT.format(governance_data=data_str),
        user_message="Generate a governance report for this round.",
        api_key=api_key,
        call_type="report.governance",
        season_id=season_id,
        round_number=round_number,
        db_session=db_session,
    )
    return Report(
        id=f"r-gov-{round_number}-{uuid.uuid4().hex[:8]}",
        report_type="governance",
        round_number=round_number,
        content=content,
    )


async def generate_private_report(
    governor_data: dict,
    governor_id: str,
    season_id: str,
    round_number: int,
    api_key: str,
    db_session: object | None = None,
) -> Report:
    """Generate a private report for a specific governor."""
    content = await _call_claude(
        system=PRIVATE_REPORT_PROMPT.format(
            governor_id=governor_id,
            governor_data=json.dumps(governor_data, indent=2),
        ),
        user_message=f"Generate a private report for governor {governor_id}.",
        api_key=api_key,
        call_type="report.private",
        season_id=season_id,
        round_number=round_number,
        db_session=db_session,
    )
    return Report(
        id=f"r-priv-{round_number}-{uuid.uuid4().hex[:8]}",
        report_type="private",
        round_number=round_number,
        governor_id=governor_id,
        content=content,
    )


async def _call_claude(
    system: str,
    user_message: str,
    api_key: str,
    call_type: str = "report",
    season_id: str = "",
    round_number: int | None = None,
    db_session: object | None = None,
) -> str:
    """Make a Claude API call for report generation.

    When ``db_session`` is provided, records token usage to the AI usage log.
    """
    from pinwheel.ai.usage import extract_usage, record_ai_usage, track_latency

    model = "claude-sonnet-4-5-20250929"
    try:
        client = anthropic.AsyncAnthropic(api_key=api_key)
        async with track_latency() as timing:
            response = await client.messages.create(
                model=model,
                max_tokens=1500,
                system=system,
                messages=[{"role": "user", "content": user_message}],
            )
        text = response.content[0].text

        # Record usage if a DB session is available
        if db_session is not None:
            input_tok, output_tok, cache_tok = extract_usage(response)
            await record_ai_usage(
                session=db_session,
                call_type=call_type,
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
        logger.error("Report generation API error: %s", e)
        return f"[Report generation failed: {e}]"


# --- Mock implementations for testing ---


def generate_simulation_report_mock(
    round_data: dict,
    season_id: str,
    round_number: int,
    narrative: NarrativeContext | None = None,
) -> Report:
    """Mock simulation report — narrative, specific, never generic."""
    import random as _rng

    games = round_data.get("games", [])
    if not games:
        return Report(
            id=f"r-sim-{round_number}-mock",
            report_type="simulation",
            round_number=round_number,
            content="Silence from the courts. No games this round.",
        )

    rng = _rng.Random(round_number * 1000 + len(games))

    # Collect narrative ingredients
    blowouts = []
    close_games = []
    for g in games:
        home = g.get("home_team", "Home")
        away = g.get("away_team", "Away")
        hs, aws = g.get("home_score", 0), g.get("away_score", 0)
        margin = abs(hs - aws)
        winner = home if hs > aws else away
        loser = away if hs > aws else home
        w_score, l_score = max(hs, aws), min(hs, aws)
        entry = {
            "winner": winner,
            "loser": loser,
            "w_score": w_score,
            "l_score": l_score,
            "margin": margin,
            "total": hs + aws,
        }
        if margin >= 10:
            blowouts.append(entry)
        elif margin <= 4:
            close_games.append(entry)

    total_points = sum(g.get("home_score", 0) + g.get("away_score", 0) for g in games)
    avg_total = total_points // max(len(games), 1)

    # Build narrative lines
    lines = []

    # Playoff phase opener — a playoff sim report must FEEL different
    if narrative and narrative.phase in ("semifinal", "finals", "championship"):
        if narrative.phase == "finals":
            lines.append(
                "THE CHAMPIONSHIP FINALS. The biggest stage. "
                "Everything this season built toward comes down to this."
            )
        elif narrative.phase == "semifinal":
            lines.append(
                "SEMIFINAL PLAYOFFS — win or go home. "
                "The pressure of elimination hangs over every possession."
            )

    # Lead with the most dramatic game
    if close_games:
        g = close_games[0]
        w, lo = g["winner"], g["loser"]
        ws, ls, m = g["w_score"], g["l_score"], g["margin"]
        if narrative and narrative.phase in ("semifinal", "finals"):
            openers = [
                (
                    f"{w} survived {lo} by {m} — a {ws}-{ls} "
                    f"{'championship' if narrative.phase == 'finals' else 'playoff'} "
                    "classic that will echo for seasons."
                ),
                (
                    f"A {m}-point margin was all that separated "
                    f"{w} from {lo}. "
                    + (
                        "A title decided by inches."
                        if narrative.phase == "finals"
                        else "Elimination avoided by the thinnest margin."
                    )
                ),
            ]
        else:
            openers = [
                (
                    f"{w} survived {lo} by {m} — a {ws}-{ls} grinder "
                    "that went down to the final Elam possession."
                ),
                (
                    f"A {m}-point margin was all that separated "
                    f"{w} from {lo}. The kind of game that turns a season."
                ),
                (
                    f"{w} edged {lo} {ws}-{ls}. Neither team blinked "
                    "until the Elam target came into view."
                ),
            ]
        lines.append(rng.choice(openers))
    elif blowouts:
        g = blowouts[0]
        w, lo = g["winner"], g["loser"]
        ws, ls, m = g["w_score"], g["l_score"], g["margin"]
        if narrative and narrative.phase in ("semifinal", "finals"):
            openers = [
                (
                    f"{w} dominated {lo} by {m} in a "
                    f"{'championship' if narrative.phase == 'finals' else 'semifinal'} "
                    f"rout. {lo}'s season ends in decisive fashion."
                ),
            ]
        else:
            openers = [
                (f"{w} dismantled {lo} by {m}. It wasn't close after the first quarter."),
                (f"A {m}-point demolition: {w} {ws}, {lo} {ls}. The Elam target was a formality."),
            ]
        lines.append(rng.choice(openers))
    else:
        g0 = games[0]
        home, away = g0.get("home_team", "Home"), g0.get("away_team", "Away")
        lines.append(
            f"{home} and {away} traded buckets all game. "
            f"Final: {g0.get('home_score', 0)}-{g0.get('away_score', 0)}."
        )

    # Add secondary observations about scoring pace
    if len(games) > 1:
        if avg_total >= 60:
            high_scoring = [
                (
                    f"The courts ran hot — {avg_total} points per game on average. "
                    "Defenses are struggling or offenses are evolving. Maybe both."
                ),
                (
                    f"{avg_total} PPG across the round. Pace was relentless — "
                    "teams aren't holding back."
                ),
                (
                    f"Buckets fell at an {avg_total}-point clip. "
                    "The shot-makers had the last word this round."
                ),
                (
                    f"Scoring surged to {avg_total} per game. "
                    "Whoever's gameplanning defense needs to go back to the drawing board."
                ),
                (
                    f"An {avg_total}-point average tells the story: "
                    "offenses found their rhythm and never let go."
                ),
            ]
            lines.append(rng.choice(high_scoring))
        elif avg_total <= 40:
            low_scoring = [
                (
                    f"Only {avg_total} points per game this round. "
                    "Someone tightened the screws. The game is getting physical."
                ),
                (f"Defense locked in. {avg_total} PPG — that's a lockdown night across the board."),
                (f"{avg_total} points per game. Every bucket earned, nothing came easy."),
                (
                    f"A grind-it-out round at {avg_total} PPG. "
                    "Possessions felt precious — nobody was giving anything away."
                ),
            ]
            lines.append(rng.choice(low_scoring))
        else:
            mid_scoring = [
                (
                    f"The pace settled at {avg_total} points per game — "
                    "neither runaway offense nor suffocating defense dominated."
                ),
                (f"{avg_total} PPG. A balanced round where matchups mattered more than systems."),
                (
                    f"Scoring landed at {avg_total} per game. "
                    "The meta feels unsettled — teams are still figuring each other out."
                ),
            ]
            lines.append(rng.choice(mid_scoring))

    if blowouts and close_games:
        lines.append("A round of extremes: blowouts and nail-biters sharing the same scorecard.")

    # Note rule changes if present in round data
    rule_changes = round_data.get("rule_changes", [])
    if rule_changes:
        change_notes = []
        for rc in rule_changes:
            param = rc.get("parameter", "")
            if param:
                change_notes.append(f"{param.replace('_', ' ')}")
        if change_notes:
            lines.append(
                f"This round marked the first games under new rules — "
                f"{', '.join(change_notes)} {'was' if len(change_notes) == 1 else 'were'} "
                f"adjusted heading into the round. The effects are starting to show."
            )

    # Narrative context enrichment
    if narrative:
        # Mention notable streaks
        for team_id, streak in narrative.streaks.items():
            if streak >= 3:
                team_name = team_id
                for s in narrative.standings:
                    if s.get("team_id") == team_id:
                        team_name = str(s.get("team_name", team_id))
                        break
                lines.append(
                    f"{team_name} are riding a {streak}-game win streak."
                )
            elif streak <= -3:
                team_name = team_id
                for s in narrative.standings:
                    if s.get("team_id") == team_id:
                        team_name = str(s.get("team_name", team_id))
                        break
                lines.append(
                    f"{team_name} have lost {abs(streak)} straight."
                )

        # Mention rule changes from narrative context
        if narrative.rules_narrative and not rule_changes:
            lines.append(
                f"Current rules: {narrative.rules_narrative}."
            )

        # Season arc note
        if narrative.season_arc == "late" and narrative.total_rounds > 0:
            lines.append(
                f"Round {narrative.round_number} of {narrative.total_rounds} — "
                f"the regular season is winding down."
            )
        elif narrative.season_arc == "playoff":
            lines.append("Every game from here on out is elimination basketball.")
        elif narrative.season_arc == "championship":
            lines.append("The championship celebration has begun.")

        # Hot players
        if narrative.hot_players:
            for hp in narrative.hot_players[:2]:
                hp_name = hp.get("name", "?")
                hp_team = hp.get("team_name", "?")
                hp_pts = hp.get("value", 0)
                lines.append(
                    f"{hp_name} ({hp_team}) is on fire with {hp_pts} points."
                )

    return Report(
        id=f"r-sim-{round_number}-mock",
        report_type="simulation",
        round_number=round_number,
        content=" ".join(lines),
    )


def generate_governance_report_mock(
    governance_data: dict,
    season_id: str,
    round_number: int,
    narrative: NarrativeContext | None = None,
) -> Report:
    """Mock governance report for testing."""
    proposals = governance_data.get("proposals", [])
    votes = governance_data.get("votes", [])
    rules_changed = governance_data.get("rules_changed", [])

    lines = []

    # Playoff phase opener — governance during playoffs carries different weight
    if narrative and narrative.phase in ("semifinal", "finals", "championship"):
        if narrative.phase == "finals":
            lines.append(
                "CHAMPIONSHIP GOVERNANCE. With the finals underway, "
                "every rule decision now shapes how the title is won."
            )
        elif narrative.phase == "semifinal":
            lines.append(
                "PLAYOFF GOVERNANCE — the stakes are higher. "
                "Rule changes enacted now land on elimination games."
            )

    if proposals:
        lines.append(
            f"Round {round_number} saw {len(proposals)} proposal(s) "
            "enter the governance arena."
        )
    else:
        lines.append(
            f"Round {round_number} was quiet on the governance front "
            "— no proposals filed."
        )

    if votes:
        yes_count = sum(1 for v in votes if v.get("vote") == "yes")
        no_count = sum(1 for v in votes if v.get("vote") == "no")
        lines.append(
            f"Governors cast {len(votes)} votes "
            f"({yes_count} yes, {no_count} no)."
        )

    if rules_changed:
        lines.append(f"{len(rules_changed)} rule(s) changed this round:")
        for rc in rules_changed:
            param = rc.get("parameter", "unknown")
            old_val = rc.get("old_value", "?")
            new_val = rc.get("new_value", "?")
            if param != "unknown" and old_val != "?" and new_val != "?":
                param_label = param.replace("_", " ").title()
                lines.append(
                    f"  {param_label} moved from {old_val} to {new_val}."
                )
            else:
                lines.append(
                    f"  A rule was changed "
                    f"(proposal {rc.get('proposal_id', '?')})."
                )
        lines.append("The next round plays under these new conditions.")

    # Narrative context enrichment
    if narrative:
        if narrative.pending_proposals > 0 and not proposals:
            lines.append(
                f"{narrative.pending_proposals} proposal(s) remain "
                "pending from prior rounds."
            )
        if (
            narrative.next_tally_round is not None
            and not narrative.governance_window_open
        ):
            lines.append(
                f"Next governance tally: Round {narrative.next_tally_round}."
            )

    return Report(
        id=f"r-gov-{round_number}-mock",
        report_type="governance",
        round_number=round_number,
        content=" ".join(lines) if lines else "Governance was silent this round.",
    )


def generate_private_report_mock(
    governor_data: dict,
    governor_id: str,
    season_id: str,
    round_number: int,
) -> Report:
    """Mock private report for testing."""
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

    return Report(
        id=f"r-priv-{round_number}-{governor_id[:8]}-mock",
        report_type="private",
        round_number=round_number,
        governor_id=governor_id,
        content=content,
    )


# --- Series Report Generation ---

SERIES_REPORT_PROMPT = """\
You are the Sports Chronicler for Pinwheel Fates, a 3v3 basketball governance game.

Write a 2-3 paragraph recap of a completed playoff series. Cover the full arc:
how the series opened, the turning point, and the clinching game.

## Rules
1. You DESCRIBE. You never PRESCRIBE.
2. Write in vivid sports journalism style — this is the record of the series.
3. Reference team names, game-by-game scores, and the series record.
4. Note momentum shifts, dominant performances, and close calls.
5. Build to the decisive moment of the clinching game.

## Series Data

{series_data}
"""


async def generate_series_report(
    series_data: dict,
    season_id: str,
    api_key: str,
    db_session: object | None = None,
) -> Report:
    """Generate an AI-powered recap of a completed playoff series.

    Args:
        series_data: Dict with team names, game-by-game scores, series record,
            series type (semifinal/finals), winner/loser info.
        season_id: Season ID for usage tracking.
        api_key: Anthropic API key.
        db_session: Optional DB session for usage logging.

    Returns:
        A Report with report_type="series".
    """
    data_str = json.dumps(series_data, indent=2)
    content = await _call_claude(
        system=SERIES_REPORT_PROMPT.format(series_data=data_str),
        user_message="Write a recap of this completed playoff series.",
        api_key=api_key,
        call_type="report.series",
        season_id=season_id,
        db_session=db_session,
    )
    return Report(
        id=f"r-series-{series_data.get('series_type', 'playoff')}-{uuid.uuid4().hex[:8]}",
        report_type="series",
        round_number=0,
        content=content,
    )


def generate_series_report_mock(series_data: dict) -> Report:
    """Generate a mock series recap for testing.

    Args:
        series_data: Dict with team names, game-by-game scores, series record,
            series type, winner/loser info.

    Returns:
        A Report with report_type="series" and deterministic content.
    """
    winner = series_data.get("winner_name", "Winner")
    loser = series_data.get("loser_name", "Loser")
    record = series_data.get("record", "?-?")
    series_type = series_data.get("series_type", "playoff")
    games = series_data.get("games", [])

    lines: list[str] = []

    if series_type == "finals":
        lines.append(
            f"The championship finals are over. {winner} claimed the title "
            f"with a {record} series victory over {loser}."
        )
    else:
        lines.append(
            f"{winner} advanced past {loser} in a {record} semifinal series."
        )

    if games:
        last_game = games[-1]
        lines.append(
            f"The clinching game ended {last_game.get('home_score', 0)}-"
            f"{last_game.get('away_score', 0)}. "
            f"From the opening tip of Game 1 to the final buzzer, "
            f"this series delivered."
        )

    return Report(
        id=f"r-series-{series_type}-mock",
        report_type="series",
        round_number=0,
        content=" ".join(lines),
    )


# --- Season Memorial Generation ---

SEASON_NARRATIVE_PROMPT = """\
You are the chronicler of Pinwheel Fates, a 3v3 basketball governance game.

Write the definitive season narrative: 3-5 paragraphs covering the full arc from
opening round to final whistle. This is the almanac entry for this season.

## Rules
1. You DESCRIBE. You never PRESCRIBE.
2. Write in the style of a sports almanac — vivid, authoritative, specific.
3. Reference specific teams, hoopers, and rule changes by name.
4. Note turning points: when the standings shifted, when a rule change reshaped
   the meta, when a streak defined a team's season.
5. Build to the playoffs and championship as a dramatic conclusion.

## Season Data

{season_data}
"""

CHAMPIONSHIP_RECAP_PROMPT = """\
You are the chronicler of Pinwheel Fates, a 3v3 basketball governance game.

Write a detailed championship recap: the playoff bracket, semifinal drama,
and the championship finals. 2-3 paragraphs of vivid sports writing.

## Rules
1. You DESCRIBE. You never PRESCRIBE.
2. Cover each playoff round — who won, the score, the momentum shifts.
3. If the Elam Ending activated, describe how it shaped the outcome.
4. Build to the championship moment — the final basket, the winning team's
   reaction, the season's capstone.

## Playoff Data

{playoff_data}
"""

CHAMPION_PROFILE_PROMPT = """\
You are the chronicler of Pinwheel Fates, a 3v3 basketball governance game.

Write a champion profile: the winning team's journey from regular season
through playoffs to the title. 1-2 paragraphs.

## Rules
1. You DESCRIBE. You never PRESCRIBE.
2. Highlight the team's regular season record and standout hoopers.
3. Describe their playoff path — close calls, dominant wins, the finals.
4. Note their roster's strengths and how they matched up against opponents.

## Champion Data

{champion_data}
"""

GOVERNANCE_LEGACY_PROMPT = """\
You are the chronicler of Pinwheel Fates, a 3v3 basketball governance game.

Write the governance legacy section: how the rules evolved during this season,
who drove changes, and what the governance record reveals about the community.
2-3 paragraphs.

## Rules
1. You DESCRIBE. You never PRESCRIBE.
2. Note which rules changed, who proposed them, and whether they passed or failed.
3. Identify patterns: were governors bold or conservative? Did consensus form?
4. Reflect on how rule changes affected gameplay outcomes.

## Governance Data

{governance_data}
"""


async def generate_season_memorial(
    memorial_data: dict,
    season_id: str,
    api_key: str,
    db_session: object | None = None,
) -> dict:
    """Generate AI narrative sections for a season memorial.

    Makes 4 concurrent Claude calls for the narrative sections:
    season_narrative, championship_recap, champion_profile, governance_legacy.

    Args:
        memorial_data: Dict from gather_memorial_data() with computed sections.
        season_id: Season being memorialized.
        api_key: Anthropic API key.
        db_session: Optional DB session for usage logging.

    Returns:
        Updated memorial_data dict with AI narratives filled in.
    """
    import asyncio

    # Prepare context for each prompt
    season_context = json.dumps(
        {
            "awards": memorial_data.get("awards", []),
            "statistical_leaders": memorial_data.get("statistical_leaders", {}),
            "key_moments": memorial_data.get("key_moments", []),
            "head_to_head": memorial_data.get("head_to_head", []),
            "rule_timeline": memorial_data.get("rule_timeline", []),
        },
        indent=2,
    )

    playoff_context = json.dumps(
        {
            "key_moments": [
                m for m in memorial_data.get("key_moments", [])
                if m.get("moment_type") == "playoff"
            ],
            "awards": memorial_data.get("awards", []),
        },
        indent=2,
    )

    champion_context = json.dumps(
        {
            "awards": [
                a for a in memorial_data.get("awards", [])
                if a.get("category") == "gameplay"
            ],
            "statistical_leaders": memorial_data.get("statistical_leaders", {}),
        },
        indent=2,
    )

    governance_context = json.dumps(
        {
            "rule_timeline": memorial_data.get("rule_timeline", []),
            "awards": [
                a for a in memorial_data.get("awards", [])
                if a.get("category") == "governance"
            ],
        },
        indent=2,
    )

    # Make 4 concurrent calls
    narrative_task = _call_claude(
        system=SEASON_NARRATIVE_PROMPT.format(season_data=season_context),
        user_message="Write the season narrative.",
        api_key=api_key,
        call_type="memorial.season_narrative",
        season_id=season_id,
        db_session=db_session,
    )
    championship_task = _call_claude(
        system=CHAMPIONSHIP_RECAP_PROMPT.format(playoff_data=playoff_context),
        user_message="Write the championship recap.",
        api_key=api_key,
        call_type="memorial.championship_recap",
        season_id=season_id,
        db_session=db_session,
    )
    champion_task = _call_claude(
        system=CHAMPION_PROFILE_PROMPT.format(champion_data=champion_context),
        user_message="Write the champion profile.",
        api_key=api_key,
        call_type="memorial.champion_profile",
        season_id=season_id,
        db_session=db_session,
    )
    governance_task = _call_claude(
        system=GOVERNANCE_LEGACY_PROMPT.format(governance_data=governance_context),
        user_message="Write the governance legacy.",
        api_key=api_key,
        call_type="memorial.governance_legacy",
        season_id=season_id,
        db_session=db_session,
    )

    results = await asyncio.gather(
        narrative_task,
        championship_task,
        champion_task,
        governance_task,
        return_exceptions=True,
    )

    # Fill in results, using empty string for any failures
    narratives = []
    for r in results:
        if isinstance(r, Exception):
            logger.error("Memorial AI call failed: %s", r)
            narratives.append("")
        else:
            narratives.append(str(r))

    memorial_data["season_narrative"] = narratives[0]
    memorial_data["championship_recap"] = narratives[1]
    memorial_data["champion_profile"] = narratives[2]
    memorial_data["governance_legacy"] = narratives[3]
    memorial_data["model_used"] = "claude-sonnet-4-5-20250929"

    return memorial_data


def generate_season_memorial_mock(memorial_data: dict) -> dict:
    """Generate mock AI narrative sections for testing.

    Fills in reasonable static content for each narrative section
    based on available computed data.

    Args:
        memorial_data: Dict from gather_memorial_data() with computed sections.

    Returns:
        Updated memorial_data dict with mock narratives filled in.
    """
    awards = memorial_data.get("awards", [])
    key_moments = memorial_data.get("key_moments", [])
    rule_timeline = memorial_data.get("rule_timeline", [])
    leaders = memorial_data.get("statistical_leaders", {})

    # Season narrative
    parts = ["Another season in the books for Pinwheel Fates."]
    if key_moments:
        closest = [m for m in key_moments if m.get("moment_type") == "closest_game"]
        if closest:
            m = closest[0]
            parts.append(
                f"The closest game of the season saw {m.get('home_team_name', '?')} "
                f"edge {m.get('away_team_name', '?')} by {m.get('margin', 0)} points."
            )
    ppg_leaders = leaders.get("ppg", [])
    if ppg_leaders:
        top = ppg_leaders[0]
        parts.append(
            f"{top['hooper_name']} ({top['team_name']}) led the league in scoring "
            f"with {top['value']} PPG across {top['games']} games."
        )
    memorial_data["season_narrative"] = " ".join(parts)

    # Championship recap
    playoff_moments = [m for m in key_moments if m.get("moment_type") == "playoff"]
    if playoff_moments:
        pm = playoff_moments[0]
        memorial_data["championship_recap"] = (
            f"The playoffs delivered. {pm.get('winner_name', '?')} "
            f"took down {pm.get('away_team_name', pm.get('home_team_name', '?'))} "
            f"{pm.get('home_score', 0)}-{pm.get('away_score', 0)} "
            f"to advance. Every possession counted under the bright lights."
        )
    else:
        memorial_data["championship_recap"] = (
            "The playoff bracket was set and the best teams battled for the title. "
            "When the final buzzer sounded, a champion was crowned."
        )

    # Champion profile
    gameplay_awards = [a for a in awards if a.get("category") == "gameplay"]
    if gameplay_awards:
        mvp = gameplay_awards[0]
        memorial_data["champion_profile"] = (
            f"The champion's run was anchored by {mvp.get('recipient_name', '?')}, "
            f"who earned {mvp.get('award', 'top honors')} with "
            f"{mvp.get('stat_value', '?')} {mvp.get('stat_label', '')}. "
            f"A roster built for the moment."
        )
    else:
        memorial_data["champion_profile"] = (
            "The champions proved their mettle across the entire season, "
            "building consistency in the regular season and peaking in the playoffs."
        )

    # Governance legacy
    if rule_timeline:
        changes = [f"{r.get('parameter', '?')}" for r in rule_timeline[:3]]
        memorial_data["governance_legacy"] = (
            f"Governors reshaped the game this season with {len(rule_timeline)} "
            f"rule change{'s' if len(rule_timeline) != 1 else ''}. "
            f"Parameters affected: {', '.join(changes)}. "
            f"The community's fingerprints are all over this ruleset."
        )
    else:
        memorial_data["governance_legacy"] = (
            "The governors held steady this season -- no rule changes were enacted. "
            "Whether by consensus or inaction, the default ruleset stood."
        )

    memorial_data["model_used"] = "mock"

    return memorial_data

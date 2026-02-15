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
You are the beat writer for Pinwheel Fates. You've watched every game. You know the standings, \
the streaks, the rule changes, the drama. After each round, you write one report — 3 to 5 \
paragraphs — that tells the story of what just happened.

Your job is to find the story, not fill in a template.

## Finding the Lede
Every round has one story. Find it. The hierarchy:
1. A champion was crowned — everything else is context for that moment.
2. A team was eliminated — a sweep, a series loss, a season ending.
3. An upset — the last-place team beat the first-place team.
4. A streak lives or dies — seven straight wins means something.
5. A blowout or a classic — a 20-point demolition or 2-point thriller.
6. The standings shifted — two teams swapped positions, a playoff berth clinched.
7. The rules changed — governance reshaped the game, first round under new parameters.

Only ONE leads the piece. The rest are supporting details.

## Composing the Story
- Open with the lede — vivid, specific. Not "Round 8 saw some exciting games." Instead: \
"Rose City Thorns are your champions."
- Highlight what changed — the NEWS. What is different after this round than before?
- Surface what humans can't see from inside — scoring variance dropped 40% since a rule passed, \
a win streak correlates with the new three-point value, the margin between first and last \
narrowed from 6 to 2.
- Name the players — connect stats to their games. "Rosa Vex poured in 27 to close out the Hammers."
- Read the standings — a 10-4 team winning is expected, a 4-10 team winning is an upset.
- Detect the sweep — 3-0 in a series is a sweep, say so.
- Know where you are — regular season vs championship are different universes.
- Close with what the round reveals — not prescriptions, but patterns newly visible.

## What You Never Do
- Never prescribe — describe only. "The Thorns have won seven straight" not "Teams need to adjust."
- Never be generic — name a team, a score, a streak, or a player.
- Never contradict the data.
- Never lead with the loser.
- Never pad.

This is AI that amplifies human judgment. Governors control the rules but can't see the \
whole system. This report IS the whole. It surfaces patterns and dynamics no single governor \
can see. It doesn't tell them what to do — it makes them dramatically more capable of \
deciding for themselves.

The AI observes. Humans decide.

## Current Round Data

{round_data}
"""

GOVERNANCE_REPORT_PROMPT = """\
You are the Governance Mirror for Pinwheel Fates, a 3v3 basketball governance game.

Your job: reflect on governance activity this round. Surface voting coalitions, proposal patterns, \
and how the rule space is evolving. Show governors what they can't see from inside the system.

## Rules
1. You DESCRIBE. You never PRESCRIBE. Never say "governors should" or "the league needs to."
2. Be thorough (3-5 paragraphs). Look for patterns that individual governors might miss:
   - Voting coalitions: which governors consistently vote together?
   - Proposal themes: are multiple proposals targeting the same parameter category?
   - The gap between what passes and what helps teams win
3. If rules changed this round, connect them to game impact. State parameter name, old value, \
new value explicitly (e.g., "three_point_value changed from 3 to 4"), then describe the \
expected effect on gameplay.
4. If proposals failed, analyze what that reveals. Is it disagreement, or shared priority \
to keep things as they are?
5. Note governance velocity: is this the most active window of the season? Unusual silence?
6. Surface what's NOT being proposed — if defense stats are declining but no proposals \
target defense, that's a story.
7. Close with governance window status: next tally round, pending proposals, window state.
8. End with a "what the Floor is building" summary — describe the trajectory of governance \
decisions, not just the count.

## Governance Activity

{governance_data}
"""

PRIVATE_REPORT_PROMPT = """\
You are generating a Private Mirror for governor "{governor_id}" in Pinwheel Fates.

A private mirror reflects a governor's OWN behavior back to them. Only they see this.
It helps them understand their patterns and blind spots without telling them what to do.

## Rules
1. You DESCRIBE their behavior patterns. You never PRESCRIBE actions.
2. Write 2-3 paragraphs. Be specific to THIS governor's actions and context.
3. Compare their focus to league-wide patterns:
   - What categories are they proposing changes for vs. what others are focused on?
   - Are they concentrated in one area while missing another?
4. Surface blind spots:
   - "You haven't proposed anything about [category] despite it being the most-changed area."
   - "Your proposals focus on offense, but the league's biggest shifts have been in defense."
5. Show their voting record relative to outcomes:
   - "You voted yes on 3 rules that passed — scoring has risen 15% since."
   - "You've opposed every defensive rule change — all passed anyway."
6. Note their engagement trajectory:
   - Are they increasing participation, stable, or fading?
   - Frame activity relative to opportunity: "You voted on 2 of 5 proposals — selective engagement."
7. Never compare them to other specific governors by name. Reflect, don't rank.
8. If they haven't been active, contextualize what they missed — but without judgment.

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
    """Mock simulation report — follows The Pinwheel Post editorial prompt.

    Implements the lede hierarchy:
    1. Champion crowned
    2. Team eliminated/swept
    3. Upset (standings-aware)
    4. Streak lives or dies
    5. Blowout or classic
    6. Standings shifted
    7. Rules changed
    """

    games = round_data.get("games", [])
    if not games:
        return Report(
            id=f"r-sim-{round_number}-mock",
            report_type="simulation",
            round_number=round_number,
            content="Silence from the courts. No games this round.",
        )

    phase = narrative.phase if narrative else "regular"
    phase_label = ""
    if phase in ("finals", "championship"):
        phase_label = "championship"
    elif phase == "semifinal":
        phase_label = "semifinal"
    is_playoff = bool(phase_label)

    # --- Parse and classify every game ---
    entries: list[dict[str, object]] = []
    for g in games:
        home = g.get("home_team", "Home")
        away = g.get("away_team", "Away")
        hs: int = g.get("home_score", 0)
        aws: int = g.get("away_score", 0)
        margin = abs(hs - aws)
        winner = home if hs > aws else away
        loser = away if hs > aws else home
        w_score, l_score = max(hs, aws), min(hs, aws)
        winner_id: str = g.get("winner_team_id", "")
        home_id: str = g.get("home_team_id", "")
        away_id: str = g.get("away_team_id", "")
        loser_id = away_id if winner_id == home_id else home_id
        entries.append({
            "winner": winner, "loser": loser,
            "w_score": w_score, "l_score": l_score,
            "margin": margin, "total": hs + aws,
            "winner_id": winner_id, "loser_id": loser_id,
            "home_id": home_id, "away_id": away_id,
        })

    # Collect team IDs and names
    played_ids: set[str] = set()
    team_id_to_name: dict[str, str] = {}
    for e in entries:
        for fld in ("home_id", "away_id"):
            tid = str(e[fld])
            if tid:
                played_ids.add(tid)
        wid, lid = str(e["winner_id"]), str(e["loser_id"])
        if wid:
            team_id_to_name[wid] = str(e["winner"])
        if lid:
            team_id_to_name[lid] = str(e["loser"])

    # Build standings lookup (rank by team_id)
    standings_by_team: dict[str, dict[str, object]] = {}
    if narrative and narrative.standings:
        for s in narrative.standings:
            tid = str(s.get("team_id", ""))
            if tid:
                standings_by_team[tid] = s

    # --- LEDE HIERARCHY ---
    lede: str = ""
    lede_type: str = ""
    supporting: list[str] = []

    # 1. Champion crowned
    if (
        narrative and
        narrative.season_arc == "championship" and
        phase in ("finals", "championship") and
        entries
    ):
        # Find the champion from the games (winner in finals phase)
            champion = str(entries[0]["winner"])
            lede = f"{champion} are your champions."
            lede_type = "championship"

    # 2. Team eliminated/swept (playoff only)
    if not lede and is_playoff and narrative and narrative.streaks:
        for team_id, streak in narrative.streaks.items():
            if team_id in played_ids and streak <= -3:
                team_name = team_id_to_name.get(team_id, team_id)
                if abs(streak) >= 3:
                    lede = (
                        f"{team_name} were swept — {abs(streak)} straight "
                        f"losses end their season in the {phase_label}."
                    )
                    lede_type = "elimination"
                    break

    # 3. Upset (standings-aware)
    if not lede and narrative and narrative.standings and len(entries) > 0:
        for e in entries:
            winner_id = str(e["winner_id"])
            loser_id = str(e["loser_id"])
            winner_rank = standings_by_team.get(winner_id, {}).get("rank", 99)
            loser_rank = standings_by_team.get(loser_id, {}).get("rank", 99)

            # Upset = lower-ranked team beats higher-ranked team by 2+ positions
            if (
                isinstance(winner_rank, int) and
                isinstance(loser_rank, int) and
                winner_rank - loser_rank >= 2
            ):
                    w = str(e["winner"])
                    lo = str(e["loser"])
                    ws = int(e["w_score"])  # type: ignore[arg-type]
                    ls = int(e["l_score"])  # type: ignore[arg-type]
                    lede = f"{w} shocked {lo} {ws}-{ls}. The standings didn't predict this one."
                    lede_type = "upset"
                    break

    # 4. Streak lives or dies (5+ games)
    if not lede and narrative and narrative.streaks:
        for team_id, streak in narrative.streaks.items():
            if team_id in played_ids and abs(streak) >= 5:
                team_name = team_id_to_name.get(team_id, team_id)
                won = any(str(e["winner_id"]) == team_id for e in entries)
                if streak > 0 and won:
                    lede = f"{team_name} extended their {streak}-game win streak."
                    lede_type = "streak"
                    break
                elif streak < 0 and not won:
                    lede = f"{team_name} have now lost {abs(streak)} straight."
                    lede_type = "streak"
                    break

    # 5. Blowout or classic
    if not lede and entries:
        biggest_blowout = max(entries, key=lambda e: int(e["margin"]))  # type: ignore[arg-type]
        closest_game = min(entries, key=lambda e: int(e["margin"]))  # type: ignore[arg-type]

        if int(biggest_blowout["margin"]) >= 15:  # type: ignore[arg-type]
            w = str(biggest_blowout["winner"])
            lo = str(biggest_blowout["loser"])
            ws = int(biggest_blowout["w_score"])  # type: ignore[arg-type]
            ls = int(biggest_blowout["l_score"])  # type: ignore[arg-type]
            m = int(biggest_blowout["margin"])  # type: ignore[arg-type]
            lede = f"{w} demolished {lo} {ws}-{ls}. The {m}-point margin speaks for itself."
            lede_type = "blowout"
        elif int(closest_game["margin"]) <= 3:  # type: ignore[arg-type]
            w = str(closest_game["winner"])
            lo = str(closest_game["loser"])
            ws = int(closest_game["w_score"])  # type: ignore[arg-type]
            ls = int(closest_game["l_score"])  # type: ignore[arg-type]
            m = int(closest_game["margin"])  # type: ignore[arg-type]
            lede = f"{w} survived {lo} {ws}-{ls} in a thriller — just {m} points separated them."
            lede_type = "classic"

    # 6. Standings shifted (check for rank swaps)
    # (We don't have pre-round standings in mock, so skip this for now)

    # 7. Rules changed
    rule_changes = round_data.get("rule_changes", [])
    if not lede and rule_changes:
        change_notes = [
            rc["parameter"].replace("_", " ")
            for rc in rule_changes
            if rc.get("parameter")
        ]
        if change_notes:
            lede = (
                f"The rules changed. First games under new parameters: "
                f"{', '.join(change_notes)} adjusted."
            )
            lede_type = "rules"

    # Default lede if nothing else hits
    if not lede:
        first_game = entries[0]
        w = str(first_game["winner"])
        lo = str(first_game["loser"])
        ws = int(first_game["w_score"])  # type: ignore[arg-type]
        ls = int(first_game["l_score"])  # type: ignore[arg-type]
        if phase_label:
            lede = f"The {phase_label} continued. {w} beat {lo} {ws}-{ls}."
        else:
            lede = f"Round {round_number}. {w} beat {lo} {ws}-{ls}."
        lede_type = "default"

    # --- SUPPORTING DETAILS ---
    # Include other significant games not covered by the lede
    for e in entries:
        w = str(e["winner"])
        lo = str(e["loser"])
        ws = int(e["w_score"])  # type: ignore[arg-type]
        ls = int(e["l_score"])  # type: ignore[arg-type]
        m = int(e["margin"])  # type: ignore[arg-type]

        # Skip the game that became the lede
        if lede_type in ("blowout", "classic", "upset") and (
            (lede_type == "blowout" and m >= 15 and w in lede) or
            (lede_type == "classic" and m <= 3 and w in lede) or
            (lede_type == "upset" and w in lede)
        ):
            continue

        # Add other notable games
        if phase_label:
            supporting.append(f"{w} beat {lo} {ws}-{ls} in the {phase_label}.")
        elif m >= 10:
            supporting.append(f"{w} rolled past {lo} {ws}-{ls}.")
        elif m <= 4:
            supporting.append(f"{w} edged {lo} {ws}-{ls}.")
        else:
            supporting.append(f"{w} beat {lo} {ws}-{ls}.")

    # --- WHAT CHANGED (system-level observation) ---
    what_changed: str = ""
    if narrative and len(entries) > 1:
        # Check for scoring variance
        totals = [int(e["total"]) for e in entries]  # type: ignore[arg-type]
        avg_total = sum(totals) // len(totals)
        if avg_total >= 80:
            what_changed = f"Scoring surged to {avg_total} per game across the slate."
        elif avg_total <= 35:
            what_changed = f"Defense dominated — just {avg_total} points per game."

        # Check for margin compression
        if not what_changed:
            margins = [int(e["margin"]) for e in entries]  # type: ignore[arg-type]
            avg_margin = sum(margins) // len(margins)
            if avg_margin <= 5:
                what_changed = f"Every game was close — average margin just {avg_margin} points."

        # Streaks context
        if not what_changed and narrative.streaks:
            active_streaks = [
                (team_id_to_name.get(tid, tid), s)
                for tid, s in narrative.streaks.items()
                if tid in played_ids and abs(s) >= 3
            ]
            if active_streaks:
                streak_team, streak_val = active_streaks[0]
                if streak_val > 0:
                    what_changed = f"{streak_team} are riding a {streak_val}-game win streak."
                else:
                    what_changed = f"{streak_team} have lost {abs(streak_val)} straight."

    # --- HOT PLAYERS ---
    hot_player_lines: list[str] = []
    if narrative and narrative.hot_players:
        for hp in narrative.hot_players[:2]:
            hp_name = hp.get("name", "?")
            hp_team = hp.get("team_name", "?")
            hp_pts = hp.get("value", 0)

            # Find their game
            player_game: dict[str, object] | None = None
            for e in entries:
                if hp_team in (e["winner"], e["loser"]):
                    player_game = e
                    break

            if player_game:
                if hp_team == player_game["winner"]:
                    hot_player_lines.append(
                        f"{hp_name} poured in {hp_pts} to lead {hp_team}'s win."
                    )
                else:
                    hot_player_lines.append(
                        f"{hp_name} scored {hp_pts} for {hp_team} in a losing effort."
                    )

    # --- SURFACE THE INVISIBLE (closing observation) ---
    closing: str = ""
    if narrative:
        # Phase context
        if phase in ("semifinals", "finals") and not is_playoff:
            closing = "Playoff seeding is coming into focus."
        elif narrative.season_arc == "late" and narrative.total_rounds > 0:
            closing = (
                f"Round {narrative.round_number} of {narrative.total_rounds}. "
                f"The regular season is winding down."
            )

        # Governance context
        if not closing and narrative.pending_proposals > 0:
            plural = 's' if narrative.pending_proposals != 1 else ''
            verb = 'awaits' if narrative.pending_proposals == 1 else 'await'
            closing = f"{narrative.pending_proposals} proposal{plural} {verb} the governors' vote."

    # --- COMPOSE THE REPORT ---
    lines = [lede]

    # Add supporting games (max 2)
    lines.extend(supporting[:2])

    # Add what changed
    if what_changed:
        lines.append(what_changed)

    # Add hot players
    lines.extend(hot_player_lines)

    # Add closing
    if closing:
        lines.append(closing)

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
    """Mock governance report — surfaces coalitions, patterns, trajectory."""
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

    # Proposal activity — count + velocity analysis
    if proposals:
        lines.append(
            f"Round {round_number} saw {len(proposals)} proposal(s) "
            "enter the governance arena."
        )
        # Detect proposal clustering by parameter category
        params = [p.get("parameter", "") for p in proposals if p.get("parameter")]
        if len(params) > 1:
            # Group by category prefix (e.g., "three_point_" or "elam_")
            categories: dict[str, int] = {}
            for p in params:
                # Extract category from parameter name
                parts = p.split("_")
                category = parts[0] if parts else p
                categories[category] = categories.get(category, 0) + 1
            # If multiple proposals target the same category, note it
            for cat, count in categories.items():
                if count > 1:
                    lines.append(
                        f"{count} proposals targeted {cat.replace('_', ' ')} parameters — "
                        "the Floor is focused on this dimension of the game."
                    )
                    break
    else:
        lines.append(
            f"Round {round_number} was quiet on the governance front "
            "— no proposals filed."
        )

    # Voting analysis — add alignment patterns
    if votes:
        yes_count = sum(1 for v in votes if v.get("vote") == "yes")
        no_count = sum(1 for v in votes if v.get("vote") == "no")
        lines.append(
            f"Governors cast {len(votes)} votes "
            f"({yes_count} yes, {no_count} no)."
        )
        # Detect voting coalitions
        if yes_count == len(votes):
            lines.append("The vote was unanimous — consensus is forming.")
        elif no_count == len(votes):
            lines.append("The vote was unanimously against — the Floor is aligned in resistance.")
        elif yes_count > 0 and no_count > 0:
            lines.append("The Floor was split on this decision — voting coalitions are emerging.")

    # Rule changes — connect to game impact
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
                # Add expected gameplay impact
                if "three_point" in param.lower():
                    if new_val > old_val:  # type: ignore[operator]
                        lines.append("    Perimeter shooting is now more valuable.")
                    else:
                        lines.append("    Inside scoring gains relative value.")
                elif "elam" in param.lower():
                    lines.append("    Endgame dynamics will shift.")
                elif "steal" in param.lower() or "defense" in param.lower():
                    lines.append("    Defensive intensity should change accordingly.")
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

    # "What the Floor is building" closing — governance trajectory
    if rules_changed:
        params_changed = [rc.get("parameter", "") for rc in rules_changed]
        if any("three_point" in p for p in params_changed):
            lines.append(
                "The Floor is reshaping the offensive meta — "
                "the next round will reveal what the new parameters unlock."
            )
        elif any("elam" in p for p in params_changed):
            lines.append(
                "The Floor is tuning endgame mechanics — "
                "close games will play differently from here forward."
            )
        else:
            lines.append(
                "The Floor is experimenting with the game's foundational parameters — "
                "governance is active and the ruleset is evolving."
            )
    elif proposals and not rules_changed:
        lines.append(
            "Proposals were submitted but not yet enacted — "
            "the next governance window will show whether they gain traction."
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
    """Mock private report — shows governor behavior relative to the system."""
    import random as _rng

    proposals = governor_data.get("proposals_submitted", 0)
    votes = governor_data.get("votes_cast", 0)

    # Seed for deterministic mock content based on governor and round
    rng = _rng.Random(hash((governor_id, round_number)))

    # Simulated league context (in production, this would be real data)
    total_proposals_this_round = rng.randint(3, 8)
    league_focus_areas = ["offense", "defense", "pace", "three-point"]
    governor_focus = rng.choice(league_focus_areas)
    league_focus = rng.choice(
        [a for a in league_focus_areas if a != governor_focus]
    )

    lines = []

    # --- Activity summary with context ---
    if proposals == 0 and votes == 0:
        # Inactive governor — contextualize what they missed
        lines.append(
            f"You were quiet this round. "
            f"The Floor saw {total_proposals_this_round} "
            f"proposals debated — most focused on {league_focus} adjustments. "
            "Your absence is noted, not judged."
        )
    else:
        # Active governor — frame activity level
        total_activity = proposals + votes
        if total_activity <= 2:
            activity_level = "light"
        elif total_activity <= 4:
            activity_level = "active"
        else:
            activity_level = "busy"

        lines.append(f"This was a {activity_level} round for you. ")

        if proposals > 0:
            if proposals >= 2:
                activity_descriptor = "one of the more active governors"
            else:
                activity_descriptor = "contributing to the debate"
            plural = "s" if proposals != 1 else ""
            lines[-1] += (
                f"You submitted {proposals} proposal{plural} — "
                f"{activity_descriptor}. "
            )

        if votes > 0:
            vote_context = (
                "selective"
                if votes < total_proposals_this_round // 2
                else "engaged"
            )
            plural = "s" if votes != 1 else ""
            lines[-1] += (
                f"You cast {votes} vote{plural} out of "
                f"{total_proposals_this_round} proposals — "
                f"{vote_context} participation."
            )

    # --- Blind spot surfacing ---
    if proposals > 0:
        lines.append(
            f"Your proposals have focused on {governor_focus}. "
            f"Meanwhile, the league has seen more changes in {league_focus} — "
            f"an area you haven't addressed yet."
        )
    elif votes > 0:
        lines.append(
            f"You voted but didn't propose. "
            f"The league's biggest debates this round "
            f"centered on {league_focus} — "
            f"an area where your voice hasn't shaped the agenda."
        )

    # --- Engagement trajectory ---
    if proposals + votes > 0:
        trajectory = rng.choice(["increasing", "steady", "declining"])
        if trajectory == "increasing":
            lines.append(
                "Your participation is trending up — "
                "you're more engaged than in earlier rounds."
            )
        elif trajectory == "steady":
            lines.append(
                "Your engagement has been consistent across the season."
            )
        else:
            lines.append(
                "Your activity has tapered off from earlier rounds. "
                "The system continues to evolve without you."
            )

    content = " ".join(lines)

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

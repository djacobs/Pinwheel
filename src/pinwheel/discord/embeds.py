"""Rich Discord embed builders for Pinwheel Fates.

Builds discord.Embed objects for game results, standings, proposals,
reports, and schedules. Each builder takes domain data and returns
a styled embed ready to send.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from pinwheel.core.onboarding import LeagueContext
    from pinwheel.models.governance import (
        Proposal,
        ProposalInterpretation,
        RuleInterpretation,
        VoteTally,
    )
    from pinwheel.models.report import Report
    from pinwheel.models.tokens import TokenBalance, Trade


# ---------------------------------------------------------------------------
# Game Context — enrichment data for smart game result embeds
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class TeamGameContext:
    """Contextual data about one team in a game result.

    Attributes:
        streak: Current streak length after this game.
            Positive = win streak, negative = loss streak.
        standing_position: 1-indexed standings position after this game,
            or None if unavailable.
        standing_movement: Change in standings position compared to
            before this round. Negative = moved up (improved).
            Positive = dropped. None if unavailable.
    """

    streak: int = 0
    standing_position: int | None = None
    standing_movement: int | None = None


@dataclasses.dataclass(frozen=True)
class GameContext:
    """Enrichment context for a single game result embed.

    Passed into ``build_game_result_embed`` and ``build_team_game_result_embed``
    to enrich the flat score card with dramatic context: streaks, standings
    movement, margin significance, and rule-change context.

    Attributes:
        home: Context for the home team.
        away: Context for the away team.
        margin_label: Human-readable margin significance label, e.g.
            "Closest game of the season" or "Biggest blowout this season".
            Empty string when the margin is unremarkable.
        new_rules: List of short descriptions of rule changes active for
            the first time in this round (e.g. "3-point range extended").
            Empty list when there are no new rules.
    """

    home: TeamGameContext = dataclasses.field(default_factory=TeamGameContext)
    away: TeamGameContext = dataclasses.field(default_factory=TeamGameContext)
    margin_label: str = ""
    new_rules: list[str] = dataclasses.field(default_factory=list)


def compute_game_context(
    game_data: dict[str, object],
    all_games: list[dict[str, object]],
    standings_before: list[dict[str, object]] | None = None,
    standings_after: list[dict[str, object]] | None = None,
    new_rules: list[str] | None = None,
) -> GameContext:
    """Compute enrichment context for a game result from history data.

    This is a pure function — no DB access. Callers pass pre-fetched data.

    Args:
        game_data: The current game result dict (same shape as game summaries).
        all_games: All game results in the season up to and including
            this game, as dicts with keys: home_team_id, away_team_id,
            winner_team_id, home_score, away_score, round_number.
        standings_before: Standings list *before* this round (each dict has
            team_id and positional ordering). None if not available.
        standings_after: Standings list *after* this round. None if not
            available.
        new_rules: List of short rule-change descriptions for rules
            enacted in this round. None or empty list if no changes.

    Returns:
        A GameContext with all enrichment fields populated.
    """
    home_id = str(game_data.get("home_team_id", ""))
    away_id = str(game_data.get("away_team_id", ""))

    home_streak = _compute_team_streak(home_id, all_games)
    away_streak = _compute_team_streak(away_id, all_games)

    home_pos = _find_standing_position(home_id, standings_after)
    away_pos = _find_standing_position(away_id, standings_after)
    home_prev_pos = _find_standing_position(home_id, standings_before)
    away_prev_pos = _find_standing_position(away_id, standings_before)

    home_movement: int | None = None
    if home_pos is not None and home_prev_pos is not None:
        home_movement = home_prev_pos - home_pos  # negative = dropped

    away_movement: int | None = None
    if away_pos is not None and away_prev_pos is not None:
        away_movement = away_prev_pos - away_pos

    margin_label = _compute_margin_label(game_data, all_games)

    return GameContext(
        home=TeamGameContext(
            streak=home_streak,
            standing_position=home_pos,
            standing_movement=home_movement,
        ),
        away=TeamGameContext(
            streak=away_streak,
            standing_position=away_pos,
            standing_movement=away_movement,
        ),
        margin_label=margin_label,
        new_rules=list(new_rules) if new_rules else [],
    )


def _compute_team_streak(
    team_id: str,
    all_games: list[dict[str, object]],
) -> int:
    """Compute the current win/loss streak for a team from game history.

    Returns positive for win streaks, negative for loss streaks, 0 if no
    games found for this team.
    """
    if not team_id or not all_games:
        return 0

    # Filter to games involving this team, sorted chronologically
    team_games = [
        g for g in all_games
        if str(g.get("home_team_id", "")) == team_id
        or str(g.get("away_team_id", "")) == team_id
    ]
    if not team_games:
        return 0

    team_games.sort(key=lambda g: (int(g.get("round_number", 0)), int(g.get("matchup_index", 0))))

    # Walk backward from most recent game
    streak = 0
    last_won: bool | None = None
    for g in reversed(team_games):
        won = str(g.get("winner_team_id", "")) == team_id
        if last_won is None:
            last_won = won
        if won != last_won:
            break
        streak += 1

    if last_won is None:
        return 0
    return streak if last_won else -streak


def _find_standing_position(
    team_id: str,
    standings: list[dict[str, object]] | None,
) -> int | None:
    """Find the 1-indexed position of a team in a standings list.

    Returns None if standings is None or team not found.
    """
    if not standings or not team_id:
        return None
    for i, entry in enumerate(standings):
        if str(entry.get("team_id", "")) == team_id:
            return i + 1
    return None


def _compute_margin_label(
    game_data: dict[str, object],
    all_games: list[dict[str, object]],
) -> str:
    """Compute margin significance label for a game.

    Returns a label like "Closest game of the season" or "Biggest blowout
    this season" if this game's margin is the most extreme seen so far.
    Returns empty string if the margin is unremarkable.
    """
    home_score = int(game_data.get("home_score", 0))
    away_score = int(game_data.get("away_score", 0))
    this_margin = abs(home_score - away_score)

    if len(all_games) < 2:
        # Only one game played — no basis for comparison
        return ""

    # Compute all margins from the season (including this game)
    margins: list[int] = []
    for g in all_games:
        hs = int(g.get("home_score", 0))
        aws = int(g.get("away_score", 0))
        margins.append(abs(hs - aws))

    if not margins:
        return ""

    min_margin = min(margins)
    max_margin = max(margins)

    # Only label if this game IS the extreme (not tied with many others)
    if this_margin == min_margin and this_margin != max_margin:
        count_at_min = margins.count(min_margin)
        if count_at_min <= 2:
            return "Closest game of the season"

    if this_margin == max_margin and this_margin != min_margin:
        count_at_max = margins.count(max_margin)
        if count_at_max <= 2:
            return "Biggest blowout of the season"

    return ""


def _format_streak(streak: int) -> str:
    """Format a streak integer into a short display string.

    Examples: 3 -> "W3", -2 -> "L2", 0 -> "".
    """
    if streak > 0:
        return f"W{streak}"
    elif streak < 0:
        return f"L{abs(streak)}"
    return ""


def _ordinal_suffix(n: int) -> str:
    """Return ordinal suffix for a number (1st, 2nd, 3rd, 4th, etc.)."""
    if 11 <= n % 100 <= 13:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


def _format_standing_movement(position: int | None, movement: int | None) -> str:
    """Format a standings movement into a display string.

    Args:
        position: Current 1-indexed position (e.g. 1 = first place).
        movement: Positive = moved up, negative = dropped.

    Returns:
        String like "moved to 1st" or "dropped to 4th", or empty.
    """
    if position is None:
        return ""
    suffix = _ordinal_suffix(position)
    if movement is not None and movement > 0:
        return f"moved to {position}{suffix}"
    elif movement is not None and movement < 0:
        return f"dropped to {position}{suffix}"
    return ""

# Brand colors
COLOR_GAME = 0xE74C3C  # Red — game results
COLOR_LIVE = 0x00FF7F  # Spring green — live game indicator
COLOR_GOVERNANCE = 0x3498DB  # Blue — governance
COLOR_REPORT = 0x9B59B6  # Purple — AI reports
COLOR_SCHEDULE = 0x2ECC71  # Green — schedule
COLOR_STANDINGS = 0xF39C12  # Gold — standings
COLOR_WARNING = 0xE67E22  # Orange — admin review / warnings
COLOR_ONBOARDING = 0x1ABC9C  # Teal — onboarding / state of the league


def build_game_result_embed(
    game_data: dict[str, object],
    playoff_context: str | None = None,
    game_context: GameContext | None = None,
) -> discord.Embed:
    """Build an embed for a completed game result.

    Args:
        game_data: Dict with keys: home_team (or home_team_name), away_team
            (or away_team_name), home_score, away_score, winner_team_id,
            elam_activated, total_possessions.
        playoff_context: 'semifinal', 'finals', or None for regular season.
        game_context: Optional enrichment context with streaks, standings
            movement, margin significance, and rule-change context.
    """
    home = str(game_data.get("home_team", game_data.get("home_team_name", "Home")))
    away = str(game_data.get("away_team", game_data.get("away_team_name", "Away")))
    home_score = game_data.get("home_score", 0)
    away_score = game_data.get("away_score", 0)
    elam_target = game_data.get("elam_target_score")

    # Playoff-aware title
    if playoff_context == "finals":
        title = f"CHAMPIONSHIP FINALS: {home} vs {away}"
    elif playoff_context == "semifinal":
        title = f"SEMIFINAL: {home} vs {away}"
    else:
        title = f"{home} vs {away}"

    description = f"**{home}** {home_score} - {away_score} **{away}**"

    if elam_target:
        description += f"\nElam Target: {elam_target}"

    # Append streak context for both teams
    if game_context:
        streak_parts: list[str] = []
        home_streak_str = _format_streak(game_context.home.streak)
        away_streak_str = _format_streak(game_context.away.streak)
        if home_streak_str:
            streak_parts.append(f"{home} {home_streak_str}")
        if away_streak_str:
            streak_parts.append(f"{away} {away_streak_str}")
        if streak_parts:
            description += "\n" + " | ".join(streak_parts)

        # Standings movement
        movement_parts: list[str] = []
        home_move = _format_standing_movement(
            game_context.home.standing_position,
            game_context.home.standing_movement,
        )
        away_move = _format_standing_movement(
            game_context.away.standing_position,
            game_context.away.standing_movement,
        )
        if home_move:
            movement_parts.append(f"{home} {home_move}")
        if away_move:
            movement_parts.append(f"{away} {away_move}")
        if movement_parts:
            description += "\n" + " | ".join(movement_parts)

        # Margin significance
        if game_context.margin_label:
            description += f"\n*{game_context.margin_label}*"

        # New rules context
        if game_context.new_rules:
            rules_text = ", ".join(game_context.new_rules[:3])
            description += f"\nFirst game under new rules: {rules_text}"

    # Use gold for championship, different shade for semis
    color = COLOR_GAME
    if playoff_context == "finals":
        color = COLOR_STANDINGS  # gold for championship
    elif playoff_context == "semifinal":
        color = 0xE91E63  # pink/magenta for semis — stands out from regular red

    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
    )
    embed.add_field(
        name="Possessions",
        value=str(game_data.get("total_possessions", "N/A")),
        inline=True,
    )
    if playoff_context:
        label = "Championship Finals" if playoff_context == "finals" else "Semifinal Playoffs"
        embed.add_field(name="Stage", value=label, inline=True)
    embed.set_footer(text="Pinwheel Fates")
    return embed


def build_games_live_embed(
    games: list[dict[str, object]],
    playoff_context: str | None = None,
) -> discord.Embed:
    """Build an embed announcing that games are now live.

    Sent when the replay presentation starts so Discord users know
    games are in progress and can watch on the arena page.

    Args:
        games: List of game_starting event dicts, each with
            home_team_name, away_team_name.
        playoff_context: 'semifinal', 'finals', or None.
    """
    if playoff_context == "finals":
        title = "CHAMPIONSHIP FINALS — LIVE NOW"
    elif playoff_context == "semifinal":
        title = "SEMIFINAL PLAYOFFS — LIVE NOW"
    else:
        title = "GAMES LIVE NOW"

    matchups = []
    for g in games:
        home = str(g.get("home_team_name", g.get("home_team", "?")))
        away = str(g.get("away_team_name", g.get("away_team", "?")))
        matchups.append(f"**{home}** vs **{away}**")

    description = "\n".join(matchups)
    description += "\n\nWatch live on the arena page."

    color = COLOR_LIVE
    if playoff_context == "finals":
        color = COLOR_STANDINGS
    elif playoff_context == "semifinal":
        color = 0xE91E63

    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
    )
    embed.set_footer(text="Pinwheel Fates")
    return embed


def build_standings_embed(
    standings: list[dict[str, object]],
    streaks: dict[str, int] | None = None,
    season_phase: str | None = None,
) -> discord.Embed:
    """Build an embed for current league standings.

    Args:
        standings: List of dicts with keys: team_name, team_id, wins,
            losses, points_for, points_against.
        streaks: Optional dict mapping team_id to streak length
            (positive=wins, negative=losses).
        season_phase: Optional phase label like 'regular', 'semifinal',
            'finals', 'championship'.
    """
    phase_labels: dict[str, str] = {
        "semifinal": "Standings -- Playoffs",
        "finals": "Standings -- Championship Finals",
        "championship": "Standings -- Championship",
    }
    title = phase_labels.get(season_phase or "", "League Standings")

    embed = discord.Embed(
        title=title,
        color=COLOR_STANDINGS,
    )

    if not standings:
        embed.description = "No games played yet."
        return embed

    streak_map = streaks or {}
    lines: list[str] = []
    for i, team in enumerate(standings, 1):
        name = team.get("team_name", team.get("team_id", "???"))
        wins = team.get("wins", 0)
        losses = team.get("losses", 0)
        team_id = str(team.get("team_id", ""))
        streak = streak_map.get(team_id, 0)
        streak_str = ""
        if streak >= 3:
            streak_str = f" W{streak}"
        elif streak <= -3:
            streak_str = f" L{abs(streak)}"
        lines.append(f"**{i}.** {name} ({wins}W-{losses}L){streak_str}")

    embed.description = "\n".join(lines)
    embed.set_footer(text="Pinwheel Fates")
    return embed


def build_proposal_embed(proposal: Proposal) -> discord.Embed:
    """Build an embed for a governance proposal.

    Args:
        proposal: The Proposal model instance.
    """
    embed = discord.Embed(
        title=f"Proposal: {proposal.raw_text[:80]}",
        color=COLOR_GOVERNANCE,
    )
    embed.add_field(name="Status", value=proposal.status.capitalize(), inline=True)
    embed.add_field(name="Tier", value=str(proposal.tier), inline=True)
    embed.add_field(name="Governor", value=proposal.governor_id, inline=True)

    if proposal.interpretation:
        interp = proposal.interpretation
        if interp.parameter:
            embed.add_field(
                name="Parameter Change",
                value=f"`{interp.parameter}`: {interp.old_value} -> {interp.new_value}",
                inline=False,
            )
        if interp.impact_analysis:
            embed.add_field(
                name="Impact Analysis",
                value=interp.impact_analysis[:1024],
                inline=False,
            )

    embed.set_footer(text="Pinwheel Fates")
    return embed


def build_vote_tally_embed(tally: VoteTally, proposal_text: str = "") -> discord.Embed:
    """Build an embed for a vote tally result.

    Args:
        tally: The VoteTally model instance.
        proposal_text: Optional text of the proposal for context.
    """
    status = "PASSED" if tally.passed else "FAILED"
    color = 0x2ECC71 if tally.passed else 0xE74C3C

    embed = discord.Embed(
        title=f"Vote Result: {status}",
        color=color,
    )
    if proposal_text:
        embed.description = proposal_text[:200]

    total_votes = tally.yes_count + tally.no_count
    yes_label = "vote" if tally.yes_count == 1 else "votes"
    no_label = "vote" if tally.no_count == 1 else "votes"
    embed.add_field(
        name="Yes",
        value=f"{tally.weighted_yes:.2f} ({tally.yes_count} {yes_label})",
        inline=True,
    )
    embed.add_field(
        name="No",
        value=f"{tally.weighted_no:.2f} ({tally.no_count} {no_label})",
        inline=True,
    )
    embed.add_field(
        name="Threshold",
        value=f"{tally.threshold:.0%}",
        inline=True,
    )
    embed.add_field(
        name="Votes Cast",
        value=f"{total_votes} governor{'s' if total_votes != 1 else ''} voted",
        inline=True,
    )
    if tally.total_eligible > 0:
        participation_pct = total_votes / tally.total_eligible * 100
        eligible = tally.total_eligible
        embed.add_field(
            name="Participation",
            value=(f"{total_votes} of {eligible} possible voters ({participation_pct:.0f}%)"),
            inline=True,
        )
    embed.set_footer(text="Pinwheel Fates")
    return embed


def build_proposal_announcement_embed(
    proposal_text: str,
    parameter: str | None = None,
    old_value: object = None,
    new_value: object = None,
    tier: int = 1,
    threshold: float = 0.5,
    wild: bool = False,
) -> discord.Embed:
    """Build a public embed announcing a proposal is open for voting.

    Args:
        proposal_text: The raw text of the proposal.
        parameter: The rule parameter being changed (if any).
        old_value: Current value of the parameter.
        new_value: Proposed new value.
        tier: Governance tier of the proposal.
        threshold: Vote threshold needed to pass.
        wild: Whether this is a wild proposal (Tier 5+ or low confidence).
    """
    embed = discord.Embed(
        title="New Proposal on the Floor",
        description=proposal_text,
        color=COLOR_GOVERNANCE,
    )
    if parameter:
        change_str = f"`{parameter}`: {old_value} -> {new_value}"
        embed.add_field(name="Parameter Change", value=change_str, inline=False)
    embed.add_field(name="Tier", value=str(tier), inline=True)
    embed.add_field(name="Threshold", value=f"{threshold:.0%}", inline=True)
    if wild:
        embed.add_field(
            name="Wild Proposal",
            value=f"This is a wild proposal (Tier {tier}). Admin may veto before tally.",
            inline=False,
        )
    embed.set_footer(text="Use /vote to cast your vote")
    return embed


def build_report_embed(report: Report) -> discord.Embed:
    """Build an embed for an AI-generated report.

    Args:
        report: The Report model instance.
    """
    type_labels = {
        "simulation": "Simulation Report",
        "governance": "The Floor \u2014 Report",
        "private": "Private Report",
        "series": "Series Report",
        "season": "Season Report",
        "state_of_the_league": "State of the League",
        "impact_validation": "Impact Validation",
        "leverage": "Your Influence",
        "behavioral": "Your Governance Pattern",
    }
    title = type_labels.get(report.report_type, f"Report: {report.report_type}")

    embed = discord.Embed(
        title=f"{title} -- Round {report.round_number}",
        description=report.content[:4096],
        color=COLOR_REPORT,
    )
    embed.set_footer(text="Pinwheel Fates")
    return embed


def build_schedule_embed(
    upcoming_slots: list[dict],
) -> discord.Embed:
    """Build an embed showing upcoming time slots with start times.

    Args:
        upcoming_slots: List of slot dicts, each with ``start_time``
            (formatted string or None) and ``games`` (list of matchup
            dicts with ``home_team_name`` and ``away_team_name``).
    """
    embed = discord.Embed(
        title="Upcoming Schedule",
        color=COLOR_SCHEDULE,
    )

    if not upcoming_slots:
        embed.description = "No games scheduled."
        embed.set_footer(text="Pinwheel Fates")
        return embed

    sections: list[str] = []
    for slot in upcoming_slots:
        start = slot.get("start_time")
        header = f"**{start}**" if start else "**Upcoming**"

        matchup_lines: list[str] = []
        for matchup in slot.get("games", []):
            home = matchup.get("home_team_name", "TBD")
            away = matchup.get("away_team_name", "TBD")
            matchup_lines.append(f"{home} vs {away}")

        sections.append(header + "\n" + "\n".join(matchup_lines))

    embed.description = "\n\n".join(sections)
    embed.set_footer(text="Pinwheel Fates")
    return embed


def build_interpretation_embed(
    raw_text: str,
    interpretation: RuleInterpretation,
    tier: int,
    token_cost: int,
    tokens_remaining: int,
    governor_name: str = "",
    interpretation_v2: ProposalInterpretation | None = None,
) -> discord.Embed:
    """Build an embed showing AI interpretation of a proposal.

    Displayed ephemeral with confirm/revise/cancel buttons.
    When interpretation_v2 is provided, shows rich V2 effects instead of
    the legacy single-parameter view.
    """
    embed = discord.Embed(
        title="Proposal Interpretation",
        color=COLOR_GOVERNANCE,
    )

    embed.description = f'"{raw_text}"'

    if interpretation_v2 and interpretation_v2.effects:
        # V2 rich effects display
        for effect in interpretation_v2.effects:
            if effect.effect_type == "parameter_change" and effect.parameter:
                embed.add_field(
                    name="Parameter Change",
                    value=(
                        f"`{effect.parameter}`: "
                        f"{effect.old_value} -> {effect.new_value}"
                    ),
                    inline=False,
                )
            elif effect.effect_type == "hook_callback":
                hook_label = effect.hook_point or "custom hook"
                embed.add_field(
                    name=f"Hook: {hook_label}",
                    value=effect.description[:1024],
                    inline=False,
                )
            elif effect.effect_type == "meta_mutation":
                target = effect.target_selector or "all"
                op = effect.meta_operation or "set"
                embed.add_field(
                    name=f"Meta: {effect.meta_field or 'update'}",
                    value=(
                        f"{op} on {effect.target_type or 'entity'} ({target})\n"
                        f"{effect.description[:900]}"
                    ),
                    inline=False,
                )
            elif effect.effect_type == "narrative":
                embed.add_field(
                    name="Narrative Effect",
                    value=effect.description[:1024],
                    inline=False,
                )
            elif effect.effect_type == "custom_mechanic":
                mechanic_desc = effect.mechanic_description or effect.description
                value_parts = [f"**{mechanic_desc[:500]}**"]
                if effect.mechanic_implementation_spec:
                    value_parts.append(
                        f"\n*Needs dev work:* {effect.mechanic_implementation_spec[:400]}"
                    )
                embed.add_field(
                    name="New Mechanic (needs dev work)",
                    value="\n".join(value_parts)[:1024],
                    inline=False,
                )
            else:
                embed.add_field(
                    name=effect.effect_type.replace("_", " ").title(),
                    value=effect.description[:1024],
                    inline=False,
                )
    elif interpretation.parameter:
        # Legacy single-parameter display
        embed.add_field(
            name="Parameter Change",
            value=(
                f"`{interpretation.parameter}`: "
                f"{interpretation.old_value} -> {interpretation.new_value}"
            ),
            inline=False,
        )
    elif interpretation.clarification_needed:
        embed.add_field(
            name="Needs Clarification",
            value="Could not map to a game parameter.",
            inline=False,
        )

    if interpretation.impact_analysis:
        embed.add_field(
            name="Impact Analysis",
            value=interpretation.impact_analysis[:1024],
            inline=False,
        )

    embed.add_field(name="Tier", value=str(tier), inline=True)
    embed.add_field(
        name="Cost",
        value=f"{token_cost} PROPOSE token",
        inline=True,
    )
    embed.add_field(
        name="Remaining",
        value=f"{tokens_remaining} PROPOSE",
        inline=True,
    )
    confidence_pct = f"{interpretation.confidence:.0%}"
    embed.add_field(
        name="Confidence",
        value=confidence_pct,
        inline=True,
    )

    if governor_name:
        embed.set_author(name=governor_name)
    embed.set_footer(text="Pinwheel Fates -- Confirm, Revise, or Cancel")
    return embed


def build_amendment_confirm_embed(
    original_text: str,
    amendment_text: str,
    interpretation: RuleInterpretation,
    amendment_number: int,
    max_amendments: int,
    amend_tokens_remaining: int,
    governor_name: str = "",
    interpretation_v2: ProposalInterpretation | None = None,
) -> discord.Embed:
    """Build an embed showing the amendment interpretation for confirmation.

    Displayed ephemeral with confirm/cancel buttons before the amendment
    is committed.

    Args:
        original_text: The original proposal text being amended.
        amendment_text: The amendment text describing the change.
        interpretation: The new AI interpretation of the amended proposal.
        amendment_number: Which amendment this is (1-indexed).
        max_amendments: Maximum amendments allowed per proposal.
        amend_tokens_remaining: AMEND tokens the governor will have after this.
        governor_name: Display name of the amending governor.
        interpretation_v2: Optional V2 interpretation for rich effects display.
    """
    embed = discord.Embed(
        title=f"Amendment {amendment_number} of {max_amendments}",
        color=COLOR_GOVERNANCE,
    )

    embed.add_field(
        name="Original Proposal",
        value=f'"{original_text[:500]}"',
        inline=False,
    )
    embed.add_field(
        name="Your Amendment",
        value=f'"{amendment_text[:500]}"',
        inline=False,
    )

    if interpretation_v2 and interpretation_v2.effects:
        for effect in interpretation_v2.effects:
            if effect.effect_type == "parameter_change" and effect.parameter:
                embed.add_field(
                    name="New Interpretation",
                    value=(
                        f"`{effect.parameter}`: "
                        f"{effect.old_value} -> {effect.new_value}"
                    ),
                    inline=False,
                )
            elif effect.effect_type == "hook_callback":
                hook_label = effect.hook_point or "custom hook"
                embed.add_field(
                    name=f"Hook: {hook_label}",
                    value=effect.description[:1024],
                    inline=False,
                )
            else:
                embed.add_field(
                    name=effect.effect_type.replace("_", " ").title(),
                    value=effect.description[:1024],
                    inline=False,
                )
    elif interpretation.parameter:
        embed.add_field(
            name="New Interpretation",
            value=(
                f"`{interpretation.parameter}`: "
                f"{interpretation.old_value} -> {interpretation.new_value}"
            ),
            inline=False,
        )

    if interpretation.impact_analysis:
        embed.add_field(
            name="Impact Analysis",
            value=interpretation.impact_analysis[:1024],
            inline=False,
        )

    embed.add_field(name="Cost", value="1 AMEND token", inline=True)
    embed.add_field(
        name="AMEND Remaining",
        value=str(amend_tokens_remaining),
        inline=True,
    )
    confidence_pct = f"{interpretation.confidence:.0%}"
    embed.add_field(name="Confidence", value=confidence_pct, inline=True)

    if governor_name:
        embed.set_author(name=governor_name)
    embed.set_footer(text="Pinwheel Fates -- Confirm or Cancel")
    return embed


def build_token_balance_embed(
    balance: TokenBalance,
    governor_name: str = "",
) -> discord.Embed:
    """Build an embed showing a governor's token balances."""
    embed = discord.Embed(
        title="Floor Tokens",
        color=COLOR_GOVERNANCE,
    )
    lines = (
        f"**PROPOSE:** {balance.propose}\n"
        f"**AMEND:** {balance.amend}\n"
        f"**BOOST:** {balance.boost}"
    )
    if balance.propose == 0 and balance.amend == 0 and balance.boost == 0:
        lines += "\n\n_You have no tokens. Tokens regenerate at the next governance interval._"
    embed.description = lines
    if governor_name:
        embed.set_author(name=governor_name)
    embed.set_footer(text="Pinwheel Fates")
    return embed


def build_trade_offer_embed(
    trade: Trade,
    from_name: str,
    to_name: str,
) -> discord.Embed:
    """Build an embed for a trade offer between governors."""
    embed = discord.Embed(
        title="Trade Offer",
        color=COLOR_GOVERNANCE,
    )
    embed.description = (
        f"**{from_name}** offers "
        f"**{trade.offered_amount} {trade.offered_type.upper()}** "
        f"token{'s' if trade.offered_amount > 1 else ''}\n"
        f"in exchange for "
        f"**{trade.requested_amount} "
        f"{trade.requested_type.upper()}** "
        f"token{'s' if trade.requested_amount > 1 else ''}\n"
        f"from **{to_name}**"
    )
    embed.set_footer(text="Pinwheel Fates")
    return embed


def build_strategy_embed(
    raw_text: str,
    team_name: str,
) -> discord.Embed:
    """Build an embed for a team strategy submission."""
    embed = discord.Embed(
        title=f"Strategy -- {team_name}",
        description=f'"{raw_text}"',
        color=COLOR_GOVERNANCE,
    )
    embed.set_footer(
        text="Pinwheel Fates -- Strategy active until changed",
    )
    return embed


def build_commentary_embed(
    game_data: dict[str, object],
    playoff_context: str | None = None,
) -> discord.Embed:
    """Build an embed showing AI commentary for a game.

    Args:
        game_data: Dict with keys: home_team (or home_team_name), away_team
            (or away_team_name), home_score, away_score, commentary.
        playoff_context: 'semifinal', 'finals', or None for regular season.
    """
    home = str(game_data.get("home_team", game_data.get("home_team_name", "Home")))
    away = str(game_data.get("away_team", game_data.get("away_team_name", "Away")))
    home_score = game_data.get("home_score", 0)
    away_score = game_data.get("away_score", 0)
    commentary = str(game_data.get("commentary", "No commentary available."))

    if playoff_context == "finals":
        title = f"CHAMPIONSHIP: {home} {home_score} - {away_score} {away}"
    elif playoff_context == "semifinal":
        title = f"SEMIFINAL: {home} {home_score} - {away_score} {away}"
    else:
        title = f"{home} {home_score} - {away_score} {away}"

    color = COLOR_GAME
    if playoff_context == "finals":
        color = COLOR_STANDINGS
    elif playoff_context == "semifinal":
        color = 0xE91E63

    embed = discord.Embed(
        title=title,
        description=commentary[:4096],
        color=color,
    )
    footer = "Pinwheel Fates -- AI Commentary"
    if playoff_context == "finals":
        footer = "Pinwheel Fates -- CHAMPIONSHIP FINALS"
    elif playoff_context == "semifinal":
        footer = "Pinwheel Fates -- SEMIFINAL PLAYOFFS"
    embed.set_footer(text=footer)
    return embed


def build_server_welcome_embed() -> discord.Embed:
    """Build a first-touch DM for someone who just joined the Discord server.

    Sent before they pick a team — explains what Pinwheel is and how to start.
    """
    embed = discord.Embed(
        title="Welcome to Pinwheel Fates!",
        description=(
            "Pinwheel starts as basketball, but becomes whatever you want.\n\n"
            "**First, choose a team** with `/join`. "
            "Don't worry -- you can switch between seasons.\n\n"
            "Once you're on a team, you're a **governor**. "
            "Use `/propose` to change the rules of the game, "
            "or `/amend` existing proposals on the Floor. "
            "Your team's hoopers play by whatever rules the governors create.\n\n"
            "**Quick start:**\n"
            "`/join` -- Pick a team and meet your hoopers\n"
            "`/propose` -- Submit a rule change\n"
            "`/vote` -- Vote on active proposals\n"
            "`/standings` -- See how the league looks"
        ),
        color=discord.Color.gold(),
    )
    embed.set_footer(text="Pinwheel Fates -- The rules are yours to write.")
    return embed


def build_welcome_embed(
    team_name: str,
    team_color: str,
    hoopers: list[dict[str, str]],
    motto: str = "",
    season_context: dict[str, object] | None = None,
) -> discord.Embed:
    """Build a welcome DM embed for a newly enrolled governor.

    Args:
        team_name: Name of the team joined.
        team_color: Hex color string (e.g. "#E74C3C").
        hoopers: List of dicts with 'name' and 'archetype' keys.
            Each dict may also have a 'backstory' key.
        motto: Optional team motto.
        season_context: Optional dict with season info to enrich the welcome.
            Keys: season_name (str), season_phase (str), current_round (int),
            total_rounds (int).
    """
    roster_lines: list[str] = []
    for h in hoopers:
        line = f"**{h['name']}** -- {h['archetype']}"
        backstory = h.get("backstory", "")
        if backstory:
            snippet = backstory[:100] + "..." if len(backstory) > 100 else backstory
            line += f"\n> {snippet}"
        roster_lines.append(line)
    roster = "\n".join(roster_lines)

    team_header = f"You're now a governor of **{team_name}**."
    if motto:
        team_header += f'\n*"{motto}"*'

    # Build the season status line when context is available
    season_line = ""
    if season_context:
        season_line = _format_season_line(season_context)

    # Build the description sections
    sections: list[str] = [team_header]

    if season_line:
        sections.append(season_line)

    sections.append(f"**Your hoopers:**\n{roster}")

    sections.append(
        "**Your starter tokens:**\n"
        "You received **2 PROPOSE**, **2 AMEND**, and **2 BOOST** tokens.\n"
        "PROPOSE tokens let you submit rule changes. "
        "AMEND tokens let you modify proposals on the Floor. "
        "BOOST tokens amplify your vote on proposals that matter to you."
    )

    sections.append(
        "**Commands you'll use:**\n"
        "`/propose` -- Submit a rule change to the Floor\n"
        "`/vote` -- Vote on active proposals\n"
        "`/strategy` -- Set your team's strategic direction\n"
        "`/tokens` -- Check your Floor token balance\n"
        "`/standings` -- See the league standings"
    )

    sections.append(
        "Read the full rules at **/play** on the web."
    )

    embed = discord.Embed(
        title=f"Welcome to {team_name}!",
        description="\n\n".join(sections),
        color=discord.Color(int(team_color.lstrip("#"), 16)),
    )
    embed.set_footer(text="Pinwheel Fates -- Lead wisely.")
    return embed


# Phase display labels for the welcome embed.
_PHASE_LABELS: dict[str, str] = {
    "setup": "Preseason",
    "active": "Regular season",
    "tiebreaker_check": "Tiebreaker seeding",
    "tiebreakers": "Tiebreakers",
    "playoffs": "Playoffs",
    "championship": "Championship",
    "offseason": "Offseason",
    "complete": "Season complete",
}


def _format_season_line(ctx: dict[str, object]) -> str:
    """Format a one-line season status string from season context.

    Example output: "Season THREE -- Regular season, Round 2 of 9"
    """
    season_name = str(ctx.get("season_name", ""))
    phase_raw = str(ctx.get("season_phase", "active"))
    current_round = int(ctx.get("current_round", 0))  # type: ignore[arg-type]
    total_rounds = int(ctx.get("total_rounds", 0))  # type: ignore[arg-type]

    phase_label = _PHASE_LABELS.get(phase_raw, phase_raw.replace("_", " ").capitalize())

    parts: list[str] = []
    if season_name:
        parts.append(f"**{season_name}**")

    if current_round > 0 and total_rounds > 0:
        parts.append(f"{phase_label}, Round {current_round} of {total_rounds}")
    elif current_round > 0:
        parts.append(f"{phase_label}, Round {current_round}")
    else:
        parts.append(phase_label)

    return " -- ".join(parts)


def build_roster_embed(
    governors: list[dict[str, object]],
    season_name: str = "this season",
) -> discord.Embed:
    """Build an embed showing all enrolled governors for the season.

    Args:
        governors: List of dicts with keys: username, team_name, propose,
            amend, boost, proposals_submitted, votes_cast.
        season_name: Display name of the current season.
    """
    embed = discord.Embed(
        title=f"Governor Roster -- {season_name}",
        color=COLOR_GOVERNANCE,
    )

    if not governors:
        embed.description = "No governors enrolled yet."
        embed.set_footer(text="Pinwheel Fates")
        return embed

    lines: list[str] = []
    for g in governors:
        username = g.get("username", "???")
        team = g.get("team_name", "???")
        propose = g.get("propose", 0)
        amend = g.get("amend", 0)
        boost = g.get("boost", 0)
        proposals = g.get("proposals_submitted", 0)
        votes = g.get("votes_cast", 0)
        lines.append(
            f"**{username}** ({team})\n"
            f"  Tokens: P:{propose} A:{amend} B:{boost} | "
            f"Proposals: {proposals} | Votes: {votes}"
        )

    # Discord embed description limit is 4096 chars
    description = "\n".join(lines)
    if len(description) > 4096:
        description = description[:4090] + "\n..."
    embed.description = description
    embed.set_footer(text="Pinwheel Fates")
    return embed


def build_bio_embed(
    hooper_name: str,
    backstory: str,
) -> discord.Embed:
    """Build an embed confirming a hooper bio was set.

    Args:
        hooper_name: Name of the hooper.
        backstory: The bio text that was set.
    """
    embed = discord.Embed(
        title=f"Bio Set -- {hooper_name}",
        description=backstory,
        color=COLOR_GOVERNANCE,
    )
    embed.set_footer(text="Pinwheel Fates")
    return embed


def build_team_list_embed(
    teams: list[dict[str, object]],
    season_name: str = "this season",
) -> discord.Embed:
    """Build an embed showing all teams and their governor counts.

    Args:
        teams: List of dicts with 'name', 'color', 'governor_count' keys.
        season_name: Display name of the current season.
    """
    embed = discord.Embed(
        title="Choose a Team",
        description=f"Use `/join <team name>` to enroll for {season_name}.",
        color=COLOR_GOVERNANCE,
    )
    min_count = min((t["governor_count"] for t in teams), default=0)
    lines: list[str] = []
    for t in teams:
        count = t["governor_count"]
        suffix = "governor" if count == 1 else "governors"
        marker = " -- needs players!" if count == min_count and count < 2 else ""
        lines.append(f"**{t['name']}** ({count} {suffix}){marker}")
    embed.add_field(name="Teams", value="\n".join(lines), inline=False)
    embed.set_footer(text="Pinwheel Fates")
    return embed


def build_hooper_trade_embed(
    from_team: str,
    to_team: str,
    offered_names: list[str],
    requested_names: list[str],
    proposer_name: str,
    votes_cast: int,
    votes_needed: int,
) -> discord.Embed:
    """Build an embed for a hooper trade proposal between two teams."""
    offered = ", ".join(offered_names) or "None"
    requested = ", ".join(requested_names) or "None"
    embed = discord.Embed(
        title="Hooper Trade Proposal",
        description=(
            f"Proposed by **{proposer_name}**\n\n"
            f"**{from_team}** sends: {offered}\n"
            f"**{to_team}** sends: {requested}\n\n"
            f"Votes: {votes_cast}/{votes_needed}"
        ),
        color=COLOR_GOVERNANCE,
    )
    embed.set_footer(text="Pinwheel Fates -- Both teams must approve")
    return embed


def build_team_game_result_embed(
    game_data: dict[str, object],
    team_id: str,
    playoff_context: str | None = None,
    game_context: GameContext | None = None,
) -> discord.Embed:
    """Build a team-specific game result embed (win/loss framing).

    Args:
        game_data: Dict with home_team (or home_team_name), away_team
            (or away_team_name), home_score, away_score, etc.
        team_id: The team to frame the result for.
        playoff_context: 'semifinal', 'finals', or None for regular season.
        game_context: Optional enrichment context with streaks, standings
            movement, margin significance, and rule-change context.
    """
    home = str(game_data.get("home_team", game_data.get("home_team_name", "Home")))
    away = str(game_data.get("away_team", game_data.get("away_team_name", "Away")))
    home_score = int(game_data.get("home_score", 0))
    away_score = int(game_data.get("away_score", 0))
    winner_id = str(game_data.get("winner_team_id", ""))
    home_id = str(game_data.get("home_team_id", ""))

    is_home = team_id == home_id
    your_team = home if is_home else away
    opponent = away if is_home else home
    your_score = home_score if is_home else away_score
    opp_score = away_score if is_home else home_score
    won = winner_id == team_id

    # Determine this team's context
    team_ctx = None
    if game_context:
        team_ctx = game_context.home if is_home else game_context.away

    # Streak-enriched title
    streak_suffix = ""
    if team_ctx and team_ctx.streak != 0:
        streak_suffix = f" ({_format_streak(team_ctx.streak)})"

    # Playoff-specific titles
    if won:
        if playoff_context == "finals":
            title = f"CHAMPIONS! {your_team} wins the title!{streak_suffix}"
            color = COLOR_STANDINGS  # gold
        elif playoff_context == "semifinal":
            title = f"ADVANCING! {your_team} wins the semifinal!{streak_suffix}"
            color = 0x2ECC71  # green
        else:
            title = f"Victory! {your_team} wins!{streak_suffix}"
            color = 0x2ECC71  # green
        description = f"**{your_team}** {your_score} - {opp_score} {opponent}"
    else:
        if playoff_context == "finals":
            title = f"So close. {your_team} falls in the championship.{streak_suffix}"
            color = 0xE74C3C  # red
        elif playoff_context == "semifinal":
            title = f"Eliminated. {your_team}'s season is over.{streak_suffix}"
            color = 0xE74C3C  # red
        else:
            title = f"Defeat. {your_team} falls.{streak_suffix}"
            color = 0xE74C3C  # red
        description = f"**{your_team}** {your_score} - {opp_score} {opponent}"

    # Standings movement for this team
    if team_ctx:
        movement_str = _format_standing_movement(
            team_ctx.standing_position, team_ctx.standing_movement,
        )
        if movement_str:
            description += f"\n{your_team} {movement_str}"

    # Margin significance
    if game_context and game_context.margin_label:
        description += f"\n*{game_context.margin_label}*"

    # New rules context
    if game_context and game_context.new_rules:
        rules_text = ", ".join(game_context.new_rules[:3])
        description += f"\nFirst game under new rules: {rules_text}"

    embed = discord.Embed(title=title, description=description, color=color)
    embed.set_footer(text="Pinwheel Fates")
    return embed


def build_governor_profile_embed(
    governor_name: str,
    team_name: str,
    activity: dict[str, object],
) -> discord.Embed:
    """Build an embed showing a governor's governance profile.

    Args:
        governor_name: Discord display name.
        team_name: Name of the governor's team.
        activity: Dict from get_governor_activity with proposals_submitted,
            proposals_passed, proposals_failed, votes_cast, token_balance.
    """
    proposals_submitted = activity.get("proposals_submitted", 0)
    proposals_passed = activity.get("proposals_passed", 0)
    proposals_failed = activity.get("proposals_failed", 0)
    votes_cast = activity.get("votes_cast", 0)
    balance = activity.get("token_balance")
    proposal_list = activity.get("proposal_list", [])

    embed = discord.Embed(
        title=f"Governor Profile: {governor_name}",
        color=COLOR_GOVERNANCE,
    )
    embed.add_field(name="Team", value=team_name, inline=True)
    proposal_summary = (
        f"{proposals_submitted} submitted / {proposals_passed} passed / {proposals_failed} failed"
    )
    embed.add_field(
        name="Proposals",
        value=proposal_summary,
        inline=False,
    )
    embed.add_field(name="Votes Cast", value=str(votes_cast), inline=True)

    if balance:
        embed.add_field(
            name="Token Balance",
            value=(f"PROPOSE: {balance.propose} | AMEND: {balance.amend} | BOOST: {balance.boost}"),
            inline=False,
        )

    # Show individual proposal details
    if proposal_list:
        for p in proposal_list[:5]:
            raw = str(p.get("raw_text", ""))
            text_preview = raw[:60] + ("..." if len(raw) > 60 else "")
            status = _STATUS_LABELS.get(str(p.get("status", "")), str(p.get("status", "")))
            param = p.get("parameter") or "none"
            embed.add_field(
                name=f'"{text_preview}"',
                value=f"**Status:** {status} | **Param:** {param} | **Tier:** {p.get('tier', '?')}",
                inline=False,
            )
        if len(proposal_list) > 5:
            embed.add_field(
                name="",
                value=f"_...and {len(proposal_list) - 5} more_",
                inline=False,
            )

    embed.set_author(name=governor_name)
    embed.set_footer(text="Pinwheel Fates")
    return embed


def build_round_summary_embed(
    round_data: dict[str, object],
    playoff_context: str | None = None,
) -> discord.Embed:
    """Build an embed summarizing a completed round.

    Args:
        round_data: Dict from round.completed event with round, games, reports, elapsed_ms.
        playoff_context: 'semifinal', 'finals', or None for regular season.
    """
    round_num = round_data.get("round", "?")
    games_count = round_data.get("games_presented", round_data.get("games", 0))
    reports_count = round_data.get("reports", 0)

    if playoff_context == "finals":
        title = f"CHAMPIONSHIP FINALS -- Round {round_num} Complete"
    elif playoff_context == "semifinal":
        title = f"SEMIFINAL PLAYOFFS -- Round {round_num} Complete"
    else:
        title = f"Round {round_num} Complete"

    parts = [f"**{games_count}** games played"]
    if reports_count:
        parts.append(f"**{reports_count}** reports generated")
    if round_data.get("playoffs_complete"):
        parts.append("**A champion has been crowned!**")

    embed = discord.Embed(
        title=title,
        description="\n".join(parts),
        color=COLOR_STANDINGS if playoff_context == "finals" else COLOR_GAME,
    )
    embed.set_footer(text="Pinwheel Fates")
    return embed


def build_admin_review_embed(
    proposal: Proposal,
    governor_name: str = "",
    interpretation_v2: ProposalInterpretation | None = None,
) -> discord.Embed:
    """Build an embed for admin review of a wild proposal.

    Shown to the admin when a Tier 5+ or low-confidence proposal is submitted.
    The proposal is already confirmed and open for voting — the admin can
    veto before tally or clear to acknowledge review.

    When ``interpretation_v2`` contains custom_mechanic effects, the title
    changes to highlight that code implementation is needed.

    Args:
        proposal: The Proposal model instance needing review.
        governor_name: Display name of the proposing governor.
        interpretation_v2: Optional V2 interpretation for rich effects display.
    """
    # Detect custom_mechanic effects
    has_custom = False
    custom_effects: list[object] = []
    if interpretation_v2 is not None:
        custom_effects = [
            e for e in interpretation_v2.effects if e.effect_type == "custom_mechanic"
        ]
        has_custom = bool(custom_effects)

    title = (
        "Custom Mechanic -- Implement or Veto"
        if has_custom
        else "Wild Proposal -- Veto or Clear"
    )

    embed = discord.Embed(
        title=title,
        description=(
            f"{proposal.raw_text[:2000]}\n\n"
            "This proposal is already open for voting. "
            "Use **Veto** to remove it before tally, or **Clear** to acknowledge."
        ),
        color=COLOR_WARNING,
    )

    param_value = "None -- uninterpretable"
    if proposal.interpretation and proposal.interpretation.parameter:
        param_value = f"`{proposal.interpretation.parameter}`"

    confidence_value = "N/A"
    if interpretation_v2 is not None:
        confidence_value = f"{interpretation_v2.confidence:.0%}"
    elif proposal.interpretation:
        confidence_value = f"{proposal.interpretation.confidence:.0%}"

    embed.add_field(name="Parameter", value=param_value, inline=True)
    embed.add_field(name="Confidence", value=confidence_value, inline=True)
    embed.add_field(name="Tier", value=str(proposal.tier), inline=True)
    if governor_name:
        embed.add_field(name="Governor", value=governor_name, inline=True)

    # Show custom mechanic details for admin
    for effect in custom_effects:
        mechanic_desc = effect.mechanic_description or effect.description
        value_parts = [mechanic_desc[:500]]
        if effect.mechanic_implementation_spec:
            value_parts.append(
                f"\n**Implementation:** {effect.mechanic_implementation_spec[:400]}"
            )
        embed.add_field(
            name="Mechanic Details",
            value="\n".join(value_parts)[:1024],
            inline=False,
        )

    embed.set_footer(text="Pinwheel Fates -- Veto or Clear")
    return embed


COLOR_MEMORIAL = 0xFFD700  # Gold — season memorial


def build_memorial_embed(
    season_name: str,
    champion_team_name: str,
    narrative_excerpt: str,
    total_games: int = 0,
    total_proposals: int = 0,
    total_rule_changes: int = 0,
    web_url: str = "",
) -> discord.Embed:
    """Build a gold-themed memorial embed for a completed season.

    Posted when a season memorial is generated. Provides a summary
    and links to the full web memorial.

    Args:
        season_name: Name of the archived season.
        champion_team_name: Name of the champion team.
        narrative_excerpt: First ~500 chars of the season narrative.
        total_games: Number of games played.
        total_proposals: Number of proposals submitted.
        total_rule_changes: Number of rule changes enacted.
        web_url: URL to the full memorial page.
    """
    embed = discord.Embed(
        title=f"Season Memorial -- {season_name}",
        description=narrative_excerpt[:2048] if narrative_excerpt else "A season to remember.",
        color=COLOR_MEMORIAL,
    )

    if champion_team_name:
        embed.add_field(name="Champion", value=champion_team_name, inline=True)
    if total_games:
        embed.add_field(name="Games", value=str(total_games), inline=True)
    if total_proposals:
        embed.add_field(name="Proposals", value=str(total_proposals), inline=True)
    if total_rule_changes:
        embed.add_field(name="Rule Changes", value=str(total_rule_changes), inline=True)
    if web_url:
        embed.add_field(
            name="Full Memorial",
            value=f"[View on web]({web_url})",
            inline=False,
        )

    embed.set_footer(text="Pinwheel Fates -- Hall of History")
    return embed


def build_history_list_embed(
    archives: list[dict[str, object]],
) -> discord.Embed:
    """Build an embed listing all archived seasons.

    Args:
        archives: List of archive dicts with season_name, champion_team_name,
            total_games keys.
    """
    embed = discord.Embed(
        title="Hall of History",
        color=COLOR_MEMORIAL,
    )

    if not archives:
        embed.description = "No seasons have been archived yet."
        embed.set_footer(text="Pinwheel Fates")
        return embed

    lines: list[str] = []
    for a in archives:
        name = a.get("season_name", "???")
        champ = a.get("champion_team_name")
        games = a.get("total_games", 0)
        champ_str = f" -- Champion: **{champ}**" if champ else ""
        lines.append(f"**{name}**{champ_str} ({games} games)")

    embed.description = "\n".join(lines)
    embed.set_footer(text="Pinwheel Fates -- Hall of History")
    return embed


def build_series_edit_embed(
    series_type: str,
    winner_name: str,
    loser_name: str,
    editor_name: str,
) -> discord.Embed:
    """Build a confirmation embed after a series report is edited.

    Args:
        series_type: 'semifinal' or 'finals'.
        winner_name: Name of the series winner.
        loser_name: Name of the series loser.
        editor_name: Discord display name of the editing governor.
    """
    label = "Championship Finals" if series_type == "finals" else "Semifinal"
    embed = discord.Embed(
        title=f"Series Report Updated -- {label}",
        description=(
            f"**{winner_name}** vs **{loser_name}**\n\n"
            f"Edited by {editor_name}. "
            "Governors on both teams can continue editing this report."
        ),
        color=COLOR_REPORT,
    )
    embed.set_footer(text="Pinwheel Fates -- Collaborative Series Report")
    return embed


COLOR_EFFECTS = 0x8E44AD  # Deep purple — effects browser


def build_effects_list_embed(
    effects: list[dict[str, object]],
    season_name: str = "this season",
) -> discord.Embed:
    """Build an embed listing all active effects for the current season.

    Args:
        effects: List of dicts with keys: effect_id, effect_type,
            description, lifetime, rounds_remaining, proposal_text.
        season_name: Display name of the current season.
    """
    embed = discord.Embed(
        title=f"Active Effects -- {season_name}",
        color=COLOR_EFFECTS,
    )

    if not effects:
        embed.description = "No active effects."
        embed.set_footer(text="Pinwheel Fates")
        return embed

    for i, effect in enumerate(effects, 1):
        eid = str(effect.get("effect_id", ""))
        short_id = eid[-8:] if len(eid) >= 8 else eid
        effect_type = str(effect.get("effect_type", "unknown"))
        desc = str(effect.get("description", "No description"))
        lifetime = str(effect.get("lifetime", "permanent"))
        rounds_remaining = effect.get("rounds_remaining")
        proposal_text = str(effect.get("proposal_text", ""))

        # Format lifetime display
        if rounds_remaining is not None and isinstance(rounds_remaining, int):
            lifetime_str = f"{rounds_remaining} rounds remaining"
        else:
            lifetime_str = lifetime.replace("_", " ").title()

        # Truncate description
        if len(desc) > 200:
            desc = desc[:197] + "..."

        value_parts = [
            f"**Type:** {effect_type.replace('_', ' ').title()}",
            f"**Duration:** {lifetime_str}",
        ]
        if proposal_text:
            preview = proposal_text[:80] + ("..." if len(proposal_text) > 80 else "")
            value_parts.append(f"**Source:** {preview}")
        value_parts.append(f"**ID:** `{short_id}`")

        embed.add_field(
            name=f"#{i}: {desc}",
            value="\n".join(value_parts),
            inline=False,
        )

    embed.set_footer(text="Use /repeal to propose removing an effect -- Pinwheel Fates")
    return embed


def build_repeal_confirm_embed(
    effect_description: str,
    effect_type: str,
    effect_id: str,
    token_cost: int,
    tokens_remaining: int,
    governor_name: str = "",
) -> discord.Embed:
    """Build an embed confirming a repeal proposal before submission.

    Args:
        effect_description: Description of the effect to repeal.
        effect_type: Type of the effect (meta_mutation, narrative, etc.).
        effect_id: Full UUID of the target effect.
        token_cost: PROPOSE tokens this repeal costs.
        tokens_remaining: Governor's PROPOSE tokens after cost.
        governor_name: Display name of the proposing governor.
    """
    short_id = effect_id[-8:] if len(effect_id) >= 8 else effect_id

    embed = discord.Embed(
        title="Repeal Proposal",
        description=(
            f'Propose repealing the **{effect_type.replace("_", " ")}** effect:\n\n'
            f'"{effect_description}"\n\n'
            "This will create a proposal on the Floor. "
            "Other governors will vote on whether to remove this effect."
        ),
        color=COLOR_EFFECTS,
    )
    embed.add_field(name="Effect ID", value=f"`{short_id}`", inline=True)
    embed.add_field(name="Cost", value=f"{token_cost} PROPOSE", inline=True)
    embed.add_field(name="Remaining", value=f"{tokens_remaining} PROPOSE", inline=True)

    if governor_name:
        embed.set_author(name=governor_name)
    embed.set_footer(text="Pinwheel Fates -- Confirm or Cancel")
    return embed


_STATUS_LABELS: dict[str, str] = {
    "pending": "Submitted",
    "pending_review": "Awaiting Admin Review",
    "confirmed": "On the Floor (voting open)",
    "flagged_for_review": "On the Floor (wild -- admin may veto)",
    "passed": "Passed",
    "failed": "Failed",
    "rejected": "Rejected by Admin",
    "vetoed": "Vetoed by Admin",
}


def build_proposals_embed(
    proposals: list[dict[str, object]],
    season_name: str,
    governor_names: dict[str, str] | None = None,
) -> discord.Embed:
    """Build an embed listing all proposals in a season with their status.

    Args:
        proposals: List of proposal dicts from get_all_proposals.
        season_name: Display name for the season.
        governor_names: Optional mapping of governor_id -> display name.
    """
    if not proposals:
        return discord.Embed(
            title=f"Proposals -- {season_name}",
            description="No proposals have been submitted this season.",
            color=COLOR_GOVERNANCE,
        )

    embed = discord.Embed(
        title=f"Proposals -- {season_name}",
        color=COLOR_GOVERNANCE,
    )

    for i, p in enumerate(proposals[:10]):
        raw = str(p.get("raw_text", ""))
        text_preview = raw[:80] + ("..." if len(raw) > 80 else "")
        status = _STATUS_LABELS.get(str(p.get("status", "")), str(p.get("status", "")))
        tier = p.get("tier", "?")
        param = p.get("parameter") or "none"
        gov_id = str(p.get("governor_id", ""))
        gov_name = (governor_names or {}).get(gov_id, "unknown")

        embed.add_field(
            name=f"#{i + 1}: {text_preview}",
            value=(
                f"**Status:** {status}\n"
                f"**Tier:** {tier} | **Parameter:** {param} | **By:** {gov_name}"
            ),
            inline=False,
        )

    if len(proposals) > 10:
        embed.set_footer(text=f"Showing 10 of {len(proposals)} proposals -- Pinwheel Fates")
    else:
        embed.set_footer(text="Pinwheel Fates")
    return embed


# Phase labels for the onboarding embed description.
_ONBOARDING_PHASE_DESCRIPTIONS: dict[str, str] = {
    "setup": "A new season is being set up. Sit tight.",
    "active": "Regular season is underway.",
    "tiebreaker_check": "Regular season is over. Tiebreaker seeding is being determined.",
    "tiebreakers": "Tiebreaker games are being played to determine playoff seeding.",
    "playoffs": "The playoffs are underway.",
    "championship": "A champion has been crowned!",
    "offseason": "The offseason governance window is open -- propose rules for next season.",
    "complete": "This season is complete.",
}


def build_onboarding_embed(
    context: LeagueContext,
    team_name: str | None = None,
) -> discord.Embed:
    """Build a State of the League embed for new player onboarding or /status.

    Formats the league context into a visually clean Discord embed with
    standings, active proposals, recent rule changes, and governor counts.

    Args:
        context: LeagueContext dataclass with all league state data.
        team_name: If provided, highlight this team in the standings.
            Typically the team the player just joined.

    Returns:
        A styled Discord embed ready to send.
    """
    phase_value = (
        context.season_phase.value
        if hasattr(context.season_phase, "value")
        else str(context.season_phase)
    )

    # Title line: "Season Name -- Phase, Round X of Y"
    title_parts: list[str] = ["State of the League"]
    subtitle_parts: list[str] = []

    if context.season_name:
        subtitle_parts.append(f"**{context.season_name}**")

    phase_desc = _ONBOARDING_PHASE_DESCRIPTIONS.get(phase_value, phase_value)

    if context.current_round > 0 and context.total_rounds > 0:
        subtitle_parts.append(f"Round {context.current_round} of {context.total_rounds}")

    description_lines: list[str] = []
    if subtitle_parts:
        description_lines.append(" -- ".join(subtitle_parts))
    description_lines.append(phase_desc)

    embed = discord.Embed(
        title=title_parts[0],
        description="\n".join(description_lines),
        color=COLOR_ONBOARDING,
    )

    # --- Standings field ---
    if context.standings:
        standings_lines: list[str] = []
        for i, team in enumerate(context.standings, 1):
            name = str(team.get("team_name", team.get("team_id", "???")))
            wins = team.get("wins", 0)
            losses = team.get("losses", 0)
            marker = ""
            if team_name and name.lower() == team_name.lower():
                marker = " <-- your team"
            standings_lines.append(f"**{i}.** {name} ({wins}W-{losses}L){marker}")
        embed.add_field(
            name="Standings",
            value="\n".join(standings_lines),
            inline=False,
        )
    else:
        embed.add_field(
            name="Standings",
            value="No games played yet.",
            inline=False,
        )

    # --- Active proposals field ---
    if context.active_proposals:
        proposal_lines: list[str] = []
        for p in context.active_proposals:
            raw = str(p.get("raw_text", ""))
            preview = raw[:80] + ("..." if len(raw) > 80 else "")
            tier = p.get("tier", "?")
            proposal_lines.append(f'"{preview}" -- Tier {tier}')
        if context.active_proposals_total > len(context.active_proposals):
            remaining = context.active_proposals_total - len(context.active_proposals)
            proposal_lines.append(f"...and {remaining} more. Use `/proposals` to see all.")
        proposal_lines.append("Use `/vote` to cast your vote.")

        embed.add_field(
            name=f"On the Floor ({context.active_proposals_total} active)",
            value="\n".join(proposal_lines),
            inline=False,
        )

    # --- Recent rule changes field ---
    if context.recent_rule_changes:
        change_lines: list[str] = []
        for rc in context.recent_rule_changes:
            param = rc.get("parameter", "unknown")
            old_val = rc.get("old_value", "?")
            new_val = rc.get("new_value", "?")
            rnd = rc.get("round_number")
            round_note = f" (Round {rnd})" if rnd else ""
            change_lines.append(f"`{param}`: {old_val} -> {new_val}{round_note}")
        embed.add_field(
            name="Recent Rule Changes",
            value="\n".join(change_lines),
            inline=False,
        )

    # --- Footer with governor count and governance interval ---
    team_count = len(context.team_governor_counts) if context.team_governor_counts else 0
    footer_parts: list[str] = []

    if context.governor_count > 0:
        gov_word = "governor" if context.governor_count == 1 else "governors"
        team_word = "team" if team_count == 1 else "teams"
        footer_parts.append(
            f"{context.governor_count} {gov_word} across {team_count} {team_word}"
        )

    if context.governance_interval == 1:
        footer_parts.append("Governance tallies every round")
    elif context.governance_interval > 1:
        footer_parts.append(
            f"Governance tallies every {context.governance_interval} rounds"
        )

    footer_text = ". ".join(footer_parts) + "." if footer_parts else "Pinwheel Fates"
    embed.set_footer(text=footer_text)

    return embed


# ---------------------------------------------------------------------------
# Bot Search
# ---------------------------------------------------------------------------

COLOR_SEARCH = 0x1ABC9C  # Teal — search / ask results


def build_search_result_embed(
    question: str,
    answer: str,
    query_type: str = "unknown",
) -> discord.Embed:
    """Build an embed for an /ask search result.

    Args:
        question: The original question asked by the user.
        answer: Formatted answer string (may contain Discord markdown).
        query_type: The resolved query type for the footer.
    """
    # Truncate answer to Discord embed description limit (4096 chars)
    if len(answer) > 4000:
        answer = answer[:3997] + "..."

    embed = discord.Embed(
        title=question[:256],
        description=answer,
        color=COLOR_SEARCH,
    )
    query_label = query_type.replace("_", " ").title()
    embed.set_footer(text=f"Pinwheel Fates -- {query_label}")
    return embed

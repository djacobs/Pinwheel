"""Rich Discord embed builders for Pinwheel Fates.

Builds discord.Embed objects for game results, standings, proposals,
reports, and schedules. Each builder takes domain data and returns
a styled embed ready to send.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from pinwheel.models.governance import Proposal, RuleInterpretation, VoteTally
    from pinwheel.models.report import Report
    from pinwheel.models.tokens import TokenBalance, Trade

# Brand colors
COLOR_GAME = 0xE74C3C  # Red — game results
COLOR_GOVERNANCE = 0x3498DB  # Blue — governance
COLOR_REPORT = 0x9B59B6  # Purple — AI reports
COLOR_SCHEDULE = 0x2ECC71  # Green — schedule
COLOR_STANDINGS = 0xF39C12  # Gold — standings
COLOR_WARNING = 0xE67E22  # Orange — admin review / warnings


def build_game_result_embed(game_data: dict[str, object]) -> discord.Embed:
    """Build an embed for a completed game result.

    Args:
        game_data: Dict with keys: home_team (or home_team_name), away_team
            (or away_team_name), home_score, away_score, winner_team_id,
            elam_activated, total_possessions.
    """
    home = str(game_data.get("home_team", game_data.get("home_team_name", "Home")))
    away = str(game_data.get("away_team", game_data.get("away_team_name", "Away")))
    home_score = game_data.get("home_score", 0)
    away_score = game_data.get("away_score", 0)
    elam_target = game_data.get("elam_target_score")

    title = f"{home} vs {away}"
    description = f"**{home}** {home_score} - {away_score} **{away}**"

    if elam_target:
        description += f"\nElam Target: {elam_target}"

    embed = discord.Embed(
        title=title,
        description=description,
        color=COLOR_GAME,
    )
    embed.add_field(
        name="Possessions",
        value=str(game_data.get("total_possessions", "N/A")),
        inline=True,
    )
    embed.set_footer(text="Pinwheel Fates")
    return embed


def build_standings_embed(standings: list[dict[str, object]]) -> discord.Embed:
    """Build an embed for current league standings.

    Args:
        standings: List of dicts with keys: team_name, team_id, wins,
            losses, points_for, points_against.
    """
    embed = discord.Embed(
        title="League Standings",
        color=COLOR_STANDINGS,
    )

    if not standings:
        embed.description = "No games played yet."
        return embed

    lines: list[str] = []
    for i, team in enumerate(standings, 1):
        name = team.get("team_name", team.get("team_id", "???"))
        wins = team.get("wins", 0)
        losses = team.get("losses", 0)
        lines.append(f"**{i}.** {name} ({wins}W-{losses}L)")

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
    }
    title = type_labels.get(report.report_type, f"Report: {report.report_type}")

    embed = discord.Embed(
        title=f"{title} -- Round {report.round_number}",
        description=report.content[:4096],
        color=COLOR_REPORT,
    )
    embed.set_footer(text="Pinwheel Fates")
    return embed


def build_schedule_embed(schedule: list[dict[str, object]], round_number: int) -> discord.Embed:
    """Build an embed for an upcoming round's schedule.

    Args:
        schedule: List of matchup dicts with home_team_name, away_team_name.
        round_number: The round number.
    """
    embed = discord.Embed(
        title=f"Schedule -- Round {round_number}",
        color=COLOR_SCHEDULE,
    )

    if not schedule:
        embed.description = "No games scheduled for this round."
        return embed

    lines: list[str] = []
    for matchup in schedule:
        home = matchup.get("home_team_name", matchup.get("home_team_id", "TBD"))
        away = matchup.get("away_team_name", matchup.get("away_team_id", "TBD"))
        lines.append(f"{home} vs {away}")

    embed.description = "\n".join(lines)
    embed.set_footer(text="Pinwheel Fates")
    return embed


def build_interpretation_embed(
    raw_text: str,
    interpretation: RuleInterpretation,
    tier: int,
    token_cost: int,
    tokens_remaining: int,
    governor_name: str = "",
) -> discord.Embed:
    """Build an embed showing AI interpretation of a proposal.

    Displayed ephemeral with confirm/revise/cancel buttons.
    """
    embed = discord.Embed(
        title="Proposal Interpretation",
        color=COLOR_GOVERNANCE,
    )

    embed.description = f'"{raw_text}"'

    if interpretation.parameter:
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


def build_commentary_embed(game_data: dict[str, object]) -> discord.Embed:
    """Build an embed showing AI commentary for a game.

    Args:
        game_data: Dict with keys: home_team (or home_team_name), away_team
            (or away_team_name), home_score, away_score, commentary.
    """
    home = str(game_data.get("home_team", game_data.get("home_team_name", "Home")))
    away = str(game_data.get("away_team", game_data.get("away_team_name", "Away")))
    home_score = game_data.get("home_score", 0)
    away_score = game_data.get("away_score", 0)
    commentary = str(game_data.get("commentary", "No commentary available."))

    title = f"{home} {home_score} - {away_score} {away}"

    embed = discord.Embed(
        title=title,
        description=commentary[:4096],
        color=COLOR_GAME,
    )
    embed.set_footer(text="Pinwheel Fates -- AI Commentary")
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
) -> discord.Embed:
    """Build a team-specific game result embed (win/loss framing).

    Args:
        game_data: Dict with home_team (or home_team_name), away_team
            (or away_team_name), home_score, away_score, etc.
        team_id: The team to frame the result for.
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

    if won:
        title = f"Victory! {your_team} wins!"
        color = 0x2ECC71  # green
        description = f"**{your_team}** {your_score} - {opp_score} {opponent}"
    else:
        title = f"Defeat. {your_team} falls."
        color = 0xE74C3C  # red
        description = f"**{your_team}** {your_score} - {opp_score} {opponent}"

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


def build_round_summary_embed(round_data: dict[str, object]) -> discord.Embed:
    """Build an embed summarizing a completed round.

    Args:
        round_data: Dict from round.completed event with round, games, reports, elapsed_ms.
    """
    round_num = round_data.get("round", "?")
    games_count = round_data.get("games_presented", round_data.get("games", 0))
    reports_count = round_data.get("reports", 0)

    parts = [f"**{games_count}** games played"]
    if reports_count:
        parts.append(f"**{reports_count}** reports generated")

    embed = discord.Embed(
        title=f"Round {round_num} Complete",
        description="\n".join(parts),
        color=COLOR_GAME,
    )
    embed.set_footer(text="Pinwheel Fates")
    return embed


def build_admin_review_embed(proposal: Proposal, governor_name: str = "") -> discord.Embed:
    """Build an embed for admin review of a wild proposal.

    Shown to the admin when a Tier 5+ or low-confidence proposal is submitted.
    The proposal is already confirmed and open for voting — the admin can
    veto before tally or clear to acknowledge review.

    Args:
        proposal: The Proposal model instance needing review.
        governor_name: Display name of the proposing governor.
    """
    embed = discord.Embed(
        title="Wild Proposal -- Veto or Clear",
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
    if proposal.interpretation:
        confidence_value = f"{proposal.interpretation.confidence:.0%}"

    embed.add_field(name="Parameter", value=param_value, inline=True)
    embed.add_field(name="Confidence", value=confidence_value, inline=True)
    embed.add_field(name="Tier", value=str(proposal.tier), inline=True)
    if governor_name:
        embed.add_field(name="Governor", value=governor_name, inline=True)
    embed.set_footer(text="Pinwheel Fates -- Veto or Clear")
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

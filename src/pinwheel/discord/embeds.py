"""Rich Discord embed builders for Pinwheel Fates.

Builds discord.Embed objects for game results, standings, proposals,
mirrors, and schedules. Each builder takes domain data and returns
a styled embed ready to send.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from pinwheel.models.governance import Proposal, VoteTally
    from pinwheel.models.mirror import Mirror

# Brand colors
COLOR_GAME = 0xE74C3C  # Red — game results
COLOR_GOVERNANCE = 0x3498DB  # Blue — governance
COLOR_MIRROR = 0x9B59B6  # Purple — AI mirrors
COLOR_SCHEDULE = 0x2ECC71  # Green — schedule
COLOR_STANDINGS = 0xF39C12  # Gold — standings


def build_game_result_embed(game_data: dict[str, object]) -> discord.Embed:
    """Build an embed for a completed game result.

    Args:
        game_data: Dict with keys: home_team, away_team, home_score,
            away_score, winner_team_id, elam_activated, total_possessions.
    """
    home = str(game_data.get("home_team", "Home"))
    away = str(game_data.get("away_team", "Away"))
    home_score = game_data.get("home_score", 0)
    away_score = game_data.get("away_score", 0)
    elam = game_data.get("elam_activated", False)

    title = f"{home} vs {away}"
    description = f"**{home}** {home_score} - {away_score} **{away}**"

    if elam:
        description += "\nElam Ending activated!"

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

    embed.add_field(
        name="Yes",
        value=f"{tally.weighted_yes:.2f}",
        inline=True,
    )
    embed.add_field(
        name="No",
        value=f"{tally.weighted_no:.2f}",
        inline=True,
    )
    embed.add_field(
        name="Threshold",
        value=f"{tally.threshold:.0%}",
        inline=True,
    )
    embed.set_footer(text="Pinwheel Fates")
    return embed


def build_mirror_embed(mirror: Mirror) -> discord.Embed:
    """Build an embed for an AI-generated mirror reflection.

    Args:
        mirror: The Mirror model instance.
    """
    type_labels = {
        "simulation": "Simulation Mirror",
        "governance": "Governance Mirror",
        "private": "Private Mirror",
        "series": "Series Mirror",
        "season": "Season Mirror",
        "state_of_the_league": "State of the League",
    }
    title = type_labels.get(mirror.mirror_type, f"Mirror: {mirror.mirror_type}")

    embed = discord.Embed(
        title=f"{title} -- Round {mirror.round_number}",
        description=mirror.content[:4096],
        color=COLOR_MIRROR,
    )
    embed.set_footer(text="Pinwheel Fates")
    return embed


def build_schedule_embed(
    schedule: list[dict[str, object]], round_number: int
) -> discord.Embed:
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


def build_round_summary_embed(round_data: dict[str, object]) -> discord.Embed:
    """Build an embed summarizing a completed round.

    Args:
        round_data: Dict from round.completed event with round, games, mirrors, elapsed_ms.
    """
    round_num = round_data.get("round", "?")
    games_count = round_data.get("games", 0)
    mirrors_count = round_data.get("mirrors", 0)
    elapsed = round_data.get("elapsed_ms", 0)

    embed = discord.Embed(
        title=f"Round {round_num} Complete",
        description=(
            f"**{games_count}** games simulated\n"
            f"**{mirrors_count}** mirrors generated\n"
            f"Elapsed: {elapsed}ms"
        ),
        color=COLOR_GAME,
    )
    embed.set_footer(text="Pinwheel Fates")
    return embed

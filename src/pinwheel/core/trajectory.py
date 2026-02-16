"""Performance trajectory analytics for team pages.

Pure functions that compute:
- Win rate timeline segmented by rule changes
- Performance deltas after governor-proposed rule changes
- Team-specific trends (streaks, home/away, etc.)
- Record comparison under different rule regimes
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RuleRegime:
    """A period of games played under a specific set of rules."""

    label: str
    start_round: int
    end_round: int | None  # None means "to present"
    wins: int = 0
    losses: int = 0
    point_diff: int = 0
    rule_description: str = ""
    parameter: str = ""
    old_value: str = ""
    new_value: str = ""

    @property
    def games_played(self) -> int:
        return self.wins + self.losses

    @property
    def win_pct(self) -> float:
        if self.games_played == 0:
            return 0.0
        return self.wins / self.games_played


@dataclass
class GovernorProposalImpact:
    """Performance delta after a specific governor's proposal was enacted."""

    governor_name: str
    proposal_text: str
    enacted_round: int
    before_wins: int = 0
    before_losses: int = 0
    after_wins: int = 0
    after_losses: int = 0
    parameter: str = ""

    @property
    def before_pct(self) -> float:
        total = self.before_wins + self.before_losses
        return self.before_wins / total if total > 0 else 0.0

    @property
    def after_pct(self) -> float:
        total = self.after_wins + self.after_losses
        return self.after_wins / total if total > 0 else 0.0

    @property
    def delta(self) -> float:
        """Change in win percentage after the rule change."""
        return self.after_pct - self.before_pct

    @property
    def impact_label(self) -> str:
        """Human-readable impact assessment."""
        after_total = self.after_wins + self.after_losses
        if after_total < 2:
            return "Too early to tell"
        d = self.delta
        if d > 0.15:
            return "Helped"
        elif d < -0.15:
            return "Hurt"
        return "Neutral"


@dataclass
class TeamTrend:
    """A notable trend for the team."""

    label: str
    detail: str
    trend_type: str  # "streak", "home_away", "recent", "opponent"


@dataclass
class PerformanceTrajectory:
    """Complete trajectory analysis for a team."""

    recent_form: str = ""
    longest_win_streak: int = 0
    longest_loss_streak: int = 0
    current_streak_type: str = ""
    current_streak_count: int = 0
    trend_desc: str = ""
    rule_regimes: list[RuleRegime] = field(default_factory=list)
    governor_impacts: list[GovernorProposalImpact] = field(default_factory=list)
    trends: list[TeamTrend] = field(default_factory=list)
    win_rate_timeline: list[dict[str, float | int | str]] = field(default_factory=list)


def compute_streaks(
    game_results: list[dict[str, object]],
) -> tuple[int, int, str, int]:
    """Compute longest win/loss streaks and current streak.

    Args:
        game_results: List of game result dicts with 'won' (bool) key.

    Returns:
        Tuple of (longest_win_streak, longest_loss_streak,
                  current_streak_type, current_streak_count).
    """
    longest_win = 0
    longest_loss = 0
    current = 0
    current_type = ""

    for g in game_results:
        won = bool(g["won"])
        if won:
            if current_type == "W":
                current += 1
            else:
                current = 1
                current_type = "W"
            longest_win = max(longest_win, current)
        else:
            if current_type == "L":
                current += 1
            else:
                current = 1
                current_type = "L"
            longest_loss = max(longest_loss, current)

    return longest_win, longest_loss, current_type, current


def compute_trend_description(
    game_results: list[dict[str, object]],
) -> str:
    """Compute first-half vs second-half trend description.

    Args:
        game_results: List of game result dicts with 'won' (bool) key.

    Returns:
        Human-readable trend description string.
    """
    total = len(game_results)
    if total < 4:
        return ""

    midpoint = total // 2
    first_half = game_results[:midpoint]
    second_half = game_results[midpoint:]

    first_wins = sum(1 for g in first_half if g["won"])
    first_losses = len(first_half) - first_wins
    second_wins = sum(1 for g in second_half if g["won"])
    second_losses = len(second_half) - second_wins

    first_total = len(first_half)
    second_total = len(second_half)

    if first_total == 0 or second_total == 0:
        return ""

    first_pct = first_wins / first_total
    second_pct = second_wins / second_total

    if second_pct > first_pct + 0.2:
        return (
            f"Strong finish \u2014 {first_wins}-{first_losses} early, "
            f"{second_wins}-{second_losses} recently"
        )
    elif first_pct > second_pct + 0.2:
        return (
            f"Started {first_wins}-{first_losses}, "
            f"cooled to {second_wins}-{second_losses}"
        )
    return (
        f"Consistent \u2014 {first_wins}-{first_losses} first half, "
        f"{second_wins}-{second_losses} second half"
    )


def compute_win_rate_timeline(
    game_results: list[dict[str, object]],
    rule_change_rounds: list[int],
) -> list[dict[str, float | int | str]]:
    """Compute a rolling win rate for each game, annotated with rule changes.

    Each entry has:
      - round_number (int)
      - cumulative_wins (int)
      - cumulative_losses (int)
      - win_rate (float, 0.0-1.0)
      - rule_change (str, non-empty if a rule changed at or before this round)

    Args:
        game_results: List of game result dicts with 'won' and 'round_number'.
        rule_change_rounds: Sorted list of round numbers where rules changed.

    Returns:
        List of timeline data points.
    """
    timeline: list[dict[str, float | int | str]] = []
    wins = 0
    losses = 0
    rc_set = set(rule_change_rounds)

    for g in game_results:
        if g["won"]:
            wins += 1
        else:
            losses += 1

        total = wins + losses
        rn = int(g["round_number"])  # type: ignore[arg-type]
        marker = "rule_change" if rn in rc_set else ""

        timeline.append({
            "round_number": rn,
            "cumulative_wins": wins,
            "cumulative_losses": losses,
            "win_rate": round(wins / total, 3) if total > 0 else 0.0,
            "marker": marker,
        })

    return timeline


def compute_rule_regimes(
    game_results: list[dict[str, object]],
    rule_events: list[dict[str, object]],
) -> list[RuleRegime]:
    """Segment the season into rule regimes and compute per-regime records.

    Args:
        game_results: List of game result dicts.
        rule_events: List of rule change event dicts with 'round_enacted',
            'parameter', 'old_value', 'new_value'.

    Returns:
        List of RuleRegime objects, one per era.
    """
    if not game_results:
        return []

    # Build regime boundaries from rule change rounds
    boundaries: list[tuple[int, str, str, str, str]] = []
    for re_event in rule_events:
        rn = int(re_event.get("round_enacted", 0))  # type: ignore[arg-type]
        param = str(re_event.get("parameter", ""))
        old_val = str(re_event.get("old_value", ""))
        new_val = str(re_event.get("new_value", ""))
        desc = f"{param}: {old_val} \u2192 {new_val}" if param else "Rule change"
        if rn > 0:
            boundaries.append((rn, desc, param, old_val, new_val))

    boundaries.sort(key=lambda x: x[0])

    if not boundaries:
        # Single regime — whole season
        wins = sum(1 for g in game_results if g["won"])
        losses = len(game_results) - wins
        diff = sum(int(g.get("margin", 0)) for g in game_results)  # type: ignore[arg-type]
        return [
            RuleRegime(
                label="Default rules",
                start_round=int(game_results[0]["round_number"]),  # type: ignore[arg-type]
                end_round=None,
                wins=wins,
                losses=losses,
                point_diff=diff,
            )
        ]

    regimes: list[RuleRegime] = []
    first_round = int(game_results[0]["round_number"])  # type: ignore[arg-type]

    # Before first change
    regime_start = first_round
    for i, (change_round, _desc, _param, _old_val, _new_val) in enumerate(boundaries):
        before_games = [
            g for g in game_results
            if int(g["round_number"]) >= regime_start  # type: ignore[arg-type]
            and int(g["round_number"]) < change_round  # type: ignore[arg-type]
        ]
        if before_games or i == 0:
            wins = sum(1 for g in before_games if g["won"])
            losses = len(before_games) - wins
            diff = sum(int(g.get("margin", 0)) for g in before_games)  # type: ignore[arg-type]
            label = "Default rules" if i == 0 else boundaries[i - 1][1]
            regimes.append(
                RuleRegime(
                    label=label if i == 0 else f"After: {label}",
                    start_round=regime_start,
                    end_round=change_round - 1,
                    wins=wins,
                    losses=losses,
                    point_diff=diff,
                    rule_description=label,
                )
            )
        regime_start = change_round

    # After last change
    after_games = [
        g for g in game_results
        if int(g["round_number"]) >= regime_start  # type: ignore[arg-type]
    ]
    if after_games:
        last_desc = boundaries[-1][1]
        last_param = boundaries[-1][2]
        last_old = boundaries[-1][3]
        last_new = boundaries[-1][4]
        wins = sum(1 for g in after_games if g["won"])
        losses = len(after_games) - wins
        diff = sum(int(g.get("margin", 0)) for g in after_games)  # type: ignore[arg-type]
        regimes.append(
            RuleRegime(
                label=f"After: {last_desc}",
                start_round=regime_start,
                end_round=None,
                wins=wins,
                losses=losses,
                point_diff=diff,
                rule_description=last_desc,
                parameter=last_param,
                old_value=last_old,
                new_value=last_new,
            )
        )

    return regimes


def compute_governor_impacts(
    game_results: list[dict[str, object]],
    team_passed_proposals: list[dict[str, object]],
) -> list[GovernorProposalImpact]:
    """Compute the before/after performance delta for each proposal passed by
    this team's governor.

    Args:
        game_results: List of game result dicts with 'won', 'round_number'.
        team_passed_proposals: List of dicts with 'governor_name',
            'raw_text', 'enacted_round', 'parameter'.

    Returns:
        List of GovernorProposalImpact objects.
    """
    impacts: list[GovernorProposalImpact] = []

    for proposal in team_passed_proposals:
        enacted_round = int(proposal["enacted_round"])  # type: ignore[arg-type]
        if enacted_round <= 0:
            continue

        before = [
            g for g in game_results
            if int(g["round_number"]) < enacted_round  # type: ignore[arg-type]
        ]
        after = [
            g for g in game_results
            if int(g["round_number"]) >= enacted_round  # type: ignore[arg-type]
        ]

        before_wins = sum(1 for g in before if g["won"])
        before_losses = len(before) - before_wins
        after_wins = sum(1 for g in after if g["won"])
        after_losses = len(after) - after_wins

        impacts.append(
            GovernorProposalImpact(
                governor_name=str(proposal.get("governor_name", "A governor")),
                proposal_text=str(proposal.get("raw_text", "")),
                enacted_round=enacted_round,
                before_wins=before_wins,
                before_losses=before_losses,
                after_wins=after_wins,
                after_losses=after_losses,
                parameter=str(proposal.get("parameter", "")),
            )
        )

    return impacts


def compute_team_trends(
    game_results: list[dict[str, object]],
    current_streak_type: str,
    current_streak_count: int,
    rule_change_rounds: list[int],
) -> list[TeamTrend]:
    """Compute team-specific trend narratives.

    Generates trends for:
    - Current streaks (3+ games)
    - Home vs away record
    - Record since last rule change
    - Recent form (last 5)

    Args:
        game_results: List of game result dicts.
        current_streak_type: 'W' or 'L'.
        current_streak_count: Length of current streak.
        rule_change_rounds: Sorted list of rule change rounds.

    Returns:
        List of TeamTrend objects.
    """
    trends: list[TeamTrend] = []

    # Current streak
    if current_streak_count >= 3:
        streak_word = "win" if current_streak_type == "W" else "loss"
        trends.append(
            TeamTrend(
                label=f"{current_streak_count}-game {streak_word} streak",
                detail=f"Currently on a {current_streak_count}-game {streak_word} streak",
                trend_type="streak",
            )
        )

    # Home vs away
    home_games = [g for g in game_results if g.get("is_home")]
    away_games = [g for g in game_results if not g.get("is_home")]

    if home_games and away_games:
        home_wins = sum(1 for g in home_games if g["won"])
        home_losses = len(home_games) - home_wins
        away_wins = sum(1 for g in away_games if g["won"])
        away_losses = len(away_games) - away_wins

        home_total = len(home_games)
        away_total = len(away_games)
        home_pct = home_wins / home_total if home_total > 0 else 0
        away_pct = away_wins / away_total if away_total > 0 else 0

        if abs(home_pct - away_pct) > 0.2 and home_total >= 2 and away_total >= 2:
            if home_pct > away_pct:
                trends.append(
                    TeamTrend(
                        label=f"{home_wins}-{home_losses} at home, "
                              f"{away_wins}-{away_losses} on the road",
                        detail="Stronger at home",
                        trend_type="home_away",
                    )
                )
            else:
                trends.append(
                    TeamTrend(
                        label=f"{away_wins}-{away_losses} on the road, "
                              f"{home_wins}-{home_losses} at home",
                        detail="Better on the road",
                        trend_type="home_away",
                    )
                )

    # Record since last rule change
    if rule_change_rounds:
        last_change = rule_change_rounds[-1]
        since_change = [
            g for g in game_results
            if int(g["round_number"]) >= last_change  # type: ignore[arg-type]
        ]
        if len(since_change) >= 2:
            wins = sum(1 for g in since_change if g["won"])
            losses = len(since_change) - wins
            trends.append(
                TeamTrend(
                    label=f"{wins}-{losses} since the last rule change (Round {last_change})",
                    detail=f"Record since Round {last_change}",
                    trend_type="recent",
                )
            )

    return trends


def build_performance_trajectory(
    game_results: list[dict[str, object]],
    rule_events: list[dict[str, object]],
    team_passed_proposals: list[dict[str, object]],
    rule_change_rounds: list[int],
) -> PerformanceTrajectory:
    """Build the full performance trajectory for a team.

    This is the main entry point that composes all the analytics.

    Args:
        game_results: From repo.get_team_game_results().
        rule_events: rule.enacted event payloads.
        team_passed_proposals: Proposals passed by this team's governors.
        rule_change_rounds: Sorted list of rounds where rules changed.

    Returns:
        Complete PerformanceTrajectory for template rendering.
    """
    if not game_results:
        return PerformanceTrajectory()

    # Recent form (last 5)
    recent = game_results[-5:]
    recent_form = "".join("W" if g["won"] else "L" for g in recent)

    # Streaks
    longest_win, longest_loss, streak_type, streak_count = compute_streaks(
        game_results
    )

    # Trend description
    trend_desc = compute_trend_description(game_results)

    # Win rate timeline
    timeline = compute_win_rate_timeline(game_results, rule_change_rounds)

    # Rule regimes
    regimes = compute_rule_regimes(game_results, rule_events)

    # Governor impacts
    gov_impacts = compute_governor_impacts(game_results, team_passed_proposals)

    # Team trends
    trends = compute_team_trends(
        game_results, streak_type, streak_count, rule_change_rounds
    )

    return PerformanceTrajectory(
        recent_form=recent_form,
        longest_win_streak=longest_win,
        longest_loss_streak=longest_loss,
        current_streak_type=streak_type,
        current_streak_count=streak_count,
        trend_desc=trend_desc,
        rule_regimes=regimes,
        governor_impacts=gov_impacts,
        trends=trends,
        win_rate_timeline=timeline,
    )

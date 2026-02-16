"""Narrative standings — enriched standings with strength of schedule, magic numbers,
trajectory tracking, and contextual callouts.

All functions are pure: they operate on lists of dicts, not database models.
The ``game_results`` input uses the same dict shape as ``compute_standings``:
``{home_team_id, away_team_id, home_score, away_score, winner_team_id}``.

Some helpers also need a ``round_number`` key on each result dict.
"""

from __future__ import annotations

from pinwheel.core.scheduler import compute_standings

# ---------------------------------------------------------------------------
# Strength of Schedule
# ---------------------------------------------------------------------------


def compute_strength_of_schedule(
    results: list[dict],
    standings: list[dict],
) -> dict[str, dict[str, int]]:
    """Compute each team's record against above-.500 opponents.

    A team is "above .500" if ``wins > losses`` in the current standings.
    Returns ``{team_id: {"wins": N, "losses": M}}`` for games against those
    opponents only.

    Args:
        results: Game result dicts (same format as ``compute_standings``).
        standings: Current standings list from ``compute_standings``.

    Returns:
        Dict mapping team_id to ``{"wins": int, "losses": int}``.
    """
    above_500: set[str] = set()
    for s in standings:
        if s["wins"] > s["losses"]:
            above_500.add(s["team_id"])

    sos: dict[str, dict[str, int]] = {}
    all_team_ids = {s["team_id"] for s in standings}
    for tid in all_team_ids:
        sos[tid] = {"wins": 0, "losses": 0}

    for r in results:
        home_id = r["home_team_id"]
        away_id = r["away_team_id"]
        winner_id = r["winner_team_id"]

        # Home vs above-.500 away
        if away_id in above_500:
            if winner_id == home_id:
                sos.setdefault(home_id, {"wins": 0, "losses": 0})["wins"] += 1
            else:
                sos.setdefault(home_id, {"wins": 0, "losses": 0})["losses"] += 1

        # Away vs above-.500 home
        if home_id in above_500:
            if winner_id == away_id:
                sos.setdefault(away_id, {"wins": 0, "losses": 0})["wins"] += 1
            else:
                sos.setdefault(away_id, {"wins": 0, "losses": 0})["losses"] += 1

    return sos


# ---------------------------------------------------------------------------
# Magic Numbers
# ---------------------------------------------------------------------------


def compute_magic_numbers(
    standings: list[dict],
    total_rounds: int,
    games_per_round: int,
    num_playoff_spots: int = 2,
) -> dict[str, int | None]:
    """Compute playoff clinch magic numbers.

    The magic number is the number of additional wins a team needs such that
    no team outside the playoff spots can catch them, regardless of remaining
    outcomes.

    For a team at position *i* (0-indexed), the magic number relative to the
    team at position ``num_playoff_spots`` (the first team out) is::

        magic = remaining_games_for_chaser + 1 - (my_wins - chaser_wins)

    A magic number of 0 or less means the team has clinched.
    ``None`` means the team is *outside* the playoff picture (or clinch is
    impossible with remaining games).

    Args:
        standings: Sorted standings list.
        total_rounds: Total scheduled rounds in the regular season.
        games_per_round: Average games per team per round (typically 1 for
            round-robin where every team plays each round).
        num_playoff_spots: How many teams make the playoffs (default 2).

    Returns:
        Dict mapping team_id to magic number (int) or None.
    """
    if len(standings) <= num_playoff_spots:
        # Everyone makes playoffs
        return {s["team_id"]: 0 for s in standings}

    magic: dict[str, int | None] = {}

    for i, team in enumerate(standings):
        games_played = team["wins"] + team["losses"]
        remaining = max(0, total_rounds * games_per_round - games_played)

        if i < num_playoff_spots:
            # This team is currently in a playoff spot.
            # Magic number = first-team-out's max possible wins + 1 - my wins
            chaser = standings[num_playoff_spots]
            chaser_played = chaser["wins"] + chaser["losses"]
            chaser_remaining = max(0, total_rounds * games_per_round - chaser_played)
            chaser_max_wins = chaser["wins"] + chaser_remaining
            magic_num = chaser_max_wins + 1 - team["wins"]
            if magic_num <= 0:
                magic[team["team_id"]] = 0  # clinched
            elif magic_num <= remaining:
                magic[team["team_id"]] = magic_num
            else:
                # Can still clinch if they win enough
                magic[team["team_id"]] = magic_num
        else:
            # Outside playoff spots — compute elimination number instead
            # (how many more losses until mathematically eliminated)
            holder = standings[num_playoff_spots - 1]
            holder_wins = holder["wins"]
            max_possible_wins = team["wins"] + remaining
            if max_possible_wins < holder_wins:
                magic[team["team_id"]] = None  # eliminated
            else:
                # Still alive — show how many wins needed to overtake
                needed = holder_wins - team["wins"] + 1
                if needed <= 0:
                    magic[team["team_id"]] = 0
                elif needed <= remaining:
                    magic[team["team_id"]] = needed
                else:
                    magic[team["team_id"]] = None  # can't catch up

    return magic


# ---------------------------------------------------------------------------
# Trajectory — standings movement over last N rounds
# ---------------------------------------------------------------------------


def compute_standings_trajectory(
    results_with_rounds: list[dict],
    current_round: int,
    lookback: int = 3,
) -> dict[str, int]:
    """Compute how many positions each team moved in the last *lookback* rounds.

    Positive = moved up (improved), negative = moved down.

    Args:
        results_with_rounds: Game result dicts with an extra ``round_number`` key.
        current_round: The current (latest played) round number.
        lookback: How many rounds to look back (default 3).

    Returns:
        Dict mapping team_id to position delta (positive = improved).
    """
    if current_round <= lookback:
        return {}

    cutoff_round = current_round - lookback

    # Standings at the cutoff point (games up to but not including cutoff+1)
    earlier_results = [r for r in results_with_rounds if r["round_number"] <= cutoff_round]
    current_results = results_with_rounds  # all results

    if not earlier_results:
        return {}

    old_standings = compute_standings(earlier_results)
    new_standings = compute_standings(current_results)

    old_pos = {s["team_id"]: idx for idx, s in enumerate(old_standings)}
    new_pos = {s["team_id"]: idx for idx, s in enumerate(new_standings)}

    trajectory: dict[str, int] = {}
    for tid in new_pos:
        if tid in old_pos:
            # Positive delta = moved up (old position was higher number = lower rank)
            trajectory[tid] = old_pos[tid] - new_pos[tid]
        else:
            trajectory[tid] = 0

    return trajectory


# ---------------------------------------------------------------------------
# Most Improved
# ---------------------------------------------------------------------------


def compute_most_improved(
    results_with_rounds: list[dict],
    current_round: int,
    window: int = 3,
) -> tuple[str | None, float, float]:
    """Find the team with the biggest win-rate improvement in the last *window* rounds.

    Returns ``(team_id, old_pct, new_pct)`` or ``(None, 0.0, 0.0)`` if no
    improvement can be computed.

    Args:
        results_with_rounds: Game result dicts with ``round_number`` key.
        current_round: Latest played round number.
        window: Number of recent rounds to compare against earlier play.

    Returns:
        Tuple of (team_id, old_win_pct, new_win_pct).
    """
    if current_round <= window:
        return None, 0.0, 0.0

    cutoff = current_round - window

    early = [r for r in results_with_rounds if r["round_number"] <= cutoff]
    recent = [r for r in results_with_rounds if r["round_number"] > cutoff]

    if not early or not recent:
        return None, 0.0, 0.0

    def _win_rates(results: list[dict]) -> dict[str, float]:
        wins: dict[str, int] = {}
        games: dict[str, int] = {}
        for r in results:
            for tid in (r["home_team_id"], r["away_team_id"]):
                games[tid] = games.get(tid, 0) + 1
                if r["winner_team_id"] == tid:
                    wins[tid] = wins.get(tid, 0) + 1
        return {
            tid: wins.get(tid, 0) / games[tid]
            for tid in games
            if games[tid] > 0
        }

    old_rates = _win_rates(early)
    new_rates = _win_rates(recent)

    best_team: str | None = None
    best_improvement = 0.0
    best_old = 0.0
    best_new = 0.0

    for tid in new_rates:
        if tid not in old_rates:
            continue
        improvement = new_rates[tid] - old_rates[tid]
        if improvement > best_improvement:
            best_improvement = improvement
            best_team = tid
            best_old = old_rates[tid]
            best_new = new_rates[tid]

    return best_team, best_old, best_new


# ---------------------------------------------------------------------------
# Enhanced Callouts
# ---------------------------------------------------------------------------


def compute_narrative_callouts(
    standings: list[dict],
    streaks: dict[str, int],
    current_round: int,
    total_rounds: int,
    sos: dict[str, dict[str, int]],
    magic_numbers: dict[str, int | None],
    trajectory: dict[str, int],
    most_improved_team: str | None,
    team_names: dict[str, str],
) -> list[str]:
    """Generate 2-6 narrative callouts for the standings page.

    Builds on the original ``_compute_standings_callouts`` with richer context:
    strength of schedule, magic numbers, trajectory, and most improved.

    Args:
        standings: Sorted standings list.
        streaks: Current streak per team (positive = wins, negative = losses).
        current_round: Latest played round.
        total_rounds: Total scheduled regular-season rounds.
        sos: Strength of schedule data from ``compute_strength_of_schedule``.
        magic_numbers: Magic numbers from ``compute_magic_numbers``.
        trajectory: Position deltas from ``compute_standings_trajectory``.
        most_improved_team: Team ID of the most improved team (or None).
        team_names: Dict mapping team_id to team_name.

    Returns:
        List of 2-6 short narrative strings.
    """
    if not standings:
        return []

    callouts: list[str] = []

    def _name(team_id: str) -> str:
        return team_names.get(team_id, "Unknown")

    # --- Tightest race ---
    if len(standings) >= 2:
        min_gap = float("inf")
        tight_pair: tuple[dict, dict, int] | None = None
        for i in range(len(standings) - 1):
            gap = standings[i]["wins"] - standings[i + 1]["wins"]
            if gap < min_gap:
                min_gap = gap
                tight_pair = (standings[i], standings[i + 1], i + 1)

        if tight_pair is not None and min_gap <= 1:
            team_a, team_b, seed = tight_pair
            remaining = total_rounds - current_round
            if min_gap == 0:
                suffix = _ordinal_suffix(seed)
                if remaining > 0:
                    callouts.append(
                        f"{_name(team_a['team_id'])} and {_name(team_b['team_id'])} "
                        f"tied for {seed}{suffix} place with {remaining} "
                        f"round{'s' if remaining != 1 else ''} left."
                    )
                else:
                    callouts.append(
                        f"{_name(team_a['team_id'])} and {_name(team_b['team_id'])} "
                        f"tied for {seed}{suffix} place."
                    )
            else:
                suffix = _ordinal_suffix(seed)
                if remaining > 0:
                    callouts.append(
                        f"{_name(team_a['team_id'])} and {_name(team_b['team_id'])} separated "
                        f"by 1 game with {remaining} round{'s' if remaining != 1 else ''} left."
                    )
                else:
                    callouts.append(
                        f"Only 1 game separates {_name(team_a['team_id'])} "
                        f"and {_name(team_b['team_id'])} for the {seed}{suffix} seed."
                    )

    # --- Dominant team ---
    if len(standings) >= 2:
        leader = standings[0]
        second = standings[1]
        lead = leader["wins"] - second["wins"]
        if lead >= 3:
            callouts.append(
                f"{_name(leader['team_id'])} has a commanding {int(lead)}-game lead."
            )

    # --- Strength of schedule insight ---
    if sos and len(standings) >= 2:
        leader = standings[0]
        leader_sos = sos.get(leader["team_id"], {"wins": 0, "losses": 0})
        total_vs_good = leader_sos["wins"] + leader_sos["losses"]
        if total_vs_good > 0 and leader_sos["wins"] == 0:
            callouts.append(
                f"{_name(leader['team_id'])} have the best record but "
                f"haven't beaten a team above .500."
            )
        elif total_vs_good >= 2 and leader_sos["wins"] >= total_vs_good:
            callouts.append(
                f"{_name(leader['team_id'])} are {leader_sos['wins']}-0 "
                f"against teams above .500."
            )

    # --- Magic number / clinch ---
    for team_id, mn in magic_numbers.items():
        if mn == 0:
            callouts.append(
                f"{_name(team_id)} have clinched a playoff berth."
            )
        elif mn is not None and mn <= 2 and mn > 0:
            callouts.append(
                f"{_name(team_id)} are {mn} win{'s' if mn != 1 else ''} "
                f"from clinching a playoff berth."
            )

    # --- Longest active streak ---
    if streaks:
        longest_tid = max(streaks, key=lambda tid: abs(streaks[tid]))
        streak_val = streaks[longest_tid]
        if abs(streak_val) >= 3:
            if streak_val > 0:
                callouts.append(
                    f"{_name(longest_tid)} riding a {streak_val}-game win streak."
                )
            else:
                callouts.append(
                    f"{_name(longest_tid)} on a {abs(streak_val)}-game losing streak."
                )

    # --- Trajectory / movers ---
    if trajectory:
        biggest_riser_tid = max(trajectory, key=lambda tid: trajectory[tid])
        biggest_rise = trajectory[biggest_riser_tid]
        if biggest_rise >= 2:
            callouts.append(
                f"{_name(biggest_riser_tid)} climbed {biggest_rise} spots in the last 3 rounds."
            )

        biggest_faller_tid = min(trajectory, key=lambda tid: trajectory[tid])
        biggest_fall = trajectory[biggest_faller_tid]
        if biggest_fall <= -2:
            callouts.append(
                f"{_name(biggest_faller_tid)} dropped "
                f"{abs(biggest_fall)} spots in the last 3 rounds."
            )

    # --- Most improved ---
    if most_improved_team and most_improved_team in team_names:
        callouts.append(
            f"{_name(most_improved_team)} are the most improved team over the last 3 rounds."
        )

    # --- Late season context ---
    if total_rounds > 0 and current_round / total_rounds > 0.7:
        remaining = total_rounds - current_round
        if remaining == 1:
            callouts.append("1 round remaining in the regular season.")
        elif remaining > 1:
            callouts.append(f"{remaining} rounds remaining in the regular season.")

    return callouts[:6]


def _ordinal_suffix(n: int) -> str:
    """Return ordinal suffix for a number (1st, 2nd, 3rd, 4th, etc.)."""
    if 11 <= n % 100 <= 13:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")

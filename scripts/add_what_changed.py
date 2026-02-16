#!/usr/bin/env python3
"""Add the 'What Changed' widget to the home page."""

import re
import sys

# Read the file
with open("src/pinwheel/api/pages.py", "r") as f:
    content = f.read()

# 1. Add the _compute_what_changed function after _compute_streaks_from_games
function_to_add = '''

def _compute_what_changed(
    standings: list[dict],
    prev_standings: list[dict],
    streaks: dict[str, int],
    prev_streaks: dict[str, int],
    rule_changes: list[dict],
    season_phase: str,
) -> list[str]:
    """Compute 1-3 "what changed" signals for the home page.

    Returns a list of short, punchy change signals in lede priority order:
    champion > elimination > standings shift > streak change > rule change.
    """
    signals: list[str] = []

    # Champion signal — overrides all else
    if season_phase in ("championship", "offseason", "completed"):
        if standings:
            champion = standings[0]["team_name"]
            signals.append(f"{champion} are your champions.")
        return signals[:1]

    # Standings movement — compare current to previous
    if standings and prev_standings:
        # Build position maps
        prev_positions = {s["team_id"]: idx for idx, s in enumerate(prev_standings)}
        curr_positions = {s["team_id"]: idx for idx, s in enumerate(standings)}

        # Find biggest climber and biggest faller
        biggest_climb = 0
        biggest_fall = 0
        climber_name = ""
        faller_name = ""

        for team_id in curr_positions:
            if team_id not in prev_positions:
                continue
            delta = prev_positions[team_id] - curr_positions[team_id]
            if delta > biggest_climb:
                biggest_climb = delta
                climber_name = next(s["team_name"] for s in standings if s["team_id"] == team_id)
            if delta < biggest_fall:
                biggest_fall = delta
                faller_name = next(s["team_name"] for s in standings if s["team_id"] == team_id)

        if biggest_climb >= 2:
            new_pos = curr_positions[next(s["team_id"] for s in standings if s["team_name"] == climber_name)]
            signals.append(f"{climber_name} climbed to {new_pos + 1}{_ordinal_suffix(new_pos + 1)} place.")
        if biggest_fall <= -2:
            new_pos = curr_positions[next(s["team_id"] for s in standings if s["team_name"] == faller_name)]
            signals.append(f"{faller_name} dropped to {new_pos + 1}{_ordinal_suffix(new_pos + 1)} place.")

    # Streak changes — new 3+ streaks or broken 3+ streaks
    for team_id, streak in streaks.items():
        prev_streak = prev_streaks.get(team_id, 0)
        team_name = next((s["team_name"] for s in standings if s["team_id"] == team_id), "Unknown")

        # New streak (crossed threshold)
        if abs(streak) >= 3 and abs(prev_streak) < 3:
            if streak > 0:
                signals.append(f"{team_name} on a {streak}-game win streak.")
            else:
                signals.append(f"{team_name} on a {abs(streak)}-game losing streak.")
        # Broken streak
        elif abs(prev_streak) >= 3 and abs(streak) < 3:
            if prev_streak > 0:
                signals.append(f"{team_name} snapped their {prev_streak}-game win streak.")
            else:
                signals.append(f"{team_name} snapped their {abs(prev_streak)}-game losing streak.")

    # Rule changes
    for rc in rule_changes:
        param = rc.get("parameter", "a rule")
        new_val = rc.get("new_value")
        signals.append(f"{param.replace('_', ' ').title()} changed to {new_val}.")

    return signals[:3]
'''

# Find the end of _compute_streaks_from_games and insert
content = re.sub(
    r'(    return streaks\n)\n\n(@router\.get\("/", response_class=HTMLResponse\))',
    r'\1' + function_to_add + r'\n\n\2',
    content
)

# 2. Add what_changed_signals to the initial variables
content = re.sub(
    r'(    team_colors: dict\[str, str\] = \{\})\n\n(    if season_id:)',
    r'\1\n    what_changed_signals: list[str] = []\n\n\2',
    content
)

# 3. Add the computation logic before the Pinwheel Post section
logic_to_add = '''
        # Compute "What Changed" signals
        if current_round > 0:
            # Previous round's standings (exclude latest round)
            prev_standings: list[dict] = []
            if current_round > 1:
                prev_results: list[dict] = []
                for rn in range(1, current_round):
                    rg = await repo.get_games_for_round(season_id, rn)
                    for g in rg:
                        prev_results.append({
                            "home_team_id": g.home_team_id,
                            "away_team_id": g.away_team_id,
                            "home_score": g.home_score,
                            "away_score": g.away_score,
                            "winner_team_id": g.winner_team_id,
                        })
                prev_standings = compute_standings(prev_results)
                for s in prev_standings:
                    team = await repo.get_team(s["team_id"])
                    if team:
                        s["team_name"] = team.name

            # Previous streaks
            prev_streaks: dict[str, int] = {}
            if current_round > 1:
                prev_games = [g for g in all_games if g.round_number < current_round]
                if prev_games:
                    prev_streaks = _compute_streaks_from_games(prev_games)

            # Rule changes in latest round
            rule_change_events = await repo.get_events_by_type(
                season_id=season_id,
                event_types=["rule.enacted"],
            )
            latest_rule_changes = [
                e.payload for e in rule_change_events
                if e.round_number == current_round
            ]

            # Compute signals
            what_changed_signals = _compute_what_changed(
                standings=standings,
                prev_standings=prev_standings,
                streaks=streaks,
                prev_streaks=prev_streaks,
                rule_changes=latest_rule_changes,
                season_phase=season_phase,
            )

'''

content = re.sub(
    r'(            streaks = _compute_streaks_from_games\(all_games\))\n\n(    # --- Pinwheel Post data)',
    r'\1\n' + logic_to_add + r'\2',
    content
)

# 4. Add to context dict
content = re.sub(
    r'(        "post_hot_players": post_hot_players,)\n(    \})',
    r'\1\n        "what_changed_signals": what_changed_signals,\n\2',
    content
)

# Write back
with open("src/pinwheel/api/pages.py", "w") as f:
    f.write(content)

print("✓ Modified src/pinwheel/api/pages.py")

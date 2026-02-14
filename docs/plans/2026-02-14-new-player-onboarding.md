# Plan: New Player Onboarding Context

## Problem

When a new player joins via `/join`, they land in a league that's already in motion. The current welcome flow gives them their team name, roster, and a list of commands -- but nothing about what's actually happening right now. They don't know:

- What season is it? What phase is the season in?
- What are the standings? Who's winning?
- What rules have already been changed?
- Are there active proposals they can vote on right now?
- How many games have been played? How many are left?
- Who else is governing?

The welcome DM (`build_welcome_embed`) is a static orientation card. It works for "what is this game?" but fails at "what's happening right now?" A player who joins mid-season is dropped into a political and competitive landscape with zero context.

## Current Join Flow

1. Player runs `/join` or `/join <team>`.
2. If no team specified, `build_team_list_embed` shows available teams with governor counts.
3. Player picks a team. `_handle_join` in `bot.py` (line 826):
   - Creates/finds player record via `repo.get_or_create_player`
   - Enrolls on team via `repo.enroll_player`
   - Grants initial tokens via `regenerate_tokens`
   - Assigns Discord role
   - Sends `build_welcome_embed` to channel AND as DM
4. That's it. No context about the league state.

## What a New Player Needs

### Immediate context (shown right after joining)

A "State of the League" briefing that answers:

1. **Season identity** -- "You're joining Season 2: Summer Classic, Round 5 of 9."
2. **Season phase** -- "Regular season" / "Playoffs (Semifinals)" / "Championship" / "Offseason governance window"
3. **Standings snapshot** -- Current W-L for all teams, with their team highlighted.
4. **Active proposals** -- "There are 2 proposals on the Floor right now. Use `/vote` to weigh in." with a one-line summary of each.
5. **Recent rule changes** -- "The league has changed 3 rules this season." with the most recent 1-2 changes shown.
6. **Governor count** -- "You're joining 7 other governors across 4 teams."
7. **Next tally** -- "The next governance tally happens after Round 6" (or "Governance tallies every round").

### Phase-specific context

The briefing should adapt based on `SeasonPhase`:

| Phase | Key context to surface |
|-------|----------------------|
| `ACTIVE` | Standings, current round / total rounds, active proposals, recent rule changes |
| `TIEBREAKER_CHECK` / `TIEBREAKERS` | "Regular season is over. Tiebreaker games are being played to determine playoff seeding." |
| `PLAYOFFS` | Playoff bracket status (who's playing whom, series scores), active proposals |
| `CHAMPIONSHIP` | Who won, awards, championship window countdown |
| `OFFSEASON` | "The season is winding down. This is the offseason governance window -- propose rules for next season." Active proposals. |
| `COMPLETE` | "This season is over. A new season hasn't started yet." Link to season memorial if it exists. |
| `SETUP` | "A new season is being set up. Sit tight." |

## Where the Data Comes From

All the data needed already exists in the codebase. No new database queries required.

| Data needed | Source |
|-------------|--------|
| Season name + status | `repo.get_active_season()` -> `SeasonRow.name`, `SeasonRow.status` |
| Season phase (normalized) | `season.normalize_phase(season.status)` from `core/season.py` |
| Current round number | `max(g.round_number for g in games)` via `repo.get_all_games(season_id)` -- or count schedule entries with results |
| Total scheduled rounds | `repo.get_full_schedule(season_id, phase="regular")` -> max round_number |
| Standings | `compute_standings(game_dicts)` from `core/scheduler.py` -- same pattern as `_query_standings()` in bot.py |
| Team names on standings | `repo.get_team(team_id)` for each entry |
| Active proposals | `repo.get_all_proposals(season_id)` filtered to `status in ("confirmed", "amended")` |
| Recent rule changes | `repo.get_events_by_type(season_id, ["rule.enacted"])` -- take last 2 |
| Governor count | `repo.get_all_governors_for_season(season_id)` or `repo.get_governor_counts_by_team(season_id)` |
| Governance interval | `settings.governance_interval` (from `PINWHEEL_GOVERNANCE_INTERVAL` env var) |
| Championship config | `season.config` dict (has `champion_team_id`, `awards`, `championship_ends_at`) |
| Offseason config | `season.config` dict (has `offseason_ends_at`) |

## Design

### 1. New function: `build_league_context()`

A new async function in a new module `src/pinwheel/core/onboarding.py` that gathers all the league state into a structured dict. This keeps the data-gathering logic testable and reusable (by both `/join` and a future `/status` command).

```python
@dataclass
class LeagueContext:
    season_name: str
    season_phase: SeasonPhase
    current_round: int
    total_rounds: int
    standings: list[dict]           # [{team_name, wins, losses, point_diff}]
    active_proposals: list[dict]    # [{raw_text, governor_name, tier}]
    recent_rule_changes: list[dict] # [{parameter, old_value, new_value}]
    governor_count: int
    team_governor_counts: dict[str, int]  # {team_name: count}
    governance_interval: int
    games_played: int
    # Phase-specific
    championship_config: dict | None  # champion name, awards, ends_at
    offseason_config: dict | None     # ends_at
    playoff_matchups: list[dict] | None  # series data if in playoffs
```

```python
async def build_league_context(
    repo: Repository,
    season: SeasonRow,
    governance_interval: int = 1,
) -> LeagueContext:
    ...
```

### 2. New embed: `build_onboarding_embed()`

A new embed builder in `src/pinwheel/discord/embeds.py` that takes a `LeagueContext` and the player's team name, and produces a rich Discord embed. This is the "State of the League" card.

The embed structure:

```
STATE OF THE LEAGUE
Season 2: Summer Classic -- Round 5 of 9

STANDINGS
1. Rose City Thorns (4W-2L) <-- your team
2. Bridge City Bolts (3W-3L)
3. Stumptown Stars (3W-3L)
4. PDX Voltage (2W-4L)

ON THE FLOOR (2 active proposals)
"Make three-pointers worth 5 points" -- Tier 1, voting open
"Require 3 passes before shooting" -- Tier 2, voting open
Use /vote to cast your vote.

RECENT RULE CHANGES
- shot_clock_seconds: 24 -> 18 (Round 3)

8 governors across 4 teams. Governance tallies every round.
```

Color: Use a new `COLOR_ONBOARDING` constant (or reuse `COLOR_STANDINGS` gold).

### 3. Integrate into `/join` flow

In `_handle_join()` (bot.py, around line 992), after sending the welcome embed:

```python
# Send welcome DM
with contextlib.suppress(discord.Forbidden, discord.HTTPException):
    await interaction.user.send(embed=embed)

# Build and send league context briefing as a second DM
league_context = await build_league_context(repo, season, self.settings.governance_interval)
onboarding_embed = build_onboarding_embed(league_context, target_team.name)
with contextlib.suppress(discord.Forbidden, discord.HTTPException):
    await interaction.user.send(embed=onboarding_embed)
```

The onboarding embed is sent as a **DM only** (not in the channel). The channel gets the welcome embed. The DM gets both welcome + league context. This keeps the channel clean while giving the new player private context.

**Important:** The DB session is already closed by the time we send embeds in the current flow. We need to restructure slightly -- gather the league context data while the session is still open, then build the embed after the session closes. This matches the existing pattern where `target_team.hoopers` is read before the session closes.

### 4. New `/status` command for returning players

Register a new slash command:

```python
@self.tree.command(name="status", description="Get a briefing on the current state of the league")
async def status_command(interaction: discord.Interaction) -> None:
    await self._handle_status(interaction)
```

`_handle_status` opens a DB session, calls `build_league_context()`, builds the onboarding embed (without the "your team" highlight if the player isn't enrolled), and sends it as an ephemeral response.

This command is useful for:
- Returning players who haven't checked in for a few rounds
- Players curious about the league state without scrolling through Discord history
- Anyone who wants a quick snapshot before proposing or voting

No enrollment required to use `/status`. It works for anyone in the Discord server.

### 5. Link to the New Governor Guide

The welcome embed already has a command quick-start list. The onboarding embed should end with a footer or link to the full guide:

```
Read the full guide: /docs or pinwheel.example.com/guide
```

The `NEW_GOVERNOR_GUIDE.md` content is comprehensive and well-written. It doesn't need to be duplicated in the embed. The onboarding embed is the "what's happening now" complement to the guide's "how things work" explanation.

## Implementation Steps

### Step 1: Create `src/pinwheel/core/onboarding.py`

- Define `LeagueContext` dataclass
- Implement `build_league_context()` async function
- Uses existing repo queries -- no new DB methods needed
- Pure data gathering, no Discord dependencies

### Step 2: Add `build_onboarding_embed()` to `src/pinwheel/discord/embeds.py`

- Takes `LeagueContext` and optional `team_name` for highlighting
- Produces a Discord embed with phase-aware content
- Handles all `SeasonPhase` variants
- Follows existing embed patterns (colors, footer, field layout)

### Step 3: Integrate into `_handle_join()` in `src/pinwheel/discord/bot.py`

- Gather league context data while DB session is open
- Send onboarding embed as DM after welcome embed
- Non-fatal: suppress Discord errors on DM send (same pattern as welcome DM)

### Step 4: Add `/status` command to `src/pinwheel/discord/bot.py`

- Register in `_setup_commands()`
- Implement `_handle_status()` -- opens session, builds context, sends ephemeral embed
- No enrollment required
- Update the CLAUDE.md Discord Commands table

### Step 5: Write tests

### Step 6: Update documentation

- Add `/status` to the command table in `NEW_GOVERNOR_GUIDE.md` and `RUN_OF_PLAY.md`
- Update `CLAUDE.md` Discord Commands table

## Files Modified

| File | Change |
|------|--------|
| `src/pinwheel/core/onboarding.py` | **NEW** -- `LeagueContext` dataclass + `build_league_context()` function |
| `src/pinwheel/discord/embeds.py` | Add `build_onboarding_embed()` function |
| `src/pinwheel/discord/bot.py` | Import + call in `_handle_join()`, add `/status` command + `_handle_status()` |
| `tests/test_onboarding.py` | **NEW** -- Tests for `build_league_context()` and `build_onboarding_embed()` |
| `tests/test_discord.py` | Test `/status` command integration, test onboarding DM sent on `/join` |
| `docs/product/NEW_GOVERNOR_GUIDE.md` | Add `/status` command to command table |
| `docs/product/RUN_OF_PLAY.md` | Add `/status` command to Discord Commands table |
| `CLAUDE.md` | Add `/status` to Discord Commands table |

## Tests to Write

### `tests/test_onboarding.py`

1. **`test_build_league_context_active_season`** -- Active season with games played, proposals, rule changes. Verify all fields populated correctly.
2. **`test_build_league_context_no_games_yet`** -- Season exists but no games played. Standings empty, current_round = 0.
3. **`test_build_league_context_with_active_proposals`** -- Proposals in confirmed/amended status appear. Passed/failed/cancelled proposals excluded.
4. **`test_build_league_context_playoffs`** -- Season in PLAYOFFS phase. Verify playoff-specific fields populated.
5. **`test_build_league_context_championship`** -- Season in CHAMPIONSHIP phase. Verify champion info surfaced.
6. **`test_build_league_context_offseason`** -- Season in OFFSEASON phase. Verify offseason window info.
7. **`test_build_league_context_complete`** -- Season in COMPLETE phase. Graceful handling.
8. **`test_build_onboarding_embed_highlights_team`** -- Verify the player's team is visually distinguished in standings.
9. **`test_build_onboarding_embed_no_proposals`** -- Embed handles zero active proposals gracefully.
10. **`test_build_onboarding_embed_phase_specific_text`** -- Each phase produces appropriate descriptive text.

### `tests/test_discord.py` (additions)

11. **`test_join_sends_onboarding_dm`** -- After successful `/join`, both welcome embed and onboarding embed are sent via DM.
12. **`test_status_command_no_enrollment`** -- `/status` works even for non-enrolled users.
13. **`test_status_command_enrolled`** -- `/status` highlights the user's team when they're enrolled.

## Edge Cases

- **No active season**: `/status` should say "No active season. Check back soon." Not an error.
- **Season exists but no games played**: Show standings as "No games played yet." Show round as "Round 0 of N."
- **Player joins between seasons**: If the only season is COMPLETE, show the completed season's final state and say "A new season hasn't started yet."
- **DM disabled**: Player has DMs turned off. The channel message (welcome embed) still works. The onboarding context is lost. Consider adding a note: "Enable DMs from server members to receive your private league briefing."
- **Large number of proposals**: Cap at 5 in the embed. Add "...and N more. Use `/proposals` to see all."
- **Long team names**: Truncate standings lines if they exceed Discord field limits.

## Open Questions

1. **Should `/status` also be available as `/catchup`?** -- "catchup" is more evocative and implies returning after absence. Could register both as aliases. Recommendation: start with `/status` (simpler, clearer), add `/catchup` alias later if players request it.

2. **Should the onboarding embed include the latest AI report excerpt?** -- The simulation and governance reports are the richest source of "what's happening." Including a 1-2 sentence excerpt from the latest report would add flavor. Risk: the report might be stale (from 3 rounds ago) or confusing without context. Recommendation: include it if a report exists for the current or previous round, skip it otherwise.

3. **Should `/status` be ephemeral or public?** -- Recommendation: ephemeral. It's a personal briefing, not a public announcement. Players who want to share can screenshot.

## Not in Scope

- AI-generated personalized onboarding narrative (future enhancement -- could use the reporter to write a "here's what you missed" summary)
- Web dashboard equivalent of the onboarding context (the web already has standings and governance pages)
- Push notifications for returning players who haven't been active
- Tutorial or guided walkthrough (the NEW_GOVERNOR_GUIDE.md serves this role)

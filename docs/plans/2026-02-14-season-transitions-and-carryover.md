# Plan: Season Transitions and Carryover

**Date:** 2026-02-14
**Status:** Draft (documents current behavior + identifies gaps)

## Season Lifecycle

**File:** `src/pinwheel/core/season.py`

The season progresses through these phases (defined in `SeasonPhase` enum):

```
SETUP -> ACTIVE -> TIEBREAKER_CHECK -> TIEBREAKERS -> PLAYOFFS
      -> CHAMPIONSHIP -> OFFSEASON -> COMPLETE
```

Allowed transitions are enforced by `ALLOWED_TRANSITIONS` dict and `transition_season()`. Invalid transitions raise `ValueError`. Legacy status strings (`"completed"`, `"archived"`, `"regular_season_complete"`) are mapped to their modern equivalents via `_LEGACY_STATUS_MAP`.

## What Happens at `/new-season`

**Entry point:** `src/pinwheel/discord/bot.py` -- `_handle_new_season()`

The `/new-season` command is admin-only (requires Discord server administrator permission). It accepts:
- `name`: Display name for the new season (required)
- `carry_rules`: Boolean, default `True` -- whether to carry forward the previous season's ruleset

### Flow

1. **Admin check:** Verifies `interaction.user.guild_permissions.administrator`.
2. **Find league:** Looks up the latest season's `league_id` from the database.
3. **Calls `start_new_season()`** in `season.py` with the league ID, name, and carry-forward preference.

### `start_new_season()` -- Full Breakdown

**File:** `src/pinwheel/core/season.py`

#### Step 1: Determine Ruleset

- If `carry_forward_rules=True`:
  - Uses `previous_season_id` if provided (the bot passes `latest_season.id`).
  - Otherwise, finds the latest completed season in the league.
  - Reads `current_ruleset` from the previous season.
- If `carry_forward_rules=False`:
  - Uses `DEFAULT_RULESET` (all parameters at their Pydantic defaults).

#### Step 2: Close Previous Season

If a source season exists and is NOT in a terminal state (`completed`, `complete`, `archived`):
- Tallies any remaining untallied proposals via `tally_pending_governance()`. This means proposals submitted during the last round that were never tallied get resolved (ties fail at 0-0 votes).
- If `carry_forward_rules=True` and the tally changed the ruleset, the updated ruleset is used for the new season.
- Sets the previous season's status to `COMPLETE`.

#### Step 3: Create New Season Row

Creates a new `SeasonRow` with the determined ruleset as both `starting_ruleset` and `current_ruleset`.

#### Step 4: Carry Over Teams

Calls `carry_over_teams(from_season_id, to_season_id)`:

- For each team in the old season:
  - Creates a **new** `TeamRow` linked to the new season (same name, colors, motto, venue).
  - Creates **new** `HooperRow` records for each hooper (same name, archetype, attributes, moves). Hoopers get fresh stat records but retain their identity.
  - Carries over **governor enrollments**: for each `PlayerRow` enrolled on the old team, calls `enroll_player()` to link them to the new team in the new season.

**Important:** Team IDs and Hooper IDs are new UUIDs each season. The identity is preserved through names, not IDs. This means cross-season stat lookups by ID will not work -- you need to match by name or maintain a separate identity mapping.

#### Step 5: Generate Schedule

If 2+ teams exist, generates a round-robin schedule using `generate_round_robin()` with `num_rounds=ruleset.round_robins_per_season`.

#### Step 6: Regenerate Tokens

Calls `regenerate_all_governor_tokens()` which gives each enrolled governor fresh tokens:
- PROPOSE tokens
- AMEND tokens
- BOOST tokens

Token balances are computed from the event store (sum of `token.regenerated` minus `token.spent` events for the new season).

#### Step 7: Mark Active

Sets the new season's status to `ACTIVE`.

### Discord Announcement

After `start_new_season()` returns, the bot:
1. Sends an ephemeral confirmation to the admin.
2. Posts a public announcement embed in the play-by-play or main channel.

## What Carries Over

| Item | Carried Over? | Details |
|------|:---:|---------|
| **Teams** | Yes | New DB rows, same names/colors/venues |
| **Hoopers** | Yes | New DB rows, same names/archetypes/attributes |
| **Governor Enrollments** | Yes | Players are re-enrolled on their teams |
| **Rules (if `carry_rules=True`)** | Yes | Previous season's final `current_ruleset` |
| **Token Balances** | No | Fresh tokens regenerated for the new season |
| **Proposals** | No | All proposals are scoped to a season via the event store |
| **Votes** | No | All votes are scoped to a season |
| **Game Results** | No | Game history stays with the old season |
| **Reports** | No | Reports are scoped to a season/round |
| **Standings** | No | Fresh standings from new games |
| **Active Effects** | No | Effect registry is loaded per-season from events |
| **Team Meta** | No | Meta columns on the new team rows start empty |
| **Hooper Backstories** | No | Backstory column is not copied in `carry_over_teams()` |
| **Team Strategies** | No | Strategy events are season-scoped |

## What Does NOT Carry Over (Potential Gaps)

### 1. Hooper Backstories

Governors who wrote backstories via `/bio` lose them on season transition. The `carry_over_teams()` function creates new hooper rows but does not copy the `backstory` column.

**Recommendation:** Add `backstory=hooper.backstory` to the `create_hooper()` call in `carry_over_teams()`. Backstories are creative work by governors and should persist.

### 2. Team Meta

The `meta` JSON column on teams (used by the effects system for things like "swagger" or "morale" tracking) is not carried over. This is probably correct behavior -- meta represents in-season state that should reset. But if players expect persistent team identity metadata, this could be surprising.

**Recommendation:** Keep current behavior (meta resets each season). If needed later, add a `persistent_meta` field that carries over.

### 3. Season Archives

`archive_season()` creates a `SeasonArchiveRow` with final standings, rule change history, and memorial data. However, `start_new_season()` does NOT call `archive_season()` -- it only marks the previous season as `COMPLETE`. Archiving appears to be a separate operation.

**Recommendation:** Consider calling `archive_season()` as part of `start_new_season()` to ensure every completed season gets archived automatically.

### 4. Hooper Identity Across Seasons

Since hoopers get new UUIDs each season, there is no built-in way to track a hooper's career across seasons. A hooper named "Blaze" on the Firebolts in Season 1 and Season 2 would have different IDs.

**Recommendation:** If career stats become important, add a `canonical_hooper_id` field that persists across seasons, set during `carry_over_teams()`.

### 5. Offseason Governance

The `OFFSEASON` phase exists between `CHAMPIONSHIP` and `COMPLETE`. `enter_offseason()` opens a governance window where governors can submit and vote on meta-rule proposals. `close_offseason()` tallies proposals and transitions to `COMPLETE`.

However, the `/new-season` flow does NOT go through the offseason phase -- it directly closes the previous season. The offseason flow appears to be an alternative path:
- **Path A (current `/new-season`):** CHAMPIONSHIP -> COMPLETE (via start_new_season closing the previous season) -> new season ACTIVE
- **Path B (offseason flow):** CHAMPIONSHIP -> OFFSEASON -> COMPLETE -> new season ACTIVE

The offseason governance window is not triggered by `/new-season`. It would need to be manually initiated (e.g., via an admin API call or a scheduler-based trigger after the championship celebration timer expires).

**Recommendation:** Document these two paths. Consider adding an `/offseason` admin command or auto-triggering offseason after the championship timer expires.

### 6. No "Carry Rules" Feedback

When `carry_rules=False`, the Discord announcement says "Rules: default" but does not enumerate what changed. Governors who spent effort changing rules in the previous season have no visibility into whether their work was preserved.

**Recommendation:** When `carry_rules=True`, log which parameters differ from defaults. When `carry_rules=False`, list parameters that were non-default in the previous season (i.e., what governors are "losing").

## Testing Considerations

The season transition flow is complex with many moving parts. Key test scenarios:

1. New season with carry_rules=True -- verify ruleset, teams, hoopers, enrollments copy
2. New season with carry_rules=False -- verify default ruleset, teams still copy
3. Previous season has untallied proposals -- verify they get resolved before closing
4. Previous season already completed -- verify no double-close
5. Token balances start fresh in new season
6. Schedule generated correctly for new season
7. Old season's game results and reports not visible in new season queries

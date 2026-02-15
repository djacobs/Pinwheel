# Spectator Journey and Team Following

## Context

PRODUCT_OVERVIEW.md identifies the spectator journey as Gap #8 (severity: Medium): "The spectator journey is completely undesigned beyond access permissions." The document describes a desired progression — discover the game, watch games, read shared reports, follow a team, engage in #trash-talk, optionally convert to governor — but none of the engagement mechanics exist.

Today, spectators can view the web dashboard without logging in and post in `#trash-talk` on Discord. There is no team-following mechanism, no spectator notifications, no conversion funnel from spectator to governor, and no way to distinguish spectator sessions from governor sessions in metrics.

This feature bridges the gap between passive observation and active governance. It gives spectators a reason to return (team attachment via following) and a path to deepen engagement (follow -> notifications -> conversion).

---

## What Exists Today

### Authentication (`src/pinwheel/auth/`)

**`src/pinwheel/auth/deps.py`:**
- `SessionUser` model: stores `discord_id`, `username`, `avatar_url` in a signed cookie.
- `get_current_user()` dependency: extracts the user from the session cookie. Returns `None` if not logged in.
- `OptionalUser` type alias: `Annotated[SessionUser | None, Depends(get_current_user)]`. Used by all page handlers.

**`src/pinwheel/auth/oauth.py`:**
- Discord OAuth2 flow: `/auth/login` -> Discord consent -> `/auth/callback` -> signed session cookie -> `/auth/logout`.
- Scopes: `identify` only. Does not request `guilds` or `guilds.join`.
- Callback creates/updates a `PlayerRow` in the database. Every OAuth user gets a player record regardless of whether they are a governor.

**Key observation:** The system already distinguishes logged-in users from anonymous visitors via `OptionalUser`. What it lacks is distinguishing *spectators* (logged in, no team) from *governors* (logged in, enrolled on a team). The `PlayerRow` has a `team_id` field that is `None` for unaffiliated users — this is the natural dividing line.

### Web Pages (`src/pinwheel/api/pages.py`)

All page handlers accept `OptionalUser`. Auth context is injected into every template via `_auth_context()`:

```python
def _auth_context(request: Request, current_user: SessionUser | None) -> dict:
    return {
        "current_user": current_user,
        "oauth_enabled": oauth_enabled,
        "pinwheel_env": settings.pinwheel_env,
        "app_version": APP_VERSION,
        "discord_invite_url": settings.discord_invite_url,
        "is_admin": is_admin,
    }
```

Pages currently available without login (all public):
- `/` (home)
- `/play` (how to play)
- `/arena` (game results)
- `/standings`
- `/games/{game_id}` (individual game)
- `/teams/{team_id}` (team profile)
- `/hoopers/{hooper_id}` (hooper profile)
- `/governance` (proposals and outcomes)
- `/rules` (current ruleset)
- `/reports` (public reports archive)
- `/seasons/archive` and `/seasons/archive/{season_id}`

Pages requiring auth:
- `/admin` (admin only)
- Hooper bio edit (governor on that team only)

**Key observation:** The entire dashboard is publicly readable. Spectators already have full read access. What's missing is *personalization* — a logged-in spectator sees exactly the same content as an anonymous visitor.

### Templates (`templates/pages/`)

Templates reference `current_user` for conditional rendering (login/logout button, admin link). The base template (`base.html`) includes a nav bar and footer. Team pages show team colors, hoopers, governors, and venue info. No template has any concept of "following" a team.

### Database (`src/pinwheel/db/models.py`, `src/pinwheel/db/repository.py`)

**`PlayerRow`:**
- `id`, `discord_id`, `username`, `avatar_url`, `team_id`, `enrolled_season_id`, `last_login`, `created_at`.
- `team_id` is nullable. A player with `team_id=None` is either a spectator or a governor between seasons.

**`Repository`:**
- `get_or_create_player()`: Creates or updates a player on OAuth login.
- `enroll_player()`: Sets `team_id` and `enrolled_season_id`. This is the "become a governor" action.
- `get_player_enrollment()`: Checks enrollment status.
- `get_governors_for_team()`: Returns enrolled players for a team.
- No concept of "following" exists in the data layer.

### Discord Bot (`src/pinwheel/discord/bot.py`)

- The `/join` command enrolls a user on a team (governor enrollment).
- The bot posts game results, governance outcomes, and reports to configured channels.
- No spectator-specific commands or roles exist beyond the implicit Spectator Discord role described in PLAYER.md.

### Event Bus (`src/pinwheel/core/event_bus.py`)

- Pub/sub pattern with typed events. Subscribers can filter by event type or receive all events (wildcard).
- Events include: `game.completed`, `round.completed`, `governance.tally`, `report.generated`, etc.
- The event bus is the natural hook point for spectator notifications — subscribe to events for followed teams.

---

## What Needs to Be Built

### Phase 1: Team Following (Core Mechanism)

#### 1a. Data Model: `TeamFollowRow`

A new database table to track which users follow which teams.

```python
class TeamFollowRow(Base):
    __tablename__ = "team_follows"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    player_id: Mapped[str] = mapped_column(ForeignKey("players.id"), nullable=False)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"), nullable=False)
    season_id: Mapped[str] = mapped_column(ForeignKey("seasons.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))

    __table_args__ = (
        UniqueConstraint("player_id", "team_id", "season_id", name="uq_follow"),
    )
```

Season-scoped: following is per-season. When a new season starts, follows reset (or carry forward — a configuration choice). The unique constraint prevents duplicate follows.

#### 1b. Repository Methods

```python
async def follow_team(self, player_id: str, team_id: str, season_id: str) -> TeamFollowRow:
    """Start following a team. Idempotent."""

async def unfollow_team(self, player_id: str, team_id: str, season_id: str) -> None:
    """Stop following a team."""

async def get_followed_teams(self, player_id: str, season_id: str) -> list[TeamFollowRow]:
    """Get all teams a player follows in a season."""

async def get_team_followers(self, team_id: str, season_id: str) -> list[PlayerRow]:
    """Get all players following a team (for notification targeting)."""

async def is_following(self, player_id: str, team_id: str, season_id: str) -> bool:
    """Check if a player follows a specific team."""
```

#### 1c. Web UI: Follow/Unfollow Buttons

Add follow/unfollow capability to the team page (`/teams/{team_id}`).

**When logged in:** Show a "Follow" button (or "Following" if already following) on the team page. Use HTMX for instant toggle without page reload.

**When not logged in:** Show a "Log in to follow" link that redirects to OAuth.

**API endpoint:**

```python
@router.post("/api/teams/{team_id}/follow")
async def follow_team(team_id: str, repo: RepoDep, current_user: RequiredUser) -> dict:
    """Toggle team follow status. Requires login."""

@router.delete("/api/teams/{team_id}/follow")
async def unfollow_team(team_id: str, repo: RepoDep, current_user: RequiredUser) -> dict:
    """Unfollow a team. Requires login."""
```

**Template changes:**
- `templates/pages/team.html`: Add follow button in the team header area.
- `templates/base.html`: Show followed team indicator in nav (optional, lightweight).

#### 1d. Personalized Home Page

When a logged-in spectator has followed teams, the home page (`/`) prioritizes content for those teams:

- Latest game results featuring followed teams appear first.
- Upcoming games featuring followed teams are highlighted.
- Standings highlight the followed team's row.

This requires modifying `home_page()` in `pages.py` to check follow status and reorder content. The existing `_get_standings()` helper already computes standings; adding a `followed_team_ids` set to the template context lets the template highlight rows.

### Phase 2: Notifications

#### 2a. Web Notifications (In-Dashboard)

A notification panel accessible from the nav bar. Shows recent events for followed teams:

- Game results: "Thorns beat Breakers 58-52 in Round 14"
- Rule changes affecting the league: "Three-point value changed to 4 (Proposal #12)"
- Reports mentioning followed teams

**Data model:** No new table needed. Notifications are computed on the fly by querying recent game results and governance events, filtered to followed teams. For performance, cache the last N events per team.

**API endpoint:**

```python
@router.get("/api/notifications")
async def get_notifications(
    repo: RepoDep,
    current_user: RequiredUser,
    since: str | None = None,  # ISO timestamp for incremental fetch
) -> dict:
    """Get recent notifications for the current user's followed teams."""
```

**Template:** A notification dropdown in the nav bar with an unread count badge. HTMX polling every 60 seconds to check for new events.

#### 2b. Discord Notifications (DM)

For spectators on the Discord server, optionally send DMs when followed team events occur.

**Preference storage:** Add a `notification_preferences` JSON column to `PlayerRow`:

```python
notification_preferences: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=dict)
# Example: {"discord_dm": true, "events": ["game_result", "rule_change"]}
```

**Hook into EventBus:** Register a notification handler in `src/pinwheel/core/hooks.py` that listens for `game.completed` and `governance.tally` events, looks up followers for affected teams, and sends DMs via the bot.

**Discord slash command:**

```python
@self.tree.command(
    name="follow",
    description="Follow a team to get notifications about their games",
)
@app_commands.describe(team="The team to follow")
async def follow_command(interaction: discord.Interaction, team: str) -> None:
    await self._handle_follow(interaction, team)
```

This mirrors the web follow action but through Discord.

### Phase 3: Spectator-to-Governor Conversion

#### 3a. Conversion Prompt

When a spectator has been following a team for N rounds (configurable, default: 3), show a gentle conversion prompt on the team page and in DM notifications:

"You've been following the Thorns for 3 rounds. Want to help govern their future? `/join Thorns` to become a governor."

This is not aggressive. It appears once and can be dismissed. It respects the spectator's choice to remain a spectator.

**Implementation:** Check the `created_at` on `TeamFollowRow`. If the follow is older than N rounds worth of time, show the prompt. Track dismissal via a simple `conversion_prompt_dismissed` boolean on the follow row.

#### 3b. Spectator Profile Page

A lightweight profile page for spectators (distinct from the governor profile at `/governors/{player_id}`):

- Teams they follow
- Games they have watched (optional, tracked via `game.result.view` events if instrumented)
- Favorite hoopers (based on most-viewed hooper pages, if tracked)
- Conversion CTA

This is lower priority than Phase 1 and 2.

### Phase 4: Metrics

#### 4a. User Role Dimension

Add a `role` dimension to session tracking. Every page view should be attributable to one of: `anonymous`, `spectator` (logged in, no team), `governor` (logged in, enrolled).

**Implementation:** Extend `_auth_context()` to include a `user_role` field:

```python
def _auth_context(request: Request, current_user: SessionUser | None) -> dict:
    # ... existing logic ...
    user_role = "anonymous"
    if current_user:
        user_role = "governor" if is_governor else "spectator"
    return {
        # ... existing fields ...
        "user_role": user_role,
    }
```

This requires checking enrollment status in the auth context, which means a DB query. Cache this on the session cookie or add it to `SessionUser` at login time. The cleaner approach: add `team_id` to the session cookie payload during OAuth callback (already available from `PlayerRow`), and refresh it on each login.

#### 4b. Spectator-Specific Events

```
spectator.follow.team    — followed a team
spectator.unfollow.team  — unfollowed a team
spectator.conversion     — spectator became a governor (follow -> /join)
spectator.session.start  — spectator visited the dashboard
spectator.notification.view — spectator opened notification panel
```

These events feed the metrics identified in PRODUCT_OVERVIEW.md:
- `spectator.session.duration`
- `spectator.conversion.governor`
- `spectator.content.preference`

---

## Files to Create/Modify

### New Files

| File | Purpose |
|---|---|
| `src/pinwheel/api/follow.py` | API routes for follow/unfollow + notifications endpoint |
| `tests/test_follow.py` | Unit tests for follow data model, API endpoints, notification queries |

### Modified Files

| File | Changes |
|---|---|
| `src/pinwheel/db/models.py` | Add `TeamFollowRow` table, add `notification_preferences` column to `PlayerRow` |
| `src/pinwheel/db/repository.py` | Add follow/unfollow/query methods |
| `src/pinwheel/api/pages.py` | Modify `home_page()` to personalize for followed teams, add `user_role` to auth context, add follow button context to `team_page()` |
| `src/pinwheel/main.py` | Register the new `follow` router |
| `src/pinwheel/discord/bot.py` | Add `/follow` and `/unfollow` slash commands |
| `src/pinwheel/core/hooks.py` | Add notification handler for followed-team events |
| `templates/pages/team.html` | Add follow/unfollow button |
| `templates/pages/home.html` | Highlight followed teams in standings and game results |
| `templates/base.html` | Add notification indicator in nav bar (Phase 2) |
| `src/pinwheel/auth/deps.py` | Optionally extend `SessionUser` with enrollment status for `user_role` |

---

## Implementation Sequence

1. **Data model.** Add `TeamFollowRow` to `src/pinwheel/db/models.py`. Add repository methods. Write tests against the data layer. Since the schema uses `auto_migrate_schema()` for additive changes, the new table will be created automatically on next startup.
2. **Follow API.** Create `src/pinwheel/api/follow.py` with `POST /api/teams/{team_id}/follow` and `DELETE /api/teams/{team_id}/follow`. Register the router in `main.py`. Test with httpx AsyncClient.
3. **Team page UI.** Add follow button to `templates/pages/team.html`. Wire it with HTMX to the follow API. Modify `team_page()` in `pages.py` to pass follow status to the template.
4. **Personalized home.** Modify `home_page()` to query followed teams and pass `followed_team_ids` to the template. Update `templates/pages/home.html` to highlight followed teams.
5. **Notifications endpoint.** Add `GET /api/notifications` that queries recent game results and governance events for followed teams. Add notification dropdown to `templates/base.html`.
6. **Discord follow command.** Add `/follow` and `/unfollow` commands to the bot. Wire them to the same repository methods.
7. **DM notifications.** Add EventBus hook in `hooks.py` that sends DMs to followers when game results are posted. Respect notification preferences.
8. **Conversion prompt.** Add age-check logic to team page. Show gentle conversion CTA for long-term followers.
9. **Metrics.** Add `user_role` to auth context. Instrument spectator events.

---

## Testing Strategy

### Unit Tests

- **Data model:** Create follows, verify uniqueness constraint prevents duplicates, test unfollow deletes the row, test `get_followed_teams` and `get_team_followers` return correct results.
- **API endpoints:** Test follow and unfollow with authenticated and unauthenticated users. Verify 401/403 for unauthenticated requests. Verify idempotency (following twice is not an error). Verify unfollowing a team you do not follow is not an error.
- **Personalized home:** Seed a database, create follows, verify the home page template context includes `followed_team_ids`. Verify followed teams appear in highlights.
- **Notifications:** Seed games and governance events, create follows, verify the notification endpoint returns correctly filtered events. Test the `since` parameter for incremental fetching.

### Integration Tests

- **Full flow:** OAuth login -> follow a team -> view home page (personalized) -> unfollow -> verify depersonalized.
- **Discord flow:** `/follow Thorns` -> verify DB row created -> game completes -> verify DM sent.
- **Conversion flow:** Follow a team -> advance N rounds -> verify conversion prompt appears on team page.

### Edge Cases

- **Spectator follows a team then joins it via `/join`.** The follow should still exist but the UI should show governor-specific content instead of spectator content. The follow row is harmless.
- **Season transition.** When a new season starts, follows reference the old season's team IDs. Teams are recreated per season, so follows do not automatically carry over. Decide: reset follows (simple) or migrate them (better UX, more complex). Recommendation: reset for v1, add migration in v2.
- **Anonymous user clicks "Follow."** Redirect to OAuth login, then back to the team page with a query param (`?follow=1`) that triggers the follow action after login.
- **Governor unfollows their own team.** Governors are implicitly "following" their team. The follow button should not appear for governors viewing their own team page.

---

## Design Decisions

1. **Season-scoped follows.** Follows are tied to a season because teams may change between seasons (new names, new hoopers, roster trades). This keeps the data model clean and avoids stale references.
2. **OAuth required for following.** Anonymous users cannot follow teams. This creates a natural conversion step (anonymous -> logged-in spectator) before the deeper conversion (spectator -> governor).
3. **HTMX for follow toggle.** No page reload needed. The button swaps between "Follow" and "Following" states via HTMX `hx-swap`. This matches the existing HTMX patterns in the codebase (see hooper bio editing).
4. **Notifications are computed, not stored.** No notification table. Recent events for followed teams are queried on demand. This avoids a write-heavy notification pipeline and keeps the system simple. If notifications become a performance bottleneck, add a materialized notification table later.
5. **Gentle conversion, never aggressive.** The conversion prompt appears once after sustained following. No pop-ups, no countdown timers, no "you're missing out" language. This aligns with Resonant Computing principles: the software serves the user, not the other way around.
6. **`auto_migrate_schema()` handles the new table.** Since the `TeamFollowRow` table is entirely new (not a column addition), it will be picked up by `Base.metadata.create_all()` which runs at startup. No manual migration needed.

---

## Open Questions

1. **Follow limit.** Should a spectator be able to follow all teams? Following every team defeats the purpose of personalization. Consider a limit of 2-3 followed teams, or no limit (let the user decide).
2. **Cross-season follow carry-forward.** When a new season starts with `/new-season`, should follows carry forward automatically? The team IDs change between seasons, so this requires mapping old teams to new teams (which `carry_rules` already does for the ruleset). Recommendation: do not carry forward in v1.
3. **Notification frequency.** How often should DM notifications fire? Every game result, or batched per round? Per-game could be noisy if a spectator follows multiple teams. Recommendation: batch per round — one DM summarizing all games for followed teams.
4. **Spectator in Discord without OAuth.** A Discord user who has the Spectator role but has never logged in via OAuth has no `PlayerRow`. The `/follow` Discord command would need to create a player record. The existing `get_or_create_player()` handles this, but the flow needs testing.
5. **Privacy of follow data.** Should follow counts be public on the team page ("42 followers")? This adds social proof but could create pressure. Recommendation: show follower counts in v2, not v1.

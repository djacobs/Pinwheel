# API Architecture Document — Plan

## Status: PLAN
## Date: 2026-02-14
## Priority: P1

---

## 1. API Overview

### FastAPI App Factory (`main.py`)
- `create_app()` creates a `FastAPI` instance with title "Pinwheel Fates", version "0.1.0".
- `docs_url` is `/docs` in development, disabled (`None`) in production.
- Lifespan context manager handles startup (engine creation, table creation, inline migrations, EventBus init, PresentationState init, Discord bot startup, APScheduler startup) and shutdown (scheduler stop, Discord bot close, engine dispose).

### Router Registration Order (from `create_app()`):
1. `auth_router` — `/auth` (OAuth2 flow)
2. `games_router` — `/api/games`
3. `teams_router` — `/api/teams`
4. `standings_router` — `/api` (prefix is `/api`, endpoint is `/standings`)
5. `governance_router` — `/api/governance`
6. `reports_router` — `/api/reports`
7. `events_router` — `/api/events`
8. `eval_dashboard_router` — `/admin`
9. `admin_roster_router` — `/admin`
10. `pace_router` — `/api/pace`
11. `seasons_router` — `/api/seasons`
12. `pages_router` — no prefix (root pages, must come after API routes)

**NOT registered:** `mirrors_router` from `mirrors.py` exists in the codebase but is NOT included in `main.py`. Dead module or pending registration.

### Static Files
Mounted at `/static` from `PROJECT_ROOT / "static"`.

### Health Check
`GET /health` returns `{"status": "ok", "env": <pinwheel_env>}`.

### Dependency Injection (`api/deps.py`)
- `get_engine(request)` — extracts `AsyncEngine` from `request.app.state.engine`
- `get_session(engine)` — yields an `AsyncSession` with auto-commit/rollback
- `get_repo(session)` — returns a `Repository` instance
- `RepoDep` = `Annotated[Repository, Depends(get_repo)]` — the standard DI alias

### Auth Dependencies (`auth/deps.py`)
- `SessionUser` — Pydantic model: `discord_id`, `username`, `avatar_url`
- `get_current_user(request)` — extracts user from signed session cookie
- `OptionalUser` = `Annotated[SessionUser | None, Depends(get_current_user)]`

---

## 2. REST API Endpoints

### Games (`/api/games`)

| Method | Path | Description | Auth | Response |
|--------|------|-------------|------|----------|
| GET | `/api/games/{game_id}` | Get a game result by ID | None | `{data: {id, home_team_id, away_team_id, home_score, away_score, winner_team_id, total_possessions, elam_target, quarter_scores, seed}}` |
| GET | `/api/games/{game_id}/boxscore` | Get box scores for a game | None | `{data: [{hooper_id, team_id, points, fg_made, fg_attempted, 3pm, 3pa, assists, steals, turnovers}]}` |

### Teams (`/api/teams`)

| Method | Path | Description | Auth | Response |
|--------|------|-------------|------|----------|
| GET | `/api/teams` | List all teams for a season | None (query: `season_id`) | `{data: [{id, name, color, motto, venue, hooper_count}]}` |
| GET | `/api/teams/{team_id}` | Get a single team with hoopers | None | `{data: {id, name, color, motto, venue, hoopers: [...]}}` |

### Standings (`/api`)

| Method | Path | Description | Auth | Response |
|--------|------|-------------|------|----------|
| GET | `/api/standings` | Get current standings | None (query: `season_id`) | `{data: [{team_id, team_name, wins, losses, points_for, points_against}]}` |

### Governance (`/api/governance`)

| Method | Path | Description | Auth | Response |
|--------|------|-------------|------|----------|
| POST | `/api/governance/proposals` | Submit a new governance proposal | None (governor_id in body) | `{data: Proposal}` |
| POST | `/api/governance/proposals/{proposal_id}/confirm` | Confirm AI interpretation | None | `{data: Proposal}` |
| POST | `/api/governance/votes` | Cast a vote on a proposal | None (governor_id in body) | `{data: Vote}` |
| GET | `/api/governance/proposals` | List all proposals for a season | None (query: `season_id`) | `{data: [Proposal]}` |
| GET | `/api/governance/rules/current` | Get current ruleset | None (query: `season_id`) | `{data: {ruleset, changes_from_default}}` |
| GET | `/api/governance/rules/history` | Get all rule changes | None (query: `season_id`) | `{data: [event_payloads]}` |

Request models: `SubmitProposalRequest`, `CastVoteRequest`, `CloseWindowRequest`.

Note: Governance endpoints accept `governor_id` in the body rather than requiring auth headers. Hackathon shortcut — in production, these should verify the requester IS the governor.

The proposal submission flow includes AI interpretation: if `ANTHROPIC_API_KEY` is set, it runs injection classification first, then AI interpretation. Otherwise falls back to `interpret_proposal_mock`.

### Reports (`/api/reports`)

| Method | Path | Description | Auth | Response |
|--------|------|-------------|------|----------|
| GET | `/api/reports/round/{season_id}/{round_number}` | Get public reports for a round | None (optional query: `report_type`) | `{data: [{id, report_type, round_number, content, created_at}]}` |
| GET | `/api/reports/private/{season_id}/{governor_id}` | Get private reports for a governor | Trusted param (hackathon) | `{data: [{id, report_type, round_number, governor_id, content, created_at}]}` |
| GET | `/api/reports/latest/{season_id}` | Get most recent sim and gov reports | None | `{data: {simulation?: {...}, governance?: {...}}}` |

### Seasons (`/api/seasons`)

| Method | Path | Description | Auth | Response |
|--------|------|-------------|------|----------|
| POST | `/api/seasons` | Create a new season (admin) | None (should be admin-gated) | `{data: {id, league_id, name, status, starting_ruleset, current_ruleset, team_count}}` |

Request model: `CreateSeasonRequest(league_id, name, carry_forward_rules, previous_season_id)`.

### Pace (`/api/pace`)

| Method | Path | Description | Auth | Response |
|--------|------|-------------|------|----------|
| GET | `/api/pace` | Get current presentation pace | None | `PaceResponse(pace, cron, auto_advance)` |
| POST | `/api/pace` | Change presentation pace | None | `PaceResponse` |
| POST | `/api/pace/advance` | Trigger manual round advance | None | `AdvanceResponse(status, round?)` |
| GET | `/api/pace/status` | Get presentation state | None | `PaceStatusResponse(is_active, current_round, current_game_index)` |

### Mirrors (`/api/mirrors`) — NOT REGISTERED

Router exists in code but is NOT included in `main.py`. Dead code or pending feature. Endpoints mirror the Reports router but use `mirror_type` / `get_mirrors_for_round` / `get_private_mirrors` / `get_latest_mirror` methods.

---

## 3. SSE Event Streaming

### Endpoint (`api/events.py`)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/events/stream` | SSE stream. Query param `event_type` optional filter. |
| GET | `/api/events/health` | EventBus health check (subscriber count) |

### How SSE works
- `EventBus` (`core/event_bus.py`) is an in-memory pub/sub system using `asyncio.Queue`.
- Each SSE client gets a `Subscription` (async context manager + async iterator).
- Events envelope: `{"type": event_type, "data": data}`.
- Heartbeat every 15 seconds (SSE comment `: heartbeat\n\n`) to keep connections alive through reverse proxies.
- Initial flush comment `: connected\n\n` transitions browsers from "connecting" to "open".
- `X-Accel-Buffering: no` header for Nginx proxy compatibility.

### Event types published

1. `game.completed` — after each game sim (game_loop.py)
2. `round.completed` — after all games in a round (game_loop.py)
3. `report.generated` — after report generation (scheduler_runner.py)
4. `governance.window_closed` — after governance tally (scheduler_runner.py)
5. `presentation.game_starting` — when replay begins for a game (presenter.py)
6. `presentation.game_finished` — when game replay completes (presenter.py)
7. `presentation.round_finished` — when round replay completes (presenter.py)
8. `presentation.possession` — each play-by-play event during replay (presenter.py)
9. `season.regular_season_complete` — end of regular season (game_loop.py)
10. `season.semifinals_complete` — end of semifinals (game_loop.py)
11. `season.playoffs_complete` — end of playoffs (game_loop.py)
12. `season.phase_changed` — season phase transitions (season.py)
13. `season.championship_started` — championship ceremony (season.py)

### Frontend consumption
Templates (arena.html, home.html) use `EventSource` to connect to `/api/events/stream` and receive live updates via HTMX or vanilla JS. The `presentation.*` events drive the live arena zone.

---

## 4. Discord Bot Commands

Complete reference for all 15 slash commands registered in `_setup_commands()` (`discord/bot.py`):

1. **`/standings`** — View current league standings
2. **`/propose <text>`** — Put a rule change on the Floor. Checks PROPOSE token, runs AI interpretation (or mock), shows ephemeral embed with ProposalConfirmView.
3. **`/schedule`** — View the upcoming game schedule
4. **`/reports`** — View the latest AI reports
5. **`/join [team]`** — Join a team as a governor for this season. Mid-season team switches denied.
6. **`/vote <choice> [boost] [proposal]`** — Vote on a proposal on the Floor. Computes vote weight from team size.
7. **`/tokens`** — Check your Floor token balance (PROPOSE, AMEND, BOOST)
8. **`/trade <target> <offer_type> <offer_amount> <request_type> <request_amount>`** — Offer a token trade
9. **`/trade-hooper <offer_hooper> <request_hooper>`** — Propose trading hoopers between teams
10. **`/strategy <text>`** — Set your team's strategic direction (AI-interpreted)
11. **`/bio <hooper> <text>`** — Write a backstory for one of your team's hoopers
12. **`/profile`** — View your governor profile and Floor record
13. **`/new-season <name> [carry_rules]`** — Start a new season (admin only)
14. **`/proposals [season]`** — View all proposals and their status
15. **`/roster`** — View all enrolled governors for this season

---

## 5. Discord Interactive Views (`discord/views.py`)

1. **ProposalConfirmView** — Confirm/Revise/Cancel for AI-interpreted proposals (300s timeout)
2. **ReviseProposalModal** — Text input popup for revising proposal text (500 char max)
3. **TradeOfferView** — Accept/Reject for token trades (1hr timeout)
4. **StrategyConfirmView** — Confirm/Cancel for team strategy (300s timeout)
5. **HooperTradeView** — Approve/Reject for hooper trades between teams (1hr timeout)
6. **AdminReviewView** — Clear/Veto for wild proposals sent via DM to admin (24hr timeout)
7. **AdminVetoReasonModal** — Text input for veto reason (500 char max)

---

## 6. Discord Event Handlers

The bot subscribes to ALL events (wildcard subscription) and routes them:

| Event Type | Channel(s) | Behavior |
|------------|-----------|----------|
| `presentation.game_finished` | play-by-play, big-plays (conditional), team channels | Posts game result embed. Posts to big-plays if margin > 15 (blowout) or margin <= 2 (buzzer-beater). |
| `presentation.round_finished` | play-by-play | Posts round summary embed |
| `report.generated` (private) | DM to governor | Sends private report embed to governor's Discord DM |
| `report.generated` (public) | play-by-play or main | Posts report embed |
| `governance.window_closed` | main, all team channels | Posts "The Floor Has Spoken" embed + per-proposal vote tally embeds |
| `season.championship_started` | main, all team channels | Posts championship ceremony embed with awards |
| `season.phase_changed` (to "complete") | main | Posts "Season Complete" embed |

### Channel structure
- `how-to-play` — Welcome/onboarding, populated on bot startup if empty
- `play-by-play` — Live game updates, report excerpts
- `big-plays` — Highlights (blowouts, buzzer-beaters)
- `team_<team_id>` — Private per-team channels (role-gated)
- Main channel (fallback from `DISCORD_CHANNEL_ID`)

### Server setup (`_setup_server`)
- Creates "PINWHEEL FATES" category
- Creates shared channels: how-to-play, play-by-play, big-plays
- Creates per-team channels and roles with proper permission overwrites
- Self-heals role enrollments on restart (`_sync_role_enrollments`)
- Persists channel IDs in `bot_state` table for cross-restart stability

---

## 7. Web Pages (Jinja2)

### Public Pages (`api/pages.py`)

| Method | Path | Template | Description |
|--------|------|----------|-------------|
| GET | `/` | `pages/home.html` | Home dashboard — standings, latest games, report, upcoming |
| GET | `/play` | `pages/play.html` | How to Play — onboarding, ruleset summary, pace info |
| GET | `/arena` | `pages/arena.html` | Arena — recent rounds, live zone, upcoming games |
| GET | `/standings` | `pages/standings.html` | Standings page |
| GET | `/games/{game_id}` | `pages/game.html` | Game detail — box scores, play-by-play narration |
| GET | `/teams/{team_id}` | `pages/team.html` | Team profile — hoopers with spider charts, governors, strategy |
| GET | `/hoopers/{hooper_id}` | `pages/hooper.html` | Hooper profile — spider chart, game log, season averages |
| GET | `/hoopers/{hooper_id}/bio/edit` | (inline HTML fragment) | HTMX fragment: bio edit form |
| GET | `/hoopers/{hooper_id}/bio/view` | (inline HTML fragment) | HTMX fragment: bio display |
| POST | `/hoopers/{hooper_id}/bio` | (inline HTML fragment) | Update hooper bio (form post) |
| GET | `/governors/{player_id}` | `pages/governor.html` | Governor profile — governance record, activity history |
| GET | `/governance` | `pages/governance.html` | Governance audit trail — proposals, outcomes, vote totals |
| GET | `/rules` | `pages/rules.html` | Current rules — tiered display, changes from default |
| GET | `/reports` | `pages/reports.html` | Reports archive (excludes private) |
| GET | `/seasons/archive` | `pages/season_archive.html` | List all archived seasons |
| GET | `/seasons/archive/{season_id}` | `pages/season_archive.html` | View a specific season's archive |
| GET | `/terms` | `pages/terms.html` | Terms of Service |
| GET | `/privacy` | `pages/privacy.html` | Privacy Policy |

### Admin Pages

| Method | Path | Template | Description | Auth |
|--------|------|----------|-------------|------|
| GET | `/admin/evals` | `pages/eval_dashboard.html` | Eval dashboard — aggregate stats, no report text | Login required if OAuth enabled |
| GET | `/admin/roster` | `pages/admin_roster.html` | Admin roster — all governors with tokens | Admin Discord ID match |

### Template patterns
- Base template: `base.html`
- Auth context (`_auth_context()`) injected into every page: `current_user`, `oauth_enabled`, `pinwheel_env`, `app_version`, `discord_invite_url`
- HTMX patterns for bio edit/view: `hx-get`, `hx-post`, `hx-target`, `hx-swap="innerHTML"`
- Spider chart helpers (`api/charts.py`): pure SVG geometry functions for 9-attribute radar charts
- No JavaScript build step; HTMX loaded from static files

---

## 8. Auth Flow

### Discord OAuth2 Flow (`auth/oauth.py`)
1. `GET /auth/login` — Redirects to Discord OAuth2 consent page with `identify` scope. Sets CSRF `pinwheel_oauth_state` cookie (300s TTL).
2. `GET /auth/callback` — Validates CSRF state, exchanges code for token, fetches user profile, creates/updates PlayerRow, signs session cookie.
3. `GET /auth/logout` — Clears the session cookie.

### Session Management
- Cookie name: `pinwheel_session`
- Max age: 7 days
- Signed with `URLSafeTimedSerializer` (from `itsdangerous`) using `SESSION_SECRET_KEY`
- Payload: `{discord_id, username, avatar_url}`
- HttpOnly, SameSite=Lax, Secure in production
- Gracefully disabled when `DISCORD_CLIENT_ID` is not configured

### Auth gates
- Web pages: Use `OptionalUser` — all pages work without auth, some features require enrollment check
- Admin pages: Redirect to login if OAuth enabled + not logged in. `admin_roster` also checks `PINWHEEL_ADMIN_DISCORD_ID`.
- API endpoints: Currently use governor_id in body (hackathon shortcut)
- Discord commands: Governor auth via `get_governor()` which raises `GovernorNotFound` if not enrolled

---

## 9. Key Design Decisions

1. **Thin route handlers, logic in core/** — API route handlers do minimal work. Governance logic in `core/governance.py`, token logic in `core/tokens.py`, simulation in `core/simulation.py`.

2. **Pydantic request/response models** — Explicit request models for governance, pace, seasons. Most other endpoints return ad-hoc dicts with `{data: ...}` convention.

3. **Repository pattern for DB access** — `Repository` class wraps all database queries. Route handlers never import SQLAlchemy directly.

4. **Event bus for cross-cutting notifications** — Fire-and-forget in-memory async pub/sub. 13+ event types.

5. **SSE for real-time web updates** — Single SSE endpoint with optional event type filtering. Heartbeat mechanism for proxy compatibility.

6. **Discord bot runs in-process** — Shares FastAPI's event loop. No separate process or message queue.

7. **Mirrors module is dead code** — `mirrors.py` exists but is not registered in `main.py`. Rename artifact.

8. **Graceful degradation** — OAuth, Discord bot, AI interpretation each degrade independently when credentials absent.

9. **Inline migrations** — `_add_column_if_missing()` handles schema evolution without Alembic.

10. **Presentation state** — `PresentationState` object on `app.state` tracks live game replay progress. Exposed via `/api/pace/status`.

---

## Files to Reference

| File | Purpose |
|------|---------|
| `main.py` | Router registration, middleware, lifespan startup/shutdown |
| `api/deps.py` | Dependency injection helpers |
| `api/events.py` | SSE event streaming |
| `api/governance.py` | Most complex API domain — interpretation + injection classification |
| `api/pages.py` | Largest router — 18+ page routes, HTMX patterns |
| `discord/bot.py` | 15 slash commands, event bus listener, server setup |
| `discord/views.py` | 7 interactive views/modals |
| `auth/oauth.py` | OAuth2 flow + session management |
| `core/event_bus.py` | In-memory pub/sub |

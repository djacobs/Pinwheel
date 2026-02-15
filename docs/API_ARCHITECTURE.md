# API Architecture

Reference documentation for all Pinwheel Fates API endpoints, page routes, SSE streaming, and authentication.

**Last updated:** 2026-02-15

---

## Overview

Pinwheel Fates uses FastAPI as its HTTP framework. The application is created via `create_app()` in `src/pinwheel/main.py`, which registers 15 routers and a health check endpoint. Routes are organized by domain: REST API endpoints serve JSON, page routes serve server-rendered HTML via Jinja2 templates, and SSE endpoints provide real-time event streaming.

### App Factory

`create_app()` creates a `FastAPI` instance with:
- Title: "Pinwheel Fates", Version: "0.1.0"
- OpenAPI docs at `/docs` in development, disabled in production
- Lifespan context manager for startup (engine, tables, auto-migration, EventBus, PresentationState, Discord bot, APScheduler) and shutdown

### Static Files

Mounted at `/static` from the project root's `static/` directory. Serves CSS and HTMX JavaScript with no build step.

### Health Check

```
GET /health  ->  {"status": "ok", "env": "<pinwheel_env>"}
```

---

## Dependency Injection

**File:** `src/pinwheel/api/deps.py`

| Dependency | Type | Description |
|-----------|------|-------------|
| `get_engine(request)` | `AsyncEngine` | Extracts database engine from `request.app.state.engine` |
| `get_session(engine)` | `AsyncSession` | Yields a session with auto-commit on success, rollback on error |
| `get_repo(session)` | `Repository` | Returns a `Repository` instance bound to the session |
| `RepoDep` | `Annotated[Repository, Depends(get_repo)]` | Standard alias used by all route handlers |

**File:** `src/pinwheel/auth/deps.py`

| Dependency | Type | Description |
|-----------|------|-------------|
| `get_current_user(request)` | `SessionUser | None` | Extracts user from signed session cookie |
| `OptionalUser` | `Annotated[SessionUser | None, ...]` | Standard alias for pages that work with or without auth |

### The Thin-Handler Pattern

All route handlers follow the same pattern: extract dependencies via FastAPI's DI system, delegate to `core/` domain logic or `Repository` data access, and return results. Route handlers never contain business logic, never import SQLAlchemy directly, and never call the Anthropic API directly. The one exception is `api/governance.py::api_submit_proposal`, which orchestrates the injection classification + AI interpretation flow because it requires access to `request.app.state.settings` for the API key.

---

## REST API Endpoints

### Games Router

**File:** `src/pinwheel/api/games.py`
**Prefix:** `/api/games`

| Method | Path | Description | Auth | Parameters |
|--------|------|-------------|------|------------|
| GET | `/api/games/playoffs/bracket` | Structured playoff bracket data | Public | -- |
| GET | `/api/games/{game_id}` | Single game result | Public | `game_id` (path) |
| GET | `/api/games/{game_id}/boxscore` | Box scores for a game | Public | `game_id` (path) |

**GET /api/games/{game_id}** returns:
```json
{
  "data": {
    "id": "...",
    "home_team_id": "...",
    "away_team_id": "...",
    "home_score": 45,
    "away_score": 42,
    "winner_team_id": "...",
    "total_possessions": 87,
    "elam_target": 50,
    "quarter_scores": [...],
    "seed": 12345
  }
}
```

**GET /api/games/{game_id}/boxscore** returns:
```json
{
  "data": [
    {
      "hooper_id": "...",
      "team_id": "...",
      "points": 18,
      "field_goals_made": 7,
      "field_goals_attempted": 14,
      "three_pointers_made": 2,
      "three_pointers_attempted": 5,
      "assists": 3,
      "steals": 1,
      "turnovers": 2
    }
  ]
}
```

**GET /api/games/playoffs/bracket** returns structured bracket data with semifinals, finals, series records, seedings, and champion information. Used by both the API and the `/playoffs` page route.

---

### Teams Router

**File:** `src/pinwheel/api/teams.py`
**Prefix:** `/api/teams`

| Method | Path | Description | Auth | Parameters |
|--------|------|-------------|------|------------|
| GET | `/api/teams` | List all teams for a season | Public | `season_id` (query, required) |
| GET | `/api/teams/{team_id}` | Single team with hoopers | Public | `team_id` (path) |

---

### Standings Router

**File:** `src/pinwheel/api/standings.py`
**Prefix:** `/api`

| Method | Path | Description | Auth | Parameters |
|--------|------|-------------|------|------------|
| GET | `/api/standings` | Current standings for a season | Public | `season_id` (query, required) |

Returns wins, losses, points for, points against per team, enriched with team names.

---

### Governance Router

**File:** `src/pinwheel/api/governance.py`
**Prefix:** `/api/governance`

| Method | Path | Description | Auth | Parameters |
|--------|------|-------------|------|------------|
| POST | `/api/governance/proposals` | Submit a governance proposal | Body: `governor_id` | `SubmitProposalRequest` body |
| POST | `/api/governance/proposals/{proposal_id}/confirm` | Confirm AI interpretation | Public | `proposal_id` (path) |
| POST | `/api/governance/votes` | Cast a vote on a proposal | Body: `governor_id` | `CastVoteRequest` body |
| GET | `/api/governance/proposals` | List all proposals for a season | Public | `season_id` (query) |
| GET | `/api/governance/rules/current` | Current ruleset with changes | Public | `season_id` (query) |
| GET | `/api/governance/rules/history` | All enacted rule changes | Public | `season_id` (query) |

**Request Models:**

```python
class SubmitProposalRequest(BaseModel):
    governor_id: str
    team_id: str
    season_id: str
    window_id: str
    raw_text: str

class CastVoteRequest(BaseModel):
    proposal_id: str
    governor_id: str
    team_id: str
    vote: str            # "yes" or "no"
    active_governors_on_team: int = 1
    boost_used: bool = False
```

**Proposal submission flow:** If `ANTHROPIC_API_KEY` is set, runs injection classification (Haiku) before AI interpretation (Sonnet). If the classifier detects injection with >0.8 confidence, the proposal is flagged with `injection_flagged=True` and `confidence=0.0`. Otherwise falls back to `interpret_proposal_mock()`.

**Note:** Governance endpoints accept `governor_id` in the request body rather than requiring auth headers. This is a hackathon shortcut -- in production these should verify the requester is the governor.

---

### Reports Router

**File:** `src/pinwheel/api/reports.py`
**Prefix:** `/api/reports`

| Method | Path | Description | Auth | Parameters |
|--------|------|-------------|------|------------|
| GET | `/api/reports/round/{season_id}/{round_number}` | Public reports for a round | Public | `report_type` (query, optional) |
| GET | `/api/reports/private/{season_id}/{governor_id}` | Private reports for a governor | Trusted param | `round_number` (query, optional) |
| GET | `/api/reports/latest/{season_id}` | Most recent sim + gov reports | Public | -- |

Private reports are filtered out of the `/round/` endpoint. The `/private/` endpoint trusts the `governor_id` parameter (hackathon shortcut -- production should verify identity).

---

### Seasons Router

**File:** `src/pinwheel/api/seasons.py`
**Prefix:** `/api/seasons`

| Method | Path | Description | Auth | Parameters |
|--------|------|-------------|------|------------|
| POST | `/api/seasons` | Create a new season | Should be admin-gated | `CreateSeasonRequest` body |

**Request Model:**
```python
class CreateSeasonRequest(BaseModel):
    league_id: str
    name: str
    carry_forward_rules: bool = True
    previous_season_id: str | None = None
```

Delegates to `core/season.py::start_new_season()` which creates teams, hoopers, schedule, and initial ruleset.

---

### Pace Router

**File:** `src/pinwheel/api/pace.py`
**Prefix:** `/api/pace`

| Method | Path | Description | Auth | Parameters |
|--------|------|-------------|------|------------|
| GET | `/api/pace` | Current presentation pace | Public | -- |
| POST | `/api/pace` | Change pace (in-memory only) | Public | `PaceRequest` body |
| POST | `/api/pace/advance` | Trigger manual round advance | Public | `quarter_seconds` (query), `game_gap_seconds` (query) |
| GET | `/api/pace/status` | Presentation state | Public | -- |

Valid paces: `fast` (1min cron), `normal` (5min), `slow` (15min), `manual` (no cron). The `/advance` endpoint returns 409 if a presentation is already active.

---

### Events Router (SSE)

**File:** `src/pinwheel/api/events.py`
**Prefix:** `/api/events`

| Method | Path | Description | Auth | Parameters |
|--------|------|-------------|------|------------|
| GET | `/api/events/stream` | SSE event stream | Public | `event_type` (query, optional filter) |
| GET | `/api/events/health` | EventBus health check | Public | -- |

**SSE Protocol:**
- Returns `text/event-stream` with `Cache-Control: no-cache`, `Connection: keep-alive`, `X-Accel-Buffering: no`
- Initial flush comment `: connected\n\n` transitions browsers from "connecting" to "open"
- Heartbeat every 15 seconds: `: heartbeat\n\n`
- Events formatted as: `event: <type>\ndata: <json>\n\n`
- If `event_type` query param is set, only matching events are delivered

**Event types published:**

| Event Type | Source | Description |
|-----------|--------|-------------|
| `game.completed` | `game_loop.py` | A game simulation finished |
| `round.completed` | `game_loop.py` | All games in a round finished |
| `report.generated` | `scheduler_runner.py` | An AI report was stored |
| `governance.window_closed` | `scheduler_runner.py` | Governance tally completed |
| `presentation.game_starting` | `presenter.py` | Replay beginning for a game |
| `presentation.possession` | `presenter.py` | Play-by-play event during replay |
| `presentation.game_finished` | `presenter.py` | Game replay completed |
| `presentation.round_finished` | `presenter.py` | Round replay completed |
| `season.regular_season_complete` | `game_loop.py` | Regular season ended |
| `season.tiebreaker_games_generated` | `season.py` | Tiebreaker games scheduled |
| `season.phase_changed` | `season.py` | Season transitioned phase |
| `season.semifinals_complete` | `game_loop.py` | Semi series decided |
| `season.playoffs_complete` | `game_loop.py` | Champion determined |
| `season.championship_started` | `season.py` | Championship phase entered |
| `season.offseason_started` | `season.py` | Offseason governance window opened |
| `season.offseason_closed` | `season.py` | Offseason window closed |

---

## Admin Endpoints

All admin routes are under the `/admin` prefix. In production they require OAuth login and `PINWHEEL_ADMIN_DISCORD_ID` match. In development without OAuth credentials they are accessible for local testing.

### Eval Dashboard

**File:** `src/pinwheel/api/eval_dashboard.py`

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| GET | `/admin/evals` | Eval dashboard -- aggregate stats | Login required (OAuth) |

Supports `?round=N` query param for round-specific filtering. Displays grounding rate, prescriptive flags, behavioral impact, rubric summary, golden dataset pass rate, A/B win rates, GQI trend, active scenario flags, rule evaluation, injection classifications, and a traffic-light safety summary. **No individual report text is ever shown.**

### Admin Season

**File:** `src/pinwheel/api/admin_season.py`

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| GET | `/admin/season` | Season dashboard -- config, history, new season form | Admin |

### Admin Roster

**File:** `src/pinwheel/api/admin_roster.py`

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| GET | `/admin/roster` | All governors with tokens, proposals, votes | Admin |

### Admin Review

**File:** `src/pinwheel/api/admin_review.py`

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| GET | `/admin/review` | Proposal review queue -- flagged proposals | Admin |

Shows proposals flagged for review (Tier 5+ or confidence < 0.5) with pending/resolved status. Also displays injection-flagged proposals.

### Admin Workbench

**File:** `src/pinwheel/api/admin_workbench.py`

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| GET | `/admin/workbench` | Safety tooling -- classifier test bench | Admin |
| POST | `/admin/workbench/test-classifier` | Test injection classifier with text | Admin |

Displays the defense stack (sanitization, classifier, sandboxed interpreter, Pydantic validation, human-in-the-loop, admin review) and provides sample proposals for testing.

### Admin Costs

**File:** `src/pinwheel/api/admin_costs.py`

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| GET | `/admin/costs` | AI API usage -- tokens, cost, per-round trends | Admin |

Aggregates from the `ai_usage_log` table: total calls, total tokens (input/output/cache), estimated cost, per-caller breakdown, per-round breakdown, average cost per round, cache hit rate, and pricing reference.

---

## Page Routes (Jinja2 HTML)

**File:** `src/pinwheel/api/pages.py`
**Prefix:** none (root)

### Public Pages

| Method | Path | Template | Description |
|--------|------|----------|-------------|
| GET | `/` | `pages/home.html` | Home dashboard -- standings, latest games, report, upcoming |
| GET | `/play` | `pages/play.html` | How to Play -- onboarding, ruleset summary, pace info |
| GET | `/arena` | `pages/arena.html` | Arena -- recent rounds, live game zone, upcoming |
| GET | `/standings` | `pages/standings.html` | Standings with streaks |
| GET | `/games/{game_id}` | `pages/game.html` | Game detail -- box scores, narrated play-by-play |
| GET | `/teams/{team_id}` | `pages/team.html` | Team profile -- hoopers (spider charts), governors, strategy |
| GET | `/hoopers/{hooper_id}` | `pages/hooper.html` | Hooper profile -- spider chart, game log, season averages |
| GET | `/hoopers/{hooper_id}/bio/edit` | (inline fragment) | HTMX fragment: bio edit form (governor-only) |
| GET | `/hoopers/{hooper_id}/bio/view` | (inline fragment) | HTMX fragment: bio display |
| POST | `/hoopers/{hooper_id}/bio` | (inline fragment) | Update hooper bio (governor-only) |
| GET | `/governors/{player_id}` | `pages/governor.html` | Governor profile -- governance record, activity |
| GET | `/governance` | `pages/governance.html` | Governance audit trail -- proposals, outcomes, votes |
| GET | `/rules` | `pages/rules.html` | Current rules -- tiered display, changes from default |
| GET | `/reports` | `pages/reports.html` | Reports archive (excludes private reports) |
| GET | `/playoffs` | `pages/playoffs.html` | Playoff bracket visualization |
| GET | `/seasons/archive` | `pages/season_archive.html` | List all archived seasons |
| GET | `/seasons/archive/{season_id}` | `pages/season_archive.html` | Specific season archive |
| GET | `/history` | `pages/history.html` | Hall of History -- championship banners |
| GET | `/seasons/{season_id}/memorial` | `pages/memorial.html` | Full memorial page for a completed season |
| GET | `/admin` | `pages/admin.html` | Admin landing page (admin-only) |
| GET | `/terms` | `pages/terms.html` | Terms of Service |
| GET | `/privacy` | `pages/privacy.html` | Privacy Policy |

### Template Patterns

- **Base template:** `templates/base.html` provides navigation, auth state, and global styles
- **Auth context:** `_auth_context()` is injected into every page with `current_user`, `oauth_enabled`, `pinwheel_env`, `app_version`, `discord_invite_url`, `is_admin`
- **HTMX patterns:** Bio edit/view uses `hx-get`, `hx-post`, `hx-target`, `hx-swap="innerHTML"` for inline editing
- **Spider charts:** `api/charts.py` provides pure SVG geometry functions for 9-attribute radar charts (scoring, passing, defense, speed, stamina, iq, ego, chaotic_alignment, fate)
- **No JavaScript build step:** HTMX loaded from static files, all interactivity via HTMX attributes or inline `<script>` blocks

---

## Authentication

**File:** `src/pinwheel/auth/oauth.py`

### Discord OAuth2 Flow

| Method | Path | Description |
|--------|------|-------------|
| GET | `/auth/login` | Redirects to Discord OAuth2 consent page (`identify` scope) |
| GET | `/auth/callback` | Exchanges code for token, fetches user profile, creates session |
| GET | `/auth/logout` | Clears session cookie |

### Session Management

- **Cookie name:** `pinwheel_session`
- **Max age:** 7 days
- **Signing:** `URLSafeTimedSerializer` from `itsdangerous` using `SESSION_SECRET_KEY`
- **Payload:** `{discord_id, username, avatar_url}`
- **Flags:** HttpOnly, SameSite=Lax, Secure in production
- **CSRF protection:** `pinwheel_oauth_state` cookie (300s TTL) for OAuth flow

### Auth Gates

| Context | Mechanism | Behavior |
|---------|-----------|----------|
| Web pages | `OptionalUser` | Pages work without auth; some features check enrollment |
| Admin pages | `is_admin` check | Redirect to login if OAuth enabled + not logged in; 403 if not admin |
| REST API endpoints | `governor_id` in body | Hackathon shortcut -- should verify identity in production |
| Discord commands | `get_governor()` | Raises `GovernorNotFound` if not enrolled |

**Graceful degradation:** When `DISCORD_CLIENT_ID` is not configured, OAuth is disabled entirely. Pages render without auth features. Admin pages are accessible in dev mode for local testing.

---

## Router Registration Order

Routers are registered in `create_app()` in this order:

1. `auth_router` -- `/auth`
2. `games_router` -- `/api/games`
3. `teams_router` -- `/api/teams`
4. `standings_router` -- `/api`
5. `governance_router` -- `/api/governance`
6. `reports_router` -- `/api/reports`
7. `events_router` -- `/api/events`
8. `eval_dashboard_router` -- `/admin`
9. `admin_review_router` -- `/admin`
10. `admin_roster_router` -- `/admin`
11. `admin_season_router` -- `/admin`
12. `admin_workbench_router` -- `/admin`
13. `admin_costs_router` -- `/admin`
14. `pace_router` -- `/api/pace`
15. `seasons_router` -- `/api/seasons`
16. `pages_router` -- no prefix (must come last so `/api/` paths match first)

### Unregistered Module

`src/pinwheel/api/mirrors.py` exists in the codebase but is NOT included in `main.py`. It mirrors the Reports router structure but uses `mirror_type` / `get_mirrors_for_round` / `get_private_mirrors` / `get_latest_mirror` methods. This is dead code.

---

## Source Files

| File | Purpose |
|------|---------|
| `src/pinwheel/main.py` | App factory, router registration, lifespan startup/shutdown |
| `src/pinwheel/api/deps.py` | Dependency injection helpers (engine, session, repo) |
| `src/pinwheel/api/games.py` | Game results, box scores, playoff bracket |
| `src/pinwheel/api/teams.py` | Team and hooper listing |
| `src/pinwheel/api/standings.py` | League standings |
| `src/pinwheel/api/governance.py` | Proposals, votes, rules (most complex API domain) |
| `src/pinwheel/api/reports.py` | AI report retrieval with access control |
| `src/pinwheel/api/events.py` | SSE event streaming |
| `src/pinwheel/api/pace.py` | Presentation pacing control |
| `src/pinwheel/api/seasons.py` | Season creation |
| `src/pinwheel/api/charts.py` | Spider chart SVG geometry (pure functions) |
| `src/pinwheel/api/eval_dashboard.py` | Eval dashboard (admin) |
| `src/pinwheel/api/admin_season.py` | Season management (admin) |
| `src/pinwheel/api/admin_roster.py` | Governor roster (admin) |
| `src/pinwheel/api/admin_review.py` | Proposal review queue (admin) |
| `src/pinwheel/api/admin_workbench.py` | Safety workbench (admin) |
| `src/pinwheel/api/admin_costs.py` | AI cost dashboard (admin) |
| `src/pinwheel/api/mirrors.py` | Dead code (not registered) |
| `src/pinwheel/api/pages.py` | Largest router -- 22+ page routes, HTMX patterns |
| `src/pinwheel/auth/oauth.py` | OAuth2 flow + session management |
| `src/pinwheel/auth/deps.py` | Auth dependency injection |

---
title: "feat: Frontend Architecture"
type: feat
date: 2026-02-11
---

> **Historical snapshot.** The frontend is fully implemented — HTMX + SSE + Jinja2 dashboard with 10+ pages. See DEV_LOG for completion state.

# Frontend Architecture

## Overview

HTMX + SSE + Jinja2 templates. No JS build step. The aesthetic is retro, bold, community-focused, slightly unhinged — joyful chaos. Full CSS control via Jinja2 templates. FastAPI serves HTML directly.

## Stack

- **Jinja2** for server-side rendering (FastAPI native support)
- **HTMX** for dynamic updates without full page reloads
- **SSE** (via HTMX `hx-ext="sse"`) for real-time game/governance/report updates
- **CSS** — hand-written, no framework. Full aesthetic control required.
- **No JavaScript build step.** HTMX and any small utility scripts served as static files.

## Template Structure

```
templates/
├── base.html                 # Base layout: nav, footer, SSE connection
├── components/
│   ├── nav.html              # Navigation bar (standings ticker, active games indicator)
│   ├── game_card.html        # Single game panel (used in Arena and listings)
│   ├── possession.html       # Single possession display (play-by-play line)
│   ├── commentary.html       # Commentary line with energy styling
│   ├── box_score.html        # Box score table
│   ├── agent_card.html       # Agent profile card (attributes radar, moves)
│   ├── proposal_card.html    # Governance proposal with interpretation
│   ├── vote_widget.html      # Vote yes/no/boost controls
│   ├── token_balance.html    # Token balance display
│   ├── report_card.html      # Report reflection display
│   ├── standings_table.html  # League standings table
│   └── rule_change.html      # Rule change diff display
├── pages/
│   ├── arena.html            # The Arena: 2x2 live game grid
│   ├── game.html             # Single game deep dive
│   ├── standings.html        # Full standings page
│   ├── team.html             # Team profile page
│   ├── agent.html            # Agent profile page
│   ├── governance.html       # Active proposals, voting, history
│   ├── rules.html            # Current ruleset + change timeline
│   ├── reports.html          # Report archive
│   ├── season.html           # Season history / narrative
│   └── login.html            # Discord OAuth login
├── admin/
│   ├── dashboard.html        # Admin performance dashboard
│   └── season_setup.html     # Season creation / management
└── errors/
    ├── 404.html
    └── 500.html

static/
├── css/
│   ├── pinwheel.css          # Main stylesheet
│   ├── arena.css             # Arena-specific styles
│   ├── game.css              # Game view styles
│   └── governance.css        # Governance panel styles
├── js/
│   └── htmx.min.js           # HTMX library (single file, ~14KB gzipped)
└── assets/
    ├── fonts/                 # Retro/bold typefaces
    └── icons/                 # Game icons, team logos
```

## HTMX Patterns

### Pattern 1: SSE-Driven Live Updates

The Arena and game views use HTMX's SSE extension to receive real-time updates:

```html
<!-- Arena: 2x2 game grid with SSE updates -->
<div hx-ext="sse" sse-connect="/api/events/stream?games=true&commentary=true">
  <div class="arena-grid">
    <!-- Game 1 -->
    <div id="game-{{ game1.id }}" class="game-panel"
         sse-swap="game.possession"
         hx-swap="innerHTML"
         hx-target="find .play-by-play">
      {% include 'components/game_card.html' %}
    </div>
    <!-- Game 2, 3, 4 ... -->
  </div>
</div>
```

HTMX SSE extension listens for named events and swaps HTML fragments into the correct targets. The server sends pre-rendered HTML fragments (not JSON) — the frontend never parses data.

### Pattern 2: Server-Rendered HTML Fragments

API endpoints return HTML fragments for HTMX, JSON for API clients:

```python
@router.get("/games/{game_id}/possession/{index}")
async def get_possession(game_id: str, index: int, request: Request):
    possession = await repository.get_possession(game_id, index)
    if "text/html" in request.headers.get("accept", ""):
        # HTMX request — return HTML fragment
        return templates.TemplateResponse(
            "components/possession.html",
            {"request": request, "possession": possession},
        )
    # API request — return JSON
    return possession
```

### Pattern 3: Partial Page Updates

Navigation between pages uses HTMX to swap the main content area without full page reloads:

```html
<!-- base.html -->
<nav>
  <a hx-get="/arena" hx-target="#main" hx-push-url="true">Arena</a>
  <a hx-get="/standings" hx-target="#main" hx-push-url="true">Standings</a>
  <a hx-get="/governance" hx-target="#main" hx-push-url="true">Governance</a>
  <a hx-get="/reports" hx-target="#main" hx-push-url="true">Reports</a>
</nav>
<main id="main">
  {% block content %}{% endblock %}
</main>
```

Pages serve full HTML on direct load, partial HTML (just the content block) when requested via HTMX (`HX-Request: true` header).

### Pattern 4: Polling for Non-SSE Updates

Some data (standings, token balances) updates less frequently. Use HTMX polling:

```html
<!-- Poll standings every 30 seconds -->
<div hx-get="/api/standings/fragment"
     hx-trigger="every 30s"
     hx-swap="outerHTML">
  {% include 'components/standings_table.html' %}
</div>
```

### Pattern 5: Form Submissions (Governance)

Governance actions on the web dashboard (for logged-in governors):

```html
<!-- Proposal submission -->
<form hx-post="/api/governance/proposals"
      hx-target="#proposal-result"
      hx-swap="innerHTML">
  <textarea name="proposal_text" placeholder="Propose a rule change..."></textarea>
  <button type="submit">Submit Proposal</button>
</form>
<div id="proposal-result"></div>
```

The response is an HTML fragment showing the AI interpretation with confirm/revise/cancel buttons.

## SSE Event → HTML Fragment

The SSE endpoint for HTMX sends pre-rendered HTML:

```python
async def sse_html_stream(request: Request, event_bus: EventBus):
    """SSE endpoint that sends HTML fragments for HTMX."""
    async def generate():
        async for event in event_bus.subscribe():
            # Render the event as an HTML fragment
            html = templates.get_template(
                f"components/{event.event_type.replace('.', '_')}.html"
            ).render(event=event.data)

            # SSE format with event name for HTMX sse-swap
            yield f"event: {event.event_type}\ndata: {html}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")
```

This means the server renders HTML and the client just inserts it. Zero client-side rendering logic.

## Visual Design Direction

### Visual Aesthetic

- **Typography:** Bold, slightly condensed sans-serif for headers. Monospace for stats and scores. A display font for dramatic moments.
- **Colors:** High contrast. Dark backgrounds, bright accent colors per team. Neon-ish highlights for active states.
- **Layout:** Dense but organized. Information-rich without clutter. Scoreboard aesthetic — numbers are prominent.
- **Animation:** Minimal, purposeful. Score changes flash. Highlights pulse. Elam countdown throbs. No gratuitous animation.
- **Tone:** Retro sports broadcast meets internet culture. Bold, confident, slightly weird.

### Color System

```css
:root {
  --bg-primary: #1a1a2e;        /* Deep navy, almost black */
  --bg-secondary: #16213e;       /* Slightly lighter navy */
  --bg-card: #0f3460;            /* Card/panel background */
  --text-primary: #e8e8e8;       /* Light gray, high contrast */
  --text-secondary: #a8a8b8;     /* Muted gray */
  --accent-score: #f0c040;       /* Gold for scores */
  --accent-highlight: #e94560;   /* Hot pink for highlights/alerts */
  --accent-governance: #53d8fb;  /* Cyan for governance elements */
  --accent-report: #b794f4;      /* Purple for reports/AI */
  --accent-success: #48bb78;     /* Green for passed proposals */
  --accent-danger: #fc5c65;      /* Red for failed/ejections */

  /* Team colors override accent for team-specific elements */
}
```

### Key Visual Elements

**Arena game panels:**
```
┌─────────────────────────────┐
│ ⬤ THORNS  42    WOLVES  39  │  ← Team colors on each side
│ Q3 — Poss 11/15             │
│──────────────────────────────│
│ ▸ Nakamura from 25 feet...  │  ← Play-by-play, monospace
│   BANG! 🔥                   │  ← Highlight styling
│──────────────────────────────│
│ 🎙️ "She had no business     │  ← Commentary, italic, --accent-report
│    taking that shot."        │
└─────────────────────────────┘
```

**Elam mode transforms the panel:**
```
┌═══════════════════════════════┐
║ ★ ELAM ENDING ★              ║  ← Pulsing border, gold accent
║ TARGET: 58                    ║
║                               ║
║ THORNS  51  ████████░░ 7 away ║  ← Progress bars to target
║ FOXES   44  ████░░░░░ 14 away ║
║                               ║
║ ▸ Every. Possession. Matters. ║
└═══════════════════════════════┘
```

## FastAPI Integration

```python
# main.py
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# Page routes (return full pages or HTMX fragments)
@app.get("/arena")
async def arena_page(request: Request):
    live_games = await repository.get_live_games()
    template = "pages/arena.html"
    context = {"request": request, "games": live_games}

    if request.headers.get("HX-Request"):
        # HTMX partial update — return just the content block
        return templates.TemplateResponse(
            "pages/arena.html",
            context,
            block_name="content",  # Jinja2 block rendering
        )
    # Full page load
    return templates.TemplateResponse(template, context)
```

## Discord OAuth

For personalized dashboard (private reports, team highlighting):

```python
# api/auth.py
@router.get("/auth/discord")
async def discord_login():
    return RedirectResponse(
        f"https://discord.com/api/oauth2/authorize"
        f"?client_id={DISCORD_CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&response_type=code"
        f"&scope=identify guilds.members.read"
    )

@router.get("/auth/callback")
async def discord_callback(code: str):
    # Exchange code for token
    # Get user info from Discord
    # Map to governor
    # Set session cookie
    ...
```

Logged-in governors see: their team highlighted in standings, their private report on the dashboard, their governance history, and the proposal submission form.

## Implementation Priority

1. **Base template + static files** — Layout, CSS variables, HTMX loaded, nav
2. **Standings page** — Simple server-rendered table. First HTMX interaction (polling refresh).
3. **Arena page** — 2x2 game grid with SSE connection. The showcase page.
4. **Single game page** — Full play-by-play, box score, commentary, rule context.
5. **Governance page** — Active proposals, voting (if logged in), history.
6. **Team/agent pages** — Roster, stats, venue info.
7. **Reports page** — Report archive with type filtering.
8. **Rules page** — Current ruleset with change timeline.
9. **Discord OAuth** — Login, session, personalization.
10. **Admin dashboard** — Performance metrics (INSTRUMENTATION.md).

## Acceptance Criteria

- [ ] Arena shows 4 live games updating via SSE
- [ ] Single game view shows play-by-play, box score, commentary, rule context
- [ ] Standings page updates without full page reload
- [ ] Governance page shows proposals with AI interpretations
- [ ] Logged-in governors can submit proposals and vote from the web
- [ ] Private reports visible only to authenticated governor
- [ ] CSS achieves retro sports broadcast aesthetic (dark, bold, community-focused)
- [ ] Works without JavaScript beyond HTMX (progressive enhancement)
- [ ] Pages load in < 200ms server-side render time
- [ ] SSE reconnection handled gracefully (HTMX built-in)

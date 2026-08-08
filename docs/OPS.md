# Pinwheel Fates: Operations & Deployment

## Overview

Pinwheel Fates deploys to [Fly.io](https://fly.io) — a platform that runs Docker containers on hardware close to users. The deployment model is a single application process running the FastAPI server, the background game loop (APScheduler), and SSE streaming from one machine. SQLite runs on a persistent Fly volume (`/data`). The Discord bot connects outbound from the same process.

This architecture is deliberately simple for the hackathon. The single-process model avoids inter-process coordination, message queues, and distributed state. If load demands it post-hackathon, the game loop worker can be split into a separate process.

## Architecture

```
┌───────────────────────────────────────────┐
│             Fly.io Machine                │
│                                           │
│  ┌───────────────────────────────────┐    │
│  │ FastAPI Process                   │    │
│  │                                   │    │
│  │  ├── HTTP API (uvicorn)           │    │
│  │  ├── SSE Streaming (/events)      │    │
│  │  ├── APScheduler (game loop)      │    │
│  │  ├── Discord Bot (outbound WS)    │    │
│  │  └── AI Client (outbound HTTPS)   │    │
│  └───────────────┬───────────────────┘    │
│                  │                         │
│  ┌───────────────▼───────────────────┐    │
│  │ SQLite (persistent volume /data)  │    │
│  │  └── pinwheel.db                  │    │
│  └───────────────────────────────────┘    │
└───────────────────────────────────────────┘
         │                    │
         ▼                    ▼
  ┌──────────┐      ┌──────────────────┐
  │ Discord  │      │ Anthropic API    │
  │ Gateway  │      │ (Opus 4.6)       │
  └──────────┘      └──────────────────┘
```

## Fly.io Configuration

The `fly.toml` at the project root defines the deployment. Key decisions:

**Machine size:** `shared-cpu-2x` with 1 GB RAM for hackathon. The simulation engine is CPU-bound but fast (single game < 100ms). The SSE connections and AI calls are I/O-bound. 1 GB is sufficient for 8 teams, ~50 concurrent SSE connections, and the APScheduler process.

**Region:** `sea` (Seattle) — closest to Portland, lowest latency for the team and for Anthropic's API endpoints.

**Scaling:** Single machine, no autoscaling during the hackathon. If the demo gets traffic, scale to 2 machines behind Fly's built-in load balancer — but SSE connections are stateful, so sticky sessions would be needed. Cross that bridge if needed.

**Health checks:** `/health` endpoint returns 200 when the API is ready and the database is reachable. Fly restarts the machine if health checks fail.

## Database

SQLite on a persistent Fly volume mounted at `/data`. The database file lives at `/data/pinwheel.db`. WAL journal mode is enabled for concurrent read access while the single writer (game loop, Discord commands, web requests) serializes writes.

**Local dev:** SQLite (`sqlite+aiosqlite:///pinwheel.db`) for zero-config local development. Same engine, same queries, same database.

**Backup:**

```bash
# Copy the database file on the Fly machine
fly ssh console -C "cp /data/pinwheel.db /data/pinwheel.db.bak"
```

## Environment Variables

Set via `fly secrets`:

```bash
fly secrets set ANTHROPIC_API_KEY=sk-ant-...
fly secrets set DISCORD_BOT_TOKEN=...
fly secrets set DISCORD_GUILD_ID=...
fly secrets set PINWHEEL_ENV=production
fly secrets set PINWHEEL_GAME_CRON="0 * * * *"
fly secrets set PINWHEEL_GOV_WINDOW=1800
```

| Variable | Description | Default |
|----------|-------------|---------|
| `ANTHROPIC_API_KEY` | Claude API key for Opus 4.6 | (required) |
| `DATABASE_URL` | SQLite connection string | `sqlite+aiosqlite:///pinwheel.db` |
| `DISCORD_BOT_TOKEN` | Discord bot token | (required) |
| `DISCORD_GUILD_ID` | Discord server ID | (required) |
| `PINWHEEL_ENV` | `development` / `staging` / `production` | `development` |
| `PINWHEEL_PRESENTATION_PACE` | Pace mode: `fast`, `normal`, `slow`, `manual` | `fast` |
| `PINWHEEL_PRESENTATION_MODE` | Presentation mode: `instant`, `replay` | `instant` |
| `PINWHEEL_GAME_CRON` | Explicit cron override (optional, derived from pace) | (from pace) |
| `PINWHEEL_GOVERNANCE_INTERVAL` | Tally governance every N ticks | `1` |
| `PINWHEEL_RULES_REQUIRE_APPROVAL` | Hold every passing proposal for admin approval before enactment (see Admin Runbook) | `false` |
| `PINWHEEL_ADMIN_DISCORD_ID` | Discord user ID for the league admin — gates admin DMs, admin web pages, and admin slash commands | (unset) |
| `PINWHEEL_GOV_WINDOW` | Governance window duration (for GQI calculations) | `900` |
| `PINWHEEL_AUTO_ADVANCE` | APScheduler auto-advance toggle | `true` |
| `PINWHEEL_LOG_LEVEL` | Logging level | `INFO` |

## Deployment

### First Deploy

```bash
# Install Fly CLI
curl -L https://fly.io/install.sh | sh

# Authenticate
fly auth login

# Launch the app (creates the Fly app from fly.toml)
fly launch --no-deploy

# Set secrets
fly secrets set ANTHROPIC_API_KEY=sk-ant-... DISCORD_BOT_TOKEN=... DISCORD_GUILD_ID=...

# Deploy
fly deploy
```

The persistent volume for SQLite is configured in `fly.toml`. The database file is created automatically on first run.

### Subsequent Deploys

```bash
fly deploy
```

The Dockerfile builds the image, and Fly handles zero-downtime deployment by starting the new machine before stopping the old one.

### Rollback

```bash
fly releases
fly deploy --image <previous-image-ref>
```

## Dockerfile

The project uses a multi-stage Docker build:

```
Stage 1: builder
  - Python 3.12 slim base
  - Install build dependencies
  - pip install the project with production extras

Stage 2: runtime
  - Python 3.12 slim base (clean, no build tools)
  - Copy installed packages from builder
  - Copy application code
  - Run uvicorn
```

The Dockerfile should live at the project root. Fly.io builds it automatically on `fly deploy`.

## Monitoring & Observability

### Fly Dashboard

Fly provides built-in metrics for CPU, memory, network, and request latency. Access via `fly dashboard` or the Fly.io web console.

### Application Logs

```bash
# Tail live logs
fly logs

# Search logs
fly logs --app pinwheel-fates | grep "ERROR"
```

Structured logging via Python's `logging` module with JSON formatting in production. Log every simulation block completion, governance window open/close, report generation, and AI API call with duration and token count.

### Health Endpoint

`GET /health` returns:

```json
{
  "status": "ok",
  "database": "connected",
  "scheduler": "running",
  "discord": "connected",
  "last_simulation": "2026-02-11T14:00:00Z",
  "last_governance_window": "2026-02-11T13:30:00Z",
  "active_sse_connections": 12
}
```

### Alerts

For the hackathon, monitor manually via `fly logs` and the health endpoint. Post-hackathon, integrate with Fly's metrics API or an external service (Datadog, Sentry) for automated alerting on error rates, latency spikes, and process restarts.

## Cost Estimates (Hackathon)

| Resource | Fly Plan | Monthly Cost | Hackathon (5 days) |
|----------|----------|-------------|-------------------|
| App machine | shared-cpu-2x, 1 GB | ~$10/mo | ~$2 |
| Volume (1 GB) | Persistent SSD | ~$0.15/mo | ~$0.01 |
| Bandwidth | Included (first 100 GB) | $0 | $0 |
| **Total Fly** | | | **~$2** |

The real cost is Anthropic API usage — see INSTRUMENTATION.md for token cost estimates (~76K-124K tokens/day for 12-24 players).

## SSE Scaling Considerations

SSE connections are long-lived HTTP connections. Each connected client holds an open connection to the server. At hackathon scale (< 100 clients), this is trivially handled by a single machine.

If scaling beyond one machine post-hackathon, options:
- **Fly.io Replay Header:** Fly supports sticky sessions via the `fly-replay` header. Route SSE connections to the same machine.
- **Redis Pub/Sub:** Decouple event production from SSE delivery. The game loop publishes events to Redis; each machine's SSE handler subscribes and forwards to its connected clients. This eliminates sticky session requirements.
- **Fly Machines API:** Spin up dedicated SSE-serving machines separate from the API/game-loop machine.

For the hackathon, none of this is needed. One machine, one process, direct SSE from FastAPI.

## Discord Bot Deployment

The Discord bot runs inside the same FastAPI process — it's not a separate service. The bot connects to the Discord Gateway via WebSocket on startup and stays connected for the lifetime of the process.

If the Fly machine restarts (deploy, crash, health check failure), the bot reconnects automatically. Discord's Gateway handles reconnection gracefully — missed events during downtime are replayed via the bot's event resume mechanism.

Slash command registration happens automatically on startup: the bot's `setup_hook` (`src/pinwheel/discord/bot.py`) copies the command tree to the guild configured by `DISCORD_GUILD_ID` and syncs it with Discord's API. There is no separate registration script. The full command list lives in `CLAUDE.md` and `docs/product/RUN_OF_PLAY.md`.

## Admin Runbook — Governance

Day-to-day governance operations for the league admin. Every admin surface — DM notifications, admin web pages in production, and the admin slash commands below — checks your Discord user ID against `PINWHEEL_ADMIN_DISCORD_ID`. If that variable is unset, admin DMs fall back to the guild owner, but the admin slash commands refuse to run.

### Approving a pending proposal (the approval gate)

With `PINWHEEL_RULES_REQUIRE_APPROVAL=true`, every passing proposal — all tiers — holds in a pending-admin state instead of enacting at tally. The tally records and announces the pass; the ruleset does not change until you act.

- **Approve** the pending proposal to enact it. The rule change applies from the next round.
- **Veto** it if you decline to enact. The proposer is notified.
- With the gate off (default), passed proposals enact at tally automatically and only wild-tier proposals come to you.

### Vetoing or clearing a wild proposal

Wild proposals (Tier 5, or AI confidence below 50%) DM you a **Clear** / **Veto** gate while voting proceeds in parallel:

- **Clear** — acknowledges review; voting continues; the proposer is told their proposal was cleared.
- **Veto** — kills the proposal (optional reason prompt) and refunds the proposer's PROPOSE token. Veto is a no-op if the proposal already passed and was enacted.
- The buttons expire after 24 hours; voting proceeds regardless. `/admin/review` on the web shows the same queue if you miss a DM.

### Codegen review commands

Codegen (Code Council) effects register in a `pending` state — the generated code never runs until you approve it. While pending or rejected, the AI's interpreted approximation of the proposal is what's live.

| Command | What it does |
|---------|-------------|
| `/review-codegen` | Lists pending codegen effects and re-attaches the Approve/Reject gate — the recovery path if you missed the DM. |
| `/rerun-council EFFECT` | Flags a codegen effect for council re-review (`effect.council_rerun_requested` event); the council pipeline re-evaluates it on the next governance tick. |
| `/disable-effect EFFECT` | Kill switch. Immediately disables a codegen effect (`codegen_enabled=False`, persisted via `effect.codegen_disabled` event). |
| `/activate-mechanic EFFECT [hook_point action_type modifier]` | Upgrades a pending `custom_mechanic` placeholder into a real hook implementation, or confirms the approximation as sufficient. |

**Approve** makes the generated code live and retires the placeholder approximation. **Reject** (with optional reason) keeps the approximation in effect. If you never act, the effect stays pending indefinitely — approximation live, code inert. Pending notifications are retried on later governance ticks until delivered; nothing auto-approves (`PINWHEEL_CODEGEN_AUTO_APPROVE` exists for dev/demo environments only).

### Codegen auto-disable — what it means and how to respond

The sandbox guards live games against bad generated code:

- **3 consecutive execution errors** auto-disable the effect (reason: "Auto-disabled after N errors"). The decision is made in-memory during a game and persisted afterward as an `effect.codegen_disabled` event.
- **250ms per-game compute budget** — an effect that exhausts it is skipped for the rest of that game (this alone does not disable it).

When you see `codegen_disabled` or repeated `codegen_execution_error` in the logs:

1. Check the stored `codegen_last_error` (visible via `/review-codegen` / logs) to see what broke.
2. `/rerun-council EFFECT` to send it back through generation and council review if the intent is worth keeping.
3. Leave it disabled (or let the Floor `/repeal` it) if it isn't.

The league keeps running either way — a disabled codegen effect simply stops firing.

### Backup before anything risky

```bash
flyctl ssh console -C "cp /data/pinwheel.db /data/pinwheel.db.bak"
```

Two seconds of insurance. Do this before vetoes-at-scale, schema experiments, `/new-season`, or any manual database surgery. Production has real players; see the LIVE DATA section of `CLAUDE.md`.

### Pace tradeoffs

Pace is switchable at runtime (`POST /api/pace`) without a restart:

| Pace | Rounds | Tradeoff |
|------|--------|----------|
| `fast` | every 1 min | Demos only. Governance tallies every minute at the default interval — proposals can pass before anyone reads them, and AI report costs scale with tick rate. |
| `normal` | every 5 min | Playtests. Enough time to vote between tallies, still compresses a season into hours. |
| `slow` | every 15 min | Production default. Humane deliberation time; a season spreads across days. |
| `manual` | none | Full control. Advance with `POST /api/pace/advance` — useful for live events and presentations. |

To slow governance without slowing games, raise `PINWHEEL_GOVERNANCE_INTERVAL` (tally every N rounds) instead of changing pace.

## Backup & Recovery

**Database:** Back up the SQLite file before any risky operation:

```bash
fly ssh console -C "cp /data/pinwheel.db /data/pinwheel.db.bak"
```

**Event sourcing as insurance:** Because governance is append-only events and simulation is deterministic, the entire league state can be reconstructed from the event log and the initial seed config. Even a total database loss is recoverable if the events survive.

## Production Readiness (Post-Hackathon)

Upgrades needed beyond the hackathon deployment:

- **Worker separation:** Split the game loop into a separate Fly Machine communicating via the database or Redis.
- **CDN:** Put static assets (HTMX, CSS, images) behind Fly's built-in CDN or Cloudflare.
- **Rate limiting:** Add rate limits on API endpoints and Discord commands to prevent abuse.
- **Error tracking:** Integrate Sentry for exception tracking and alerting.
- **Secrets management:** Consider Fly's built-in secrets rotation or an external vault.
- **Multi-region:** Deploy to multiple regions if the player base is geographically distributed.

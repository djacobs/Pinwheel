# Pinwheel Fates: Operations & Deployment

## Overview

Pinwheel Fates deploys to [Fly.io](https://fly.io) — a platform that runs Docker containers on hardware close to users. The deployment model is a single application process running the FastAPI server, the background game loop (APScheduler), and SSE streaming from one machine. PostgreSQL runs as a Fly-managed database. The Discord bot connects outbound from the same process.

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
│  │ Fly Postgres (attached)           │    │
│  │  └── pinwheel_db                  │    │
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

**Hackathon:** Fly Postgres (managed). Single-node, 256 MB shared plan. Sufficient for a 5-day hackathon with 8 teams and 21 rounds.

```bash
fly postgres create --name pinwheel-db --region sea --vm-size shared-cpu-1x --volume-size 1
fly postgres attach pinwheel-db
```

This sets `DATABASE_URL` automatically in the Fly environment.

**Local dev:** SQLite (`sqlite:///pinwheel.db`) for zero-config local development. The repository layer abstracts the difference — same queries, different engine.

**Migrations:** Alembic for schema migrations. Run migrations on deploy via the release command in `fly.toml`.

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

The `DATABASE_URL` is injected automatically by `fly postgres attach`.

| Variable | Description | Default |
|----------|-------------|---------|
| `ANTHROPIC_API_KEY` | Claude API key for Opus 4.6 | (required) |
| `DATABASE_URL` | PostgreSQL connection string | (set by Fly) |
| `DISCORD_BOT_TOKEN` | Discord bot token | (required) |
| `DISCORD_GUILD_ID` | Discord server ID | (required) |
| `PINWHEEL_ENV` | `development` / `staging` / `production` | `development` |
| `PINWHEEL_GAME_CRON` | Cron schedule for simulation blocks | `0 * * * *` |
| `PINWHEEL_GOV_WINDOW` | Seconds per governance window | `1800` |
| `PINWHEEL_LOG_LEVEL` | Logging level | `INFO` |
| `PINWHEEL_PRESENTATION_PACE` | `production` / `fast` / `instant` | `production` |

## Deployment

### First Deploy

```bash
# Install Fly CLI
curl -L https://fly.io/install.sh | sh

# Authenticate
fly auth login

# Launch the app (creates the Fly app from fly.toml)
fly launch --no-deploy

# Create and attach Postgres
fly postgres create --name pinwheel-db --region sea --vm-size shared-cpu-1x --volume-size 1
fly postgres attach pinwheel-db

# Set secrets
fly secrets set ANTHROPIC_API_KEY=sk-ant-... DISCORD_BOT_TOKEN=... DISCORD_GUILD_ID=...

# Deploy
fly deploy
```

### Subsequent Deploys

```bash
fly deploy
```

The Dockerfile builds the image, `fly.toml` defines the release command (run migrations), and Fly handles zero-downtime deployment by starting the new machine before stopping the old one.

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

Structured logging via Python's `logging` module with JSON formatting in production. Log every simulation block completion, governance window open/close, mirror generation, and AI API call with duration and token count.

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
| Postgres | shared-cpu-1x, 256 MB, 1 GB disk | ~$7/mo | ~$1 |
| Bandwidth | Included (first 100 GB) | $0 | $0 |
| **Total Fly** | | | **~$3** |

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

Bot registration (slash commands) happens once via a setup script:

```bash
python -m pinwheel.bot.register_commands
```

This registers the `/propose`, `/amend`, `/vote`, `/boost`, `/trade`, `/tokens`, `/strategy`, `/rules`, `/standings`, `/team`, and `/join` commands with Discord's API. Slash commands are global (not per-guild) for simplicity during the hackathon.

## Backup & Recovery

**Database:** Fly Postgres includes automatic daily backups. For the hackathon, this is sufficient. Manual backups:

```bash
fly postgres connect --app pinwheel-db
pg_dump pinwheel > backup.sql
```

**Event sourcing as insurance:** Because governance is append-only events and simulation is deterministic, the entire league state can be reconstructed from the event log and the initial seed config. Even a total database loss is recoverable if the events survive.

## Production Readiness (Post-Hackathon)

Upgrades needed beyond the hackathon deployment:

- **Dedicated Postgres:** Move from shared to dedicated CPU, increase storage, enable point-in-time recovery.
- **Worker separation:** Split the game loop into a separate Fly Machine communicating via the database or Redis.
- **CDN:** Put static assets (HTMX, CSS, images) behind Fly's built-in CDN or Cloudflare.
- **Rate limiting:** Add rate limits on API endpoints and Discord commands to prevent abuse.
- **Error tracking:** Integrate Sentry for exception tracking and alerting.
- **Secrets management:** Consider Fly's built-in secrets rotation or an external vault.
- **Multi-region:** Deploy to multiple regions if the player base is geographically distributed.

# Pinwheel Fates — Admin Guide

## Re-seeding Production

When you need to start a fresh season from scratch (new season structure, clean game history, same players on same teams), use `scripts/prod_reseed.py`.

### What it does

1. **Drops all tables** and recreates the schema
2. Creates the league, Season 2, 4 teams, 4 hoopers per team, and a full round-robin schedule
3. Sets the season to `active` with `DEFAULT_RULESET`

**Player re-enrollment is automatic.** After the reseed, the Discord bot's self-heal mechanism (`_sync_role_enrollments`) runs on startup. It matches Discord members' team roles to the new team records by name and re-enrolls them — no manual work needed.

### Prerequisites

- `flyctl` CLI installed and authenticated
- The Discord bot has the **Members** privileged intent enabled (Discord Developer Portal → Bot → Privileged Gateway Intents)
- Discord team roles still exist and match team names exactly: `Rose City Thorns`, `Burnside Breakers`, `St. Johns Herons`, `Hawthorne Hammers`

### Step-by-step

```bash
# 1. Back up the production database
fly ssh console -C "cp /data/pinwheel.db /data/pinwheel.db.bak"

# 2. Deploy (ensures the script is on the machine)
fly deploy

# 3. Run the reseed (--force skips the confirmation prompt)
fly ssh console -C "python /app/scripts/prod_reseed.py --force"

# 4. Restart the app (triggers bot self-heal for player re-enrollment)
fly apps restart pinwheel
```

### Verifying

- Check `https://pinwheel.fly.dev/admin/roster` — all governors should appear with their teams
- Use `/roster` in Discord — confirms enrollment and token balances
- Check `https://pinwheel.fly.dev/standings` — should show 4 teams at 0-0

### Running interactively

Without `--force`, the script prompts for confirmation before dropping tables:

```bash
fly ssh console -C "python /app/scripts/prod_reseed.py"
# -> "This will DROP ALL TABLES... Type 'yes' to continue:"
```

### What gets wiped

Everything: game results, box scores, governance events, reports, season archives, player records, bot state (channel IDs). The bot recreates Discord channels and roles on startup if they're missing.

### What survives

Discord roles and channel structure persist in Discord itself (not in the database). The bot reads these on startup to heal player enrollments.

---

## Fixing Lost Player Enrollments

If `/roster` shows missing players after a season transition (e.g., only one governor visible when there should be many), player enrollments were likely dropped during `/new-season`. Use `scripts/fix_enrollments.py` to restore them.

### What happened

Player enrollment is season-scoped — each `PlayerRow` stores `enrolled_season_id` + `team_id`. When `/new-season` is called, `carry_over_teams()` should migrate enrollments to the new season. But if the previous season wasn't in `completed` or `archived` status, the carryover was silently skipped, leaving players stranded on old season IDs invisible to `/roster`.

This was fixed in the `start_new_season` code — it now carries from the most recent season regardless of status. But if damage already occurred, the fix script repairs it.

### Step-by-step

```bash
# 1. Dry run — see what would change (safe, no modifications)
fly ssh console -C "python /app/scripts/fix_enrollments.py"

# 2. Apply the fix
fly ssh console -C "python /app/scripts/fix_enrollments.py --apply"
```

### How it works

1. Finds the active season and its teams
2. Finds all players whose `enrolled_season_id` doesn't match the active season
3. Maps each player to the current season's team by matching team names
4. Updates their `team_id` and `enrolled_season_id`

Idempotent — safe to run multiple times. Players already enrolled in the active season are skipped.

### Verifying

- `/roster` in Discord should show all governors with correct team assignments
- Token balances may need regeneration if governors missed the season start — use `/new-season` token refresh or manually call `regenerate_all_governor_tokens`

---

## Starting a New Season

When a season ends, type `/new-season NAME` in Discord. You must have the Discord server's Administrator permission.

> `/new-season` name: **Summer Classic** carry_rules: **True**

- **carry_rules** (default: yes) brings the current ruleset forward. Set to `False` to reset to defaults.
- Teams, hoopers, and governor enrollments carry over automatically. All governors receive fresh tokens.
- A public announcement is posted to the main channel. Players do not need to re-enroll.
- There must be an existing season in the database. If there isn't one, seed the league first.

**Note:** The admin permission check uses Discord's server Administrator flag, not the `PINWHEEL_ADMIN_DISCORD_ID` variable. Those are separate: the env var controls who gets DMs and web page access; the Discord permission controls who can run `/new-season`.

---

## Wild Proposal Review & Admin Veto

When a player confirms a "wild" proposal (Tier 5+, or one the AI flagged with low confidence), two things happen at once:

1. The proposal goes to the Floor and voting opens normally.
2. You receive a DM with two buttons: **Clear** and **Veto**.

**Clear** acknowledges you've reviewed it. Voting continues. The proposer gets a DM saying their proposal was cleared.

**Veto** kills the proposal. You'll be asked for an optional reason. The proposer gets a DM explaining the veto and receives their PROPOSE token back.

If you do nothing for 24 hours, the buttons expire. Voting continues regardless — you are a safety valve, not a gatekeeper. The system does not block on your review.

The admin who receives these DMs is determined by the `PINWHEEL_ADMIN_DISCORD_ID` environment variable. If that isn't set, the server owner gets the DMs instead.

---

## Pace Control

The game advances automatically on a cron schedule. You can change the speed at runtime without restarting.

**Check current pace:**

```
GET /api/pace
```

**Change pace:**

```bash
curl -X POST http://localhost:8000/api/pace -H 'Content-Type: application/json' -d '{"pace":"slow"}'
```

| Pace | Cron | Round Interval |
|------|------|----------------|
| `fast` | every 1 minute | 1 min |
| `normal` | every 5 minutes | 5 min |
| `slow` | every 15 minutes | 15 min |
| `manual` | none (auto-advance off) | you trigger it |

**Advance one round manually** (useful in `manual` pace or for demos):

```
POST /api/pace/advance?quarter_seconds=300&game_gap_seconds=0
```

This triggers a single round with replay-mode presentation. Returns 409 if a presentation is already running.

**Check presentation status:**

```
GET /api/pace/status
```

Returns whether a presentation is currently active, and if so, which round and game index.

---

## Admin Web Pages

### Admin Roster

Visit `/admin/roster` in the web UI. Shows every enrolled governor with:

- Team assignment and team color
- Token balances (PROPOSE, AMEND, BOOST)
- Proposals submitted, passed, and failed
- Total votes cast

In production (with OAuth enabled), only the admin can see this page. In local dev, it's open to everyone for testing.

### Eval Dashboard

Visit `/admin/evals` in the web UI. This is your health check on the AI and the game's governance quality. It shows aggregate stats only — no individual report text, no private content.

What you'll find:

- **Grounding rate** — how often the AI's reports reference real entities from the simulation
- **Prescriptive flags** — how often the AI slips into telling players what to do (it shouldn't)
- **Report Impact Rate** — whether AI reports appear to influence governance behavior
- **Rubric summary** — manual quality scores for public reports
- **Golden dataset pass rate** — how well the AI handles a fixed set of 20 eval cases
- **A/B win rates** — dual-prompt comparison results
- **GQI trend** — Governance Quality Index over the last 5 rounds (diversity, participation breadth, consequence awareness, vote deliberation)
- **Active scenario flags** — recent flags for unusual game states (dominant strategies, degenerate equilibria, etc.)
- **Rule evaluation** — the AI's admin-facing analysis: suggested experiments, stale parameters, equilibrium health, and flagged concerns

The rule evaluator is different from the reporter. The reporter describes and never prescribes. The rule evaluator prescribes freely — it's your advisor, not the players'.

---

## Environment Variables Reference

| Variable | What It Controls | Default |
|----------|-----------------|---------|
| `PINWHEEL_ADMIN_DISCORD_ID` | Your Discord user ID. Receives wild proposal DMs. Gates admin web pages in production. | (unset — falls back to server owner) |
| `PINWHEEL_PRESENTATION_PACE` | Game speed: `fast`, `normal`, `slow`, `manual` | `slow` |
| `PINWHEEL_PRESENTATION_MODE` | `replay` (live quarter-by-quarter arena) or `instant` (results appear immediately) | `replay` |
| `PINWHEEL_AUTO_ADVANCE` | Whether the scheduler auto-advances rounds on the cron schedule | `true` |
| `PINWHEEL_GAME_CRON` | Explicit cron override. If set, ignores pace. | derived from pace |
| `PINWHEEL_GOVERNANCE_INTERVAL` | Tally governance every N rounds | `1` |
| `PINWHEEL_GOV_WINDOW` | Governance window duration in seconds (for GQI vote deliberation) | `900` |
| `PINWHEEL_EVALS_ENABLED` | Run evals (grounding, prescriptive, GQI, flags, rule evaluator) after each round | `true` |
| `ANTHROPIC_API_KEY` | Claude API key. If unset, AI features fall back to mocks. | (unset) |
| `DATABASE_URL` | SQLite connection string (e.g. `sqlite+aiosqlite:///pinwheel.db`) | `sqlite+aiosqlite:///pinwheel.db` |
| `PINWHEEL_ENV` | `development`, `staging`, or `production` | `development` |
| `SESSION_SECRET_KEY` | Session signing key. **Must set in production.** | (unset) |
| `DISCORD_TOKEN` | Discord bot token | (required for Discord) |
| `DISCORD_GUILD_ID` | Target guild ID | (required for Discord) |
| `DISCORD_CLIENT_ID` | OAuth2 client ID | (required for OAuth) |
| `DISCORD_CLIENT_SECRET` | OAuth2 client secret | (required for OAuth) |
| `DISCORD_REDIRECT_URI` | OAuth2 callback URL | (required for OAuth) |

---

## Fly.io Deployment

### First Deploy

```bash
fly launch --no-deploy
fly secrets set ANTHROPIC_API_KEY=sk-ant-... DISCORD_TOKEN=... DISCORD_GUILD_ID=...
fly deploy
```

### Subsequent Deploys

```bash
fly deploy
```

### Rollback

```bash
fly releases
fly deploy --image <previous-image-ref>
```

### Setting Secrets

```bash
fly secrets set ANTHROPIC_API_KEY=sk-ant-...
fly secrets set DISCORD_TOKEN=...
fly secrets set PINWHEEL_ENV=production
fly secrets set PINWHEEL_PRESENTATION_PACE=slow
```

`DATABASE_URL` defaults to `sqlite+aiosqlite:///pinwheel.db`. In production on Fly, it points to `/data/pinwheel.db` on the persistent volume.

### Monitoring

```bash
# Tail live logs
fly logs

# Health check
curl https://pinwheel.fly.dev/health
```

The `/health` endpoint returns database connectivity, scheduler status, Discord connection state, and last simulation timestamp.

---

## Database Backup

```bash
# Manual backup
fly ssh console -C "pg_dump \$DATABASE_URL > /data/backup_$(date +%Y%m%d).sql"
```

Fly Postgres includes automatic daily backups. For manual backups before risky operations (reseeds, migrations, schema changes), always back up first.

---

## Discord Bot Setup

The bot runs inside the same FastAPI process — it is not a separate service. It reconnects automatically on restarts.

### Required Privileged Intents

Enable in the Discord Developer Portal (Bot > Privileged Gateway Intents):

- **Members** — required for `_sync_role_enrollments` (self-heal on startup)
- **Message Content** — required for message-based features

### Team Roles

Discord team roles must match team names exactly: `Rose City Thorns`, `Burnside Breakers`, `St. Johns Herons`, `Hawthorne Hammers`. The bot creates roles and channels on first startup if they don't exist.

---

## Things to Know

- **Presentation survives restarts.** If a replay is in progress and the server redeploys, it picks up where it left off. Presentation state is persisted in the database.
- **Completed seasons still tally governance.** After a season's games are done, the scheduler keeps running governance tally cycles so late votes still count.
- **Championship window.** When a season enters championship status, the scheduler checks a `championship_ends_at` timestamp. When it expires, the season transitions to complete automatically.
- **Governance interval is governable.** Players can vote to change `governance_rounds_interval` (Tier 4), making tallying more or less frequent.

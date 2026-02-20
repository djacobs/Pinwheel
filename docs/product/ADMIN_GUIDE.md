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

### Monitoring & Log Debugging

```bash
# Tail live logs
fly logs -a pinwheel

# Health check
curl https://pinwheel.fly.dev/health
```

The `/health` endpoint returns database connectivity, scheduler status, Discord connection state, and last simulation timestamp.

**Debugging Discord command failures.** All Discord command errors log the full traceback at ERROR level. The log key tells you which command failed:

| Log Key | Command | What It Means |
|---------|---------|---------------|
| `discord_join_failed` | `/join` | Enrollment failed (DB contention, missing season, etc.) |
| `discord_team_autocomplete_failed` | `/join` autocomplete | Team list couldn't be loaded |
| `discord_propose_failed` | `/propose` | AI interpretation or DB write failed |
| `discord_vote_failed` | `/vote` | Vote recording failed |
| `discord_roster_failed` | `/roster` | Roster lookup failed |
| `discord_proposals_failed` | `/proposals` | Proposal list lookup failed |

```bash
# Find a specific command failure with full traceback
fly logs -a pinwheel --no-tail | grep -A 20 "discord_join_failed"

# Find all Discord errors in recent logs
fly logs -a pinwheel --no-tail | grep "discord_.*_failed"

# Watch for errors in real time while a player retries
fly logs -a pinwheel | grep -E "discord_.*_failed|ERROR"

# Check scheduler (tick_round) activity
fly logs -a pinwheel --no-tail | grep "tick_round"

# Check bot connection state
fly logs -a pinwheel --no-tail | grep "discord_bot"
```

**Important:** `auto_stop_machines` is set to `"off"` in `fly.toml`. The Discord bot requires a persistent WebSocket connection — if Fly stops the machine, the bot disconnects and players see Discord's generic "Something went wrong" error instead of our messages. Never change this to `"stop"` or `"suspend"`.

---

## Database Backup

```bash
# Manual backup (SQLite file copy — fast and safe)
fly ssh console -C "cp /data/pinwheel.db /data/pinwheel.db.bak"
```

Always back up before risky operations (reseeds, migrations, schema changes). The backup takes ~2 seconds.

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

## Reviewing Proposal History

Every proposal is permanently logged in the `governance_events` table as a `proposal.submitted` event. The `payload` JSON column contains the full `Proposal` object — raw text, sanitized text, AI interpretation, tier, confidence, effects, and governor/team info. AI call performance (model, tokens, latency) is logged separately in `ai_usage_log`.

This is the primary quality control loop for the interpreter. Review proposals regularly to catch:
- Misinterpretations (creative intent mapped to wrong mechanic)
- Low-confidence results that should have been caught
- Patterns in what players are asking for vs. what the system can express
- Timeout/fallback frequency

### Querying proposals on production

SSH into the Fly machine and use SQLite directly:

```bash
# Open a SQLite shell on the production database
fly ssh console -C "sqlite3 /data/pinwheel.db"
```

**All proposals, most recent first:**

```sql
SELECT
  json_extract(payload, '$.raw_text') AS proposal,
  json_extract(payload, '$.tier') AS tier,
  json_extract(payload, '$.interpretation.confidence') AS confidence,
  json_extract(payload, '$.interpretation.impact_analysis') AS impact,
  created_at
FROM governance_events
WHERE event_type = 'proposal.submitted'
ORDER BY created_at DESC;
```

**Proposals with full interpretation effects:**

```sql
SELECT
  json_extract(payload, '$.raw_text') AS proposal,
  json_extract(payload, '$.interpretation.effects') AS effects,
  json_extract(payload, '$.interpretation.confidence') AS confidence,
  json_extract(payload, '$.interpretation.clarification_needed') AS needs_clarification
FROM governance_events
WHERE event_type = 'proposal.submitted'
ORDER BY created_at DESC;
```

**Low-confidence or flagged proposals (interpreter struggled):**

```sql
SELECT
  json_extract(payload, '$.raw_text') AS proposal,
  json_extract(payload, '$.interpretation.confidence') AS confidence,
  json_extract(payload, '$.interpretation.impact_analysis') AS impact
FROM governance_events
WHERE event_type = 'proposal.submitted'
  AND (json_extract(payload, '$.interpretation.confidence') < 0.7
       OR json_extract(payload, '$.interpretation.clarification_needed') = 1)
ORDER BY created_at DESC;
```

**Proposals by a specific governor:**

```sql
SELECT
  json_extract(payload, '$.raw_text') AS proposal,
  json_extract(payload, '$.tier') AS tier,
  created_at
FROM governance_events
WHERE event_type = 'proposal.submitted'
  AND governor_id = '<governor-uuid>'
ORDER BY created_at DESC;
```

**Interpreter performance (latency and model usage):**

```sql
SELECT
  model,
  call_type,
  round(latency_ms) AS latency_ms,
  input_tokens,
  output_tokens,
  round(cost_usd, 4) AS cost,
  created_at
FROM ai_usage_log
WHERE call_type LIKE 'interpreter%'
ORDER BY created_at DESC
LIMIT 20;
```

**Full proposal lifecycle (submitted → voted → passed/failed):**

```sql
SELECT
  event_type,
  json_extract(payload, '$.raw_text') AS proposal,
  json_extract(payload, '$.status') AS status,
  created_at
FROM governance_events
WHERE aggregate_id = '<proposal-uuid>'
ORDER BY sequence_number;
```

### What to look for

- **Confidence < 0.7 on clear proposals** — the prompt may need a new concept or example
- **Repeated fallback to mock** — check `ai_usage_log` for timeout patterns; may need prompt trimming or timeout adjustment
- **Players rewording and resubmitting** — same governor, similar `raw_text`, multiple attempts = friction signal
- **Tier misclassification** — a simple parameter change classified as Tier 5+ wastes admin review time
- **Effects that don't match intent** — the most important signal. If "blocks are worth one point" produces anything other than a `parameter_change` on `block_points`, the interpreter needs work

---

## Resubmitting Failed or Stuck Proposals

Proposals can get stuck or fail to take effect for several reasons:

- **Interpreter failure** — the AI interpreter times out or returns unparseable JSON, leaving the proposal as `proposal.pending_interpretation` with no submit event and no way to vote.
- **Season ended before tally** — a confirmed proposal with votes sits in a season that completes before the governance tally runs. The proposal is orphaned.
- **Effects pipeline bug** — a proposal passes the vote but its `effects_v2` data was never persisted in the event payload, so the game loop has nothing to enact.

When proposals are stuck, use `scripts/resubmit_proposals.py` to re-interpret and resubmit them into the current season, gratis (no token debit). The script also refunds the original token cost.

### How it works

1. Each proposal is re-interpreted via `interpret_proposal_v2` (real AI, not mock)
2. Submitted to the current season with `token_already_spent=True` (gratis)
3. Auto-confirmed so it's immediately open for voting
4. Original token cost refunded to the governor in the original season

### Usage

```bash
# Dry run — shows what would happen
fly ssh console -C "python /app/scripts/resubmit_proposals.py"

# Apply
fly ssh console -C "python /app/scripts/resubmit_proposals.py --apply"
```

### Editing the proposal list

The proposals to resubmit are hardcoded in the `PROPOSALS` list at the top of the script. To resubmit different proposals, edit the list with the proposal's `original_id`, `original_season_id`, `governor_username`, `raw_text`, and `original_cost`.

### Refunding stuck tokens without resubmission

If proposals are stuck in `pending_interpretation` and you just want to refund without resubmitting, use `scripts/refund_stuck_proposals.py`:

```bash
# Dry run
fly ssh console -C "python /app/scripts/refund_stuck_proposals.py"

# Apply
fly ssh console -C "python /app/scripts/refund_stuck_proposals.py --apply"
```

This finds all `proposal.pending_interpretation` events with no corresponding `interpretation_ready` or `interpretation_expired` event, expires them, and refunds the PROPOSE token.

### History: Feb 19 2026 resubmission

Five proposals were resubmitted into season "number nine" after being stuck or ineffective in prior seasons:

| Original | Governor | Text | Original Season | Problem | New ID |
|----------|----------|------|----------------|---------|--------|
| #8 | Adriana | "la pelota es lava" | Season 5 | Confirmed with 3 yes votes, never tallied (season ended) | `7037de91` |
| #9 | Rob Drimmie | "baskets made from inside the key score 0 points" | Season 6! | Passed vote but effects never registered | `2afc2a91` |
| #10 | .djacobs | "the more baskets a hooper scores, the more their ability scores go up" | Season 6! | Got zero votes (interpreter was down, mock fallback) | `04ebdb15` |
| #14 | JudgeJedd | "no one can hold the ball for more than 4 seconds" | Season 7 | Stuck in pending_interpretation, never submitted | `dc7cf317` |
| #15 | JudgeJedd | "no one can hold the ball longer than 3 seconds" | I ate the sandbox | Stuck in pending_interpretation, never submitted | `94bbf4c7` |

The AI interpreter failed to produce structured effects for 4 of 5 proposals during resubmission (Sonnet, Haiku, and Opus all returned unparseable JSON). All 5 fell back to narrative-only effects. This points to a separate bug in the interpreter's JSON output handling that needs investigation.

---

## Things to Know

- **Presentation survives restarts.** If a replay is in progress and the server redeploys, it picks up where it left off. Presentation state is persisted in the database.
- **Completed seasons still tally governance.** After a season's games are done, the scheduler keeps running governance tally cycles so late votes still count.
- **Championship window.** When a season enters championship status, the scheduler checks a `championship_ends_at` timestamp. When it expires, the season transitions to complete automatically.
- **Governance interval is governable.** Players can vote to change `governance_rounds_interval` (Tier 4), making tallying more or less frequent.

# Pinwheel Fates: How It Works

## The Core Loop

**Govern → Simulate → Observe → Reflect → Govern**

Manage a basketball team, change the game to change the world.

## Joining

1. Type `/join TEAM` in Discord.
2. You are locked to that team for the season.
3. You receive 6 governance tokens: 2 PROPOSE, 2 AMEND, 2 BOOST.
4. You can now propose rule changes, vote, trade tokens, and set team strategy.
5. Players may change teams inbetween seasons if they desire. 

## Seasons

A season is a round-robin schedule. Four teams play each other. There are three rounds of six games, with a post-season to follow. 

After each round, votes and proposals are tallied, and winning proposals are put in effect immediately. 

When all regular-season games finish, the top 4 teams enter playoffs. Semifinals (best of 3), then finals (best of 5). The winner is crowned champion.

The season archives. A new season begins. Rules carry over to new seasons unless written otherwise.

### Season Lifecycle Phases

A season progresses through these phases:

**SETUP** -- Season created, teams being assigned.
**ACTIVE** -- Regular-season games in progress.
**TIEBREAKER_CHECK** -- Regular season complete, checking for ties at the playoff cutoff. Tiebreaker criteria: head-to-head record, then point differential, then total points scored.
**TIEBREAKERS** -- Unresolvable ties require tiebreaker games (round-robin among tied teams).
**PLAYOFFS** -- Semifinal and championship series.
**CHAMPIONSHIP** -- Champion crowned. Awards computed. A timed celebration window (default 30 minutes).
**OFFSEASON** -- Post-championship governance window where governors can submit and vote on meta-rule proposals for the next season. Any rules enacted during the offseason carry forward.
**COMPLETE** -- Season archived. Memorial created.

### Season Memorial & Archive

When a season completes, `archive_season()` creates an immutable snapshot capturing the full story of that season. The archive includes:

- **Final standings** with team names, win/loss records, and point differentials.
- **Champion** -- the winning team and their record.
- **Rule change history** -- every rule enacted during the season, in order.
- **Awards** -- six end-of-season awards across gameplay and governance:
  - *MVP* (highest PPG), *Defensive Player of the Season* (highest SPG), *Most Efficient* (best FG%, min 20 FGA).
  - *Most Active Governor* (most proposals + votes), *Coalition Builder* (most token trades), *Rule Architect* (highest proposal pass rate).
- **Statistical leaders** -- top 3 hoopers in PPG, APG, SPG, and FG%.
- **Key moments** -- 5-8 notable games: playoff games, nail-biters, blowouts, and Elam Ending activations.
- **Head-to-head records** -- team-vs-team win/loss and point differentials.
- **Rule timeline** -- chronological list of every rule change with the proposing governor.
- **AI narrative placeholders** -- slots for season narrative, championship recap, champion profile, and governance legacy (filled by AI in a separate generation phase).

The memorial data is stored as JSON on the `SeasonArchiveRow` and serves as the data backbone for end-of-season reports and the season history page.

## Proposals

Players spend PROPOSE tokens to propose new rules. Proposals should be written in plain language. 

> “Make the floor lava: Held-ball dramatically saps players stamina.”

> “Reward sharpshooting: Make three-pointers worth 5 points"

Proposals go to vote immediately. 

The AI interprets your text, asks you for review, and you confirm or cancel. Once confirmed, the proposal goes to the Floor and voting opens. “Wild” proposals (Tier 5+, defined below) are reviewed by admin in parallel with the vote. 

### Tiers

| Tier | What Changes | Token Cost | Threshold |
|------|-------------|------|-----------|
| 1 | Game mechanics (shot clock, scoring, fouls) | 1 | 50% |
| 2 | Agent behavior (shot limits, home court) | 1 | 50% |
| 3 | League structure (teams, playoffs, schedule) | 1 | 60% |
| 4 | Meta-governance (vote threshold, token regen) | 1 | 60% |
| 5+ | Uninterpretable or novel | 2 | 67% |

## Voting

Type `/vote YES` or `/vote NO` on an active proposal.

If multiple proposals are open, Discord shows an autocomplete list when you type in the `proposal` field. Pick the one you want. If you skip it, your vote goes to the most recent proposal.

Each team's total vote weight is 1.0, split equally among its governors. If your team has 3 governors, your weight is 0.33.

To double your weight, add `boost: True` to your vote:

> `/vote` choice: **Yes** boost: **True**

This spends one BOOST token, restored between seasons.

Ties fail. Votes are counted every round (configurable via `PINWHEEL_GOVERNANCE_INTERVAL`). Passed proposals change rules immediately. Failed proposals do nothing.

## Tokens

| Token | What It Does | Regeneration |
|-------|-------------|-------------|
| PROPOSE | Submit a rule change | 2 per tally cycle |
| AMEND | Modify someone else's proposal | 2 per tally cycle |
| BOOST | Double your vote weight once | 2 on join (does not regenerate at tally) |

Tokens are tradeable between any players via `/trade`. The terms are visible to both parties. The AI may notice patterns.

## The Reporter (AI)

The AI writes three reports after each round. The reporter's constraint: **describe, never prescribe.** It tells you what happened and what it might mean. It never tells you what to do.

**Simulation Report** (public): What happened in the games. Statistical patterns. Effects of recent rule changes.

**Governance Report** (public): Voting trends. Coalition formation. Who is proposing what and why it might matter.

**Private Report** (DM to you): Your own governance behavior reflected back. Patterns you might not see. Never prescribes. Only describes.

## The Admin

The admin keeps the game running.

- Starts and ends seasons.
- Receives DM notifications when wild proposals are submitted.
- Can **veto** a wild proposal before tally (refunds the proposer's tokens).
- Can **clear** a wild proposal to acknowledge review (voting continues normally).
- If the admin does nothing, voting proceeds. The admin is a safety valve, not a gatekeeper.

## Discord Commands

| Command | What It Does |
|---------|-------------|
| `/join TEAM` | Enroll on a team |
| `/propose TEXT` | Submit a rule change |
| `/vote YES\|NO [boost] [proposal]` | Vote on a proposal (boost and proposal are optional) |
| `/tokens` | Check your token balance |
| `/trade @USER TOKENS` | Trade tokens with another governor |
| `/trade-hooper OFFER WANT` | Propose a player trade between teams |
| `/strategy TEXT` | Set your team's play style |
| `/bio HOOPER TEXT` | Write a backstory for a hooper |
| `/standings` | View league standings |
| `/schedule` | View upcoming matchups |
| `/reports` | View latest AI reports |
| `/profile` | View your governance record |
| `/rules` | View current ruleset |

## Run of Admin

Everything above is the player experience. This section is for the person keeping the lights on.

### Starting a New Season

When a season ends, type `/new-season NAME` in Discord. You must have the Discord server's Administrator permission.

> `/new-season` name: **Summer Classic** carry_rules: **True**

- **carry_rules** (default: yes) brings the current ruleset forward. Set to `False` to reset to defaults.
- Teams, hoopers, and governor enrollments carry over automatically. All governors receive fresh tokens.
- A public announcement is posted to the main channel. Players do not need to re-enroll.
- There must be an existing season in the database. If there isn't one, seed the league first.

### Wild Proposal Review

When a player confirms a "wild" proposal (Tier 5+, or one the AI flagged with low confidence), two things happen at once:

1. The proposal goes to the Floor and voting opens normally.
2. You receive a DM with two buttons: **Clear** and **Veto**.

**Clear** acknowledges you've reviewed it. Voting continues. The proposer gets a DM saying their proposal was cleared.

**Veto** kills the proposal. You'll be asked for an optional reason. The proposer gets a DM explaining the veto and receives their PROPOSE token back.

If you do nothing for 24 hours, the buttons expire. Voting continues regardless -- you are a safety valve, not a gatekeeper. The system does not block on your review.

The admin who receives these DMs is determined by the `PINWHEEL_ADMIN_DISCORD_ID` environment variable. If that isn't set, the server owner gets the DMs instead.

#### Web Review Queue

Visit `/admin/review` in the web UI. This is a companion to the Discord DM flow -- a centralized view of all proposals flagged for admin review.

The queue shows:

- **Pending proposals** -- Tier 5+ or AI confidence below 50%, awaiting admin action. Sorted newest first.
- **Resolved proposals** -- Previously reviewed proposals (cleared, vetoed, or resolved through voting).
- **Injection alerts** -- Proposals that the pre-flight injection classifier flagged as suspicious or injection attempts. Shows the classification confidence, reason, and whether the proposal was blocked.

Each proposal card displays the raw text, the AI's interpretation (parameter, new value, confidence), impact analysis, and the proposing governor. Injection-flagged proposals and low-confidence interpretations are badged prominently.

In production (with OAuth enabled), only the admin can access this page. In local dev without OAuth, it is open for testing.

### Admin Roster

Visit `/admin/roster` in the web UI. This page shows every enrolled governor with:

- Team assignment and team color
- Token balances (PROPOSE, AMEND, BOOST)
- Proposals submitted, passed, and failed
- Total votes cast

In production (with OAuth enabled), only the admin can see this page. In local dev, it's open to everyone for testing.

### Eval Dashboard

Visit `/admin/evals` in the web UI. This is your health check on the AI and the game's governance quality. It shows aggregate stats only -- no individual report text, no private content.

What you'll find:

- **Grounding rate** -- how often the AI's reports reference real entities from the simulation
- **Prescriptive flags** -- how often the AI slips into telling players what to do (it shouldn't)
- **Report Impact Rate** -- whether AI reports appear to influence governance behavior
- **Rubric summary** -- manual quality scores for public reports
- **Golden dataset pass rate** -- how well the AI handles a fixed set of 20 eval cases
- **A/B win rates** -- dual-prompt comparison results
- **GQI trend** -- Governance Quality Index over the last 5 rounds (diversity, participation breadth, consequence awareness, vote deliberation)
- **Active scenario flags** -- recent flags for unusual game states (dominant strategies, degenerate equilibria, etc.)
- **Rule evaluation** -- the AI's admin-facing analysis: suggested experiments, stale parameters, equilibrium health, and flagged concerns

The rule evaluator is different from the reporter. The reporter describes and never prescribes. The rule evaluator prescribes freely -- it's your advisor, not the players'.

### Pace Control

The game advances automatically on a cron schedule. You can change the speed at runtime without restarting.

**Check current pace:**

```
GET /api/pace
```

**Change pace:**

```
POST /api/pace
{"pace": "fast"}
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

### Environment Variables for Admin

| Variable | What It Controls | Default |
|----------|-----------------|---------|
| `PINWHEEL_ADMIN_DISCORD_ID` | Your Discord user ID. Receives wild proposal DMs. Gates admin web pages in production. | (unset -- falls back to server owner) |
| `PINWHEEL_PRESENTATION_PACE` | Game speed: `fast`, `normal`, `slow`, `manual` | `slow` |
| `PINWHEEL_PRESENTATION_MODE` | `replay` (live quarter-by-quarter arena) or `instant` (results appear immediately). Production forces `replay`. | `replay` |
| `PINWHEEL_AUTO_ADVANCE` | Whether the scheduler auto-advances rounds on the cron schedule | `true` |
| `PINWHEEL_GAME_CRON` | Explicit cron override. If set, ignores pace. | derived from pace |
| `PINWHEEL_GOVERNANCE_INTERVAL` | Tally governance every N rounds | `1` |
| `PINWHEEL_EVALS_ENABLED` | Run evals (grounding, prescriptive, GQI, flags, rule evaluator) after each round | `true` |
| `PINWHEEL_QUARTER_REPLAY_SECONDS` | How long each quarter takes in replay mode | `300` (5 min) |
| `PINWHEEL_GAME_INTERVAL_SECONDS` | Gap between games in a round during replay | `1800` (30 min) |
| `ANTHROPIC_API_KEY` | Claude API key. If unset, AI features fall back to mocks. | (unset) |

### Other Things to Know

- **Presentation survives restarts.** If a replay is in progress and the server redeploys, it picks up where it left off. The presentation state is persisted in the database, and on startup the system calculates how many quarters elapsed and skips ahead.
- **Completed seasons still tally governance.** After a season's games are done, the scheduler keeps running governance tally cycles so late votes still count.
- **Championship window.** When a season enters championship status, the scheduler checks a `championship_ends_at` timestamp. When the window expires, the season transitions to complete automatically.
- **The admin permission check uses Discord's server Administrator flag**, not the `PINWHEEL_ADMIN_DISCORD_ID` variable. Those are separate: the env var controls who gets DMs and web page access; the Discord permission controls who can run `/new-season`.

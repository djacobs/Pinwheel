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

### Definitions

**Round:** Every team plays every other team exactly once. With 4 teams, a round is 6 games. Games within a round are scheduled so no team plays more than one game at once -- with 4 teams, that's 3 sets of 2 simultaneous games, presented sequentially.

**Tick:** One firing of the scheduler. Each tick plays one set of simultaneous games where no team appears twice. With 4 teams, a tick is 2 games. A round takes 3 ticks to complete. The cron schedule controls how often ticks fire.

**Series:** A playoff matchup between two teams, played until one team meets the progression criteria (best-of-3 for semifinals, best-of-5 for finals).

### Regular Season

A season has a configurable number of rounds (default 3, governable via `round_robins_per_season`, range 1-5). No team plays more than one game at once. With 4 teams and 3 rounds: 18 total games.

After each set of games, votes and proposals are tallied. Winning proposals normally take effect immediately. If the admin approval gate is enabled (`PINWHEEL_RULES_REQUIRE_APPROVAL=true`), the tally still records and announces the pass, but the change holds in a pending-admin state and is enacted only when the admin approves it (see Run of Admin below).

### Playoffs

When all regular-season games finish, the top 4 teams enter playoffs. Semifinals: #1 vs #4 and #2 vs #3 (best-of-3 series). Finals: series winners meet (best-of-5 series). No team plays more than one game at once. Two semifinal games can be simultaneous since no team overlaps.

The winner is crowned champion. The season archives. A new season begins. Rules carry over to new seasons unless written otherwise. When the admin starts the new season with `carry_rules` on, registered effects (the runtime effects created by passed proposals) carry over along with the ruleset, and the league gets an announcement listing what carried forward.

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

The tier is decided by *what kind of change* the AI's interpretation produces, not by the topic of your text. Each interpreted effect maps to a tier; a compound proposal (several effects at once) takes the highest tier among them. (Compound proposals are simply multiple effects attached to one proposal — there is no separate "composite" effect type.)

| Tier | How a proposal lands here | Token Cost | Threshold |
|------|--------------------------|------------|-----------|
| 1 | Changes a core game-mechanics parameter (shot clock, point values, quarter length, fouls, stamina, Elam settings) | 1 | 50%* |
| 2 | Changes a behavior or venue parameter (shot-share limits, home court, crowd, travel fatigue) — or produces only narrative effects (flavor with no mechanical change) | 1 | 50%* |
| 3 | Changes a league-structure parameter (team count, round-robins, playoff format) — or produces any structural effect: hook callbacks, meta-mutations, move grants, custom mechanics, game-definition patches | 1 | 60% |
| 4 | Changes a meta-governance parameter (`proposals_per_window`, `vote_threshold`) | 1 | 60% |
| 5 | Wild: AI-generated code (the Code Council), proposals the AI could not interpret (no effects), or submissions flagged as injection attempts | 2 | 67% |

\* Tiers 1-2 use the league's governable `vote_threshold` (default 50%). Tiers 3-4 require 60% — or the league threshold if it has been voted above that. Tiers 5-6 require 67%. Tiers 7+ are reserved (75% threshold, 3 tokens).

Proposals beyond the AI's primitive vocabulary escalate to the **Code Council**: the AI generates real game code, three independent AI reviewers must unanimously approve it, and the admin gives final sign-off before it runs in live games. While the code awaits sign-off, the AI's interpreted approximation is what's live. There is no timeout on that sign-off: if the admin never acts, the approximation stays live indefinitely and the generated code stays inert. The system re-sends the admin notification on later governance ticks until it is delivered, but it never auto-approves. This is a deliberate accepted risk — an absent admin degrades the league to the interpreted approximation, never to unreviewed generated code. The admin gate is deliberate: votes decide *what* the league wants; a human verifies the generated code does that and nothing else.

**Structural proposals** change the game itself, not just its numbers: new shot types with their own point values and announcer calls ("add a half-court shot called The Prayer worth 4 points"), different period counts and lengths, disabling the Elam Ending, changing how many hoopers play per side. These are expressed as game-definition patches — validated against playability invariants and a test simulation before they can take effect, and admin-reviewed in parallel with the vote. This is the door out of basketball: what the league plays three seasons from now is up to the Floor.

## Voting

Type `/vote YES` or `/vote NO` on an active proposal.

If multiple proposals are open, Discord shows an autocomplete list when you type in the `proposal` field. Pick the one you want. If you skip it, your vote goes to the most recent proposal.

Each team's total vote weight is 1.0, split equally among its governors. If your team has 3 governors, your weight is 0.33.

To double your weight, add `boost: True` to your vote:

> `/vote` choice: **Yes** boost: **True**

This spends one BOOST token. Like PROPOSE and AMEND, BOOST regenerates at 2 per tally cycle, and every governor's tokens are refreshed again when a new season starts.

Ties fail. Votes are counted every round (configurable via `PINWHEEL_GOVERNANCE_INTERVAL`). Passed proposals change rules immediately — or, when the admin approval gate is enabled, as soon as the admin approves them. Failed proposals do nothing.

## Tokens

| Token | What It Does | Regeneration |
|-------|-------------|-------------|
| PROPOSE | Submit a rule change | 2 per tally cycle |
| AMEND | Modify someone else's proposal | 2 per tally cycle |
| BOOST | Double your vote weight once | 2 per tally cycle |

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
- Exception: when the league runs with the approval gate on (`PINWHEEL_RULES_REQUIRE_APPROVAL=true`), every passing proposal waits for admin approval before it is enacted. The vote still decides; the admin controls when the change goes live.

## Discord Commands

| Command | What It Does |
|---------|-------------|
| `/join TEAM` | Enroll on a team |
| `/propose TEXT` | Submit a rule change |
| `/amend PROPOSAL TEXT` | Propose an amendment to an active proposal |
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
| `/proposals [season]` | View all proposals and their status |
| `/roster` | View all enrolled governors |
| `/effects` | View all active game effects |
| `/repeal EFFECT` | Propose repealing an active effect |
| `/ask QUESTION` | Ask the AI anything about the league — stats, standings, games, rules |
| `/status` | Get a briefing on the current state of the league |
| `/history [season]` | View past season memorials |
| `/edit-series REPORT` | Collaboratively edit a playoff series report (governors on the two teams involved) |

There is no `/rules` slash command — to see the current ruleset, use `/effects`, `/ask`, or the Rules page on the web. Admin-only commands (`/new-season`, `/activate-mechanic`, `/review-codegen`, `/disable-effect`, `/rerun-council`) are covered in Run of Admin below and in the Admin Runbook in `docs/OPS.md`.

## Run of Admin

Everything above is the player experience. This section is for the person keeping the lights on.

### Starting a New Season

When a season ends, type `/new-season NAME` in Discord. Your Discord user ID must match `PINWHEEL_ADMIN_DISCORD_ID`.

> `/new-season` name: **Summer Classic** carry_rules: **True**

- **carry_rules** (default: yes) brings the current ruleset forward. Set to `False` to reset to defaults.
- With **carry_rules** on, registered effects from passed proposals carry into the new season too, and an announcement lists what carried forward.
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

### Proposal Approval Gate

By default, proposals that pass a tally enact immediately — the only human checkpoint is the wild-proposal veto above. Setting `PINWHEEL_RULES_REQUIRE_APPROVAL=true` enables a stricter mode for leagues that want a human in the loop on every change:

- **Every** passing proposal, regardless of tier, holds in a pending-admin state instead of enacting.
- The tally still records and announces the pass — the Floor's decision is on the record.
- Enactment happens when you approve the pending proposal; until then, the ruleset is unchanged.
- Veto remains available for proposals you decline to enact.

With the gate off (the default), behavior is unchanged: passed proposals enact at tally.

### Codegen Sign-Off

Code Council (Tier 5 codegen) effects have their own gate that is **always on**, independent of `PINWHEEL_RULES_REQUIRE_APPROVAL`. When a council-approved codegen effect registers, it starts in a `pending` state and its generated code never runs until you approve it. You get a DM with **Approve**/**Reject** buttons; `/review-codegen` shows the same gate for anything you missed. While it's pending — or if you reject it, or if you never act at all — the AI's interpreted approximation of the proposal is what stays live. The pending notification is retried on later governance ticks until delivered, but nothing auto-approves. See the Admin Runbook in `docs/OPS.md` for the full codegen toolset (`/review-codegen`, `/rerun-council`, `/disable-effect`, `/activate-mechanic`) and what to do when an effect auto-disables.

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
| `PINWHEEL_RULES_REQUIRE_APPROVAL` | Hold every passing proposal in a pending-admin state until you approve it (see Proposal Approval Gate) | `false` |
| `PINWHEEL_EVALS_ENABLED` | Run evals (grounding, prescriptive, GQI, flags, rule evaluator) after each round | `true` |
| `PINWHEEL_QUARTER_REPLAY_SECONDS` | How long each quarter takes in replay mode | `300` (5 min) |
| `PINWHEEL_GAME_INTERVAL_SECONDS` | Gap between games in a round during replay | `1800` (30 min) |
| `ANTHROPIC_API_KEY` | Claude API key. If unset, AI features fall back to mocks. | (unset) |

### Other Things to Know

- **Presentation survives restarts.** If a replay is in progress and the server redeploys, it picks up where it left off. The presentation state is persisted in the database, and on startup the system calculates how many quarters elapsed and skips ahead.
- **Completed seasons still tally governance.** After a season's games are done, the scheduler keeps running governance tally cycles so late votes still count.
- **Championship window.** When a season enters championship status, the scheduler checks a `championship_ends_at` timestamp. When the window expires, the season transitions to complete automatically.
- **All admin surfaces key off `PINWHEEL_ADMIN_DISCORD_ID`.** The same variable controls who gets wild-proposal and codegen DMs, who can access the admin web pages in production, and who can run the admin slash commands (`/new-season`, `/activate-mechanic`, `/review-codegen`, `/disable-effect`, `/rerun-council`). Discord's server Administrator permission is not consulted.

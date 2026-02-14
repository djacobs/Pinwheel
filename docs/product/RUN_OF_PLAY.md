# Pinwheel Fates: How It Works

## The Core Loop

**Govern → Simulate → Observe → Reflect → Govern**

Manage a basketball team, change the game to change the world.

## Joining

1. Type `/join TEAM` in Discord.
2. You are locked to that team for the season.
3. You receive 6 governance tokens: 2 PROPOSE, 2 AMEND, 2 BOOST.
4. You can now propose rule changes, vote, trade tokens, and set team strategy.

## Seasons

A season is a round-robin schedule. Four teams play each other. There are three rounds of six games, with a post-season to follow. 

After each round, votes and proposals are tallied, and winning proposals are put in effect immediately. 

When all regular-season games finish, the top 4 teams enter playoffs. Semifinals (best of 3), then finals (best of 5). The winner is crowned champion.

The season archives. A new season begins. Rules carry over to new seasons unless written otherwise. 

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

Ties fail. Votes are counted every 3 rounds. Passed proposals change rules immediately. Failed proposals do nothing.

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

# New Governor Guide

Welcome to Pinwheel Fates. This guide tells you exactly what to do, step by step.

## What Is This Game?

Pinwheel Fates is an auto-simulated 3v3 basketball league. You don't play basketball — AI-controlled hoopers do that. You **govern the rules**. You propose rule changes in plain English, vote on other people's proposals, and watch what happens when the rules change.

The game simulates itself. Games run automatically every few minutes. After every few rounds of games, the league tallies all the votes, changes the rules, and the next batch of games runs under the new rules. You watch the consequences of your Floor decisions play out in real time.

It starts as basketball. Where it ends is up to you.

## Getting Started

### Step 1: Join a Team

Type this in any channel where the Pinwheel bot is present:

```
/join
```

The bot will show you a list of teams with how many governors each team has. Pick one. Then type:

```
/join Rose City Thorns
```

(Replace with your team's actual name. The bot autocompletes — start typing and it will suggest matches.)

**What happens:** You become a governor of that team for the rest of the season. You get a team role in Discord, access to your team's private channel, and a welcome message listing your team's three hoopers (the AI players on your squad).

**You cannot switch teams mid-season.** Choose carefully.

### Step 2: Check Your Tokens

Type:

```
/tokens
```

The bot responds (only you can see it) with your current token balance:

```
PROPOSE: 2
AMEND: 2
BOOST: 2
```

These are your Floor tokens. They regenerate every time the league runs a Floor tally (every 3 rounds of games by default). Here's what each one does:

| Token | What It Does | Cost |
|-------|-------------|------|
| **PROPOSE** | Submit a rule change proposal | 1 per proposal (Tier 1-4) |
| **AMEND** | Modify someone else's active proposal | 1 per amendment |
| **BOOST** | Double your vote weight on one vote | 1 per boosted vote |

**Voting is free.** You never need tokens to vote.

---

## Proposals: How to Change the Rules

This is the core of the game. You propose rule changes in plain English. An AI interprets what you mean, maps it to a specific game parameter, and shows you what it thinks you're asking for. You confirm, revise, or cancel.

### Step 1: Write Your Proposal

Type this in any channel:

```
/propose Make three-pointers worth 5 points
```

The `text` parameter is required. Write what you want to change in natural language. Be specific. Examples:

- `/propose Increase the shot clock to 30 seconds`
- `/propose Make the Elam Ending start after the 2nd quarter instead of the 3rd`
- `/propose Turn off home court advantage`
- `/propose Require at least 2 passes before anyone can shoot`

The bot will say "thinking..." for a few seconds while the AI interprets your proposal.

### Step 2: Review the AI Interpretation

The bot sends you a private embed (only you can see it) that looks like this:

```
PROPOSAL INTERPRETATION

"Make three-pointers worth 5 points"

Parameter Change: three_point_value: 3 -> 5
Impact Analysis:  Sharpshooter archetypes benefit significantly.
                  Games will likely have higher scores. Teams with
                  strong three-point shooters gain an advantage.
Tier:            1 (Game Mechanics)
Cost:            1 PROPOSE token
Remaining:       1 PROPOSE
Confidence:      95%
```

Read this carefully. The AI has mapped your plain-English request to a specific parameter (`three_point_value`) and a specific new value (`5`). The impact analysis tells you what the AI thinks will happen.

**Three buttons appear beneath the embed:**

- **Confirm** (green) — Submit the proposal as-is. Spends your PROPOSE token. Posts the proposal publicly to the Floor for everyone to see and vote on.
- **Revise** (blue) — Opens a text box where you can rewrite your proposal. The AI will re-interpret the new text and show you an updated embed. You can revise as many times as you want before confirming. No tokens are spent until you confirm.
- **Cancel** (red) — Throws away the proposal. No tokens spent.

### Step 3: Confirm or Revise

If the AI got it right, click **Confirm**.

If the AI misunderstood, click **Revise**. A popup appears with a text box (500 character max). Rewrite your proposal more clearly. The AI re-interprets and you get a new embed with updated parameter, impact analysis, and confidence. You can keep revising until you're satisfied.

If you change your mind entirely, click **Cancel**.

### What Happens After You Confirm

Your proposal is now public. It appears on the Floor for all governors to see. It includes your original text, the AI's interpretation, the parameter change, and the impact analysis.

The proposal stays open for voting until the next Floor tally (every 3 rounds of games). During that time:
- Other governors can vote yes or no
- Other governors can amend your proposal (costs them 1 AMEND token)
- Everyone can discuss it

### What You Can Change

There are 33 governable parameters organized into 4 tiers. Here is every single one:

**Tier 1: Game Mechanics** (needs >50% to pass)

| Parameter | Default | Range | What It Does |
|-----------|---------|-------|-------------|
| `quarter_minutes` | 10 | 3-20 | How long each quarter lasts |
| `shot_clock_seconds` | 15 | 10-60 | How long a team has to shoot |
| `three_point_value` | 3 | 1-10 | Points for a three-pointer |
| `two_point_value` | 2 | 1-10 | Points for a two-pointer |
| `free_throw_value` | 1 | 1-5 | Points per free throw |
| `personal_foul_limit` | 5 | 3-10 | Fouls before a hooper fouls out |
| `team_foul_bonus_threshold` | 4 | 3-10 | Team fouls before bonus free throws |
| `three_point_distance` | 22.15 ft | 15-30 ft | Distance of the three-point line |
| `elam_trigger_quarter` | 3 | 1-4 | Quarter when the Elam Ending activates |
| `elam_margin` | 15 | 5-40 | Target score margin for Elam Ending |
| `halftime_stamina_recovery` | 0.40 | 0.0-0.6 | How much stamina hoopers recover at halftime |
| `quarter_break_stamina_recovery` | 0.15 | 0.0-0.3 | Stamina recovery between quarters |
| `safety_cap_possessions` | 300 | 50-500 | Max possessions before a game force-ends |
| `substitution_stamina_threshold` | 0.35 | 0.1-0.8 | Stamina level that triggers bench substitution |

**Tier 2: Hooper Behavior** (needs >50% to pass)

| Parameter | Default | Range | What It Does |
|-----------|---------|-------|-------------|
| `max_shot_share` | 1.0 | 0.2-1.0 | Max fraction of team shots one hooper can take |
| `min_pass_per_possession` | 0 | 0-5 | Minimum passes required before shooting |
| `home_court_enabled` | true | true/false | Whether home court advantage exists |
| `home_crowd_boost` | 0.05 | 0.0-0.15 | Accuracy boost for the home team |
| `away_fatigue_factor` | 0.02 | 0.0-0.10 | Extra fatigue for away teams |
| `crowd_pressure` | 0.03 | 0.0-0.10 | Accuracy penalty from hostile crowd |
| `altitude_stamina_penalty` | 0.01 | 0.0-0.05 | Extra stamina drain from altitude |
| `travel_fatigue_enabled` | true | true/false | Whether travel distance affects fatigue |
| `travel_fatigue_per_mile` | 0.001 | 0.0-0.005 | Stamina drain per mile traveled |

**Tier 3: League Structure** (needs >60% to pass)

| Parameter | Default | Range | What It Does |
|-----------|---------|-------|-------------|
| `teams_count` | 8 | 4-16 | Number of teams in the league |
| `round_robins_per_season` | 3 | 1-5 | How many times each team plays each other |
| `playoff_teams` | 4 | 2-8 | How many teams make the playoffs |
| `playoff_semis_best_of` | 5 | 1-7 | Semifinal series length |
| `playoff_finals_best_of` | 7 | 1-7 | Finals series length |

**Tier 4: Meta-Governance** (needs >60% to pass)

| Parameter | Default | Range | What It Does |
|-----------|---------|-------|-------------|
| `proposals_per_window` | 3 | 1-10 | Max proposals per Floor period |
| `vote_threshold` | 0.5 | 0.3-0.8 | Base vote threshold for passing proposals |

**You can change the rules about changing rules.** Tier 4 lets you alter the voting threshold itself. Want to require 80% supermajority to pass anything? Propose it. But you'll need 60% support to pass a Tier 4 change.

---

## Voting: How to Support or Block Proposals

### How to Vote

When there's an active proposal, type:

```
/vote yes
```

or

```
/vote no
```

That's it. The bot confirms your vote with a private message. Nobody else can see how you voted until the Floor tally.

### Boosting a Vote

If you feel strongly, you can spend a BOOST token to double your vote weight:

```
/vote yes boost:True
```

The `boost` parameter is optional and defaults to `False`. Set it to `True` to burn 1 BOOST token and double your weight on this particular vote.

### What Happens If Multiple Proposals Are Active

The `/vote` command targets the most recent unresolved proposal. If you need to vote on a specific proposal when multiple are active, the bot will guide you.

### How Vote Weight Works

Your vote weight depends on how many governors are on your team:

- 1 governor on your team: your vote weight is **1.0**
- 2 governors: each has weight **0.5**
- 3 governors: each has weight **0.33**
- 5 governors: each has weight **0.2**

**Every team's total voting power is always 1.0**, regardless of how many governors it has. This prevents one team from dominating by recruiting more governors.

If you use BOOST, your weight doubles. So a governor on a 3-person team using BOOST has weight 0.66 instead of 0.33.

### How Tallying Works

Floor tallies happen every 3 rounds of games. When a tally triggers:

1. All confirmed proposals with votes are tallied
2. For each proposal: sum of YES weights vs sum of NO weights
3. The proposal passes if `YES / (YES + NO) > threshold`
4. The threshold depends on the tier (see tables above)
5. **Ties fail.** If YES votes exactly equal the threshold, the proposal does not pass.
6. If passed, the rule change takes effect immediately — the next game uses the new rules
7. Votes are revealed

### Example Tally

A league with 4 teams. Each team has total voting power of 1.0. Maximum possible vote weight is 4.0.

| Team | Governors | Vote | Weight Each | Total |
|------|-----------|------|-------------|-------|
| Thorns | 3 | 2 yes, 1 no | 0.33 | 0.66 yes, 0.33 no |
| Breakers | 2 | 2 yes | 0.50 | 1.0 yes |
| Foxes | 1 | 1 no | 1.0 | 1.0 no |
| Wolves | 2 | 1 yes, 1 no | 0.50 | 0.5 yes, 0.5 no |

**Result:** YES = 2.16, NO = 1.83. Total = 4.0. YES/Total = 0.54. Threshold = 0.50 (Tier 1). **Passes.**

---

## Token Trading

You can trade tokens with other governors:

```
/trade @OtherGovernor offer_type:PROPOSE offer_amount:1 request_type:BOOST request_amount:2
```

This sends a trade offer to `@OtherGovernor`. They receive a DM with Accept/Reject buttons. They have 1 hour to respond.

**Why trade?** Maybe you don't plan to put anything on the Floor this cycle but want to boost two votes. Trade your PROPOSE tokens for someone else's BOOST tokens.

---

## Team Strategy

Set a strategic direction for your team's AI hoopers:

```
/strategy Focus on three-point shooting and fast breaks
```

The bot shows a Confirm/Cancel prompt. Once confirmed, this strategy influences how your hoopers play. You can change it anytime.

---

## Hooper Trades

You can trade hoopers (players) between teams:

```
/trade-hooper offer_hooper:Kai Swift request_hooper:Rosa Vex
```

The bot autocompletes hooper names. Your offered hooper must be on your team. The requested hooper must be on a different team. Both teams' governors must vote to approve the trade.

---

## What to Watch For

### The Web Dashboard

Visit the web dashboard to watch games live. The Arena page shows all games simultaneously with:
- Live scores updating possession by possession
- Play-by-play commentary
- Elam Ending countdown when it kicks in
- Quarter scores

Click into any game for the full box score.

### AI Mirrors

After each round of games, the AI generates mirrors — observations about what's happening in the league:

- **Simulation Mirror** (public): What happened in the games. Trends, upsets, stat leaders.
- **Floor Mirror** (public): Patterns in how governors are governing. Who's proposing what, voting patterns, coalition formation.
- **Private Mirror** (DM, only you see it): Personal observations about your Floor behavior — voting patterns you might not notice, how your proposals have affected your team.

View the latest mirrors:

```
/mirrors
```

The mirrors are the game's feedback mechanism. They show you consequences you can't see from inside the system.

---

## Other Commands

| Command | What It Does |
|---------|-------------|
| `/standings` | Current league standings (win-loss records) |
| `/schedule` | Upcoming game matchups |
| `/mirrors` | Latest AI mirror reflection |
| `/tokens` | Your current Floor token balance |
| `/join` | Join or view teams |

---

## Quick Reference: The Floor Cycle

1. **Games run** (automatically, every few minutes)
2. **You watch** the results on the web dashboard or Discord
3. **You propose** rule changes with `/propose` (costs 1 PROPOSE token)
4. **You debate** in your team channel or on the Floor
5. **You vote** on active proposals with `/vote yes` or `/vote no` (free)
6. **Every 3 rounds**, the Floor tallies: proposals pass or fail, rules change
7. **Tokens regenerate** (2 PROPOSE, 2 AMEND, 2 BOOST) after each tally
8. **Next games run** under the new rules
9. **AI mirrors** reflect what just happened — game patterns, Floor patterns, your patterns
10. **Repeat**

The game is: watch what happens, decide what should change, convince others, vote, watch the consequences.

---

## Frequently Asked Questions

**How do I see what's up for vote right now?**
Use `/vote` — the bot will tell you the current active proposal. You can also check the Floor page on the web dashboard, which shows all open proposals with their status.

**When does voting close?**
Floor tallies happen every 3 rounds of games. There's no visible countdown yet — watch for the "round completed" notifications in Discord and count rounds. We're adding a countdown timer soon.

**What happened to my proposal?**
After the Floor tally, check the web dashboard's Floor page. It shows whether each proposal passed or failed, with the final vote totals. Discord posts a summary when the tally runs.

**Can I change my vote?**
No. Once you `/vote`, it's locked in. Think before you vote — or save a BOOST token for the votes that matter most.

**What rules have already been changed?**
The rules page on the web dashboard shows the current ruleset. Any parameter that differs from the default was changed by a Floor vote.

**How do I talk to my team about strategy?**
Your team has a private Discord channel (e.g., `#rose-city-thorns`). Only governors on your team can see it. Use it to debate proposals, coordinate votes, and plan trades before going public.

**What does "2.50 Yes" mean in vote results?**
Vote weights are fractional because each team's total voting power is 1.0, split among its governors. "2.50 Yes" means the weighted sum of all yes votes is 2.50 — roughly equivalent to 2.5 teams voting yes. The raw vote count is shown alongside.

**Can I propose something weird like "the floor is lava"?**
Yes. The AI will try to interpret it. If it can't map your idea to a specific game parameter, it becomes a Tier 5 proposal — costs 2 PROPOSE tokens, needs 67% supermajority, and even if it passes, it can't change the rules because there's no parameter to change. Creative proposals work best when you tie them to a real mechanic: "Make fatigue drain 10% faster" is something the game can actually do.

**What's the Elam Ending?**
A real basketball innovation. Instead of playing the 4th quarter on a clock, the game sets a target score (leading team's score + the Elam margin, default 15). First team to hit the target wins. No clock, no fouling to stop the clock, no garbage time. Every game ends on a made basket. The `elam_trigger_quarter` and `elam_margin` parameters are both governable — you can change when it kicks in and how big the margin is.

**Where's my private mirror?**
Private mirrors are sent to your Discord DMs after each round. Make sure you have DMs enabled from server members. If you're not receiving them, check your Discord privacy settings.

**How do hooper trades work?**
Use `/trade-hooper` to propose swapping one of your hoopers for a hooper on another team. The bot posts the trade to both teams' channels. Every governor on both teams votes Approve or Reject. The trade only goes through if both teams approve.

**Do my tokens carry over between tallies?**
Yes. Unspent tokens accumulate. You get 2 PROPOSE, 2 AMEND, and 2 BOOST added to your balance every time the Floor tallies (every 3 rounds). Save them up or spend them fast — your choice.

**What if nobody on my team votes?**
Your team's voting power (1.0) goes to waste. The proposal can still pass or fail based on other teams' votes, but your team had no say. Vote on everything — it's free.

---

## Tips for Alpha Testers

- **Propose something in your first session.** Don't wait. Try `/propose Make the shot clock 30 seconds` and see what the AI does with it.
- **Read the impact analysis.** The AI tells you what it thinks will happen. Sometimes it's wrong. That's interesting too.
- **Vote on everything.** It's free. Your team's voting power goes to waste if you don't use it.
- **Use your team channel.** Coordinate with your team before voting. Private strategy is part of the game.
- **Watch the mirrors.** The Floor mirror will tell you things about the league's power dynamics that aren't obvious from individual votes.
- **Try weird proposals.** The parameter ranges are wide. Three-pointers worth 10 points? Shot clock of 60 seconds? Elam Ending starting in Q1? Go for it.
- **Report bugs.** This is alpha. If something breaks, tell us.

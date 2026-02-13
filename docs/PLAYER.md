# Pinwheel Fates: Player Experience & Community Platform

## Overview

Pinwheel Fates has two surfaces: a **web dashboard** for watching and a **Discord server** for governing. The dashboard is the stadium — you go there to watch games, check standings, read box scores, and see the AI reporter's public reflections. Discord is the floor of the legislature — you go there to debate, propose rules, vote, trade tokens, strategize with your team, and receive Opus 4.6's private reflections on your governance behavior.

## The Two Surfaces

### Web Dashboard (Watch)

The dashboard is a live-updating, spectator-friendly view of the league. It's built with HTMX + SSE + Jinja2 (see CLAUDE.md). Designed for both governors and spectators — anyone can watch.

The centerpiece is **The Arena** — a live multi-game view showing all 4 simultaneous games per round with AI-generated commentary, dramatic moment highlights, and Elam Ending countdowns. Beyond the Arena, the dashboard includes standings, box scores, team/agent pages, rule history, reports, and season stats.

**See `VIEWER.md` for the full viewer experience spec** — Arena layout, Single Game view, AI commentary engine architecture, API endpoints, dashboard pages, and presentation pacing.

**Auth:** Discord OAuth. Governors log in with their Discord account to see personalized content — their team highlighted, their private report accessible on the dashboard, their governance history. Spectators can view everything except private reports without logging in.

### Discord Server (Govern)

Discord is where the social game happens. All governance actions flow through a Discord bot (`Pinwheel`) that is friendly, conversational, and powered by the FastAPI backend and Opus 4.6.

**The bot is not a command terminal.** It's a character. It has personality. It responds conversationally, explains consequences, asks clarifying questions, and occasionally edits. It's the league commissioner, rules interpreter, and town crier rolled into one. Opus 4.6 powers its conversational ability; the API powers its actions.

## Discord Server Structure

### Channels

```
PINWHEEL FATES
│
├── 📢 LEAGUE-WIDE
│   ├── #announcements       → Bot posts: game results, standings, governance outcomes
│   ├── #game-day            → Live game updates relayed from dashboard
│   ├── #governance-floor    → Active proposals, voting, public debate
│   ├── #governance-log      → Append-only record of all governance actions
│   ├── #reports             → Shared AI reports (simulation, governance, series, season)
│   ├── #trash-talk          → Cross-team banter (spectators welcome)
│   ├── #rules               → Current ruleset, pinned and updated by bot
│   └── #new-governors       → Onboarding, team selection, FAQ
│
├── 🏀 TEAM CHANNELS (private, one per team)
│   ├── #rose-city-thorns        → Private debate, strategy, drafting proposals
│   ├── #burnside-breakers       → ...
│   ├── #... (8 teams)
│   └── Each team channel has:
│       ├── Strategy discussion
│       ├── Proposal drafting (before submitting publicly)
│       ├── Token balance visibility
│       ├── Private report delivery (individual DMs, but team reports here)
│       └── Bot responds to /strategy commands
│
├── 👤 DIRECT MESSAGES (bot → individual governor)
│   ├── Private reports — "You voted for X, which benefited team Y..."
│   ├── Token balance updates
│   └── Trade offers
│
└── 👀 SPECTATOR
    └── Read access to all league-wide channels
        No governance actions. Watch, react, discuss in #trash-talk.
```

### Roles

| Role | Who | Permissions |
|------|-----|-------------|
| **Governor** | Active player on a team | Full governance: propose, amend, vote, boost, trade. Access to their team's private channel. |
| **Spectator** | Anyone in the server | Read league-wide channels. React. Post in #trash-talk. No governance actions. |
| **Team: [Name]** | Governor assigned to a specific team | Access to that team's private channel. One team per governor per season. |
| **Commissioner** | The Pinwheel bot | Posts in all channels. Manages governance lifecycle. Delivers reports. |
| **Admin** | Server operator | Server management, season setup, emergency controls. |

## Governor Lifecycle

### 1. Joining

A new player joins the Discord server and lands in #new-governors. The bot greets them, explains the game, and asks them to pick a team.

```
🤖 Pinwheel: Welcome to Pinwheel Fates! You're about to become a governor
of a 3v3 basketball league where YOU make the rules.

Pick your team — you'll govern with them for the whole season.
Once you choose, you can't switch until the offseason. Choose wisely.
Your team's agents are counting on you.

🏀 Rose City Thorns — Kaia 'Deadeye' Nakamura, DJ 'The Wall' Baptiste, ...
🏀 Burnside Breakers — Indigo Moon, ...
🏀 ... (8 teams)

React with your team's emoji or type /join [team name].
```

**Self-selection.** This is deliberate — the tribalism is a feature. You pick your team because you *want* to be on that team. The emotional investment starts at signup.

### 2. Team Lock

Once a governor joins a team, they're locked in for the season. They cannot switch teams mid-season.

**Why:** Governors debate strategy in private team channels. Allowing mid-season transfers would leak intelligence. The commitment creates genuine stakes — your team's success is your success, and you can't bail when things go badly.

**Between seasons:** Governors can switch teams during the offseason governance window. The bot announces free agency, and players can `/transfer [team name]`. Teams can also recruit in #new-governors.

### 3. Governing

Governors interact with the league through bot commands and natural conversation in their team channels.

### 4. Leaving

Governors can leave mid-season (life happens), but their tokens are forfeited, not redistributed. The team's vote weight stays the same (normalized by *active* governors). The bot notes their departure in the governance log.

## Governance Actions (Bot Commands)

All governance actions are bot commands in Discord. The bot is conversational — it doesn't just execute commands, it responds with context, consequences, and personality.

### `/propose [natural language description]`

Submit a rule change proposal.

**Flow:**
1. Governor types `/propose "Make three-pointers worth 5 points"` (in team channel for drafting, or #governance-floor to submit immediately)
2. Bot sends the text to the AI interpreter (Opus 4.6, sandboxed)
3. AI returns a structured interpretation:
   ```
   🤖 Pinwheel: I hear you. Here's what that would look like:

   📋 Proposal: Change `three_point_value` from 3 → 5
   Tier: 1 (Game Mechanics)

   ⚠️ Impact analysis:
   - Sharpshooters become significantly more valuable
   - Games will end faster (more points per scoring possession)
   - Elam target will be reached sooner

   This costs 1 PROPOSE token. You have 2 remaining.
   React ✅ to submit, ❌ to cancel, ✏️ to revise.
   ```
4. Governor confirms → proposal posted to #governance-floor for voting
5. Bot creates a thread under the proposal for public debate

**Drafting in team channels:** A governor can `/propose` in their team's private channel first. The bot interprets it, the team discusses, and when ready, the governor types `/submit` to move it to #governance-floor. This means teams can workshop proposals privately before going public.

**Cost:** 1 PROPOSE token per submission to #governance-floor. Drafting in team channels is free.

### `/amend [proposal-id] [change]`

Modify an active proposal before voting closes.

```
🤖 Pinwheel: Amendment to Proposal #7:
Original: three_point_value: 3 → 5
Amendment by @governor: three_point_value: 3 → 4

"Splitting the difference. 5 was too aggressive." — @governor

This costs 1 AMEND token. React 👍 to support the amendment.
```

### `/vote [proposal-id] [yes/no]`

Cast your vote on an active proposal.

```
🤖 Pinwheel: @governor voted on Proposal #7.
(Votes are hidden until the governance window closes.
No peeking — politics is a private matter.)
```

**Votes are hidden until window close.** This prevents bandwagon voting and lets governors vote their conscience. The bot announces results when the window closes.

### `/boost [proposal-id]`

Spend a BOOST token to amplify a proposal's visibility — pins it, highlights it, and the bot draws attention to it in #announcements.

### `/trade [token-type] [amount] [to-governor]`

Trade tokens with another governor.

```
🤖 Pinwheel: @governor_a offers 1 PROPOSE token to @governor_b.
@governor_b, react ✅ to accept.

(A wise trade? Or a Faustian bargain?
The reporter will have thoughts.)
```

### `/tokens`

Check your current token balances.

```
🤖 Pinwheel: Your governance wallet:
  PROPOSE: 2 / 2
  AMEND:   1 / 2
  BOOST:   2 / 2

  Next regeneration: 3h 22m
```

### `/strategy [instruction]` (Team channels only, Day 1-2)

Submit a tactical instruction for your team's defensive/offensive strategy.

```
🤖 Pinwheel: Got it. Here's what I'm telling your team:

📋 Strategy: "Press defense in the Elam period"
Parsed: { scheme_override: "press", condition: { elam_active: true } }

⚠️ Warning: Press defense is exhausting. If your team's
Stamina is low by the Elam period, this could backfire.

React ✅ to activate, ❌ to cancel.
```

### `/rules`

View the current ruleset.

### `/standings`

View current standings.

### `/team`

View your team's roster, venue, record, and active strategies.

## Vote Normalization

**Core principle:** Each team's vote carries equal weight, regardless of how many governors it has.

### How It Works

If there are 8 teams:
- Each team has a **team vote weight** of 1.0
- The team's weight is divided equally among its active governors
- If Team A has 5 governors, each has vote weight 0.2
- If Team B has 2 governors, each has vote weight 0.5
- Both teams contribute a maximum of 1.0 to the total vote

**Passing threshold:** A proposal passes when the sum of weighted YES votes exceeds `vote_threshold` (default 0.5) × total possible vote weight (8.0 for 8 teams). So a proposal needs weighted YES votes > 4.0 to pass with default threshold.

**Within a team:** Governors on the same team may vote differently. If Team A has 5 governors and 3 vote YES while 2 vote NO, Team A contributes 0.6 to the YES total and 0.4 to the NO total.

**Why normalize:** Without normalization, a team with 10 governors dominates a team with 2. Normalization ensures the *tribal unit* is the team, not the individual. This creates a natural incentive for each team to recruit roughly equally, and makes intra-team politics matter — convincing your own team to vote together is as important as convincing other teams.

**Edge cases:**
- Team with 0 active governors: weight is 0 (they have no voice). Other teams' weights don't change — total possible weight decreases.
- Team with 1 governor: that governor has full 1.0 weight. Heavy responsibility, strong voice.
- Governor leaves mid-season: weight redistributes among remaining governors immediately.

## Token Economy

Each governor receives governance tokens that regenerate on a schedule (governed by Tier 4 meta-governance parameters).

### Token Types

| Token | Use | Default Regen |
|-------|-----|---------------|
| **PROPOSE** | Submit a proposal to #governance-floor | 2 per governance window |
| **AMEND** | Modify an active proposal | 2 per governance window |
| **BOOST** | Amplify a proposal's visibility | 2 per governance window |

**Voting is free.** Every governor can vote on every proposal without spending tokens. Tokens gate *action*, not *voice*.

### Token Trading

Governors can trade tokens with any other governor (within or across teams). This creates a secondary economy:
- A governor with no proposals to make can sell PROPOSE tokens to someone who's full of ideas
- Cross-team token trades create alliances and obligations
- The AI reporter tracks trading patterns — "Team A has been funneling PROPOSE tokens to one governor. What are they building toward?"

### Regeneration

Tokens regenerate at each governance window based on `propose_regen_rate`, `amend_regen_rate`, and `boost_regen_rate` (Tier 4 parameters, governable). Tokens cap at their regen rate — you can't stockpile indefinitely.

## Report Delivery

### Shared Reports → Discord Channels

When a shared report generates (simulation, governance, series, season, State of the League), the bot posts it to #reports with a summary and a link to the full analysis on the web dashboard.

```
🤖 Pinwheel: 📊 Governance Report — Round 7

The Rose City Thorns and Burnside Breakers voted together on
4 of 5 proposals this round-robin. A coalition is forming.
Meanwhile, nobody's talking about the quiet rule change in
Round 4 that gave high-Stamina teams a 15% edge in the Elam
period. The Iron Horses have won 5 straight.

Coincidence? The data says no.

Full analysis → [dashboard link]
```

### Private Reports → DMs

Private reports are delivered via bot DM to individual governors. No one else sees them.

```
🤖 Pinwheel: 🪞 Your Private Report — Round 7

You've voted YES on every proposal from @other_governor.
Every single one. You might not have noticed, but the
governance report did.

Those proposals have collectively benefited the Breakers
more than any other team. Your team, the Thorns, has
dropped two spots in the standings since Round 3.

Something to think about. Or not — you're the governor.
```

### Report Tone

Reports are not neutral summaries. They have voice. They're observational, sometimes pointed, occasionally funny. They notice things humans miss — coalition patterns, unintended consequences of rule changes, correlations between governance behavior and game outcomes. They never tell governors what to do. The reporter holds up a lens and lets players see themselves.

## Bot Personality

The Pinwheel bot is the league's commissioner, town crier, and constitutional interpreter. It is:

- **Conversational, not transactional.** It doesn't just execute commands — it responds with context, personality, and occasionally unsolicited observations.
- **Knowledgeable.** It knows the rules, the standings, the history. It can answer questions about what a rule change would do.
- **Impartial.** It never favors a team. Its AI-powered responses (via Opus 4.6) are instructed to be neutral on governance outcomes.
- **Witty but not annoying.** Personality without being in the way. It has a voice but knows when to be brief.
- **The interpreter.** When a governor proposes a rule in natural language, the bot interprets it into structured parameters. It explains its interpretation and lets the governor confirm, revise, or cancel. This is the AI-as-constitutional-interpreter design from CLAUDE.md.

**The bot does NOT:**
- Vote or express opinions on proposals
- Reveal hidden votes before window close
- Share private reports or team strategy with other teams
- Make governance decisions autonomously (except for Fate events, if enabled)

## Web ↔ Discord Integration

The web dashboard and Discord server share the same backend (FastAPI). They're two views of the same state.

```
┌─────────────────┐         ┌──────────────────┐
│  Web Dashboard   │         │  Discord Server   │
│  (HTMX + SSE)   │         │  (Pinwheel Bot)   │
│                  │         │                   │
│  Watch games     │         │  Govern           │
│  Read reports    │         │  Debate           │
│  View standings  │         │  Propose/Vote     │
│  Check box scores│         │  Trade tokens     │
│  Browse rules    │         │  Team strategy    │
│  Private report  │         │  Receive reports  │
│  (logged in)     │         │                   │
└────────┬─────────┘         └────────┬──────────┘
         │                            │
         │         ┌──────────┐       │
         └────────►│ FastAPI  │◄──────┘
                   │ Backend  │
                   │          │
                   │ core/    │
                   │ ai/      │
                   │ models/  │
                   │ db/      │
                   └──────────┘
```

**Auth flow:**
1. Governor clicks "Log in" on web dashboard
2. Redirected to Discord OAuth
3. Dashboard receives Discord user ID + guild membership
4. Backend maps Discord user ID → governor → team
5. Dashboard shows personalized content (team highlighted, private report, governance history)

**Real-time sync:**
- Game results computed by backend → pushed to dashboard via SSE AND posted to Discord via bot
- Governance actions submitted in Discord → processed by backend → reflected on dashboard in real time
- Reports generated by backend → delivered to Discord AND displayed on dashboard

## Implementation Priority

1. **Discord bot scaffolding** — connect to Discord API, register slash commands, basic channel management
2. **`/join` and team assignment** — governor registration, role assignment, team lock
3. **`/propose` → AI interpretation → confirm flow** — the core governance loop
4. **`/vote` with hidden votes + window close reveal** — voting lifecycle
5. **Token management** — balances, spending, regeneration, display
6. **Report delivery** — bot posts shared reports to channels, private reports via DM
7. **`/trade`** — token trading between governors
8. **`/strategy`** — team tactical overrides (Day 1-2)
9. **Discord OAuth for web dashboard** — personalized dashboard experience
10. **`/amend`, `/boost`** — remaining governance actions

## Decisions

1. **Governor minimum per team:** None. A team with 0 governors has 0 vote weight. That's their problem.
2. **Cross-team communication:** Allowed. Governors can DM each other through the bot. Back-channel dealing is politically interesting — and the reporter may notice patterns even if it can't see the messages.
3. **Proposal debate threads:** Yes. Bot auto-creates a thread for each proposal in #governance-floor.
4. **Bot personality:** The bot responds to governance actions but does not insert itself into player conversations. Players are the personality. The bot's personality may evolve over time, but it starts restrained.

## Open Questions

1. **Report frequency vs. cost:** Every report is an Opus 4.6 API call. With 8 report types and 21 rounds, that's a lot of calls. Batch at a minimum. Can some reports be cached (e.g., if no rule changes happened, skip the simulation report)? Can reports share context across calls to reduce redundancy? Needs costing analysis once we know actual API usage.

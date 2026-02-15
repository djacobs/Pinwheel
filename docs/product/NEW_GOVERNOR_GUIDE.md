# New Governor Guide

Welcome to Pinwheel Fates. You're about to remake basketball.

## What Is This Game?

Pinwheel Fates is a basketball league that doesn't want to stay basketball. AI hoopers play 3v3 games automatically. You never touch the ball. What you touch are **the rules** — and through the rules, *everything*.

You propose changes in plain English. "Make three-pointers worth 10 points." "The floor is lava." "Nobody can shoot until they've passed three times." The game interprets your intent, maps it to the simulation, shows you what it understood. You confirm, the league votes, and if it passes — the next game runs in a different world.

Games run every few minutes. Every few rounds, the Floor tallies votes and rewrites reality. The next batch of games runs under your new rules. You watch the consequences cascade in real time.

**It starts as basketball. It becomes whatever you make it.**

---

## Getting Started

### Join a Team

```
/join
```

The bot shows you the teams. Pick one. You're locked in for the season — choose the hoopers you believe in, or the governors you want to conspire with.

```
/join Rose City Thorns
```

You get a team role, a private team channel, and three hoopers. They are your athletes. You are their legislature.

### Your Floor Tokens

```
/tokens
```

You start with political currency:

-   **PROPOSE** (2) — Put something on the Floor. This is agenda-setting power. The most valuable token in the game.
    
-   **AMEND** (2) — Rewrite someone else's proposal before it goes to vote. You can't stop them from proposing, but you can change what they proposed.
    
-   **BOOST** (2) — Double your voting weight on a single vote. Save it for the vote that matters most.
    

Tokens regenerate every tally round (every round by default, configurable via `PINWHEEL_GOVERNANCE_INTERVAL`) and accumulate if unspent. They're tradeable — your PROPOSE token is someone else's leverage.

**Voting is always free.** You never need tokens to vote.

---

## The Floor: How to Reshape the Game

The game interprets your intent, maps it to the simulation, and shows you what it understood. Then you decide: confirm it, revise it, or walk away.

```
/propose Make three-pointers worth 10 points
```

The game thinks for a few seconds, then sends you a private interpretation:
```

PROPOSAL INTERPRETATION

"Make three-pointers worth 10 points"

Parameter Change: three\_point\_value: 3 -> 10  
Impact Analysis: This fundamentally changes game dynamics.  
Sharpshooter archetypes become dominant.  
Scores will roughly double. Teams without  
strong shooters are in serious trouble.  
Tier: 1 (Game Mechanics)  
Cost: 1 PROPOSE token  
Confidence: 98%

```

The game does the work of figuring out which simulation lever to pull and predicts the consequences.

Three buttons:

-   **Confirm** — Spend the token. Post it to the Floor. Let the league decide.
    
-   **Revise** — Rewrite it. The game re-interprets. No tokens spent until you confirm.
    
-   **Cancel** — Walk away. No cost.
    

### What Can You Propose?

Anything. The game is your interpreter — your constitutional translator. You provide the imagination; the game figures out how to express it in the simulation.

**Change how the game plays:**

-   "Games should be shorter — end them after the 2nd quarter"
    
-   "Make every shot worth 1 point"
    
-   "If you're losing by 20, the game should just end"
    

**Change how hoopers behave:**

-   "Nobody can take more than a third of their team's shots"
    
-   "Require 4 passes before anyone can shoot"
    
-   "Turn off home court advantage — every game is neutral"
    

**Change the shape of the league:**

-   "Only 2 teams should make the playoffs"
    
-   "Make the finals a single winner-take-all game"
    

**Change the rules about changing rules:**

-   "Require 80% supermajority to pass anything"
    
-   "Make it so only 1 proposal can be on the Floor at a time"
    

**Propose something the game has never seen:**

-   "The floor is lava" — the game might crank up fatigue, eliminate recovery, make every step cost stamina. Your hoopers are playing on a hostile surface now. The commentary will describe it.
    
-   "Play the game underwater" — the game could slow everyone down, drain stamina faster, make shooting harder. The *feel* is underwater. The mechanics adapt.
    
-   "Ban defense" — maybe every shot becomes uncontested. Maybe defensive stats drop to zero. The game finds the closest expressible version and shows you.
    

If the game can't express your idea perfectly, it'll show you the closest version it can build and explain the gap. Sometimes the gap is the most interesting part — it becomes a conversation on the Floor about what the game *should* be able to do.

The bigger proposals (league structure, meta-governance, wild ideas) need broader consensus — more votes to pass. Simple game mechanics need a simple majority. Changing the rules about changing rules needs a supermajority. This is by design: the wilder the change, the more people have to want it.

### After You Confirm

Your proposal goes public. Every governor can see your original words, the game's interpretation, and the predicted impact.

You can propose and amend anytime between tallies — the window is always open. Games keep running while the Floor debates. Everything submitted before the tally gets counted; anything after rolls into the next one.

During the window:

-   Governors vote yes or no
    
-   Someone can spend an AMEND token to rewrite your proposal (the amended version replaces yours on the ballot — you don't get a veto, just the chance to argue against the change)
    
-   Everyone debates in team channels and on the Floor
    

---

## What Can This Become?

The parameters have wide ranges. Changes compound. After a few rounds of governance, you might not recognize what you're watching. Some possibilities:

### The Shootout

Someone proposes three-pointers worth 10. It passes. Then someone moves the Elam Ending to start after the 1st quarter with a tiny margin. Now games are 90-second scoring explosions — two teams sprinting to hit a target score, every three-pointer a seismic event. The commentary is breathless. The box scores are absurd. This is not basketball. It's something faster.

### The Beautiful Game

A coalition pushes through "require 4 passes before anyone can shoot" and "no hooper can take more than 25% of their team's shots." Individual heroics are dead. Every possession is a passing clinic. The hoopers with high Passing and IQ stats — previously overlooked — are suddenly the most valuable players in the league. Teams that can't move the ball can't score. You've reinvented the sport around teamwork.

### The Gauntlet

Fatigue cranked up. Recovery eliminated. The Elam margin set to 40. Games go long. Hoopers collapse. The bench player — the one everyone ignored — becomes the most important hooper on the roster because they're the only one still standing in the 4th quarter. Iron Horse archetypes are kings. Every game is an endurance trial.

### Governance Lock

Someone proposes raising the vote threshold to 80%. It passes with 62% support. Now almost nothing else can pass. The current rules are frozen. The league calcifies. The reporter starts noting that the same team keeps winning under rules that favor them. Other governors scramble to build a supermajority coalition to undo the lock — but the team on top is trading tokens to keep the coalition from forming. This is no longer a basketball game. It's a political crisis.

### Your Version

We don't know what you'll build. That's the point. Every Pinwheel season ends somewhere different. The game is a canvas. Basketball is the primer coat.

---

## Voting: Power and Coalition

Voting is where proposals live or die. It's free, it's secret until the tally, and it's the most important thing you do.
```

/vote yes

/vote no

```

That's it. The bot confirms privately. Nobody sees your vote until the Floor tally resolves.

### Boosting

If a vote matters enough to spend a token on:
```

/vote yes boost:True

```

This burns 1 BOOST token and doubles your weight on that vote. Save it for the proposal that changes everything — or the one you need to kill.

### How Power Works

Every team has equal voting power, regardless of how many governors it has. This means:

-   On a team by yourself, your vote carries your team's full weight
    
-   On a crowded team, you share power with your co-governors
    
-   BOOST lets you punch above your weight when it counts
    

The political game is real. Trade tokens with rivals. Coordinate votes in your team channel. Build cross-team coalitions for the big proposals. Betray those coalitions when the moment is right. The reporter is watching — and it will tell the league what it sees.

### The Tally

Every round (by default), the Floor tallies:

1.  Every open proposal gets counted — weighted yes vs. weighted no
    
2.  Simple proposals (game mechanics, hooper behavior) need a majority to pass
    
3.  Bigger proposals (league structure, meta-governance) need a supermajority
    
4.  Ties fail. Close calls are noted by the reporter — "one vote away" is a story
    
5.  Passed rules take effect immediately. The next game runs in the new world
    
6.  All votes are revealed. Everyone sees who voted which way
    
7.  Tokens regenerate. The next governance window opens immediately
    

---

## Token Trading

Tokens are political currency. Trade them.
```

/trade @OtherGovernor offer\_type:PROPOSE offer\_amount:1 request\_type:BOOST request\_amount:2

```

They get a DM with Accept/Reject. One hour to respond.

Why trade? Because a PROPOSE token in the right hands changes the game. You might not have a proposal in mind, but someone on another team does — and they'll pay for the privilege of agenda-setting. Or you need three BOOSTs to kill a proposal that would destroy your team, and the only way to get them is to deal.

Trades are visible to the reporter. It will notice if two teams are trading heavily. It will notice if one governor is hoarding. The politics are part of the game.

---

## Team Strategy

Tell your hoopers how to play:
```

/strategy Focus on three-point shooting and fast breaks

```

The game interprets your strategy into something the simulation understands. Confirm it, and your hoopers adjust. Change it anytime. This is coaching — and under different rule regimes, the right strategy changes completely.

---

## Hooper Trades

Trade hoopers between teams:
```

/trade-hooper offer\_hooper:Kai Swift request\_hooper:Rosa Vex

```

Both teams vote. Both must approve. The reporter will note who moved where and what it means for the balance of power.

---

## The Reports

After each round, the AI files reports — observations about what's really happening:

-   **Simulation Report** (public): What the games revealed. Which rule changes are working. Which teams are rising or falling and why.
    
-   **Floor Report** (public): The politics. Who's voting together. Who's trading with whom. Where power is concentrating. Which voices are being drowned out.
    
-   **Private Report** (DM, only you): What the game sees about *you*. Your voting patterns. Your blind spots. The consequences of your proposals that you might not have noticed. This one is just for you.
    
```

/reports

```

The reports show you what you can't see from inside the system. What you do with that information is governance.

---

## Commands

<table class="prose-table" style="min-width: 50px;"><colgroup><col style="min-width: 25px;"><col style="min-width: 25px;"></colgroup><tbody><tr><th colspan="1" rowspan="1"><p>Command</p></th><th colspan="1" rowspan="1"><p>What It Does</p></th></tr><tr><td colspan="1" rowspan="1"><p><code>/join</code></p></td><td colspan="1" rowspan="1"><p>Join a team</p></td></tr><tr><td colspan="1" rowspan="1"><p><code>/propose</code></p></td><td colspan="1" rowspan="1"><p>Put a rule change on the Floor</p></td></tr><tr><td colspan="1" rowspan="1"><p><code>/vote</code></p></td><td colspan="1" rowspan="1"><p>Vote on the current proposal</p></td></tr><tr><td colspan="1" rowspan="1"><p><code>/tokens</code></p></td><td colspan="1" rowspan="1"><p>Check your token balance</p></td></tr><tr><td colspan="1" rowspan="1"><p><code>/trade</code></p></td><td colspan="1" rowspan="1"><p>Trade tokens with another governor</p></td></tr><tr><td colspan="1" rowspan="1"><p><code>/trade-hooper</code></p></td><td colspan="1" rowspan="1"><p>Trade hoopers between teams</p></td></tr><tr><td colspan="1" rowspan="1"><p><code>/strategy</code></p></td><td colspan="1" rowspan="1"><p>Set your team's play strategy</p></td></tr><tr><td colspan="1" rowspan="1"><p><code>/bio</code></p></td><td colspan="1" rowspan="1"><p>Write a backstory for one of your hoopers</p></td></tr><tr><td colspan="1" rowspan="1"><p><code>/profile</code></p></td><td colspan="1" rowspan="1"><p>View your Floor record</p></td></tr><tr><td colspan="1" rowspan="1"><p><code>/standings</code></p></td><td colspan="1" rowspan="1"><p>Current league standings</p></td></tr><tr><td colspan="1" rowspan="1"><p><code>/reports</code></p></td><td colspan="1" rowspan="1"><p>Latest report</p></td></tr></tbody></table>

---

## The Loop

1.  **Games run** automatically
    
2.  **You watch** on the web dashboard or Discord
    
3.  **You imagine** what the game should become
    
4.  **You propose** it in plain English (`/propose`)
    
5.  **You build coalitions** — debate, trade, persuade
    
6.  **You vote** (`/vote yes` or `/vote no`)
    
7.  **The Floor tallies** — rules change, tokens regenerate
    
8.  **Games run under the new rules** — watch what you built
    
9.  **The reports** tell you what really happened
    
10.  **Repeat** — until it isn't basketball anymore
     

---

## FAQ

**Can I propose something weird?**  
Yes. Please. "The floor is lava." "Gravity is doubled." "Every game is sudden death." The game will interpret your proposal into something the simulation can express. If your idea is too wild to map directly, the game shows you the closest version it can build — and that gap is a conversation worth having. The weirdest proposals often produce the best games.

**What's the Elam Ending?**  
A real basketball innovation we borrowed. Instead of a timed 4th quarter, the game sets a target score: the leader's score plus a margin. First team to hit it wins — on a made basket. No clock. No garbage time. Every game ends at its climax. And you can change when it triggers and how big the margin is. Set the margin to 5 and the trigger to Q1, and the whole game becomes a sprint to a target.

**How do I see what's up for vote?**  
`/vote` — the bot tells you. Or check the Floor page on the web dashboard.

**Can I change my vote?**  
No. Commit.

**What does "2.50 Yes" mean?**  
Voting power is weighted — each team's total power is equal, split among its governors. "2.50 Yes" means roughly 2.5 teams' worth of support. The raw vote count is shown alongside.

**How do hooper trades work?**  
`/trade-hooper` to propose a swap. Both teams vote. Both must approve. The reporter takes note.

**Where's my private report?**  
Check your Discord DMs. If you're not getting them, make sure DMs from server members are enabled in your Discord privacy settings.

**Do tokens carry over?**  
Yes. Hoard them or spend them. 2 of each type are added every tally round.

**What if nobody on my team votes?**  
Your team's power goes to waste. The proposal can still pass or fail without you, but you had no say. Voting is free. Use it.

**Can I change the rules about changing rules?**  
Yes. That's the deepest level of the game. Raise the vote threshold and governance freezes. Lower it and everything changes every tally. Change how many proposals can be on the Floor at once. This is meta-governance — governing governance — and it's where Pinwheel gets genuinely strange.

---

## First Session

-   **Propose something.** Anything. `/propose Make the shot clock twice as long` — see what happens. You won't break anything.
    
-   **Vote on everything.** It's free. Your silence is someone else's majority.
    
-   **Read the reports.** They'll tell you things about the league's power dynamics that aren't obvious from individual votes.
    
-   **Use your team channel.** Coordinate before going public. Private strategy is part of the game.
    
-   **Go weird.** The game rewards imagination. The sensible proposals are fine. The unhinged ones are where it gets interesting.
    
-   **Report bugs.** This is alpha. Tell us what breaks.
```

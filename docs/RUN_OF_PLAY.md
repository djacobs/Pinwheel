# Pinwheel: Run of Play

## The Core Loop

Players never play basketball. They govern a basketball league. The simulation runs itself; human agency lives entirely in the governance layer. The game loop is:

**Govern → Simulate → Observe → Reflect → Govern**

This loop runs continuously. Games happen throughout the day. Governance windows open twice daily. The AI reflects between and during both. The tight loop means you never have to wait long to see what your decisions did.

## Daily Cadence

A typical day in Pinwheel:

**Morning Governance Window (~30 min)**
Players review overnight game results and AI observations. Propose rules, amend active proposals, trade governance tokens, debate in the group feed, and vote. Passed rules take effect immediately.

**Morning Simulation Block (4-5 games per team)**
The simulation engine runs the day's first set of matchups under the current ruleset. Results post in real time: box scores, play-by-play highlights, standings updates. The AI simulation mirror updates with pattern observations.

**Evening Governance Window (~30 min)**
A second governance cycle. Players have now seen their morning rules play out across several games. Adjust course, propose corrections, double down. The feedback is fresh.

**Evening Simulation Block (4-5 games per team)**
More games under the (possibly updated) rules. Daily totals, standings shifts, and AI digest generated.

**Night: AI Daily Digest**
Opus 4.6 generates a league-wide summary and private mirror updates for each player. These are waiting when players return the next morning.

*For hackathon demo: accelerate everything. Games every 2-3 minutes, governance windows every 15 minutes. A judge should see a full govern→simulate→observe→reflect cycle in under 10 minutes.*

## The Simulation

3v3 basketball, auto-simulated possession by possession.

Each agent has nine core attributes: Scoring, Passing, Defense, Speed, Stamina, IQ, Ego, Chaotic Alignment, and Fate. These interact with the current ruleset to determine behavior. A possession plays out as a sequence of decisions — pass, drive, shoot, contest — resolved probabilistically based on agent attributes, defensive scheme, matchups, and rules. See SIMULATION.md for the full attribute model, archetype table, and defensive model.

A game produces: a box score (points, assists, rebounds, steals, turnovers per agent), a compressed play-by-play log, and metadata (pace, shot distribution, lead changes). The simulation is deterministic given a seed, so any game can be replayed and any rule change can be A/B tested against historical games.

The simulation must be a pure function: `(teams, rules, rng_seed) → game_result`. No side effects. This is the contract that makes the system trustworthy and the Rust port clean.

## The Governance Layer

### Tokens

Each player holds three types of governance tokens:

- **PROPOSE** — Submit a rule change in natural language. The most powerful token: you set the agenda.
- **AMEND** — Modify an active proposal before it goes to vote. Lets you shape what you can't control.
- **BOOST** — Double your voting weight on a single vote. The kingmaker token.

Tokens regenerate slowly: 1 of each per governance cycle (twice daily). They're tradeable between any players — teammates or opponents. This creates a political economy around governance itself.

Token trades are public (the governance mirror will note them) but the terms are private. You can see that Player A traded with Player B, but not what was exchanged. Unless someone tells the group — or the AI notices a pattern.

### Proposals

A player spends a PROPOSE token and writes a rule change in natural language. Examples:

- "Three-point shots should be worth 4 points"
- "No agent can take more than 40% of their team's shots"
- "Add a mercy rule: if a team is down by 20, the game ends"
- "Change the voting threshold from simple majority to two-thirds"
- "Reduce PROPOSE token regeneration to one per day instead of two"

The proposal enters the **AI interpretation pipeline**:

1. **Quarantine:** The raw text is isolated. It never touches the simulation engine's context.
2. **Interpretation:** Opus 4.6, operating in a sandboxed context with strict system instructions, parses the proposal into a structured rule modification. "I interpret this as: [structured rule]. Estimated impact: [brief analysis]."
3. **Injection defense:** Anything that isn't a valid rule modification — including prompt injection attempts, ambiguous language, or proposals that can't map to simulation parameters — gets flagged with an explanation. The AI may ask the proposer to clarify.
4. **Publication:** The structured interpretation is posted to the league. All players can see the original text and the AI's interpretation side by side.
5. **Amendment window:** Other players can spend AMEND tokens to modify the proposal before the vote. An amendment is itself a natural language statement ("Make it 4 points instead of 5"). The AI interprets the amendment in the context of the original proposal, producing a revised structured rule. The amended version *replaces* the original on the ballot — there is no split vote between original and amended versions. The original proposer has no veto over amendments; their recourse is to argue against the amendment in the feed, or to cancel the proposal before it goes to vote. Multiple amendments can be submitted; each one replaces the previous version. This creates a miniature legislative process: propose → amend → counter-amend → vote on the final form.
6. **Vote:** All players vote. BOOST tokens can be spent here. Simple majority passes (unless the players have changed this meta-rule). Votes are hidden until the governance window closes to prevent bandwagon effects.
7. **Enactment:** Passed rules modify simulation parameters through a typed, validated interface. The simulation engine only accepts structured rule objects, never raw text.

### The Rule Space

What can players change? Four tiers, from shallow to deep:

**Tier 1: Game Mechanics**
Shot clock duration, foul limits, court dimensions, scoring values, overtime rules, quarter length. These are the knobs that directly affect simulation behavior. Easy to understand, consequences visible within a few games.

**Tier 2: Agent Behavior Constraints**
Shot attempt limits, minutes restrictions, mandatory passing rules, defensive assignment rules. These constrain how agents play within the game mechanics. More subtle; consequences emerge over many games.

**Tier 3: League Structure**
Schedule format, draft/trade rules, salary caps, promotion/relegation, playoff structure. These reshape the competitive landscape. Consequences are structural and long-term.

**Tier 4: Meta-Governance**
Token regeneration rates, voting thresholds, proposal limits, veto mechanics, what the AI reports on. These are changes to the rules of rule-changing. The most powerful and dangerous tier. When players start governing the governance system, the game reaches its deepest expression.

### Token Trading & Fungibility

Governance tokens can be traded between any players. Possible trades:

- Governance tokens for governance tokens (PROPOSE for 2 AMENDs)
- Governance tokens for agent trades (I'll give you my best scorer for 3 PROPOSE tokens)
- Governance tokens for information (I'll tell you how Team 4 is voting if you give me a BOOST)
- Bulk deals, conditional trades, futures ("I'll give you a PROPOSE next cycle if you BOOST my proposal today")

The trading interface should be simple: offer, accept/reject. The complexity comes from the social dynamics around trading, which the AI will surface.

## The Three AI Layers

### Layer 1: Simulation Mirror (Shared)

Pattern observations about game outcomes and rule effects. Visible to all players.

Examples:
- "Since the three-point line was moved back, Team A's win rate dropped from 60% to 35%. Their best agent's range no longer reaches the new line."
- "The mercy rule has ended 4 of the last 10 games early. Average game length is down 18%."
- "The new shot-attempt cap has equalized scoring across agents. Team variance in points-per-agent dropped from 12.3 to 4.1."

### Layer 2: Governance Mirror (Shared)

Pattern observations about governance dynamics. Visible to all players.

Examples:
- "Player 3 proposed the three-point line change. Player 3's team is the only one with an agent whose range exceeds the new line. Every rule Player 3 has proposed this week has increased their team's structural advantage."
- "Teams 1 and 4 have voted the same way on the last 6 proposals. A voting bloc appears to have formed."
- "The last four rule changes were proposed by three players who traded PROPOSE tokens among themselves. Two players haven't had a successful proposal in nine days."

### Layer 3: Private Mirror (Per-Player)

Behavioral reflections visible only to the individual player. This is the deep water.

Examples:
- "You've voted against every proposal that would redistribute resources downward. You've also traded away your AMEND tokens three times to avoid negotiating modifications to proposals you disliked. Other players appear to be routing trades around you."
- "Your co-governor has been more active in cross-team trading than intra-team discussion over the past two days. Your team's internal alignment on proposals has dropped."
- "You've used 80% of your PROPOSE tokens on Tier 1 rule changes (game mechanics). No proposals from you have touched league structure or meta-governance. Your influence is concentrated in the shallowest layer of the rule space."

The private mirror never prescribes. It reflects. The player decides what to do with the information.

## Player Interaction Surface

### The Dashboard (Always Visible)
League standings, recent game results, upcoming matchups, your team's roster and agent stats. The scoreboard of the world you're governing.

### The Governance Panel
Your token balance, active proposals with AI interpretations, voting interface, token trading marketplace, and the history of all passed and failed proposals. This is where you exercise power.

### The Mirror Panel (Private)
Your private AI reflections, updated after each governance window and simulation block. A running log of self-knowledge earned through play.

### The Feed: Three-Layer Communication Topology

The social game of governance depends on where conversations happen and who can see them. Pinwheel's feed architecture has three layers, each serving a different function. The AI observes all three layers (to different degrees) so that the mirror system can surface dynamics that span them.

**Layer 1: The Public Square (League-Wide Feed)**
AI simulation and governance mirror observations, game highlights, proposal announcements, vote results, and open debate. Visible to all players and spectators. This is where coalitions are forged in public, where persuasion happens, and where the governance mirror posts its shared analysis. On Discord, this maps to #announcements, #game-day, #governance-floor, and #mirrors.

**Layer 2: The Legislature Floor (Proposal Threads)**
Each proposal gets its own threaded discussion. The original text, the AI's structured interpretation, any amendments, and the debate about that specific proposal all live in one thread. This is where the sausage gets made — line-by-line argumentation, amendment counter-proposals, and tactical positioning. On Discord, these are threads under each proposal in #governance-floor.

**Layer 3: The War Room (Team Channels)**
Private discussion space for co-governors of a team. Strategy, coordination, internal governance, and proposal drafting before public submission. The AI can observe team channel content for private mirror reflections but doesn't post here — the war room belongs to the governors. On Discord, these are the private team channels (#rose-city-thorns, #burnside-breakers, etc.).

## Conflict Resolution

Governance produces edge cases. When the system encounters contradictions, ambiguity, or failures, it resolves them through a defined hierarchy rather than ad hoc judgment.

### Contradictory Rules

Two rules may pass in the same governance window that contradict each other (e.g., "three-pointers are worth 5" and "three-pointers are worth 1"). Resolution: proposals are ordered by their submission timestamp. The later proposal overwrites the earlier one for any parameter they both modify. The governance log records both, and the AI mirror notes the contradiction — "Two proposals about three-point values passed in the same window. The later one (worth 1) takes precedence. The first proposer may want to revisit their approach."

### Governance Window Ties

A proposal that receives exactly 50% weighted YES votes (on a `vote_threshold` of 0.5) does not pass. The threshold is strictly greater-than, not greater-than-or-equal. Ties fail. The proposer can resubmit in the next window. The mirror notes the tie and the near-miss — "Proposal #12 was one vote away. The Rose City Thorns split 3-2 internally. If one governor had flipped, it would have passed."

### Simulation Errors from Enacted Rules

If an enacted rule produces a simulation error (parameter combination that causes a division-by-zero, a probability outside [0, 1], or an infinite loop in effect chaining), the system responds in layers: first, Pydantic validation catches out-of-range values before enactment (this should prevent most errors). Second, if an error slips through validation and surfaces during simulation, the offending rule is automatically rolled back to its previous value, the game re-simulates under the corrected ruleset, and the rollback is logged as a governance event. The mirror reports: "The rule enacted from Proposal #14 caused a simulation error and was automatically reversed. The parameter has returned to its previous value." The proposer is refunded their PROPOSE token.

### Effect Stacking Conflicts

Multiple Game Effects active in the same game can produce conflicting actions (one grants a possession, another forces a substitution on the same trigger). Resolution: effects are ordered by enactment order (the order in which their source proposals passed). Earlier-enacted effects resolve first. If the combined effects would exceed safety boundaries (e.g., infinite possession chain), the depth limit (max 3 levels of effect chaining) applies and excess effects are suppressed for that trigger event. See SIMULATION.md for the full safety boundaries.

### Disconnected Governors

If a governor disconnects or goes inactive during a governance window, their vote is not cast. Their team's vote weight redistributes among active governors immediately. If all governors on a team are inactive, that team's weight drops to zero for that window and total possible vote weight decreases accordingly. The mirror tracks inactivity patterns.

## What Makes This Fun

The joy of Pinwheel lives in four feelings:

1. **The thrill of consequence.** You proposed a rule. You watched it pass. You watched your team lose three straight because of it. Now what?
2. **The satisfaction of insight.** The private mirror tells you something about yourself you didn't see. You change your approach. It works.
3. **The drama of politics.** Two teams form a coalition. The other four scramble. Someone betrays the coalition for a short-term agent trade. The AI reports it all.
4. **The pride of craft.** You and your co-governor designed a rule that made the league better for everyone. The standings are more competitive. The games are closer. You built that.

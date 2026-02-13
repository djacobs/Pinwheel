# Pinwheel Fates: Simulation Engine Spec

## Design Constraint

The simulation engine is a pure function:

```
simulate_game(home: Team, away: Team, rules: RuleSet, seed: int) → GameResult
```

No side effects. No database access. No API calls. Deterministic given the same seed. This is the contract that makes everything else possible — testing, replay, A/B comparison of rule changes, and eventually the Rust port.

## Why Basketball (3v3)

3v3 basketball has properties that serve the governance layer:

- **Individual impact is legible.** A single dominant agent warps the entire game's geometry — spacing, shot selection, defensive assignments all shift. Rule changes that target one agent's behavior ripple through every other agent on the floor.
- **Team dynamics are irreducible.** The best individual loses to the best team. Passing, spacing, and defensive coordination can't be reduced to individual stats. This means governance must consider relationships, not just attributes.
- **The rule space is intuitively understandable.** Shot clocks, foul limits, scoring values, court dimensions — anyone who's watched basketball grasps these immediately. Low barrier to governance participation.
- **Games are fast to simulate.** A 3v3 possession resolves in seconds of compute. A full game in under a second. This enables the tight govern → simulate → observe → reflect loop.
- **Basketball is beautiful.** The simulation computes in milliseconds, but fans experience games over 20-30 minutes — play-by-play streamed in real time, tension building toward moments the simulation has already decided. Because we know the whole story before we start telling it, we can use visualizations, illustrations, and animations to build toward the moments that matter. The natural grammar of basketball (possessions, runs, comebacks, buzzer-beaters) combined with the absurd, comic-book-like laws designed by the players themselves will produce games that are dramatic, unexpected, and genuinely fun to watch. Results are deterministic (same seed, same game — essential for replay and fairness) but never predictable from the stands.

## Agent Model

Each agent has nine core attributes, scored 1–100:

| Attribute | What It Governs |
|-----------|----------------|
| **Scoring** | Shot accuracy at all ranges. Higher = better shooter. A’Ja Wilson is an the platonic ideal of a scorer. |
| **Passing** | Assist quality, turnover avoidance when passing, tendency to find open teammates. Chelsea Gray is the best living passer. |
| **Defense** | Shot contest effectiveness, steal probability, ability to disrupt opposing possessions. Scottie Pippen is an incredible defender. |
| **Speed** | Fast break effectiveness, driving to the basket, closeout speed on defense. Chennedy Carter is the fastest player. |
| **Stamina** | Performance degradation over the course of a game, and over the course of a season. Low stamina = stats decline in later quarters and when playing many minutes in many games close together. LeBron James has incredible stamina. |
| **IQ** | Decision quality under pressure. Affects shot selection (does the agent take bad shots?), defensive rotations, and situational awareness (clock management, foul trouble). On offense, players with high IQ are more effective at moving while off-the-ball. Allisha Gray is the exemplar of a high IQ player. |
| **Ego** | Similar to passing, decision quality under pressure. May take an ill-advised shot, especially in a dramatic moment, but are also more likely to succeed in those high-pressure moments. Ja Morant is high in this. |
| **Chaotic Alignment** | “Ball Don’t Lie!” When these players are involved in play, the results have a higher variance compared to when they are not on the floor. Rasheed Wallace is the top all-time chaos agent. |
| **Fate** | In rare occasions, players will get to author their own attributes, for players, games, teams or, seasons. These will need to make as-defined changes to all of those objects, which will complicate the plan for running a simulation.  |



### Attribute Interactions

Attributes don't operate in isolation. Key interactions:

- **Scoring × IQ** = shot selection quality. A high scorer with low IQ takes (and sometimes makes) bad shots. A high scorer with high IQ takes efficient shots.
- **Passing × Speed** = fast break generation. Teams with both create easy transition baskets.
- **Defense × IQ** = help defense and rotations. Smart defenders cover for teammates; low-IQ defenders leave gaps.
- **Stamina × everything** = late-game performance. A team with low stamina that's dominating early may collapse late. Rules that lengthen games (longer shot clock, more fouls allowed) punish low-stamina teams disproportionately.
- **Ego × Scoring** = clutch performance. High Ego + high Scoring = a closer who takes over when it matters. High Ego + low Scoring = a player who *thinks* they're the closer. The AI report will have things to say about this.
- **Chaotic Alignment × everything** = variance amplifier. High-chaos players widen the probability distribution on every action they're involved in. A high-chaos game is wilder, less predictable, more dramatic. Stacks multiplicatively — two high-chaos players on the floor at once is exponentially more chaotic.
- **Fate × everything** = black-swan events. High-Fate agents occasionally get to author changes to attributes, game parameters, team composition, or season rules — from within the simulation. This is governance-from-below: the governors write the rules, but Fate rewrites reality. Trigger probability scales with Fate attribute value; configurable frequency. Scope is wide, almost total. Opus 4.6 generates the Fate event in character. Whether Fate events bypass governance or create auto-enacted proposals is a **Tier 4 meta-governance parameter** — players can govern how Fate works. Not Day 1; build the model to be flexible so it works when we add it.

> **IMPLEMENTATION NOTE:** Fates are post-Day-1. But the simulation model must be designed so that any parameter at any level (player, game, team, season) can be modified mid-run. This flexibility is the foundation that makes Fates possible later and also makes governance robust now.

### Agent Generation

### DECIDED — Agent Generation

**Budget: 360 total points** across 9 attributes (average 40 per attribute). 360 like degrees in a circle, like a ball, like a pinwheel. Creates meaningful tradeoffs — elite (80+) in 1-2 areas means weak elsewhere.

**Agents have names, personalities, backstories, and rivalries.** Tribal association drives governance opinions. AI-generated at league creation time using Opus 4.6, based on archetype and attributes.

**Team composition:** Start roughly balanced (one scorer, one defender, one playmaker type) but not perfectly — governors will trade.

**9 archetypes** (one per attribute as the signature trait, ±10 random variance per attribute):

| Archetype | Scr | Pas | Def | Spd | Sta | IQ | Ego | Cha | Fate | Identity |
|-----------|-----|-----|-----|-----|-----|-----|-----|-----|------|----------|
| Sharpshooter | 80 | 40 | 25 | 35 | 35 | 55 | 30 | 25 | 35 | Lives behind the arc. Lethal when open, invisible on defense. |
| Floor General | 45 | 80 | 30 | 40 | 35 | 55 | 25 | 25 | 25 | Makes everyone better. Sees passes nobody else sees. |
| Lockdown | 25 | 35 | 80 | 50 | 45 | 40 | 25 | 30 | 30 | Defensive anchor. Shuts down the other team's best player. |
| Slasher | 50 | 30 | 25 | 80 | 40 | 35 | 40 | 30 | 30 | Gets to the rim. Draws fouls. Fast break nightmare. |
| Iron Horse | 35 | 35 | 45 | 35 | 80 | 35 | 25 | 35 | 35 | Never tires. Grinds you down. Dominates late. |
| Savant | 40 | 50 | 40 | 30 | 30 | 80 | 20 | 35 | 35 | Always in the right place. Never flashy. Coaches love them. |
| The Closer | 55 | 25 | 25 | 35 | 30 | 30 | 80 | 30 | 50 | Takes over in crunch time. Hero or goat, no in between. |
| Wildcard | 35 | 30 | 30 | 40 | 30 | 25 | 40 | 80 | 50 | Every game they play is unpredictable. Fans love them. |
| Oracle | 30 | 35 | 30 | 25 | 35 | 45 | 35 | 45 | 80 | Something about this player bends the rules of the game itself. Rare and strange. |

All total 360. Each gets ±10 random variance per attribute (clamped to 1-100) so no two of any archetype are identical.

> **CLAUDE NOTE:** The Oracle archetype (high Fate) is designed for when Fates are implemented. Until then, their high Fate stat is dormant — they're a slightly below-average player with a mysterious quality. When Fates go live, Oracles become the most narratively interesting agents in the league.

## Team Composition

Each team has a roster of agents. For 3v3:

- **Active roster:** 3 agents on the floor at any time
- **Bench:** 1 (4 total)
- **Substitution:** 1 at halftime (between Q2 and Q3).

> **CLAUDE'S TAKE:** I'd recommend **1 bench agent** per team (4 total roster).
>
> Why 1, not 2: With 3v3, adding 2 bench agents means over half your roster is sitting. 1 bench agent creates a single, meaningful substitution decision: *when* do you bring in the fresh legs? This is also a governance surface — rules like `max_minutes_share` force substitution patterns.
>> I agree, thanks. 
> For substitutions, I'd start with **automatic, stamina-triggered**: when an agent's effective stamina drops below a threshold (e.g., 30%), they sub out for the bench agent. But make the threshold **rule-changeable** (Tier 2 parameter). 
>> For P0, let’s do it at the half. For P1, let’s make it configurable and rule-changeable. Thanks. 

This way:
> - Default: subs happen naturally based on fatigue
> - Governance can force more/less rotation by changing the threshold
> - A team with a high-stamina roster gets a natural advantage from fewer forced subs
>
> Governor-controlled subs are more interesting but harder to implement and require real-time player input during games, which breaks the "auto-simulated" model. Save for post-hackathon.
>
> **Questions:**
> 1. Does 4-agent roster (3 active + 1 bench) feel right?
>> 4 for now, I expect it will expand to 5!
> 2. Auto-substitution based on stamina threshold — good starting point?
> 3. Should the bench agent be a different archetype from the starters (strategic depth), or random?
>> Random, governors will make trades.

## Venue & Home Court

Every team has a home venue. Venue characteristics create modifiers that affect the simulation — and every modifier is a governance surface.

### Venue Model

```python
class Venue(BaseModel):
    name: str                          # "The Thorn Garden", "Breaker Bay Arena"
    capacity: int = Field(ge=500, le=50000)  # Audience size scales crowd effects
    altitude_ft: int = Field(ge=0, le=10000)  # Feet above sea level (stamina factor)
    surface: str = "hardwood"          # Governable — what if someone votes for grass?
    location: tuple[float, float]      # lat/lon for travel distance calculation
```

### How Venue Affects the Simulation

Before possession resolution, the simulation computes **venue modifiers** from the matchup:

1. **Crowd boost** — Home team gets a shooting accuracy bonus scaled by venue capacity and the `home_crowd_boost` parameter. A 2,000-seat venue has less crowd impact than a 20,000-seat arena.
2. **Crowd pressure** — Ego checks are modified: home players get a boost (crowd fuels confidence), away players get a penalty (crowd rattles them). High-Ego agents resist crowd pressure; low-Ego agents are more affected.
3. **Altitude penalty** — Away team's stamina drain increases based on altitude differential between their home venue and the game venue. A sea-level team visiting a high-altitude venue tires faster.
4. **Travel fatigue** — Away team gets a stamina penalty proportional to the distance between venues. Long road trips compound fatigue, especially for low-Stamina agents.
5. **Surface modifier** — If a governance vote changes a venue's surface (grass, sand, ice?), Speed and Drive actions are modified. The simulation checks `venue.surface` against a surface effects table.

```
venue_modifiers = compute_venue_modifiers(
    home_venue=home_team.venue,
    away_venue=away_team.venue,  # for travel distance + altitude delta
    rules=ruleset                # governable parameters control modifier strength
)
```

These modifiers feed into the possession model as multipliers on the relevant attribute checks.

### Governance Surface

Venue parameters create a rich governance surface:

- **Eliminate home court advantage entirely** — set `home_court_enabled: false`
- **Increase crowd pressure** — makes away games brutal for low-Ego agents
- **Modify a venue's altitude** — governance as terraforming
- **Change a venue's surface** — what happens to Speed on grass? To drives on ice?
- **Disable travel fatigue** — levels the playing field or removes a strategic lever
- **Change crowd boost scaling** — should a 50,000-seat arena give 5x the boost of a 10,000-seat one, or diminishing returns?

Venue identity also enriches the narrative layer. The AI report can comment on road trip difficulty, hostile crowds, altitude adjustments, and the effect of a governance vote that turned someone's court into a sand pit.

## Moves

Agents don't just have attributes — they have **moves**. Moves are learned abilities (special dribbles, tricks, plays) that modify outcomes within the possession model. Moves are the verbs to attributes' adjectives.

### How Moves Work

A move is a named ability with:
- **Trigger condition:** When can this move activate? (e.g., "on a drive attempt", "when contested from behind", "in transition")
- **Effect:** What does it modify? (e.g., "+15% scoring probability on drives", "steal chance doubled when ball handler has low IQ", "pass accuracy ignores one defender")
- **Cost:** Does it drain stamina faster? Increase foul probability?
- **Attribute gate:** Minimum attribute required to learn/use this move (e.g., "requires Speed 60+")

### Example Moves

| Move | Trigger | Effect | Gate |
|------|---------|--------|------|
| Killer Crossover | Drive attempt | +20% drive success, +10% foul drawn | Speed 65+ |
| No-Look Pass | Pass action, 2+ defenders in lane | Pass success ignores contest modifier | Passing 70+, IQ 50+ |
| Lockdown Stance | Defending ball handler | Contest modifier doubled | Defense 75+ |
| Heat Check | Made a 3-pointer last possession | +15% on next 3-point attempt, but IQ modifier ignored | Ego 60+ |
| Chaos Dunk | Drive to basket | Outcome variance tripled (spectacular make or spectacular miss) | Chaotic Alignment 70+ |
| Clutch Gene | Score differential ≤ 3, possession matters | All shot probabilities +10% | Ego 70+, Scoring 50+ |
| Iron Legs | Second half, stamina < 50% | Stamina modifier halved (less penalty) | Stamina 70+ |
| Court Vision | Half court setup | Ball handler sees the optimal pass; assist tracking window doubled | IQ 75+, Passing 60+ |

### Moves in the Possession Model

During action selection, after an agent chooses an action (SHOOT, PASS, DRIVE), the simulation checks if any of their moves trigger. If a move triggers, its effect modifies the probability calculation before resolution. Multiple moves can trigger on the same action.

### Move Acquisition — DECIDED: All of the above

Agents acquire moves through three channels:

1. **Seeded at creation** — Each archetype comes with 1-2 signature moves. A Sharpshooter starts with Heat Check. A Lockdown starts with Lockdown Stance. This is the Day 1 implementation.
2. **Earned through play** — Agents unlock moves by hitting stat milestones across games (e.g., "10 steals in a season → learn Pickpocket", "50 assists → learn No-Look Pass"). Rewards performance, creates progression, gives the AI report something to narrate. Post-Day-1.
3. **Governed** — Players can propose granting moves to agents via governance. "I propose that Kaia Nakamura learns Chaos Dunk." This makes the move system a governance surface — players can craft their team's identity through the rules. Post-Day-1.

The model must support all three from the start. A move is a move regardless of how it was acquired. The `Move` model tracks its `source: Literal["archetype", "earned", "governed"]` for the AI report to reference.

## Possession Model

The atomic unit of basketball simulation is the **possession**. Each possession follows this decision tree:

```
POSSESSION START (team has ball)
│
├─ DEFENSIVE SETUP (see Defensive Model)
│   ├─ Scheme selection: man-tight, man-switch, zone, or press
│   │   (based on opponent lineup, own resources, game context, stamina)
│   ├─ Matchup assignment (for man schemes): optimize defender-attacker pairs
│   │   (minimize matchup cost function across all 3 pairings)
│   ├─ Apply strategy overrides (if team has active strategy instructions)
│   └─ Scheme modifies contest, turnover, and driving probabilities for this possession
│
├─ TRANSITION CHECK
│   └─ Fast break opportunity? (based on Speed differential + turnover type)
│       ├─ Yes → FAST BREAK (higher scoring probability, simpler resolution)
│       │   └─ Press scheme increases both fast break chance and turnover chance
│       └─ No → HALF COURT
│
├─ HALF COURT SETUP
│   ├─ Ball handler selected (highest IQ, or rule-modified)
│   ├─ Shot clock begins
│   │
│   ├─ ACTION SELECTION (repeated until possession ends or shot clock expires)
│   │   ├─ SHOOT
│   │   │   ├─ Shot type: 2-pointer, 3-pointer, drive to basket
│   │   │   │   (selected based on Scoring, range, IQ, defensive matchup + scheme)
│   │   │   ├─ Contest: defender's Defense vs. shooter's Scoring
│   │   │   │   (modified by scheme: man-tight = full, zone = reduced, switch = slight reduction)
│   │   │   ├─ Outcome: make/miss (probability from attributes + contest + shot type)
│   │   │   └─ If missed → REBOUND
│   │   │
│   │   ├─ PASS
│   │   │   ├─ Target selection (Passing + IQ)
│   │   │   │   (zone: high-IQ handlers find gaps, low-IQ get confused)
│   │   │   ├─ Turnover check (Passing vs. defender's Defense)
│   │   │   │   (man-tight: tighter lanes. press: much higher turnover chance)
│   │   │   │   ├─ Turnover → opponent possession
│   │   │   │   └─ Success → recipient becomes ball handler, ACTION SELECTION continues
│   │   │   └─ Assist tracking (if pass leads to made shot within N actions)
│   │   │
│   │   └─ DRIVE
│   │       ├─ Speed vs. defender's Speed + Defense
│   │       │   (zone: face help defense from multiple defenders)
│   │       ├─ Foul check (drives generate fouls at higher rate)
│   │       ├─ Outcome: layup attempt (high %) / blocked / foul
│   │       └─ If fouled → FREE THROWS
│   │
│   └─ SHOT CLOCK EXPIRATION → forced bad shot (low % regardless of attributes)
│
├─ REBOUND
│   ├─ Offensive vs. defensive rebound (Speed + Defense derived)
│   └─ Winner gets possession
│
├─ FREE THROWS
│   ├─ Scoring attribute determines make probability
│   └─ 2 free throws default, rule-changeable
│
├─ FOUL TRACKING
│   ├─ Personal fouls per agent
│   ├─ Team fouls per half
│   ├─ Foul limit → agent ejection (rule-changeable!)
│   └─ Bonus free throws when team foul limit reached
│
└─ STAMINA DRAIN
    ├─ Offensive actions cost stamina (drives > shots > passes)
    ├─ Defensive effort costs stamina (see Defensive Model: stamina economics)
    │   (guarding fast/chaotic/high-IQ players costs more)
    └─ Scheme affects drain rate (press > man-tight > man-switch > zone)
```

## Defensive Model

Defense isn't a single attribute check — it's a team-level strategic decision that happens before each possession. The defending team selects a **scheme**, assigns **matchups**, and adapts based on **game context**. Every part of this model interacts with the 9 agent attributes, and every part is a governance surface.

### Scheme Selection

Before each possession, the defending team's AI selects a defensive scheme. The selection is a function of the opponent lineup's attributes, the defending team's resources, and game context.

**Schemes:**

| Scheme | Description | Best Against | Weak Against | Stamina Cost |
|--------|-------------|--------------|--------------|-------------|
| **Man-tight** | Each defender assigned 1-on-1, stays close. Maximum contest on shots. | Strong individual scorers (high Scoring) | Fast ball movement (high Passing + IQ teams) | High |
| **Man-switch** | Defenders switch assignments on screens/drives. Fewer mismatches from motion. | Teams that use motion/picks | Creates size/speed mismatches | Medium |
| **Zone** | Defenders guard areas, not players. Ball-side defenders contest, weak-side helps. | Weak shooting teams. Hides a weak defender. | Sharpshooters (high Scoring from range). High-IQ playmakers pick it apart. | Low |
| **Press** | Aggressive full-court pressure. Forces turnovers but leaves gaps. | Low-IQ ball handlers. Late-game desperation. | Fast teams (high Speed). High-IQ passers. | Very High |

**Scheme selection logic:**

```
def select_scheme(offense_lineup, defense_lineup, game_state, rules):
    # Threat assessment
    shooting_threat = avg(a.scoring for a in offense_lineup)
    playmaking_threat = max(a.passing + a.iq for a in offense_lineup)
    speed_threat = max(a.speed for a in offense_lineup)
    chaos_factor = avg(a.chaotic_alignment for a in offense_lineup)

    # Defensive resources
    best_defender = max(d.defense for d in defense_lineup)
    team_defensive_iq = avg(d.iq for d in defense_lineup)
    avg_stamina = avg(d.current_stamina for d in defense_lineup)

    # Game context weights
    if game_state.elam_active:
        # Elam period: matchup quality over everything
        stamina_weight = 0.1
    elif game_state.score_differential > 10:
        # Big lead: preserve stamina
        stamina_weight = 0.8
    elif game_state.score_differential < -10:
        # Big deficit: aggressive schemes
        prefer_press = True
    else:
        # Normal: balance matchup quality and stamina
        stamina_weight = f(game_state.quarter, game_state.possessions_remaining)

    # Scheme scoring (higher = preferred)
    # Strong shooters demand man-tight
    # Weak shooters allow zone (saves stamina)
    # High playmaking punishes zone
    # Low team stamina favors zone
    # Trailing late favors press

    return best_scheme + variance  # not always optimal (simulates imperfection)
```

The scheme is not deterministic — there's variance so the same game state doesn't always produce the same scheme. This models coaching intuition, adjustments, and imperfection. High team IQ reduces this variance (smarter teams make better scheme calls more consistently).

### Matchup Assignment (Man-to-Man Schemes)

When man-tight or man-switch is selected, each defender is assigned to a specific attacker. This is an optimization problem: maximize total matchup quality while managing stamina budgets.

**Matchup cost function** — for each (defender, attacker) pair:

```
def matchup_cost(defender, attacker, game_state):
    # How dangerous is this attacker?
    threat = (
        attacker.scoring * 0.4
        + attacker.speed * 0.2        # fast players draw fouls, create transition
        + attacker.ego * 0.15         # high-ego players demand the ball in crunch time
        + attacker.chaotic_alignment * 0.1  # unpredictable = harder to prepare for
        + attacker.iq * 0.15          # smart players exploit every mismatch
    )

    # How well does this defender contain this attacker?
    containment = (
        defender.defense * 0.4        # raw defensive ability
        + min(defender.speed, attacker.speed) * 0.25  # can you keep up?
          # ↑ capped at attacker speed — being faster than needed doesn't help more
        + defender.iq * 0.2           # reads plays, doesn't bite on fakes
        + defender.stamina * 0.15     # can sustain effort
    )

    # Stamina cost of this assignment
    stamina_drain = (
        attacker.speed * 0.3          # chasing fast players is tiring
        + attacker.chaotic_alignment * 0.2  # unpredictable movement patterns
        + attacker.iq * 0.2           # constant repositioning against smart players
        + (1 - defender.stamina_pct) * 0.3  # already tired = drains faster
    )

    # Game context adjustment
    if game_state.elam_active:
        stamina_weight = 0.1          # ignore stamina, win now
    elif game_state.quarter <= 2:
        stamina_weight = 0.3          # early game: invest in matchup quality
    else:
        stamina_weight = 0.5          # late game: manage resources

    return threat - containment + (stamina_drain * stamina_weight)
```

The assignment algorithm minimizes total cost across all 3 matchups. But it's not a perfect optimizer — variance is applied, and high team IQ reduces that variance. This means:

- A smart defensive team (high avg IQ) consistently finds the right matchups
- A low-IQ defensive team sometimes puts the wrong defender on the wrong player
- A fatigued team may "hide" their tired defender on the weakest scorer, even if it's suboptimal

### Stamina Economics of Defense

Guarding different players costs different amounts of stamina. This is where **IQ** and **Chaotic Alignment** become defensive weapons even for non-scorers:

| Offensive Attribute | Defensive Stamina Cost | Why |
|---|---|---|
| High Speed | +++ | Chasing them on drives, closeouts, transition |
| High Chaotic Alignment | ++ | Can't predict their movement, constant readjustment |
| High IQ | ++ | Constant cutting, screening, repositioning — the defender works harder mentally and physically |
| High Ego | + | They demand the ball, so their defender can't rest on weak-side |
| High Scoring | + | Must stay close at all times, can't sag off |

This means a high-IQ, high-Speed player like a **Savant** or **Slasher** is exhausting to defend even if they're not scoring much. A **Wildcard** (high Chaotic Alignment) forces the defender to burn stamina just staying in position. The defensive stamina cost is the hidden tax these players impose.

### Defensive Attribute Interactions

How each attribute contributes on defense:

- **Defense** — Raw ability. Contest strength, steal probability, shot-blocking. The primary defensive attribute.
- **Speed** — Closeout speed (reaching a shooter before they release), recovery after being beaten on a drive, transition defense.
- **IQ** — Help defense timing (rotating to cover a driving lane without leaving a shooter open), reading the offense's intent, not biting on pump fakes, positioning.
- **Stamina** — Sustaining defensive effort across a game. A high-Defense, low-Stamina player is elite in Q1 and a liability in the Elam period.
- **Ego** — High-Ego defenders gamble. They go for steals more often (higher steal probability but also higher chance of being beaten). In the Elam period, this risk/reward profile becomes more pronounced.
- **Chaotic Alignment** — Unpredictable positioning. Sometimes the defender is in exactly the right place (brilliant instinct). Sometimes they're completely lost (blown assignment). Widens the variance on all defensive outcomes. Chaos on defense is a double-edged sword.

### Scheme Interactions with Offense

The defensive scheme modifies how the possession model resolves:

**Man-tight:**
- Contest modifier on shots is at full strength
- Driving is harder (defender is in position)
- Passing lanes are tighter (higher turnover probability on passes)
- Ball handler's IQ matters more (reading the 1-on-1 matchup)
- Offensive Chaotic Alignment is more effective (harder to predict 1-on-1)

**Zone:**
- Contest modifier is reduced (defenders aren't always on the shooter)
- Shooters get more open looks, but only from certain spots
- High-IQ ball handlers exploit gaps: passing probability increases, assist rate goes up
- Low-IQ ball handlers get confused: turnover probability increases
- Sharpshooters (high Scoring) are barely affected — they find the open spots
- Drives to the basket face help defense (multiple defenders contest)

**Press:**
- Turnover probability is significantly higher (especially against low-IQ handlers)
- Fast break probability for the offense increases if they break the press (Speed check)
- Extremely draining for both teams
- High-risk/high-reward: either creates turnovers or gives up easy baskets

**Man-switch:**
- Contest modifier slightly reduced (late switches = brief opening)
- Reduces effect of off-ball movement
- Can create mismatches that IQ exploits
- Lower stamina cost than man-tight

### Adaptive Strategy

The scheme isn't static across a game. The defending team's AI adapts:

1. **Hot player adjustment** — If an offensive agent has made their last 2+ shots, the defense shifts toward tighter coverage on them, even at the cost of weaker coverage elsewhere.
2. **Foul trouble adjustment** — If a key defender is in foul trouble, the scheme shifts to reduce their involvement (zone, or hide them on the weakest scorer).
3. **Stamina-driven shift** — As the game progresses, tired teams drift from man-tight to zone or man-switch. The transition point depends on team Stamina attributes.
4. **Score-driven shift** — Trailing teams get more aggressive (press, gambling for steals). Leading teams get more conservative (zone, preserving energy).
5. **Elam period shift** — When the Elam Ending activates, defensive intensity increases. Stamina management is deprioritized. Teams with high-Stamina rosters have a significant advantage here.

### Strategy Overrides (Governance Surface)

The default defensive model is an AI-driven optimizer. But human players should eventually be able to **override it with strategic instructions** — this is a natural extension of governance.

**How it works (Day 1–2):**

Players submit strategic instructions in natural language, just like governance proposals:
- "Always put our best defender on their highest scorer"
- "Switch to zone when we're up by 8 or more"
- "Press in the Elam period"
- "Hide [agent name] on defense — assign them to the weakest scorer"
- "Never switch — stay on your man no matter what"

The AI interpreter (same sandboxed system used for rule proposals) parses these into structured `TeamStrategy` objects:

```python
class TeamStrategy(BaseModel):
    team_id: str
    instructions: list[StrategyInstruction]

class StrategyInstruction(BaseModel):
    condition: StrategyCondition   # when does this apply?
    action: StrategyAction         # what does it override?
    priority: int                  # higher priority overrides lower

class StrategyCondition(BaseModel):
    # Structured conditions the simulation can evaluate
    quarter: int | None = None
    score_differential_gte: int | None = None
    score_differential_lte: int | None = None
    elam_active: bool | None = None
    agent_ref: str | None = None       # specific agent involved
    opponent_agent_ref: str | None = None

class StrategyAction(BaseModel):
    scheme_override: SchemeEnum | None = None
    assignment_override: dict[str, str] | None = None  # defender → attacker
    press_enabled: bool | None = None
```

This means:
- Strategy overrides are **per-team**, not league-wide
- They're parsed through the same AI interpreter pipeline (sandboxed, validated)
- The simulation engine checks active strategies before applying default logic
- Bad strategies cost you — "always press" will exhaust your team. The model doesn't protect you from yourself.
- Whether teams can set strategies could itself be a **Tier 2 governance parameter** (`team_strategy_enabled`)

**Day 0:** The defensive model runs on its own AI logic. No player overrides.
**Day 1–2:** Strategy overrides become a governance surface — one of the richest in the game.

## Scoring Resolution

Shot probability is computed as:

```
base_probability = f(shot_type, range, agent.scoring)
contest_modifier = g(defender.defense, shot_type)
iq_modifier = h(agent.iq, shot_quality)  # bad shots get penalized
stamina_modifier = s(agent.stamina, game_progress)  # degrades late
rule_modifier = r(rules)  # e.g., rules might grant bonus % to certain shot types

final_probability = base_probability × contest_modifier × iq_modifier × stamina_modifier × rule_modifier
```

> **CLAUDE'S TAKE — Scoring probability functions:**
>
> The key tension is right: simple enough to be transparent, complex enough that rule changes cascade. I'd start with **logistic curves, not linear**. Linear models break at the edges (probability > 1.0 or < 0.0). Logistic curves naturally clamp to [0, 1] and create diminishing returns — the difference between 60 and 70 Scoring matters more than 90 and 100.
>
> **Proposed approach:**
> ```
> base_probability:
>   2-pointer:  logistic(agent.scoring, midpoint=50, steepness=0.08)  → range ~0.30 to ~0.65
>   3-pointer:  logistic(agent.scoring, midpoint=60, steepness=0.07)  → range ~0.20 to ~0.45
>   drive/layup: logistic(agent.scoring, midpoint=40, steepness=0.08) → range ~0.40 to ~0.75
>
> contest_modifier: 1.0 - (defender.defense / 200)
>   → A 100-Defense agent reduces shot probability by 50%. A 50-Defense agent reduces by 25%.
>
> iq_modifier: 0.7 + (agent.iq / 333)
>   → Range 0.7 to 1.0. Low IQ agents take bad shots (30% penalty). High IQ is neutral (no bonus, just no penalty).
>
> stamina_modifier: 1.0 - (0.3 × (1 - current_stamina_pct))
>   → At full stamina: 1.0. At 0% stamina: 0.7. So fatigue costs up to 30% of shot probability.
>
> rule_modifier: product of applicable rule effects (default 1.0)
> ```
>
> These are starting values. The real tuning happens from simulation runs — run 1000 games, check that scoring averages and game lengths look basketball-like, adjust midpoints.
>
> **Questions:**
> 1. Does a logistic base make sense, or do you prefer something simpler for Day 1?
> 2. The contest_modifier is deliberately strong (a great defender halves your accuracy). Too much? This makes Defense the most impactful attribute.
> 3. Should IQ provide a *bonus* for good shot selection, or only a *penalty* for bad selection? I went penalty-only (good IQ = you don't take bad shots), but a bonus would reward smart players more visibly.
> 4. The `three_point_distance` rule parameter — how does it interact with Scoring? My proposal: agents have an implicit "range" derived from their Scoring attribute. If `three_point_distance` exceeds their range, their 3-point probability drops further. This means moving the line back doesn't hurt all shooters equally — it specifically punishes mid-range scorers.

## Game Structure

The game must *feel* like basketball to an observer. The narrative leaps Pinwheel Fates makes — absurd rules, chaotic agents, Fate events — are only possible because the underlying grammar is familiar. Quarters, halftimes, late-game drama, end-of-quarter buzzer beaters — these are the rhythms that make basketball legible and the departures from them meaningful.

### Periods

- **4 quarters.** Each quarter is `quarter_possessions` possessions long (default 15, so ~60 possessions per regulation game).
- **Halftime** between Q2 and Q3. Substitutions happen at halftime (P0). Stamina partially recovers.
- **Quarter breaks** between Q1/Q2 and Q3/Q4. Shorter pauses. Team fouls reset per half (Q1+Q2 share a foul count, Q3+Q4 share a foul count).
- **Game clock:** Each possession consumes `possession_duration_seconds` of fictional game time (default 24, matches real shot clock). A 15-possession quarter = 6 fictional minutes. A full game ≈ 24 fictional minutes. The presenter uses this clock for display, tension, and pacing.

### Elam Ending

At the **end of the 3rd quarter** (configurable via `elam_trigger_quarter`), the game clock turns off:

1. Take the leading team's score at end of Q3.
2. Add `elam_margin` (default 13).
3. That's the **target score.** First team to reach it wins.
4. If tied at end of Q3, target = tied score + `elam_margin`.
5. No more quarter structure — just possessions until someone hits the target.
6. Every game ends on a made basket. The presenter knows who wins and can build toward the final shot.

**Why the Elam Ending serves governance:**
- `elam_trigger_quarter` — when does the endgame begin? Move it to Q2 for fast games. Governable.
- `elam_margin` — how big is the final stretch? +7 means quick sprints. +20 means marathon endings. Governable.
- `quarter_possessions` — longer quarters mean more regulation play before the Elam kicks in. Governable.
- The interaction between scoring rule changes and the Elam Ending is rich: if governance votes to make 3-pointers worth 5, the endgame gets explosive.
- Stamina matters more in close games where the Elam Ending extends play past regulation length.

### Game Timeline

```
Q1 (15 poss)  →  Break  →  Q2 (15 poss)  →  HALFTIME  →  Q3 (15 poss)  →  ELAM ENDING
                                                  │                              │
                                            Subs happen                   Clock off.
                                            Stamina recovery              Target score set.
                                            Foul count resets             Play until someone
                                                                          hits the target.
                                                                          Game ends on a
                                                                          made basket.
```

### Summary

- **Periods:** 4 quarters of `quarter_possessions` (default 15) possessions each.
- **Halftime:** Between Q2 and Q3. Subs, partial stamina recovery.
- **Elam trigger:** End of `elam_trigger_quarter` (default 3). Target = leader + `elam_margin` (default 13).
- **Safety cap:** Max `safety_cap_possessions` (default 200) total possessions. If reached, highest score wins. If tied, sudden death.
- **Home court advantage:** Venue modifiers applied per [Venue & Home Court](#venue--home-court). Governable via Tier 2 parameters.
- **Game clock:** Fictional, derived from possession count × `possession_duration_seconds`. For display and narrative, not simulation logic.

## Rule Expressiveness: From Parameters to Effects

The rule space is the language of governance. If the language is too narrow (just typed parameters), governance becomes boring knob-twiddling. If it's too wide (arbitrary code execution), the simulation enters undefined states. The right design makes governance creative, surprising, and safe.

### The Three Layers of Governance Expression

Governance proposals fall into three categories. Each has a different technical model:

```
Layer 1: PARAMETER CHANGES          Layer 2: GAME EFFECTS             Layer 3: LEAGUE EFFECTS
"Make 3s worth 5"                   "Dunking gives an extra           "The last-place team's
                                     possession"                       next opponent starts
                                                                       with -5 points"

Typed value changes.                Conditional modifications         Cross-game modifications
Validated against ranges.           within a single game.             that run after simulation.
Simplest. Safest.                   More expressive. Still safe.      Most expressive. Careful.

RuleChange(                         GameEffect(                       LeagueEffect(
  parameter="three_point_value",      trigger="on_made_drive",          trigger="post_round",
  new_value=5                         condition=None,                   condition="team.standing==last",
)                                     action="grant_possession",        action="modify_next_game_score",
                                      scope="scoring_team",             target="opponent",
                                      duration="this_possession"        value=-5
                                    )                                 )
```

### Layer 1: Parameter Changes (Day 0)

This is the current system. Typed parameters with ranges and defaults. The interpreter maps natural language to a `RuleChange`. Pydantic validates. Safe, tested, deterministic.

**~60% of proposals will map here.** "Make 3-pointers worth 5." "Extend quarters to 20 possessions." "Allow press defense only in the Elam period." These are the bread and butter of governance.

### Layer 2: Game Effects (Day 2-3)

Game Effects are conditional modifications that fire during a game. They're more expressive than parameters but still operate within a single game's simulation — no cross-game state.

**The model:**

```python
class GameEffect(BaseModel):
    """A conditional modification to game behavior, enacted by governance."""
    name: str                           # human-readable name
    trigger: EffectTrigger              # WHEN does this fire?
    condition: EffectCondition | None    # WHAT must be true? (None = always)
    action: EffectAction                # WHAT happens?
    scope: EffectScope                  # WHO is affected?
    duration: EffectDuration            # HOW LONG does it last?
    source_proposal: str                # which proposal created this

class EffectTrigger(str, Enum):
    on_score = "on_score"               # any made basket
    on_miss = "on_miss"                 # any missed shot
    on_steal = "on_steal"               # turnover via steal
    on_foul = "on_foul"                 # foul called
    on_move_trigger = "on_move_trigger" # a Move activates
    on_quarter_end = "on_quarter_end"
    on_halftime = "on_halftime"
    on_elam_start = "on_elam_start"
    on_lead_change = "on_lead_change"
    on_possession_start = "on_possession_start"

class EffectCondition(BaseModel):
    """Structured conditions the simulation can evaluate."""
    score_differential_gte: int | None = None
    score_differential_lte: int | None = None
    quarter: int | None = None
    elam_active: bool | None = None
    shot_type: Literal["two", "three", "drive", "free_throw"] | None = None
    agent_attribute_gte: tuple[str, int] | None = None  # ("scoring", 70)
    streak_gte: int | None = None       # consecutive makes/misses
    move_name: str | None = None        # specific Move triggered

class EffectAction(str, Enum):
    modify_score = "modify_score"               # add/subtract points
    modify_attribute = "modify_attribute"        # temporary attribute buff/debuff
    grant_possession = "grant_possession"        # extra possession
    force_substitution = "force_substitution"    # trigger a sub
    modify_probability = "modify_probability"    # adjust shot/steal/etc probability
    apply_stamina = "apply_stamina"              # stamina drain/recovery
    modify_foul_count = "modify_foul_count"      # add/remove fouls

class EffectScope(str, Enum):
    scoring_agent = "scoring_agent"
    defending_agent = "defending_agent"
    ball_handler = "ball_handler"
    team = "team"                       # whole team
    opponent = "opponent"               # opposing team
    all_agents = "all_agents"           # everyone on floor

class EffectDuration(str, Enum):
    this_possession = "this_possession"
    this_quarter = "this_quarter"
    this_game = "this_game"
    next_n_possessions = "next_n_possessions"   # paired with a count
    until_lead_change = "until_lead_change"
```

**Examples of proposals → Game Effects:**

| Proposal | Effect |
|----------|--------|
| "Dunking gives you an extra possession" | `trigger=on_score, condition={shot_type: "drive"}, action=grant_possession, scope=team` |
| "The losing team at halftime gets +10% shooting" | `trigger=on_halftime, condition={score_differential_lte: -1}, action=modify_probability, scope=team, duration=this_game` |
| "Every lead change drains 5 stamina from all players" | `trigger=on_lead_change, action=apply_stamina, scope=all_agents` |
| "Players with Ego > 70 score double on contested shots" | `trigger=on_score, condition={agent_attribute_gte: ("ego", 70)}, action=modify_score, scope=scoring_agent` |
| "A 10-0 run triggers a mandatory substitution" | `trigger=on_score, condition={streak_gte: 10}, action=force_substitution, scope=opponent` |
| "Heat Check activates for the ENTIRE team after a buzzer beater" | `trigger=on_move_trigger, condition={move_name: "heat_check", quarter_end: true}, action=modify_probability, scope=team, duration=next_n_possessions` |

**Validation:** Each component (trigger, condition, action, scope, duration) is an enum or structured type. The interpreter can only compose from this vocabulary — it can't invent new triggers or actions. Pydantic validates the full Effect. The simulation engine has explicit hooks at each trigger point.

**Stacking and conflicts:** Multiple Effects can be active simultaneously. They compose multiplicatively for probability modifiers, additively for score/stamina modifiers. Conflicting effects (one grants possession, another forces substitution on the same trigger) resolve by priority order (proposal enactment order, or a `priority` field).

**Why this is safe:** The simulation engine has a fixed set of hooks (the EffectTrigger enum). Effects can only modify things the engine knows how to modify (the EffectAction enum). Conditions can only test things the engine can evaluate (the EffectCondition fields). The vocabulary is finite and validated. A creative proposal is expressed through composition, not through arbitrary code.

### Layer 3: League Effects (Post-Hackathon, but model now)

League Effects operate across games — they run *after* individual game simulation, modifying results or setting up conditions for future games. This is where the truly wild governance lives.

**Architectural key:** The simulation is still a pure function. `simulate_game()` knows nothing about League Effects. The effects run in a post-processing step between simulation and storage:

```
SIMULATE all 4 games (pure, independent)
    ↓
LEAGUE EFFECTS PROCESSOR
    Reads all GameResults for this round
    Applies active League Effects
    May modify scores, stats, or next-round conditions
    ↓
STORE modified results
```

This preserves the simulation contract. Each game is still deterministic given its inputs. League Effects are a separate layer with separate validation.

**The model:**

```python
class LeagueEffect(BaseModel):
    """A modification that operates across games or rounds."""
    name: str
    trigger: LeagueTrigger
    condition: LeagueCondition | None
    action: LeagueAction
    source_proposal: str

class LeagueTrigger(str, Enum):
    post_round = "post_round"               # after all games in a round
    post_game = "post_game"                 # after each individual game
    pre_round = "pre_round"                 # before next round simulates
    on_standings_change = "on_standings_change"
    on_series_start = "on_series_start"     # playoff series begins
    on_elimination = "on_elimination"       # team eliminated from playoffs

class LeagueCondition(BaseModel):
    team_standing: int | None = None        # e.g., 1 = first, 8 = last
    team_win_streak_gte: int | None = None
    team_loss_streak_gte: int | None = None
    round_number_gte: int | None = None
    game_differential_gte: int | None = None  # margin of victory

class LeagueAction(str, Enum):
    modify_next_game_score = "modify_next_game_score"   # start with +/- N
    swap_scores = "swap_scores"                         # swap two teams' results
    modify_agent_attributes = "modify_agent_attributes" # buff/debuff for next round
    grant_tokens = "grant_tokens"                       # governance tokens
    schedule_extra_game = "schedule_extra_game"
    modify_venue = "modify_venue"                       # change where next game is played
    force_roster_change = "force_roster_change"         # mandatory sub/trade
```

**The score-swap example:**

"A team can swap scores with another team" → `LeagueEffect(trigger=post_round, action=swap_scores)`. But this needs additional structure: *which* teams? Under what conditions? The interpreter would ask clarifying questions:

```
🤖 Pinwheel: That's a wild one. Here's what I can express:

📋 League Effect: "Score Swap"
Trigger: Post-round
Condition: Proposing team won their game by 10+
Action: Swap final scores between proposing team's game
        and another game of their choice (selected pre-round)
Duration: Single use (one round, then expires)

⚠️ This modifies game results AFTER simulation. The games
play out normally — then the scores switch. Standings update
based on swapped results.

This is a League Effect (Tier 5). Requires supermajority
(66% weighted vote). Costs 2 PROPOSE tokens.

React ✅ to submit, ❌ to cancel, ✏️ to revise.
```

**Why this stays safe:**
- Games still simulate as pure functions. No cross-game state during simulation.
- League Effects run in a clearly defined post-processing step.
- The vocabulary of League Actions is finite and validated.
- League Effects require higher approval thresholds (supermajority, higher token cost).
- The AI report can analyze the impact of League Effects with full context.

### The Interpreter as Constitutional Court

The interpreter's most important job is NOT parsing parameters — it's *translating creative intent into something the system can express.*

When a governor proposes something wild, the interpreter:

1. **Understands the intent.** What is the governor trying to accomplish? What's the spirit of the proposal?
2. **Maps to the expressible.** Can this be expressed as a Parameter Change, Game Effect, or League Effect? If not exactly, what's the closest expressible version?
3. **Explains the translation.** Shows the governor what the system CAN do and how it differs from what was proposed.
4. **Offers alternatives.** If the proposal can't be expressed at all, suggests 2-3 alternatives that capture parts of the intent.
5. **Lets the governor decide.** The governor sees the interpretation and confirms, revises, or cancels.

**Examples of constitutional interpretation:**

| Proposal | Interpretation |
|----------|---------------|
| "Let teams steal each other's players mid-game" | "I can't move agents between rosters during simulation. But I CAN express: 'If a team is down by 15+, their weakest agent gets a temporary +10 to all attributes for the rest of the game.' This captures the 'comeback mechanic' spirit. Alternatively: make agent trades cheaper during governance windows when a team is on a losing streak." |
| "Play the game underwater" | "The simulation doesn't model physics environments. But I CAN modify Speed (-20% for all agents), increase stamina drain (+50%), and change the venue surface to 'aquatic' — which the commentary engine will narrate accordingly. The FEEL is underwater; the mechanics adjust to match." |
| "If our team wins, the three-point line moves closer for our next game only" | "This is a League Effect: `trigger=post_game, condition=team_won, action=modify_next_game_rules, parameter=three_point_distance, delta=-2, scope=winning_team_next_game`." |
| "Ban the other team's best player" | "I can't remove agents from rosters via game effects. But I CAN express: 'Reduce one named agent's attributes by 30% for one game.' This is effectively a suspension. Alternatively: increase the personal foul limit for that agent to 2 (they'll foul out fast)." |

**The gap is a feature.** The distance between what's proposed and what's enacted creates politics. A governor who proposes something wild and gets a partial interpretation can argue that the system should be expanded. Other governors can counter that the constraints ARE the game. This tension — between creative aspiration and systemic constraint — is itself a governance surface.

### Expanding the Rule Space (Meta-Meta-Governance)

What if players want to add entirely new parameters, triggers, or actions that the system doesn't support? This is governance about the language of governance itself.

**How it could work:**
1. A governor proposes a new capability: "We should be able to modify agent attributes between games based on performance."
2. The interpreter flags this as a **Rule Space Expansion Request** — not a rule change, but a request to expand what rules can express.
3. This goes through a **supermajority vote** (Tier 4 meta-governance).
4. If passed, the development team (or, in the long term, an AI-assisted code generator with human review) implements the new trigger/action/parameter.
5. The new capability becomes part of the rule space vocabulary.

For the hackathon, this is manual. Post-hackathon, this could be semi-automated — Opus 4.6 generates the implementation, a human reviews it, and the new capability deploys. The game's rule space literally grows through governance.

### Tier Structure (Updated)

| Tier | Scope | Approval | Token Cost | Examples |
|------|-------|----------|------------|---------|
| **Tier 1** | Game mechanics (parameters) | Simple majority | 1 PROPOSE | Shot clock, point values, foul limits |
| **Tier 2** | Agent/behavior constraints (parameters) | Simple majority | 1 PROPOSE | Max shot share, venue modifiers, defensive schemes |
| **Tier 3** | League structure (parameters) | Simple majority | 1 PROPOSE | Schedule format, trade window, salary cap |
| **Tier 4** | Meta-governance (parameters) | Simple majority | 1 PROPOSE | Token regen, vote threshold, Fate toggle |
| **Tier 5** | Game Effects (conditional) | **Supermajority (60%)** | **2 PROPOSE** | Extra possessions, attribute buffs, stamina effects |
| **Tier 6** | League Effects (cross-game) | **Supermajority (66%)** | **2 PROPOSE** | Score modification, roster effects, schedule changes |
| **Tier 7** | Rule Space Expansion | **Supermajority (75%)** | **3 PROPOSE** | New triggers, new actions, new parameters |

Higher tiers require more consensus and more tokens. This creates a natural pressure: simple parameter changes are easy to pass. Wild structural changes require broad coalition support. The wildest proposals — expanding the rule space itself — require near-unanimous agreement.

### Safety Boundaries

Even with Effects and League Effects, certain things are architecturally forbidden:

1. **No arbitrary code execution.** Effects compose from a finite vocabulary. You cannot inject logic the simulation doesn't have hooks for.
2. **No information leakage.** Effects cannot expose private data (team strategies, hidden votes, private reports) to other teams.
3. **No retroactive changes.** Effects cannot modify games that have already been stored and presented. They can only affect the current or future rounds.
4. **No infinite loops.** Effects that trigger other effects are depth-limited (max 3 levels of effect chaining). An effect that grants a possession that triggers another effect that grants another possession is capped.
5. **No breaking determinism.** Effects are applied deterministically. The same game state + effects always produce the same outcome. Seeded randomness, never true randomness.
6. **No modifying the AI.** Effects cannot change the report's behavior, the interpreter's system prompt, or any AI context. The AI layer is not a governance surface (except `report_scope` in Tier 4).

These boundaries are enforced in code, not by the AI. The interpreter cannot produce output that violates them, and the validation layer rejects anything that slips through.

## The Rule Space: Simulation Parameters

These are the typed, validated parameters that governance can modify. Each has a name, type, range, default, and description. The AI interpreter maps natural language proposals to changes in these parameters.

### Tier 1: Game Mechanics

| Parameter | Type | Range | Default | Description |
|-----------|------|-------|---------|-------------|
| `quarter_possessions` | int | 5–30 | 15 | Possessions per quarter (4 quarters per game) |
| `possession_duration_seconds` | int | 10–60 | 24 | Fictional seconds per possession (for display/clock) |
| `shot_clock_seconds` | int | 10–60 | 15 | Possessions exceeding this → forced bad shot |
| `three_point_value` | int | 1–10 | 3 | Points awarded for shots beyond the arc |
| `two_point_value` | int | 1–10 | 2 | Points awarded for shots inside the arc |
| `free_throw_value` | int | 1–5 | 1 | Points per made free throw |
| `personal_foul_limit` | int | 3–10 | 5 | Fouls before agent ejection |
| `team_foul_bonus_threshold` | int | 3–10 | 4 | Team fouls per half before bonus free throws |
| `three_point_distance` | float | 15.0–30.0 | 22.15 | Feet from basket to three-point line |
| `elam_trigger_quarter` | int | 1–4 | 3 | Quarter after which Elam Ending activates |
| `elam_margin` | int | 5–25 | 13 | Points added to leading score to set target |
| `halftime_stamina_recovery` | float | 0.0–0.6 | 0.40 | Fraction of max stamina recovered at halftime |
| `safety_cap_possessions` | int | 50–500 | 200 | Max total possessions before forced resolution |

<!-- TODO: Add more parameters as the simulation model solidifies -->

### Tier 2: Agent Behavior Constraints

| Parameter | Type | Range | Default | Description |
|-----------|------|-------|---------|-------------|
| `max_shot_share` | float | 0.2–1.0 | 1.0 | Max % of team shots any single agent can take |
| `min_pass_per_possession` | int | 0–5 | 0 | Minimum passes before a shot attempt is allowed |
| `max_minutes_share` | float | 0.5–1.0 | 1.0 | Max % of game any agent can play (for bench rotation) |
| `home_court_enabled` | bool | — | true | Whether venue modifiers apply |
| `home_crowd_boost` | float | 0.0–0.15 | 0.05 | Shooting % bonus for home team, scaled by venue capacity |
| `away_fatigue_factor` | float | 0.0–0.10 | 0.02 | Extra stamina drain modifier for away team |
| `crowd_pressure` | float | 0.0–0.10 | 0.03 | Ego check modifier (home boost / away penalty) |
| `altitude_stamina_penalty` | float | 0.0–0.05 | 0.01 | Stamina penalty per 1000ft altitude differential |
| `travel_fatigue_enabled` | bool | — | true | Whether distance between venues affects stamina |
| `travel_fatigue_per_mile` | float | 0.0–0.005 | 0.001 | Stamina penalty scaled by distance between venues |
| `allowed_schemes` | list[enum] | man_tight, man_switch, zone, press | all | Which defensive schemes teams are allowed to use |
| `press_allowed_quarters` | list[int] | 1–4, elam | all | When press defense is permitted |
| `team_strategy_enabled` | bool | — | false | Whether teams can submit strategic overrides (Day 1–2) |

### Tier 3: League Structure

| Parameter | Type | Range | Default | Description |
|-----------|------|-------|---------|-------------|
| `schedule_format` | enum | round_robin, divisions, random | round_robin | How matchups are generated |
| `games_per_round` | int | 1–6 | 3 | Games each team plays per simulation block |
| `trade_window_open` | bool | — | true | Whether agent trades between teams are allowed |
| `salary_cap` | int | 0–1000 | 0 | Total attribute points allowed per team (0 = no cap) |

<!-- TODO: Draft system, promotion/relegation, playoff format -->

### Tier 4: Meta-Governance

| Parameter | Type | Range | Default | Description |
|-----------|------|-------|---------|-------------|
| `propose_regen_rate` | int | 1–5 | 2 | PROPOSE tokens regenerated per day |
| `amend_regen_rate` | int | 1–5 | 2 | AMEND tokens regenerated per day |
| `boost_regen_rate` | int | 1–5 | 2 | BOOST tokens regenerated per day |
| `vote_threshold` | float | 0.5–1.0 | 0.5 | Fraction of votes needed to pass a proposal |
| `proposal_limit_per_window` | int | 1–10 | 3 | Max proposals per governance window |
| `governance_rounds_interval` | int | 1–7 | 1 | Governance window opens every N rounds (1 = every round) |
| `report_scope` | enum | full, limited, off | full | What the AI is allowed to report on |
| `fate_enabled` | bool | — | false | Whether Fate events can trigger |
| `fate_trigger_rate` | float | 0.001–0.10 | 0.02 | Base probability of Fate event per game per agent, scaled by Fate attribute |
| `fate_bypass_governance` | bool | — | false | If true, Fate events enact immediately. If false, they create auto-proposals that go through voting. |

## Output: GameResult

Every simulated game produces:

```python
@dataclass
class GameResult:
    game_id: str
    home_team: TeamRef
    away_team: TeamRef
    venue: Venue                # where the game was played
    rules: RuleSet              # snapshot of rules at game time
    seed: int                   # for deterministic replay

    home_score: int
    away_score: int
    winner: TeamRef

    # Period scores (Q1, Q2, Q3, Elam)
    quarter_scores: list[QuarterScore]  # per-quarter scoring breakdown
    elam_target: int            # the target score set after Q3
    elam_possessions: int       # possessions played in the Elam period

    box_scores: list[AgentBoxScore]   # per-agent stats
    play_by_play: list[PossessionLog] # compressed possession log

    total_possessions: int
    game_clock_seconds: int     # fictional total game time
    lead_changes: int
    largest_lead: int

    metadata: GameMetadata      # duration, rule effects, anomalies

@dataclass
class QuarterScore:
    quarter: int                # 1-4 (4 = Elam period)
    home_score: int             # points scored this quarter
    away_score: int
    possessions: int
    fouls_home: int
    fouls_away: int
```

```python
@dataclass
class AgentBoxScore:
    agent_id: str
    team_id: str
    minutes: float
    points: int
    field_goals_made: int
    field_goals_attempted: int
    three_pointers_made: int
    three_pointers_attempted: int
    free_throws_made: int
    free_throws_attempted: int
    rebounds: int
    assists: int
    steals: int
    turnovers: int
    fouls: int
    plus_minus: int            # point differential while agent on floor
```

## What the AI Needs from the Simulation

The report layers consume simulation output. Key data flows:

- **Simulation report** needs: GameResult + recent historical GameResults + current RuleSet + recent rule changes. It looks for correlations between rule changes and outcome shifts.
- **Governance report** needs: governance event log + simulation results. It looks for patterns in who benefits from which rules.
- **Private report** needs: per-player governance actions + per-team simulation results + trading history. It connects individual governance behavior to team outcomes and social dynamics.

The simulation engine should produce rich enough output that the AI can find patterns, but shouldn't try to do the pattern recognition itself. That's Opus 4.6's job.

## Decisions Log

Resolved questions, captured for the record.

| # | Question | Decision |
|---|----------|----------|
| 1 | Rebounding attribute | Derive from Speed + Defense. No separate attribute. |
| 2 | Substitutions | P0: 1 sub at the half. P1: configurable, stamina-triggered, rule-changeable. |
| 3 | Game format | 4 quarters of 15 possessions each. Elam Ending after Q3. Build from Day 1. |
| 4 | Agent generation | 9 archetypes, 360 budget, ±10 variance, AI-generated names/backstories. Teams start roughly balanced; governors trade. |
| 5 | Roster size | 4 (3 active + 1 bench). Expect to expand to 5. |
| 6 | Bench archetype | Random. Governors will trade. |
| 7 | Agent identity | Names, personalities, backstories, rivalries. AI-generated at league creation. |
| 8 | Determinism | Fully deterministic. `random.Random(seed)` instance, no global state. |
| 9 | Free throws | 2 per foul, rule-changeable. |
| 10 | Play-by-play | Structured `PossessionEvent` objects, not prose. AI converts to narrative. |
| 11 | Fate | Black-swan events with wide scope. Configurable trigger rate. Tier 4 meta-governance parameter. AI-authored in character. Not Day 1, but model must support it. |
| 12 | Name | Pinwheel Fates. |
| 13 | Move acquisition | All of the above: seeded at creation (1-2 from archetype), earned through play, and governed by players. Moves have a `source` field tracking origin. |
| 14 | Home court advantage | Included from Day 1. Venue model on each team (name, capacity, altitude, surface, location). Crowd boost, crowd pressure, altitude penalty, travel fatigue as Tier 2 rule-changeable parameters. Every modifier is a governance surface. |
| 15 | Game ending format | Elam Ending triggered at end of `elam_trigger_quarter` (default Q3). Target = leader + `elam_margin` (default 13). First to target wins on a made bucket. Replaces play-to-21 / win-by-margin. |
| 16 | Game periods | 4 quarters, `quarter_possessions` each (default 15). Halftime between Q2/Q3 with subs and stamina recovery. Team fouls reset per half. Fictional game clock: `possession_duration_seconds` (default 24) × possessions. Game must feel like basketball. |
| 17 | Defensive model | Full strategic model, not simple matching. 4 schemes (man-tight, man-switch, zone, press). Matchup assignment via cost function (threat × containment × stamina economics × game context). Adaptive per possession. All 9 attributes contribute. Scheme governance (Tier 2: `allowed_schemes`, `press_allowed_quarters`). Strategy overrides (Day 1–2): natural language instructions parsed into structured `TeamStrategy` objects. |
| 18 | Season structure | 8 teams, 3 round-robins (21 rounds, 4 games/round, 21 games/team). Governance between rounds (frequency governable via `governance_rounds_interval`). Tiebreakers: head-to-head game with extra governance round. Playoffs: top 4, best-of-5 semis, best-of-7 finals. Governance active during playoffs. Offseason governance session between seasons. |
| 19 | Team count | 8 teams (up from 6). 4 games per round, 7 rounds per round-robin. |
| 20 | Viewer experience | Three viewer surfaces: Arena (live 2x2 multi-game dashboard), Single Game (full play-by-play + commentary), Discord bot search (natural language stat queries). AI Commentary Engine uses Opus 4.6 as omniscient narrator — receives full GameResult, generates batch commentary ahead of presenter, cached for replay. ~30 REST API endpoints. Single SSE endpoint with query param filtering. See VIEWER.md. |
| 21 | Rule expressiveness | Three layers: (1) Parameter Changes (typed values, Day 0), (2) Game Effects (conditional modifications within a game, Day 2-3), (3) League Effects (cross-game modifications post-simulation, post-hackathon). AI interpreter acts as constitutional court — translates creative intent into expressible changes. Higher tiers require supermajority + more tokens. 6 safety boundaries (no code execution, no info leakage, no retroactivity, no infinite loops, no breaking determinism, no modifying AI). Rule space itself is expandable via Tier 7 supermajority vote. |

## Open Questions

No blockers. The following use proposed defaults and will be tuned from early simulation runs:

- **Shot probability curves:** Logistic curves as proposed. Tune contest modifier strength, IQ bonus/penalty, three-point interaction from 1000-game batch runs. The contest modifier will need retuning now that defensive scheme affects its strength (man-tight = full, zone = reduced).

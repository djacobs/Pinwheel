# Pinwheel Fates: Glossary

Canonical naming authority for the project. Every term that appears in code, docs, API, or Discord is defined here. When in doubt, check this file.

| Term | Definition | Aliases (acceptable) | Never use |
|------|-----------|---------------------|-----------|
| **Agent** | *Renamed to Hooper.* See **Hooper** below. | — | "player" (ambiguous with Governor), "character", "bot" |
| **Amendment** | A modification to an active Proposal while the Floor is open. Replaces the original on the ballot. No proposer veto. | — | "edit", "revision" |
| **Archetype** | One of 9 attribute templates that define an Agent's playstyle. Each emphasizes one attribute. (Sharpshooter, Floor General, Lockdown, Slasher, Iron Horse, Savant, The Closer, Wildcard, Oracle) | — | "class", "role" |
| **Arena** | The web dashboard's live multi-game view. 2x2 grid of simultaneous games with SSE updates. | "Arena view" | "homepage", "dashboard" |
| **Boost** | A Floor token spent when casting a Vote to double your vote weight. Does not add a separate vote — it amplifies the one you cast. | — | "upvote", "like" |
| **Box Score** | Per-Agent stat line for a single Game. Points, assists, rebounds, etc. | — | "stat sheet", "score card" |
| **Elam Ending** | End-of-game format: after Q3, a target score is set (leading score + elam_margin). First team to reach it wins on a made basket. | "Elam", "Elam period" | "overtime", "sudden death" |
| **Game** | A single simulated 3v3 basketball contest between two Teams. 4 quarters + Elam period. Deterministic given inputs + seed. | "Match" (acceptable but not preferred) | "round" (that's a different thing) |
| **Effect** | A non-parameter rule change enacted through the Effects system. Effects use hooks (`sim.possession.pre`, `sim.shot.pre`, etc.) to modify game behavior. Registered in the `EffectRegistry` and can be repealed via `/repeal`. | "Game Effect" | "buff", "debuff", "modifier" |
| **Governor** | A human player. Joins a Team, proposes rules, votes, trades tokens, receives Reports. The primary user. | "Gov" (informal, in Discord) | "player" (ambiguous), "user" (too generic) |
| **Report** | An AI-generated reflection on gameplay or governance patterns. Never prescriptive. Types: simulation, governance, private, tiebreaker, series, season, offseason, state_of_the_league, impact_validation, leverage, behavioral. The AI agent that generates them is the **Reporter**. | "Reflection" | "analysis", "feedback" |
| **Move** | A special ability an Agent can activate during a Possession when trigger conditions are met. Has an attribute gate. | — | "skill", "ability", "power" |
| **Possession** | One offensive sequence. The atomic unit of gameplay. Ball handler -> action selection -> resolution -> scoring/miss -> rebound. | "Play" (acceptable in commentary) | "turn" |
| **Presenter** | The server-side system that paces a pre-computed GameResult through SSE events over real time. Makes instant simulation feel like a live broadcast. | "Game Presenter" | "broadcaster", "streamer" |
| **Proposal** | A natural-language rule change submitted by a Governor. AI-interpreted into structured parameters. Goes to vote during a Window. | — | "suggestion", "request", "motion" |
| **Round** | One simulation block. With 4 teams, contains 2 simultaneous Games (every team plays once per round). With 8 teams, contains 4 simultaneous Games. The Floor opens after each Round. | "Simulation block" | "game" (that's a single contest) |
| **Rule / RuleSet** | A governable parameter with type, range, and default. The RuleSet is the complete collection of all parameters. | "Rule change" (for a diff), "Parameter" | "setting", "config" (those are system config, not game rules) |
| **Scheme** | A team's defensive strategy for a Possession. One of: man-tight, man-switch, zone, press. | "Defensive scheme" | "formation", "play" |
| **Season** | The complete competitive arc. Regular season (21 Rounds) -> tiebreakers -> playoffs -> championship -> offseason. | — | "league" (the league persists across seasons) |
| **Seed** | (1) The random seed that makes a Game deterministic. (2) The act of populating the database with initial Teams/Agents. Context makes it clear. | — | — |
| **Series** | A best-of-N playoff matchup between two Teams. The Floor opens between every Game in a Series. | "Playoff series" | "round" (that's a simulation block) |
| **Team** | A group of 4 Agents (3 starters + 1 bench). Has a Venue, color, motto. Governed by one or more Governors. | — | "squad", "roster" (roster is the list of agents on a team) |
| **Token** | Floor currency. Three types: PROPOSE (submit proposals), AMEND (modify proposals), BOOST (amplify visibility). Regenerate per Window. Tradeable. | — | "coin", "credit", "point" |
| **Venue** | A Team's home court. Has capacity, altitude, surface, location. Affects gameplay via home court modifiers. | "Home court" | "arena" (that's the web view), "stadium" |
| **The Floor** | The governance space where governors propose, vote on, and repeal rule changes. Named for the floor of a legislative chamber. | "Governance floor" | "window", "session" |
| **The Pinwheel Post** | The AI-generated newspaper section on the home page. Features headlines, game reports, and governance coverage following a strict lede hierarchy. | "The Post" | "news feed", "blog" |
| **Hooper** | A simulated basketball player on a team. Hoopers have attributes (shooting, defense, speed, etc.), can earn milestone moves, and can have backstories written by their team's governor. Formerly called "Agent". | — | "player" (ambiguous with Governor), "character", "bot" |
| **Milestone** | An achievement unlocked by a hooper reaching a statistical threshold (e.g., 50 points unlocks Fadeaway). Milestones grant signature moves that modify gameplay. | — | "level up", "badge" |
| **Repeal** | A governance action to remove an active Effect. Requires Tier 5, 2 PROPOSE tokens, and 67% supermajority. | — | "undo", "rollback" (rollback is for failed enactments, not governance actions) |
| **Tally Round** | A Round where governance tallying occurs. Unresolved Proposals are voted on, passed rules are enacted, and tokens regenerate. Frequency controlled by `PINWHEEL_GOVERNANCE_INTERVAL` (default every 1 Round, governable). Proposals, amendments, votes, and trades happen asynchronously between any rounds — the tally round is when they resolve. | "The Floor", "governance round" | "window" (there is no time-bounded window), "session", "voting period" |

---

**Usage guide:** When writing code, use the **Term** column for variable names, class names, and API paths. When writing for players, **Aliases** are acceptable in Discord bot messages and commentary. **Never Use** terms should be caught in code review.

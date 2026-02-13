# Pinwheel Fates: Glossary

Canonical naming authority for the project. Every term that appears in code, docs, API, or Discord is defined here. When in doubt, check this file.

| Term | Definition | Aliases (acceptable) | Never use |
|------|-----------|---------------------|-----------|
| **Agent** | A simulated basketball player. Has attributes, moves, a backstory. Controlled by the simulation engine, not by humans. | — | "player" (ambiguous with Governor), "character", "bot" |
| **Amendment** | A modification to an active Proposal while the Floor is open. Replaces the original on the ballot. No proposer veto. | — | "edit", "revision" |
| **Archetype** | One of 9 attribute templates that define an Agent's playstyle. Each emphasizes one attribute. (Sharpshooter, Floor General, Lockdown, Slasher, Iron Horse, Savant, The Closer, Wildcard, Oracle) | — | "class", "role" |
| **Arena** | The web dashboard's live multi-game view. 2x2 grid of simultaneous games with SSE updates. | "Arena view" | "homepage", "dashboard" |
| **Boost** | A Floor token spent to increase a Proposal's visibility. Does not add a vote. | — | "upvote", "like" |
| **Box Score** | Per-Agent stat line for a single Game. Points, assists, rebounds, etc. | — | "stat sheet", "score card" |
| **Elam Ending** | End-of-game format: after Q3, a target score is set (leading score + elam_margin). First team to reach it wins on a made basket. | "Elam", "Elam period" | "overtime", "sudden death" |
| **Game** | A single simulated 3v3 basketball contest between two Teams. 4 quarters + Elam period. Deterministic given inputs + seed. | "Match" (acceptable but not preferred) | "round" (that's a different thing) |
| **Game Effect** | A conditional rule modification within a single Game. Composed of trigger x condition x action x scope x duration. Layer 2 of rule expressiveness. | "Effect" | "buff", "debuff", "modifier" |
| **Governor** | A human player. Joins a Team, proposes rules, votes, trades tokens, receives Mirrors. The primary user. | "Gov" (informal, in Discord) | "player" (ambiguous), "user" (too generic) |
| **Mirror** | An AI-generated reflection on gameplay or Floor patterns. Never prescriptive. Types: simulation, Floor, private, tiebreaker, series, season, offseason, State of the League. | "Reflection" | "report", "analysis", "feedback" |
| **Move** | A special ability an Agent can activate during a Possession when trigger conditions are met. Has an attribute gate. | — | "skill", "ability", "power" |
| **Possession** | One offensive sequence. The atomic unit of gameplay. Ball handler -> action selection -> resolution -> scoring/miss -> rebound. | "Play" (acceptable in commentary) | "turn" |
| **Presenter** | The server-side system that paces a pre-computed GameResult through SSE events over real time. Makes instant simulation feel like a live broadcast. | "Game Presenter" | "broadcaster", "streamer" |
| **Proposal** | A natural-language rule change submitted by a Governor. AI-interpreted into structured parameters. Goes to vote during a Window. | — | "suggestion", "request", "motion" |
| **Round** | One simulation block. Contains 4 simultaneous Games (with 8 teams). The Floor opens after each Round. | "Simulation block" | "game" (that's a single contest) |
| **Rule / RuleSet** | A governable parameter with type, range, and default. The RuleSet is the complete collection of all parameters. | "Rule change" (for a diff), "Parameter" | "setting", "config" (those are system config, not game rules) |
| **Scheme** | A team's defensive strategy for a Possession. One of: man-tight, man-switch, zone, press. | "Defensive scheme" | "formation", "play" |
| **Season** | The complete competitive arc. Regular season (21 Rounds) -> tiebreakers -> playoffs -> championship -> offseason. | — | "league" (the league persists across seasons) |
| **Seed** | (1) The random seed that makes a Game deterministic. (2) The act of populating the database with initial Teams/Agents. Context makes it clear. | — | — |
| **Series** | A best-of-N playoff matchup between two Teams. The Floor opens between every Game in a Series. | "Playoff series" | "round" (that's a simulation block) |
| **Team** | A group of 4 Agents (3 starters + 1 bench). Has a Venue, color, motto. Governed by one or more Governors. | — | "squad", "roster" (roster is the list of agents on a team) |
| **Token** | Floor currency. Three types: PROPOSE (submit proposals), AMEND (modify proposals), BOOST (amplify visibility). Regenerate per Window. Tradeable. | — | "coin", "credit", "point" |
| **Venue** | A Team's home court. Has capacity, altitude, surface, location. Affects gameplay via home court modifiers. | "Home court" | "arena" (that's the web view), "stadium" |
| **Window** | A time-bounded Floor period. Proposals, amendments, and votes happen during a Window. Opens after each Round. | "The Floor", "Floor window" | "session", "voting period" (too narrow — proposals and amendments also happen) |

---

**Usage guide:** When writing code, use the **Term** column for variable names, class names, and API paths. When writing for players, **Aliases** are acceptable in Discord bot messages and commentary. **Never Use** terms should be caught in code review.

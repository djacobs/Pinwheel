# Pinwheel: 5-Day Hackathon Plan

## Constraints
- 5 days, heavy Claude Code usage
- Python/FastAPI throughout (no Rust port during hackathon)
- 6 teams at launch, architecture supports 12+
- Must be demoable: a judge should see a full governance→simulate→observe→reflect cycle in under 10 minutes

## Day 1: The Engine

**Goal:** Simulate a 3v3 basketball game, run a round-robin, store results.

### Morning
- [ ] Project scaffolding: pyproject.toml, directory structure, dev dependencies
- [ ] Data models: Agent, Team, GameResult, BoxScore, Rules (Pydantic)
- [ ] Default ruleset definition (baseline 3v3 rules before any governance)

### Afternoon
- [ ] Simulation engine: `simulate_game(teams, rules, seed) → GameResult`
  - Possession-by-possession resolution
  - Agent attributes affect decisions and outcomes
  - Rules modify simulation parameters
- [ ] Tests for simulation: deterministic with seeds, edge cases, rule variations
- [ ] Round-robin scheduler: generate matchups for N teams

### Evening
- [ ] Basic FastAPI app with endpoints: GET /games, GET /teams, GET /standings
- [ ] SQLite integration for local dev
- [ ] API tests with httpx AsyncClient
- [ ] Run a full 6-team round-robin, verify standings compute correctly

**Day 1 deliverable:** You can hit an API and get box scores from auto-simulated 3v3 basketball games.

---

## Day 2: Governance + AI Interpretation

**Goal:** Players can propose rule changes in English, Opus 4.6 interprets them, the league votes, and the simulation parameters update.

### Morning
- [ ] Governance models: Proposal, Amendment, Vote, GovernanceEvent
- [ ] Token system: TokenBalance, Trade, regeneration logic
- [ ] Event sourcing: append-only governance event log
- [ ] Tests for token economy: balances, trades, regeneration

### Afternoon
- [ ] AI interpretation pipeline:
  - Sandboxed Opus 4.6 call: natural language → structured rule change
  - Rule space validation: does the interpreted change map to valid parameters?
  - Injection defense: flag non-rule content, return explanations
  - Interpretation display: original text + structured interpretation side by side
- [ ] Tests for AI interpreter: valid proposals, ambiguous proposals, injection attempts, out-of-scope proposals
- [ ] Governance API endpoints: POST /proposals, POST /votes, POST /trades, GET /proposals/active

### Evening
- [ ] Wire it together: proposal → interpret → vote → enact → simulation uses new rules
- [ ] Integration test: full governance cycle from proposal to rule change to game simulation under new rules
- [ ] Test that a rule change actually affects game outcomes (before/after comparison with same seed)

**Day 2 deliverable:** You can POST a natural language rule change, see Opus 4.6's interpretation, vote on it, and watch it change how games play out.

---

## Day 3: The Mirrors + Game Loop

**Goal:** AI reflections are live. Games auto-run on schedule. The system feels alive.

### Morning
- [ ] Simulation mirror: after each game batch, Opus 4.6 analyzes results in context of recent rule changes
- [ ] Governance mirror: after each governance window, Opus 4.6 analyzes voting patterns, coalitions, power dynamics
- [ ] Private mirror: per-player reflections on their governance behavior, trading patterns, and how others respond to them
- [ ] Mirror models and storage

### Afternoon
- [ ] Game loop scheduler: auto-run simulation rounds at configurable intervals
  - In demo mode: every 2-3 minutes
  - In production mode: every 1-2 hours
- [ ] Governance window scheduler: open/close governance windows on cadence
- [ ] Background task management (FastAPI BackgroundTasks or separate worker)
- [ ] WebSocket or SSE endpoint for real-time game result streaming

### Evening
- [ ] Tests for mirror generation: verify reflections reference actual game/governance data
- [ ] Tests for scheduler: correct timing, proper rule application between windows
- [ ] End-to-end test: start system, auto-run games, open governance window, pass rule, see mirrors update
- [ ] Run the system for 30 minutes and observe

**Day 3 deliverable:** The system runs autonomously. Games simulate, mirrors reflect, governance windows open and close. It breathes.

---

## Day 4: The Player Experience

**Goal:** A frontend that makes governance feel tactile and mirrors feel personal.

### Morning
- [ ] Dashboard: league standings, recent results, box score viewer
- [ ] Game feed: real-time updates as games complete (WebSocket/SSE)
- [ ] Team view: roster, agent stats, team performance trends

### Afternoon
- [ ] Governance panel: active proposals with AI interpretations, voting buttons, token balances
- [ ] Token trading interface: offer/accept flow
- [ ] Proposal submission: text input → AI interpretation preview → confirm → submit
- [ ] Amendment flow: modify active proposals

### Evening
- [ ] Private mirror panel: per-player reflections, behavioral patterns, history
- [ ] Governance mirror display in the shared feed
- [ ] Team channel / discussion space (can be simple chat or text feed)
- [ ] Polish: loading states, error handling, responsive layout

**Day 4 deliverable:** A player can open their browser and play Pinwheel. See games, read mirrors, propose rules, vote, trade tokens.

---

## Day 5: Polish + Demo

**Goal:** Demoable, stable, and compelling.

### Morning
- [ ] Real-player stress test: invite 6-12 people to play accelerated demo mode
- [ ] Fix bugs surfaced by real players
- [ ] Performance profiling: simulation speed, AI response latency, WebSocket stability

### Afternoon
- [ ] Demo script: a 10-minute walkthrough that shows the full loop
  - Show a game in progress
  - Show AI simulation mirror observation
  - Propose a rule change, show AI interpretation
  - Vote, pass the rule
  - Show games under new rules
  - Show governance mirror noting the pattern
  - Show a private mirror reflection
- [ ] README for open source release
- [ ] Clean up code, ensure all tests pass, remove debug artifacts

### Evening
- [ ] Practice the demo
- [ ] Record a backup demo video (in case of live-demo gremlins)
- [ ] Prepare 2-minute pitch:
  - "Games are where humanity prototypes its next societies."
  - "We built a game where AI doesn't play — it helps you see."
  - "The urgency of better governance is the moral steel thread."
  - Live demo or video.

**Day 5 deliverable:** A polished demo that makes a judge feel the game's thesis in their gut.

---

## Risk Register

| Risk | Mitigation |
|------|------------|
| Simulation engine too slow for tight loop | Profile early Day 1. numpy/vectorized math. Can always reduce games-per-round. |
| Opus 4.6 latency delays mirrors | Async calls, cache aggressively, generate mirrors in background not on-demand. |
| Prompt injection in proposals | Sandboxed interpretation pipeline. Rule space validation. Day 2 priority. |
| Not enough real players for demo | Can simulate governance with scripted "bot governors" that propose/vote with different personalities. |
| Frontend takes too long | Day 4 scope can shrink to a functional but minimal UI. The backend is the star. |
| Rule changes break simulation | Parameter validation with strict ranges. Simulation engine rejects invalid rule states. |

## Success Criteria

The project succeeds if a judge watches the demo and:
1. Understands the thesis (AI amplifies governance, doesn't replace it) within 60 seconds
2. Sees a full governance cycle play out with real consequences
3. Reads a private mirror reflection and thinks "I'd want to know that about myself"
4. Leaves wanting to play

## Post-Demo Steps

- [ ] **Reset Discord bot token** — token was shared in plaintext during setup. Go to [Developer Portal](https://discord.com/developers/applications) → Bot → Reset Token. Update `.env` and `fly secrets set DISCORD_BOT_TOKEN=<new>`.
- [ ] **Reset Discord client secret** — same reason. OAuth2 → Reset Secret. Update `.env` and `fly secrets set DISCORD_CLIENT_SECRET=<new>`.
- [ ] **Rotate session secret key** — replace the dev default (`pinwheel-dev-secret-change-in-production`) with a cryptographically random value for production. `fly secrets set SESSION_SECRET_KEY=$(openssl rand -hex 32)`.
- [ ] **Set production redirect URI** — ensure `DISCORD_REDIRECT_URI=https://pinwheel.fly.dev/auth/callback` in Fly secrets.
- [ ] **Disable Public Bot** — in Developer Portal → Bot, uncheck "Public Bot" so only you can add it to servers.
- [ ] **Review OAuth2 redirect URIs** — remove `http://localhost:8000/auth/callback` from the Developer Portal if no longer needed for local dev.

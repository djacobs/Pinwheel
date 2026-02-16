# Pinwheel Fates: Product Overview & User Journey

## Product Thesis

Pinwheel Fates is an auto-simulated 3v3 basketball league where human players govern the rules through AI-interpreted natural language proposals. Starts out as basketball, finishes as ???. The AI (Opus 4.6) serves as the social system's unconscious — surfacing patterns in gameplay and governance that players cannot see from inside the system.

The product validates a single hypothesis: **visibility improves governance.** If the AI report system makes invisible social dynamics legible, players will govern more thoughtfully, and the game will become more fun as a result.

## Goals & Success Criteria

VISION.md defines five goals. Each needs a measurable success threshold to know whether the product is working.

| # | Goal | What It Means | Success Criterion | How to Measure |
|---|------|---------------|-------------------|----------------|
| 1 | **AI as judgment amplifier** | Opus 4.6 makes consequences visible, not decisions for players | Report Impact > 30% — reports change governor behavior in the next governance window at least 30% of the time | Compare each governor's actions post-report to their pre-report pattern. Did they vote differently, propose in a new tier, trade with a new partner, or change their team strategy? |
| 2 | **Tight feedback loop** | Governance → simulation → consequences fast enough to feel in your gut | Time-to-consequence < 60 minutes — propose a rule, see it affect a game within 1 hour | Measure elapsed time from governance enactment timestamp to first game completed under the new rule |
| 3 | **Invisible dynamics legible** | Coalitions, power concentration, free-riding made visible | Report Read Rate > 60%. Report Dwell Time > 30s. At least 1 governance report observation per window that references a pattern no individual player surfaced in the feed first | Track report.private.view events, dwell times, and cross-reference report content with feed content |
| 4 | **Resonant computing embodied** | Software that is private, dedicated, plural, adaptable, prosocial | Architectural — this is true or false by design | Audit: private reports are only visible to the individual. AI has no engagement optimization. Governance is distributed. Rules are open-ended. The game practices self-governance. |
| 5 | **Genuinely fun** | The craft and joy of the game stands on its own | Return Rate > 70% (governors return to next governance window). Governance Participation Rate > 40% | Track session patterns and governance action rates |

The PLAN.md "Success Criteria" section adds four hackathon-specific tests: (1) a judge understands the thesis within 60 seconds, (2) sees a full governance cycle, (3) reads a private report and wants one, (4) leaves wanting to play. These are demo-day criteria, not product health metrics.

## User Personas

### The Governor

The primary user. A person who joins a team, proposes rules, votes, trades tokens, and receives AI report reflections. They experience the full Govern → Simulate → Observe → Reflect loop. Their engagement is the product's lifeblood.

### The Spectator

A secondary user. A person who watches games, reads shared reports, follows the governance drama, and discusses in #trash-talk. They do not take governance actions. Their engagement validates that the game is entertaining beyond the governance mechanics.

### The Admin

The operator. Sets up the league, seeds teams, manages the Discord server, monitors system health. Not a player-facing persona but essential for the product to function.

## User Journey: Phase by Phase

### Phase 0: Discovery & Onboarding

**The moment:** A person hears about Pinwheel Fates and wants to play.

**What exists:**
- The `#new-governors` Discord channel with a bot greeting (PLAYER.md)
- The bot greeting shows the team list and asks the user to pick
- Self-selection is deliberate — tribalism is a feature

**User benefit:** The governor chooses their team because they *want* to be on it. Emotional investment starts at signup.

**Desired journey:** Hear about the game → join Discord → read the bot greeting → understand what governing means → pick a team → get team role and private channel access → see what's happening in the league right now.

**Gaps identified:**
- No player-facing explanation of what the game is and why it matters. VISION.md speaks to builders, not players. The bot greeting jumps to "pick your team" without explaining the time commitment, the governance loop, or what a report is.
- A governor who joins mid-season has no way to understand rule history, the current political landscape, or why certain rules exist. The `#rules` channel shows the current ruleset but not its story.
- No onboarding funnel metrics exist. We cannot measure drop-off between "joins Discord" and "picks a team" and "takes first governance action."

**Metrics needed:**
- `governor.onboard.server_join` — joined the Discord server
- `governor.onboard.team_select` — picked a team
- `governor.onboard.first_action` — first governance action (proposal, vote, or trade)
- Time-to-first-action for new governors (distinct from returning governors)

**Functions that serve this phase:**
| Function | Purpose | Exists? |
|----------|---------|---------|
| Bot greeting in #new-governors | Explain the game, present teams | Yes (PLAYER.md) |
| Team selection (/join) | Assign governor to team | Yes (PLAYER.md) |
| Team lock | Prevent intelligence leakage from strategy channels | Yes (PLAYER.md) |
| Player-facing "what is this" | 30-second explanation for new players | **No** |
| Rule history context | Help mid-season joiners understand current state | **No** |

---

### Phase 1: First Governance Window

**The moment:** A new governor's first governance window opens. They can see active proposals, vote, and maybe propose.

**What exists:**
- The full governance pipeline: `/propose` → AI interpretation → confirm → publish → debate thread → `/amend` → `/vote` → enact (PLAYER.md, RUN_OF_PLAY.md)
- Token economy: PROPOSE, AMEND, BOOST tokens with regeneration and trading (RUN_OF_PLAY.md)
- Amendment mechanic: natural language → AI interprets in context → replaces original on ballot → no proposer veto (RUN_OF_PLAY.md)
- Vote normalization: each team weight = 1.0, divided among active governors (PLAYER.md)
- Three-layer feed topology: Public Square, Legislature Floor, War Room (RUN_OF_PLAY.md)

**User benefit:** The governor exercises real power over a live system. The AI interpretation pipeline makes rule-writing accessible — you don't need to understand simulation parameters, you just write what you want in English. The token economy makes every governance action a meaningful choice (spend your PROPOSE token now, or save it?).

**Desired journey:** See active proposals → understand them via AI interpretations → vote → (optionally) propose a rule → see the AI's interpretation of your proposal → confirm or revise → watch the community debate → see the vote result.

**Gaps identified:**
- The strategic difference between amending and counter-proposing is not surfaced to the user. A governor who disagrees with a proposal has three options (vote no, amend, counter-propose) but the tradeoffs are not explained contextually.
- No amendment-specific metrics. We track proposals and votes but not `governance.amendment.submit`, amendment success rate, or whether amended proposals pass at higher rates.

**Metrics (existing):**
- `governance.proposal.submit` / `governance.proposal.abandon`
- `governance.vote.cast` / `governance.vote.skip`
- `token.trade.offer` / `token.trade.accept` / `token.trade.reject`
- Governance Participation Rate, Vote Participation Rate, Proposal Conversion Rate

**Metrics needed:**
- `governance.amendment.submit` — an amendment was submitted
- `governance.amendment.accept` — the amended version went to vote
- Amendment pass rate vs. unamended proposal pass rate

**Functions that serve this phase:**
| Function | Purpose | Exists? |
|----------|---------|---------|
| /propose | Submit a rule change | Yes |
| AI interpretation pipeline | Parse natural language into structured rules | Yes |
| /amend | Modify active proposals | Yes |
| /vote | Cast weighted vote | Yes |
| /boost | Amplify proposal visibility | Yes |
| /trade | Trade tokens between governors | Yes |
| /tokens | Check token balance | Yes |
| Hidden votes | Prevent bandwagon effects | Yes |
| Proposal debate threads | Structured discussion per proposal | Yes |
| Amendment contextual explanation | Explain amend vs. counter-propose tradeoff | **No** |

---

### Phase 2: Watching Games

**The moment:** Games simulate. The governor watches via the Arena (web dashboard) or receives results in Discord.

**What exists:**
- The Arena: 2x2 live game grid with AI commentary, Elam countdown, dramatic moment alerts (VIEWER.md)
- Single Game view: full play-by-play, box score, rule context panel (VIEWER.md)
- AI Commentary Engine: omniscient narrator with batch generation, cached for replay (VIEWER.md)
- Game Presenter: instant simulation (~100ms) paced to 20-30 min via SSE (GAME_LOOP.md)
- Dramatic pacing: variable speed based on game state (VIEWER.md)
- REST API: ~30 endpoints across game, team, agent, governance, report resources (VIEWER.md)

**User benefit:** The governor sees what their governance decisions did. The rule context panel connects specific possession outcomes to specific rules. The AI commentary narrates the drama, connecting gameplay to the governance decisions that shaped it.

**Desired journey:** Open the Arena → watch live games → notice how rules affect play → read AI commentary connecting gameplay to governance → check box scores → understand which rules helped or hurt your team → form opinions for the next governance window.

**Gaps identified:**
- The rule context panel is described but its interaction model is undefined. Does it passively list active rules, or does it actively highlight when a specific rule affected a specific possession? The latter is transformative for Goal #1 (judgment amplifier); the former is a reference sidebar.
- No metrics for *which part* of the viewing experience delivers value. We measure that someone watched a game but not whether they engaged with commentary, the rule context panel, or the box score.

**Metrics (existing):**
- `game.result.view` — viewed a game result
- Time spent on game

**Metrics needed:**
- `game.commentary.expand` — engaged with AI commentary
- `game.rule_context.interact` — clicked or hovered on rule context panel
- `game.replay.start` — replayed a previous game
- `game.view.completion` — watched from start to finish vs. skipped to box score

**Functions that serve this phase:**
| Function | Purpose | Exists? |
|----------|---------|---------|
| The Arena (2x2 live grid) | Multi-game spectating | Yes |
| Single Game view | Deep dive into one game | Yes |
| AI Commentary Engine | Narrative connecting gameplay to governance | Yes |
| Rule context panel | Show which rules affect current play | Yes (defined) |
| Rule-to-possession highlighting | Active connection between rule and outcome | **Undefined** |
| Game Presenter + SSE | Paced delivery of pre-computed results | Yes |
| Box scores | Traditional stat summary | Yes |
| Replay | Re-watch previous games | Yes |

---

### Phase 3: Receiving Reports

**The moment:** A governance window closes. Shared reports post to #reports. A private report arrives via DM.

**What exists:**
- 11 report types: simulation, governance, private, tiebreaker, series, season, offseason, state_of_the_league, impact_validation, leverage, behavioral (GAME_LOOP.md). Note: offseason and state_of_the_league types exist in the `ReportType` enum but are not yet generated.
- Shared report delivery to Discord channels and web dashboard (PLAYER.md)
- Private report delivery via DM and personalized dashboard (PLAYER.md)
- Report tone: observational, pointed, never prescriptive (PLAYER.md)
- Report staleness tolerance and tiered depth as cost optimization (INSTRUMENTATION.md)

**User benefit:** The governor sees patterns they cannot see from inside the system. The private report connects their individual actions to collective consequences. The governance report surfaces coalitions, power concentration, and unintended consequences.

**Desired journey:** Receive private report → read it → feel seen → connect the insight to your recent actions → decide whether to change your approach → act on that decision in the next governance window.

**Gaps identified — this is the product's most important gap:**
- There is no articulation of what a player is supposed to *do* with a report. The report "never tells governors what to do" — philosophically clean, practically ambiguous. The bridge from awareness to action is undesigned.
- The product's core thesis is that visibility improves governance. If reports don't lead to changed behavior, the thesis fails. We need to measure whether reports change behavior — not just whether they're read.
- No "Report Impact" metric exists. We measure consumption (read rate, dwell time) but not impact (did behavior change after reading?).

**Metrics (existing):**
- `report.private.view` / `report.private.dismiss`
- Report Read Rate, Report Dwell Time

**Metrics needed:**
- **Report Impact:** For each private report, track whether the governor's behavior in the next governance window differs from their pattern in the previous N windows. Did they vote differently, propose in a new tier, trade with a new partner, or change their team strategy? This is the thesis-validating metric.
- `report.shared.view` — engagement with shared reports (simulation, governance)
- `report.shared.dwell_time` — time spent on shared reports

**Functions that serve this phase:**
| Function | Purpose | Exists? |
|----------|---------|---------|
| Private report generation | Per-player behavioral reflections | Yes |
| Simulation report generation | Game outcome analysis in context of rules | Yes |
| Governance report generation | Voting pattern, coalition, power analysis | Yes |
| Report delivery (DM + dashboard) | Get reports to players | Yes |
| Series/season/offseason reports | Season-arc narrative | Yes |
| State of the League report | Periodic zoom-out | Yes |
| Report → action bridge | Guide from insight to governance action | **Undesigned** |
| Report Impact metric | Measure whether reports change behavior | **No** |

---

### Phase 4: Deepening Engagement

**The moment:** A governor becomes more sophisticated over time. They draft proposals privately, build coalitions, trade strategically, submit team strategies.

**What exists:**
- Private proposal drafting in team channels before public submission (PLAYER.md)
- Token trading across teams creates alliances and obligations (RUN_OF_PLAY.md)
- Team strategy overrides via /strategy: natural language tactical instructions parsed into structured TeamStrategy objects (SIMULATION.md)
- The tier system (1 → 7) creates natural escalation of governance ambition (SIMULATION.md)
- Cross-team DMs through the bot (PLAYER.md)

**User benefit:** The governor's influence grows as they master the system. They move from voting on others' proposals to shaping the league's future. The political economy (token trading, coalition-building) creates emergent social dynamics that the reports then surface.

**Desired journey:** Start with simple votes → propose a Tier 1 rule change → see its impact → start trading tokens → draft proposals in team channels → build cross-team alliances → propose higher-tier rules → submit team strategies → govern the governance system itself (Tier 4).

**Gaps identified:**
- No explicit player progression model. The game does not surface "you've leveled up" moments. A governor who has only ever proposed Tier 1 changes could be nudged toward deeper governance through the report system, but this isn't designed as an intentional progression.
- The /strategy mechanic has no discovery path. Governors need to know the command exists, understand what strategies are available, and see impact on outcomes. None of this is surfaced.
- No metrics for governance sophistication. We cannot measure whether players propose at higher tiers over time, use more complex governance tools, or deepen their cross-team relationships.

**Metrics needed:**
- Governance Sophistication Index: average tier of proposals by a governor over time (should trend upward)
- Cross-team trade ratio: cross-team trades / total trades (higher = more sophisticated political economy)
- Strategy adoption rate: teams using /strategy / total teams
- Strategy impact: win rate differential when strategy is active vs. default AI

**Functions that serve this phase:**
| Function | Purpose | Exists? |
|----------|---------|---------|
| Private proposal drafting | Workshop proposals before going public | Yes |
| Token trading | Create political economy | Yes |
| /strategy | Team tactical overrides | Yes |
| Tier system (1-7) | Escalating governance ambition | Yes |
| Cross-team DMs | Back-channel dealing | Yes |
| Progression surfacing | Show governors their growth | **No** |
| /strategy discovery | Introduce governors to tactical governance | **No** |

---

### Phase 5: Season Arc (Playoffs, Championship, Offseason)

**The moment:** The season peaks with playoffs, a championship, and offseason governance.

**What exists:**
- Regular season: 3 round-robins, 21 rounds (GAME_LOOP.md)
- Tiebreakers: head-to-head game + extra governance round (GAME_LOOP.md)
- Playoffs: top 4 qualify, best-of-3 semis, best-of-5 finals (GAME_LOOP.md). Series lengths governable via `playoff_semis_best_of` and `playoff_finals_best_of`.
- Governance between every playoff game (GAME_LOOP.md)
- Season report, awards, championship narrative (GAME_LOOP.md)
- Offseason governance: carry-forward vote, roster changes, next season params (GAME_LOOP.md)

**User benefit:** The stakes escalate. Governance between playoff games is where things get wild — rules change mid-series. The season report writes the definitive narrative, giving the community a shared story.

**Desired journey (qualifying teams):** Clinch playoff spot → higher-stakes governance → playoff series with rule changes between games → championship → read season report → offseason governance → next season.

**Desired journey (eliminated teams):** Eliminated → ??? → offseason governance → next season.

**Gaps identified:**
- The emotional arc for eliminated teams is undesigned. Four of eight teams don't make playoffs. Their competitive journey ends at round 21, but the season continues for 2-3 more phases. These governors can still vote on league-wide proposals, but their team's narrative is over. Risk: half the governor base disengages for the final third of the season.
- No season-level engagement metrics. Return Rate is per governance window, but there's no "playoff engagement for non-qualifying teams" metric.

**Metrics needed:**
- `engagement_by_team_standing` — governance participation rate segmented by team playoff status
- Eliminated-team retention: do governors on eliminated teams return for playoff governance windows?
- Offseason participation rate: what fraction of governors participate in the constitutional convention?

**Functions that serve this phase:**
| Function | Purpose | Exists? |
|----------|---------|---------|
| Playoff bracket | Competitive arc | Yes |
| Governance between playoff games | High-stakes rule changes | Yes |
| Season report | Definitive narrative | Yes |
| Awards | Recognition and closure | Yes |
| Offseason governance | Constitutional convention | Yes |
| Eliminated-team engagement | Keep non-qualifying governors invested | **No** |

---

### Phase 6: The Spectator

**The moment:** A non-governor watches games, reads reports, follows the drama.

**What exists:**
- Spectator role: read access to league-wide channels, post in #trash-talk (PLAYER.md)
- Web dashboard fully accessible without login (PLAYER.md)
- Discord OAuth only needed for personalized content (PLAYER.md)

**User benefit:** The spectator enjoys the drama of governance and gameplay without the commitment of governing. They may convert to a governor.

**Desired journey:** Discover the game → watch a game → read the shared report → follow a team → engage in #trash-talk → (optionally) convert to governor.

**Gaps identified:**
- The spectator journey is completely undesigned beyond access permissions. There is no articulation of why a spectator would return. What's the spectator-specific value proposition?
- Agents have names, backstories, and rivalries — but the spectator's relationship to these agents isn't surfaced. Can a spectator "follow" a team? Get notifications?
- Zero spectator-specific metrics. We cannot distinguish a spectator's session from a governor's, measure spectator retention, or track spectator-to-governor conversion.

**Metrics needed:**
- `user.role` dimension on all events (governor vs. spectator)
- `spectator.session.duration` — how long spectators stay
- `spectator.conversion.governor` — spectator who becomes a governor
- `spectator.content.preference` — what do spectators engage with?

**Functions that serve this phase:**
| Function | Purpose | Exists? |
|----------|---------|---------|
| Spectator Discord role | Read-only access | Yes |
| Public web dashboard | No-login viewing | Yes |
| #trash-talk channel | Spectator participation | Yes |
| Team following / notifications | Spectator attachment | **No** |
| Spectator-to-governor conversion | Grow the governor base | **No** |

---

## Summary: Gap Register

| # | Gap | Severity | Phase | Recommendation | Decision Deadline | Default Fallback |
|---|-----|----------|-------|----------------|-------------------|------------------|
| 1 | **Report → action bridge** | Critical | 3 | Design and measure whether reports change governor behavior. This is the thesis-validating metric. | Before Day 3 (report delivery) | Report displays only — no action bridge. Measure read rate and dwell time as proxy. |
| 2 | **Onboarding funnel** | High | 0 | Add onboarding events. Write a player-facing "what is this" explanation. | Before Discord bot (Day 2-3) | **PARTIAL:** `/admin/roster` page shows enrolled governors with team + token balances. NEW_GOVERNOR_GUIDE written. Onboarding metrics not instrumented. |
| 3 | **Amendment instrumentation** | Medium | 1 | Add `governance.amendment.*` events. Measure amendment impact on proposal pass rates. | Before Day 3 (governance polish) | Amendments work but are not instrumented separately from proposals. |
| 4 | **Eliminated-team retention** | High | 5 | Design the post-elimination governor role. Measure engagement by team standing. | Post-hackathon | Eliminated governors can still vote on league-wide proposals. No special engagement design. |
| 5 | **Success criteria on goals** | High | All | Convert the five VISION.md goals from aspirations to the measurable thresholds defined in this document. | Before Day 2 (thresholds inform instrumentation) | Use thresholds defined in this doc's Goals table as-is. |
| 6 | **Rule context panel interaction model** | Medium | 2 | Define whether the panel passively lists rules or actively highlights rule-to-outcome connections. | Before frontend (Day 4) | **DONE:** Rule context sections in `game.html` and `play.html` templates show active rules alongside gameplay. |
| 7 | **Viewing engagement depth** | Medium | 2 | Add `game.commentary.expand`, `game.rule_context.interact`, `game.replay.start` events. | Before frontend (Day 4) | Events defined but not wired to alarms until post-hackathon. |
| 8 | **Spectator journey** | Medium | 6 | Design the spectator value proposition, team-following, and conversion path. | Post-hackathon | Spectators have read-only web access and #trash-talk. No team-following or conversion tracking. |
| 9 | **Governance sophistication metrics** | Low | 4 | Track proposal tier trends, cross-team trade ratio, strategy adoption. | Post-hackathon | Not tracked during hackathon. Report content may reference patterns qualitatively. |
| 10 | **Player progression surfacing** | Low | 4 | Use the report system to reflect governance growth, not just governance patterns. | Post-hackathon | Reports reflect current patterns only. No longitudinal progression analysis. |

---

## Open Questions — Decision Table

Consolidated from open questions in VIEWER.md, GAME_LOOP.md, PLAYER.md, and SIMULATION.md.

| # | Question | Options | Recommendation | Deadline | Default if Not Decided |
|---|----------|---------|----------------|----------|----------------------|
| 1 | **Report → action bridge:** How should reports connect insight to governance action? | (a) Report includes contextual action buttons (e.g., "Propose a rule about this") (b) Report links to relevant governance page sections (c) Report is read-only; action is implicit | **(b) Link to governance.** Buttons are too prescriptive (violates "never tells governors what to do"). Links preserve agency while reducing friction. | Before Day 3 (report delivery) | Report is read-only. Governors navigate to governance independently. |
| 2 | **Rule context panel interaction:** Does the panel passively list rules or actively highlight rule-to-outcome connections? | (a) Passive list of non-default rules (b) Active highlighting: pulse/color when a rule affects the current possession (c) Both — list always visible, highlights on relevant possessions | **(c) Both.** List is always visible. Highlights pulse when a rule affected the outcome. This is the key judgment-amplifier UX. | Before frontend (Day 4) | Active highlighting only (no persistent list). |
| 3 | **Commentary model tier:** Which Claude model generates live commentary? | (a) Opus 4.6 — highest quality, highest cost (~$0.15/game) (b) Sonnet 4.5 — good quality, moderate cost (~$0.03/game) (c) Haiku 4.5 — fast and cheap (~$0.005/game), lower narrative quality | **(b) Sonnet 4.5.** Commentary is high-volume (batches per game). Opus for reports (low-volume, high-stakes). Sonnet for commentary (high-volume, moderate-stakes). | Before commentary engine (Day 3) | Sonnet 4.5. Switch to Haiku if costs spike. |
| 4 | **Governance window timing:** How are windows opened and closed? | (a) Pure cron — windows open on schedule, close on schedule (b) Cron + admin override — scheduled but admins can extend/close early (c) Dynamic — window stays open until quorum reached | **(b) Cron + admin override.** Predictable for governors, flexible for demos and edge cases. | Before scheduler (Day 2) | Pure cron. Windows open and close on `PINWHEEL_GOV_WINDOW` schedule. |
| 5 | **Concurrent simulation blocks:** What happens if a new round triggers while the presenter is still pacing the previous round? | (a) Queue — new round waits until current presentation finishes (b) Overlap — multiple rounds present simultaneously (c) Fast-forward — current round finishes instantly, new round starts pacing | **(a) Queue.** Overlap is confusing for viewers. Fast-forward loses dramatic pacing. Queue is simplest and preserves the viewing experience. | Before game loop (Day 2) | Queue for next block. Presenter finishes current round before starting next. |
| 6 | **Report priority:** When multiple reports are ready (simulation + governance + private), what order are they delivered? | (a) Private first, then shared (b) Shared first, then private (c) Interleaved (shared → private → shared) | **(a) Private first.** The private report is the product's differentiator. Deliver it while the governance report is generating. Shared reports land in channels where they persist; private reports land in DMs where timeliness matters. | Before report delivery (Day 3) | Private first. Shared reports post to channels within 60s. |

## Metrics Coverage Matrix

| User Journey Phase | Events Defined | Derived Metrics Defined | Alarms Defined | Success Threshold Defined |
|---|---|---|---|---|
| 0. Discovery & Onboarding | **No** | **No** | **No** | **No** |
| 1. First Governance Window | Yes | Yes | Yes | **No** |
| 2. Watching Games | Partial | **No** | **No** | **No** |
| 3. Receiving Reports | Yes | Yes | Yes | **No** |
| 4. Deepening Engagement | Partial | Partial | **No** | **No** |
| 5. Season Arc | **No** | **No** | **No** | **No** |
| 6. Spectator | **No** | **No** | **No** | **No** |

The governance loop (Phases 1 and 3) has strong instrumentation coverage. Everything else is thin or absent. The watching experience (Phase 2) — which is where the tight feedback loop either works or doesn't — has almost no metrics.

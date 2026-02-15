# Pinwheel: Instrumentation & Profiling

Three optimization targets, three measurement systems. Every metric we collect serves at least one of these goals: making the game more joyful, making the experience more performant, or making the AI layer more cost-efficient.

## A) Gameplay Joy Instrumentation

The question: **Is the game fun? Where does engagement live? Where does it die?**

### Player Behavior Events

Every player action is a timestamped event. These are the raw signal for understanding what players actually do vs. what we designed for them to do.

| Event | Payload | What It Reveals |
|-------|---------|----------------|
| `governance.proposal.submit` | player_id, proposal_text, token_spent, timestamp | Who's engaged enough to propose? How often? |
| `governance.proposal.abandon` | player_id, draft_text, time_spent_drafting | Where do proposals die? Is the AI interpretation step a friction point? |
| `governance.vote.cast` | player_id, proposal_id, vote, boost_used, time_to_vote | How long do people deliberate? Do they boost? |
| `governance.vote.skip` | player_id, proposal_id, window_id | Who's disengaging? On which proposals? |
| `token.trade.offer` | from_player, to_player, offered, requested | Who trades? With whom? What's the economy's shape? |
| `token.trade.accept` | trade_id, time_to_accept | How fast do deals close? |
| `token.trade.reject` | trade_id, time_to_reject | What gets rejected? |
| `report.private.view` | player_id, report_id, time_spent_reading | Do people read their reports? How long? |
| `report.private.dismiss` | player_id, report_id, time_before_dismiss | Are reports being ignored? |
| `game.result.view` | player_id, game_id, time_spent | Do people watch games? Which ones? |
| `feed.scroll_depth` | player_id, session_id, max_depth | How far into the feed do people go? |
| `session.start` / `session.end` | player_id, duration, pages_visited | Session length and navigation patterns |

### Derived Joy Metrics

Computed from raw events, reviewed daily:

- **Governance Participation Rate:** proposals_submitted / (players × governance_windows). If this drops below 30%, the game is losing engagement.
- **Vote Participation Rate:** votes_cast / (eligible_voters × proposals). Healthy floor: 60%.
- **Report Read Rate:** report_views / report_generated. If players aren't reading reports, the AI layer isn't earning its weight.
- **Report Dwell Time:** Average seconds spent on private report. Longer = more valuable. Target: >30 seconds.
- **Proposal Conversion Rate:** proposals_that_receive_votes / proposals_submitted. If proposals are being ignored, the governance surface has a discovery problem.
- **Token Velocity:** trades_per_day / total_tokens_in_circulation. High velocity = active political economy. Low = stagnation.
- **Return Rate:** players_who_return_next_governance_window / players_active_this_window. The single most important retention signal.
- **Time-to-First-Action:** seconds from session start to first governance action. If this is >60s, the dashboard isn't surfacing the right information.

### Joy Alarms

Automated flags for when the game might not be fun:

- A player hasn't taken a governance action in 2+ windows → potential disengagement
- A team hasn't had a successful proposal in 5+ windows → political exclusion
- Token trading volume drops >50% window-over-window → economy stalling
- Report read rate drops below 20% → reflections aren't resonating
- A single player/coalition has passed >60% of proposals in a week → power concentration (this is also gameplay — the governance report should surface it)

## B) UX Performance Instrumentation

The question: **Is the system fast enough that it never breaks flow?**

### Latency Tracking

Every operation that touches the network or CPU gets timed:

| Operation | Target | Alarm Threshold |
|-----------|--------|----------------|
| `api.games.list` | <100ms | >300ms |
| `api.standings.get` | <50ms | >200ms |
| `api.proposal.submit` | <200ms (excl. AI) | >500ms |
| `ai.interpret.proposal` | <3s | >8s |
| `ai.report.simulation` | <5s | >15s |
| `ai.report.governance` | <5s | >15s |
| `ai.report.private` | <3s per player | >10s |
| `sim.game.run` | <500ms | >2s |
| `sim.round.run` (all matchups) | <5s | >15s |
| `ws.game_result.broadcast` | <50ms | >200ms |
| `db.write.game_result` | <50ms | >200ms |
| `db.read.player_history` | <100ms | >500ms |

### Performance Profiling

Built into the system from Day 1, not bolted on later:

- **Middleware timer:** FastAPI middleware that logs request path, method, duration, and status code for every request. Structured JSON logs.
- **Simulation profiler:** The simulation engine has an optional `profile=True` flag that records time-per-possession, time-per-decision-node, and total game time. Used to find hot paths.
- **AI call tracker:** Every Opus 4.6 call is logged with: prompt token count, completion token count, latency, cache hit (if applicable), and the calling context (interpretation vs. report type).
- **WebSocket health:** Track connection count, message throughput, failed deliveries, and reconnection rate.
- **Database query log:** Log slow queries (>100ms) with the full query and execution plan.

### Performance Dashboard

**Status: Renamed to `/admin/evals`.** The evaluation dashboard (`eval_dashboard.py`) tracks AI report quality, governance health (GQI), injection classification, and scenario flags across 12 eval modules. The original perf metrics (latencies, throughput, connection pools) are not yet surfaced in a dashboard.

The `/admin/evals` endpoint (not player-facing) shows:

- Eval aggregate stats (grounding, prescriptive, behavioral scores)
- Governance Quality Index (GQI) trends
- Injection classification history
- Scenario flags and rule evaluator outputs
- Report quality metrics by type

### Evaluation Dashboard (Implemented)

The eval dashboard at `/admin/evals` is the primary health check for AI report quality and governance integrity. It aggregates data from 12 eval modules into a single admin-facing page. No individual report text or private content is exposed.

**Route:** `GET /admin/evals` (optional `?round=N` query parameter for round-specific drill-down)

**Safety Summary (traffic light):**

The dashboard opens with a computed traffic-light status (green / yellow / red) derived from all eval signals:
- **Green ("All Clear"):** No critical flags, no injection attempts, grounding rate healthy, golden dataset passing.
- **Yellow ("Warnings Present"):** Warning-level flags, isolated injection attempts, prescriptive language detected, grounding below 50%, or golden pass rate below 70%.
- **Red ("Issues Detected"):** Critical flags active or 3+ injection attempts detected.

The summary also shows total reports evaluated, injection attempt count, eval coverage percentage (how many of the 5 signal types have data), and the latest GQI composite score.

**Eval panels:**

| Panel | Source Module | What It Shows |
|-------|-------------|---------------|
| Grounding Rate | `evals/grounding.py` | Percentage of AI reports that reference real entities (team names, governors, rule parameters) from the simulation data. |
| Prescriptive Flags | `evals/prescriptive.py` | Count of reports flagged for directive language ("should", "must", "needs to"). The reporter's constraint is "describe, never prescribe." |
| Report Impact Rate | `evals/behavioral.py` | Whether governors who received private reports changed behavior in the next governance window, compared to their running baseline. Correlation, not causation. |
| Rubric Summary | `evals/rubric.py` | Manual quality scores for public reports across four dimensions: grounded, novel, concise, observational. |
| Golden Dataset | `evals/golden.py` | Pass rate against 20 curated eval cases with known-correct report patterns. |
| A/B Win Rates | `evals/ab_compare.py` | Dual-prompt comparison results -- which prompt version produces better reports. |
| GQI Trend | `evals/gqi.py` | Governance Quality Index over the last 5 rounds: proposal diversity (Shannon entropy), participation breadth (inverted Gini), consequence awareness, vote deliberation. |
| Scenario Flags | `evals/flags.py` | Active flags for unusual game states: dominant strategies, degenerate equilibria, power concentration. Shows flag type, severity, round, and details. |
| Rule Evaluation | `evals/rule_evaluator.py` | AI-powered admin analysis: suggested experiments, stale parameters, equilibrium health notes, flagged concerns. The rule evaluator prescribes freely -- it is the admin's advisor, not the players'. |
| Injection Classifications | `evals/injection.py` | Recent injection classifier results: proposal preview, classification, confidence, reason, whether it was blocked. Up to 20 most recent, with counts of attempts and blocks. |

**Round navigation:** When `?round=N` is specified, all eval panels filter to that specific round. Previous/next round links allow stepping through available rounds.

**Auth:** In production with OAuth, redirects unauthenticated users to login. In dev mode without OAuth, the page is open for testing.

## C) Token Cost Instrumentation

The question: **How much does each unit of game value cost in API tokens, and how do we reduce it without reducing value?**

### Token Accounting

Every Opus 4.6 call is tagged with its purpose and tracked:

| Call Type | Frequency | Est. Input Tokens | Est. Output Tokens |
|-----------|-----------|-------------------|-------------------|
| `interpret.proposal` | ~3 per governance window | 500-1000 (proposal + rules context + system prompt) | 200-500 (structured rule + explanation) |
| `report.simulation` | 1 per simulation block | 2000-5000 (recent game results + rule history) | 300-800 (observations) |
| `report.governance` | 1 per governance window | 1500-3000 (governance event log + recent results) | 300-800 (observations) |
| `report.private` | 1 per player per window | 1000-2000 (player history + governance actions + team results) | 200-500 (reflection) |
| `interpret.ambiguity` | occasional | 300-600 (clarification request) | 100-300 (clarification) |

### Cost Model

With 6 teams, ~12 players, 2 governance windows/day, ~10 games/day:

```
Per governance window:
  Interpretations:    ~3 calls × ~1,500 tokens avg = 4,500 tokens
  Governance report:  ~1 call  × ~3,500 tokens avg = 3,500 tokens
  Private reports:    ~12 calls × ~2,000 tokens avg = 24,000 tokens
  Window total:       ~32,000 tokens

Per simulation block:
  Simulation report:  ~1 call  × ~4,000 tokens avg = 4,000 tokens

Daily total (2 windows, 2-3 sim blocks):
  ~76,000 tokens/day

Scaling to 12 teams (~24 players):
  Private reports double: ~124,000 tokens/day
```

<!-- TODO: Update with actual Opus 4.6 pricing once confirmed -->
<!-- TODO: Estimate cost per player per day at scale -->

### Cost Optimization Strategies

Instrumented so we can measure impact:

**1. Prompt caching**
- Cache the system prompt and rule space definition across calls (they change infrequently).
- Measure: cache hit rate, tokens saved per cache hit.
- Tag cached vs. uncached calls to compare latency and cost.

**2. Report batching**
- Instead of one AI call per private report, batch multiple players' contexts into a single call with structured output.
- Measure: tokens-per-player before vs. after batching, quality comparison.

**3. Report staleness tolerance**
- Not every report needs to update every window. If a player took no actions since last report, skip regeneration and show previous report with a "no new activity" note.
- Measure: reports_skipped / reports_eligible, player satisfaction impact (via dwell time).

**4. Tiered report depth**
- Active players (multiple actions per window) get full-depth reports.
- Low-activity players get lighter reports with less historical context.
- Measure: tokens-per-tier, engagement-per-tier.

**5. Interpretation caching**
- If a proposal is semantically similar to a previous one, reuse the interpretation with modification rather than a full re-interpretation.
- Measure: cache hits, player acceptance of cached interpretations.

**6. Context window management**
- As game history grows, summarize older data rather than passing raw events.
- Rolling summary: AI generates a compressed history every N windows, and subsequent calls reference the summary instead of raw events.
- Measure: context tokens over time, report quality before vs. after summarization.

### Cost Alarms

- Daily token spend exceeds 2× budget → investigate which call type spiked
- Single AI call exceeds 10,000 tokens → likely a context window management issue
- Cost-per-player-per-day exceeds threshold → optimize report generation
- Interpretation calls exceed 2× proposal volume → ambiguity clarification loops need prompt tuning

### Token Cost Dashboard

**Status: Not yet implemented.** Extend `/admin/evals` with:

- Daily/weekly token spend by call type (stacked bar chart)
- Cost per player per day (trend line)
- Tokens per report by type (box plot showing distribution)
- Cache hit rates over time
- Cost-per-governance-window trend (should decrease as optimizations land)

## Implementation Priority

**Day 1:** Structured logging middleware (request timing, simulation profiling). This is infrastructure that everything else depends on.

**Day 2:** AI call tracking (token counts, latency, call type tagging). Every Opus 4.6 call gets instrumented from the moment the AI layer exists.

**Day 3:** Player behavior events. As the game loop goes live, start capturing governance actions and report engagement.

**Day 4:** Dashboards. Wire up `/admin/perf` with the data collected on Days 1-3. This is also useful for the demo — showing judges the instrumentation demonstrates engineering rigor.

**Day 5:** Cost analysis. With real play data from stress testing, calculate actual cost-per-player and identify the highest-ROI optimization.

## D) Evals Loop: Measuring Human/AI Interaction Effectiveness

The question: **Does the AI report system actually improve human governance? And how do we know?**

Traditional AI evals measure model performance in isolation — accuracy, fluency, faithfulness. Pinwheel needs to evaluate a **sociotechnical system**: the coupled loop where the AI generates reflections, humans perceive them, behavior changes (or doesn't), and governance outcomes shift. This is a four-link causal chain, and each link can break independently:

```
AI generates a pattern  →  Human perceives the pattern  →  Human changes behavior  →  Governance improves
     (Quality)                  (Communication)               (Impact)                (Outcome)
```

The evals loop is not a one-time measurement. It's a continuous cycle:

```
Generate reports → Evaluate quality → Measure behavioral impact → Feed insights back into prompts → Repeat
```

Three proposals follow, tiered by investment. Pick one.

---

### Proposal S: Lightweight Evals (Hackathon-Appropriate)

**Investment:** ~4 hours of engineering. Runs within existing infrastructure. No new dependencies.

**Philosophy:** Measure what we can measure automatically. Spot-check what we can't. Ship the demo with confidence that the reports are doing *something*, even if we can't prove *what*.

#### S.1 Report Quality Rubric (Manual, Sampled)

After each play session or demo run, a human reviewer scores a sample of 5 reports (2 simulation, 2 governance, 1 private) on a 1-5 scale across four dimensions:

| Dimension | 1 (Failing) | 3 (Adequate) | 5 (Excellent) |
|-----------|-------------|---------------|---------------|
| **Grounded** | References events that didn't happen | References real events but loosely | Every claim maps to specific data |
| **Novel** | Restates what's already visible in the box score | Combines stats in non-obvious ways | Surfaces a pattern no player could see from their position |
| **Concise** | Rambling, >4 paragraphs | Appropriate length, some filler | Every sentence earns its place |
| **Observational** | Prescribes actions ("you should...") | Mostly observational, occasional slip | Purely descriptive, never prescriptive |

Scores are logged in a simple CSV: `round, report_type, grounded, novel, concise, observational, reviewer, notes`. This takes ~10 minutes per review session.

**Threshold:** Average score ≥3.0 across all dimensions means reports are "good enough for demo." Below 3.0 on any dimension triggers a prompt revision.

#### S.2 Automated Behavioral Signals

Implement three automated checks that run after every governance window close:

1. **Behavioral Shift Detector:** For each governor who received a private report, compare their actions in the window *after* the report to their running baseline (average of last 3 windows). Track:
   - Did they vote differently from their usual pattern? (yes/no)
   - Did they propose in a different tier than usual? (yes/no)
   - Did they trade with a new partner? (yes/no)

   Store as `eval.behavioral_shift` events. Compute **Report Impact Rate** = governors_who_shifted / governors_who_received_reports. No causal claim — just correlation.

2. **Report Grounding Check:** Automated structural validation that each report references at least one entity (team name, governor ID, rule parameter) that actually exists in the round's data. A report that hallucinates team names or invents statistics is failing at the most basic level.

3. **Prescriptive Language Detector:** Regex/keyword scan for prescriptive phrases in report output: "should", "needs to", "must", "ought to", "it would be wise to", "the league needs". Flag any report that contains >2 prescriptive phrases. The constraint is "describe, never prescribe" — this is the most automatable quality check.

#### S.3 Eval Cadence

- **During hackathon:** Manual rubric review once per play session (before/after major prompt changes). Automated signals run continuously.
- **Demo day:** Pull the Report Impact Rate and grounding check results for the pitch. "In our play sessions, X% of governors changed their behavior after receiving a private report. Zero reports contained hallucinated data."

#### S.4 What This Doesn't Cover

- No causal inference (can't distinguish report-driven change from organic learning)
- No quality comparison across prompt versions (would need A/B testing)
- No inter-rater reliability (single reviewer)
- No longitudinal tracking (sessions too short during hackathon)

---

### Proposal M: Structured Eval Framework (Post-Hackathon V1)

**Investment:** ~2-3 days of engineering. Adds eval infrastructure, a golden dataset, and A/B capability. Requires a play session with 6+ real governors.

**Philosophy:** Move from "are reports OK?" to "are reports *improving*?" Introduce controlled comparison, structured datasets, and a dashboard. This is what you'd run for the first real season to validate the thesis.

#### M.1 Golden Dataset for Report Quality

Create a curated eval dataset of 20 game states with known-correct report content. Each entry contains:

```python
@dataclass
class ReportEvalCase:
    """A test case for report quality evaluation."""
    case_id: str                    # e.g., "sim-coalition-formation"
    report_type: str                # simulation | governance | private
    input_data: dict                # The round data / governance data / governor data
    expected_patterns: list[str]    # Patterns the report MUST surface
    forbidden_patterns: list[str]   # Patterns the report MUST NOT contain
    minimum_entities: list[str]     # Entity names that must appear
    difficulty: str                 # easy | medium | hard
    notes: str                     # Why this case matters
```

**Example cases:**

| Case | Type | What It Tests |
|------|------|---------------|
| `sim-blowout` | Simulation | A game ending 45-12. Report should note the disparity and connect it to a rule change. |
| `gov-unanimous` | Governance | All teams vote YES on a proposal. Report should note the rare consensus. |
| `gov-coalition` | Governance | Teams 1+4 vote identically on 5 proposals while 2+3 vote opposite. Report should identify both blocs. |
| `priv-inactive` | Private | A governor who took zero actions in 3 windows. Report should note absence without judgment. |
| `priv-self-serving` | Private | A governor whose proposals all benefit their team. Report should surface the pattern. |
| `sim-rule-backfire` | Simulation | A team proposed a rule change, it passed, and their win rate dropped. Report should connect proposal to outcome. |
| `gov-power-concentration` | Governance | One governor passed 4 of the last 5 proposals. Report should surface the concentration. |

Run the golden dataset against every prompt revision. Track scores over time. A prompt change that improves simulation reports but degrades governance reports gets caught.

#### M.2 A/B Report Comparison

For a subset of governors during a play session, run **two** versions of the private report prompt simultaneously (the current prompt and a candidate revision). Don't deliver both — deliver one randomly, but generate and store both.

A human reviewer (or a secondary Claude call acting as a judge) scores both versions blind:

- Which report is more grounded in the data?
- Which surfaces a more novel pattern?
- Which would be more useful to the governor?

Track win rates by prompt version. A new prompt version must achieve ≥60% win rate against the current version across 20 comparisons before it replaces the production prompt.

#### M.3 Behavioral Change Attribution

Extend the Proposal S behavioral shift detector with a **control group**:

- **Treatment group:** Governors who receive their private report before the next governance window opens.
- **Control group:** Governors whose private report is delayed until *after* they've taken their governance actions (they still receive it, just later).

Compare behavioral shift rates between groups. If the treatment group shifts more, the report is causing the change, not just correlating with it. This is a basic randomized experiment embedded in gameplay.

**Implementation:** The report delivery system randomly assigns each governor to treatment or control for each window. Treatment = report delivered immediately. Control = report queued for delivery after the window closes. The governor doesn't know their assignment.

**Ethics note:** All governors receive all reports. Control governors receive theirs slightly later, not never. No one is deprived of the experience — timing is shifted, not content.

#### M.4 Governance Quality Index

Define a composite metric for "governance quality" that the evals loop tracks over time:

```
Governance Quality Index (GQI) = weighted average of:
  - Proposal Diversity (30%): How many unique rule parameters have been proposed on?
    Shannon entropy of the parameter distribution.
  - Participation Breadth (25%): What fraction of governors have successfully passed
    at least 1 proposal? Gini coefficient inverted.
  - Consequence Awareness (25%): After a rule change, do subsequent proposals reference
    its effects? Measured by keyword overlap between report content and next-window proposals.
  - Vote Deliberation (20%): Average time-to-vote. Longer deliberation = more thoughtful
    (up to a point; capped at window duration).
```

Track GQI per governance window. Plot it over time. The hypothesis: GQI trends upward over a season as the report system makes governance dynamics visible. If GQI is flat or declining, the report system isn't working.

#### M.5 Eval Dashboard

Add an `/admin/evals` page (not player-facing) that displays:

- Golden dataset scores by report type and case difficulty (bar chart)
- Report Impact Rate over time (line chart)
- A/B comparison win rates for active prompt experiments
- GQI trend line
- Prescriptive language flag count per window
- Grounding check pass rate

This dashboard is the builder's report — the instrumentation system instrumenting itself.

#### M.6 Eval Cadence

- **Per governance window:** Automated signals (behavioral shift, grounding check, prescriptive scan) run automatically.
- **Per play session:** Golden dataset eval against any prompt changes (automated, ~5 min).
- **Weekly (during active season):** Human review of 10 report samples using the rubric. A/B comparison review. GQI trend analysis.
- **Per prompt revision:** Must pass golden dataset regression (no score decreases >0.5 on any dimension) and A/B win rate ≥60% before deployment.

#### M.7 What This Doesn't Cover

- No long-term longitudinal analysis (requires multiple seasons)
- No cross-league comparison (single instance)
- No player self-reported satisfaction (surveys not implemented)
- No formal statistical power analysis on the A/B experiments

---

### Proposal L: Research-Grade Evaluation (Publication-Ready)

**Investment:** ~2 weeks of engineering + experimental design. Requires IRB-style ethical review if publishing. Designed for a multi-season deployment with 24+ governors across 3+ leagues.

**Philosophy:** Produce evidence that would satisfy a peer reviewer at CHI, FAccT, or CSCW. Pre-registered hypotheses. Controlled experiments with statistical power. Mixed-methods (quantitative + qualitative). This is how you'd answer "does AI-mediated visibility actually improve collective governance?" with rigor.

#### L.1 Pre-Registered Hypotheses

Before the first evaluation season, register these hypotheses publicly (e.g., on OSF):

**H1 (Report Impact):** Governors who receive private reports before governance windows will exhibit higher behavioral diversity (measured by entropy of governance actions) than governors in the delayed-report control condition. Effect size: Cohen's d ≥ 0.3.

**H2 (Governance Quality):** Leagues with the full report system active will achieve higher Governance Quality Index scores than leagues running with reports disabled (simulation-only, no governance or private reports). Measured at season end.

**H3 (Power Distribution):** In report-active leagues, the Gini coefficient of proposal pass rates across governors will be lower (more equal) than in report-disabled leagues. The governance report's visibility of power concentration should self-correct the concentration.

**H4 (Consequence Learning):** The rate of "rule-referencing proposals" (proposals that explicitly address the effects of a previous rule change, as classified by the AI interpreter) will be higher in report-active leagues than report-disabled leagues. Reports accelerate the feedback loop.

**H5 (Self-Awareness):** Governors in the report-active condition will demonstrate higher self-awareness scores on a post-season questionnaire assessing their understanding of their own governance patterns, compared to report-disabled governors.

#### L.2 Multi-League Experimental Design

Run 3 leagues simultaneously with different report conditions:

| League | Simulation Report | Governance Report | Private Report | Control Type |
|--------|:-:|:-:|:-:|---|
| **Full Report** | Yes | Yes | Yes | Treatment |
| **Shared Only** | Yes | Yes | No | Partial treatment |
| **No Report** | No | No | No | Control |

Each league has 8 teams, 3 governors per team (24 governors per league, 72 total). Governors are randomly assigned to leagues. All leagues play the same schedule with the same starting ruleset and the same initial team compositions (seeded identically).

**Why three conditions?** The Shared Only league isolates the effect of *private* reports. If H2 holds for Full Report but not Shared Only, private reports are the key differentiator. If both hold, shared visibility alone drives improvement.

#### L.3 Quantitative Measures

Everything from Proposals S and M, plus:

**Report Quality — Automated Scoring:**

Use a secondary Claude instance (different from the report generator) as an automated evaluator. For each report, the evaluator scores it on the rubric dimensions (grounded, novel, concise, observational) using the game state as ground truth. Validate the automated scorer against human scores on a calibration set of 50 reports — require Pearson r ≥ 0.80 on each dimension before trusting automated scores.

**Behavioral Complexity:**

Beyond simple behavioral shift detection, compute the **Shannon entropy** of each governor's action distribution per window:

```
H(actions) = -Σ p(a) log p(a) for a ∈ {vote_yes, vote_no, propose_t1, propose_t2,
                                         propose_t3, propose_t4, amend, boost,
                                         trade_propose, trade_amend, trade_boost,
                                         no_action}
```

Higher entropy = more diverse governance behavior. Plot entropy over time per condition. Hypothesis: entropy increases faster in report-active conditions.

**Social Network Analysis:**

From token trading data, construct a directed graph of governor interactions per window. Compute:
- **Density:** fraction of possible trading pairs that actually trade (higher = more politically active)
- **Betweenness centrality:** identifies political brokers
- **Modularity:** detects coalition structure (teams that trade internally vs. cross-team)

Track network metrics over time. Hypothesis: report-active leagues develop richer, more cross-team trading networks (lower modularity, higher density) because the governance report makes insularity visible.

**Rule Space Coverage:**

Track which parameters of the RuleSet have been proposed on, enacted, and reversed over the season. Compute the fraction of the total rule space that has been "explored" (at least one proposal touching that parameter). Hypothesis: report-active leagues explore more of the rule space because simulation reports highlight underexplored parameters.

#### L.4 Qualitative Measures

**Post-Window Micro-Surveys (30 seconds):**

After each governance window, present governors with 3 quick questions (Likert 1-5):
1. "I understood the consequences of my votes this window."
2. "I noticed a pattern in the league I hadn't seen before."
3. "I changed my approach based on something I learned."

Responses are anonymous and stored as events. Correlate with report condition. These are subjective self-reports that complement the behavioral data.

**Post-Season Interview Protocol:**

After each season, conduct 20-minute semi-structured interviews with 6 governors per league (18 total, stratified by engagement level). Interview protocol:

1. "Describe a moment where you changed your governance strategy. What prompted the change?"
2. "Did you ever feel like the league had dynamics you couldn't see? What helped you understand them?"
3. (Report-active only) "Can you describe a report reflection that stuck with you? Why?"
4. "If you could change one thing about the governance experience, what would it be?"

Code interviews using thematic analysis. Two independent coders; compute inter-rater reliability (Cohen's kappa ≥ 0.70).

**Governor Self-Models:**

At the start and end of each season, ask each governor to write a 2-3 sentence description of their own governance style. Use semantic similarity (embedding distance) to compare self-descriptions against the AI's private report descriptions of the same governor. Hypothesis: in report-active leagues, self-descriptions converge toward report descriptions over the season (governors internalize the report's perspective). In report-disabled leagues, self-descriptions remain stable or diverge from what an AI observer would say.

#### L.5 Longitudinal Tracking

**Cross-Season Analysis:**

If a governor plays multiple seasons, track their "governance maturity" trajectory:
- First season: exploration, learning the mechanics
- Second season: strategic play, coalition building
- Third season: meta-governance, institutional design

The report system should accelerate this trajectory. Compare time-to-first-Tier-4-proposal for governors in report-active vs. report-disabled conditions across seasons.

**Rule Evolution Archaeology:**

For each season, reconstruct the full rule evolution timeline:
```
Round 1: defaults → Round 3: three_point_value 3→5 → Round 7: reverted to 3 →
Round 12: quarter_possessions 15→20 → ...
```

Classify each rule change as:
- **Exploratory:** No stated connection to previous outcomes
- **Reactive:** Explicitly responding to game results
- **Strategic:** Part of a multi-step governance plan
- **Corrective:** Reverting or modifying a previous change

Hypothesis: report-active leagues have a higher proportion of reactive and corrective changes (the feedback loop is working).

#### L.6 Statistical Analysis Plan

- **Primary analysis:** Two-sample t-tests (or Mann-Whitney U for non-normal distributions) comparing report-active vs. report-disabled leagues on GQI, behavioral entropy, and power distribution (Gini). Bonferroni correction for multiple comparisons.
- **Effect sizes:** Report Cohen's d for all comparisons. The minimum interesting effect size is d = 0.3 (small-to-medium).
- **Power analysis:** With 24 governors per condition and within-subject repeated measures (21 governance windows per season), we have >80% power to detect d = 0.3 effects at α = 0.05.
- **Mixed-effects models:** For longitudinal data, use linear mixed-effects models with governor as random effect and condition as fixed effect. This accounts for individual differences in baseline governance style.
- **Mediation analysis:** Test whether Report Impact Rate mediates the relationship between report condition and GQI. Does the report → behavior change → governance quality causal chain hold?

#### L.7 Eval Infrastructure

- **Eval Runner:** A `pinwheel eval` CLI command that runs the golden dataset, scores reports, computes all metrics, and generates a report. Can run against historical data or live.
- **Eval Database:** Separate SQLite database storing all eval scores, comparisons, and reviewer annotations. Never co-mingled with game state.
- **Eval Dashboard:** Extended `/admin/evals` with: hypothesis tracking (green/yellow/red by H1-H5), cross-league comparison charts, network visualizations, rule evolution timelines, and survey response distributions.
- **Automated Nightly Report:** After each day of play, generate a summary: which hypotheses are trending toward significance, which reports scored lowest, which governors showed the most behavioral shift. Email to the research team.

#### L.8 Eval Cadence

- **Per governance window:** All automated signals run. Micro-survey collected.
- **Daily:** Automated nightly report. Golden dataset regression if prompts changed.
- **Weekly:** Human report quality review (10 reports, 2 reviewers). Network analysis snapshot. GQI trend review.
- **Per season:** Full statistical analysis. Post-season interviews. Self-model comparison. Publish results.
- **Per prompt revision:** Golden dataset regression + A/B test (≥60% win rate, N≥20 comparisons) before deployment.

#### L.9 Ethical Considerations

- **Informed consent:** All governors consent to data collection and behavioral analysis. They know the AI observes their governance actions. This is part of the game's design, not hidden research.
- **Debrief:** Governors in the delayed-report control condition are debriefed after the study. They receive their full report history.
- **No deception:** The report system is transparent. Governors know reports exist and what they do. The only experimental manipulation is *timing* of delivery, not content.
- **Data handling:** All behavioral data is pseudonymized (governor IDs, not real names). Interview transcripts are anonymized. Data is stored securely and not shared beyond the research team without explicit consent.
- **Right to withdraw:** Any governor can opt out of the evaluation (their data is excluded) without leaving the game.

---

### Choosing a Proposal

| | Proposal S (Lightweight) | Proposal M (Structured) | Proposal L (Research-Grade) |
|---|---|---|---|
| **Build time** | ~4 hours | ~2-3 days | ~2 weeks |
| **When to use** | Hackathon demo, early play sessions | First real season, post-hackathon V1 | Multi-season deployment, publication |
| **Answers** | "Are reports basically working?" | "Are reports *improving* governance?" | "Does AI visibility *cause* better governance?" |
| **Confidence level** | Anecdotal + automated signals | Correlational + structured comparison | Causal (RCT) + mixed-methods |
| **Dependencies** | Nothing new | Golden dataset curation, prompt A/B infra | Multiple leagues, 72+ governors, IRB |
| **Best for** | Proving the concept works at all | Proving it works well enough to invest in | Proving it works well enough to publish about |

**Recommendation for the hackathon:** Ship Proposal S now. Design Proposal M's golden dataset and data model now (it's cheap to design, expensive to implement). Reference Proposal L in the pitch: "We've designed a research-grade evaluation framework for multi-season deployment" — judges will notice the rigor even if you haven't run the study.

---

## What This Enables Post-Hackathon

The instrumentation system is designed to answer questions that emerge only after real players touch the game:

- Which report type do players value most? (Report dwell time by type)
- At what token velocity does governance feel "alive"? (Correlate velocity with return rate)
- What's the cost floor for a satisfying player experience? (Cost-per-player vs. engagement)
- Where do players churn? (Funnel analysis on governance participation)
- Does power concentration reduce engagement for the excluded? (Cross-reference governance report observations with player return rates)

The game's thesis is that visibility improves governance. The instrumentation layer applies that same thesis to the game itself: by making the game's own dynamics visible to its builders, we can govern its development with the same rigor we're asking of players. The evals loop is the ultimate expression of this principle: the AI system that helps governors see their own patterns is itself made visible through structured evaluation — and improved through the same observe → reflect → adjust cycle it offers to players.

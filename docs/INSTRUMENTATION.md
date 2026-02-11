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
| `mirror.private.view` | player_id, mirror_id, time_spent_reading | Do people read their mirrors? How long? |
| `mirror.private.dismiss` | player_id, mirror_id, time_before_dismiss | Are mirrors being ignored? |
| `game.result.view` | player_id, game_id, time_spent | Do people watch games? Which ones? |
| `feed.scroll_depth` | player_id, session_id, max_depth | How far into the feed do people go? |
| `session.start` / `session.end` | player_id, duration, pages_visited | Session length and navigation patterns |

### Derived Joy Metrics

Computed from raw events, reviewed daily:

- **Governance Participation Rate:** proposals_submitted / (players × governance_windows). If this drops below 30%, the game is losing engagement.
- **Vote Participation Rate:** votes_cast / (eligible_voters × proposals). Healthy floor: 60%.
- **Mirror Read Rate:** mirror_views / mirror_generated. If players aren't reading mirrors, the AI layer isn't earning its weight.
- **Mirror Dwell Time:** Average seconds spent on private mirror. Longer = more valuable. Target: >30 seconds.
- **Proposal Conversion Rate:** proposals_that_receive_votes / proposals_submitted. If proposals are being ignored, the governance surface has a discovery problem.
- **Token Velocity:** trades_per_day / total_tokens_in_circulation. High velocity = active political economy. Low = stagnation.
- **Return Rate:** players_who_return_next_governance_window / players_active_this_window. The single most important retention signal.
- **Time-to-First-Action:** seconds from session start to first governance action. If this is >60s, the dashboard isn't surfacing the right information.

### Joy Alarms

Automated flags for when the game might not be fun:

- A player hasn't taken a governance action in 2+ windows → potential disengagement
- A team hasn't had a successful proposal in 5+ windows → political exclusion
- Token trading volume drops >50% window-over-window → economy stalling
- Mirror read rate drops below 20% → reflections aren't resonating
- A single player/coalition has passed >60% of proposals in a week → power concentration (this is also gameplay — the governance mirror should surface it)

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
| `ai.mirror.simulation` | <5s | >15s |
| `ai.mirror.governance` | <5s | >15s |
| `ai.mirror.private` | <3s per player | >10s |
| `sim.game.run` | <500ms | >2s |
| `sim.round.run` (all matchups) | <5s | >15s |
| `ws.game_result.broadcast` | <50ms | >200ms |
| `db.write.game_result` | <50ms | >200ms |
| `db.read.player_history` | <100ms | >500ms |

### Performance Profiling

Built into the system from Day 1, not bolted on later:

- **Middleware timer:** FastAPI middleware that logs request path, method, duration, and status code for every request. Structured JSON logs.
- **Simulation profiler:** The simulation engine has an optional `profile=True` flag that records time-per-possession, time-per-decision-node, and total game time. Used to find hot paths.
- **AI call tracker:** Every Opus 4.6 call is logged with: prompt token count, completion token count, latency, cache hit (if applicable), and the calling context (interpretation vs. mirror type).
- **WebSocket health:** Track connection count, message throughput, failed deliveries, and reconnection rate.
- **Database query log:** Log slow queries (>100ms) with the full query and execution plan.

### Performance Dashboard

A `/admin/perf` endpoint (not player-facing) that shows:

- P50, P95, P99 latencies for all tracked operations
- AI call volume and cost over time
- Simulation throughput (games/minute)
- Active WebSocket connections
- Database connection pool utilization
- Error rates by endpoint

## C) Token Cost Instrumentation

The question: **How much does each unit of game value cost in API tokens, and how do we reduce it without reducing value?**

### Token Accounting

Every Opus 4.6 call is tagged with its purpose and tracked:

| Call Type | Frequency | Est. Input Tokens | Est. Output Tokens |
|-----------|-----------|-------------------|-------------------|
| `interpret.proposal` | ~3 per governance window | 500-1000 (proposal + rules context + system prompt) | 200-500 (structured rule + explanation) |
| `mirror.simulation` | 1 per simulation block | 2000-5000 (recent game results + rule history) | 300-800 (observations) |
| `mirror.governance` | 1 per governance window | 1500-3000 (governance event log + recent results) | 300-800 (observations) |
| `mirror.private` | 1 per player per window | 1000-2000 (player history + governance actions + team results) | 200-500 (reflection) |
| `interpret.ambiguity` | occasional | 300-600 (clarification request) | 100-300 (clarification) |

### Cost Model

With 6 teams, ~12 players, 2 governance windows/day, ~10 games/day:

```
Per governance window:
  Interpretations:    ~3 calls × ~1,500 tokens avg = 4,500 tokens
  Governance mirror:  ~1 call  × ~3,500 tokens avg = 3,500 tokens
  Private mirrors:    ~12 calls × ~2,000 tokens avg = 24,000 tokens
  Window total:       ~32,000 tokens

Per simulation block:
  Simulation mirror:  ~1 call  × ~4,000 tokens avg = 4,000 tokens

Daily total (2 windows, 2-3 sim blocks):
  ~76,000 tokens/day

Scaling to 12 teams (~24 players):
  Private mirrors double: ~124,000 tokens/day
```

<!-- TODO: Update with actual Opus 4.6 pricing once confirmed -->
<!-- TODO: Estimate cost per player per day at scale -->

### Cost Optimization Strategies

Instrumented so we can measure impact:

**1. Prompt caching**
- Cache the system prompt and rule space definition across calls (they change infrequently).
- Measure: cache hit rate, tokens saved per cache hit.
- Tag cached vs. uncached calls to compare latency and cost.

**2. Mirror batching**
- Instead of one AI call per private mirror, batch multiple players' contexts into a single call with structured output.
- Measure: tokens-per-player before vs. after batching, quality comparison.

**3. Mirror staleness tolerance**
- Not every mirror needs to update every window. If a player took no actions since last mirror, skip regeneration and show previous mirror with a "no new activity" note.
- Measure: mirrors_skipped / mirrors_eligible, player satisfaction impact (via dwell time).

**4. Tiered mirror depth**
- Active players (multiple actions per window) get full-depth mirrors.
- Low-activity players get lighter mirrors with less historical context.
- Measure: tokens-per-tier, engagement-per-tier.

**5. Interpretation caching**
- If a proposal is semantically similar to a previous one, reuse the interpretation with modification rather than a full re-interpretation.
- Measure: cache hits, player acceptance of cached interpretations.

**6. Context window management**
- As game history grows, summarize older data rather than passing raw events.
- Rolling summary: AI generates a compressed history every N windows, and subsequent calls reference the summary instead of raw events.
- Measure: context tokens over time, mirror quality before vs. after summarization.

### Cost Alarms

- Daily token spend exceeds 2× budget → investigate which call type spiked
- Single AI call exceeds 10,000 tokens → likely a context window management issue
- Cost-per-player-per-day exceeds threshold → optimize mirror generation
- Interpretation calls exceed 2× proposal volume → ambiguity clarification loops need prompt tuning

### Token Cost Dashboard

Extend `/admin/perf` with:

- Daily/weekly token spend by call type (stacked bar chart)
- Cost per player per day (trend line)
- Tokens per mirror by type (box plot showing distribution)
- Cache hit rates over time
- Cost-per-governance-window trend (should decrease as optimizations land)

## Implementation Priority

**Day 1:** Structured logging middleware (request timing, simulation profiling). This is infrastructure that everything else depends on.

**Day 2:** AI call tracking (token counts, latency, call type tagging). Every Opus 4.6 call gets instrumented from the moment the AI layer exists.

**Day 3:** Player behavior events. As the game loop goes live, start capturing governance actions and mirror engagement.

**Day 4:** Dashboards. Wire up `/admin/perf` with the data collected on Days 1-3. This is also useful for the demo — showing judges the instrumentation demonstrates engineering rigor.

**Day 5:** Cost analysis. With real play data from stress testing, calculate actual cost-per-player and identify the highest-ROI optimization.

## What This Enables Post-Hackathon

The instrumentation system is designed to answer questions that emerge only after real players touch the game:

- Which mirror type do players value most? (Mirror dwell time by type)
- At what token velocity does governance feel "alive"? (Correlate velocity with return rate)
- What's the cost floor for a satisfying player experience? (Cost-per-player vs. engagement)
- Where do players churn? (Funnel analysis on governance participation)
- Does power concentration reduce engagement for the excluded? (Cross-reference governance mirror observations with player return rates)

The game's thesis is that visibility improves governance. The instrumentation layer applies that same thesis to the game itself: by making the game's own dynamics visible to its builders, we can govern its development with the same rigor we're asking of players.

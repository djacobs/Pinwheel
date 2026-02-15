# Pinwheel Fates: Interface Contracts

Single canonical source for everything shared across backend, frontend, presenter, and Discord. If it crosses a boundary, it's defined here.

See `docs/product/GLOSSARY.md` for canonical naming. This document uses those terms exclusively.

---

## 1. ID Formats

| Entity | Format | Example | Notes |
|--------|--------|---------|-------|
| Game | `g-{round}-{matchup}` | `g-14-1` | Round number, matchup index within round |
| Team | UUID v4 | `a1b2c3d4-...` | Generated at seed time |
| Agent | UUID v4 | `e5f6g7h8-...` | Generated at seed time |
| Season | UUID v4 | `i9j0k1l2-...` | One per season |
| Proposal | UUID v4 | `m3n4o5p6-...` | Created on submission |
| Governor | Discord snowflake (str) | `"123456789012345678"` | Discord user ID, stored as string |
| Window | UUID v4 | `q7r8s9t0-...` | One per governance window |
| Trade | UUID v4 | `u1v2w3x4-...` | Created on offer |
| Report | UUID v4 | `y5z6a7b8-...` | One per report instance |

**Pydantic validator example:**

```python
from pydantic import field_validator

class GameId(BaseModel):
    game_id: str

    @field_validator("game_id")
    @classmethod
    def validate_game_id(cls, v: str) -> str:
        if not v.startswith("g-"):
            raise ValueError("game_id must start with 'g-'")
        parts = v.split("-")
        if len(parts) != 3:
            raise ValueError("game_id must be 'g-{round}-{matchup}'")
        return v
```

---

## 2. SSE Events

All events are delivered via `GET /api/events/stream`. Clients filter by query parameter.

### Connection

```
GET /api/events/stream?games=true&commentary=true&governance=true&reports=true
GET /api/events/stream?game_id={id}          # single game
GET /api/events/stream?team_id={id}          # team-specific events
```

### Game Events (`?games=true` or `?game_id={id}`)

| Event | Payload | Description |
|-------|---------|-------------|
| `game.possession` | `PossessionEvent` | One possession resolved. Contains updated score, box score delta, play description. |
| `game.move` | `MoveEvent` | An Agent's Move activated during a possession. Includes move name, agent, effect. |
| `game.highlight` | `HighlightEvent` | A dramatic moment (lead change, run, clutch play). Triggers visual treatment. |
| `game.commentary` | `CommentaryEvent` | AI commentary line. Includes `energy` field (low/medium/high/peak) and `tags`. |
| `game.quarter_end` | `QuarterEndEvent` | Quarter completed. Contains quarter scores. |
| `game.elam_start` | `ElamStartEvent` | Elam Ending activated. Contains target score. Triggers scoreboard transformation. |
| `game.boxscore` | `BoxScoreEvent` | Full box score snapshot. Sent periodically and at game end. |
| `game.result` | `GameResultEvent` | Game finished. Final score, winner, link to full box score. |

### Governance Events (`?governance=true`)

| Event | Payload | Description |
|-------|---------|-------------|
| `governance.window_closed` | `GovernanceTallyEvent` | Governance tally complete on an interval round. Results available. Contains proposals resolved, vote tallies. |

### Report Events (`?reports=true`)

| Event | Payload | Description |
|-------|---------|-------------|
| `report.simulation` | `ReportEvent` | New simulation report available for the completed round. |
| `report.governance` | `ReportEvent` | Governance report after window close. |
| `report.private` | `PrivateReportEvent` | Private report updated (filtered per-governor via auth). |
| `report.series` | `ReportEvent` | Playoff series report after a series game. |
| `report.season` | `ReportEvent` | Full-season narrative report (post-championship). |
| `report.league` | `ReportEvent` | State of the League periodic report. |

### Season Events (always delivered)

| Event | Payload | Description |
|-------|---------|-------------|
| `season.round` | `RoundEvent` | Round completed, standings updated. |
| `season.tiebreaker` | `TiebreakerEvent` | Tiebreaker game scheduled. |
| `season.playoffs` | `PlayoffsEvent` | Playoff bracket set. |
| `season.series` | `SeriesEvent` | Series update (game result within a series). |
| `season.champion` | `ChampionEvent` | Championship decided. |
| `season.awards` | `AwardsEvent` | Season awards announced. |
| `season.offseason` | `OffseasonEvent` | Offseason governance window opened. |

### Standings Events (always delivered)

| Event | Payload | Description |
|-------|---------|-------------|
| `standings.update` | `StandingsEvent` | Standings changed (after game result or tiebreaker). |

**Total: 22 SSE event types** across 5 categories.

---

## 3. Governance Event Store Types

Append-only event log. These are the source of truth for all governance state. Token balances, current ruleset, and proposal status are all derived from this log.

| Event Type | Aggregate | Payload |
|-----------|-----------|---------|
| `proposal.submitted` | proposal | raw_text, sanitized_text, ai_interpretation, tier, token_cost, governor_id |
| `proposal.confirmed` | proposal | governor confirmed the AI interpretation |
| `proposal.cancelled` | proposal | governor or system cancelled; reason |
| `proposal.amended` | proposal | amendment_text, new_interpretation, amending_governor_id, token_cost |
| `vote.cast` | proposal | vote (yes/no), weight, boost_used, governor_id |
| ~~`vote.revealed`~~ | ~~proposal~~ | ~~all votes for this proposal (on window close)~~ — **Dead: defined but never written. Votes resolve at tally time via `proposal.passed`/`proposal.failed`.** |
| `proposal.passed` | proposal | final_vote_tally, weighted_yes, weighted_no |
| `proposal.failed` | proposal | final_vote_tally, weighted_yes, weighted_no |
| `rule.enacted` | rule_change | parameter, old_value, new_value, source_proposal_id |
| `rule.rolled_back` | rule_change | parameter, rolled_back_value, reason |
| `token.regenerated` | token | governor_id, token_type, amount, new_balance |
| `token.spent` | token | governor_id, token_type, amount, reason (propose/amend/boost) |
| `trade.offered` | trade | from_governor, to_governor, offered_tokens, requested_tokens |
| `trade.accepted` | trade | trade_id (transfer complete) |
| `trade.rejected` | trade | trade_id (offer rejected) |
| `trade.expired` | trade | trade_id (offer expired, window closed) |
| ~~`window.opened`~~ | ~~governance_window~~ | ~~window_id, round_number, opens_at~~ — **Dead: defined but never written. Governance is interval-based, not window-based (Session 37).** |
| ~~`window.closed`~~ | ~~governance_window~~ | ~~window_id, proposals_resolved, closes_at~~ — **Dead: defined but never written. Tallying happens in `step_round()` on interval rounds.** |

**Total: 16 active event types** across 4 aggregates (proposal, rule_change, token, trade). 2 dead types (`vote.revealed`, `window.opened`, `window.closed`) remain in the `GovernanceEventType` Literal but are never written. Also includes `proposal.pending_review` and `proposal.rejected` (added Session 40 for admin veto flow).

Source: `docs/plans/2026-02-11-database-schema-plan.md`

---

## 4. API Response Envelope

All REST responses use this shape:

```json
{
  "data": { ... },
  "meta": {
    "round": 14,
    "timestamp": "2026-02-10T18:30:00Z",
    "links": {
      "boxscore": "/api/games/g-14-1/boxscore",
      "commentary": "/api/games/g-14-1/commentary"
    }
  },
  "governance_context": [
    {
      "parameter": "three_point_value",
      "value": 4,
      "default": 3,
      "changed_by": "proposal-12",
      "round_enacted": 8
    }
  ]
}
```

- **`data`** — The primary response payload. Shape varies per endpoint.
- **`meta`** — Request metadata: current round, timestamp, HATEOAS links to related resources.
- **`governance_context`** — Non-default rule parameters currently in effect. Included on game and team responses so the frontend can always show which rules shaped the outcome. Omit when empty (all defaults).

---

## 5. API Endpoints

### Real-Time (SSE)

| Method | Path | Description | Auth |
|--------|------|-------------|------|
| GET | `/api/events/stream` | SSE event stream. Filter via query params: `games`, `commentary`, `governance`, `reports`, `game_id`, `team_id`. | None |

### Game Data

| Method | Path | Response Model | Auth |
|--------|------|---------------|------|
| GET | `/api/games/live` | `list[GameSummary]` | None |
| GET | `/api/games/{game_id}` | `GameResult` | None |
| GET | `/api/games/{game_id}/boxscore` | `list[AgentBoxScore]` | None |
| GET | `/api/games/{game_id}/play-by-play` | `list[PossessionLog]` | None |
| GET | `/api/games/{game_id}/commentary` | `list[CommentaryLine]` | None |
| GET | `/api/games/{game_id}/state` | `GameState` | None |

### Round Data

| Method | Path | Response Model | Auth |
|--------|------|---------------|------|
| GET | `/api/rounds/current` | `RoundInfo` | None |
| GET | `/api/rounds/{round_number}` | `RoundResult` | None |
| GET | `/api/rounds/{round_number}/games` | `list[GameSummary]` | None |

### Season & Standings

| Method | Path | Response Model | Auth |
|--------|------|---------------|------|
| GET | `/api/standings` | `list[TeamStanding]` | None |
| GET | `/api/stats/leaders` | `StatLeaders` | None |
| GET | `/api/stats/leaders/{stat}` | `list[AgentStatLine]` | None |
| GET | `/api/stats/teams` | `list[TeamStats]` | None |
| GET | `/api/playoffs/bracket` | `PlayoffBracket` | None |

### Team Data

| Method | Path | Response Model | Auth |
|--------|------|---------------|------|
| GET | `/api/teams` | `list[Team]` | None |
| GET | `/api/teams/{team_id}` | `Team` | None |
| GET | `/api/teams/{team_id}/schedule` | `list[ScheduleEntry]` | None |
| GET | `/api/teams/{team_id}/stats` | `TeamStats` | None |

### Agent Data

| Method | Path | Response Model | Auth |
|--------|------|---------------|------|
| GET | `/api/agents/{agent_id}` | `Agent` | None |
| GET | `/api/agents/{agent_id}/stats` | `AgentSeasonStats` | None |
| GET | `/api/agents/{agent_id}/gamelog` | `list[AgentGameLine]` | None |

### Head-to-Head

| Method | Path | Response Model | Auth |
|--------|------|---------------|------|
| GET | `/api/matchups/{team_a}/{team_b}` | `MatchupHistory` | None |

### Governance (Public)

| Method | Path | Response Model | Auth |
|--------|------|---------------|------|
| GET | `/api/rules/current` | `RuleSet` | None |
| GET | `/api/rules/history` | `list[RuleChange]` | None |
| GET | `/api/governance/proposals` | `list[Proposal]` | None |
| GET | `/api/governance/proposals/{id}` | `ProposalDetail` | None |

### Reports (Public)

| Method | Path | Response Model | Auth |
|--------|------|---------------|------|
| GET | `/api/reports/latest` | `dict[str, Report]` | None |
| GET | `/api/reports/{type}/{round}` | `Report` | None |

### Mirrors (AI Reports API)

| Method | Path | Response Model | Auth |
|--------|------|---------------|------|
| GET | `/api/mirrors/round/{season_id}/{round_number}` | `dict` (list of public mirrors) | None |
| GET | `/api/mirrors/private/{season_id}/{governor_id}` | `dict` (list of private mirrors) | Governor (trust-based for hackathon) |
| GET | `/api/mirrors/latest/{season_id}` | `dict` (latest simulation + governance mirrors) | None |

The mirrors API provides access to AI-generated reports. Public mirrors (simulation, governance) are returned from the round endpoint. Private mirrors are governor-scoped -- in production this would verify the requester is the governor; for the hackathon, the `governor_id` parameter is trusted. Optional `mirror_type` and `round_number` query parameters filter results.

### Charts (SVG Helpers)

| Method | Path | Response Model | Auth |
|--------|------|---------------|------|
| (utility) | N/A | N/A | N/A |

`api/charts.py` provides pure-function SVG geometry helpers for spider charts (hooper attribute radar) and season average computation. Exports `spider_chart_data()`, `compute_grid_rings()`, `polygon_points()`, `axis_lines()`, and `compute_season_averages()`. These are consumed by Jinja2 templates, not as API endpoints.

### Admin Pages

| Method | Path | Response Model | Auth |
|--------|------|---------------|------|
| GET | `/admin/review` | HTML page | Admin (OAuth) or open (dev) |
| GET | `/admin/workbench` | HTML page | Admin (OAuth) or open (dev) |
| POST | `/admin/workbench/test-classifier` | HTML fragment (HTMX) | Admin (OAuth) or open (dev) |
| GET | `/admin/evals` | HTML page | Admin (OAuth) or open (dev) |
| GET | `/admin/roster` | HTML page | Admin (OAuth) or open (dev) |
| GET | `/admin/season` | HTML page | Admin (OAuth) or open (dev) |

- **`/admin/review`** -- Proposal review queue. Shows proposals flagged for admin review (Tier 5+, low confidence), injection-classified proposals, with pending/resolved/passed/failed status. Source: `api/admin_review.py`.
- **`/admin/workbench`** -- Safety tooling workbench. Test the injection classifier interactively, view the 6-layer defense stack, and review classifier configuration. POST endpoint accepts JSON `{"text": "..."}` and returns an HTMX HTML fragment with classification results. Source: `api/admin_workbench.py`.
- **`/admin/evals`** -- Evaluation dashboard. Aggregate stats from 12 eval modules: grounding, prescriptive, behavioral, rubric, golden dataset, A/B comparison, GQI trend, scenario flags, rule evaluation, injection classifications. Supports `?round=N` for per-round drill-down. Source: `api/eval_dashboard.py`.
- **`/admin/roster`** -- Governor roster. All enrolled governors with team, token balances, proposal/vote history across all seasons. Source: `api/admin_roster.py`.
- **`/admin/season`** -- Season dashboard. Current season attributes, runtime configuration (pace, cron, auto-advance, governance interval, evals), past seasons with game/team counts, and new season form. Source: `api/admin_season.py`.

All admin pages are auth-gated in production: requires the requesting user's Discord ID to match `PINWHEEL_ADMIN_DISCORD_ID`. In dev mode without OAuth credentials, pages are open to support local testing.

### System

| Method | Path | Response Model | Auth |
|--------|------|---------------|------|
| GET | `/health` | `HealthStatus` | None |
| POST | `/admin/seasons/{id}/start` | `SeasonInfo` | Admin |

**Total: ~40 endpoints** (35 GET, 2 POST, 1 SSE stream, plus utility functions).

Source: `docs/product/VIEWER.md`

---

## 6. Pydantic Model Index

Registry of shared Pydantic models. Definitions live in code (`src/pinwheel/models/`). This table maps each model to its source file and consumers.

| Model | File | Consumers |
|-------|------|-----------|
| `RuleSet` | `models/rules.py` | simulation, governance, API, AI interpreter |
| `RuleChange` | `models/rules.py` | governance, API, season page |
| `GameEffect` | `models/rules.py` | simulation (hooks), governance |
| `PlayerAttributes` | `models/team.py` | simulation, seeding, API |
| `Move` | `models/team.py` | simulation, API, agent page |
| `Venue` | `models/team.py` | simulation, API, team page |
| `Agent` | `models/team.py` | simulation, API, seeding |
| `Team` | `models/team.py` | simulation, API, seeding, scheduler |
| `GameResult` | `models/game.py` | simulation output, API, presenter, DB |
| `AgentBoxScore` | `models/game.py` | simulation output, API, live game SSE |
| `PossessionLog` | `models/game.py` | simulation output, API, presenter |
| `QuarterScore` | `models/game.py` | simulation output, API |
| `CommentaryEvent` | `models/game.py` | presenter, AI commentary, API |
| `Proposal` | `models/governance.py` | governance, API, Discord bot |
| `Amendment` | `models/governance.py` | governance, API, Discord bot |
| `Vote` | `models/governance.py` | governance, API |
| `GovernanceEvent` | `models/governance.py` | event store, governance, reports |
| `TokenBalance` | `models/tokens.py` | token economy, API, Discord bot |
| `Trade` | `models/tokens.py` | token economy, API, Discord bot |
| `Report` | `models/report.py` | AI report generation, API, Discord delivery |
| `ReportUpdate` | `models/report.py` | SSE, Discord delivery |
| `TeamStanding` | `models/game.py` | standings computation, API |
| `GameState` | `core/state.py` | simulation (internal), late-join API |
| `AgentState` | `core/state.py` | simulation (internal) |
| `PossessionState` | `core/state.py` | simulation (internal) |

---

## 7. Behavioral Tracking Events

Analytics events for gameplay health metrics. Each event fires at the described moment. All events include `timestamp` and `session_id`.

### Governance Events

| Event | Additional Payload | When It Fires |
|-------|-------------------|---------------|
| `governance.proposal.submit` | governor_id, proposal_text, token_spent | Governor submits a proposal |
| `governance.proposal.abandon` | governor_id, draft_text, time_spent_drafting | Governor cancels during AI interpretation |
| `governance.amendment.submit` | governor_id, proposal_id, amendment_text | Governor submits an amendment |
| `governance.vote.cast` | governor_id, proposal_id, vote, boost_used, time_to_vote | Governor casts a vote |
| `governance.vote.skip` | governor_id, proposal_id, window_id | Governor eligible but did not vote before window close |

### Token Events

| Event | Additional Payload | When It Fires |
|-------|-------------------|---------------|
| `token.trade.offer` | from_governor, to_governor, offered_tokens, requested_tokens | Governor offers a trade |
| `token.trade.accept` | trade_id, time_to_accept | Trade accepted |
| `token.trade.reject` | trade_id, time_to_reject | Trade rejected |

### Report Events

| Event | Additional Payload | When It Fires |
|-------|-------------------|---------------|
| `report.private.view` | governor_id, report_id, time_spent_reading | Governor opens their private report |
| `report.private.dismiss` | governor_id, report_id, time_before_dismiss | Governor dismisses without reading (< threshold) |
| `report.shared.view` | governor_id, report_id, report_type | Governor views a shared report |
| `report.shared.dwell_time` | governor_id, report_id, seconds | Time spent on shared report |

### Viewing Events

| Event | Additional Payload | When It Fires |
|-------|-------------------|---------------|
| `game.result.view` | governor_id, game_id, time_spent | Governor views a game result |
| `game.commentary.expand` | governor_id, game_id | Governor expands AI commentary |
| `game.rule_context.interact` | governor_id, game_id | Governor clicks/hovers on rule context panel |
| `game.replay.start` | governor_id, game_id | Governor starts a replay |
| `game.view.completion` | governor_id, game_id, watched_from_start | Governor watched start to finish vs. skipped |

### Session Events

| Event | Additional Payload | When It Fires |
|-------|-------------------|---------------|
| `session.start` | governor_id, platform (web/discord) | Session begins |
| `session.end` | governor_id, duration, pages_visited | Session ends |
| `feed.scroll_depth` | governor_id, session_id, max_depth | Scroll depth in feed (web) |

### Onboarding Events

| Event | Additional Payload | When It Fires |
|-------|-------------------|---------------|
| `governor.onboard.server_join` | discord_user_id | New user joins Discord server |
| `governor.onboard.team_select` | governor_id, team_id | Governor picks a team |
| `governor.onboard.first_action` | governor_id, action_type | First governance action taken |

**Total: ~22 behavioral events** across 6 categories.

Source: `docs/INSTRUMENTATION.md`, `docs/product/PRODUCT_OVERVIEW.md`

---

## 8. Cross-References

- **Instrumentation targets and alarms:** `docs/INSTRUMENTATION.md`
- **Demo mode and environment config:** `docs/DEMO_MODE.md`
- **Page-level data contracts (which page uses which endpoint):** `docs/plans/2026-02-11-page-designs.md`
- **Governance event store schema:** `docs/plans/2026-02-11-database-schema-plan.md`
- **Presenter pacing and SSE delivery:** `docs/plans/2026-02-11-presenter-plan.md`
- **Full simulation model definitions:** `docs/SIMULATION.md`
- **Discord bot commands (governance surface):** `docs/product/PLAYER.md`
- **AI commentary engine:** `docs/product/VIEWER.md`

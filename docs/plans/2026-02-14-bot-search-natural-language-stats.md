# Bot Search — Natural Language Stats Queries via Discord

## Context

VIEWER.md describes a "Bot Search" feature: a Discord stats desk where governors and spectators can ask natural language questions about the league and get conversational answers. The spec defines a two-call Opus pattern — (1) parse the user's question into structured API/DB calls, (2) format the raw data into a conversational Discord response with personality and governance context.

This feature sits at the intersection of the spectator journey (PRODUCT_OVERVIEW.md Phase 6) and the viewer experience. It makes league data accessible without navigating the web dashboard, deepens engagement for both governors and spectators, and creates a natural entry point for the AI-as-judgment-amplifier thesis — the bot can weave governance context into data answers.

No code for this feature exists today. The Discord bot (`src/pinwheel/discord/bot.py`) has 15 slash commands but no freeform query capability. The data layer (`src/pinwheel/db/repository.py`) has the queries needed but they are not exposed through any search-oriented interface.

---

## What Exists Today

### Discord Bot (`src/pinwheel/discord/bot.py`)

- `PinwheelBot` extends `commands.Bot` with 15 slash commands: `/standings`, `/schedule`, `/reports`, `/join`, `/vote`, `/propose`, `/tokens`, `/trade`, `/trade-hooper`, `/strategy`, `/bio`, `/profile`, `/new-season`, `/proposals`, `/roster`.
- The bot subscribes to the `EventBus` for real-time event dispatching to Discord channels.
- Commands use `self.engine` to get database sessions via `get_session()`.
- Embed builders in `src/pinwheel/discord/embeds.py` handle all Discord formatting.
- Autocomplete infrastructure exists for teams, proposals, and hoopers.

### Data Layer (`src/pinwheel/db/repository.py`)

The repository already provides the query methods needed for most bot search categories:

| Query Category | Existing Repository Methods |
|---|---|
| Game results | `get_game_result()`, `get_games_for_round()`, `get_all_game_results_for_season()` |
| Box scores | Box scores eagerly loaded with game results via `selectinload(GameResultRow.box_scores)` |
| Standings | `get_games_for_round()` + `compute_standings()` in `core/scheduler.py` |
| Teams | `get_team()`, `get_teams_for_season()` |
| Hoopers | `get_hooper()`, `get_hoopers_for_team()`, `get_box_scores_for_hooper()` |
| Governance | `get_events_by_type()`, `get_all_proposals()`, `get_governor_activity()` |
| Reports | `get_reports_for_round()`, `get_latest_report()` |
| Schedule | `get_schedule_for_round()`, `get_full_schedule()` |
| Seasons | `get_active_season()`, `get_season()` |

### API Layer (`src/pinwheel/api/games.py`, `src/pinwheel/api/standings.py`)

- `GET /api/games/{game_id}` returns game result data.
- `GET /api/games/{game_id}/boxscore` returns box scores.
- `GET /api/standings` computes standings for a season.
- These are thin wrappers around the repository. The bot search does NOT need to go through the HTTP API; it can call the repository directly (same process, same async loop).

### AI Layer (`src/pinwheel/ai/`)

- `interpreter.py` demonstrates the pattern: build a system prompt with structured context, call Claude, parse the JSON response. Bot search would follow the same pattern.
- `commentary.py` and `report.py` show how to format AI prompts with game/governance context.
- All AI calls use the `anthropic` SDK directly.

### Gaps in Data Layer

Several categories from VIEWER.md's queryable data table do NOT have existing repository methods:

| Category | What's Missing |
|---|---|
| Season stat leaders | No aggregate query for league-wide stat leaders (top scorers, most assists, etc.). Must be computed from box score rows. |
| Head-to-head records | No method to get all games between two specific teams. |
| Agent season averages | `get_box_scores_for_hooper()` exists but no aggregation method for per-game averages. |
| Venue data | Venue info is stored as JSON on `TeamRow.venue` but there is no venue-specific query. |
| Rule history timeline | `get_events_by_type(event_types=["rule.enacted"])` exists but returns raw event rows; no helper to build a narrative timeline. |

---

## What Needs to Be Built

### 1. Slash Command: `/ask`

A new Discord slash command that accepts a natural language question.

```python
@self.tree.command(
    name="ask",
    description="Ask anything about the league — stats, standings, games, rules",
)
@app_commands.describe(
    question="Your question in natural language",
)
async def ask_command(
    interaction: discord.Interaction,
    question: str,
) -> None:
    await self._handle_ask(interaction, question)
```

The handler defers the interaction (since AI calls take time), then runs the two-call pipeline and responds with an embed or plain message.

### 2. Query Parser (AI Call 1)

A new module `src/pinwheel/ai/search.py` that takes a natural language question and returns a structured query plan.

**System prompt context:** The available query categories, parameter names, team names, hooper names. This is a small, focused prompt — no game data, just the schema.

**Input:** The user's natural language question.

**Output:** A structured query plan, e.g.:

```json
{
  "query_type": "stat_leaders",
  "stat": "three_pointers_made",
  "scope": "season",
  "limit": 5
}
```

Or for multi-part queries:

```json
{
  "queries": [
    {"query_type": "team_record", "team_name": "Thorns"},
    {"query_type": "last_game", "team_name": "Thorns"}
  ]
}
```

Query types to support in v1:

| Query Type | Parameters | Maps To |
|---|---|---|
| `standings` | (none) | `compute_standings()` |
| `team_record` | `team_name` | Standings filtered to one team |
| `last_game` | `team_name` or `round_number` | `get_games_for_round()` |
| `box_score` | `game_id` or `team_name` + `round_number` | Game result + box scores |
| `stat_leaders` | `stat` (points, assists, steals, threes, etc.), `limit` | Aggregate from box scores |
| `hooper_stats` | `hooper_name` | `get_box_scores_for_hooper()` |
| `head_to_head` | `team_a`, `team_b` | Filter all games for matchup |
| `schedule` | `team_name` or `round_number` | `get_schedule_for_round()` |
| `rules_current` | (none) | Season's `current_ruleset` |
| `rule_history` | `parameter` (optional) | Governance events filtered to `rule.enacted` |
| `proposals` | `status` (optional) | `get_all_proposals()` |
| `team_roster` | `team_name` | `get_teams_for_season()` + hoopers |
| `hooper_profile` | `hooper_name` | `get_hooper()` + attributes |
| `unknown` | — | Graceful fallback: "I can answer questions about..." |

### 3. Query Executor

A function in `src/pinwheel/ai/search.py` that takes the parsed query plan, runs the appropriate repository calls, and returns raw data as a dict.

This is pure data fetching — no AI involved. It maps each `query_type` to the correct repository method(s), handles name resolution (team name to team ID, hooper name to hooper ID), and returns structured results.

Name resolution is critical: users will type "Thorns" not a UUID. The executor needs a name-to-ID lookup. The existing `_team_names_cache` on `PinwheelBot` and the autocomplete helpers provide precedent. Build a small `NameResolver` that loads teams and hoopers for the active season and does fuzzy matching.

### 4. Response Formatter (AI Call 2)

A second AI call that takes the raw data and formats it as a conversational Discord message.

**System prompt:** Personality instructions (conversational sports stats desk), formatting rules (Discord markdown, no spoilers on predictions), governance context instructions (weave in relevant rule changes when they affect the data).

**Input:** The raw query results + the original question + governance context (current ruleset, recent rule changes).

**Output:** A formatted string ready to post to Discord.

This call can reference governance context to make answers richer — e.g., "Rivera leads the league in steals (42). Interestingly, steals jumped 15% after governance banned press defense in Q1-Q3 (Proposal #15)."

### 5. Rate Limiting and Guards

Per VIEWER.md:
- One query at a time per user, with a cooldown (e.g., 10 seconds).
- Public data only — never reveal private reports, team strategies, or hidden votes.
- No predictions — the bot reports data, not forecasts.

Implement as a simple dict of `{discord_user_id: last_query_timestamp}` on the bot instance. The system prompt for both AI calls must include explicit guardrails against revealing private data or making predictions.

### 6. Repository Extensions

New methods needed on `Repository`:

```python
async def get_stat_leaders(
    self,
    season_id: str,
    stat: str,  # "points", "assists", "steals", "three_pointers_made", etc.
    limit: int = 10,
) -> list[dict]:
    """Aggregate box score stats across the season, return top N hoopers."""

async def get_head_to_head(
    self,
    season_id: str,
    team_a_id: str,
    team_b_id: str,
) -> list[GameResultRow]:
    """Get all games between two teams in a season."""

async def get_games_for_team(
    self,
    season_id: str,
    team_id: str,
) -> list[GameResultRow]:
    """Get all games involving a specific team."""
```

The `get_stat_leaders` method requires aggregating `BoxScoreRow` values across the season. Since SQLite supports basic aggregation, this can be done in SQL:

```python
stmt = (
    select(
        BoxScoreRow.hooper_id,
        func.sum(getattr(BoxScoreRow, stat)).label("total"),
    )
    .join(GameResultRow, BoxScoreRow.game_id == GameResultRow.id)
    .where(GameResultRow.season_id == season_id)
    .group_by(BoxScoreRow.hooper_id)
    .order_by(func.sum(getattr(BoxScoreRow, stat)).desc())
    .limit(limit)
)
```

---

## Files to Create/Modify

### New Files

| File | Purpose |
|---|---|
| `src/pinwheel/ai/search.py` | Query parser (AI call 1), query executor, response formatter (AI call 2), name resolver |
| `tests/test_bot_search.py` | Unit tests for query parsing, execution, formatting, rate limiting |

### Modified Files

| File | Changes |
|---|---|
| `src/pinwheel/discord/bot.py` | Add `/ask` command, `_handle_ask()` handler, rate limit tracking |
| `src/pinwheel/db/repository.py` | Add `get_stat_leaders()`, `get_head_to_head()`, `get_games_for_team()` |
| `src/pinwheel/discord/embeds.py` | Add `build_search_result_embed()` for formatted responses |

---

## Implementation Sequence

1. **Repository extensions.** Add the three new query methods with tests. These are pure data operations, testable without AI.
2. **Name resolver.** Build the team/hooper name-to-ID lookup. Test with known seed data.
3. **Query parser.** Implement the AI-powered query parser with mock fallback. Test with golden examples: "who leads the league in scoring?" should parse to `{query_type: "stat_leaders", stat: "points"}`.
4. **Query executor.** Wire parser output to repository calls. Test each query type against seeded data.
5. **Response formatter.** Implement the AI-powered formatter with mock fallback. Test that output is Discord-appropriate (length limits, markdown).
6. **Slash command.** Wire everything into the `/ask` command handler. Add rate limiting. Test the full pipeline end-to-end.
7. **Guards.** Add private data and prediction guardrails to both AI system prompts. Test with adversarial queries ("show me the Thorns' strategy", "who will win the championship?").

---

## Testing Strategy

### Unit Tests

- **Query parser (mocked AI):** Provide 10-15 golden question-to-query-plan mappings. Verify the parser produces the correct query type and parameters for each. Mock the Anthropic API call; test only the prompt construction and response parsing.
- **Query executor:** Seed a database with known data (via `demo_seed.py` fixtures). Run each query type and verify the returned data matches expected values. Test edge cases: unknown team name, no games played yet, empty season.
- **Name resolver:** Test exact match, partial match, case-insensitive match. Verify that "thorns", "Thorns", "Rose City Thorns" all resolve to the same team ID.
- **Rate limiting:** Test that a second query within the cooldown period is rejected. Test that queries from different users are independent.
- **Response formatter (mocked AI):** Verify the formatter receives the correct context (raw data + governance context). Test the mock fallback produces valid Discord messages.

### Integration Tests

- **Full pipeline (mocked AI):** Send a question through the full `/ask` handler with mocked AI calls. Verify the Discord response is well-formed and contains the expected data.
- **Repository extension queries:** Test `get_stat_leaders()`, `get_head_to_head()`, `get_games_for_team()` against a seeded database. Verify correct aggregation, ordering, and filtering.

### Guard Tests

- **Private data rejection:** Ask "show me private reports" and verify the response does not include private report content.
- **Prediction rejection:** Ask "who will win the championship?" and verify the response declines to predict.
- **Injection resistance:** Send prompt injection attempts as questions and verify the system prompt guardrails prevent them from affecting behavior.

### Cost Considerations

- Each `/ask` invocation makes 2 AI calls (parse + format). Use a fast, cheap model for parsing (Haiku) and a moderate model for formatting (Sonnet).
- Cache parsed query plans for identical questions within a session to avoid redundant parse calls.
- Rate limiting prevents cost abuse from individual users.
- In tests, always mock AI calls. Real AI calls only in clearly-marked integration tests.

---

## Design Decisions

1. **Slash command (`/ask`) rather than message listener.** A slash command is explicit, discoverable, and doesn't require the bot to process every message in every channel. It also sidesteps the complexity of determining which messages are questions vs. conversation.
2. **Direct repository access, not HTTP API.** The bot runs in-process with FastAPI. Going through HTTP adds latency and complexity for no benefit. The repository is the single source of truth.
3. **Two AI calls, not one.** Separating parsing from formatting allows caching at the parse layer, model-tier optimization (cheap parser, better formatter), and independent testing of each step.
4. **Mock fallback when `ANTHROPIC_API_KEY` is unset.** The mock parser uses keyword matching (e.g., "standings" -> `standings` query type). The mock formatter returns structured but unstyled data. This allows the full feature to work in dev/test without API costs.
5. **Name resolver preloaded at season start.** Team and hooper names are loaded once when the season starts and cached on the bot instance. This avoids per-query DB lookups for name resolution.

---

## Open Questions

1. **Model tier for parsing.** Haiku is fast and cheap but may struggle with ambiguous queries. Sonnet is more reliable but 5x the cost. Start with Haiku and upgrade if parse accuracy is below 80% on golden examples.
2. **Embed vs. plain text response.** Embeds look better but are harder to format dynamically. Start with plain text (Discord markdown) for v1; add embeds for common query types (box scores, standings) in v2.
3. **Channel restrictions.** Should `/ask` work in all channels or only designated ones? Starting with all channels is simpler. If it becomes noisy, restrict to `#stats` or similar.
4. **Query history.** Should the bot remember previous queries in a conversation? v1 treats each query independently. Conversation memory could be added later by passing prior Q&A pairs to the formatter's context.

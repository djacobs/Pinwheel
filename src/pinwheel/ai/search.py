"""Natural language search — parse questions, execute queries, format responses.

Two-call AI pattern:
1. Parse the user's natural language question into a structured query plan.
2. Execute the query plan against the repository, then format results
   into a conversational Discord message.

Mock fallback works without ANTHROPIC_API_KEY set: keyword-based parsing
and structured-but-plain formatting.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

import anthropic

from pinwheel.db.models import HooperRow, TeamRow

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Query Plan model
# ---------------------------------------------------------------------------

VALID_QUERY_TYPES = frozenset(
    {
        "standings",
        "team_record",
        "last_game",
        "stat_leaders",
        "hooper_stats",
        "head_to_head",
        "schedule",
        "rules_current",
        "team_roster",
        "proposals",
        "unknown",
    }
)

# Box score stat columns available for aggregation.
VALID_STATS = frozenset(
    {
        "points",
        "assists",
        "steals",
        "turnovers",
        "three_pointers_made",
        "three_pointers_attempted",
        "field_goals_made",
        "field_goals_attempted",
        "free_throws_made",
        "free_throws_attempted",
        "minutes",
    }
)

# Friendly aliases for stat names users might type.
STAT_ALIASES: dict[str, str] = {
    "scoring": "points",
    "pts": "points",
    "score": "points",
    "scorer": "points",
    "scorers": "points",
    "ast": "assists",
    "assist": "assists",
    "stl": "steals",
    "steal": "steals",
    "tov": "turnovers",
    "turnover": "turnovers",
    "threes": "three_pointers_made",
    "three": "three_pointers_made",
    "3pt": "three_pointers_made",
    "3s": "three_pointers_made",
    "three pointers": "three_pointers_made",
    "three-pointers": "three_pointers_made",
    "fg": "field_goals_made",
    "field goals": "field_goals_made",
    "ft": "free_throws_made",
    "free throws": "free_throws_made",
    "min": "minutes",
}


@dataclass
class QueryPlan:
    """Structured representation of a parsed natural language query."""

    query_type: str = "unknown"
    stat: str | None = None
    team_name: str | None = None
    team_a_name: str | None = None
    team_b_name: str | None = None
    hooper_name: str | None = None
    limit: int = 5

    def __post_init__(self) -> None:
        if self.query_type not in VALID_QUERY_TYPES:
            self.query_type = "unknown"


# ---------------------------------------------------------------------------
# Name Resolver — maps display names to DB IDs
# ---------------------------------------------------------------------------


class NameResolver:
    """Case-insensitive resolver for team and hooper names within a season."""

    def __init__(
        self,
        teams: list[TeamRow],
        hoopers: list[HooperRow] | None = None,
    ) -> None:
        self._teams: dict[str, TeamRow] = {}
        self._hoopers: dict[str, HooperRow] = {}
        self._team_id_to_name: dict[str, str] = {}

        for t in teams:
            self._teams[t.name.lower()] = t
            self._team_id_to_name[t.id] = t.name

        for h in hoopers or []:
            self._hoopers[h.name.lower()] = h

    def resolve_team(self, name: str) -> TeamRow | None:
        """Resolve a team name (exact, case-insensitive, then partial match)."""
        lowered = name.lower().strip()
        # Exact match
        if lowered in self._teams:
            return self._teams[lowered]
        # Partial/substring match
        for key, team in self._teams.items():
            if lowered in key or key in lowered:
                return team
        return None

    def resolve_hooper(self, name: str) -> HooperRow | None:
        """Resolve a hooper name (exact, case-insensitive, then partial match)."""
        lowered = name.lower().strip()
        if lowered in self._hoopers:
            return self._hoopers[lowered]
        for key, hooper in self._hoopers.items():
            if lowered in key or key in lowered:
                return hooper
        return None

    def team_name(self, team_id: str) -> str:
        """Look up a team display name by ID."""
        return self._team_id_to_name.get(team_id, team_id)


# ---------------------------------------------------------------------------
# Query Parser — mock (keyword-based)
# ---------------------------------------------------------------------------


def parse_query_mock(question: str) -> QueryPlan:
    """Parse a natural language question into a QueryPlan using keyword matching.

    No AI call required. Handles the most common question patterns.
    """
    q = question.lower().strip()

    # Head to head: "X vs Y", "X against Y", "head to head"
    vs_match = re.search(r"(.+?)\s+(?:vs\.?|versus|against)\s+(.+)", q)
    if vs_match:
        return QueryPlan(
            query_type="head_to_head",
            team_a_name=vs_match.group(1).strip(),
            team_b_name=vs_match.group(2).strip(),
        )
    if "head to head" in q:
        return QueryPlan(query_type="head_to_head")

    # Stat leaders: "who leads", "top", "best", "most", "leader"
    if any(kw in q for kw in ("who leads", "top", "best", "most", "leader")):
        stat = _extract_stat(q)
        limit = _extract_limit(q)
        return QueryPlan(query_type="stat_leaders", stat=stat, limit=limit)

    # Standings
    if any(kw in q for kw in ("standings", "ranking", "rankings", "leaderboard")):
        return QueryPlan(query_type="standings")

    # Team record: "record" combined with a team context word
    if "record" in q:
        team = _extract_team_name_from_question(q)
        return QueryPlan(query_type="team_record", team_name=team)

    # Last game
    if any(kw in q for kw in ("last game", "latest game", "recent game", "last result")):
        team = _extract_team_name_from_question(q)
        return QueryPlan(query_type="last_game", team_name=team)

    # Schedule
    if any(kw in q for kw in ("schedule", "next game", "upcoming")):
        team = _extract_team_name_from_question(q)
        return QueryPlan(query_type="schedule", team_name=team)

    # Rules
    if any(kw in q for kw in ("rules", "ruleset", "rule set", "current rules")):
        return QueryPlan(query_type="rules_current")

    # Roster
    if any(kw in q for kw in ("roster", "hoopers", "players", "lineup")):
        team = _extract_team_name_from_question(q)
        return QueryPlan(query_type="team_roster", team_name=team)

    # Proposals
    if any(kw in q for kw in ("proposal", "proposals", "governance")):
        return QueryPlan(query_type="proposals")

    # Hooper stats: "stats for X", "how is X doing"
    if any(kw in q for kw in ("stats for", "stats on", "how is", "how's")):
        hooper = _extract_hooper_name_from_question(q)
        return QueryPlan(query_type="hooper_stats", hooper_name=hooper)

    return QueryPlan(query_type="unknown")


def _extract_stat(question: str) -> str:
    """Extract a stat name from the question, defaulting to 'points'."""
    for alias, canonical in STAT_ALIASES.items():
        if alias in question:
            return canonical
    return "points"


def _extract_limit(question: str) -> int:
    """Extract a numeric limit from the question, defaulting to 5."""
    match = re.search(r"\btop\s+(\d+)\b", question)
    if match:
        return min(int(match.group(1)), 25)
    return 5


def _extract_team_name_from_question(question: str) -> str | None:
    """Try to extract a team name from a question.

    Very rough heuristic: returns words after certain prepositions/verbs.
    The NameResolver does the real matching; this just narrows the search.
    """
    for pattern in (
        r"(?:for|of|about|the)\s+(?:the\s+)?(.+?)(?:\?|$|'s)",
        r"(?:for|of|about|the)\s+(.+?)(?:\?|$)",
    ):
        match = re.search(pattern, question)
        if match:
            candidate = match.group(1).strip().rstrip("?. ")
            # Skip if the candidate is just a stat word
            if candidate and candidate not in STAT_ALIASES and len(candidate) > 2:
                return candidate
    return None


def _extract_hooper_name_from_question(question: str) -> str | None:
    """Try to extract a hooper name from a question."""
    for pattern in (
        r"stats (?:for|on)\s+(.+?)(?:\?|$)",
        r"how (?:is|'s)\s+(.+?)\s+doing",
        r"how (?:is|'s)\s+(.+?)(?:\?|$)",
    ):
        match = re.search(pattern, question)
        if match:
            return match.group(1).strip().rstrip("?. ")
    return None


# ---------------------------------------------------------------------------
# Query Parser — AI-powered
# ---------------------------------------------------------------------------

SEARCH_PARSER_SYSTEM_PROMPT = """\
You are a sports stats assistant for Pinwheel Fates, a 3v3 basketball governance game.

Your job: parse the user's natural language question into a structured query plan.

## Available Query Types

- standings: League standings
- team_record: Win-loss record for a specific team
- last_game: Most recent game result (optionally for a team)
- stat_leaders: Top hoopers by a stat (points, assists, steals, etc.)
- hooper_stats: Stats for a specific hooper
- head_to_head: Games between two teams
- schedule: Upcoming schedule
- rules_current: Current game rules
- team_roster: Hoopers on a team
- proposals: Governance proposals
- unknown: Can't determine what they're asking

## Available Stats
points, assists, steals, turnovers, three_pointers_made, three_pointers_attempted,
field_goals_made, field_goals_attempted, free_throws_made, free_throws_attempted, minutes

## Teams in This Season
{team_names}

## Hoopers in This Season
{hooper_names}

## Response Format
Respond with ONLY a JSON object:
{{
  "query_type": "<type>",
  "stat": "<stat_name or null>",
  "team_name": "<team name or null>",
  "team_a_name": "<team name or null (for head_to_head)>",
  "team_b_name": "<team name or null (for head_to_head)>",
  "hooper_name": "<hooper name or null>",
  "limit": <int, default 5>
}}

## Rules
1. NEVER reveal private data (strategies, private reports, hidden votes).
2. NEVER make predictions about future games.
3. Map the question to the most specific query type possible.
4. If ambiguous, default to "unknown".
"""


async def parse_query_ai(
    question: str,
    api_key: str,
    team_names: list[str],
    hooper_names: list[str],
) -> QueryPlan:
    """Parse a question using Claude API. Falls back to mock on failure."""
    system = SEARCH_PARSER_SYSTEM_PROMPT.format(
        team_names=", ".join(team_names) if team_names else "(none yet)",
        hooper_names=", ".join(hooper_names) if hooper_names else "(none yet)",
    )

    model = "claude-sonnet-4-5-20250929"
    try:
        client = anthropic.AsyncAnthropic(api_key=api_key)
        response = await client.messages.create(
            model=model,
            max_tokens=300,
            system=system,
            messages=[{"role": "user", "content": question}],
        )

        text = response.content[0].text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        data = json.loads(text)
        return QueryPlan(**{k: v for k, v in data.items() if v is not None})

    except (json.JSONDecodeError, anthropic.APIError, KeyError, IndexError) as e:
        logger.warning("AI query parse failed, using mock: %s", e)
        return parse_query_mock(question)


# ---------------------------------------------------------------------------
# Query Executor — runs repository calls based on QueryPlan
# ---------------------------------------------------------------------------


@dataclass
class QueryResult:
    """Raw result from executing a query plan."""

    query_type: str
    data: dict[str, object] = field(default_factory=dict)
    error: str | None = None


async def execute_query(
    plan: QueryPlan,
    repo: object,
    season_id: str,
    resolver: NameResolver,
) -> QueryResult:
    """Execute a QueryPlan against the repository and return raw data.

    Args:
        plan: The parsed query plan.
        repo: A Repository instance (typed as object to avoid circular import).
        season_id: Active season ID.
        resolver: A NameResolver loaded with teams/hoopers for this season.
    """
    from pinwheel.db.repository import Repository

    r: Repository = repo  # type: ignore[assignment]

    try:
        if plan.query_type == "standings":
            return await _exec_standings(r, season_id, resolver)

        elif plan.query_type == "team_record":
            return await _exec_team_record(r, season_id, resolver, plan.team_name)

        elif plan.query_type == "last_game":
            return await _exec_last_game(r, season_id, resolver, plan.team_name)

        elif plan.query_type == "stat_leaders":
            stat = plan.stat or "points"
            if stat not in VALID_STATS:
                stat = STAT_ALIASES.get(stat, "points")
            return await _exec_stat_leaders(r, season_id, resolver, stat, plan.limit)

        elif plan.query_type == "hooper_stats":
            return await _exec_hooper_stats(r, season_id, resolver, plan.hooper_name)

        elif plan.query_type == "head_to_head":
            return await _exec_head_to_head(
                r, season_id, resolver, plan.team_a_name, plan.team_b_name
            )

        elif plan.query_type == "schedule":
            return await _exec_schedule(r, season_id, resolver, plan.team_name)

        elif plan.query_type == "rules_current":
            return await _exec_rules_current(r, season_id)

        elif plan.query_type == "team_roster":
            return await _exec_team_roster(r, season_id, resolver, plan.team_name)

        elif plan.query_type == "proposals":
            return await _exec_proposals(r, season_id)

        else:
            return QueryResult(
                query_type="unknown",
                data={
                    "message": (
                        "I can answer questions about standings, stats, "
                        "schedules, rules, rosters, and game results. "
                        "Try asking something like 'who leads the league in scoring?' "
                        "or 'what are the current standings?'"
                    )
                },
            )

    except Exception as e:
        logger.exception("query_execution_failed type=%s", plan.query_type)
        return QueryResult(
            query_type=plan.query_type,
            error=f"Query failed: {e}",
        )


async def _exec_standings(
    repo: object,
    season_id: str,
    resolver: NameResolver,
) -> QueryResult:
    """Execute a standings query."""
    from pinwheel.core.scheduler import compute_standings
    from pinwheel.db.repository import Repository

    r: Repository = repo  # type: ignore[assignment]
    all_games = await r.get_all_game_results_for_season(season_id)
    results = [
        {
            "home_team_id": g.home_team_id,
            "away_team_id": g.away_team_id,
            "home_score": g.home_score,
            "away_score": g.away_score,
            "winner_team_id": g.winner_team_id,
        }
        for g in all_games
    ]
    standings = compute_standings(results)
    for s in standings:
        s["team_name"] = resolver.team_name(s["team_id"])
    return QueryResult(query_type="standings", data={"standings": standings})


async def _exec_team_record(
    repo: object,
    season_id: str,
    resolver: NameResolver,
    team_name: str | None,
) -> QueryResult:
    """Execute a team record query."""
    from pinwheel.core.scheduler import compute_standings
    from pinwheel.db.repository import Repository

    r: Repository = repo  # type: ignore[assignment]

    if not team_name:
        return QueryResult(
            query_type="team_record",
            error="Which team? Try asking like 'what is the Thorns record?'",
        )

    team = resolver.resolve_team(team_name)
    if not team:
        return QueryResult(
            query_type="team_record",
            error=f"Could not find a team matching '{team_name}'.",
        )

    all_games = await r.get_all_game_results_for_season(season_id)
    results = [
        {
            "home_team_id": g.home_team_id,
            "away_team_id": g.away_team_id,
            "home_score": g.home_score,
            "away_score": g.away_score,
            "winner_team_id": g.winner_team_id,
        }
        for g in all_games
    ]
    standings = compute_standings(results)
    team_standing = None
    for s in standings:
        if s["team_id"] == team.id:
            s["team_name"] = team.name
            team_standing = s
            break

    if not team_standing:
        return QueryResult(
            query_type="team_record",
            data={"team_name": team.name, "wins": 0, "losses": 0, "games_played": 0},
        )

    return QueryResult(query_type="team_record", data=team_standing)


async def _exec_last_game(
    repo: object,
    season_id: str,
    resolver: NameResolver,
    team_name: str | None,
) -> QueryResult:
    """Execute a last game query."""
    from pinwheel.db.repository import Repository

    r: Repository = repo  # type: ignore[assignment]

    if team_name:
        team = resolver.resolve_team(team_name)
        if not team:
            return QueryResult(
                query_type="last_game",
                error=f"Could not find a team matching '{team_name}'.",
            )
        games = await r.get_games_for_team(season_id, team.id)
    else:
        games = await r.get_all_game_results_for_season(season_id)

    if not games:
        return QueryResult(
            query_type="last_game",
            data={"message": "No games have been played yet."},
        )

    last = games[-1]
    return QueryResult(
        query_type="last_game",
        data={
            "home_team": resolver.team_name(last.home_team_id),
            "away_team": resolver.team_name(last.away_team_id),
            "home_score": last.home_score,
            "away_score": last.away_score,
            "winner": resolver.team_name(last.winner_team_id),
            "round": last.round_number,
        },
    )


async def _exec_stat_leaders(
    repo: object,
    season_id: str,
    resolver: NameResolver,
    stat: str,
    limit: int,
) -> QueryResult:
    """Execute a stat leaders query."""
    from pinwheel.db.repository import Repository

    r: Repository = repo  # type: ignore[assignment]
    leaders = await r.get_stat_leaders(season_id, stat, limit=limit)

    # Resolve hooper names
    for entry in leaders:
        hooper = await r.get_hooper(entry["hooper_id"])
        if hooper:
            entry["hooper_name"] = hooper.name
            entry["team_name"] = resolver.team_name(hooper.team_id)
        else:
            entry["hooper_name"] = entry["hooper_id"]
            entry["team_name"] = "Unknown"

    return QueryResult(
        query_type="stat_leaders",
        data={"stat": stat, "leaders": leaders},
    )


async def _exec_hooper_stats(
    repo: object,
    season_id: str,
    resolver: NameResolver,
    hooper_name: str | None,
) -> QueryResult:
    """Execute a hooper stats query."""
    from pinwheel.db.repository import Repository

    r: Repository = repo  # type: ignore[assignment]

    if not hooper_name:
        return QueryResult(
            query_type="hooper_stats",
            error="Which hooper? Try asking like 'stats for Rivera'.",
        )

    hooper = resolver.resolve_hooper(hooper_name)
    if not hooper:
        return QueryResult(
            query_type="hooper_stats",
            error=f"Could not find a hooper matching '{hooper_name}'.",
        )

    box_scores = await r.get_box_scores_for_hooper(hooper.id)
    if not box_scores:
        return QueryResult(
            query_type="hooper_stats",
            data={
                "hooper_name": hooper.name,
                "team_name": resolver.team_name(hooper.team_id),
                "games_played": 0,
            },
        )

    # Aggregate
    totals: dict[str, float] = {
        "points": 0,
        "assists": 0,
        "steals": 0,
        "turnovers": 0,
        "three_pointers_made": 0,
        "field_goals_made": 0,
        "field_goals_attempted": 0,
        "free_throws_made": 0,
        "free_throws_attempted": 0,
        "minutes": 0.0,
    }
    games_played = len(box_scores)
    for bs, _game in box_scores:
        for stat_key in totals:
            totals[stat_key] += getattr(bs, stat_key, 0)

    averages = {k: round(v / games_played, 1) if games_played else 0 for k, v in totals.items()}

    return QueryResult(
        query_type="hooper_stats",
        data={
            "hooper_name": hooper.name,
            "team_name": resolver.team_name(hooper.team_id),
            "games_played": games_played,
            "totals": totals,
            "averages": averages,
        },
    )


async def _exec_head_to_head(
    repo: object,
    season_id: str,
    resolver: NameResolver,
    team_a_name: str | None,
    team_b_name: str | None,
) -> QueryResult:
    """Execute a head-to-head query."""
    from pinwheel.db.repository import Repository

    r: Repository = repo  # type: ignore[assignment]

    if not team_a_name or not team_b_name:
        return QueryResult(
            query_type="head_to_head",
            error="Please specify two teams, like 'Thorns vs Voltage'.",
        )

    team_a = resolver.resolve_team(team_a_name)
    team_b = resolver.resolve_team(team_b_name)
    if not team_a:
        return QueryResult(
            query_type="head_to_head",
            error=f"Could not find a team matching '{team_a_name}'.",
        )
    if not team_b:
        return QueryResult(
            query_type="head_to_head",
            error=f"Could not find a team matching '{team_b_name}'.",
        )

    games = await r.get_head_to_head(season_id, team_a.id, team_b.id)
    matchups = []
    a_wins = 0
    b_wins = 0
    for g in games:
        matchups.append(
            {
                "round": g.round_number,
                "home_team": resolver.team_name(g.home_team_id),
                "away_team": resolver.team_name(g.away_team_id),
                "home_score": g.home_score,
                "away_score": g.away_score,
                "winner": resolver.team_name(g.winner_team_id),
            }
        )
        if g.winner_team_id == team_a.id:
            a_wins += 1
        else:
            b_wins += 1

    return QueryResult(
        query_type="head_to_head",
        data={
            "team_a": team_a.name,
            "team_b": team_b.name,
            "a_wins": a_wins,
            "b_wins": b_wins,
            "games": matchups,
        },
    )


async def _exec_schedule(
    repo: object,
    season_id: str,
    resolver: NameResolver,
    team_name: str | None,
) -> QueryResult:
    """Execute a schedule query."""
    from pinwheel.db.repository import Repository

    r: Repository = repo  # type: ignore[assignment]

    schedule = await r.get_full_schedule(season_id)
    entries = []
    for s in schedule:
        if team_name:
            team = resolver.resolve_team(team_name)
            if team and s.home_team_id != team.id and s.away_team_id != team.id:
                continue
        entries.append(
            {
                "round": s.round_number,
                "home_team": resolver.team_name(s.home_team_id),
                "away_team": resolver.team_name(s.away_team_id),
                "status": s.status,
            }
        )

    return QueryResult(
        query_type="schedule",
        data={"schedule": entries},
    )


async def _exec_rules_current(
    repo: object,
    season_id: str,
) -> QueryResult:
    """Execute a current rules query."""
    from pinwheel.db.repository import Repository

    r: Repository = repo  # type: ignore[assignment]
    season = await r.get_season(season_id)
    if not season or not season.current_ruleset:
        return QueryResult(
            query_type="rules_current",
            data={"rules": {}},
        )
    return QueryResult(
        query_type="rules_current",
        data={"rules": season.current_ruleset},
    )


async def _exec_team_roster(
    repo: object,
    season_id: str,
    resolver: NameResolver,
    team_name: str | None,
) -> QueryResult:
    """Execute a team roster query."""
    from pinwheel.db.repository import Repository

    r: Repository = repo  # type: ignore[assignment]

    if not team_name:
        # Return all teams
        teams = await r.get_teams_for_season(season_id)
        all_rosters = []
        for t in teams:
            hoopers = [
                {"name": h.name, "archetype": h.archetype}
                for h in t.hoopers
            ]
            all_rosters.append({"team_name": t.name, "hoopers": hoopers})
        return QueryResult(
            query_type="team_roster",
            data={"rosters": all_rosters},
        )

    team = resolver.resolve_team(team_name)
    if not team:
        return QueryResult(
            query_type="team_roster",
            error=f"Could not find a team matching '{team_name}'.",
        )

    hoopers_list = await r.get_hoopers_for_team(team.id)
    hoopers = [
        {"name": h.name, "archetype": h.archetype}
        for h in hoopers_list
    ]
    return QueryResult(
        query_type="team_roster",
        data={"team_name": team.name, "hoopers": hoopers},
    )


async def _exec_proposals(
    repo: object,
    season_id: str,
) -> QueryResult:
    """Execute a proposals query."""
    from pinwheel.db.repository import Repository

    r: Repository = repo  # type: ignore[assignment]
    proposals = await r.get_all_proposals(season_id)
    return QueryResult(
        query_type="proposals",
        data={"proposals": proposals},
    )


# ---------------------------------------------------------------------------
# Response Formatter — mock (structured plain text)
# ---------------------------------------------------------------------------


def format_response_mock(question: str, result: QueryResult) -> str:
    """Format a QueryResult into a readable Discord message string.

    No AI call. Produces structured, plain-language responses.
    """
    if result.error:
        return result.error

    data = result.data

    if result.query_type == "standings":
        standings = data.get("standings", [])
        if not standings:
            return "No games have been played yet."
        lines = ["**League Standings**"]
        for i, s in enumerate(standings, 1):
            name = s.get("team_name", s.get("team_id", "???"))
            w = s.get("wins", 0)
            lo = s.get("losses", 0)
            lines.append(f"{i}. **{name}** ({w}W-{lo}L)")
        return "\n".join(lines)

    elif result.query_type == "team_record":
        name = data.get("team_name", "Unknown")
        w = data.get("wins", 0)
        lo = data.get("losses", 0)
        return f"**{name}** are {w}-{lo} this season."

    elif result.query_type == "last_game":
        if "message" in data:
            return str(data["message"])
        home = data.get("home_team", "Home")
        away = data.get("away_team", "Away")
        hs = data.get("home_score", 0)
        a_s = data.get("away_score", 0)
        winner = data.get("winner", "???")
        rnd = data.get("round", "?")
        return (
            f"**Round {rnd}:** {home} {hs} - {a_s} {away}\n"
            f"Winner: **{winner}**"
        )

    elif result.query_type == "stat_leaders":
        stat = data.get("stat", "points")
        leaders = data.get("leaders", [])
        if not leaders:
            return f"No stats recorded yet for {stat}."
        stat_display = stat.replace("_", " ").title()
        lines = [f"**{stat_display} Leaders**"]
        for i, entry in enumerate(leaders, 1):
            name = entry.get("hooper_name", "???")
            team = entry.get("team_name", "")
            total = entry.get("total", 0)
            lines.append(f"{i}. **{name}** ({team}) -- {total}")
        return "\n".join(lines)

    elif result.query_type == "hooper_stats":
        name = data.get("hooper_name", "???")
        team = data.get("team_name", "")
        gp = data.get("games_played", 0)
        if gp == 0:
            return f"**{name}** ({team}) has not played any games yet."
        avgs = data.get("averages", {})
        lines = [f"**{name}** ({team}) -- {gp} games"]
        if avgs:
            lines.append(
                f"PPG: {avgs.get('points', 0)} | APG: {avgs.get('assists', 0)} | "
                f"SPG: {avgs.get('steals', 0)} | TPG: {avgs.get('turnovers', 0)}"
            )
            lines.append(
                f"3PM: {avgs.get('three_pointers_made', 0)} | "
                f"FGM: {avgs.get('field_goals_made', 0)} | "
                f"FTM: {avgs.get('free_throws_made', 0)}"
            )
        return "\n".join(lines)

    elif result.query_type == "head_to_head":
        ta = data.get("team_a", "Team A")
        tb = data.get("team_b", "Team B")
        aw = data.get("a_wins", 0)
        bw = data.get("b_wins", 0)
        games = data.get("games", [])
        if not games:
            return f"**{ta}** and **{tb}** have not played each other yet."
        lines = [f"**{ta}** vs **{tb}**: {aw}-{bw}"]
        for g in games:
            lines.append(
                f"  Round {g['round']}: {g['home_team']} {g['home_score']} - "
                f"{g['away_score']} {g['away_team']}"
            )
        return "\n".join(lines)

    elif result.query_type == "schedule":
        entries = data.get("schedule", [])
        if not entries:
            return "No games scheduled."
        lines = ["**Schedule**"]
        for e in entries[:15]:
            status = e.get("status", "scheduled")
            marker = " (played)" if status == "completed" else ""
            lines.append(
                f"Round {e['round']}: {e['home_team']} vs {e['away_team']}{marker}"
            )
        if len(entries) > 15:
            lines.append(f"...and {len(entries) - 15} more matchups")
        return "\n".join(lines)

    elif result.query_type == "rules_current":
        rules = data.get("rules", {})
        if not rules:
            return "No ruleset is currently active."
        lines = ["**Current Rules**"]
        for param, value in sorted(rules.items()):
            display = param.replace("_", " ").title()
            lines.append(f"  {display}: {value}")
        return "\n".join(lines)

    elif result.query_type == "team_roster":
        if "rosters" in data:
            rosters = data["rosters"]
            lines = ["**All Teams**"]
            for roster in rosters:
                tn = roster["team_name"]
                hoopers = ", ".join(h["name"] for h in roster["hoopers"])
                lines.append(f"**{tn}:** {hoopers}")
            return "\n".join(lines)
        tn = data.get("team_name", "???")
        hoopers = data.get("hoopers", [])
        if not hoopers:
            return f"**{tn}** has no hoopers."
        lines = [f"**{tn} Roster**"]
        for h in hoopers:
            lines.append(f"  {h['name']} ({h['archetype']})")
        return "\n".join(lines)

    elif result.query_type == "proposals":
        proposals = data.get("proposals", [])
        if not proposals:
            return "No proposals have been submitted this season."
        lines = ["**Proposals**"]
        for p in proposals[:10]:
            raw = str(p.get("raw_text", ""))[:80]
            status = p.get("status", "pending")
            lines.append(f'  "{raw}" -- {status}')
        if len(proposals) > 10:
            lines.append(f"...and {len(proposals) - 10} more")
        return "\n".join(lines)

    elif result.query_type == "unknown":
        msg = data.get("message", "I'm not sure what you're asking.")
        return str(msg)

    return "I could not process that query."


# ---------------------------------------------------------------------------
# Response Formatter — AI-powered
# ---------------------------------------------------------------------------

SEARCH_FORMATTER_SYSTEM_PROMPT = """\
You are a conversational sports stats desk for Pinwheel Fates, a 3v3 basketball \
governance game. Format the raw data below into a brief, engaging Discord message.

## Rules
1. Be conversational and fun, like a sports radio host.
2. Use Discord markdown (bold, bullet points) for readability.
3. Keep responses under 1500 characters.
4. NEVER reveal private data (strategies, private reports, hidden votes).
5. NEVER make predictions about future game outcomes.
6. If governance context is relevant to the stats, mention it briefly.
7. Use the team and hooper names from the data, not invented ones.
"""


async def format_response_ai(
    question: str,
    result: QueryResult,
    api_key: str,
) -> str:
    """Format a QueryResult using Claude API. Falls back to mock on failure."""
    if result.error:
        return result.error

    user_msg = (
        f"Original question: {question}\n\n"
        f"Query type: {result.query_type}\n\n"
        f"Raw data:\n{json.dumps(result.data, indent=2, default=str)}"
    )

    model = "claude-sonnet-4-5-20250929"
    try:
        client = anthropic.AsyncAnthropic(api_key=api_key)
        response = await client.messages.create(
            model=model,
            max_tokens=500,
            system=SEARCH_FORMATTER_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
        return response.content[0].text.strip()

    except (anthropic.APIError, KeyError, IndexError) as e:
        logger.warning("AI response format failed, using mock: %s", e)
        return format_response_mock(question, result)

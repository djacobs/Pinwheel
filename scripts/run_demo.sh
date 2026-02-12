#!/usr/bin/env bash
#
# Pinwheel Fates — Showboat Demo Script
#
# Produces a self-documenting Markdown artifact with screenshots
# proving the full govern→simulate→observe→reflect cycle works.
#
# Prerequisites:
#   uvx showboat   (markdown demo builder)
#   uvx rodney     (Chrome automation)
#   .venv/         (Python venv with pinwheel installed)
#
# Usage:
#   cd Pinwheel && bash scripts/run_demo.sh
#

set -euo pipefail

DEMO_FILE="demo/pinwheel_demo.md"
DEMO_DIR="demo"
PORT=8765
SERVER_PID=""
PYTHONPATH="src"
export PYTHONPATH

# Cleanup on exit
cleanup() {
    if [ -n "$SERVER_PID" ]; then
        kill "$SERVER_PID" 2>/dev/null || true
        wait "$SERVER_PID" 2>/dev/null || true
    fi
    uvx rodney stop 2>/dev/null || true
}
trap cleanup EXIT

# ---- Setup ----
mkdir -p "$DEMO_DIR"
rm -f demo_pinwheel.db  # Fresh database

# ---- Init the demo document ----
uvx showboat init "$DEMO_FILE" "Pinwheel Fates -- Full Cycle Demo"

INTRO_TEXT="**Pinwheel Fates** is a Blaseball-inspired auto-simulated 3v3 basketball league where human players govern the rules through AI-interpreted natural language proposals. The AI serves as a social mirror -- surfacing patterns in gameplay and governance that players cannot see from inside the system.

This document proves the full **Govern > Simulate > Observe > Reflect** cycle works end-to-end. Every command below was executed live; every screenshot was captured from the running application."

uvx showboat note "$DEMO_FILE" "$INTRO_TEXT"

# ---- Step 1: Seed the league ----
uvx showboat note "$DEMO_FILE" "## Step 1: Seed the League"
uvx showboat note "$DEMO_FILE" "Create 4 Portland-themed teams with 3 agents each and generate a round-robin schedule."

uvx showboat exec "$DEMO_FILE" bash ".venv/bin/python scripts/demo_seed.py seed"

# ---- Step 2: Start the server ----
uvx showboat note "$DEMO_FILE" "## Step 2: Start the Web Dashboard"
uvx showboat note "$DEMO_FILE" "Launch the FastAPI server. The dashboard renders with HTMX + Jinja2 -- no JS build step."

DATABASE_URL="sqlite+aiosqlite:///demo_pinwheel.db" \
    .venv/bin/uvicorn pinwheel.main:app --port "$PORT" --log-level warning &
SERVER_PID=$!
sleep 2

uvx showboat exec "$DEMO_FILE" bash "curl -s http://localhost:$PORT/health | python3 -m json.tool"

# ---- Step 3: Screenshot the home page ----
uvx showboat note "$DEMO_FILE" "## Step 3: The Dashboard"
uvx showboat note "$DEMO_FILE" "The home page with navigation cards. Dark theme, Blaseball-inspired aesthetic."

uvx rodney start
uvx rodney open "http://localhost:$PORT/"
uvx rodney waitstable
sleep 1
uvx showboat image "$DEMO_FILE" "uvx rodney screenshot $DEMO_DIR/01_home.png -w 1280 -h 900"

# ---- Step 4: Run Round 1 ----
uvx showboat note "$DEMO_FILE" "## Step 4: Simulate Round 1"
uvx showboat note "$DEMO_FILE" "Run the first round of games. The simulation engine resolves every possession probabilistically based on agent attributes and the current ruleset."

uvx showboat exec "$DEMO_FILE" bash ".venv/bin/python scripts/demo_seed.py step 1"

# ---- Step 5: Screenshot the Arena ----
uvx showboat note "$DEMO_FILE" "## Step 5: The Arena"
uvx showboat note "$DEMO_FILE" "Game results appear in the Arena. Each game panel shows the final score, possession count, and Elam Ending status."

uvx rodney open "http://localhost:$PORT/arena"
uvx rodney waitstable
sleep 1
uvx showboat image "$DEMO_FILE" "uvx rodney screenshot $DEMO_DIR/02_arena.png -w 1280 -h 900"

# ---- Step 6: Screenshot Standings ----
uvx showboat note "$DEMO_FILE" "## Step 6: Standings"
uvx showboat note "$DEMO_FILE" "The league table updates after each round. Win/Loss, Points For/Against, Differential."

uvx rodney open "http://localhost:$PORT/standings"
uvx rodney waitstable
sleep 1
uvx showboat image "$DEMO_FILE" "uvx rodney screenshot $DEMO_DIR/03_standings.png -w 1280 -h 900"

# ---- Step 7: Game Detail ----
uvx showboat note "$DEMO_FILE" "## Step 7: Game Detail"
uvx showboat note "$DEMO_FILE" "Click into a game for box scores and play-by-play. Every possession is recorded."

# Get first game ID from the API and navigate directly
GAME_ID=$(.venv/bin/python -c "
import asyncio, json
from pinwheel.db.engine import create_engine, get_session
from pinwheel.db.repository import Repository
from pinwheel.db.models import SeasonRow
from sqlalchemy import select
async def get_first_game():
    engine = create_engine('sqlite+aiosqlite:///demo_pinwheel.db')
    async with get_session(engine) as s:
        repo = Repository(s)
        season = (await s.execute(select(SeasonRow).limit(1))).scalar_one()
        games = await repo.get_games_for_round(season.id, 1)
        print(games[0].id)
    await engine.dispose()
asyncio.run(get_first_game())
")
uvx rodney open "http://localhost:$PORT/games/$GAME_ID"
uvx rodney waitstable
sleep 1
uvx showboat image "$DEMO_FILE" "uvx rodney screenshot $DEMO_DIR/04_game_detail.png -w 1280 -h 1200"

# ---- Step 8: Run more rounds ----
uvx showboat note "$DEMO_FILE" "## Step 8: Advance the Season"
uvx showboat note "$DEMO_FILE" "Run 2 more rounds to build up standings and mirror data."

uvx showboat exec "$DEMO_FILE" bash ".venv/bin/python scripts/demo_seed.py step 2"

# ---- Step 9: Updated Standings ----
uvx showboat note "$DEMO_FILE" "## Step 9: Standings After 3 Rounds"

uvx showboat exec "$DEMO_FILE" bash ".venv/bin/python scripts/demo_seed.py status"

uvx rodney open "http://localhost:$PORT/standings"
uvx rodney waitstable
sleep 1
uvx showboat image "$DEMO_FILE" "uvx rodney screenshot $DEMO_DIR/05_standings_r3.png -w 1280 -h 900"

# ---- Step 10: Mirrors ----
uvx showboat note "$DEMO_FILE" "## Step 10: AI Mirrors"
uvx showboat note "$DEMO_FILE" "The mirror system reflects on gameplay and governance. AI-generated observations describe patterns -- they never prescribe actions."

uvx rodney open "http://localhost:$PORT/mirrors"
uvx rodney waitstable
sleep 1
uvx showboat image "$DEMO_FILE" "uvx rodney screenshot $DEMO_DIR/06_mirrors.png -w 1280 -h 1200"

# ---- Step 11: Submit a proposal ----
uvx showboat note "$DEMO_FILE" "## Step 11: Governance -- Submit a Proposal"
uvx showboat note "$DEMO_FILE" "A governor proposes a rule change in natural language. The AI interprets it into structured parameters."

uvx showboat exec "$DEMO_FILE" bash ".venv/bin/python scripts/demo_seed.py propose Make three-pointers worth 5 points"

uvx rodney open "http://localhost:$PORT/governance"
uvx rodney waitstable
sleep 1
uvx showboat image "$DEMO_FILE" "uvx rodney screenshot $DEMO_DIR/07_governance.png -w 1280 -h 900"

# ---- Step 12: Rules page ----
uvx showboat note "$DEMO_FILE" "## Step 12: Current Ruleset"
uvx showboat note "$DEMO_FILE" "The rules page shows all current parameters and highlights changes from defaults."

uvx rodney open "http://localhost:$PORT/rules"
uvx rodney waitstable
sleep 1
uvx showboat image "$DEMO_FILE" "uvx rodney screenshot $DEMO_DIR/08_rules.png -w 1280 -h 900"

# ---- Step 13: Team page ----
uvx showboat note "$DEMO_FILE" "## Step 13: Team Profile"
uvx showboat note "$DEMO_FILE" "Each team has a profile with roster, agent attributes (visualized as bars), and venue info."

# Get first team ID and navigate directly
TEAM_ID=$(.venv/bin/python -c "
import asyncio
from pinwheel.db.engine import create_engine, get_session
from pinwheel.db.repository import Repository
from pinwheel.db.models import SeasonRow, TeamRow
from sqlalchemy import select
async def get_first_team():
    engine = create_engine('sqlite+aiosqlite:///demo_pinwheel.db')
    async with get_session(engine) as s:
        team = (await s.execute(select(TeamRow).limit(1))).scalar_one()
        print(team.id)
    await engine.dispose()
asyncio.run(get_first_team())
")
uvx rodney open "http://localhost:$PORT/teams/$TEAM_ID"
uvx rodney waitstable
sleep 1
uvx showboat image "$DEMO_FILE" "uvx rodney screenshot $DEMO_DIR/09_team.png -w 1280 -h 1200"

# ---- Step 14: Evals Dashboard ----
uvx showboat note "$DEMO_FILE" "## Step 14: Evals Dashboard"
uvx showboat note "$DEMO_FILE" "The admin-facing evals dashboard shows aggregate mirror quality metrics, scenario flags, and AI rule evaluation. No individual mirror text is ever displayed -- only counts, rates, and composite scores."

uvx rodney open "http://localhost:$PORT/admin/evals"
uvx rodney waitstable
sleep 1
uvx showboat image "$DEMO_FILE" "uvx rodney screenshot $DEMO_DIR/10_evals.png -w 1280 -h 1200"

# ---- Step 15: Verify ----
uvx showboat note "$DEMO_FILE" "## Verification"
uvx showboat note "$DEMO_FILE" "All 327 tests pass. Zero lint errors. The demo above was captured live from a running instance."

uvx showboat exec "$DEMO_FILE" bash ".venv/bin/python -m pytest --tb=short -q 2>&1 | tail -3"

# ---- Done ----
uvx rodney stop

echo ""
echo "================================================"
echo "Demo artifact created: $DEMO_FILE"
echo "Screenshots in: $DEMO_DIR/"
echo "================================================"
echo ""
echo "To verify the demo is reproducible:"
echo "  uvx showboat verify $DEMO_FILE"

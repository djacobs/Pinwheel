#!/usr/bin/env bash
#
# Pinwheel Fates — Video Demo Script (Showboat/Rodney)
#
# Captures the visual beats for the 3-minute hackathon video.
# Each beat maps to demo/teleprompter.md (the final script).
#
# Produces: demo/video_demo.md — storyboard with embedded screenshots
#           demo/video_*.png   — individual frames for video editing
#
# Prerequisites:
#   uvx showboat   (markdown demo builder)
#   uvx rodney     (Chrome automation)
#   uv sync        (Python deps)
#
# Usage:
#   cd Pinwheel && bash scripts/run_video_demo.sh
#

set -euo pipefail

DEMO_FILE="demo/video_demo.md"
DEMO_DIR="demo"
PORT=8765
SERVER_PID=""
PYTHONPATH="src"
export PYTHONPATH

cleanup() {
    if [ -n "$SERVER_PID" ]; then
        kill "$SERVER_PID" 2>/dev/null || true
        wait "$SERVER_PID" 2>/dev/null || true
    fi
    uvx rodney stop 2>/dev/null || true
}
trap cleanup EXIT

mkdir -p "$DEMO_DIR"
rm -f demo_pinwheel.db
rm -f "$DEMO_FILE"

# ============================================================
# INIT
# ============================================================

uvx showboat init "$DEMO_FILE" "Pinwheel Fates -- Video Demo Storyboard"

uvx showboat note "$DEMO_FILE" "Visual storyboard for the 3-minute hackathon video. Each beat maps to \`demo/teleprompter.md\`. Every screenshot was captured live from a running instance.

**Judging criteria:** Demo 30% | Opus 4.6 Use 25% | Impact 25% | Depth & Execution 20%"

# ============================================================
# SEED + SERVER
# ============================================================

uvx showboat note "$DEMO_FILE" "## Setup: Seed the League"

uvx showboat exec "$DEMO_FILE" bash "uv run python scripts/demo_seed.py seed"
uvx showboat exec "$DEMO_FILE" bash "uv run python scripts/demo_seed.py step 2"

DATABASE_URL="sqlite+aiosqlite:///demo_pinwheel.db" \
    uv run uvicorn pinwheel.main:app --port "$PORT" --log-level warning &
SERVER_PID=$!
sleep 2

uvx rodney start

# ============================================================
# HOOK (0:00–0:25)
# ============================================================

uvx showboat note "$DEMO_FILE" "## Hook (0:00–0:25)

**Voiceover:** *Pinwheel Fates — a basketball simulation game where players choose teams and govern the rules together. Sports drives fierce opinions and loyalty — the perfect arena to test whether AI can help groups make better decisions together. They play through Discord and on the web. After each round of games, players propose and vote on rule changes — and Opus interprets the proposals, simulates consequences, and transparently shares what it sees.*

**Visual:** Arena page — live games in progress, commentary scrolling."

uvx rodney open "http://localhost:$PORT/arena"
uvx rodney waitstable
sleep 1
uvx rodney screenshot "$DEMO_DIR/video_01_arena_hook.png" -w 1280 -h 1400
uvx showboat image "$DEMO_FILE" "$DEMO_DIR/video_01_arena_hook.png"

# ============================================================
# WHY A GAME (0:25–0:55)
# ============================================================

uvx showboat note "$DEMO_FILE" "## Why a Game (0:25–0:55)

**Voiceover:** *Games are where humanity prototypes its next societies — low stakes, high reps, fast feedback. Coalition detection, power concentration, participation gaps — these are the same patterns that matter in newsrooms, neighborhood associations, and city councils.*

*Pinwheel is a place to experiment with direct democracy and understand what AI-augmented decision-making can actually do. Not a finished handbook — a step.*

**Visual:** Standings page, governance page — showing the patterns in action."

uvx rodney open "http://localhost:$PORT/standings"
uvx rodney waitstable
sleep 1
uvx rodney screenshot "$DEMO_DIR/video_02_standings.png" -w 1280 -h 900
uvx showboat image "$DEMO_FILE" "$DEMO_DIR/video_02_standings.png"

uvx rodney open "http://localhost:$PORT/governance"
uvx rodney waitstable
sleep 1
uvx rodney screenshot "$DEMO_DIR/video_03_governance.png" -w 1280 -h 900
uvx showboat image "$DEMO_FILE" "$DEMO_DIR/video_03_governance.png"

# ============================================================
# DEMO: PROPOSE (0:55–1:05)
# ============================================================

uvx showboat note "$DEMO_FILE" "## Demo: Propose (0:55–1:05)

**Voiceover:** *Here, a player proposes a rule change. Opus interprets the proposal, and confirms with the player. The community votes on rules between rounds.*

**Visual:** Discord \`/propose\` flow. Here we capture the governance page after a proposal."

uvx showboat exec "$DEMO_FILE" bash "uv run python scripts/demo_seed.py propose Make three-pointers worth 5 points"

uvx rodney open "http://localhost:$PORT/governance"
uvx rodney waitstable
sleep 1
uvx rodney screenshot "$DEMO_DIR/video_04_governance_propose.png" -w 1280 -h 900
uvx showboat image "$DEMO_FILE" "$DEMO_DIR/video_04_governance_propose.png"

# ============================================================
# DEMO: SIMULATE + REFLECT (1:05–1:30)
# ============================================================

uvx showboat note "$DEMO_FILE" "## Demo: Simulate + Reflect (1:05–1:30)

**Voiceover:** *The rule proposed at noon impacts the next round of games, starting at 1pm. Opus reports feedback to the league about the impact of the rules, and gives direct, private feedback to players — visible only to them — surfacing patterns in their governance behavior. The shared report surfaces league-wide dynamics: coalitions forming, power concentrating, voices going silent.*

**Visual:** Arena with games under new rules. Game detail with rule context. Reports page."

uvx showboat exec "$DEMO_FILE" bash "uv run python scripts/demo_seed.py step 1"

uvx rodney open "http://localhost:$PORT/arena"
uvx rodney waitstable
sleep 1
uvx rodney screenshot "$DEMO_DIR/video_05_arena_games.png" -w 1280 -h 1400
uvx showboat image "$DEMO_FILE" "$DEMO_DIR/video_05_arena_games.png"

# Game detail — box score + rule context
GAME_ID=$(uv run python -c "
import asyncio
from pinwheel.db.engine import create_engine, get_session
from pinwheel.db.repository import Repository
from pinwheel.db.models import SeasonRow
from sqlalchemy import select
async def get_first_game():
    engine = create_engine('sqlite+aiosqlite:///demo_pinwheel.db')
    async with get_session(engine) as s:
        repo = Repository(s)
        season = (await s.execute(select(SeasonRow).limit(1))).scalar_one()
        games = await repo.get_games_for_round(season.id, 3)
        print(games[0].id)
    await engine.dispose()
asyncio.run(get_first_game())
")
uvx rodney open "http://localhost:$PORT/games/$GAME_ID"
uvx rodney waitstable
sleep 1
uvx rodney screenshot "$DEMO_DIR/video_06_game_detail.png" -w 1280 -h 1200
uvx showboat image "$DEMO_FILE" "$DEMO_DIR/video_06_game_detail.png"

uvx rodney open "http://localhost:$PORT/reports"
uvx rodney waitstable
sleep 1
uvx rodney screenshot "$DEMO_DIR/video_07_reports.png" -w 1280 -h 1200
uvx showboat image "$DEMO_FILE" "$DEMO_DIR/video_07_reports.png"

# ============================================================
# IMPACT (1:30–1:40)
# ============================================================

uvx showboat note "$DEMO_FILE" "## Impact (1:30–1:40)

**Voiceover:** *Opus helps to illuminate hidden dynamics, amplifying human judgment by making collective decisions legible. Human players always have the last word.*

**Visual:** Reuse governance → standings cut from earlier screenshots."

# ============================================================
# WHY DISCORD (1:40–1:55)
# ============================================================

uvx showboat note "$DEMO_FILE" "## Why Discord (1:40–1:55)

**Voiceover:** *On top of a basketball simulator and Opus-powered rules engine, I chose Discord for user interaction. Any chat app with persistent memory can sit on the same stack. Discord is only the proof of concept, and different communities will choose different tools.*

**Visual:** Rules page — showing how governance shapes the system."

uvx rodney open "http://localhost:$PORT/rules"
uvx rodney waitstable
sleep 1
uvx rodney screenshot "$DEMO_DIR/video_08_rules.png" -w 1280 -h 900
uvx showboat image "$DEMO_FILE" "$DEMO_DIR/video_08_rules.png"

# ============================================================
# OPUS: FOUR ROLES (1:55–2:10)
# ============================================================

uvx showboat note "$DEMO_FILE" "## Opus: Four Roles (1:55–2:10)

**Voiceover:** *Opus played four roles. First, build partner — 200 commits over six days. Constitutional interpreter, social reporter — behavioral profiling, coalition detection, private reflections, and broadcaster — game commentary woven with league context.*

**Visual:** Team page showing AI-interpreted strategy. Quick cuts of code files."

# Team page
TEAM_ID=$(uv run python -c "
import asyncio
from pinwheel.db.engine import create_engine, get_session
from pinwheel.db.models import TeamRow
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
uvx rodney screenshot "$DEMO_DIR/video_09_team.png" -w 1280 -h 1200
uvx showboat image "$DEMO_FILE" "$DEMO_DIR/video_09_team.png"

# ============================================================
# OPUS: AGENT-NATIVE (2:10–2:30)
# ============================================================

uvx showboat note "$DEMO_FILE" "## Opus: Agent-Native (2:10–2:30)

**Voiceover:** *Nearly 2,000 tests measure how much code exists — and that is too many. The vision: ship narrative, not code. Replace classes and validators with a product document describing input and output. The dev environment looks less like an IDE and more like the narrative design tools game makers use.*

*With the model of six months from now, each component shrinks from hundreds of lines to a prompt.*

**Visual:** Test suite output as evidence of depth."

uvx showboat exec "$DEMO_FILE" bash "uv run pytest --tb=short -q 2>&1 | tail -5"

# ============================================================
# DEPTH: MAKE IT TAKE IT (2:30–2:55)
# ============================================================

uvx showboat note "$DEMO_FILE" "## Depth: Make It Take It (2:30–2:55)

**Voiceover:** *Our biggest challenge was convincing Opus to expand its scope: a player proposed 'make it take it' — a real basketball rule meaning the scoring team keeps possession. Opus knows this, and in open conversation, it explains the rule perfectly. But our structured interpreter was unable to modify the game, because the pipeline was optimized for schema-compatible fields. The model knew the answer. Our code prevented it from using what it knew.*

**Visual:** Evals dashboard — measurement infrastructure."

uvx rodney open "http://localhost:$PORT/admin/evals"
uvx rodney waitstable
sleep 1
uvx rodney screenshot "$DEMO_DIR/video_10_evals.png" -w 1280 -h 1200
uvx showboat image "$DEMO_FILE" "$DEMO_DIR/video_10_evals.png"

# ============================================================
# CLOSE (2:55–3:05)
# ============================================================

uvx showboat note "$DEMO_FILE" "## Close (2:55–3:05)

**Voiceover:** *Pinwheel is my expression of Opus helping groups make better decisions together by amplifying what we already do well: negotiate, change minds, and form coalitions.*

**Visual:** Home page with league activity. URL: **pinwheel.fly.dev**"

uvx rodney open "http://localhost:$PORT/"
uvx rodney waitstable
sleep 1
uvx rodney screenshot "$DEMO_DIR/video_11_home_close.png" -w 1280 -h 900
uvx showboat image "$DEMO_FILE" "$DEMO_DIR/video_11_home_close.png"

# ============================================================
# DONE
# ============================================================

uvx rodney stop

echo ""
echo "================================================"
echo "Video storyboard created: $DEMO_FILE"
echo "Screenshots in: $DEMO_DIR/video_*.png"
echo "================================================"
echo ""
echo "Next steps:"
echo "  1. Screen-record Discord /propose flow"
echo "  2. Screen-record a private report DM"
echo "  3. Assemble in video editor using storyboard beats"
echo ""

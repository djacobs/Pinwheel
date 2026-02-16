#!/usr/bin/env bash
#
# Pinwheel Fates — Video Demo Script (Showboat/Rodney)
#
# Captures the visual beats for the 3-minute hackathon video.
# Each step maps to a section of docs/DEMO_VIDEO_OUTLINE.md.
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

# ============================================================
# INIT
# ============================================================

uvx showboat init "$DEMO_FILE" "Pinwheel Fates -- Video Demo Storyboard"

uvx showboat note "$DEMO_FILE" "Visual storyboard for the 3-minute hackathon video. Each section maps to a beat in \`docs/DEMO_VIDEO_OUTLINE.md\`. Every screenshot was captured live from a running instance.

**Production principle:** Show, don't tell. Every voiceover claim has a corresponding visual below."

# ============================================================
# BEAT 1: HOOK (0:00–0:15) — Arena with live games
# ============================================================

uvx showboat note "$DEMO_FILE" "## Beat 1: Opening Hook (0:00–0:15)

**Voiceover:** *This is Pinwheel Fates — a basketball league where humans don't play basketball. They govern it. Rule changes in plain language, interpreted by AI, cascading through dozens of simulated games. A governance lab disguised as a sport.*

**Visual:** Arena page — live games in progress, AI commentary scrolling."

# Seed league and run 2 rounds so the arena has content
uvx showboat exec "$DEMO_FILE" bash "uv run python scripts/demo_seed.py seed"
uvx showboat exec "$DEMO_FILE" bash "uv run python scripts/demo_seed.py step 2"

# Start the server
DATABASE_URL="sqlite+aiosqlite:///demo_pinwheel.db" \
    uv run uvicorn pinwheel.main:app --port "$PORT" --log-level warning &
SERVER_PID=$!
sleep 2

uvx rodney start

# Arena — the opening image
uvx rodney open "http://localhost:$PORT/arena"
uvx rodney waitstable
sleep 1
uvx showboat image "$DEMO_FILE" "uvx rodney screenshot $DEMO_DIR/video_01_arena_hook.png -w 1280 -h 1400"

# ============================================================
# BEAT 2: GOVERN (0:15–0:35) — Proposal flow
# ============================================================

uvx showboat note "$DEMO_FILE" "## Beat 2: Govern (0:15–0:35)

**Voiceover:** *The AI interprets what you mean — in whatever language you say it. You decide whether that interpretation is right. Then the community votes.*

**Visual:** Discord \`/propose\` flow. In the video, this is a screen recording of Discord. Here we capture the governance page showing proposals.

**Player need:** I have an idea for how this game should work, and I want to say it in my own words.
**Feature:** Natural language interpreter — Opus reads free text, classifies tier, explains mechanical meaning, asks for confirmation."

# Submit a proposal via CLI
uvx showboat exec "$DEMO_FILE" bash "uv run python scripts/demo_seed.py propose Make three-pointers worth 5 points"

# Capture the governance page showing the proposal
uvx rodney open "http://localhost:$PORT/governance"
uvx rodney waitstable
sleep 1
uvx showboat image "$DEMO_FILE" "uvx rodney screenshot $DEMO_DIR/video_02_governance_propose.png -w 1280 -h 900"

uvx showboat note "$DEMO_FILE" "### Multilingual Moment

**Visual:** A second proposal in Spanish (or another language). The interpreter handles it natively.

**Player need:** I don't think in English.
**Feature:** Multilingual interpretation — same flow, any language. Opus handles this natively.

*Note: For the video, screen-record a Discord \`/propose\` in Spanish. The interpreter returns structured English; the governor confirms.*"

# Submit a Spanish-language proposal
uvx showboat exec "$DEMO_FILE" bash "uv run python scripts/demo_seed.py propose Los triples deben valer 4 puntos cuando el equipo va perdiendo"

# ============================================================
# BEAT 3: SIMULATE + OBSERVE (0:35–0:55) — Games + rule context
# ============================================================

uvx showboat note "$DEMO_FILE" "## Beat 3: Simulate + Observe (0:35–0:55)

**Voiceover:** *Propose a rule at noon, watch it reshape the league by 1pm. The feedback loop is tight enough to feel in your gut.*

**Visual:** Arena with games under the new rule. Game detail with rule context panel.

**Player need:** I voted for this rule — did it actually do anything?
**Feature:** Rule context panel on every game. Causation, not just correlation."

# Run another round so games reflect the new state
uvx showboat exec "$DEMO_FILE" bash "uv run python scripts/demo_seed.py step 1"

# Arena — games in progress
uvx rodney open "http://localhost:$PORT/arena"
uvx rodney waitstable
sleep 1
uvx showboat image "$DEMO_FILE" "uvx rodney screenshot $DEMO_DIR/video_03_arena_games.png -w 1280 -h 1400"

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
uvx showboat image "$DEMO_FILE" "uvx rodney screenshot $DEMO_DIR/video_04_game_detail.png -w 1280 -h 1200"

# Standings — consequences are visible
uvx rodney open "http://localhost:$PORT/standings"
uvx rodney waitstable
sleep 1
uvx showboat image "$DEMO_FILE" "uvx rodney screenshot $DEMO_DIR/video_05_standings.png -w 1280 -h 900"

# ============================================================
# BEAT 4: REFLECT (0:55–1:15) — Reports
# ============================================================

uvx showboat note "$DEMO_FILE" "## Beat 4: Reflect (0:55–1:15)

**Voiceover:** *The AI never decides. It illuminates. Each governor gets a private mirror — honest feedback delivered directly, visible only to them.*

**Visual:** Private report DM (screen recording from Discord). Shared governance report on web.

**Player need:** Am I actually governing well, or just going along with my friends?
**Feature:** Private reports via DM — behavioral profiling, coalition detection, participation gaps. Only you see yours.

**Player need:** What's happening in the league that I can't see from my position?
**Feature:** Shared governance report — coalition formation, power concentration, rule drift."

uvx rodney open "http://localhost:$PORT/reports"
uvx rodney waitstable
sleep 1
uvx showboat image "$DEMO_FILE" "uvx rodney screenshot $DEMO_DIR/video_06_reports.png -w 1280 -h 1200"

# ============================================================
# BEAT 5: ARCHITECTURE (1:15–1:35) — Agent-native
# ============================================================

uvx showboat note "$DEMO_FILE" "## Beat 5: Agent-Native Architecture (1:15–1:35)

**Voiceover:** *Opus 4.6 isn't just inside the game — it built the game. Agent-native from the ground up: 83 sessions, 1515 tests, every decision traceable.*

**Visual:** Architecture diagram, dev log scrolling, test output.

**Builder need:** How do I build a human-in-the-loop AI system without drowning in ops overhead?
**Approach:** Agent-native — AI is a first-class participant at every layer. Prompts treated as code.

**Builder need:** What if my community doesn't use Discord?
**Approach:** API-first. REST API and CLI exist as proof any chat client can connect."

# Show the test suite as proof of depth
uvx showboat exec "$DEMO_FILE" bash "uv run pytest --tb=short -q 2>&1 | tail -5"

# Rules page — shows how the rule system works
uvx rodney open "http://localhost:$PORT/rules"
uvx rodney waitstable
sleep 1
uvx showboat image "$DEMO_FILE" "uvx rodney screenshot $DEMO_DIR/video_07_rules.png -w 1280 -h 900"

# ============================================================
# BEAT 6: FOUR ROLES (1:35–1:55) — Opus usage
# ============================================================

uvx showboat note "$DEMO_FILE" "## Beat 6: Four Roles for Opus 4.6 (1:35–1:55)

**Voiceover:** *Four roles: build partner, interpreter, reporter, broadcaster. It shaped both the code and the gameplay — without making a single governance decision.*

**Visual:** Quick cuts between code snippets and outputs.

1. **Build Partner** — 83 sessions, pair-programmed. Dev log is the evidence.
2. **Constitutional Interpreter** — free text in, structured rules out, sandboxed.
3. **Social Reporter** — simulation, governance, and private reports.
4. **Broadcaster** — contextual game commentary with rule and rivalry awareness.

*Note: For the video, show code from \`ai/interpreter.py\`, \`ai/report.py\`, \`ai/commentary.py\` alongside their outputs.*"

# Team page — shows agent detail and team strategy (AI-interpreted)
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
uvx showboat image "$DEMO_FILE" "uvx rodney screenshot $DEMO_DIR/video_08_team.png -w 1280 -h 1200"

# ============================================================
# BEAT 7: IMPACT (1:55–2:40) — Amplify Human Judgment
# ============================================================

uvx showboat note "$DEMO_FILE" "## Beat 7: Impact — Amplify Human Judgment (1:55–2:40)

**Voiceover:** *Most groups have no tools for seeing their own social dynamics while those dynamics are happening. Coalitions form. Power concentrates. Voices go silent. And nobody inside the system can see it.*

*Pinwheel is a governance lab through basketball. The AI makes invisible dynamics legible to the people inside the system. Visibility improves governance.*

*We will need completely new, verified means of communication and negotiation. Pinwheel is a rehearsal space for that future.*

**Visual:** Montage of gameplay moments. Text overlay of real-world applications. Resonant Computing principles.

**The real-world connection:** The patterns surfaced in Pinwheel — coalition detection, power concentration, free-riding, participation gaps — are the same patterns that matter in newsrooms, fan communities, neighborhood associations, city councils, and federal agencies.

**Accessibility:** Open source (financial), multilingual proposals (linguistic), agent-native (operational)."

# Evals dashboard — shows the measurement infrastructure
uvx rodney open "http://localhost:$PORT/admin/evals"
uvx rodney waitstable
sleep 1
uvx showboat image "$DEMO_FILE" "uvx rodney screenshot $DEMO_DIR/video_09_evals.png -w 1280 -h 1200"

# ============================================================
# BEAT 8: CLOSE (2:40–3:00) — Home + tagline
# ============================================================

uvx showboat note "$DEMO_FILE" "## Beat 8: Close (2:40–3:00)

**Voiceover:** *Pinwheel is built for what comes next — where any community can see its own dynamics clearly enough to change them.*

*Pinwheel Fates. The game where AI doesn't play — it helps you see.*

**Visual:** Home page with league activity. URL: pinwheel.fly.dev"

uvx rodney open "http://localhost:$PORT/"
uvx rodney waitstable
sleep 1
uvx showboat image "$DEMO_FILE" "uvx rodney screenshot $DEMO_DIR/video_10_home_close.png -w 1280 -h 900"

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
echo "  1. Screen-record Discord /propose flows (English + Spanish)"
echo "  2. Screen-record a private report DM"
echo "  3. Create architecture diagram"
echo "  4. Assemble in video editor using storyboard beats"
echo ""

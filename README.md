# Pinwheel

Simulated 3v3 basketball league with human-driven, AI-interpreted governance and rules. Starts out as basketball, finishes as ???.

Players don't play the games — they govern them. Teams of co-governors propose rule changes in natural language, Claude interprets them into structured parameters, and the simulation engine runs with whatever the players decided. Three-pointers worth 7? Sure. 45-second shot clock? Done. The game evolves as the community evolves.

The AI serves as a social reporter — surfacing patterns in gameplay and governance that players can't see from inside the system. It never decides. It illuminates.

Built for the Anthropic hackathon track: **Amplify Human Judgment**.

**Live at:** https://pinwheel.fly.dev

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (package manager)
- An [Anthropic API key](https://console.anthropic.com/) (for AI interpretation, reports, and commentary)
- A [Discord bot token](https://discord.com/developers/applications) and server (for governance)
- [Fly.io CLI](https://fly.io/docs/flyctl/install/) (for deployment only)

## Local Development

### Install

```bash
git clone <repo-url>
cd Pinwheel

# Install with dev dependencies (creates .venv automatically)
uv sync --extra dev
```

### Configure

Create a `.env` file in the project root:

```
ANTHROPIC_API_KEY=sk-ant-...
DATABASE_URL=sqlite+aiosqlite:///pinwheel.db
PINWHEEL_ENV=development
PINWHEEL_PRESENTATION_PACE=fast
PINWHEEL_GOV_WINDOW=120
```

For Discord integration (optional for local dev):

```
DISCORD_TOKEN=...
DISCORD_GUILD_ID=...
DISCORD_CLIENT_ID=...
DISCORD_CLIENT_SECRET=...
DISCORD_REDIRECT_URI=http://localhost:8000/auth/callback
```

### Seed a League

```bash
# Create 4 teams with 3 agents each + round-robin schedule
uv run python scripts/demo_seed.py seed

# Advance 3 rounds (simulation + governance + reports + evals)
uv run python scripts/demo_seed.py step 3

# Check current standings
uv run python scripts/demo_seed.py status

# Submit a governance proposal
uv run python scripts/demo_seed.py propose "Make three-pointers worth 5 points"
```

### Run

```bash
# Start the dev server
uv run uvicorn pinwheel.main:app --reload
```

The web dashboard is at `http://localhost:8000`. API docs at `http://localhost:8000/docs`.

### Test

```bash
# Run all tests
uv run pytest -x -q

# With coverage
uv run pytest --cov=pinwheel --cov-report=term-missing

# Lint
uv run ruff check src/ tests/

# Format
uv run ruff format src/ tests/
```

All tests must pass before every commit.

## Deploy to Fly.io

### First Deploy

```bash
fly auth login
fly launch --no-deploy

# Create and attach Postgres
fly postgres create --name pinwheel-db --region sjc --vm-size shared-cpu-1x --volume-size 1
fly postgres attach pinwheel-db

# Set secrets
fly secrets set \
  ANTHROPIC_API_KEY=sk-ant-... \
  SESSION_SECRET_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(32))") \
  DISCORD_TOKEN=... \
  DISCORD_GUILD_ID=... \
  PINWHEEL_ENV=production

fly deploy
```

### Subsequent Deploys

```bash
fly deploy
```

### Monitor

```bash
fly logs
curl https://pinwheel.fly.dev/health
```

## License

MIT

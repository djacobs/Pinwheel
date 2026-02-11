# Pinwheel

A Blaseball-inspired auto-simulated 3v3 basketball league where human players govern the rules through AI-interpreted natural language proposals. Claude Opus 4.6 serves as the game's social mirror — surfacing patterns in gameplay and governance that players can't see from inside the system.

Built for the Anthropic hackathon track: **Amplify Human Judgment**.

## Requirements

- Python 3.12+
- An [Anthropic API key](https://console.anthropic.com/) (for AI interpretation, mirrors, and commentary)
- A [Discord bot token](https://discord.com/developers/applications) and server (for governance)
- [Fly.io CLI](https://fly.io/docs/flyctl/install/) (for deployment only)

## Local Development

### Install

```bash
git clone <repo-url>
cd Pinwheel

# Create a virtual environment
python -m venv .venv
source .venv/bin/activate

# Install with dev dependencies
pip install -e ".[dev]"
```

### Configure

Create a `.env` file in the project root:

```
ANTHROPIC_API_KEY=sk-ant-...
DATABASE_URL=sqlite+aiosqlite:///pinwheel.db
PINWHEEL_ENV=development
PINWHEEL_GAME_CRON="*/2 * * * *"
PINWHEEL_GOV_WINDOW=120
```

For Discord integration (optional for local dev):

```
DISCORD_BOT_TOKEN=...
DISCORD_GUILD_ID=...
```

### Seed a League

```bash
# AI-generate teams and agents, output to YAML for editing
python -m pinwheel.seed --generate --output league.yaml

# Edit league.yaml if you want to tweak names, attributes, etc.

# Seed into the local database
python -m pinwheel.seed --config league.yaml
```

### Run

```bash
# Apply database migrations
alembic upgrade head

# Start the dev server
uvicorn pinwheel.main:app --reload
```

The API is at `http://localhost:8000`. OpenAPI docs at `http://localhost:8000/docs`.

### Test

```bash
# Run all tests
pytest

# With coverage
pytest --cov=pinwheel --cov-report=term-missing

# Format and lint
ruff format . && ruff check . --fix
```

All tests must pass before every commit.

## Deploy to Fly.io

### First Deploy

```bash
# Install Fly CLI
curl -L https://fly.io/install.sh | sh

# Authenticate
fly auth login

# Launch the app (creates from fly.toml)
fly launch --no-deploy

# Create and attach Postgres
fly postgres create --name pinwheel-db --region sea --vm-size shared-cpu-1x --volume-size 1
fly postgres attach pinwheel-db

# Set secrets
fly secrets set \
  ANTHROPIC_API_KEY=sk-ant-... \
  DISCORD_BOT_TOKEN=... \
  DISCORD_GUILD_ID=... \
  PINWHEEL_ENV=production

# Deploy
fly deploy
```

### Subsequent Deploys

```bash
fly deploy
```

Migrations run automatically on deploy via the release command in `fly.toml`.

### Rollback

```bash
fly releases
fly deploy --image <previous-image-ref>
```

### Monitor

```bash
# Tail live logs
fly logs

# Check health
curl https://pinwheel-fates.fly.dev/health
```

## License

MIT

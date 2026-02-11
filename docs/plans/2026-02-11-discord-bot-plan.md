---
title: "feat: Discord Bot Implementation"
type: feat
date: 2026-02-11
---

# Discord Bot Implementation

## Overview

discord.py 2.x running inside the FastAPI process. The bot connects to Discord Gateway on startup and stays connected for the process lifetime. All governance actions flow through slash commands → FastAPI service layer → database.

## Architecture

```
FastAPI Process
├── uvicorn (HTTP + SSE)
├── APScheduler (game loop)
└── discord.py Client (Gateway WebSocket)
     ├── Slash commands → call service layer directly (in-process)
     ├── Mirror delivery → bot.get_channel().send() / user.send()
     └── Game results → bot.get_channel().send()
```

### Startup Integration

```python
# main.py
from contextlib import asynccontextmanager
import discord
from pinwheel.bot.client import PinwheelBot

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start Discord bot in background task
    bot = PinwheelBot(service_layer=app.state.services)
    bot_task = asyncio.create_task(bot.start(settings.DISCORD_BOT_TOKEN))
    app.state.bot = bot
    yield
    # Shutdown
    await bot.close()
    bot_task.cancel()

app = FastAPI(lifespan=lifespan)
```

The bot runs as an asyncio task within the same event loop as FastAPI. No inter-process communication needed. The bot holds a reference to the service layer and calls it directly.

### Why In-Process

- No message queue, no serialization, no separate deployment
- Bot can call `services.governance.submit_proposal()` directly
- SSE events and bot messages share the same event bus
- Single deploy, single health check, single log stream
- Fly.io process restarts reconnect both HTTP and Discord Gateway

### Trade-off

If the bot crashes, it takes the API with it (and vice versa). For hackathon scale this is fine. Post-hackathon, the bot could move to a separate Fly Machine communicating via the API.

## Bot Structure

```
bot/
├── __init__.py
├── client.py           # PinwheelBot(discord.Client) — setup, event handlers
├── commands/
│   ├── __init__.py
│   ├── governance.py   # /propose, /amend, /vote, /boost
│   ├── tokens.py       # /tokens, /trade
│   ├── strategy.py     # /strategy (team channels only)
│   ├── info.py         # /rules, /standings, /team
│   └── join.py         # /join — team selection
├── views/
│   ├── __init__.py
│   ├── proposal.py     # Proposal confirmation view (confirm/revise/cancel buttons)
│   ├── trade.py        # Trade offer view (accept/reject)
│   └── interpretation.py  # AI interpretation display
├── delivery.py         # Mirror delivery, game result posting
└── register.py         # Slash command registration script
```

## Discord User → Governor Mapping

```python
# When a user runs /join
async def join_command(interaction: discord.Interaction, team: str):
    governor = await services.governors.register(
        discord_user_id=str(interaction.user.id),
        discord_username=interaction.user.display_name,
        team_name=team,
        season_id=current_season.id,
    )
    # Assign Discord role
    role = discord.utils.get(interaction.guild.roles, name=f"Team: {team}")
    await interaction.user.add_roles(role)
    # Grant access to team channel
    # (role-based permissions already configured)
```

Every subsequent command authenticates by looking up the governor from `interaction.user.id`:

```python
async def get_governor(interaction: discord.Interaction) -> Governor:
    governor = await services.governors.get_by_discord_id(str(interaction.user.id))
    if not governor or not governor.is_active:
        raise GovernorNotFound("You need to /join a team first.")
    return governor
```

## Slash Command Registration

Discord slash commands are registered once via a setup script. This happens at initial deploy and whenever commands change.

```python
# bot/register.py
import discord

async def register_commands(guild_id: int, token: str):
    client = discord.Client(intents=discord.Intents.default())

    @client.event
    async def on_ready():
        guild = discord.Object(id=guild_id)
        # Sync commands to this guild (instant, vs global which takes ~1 hour)
        client.tree.copy_global_to(guild=guild)
        await client.tree.sync(guild=guild)
        await client.close()

    await client.start(token)
```

Guild-scoped registration (not global) for instant availability during development. The setup script runs as: `python -m pinwheel.bot.register`.

## Command Flows

### /propose

```
User: /propose "Make three-pointers worth 5 points"
  │
  ├─ Bot validates: governor exists, has PROPOSE tokens, window is open
  ├─ Bot sanitizes text (security layer)
  ├─ Bot calls services.ai.interpret_proposal(sanitized_text)
  │   └─ Opus 4.6 returns structured interpretation
  │
  ├─ Bot sends ephemeral response with interpretation:
  │   "I hear you. Here's what that would look like:
  │    📋 Proposal: Change three_point_value from 3 → 5
  │    ⚠️ Impact: Sharpshooters become more valuable...
  │    This costs 1 PROPOSE token. You have 2 remaining.
  │    [Confirm] [Revise] [Cancel]"
  │
  ├─ User clicks [Confirm]
  │   ├─ Bot calls services.governance.submit_proposal(...)
  │   ├─ Governance event appended to event store
  │   ├─ Bot posts to #governance-floor:
  │   │   "📋 New Proposal #7 by @user
  │   │    Original: 'Make three-pointers worth 5 points'
  │   │    Interpretation: three_point_value: 3 → 5
  │   │    Tier 1 — Simple majority required
  │   │    Vote with /vote 7 yes or /vote 7 no"
  │   └─ Bot creates a thread for debate
  │
  └─ User clicks [Revise]
      └─ Bot prompts for revised text, re-interprets
```

### /vote

```
User: /vote 7 yes
  │
  ├─ Bot validates: governor exists, proposal active, haven't voted yet
  ├─ Bot calls services.governance.cast_vote(governor, proposal_id, yes)
  ├─ Vote event appended (hidden until window close)
  │
  └─ Bot sends ephemeral: "Your vote on Proposal #7 has been recorded.
     Votes are hidden until the governance window closes."
```

### /trade

```
User: /trade @other_user propose 1
  │
  ├─ Bot validates: both governors exist, sender has tokens
  ├─ Bot sends trade offer to target user:
  │   "@user offers you 1 PROPOSE token.
  │    What do they want in return?
  │    [Accept] [Counter] [Reject]"
  │
  ├─ Target clicks [Accept]
  │   ├─ services.tokens.execute_trade(...)
  │   ├─ Trade events appended
  │   └─ Bot announces in #governance-log (public, terms visible)
  │
  └─ Target clicks [Counter]
      └─ DM flow for counter-offer
```

### /strategy (team channels only)

```
User (in #rose-city-thorns): /strategy "Press defense in the Elam period"
  │
  ├─ Bot validates: governor is on this team, team_strategy_enabled
  ├─ Bot calls services.ai.interpret_strategy(text)
  │   └─ Opus returns structured TeamStrategy
  ├─ Bot shows interpretation with confirm/cancel
  ├─ User confirms → strategy stored for team
  └─ Bot warns: "Press is exhausting. If stamina is low by Elam, this backfires."
```

## Mirror Delivery

Mirrors are delivered after generation:

```python
# delivery.py
async def deliver_mirrors(bot: PinwheelBot, mirrors: list[Mirror]):
    for mirror in mirrors:
        if mirror.mirror_type == "private":
            # DM to individual governor
            user = await bot.fetch_user(int(mirror.governor.discord_user_id))
            await user.send(format_private_mirror(mirror))
        else:
            # Post to #mirrors channel
            channel = bot.get_channel(MIRRORS_CHANNEL_ID)
            await channel.send(format_shared_mirror(mirror))
```

## Game Result Posting

When games complete presentation:

```python
async def post_game_results(bot: PinwheelBot, results: list[GameResult]):
    channel = bot.get_channel(GAME_DAY_CHANNEL_ID)
    for result in results:
        embed = format_game_result_embed(result)
        await channel.send(embed=embed)

    # Update standings in #announcements
    announcements = bot.get_channel(ANNOUNCEMENTS_CHANNEL_ID)
    standings = await services.standings.get_current()
    await announcements.send(format_standings(standings))
```

## Server Setup Requirements

The Discord server needs to be pre-configured:

```
Channels:
  #announcements (read-only for governors, bot posts)
  #game-day (read-only for governors, bot posts)
  #governance-floor (governors can post commands)
  #governance-log (read-only, bot posts)
  #mirrors (read-only, bot posts)
  #trash-talk (everyone can post)
  #rules (read-only, bot posts)
  #new-governors (everyone can post)
  #rose-city-thorns (team role only)
  ... (one per team)

Roles:
  Governor (base permissions for governance channels)
  Team: Rose City Thorns (access to #rose-city-thorns)
  ... (one per team)
  Spectator (read access to league-wide channels)
  Commissioner (bot role — admin permissions)
```

A setup script could create these, or they can be manually configured.

## Environment Variables

```
DISCORD_BOT_TOKEN       # Bot token from Discord Developer Portal
DISCORD_GUILD_ID        # Server ID (for guild-scoped command registration)
DISCORD_MIRRORS_CHANNEL_ID
DISCORD_GAME_DAY_CHANNEL_ID
DISCORD_ANNOUNCEMENTS_CHANNEL_ID
DISCORD_GOVERNANCE_CHANNEL_ID
DISCORD_GOVERNANCE_LOG_CHANNEL_ID
```

## Implementation Priority

1. **Bot scaffolding** — PinwheelBot class, lifespan integration, connect to Discord
2. **`/join`** — Governor registration, role assignment, team lock
3. **`/propose` → AI interpretation → confirm** — Core governance loop
4. **`/vote`** with hidden votes — Voting lifecycle
5. **`/tokens`** — Balance display
6. **Mirror delivery** — Post shared mirrors, DM private mirrors
7. **Game result posting** — Announcements and #game-day
8. **`/trade`** — Token trading
9. **`/strategy`** — Team tactical overrides
10. **`/amend`, `/boost`** — Remaining governance actions
11. **`/rules`, `/standings`, `/team`** — Info commands

## Acceptance Criteria

- [ ] Bot connects to Discord and stays connected alongside FastAPI
- [ ] `/join` registers a governor, assigns team role, grants channel access
- [ ] `/propose` flows through AI interpretation with confirm/revise/cancel UI
- [ ] `/vote` records hidden votes, revealed on window close
- [ ] Mirror delivery works (shared → channel, private → DM)
- [ ] Game results post to #game-day and #announcements
- [ ] Bot reconnects after Fly.io deploy/restart
- [ ] Governor authentication via Discord user ID on every command
- [ ] Team channel commands restricted to team members

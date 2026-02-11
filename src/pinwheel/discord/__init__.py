"""Discord bot integration for Pinwheel Fates.

The bot runs in-process with FastAPI, sharing the same event loop.
It subscribes to EventBus for real-time game updates and posts
results, governance outcomes, and mirrors to configured channels.

Optional: if DISCORD_BOT_TOKEN is not set, the app runs without Discord.
"""

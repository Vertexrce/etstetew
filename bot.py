"""
bot.py — Main entry point for the Discord bot.

Environment variables (set in Railway or a .env file):
  DISCORD_TOKEN   — Your bot token (required)
  GUILD_ID        — Your Discord server's guild ID for instant slash-command sync (required)

Railway setup:
  1. Push your project to a GitHub repo.
  2. Create a new Railway project → "Deploy from GitHub repo".
  3. Add the env vars above in Railway → Variables.
  4. Set the start command to:  python bot.py
"""

import asyncio
import logging
import os
from pathlib import Path

import discord
from discord.ext import commands

# ─────────────────────────────────────────────────────────────────────────────
# Environment
# ─────────────────────────────────────────────────────────────────────────────

TOKEN    : str = os.environ["DISCORD_TOKEN"]   # raises KeyError if missing — intentional
GUILD_ID : int = int(os.environ["GUILD_ID"])   # used for instant guild-scoped command sync

# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level   = logging.INFO,
    format  = "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt = "%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("bot")

# ─────────────────────────────────────────────────────────────────────────────
# Bot setup
# ─────────────────────────────────────────────────────────────────────────────

intents                  = discord.Intents.default()
intents.members          = True   # required for on_member_join and member_count
intents.message_content  = True   # required if any command reads message content

bot = discord.ext.commands.Bot(
    command_prefix = "!",          # prefix for legacy text commands (slash commands don't need this)
    intents        = intents,
    help_command   = None,         # disable the default help command
)

# ─────────────────────────────────────────────────────────────────────────────
# Events
# ─────────────────────────────────────────────────────────────────────────────

@bot.event
async def on_ready() -> None:
    logger.info("Logged in as %s (ID: %s)", bot.user, bot.user.id)

    # Copy all global commands into the guild, then sync instantly.
    # For production global sync (takes up to 1 hour), replace the three
    # lines below with just:  await bot.tree.sync()
    guild = discord.Object(id=GUILD_ID)
    bot.tree.copy_global_to(guild=guild)
    await bot.tree.sync(guild=guild)
    logger.info("Slash commands synced to guild %s.", GUILD_ID)


@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError) -> None:
    if isinstance(error, commands.CommandNotFound):
        return
    logger.error("Command error in %s: %s", ctx.command, error, exc_info=error)


# ─────────────────────────────────────────────────────────────────────────────
# Cog loader
# ─────────────────────────────────────────────────────────────────────────────

async def load_cogs() -> None:
    cogs_dir = Path(__file__).parent / "cogs"
    if not cogs_dir.exists():
        logger.warning("No 'cogs/' directory found — no cogs loaded.")
        return

    for path in sorted(cogs_dir.glob("*.py")):
        if path.stem.startswith("_"):
            continue                              # skip __init__.py etc.
        module = f"cogs.{path.stem}"
        try:
            await bot.load_extension(module)
            logger.info("Loaded cog: %s", module)
        except Exception as exc:
            logger.error("Failed to load cog %s: %s", module, exc, exc_info=exc)


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

async def main() -> None:
    async with bot:
        await load_cogs()
        await bot.start(TOKEN)


if __name__ == "__main__":
    asyncio.run(main())

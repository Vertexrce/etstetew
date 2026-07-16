"""
welcome.py — Welcome Cog for discord.py

SETUP INSTRUCTIONS
──────────────────
1. Place this file inside your cogs/ directory.
2. Run the bot and use /welcome setup to configure the channel,
   the name shown in the embed, and the custom message line.
3. Load the cog: await bot.load_extension("cogs.welcome")
"""

import logging
from datetime import datetime
from typing import Optional

import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands

from db import DB_PATH

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Ordinal helper  (1 → "1st", 2 → "2nd", 113 → "113th", etc.)
# ─────────────────────────────────────────────────────────────────────────────

def _ordinal(n: int) -> str:
    if 11 <= (n % 100) <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


# ─────────────────────────────────────────────────────────────────────────────
# Database helpers
# ─────────────────────────────────────────────────────────────────────────────

async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS welcome_config (
                guild_id    INTEGER PRIMARY KEY,
                channel_id  INTEGER NOT NULL,
                embed_name  TEXT    NOT NULL DEFAULT 'the server',
                message     TEXT    NOT NULL DEFAULT 'Welcome to the server!'
            )
        """)
        await db.commit()


async def _get_config(guild_id: int) -> Optional[aiosqlite.Row]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM welcome_config WHERE guild_id = ?", (guild_id,)
        )
        return await cur.fetchone()


async def _set_config(
    guild_id: int,
    channel_id: int,
    embed_name: str,
    message: str,
) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO welcome_config (guild_id, channel_id, embed_name, message)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                channel_id = excluded.channel_id,
                embed_name = excluded.embed_name,
                message    = excluded.message
        """, (guild_id, channel_id, embed_name, message))
        await db.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Embed builder
# ─────────────────────────────────────────────────────────────────────────────

def _build_welcome_embed(member: discord.Member, embed_name: str, message: str) -> discord.Embed:
    member_count = member.guild.member_count or 0
    ordinal      = _ordinal(member_count)
    timestamp    = datetime.now().strftime("%-m/%-d/%Y %-I:%M %p")   # e.g. 5/22/2026 2:29 PM

    embed = discord.Embed(
        title       = f"Welcome To {embed_name}",
        description = (
            f"Hello {member.mention}, you are the **{ordinal}** member!\n\n"
            f"{message}"
        ),
        color       = 0x2B2D31,   # dark Discord-like background colour
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text=f"{embed_name} • {timestamp}")
    return embed


# ─────────────────────────────────────────────────────────────────────────────
# Cog
# ─────────────────────────────────────────────────────────────────────────────

class Welcome(commands.Cog):
    """Sends a welcome embed when a member joins the server."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ── on_member_join ────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        config = await _get_config(member.guild.id)
        if not config:
            logger.debug("[Welcome] No config for guild %s — skipping.", member.guild.id)
            return

        channel = member.guild.get_channel(config["channel_id"])
        if not channel or not isinstance(channel, discord.TextChannel):
            logger.warning("[Welcome] Channel %s not found in guild %s.", config["channel_id"], member.guild.id)
            return

        try:
            embed = _build_welcome_embed(member, config["embed_name"], config["message"])
            await channel.send(embed=embed)
        except Exception:
            logger.exception("[Welcome] Failed to send welcome embed for %s.", member)

    # ── /welcome group ────────────────────────────────────────────────────────

    group = app_commands.Group(name="welcome", description="Configure the welcome message.")

    @group.command(name="setup", description="Set up the welcome embed for new members.")
    @app_commands.describe(
        channel    = "Channel where welcome messages will be sent.",
        embed_name = "Name shown in the embed title and footer (e.g. 'IHN Clan').",
        message    = "Custom message shown below the member count line.",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def setup(
        self,
        interaction: discord.Interaction,
        channel   : discord.TextChannel,
        embed_name: str,
        message   : str,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            await _ensure_table()
            await _set_config(interaction.guild_id, channel.id, embed_name, message)

            # Preview embed
            preview = _build_welcome_embed(interaction.user, embed_name, message)
            await interaction.followup.send(
                content=(
                    f"✅ Welcome message configured for {channel.mention}.\n"
                    f"Here's a preview of what new members will see:"
                ),
                embed   = preview,
                ephemeral=True,
            )
        except Exception:
            logger.exception("[Welcome] setup error")
            await interaction.followup.send(
                embed=discord.Embed(
                    title       = "Error",
                    description = "An error occurred while saving the configuration. Please try again.",
                    color       = 0xFF0000,
                ),
                ephemeral=True,
            )

    @group.command(name="test", description="Send a test welcome message for yourself.")
    @app_commands.default_permissions(manage_guild=True)
    async def test(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            await _ensure_table()
            config = await _get_config(interaction.guild_id)
            if not config:
                await interaction.followup.send(
                    content="⚠️ No welcome config found. Run `/welcome setup` first.",
                    ephemeral=True,
                )
                return

            channel = interaction.guild.get_channel(config["channel_id"])
            if not channel or not isinstance(channel, discord.TextChannel):
                await interaction.followup.send(
                    content="⚠️ The configured welcome channel no longer exists. Run `/welcome setup` again.",
                    ephemeral=True,
                )
                return

            embed = _build_welcome_embed(interaction.user, config["embed_name"], config["message"])
            await channel.send(embed=embed)
            await interaction.followup.send(content=f"✅ Test welcome sent to {channel.mention}.", ephemeral=True)
        except Exception:
            logger.exception("[Welcome] test error")
            await interaction.followup.send(content="An error occurred. Please try again.", ephemeral=True)

    @group.command(name="disable", description="Disable welcome messages for this server.")
    @app_commands.default_permissions(manage_guild=True)
    async def disable(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("DELETE FROM welcome_config WHERE guild_id = ?", (interaction.guild_id,))
                await db.commit()
            await interaction.followup.send(content="✅ Welcome messages disabled.", ephemeral=True)
        except Exception:
            logger.exception("[Welcome] disable error")
            await interaction.followup.send(content="An error occurred. Please try again.", ephemeral=True)


# ─────────────────────────────────────────────────────────────────────────────
# Extension entry-point
# ─────────────────────────────────────────────────────────────────────────────

async def setup(bot: commands.Bot) -> None:
    await _ensure_table()
    await bot.add_cog(Welcome(bot))

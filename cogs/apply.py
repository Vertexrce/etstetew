"""
apply.py — Application Cog for discord.py

Commands
────────
/apply          — Opens the application modal (any member).
/apply setup    — Configure staff channel, staff role, and accepted role (admin only).
/apply disable  — Remove this server's application config (admin only).

Flow
────
1. Member runs /apply → modal with 5 questions pops up.
2. On submit → embed posted to the staff channel with Accept / Decline buttons.
3. Staff member clicks Accept → applicant receives the accepted role + DM.
   Staff member clicks Decline → applicant receives a DM notifying them.
4. Buttons are disabled after a decision so they can't be clicked twice.
"""

import logging
import os
from pathlib import Path
from typing import Optional

import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands

logger = logging.getLogger(__name__)

DB_PATH: str = os.environ.get("DB_PATH", str(Path(__file__).parent.parent / "bot.db"))


# ─────────────────────────────────────────────────────────────────────────────
# Database helpers
# ─────────────────────────────────────────────────────────────────────────────

async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS apply_config (
                guild_id          INTEGER PRIMARY KEY,
                staff_channel_id  INTEGER NOT NULL,
                staff_role_id     INTEGER NOT NULL,
                accepted_role_id  INTEGER NOT NULL
            )
        """)
        await db.commit()


async def _get_config(guild_id: int) -> Optional[aiosqlite.Row]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM apply_config WHERE guild_id = ?", (guild_id,)
        )
        return await cur.fetchone()


async def _set_config(
    guild_id: int,
    staff_channel_id: int,
    staff_role_id: int,
    accepted_role_id: int,
) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO apply_config
                (guild_id, staff_channel_id, staff_role_id, accepted_role_id)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                staff_channel_id = excluded.staff_channel_id,
                staff_role_id    = excluded.staff_role_id,
                accepted_role_id = excluded.accepted_role_id
        """, (guild_id, staff_channel_id, staff_role_id, accepted_role_id))
        await db.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Application Modal
# ─────────────────────────────────────────────────────────────────────────────

class ApplicationModal(discord.ui.Modal, title="Team Application"):
    hours = discord.ui.TextInput(
        label="How many hours do you have?",
        placeholder="e.g. 500 hours",
        max_length=100,
    )
    previous_teams = discord.ui.TextInput(
        label="What are your previous teams?",
        placeholder="List any previous teams or 'None'",
        style=discord.TextStyle.paragraph,
        max_length=500,
    )
    benefit = discord.ui.TextInput(
        label="What can you do to benefit the team?",
        placeholder="Describe your skills and contributions",
        style=discord.TextStyle.paragraph,
        max_length=500,
    )
    active = discord.ui.TextInput(
        label="Will you be active Friday–Tuesday?",
        placeholder="Yes / No / explain",
        max_length=200,
    )
    age = discord.ui.TextInput(
        label="How old are you?",
        placeholder="e.g. 18",
        max_length=10,
    )

    def __init__(self, config: aiosqlite.Row) -> None:
        super().__init__()
        self._config = config

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        guild          = interaction.guild
        staff_channel  = guild.get_channel(self._config["staff_channel_id"])
        staff_role     = guild.get_role(self._config["staff_role_id"])

        if not staff_channel or not isinstance(staff_channel, discord.TextChannel):
            await interaction.followup.send(
                "⚠️ The staff review channel no longer exists. Please contact an admin.",
                ephemeral=True,
            )
            return

        # Build the review embed
        embed = discord.Embed(
            title       = "📋 New Application",
            color       = 0x5865F2,
            description = (
                f"**Applicant:** {interaction.user.mention} (`{interaction.user}`)\n"
                f"**User ID:** `{interaction.user.id}`"
            ),
        )
        embed.add_field(
            name   = "⏱ Hours",
            value  = self.hours.value,
            inline = False,
        )
        embed.add_field(
            name   = "🏅 Previous Teams",
            value  = self.previous_teams.value,
            inline = False,
        )
        embed.add_field(
            name   = "💡 How They'll Benefit the Team",
            value  = self.benefit.value,
            inline = False,
        )
        embed.add_field(
            name   = "📅 Active Friday–Tuesday?",
            value  = self.active.value,
            inline = False,
        )
        embed.add_field(
            name   = "🎂 Age",
            value  = self.age.value,
            inline = False,
        )
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        embed.set_footer(text="Use the buttons below to Accept or Decline.")

        # Mention the staff role so they're pinged
        mention = staff_role.mention if staff_role else ""
        view    = ReviewView(
            applicant_id     = interaction.user.id,
            accepted_role_id = self._config["accepted_role_id"],
            staff_role_id    = self._config["staff_role_id"],
        )

        await staff_channel.send(content=mention, embed=embed, view=view)
        await interaction.followup.send(
            "✅ Your application has been submitted! You'll be notified once it's reviewed.",
            ephemeral=True,
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        logger.exception("[Apply] Modal error: %s", error)
        if not interaction.response.is_done():
            await interaction.response.send_message(
                "An error occurred. Please try again.", ephemeral=True
            )
        else:
            await interaction.followup.send(
                "An error occurred. Please try again.", ephemeral=True
            )


# ─────────────────────────────────────────────────────────────────────────────
# Review Buttons (Accept / Decline)
# ─────────────────────────────────────────────────────────────────────────────

class ReviewView(discord.ui.View):
    """Persistent-style view attached to each application message."""

    def __init__(
        self,
        applicant_id    : int,
        accepted_role_id: int,
        staff_role_id   : int,
    ) -> None:
        super().__init__(timeout=None)   # buttons never time out on their own
        self.applicant_id     = applicant_id
        self.accepted_role_id = accepted_role_id
        self.staff_role_id    = staff_role_id

    def _is_staff(self, interaction: discord.Interaction) -> bool:
        """Return True if the user pressing the button has the staff role."""
        role = interaction.guild.get_role(self.staff_role_id)
        if role is None:
            return False
        return role in interaction.user.roles

    def _disable_all(self) -> None:
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True

    # ── Accept ────────────────────────────────────────────────────────────────

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success, emoji="✅")
    async def accept(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if not self._is_staff(interaction):
            await interaction.response.send_message(
                "❌ You don't have permission to review applications.", ephemeral=True
            )
            return

        await interaction.response.defer()

        guild    = interaction.guild
        member   = guild.get_member(self.applicant_id)
        role     = guild.get_role(self.accepted_role_id)

        result_lines: list[str] = []

        if member and role:
            try:
                await member.add_roles(role, reason=f"Application accepted by {interaction.user}")
                result_lines.append(f"✅ Gave {member.mention} the **{role.name}** role.")
            except discord.Forbidden:
                result_lines.append("⚠️ Couldn't add the role — check the bot's role hierarchy.")
        elif not member:
            result_lines.append("⚠️ Applicant is no longer in the server.")
        else:
            result_lines.append("⚠️ Accepted role not found.")

        # DM the applicant
        if member:
            try:
                await member.send(
                    "🎉 Your application has been **accepted**! Welcome to the team."
                )
                result_lines.append("📬 DM sent to applicant.")
            except discord.Forbidden:
                result_lines.append("⚠️ Couldn't DM the applicant (DMs closed).")

        # Update the embed to show who accepted it
        embed = interaction.message.embeds[0] if interaction.message.embeds else None
        if embed:
            embed.color = 0x57F287   # green
            embed.add_field(
                name   = "✅ Accepted by",
                value  = interaction.user.mention,
                inline = False,
            )

        self._disable_all()
        await interaction.message.edit(embed=embed, view=self)
        await interaction.followup.send("\n".join(result_lines), ephemeral=True)

    # ── Decline ───────────────────────────────────────────────────────────────

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.danger, emoji="❌")
    async def decline(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if not self._is_staff(interaction):
            await interaction.response.send_message(
                "❌ You don't have permission to review applications.", ephemeral=True
            )
            return

        await interaction.response.defer()

        guild  = interaction.guild
        member = guild.get_member(self.applicant_id)

        result_lines: list[str] = []

        if member:
            try:
                await member.send(
                    "❌ Your application has been **declined**. "
                    "Feel free to re-apply in the future!"
                )
                result_lines.append("📬 DM sent to applicant.")
            except discord.Forbidden:
                result_lines.append("⚠️ Couldn't DM the applicant (DMs closed).")
        else:
            result_lines.append("⚠️ Applicant is no longer in the server.")

        # Update the embed to show who declined it
        embed = interaction.message.embeds[0] if interaction.message.embeds else None
        if embed:
            embed.color = 0xED4245   # red
            embed.add_field(
                name   = "❌ Declined by",
                value  = interaction.user.mention,
                inline = False,
            )

        self._disable_all()
        await interaction.message.edit(embed=embed, view=self)
        await interaction.followup.send("\n".join(result_lines), ephemeral=True)


# ─────────────────────────────────────────────────────────────────────────────
# Cog
# ─────────────────────────────────────────────────────────────────────────────

class Apply(commands.Cog):
    """Handles the /apply command and application review flow."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    group = app_commands.Group(name="apply", description="Application system.")

    # ── /apply ────────────────────────────────────────────────────────────────

    @group.command(name="now", description="Submit an application to join the team.")
    async def apply_now(self, interaction: discord.Interaction) -> None:
        config = await _get_config(interaction.guild_id)
        if not config:
            await interaction.response.send_message(
                "⚠️ Applications aren't set up yet. Ask an admin to run `/apply setup`.",
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(ApplicationModal(config))

    # ── /apply setup ──────────────────────────────────────────────────────────

    @group.command(
        name        = "setup",
        description = "Configure the application system (admin only).",
    )
    @app_commands.describe(
        staff_channel = "Channel where applications are sent for review.",
        staff_role    = "Role allowed to accept or decline applications.",
        accepted_role = "Role given to applicants when they are accepted.",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def setup(
        self,
        interaction  : discord.Interaction,
        staff_channel: discord.TextChannel,
        staff_role   : discord.Role,
        accepted_role: discord.Role,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            await _set_config(
                interaction.guild_id,
                staff_channel.id,
                staff_role.id,
                accepted_role.id,
            )
            await interaction.followup.send(
                f"✅ Application system configured!\n"
                f"• **Staff channel:** {staff_channel.mention}\n"
                f"• **Staff role:** {staff_role.mention}\n"
                f"• **Accepted role:** {accepted_role.mention}\n\n"
                f"Members can now use `/apply now` to submit an application.",
                ephemeral=True,
            )
        except Exception:
            logger.exception("[Apply] setup error")
            await interaction.followup.send(
                "An error occurred while saving. Please try again.", ephemeral=True
            )

    # ── /apply disable ────────────────────────────────────────────────────────

    @group.command(
        name        = "disable",
        description = "Disable the application system for this server (admin only).",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def disable(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute(
                    "DELETE FROM apply_config WHERE guild_id = ?",
                    (interaction.guild_id,)
                )
                await db.commit()
            await interaction.followup.send(
                "✅ Application system disabled.", ephemeral=True
            )
        except Exception:
            logger.exception("[Apply] disable error")
            await interaction.followup.send(
                "An error occurred. Please try again.", ephemeral=True
            )


# ─────────────────────────────────────────────────────────────────────────────
# Extension entry-point
# ─────────────────────────────────────────────────────────────────────────────

async def setup(bot: commands.Bot) -> None:
    await _ensure_table()
    await bot.add_cog(Apply(bot))

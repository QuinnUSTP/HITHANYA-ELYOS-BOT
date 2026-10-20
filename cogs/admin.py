import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from database import slugify
from services.role_manager import check_role_assignable

log = logging.getLogger("admin")


class AdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="add_legion", description="Add a Legion option to the panel")
    @app_commands.describe(name="Legion name", role="Existing role to use (optional)")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def add_legion(
        self,
        interaction: discord.Interaction,
        name: app_commands.Range[str, 2, 40],
        role: Optional[discord.Role] = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        db = self.bot.db

        await db.ensure_guild(guild.id)
        await db.seed_defaults(guild.id)

        key = slugify(name)
        if key == "none":
            await interaction.followup.send("⚠️ Please choose a different name.", ephemeral=True)
            return

        legions = await db.list_mappings(guild.id, "legion", enabled_only=False)
        if len(legions) >= 25 and not any(m["item_key"] == key for m in legions):
            await interaction.followup.send("⚠️ A dropdown holds at most 25 options.", ephemeral=True)
            return

        if role is None:
            try:
                role = await guild.create_role(
                    name=f"Legion: {name}",
                    permissions=discord.Permissions.none(),
                    hoist=True,
                    reason=f"Legion added by {interaction.user}",
                )
            except discord.Forbidden:
                await interaction.followup.send("⚠️ I can't create roles. Check Manage Roles.", ephemeral=True)
                return
        else:
            problem = check_role_assignable(guild, role)
            if problem:
                await interaction.followup.send(f"⚠️ {problem}", ephemeral=True)
                return

        await db.upsert_mapping(guild.id, "legion", key, name, role.id, "🏯")
        await db.log(guild.id, interaction.user.id, "add_legion", f"{name} -> {role.id}")
        log.info("Legion %s added in guild %s", name, guild.id)
        await interaction.followup.send(
            f"✅ Added **{name}** with {role.mention}. Run `/setup_roles` to refresh the panel.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdminCog(bot))
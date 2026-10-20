import logging
import os

import discord
from discord import app_commands
from discord.ext import commands

from config import DISCORD_TOKEN, LOG_LEVEL, ALLOWED_CHANNEL_ID
from database import Database

LOCK_EXEMPT_COMMANDS = {"boss_panel"}
GUILD_ID = os.getenv("GUILD_ID")


class ChannelLockedTree(app_commands.CommandTree):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        command = interaction.command
        if command is not None and command.name in LOCK_EXEMPT_COMMANDS:
            return True
        if ALLOWED_CHANNEL_ID and interaction.channel_id != ALLOWED_CHANNEL_ID:
            await interaction.response.send_message(
                f"This bot only works in <#{ALLOWED_CHANNEL_ID}>.",
                ephemeral=True,
            )
            return False
        return True

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            text = "You need the **Manage Server** permission to use this command."
        elif isinstance(error, app_commands.CheckFailure):
            return
        else:
            logging.getLogger("bot").exception("Command error", exc_info=error)
            text = "Something went wrong. The error was logged."

        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)


class AionRoleBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True

        super().__init__(
            command_prefix="!",
            intents=intents,
            tree_cls=ChannelLockedTree,
        )
        self.db = Database()

    async def setup_hook(self) -> None:
        await self.db.initialize()
        await self.load_extension("cogs.legions")
        await self.load_extension("cogs.admin")
        await self.load_extension("cogs.roles")
        await self.load_extension("cogs.events")
        await self.load_extension("cogs.rifts")
        await self.load_extension("cogs.fieldboss")

        if GUILD_ID:
            guild = discord.Object(id=int(GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()

    async def on_ready(self) -> None:
        logging.getLogger("bot").info(
            "Logged in as %s in %d server(s)", self.user, len(self.guilds)
        )


async def main() -> None:
    if not DISCORD_TOKEN:
        raise RuntimeError(
            "DISCORD_TOKEN is missing. Copy .env.example to .env and add your token."
        )

    async with AionRoleBot() as bot:
        await bot.start(DISCORD_TOKEN)


if __name__ == "__main__":
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL, logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    import asyncio
    asyncio.run(main())
import asyncio
import json
import os

import discord
from discord import app_commands
from discord.ext import commands
from deep_translator import GoogleTranslator

PREFS_FILE = "translate_prefs.json"
LANGS = {
    "English": "en",
    "Filipino": "tl",
    "Chinese (Simplified)": "zh-CN",
    "Japanese": "ja",
    "Korean": "ko",
    "Thai": "th",
    "Indonesian": "id",
}


def _translate(text: str, target: str) -> str:
    return GoogleTranslator(source="auto", target=target).translate(text[:4500])


class TranslateCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.prefs = self._load()
        self.menu = app_commands.ContextMenu(name="Translate", callback=self.translate_message)

    def _load(self) -> dict:
        try:
            with open(PREFS_FILE, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {}

    def _save(self) -> None:
        tmp = f"{PREFS_FILE}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.prefs, fh)
        os.replace(tmp, PREFS_FILE)

    async def cog_load(self) -> None:
        self.bot.tree.add_command(self.menu)

    async def cog_unload(self) -> None:
        self.bot.tree.remove_command(self.menu.name, type=self.menu.type)

    def target_for(self, interaction: discord.Interaction) -> str:
        saved = self.prefs.get(str(interaction.user.id))
        if saved:
            return saved
        locale = str(interaction.locale)
        if locale in ("zh-CN", "zh-TW"):
            return locale
        return locale.split("-")[0]

    async def translate_message(self, interaction: discord.Interaction, message: discord.Message) -> None:
        text = message.content or " ".join(e.description or "" for e in message.embeds)
        if not text.strip():
            await interaction.response.send_message("Nothing to translate.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        target = self.target_for(interaction)
        try:
            result = await asyncio.to_thread(_translate, text, target)
        except Exception:
            await interaction.followup.send("⚠️ Translation failed. Try again in a moment.", ephemeral=True)
            return
        embed = discord.Embed(description=result, color=discord.Color.blurple())
        embed.set_footer(text=f"Translated to {target} · message by {message.author.display_name}")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="mylanguage", description="Set the language the Translate option uses for you")
    @app_commands.choices(language=[app_commands.Choice(name=n, value=c) for n, c in LANGS.items()])
    async def mylanguage(self, interaction: discord.Interaction, language: app_commands.Choice[str]) -> None:
        self.prefs[str(interaction.user.id)] = language.value
        self._save()
        await interaction.response.send_message(f"✅ Translations will now be in **{language.name}**.", ephemeral=True)

    @app_commands.command(name="translate", description="Translate text into a language")
    @app_commands.choices(to=[app_commands.Choice(name=n, value=c) for n, c in LANGS.items()])
    async def translate(self, interaction: discord.Interaction, text: str, to: app_commands.Choice[str]) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            result = await asyncio.to_thread(_translate, text, to.value)
        except Exception:
            await interaction.followup.send("⚠️ Translation failed. Try again in a moment.", ephemeral=True)
            return
        await interaction.followup.send(result, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TranslateCog(bot))
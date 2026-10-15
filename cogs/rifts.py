import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import RIFT_EVENT_CHANNEL_ID

log = logging.getLogger("rifts")

SERVER_TZ = timezone(timedelta(hours=9))
DEFAULT_ANCHOR = datetime(2026, 10, 16, 2, 0, tzinfo=SERVER_TZ)
CYCLE = timedelta(hours=3)
PORTAL = timedelta(minutes=10)
EVENT = timedelta(hours=1)
GRACE = timedelta(minutes=4)
BAR_LENGTH = 12
STATE_FILE = Path("rift_anchor.json")
PING_ROLE_NAME = "Ping: Rifts"

GREEN = discord.Color.from_rgb(87, 242, 135)
RED = discord.Color.from_rgb(237, 66, 69)
GOLD = discord.Color.from_rgb(254, 190, 60)


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state))


def last_opening(anchor: datetime, moment: datetime) -> datetime:
    return anchor + ((moment - anchor) // CYCLE) * CYCLE


def ts(moment: datetime) -> int:
    return int(moment.timestamp())


class RiftView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Rift alerts", emoji="🔔",
        style=discord.ButtonStyle.primary, custom_id="aion2:rift:ping",
    )
    async def toggle(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        guild = interaction.guild
        member = interaction.user
        if guild is None or not isinstance(member, discord.Member):
            await interaction.response.send_message("This only works in a server.", ephemeral=True)
            return
        role = discord.utils.get(guild.roles, name=PING_ROLE_NAME)
        try:
            if role is None:
                role = await guild.create_role(
                    name=PING_ROLE_NAME, mentionable=True, reason="Rift alerts role"
                )
            if role in member.roles:
                await member.remove_roles(role, reason="Rift alerts off")
                text = "🔕 Rift alerts are **off**. You won't be pinged."
            else:
                await member.add_roles(role, reason="Rift alerts on")
                text = "🔔 Rift alerts are **on**. You'll be pinged when a rift opens."
        except discord.Forbidden:
            text = "⚠️ I can't manage that role. An admin should move my role above it."
        await interaction.response.send_message(text, ephemeral=True)


class RiftsCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.state = load_state()
        self.message: discord.Message | None = None
        self.last_sig: tuple | None = None
        self.pinged: set[int] = set()

    @property
    def anchor(self) -> datetime:
        value = self.state.get("anchor")
        if value:
            return datetime.fromtimestamp(int(value), tz=timezone.utc)
        return DEFAULT_ANCHOR

    async def cog_load(self) -> None:
        self.bot.add_view(RiftView())
        self.rift_loop.start()

    async def cog_unload(self) -> None:
        self.rift_loop.cancel()

    async def get_rift_channel(self):
        channel = self.bot.get_channel(RIFT_EVENT_CHANNEL_ID)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(RIFT_EVENT_CHANNEL_ID)
            except discord.HTTPException:
                return None
        return channel if isinstance(channel, discord.abc.Messageable) else None

    def build_embed(self, now: datetime) -> tuple[discord.Embed, tuple]:
        opening = last_opening(self.anchor, now)
        elapsed = now - opening
        nxt = opening + CYCLE
        filled = min(BAR_LENGTH, int(elapsed / CYCLE * BAR_LENGTH))
        bar = "▰" * filled + "▱" * (BAR_LENGTH - filled)

        if elapsed < PORTAL:
            phase = "open"
            color = GREEN
            headline = "🟢 **RIFT OPEN!!**"
            detail = f"The portal closes <t:{ts(opening + PORTAL)}:R>. Get in now!"
        elif elapsed < EVENT:
            phase = "closed"
            color = RED
            headline = "🔴 **PORTAL CLOSED**"
            detail = f"The rift event ends <t:{ts(opening + EVENT)}:R>."
        else:
            phase = "waiting"
            color = GOLD
            headline = "🟡 **WAITING FOR THE NEXT RIFT**"
            detail = "Get ready. The portal opens at the time below."

        embed = discord.Embed(
            title="🌀 Spacetime Rift",
            description=f"{headline}\n{detail}",
            color=color,
        )
        embed.add_field(
            name="⏳ Next rift",
            value=f"<t:{ts(nxt)}:R>\n<t:{ts(nxt)}:t> your time",
            inline=True,
        )
        embed.add_field(name="📊 Cycle", value=f"`{bar}`", inline=True)
        upcoming = "\n".join(
            f"<t:{ts(nxt + CYCLE * i)}:t> · <t:{ts(nxt + CYCLE * i)}:R>" for i in range(4)
        )
        embed.add_field(name="📅 Upcoming rifts", value=upcoming, inline=False)
        embed.set_footer(text="Every 3 hours • portal open 10 min • event lasts 1 hour")
        return embed, (phase, filled, ts(opening), ts(self.anchor))

    async def get_panel_message(self) -> discord.Message | None:
        if self.message is not None:
            return self.message
        message_id = self.state.get("message_id")
        if not message_id:
            return None
        channel = await self.get_rift_channel()
        if channel is None:
            return None
        try:
            self.message = await channel.fetch_message(int(message_id))
        except discord.NotFound:
            self.state.pop("message_id", None)
            save_state(self.state)
            return None
        except discord.HTTPException:
            return None
        return self.message

    async def refresh_panel(self, force: bool = False) -> bool:
        message = await self.get_panel_message()
        if message is None:
            return False
        embed, sig = self.build_embed(discord.utils.utcnow())
        if not force and sig == self.last_sig:
            return True
        try:
            await message.edit(embed=embed, view=RiftView())
        except discord.NotFound:
            self.message = None
            self.state.pop("message_id", None)
            save_state(self.state)
            return False
        except discord.HTTPException as exc:
            log.warning("Could not update rift panel: %s", exc)
            return False
        self.last_sig = sig
        return True

    async def send_open_ping(self, now: datetime) -> None:
        opening = last_opening(self.anchor, now)
        key = ts(opening)
        if key in self.pinged or not (opening <= now < opening + GRACE):
            return
        self.pinged.add(key)
        self.pinged = {k for k in self.pinged if k >= key - 7200}
        channel = await self.get_rift_channel()
        if channel is None:
            return
        guild = getattr(channel, "guild", None)
        role = discord.utils.get(guild.roles, name=PING_ROLE_NAME) if guild else None
        mention = f"{role.mention} " if role else ""
        await channel.send(
            f"{mention}🌀 **RIFT OPEN!!** The portal closes <t:{ts(opening + PORTAL)}:R>.",
            allowed_mentions=discord.AllowedMentions(roles=[role] if role else False),
            delete_after=PORTAL.total_seconds(),
        )

    @tasks.loop(seconds=30)
    async def rift_loop(self) -> None:
        try:
            await self.send_open_ping(discord.utils.utcnow())
            await self.refresh_panel()
        except Exception:
            log.exception("Rift loop failed")

    @rift_loop.before_loop
    async def before_rift_loop(self) -> None:
        await self.bot.wait_until_ready()

    @app_commands.command(name="rift_panel", description="Post or refresh the live rift dashboard")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def rift_panel(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        channel = await self.get_rift_channel()
        if channel is None:
            await interaction.followup.send("⚠️ I can't find the rift channel.", ephemeral=True)
            return
        embed, sig = self.build_embed(discord.utils.utcnow())
        try:
            message = await self.get_panel_message()
            if message is not None:
                await message.edit(embed=embed, view=RiftView())
            else:
                message = await channel.send(embed=embed, view=RiftView())
        except discord.Forbidden:
            await interaction.followup.send(
                "⚠️ I can't send messages in the rift channel. Check my permissions there.",
                ephemeral=True,
            )
            return
        self.message = message
        self.last_sig = sig
        self.state["message_id"] = message.id
        save_state(self.state)
        await interaction.followup.send(f"✅ Rift dashboard ready in <#{RIFT_EVENT_CHANNEL_ID}>.", ephemeral=True)

    @app_commands.command(name="rift", description="Show when the next rift opens")
    @app_commands.guild_only()
    async def rift(self, interaction: discord.Interaction) -> None:
        embed, _ = self.build_embed(discord.utils.utcnow())
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="rift_set", description="Set the rift timer from the in-game countdown")
    @app_commands.describe(minutes_until_open="Minutes until the next rift opens, as shown in the game")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def rift_set(
        self, interaction: discord.Interaction, minutes_until_open: app_commands.Range[int, 0, 179]
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        anchor = discord.utils.utcnow() + timedelta(minutes=minutes_until_open)
        self.state["anchor"] = ts(anchor)
        save_state(self.state)
        self.pinged.clear()
        await self.refresh_panel(force=True)
        await interaction.followup.send(
            f"✅ Timer saved. The next rift opens <t:{ts(anchor)}:R> (<t:{ts(anchor)}:t> your time), then every 3 hours.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RiftsCog(bot))
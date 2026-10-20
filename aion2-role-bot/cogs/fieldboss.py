import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import timedelta

import discord
from discord import app_commands
from discord.ext import commands, tasks

log = logging.getLogger("fieldboss")

STATE_FILE = "fieldboss_state.json"
PING_ROLE_NAME = "Ping: Field Boss"
ALERT_MIN_HOURS = 6
ALERT_LEAD_SECONDS = 300
SOON_SECONDS = 600
FIELD_BOSS_CHANNEL_ID = int(os.getenv("FIELD_BOSS_CHANNEL_ID", "1561838977282216046"))

@dataclass(frozen=True)
class Boss:
    name: str
    hours: float
    region: str = "verteron"

    @property
    def key(self) -> str:
        return self.name if self.region == "verteron" else f"{self.region}:{self.name}"


VERTERON = [
    Boss("Blooming Korin", 1),
    Boss("Heretic Layla", 3),
    Boss("Black Tentacle Lawa", 2),
    Boss("Phantasm Kasia", 6),
    Boss("Centurion Demiros", 2),
    Boss("Divine Ansas", 6),
    Boss("Harvest Manager Moshav", 3),
    Boss("Sentinel K'nash", 2),
    Boss("Scholar Aulla", 2),
    Boss("Forest Warrior Aullamu", 2),
    Boss("Chaser Taulo", 2),
    Boss("Neikel of the East", 0.5),
    Boss("Researcher Setram", 6),
    Boss("Eternal Gartua", 12),
    Boss("Silent Dartan", 6),
    Boss("Soul Ruler Kashapa", 6),
    Boss("High Commander Lagta", 12),
    Boss("Rotten Kutar", 0.5),
    Boss("Kernon of the West", 0.5),
    Boss("Kusan the Mad Gladiator", 2),
    Boss("Ritualist Garshim", 2),
    Boss("Bodyguard Teegant", 1.5),
    Boss("Furious Saursus", 3),
    Boss("Bloodfang Pnyn", 3),
]

ALTGARD = [
    Boss("Black Warrior Aed", 0.5, "altgard"),
    Boss("Faithful Rajit", 0.5, "altgard"),
    Boss("Melted Danar", 0.5, "altgard"),
    Boss("Berserker Vargor", 1, "altgard"),
    Boss("Blood Warrior Lannar", 1.5, "altgard"),
    Boss("Predator Garsan", 2, "altgard"),
    Boss("Deceiver Trid", 2, "altgard"),
    Boss("Blue Wave Kelpina", 2, "altgard"),
    Boss("High Overseer Nutah", 2, "altgard"),
    Boss("Advisor Resana", 3, "altgard"),
    Boss("Special Operations Leader Linx", 3, "altgard"),
    Boss("Addicted Hardirun", 3, "altgard"),
    Boss("Veteran Shujakan", 3, "altgard"),
    Boss("Desecrator Newbold", 4, "altgard"),
    Boss("Specter Archon Axios", 4, "altgard"),
    Boss("Executioner Barthien", 4, "altgard"),
    Boss("Visionary Karuka", 4, "altgard"),
    Boss("Drakan Battalion Weapon Guruta", 6, "altgard"),
    Boss("Dark Shadow Vishwada", 6, "altgard"),
    Boss("Sharp Shylak", 6, "altgard"),
    Boss("Silent Dartan", 6, "altgard"),
    Boss("Soul Ruler Kashapa", 6, "altgard"),
    Boss("High Commander Lagta", 12, "altgard"),
    Boss("Immortal Gartua", 12, "altgard"),
]

REGIONS = {
    "verteron": {
        "title": "🐉 Verteron Field Bosses",
        "bosses": VERTERON,
        "select_id": "aion2:boss:select",
        "alerts_id": "aion2:boss:alerts",
    },
    "altgard": {
        "title": "🏔️ Altgard Field Bosses",
        "bosses": ALTGARD,
        "select_id": "aion2:boss:select:altgard",
        "alerts_id": "aion2:boss:alerts:altgard",
    },
}
for _region, _info in REGIONS.items():
    assert len(_info["bosses"]) <= 25, f"{_region} has too many bosses for one dropdown"

ALL_BOSSES = VERTERON + ALTGARD
BY_KEY = {b.key: b for b in ALL_BOSSES}
ORDER = {"up": 0, "soon": 1, "down": 2, "unknown": 3}


def fmt_hours(hours: float) -> str:
    h, m = divmod(int(round(hours * 60)), 60)
    if h and m:
        return f"{h}h {m}m"
    return f"{h}h" if h else f"{m}m"


def make_options(region: str) -> list[discord.SelectOption]:
    return [
        discord.SelectOption(
            label=b.name, value=b.key, description=f"Respawn {fmt_hours(b.hours)}"
        )
        for b in REGIONS[region]["bosses"]
    ]


def ts(moment) -> int:
    return int(moment.timestamp())


def parse_duration(text: str) -> timedelta | None:
    text = text.strip().lower()
    if not text:
        return None
    if ":" in text:
        try:
            parts = [int(p) for p in text.split(":")]
        except ValueError:
            return None
        if len(parts) == 2:
            parts.append(0)
        if len(parts) != 3:
            return None
        return timedelta(hours=parts[0], minutes=parts[1], seconds=parts[2])
    if text.isdigit():
        return timedelta(minutes=int(text))
    pairs = re.findall(r"(\d+)\s*([a-z]+)", text)
    if not pairs:
        return None
    total = timedelta()
    for number, unit in pairs:
        n = int(number)
        if unit[0] == "h":
            total += timedelta(hours=n)
        elif unit[0] == "m":
            total += timedelta(minutes=n)
        elif unit[0] == "s":
            total += timedelta(seconds=n)
        else:
            return None
    return total


class TimeModal(discord.ui.Modal):
    def __init__(self, cog: "FieldBossCog", boss: Boss, mode: str) -> None:
        super().__init__(title=boss.name[:45])
        self.cog = cog
        self.boss = boss
        self.mode = mode
        label = "Time left (as shown in-game)" if mode == "left" else "Killed how long ago?"
        self.amount = discord.ui.TextInput(
            label=label,
            placeholder="e.g. 2h 51m 4s, 39m 45s or 1:48:41",
            max_length=24,
        )
        self.add_item(self.amount)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        delta = parse_duration(self.amount.value)
        if delta is None or delta > timedelta(hours=24):
            await interaction.response.send_message(
                "⚠️ I couldn't read that. Try `2h 51m 4s`, `39m 45s` or `1:48:41`.",
                ephemeral=True,
            )
            return
        now_ts = ts(discord.utils.utcnow())
        secs = int(delta.total_seconds())
        if self.mode == "left":
            at = now_ts + secs
            note = f"time left set to **{self.amount.value.strip()}**"
        else:
            at = now_ts - secs + int(self.boss.hours * 3600)
            note = f"marked as killed **{self.amount.value.strip()} ago**"
        await self.cog.report(interaction, self.boss, at, note, via_modal=True)


class BossActionView(discord.ui.View):
    def __init__(self, cog: "FieldBossCog", boss: Boss) -> None:
        super().__init__(timeout=180)
        self.cog = cog
        self.boss = boss

    @discord.ui.button(label="Killed just now", emoji="☠️", style=discord.ButtonStyle.danger)
    async def killed(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        at = ts(discord.utils.utcnow()) + int(self.boss.hours * 3600)
        await self.cog.report(interaction, self.boss, at, "marked as **killed just now**")

    @discord.ui.button(label="Set time left", emoji="⏱️", style=discord.ButtonStyle.primary)
    async def time_left(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(TimeModal(self.cog, self.boss, "left"))

    @discord.ui.button(label="Killed ago", emoji="🕘", style=discord.ButtonStyle.secondary)
    async def killed_ago(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(TimeModal(self.cog, self.boss, "ago"))

    @discord.ui.button(label="Mark UP", emoji="🟢", style=discord.ButtonStyle.success)
    async def mark_up(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog.report(interaction, self.boss, ts(discord.utils.utcnow()), "marked as **UP now**")

    @discord.ui.button(label="Clear", emoji="⚪", style=discord.ButtonStyle.secondary)
    async def clear(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog.report(interaction, self.boss, None, "**cleared** (no data)")


class BossPanelView(discord.ui.View):
    def __init__(self, cog: "FieldBossCog", region: str) -> None:
        super().__init__(timeout=None)
        self.cog = cog
        self.region = region
        info = REGIONS[region]

        select = discord.ui.Select(
            placeholder="Pick a boss to report…",
            custom_id=info["select_id"],
            options=make_options(region),
            min_values=1,
            max_values=1,
        )
        select.callback = self.on_pick
        self.add_item(select)

        alerts = discord.ui.Button(
            label="Boss alerts",
            emoji="🔔",
            style=discord.ButtonStyle.primary,
            custom_id=info["alerts_id"],
        )
        alerts.callback = self.on_toggle
        self.add_item(alerts)

    async def on_pick(self, interaction: discord.Interaction) -> None:
        values = interaction.data.get("values") or []
        boss = BY_KEY.get(values[0]) if values else None
        if boss is None or interaction.guild_id is None:
            await interaction.response.send_message("Unknown boss.", ephemeral=True)
            return
        await interaction.response.edit_message(view=BossPanelView(self.cog, self.region))
        text = (
            f"**{boss.name}** · respawn {fmt_hours(boss.hours)}\n"
            f"Now: {self.cog.status_text(boss, interaction.guild_id)}\n\nWhat happened?"
        )
        await interaction.followup.send(text, view=BossActionView(self.cog, boss), ephemeral=True)

    async def on_toggle(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        member = interaction.user
        if guild is None or not isinstance(member, discord.Member):
            await interaction.response.send_message("This only works in a server.", ephemeral=True)
            return
        role = discord.utils.get(guild.roles, name=PING_ROLE_NAME)
        try:
            if role is None:
                role = await guild.create_role(
                    name=PING_ROLE_NAME, mentionable=True, reason="Field boss alerts role"
                )
            if role in member.roles:
                await member.remove_roles(role, reason="Boss alerts off")
                text = "🔕 Boss alerts are **off**."
            else:
                await member.add_roles(role, reason="Boss alerts on")
                text = (
                    "🔔 Boss alerts are **on**. You'll be pinged 5 minutes before "
                    f"bosses of {ALERT_MIN_HOURS}h or longer."
                )
        except discord.Forbidden:
            text = "⚠️ I can't manage that role. An admin should move my role above it."
        await interaction.response.send_message(text, ephemeral=True)


class FieldBossCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.state = self.load_state()
        self.messages: dict[tuple[int, str], discord.Message] = {}
        self.last_sig: dict[tuple[int, str], tuple] = {}
        self.alerted: set[tuple[int, str, int]] = set()

    def load_state(self) -> dict:
        try:
            with open(STATE_FILE, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            data = {}
        data.setdefault("guilds", {})
        return data

    def save_state(self) -> None:
        tmp = f"{STATE_FILE}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.state, fh)
        os.replace(tmp, STATE_FILE)

    def guild_data(self, guild_id: int) -> dict:
        data = self.state["guilds"].setdefault(str(guild_id), {})
        data.setdefault("bosses", {})
        data.setdefault("panels", {})
        return data

    async def cog_load(self) -> None:
        for region in REGIONS:
            self.bot.add_view(BossPanelView(self, region))
        self.boss_loop.start()

    async def cog_unload(self) -> None:
        self.boss_loop.cancel()

    async def migrate_legacy(self) -> None:
        old_bosses = self.state.pop("bosses", None)
        old_channel = self.state.pop("channel_id", None)
        old_message = self.state.pop("message_id", None)
        if not old_bosses and not old_channel:
            return
        guild_id = None
        if old_channel:
            channel = await self.get_channel(old_channel)
            guild = getattr(channel, "guild", None)
            if guild:
                guild_id = guild.id
        if guild_id is None and len(self.bot.guilds) == 1:
            guild_id = self.bot.guilds[0].id
        if guild_id is None:
            self.state["bosses"] = old_bosses or {}
            if old_channel:
                self.state["channel_id"] = old_channel
            if old_message:
                self.state["message_id"] = old_message
            return
        data = self.guild_data(guild_id)
        for key, value in (old_bosses or {}).items():
            data["bosses"].setdefault(key, value)
        if old_channel and "verteron" not in data["panels"]:
            data["panels"]["verteron"] = {"channel_id": old_channel, "message_id": old_message}
        self.save_state()
        log.info("Migrated legacy field boss state to guild %s", guild_id)

    def phase_of(self, boss: Boss, guild_id: int, now_ts: int) -> tuple[str, int | None]:
        entry = self.guild_data(guild_id)["bosses"].get(boss.key)
        if not entry:
            return "unknown", None
        at = int(entry["respawn_at"])
        if at <= now_ts:
            return "up", at
        if at - now_ts <= SOON_SECONDS:
            return "soon", at
        return "down", at

    def status_text(self, boss: Boss, guild_id: int) -> str:
        phase, at = self.phase_of(boss, guild_id, ts(discord.utils.utcnow()))
        entry = self.guild_data(guild_id)["bosses"].get(boss.key, {})
        who = f" (reported by <@{entry['by']}>)" if entry.get("by") else ""
        if phase == "unknown":
            return "no data yet"
        if phase == "up":
            return f"🟢 UP, spawned <t:{at}:R>{who}"
        return f"respawns <t:{at}:R> (<t:{at}:t> your time){who}"

    def build_embed(self, guild_id: int, region: str) -> tuple[discord.Embed, tuple]:
        now_ts = ts(discord.utils.utcnow())
        rows = [(*self.phase_of(b, guild_id, now_ts), b) for b in REGIONS[region]["bosses"]]
        rows.sort(key=lambda r: (ORDER[r[0]], r[1] or 0, r[2].name))
        lines = []
        for phase, at, boss in rows:
            if phase == "up":
                lines.append(f"🟢 **{boss.name}** · spawned <t:{at}:R>")
            elif phase == "soon":
                lines.append(f"🟡 **{boss.name}** · spawns <t:{at}:R>")
            elif phase == "down":
                lines.append(f"🔴 **{boss.name}** · <t:{at}:R> (<t:{at}:t>)")
            else:
                lines.append(f"⚪ **{boss.name}** · no data ({fmt_hours(boss.hours)} respawn)")
        phases = {r[0] for r in rows}
        color = (
            discord.Color.from_rgb(87, 242, 135) if "up" in phases
            else discord.Color.from_rgb(254, 190, 60) if "soon" in phases
            else discord.Color.blurple()
        )
        embed = discord.Embed(
            title=REGIONS[region]["title"],
            description="🟢 up · 🟡 under 10 min · 🔴 respawning · ⚪ no data\n\n" + "\n".join(lines),
            color=color,
        )
        embed.set_footer(text="Pick a boss below to report. Copy the in-game Time Left for the best accuracy.")
        sig = tuple((b.key, p, a) for p, a, b in rows)
        return embed, sig

    async def get_channel(self, channel_id):
        if not channel_id:
            return None
        channel = self.bot.get_channel(int(channel_id))
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(int(channel_id))
            except discord.HTTPException:
                return None
        return channel if isinstance(channel, discord.abc.Messageable) else None

    async def get_message(self, guild_id: int, region: str) -> discord.Message | None:
        cache_key = (guild_id, region)
        if cache_key in self.messages:
            return self.messages[cache_key]
        panel = self.guild_data(guild_id)["panels"].get(region)
        if not panel or not panel.get("message_id"):
            return None
        channel = await self.get_channel(panel.get("channel_id"))
        if channel is None:
            return None
        try:
            message = await channel.fetch_message(int(panel["message_id"]))
        except discord.NotFound:
            panel.pop("message_id", None)
            self.save_state()
            return None
        except discord.HTTPException:
            return None
        self.messages[cache_key] = message
        return message

    async def refresh_panel(self, guild_id: int, region: str, force: bool = False) -> None:
        message = await self.get_message(guild_id, region)
        if message is None:
            return
        embed, sig = self.build_embed(guild_id, region)
        cache_key = (guild_id, region)
        if not force and sig == self.last_sig.get(cache_key):
            return
        try:
            await message.edit(embed=embed, view=BossPanelView(self, region))
        except discord.NotFound:
            self.messages.pop(cache_key, None)
            self.guild_data(guild_id)["panels"].get(region, {}).pop("message_id", None)
            self.save_state()
        except discord.HTTPException as exc:
            log.warning("Could not update %s boss panel: %s", region, exc)
        else:
            self.last_sig[cache_key] = sig

    async def report(self, interaction: discord.Interaction, boss: Boss, at: int | None,
                     note: str, via_modal: bool = False) -> None:
        guild_id = interaction.guild_id
        if guild_id is None:
            await interaction.response.send_message("This only works in a server.", ephemeral=True)
            return
        bosses = self.guild_data(guild_id)["bosses"]
        if at is None:
            bosses.pop(boss.key, None)
        else:
            bosses[boss.key] = {"respawn_at": int(at), "by": interaction.user.id}
        self.save_state()
        text = f"✅ **{boss.name}** ({boss.region.title()}) {note}."
        if via_modal:
            await interaction.response.send_message(text, ephemeral=True)
        else:
            await interaction.response.edit_message(content=text, view=None)
        await self.refresh_panel(guild_id, boss.region, force=True)

    async def send_alerts(self, guild_id: int, now_ts: int) -> None:
        data = self.guild_data(guild_id)
        for boss in ALL_BOSSES:
            if boss.hours < ALERT_MIN_HOURS:
                continue
            entry = data["bosses"].get(boss.key)
            if not entry:
                continue
            at = int(entry["respawn_at"])
            key = (guild_id, boss.key, at)
            if key in self.alerted or not (0 < at - now_ts <= ALERT_LEAD_SECONDS):
                continue
            self.alerted.add(key)
            panel = data["panels"].get(boss.region) or next(iter(data["panels"].values()), None)
            channel = await self.get_channel(panel.get("channel_id")) if panel else None
            if channel is None:
                continue
            guild = getattr(channel, "guild", None)
            role = discord.utils.get(guild.roles, name=PING_ROLE_NAME) if guild else None
            mention = f"{role.mention} " if role else ""
            await channel.send(
                f"{mention}🐉 **{boss.name}** ({boss.region.title()}) spawns <t:{at}:R>!",
                allowed_mentions=discord.AllowedMentions(roles=[role] if role else False),
                delete_after=900,
            )
    async def ensure_panels(self) -> None:
        channel = await self.get_channel(FIELD_BOSS_CHANNEL_ID)
        guild = getattr(channel, "guild", None)
        if channel is None or guild is None:
            log.warning("Field boss channel %s not found or not accessible", FIELD_BOSS_CHANNEL_ID)
            return
        data = self.guild_data(guild.id)
        for name in REGIONS:
            panel = data["panels"].get(name, {})
            in_place = panel.get("channel_id") == channel.id and panel.get("message_id")
            if in_place and await self.get_message(guild.id, name) is not None:
                continue
            old = await self.get_message(guild.id, name)
            embed, sig = self.build_embed(guild.id, name)
            try:
                message = await channel.send(embed=embed, view=BossPanelView(self, name))
            except discord.HTTPException as exc:
                log.warning("Could not post %s panel: %s", name, exc)
                return
            if old is not None:
                try:
                    await old.delete()
                except discord.HTTPException:
                    pass
            self.messages[(guild.id, name)] = message
            self.last_sig[(guild.id, name)] = sig
            data["panels"][name] = {"channel_id": channel.id, "message_id": message.id}
        self.save_state()

    @tasks.loop(seconds=30)
    async def boss_loop(self) -> None:
        try:
            now_ts = ts(discord.utils.utcnow())
            self.alerted = {k for k in self.alerted if k[2] > now_ts - 3600}
            for guild_key in list(self.state["guilds"]):
                guild_id = int(guild_key)
                await self.send_alerts(guild_id, now_ts)
                for region in list(self.guild_data(guild_id)["panels"]):
                    if region in REGIONS:
                        await self.refresh_panel(guild_id, region)
        except Exception:
            log.exception("Boss loop failed")

    @boss_loop.before_loop
    async def before_boss_loop(self) -> None:
        await self.bot.wait_until_ready()
        try:
            await self.migrate_legacy()
        except Exception:
            log.exception("Legacy migration failed")
        await self.ensure_panels()

    @app_commands.command(name="boss_panel", description="Repost the field boss dashboards in #field-bosses")
    @app_commands.describe(region="Which dashboard to repost (default: both)")
    @app_commands.choices(region=[
        app_commands.Choice(name="Both", value="both"),
        app_commands.Choice(name="Verteron", value="verteron"),
        app_commands.Choice(name="Altgard", value="altgard"),
    ])
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def boss_panel(self, interaction: discord.Interaction,
                         region: app_commands.Choice[str] | None = None) -> None:
        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild_id
        target = await self.get_channel(FIELD_BOSS_CHANNEL_ID) or interaction.channel
        wanted = list(REGIONS) if region is None or region.value == "both" else [region.value]
        data = self.guild_data(guild_id)

        for name in wanted:
            old = await self.get_message(guild_id, name)
            embed, sig = self.build_embed(guild_id, name)
            try:
                message = await target.send(embed=embed, view=BossPanelView(self, name))
            except discord.Forbidden:
                await interaction.followup.send(
                    "⚠️ I can't send messages in the field boss channel. Check my permissions there.",
                    ephemeral=True,
                )
                return
            if old is not None:
                try:
                    await old.delete()
                except discord.HTTPException:
                    pass
            self.messages[(guild_id, name)] = message
            self.last_sig[(guild_id, name)] = sig
            data["panels"][name] = {"channel_id": target.id, "message_id": message.id}

        self.save_state()
        await interaction.followup.send(
            f"✅ Posted {', '.join(n.title() for n in wanted)} in <#{target.id}>.", ephemeral=True
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(FieldBossCog(bot))
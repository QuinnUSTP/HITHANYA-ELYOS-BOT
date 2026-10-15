import logging
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import ABYSS_EVENT_CHANNEL_ID, EVENT_CHANNEL_ID

log = logging.getLogger("events")

SERVER_TZ = timezone(timedelta(hours=9))

SCHEDULE = [
    {
        "name": "⚔️ Artifact Siege",
        "weekdays": (0, 3, 5),
        "times": [(21, 0)],
        "duration_min": 40,
        "channel_id": ABYSS_EVENT_CHANNEL_ID,
        "location": "Abyss, Lower Reshanta",
        "description": "Large-scale faction PvP for the Abyss Artifact. Preparation starts at the event time, and the fight begins 10 minutes later.",
        "horizon_days": 7,
        "remind_min": 15,
        "ping_role": "Ping: Siege & Fortress",
        "remind_text": "Preparation begins at the start time, and the fight starts 10 minutes later.",
    },
]


def occurrences(now_utc: datetime, item: dict, lead_minutes: int):
    now_server = now_utc.astimezone(SERVER_TZ)
    limit = now_utc + timedelta(days=item["horizon_days"])
    earliest = now_utc + timedelta(minutes=lead_minutes)
    for offset in range(item["horizon_days"] + 2):
        day = (now_server + timedelta(days=offset)).date()
        if day.weekday() not in item["weekdays"]:
            continue
        for hour, minute in item["times"]:
            start = datetime(day.year, day.month, day.day, hour, minute, tzinfo=SERVER_TZ)
            if earliest < start <= limit:
                yield start


class EventsCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.reminded: set[tuple[str, int]] = set()

    async def cog_load(self) -> None:
        self.sync_loop.start()
        self.reminder_loop.start()

    async def cog_unload(self) -> None:
        self.sync_loop.cancel()
        self.reminder_loop.cancel()

    async def create_event(self, guild: discord.Guild, item: dict, start: datetime) -> None:
        channel = guild.get_channel(item["channel_id"]) if item["channel_id"] else None
        kwargs = {
            "name": item["name"],
            "start_time": start,
            "end_time": start + timedelta(minutes=item["duration_min"]),
            "description": item["description"],
            "privacy_level": discord.PrivacyLevel.guild_only,
            "reason": "AION2 automatic event",
        }
        if isinstance(channel, (discord.StageChannel, discord.VoiceChannel)):
            kwargs["channel"] = channel
        else:
            kwargs["entity_type"] = discord.EntityType.external
            kwargs["location"] = item["location"]
        await guild.create_scheduled_event(**kwargs)

    async def sync_guild(self, guild: discord.Guild) -> tuple[int, int, str]:
        try:
            existing = await guild.fetch_scheduled_events()
        except discord.HTTPException as exc:
            log.warning("Could not read events in %s: %s", guild.id, exc)
            return 0, 0, str(exc)

        have = {(e.name, int(e.start_time.timestamp())) for e in existing}
        created = failed = 0
        last_error = ""
        now = discord.utils.utcnow()
        for item in SCHEDULE:
            for start in occurrences(now, item, lead_minutes=2):
                if (item["name"], int(start.timestamp())) in have:
                    continue
                try:
                    await self.create_event(guild, item, start)
                    created += 1
                except discord.HTTPException as exc:
                    failed += 1
                    last_error = str(exc)
                    log.warning("Could not create event %s: %s", item["name"], exc)
        return created, failed, last_error

    @tasks.loop(minutes=30)
    async def sync_loop(self) -> None:
        try:
            for guild in self.bot.guilds:
                await self.sync_guild(guild)
        except Exception:
            log.exception("Event sync failed")

    @sync_loop.before_loop
    async def before_sync(self) -> None:
        await self.bot.wait_until_ready()

    def reminder_channel(self, item: dict) -> discord.TextChannel | None:
        channel = self.bot.get_channel(item["channel_id"]) if item["channel_id"] else None
        if isinstance(channel, discord.TextChannel):
            return channel
        fallback = self.bot.get_channel(EVENT_CHANNEL_ID) if EVENT_CHANNEL_ID else None
        return fallback if isinstance(fallback, discord.TextChannel) else None

    @tasks.loop(minutes=1)
    async def reminder_loop(self) -> None:
        try:
            now = discord.utils.utcnow()
            self.reminded = {k for k in self.reminded if k[1] > now.timestamp() - 3600}
            for item in SCHEDULE:
                channel = self.reminder_channel(item)
                if channel is None:
                    continue
                role = discord.utils.get(channel.guild.roles, name=item["ping_role"])
                for start in occurrences(now, item, lead_minutes=0):
                    key = (item["name"], int(start.timestamp()))
                    minutes_left = (start - now).total_seconds() / 60
                    if key in self.reminded or minutes_left > item["remind_min"]:
                        continue
                    self.reminded.add(key)
                    mention = f"{role.mention} " if role else ""
                    await channel.send(
                        f"{mention}**{item['name']}** starts <t:{key[1]}:R> (<t:{key[1]}:t> your time). {item['remind_text']}",
                        allowed_mentions=discord.AllowedMentions(roles=[role] if role else False),
                    )
        except Exception:
            log.exception("Reminder failed")

    @reminder_loop.before_loop
    async def before_reminder(self) -> None:
        await self.bot.wait_until_ready()

    @app_commands.command(name="sync_events", description="Create the upcoming AION2 events now")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def sync_events(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        created, failed, error = await self.sync_guild(interaction.guild)
        text = f"✅ Created {created} new event(s)."
        if failed:
            text += f"\n⚠️ {failed} failed. Last error: {error[:300]}"
        await interaction.followup.send(text, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(EventsCog(bot))
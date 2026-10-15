import logging
import discord
from discord import app_commands
from discord.ext import commands

from config import ALLOWED_CHANNEL_ID
from services.channel_guard import ChannelLockedView
from services.role_manager import check_role_assignable, apply_single_choice

log = logging.getLogger("roles")

ROLE_PREFIX = {"class": "", "language": "Lang: ", "notification": "Ping: "}

SELECT_INFO = {
    "class": ("⚔️ Main class", 0),
    "legion": ("🏯 Legion", 1),
    "language": ("🌐 Language", 2),
}


def make_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🎭 Welcome to AION2 | Roles!",
        description=(
            "**Class**, **Legion**, and **language**: pick from the menus. "
            "A new pick replaces the old one.\n"
            "**Pings**: tap a button to turn that notification on or off. "
            "Tap again to turn it off."
        ),
        color=discord.Color.from_rgb(47, 49, 54),
    )
    embed.set_footer(text="AION2 • Legion is self-selected and is not verified in-game")
    return embed


async def build_options(db, guild_id: int) -> dict[str, list[discord.SelectOption]]:
    result: dict[str, list[discord.SelectOption]] = {}
    for category in SELECT_INFO:
        opts = []
        for m in await db.list_mappings(guild_id, category):
            if m["role_id"] is None and not (category == "legion" and m["item_key"] == "none"):
                continue
            opts.append(
                discord.SelectOption(
                    label=m["display_name"][:100],
                    value=m["item_key"],
                    emoji=m["emoji"],
                )
            )
        result[category] = opts[:25]
    return result


class CategorySelect(discord.ui.Select):
    def __init__(self, category: str, options: list[discord.SelectOption] | None = None) -> None:
        placeholder, row = SELECT_INFO[category]
        super().__init__(
            custom_id=f"aion2:{category}",
            placeholder=placeholder,
            min_values=1,
            max_values=1,
            options=options or [discord.SelectOption(label="Not set up yet", value="none-set")],
            row=row,
        )
        self.category = category

    async def callback(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        member = interaction.user
        if guild is None or not isinstance(member, discord.Member):
            await interaction.response.send_message("This only works inside a server.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        db = interaction.client.db

        all_maps = await db.list_mappings(guild.id, self.category, enabled_only=False)
        chosen = next(
            (m for m in all_maps if m["item_key"] == self.values[0] and m["enabled"]), None
        )
        if chosen is None:
            await interaction.followup.send(
                "That option is no longer available. Ask an admin to refresh the panel.",
                ephemeral=True,
            )
            return

        managed = [guild.get_role(m["role_id"]) for m in all_maps if m["role_id"]]
        managed = [r for r in managed if r is not None]

        target = None
        if chosen["role_id"]:
            target = guild.get_role(chosen["role_id"])
            if target is None:
                await interaction.followup.send(
                    "That role was deleted. Ask an admin to run /setup_roles.", ephemeral=True
                )
                return
            problem = check_role_assignable(guild, target)
            if problem:
                await interaction.followup.send(f"⚠️ {problem}", ephemeral=True)
                return

        error = await apply_single_choice(
            member, managed, target, reason=f"AION2 role panel: {self.category}"
        )
        if error:
            await interaction.followup.send(f"⚠️ {error}", ephemeral=True)
            return

        label = f"{chosen['emoji'] or ''} {chosen['display_name']}".strip()
        if target is None:
            msg = f"✅ Your {self.category} selection was cleared ({label})."
        else:
            msg = f"✅ Your {self.category} is now **{label}**."
        await interaction.followup.send(msg, ephemeral=True)


class RolePanelView(ChannelLockedView):
    def __init__(self, options: dict[str, list[discord.SelectOption]] | None = None) -> None:
        super().__init__(timeout=None)
        options = options or {}
        for category in SELECT_INFO:
            self.add_item(CategorySelect(category, options.get(category)))
        # Phase 4 adds notification buttons on rows 3 and 4.


class RolesCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.bot.add_view(RolePanelView())

    async def refresh_panel(self, guild: discord.Guild) -> bool:
        settings = await self.bot.db.get_guild_settings(guild.id)
        if not settings or not settings["panel_message_id"]:
            return False
        channel = guild.get_channel(settings["roles_channel_id"])
        if not isinstance(channel, discord.TextChannel):
            return False
        try:
            message = await channel.fetch_message(settings["panel_message_id"])
            options = await build_options(self.bot.db, guild.id)
            await message.edit(embed=make_embed(), view=RolePanelView(options))
            return True
        except (discord.NotFound, discord.Forbidden):
            return False

    async def ensure_roles(self, guild: discord.Guild) -> list[str]:
        created = []
        for category, prefix in ROLE_PREFIX.items():
            for m in await self.bot.db.list_mappings(guild.id, category, enabled_only=False):
                existing = guild.get_role(m["role_id"]) if m["role_id"] else None
                if existing:
                    continue
                name = f"{prefix}{m['display_name']}"
                role = discord.utils.get(guild.roles, name=name)
                if role is None:
                    role = await guild.create_role(
                        name=name,
                        permissions=discord.Permissions.none(),
                        mentionable=(category == "notification"),
                        reason="AION2 role bot setup",
                    )
                    created.append(name)
                await self.bot.db.set_mapping_role(guild.id, category, m["item_key"], role.id)
        return created
        

    @app_commands.command(name="setup_roles", description="Create or refresh the role-selection panel")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def setup_roles(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        db = self.bot.db

        await db.ensure_guild(guild.id)
        await db.seed_defaults(guild.id)

        try:
            created = await self.ensure_roles(guild)
        except discord.Forbidden:
            await interaction.followup.send(
                "⚠️ I can't create roles. Give my role **Manage Roles** and try again.", ephemeral=True
            )
            return

        channel = guild.get_channel(ALLOWED_CHANNEL_ID) if ALLOWED_CHANNEL_ID else interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.followup.send("⚠️ The roles channel was not found.", ephemeral=True)
            return

        perms = channel.permissions_for(guild.me)
        if not (perms.view_channel and perms.send_messages and perms.embed_links):
            await interaction.followup.send(
                f"⚠️ I need View Channel, Send Messages, and Embed Links in {channel.mention}.",
                ephemeral=True,
            )
            return

        embed = make_embed()
        view = RolePanelView(await build_options(db, guild.id))

        settings = await db.get_guild_settings(guild.id)
        message = None
        if settings and settings["panel_message_id"] and settings["roles_channel_id"] == channel.id:
            try:
                message = await channel.fetch_message(settings["panel_message_id"])
                await message.edit(embed=embed, view=view)
            except discord.NotFound:
                message = None

        if message is None:
            message = await channel.send(embed=embed, view=view)

        await db.set_panel(guild.id, channel.id, message.id)
        await db.log(guild.id, interaction.user.id, "setup_roles", f"created={created}")
        log.info("Panel published in guild %s by %s", guild.id, interaction.user.id)

        note = f" Created roles: {', '.join(created)}." if created else ""
        await interaction.followup.send(f"✅ Panel ready in {channel.mention}.{note}", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RolesCog(bot))
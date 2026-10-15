import logging
import re
import time
import io
from typing import Optional
import discord
from discord import app_commands
from discord.ext import commands

from config import ADVERTISE_CHANNEL_ID, ALLOWED_CHANNEL_ID, LEGION_LEADER_ROLE_ID
from database import slugify
from services.channel_guard import ChannelLockedView
from services.role_manager import apply_single_choice, check_role_assignable

log = logging.getLogger("legions")

NAME_RE = re.compile(r"^[\w \-\[\]'.!&]{2,32}$")
INVITE_RE = re.compile(
    r"^https://(www\.)?(discord\.gg/|discord(app)?\.com/invite/)[A-Za-z0-9-]{2,32}/?$"
)
MAX_REAL_LEGIONS = 24
COOLDOWN_SECONDS = 30


def one_line(text: str | None) -> str:
    return " ".join((text or "").split())

FOCUS_COLORS = [
    ("hardcore", discord.Color.from_rgb(255, 140, 0)),
    ("pvp", discord.Color.from_rgb(237, 66, 69)),
    ("pve", discord.Color.from_rgb(88, 101, 242)),
    ("casual", discord.Color.from_rgb(87, 242, 135)),
]

LANGUAGE_FLAGS = {
    "english": "🇺🇸",
    "filipino": "🇵🇭",
    "tagalog": "🇵🇭",
    "indonesian": "🇮🇩",
    "thai": "🇹🇭",
    "vietnamese": "🇻🇳",
    "korean": "🇰🇷",
    "japanese": "🇯🇵",
    "chinese": "🇨🇳",
}


def focus_color(focus: str | None) -> discord.Color:
    text = (focus or "").lower()
    for word, color in FOCUS_COLORS:
        if word in text:
            return color
    return discord.Color.gold()


def decorate_language(language: str) -> str:
    text = language.lower()
    for word, flag in LANGUAGE_FLAGS.items():
        if word in text:
            return f"{flag} {language}"
    return f"🌐 {language}"


def quote(text: str, limit: int = 900) -> str:
    text = text.strip()
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return "\n".join(f"> {line}" if line.strip() else ">" for line in text.splitlines())

MAX_LOGO_BYTES = 2 * 1024 * 1024


def detect_image_ext(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None

class LegionModal(discord.ui.Modal, title="Register your Legion"):
    def __init__(self, cog: "LegionsCog", existing: dict | None) -> None:
        super().__init__()
        self.cog = cog
        d = existing or {}
        self.name_in = discord.ui.TextInput(
            label="Legion name", min_length=2, max_length=32,
            default=d.get("name"), placeholder="Example: MAHARLIKA",
        )
        self.focus_in = discord.ui.TextInput(
            label="Focus (optional)", required=False, max_length=60,
            default=d.get("focus"), placeholder="PvP / PvE / Casual / Hardcore",
        )
        self.lang_in = discord.ui.TextInput(
            label="Language (optional)", required=False, max_length=40,
            default=d.get("language"), placeholder="English, Filipino",
        )
        self.desc_in = discord.ui.TextInput(
            label="Description (optional)", required=False, max_length=500,
            style=discord.TextStyle.paragraph, default=d.get("description"),
            placeholder="Tell players about your Legion",
        )
        self.invite_in = discord.ui.TextInput(
            label="Discord invite link (optional)", required=False, max_length=100,
            default=d.get("invite_url"), placeholder="https://discord.gg/yourcode",
        )
        for item in (self.name_in, self.focus_in, self.lang_in, self.desc_in, self.invite_in):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.handle_submission(
            interaction,
            name=one_line(self.name_in.value),
            focus=one_line(self.focus_in.value),
            language=one_line(self.lang_in.value),
            description=(self.desc_in.value or "").strip(),
            invite=one_line(self.invite_in.value),
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        log.exception("Legion modal error", exc_info=error)
        text = "⚠️ Something went wrong. The error was logged."
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)


class ConfirmRemoveView(discord.ui.View):
    def __init__(self, cog: "LegionsCog", leader_id: int) -> None:
        super().__init__(timeout=60)
        self.cog = cog
        self.leader_id = leader_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.leader_id

    @discord.ui.button(label="Yes, remove it", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=True)
        message = await self.cog.remove_legion(interaction.guild, interaction.user)
        await interaction.edit_original_response(content=message, view=None)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(content="Cancelled.", view=None)
        self.stop()


class RegistrationView(ChannelLockedView):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Register / edit my Legion", emoji="📝",
        style=discord.ButtonStyle.primary, custom_id="aion2:legion:register",
    )
    async def register(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        cog: LegionsCog = interaction.client.get_cog("LegionsCog")
        await cog.open_modal(interaction)

    @discord.ui.button(
        label="Remove my Legion", emoji="🗑️",
        style=discord.ButtonStyle.secondary, custom_id="aion2:legion:remove",
    )
    async def remove(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        cog: LegionsCog = interaction.client.get_cog("LegionsCog")
        profile = await cog.bot.db.get_profile(interaction.guild_id, interaction.user.id)
        if not profile:
            await interaction.response.send_message("You haven't registered a Legion.", ephemeral=True)
            return
        await interaction.response.send_message(
            f"Remove **{profile['name']}**? This deletes its role and its advertisement.",
            view=ConfirmRemoveView(cog, interaction.user.id),
            ephemeral=True,
        )


class LegionsCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.last_submit: dict[int, float] = {}

    async def cog_load(self) -> None:
        self.bot.add_view(RegistrationView())

    def leader_allowed(self, member: discord.Member) -> bool:
        if not LEGION_LEADER_ROLE_ID:
            return True
        return any(r.id == LEGION_LEADER_ROLE_ID for r in member.roles)

    async def open_modal(self, interaction: discord.Interaction) -> None:
        member = interaction.user
        if interaction.guild is None or not isinstance(member, discord.Member):
            await interaction.response.send_message("This only works in a server.", ephemeral=True)
            return
        if not self.leader_allowed(member):
            await interaction.response.send_message(
                "Only members with the Legion Leader role can register a Legion.", ephemeral=True
            )
            return
        existing = await self.bot.db.get_profile(interaction.guild.id, member.id)
        await interaction.response.send_modal(LegionModal(self, existing))

    async def handle_submission(
        self, interaction: discord.Interaction, *,
        name: str, focus: str, language: str, description: str, invite: str,
    ) -> None:
        guild = interaction.guild
        member = interaction.user
        db = self.bot.db
        await interaction.response.defer(ephemeral=True)

        wait = COOLDOWN_SECONDS - (time.monotonic() - self.last_submit.get(member.id, 0))
        if wait > 0:
            await interaction.followup.send(f"⏳ Please wait {int(wait) + 1}s and try again.", ephemeral=True)
            return
        self.last_submit[member.id] = time.monotonic()

        if not NAME_RE.match(name):
            await interaction.followup.send(
                "⚠️ The name must be 2 to 32 characters: letters, numbers, spaces, and - [ ] ' . ! & only.",
                ephemeral=True,
            )
            return

        warnings: list[str] = []
        if invite:
            if not INVITE_RE.match(invite):
                await interaction.followup.send(
                    "⚠️ Use a link like `https://discord.gg/yourcode`, or leave it empty.", ephemeral=True
                )
                return
            try:
                found = await self.bot.fetch_invite(invite, with_expiration=True)
                if found.expires_at is not None:
                    warnings.append("Your invite will expire. A permanent invite works better.")
            except discord.NotFound:
                await interaction.followup.send("⚠️ That invite is invalid or expired.", ephemeral=True)
                return
            except discord.HTTPException:
                warnings.append("I couldn't check your invite link, so it was saved as is.")

        await db.ensure_guild(guild.id)
        await db.seed_defaults(guild.id)

        existing = await db.get_profile(guild.id, member.id)
        key = existing["legion_key"] if existing else slugify(name)
        new_slug = slugify(name)
        legions = await db.list_mappings(guild.id, "legion", enabled_only=False)

        if new_slug == "none" or any(
            m["item_key"] != key and new_slug in (m["item_key"], slugify(m["display_name"]))
            for m in legions
        ):
            await interaction.followup.send("⚠️ A Legion with that name already exists.", ephemeral=True)
            return

        real_count = sum(1 for m in legions if m["item_key"] != "none")
        if not existing and real_count >= MAX_REAL_LEGIONS:
            await interaction.followup.send(
                "⚠️ The Legion list is full. Ask an admin for help.", ephemeral=True
            )
            return

        role_name = f"Legion: {name}"
        role = None
        if existing:
            mapping = next((m for m in legions if m["item_key"] == key), None)
            if mapping and mapping["role_id"]:
                role = guild.get_role(mapping["role_id"])
        try:
            if role is None:
                role = await guild.create_role(
                    name=role_name,
                    permissions=discord.Permissions.none(),
                    hoist=True,
                    reason=f"Legion registered by {member}",
                )
            elif role.name != role_name or not role.hoist:
                await role.edit(name=role_name, hoist=True, reason=f"Legion updated by {member}")
        except discord.Forbidden:
            await interaction.followup.send(
                "⚠️ I can't manage roles. An admin needs to check my permissions.", ephemeral=True
            )
            return

        problem = check_role_assignable(guild, role)
        if problem:
            await interaction.followup.send(f"⚠️ {problem}", ephemeral=True)
            return

        await db.upsert_mapping(guild.id, "legion", key, name, role.id, "🏯")
        await db.save_profile(
            guild.id, member.id, key, name,
            focus or None, language or None, description or None, invite or None,
        )

        managed = [guild.get_role(m["role_id"]) for m in legions if m["role_id"]]
        managed = [r for r in managed if r is not None] + [role]
        error = await apply_single_choice(member, managed, role, reason="Legion leader registration")
        if error:
            warnings.append(error)

        profile = await db.get_profile(guild.id, member.id)
        ad_ok = await self.publish_ad(guild, member, profile)
        if not ad_ok:
            warnings.append("I couldn't post the advertisement. An admin should check the ad channel permissions.")

        roles_cog = self.bot.get_cog("RolesCog")
        if roles_cog:
            await roles_cog.refresh_panel(guild)

        await db.log(guild.id, member.id, "legion_saved", f"{name} role={role.id}")
        log.info("Legion %s saved by %s in guild %s", name, member.id, guild.id)

        verb = "updated" if existing else "registered"
        extra = "\n" + "\n".join(f"⚠️ {w}" for w in warnings) if warnings else ""
        await interaction.followup.send(
            f"✅ **{name}** {verb}. It's now in the Legion dropdown and advertised.{extra}",
            ephemeral=True,
        )

    async def publish_ad(self, guild: discord.Guild, leader: discord.Member, profile: dict) -> bool:
        channel = guild.get_channel(ADVERTISE_CHANNEL_ID) if ADVERTISE_CHANNEL_ID else None
        if not isinstance(channel, discord.TextChannel):
            return False

        name = discord.utils.escape_markdown(profile["name"])
        about = profile["description"] or "This Legion hasn't written a description yet."

        embed = discord.Embed(
            title=f"🏯  {name}",
            description=quote(discord.utils.escape_mentions(about)),
            color=focus_color(profile["focus"]),
            timestamp=discord.utils.utcnow(),
        )
        embed.set_author(name="LEGION RECRUITMENT")

        logo = await self.bot.db.get_logo(guild.id, leader.id)
        if logo:
            embed.set_thumbnail(url=f"attachment://{logo['filename']}")

        def make_file() -> discord.File | None:
            if not logo:
                return None
            return discord.File(io.BytesIO(logo["data"]), filename=logo["filename"])

        embed.add_field(name="👑 Leader", value=leader.mention, inline=True)
        if profile["focus"]:
            embed.add_field(
                name="⚔️ Focus",
                value=discord.utils.escape_mentions(profile["focus"]),
                inline=True,
            )
        if profile["language"]:
            embed.add_field(
                name="🗣️ Language",
                value=discord.utils.escape_mentions(decorate_language(profile["language"])),
                inline=True,
            )

        how_to = "Pick this Legion from the **Legion** menu"
        if ALLOWED_CHANNEL_ID:
            how_to += f" in <#{ALLOWED_CHANNEL_ID}>"
        how_to += " to get its role."
        embed.add_field(name="📌 How to join", value=how_to, inline=False)
        embed.set_footer(text="Self-registered • not verified in-game")

        view = discord.ui.View()
        if profile["invite_url"]:
            view.add_item(
                discord.ui.Button(
                    label="Join Discord",
                    emoji="🔗",
                    style=discord.ButtonStyle.link,
                    url=profile["invite_url"],
                )
            )

        no_pings = discord.AllowedMentions.none()
        try:
            if profile["ad_message_id"]:
                try:
                    message = await channel.fetch_message(profile["ad_message_id"])
                    file = make_file()
                    await message.edit(
                        embed=embed,
                        view=view,
                        attachments=[file] if file else [],
                        allowed_mentions=no_pings,
                    )
                    return True
                except discord.NotFound:
                    pass

            file = make_file()
            kwargs = {"embed": embed, "view": view, "allowed_mentions": no_pings}
            if file:
                kwargs["file"] = file
            message = await channel.send(**kwargs)
            await self.bot.db.set_ad_message(guild.id, leader.id, message.id)
            return True
        except (discord.Forbidden, discord.HTTPException) as exc:
            log.warning("Could not publish ad: %s", exc)
            return False

    async def remove_legion(self, guild: discord.Guild, member: discord.Member) -> str:
        db = self.bot.db
        profile = await db.get_profile(guild.id, member.id)
        if not profile:
            return "You haven't registered a Legion."

        channel = guild.get_channel(ADVERTISE_CHANNEL_ID) if ADVERTISE_CHANNEL_ID else None
        if isinstance(channel, discord.TextChannel) and profile["ad_message_id"]:
            try:
                msg = await channel.fetch_message(profile["ad_message_id"])
                await msg.delete()
            except (discord.NotFound, discord.Forbidden):
                pass

        legions = await db.list_mappings(guild.id, "legion", enabled_only=False)
        mapping = next((m for m in legions if m["item_key"] == profile["legion_key"]), None)
        if mapping and mapping["role_id"]:
            role = guild.get_role(mapping["role_id"])
            if role:
                try:
                    await role.delete(reason=f"Legion removed by {member}")
                except discord.Forbidden:
                    log.warning("Could not delete role %s", role.id)

        await db.delete_mapping(guild.id, "legion", profile["legion_key"])
        await db.delete_profile(guild.id, member.id)
        await db.log(guild.id, member.id, "legion_removed", profile["name"])

        roles_cog = self.bot.get_cog("RolesCog")
        if roles_cog:
            await roles_cog.refresh_panel(guild)
        return f"🗑️ **{profile['name']}** was removed."
    @app_commands.command(name="hoist_legions", description="Show each Legion as its own group in the member list")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def hoist_legions(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        mappings = await self.bot.db.list_mappings(guild.id, "legion", enabled_only=False)

        changed, failed = 0, 0
        for m in mappings:
            if m["item_key"] == "none" or not m["role_id"]:
                continue
            role = guild.get_role(m["role_id"])
            if role is None or role.hoist:
                continue
            try:
                await role.edit(hoist=True, reason=f"Legion grouping by {interaction.user}")
                changed += 1
            except discord.HTTPException:
                failed += 1

        text = f"✅ {changed} Legion role(s) now show separately."
        if failed:
            text += f" ⚠️ {failed} failed. Check that my role is above them."
        await interaction.followup.send(text, ephemeral=True)
    @app_commands.command(name="legion_logo", description="Set or remove your Legion logo (optional)")
    @app_commands.describe(
        image="PNG, JPG, WEBP, or GIF, up to 2 MB",
        remove="Remove your current logo",
    )
    @app_commands.guild_only()
    async def legion_logo(
        self,
        interaction: discord.Interaction,
        image: Optional[discord.Attachment] = None,
        remove: bool = False,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        member = interaction.user
        db = self.bot.db

        profile = await db.get_profile(guild.id, member.id)
        if not profile:
            await interaction.followup.send(
                "⚠️ Register your Legion first with the **Register / edit my Legion** button.",
                ephemeral=True,
            )
            return

        if remove:
            await db.delete_logo(guild.id, member.id)
            await self.publish_ad(guild, member, profile)
            await interaction.followup.send("🗑️ Your logo was removed. The ad has no picture now.", ephemeral=True)
            return

        if image is None:
            await interaction.followup.send(
                "Attach an image to this command, or use `remove: True` to remove your logo.",
                ephemeral=True,
            )
            return

        if image.size > MAX_LOGO_BYTES:
            await interaction.followup.send("⚠️ That image is over 2 MB. Please use a smaller one.", ephemeral=True)
            return

        try:
            data = await image.read()
        except discord.HTTPException:
            await interaction.followup.send("⚠️ I couldn't download that image. Try again.", ephemeral=True)
            return

        ext = detect_image_ext(data)
        if ext is None:
            await interaction.followup.send("⚠️ Use a PNG, JPG, WEBP, or GIF image.", ephemeral=True)
            return

        await db.save_logo(guild.id, member.id, f"logo.{ext}", data)
        ok = await self.publish_ad(guild, member, profile)
        await db.log(guild.id, member.id, "legion_logo_set", profile["name"])

        if ok:
            await interaction.followup.send("✅ Logo saved. Your ad was updated.", ephemeral=True)
        else:
            await interaction.followup.send(
                "✅ Logo saved, but I couldn't update the ad. An admin should check the ad channel permissions.",
                ephemeral=True,
            )

    @app_commands.command(
        name="setup_legion_registration",
        description="Post or refresh the Legion registration message",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def setup_legion_registration(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        channel = guild.get_channel(ALLOWED_CHANNEL_ID) if ALLOWED_CHANNEL_ID else interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.followup.send("⚠️ The roles channel was not found.", ephemeral=True)
            return

        embed = discord.Embed(
            title="🏯 Legion Leaders: register here",
            description=(
                "Press **Register / edit my Legion** and fill in the form.\n"
                "Your Legion is added to the **Legion** menu above, "
                "and an advertisement is posted for recruits.\n"
                "Want a logo? Use `/legion_logo` and attach an image (optional).\n"
                "A Discord invite link is optional. Press the button again to edit later."
            ),
            color=discord.Color.from_rgb(47, 49, 54),
        )
        embed.set_footer(text="Legions are self-registered and not verified in-game")

        await self.bot.db.ensure_guild(guild.id)
        panel = await self.bot.db.get_extra_panel(guild.id, "registration")
        message = None
        if panel and panel["channel_id"] == channel.id:
            try:
                message = await channel.fetch_message(panel["message_id"])
                await message.edit(embed=embed, view=RegistrationView())
            except discord.NotFound:
                message = None
        if message is None:
            message = await channel.send(embed=embed, view=RegistrationView())

        await self.bot.db.set_extra_panel(guild.id, "registration", channel.id, message.id)
        await self.bot.db.log(guild.id, interaction.user.id, "setup_legion_registration")
        await interaction.followup.send(f"✅ Registration message ready in {channel.mention}.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(LegionsCog(bot))
import logging
import discord

log = logging.getLogger("role_manager")

DANGEROUS_PERMISSIONS = (
    "administrator", "manage_guild", "manage_roles", "manage_channels",
    "manage_webhooks", "manage_messages", "manage_nicknames",
    "kick_members", "ban_members", "moderate_members", "mention_everyone",
)


def check_role_assignable(guild: discord.Guild, role: discord.Role) -> str | None:
    if role.is_default():
        return "The @everyone role cannot be used."
    if role.managed:
        return f"{role.name} is managed by an integration and cannot be assigned."
    if role >= guild.me.top_role:
        return f"{role.name} is above the bot's role. Move the bot's role higher."
    for name in DANGEROUS_PERMISSIONS:
        if getattr(role.permissions, name, False):
            return f"{role.name} has the '{name}' permission and cannot be self-assigned."
    return None


async def apply_single_choice(
    member: discord.Member,
    managed_roles: list[discord.Role],
    target: discord.Role | None,
    reason: str,
) -> str | None:
    """Keep only `target` among `managed_roles`. Returns an error message or None."""
    managed_ids = {r.id for r in managed_roles}
    to_remove = [r for r in member.roles if r.id in managed_ids and (target is None or r.id != target.id)]
    needs_add = target is not None and target not in member.roles

    try:
        if to_remove:
            await member.remove_roles(*to_remove, reason=reason)
        if needs_add:
            await member.add_roles(target, reason=reason)
    except discord.Forbidden:
        log.warning("Forbidden changing roles for %s", member.id)
        return "I don't have permission to change that role. An admin needs to move my role higher."
    except discord.HTTPException as exc:
        log.error("HTTP error changing roles: %s", exc)
        return "Discord had a problem. Please try again in a moment."
    return None
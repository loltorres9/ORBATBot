"""Game roles from the browser.

Like `web/service.py` for events, this holds no rules of its own: the checks on
what may become a game role, the emoji validation, the add/remove calls and the
panel refresh all come from `cogs/gameroles.py`. A `ValueError` raised here is a
message meant for the user.
"""

import discord
from discord.ext import commands

from cogs.gameroles import (
    MAX_GAME_ROLES,
    GameRolePanelView,
    _apply_role_changes,
    _build_panel_embed,
    _change_summary,
    _parse_emoji,
    _resolve_game_roles,
    _role_rejection,
    _update_game_role_panel,
)
from utils import database
from web.guilds import postable_channels

MAX_ROLE_NAME = 100
MAX_DESCRIPTION = 100


def _plain(text: str) -> str:
    """Strip Discord's markdown — these strings are written for a chat message,
    and asterisks and backticks would show up literally on a web page."""
    return text.replace('**', '').replace('`', '')


def can_assign(guild: discord.Guild) -> bool:
    """Whether the bot can hand out roles at all. `guild.me` is Optional in
    discord.py, so it is checked rather than assumed."""
    return guild.me is not None and guild.me.guild_permissions.manage_roles


def _needs_manage_roles(guild: discord.Guild) -> None:
    if not can_assign(guild):
        raise ValueError(
            "I need the Manage Roles permission on this server to hand out game roles. "
            "Ask a server admin to grant it."
        )


async def role_entries(guild: discord.Guild, member: discord.Member) -> list:
    """Every game role, with whether this member currently holds it.

    Registrations whose Discord role was deleted are pruned by
    `_resolve_game_roles()` on the way through, exactly as in Discord.
    """
    held = set(member.roles)
    return [
        {'row': row, 'role': role, 'held': role in held}
        for row, role in await _resolve_game_roles(guild)
    ]


async def set_member_roles(guild: discord.Guild, member: discord.Member,
                           chosen_ids: list) -> str:
    """Set the member's game roles to exactly what they ticked.

    Ids that aren't registered game roles are ignored rather than trusted, so a
    hand-edited form can't be used to grant an arbitrary role.
    """
    _needs_manage_roles(guild)

    by_id = {str(role.id): role for _, role in await _resolve_game_roles(guild)}
    chosen = {str(value) for value in chosen_ids} & set(by_id)
    held = set(member.roles)

    to_add = [role for role_id, role in by_id.items() if role_id in chosen and role not in held]
    to_remove = [role for role_id, role in by_id.items() if role_id not in chosen and role in held]

    if not to_add and not to_remove:
        return "Nothing changed — your game roles are already set that way."

    error = await _apply_role_changes(member, to_add, to_remove)
    if error:
        raise ValueError(_plain(error).lstrip('❌ '))
    return _plain(_change_summary(to_add, to_remove).replace('\n', ' '))


async def add_role(bot: commands.Bot, guild: discord.Guild, actor: discord.Member,
                   name: str, emoji: str, description: str) -> list:
    """Register a game role, creating the Discord role if it doesn't exist."""
    name = (name or '').strip()
    if not name:
        raise ValueError("Give the role a name.")
    if len(name) > MAX_ROLE_NAME:
        raise ValueError(f"Role names can be at most {MAX_ROLE_NAME} characters.")

    _needs_manage_roles(guild)
    try:
        emoji_str = _parse_emoji(bot, emoji) if (emoji or '').strip() else None
    except ValueError as e:
        raise ValueError(_plain(str(e)))

    existing = await _resolve_game_roles(guild)
    already = discord.utils.find(lambda entry: entry[1].name == name, existing)
    if already is None and len(existing) >= MAX_GAME_ROLES:
        raise ValueError(
            f"There are already {MAX_GAME_ROLES} game roles, which is as many as a Discord "
            "picker can show. Remove one first."
        )

    role = discord.utils.get(guild.roles, name=name)
    created = False
    if role is None:
        try:
            role = await guild.create_role(
                name=name,
                permissions=discord.Permissions.none(),
                mentionable=True,
                reason=f'Game role added by {actor} from the web UI',
            )
            created = True
        except discord.Forbidden:
            raise ValueError(
                "Discord wouldn't let me create that role — check that I have Manage Roles."
            )
        except discord.HTTPException as e:
            raise ValueError(f"Could not create the role: {e}")
    else:
        rejection = _role_rejection(guild, role)
        if rejection:
            raise ValueError(_plain(rejection))

    await database.add_game_role(
        str(guild.id), str(role.id), name, emoji_str,
        (description or '').strip()[:MAX_DESCRIPTION] or None,
    )
    await _update_game_role_panel(bot, guild)

    notes = [f"{name} is now a self-assignable game role."]
    if created:
        notes.append("Created it with no permissions, mentionable by everyone.")
    else:
        notes.append("Reused the existing role — it grants no permissions.")
        if not role.mentionable:
            notes.append(
                "It isn't mentionable, so nobody can ping it until you enable that "
                "in the role's settings."
            )
    return notes


async def remove_role(bot: commands.Bot, guild: discord.Guild, actor: discord.Member,
                      role_id: str, delete_role: bool) -> list:
    """Unregister a game role, optionally deleting the Discord role with it."""
    if not (role_id or '').isdigit():
        raise ValueError("That isn't a role I know.")

    role = guild.get_role(int(role_id))
    removed = await database.remove_game_role(str(guild.id), str(role_id))
    if not removed:
        raise ValueError("That role isn't registered as a game role.")

    name = role.name if role else 'The role'
    notes = [f"{name} is no longer self-assignable."]
    if delete_role and role is not None:
        try:
            await role.delete(reason=f'Game role deleted by {actor} from the web UI')
            notes.append("The Discord role was deleted, so everyone who had it has lost it.")
        except (discord.Forbidden, discord.HTTPException) as e:
            notes.append(f"I couldn't delete the Discord role itself: {e}")
    elif role is not None:
        notes.append("The Discord role still exists — members who have it keep it.")

    await _update_game_role_panel(bot, guild)
    return notes


async def post_panel(bot: commands.Bot, guild: discord.Guild, channel_id: str) -> str:
    """Post the self-assign panel, replacing wherever it was before."""
    if not (channel_id or '').strip():
        raise ValueError("Pick a channel to post the panel in.")
    channel = guild.get_channel(int(channel_id)) if str(channel_id).isdigit() else None
    if channel is None or channel not in postable_channels(guild):
        raise ValueError("I can't post in that channel — pick another one.")

    entries = await _resolve_game_roles(guild)
    try:
        msg = await channel.send(
            embed=_build_panel_embed(entries), view=GameRolePanelView(bot)
        )
    except (discord.Forbidden, discord.HTTPException) as e:
        raise ValueError(f"Discord wouldn't let me post there: {e}")

    await database.save_game_role_panel(str(guild.id), str(channel.id), str(msg.id))
    return (
        f"Panel posted in #{channel.name}. It updates itself whenever a game role "
        "is added or removed."
    )


async def panel_location(guild: discord.Guild) -> str:
    """Where the live panel currently sits, for showing on the page."""
    stored = await database.get_game_role_panel(str(guild.id))
    if not stored:
        return ''
    channel = guild.get_channel(int(stored['channel_id']))
    return f"#{channel.name}" if channel else 'a channel I can no longer see'

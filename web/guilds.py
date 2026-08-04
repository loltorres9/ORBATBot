"""Resolving the signed-in user against the bot's live view of each guild.

The web session only proves *who* someone is. What they may do is decided here,
from the same `discord.Member` the slash commands see — so the web UI and the
bot can never drift apart on permissions.
"""

import time
from typing import Optional

import discord
from discord.ext import commands

from cogs.admin import _is_unit_leader_or_admin
from cogs.events import _is_organiser

# `Intents.default()` has no members intent, so `guild.get_member()` is often
# empty and the member has to be fetched over REST. One request per guild per
# page view would be wasteful and rate-limit prone, hence a short TTL cache.
# The flip side is that a role change can take up to this long to show up.
_MEMBER_TTL = 60
_member_cache: dict = {}


def _cache_get(key):
    hit = _member_cache.get(key)
    if hit and hit[0] > time.monotonic():
        return hit[1]
    _member_cache.pop(key, None)
    return None


def _cache_put(key, value):
    # Bound the cache so a long-running process can't grow it without limit.
    if len(_member_cache) > 2000:
        _member_cache.clear()
    _member_cache[key] = (time.monotonic() + _MEMBER_TTL, value)


async def resolve_member(bot: commands.Bot, guild: discord.Guild,
                         user_id: str) -> Optional[discord.Member]:
    """The user as a member of *guild*, or None if they aren't in it."""
    key = (guild.id, str(user_id))
    cached = _cache_get(key)
    if cached is not None:
        return cached or None      # False is cached for "not a member"

    member = guild.get_member(int(user_id))
    if member is None:
        try:
            member = await guild.fetch_member(int(user_id))
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            member = None
    _cache_put(key, member or False)
    return member


def forget_member(guild_id, user_id) -> None:
    """Drop a cached member — used after an action, so a permission change the
    user just made in Discord isn't hidden behind the TTL."""
    _member_cache.pop((int(guild_id), str(user_id)), None)


async def user_guilds(bot: commands.Bot, user_id: str) -> list:
    """Every guild the bot and the user are both in, with that member object."""
    found = []
    for guild in bot.guilds:
        member = await resolve_member(bot, guild, user_id)
        if member is not None:
            found.append({'guild': guild, 'member': member})
    found.sort(key=lambda item: item['guild'].name.lower())
    return found


def is_admin(member: discord.Member) -> bool:
    """Server admin — the same bar `@app_commands.default_permissions(manage_guild=True)`
    puts on the game-role management commands."""
    perms = member.guild_permissions
    return perms.manage_guild or perms.administrator


def can_create_events(member: discord.Member) -> bool:
    return _is_unit_leader_or_admin(member)


def can_manage_event(member: discord.Member, event) -> bool:
    return _is_organiser(member, event)


def postable_channels(guild: discord.Guild) -> list:
    """Text channels the bot can actually post an event into."""
    me = guild.me
    if me is None:
        return []
    channels = [
        channel for channel in guild.text_channels
        if channel.permissions_for(me).send_messages
        and channel.permissions_for(me).embed_links
    ]
    channels.sort(key=lambda c: (c.category.position if c.category else -1, c.position))
    return channels


def mentionable_roles(guild: discord.Guild) -> list:
    """Roles offered as ping targets — everything except @everyone and the
    managed integration roles nobody wants to ping."""
    roles = [r for r in guild.roles if not r.is_default() and not r.managed]
    roles.sort(key=lambda r: r.position, reverse=True)
    return roles

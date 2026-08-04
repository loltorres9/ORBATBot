"""Announcements when someone joins, leaves, is kicked, banned or unbanned.

Two things decide whether this does anything at all:

* **The members intent.** `on_member_join` and `on_member_remove` are privileged.
  `bot.py` only requests the intent when `MEMBER_EVENTS` is set, because
  requesting one that isn't ticked in the Developer Portal makes the bot fail to
  start — so joins and leaves stay silent until it is deliberately switched on.
  Bans and unbans need no privileged intent and work either way.
* **A configured channel.** Nothing is posted until `log_settings.channel_id` is
  set for the guild, which the web UI does.
"""

import asyncio
from datetime import timedelta

import discord
from discord.ext import commands

from utils import database

# Audit-log entries appear a moment after the event, and their timestamps are
# close but not identical to it. Anything inside this window counts as the same
# action; beyond it, an unrelated older kick would be mistaken for this one.
AUDIT_WINDOW = timedelta(seconds=20)

# How long to wait before reading the audit log, so the entry exists by then.
AUDIT_DELAY = 2

COLOR_JOIN = discord.Color.green()
COLOR_LEAVE = discord.Color.light_grey()
COLOR_KICK = discord.Color.orange()
COLOR_BAN = discord.Color.red()
COLOR_UNBAN = discord.Color.blue()


def _stamp(moment) -> str:
    """A Discord timestamp, which every client shows in the viewer's own zone."""
    if moment is None:
        return 'unknown'
    return f"<t:{int(moment.timestamp())}:F> (<t:{int(moment.timestamp())}:R>)"


def _user_line(user) -> str:
    return f"{user.mention}\n`{user}` · `{user.id}`"


class MemberLogCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # guild id -> {invite code: use count}, kept so a join can be matched to
        # the invite whose counter went up.
        self._invites: dict = {}
        # Guarded because two joins seconds apart would otherwise both re-read
        # the invite list and each credit the other's increment.
        self._invite_lock = asyncio.Lock()

    # -- settings -----------------------------------------------------------

    async def _channel_for(self, guild: discord.Guild, kind: str):
        """The log channel for one kind of event, or None if it is switched off."""
        settings = await database.get_log_settings(str(guild.id))
        if not settings or not settings['channel_id'] or not settings[f'log_{kind}']:
            return None
        channel = guild.get_channel(int(settings['channel_id']))
        if channel is None or not channel.permissions_for(guild.me).send_messages:
            return None
        return channel

    async def _send(self, guild: discord.Guild, kind: str, embed: discord.Embed):
        channel = await self._channel_for(guild, kind)
        if channel is None:
            return
        try:
            await channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            pass

    # -- invite tracking ----------------------------------------------------

    async def _snapshot_invites(self, guild: discord.Guild) -> dict:
        """Current use counts, or an empty mapping when we may not read them."""
        try:
            return {invite.code: (invite.uses or 0) for invite in await guild.invites()}
        except (discord.Forbidden, discord.HTTPException):
            return {}

    async def _cache_invites(self, guild: discord.Guild):
        settings = await database.get_log_settings(str(guild.id))
        if settings and not settings['track_invites']:
            return
        self._invites[guild.id] = await self._snapshot_invites(guild)

    async def _used_invite(self, guild: discord.Guild):
        """Which invite a join came through, as (code, inviter) — best effort.

        Works by diffing use counts against the cached snapshot. Two people
        joining in the same instant can't be told apart this way; the counter is
        still corrected, only the attribution of that one join may be wrong.
        """
        settings = await database.get_log_settings(str(guild.id))
        if settings and not settings['track_invites']:
            return None, None

        async with self._invite_lock:
            before = self._invites.get(guild.id, {})
            try:
                invites = await guild.invites()
            except (discord.Forbidden, discord.HTTPException):
                return None, None

            after = {invite.code: (invite.uses or 0) for invite in invites}
            self._invites[guild.id] = after

            for invite in invites:
                if (invite.uses or 0) > before.get(invite.code, 0):
                    return invite.code, invite.inviter

        # No counter moved: either a vanity URL, or the member was added by a
        # bot, or the snapshot was stale.
        if 'VANITY_URL' in guild.features:
            try:
                vanity = await guild.vanity_invite()
                if vanity is not None:
                    return f"{vanity.code} (vanity)", None
            except (discord.Forbidden, discord.HTTPException):
                pass
        return None, None

    @commands.Cog.listener()
    async def on_ready(self):
        for guild in self.bot.guilds:
            await self._cache_invites(guild)

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        await self._cache_invites(guild)

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite):
        if invite.guild is not None:
            self._invites.setdefault(invite.guild.id, {})[invite.code] = invite.uses or 0

    @commands.Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite):
        if invite.guild is not None:
            self._invites.get(invite.guild.id, {}).pop(invite.code, None)

    # -- audit log ----------------------------------------------------------

    async def _audit_entry(self, guild: discord.Guild, action, target_id: int):
        """The most recent matching audit entry for *target_id*, if it is fresh.

        Needs View Audit Log; without it the events still fire, they just can't
        say who did it — which is why a missing entry is not an error.
        """
        try:
            async for entry in guild.audit_logs(limit=8, action=action):
                if entry.target is not None and entry.target.id == target_id:
                    if discord.utils.utcnow() - entry.created_at <= AUDIT_WINDOW:
                        return entry
                    return None
        except (discord.Forbidden, discord.HTTPException):
            return None
        return None

    # -- events -------------------------------------------------------------

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        try:
            code, inviter = await self._used_invite(member.guild)
            embed = discord.Embed(
                title='📥 Member joined',
                description=_user_line(member),
                color=COLOR_JOIN,
                timestamp=discord.utils.utcnow(),
            )
            embed.add_field(name='Account created', value=_stamp(member.created_at), inline=False)
            if code:
                invited_by = f" · invited by {inviter.mention}" if inviter else ''
                embed.add_field(name='Invite used', value=f"`{code}`{invited_by}", inline=False)
            embed.set_footer(text=f"{member.guild.member_count} members")
            if member.display_avatar:
                embed.set_thumbnail(url=member.display_avatar.url)
            await self._send(member.guild, 'join', embed)
        except Exception as e:
            print(f"❌ memberlog on_member_join failed: {e}")

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        """Fires for a voluntary leave, a kick and a ban alike — the audit log is
        what tells them apart."""
        try:
            await asyncio.sleep(AUDIT_DELAY)

            # A ban also removes the member; on_member_ban reports that one, so
            # this path must stay quiet or every ban would be logged twice.
            if await self._audit_entry(member.guild, discord.AuditLogAction.ban, member.id):
                return

            kick = await self._audit_entry(member.guild, discord.AuditLogAction.kick, member.id)
            roles = [role.mention for role in member.roles if not role.is_default()]

            embed = discord.Embed(
                title='👢 Member kicked' if kick else '📤 Member left',
                description=_user_line(member),
                color=COLOR_KICK if kick else COLOR_LEAVE,
                timestamp=discord.utils.utcnow(),
            )
            if kick:
                embed.add_field(name='Kicked by', value=kick.user.mention if kick.user else 'unknown')
                if kick.reason:
                    embed.add_field(name='Reason', value=kick.reason[:1024], inline=False)
            embed.add_field(name='Joined', value=_stamp(member.joined_at), inline=False)
            if roles:
                value = ', '.join(roles)
                embed.add_field(
                    name=f'Roles ({len(roles)})',
                    value=value[:1021] + '...' if len(value) > 1024 else value,
                    inline=False,
                )
            embed.set_footer(text=f"{member.guild.member_count} members")
            if member.display_avatar:
                embed.set_thumbnail(url=member.display_avatar.url)

            await self._send(member.guild, 'kick' if kick else 'leave', embed)
        except Exception as e:
            print(f"❌ memberlog on_member_remove failed: {e}")

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User):
        try:
            await asyncio.sleep(AUDIT_DELAY)
            entry = await self._audit_entry(guild, discord.AuditLogAction.ban, user.id)

            embed = discord.Embed(
                title='🔨 Member banned',
                description=_user_line(user),
                color=COLOR_BAN,
                timestamp=discord.utils.utcnow(),
            )
            if entry:
                embed.add_field(name='Banned by', value=entry.user.mention if entry.user else 'unknown')
                if entry.reason:
                    embed.add_field(name='Reason', value=entry.reason[:1024], inline=False)
            if user.display_avatar:
                embed.set_thumbnail(url=user.display_avatar.url)
            await self._send(guild, 'ban', embed)
        except Exception as e:
            print(f"❌ memberlog on_member_ban failed: {e}")

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User):
        try:
            await asyncio.sleep(AUDIT_DELAY)
            entry = await self._audit_entry(guild, discord.AuditLogAction.unban, user.id)

            embed = discord.Embed(
                title='🕊️ Member unbanned',
                description=_user_line(user),
                color=COLOR_UNBAN,
                timestamp=discord.utils.utcnow(),
            )
            if entry and entry.user:
                embed.add_field(name='Unbanned by', value=entry.user.mention)
            await self._send(guild, 'unban', embed)
        except Exception as e:
            print(f"❌ memberlog on_member_unban failed: {e}")


async def setup(bot: commands.Bot):
    await bot.add_cog(MemberLogCog(bot))

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
from collections import namedtuple
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

# How many invite_create audit entries to walk when looking for who made a link.
# One page; the walk caches every code it passes, so one scan usually answers
# every future join too.
AUDIT_INVITE_SCAN = 100

# How recently an invite must have been deleted for a join to be credited to it.
# Discord deletes a link the moment it hits `max_uses`, so the one that let this
# member in can be gone before its counter is ever seen to move.
CONSUMED_WINDOW = timedelta(seconds=15)

# What a join could be told about the invite it came through. `code` is set
# when it was worked out; otherwise `reason` says why not, so the join message
# can print that instead of silently dropping the field — a missing line is
# indistinguishable from a bot that was never given Manage Server.
Attribution = namedtuple('Attribution', 'code inviter kind reason')

NO_ATTRIBUTION = Attribution(None, None, None, None)

# Why an invite could not be named. `off` is the one case that prints nothing:
# the admin switched tracking off, so saying so on every join is just noise.
REASONS = {
    'forbidden': "Unknown — I need **Manage Server** to read the invite list.",
    'nocache': "Unknown — I hadn't read the invite list yet when they joined.",
    'unmoved': ('Unknown — no invite counter moved. Added by another bot, found '
                'through server discovery, or two people joined in the same moment.'),
}

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
        # invite code -> who created it. Kept separately because a consumed
        # single-use link is gone from the API by the time we look it up.
        self._inviters: dict = {}
        # invite code -> (guild id, when it went), for the same reason.
        self._deleted: dict = {}
        # Codes already credited to a join, so the second half of the race can't
        # hand the same consumed link to the next person through the door.
        self._spent: dict = {}
        # invite code -> who made it, from the audit log. A code is immutable and
        # so is its creator, so this never needs invalidating. Only successful
        # reads are recorded, a miss included; a failed read stays absent so a
        # permission granted later is picked up.
        self._creators: dict = {}

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

    async def _fetch_invites(self, guild: discord.Guild):
        """The guild's invites, or **None** when the list may not be read.

        None and an empty list have to stay distinct: a guild really can have no
        invites, while a missing Manage Server means we know nothing. Diffing a
        join against an empty snapshot would read every existing invite as
        freshly incremented and credit the first one it saw.
        """
        try:
            return await guild.invites()
        except (discord.Forbidden, discord.HTTPException):
            return None

    async def _snapshot_invites(self, guild: discord.Guild):
        """Just the use counts, which is all the cache between joins needs."""
        invites = await self._fetch_invites(guild)
        if invites is None:
            return None
        return {invite.code: (invite.uses or 0) for invite in invites}

    async def _cache_invites(self, guild: discord.Guild):
        settings = await database.get_log_settings(str(guild.id))
        if settings and not settings['track_invites']:
            return
        self._invites[guild.id] = await self._snapshot_invites(guild)

    async def _used_invite(self, guild: discord.Guild) -> Attribution:
        """Which invite a join came through, and who made it — best effort.

        Naming the link and naming its creator are two lookups against two
        different permissions, so they are kept apart: `_match_invite()` needs
        Manage Server, and the audit-log fallback below needs View Audit Log.
        Either can succeed without the other.
        """
        settings = await database.get_log_settings(str(guild.id))
        if settings and not settings['track_invites']:
            return NO_ATTRIBUTION._replace(reason='off')

        attribution = await self._match_invite(guild)
        # A vanity URL is a guild setting, not an invite anybody created, so it
        # has no invite_create entry and scanning for one is a wasted call.
        if attribution.code and attribution.inviter is None and attribution.kind != 'vanity':
            # A live invite carries its creator, so this is for the link that is
            # already gone — the single-use one an admin made for one person,
            # which is exactly the case where who sent it is the useful part.
            inviter = await self._invite_creator(guild, attribution.code)
            if inviter is not None:
                attribution = attribution._replace(inviter=inviter)
        return attribution

    async def _invite_creator(self, guild: discord.Guild, code: str):
        """Who created *code*, from the audit log — None if it can't be found.

        Needs View Audit Log, and the log only reaches back 90 days, so a miss
        is ordinary rather than an error. A read that fails is not cached, so a
        permission granted afterwards takes effect on the next join.
        """
        if code in self._creators:
            return self._creators[code]
        try:
            async for entry in guild.audit_logs(
                limit=AUDIT_INVITE_SCAN, action=discord.AuditLogAction.invite_create
            ):
                seen = getattr(entry.target, 'code', None)
                if seen:
                    self._creators.setdefault(seen, entry.user)
        except (discord.Forbidden, discord.HTTPException):
            return None
        # Recorded even when absent: a code the log does not reach must not cost
        # a scan on every join that comes through it.
        return self._creators.setdefault(code, None)

    async def _match_invite(self, guild: discord.Guild) -> Attribution:
        """Which invite a join came through, by diffing use counts.

        Two people joining in the same instant can't be told apart this way; the
        counter is still corrected, only the attribution of that one join may be
        wrong.
        """
        async with self._invite_lock:
            before = self._invites.get(guild.id)
            invites = await self._fetch_invites(guild)
            if invites is None:
                return NO_ATTRIBUTION._replace(reason='forbidden')
            after = {invite.code: (invite.uses or 0) for invite in invites}
            self._invites[guild.id] = after

            if before is None:
                return NO_ATTRIBUTION._replace(reason='nocache')

            for invite in invites:
                if (invite.uses or 0) > before.get(invite.code, 0):
                    return Attribution(invite.code, invite.inviter, 'invite', None)

            # A link with `max_uses` reached is deleted by Discord the moment it
            # is used, so its counter is never seen to move — the code is simply
            # gone. Two paths find it, because INVITE_DELETE and GUILD_MEMBER_ADD
            # race: if the delete arrived first the code is in `_deleted`, and if
            # it has not arrived yet the code is still in `before` but already
            # absent from the list we just fetched.
            #
            # Both are bounded to the seconds around this join. An unbounded
            # "gone since the last snapshot" would credit a link an admin tidied
            # up yesterday to the next person through the door.
            gone = {code for code in before if code not in after}
            gone |= self._recently_deleted(guild)
            if len(gone) == 1:
                code = gone.pop()
                inviter = self._inviters.get(code)
                # Spent, so nothing credits it twice. Both halves of the race
                # can still see it — the diff above found it before its
                # INVITE_DELETE arrived, and that event lands moments later —
                # and without this the next join inside the window would be
                # credited to a link that is already accounted for.
                self._spend(code)
                return Attribution(code, inviter, 'consumed', None)

        # No counter moved: either a vanity URL, or the member was added by a
        # bot, or the snapshot was stale.
        if 'VANITY_URL' in guild.features:
            try:
                vanity = await guild.vanity_invite()
                if vanity is not None:
                    return Attribution(vanity.code, None, 'vanity', None)
            except (discord.Forbidden, discord.HTTPException):
                pass
        return NO_ATTRIBUTION._replace(reason='unmoved')

    def _invite_field(self, attribution: Attribution, labels: dict) -> str:
        """The body of the "Invite used" field, however the lookup turned out."""
        if not attribution.code:
            return REASONS.get(attribution.reason, REASONS['unmoved'])

        parts = [f"`{attribution.code}`"]
        # The label says where that link was published — the whole point of
        # keeping it, so nobody has to look the code up in a spreadsheet.
        if labels.get(attribution.code):
            parts.append(f"**{labels[attribution.code]}**")
        if attribution.kind == 'vanity':
            parts.append('vanity URL')
        elif attribution.kind == 'consumed':
            parts.append('single-use link, used up')
        if attribution.inviter:
            # A shared link is *created by* somebody and used by many; a consumed
            # one was made for the person who just walked through it.
            made = 'invited by' if attribution.kind == 'consumed' else 'created by'
            parts.append(f"{made} {attribution.inviter.mention}")
        return ' · '.join(parts)

    @commands.Cog.listener()
    async def on_ready(self):
        for guild in self.bot.guilds:
            await self._cache_invites(guild)

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        await self._cache_invites(guild)

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite):
        if invite.guild is None:
            return
        if invite.inviter is not None:
            self._inviters[invite.code] = invite.inviter
        cached = self._invites.get(invite.guild.id)
        if cached is not None:
            cached[invite.code] = invite.uses or 0

    @commands.Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite):
        if invite.guild is None:
            return
        cached = self._invites.get(invite.guild.id)
        if cached is not None:
            cached.pop(invite.code, None)
        if invite.code in self._spent:
            return
        # Remembered briefly: dropping it from the snapshot alone is what made a
        # used-up single-use link unattributable, because the join it let in is
        # handled moments later.
        self._deleted[invite.code] = (invite.guild.id, discord.utils.utcnow())

    def _spend(self, code: str):
        """Mark a consumed code as accounted for, so no later join reuses it."""
        self._deleted.pop(code, None)
        self._inviters.pop(code, None)
        self._spent[code] = discord.utils.utcnow()

    def _recently_deleted(self, guild: discord.Guild) -> set:
        """Codes this guild lost in the last few seconds, pruning the older ones."""
        now = discord.utils.utcnow()
        for code, (guild_id, when) in list(self._deleted.items()):
            if now - when > CONSUMED_WINDOW:
                del self._deleted[code]
                self._inviters.pop(code, None)
        for code, when in list(self._spent.items()):
            if now - when > CONSUMED_WINDOW:
                del self._spent[code]
        return {
            code for code, (guild_id, _) in self._deleted.items()
            if guild_id == guild.id
        }

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
            attribution = await self._used_invite(member.guild)
            embed = discord.Embed(
                title='📥 Member joined',
                description=_user_line(member),
                color=COLOR_JOIN,
                timestamp=discord.utils.utcnow(),
            )
            embed.add_field(name='Account created', value=_stamp(member.created_at), inline=False)
            if attribution.reason != 'off':
                # Only worth a query once there is a code to look up.
                labels = (await database.get_invite_labels(str(member.guild.id))
                          if attribution.code else {})
                embed.add_field(
                    name='Invite used',
                    value=self._invite_field(attribution, labels),
                    inline=False,
                )
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

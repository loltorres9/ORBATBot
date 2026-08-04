"""How long members spend in voice channels.

A *visit* is one stay in one voice channel. A visit is split into **counted
intervals**: counting pauses whenever the rules stop applying — the member ends
up alone, or the channel is excluded — and resumes when they do again. Each
interval is a row in `voice_sessions`, so the sum over rows is time that
actually counted, not time the member was merely connected.

Voice state updates are **not** a privileged intent: `Intents.default()` already
carries them, and no extra server permission is needed.
"""

import asyncio

import discord
from discord.ext import commands, tasks

from utils import database

# How often an open interval's heartbeat is refreshed. A hard crash therefore
# costs at most this much unrecorded time instead of the whole interval, and a
# live total can include the interval somebody is in right now.
HEARTBEAT_SECONDS = 300

# The settings are read on every voice event; a short cache keeps that off the
# database without making a change on the web page take noticeably long to apply.
SETTINGS_TTL = 30


def format_duration(seconds) -> str:
    """`2h 13m`, `47m`, `35s` — short enough for a leaderboard cell."""
    seconds = int(seconds or 0)
    if seconds < 60:
        return f"{seconds}s"
    hours, rest = divmod(seconds, 3600)
    minutes = rest // 60
    if hours and minutes:
        return f"{hours}h {minutes}m"
    if hours:
        return f"{hours}h"
    return f"{minutes}m"


def parse_excluded(raw: str) -> set:
    return {part.strip() for part in (raw or '').split(',') if part.strip()}


class VoiceLogCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # (guild id, member id) -> the visit in progress
        self._visits: dict = {}
        # Voice events for one channel arrive in quick succession; serialising
        # keeps two of them from both deciding whether a channel is "alone".
        self._lock = asyncio.Lock()
        self._settings_cache: dict = {}
        self.heartbeat.start()

    def cog_unload(self):
        self.heartbeat.cancel()

    # -- settings -----------------------------------------------------------

    async def _settings(self, guild_id: str):
        cached = self._settings_cache.get(guild_id)
        now = discord.utils.utcnow().timestamp()
        if cached and cached[0] > now:
            return cached[1]
        settings = await database.get_voice_settings(guild_id)
        self._settings_cache[guild_id] = (now + SETTINGS_TTL, settings)
        return settings

    def forget_settings(self, guild_id: str):
        """Drop the cache so a change made on the web page applies at once."""
        self._settings_cache.pop(str(guild_id), None)

    def _should_count(self, channel, settings) -> bool:
        if not settings or not settings['enabled'] or channel is None:
            return False
        if str(channel.id) in parse_excluded(settings['excluded_channels']):
            return False
        if not settings['count_afk'] and channel.guild.afk_channel == channel:
            return False
        if not settings['count_solo']:
            if len([m for m in channel.members if not m.bot]) < 2:
                return False
        return True

    # -- intervals ----------------------------------------------------------

    async def _open_interval(self, member, channel, visit):
        started = discord.utils.utcnow()
        visit['session_id'] = await database.start_voice_session(
            str(member.guild.id), str(member.id), member.display_name,
            str(channel.id), channel.name, started,
        )
        visit['interval_started'] = started

    async def _close_interval(self, visit):
        if not visit.get('session_id'):
            return
        seconds = await database.end_voice_session(
            visit['session_id'], discord.utils.utcnow()
        )
        visit['counted'] += seconds or 0
        visit['session_id'] = None

    async def _sync_channel(self, channel):
        """Start or stop counting for everyone in *channel*, per the rules."""
        if channel is None:
            return
        settings = await self._settings(str(channel.guild.id))
        counting = self._should_count(channel, settings)
        for member in channel.members:
            if member.bot:
                continue
            visit = self._visits.get((channel.guild.id, member.id))
            if visit is None or visit['channel_id'] != channel.id:
                continue
            if counting and not visit.get('session_id'):
                await self._open_interval(member, channel, visit)
            elif not counting and visit.get('session_id'):
                await self._close_interval(visit)

    # -- visits -------------------------------------------------------------

    def _begin_visit(self, member, channel):
        self._visits[(member.guild.id, member.id)] = {
            'channel_id': channel.id,
            'channel_name': channel.name,
            'started': discord.utils.utcnow(),
            'session_id': None,
            'counted': 0,
        }

    async def _end_visit(self, member):
        visit = self._visits.pop((member.guild.id, member.id), None)
        if visit is None:
            return None
        await self._close_interval(visit)
        return visit

    # -- events -------------------------------------------------------------

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        # Mutes, deafens and stream toggles fire this too, with the channel
        # unchanged — following them would shred every visit into fragments.
        if member.bot or before.channel == after.channel:
            return

        finished = None
        try:
            async with self._lock:
                if before.channel is not None:
                    finished = await self._end_visit(member)
                if after.channel is not None:
                    self._begin_visit(member, after.channel)
                # Someone arriving or leaving can take a channel over or under
                # the "not alone" line for everybody else in it.
                for channel in {before.channel, after.channel} - {None}:
                    await self._sync_channel(channel)
        except Exception as e:
            print(f"❌ voicelog on_voice_state_update failed: {e}")
            return

        if finished is not None:
            await self._announce(member.guild, member, finished)

    async def _announce(self, guild, member, visit):
        """Post the summary of a finished visit, if that is switched on."""
        try:
            settings = await self._settings(str(guild.id))
            if not settings or not settings['enabled'] or not settings['channel_id']:
                return
            minimum = (settings['min_log_minutes'] or 0) * 60
            if visit['counted'] < max(minimum, 1):
                return

            channel = guild.get_channel(int(settings['channel_id']))
            if channel is None or not channel.permissions_for(guild.me).send_messages:
                return

            embed = discord.Embed(
                description=(
                    f"🔊 {member.mention} spent **{format_duration(visit['counted'])}** "
                    f"in **{visit['channel_name']}**"
                ),
                color=discord.Color.blurple(),
                timestamp=discord.utils.utcnow(),
            )
            await channel.send(embed=embed)
        except Exception as e:
            print(f"❌ voicelog announce failed: {e}")

    @commands.Cog.listener()
    async def on_ready(self):
        """Pick up whoever is already in voice.

        Runs on every reconnect, so it has to be idempotent: members already
        being tracked are left alone.
        """
        try:
            async with self._lock:
                for guild in self.bot.guilds:
                    for channel in guild.voice_channels:
                        for member in channel.members:
                            if member.bot:
                                continue
                            if (guild.id, member.id) not in self._visits:
                                self._begin_visit(member, channel)
                        await self._sync_channel(channel)
        except Exception as e:
            print(f"❌ voicelog startup scan failed: {e}")

    @tasks.loop(seconds=HEARTBEAT_SECONDS)
    async def heartbeat(self):
        """Mark open intervals as still running."""
        try:
            ids = [v['session_id'] for v in self._visits.values() if v.get('session_id')]
            await database.heartbeat_voice_sessions(ids, discord.utils.utcnow())
        except Exception as e:
            print(f"❌ voicelog heartbeat failed: {e}")

    @heartbeat.before_loop
    async def _before_heartbeat(self):
        await self.bot.wait_until_ready()

    # -- shutdown -----------------------------------------------------------

    async def flush_open_sessions(self):
        """Close everything cleanly. Called from `ORBATBot.close()`, so a normal
        redeploy records the time up to the moment the bot went down."""
        async with self._lock:
            for visit in list(self._visits.values()):
                try:
                    await self._close_interval(visit)
                except Exception as e:
                    print(f"❌ voicelog flush failed: {e}")
            self._visits.clear()


async def setup(bot: commands.Bot):
    await bot.add_cog(VoiceLogCog(bot))

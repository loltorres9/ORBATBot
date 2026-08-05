"""Voice time in the browser: the leaderboard and the settings form.

The rules themselves — what counts, when counting pauses — live in
`cogs/voicelog.py`. This module only shapes stored intervals for a page and
validates the settings form.
"""

import discord

# The periods and the embed live in the cog: the daily board there needs the
# same definitions, and web/ is the layer that may depend on cogs, not the
# other way round.
from cogs.voicelog import (
    DEFAULT_PERIOD,
    PERIODS,
    build_leaderboard_embed,
    clean_period,
    format_duration,
    parse_excluded,
    period_label,
    period_start,
)
from utils import database
from web.guilds import postable_channels

MAX_LOG_MINUTES = 1440
MAX_BOARD_HOUR = 23

# PERIODS and the period helpers are re-exported so the routes and templates can
# use them without reaching into the cog themselves.
__all__ = [
    'PERIODS', 'DEFAULT_PERIOD', 'MAX_LOG_MINUTES', 'MAX_BOARD_HOUR',
    'clean_period', 'period_start', 'overview', 'read_settings_form',
    'excluded_set', 'post_leaderboard',
]


async def overview(guild: discord.Guild, member: discord.Member, period: str) -> dict:
    """Leaderboard, per-channel totals and the viewer's own time."""
    since = period_start(period)
    rows = await database.get_voice_leaderboard(str(guild.id), since, limit=25)
    channels = await database.get_voice_channel_totals(str(guild.id), since, limit=10)
    mine = await database.get_voice_member_total(str(guild.id), str(member.id), since)

    top = rows[0]['total_seconds'] if rows else 0
    leaderboard = [{
        'rank': index,
        # The name stored when the interval was recorded — a rename shows up
        # from the next session on, which beats a REST call per row.
        'name': row['member_name'] or row['member_id'],
        'is_me': row['member_id'] == str(member.id),
        'seconds': int(row['total_seconds'] or 0),
        'duration': format_duration(row['total_seconds']),
        'sessions': row['sessions'],
        # Width of the bar behind each row, relative to the leader.
        'share': round(100 * (row['total_seconds'] or 0) / top) if top else 0,
    } for index, row in enumerate(rows, start=1)]

    return {
        'leaderboard': leaderboard,
        'channels': [{
            'name': row['channel_name'] or 'deleted channel',
            'duration': format_duration(row['total_seconds']),
        } for row in channels],
        'my_duration': format_duration(mine['total_seconds'] if mine else 0),
        'my_sessions': (mine['sessions'] if mine else 0) or 0,
        'period': period,
    }


def read_settings_form(guild: discord.Guild, form) -> dict:
    """Validate the settings form. Raises ValueError with a message for the user."""
    channel_id = (form.get('channel_id') or '').strip()
    if channel_id:
        if channel_id not in [str(c.id) for c in postable_channels(guild)]:
            raise ValueError("I can't post in that channel — pick another one.")

    raw_minutes = (form.get('min_log_minutes') or '').strip()
    try:
        minutes = int(raw_minutes or 0)
    except ValueError:
        raise ValueError("The minimum length has to be a whole number of minutes.")
    if minutes < 0 or minutes > MAX_LOG_MINUTES:
        raise ValueError(f"Keep the minimum length between 0 and {MAX_LOG_MINUTES} minutes.")

    voice_ids = {str(c.id) for c in guild.voice_channels}
    excluded = [value for value in form.getlist('excluded_channels') if value in voice_ids]

    board_channel = (form.get('board_channel_id') or '').strip()
    if board_channel and board_channel not in [str(c.id) for c in postable_channels(guild)]:
        raise ValueError("I can't post the daily board in that channel — pick another one.")
    board_enabled = 1 if form.get('board_enabled') else 0
    if board_enabled and not board_channel:
        raise ValueError("Pick a channel for the daily board, or switch it off.")

    try:
        board_hour = int((form.get('board_hour') or 0))
    except ValueError:
        raise ValueError("That isn't a time of day I recognise.")
    if not 0 <= board_hour <= MAX_BOARD_HOUR:
        raise ValueError("The board's time of day has to be a whole hour, 0 to 23.")

    return {
        'board_enabled': board_enabled,
        'board_channel_id': board_channel or None,
        'board_period': clean_period(form.get('board_period')),
        'board_hour': board_hour,
        'enabled': 1 if form.get('enabled') else 0,
        'channel_id': channel_id or None,
        'min_log_minutes': minutes,
        'count_afk': 1 if form.get('count_afk') else 0,
        'count_solo': 1 if form.get('count_solo') else 0,
        'excluded_channels': ','.join(excluded) or None,
    }


def excluded_set(settings) -> set:
    return parse_excluded(settings['excluded_channels']) if settings else set()


async def post_leaderboard(bot, guild: discord.Guild, channel_id: str, period: str,
                           limit: int = 10) -> str:
    """Post the current top *limit* into a channel."""
    channel = None
    if (channel_id or '').strip() and str(channel_id).isdigit():
        channel = guild.get_channel(int(channel_id))
    if channel is None or channel not in postable_channels(guild):
        raise ValueError("I can't post in that channel — pick another one.")

    period = clean_period(period)
    rows = await database.get_voice_leaderboard(str(guild.id), period_start(period), limit=limit)
    label = period_label(period)

    try:
        await channel.send(embed=build_leaderboard_embed(rows, label, limit))
    except (discord.Forbidden, discord.HTTPException) as e:
        raise ValueError(f"Discord wouldn't let me post there: {e}")
    return f"Posted the top {limit} for “{label.lower()}” in #{channel.name}."

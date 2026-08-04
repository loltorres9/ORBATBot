"""Voice time in the browser: the leaderboard and the settings form.

The rules themselves — what counts, when counting pauses — live in
`cogs/voicelog.py`. This module only shapes stored intervals for a page and
validates the settings form.
"""

from datetime import datetime, timedelta, timezone

import discord

from cogs.voicelog import format_duration, parse_excluded
from utils import database
from web.guilds import postable_channels

# The windows offered above the leaderboard.
PERIODS = (
    ('7', 'Last 7 days'),
    ('30', 'Last 30 days'),
    ('90', 'Last 90 days'),
    ('all', 'All time'),
)
DEFAULT_PERIOD = '7'

MAX_LOG_MINUTES = 1440


def period_start(period: str):
    """Naive UTC cut-off for a period key, or None for all time."""
    if period == 'all':
        return None
    try:
        days = int(period)
    except (TypeError, ValueError):
        days = int(DEFAULT_PERIOD)
    return (datetime.now(timezone.utc) - timedelta(days=days)).replace(tzinfo=None)


def clean_period(raw: str) -> str:
    keys = [key for key, _ in PERIODS]
    return raw if raw in keys else DEFAULT_PERIOD


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

    return {
        'enabled': 1 if form.get('enabled') else 0,
        'channel_id': channel_id or None,
        'min_log_minutes': minutes,
        'count_afk': 1 if form.get('count_afk') else 0,
        'count_solo': 1 if form.get('count_solo') else 0,
        'excluded_channels': ','.join(excluded) or None,
    }


def excluded_set(settings) -> set:
    return parse_excluded(settings['excluded_channels']) if settings else set()

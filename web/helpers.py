"""Small shared helpers for the web UI — mostly time.

Stored times are naive UTC (see CLAUDE.md). The Discord messages render as
`<t:…>` timestamps, which every client localises on its own; a web page has no
such luxury, so everything here converts to the guild's configured timezone
before it is shown or read back.
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from cogs.admin import _parse_event_time

_DAY = ('Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun')
_MONTH = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
          'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')


def zone(tz_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz_name or 'UTC')
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo('UTC')


def to_local(dt: datetime, tz_name: str) -> datetime:
    """Naive-UTC column value → aware datetime in the guild's timezone."""
    if dt is None:
        return None
    aware = dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    return aware.astimezone(zone(tz_name))


def fmt_dt(dt: datetime, tz_name: str) -> str:
    """`Sat 10 Aug 2026 · 19:00 CEST` — fixed English names, not the process locale."""
    if dt is None:
        return '—'
    local = to_local(dt, tz_name)
    return (
        f"{_DAY[local.weekday()]} {local.day} {_MONTH[local.month - 1]} {local.year} "
        f"· {local:%H:%M} {local.tzname() or ''}".strip()
    )


def fmt_date(dt: datetime, tz_name: str) -> str:
    if dt is None:
        return '—'
    local = to_local(dt, tz_name)
    return f"{local.day} {_MONTH[local.month - 1]} {local.year}"


def fmt_input(dt: datetime, tz_name: str) -> str:
    """Value for an `<input type="datetime-local">`, in guild-local time."""
    if dt is None:
        return ''
    return to_local(dt, tz_name).strftime('%Y-%m-%dT%H:%M')


def parse_input(raw: str, tz_name: str, label: str = 'time') -> datetime:
    """Read an `<input type="datetime-local">` back as naive UTC.

    Some browsers append seconds, so the value is trimmed to minutes and handed
    to the same parser the slash commands use — one definition of what a guild's
    local time means.
    """
    raw = (raw or '').strip().replace('T', ' ')[:16]
    if not raw:
        raise ValueError(f"Pick a {label}.")
    try:
        return _parse_event_time(raw, tz_name)
    except ValueError:
        raise ValueError(f"`{raw}` isn't a {label} I can read. Use the date picker.")


def relative(dt: datetime) -> str:
    """`in 3 days` / `2 hours ago` — a rough humanised distance from now."""
    if dt is None:
        return ''
    aware = dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    seconds = (aware - datetime.now(timezone.utc)).total_seconds()
    past = seconds < 0
    seconds = abs(seconds)
    for size, name in ((86400, 'day'), (3600, 'hour'), (60, 'minute')):
        if seconds >= size:
            count = int(seconds // size)
            text = f"{count} {name}{'s' if count != 1 else ''}"
            return f"{text} ago" if past else f"in {text}"
    return 'just now' if past else 'in under a minute'


def message_link(event) -> str:
    if not (event['channel_id'] and event['message_id']):
        return ''
    return (
        f"https://discord.com/channels/{event['guild_id']}/"
        f"{event['channel_id']}/{event['message_id']}"
    )

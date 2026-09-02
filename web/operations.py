"""Running an operation from the browser.

The admin half of the slot system: starting an operation on a roster, moving
its start time, posting the board and the announcement, clearing the queue, and
choosing which channels any of that lands in.

Like `web/service.py` and `web/slots.py`, this owns no rules. Every one of them
lives in `cogs/admin.py` and `cogs/slots.py` — `start_operation()`,
`set_operation_time()`, `build_announcement_embed()`, `publish_board()` — and
is called from here, so an operation started on the web is the same operation
`/setup-slots` starts. What this module does is read forms and turn an
`ActionError` into the `ValueError` the routes render as a flash.
"""

import discord

from cogs.admin import (
    REMINDER_CHOICES,
    _TIMEZONE_CHOICES,
    build_announcement_embed,
    set_operation_time,
    start_operation,
)
from cogs.slots import ActionError, clear_pending_queue, publish_board
from utils import database, roster
from web import helpers

# The same list `/set-timezone` offers, as (value, label) pairs for a <select>.
TIMEZONE_CHOICES = [(choice.value, choice.name) for choice in _TIMEZONE_CHOICES]

MAX_MISSION_NAME = 100


def _reminder(raw, fallback: int = 30) -> int:
    try:
        minutes = int(raw)
    except (TypeError, ValueError):
        return fallback
    return minutes if minutes in REMINDER_CHOICES else fallback


async def overview(guild, tz: str) -> dict:
    """Everything the Operation page renders before anyone touches a form.

    The channel and timezone settings are deliberately not in here: they belong
    to the server rather than to an operation, they are set once, and having
    them on the same page as the weekly work made three channel dropdowns visible
    at the same time. They live on `/operation/settings` and read
    `channel_settings()` directly.
    """
    op = await database.get_active_operation(str(guild.id))
    source, orbat_name = None, None
    counts = {'total': 0, 'open': 0, 'pending': 0, 'filled': 0}

    if op is not None:
        if roster.is_db_backed(op):
            record = await database.get_orbat(op['orbat_id'])
            orbat_name = record['name'] if record else None
            source = f"ORBAT: {orbat_name}" if record else 'ORBAT (deleted)'
        else:
            source = 'Google Sheet'
        counts = await _counts(op)

    return {
        'operation': op,
        'source': source,
        'orbat_name': orbat_name,
        'counts': counts,
        'orbats': await database.get_guild_orbats(str(guild.id)),
        # So "Post the live board" opens on the channel the board already goes
        # to, rather than on whichever channel happens to sort first.
        'orbat_channel_id': (await database.get_guild_channels(str(guild.id))).get('orbat'),
        'timezone': tz,
        'reminder_choices': REMINDER_CHOICES,
    }


async def _counts(op) -> dict:
    """Open / pending / filled, counted the way the board's header counts them.

    A slot the ORBAT marks `nocount` is left out here for the same reason it is
    left out there: a reserve bench would make the operation look permanently
    under-strength.
    """
    try:
        data = await roster.load_all(op)
    except Exception:
        return {'total': 0, 'open': 0, 'pending': 0, 'filled': 0, 'error': True}

    pending = set(await database.get_pending_slots(op['id']))
    counted = [slot for slot in data['slots'] if not slot['excluded']]
    filled = sum(1 for slot in counted if slot['assigned_to'])
    waiting = sum(1 for slot in counted
                  if not slot['assigned_to'] and slot['key'] in pending)
    return {
        'total': len(counted),
        'filled': filled,
        'pending': waiting,
        'open': len(counted) - filled - waiting,
    }


async def channel_settings(guild) -> list:
    """One row per configurable channel: what is set, and what it falls back to.

    The fallback is shown rather than silently applied, because "nothing chosen"
    and "chosen, and it happens to be #orbat" look identical on a form and mean
    different things the day somebody renames a channel.
    """
    chosen = await database.get_guild_channels(str(guild.id))
    labels = {
        'orbat': ('ORBAT board', 'Where the live board and the reminder ping go.'),
        'approvals': ('Slot approvals', 'Where a new request goes to be decided.'),
        'archive': ('Approval archive', 'Where every decided request is recorded.'),
    }
    rows = []
    for kind, (_, name) in database.CHANNEL_KINDS.items():
        channel_id = chosen.get(kind)
        current = guild.get_channel(int(channel_id)) if channel_id else None
        title, hint = labels[kind]
        rows.append({
            'kind': kind,
            'title': title,
            'hint': hint,
            'default_name': name,
            'value': str(current.id) if current else '',
            # A channel that was chosen and has since been deleted: the id is
            # still stored, so say so rather than quietly showing the default.
            'missing': bool(channel_id) and current is None,
        })
    return rows


async def save_channels(guild, form) -> str:
    """Store the channel choices. An empty box means "back to the default"."""
    postable = {str(channel.id) for channel in guild.text_channels}
    chosen = {}
    for kind in database.CHANNEL_KINDS:
        raw = (form.get(f"channel_{kind}") or '').strip()
        if raw and raw not in postable:
            raise ValueError('Pick a channel from this server.')
        chosen[kind] = raw or None
    await database.set_guild_channels(str(guild.id), chosen)

    named = sum(1 for value in chosen.values() if value)
    if not named:
        return 'Channels saved — all three are back to their default names.'
    return f"Channels saved ({named} of {len(chosen)} set explicitly)."


async def start(bot, guild, form, tz: str) -> str:
    """`/setup-slots` — load a roster and make it the active operation."""
    backing = (form.get('backing') or '').strip()
    orbat_id, sheet_url = None, None
    if backing == 'orbat':
        raw = (form.get('orbat_id') or '').strip()
        if not raw.isdigit():
            raise ValueError('Pick an ORBAT.')
        orbat_id = int(raw)
    elif backing == 'sheet':
        sheet_url = (form.get('sheet_url') or '').strip()
        if not sheet_url:
            raise ValueError('Paste the Google Sheets URL.')
    else:
        raise ValueError('Choose whether this operation runs on an ORBAT or a sheet.')

    event_time = None
    if (form.get('event_time') or '').strip():
        event_time = helpers.parse_input(form.get('event_time'), tz, 'start time')

    try:
        result = await start_operation(
            bot, guild, orbat_id=orbat_id, sheet_url=sheet_url,
            name=form.get('name'), event_time=event_time,
            reminder_minutes=_reminder(form.get('reminder_minutes')),
        )
    except ActionError as e:
        raise ValueError(str(e))

    note = (f"{result['operation']['name']} is live with "
            f"{result['slot_count']} slot(s).")
    if result['channel']:
        return f"{note} The board was posted to #{result['channel'].name}."
    return (f"{note} The board could not be posted — check the ORBAT channel "
            f"below and my permissions on it.")


async def set_time(bot, guild, op, form, tz: str) -> str:
    """`/set-event-time` — move the start and re-arm the reminder."""
    if op is None:
        raise ValueError('No operation is running.')
    when = helpers.parse_input(form.get('event_time'), tz, 'start time')
    minutes = _reminder(form.get('reminder_minutes'))
    try:
        await set_operation_time(bot, guild, op, when, minutes)
    except ActionError as e:
        raise ValueError(str(e))
    return (f"Start time set to {helpers.fmt_dt(when, tz)}, "
            f"reminder {minutes} minutes before.")


async def set_timezone(guild, form) -> str:
    """`/set-timezone` — what a time typed anywhere on this site means."""
    tz_name = (form.get('timezone') or '').strip()
    if tz_name not in {value for value, _ in TIMEZONE_CHOICES}:
        raise ValueError('Pick a timezone from the list.')
    await database.set_guild_timezone(str(guild.id), tz_name)
    return f"Server timezone set to {tz_name}."


async def post_board(bot, guild, op, form) -> str:
    """`/post-orbat` — a fresh live board, which becomes the one that updates."""
    if op is None:
        raise ValueError('No operation is running.')
    channel = _channel(guild, form.get('channel_id'))
    try:
        await publish_board(bot, guild, channel, op)
    except discord.Forbidden:
        raise ValueError(f"I can't post in #{channel.name}.")
    except Exception as e:
        raise ValueError(f"Could not post the board: {e}")
    return (f"Board posted to #{channel.name}. It updates itself from now on — "
            'the previous one stops.')


async def post_announcement(bot, guild, member, op, form, tz: str) -> str:
    """`/post-event` — the "we play at 19:00, sign up here" message."""
    channel = _channel(guild, form.get('channel_id'))

    mission_name = (form.get('mission_name') or '').strip()
    if not mission_name:
        if op is None:
            raise ValueError('No operation is running — type a mission name.')
        mission_name = op['name']
    if len(mission_name) > MAX_MISSION_NAME:
        raise ValueError(f'Keep the mission name under {MAX_MISSION_NAME} characters.')

    if (form.get('event_time') or '').strip():
        when = helpers.parse_input(form.get('event_time'), tz, 'start time')
    else:
        when = op['event_time'] if op else None

    embed = await build_announcement_embed(guild, mission_name, when,
                                           member.display_name)
    try:
        await channel.send(embed=embed)
    except discord.Forbidden:
        raise ValueError(f"I can't post in #{channel.name}.")
    return f"Announcement posted to #{channel.name}."


async def clear_pending(bot, guild, op) -> str:
    """`/clear-requests` — take every undecided request out of the queue."""
    if op is None:
        raise ValueError('No operation is running.')
    count = await clear_pending_queue(bot, guild, op)
    if not count:
        return 'There was nothing waiting.'
    return f"Cleared {count} pending request(s). Nobody was DMed."


async def raw_slots(op, squad: str = None) -> dict:
    """`/debug-slots` — the roster exactly as the bot reads it, keys and all."""
    if op is None:
        raise ValueError('No operation is running.')
    try:
        data = await roster.load_all(op)
    except Exception as e:
        raise ValueError(f"Failed to load the roster: {e}")
    slots = data['slots']
    if squad:
        slots = [slot for slot in slots if squad.lower() in slot['squad'].lower()]
    return {
        'slots': slots,
        'source': 'ORBAT' if roster.is_db_backed(op) else 'sheet',
        'nets': data['nets'],
    }


def _channel(guild, raw):
    """A text channel of this guild, by id — the one thing a form can't be trusted on."""
    raw = (raw or '').strip()
    channel = guild.get_channel(int(raw)) if raw.isdigit() else None
    if not isinstance(channel, discord.TextChannel):
        raise ValueError('Pick a channel from this server.')
    return channel

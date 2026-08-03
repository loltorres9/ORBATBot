"""Creating and managing events from the browser.

Every rule here — who may do what, what a valid response set is, how a series is
anchored — is the same code the slash commands use; this module only translates
between HTML form fields and those helpers. A `ValueError` raised in here is a
message meant for the user and is shown on the form.
"""

import discord
from discord.ext import commands

from cogs.events import (
    _DAY_NAMES,
    _RECURRENCE_LABELS,
    _as_utc,
    _attending,
    _delete_event_message,
    _is_last_weekday_of_month,
    _ordinal,
    _parse_responses,
    _refresh_event_message,
    _spawn_next_occurrence,
    _weekday_position,
    load_responses,
    publish_event,
)
from utils import database
from web.guilds import postable_channels
from web.helpers import fmt_dt, parse_input

# What the reminder dropdown offers, mirroring the slash command's choices.
REMINDER_CHOICES = (
    (0, 'No reminder'),
    (15, '15 minutes before'),
    (30, '30 minutes before'),
    (60, '1 hour before'),
    (120, '2 hours before'),
    (1440, '24 hours before'),
)

REPEAT_CHOICES = (
    ('none', "Don't repeat"),
    ('daily', 'Daily'),
    ('weekly', 'Weekly'),
    ('biweekly', 'Every 2 weeks'),
    ('monthly', 'Monthly — same date (e.g. the 15th)'),
    ('monthly_last', 'Monthly — last weekday (e.g. last Saturday)'),
    ('monthly_nth', 'Monthly — same weekday (e.g. 2nd Saturday)'),
    ('weekly_not_last', 'Weekly — except the last one of the month'),
)

MAX_TITLE = 200


def _clean(raw) -> str:
    return (raw or '').strip() or None


def _duration(raw) -> int:
    if not (raw or '').strip():
        return None
    try:
        minutes = int(raw)
    except (TypeError, ValueError):
        raise ValueError("Duration has to be a whole number of minutes.")
    if minutes <= 0:
        raise ValueError("Duration has to be a positive number of minutes.")
    return minutes


def _reminder(raw) -> int:
    """None means no reminder, which is stored as NULL."""
    try:
        minutes = int(raw or 0)
    except (TypeError, ValueError):
        raise ValueError("That isn't a reminder window I recognise.")
    return minutes or None


def _title(raw) -> str:
    title = (raw or '').strip()
    if not title:
        raise ValueError("Give the event a title.")
    if len(title) > MAX_TITLE:
        raise ValueError(f"Keep the title under {MAX_TITLE} characters.")
    return title


def _resolve_roles(guild: discord.Guild, role_ids) -> tuple:
    """Turn the ping-role checkboxes into roles, reporting any that vanished."""
    roles, unknown = [], []
    for role_id in role_ids or []:
        role = guild.get_role(int(role_id)) if str(role_id).isdigit() else None
        if role is None:
            unknown.append(str(role_id))
        elif role not in roles:
            roles.append(role)
    return roles, unknown


def _mention_warnings(roles: list, unknown: list) -> list:
    warnings = []
    not_pingable = [r.name for r in roles if not r.mentionable]
    if not_pingable:
        warnings.append(
            f"{', '.join(not_pingable)} isn't mentionable, so it shows as text without "
            "notifying anyone unless the bot has Mention All Roles."
        )
    if unknown:
        warnings.append(f"Ignored {len(unknown)} ping role(s) that no longer exist.")
    return warnings


def _recurrence(raw) -> str:
    value = (raw or 'none').strip()
    if value in _RECURRENCE_LABELS:
        return value
    if value in ('', 'none'):
        return None
    raise ValueError("That isn't a repeat pattern I recognise.")


def _repeat_warnings(recurrence: str, start) -> list:
    """The same "your first date doesn't match the pattern" notes the slash
    command gives, so the jump doesn't surprise anyone a month later."""
    if recurrence == 'monthly_last' and not _is_last_weekday_of_month(start):
        return [
            f"Your first date is the {_ordinal(_weekday_position(start))} "
            f"{_DAY_NAMES[start.weekday()]}, not the last one. Every occurrence after it "
            f"is the last {_DAY_NAMES[start.weekday()]} of the month."
        ]
    if recurrence == 'weekly_not_last' and _is_last_weekday_of_month(start):
        return [
            f"Your first date is the last {_DAY_NAMES[start.weekday()]} of its month, "
            "which this pattern normally skips. It stays where you put it; every "
            "occurrence after it skips the last one."
        ]
    return []


def _responses(raw):
    """None means "use the built-in Accepted / Tentative / Declined set"."""
    if not (raw or '').strip():
        return None
    return _parse_responses(raw)


def _channel(guild: discord.Guild, raw):
    if not (raw or '').strip():
        raise ValueError("Pick a channel to post the event in.")
    channel = guild.get_channel(int(raw)) if str(raw).isdigit() else None
    if channel is None or channel not in postable_channels(guild):
        raise ValueError("I can't post in that channel — pick another one.")
    return channel


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------

async def create_event(bot: commands.Bot, guild: discord.Guild,
                       member: discord.Member, tz_name: str, form: dict) -> tuple:
    """Create an event and post it. Returns (event_id, warnings)."""
    title = _title(form.get('title'))
    start = parse_input(form.get('start_time'), tz_name, 'start time')
    if _as_utc(start) <= discord.utils.utcnow():
        raise ValueError("That start time is in the past. Events have to start in the future.")

    duration = _duration(form.get('duration'))
    reminder = _reminder(form.get('reminder'))
    recurrence = _recurrence(form.get('repeat'))
    responses = _responses(form.get('responses'))
    channel = _channel(guild, form.get('channel_id'))
    roles, unknown_roles = _resolve_roles(guild, form.get('mention_ids'))

    until = None
    if (form.get('repeat_until') or '').strip():
        if recurrence is None:
            raise ValueError("An end date only makes sense together with a repeat pattern.")
        until = parse_input(form.get('repeat_until'), tz_name, 'repeat end date')
        if until <= start:
            raise ValueError(
                "The repeat end date has to be after the first start time, "
                "otherwise the event would never repeat."
            )

    event_id = await database.create_event(
        guild_id=str(guild.id),
        title=title,
        event_time=start,
        created_by=str(member.id),
        created_by_name=member.display_name,
        description=_clean(form.get('description')),
        duration_minutes=duration,
        location=_clean(form.get('location')),
        image_url=_clean(form.get('image_url')),
        mention_role_id=','.join(str(r.id) for r in roles) or None,
        reminder_minutes=reminder,
        recurrence=recurrence,
        recurrence_until=until,
    )
    if responses:
        await database.set_event_responses(event_id, responses)

    try:
        await publish_event(bot, channel, event_id)
    except discord.Forbidden:
        await database.set_event_status(event_id, 'cancelled')
        raise ValueError(f"I can't post in #{channel.name}, so the event was discarded.")
    except discord.HTTPException as e:
        # Most likely an image URL Discord won't accept.
        await database.set_event_status(event_id, 'cancelled')
        raise ValueError(
            f"Discord rejected the event message, so it was discarded: {e}. "
            "If you gave a banner image, check that it's a direct link to an image file."
        )

    warnings = _mention_warnings(roles, unknown_roles)
    if recurrence:
        warnings += _repeat_warnings(recurrence, start)
    return event_id, warnings


async def edit_event(bot: commands.Bot, guild: discord.Guild, event,
                     tz_name: str, form: dict) -> list:
    """Apply the edit form to an existing event. Returns a list of notes."""
    event_id = event['id']
    notes = []

    title = _title(form.get('title'))
    start = parse_input(form.get('start_time'), tz_name, 'start time')
    duration = _duration(form.get('duration'))
    reminder = _reminder(form.get('reminder'))
    recurrence = _recurrence(form.get('repeat'))
    responses = _responses(form.get('responses'))
    roles, unknown_roles = _resolve_roles(guild, form.get('mention_ids'))
    description = _clean(form.get('description'))
    location = _clean(form.get('location'))
    image_url = _clean(form.get('image_url'))

    # Only pass the start time on when it actually moved: update_event re-arms
    # the reminder whenever it is given one, and an unchanged form submission
    # must not make a reminder that already fired fire a second time.
    time_changed = start != event['event_time']
    if time_changed and _as_utc(start) <= discord.utils.utcnow():
        raise ValueError("That start time is in the past.")

    until = None
    if (form.get('repeat_until') or '').strip():
        if recurrence is None:
            raise ValueError("An end date only makes sense together with a repeat pattern.")
        until = parse_input(form.get('repeat_until'), tz_name, 'repeat end date')
        if until <= start:
            raise ValueError("The repeat end date has to be after the start time.")

    await database.update_event(
        event_id,
        title=title,
        description=description,
        event_time=start if time_changed else None,
        duration_minutes=duration,
        location=location,
        image_url=image_url,
        reminder_minutes=reminder,
    )
    # COALESCE can only ever set a value, so emptied fields are cleared separately.
    await database.clear_event_fields(event_id, [
        name for name, value, stored in (
            ('description', description, event['description']),
            ('location', location, event['location']),
            ('image_url', image_url, event['image_url']),
            ('duration_minutes', duration, event['duration_minutes']),
            ('reminder_minutes', reminder, event['reminder_minutes']),
        ) if value is None and stored is not None
    ])

    if time_changed:
        notes.append("The reminder was re-armed for the new time.")

    if recurrence is None:
        if event['recurrence'] in _RECURRENCE_LABELS:
            await database.set_event_recurrence(event_id, None, None, None)
            notes.append("The series is stopped — no further occurrences will be posted.")
    else:
        # Re-anchor when the start time moved, so the series follows it.
        anchor = start if time_changed else (event['recurrence_anchor'] or event['event_time'])
        await database.set_event_recurrence(event_id, recurrence, until, anchor)
        notes += _repeat_warnings(recurrence, anchor)

    await database.set_event_mentions(event_id, ','.join(str(r.id) for r in roles) or None)
    notes += _mention_warnings(roles, unknown_roles)

    await database.set_event_responses(event_id, responses or [])
    keys = [item['key'] for item in (responses or await load_responses(event_id))]
    dropped = await database.drop_signups_not_in(event_id, keys)
    if dropped:
        notes.append(
            f"{dropped} sign-up(s) used an option that no longer exists and were "
            "cleared — those members need to answer again."
        )

    record = await database.get_event(event_id)
    if not await _refresh_event_message(bot, record):
        notes.append("I couldn't update the event message — it may have been deleted.")
    return notes


async def cancel_event(bot: commands.Bot, guild: discord.Guild, event,
                       canceller: discord.Member, reason: str,
                       stop_series: bool, tz_name: str) -> list:
    """Cancel an event, notify everyone attending, and hand over to the next
    occurrence unless the whole series is being stopped."""
    event_id = event['id']
    recurring = event['recurrence'] in _RECURRENCE_LABELS

    if recurring and stop_series:
        # Clear it first so nothing can spawn a successor afterwards.
        await database.set_event_recurrence(event_id, None, None, None)

    await database.set_event_status(event_id, 'cancelled')
    record = await database.get_event(event_id)
    await _refresh_event_message(bot, record, view=None)

    notes = []
    if recurring:
        if stop_series:
            notes.append("The whole series is stopped — no further occurrences.")
        else:
            next_id = await _spawn_next_occurrence(bot, record)
            if next_id:
                nxt = await database.get_event(next_id)
                notes.append(
                    f"Only this occurrence was cancelled. The next one is up as "
                    f"#{next_id} for {fmt_dt(nxt['event_time'], tz_name)}."
                )
            else:
                notes.append("This was the last occurrence in the series — nothing follows it.")

    signups = await database.get_event_signups(event_id)
    notify = _attending(signups, await load_responses(event_id))
    reason_line = f"\nReason: {reason}" if reason else ""
    for row in notify:
        try:
            member = await guild.fetch_member(int(row['member_id']))
            await member.send(
                f"❌ **Event Cancelled — {record['title']}**\n"
                f"Cancelled by {canceller.display_name}.{reason_line}"
            )
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            pass

    notes.insert(0, f"Notified {len(notify)} attendee(s).")
    return notes


async def delete_event(bot: commands.Bot, event) -> list:
    notes = []
    if not await _delete_event_message(bot, event):
        notes.append(
            "The message couldn't be removed — it may already be gone, or I lack "
            "permission in that channel."
        )
    await database.delete_event(event['id'])
    return notes


async def rsvp(bot: commands.Bot, event, member: discord.Member, key: str) -> str:
    """Answer an event from the browser. Pressing the answer you already gave
    withdraws it, exactly like the buttons on the Discord message."""
    if event['status'] != 'scheduled':
        raise ValueError(f"This event is {event['status']} — sign-ups are closed.")

    responses = await load_responses(event['id'])
    item = next((r for r in responses if r['key'] == key), None)
    if item is None:
        raise ValueError("That option no longer exists on this event.")

    existing = await database.get_event_signup(event['id'], str(member.id))
    if existing and existing['response'] == key:
        await database.remove_event_signup(event['id'], str(member.id))
        note = f"Withdrawn — you're no longer marked as {item['label']}."
    else:
        await database.set_event_signup(
            event['id'], str(member.id), member.display_name, key
        )
        note = f"You're marked as {item['label']}."

    record = await database.get_event(event['id'])
    await _refresh_event_message(bot, record)
    return note


async def event_view_model(event, tz_name: str) -> dict:
    """Everything a template needs about one event, resolved in one place."""
    responses = await load_responses(event['id'])
    signups = await database.get_event_signups(event['id'])
    grouped = {item['key']: [] for item in responses}
    for row in signups:
        grouped.get(row['response'], []).append(row)
    return {
        'event': event,
        'responses': responses,
        'grouped': grouped,
        'signup_count': len(signups),
        'tz': tz_name,
    }

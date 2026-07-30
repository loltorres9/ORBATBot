import asyncio
import calendar
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from utils import database
from cogs.admin import _is_unit_leader_or_admin, _parse_event_time

# The built-in response set, used by every event that defines no custom one.
# Keys match what pre-existing sign-up rows already store.
DEFAULT_RESPONSES = (
    {'key': 'accepted',  'label': 'Accepted',  'emoji': '✅', 'is_decline': 0},
    {'key': 'tentative', 'label': 'Tentative', 'emoji': '❓', 'is_decline': 0},
    {'key': 'declined',  'label': 'Declined',  'emoji': '❌', 'is_decline': 1},
)

# Discord allows 5 buttons per action row; 10 keeps the view to two tidy rows.
MAX_RESPONSES = 10
MIN_RESPONSES = 2
MAX_RESPONSE_LABEL = 40

# Recurrence keys stored in events.recurrence. The two weekday variants take
# their weekday — and for monthly_nth their position — from the series anchor.
_RECURRENCE_LABELS = {
    'daily': 'Daily',
    'weekly': 'Weekly',
    'biweekly': 'Every 2 weeks',
    'monthly': 'Monthly',
    'monthly_nth': 'Monthly by weekday',
    'monthly_last': 'Monthly, last weekday',
    'weekly_not_last': 'Weekly except the last weekday',
}

_REPEAT_CHOICES = [
    app_commands.Choice(name="Don't repeat", value='none'),
    app_commands.Choice(name='Daily', value='daily'),
    app_commands.Choice(name='Weekly', value='weekly'),
    app_commands.Choice(name='Every 2 weeks', value='biweekly'),
    app_commands.Choice(name='Monthly — same date (e.g. the 15th)', value='monthly'),
    app_commands.Choice(name='Monthly — last weekday (e.g. last Saturday)', value='monthly_last'),
    app_commands.Choice(name='Monthly — same weekday (e.g. 2nd Saturday)', value='monthly_nth'),
    app_commands.Choice(name='Weekly — except the last one of the month', value='weekly_not_last'),
]

# Fixed English names: calendar.day_name follows the process locale, and these
# strings end up in user-facing text and in the docs.
_DAY_NAMES = ('Monday', 'Tuesday', 'Wednesday', 'Thursday',
              'Friday', 'Saturday', 'Sunday')

_REMINDER_CHOICES = [
    app_commands.Choice(name='No reminder', value=0),
    app_commands.Choice(name='15 minutes before', value=15),
    app_commands.Choice(name='30 minutes before', value=30),
    app_commands.Choice(name='60 minutes before', value=60),
    app_commands.Choice(name='2 hours before', value=120),
    app_commands.Choice(name='24 hours before', value=1440),
]


def _is_emoji(token: str) -> bool:
    """True if *token* is something Discord accepts as a button emoji.

    PartialEmoji.from_str() turns plain words into named emoji that Discord then
    rejects, taking the whole component with it, so anything with ASCII letters
    is treated as an ordinary label word instead.
    """
    try:
        parsed = discord.PartialEmoji.from_str(token)
    except Exception:
        return False
    if parsed.id is not None:
        return True
    return bool(token) and not any(c.isascii() and c.isalpha() for c in token)


def _response_key(label: str) -> str:
    slug = re.sub(r'[^a-z0-9]+', '_', label.lower()).strip('_')
    return (slug or 'option')[:24]


def _parse_responses(raw: str) -> list:
    """Parse `✅ Coming | ❓ Maybe | -❌ Can't make it` into response definitions.

    A leading `-` marks a response that means "not coming": those people are
    left out of reminders, the way Declined always has been.
    """
    entries = [part.strip() for part in raw.split('|')]
    entries = [part for part in entries if part]

    if len(entries) < MIN_RESPONSES:
        raise ValueError(
            f"Give at least {MIN_RESPONSES} responses, separated by `|` — "
            "for example `✅ Coming | ❓ Maybe | -❌ Can't make it`."
        )
    if len(entries) > MAX_RESPONSES:
        raise ValueError(
            f"That's {len(entries)} responses; {MAX_RESPONSES} is the most that fits on a message."
        )

    parsed, used = [], set()
    for order, entry in enumerate(entries):
        is_decline = 0
        if entry.startswith('-'):
            is_decline, entry = 1, entry[1:].strip()

        emoji = None
        parts = entry.split(None, 1)
        if parts and _is_emoji(parts[0]):
            if len(parts) == 1:
                raise ValueError(f"`{parts[0]}` has no wording after it — every response needs a label.")
            emoji, entry = parts[0], parts[1].strip()

        if not entry:
            raise ValueError("Every response needs a label, e.g. `✅ Coming`.")
        if len(entry) > MAX_RESPONSE_LABEL:
            raise ValueError(
                f"`{entry[:20]}…` is too long — keep response labels under "
                f"{MAX_RESPONSE_LABEL} characters so they fit on a button."
            )

        key = base = _response_key(entry)
        suffix = 2
        while key in used:
            key = f"{base[:22]}_{suffix}"
            suffix += 1
        used.add(key)
        parsed.append({'key': key, 'label': entry, 'emoji': emoji,
                       'is_decline': is_decline, 'sort_order': order})

    if all(item['is_decline'] for item in parsed):
        raise ValueError(
            "At least one response has to mean *coming* — right now every one is "
            "marked with `-`, so nobody could ever sign up."
        )
    return parsed


async def load_responses(event_id: int) -> list:
    """An event's response set, falling back to the built-in one."""
    rows = await database.get_event_responses(event_id)
    return [dict(row) for row in rows] if rows else [dict(r) for r in DEFAULT_RESPONSES]


def _attending(signups: list, responses: list) -> list:
    """Everyone whose response doesn't mean "not coming" — the people a reminder
    or a cancellation notice is actually for."""
    declining = {item['key'] for item in responses if item['is_decline']}
    return [row for row in signups if row['response'] not in declining]


def _response_label(responses: list, key: str) -> str:
    item = discord.utils.find(lambda r: r['key'] == key, responses)
    return item['label'] if item else key


def _describe_responses(responses: list) -> str:
    return ' · '.join(
        f"{(r['emoji'] + ' ') if r['emoji'] else ''}{r['label']}"
        + (' *(not coming)*' if r['is_decline'] else '')
        for r in responses
    )


def _as_utc(dt: datetime) -> datetime:
    """Attach UTC to the naive timestamps we store, so .timestamp() is correct."""
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _add_months(start: datetime, months: int) -> datetime:
    """Shift by whole months, clamping to the last valid day of the target month
    so 31 January + 1 month lands on 28/29 February rather than overflowing."""
    total = start.month - 1 + months
    year = start.year + total // 12
    month = total % 12 + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return start.replace(year=year, month=month, day=day)


def _weekday_position(dt: datetime) -> int:
    """1-based position of dt's weekday within its month — 4 for the 4th Saturday."""
    return (dt.day - 1) // 7 + 1


def _is_last_weekday_of_month(dt: datetime) -> bool:
    return dt.day + 7 > calendar.monthrange(dt.year, dt.month)[1]


def _weekday_day(year: int, month: int, weekday: int, position: int) -> Optional[int]:
    """Day-of-month of the *position*-th given weekday, or of the last one when
    position is -1. None if that month has no such day (e.g. no 5th Saturday)."""
    last = calendar.monthrange(year, month)[1]
    if position == -1:
        day = last
        while datetime(year, month, day).weekday() != weekday:
            day -= 1
        return day
    first_weekday = datetime(year, month, 1).weekday()
    day = 1 + (weekday - first_weekday) % 7 + (position - 1) * 7
    return day if day <= last else None


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = 'th'
    else:
        suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')
    return f"{n}{suffix}"


def _nth_occurrence(anchor: datetime, recurrence: str, n: int) -> Optional[datetime]:
    """The nth occurrence counting from the series anchor (n=0 is the anchor).

    None means "this n has no valid date" — for the weekday variants a month may
    simply lack a 5th Saturday — so callers must skip rather than stop.
    """
    if recurrence == 'daily':
        return anchor + timedelta(days=n)
    if recurrence == 'weekly':
        return anchor + timedelta(weeks=n)
    if recurrence == 'weekly_not_last':
        candidate = anchor + timedelta(weeks=n)
        # Returning None makes the caller skip this week and keep walking, which
        # is exactly the exclusion this pattern needs.
        return None if _is_last_weekday_of_month(candidate) else candidate
    if recurrence == 'biweekly':
        return anchor + timedelta(weeks=2 * n)
    if recurrence == 'monthly':
        return _add_months(anchor, n)
    if recurrence in ('monthly_nth', 'monthly_last'):
        # Shift the month from the 1st so no day-clamping can move us months.
        target = _add_months(anchor.replace(day=1), n)
        position = -1 if recurrence == 'monthly_last' else _weekday_position(anchor)
        day = _weekday_day(target.year, target.month, anchor.weekday(), position)
        if day is None:
            return None
        return anchor.replace(year=target.year, month=target.month, day=day)
    return None


def _recurrence_text(event) -> Optional[str]:
    """Human description of an event's recurrence, naming the weekday where the
    pattern depends on it."""
    recurrence = event['recurrence']
    if recurrence not in _RECURRENCE_LABELS:
        return None
    anchor = event['recurrence_anchor'] or event['event_time']
    day = _DAY_NAMES[anchor.weekday()]
    if recurrence == 'monthly_last':
        return f"Monthly · last {day} of the month"
    if recurrence == 'monthly_nth':
        return f"Monthly · {_ordinal(_weekday_position(anchor))} {day} of the month"
    if recurrence == 'weekly_not_last':
        return f"Every {day} except the last one of the month"
    return _RECURRENCE_LABELS[recurrence]


def _next_occurrence(anchor: datetime, recurrence: str, after: datetime) -> Optional[datetime]:
    """First occurrence strictly after *after*.

    Always measured from the anchor, never from the previous occurrence, so a
    monthly series stays on its original day and a bot that was offline for
    weeks catches up to the present in one step instead of posting every
    missed occurrence.
    """
    if recurrence not in _RECURRENCE_LABELS or anchor is None:
        return None
    n = 1
    # Bound the walk so a pathological anchor can't spin forever.
    while n <= 4000:
        candidate = _nth_occurrence(anchor, recurrence, n)
        # None means this month has no matching day (no 5th Saturday, say) —
        # skip it and keep looking rather than ending the series.
        if candidate is not None and candidate > after:
            return candidate
        n += 1
    return None


def _is_organiser(member: discord.Member, event) -> bool:
    """The event's creator can manage it; admins can manage anyone's."""
    perms = member.guild_permissions
    if perms.manage_guild or perms.administrator:
        return True
    return str(member.id) == event['created_by']


def _group_signups(signups: list, responses: list) -> dict:
    grouped = {item['key']: [] for item in responses}
    for row in signups:
        # Ignore any response value we don't recognise rather than crashing.
        grouped.get(row['response'], []).append(row)
    return grouped


def _format_attendees(rows: list) -> str:
    """Mention list for an embed field, trimmed to Discord's 1024-char limit."""
    if not rows:
        return '—'
    lines = []
    used = 0
    for index, row in enumerate(rows):
        line = f"<@{row['member_id']}>"
        remaining = len(rows) - index
        # Reserve room for the overflow notice before committing to this line.
        notice = f"\n_…and {remaining} more_"
        if used + len(line) + 1 + len(notice) > 1024:
            lines.append(f"_…and {remaining} more_")
            break
        lines.append(line)
        used += len(line) + 1
    return '\n'.join(lines)


def _build_event_embed(event, signups: list, responses: list = None) -> discord.Embed:
    responses = responses or [dict(r) for r in DEFAULT_RESPONSES]
    grouped = _group_signups(signups, responses)
    start = _as_utc(event['event_time'])
    start_ts = int(start.timestamp())
    status = event['status']

    if status == 'cancelled':
        title, color = f"❌ {event['title']} — Cancelled", discord.Color.red()
    elif status == 'completed':
        title, color = f"🏁 {event['title']} — Finished", discord.Color.dark_gray()
    else:
        title, color = f"📅 {event['title']}", discord.Color.blurple()

    embed = discord.Embed(
        title=title[:256],
        description=event['description'][:4096] if event['description'] else None,
        color=color,
    )

    when = f"<t:{start_ts}:F>  (<t:{start_ts}:R>)"
    if event['duration_minutes']:
        end_ts = int((start + timedelta(minutes=event['duration_minutes'])).timestamp())
        when += f"\nUntil <t:{end_ts}:t> · {event['duration_minutes']} min"
    embed.add_field(name='🕐 When', value=when, inline=False)

    if event['location']:
        embed.add_field(name='📍 Where', value=event['location'][:1024], inline=False)

    if event['recurrence'] in _RECURRENCE_LABELS:
        repeat = _recurrence_text(event)
        if event['recurrence_until']:
            until_ts = int(_as_utc(event['recurrence_until']).timestamp())
            repeat += f" · until <t:{until_ts}:d>"
        if status == 'scheduled':
            repeat += "\nThe next one is posted automatically when this one ends."
        embed.add_field(name='🔁 Repeats', value=repeat, inline=False)

    for item in responses:
        rows = grouped[item['key']]
        prefix = f"{item['emoji']} " if item['emoji'] else ''
        embed.add_field(
            name=f"{prefix}{item['label']} ({len(rows)})"[:256],
            value=_format_attendees(rows),
            inline=True,
        )

    if event['image_url']:
        embed.set_image(url=event['image_url'])

    footer = f"Event #{event['id']}"
    if event['created_by_name']:
        footer += f" · organised by {event['created_by_name']}"
    if status == 'scheduled':
        footer += " · press the same button again to withdraw"
    embed.set_footer(text=footer[:2048])
    embed.timestamp = discord.utils.utcnow()
    return embed


async def _delete_event_message(bot: commands.Bot, event) -> bool:
    """Remove an event's message from its channel. False if it can't be reached."""
    if not event['channel_id'] or not event['message_id']:
        return False
    channel = bot.get_channel(int(event['channel_id']))
    if channel is None:
        try:
            channel = await bot.fetch_channel(int(event['channel_id']))
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return False
    try:
        msg = await channel.fetch_message(int(event['message_id']))
        await msg.delete()
        return True
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        return False


async def _refresh_event_message(bot: commands.Bot, event, view=None) -> bool:
    """Re-render an event's message. Returns False if it could not be reached."""
    if not event['channel_id'] or not event['message_id']:
        return False

    channel = bot.get_channel(int(event['channel_id']))
    if channel is None:
        try:
            channel = await bot.fetch_channel(int(event['channel_id']))
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return False

    try:
        msg = await channel.fetch_message(int(event['message_id']))
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        return False

    signups = await database.get_event_signups(event['id'])
    responses = await load_responses(event['id'])
    if view is None:
        view = (EventRsvpView(event['id'], bot, responses)
                if event['status'] == 'scheduled' else None)

    try:
        await msg.edit(embed=_build_event_embed(event, signups, responses), view=view)
        return True
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        return False


async def _spawn_next_occurrence(bot: commands.Bot, event) -> Optional[int]:
    """Create and post the next occurrence of a recurring event.

    Returns the new event id, or None if the series has no next occurrence —
    because it doesn't recur, has run past recurrence_until, or its channel
    is gone. Sign-ups deliberately do not carry over: each occurrence is
    answered fresh.
    """
    if event['recurrence'] not in _RECURRENCE_LABELS:
        return None

    anchor = event['recurrence_anchor'] or event['event_time']
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    # Never behind the event itself, so a series can't spawn into its own past.
    after = max(now, event['event_time'])
    next_time = _next_occurrence(anchor, event['recurrence'], after)
    if next_time is None:
        return None
    if event['recurrence_until'] and next_time > event['recurrence_until']:
        return None
    if not event['channel_id']:
        return None

    channel = bot.get_channel(int(event['channel_id']))
    if channel is None:
        try:
            channel = await bot.fetch_channel(int(event['channel_id']))
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None

    new_id = await database.create_event(
        guild_id=event['guild_id'],
        title=event['title'],
        event_time=next_time,
        created_by=event['created_by'],
        created_by_name=event['created_by_name'],
        description=event['description'],
        duration_minutes=event['duration_minutes'],
        location=event['location'],
        image_url=event['image_url'],
        mention_role_id=event['mention_role_id'],
        reminder_minutes=event['reminder_minutes'],
        recurrence=event['recurrence'],
        recurrence_until=event['recurrence_until'],
        recurrence_anchor=anchor,
    )

    # A series keeps its response set — the next occurrence must look identical.
    custom = await database.get_event_responses(event['id'])
    if custom:
        await database.set_event_responses(new_id, [dict(row) for row in custom])
    responses = await load_responses(new_id)

    new_event = await database.get_event(new_id)
    view = EventRsvpView(new_id, bot, responses)
    try:
        msg = await channel.send(
            content=f"<@&{event['mention_role_id']}>" if event['mention_role_id'] else None,
            embed=_build_event_embed(new_event, [], responses),
            view=view,
        )
    except (discord.Forbidden, discord.HTTPException):
        # Couldn't post it — drop the recurrence so the series stops cleanly
        # rather than retrying every minute forever.
        await database.set_event_status(new_id, 'cancelled')
        return None

    await database.save_event_message(new_id, str(channel.id), str(msg.id))
    bot.add_view(view)
    return new_id


# ---------------------------------------------------------------------------
# RSVP view (persistent — custom_ids encode the event id)
# ---------------------------------------------------------------------------

class EventRsvpView(discord.ui.View):
    """Accept / Tentative / Decline buttons on an event message. Pressing the
    response you already gave withdraws it."""

    def __init__(self, event_id: int, bot: commands.Bot, responses: list = None):
        super().__init__(timeout=None)
        self.event_id = event_id
        self.bot = bot
        self.responses = responses or [dict(r) for r in DEFAULT_RESPONSES]

        for index, item in enumerate(self.responses[:MAX_RESPONSES]):
            if item['is_decline']:
                style = discord.ButtonStyle.danger
            elif index == 0:
                style = discord.ButtonStyle.success
            else:
                style = discord.ButtonStyle.secondary
            button = discord.ui.Button(
                label=item['label'][:80],
                style=style,
                emoji=item['emoji'] or None,
                custom_id=f"event_rsvp:{event_id}:{item['key']}",
                row=index // 5,
            )
            button.callback = self._make_callback(item['key'])
            self.add_item(button)

    def _make_callback(self, response: str):
        async def callback(interaction: discord.Interaction):
            await self._respond(interaction, response)
        return callback

    async def _respond(self, interaction: discord.Interaction, response: str):
        event = await database.get_event(self.event_id)
        if event is None:
            await interaction.response.send_message(
                "❌ This event no longer exists.", ephemeral=True
            )
            return
        if event['status'] != 'scheduled':
            await interaction.response.send_message(
                f"⚠️ This event is **{event['status']}** — sign-ups are closed.",
                ephemeral=True,
            )
            return

        existing = await database.get_event_signup(self.event_id, str(interaction.user.id))
        item = discord.utils.find(lambda r: r['key'] == response, self.responses)
        if item is None:
            await interaction.response.send_message(
                "⚠️ That option no longer exists on this event.", ephemeral=True
            )
            return
        emoji, label = item['emoji'] or '•', item['label']

        if existing and existing['response'] == response:
            await database.remove_event_signup(self.event_id, str(interaction.user.id))
            note = f"↩️ Withdrawn — you're no longer marked as **{label}**."
        else:
            await database.set_event_signup(
                self.event_id, str(interaction.user.id),
                interaction.user.display_name, response,
            )
            note = f"{emoji} You're marked as **{label}** for **{event['title']}**."

        await interaction.response.send_message(note, ephemeral=True)
        # Re-read so the refreshed embed counts this change.
        event = await database.get_event(self.event_id)
        asyncio.create_task(_refresh_event_message(self.bot, event, view=self))


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class EventsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.event_task.start()

    async def cog_unload(self):
        self.event_task.cancel()

    # -- background loop ----------------------------------------------------

    @tasks.loop(minutes=1)
    async def event_task(self):
        for event in await database.get_events_needing_reminder():
            await database.mark_event_reminder_fired(event['id'])
            await self._send_event_reminder(event)

        for event in await database.get_finished_events():
            await database.set_event_status(event['id'], 'completed')
            event = await database.get_event(event['id'])
            # Buttons come off once the event is over.
            await _refresh_event_message(self.bot, event, view=None)
            # A recurring event hands over to its next occurrence here.
            await _spawn_next_occurrence(self.bot, event)

    @event_task.before_loop
    async def before_event_task(self):
        await self.bot.wait_until_ready()

    async def _send_event_reminder(self, event):
        guild = self.bot.get_guild(int(event['guild_id']))
        if guild is None:
            return

        start_ts = int(_as_utc(event['event_time']).timestamp())
        signups = await database.get_event_signups(event['id'])
        responses = await load_responses(event['id'])
        # Anyone whose answer isn't a "not coming" one gets the nudge.
        attending = _attending(signups, responses)

        for row in attending:
            try:
                member = await guild.fetch_member(int(row['member_id']))
                await member.send(
                    f"⏰ **Event Reminder — {event['title']}**\n"
                    f"Starts <t:{start_ts}:R> (<t:{start_ts}:F>).\n"
                    f"Your response: **{_response_label(responses, row['response'])}**"
                )
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                pass

        if not event['channel_id']:
            return
        channel = guild.get_channel(int(event['channel_id']))
        if channel is None:
            return

        mentions = ' '.join(f"<@{row['member_id']}>" for row in attending)
        if event['mention_role_id']:
            mentions = f"<@&{event['mention_role_id']}> {mentions}".strip()
        try:
            await channel.send(
                f"⏰ **{event['title']}** starts <t:{start_ts}:R>!\n{mentions}".strip()
            )
        except (discord.Forbidden, discord.HTTPException):
            pass

    # -- autocomplete -------------------------------------------------------

    async def _event_autocomplete(self, interaction: discord.Interaction, current: str):
        events = await database.get_upcoming_events(str(interaction.guild_id), limit=25)
        needle = current.lower()
        return [
            app_commands.Choice(name=f"#{e['id']} · {e['title']}"[:100], value=e['id'])
            for e in events
            if needle in e['title'].lower() or needle in str(e['id'])
        ][:25]

    # -- commands -----------------------------------------------------------

    @app_commands.command(
        name='event-create',
        description='Create an event members can sign up to (Admin or Unit Leader)',
    )
    @app_commands.guild_only()
    @app_commands.describe(
        title='Event name, e.g. Weekly Training',
        start_time='Start time, e.g. 25/06/2025 19:00 (uses the server timezone)',
        description='What the event is about',
        duration='Length in minutes',
        location='Where it happens, e.g. a voice channel or server name',
        channel='Where to post the event (defaults to the current channel)',
        mention='Role to ping when the reminder fires',
        reminder='How long before the start to remind attendees',
        image_url='Banner image shown on the event',
        repeat='Repeat the event automatically after each occurrence ends',
        repeat_until='Stop repeating after this date, e.g. 31/12/2025 23:59',
        responses="Your own sign-up buttons, e.g. ✅ Coming | ❓ Maybe | -❌ Can't. Prefix with - for 'not coming'",
    )
    @app_commands.choices(reminder=_REMINDER_CHOICES, repeat=_REPEAT_CHOICES)
    async def event_create(
        self,
        interaction: discord.Interaction,
        title: str,
        start_time: str,
        description: str = None,
        duration: int = None,
        location: str = None,
        channel: discord.TextChannel = None,
        mention: discord.Role = None,
        reminder: int = 30,
        image_url: str = None,
        repeat: str = 'none',
        repeat_until: str = None,
        responses: str = None,
    ):
        await interaction.response.defer(ephemeral=True)

        if not _is_unit_leader_or_admin(interaction.user):
            await interaction.followup.send(
                "🚫 You need the **Unit Leader** role or admin permissions to create events.",
                ephemeral=True,
            )
            return

        title = title.strip()
        if not title:
            await interaction.followup.send("❌ Give the event a title.", ephemeral=True)
            return
        if duration is not None and duration <= 0:
            await interaction.followup.send(
                "❌ Duration has to be a positive number of minutes.", ephemeral=True
            )
            return

        try:
            tz_name = await database.get_guild_timezone(str(interaction.guild_id))
            parsed = _parse_event_time(start_time, tz_name)
        except ValueError as e:
            await interaction.followup.send(f"❌ {e}", ephemeral=True)
            return

        if _as_utc(parsed) <= discord.utils.utcnow():
            await interaction.followup.send(
                "❌ That start time is in the past. Events have to start in the future.",
                ephemeral=True,
            )
            return

        custom_responses = None
        if responses:
            try:
                custom_responses = _parse_responses(responses)
            except ValueError as e:
                await interaction.followup.send(f"❌ {e}", ephemeral=True)
                return

        recurrence = repeat if repeat in _RECURRENCE_LABELS else None
        until = None
        if repeat_until:
            if recurrence is None:
                await interaction.followup.send(
                    "❌ `repeat_until` only makes sense together with `repeat`.",
                    ephemeral=True,
                )
                return
            try:
                until = _parse_event_time(repeat_until, tz_name)
            except ValueError as e:
                await interaction.followup.send(f"❌ {e}", ephemeral=True)
                return
            if until <= parsed:
                await interaction.followup.send(
                    "❌ `repeat_until` has to be after the first start time, "
                    "otherwise the event would never repeat.",
                    ephemeral=True,
                )
                return

        target = channel or interaction.channel
        event_id = await database.create_event(
            guild_id=str(interaction.guild_id),
            title=title,
            event_time=parsed,
            created_by=str(interaction.user.id),
            created_by_name=interaction.user.display_name,
            description=description.strip() if description else None,
            duration_minutes=duration,
            location=location.strip() if location else None,
            image_url=image_url.strip() if image_url else None,
            mention_role_id=str(mention.id) if mention else None,
            reminder_minutes=reminder or None,
            recurrence=recurrence,
            recurrence_until=until,
        )

        if custom_responses:
            await database.set_event_responses(event_id, custom_responses)
        active_responses = await load_responses(event_id)

        event = await database.get_event(event_id)
        view = EventRsvpView(event_id, self.bot, active_responses)
        try:
            msg = await target.send(
                content=mention.mention if mention else None,
                embed=_build_event_embed(event, [], active_responses),
                view=view,
            )
        except discord.Forbidden:
            await database.set_event_status(event_id, 'cancelled')
            await interaction.followup.send(
                f"❌ I can't post in {target.mention}, so the event was discarded.",
                ephemeral=True,
            )
            return
        except discord.HTTPException as e:
            # Most likely an image_url Discord won't accept.
            await database.set_event_status(event_id, 'cancelled')
            await interaction.followup.send(
                f"❌ Discord rejected the event message, so it was discarded: `{e}`\n"
                "If you passed an `image_url`, check that it's a direct link to an image.",
                ephemeral=True,
            )
            return

        await database.save_event_message(event_id, str(target.id), str(msg.id))
        self.bot.add_view(view)

        ts = int(_as_utc(parsed).timestamp())
        repeat_line = ""
        if recurrence:
            nxt = _next_occurrence(parsed, recurrence, parsed)
            described = _recurrence_text(
                {'recurrence': recurrence, 'recurrence_anchor': parsed, 'event_time': parsed}
            )
            repeat_line = f"\n🔁 Repeats: **{described}**"
            if until:
                repeat_line += f" until <t:{int(_as_utc(until).timestamp())}:d>"
            # Say so when the first date doesn't itself match the pattern, rather
            # than letting the jump surprise them a month later.
            if recurrence == 'monthly_last' and not _is_last_weekday_of_month(parsed):
                repeat_line += (
                    f"\n⚠️ Note: your first date is the "
                    f"{_ordinal(_weekday_position(parsed))} {_DAY_NAMES[parsed.weekday()]}, "
                    f"not the last one. Every occurrence after it is the **last "
                    f"{_DAY_NAMES[parsed.weekday()]}** of the month."
                )
            if recurrence == 'weekly_not_last' and _is_last_weekday_of_month(parsed):
                repeat_line += (
                    f"\n⚠️ Note: your first date **is** the last "
                    f"{_DAY_NAMES[parsed.weekday()]} of its month, which this pattern "
                    f"normally skips. It stays where you put it; every occurrence "
                    f"after it skips the last {_DAY_NAMES[parsed.weekday()]}."
                )
            if nxt and not (until and nxt > until):
                repeat_line += (
                    f"\nThe next one goes up when this one ends, for "
                    f"<t:{int(_as_utc(nxt).timestamp())}:F>."
                )
        response_line = (
            f"\n🗳️ Sign-up options: {_describe_responses(custom_responses)}"
            if custom_responses else ""
        )
        await interaction.followup.send(
            f"✅ **{title}** created as event **#{event_id}** in {target.mention}.\n"
            f"Starts <t:{ts}:F> (<t:{ts}:R>)."
            + (f"\nReminder {reminder} min before." if reminder else "\nNo reminder set.")
            + repeat_line + response_line,
            ephemeral=True,
        )

    @app_commands.command(
        name='event-edit',
        description='Change an event — organiser or admin only',
    )
    @app_commands.guild_only()
    @app_commands.describe(
        event='The event to change',
        title='New title',
        start_time='New start time, e.g. 25/06/2025 19:00',
        description='New description',
        duration='New length in minutes',
        location='New location',
        reminder='New reminder window',
        repeat='Change the repeat interval, or pick "Don\'t repeat" to stop the series',
        repeat_until='Stop repeating after this date, e.g. 31/12/2025 23:59',
        responses="Replace the sign-up buttons, e.g. ✅ Coming | ❓ Maybe | -❌ Can't",
    )
    @app_commands.choices(reminder=_REMINDER_CHOICES, repeat=_REPEAT_CHOICES)
    @app_commands.autocomplete(event=_event_autocomplete)
    async def event_edit(
        self,
        interaction: discord.Interaction,
        event: int,
        title: str = None,
        start_time: str = None,
        description: str = None,
        duration: int = None,
        location: str = None,
        reminder: int = None,
        repeat: str = None,
        repeat_until: str = None,
        responses: str = None,
    ):
        await interaction.response.defer(ephemeral=True)

        record = await database.get_event(event)
        if record is None or record['guild_id'] != str(interaction.guild_id):
            await interaction.followup.send("❌ No such event on this server.", ephemeral=True)
            return
        if not _is_organiser(interaction.user, record):
            await interaction.followup.send(
                "🚫 Only the organiser or an admin can change this event.", ephemeral=True
            )
            return
        if record['status'] != 'scheduled':
            await interaction.followup.send(
                f"⚠️ Event #{event} is **{record['status']}** and can't be changed.",
                ephemeral=True,
            )
            return

        parsed = None
        if start_time:
            try:
                tz_name = await database.get_guild_timezone(str(interaction.guild_id))
                parsed = _parse_event_time(start_time, tz_name)
            except ValueError as e:
                await interaction.followup.send(f"❌ {e}", ephemeral=True)
                return
            if _as_utc(parsed) <= discord.utils.utcnow():
                await interaction.followup.send(
                    "❌ That start time is in the past.", ephemeral=True
                )
                return

        if duration is not None and duration <= 0:
            await interaction.followup.send(
                "❌ Duration has to be a positive number of minutes.", ephemeral=True
            )
            return

        until = None
        if repeat_until:
            if repeat == 'none':
                await interaction.followup.send(
                    "❌ `repeat_until` conflicts with stopping the repeat. "
                    "Pass one or the other.",
                    ephemeral=True,
                )
                return
            try:
                until = _parse_event_time(repeat_until, tz_name if start_time else
                                          await database.get_guild_timezone(str(interaction.guild_id)))
            except ValueError as e:
                await interaction.followup.send(f"❌ {e}", ephemeral=True)
                return

        new_responses = None
        if responses:
            try:
                new_responses = _parse_responses(responses)
            except ValueError as e:
                await interaction.followup.send(f"❌ {e}", ephemeral=True)
                return

        changed = [
            name for name, value in (
                ('title', title), ('start time', parsed), ('description', description),
                ('duration', duration), ('location', location), ('reminder', reminder),
                ('repeat', repeat), ('repeat end', until),
                ('sign-up options', new_responses),
            ) if value is not None
        ]
        if not changed:
            await interaction.followup.send(
                "ℹ️ Nothing to change — pass at least one field.", ephemeral=True
            )
            return

        await database.update_event(
            event,
            title=title.strip() if title else None,
            description=description.strip() if description else None,
            event_time=parsed,
            duration_minutes=duration,
            location=location.strip() if location else None,
            reminder_minutes=reminder,
        )

        repeat_note = ""
        if repeat is not None or until is not None:
            new_rec = record['recurrence'] if repeat is None else (
                repeat if repeat in _RECURRENCE_LABELS else None
            )
            if new_rec is None:
                await database.set_event_recurrence(event, None, None, None)
                repeat_note = "\n🔁 The series is stopped — no further occurrences will be posted."
            else:
                # Re-anchor when the start time moved, so the series follows it.
                anchor = parsed or record['recurrence_anchor'] or record['event_time']
                new_until = until if until is not None else record['recurrence_until']
                await database.set_event_recurrence(event, new_rec, new_until, anchor)
                described = _recurrence_text(
                    {'recurrence': new_rec, 'recurrence_anchor': anchor,
                     'event_time': anchor}
                )
                repeat_note = f"\n🔁 Now repeats: **{described}**"
                if new_until:
                    repeat_note += f" until <t:{int(_as_utc(new_until).timestamp())}:d>"
                repeat_note += "."

        response_note = ""
        if new_responses:
            await database.set_event_responses(event, new_responses)
            # Answers people gave under the old options would otherwise survive
            # in the table while rendering nowhere.
            dropped = await database.drop_signups_not_in(
                event, [item['key'] for item in new_responses]
            )
            response_note = f"\n🗳️ Sign-up options: {_describe_responses(new_responses)}"
            if dropped:
                response_note += (
                    f"\n⚠️ **{dropped}** existing sign-up(s) used an option that no longer "
                    "exists and were cleared — those members need to answer again."
                )

        record = await database.get_event(event)
        reached = await _refresh_event_message(self.bot, record)
        note = "" if reached else "\n⚠️ I couldn't update the event message — it may have been deleted."
        if parsed:
            note += "\nThe reminder was re-armed for the new time."
        note += repeat_note + response_note
        await interaction.followup.send(
            f"✅ Updated **{record['title']}** (#{event}): {', '.join(changed)}.{note}",
            ephemeral=True,
        )

    @app_commands.command(
        name='event-cancel',
        description='Cancel an event and notify everyone who signed up',
    )
    @app_commands.guild_only()
    @app_commands.describe(
        event='The event to cancel',
        reason='Why it is being cancelled',
        stop_series='For a repeating event: also stop all future occurrences',
    )
    @app_commands.autocomplete(event=_event_autocomplete)
    async def event_cancel(
        self, interaction: discord.Interaction, event: int, reason: str = None,
        stop_series: bool = False,
    ):
        await interaction.response.defer(ephemeral=True)

        record = await database.get_event(event)
        if record is None or record['guild_id'] != str(interaction.guild_id):
            await interaction.followup.send("❌ No such event on this server.", ephemeral=True)
            return
        if not _is_organiser(interaction.user, record):
            await interaction.followup.send(
                "🚫 Only the organiser or an admin can cancel this event.", ephemeral=True
            )
            return
        if record['status'] != 'scheduled':
            await interaction.followup.send(
                f"ℹ️ Event #{event} is already **{record['status']}**.", ephemeral=True
            )
            return

        recurring = record['recurrence'] in _RECURRENCE_LABELS
        if recurring and stop_series:
            # Clear it first so nothing can spawn a successor afterwards.
            await database.set_event_recurrence(event, None, None, None)
            record = await database.get_event(event)

        await database.set_event_status(event, 'cancelled')
        record = await database.get_event(event)
        await _refresh_event_message(self.bot, record, view=None)

        series_note = ""
        if recurring:
            if stop_series:
                series_note = "\n🔁 The whole series is stopped — no further occurrences."
            else:
                next_id = await _spawn_next_occurrence(self.bot, record)
                if next_id:
                    nxt = await database.get_event(next_id)
                    ts = int(_as_utc(nxt['event_time']).timestamp())
                    series_note = (
                        f"\n🔁 Only this occurrence was cancelled. The next one is up "
                        f"as **#{next_id}** for <t:{ts}:F>."
                    )
                else:
                    series_note = (
                        "\n🔁 This was the last occurrence in the series — nothing follows it."
                    )

        signups = await database.get_event_signups(event)
        notify = _attending(signups, await load_responses(event))
        reason_line = f"\nReason: {reason}" if reason else ""
        for row in notify:
            try:
                member = await interaction.guild.fetch_member(int(row['member_id']))
                await member.send(
                    f"❌ **Event Cancelled — {record['title']}**\n"
                    f"Cancelled by {interaction.user.display_name}.{reason_line}"
                )
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                pass

        await interaction.followup.send(
            f"✅ Cancelled **{record['title']}** (#{event}) and notified "
            f"**{len(notify)}** attendee(s).{series_note}",
            ephemeral=True,
        )

    async def _any_event_autocomplete(self, interaction: discord.Interaction, current: str):
        """Unlike the others this offers cancelled and finished events too — those
        are exactly the ones you clean up."""
        events = await database.get_guild_events(str(interaction.guild_id), limit=25)
        needle = current.lower()
        marks = {'scheduled': '📅', 'cancelled': '❌', 'completed': '🏁'}
        return [
            app_commands.Choice(
                name=f"{marks.get(e['status'], '•')} #{e['id']} · {e['title']}"[:100],
                value=e['id'],
            )
            for e in events
            if needle in e['title'].lower() or needle in str(e['id'])
        ][:25]

    @app_commands.command(
        name='event-delete',
        description='Delete an event and its message for good — organiser or admin only',
    )
    @app_commands.guild_only()
    @app_commands.describe(event='The event to delete, including cancelled and finished ones')
    @app_commands.autocomplete(event=_any_event_autocomplete)
    async def event_delete(self, interaction: discord.Interaction, event: int):
        await interaction.response.defer(ephemeral=True)

        record = await database.get_event(event)
        if record is None or record['guild_id'] != str(interaction.guild_id):
            await interaction.followup.send("❌ No such event on this server.", ephemeral=True)
            return
        if not _is_organiser(interaction.user, record):
            await interaction.followup.send(
                "🚫 Only the organiser or an admin can delete this event.", ephemeral=True
            )
            return

        signups = await database.get_event_signups(event)
        responses = await load_responses(event)
        attending = _attending(signups, responses)

        warning = ""
        if record['status'] == 'scheduled' and attending:
            warning = (
                f"\n\n⚠️ **{len(attending)}** member(s) are signed up and **will not be "
                "told**. If this event was real, use `/event-cancel` instead — that keeps "
                "the record and DMs everyone."
            )
        if record['recurrence'] in _RECURRENCE_LABELS:
            warning += "\n🔁 This is a repeating event; deleting it ends the series."

        ts = int(_as_utc(record['event_time']).timestamp())
        embed = discord.Embed(
            title='⚠️ Delete Event — Confirmation',
            description=(
                f"**{record['title']}** (#{event}) — <t:{ts}:F>\n"
                f"Status: **{record['status']}** · **{len(signups)}** sign-up(s)\n\n"
                "This removes the event message and every sign-up on it. "
                "**It cannot be undone.**" + warning
            ),
            color=discord.Color.red(),
        )

        bot_ref = self.bot

        class ConfirmDeleteView(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=60)

            @discord.ui.button(label='Yes, delete it', style=discord.ButtonStyle.danger, emoji='🗑️')
            async def confirm(self, btn: discord.Interaction, _button: discord.ui.Button):
                self.stop()
                current = await database.get_event(event)
                if current is None:
                    await btn.response.edit_message(
                        content="ℹ️ That event is already gone.", embed=None, view=None
                    )
                    return
                removed_message = await _delete_event_message(bot_ref, current)
                await database.delete_event(event)
                note = "" if removed_message else (
                    "\n⚠️ The message couldn't be removed — it may already be deleted, "
                    "or I lack permission in that channel."
                )
                await btn.response.edit_message(
                    content=(
                        f"🗑️ Deleted **{current['title']}** (#{event}) and its "
                        f"**{len(signups)}** sign-up(s).{note}"
                    ),
                    embed=None, view=None,
                )

            @discord.ui.button(label='Keep it', style=discord.ButtonStyle.secondary, emoji='✖️')
            async def keep(self, btn: discord.Interaction, _button: discord.ui.Button):
                self.stop()
                await btn.response.edit_message(
                    content="Nothing deleted.", embed=None, view=None
                )

        await interaction.followup.send(embed=embed, view=ConfirmDeleteView(), ephemeral=True)

    @app_commands.command(
        name='event-list',
        description='Show the upcoming events on this server',
    )
    @app_commands.guild_only()
    async def event_list(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        events = await database.get_upcoming_events(str(interaction.guild_id), limit=25)
        if not events:
            await interaction.followup.send(
                "ℹ️ No upcoming events. An Admin or Unit Leader can create one "
                "with `/event-create`.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title='📅 Upcoming Events',
            color=discord.Color.blurple(),
        )
        for record in events:
            ts = int(_as_utc(record['event_time']).timestamp())
            signups = await database.get_event_signups(record['id'])
            responses = await load_responses(record['id'])
            grouped = _group_signups(signups, responses)
            counts = ' · '.join(
                f"{item['emoji'] or item['label']} {len(grouped[item['key']])}"
                for item in responses
            )
            link = ''
            if record['channel_id'] and record['message_id']:
                link = (
                    f" · [jump](https://discord.com/channels/"
                    f"{record['guild_id']}/{record['channel_id']}/{record['message_id']})"
                )
            repeat = (
                f" · 🔁 {_recurrence_text(record)}"
                if record['recurrence'] in _RECURRENCE_LABELS else ''
            )
            embed.add_field(
                name=f"#{record['id']} · {record['title']}"[:256],
                value=f"<t:{ts}:F> (<t:{ts}:R>)\n{counts}{repeat}{link}",
                inline=False,
            )
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(EventsCog(bot))

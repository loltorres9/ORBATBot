import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from utils import database
from cogs.admin import _is_unit_leader_or_admin, _parse_event_time

# Response keys stored in event_signups.response, in the order they're displayed.
RESPONSES = ('accepted', 'tentative', 'declined')

_RESPONSE_LABELS = {
    'accepted': ('✅', 'Accepted'),
    'tentative': ('❓', 'Tentative'),
    'declined': ('❌', 'Declined'),
}

_REMINDER_CHOICES = [
    app_commands.Choice(name='No reminder', value=0),
    app_commands.Choice(name='15 minutes before', value=15),
    app_commands.Choice(name='30 minutes before', value=30),
    app_commands.Choice(name='60 minutes before', value=60),
    app_commands.Choice(name='2 hours before', value=120),
    app_commands.Choice(name='24 hours before', value=1440),
]


def _as_utc(dt: datetime) -> datetime:
    """Attach UTC to the naive timestamps we store, so .timestamp() is correct."""
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _is_organiser(member: discord.Member, event) -> bool:
    """The event's creator can manage it; admins can manage anyone's."""
    perms = member.guild_permissions
    if perms.manage_guild or perms.administrator:
        return True
    return str(member.id) == event['created_by']


def _group_signups(signups: list) -> dict:
    grouped = {key: [] for key in RESPONSES}
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


def _build_event_embed(event, signups: list) -> discord.Embed:
    grouped = _group_signups(signups)
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

    for key in RESPONSES:
        emoji, label = _RESPONSE_LABELS[key]
        rows = grouped[key]
        embed.add_field(
            name=f"{emoji} {label} ({len(rows)})",
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
    if view is None:
        view = EventRsvpView(event['id'], bot) if event['status'] == 'scheduled' else None

    try:
        await msg.edit(embed=_build_event_embed(event, signups), view=view)
        return True
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        return False


# ---------------------------------------------------------------------------
# RSVP view (persistent — custom_ids encode the event id)
# ---------------------------------------------------------------------------

class EventRsvpView(discord.ui.View):
    """Accept / Tentative / Decline buttons on an event message. Pressing the
    response you already gave withdraws it."""

    def __init__(self, event_id: int, bot: commands.Bot):
        super().__init__(timeout=None)
        self.event_id = event_id
        self.bot = bot

        styles = {
            'accepted': discord.ButtonStyle.success,
            'tentative': discord.ButtonStyle.secondary,
            'declined': discord.ButtonStyle.danger,
        }
        for key in RESPONSES:
            emoji, label = _RESPONSE_LABELS[key]
            button = discord.ui.Button(
                label=label,
                style=styles[key],
                emoji=emoji,
                custom_id=f'event_rsvp:{event_id}:{key}',
            )
            button.callback = self._make_callback(key)
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
        emoji, label = _RESPONSE_LABELS[response]

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

    @event_task.before_loop
    async def before_event_task(self):
        await self.bot.wait_until_ready()

    async def _send_event_reminder(self, event):
        guild = self.bot.get_guild(int(event['guild_id']))
        if guild is None:
            return

        start_ts = int(_as_utc(event['event_time']).timestamp())
        signups = await database.get_event_signups(event['id'])
        # Anyone who said yes or maybe gets the nudge; people who declined don't.
        attending = [s for s in signups if s['response'] in ('accepted', 'tentative')]

        for row in attending:
            try:
                member = await guild.fetch_member(int(row['member_id']))
                await member.send(
                    f"⏰ **Event Reminder — {event['title']}**\n"
                    f"Starts <t:{start_ts}:R> (<t:{start_ts}:F>).\n"
                    f"Your response: **{_RESPONSE_LABELS[row['response']][1]}**"
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
    )
    @app_commands.choices(reminder=_REMINDER_CHOICES)
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
        )

        event = await database.get_event(event_id)
        view = EventRsvpView(event_id, self.bot)
        try:
            msg = await target.send(
                content=mention.mention if mention else None,
                embed=_build_event_embed(event, []),
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
        await interaction.followup.send(
            f"✅ **{title}** created as event **#{event_id}** in {target.mention}.\n"
            f"Starts <t:{ts}:F> (<t:{ts}:R>)."
            + (f"\nReminder {reminder} min before." if reminder else "\nNo reminder set."),
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
    )
    @app_commands.choices(reminder=_REMINDER_CHOICES)
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

        changed = [
            name for name, value in (
                ('title', title), ('start time', parsed), ('description', description),
                ('duration', duration), ('location', location), ('reminder', reminder),
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

        record = await database.get_event(event)
        reached = await _refresh_event_message(self.bot, record)
        note = "" if reached else "\n⚠️ I couldn't update the event message — it may have been deleted."
        if parsed:
            note += "\nThe reminder was re-armed for the new time."
        await interaction.followup.send(
            f"✅ Updated **{record['title']}** (#{event}): {', '.join(changed)}.{note}",
            ephemeral=True,
        )

    @app_commands.command(
        name='event-cancel',
        description='Cancel an event and notify everyone who signed up',
    )
    @app_commands.guild_only()
    @app_commands.describe(event='The event to cancel', reason='Why it is being cancelled')
    @app_commands.autocomplete(event=_event_autocomplete)
    async def event_cancel(
        self, interaction: discord.Interaction, event: int, reason: str = None
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

        await database.set_event_status(event, 'cancelled')
        record = await database.get_event(event)
        await _refresh_event_message(self.bot, record, view=None)

        signups = await database.get_event_signups(event)
        notify = [s for s in signups if s['response'] in ('accepted', 'tentative')]
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
            f"**{len(notify)}** attendee(s).",
            ephemeral=True,
        )

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
            grouped = _group_signups(signups)
            counts = ' · '.join(
                f"{_RESPONSE_LABELS[key][0]} {len(grouped[key])}" for key in RESPONSES
            )
            link = ''
            if record['channel_id'] and record['message_id']:
                link = (
                    f" · [jump](https://discord.com/channels/"
                    f"{record['guild_id']}/{record['channel_id']}/{record['message_id']})"
                )
            embed.add_field(
                name=f"#{record['id']} · {record['title']}"[:256],
                value=f"<t:{ts}:F> (<t:{ts}:R>)\n{counts}{link}",
                inline=False,
            )
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(EventsCog(bot))

import asyncio
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from utils import database, roster, sheets
from cogs.slots import (_get_unit_role, _update_orbat, ActionError,
                       assign_slot_request, check_can_assign,
                       clear_pending_queue, clear_slot_request, guild_channel,
                       publish_board, SquadSelectView, UNIT_LEADER_ROLE)


def _is_unit_leader_or_admin(member: discord.Member) -> bool:
    if member.guild_permissions.manage_guild or member.guild_permissions.administrator:
        return True
    return any(r.name == UNIT_LEADER_ROLE for r in member.roles)

_EVENT_TIME_FORMATS = ['%d/%m/%Y %H:%M', '%Y-%m-%d %H:%M', '%d-%m-%Y %H:%M']

# Common timezone choices (max 25 for Discord)
_TIMEZONE_CHOICES = [
    app_commands.Choice(name='UTC',                       value='UTC'),
    app_commands.Choice(name='London (GMT/BST)',          value='Europe/London'),
    app_commands.Choice(name='Amsterdam/Paris/Berlin (CET/CEST)', value='Europe/Amsterdam'),
    app_commands.Choice(name='Helsinki/Kyiv (EET/EEST)',  value='Europe/Helsinki'),
    app_commands.Choice(name='Moscow (MSK)',               value='Europe/Moscow'),
    app_commands.Choice(name='Dubai (GST)',                value='Asia/Dubai'),
    app_commands.Choice(name='Karachi (PKT)',              value='Asia/Karachi'),
    app_commands.Choice(name='Bangkok (ICT)',              value='Asia/Bangkok'),
    app_commands.Choice(name='Singapore/KL (SGT)',         value='Asia/Singapore'),
    app_commands.Choice(name='Tokyo (JST)',                value='Asia/Tokyo'),
    app_commands.Choice(name='Sydney (AEST/AEDT)',         value='Australia/Sydney'),
    app_commands.Choice(name='Auckland (NZST/NZDT)',       value='Pacific/Auckland'),
    app_commands.Choice(name='New York (EST/EDT)',         value='America/New_York'),
    app_commands.Choice(name='Chicago (CST/CDT)',          value='America/Chicago'),
    app_commands.Choice(name='Denver (MST/MDT)',           value='America/Denver'),
    app_commands.Choice(name='Los Angeles (PST/PDT)',      value='America/Los_Angeles'),
]


def _parse_event_time(raw: str, tz_name: str = 'UTC') -> datetime:
    """Parse event time in the given timezone, return as naive UTC for storage."""
    raw = raw.strip()
    try:
        tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        tz = ZoneInfo('UTC')

    for fmt in _EVENT_TIME_FORMATS:
        try:
            local_dt = datetime.strptime(raw, fmt).replace(tzinfo=tz)
            # Convert to naive UTC for DB storage
            return local_dt.astimezone(ZoneInfo('UTC')).replace(tzinfo=None)
        except ValueError:
            continue
    raise ValueError(
        f"Could not parse `{raw}`.\n"
        "Use format `DD/MM/YYYY HH:MM`, e.g. `25/06/2025 19:00`"
    )

ORBAT_CHANNEL_NAME = 'orbat'


# ---------------------------------------------------------------------------
# Running an operation
#
# `/setup-slots`, `/set-event-time` and `/post-event` are thin callers of the
# three functions below, and so is the Operation page on the web. Everything
# these do — reading the roster, deactivating the previous operation, posting
# the board, re-arming the reminder — therefore happens once, wherever it was
# asked for. `ActionError` carries the reasons a person can get wrong.
# ---------------------------------------------------------------------------

REMINDER_CHOICES = (15, 30, 60)


async def start_operation(bot, guild: discord.Guild, *, orbat_id: int = None,
                          sheet_url: str = None, name: str = None,
                          event_time=None, reminder_minutes: int = 30) -> dict:
    """Load a roster and make it this guild's active operation.

    Exactly one of *orbat_id* and *sheet_url* — the two rosters an operation can
    run on. *event_time* is naive UTC, already parsed: the two surfaces read a
    date very differently and neither should be doing it in here.

    Returns the new operation, its slot count, and the channel the board was
    posted to (None when there was nowhere to post it — non-fatal, the same as
    it always was, because the operation itself is loaded either way).
    """
    if bool(orbat_id) == bool(sheet_url):
        raise ActionError('Give either an ORBAT or a sheet URL — not both, not neither.')
    if reminder_minutes not in REMINDER_CHOICES:
        raise ActionError(
            f"Reminder must be one of {', '.join(str(m) for m in REMINDER_CHOICES)} minutes."
        )

    new_operation = dict(guild_id=str(guild.id))
    if orbat_id:
        record = await database.get_orbat(orbat_id)
        if record is None or record['guild_id'] != str(guild.id):
            raise ActionError('No such ORBAT on this server.')
        squads = await database.get_orbat_structure(orbat_id)
        slot_count = sum(len(squad['slots']) for squad in squads)
        if not slot_count:
            raise ActionError(
                f"{record['name']} has no slots yet — write the roster on the website first."
            )
        new_operation.update(name=(name or '').strip() or record['name'],
                             orbat_id=orbat_id)
    else:
        try:
            loop = asyncio.get_event_loop()
            data = await asyncio.wait_for(
                loop.run_in_executor(None, sheets.load_slots, sheet_url), timeout=30,
            )
        except asyncio.TimeoutError:
            raise ActionError(
                'Timed out reading the sheet (30s). Make sure it is shared with '
                'the service account.'
            )
        except ValueError as e:
            raise ActionError(str(e))
        except Exception as e:
            raise ActionError(
                f"Failed to read the sheet: {e}. Make sure you have shared it "
                'with the service account.'
            )
        slot_count = len(data['slots'])
        new_operation.update(
            name=(name or '').strip() or data['operation_name'],
            sheet_url=sheet_url, sheet_id=data['sheet_id'],
            squad_col=data['squad_col'], role_col=data['role_col'],
            status_col=data['status_col'], assigned_col=data['assigned_col'],
        )

    try:
        op_id = await database.create_operation(**new_operation)
        if event_time:
            await database.set_event_time(op_id, event_time, reminder_minutes)
    except Exception as e:
        raise ActionError(f"Database error: {e}")

    # Re-read rather than reuse what went in: `create_operation()` deactivated
    # the previous operation and `set_event_time()` may have written a time, and
    # the board has to be drawn from the row as it now stands.
    op = await database.get_operation(op_id)

    channel = await guild_channel(guild, 'orbat')
    if channel:
        try:
            await publish_board(bot, guild, channel, op)
        except Exception:
            channel = None      # Non-fatal: the operation is loaded regardless.

    return {'operation': op, 'slot_count': slot_count, 'channel': channel}


async def set_operation_time(bot, guild: discord.Guild, op, event_time,
                             reminder_minutes: int = 30):
    """Move an operation's start time and re-arm its reminder.

    `set_event_time()` resets `reminder_fired`, which is the point: a time that
    moved has to be announced again.
    """
    if reminder_minutes not in REMINDER_CHOICES:
        raise ActionError(
            f"Reminder must be one of {', '.join(str(m) for m in REMINDER_CHOICES)} minutes."
        )
    await database.set_event_time(op['id'], event_time, reminder_minutes)
    # Re-read so the redrawn board carries the new time.
    op = await database.get_operation(op['id'])
    asyncio.create_task(_update_orbat(bot, guild, op))
    return op


async def build_announcement_embed(guild: discord.Guild, mission_name: str,
                                   event_time, posted_by: str) -> discord.Embed:
    """The `/post-event` announcement — a mission name, when it starts, and
    where to sign up. *event_time* may be naive UTC or aware; None omits the row.
    """
    embed = discord.Embed(title=f"🎖️ {mission_name}", color=discord.Color.dark_red())

    if event_time is not None:
        when = event_time
        if getattr(when, 'tzinfo', None) is None:
            when = when.replace(tzinfo=timezone.utc)
        ts = int(when.timestamp())
        embed.add_field(name='🕐 Operation starts',
                        value=f"<t:{ts}:F>  (<t:{ts}:R>)", inline=False)

    orbat_channel = await guild_channel(guild, 'orbat', create=False)
    orbat_ref = orbat_channel.mention if orbat_channel else '`#orbat`'
    embed.add_field(
        name='📋 Sign up',
        value=f'Head to {orbat_ref} to view available slots and request your position.',
        inline=False,
    )
    embed.set_footer(text=f'Posted by {posted_by}')
    embed.timestamp = discord.utils.utcnow()
    return embed

_RAILWAY_API_URL = 'https://backboard.railway.com/graphql/v2'


async def _railway_restart() -> str:
    """Restart the currently running Railway deployment via the public GraphQL API.

    Returns the ID of the restarted deployment.

    Requires RAILWAY_API_TOKEN (set manually in the service variables).
    RAILWAY_DEPLOYMENT_ID / RAILWAY_SERVICE_ID / RAILWAY_ENVIRONMENT_ID are
    injected automatically by Railway at runtime.
    """
    token = os.getenv('RAILWAY_API_TOKEN')
    if not token:
        raise RuntimeError('RAILWAY_API_TOKEN is not set')

    headers = {'Authorization': f'Bearer {token}'}
    timeout = aiohttp.ClientTimeout(total=15)

    async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
        # Railway injects the running container's own deployment ID — using it
        # guarantees we restart ourselves, not whatever a list query returns.
        deployment_id = os.getenv('RAILWAY_DEPLOYMENT_ID')
        if not deployment_id:
            service_id = os.getenv('RAILWAY_SERVICE_ID')
            environment_id = os.getenv('RAILWAY_ENVIRONMENT_ID')
            if not (service_id and environment_id):
                raise RuntimeError(
                    'RAILWAY_DEPLOYMENT_ID / RAILWAY_SERVICE_ID not found — not running on Railway?'
                )
            list_input = {
                'serviceId': service_id,
                'environmentId': environment_id,
                'status': {'in': ['SUCCESS']},
            }
            project_id = os.getenv('RAILWAY_PROJECT_ID')
            if project_id:
                list_input['projectId'] = project_id
            query = (
                'query Deployments($input: DeploymentListInput!) {'
                '  deployments(input: $input, first: 1) {'
                '    edges { node { id status } }'
                '  }'
                '}'
            )
            async with session.post(
                _RAILWAY_API_URL, json={'query': query, 'variables': {'input': list_input}}
            ) as resp:
                data = await resp.json()
            if data.get('errors'):
                raise RuntimeError(data['errors'][0].get('message', 'Railway API error'))

            edges = data['data']['deployments']['edges']
            if not edges:
                raise RuntimeError('No active deployment found for this service')
            deployment_id = edges[0]['node']['id']

        print(f"Triggering Railway restart for deployment {deployment_id}")
        mutation = 'mutation Restart($id: String!) { deploymentRestart(id: $id) }'
        async with session.post(
            _RAILWAY_API_URL, json={'query': mutation, 'variables': {'id': deployment_id}}
        ) as resp:
            data = await resp.json()
        if data.get('errors'):
            raise RuntimeError(data['errors'][0].get('message', 'Railway API error'))
        if not data['data'].get('deploymentRestart'):
            raise RuntimeError('Railway API did not confirm the restart')
        return deployment_id


class _NothingToRepair(Exception):
    """An ORBAT-backed operation has no sheet coordinates to go stale."""


class AdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _orbat_autocomplete(self, interaction: discord.Interaction, current: str):
        orbats = await database.get_guild_orbats(str(interaction.guild_id))
        needle = current.lower()
        return [
            app_commands.Choice(
                name=f"{o['name']} · {o['slot_count']} slots"[:100], value=o['id']
            )
            for o in orbats if needle in o['name'].lower()
        ][:25]

    @app_commands.command(
        name='setup-slots',
        description='Start an operation from an ORBAT or a Google Sheet (Admin only)',
    )
    @app_commands.describe(
        orbat='An ORBAT built on the website — leave the sheet URL empty when using this',
        sheet_url='Full Google Sheets URL, for the old sheet-backed way',
        event_time='Event start time in UTC, e.g. 25/06/2025 19:00',
        reminder_minutes='Send reminders this many minutes before the event (15, 30, or 60)',
        name='Operation name — defaults to the ORBAT\'s name',
    )
    @app_commands.autocomplete(orbat=_orbat_autocomplete)
    @app_commands.choices(reminder_minutes=[
        app_commands.Choice(name='15 minutes before', value=15),
        app_commands.Choice(name='30 minutes before', value=30),
        app_commands.Choice(name='60 minutes before', value=60),
    ])
    @app_commands.default_permissions(manage_guild=True)
    async def setup_slots(
        self,
        interaction: discord.Interaction,
        orbat: int = None,
        sheet_url: str = None,
        event_time: str = None,
        reminder_minutes: int = 30,
        name: str = None,
    ):
        """Start an operation on one roster or the other.

        Both paths end in the same `operations` row and the same live board —
        `utils/roster.py` is the only thing that knows which one it reads.
        """
        await interaction.response.defer(ephemeral=True)

        parsed_event_time = None
        if event_time:
            try:
                tz_name = await database.get_guild_timezone(str(interaction.guild_id))
                parsed_event_time = _parse_event_time(event_time, tz_name)
            except ValueError as e:
                await interaction.followup.send(f"❌ {e}", ephemeral=True)
                return

        try:
            result = await start_operation(
                self.bot, interaction.guild, orbat_id=orbat, sheet_url=sheet_url,
                name=name, event_time=parsed_event_time,
                reminder_minutes=reminder_minutes,
            )
        except ActionError as e:
            await interaction.followup.send(f"❌ {e}", ephemeral=True)
            return

        event_line = (
            f"\n🕐 Event time: <t:{int(parsed_event_time.replace(tzinfo=timezone.utc).timestamp())}:F> "
            f"(reminder {reminder_minutes} min before)"
            if parsed_event_time else ""
        )
        confirm_embed = discord.Embed(
            title='✅ Operation Loaded',
            description=(
                f"**{result['operation']['name']}**\n"
                f"Found **{result['slot_count']}** slot(s)."
                f"{event_line}\n\n"
                f"Members can now use `/request-slot` to sign up."
            ),
            color=discord.Color.green(),
        )
        if result['channel']:
            confirm_embed.description += f"\n\n📋 ORBAT posted to {result['channel'].mention}."

        await interaction.followup.send(embed=confirm_embed, ephemeral=True)

    @app_commands.command(
        name='clear-slot',
        description='Remove a member from an approved slot (Admin or Unit Leader)',
    )
    async def clear_slot(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        if not _is_unit_leader_or_admin(interaction.user):
            await interaction.followup.send(
                "🚫 You need the **Unit Leader** role or admin permissions to use this command.",
                ephemeral=True,
            )
            return

        op = await database.get_active_operation(str(interaction.guild_id))
        if not op:
            await interaction.followup.send("❌ No active operation.", ephemeral=True)
            return

        active = await database.get_active_requests(op['id'])

        # Unit Leaders can only clear slots belonging to their own unit
        is_admin = interaction.user.guild_permissions.manage_guild or interaction.user.guild_permissions.administrator
        if not is_admin:
            leader_unit = _get_unit_role(interaction.user)
            if not leader_unit:
                await interaction.followup.send(
                    "🚫 You need a unit role (e.g. 2nd USC) alongside **Unit Leader** to use this command.",
                    ephemeral=True,
                )
                return
            active = [r for r in active if r['unit_role'] == leader_unit]

        if not active:
            await interaction.followup.send(
                "ℹ️ No active slots to clear.", ephemeral=True
            )
            return

        options = [
            discord.SelectOption(
                label=f"{req['member_name']} — {req['slot_label']}"[:100],
                value=str(req['id']),
                description=f"{'✅ approved' if req['status'] == 'approved' else '⏳ pending'}",
            )
            for req in active[:25]
        ]

        select = discord.ui.Select(
            placeholder='Select a slot to clear…',
            options=options,
            min_values=1,
            max_values=1,
        )

        bot_ref = self.bot

        async def _select_callback(sel_interaction: discord.Interaction):
            # Deferred first: the shared path writes the sheet, DMs the member
            # and redraws the board, which is well past Discord's three seconds.
            await sel_interaction.response.defer(ephemeral=True)
            try:
                result = await clear_slot_request(
                    bot_ref, sel_interaction.guild,
                    int(sel_interaction.data['values'][0]), sel_interaction.user,
                )
            except ActionError as e:
                await sel_interaction.followup.send(f"⚠️ {e}", ephemeral=True)
                return

            req = result['request']
            status_word = 'approved slot' if result['was_approved'] else 'pending request'
            await sel_interaction.followup.send(
                f"✅ Cleared {status_word} **{req['slot_label']}** for **{req['member_name']}**.",
                ephemeral=True,
            )

        select.callback = _select_callback
        view = discord.ui.View(timeout=120)
        view.add_item(select)
        await interaction.followup.send(
            "Select the slot to clear:", view=view, ephemeral=True
        )

    @app_commands.command(
        name='debug-slots',
        description='Show raw slot data the bot reads from the sheet — use to diagnose missing slots (Admin only)',
    )
    @app_commands.describe(squad='Filter to a specific squad name (optional)')
    @app_commands.default_permissions(manage_guild=True)
    async def debug_slots(self, interaction: discord.Interaction, squad: str = None):
        await interaction.response.defer(ephemeral=True)
        op = await database.get_active_operation(str(interaction.guild_id))
        if not op:
            await interaction.followup.send("❌ No active operation.", ephemeral=True)
            return
        try:
            data = await roster.load_available(op)
        except Exception as e:
            await interaction.followup.send(f"❌ Failed to load the roster: `{e}`", ephemeral=True)
            return

        slots = data['slots']
        if squad:
            slots = [s for s in slots if squad.lower() in s['squad'].lower()]

        if not slots:
            await interaction.followup.send(
                f"No available slots found{f' for squad matching `{squad}`' if squad else ''}.\n"
                "On a sheet that means no `<Insert Name>` cells were found; on an "
                "ORBAT it means every slot is taken.",
                ephemeral=True,
            )
            return

        source = 'ORBAT' if roster.is_db_backed(op) else 'sheet'
        lines = [f"**{len(slots)} available slot(s) found** ({source} → bot view):\n"]
        for s in slots[:40]:
            lines.append(f"`{s['key']}` **{s['squad']}** — {s['role']}")
        if len(slots) > 40:
            lines.append(f"_…and {len(slots) - 40} more_")

        await interaction.followup.send('\n'.join(lines), ephemeral=True)

    @app_commands.command(
        name='current-operation',
        description='Show which operation is currently active (Admin only)',
    )
    @app_commands.default_permissions(manage_guild=True)
    async def current_operation(self, interaction: discord.Interaction):
        op = await database.get_active_operation(str(interaction.guild_id))
        if not op:
            await interaction.response.send_message(
                "No active operation. An admin can load one with `/setup-slots`.",
                ephemeral=True,
            )
            return

        if roster.is_db_backed(op):
            record = await database.get_orbat(op['orbat_id'])
            source = f"ORBAT: **{record['name']}**" if record else 'ORBAT (deleted)'
        else:
            source = f"[View Sheet]({op['sheet_url']})"
        embed = discord.Embed(
            title='🎖️ Current Operation',
            description=f"**{op['name']}**\n{source}",
            color=discord.Color.blurple(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


    @app_commands.command(
        name='clear-requests',
        description='Cancel all pending slot requests for the current operation (Admin only)',
    )
    @app_commands.default_permissions(manage_guild=True)
    async def clear_requests(self, interaction: discord.Interaction):
        op = await database.get_active_operation(str(interaction.guild_id))
        if not op:
            await interaction.response.send_message(
                "❌ No active operation.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        count = await clear_pending_queue(self.bot, interaction.guild, op)
        await interaction.followup.send(
            f"✅ Cleared **{count}** pending request(s) for **{op['name']}**.",
            ephemeral=True,
        )


    @app_commands.command(
        name='set-timezone',
        description='Set the server timezone used for all event times (Admin only)',
    )
    @app_commands.describe(timezone='Your local timezone')
    @app_commands.choices(timezone=_TIMEZONE_CHOICES)
    @app_commands.default_permissions(manage_guild=True)
    async def set_timezone(self, interaction: discord.Interaction, timezone: str):
        await database.set_guild_timezone(str(interaction.guild_id), timezone)
        await interaction.response.send_message(
            f"✅ Server timezone set to **{timezone}**. "
            f"Event times you enter will now be interpreted as {timezone}.",
            ephemeral=True,
        )

    @app_commands.command(
        name='set-event-time',
        description='Set or update the event start time and reminder for the current operation (Admin only)',
    )
    @app_commands.describe(
        event_time='Event start time in UTC, e.g. 25/06/2025 19:00',
        reminder_minutes='Send reminders this many minutes before the event (15, 30, or 60)',
    )
    @app_commands.choices(reminder_minutes=[
        app_commands.Choice(name='15 minutes before', value=15),
        app_commands.Choice(name='30 minutes before', value=30),
        app_commands.Choice(name='60 minutes before', value=60),
    ])
    @app_commands.default_permissions(manage_guild=True)
    async def set_event_time(
        self,
        interaction: discord.Interaction,
        event_time: str,
        reminder_minutes: int = 30,
    ):
        op = await database.get_active_operation(str(interaction.guild_id))
        if not op:
            await interaction.response.send_message("❌ No active operation.", ephemeral=True)
            return

        try:
            tz_name = await database.get_guild_timezone(str(interaction.guild_id))
            parsed = _parse_event_time(event_time, tz_name)
            await set_operation_time(self.bot, interaction.guild, op, parsed,
                                     reminder_minutes)
        except (ValueError, ActionError) as e:
            await interaction.response.send_message(f"❌ {e}", ephemeral=True)
            return

        ts = int(parsed.replace(tzinfo=timezone.utc).timestamp())
        await interaction.response.send_message(
            f"✅ Event time set to <t:{ts}:F> "
            f"with a **{reminder_minutes}-minute** reminder.",
            ephemeral=True,
        )

    @app_commands.command(
        name='assign-slot',
        description='Directly assign a member to a slot without approval (Admin or Unit Leader)',
    )
    @app_commands.describe(member='The Discord member to assign')
    async def assign_slot(self, interaction: discord.Interaction, member: discord.Member):
        await interaction.response.defer(ephemeral=True)

        try:
            check_can_assign(interaction.user, member)
        except ActionError as e:
            await interaction.followup.send(f"🚫 {e}", ephemeral=True)
            return

        op = await database.get_active_operation(str(interaction.guild_id))
        if not op:
            await interaction.followup.send("❌ No active operation.", ephemeral=True)
            return

        # Checked here as well as inside `assign_slot_request()`, so somebody who
        # already holds a slot is not found out only after picking one for them.
        existing = await database.get_member_active_request(
            str(interaction.guild_id), op['id'], str(member.id)
        )
        if existing:
            await interaction.followup.send(
                f"⚠️ **{member.display_name}** already has a **{existing['status']}** slot: "
                f"**{existing['slot_label']}**.\nUse `/clear-slot` first if you want to reassign them.",
                ephemeral=True,
            )
            return

        try:
            data = await roster.load_available(op)
        except asyncio.TimeoutError:
            await interaction.followup.send(
                "❌ Timed out loading the sheet (30s). Make sure it's shared with the service account.",
                ephemeral=True,
            )
            return
        except Exception as e:
            await interaction.followup.send(f"❌ Failed to load slots: `{e}`", ephemeral=True)
            return

        pending_rows = set(await database.get_pending_slots(op['id']))
        approved_rows = set(await database.get_approved_slots(op['id']))
        # `load_available()` has already subtracted the approved slots, by key.
        available = data['slots']

        if not available:
            await interaction.followup.send("ℹ️ All slots are currently filled.", ephemeral=True)
            return

        squads: dict = {}
        for s in available:
            squads.setdefault(s['squad'], []).append(s)

        bot_ref = self.bot

        async def _on_slot_selected(sel_interaction: discord.Interaction, slot: dict):
            # Deferred first: assigning writes the sheet, DMs the member and
            # redraws the board, all well past Discord's three seconds.
            await sel_interaction.response.defer(ephemeral=True)
            try:
                await assign_slot_request(
                    bot_ref, sel_interaction.guild, sel_interaction.user, member, slot,
                )
            except ActionError as e:
                await sel_interaction.followup.send(f"⚠️ {e}", ephemeral=True)
                return
            await sel_interaction.followup.send(
                f"✅ Assigned **{member.display_name}** to **{slot['label']}**.",
                ephemeral=True,
            )

        view = SquadSelectView(
            squads=squads,
            all_slots=available,
            operation_id=op['id'],
            pending_rows=pending_rows,
            approved_rows=approved_rows,
            bot=self.bot,
            on_select=_on_slot_selected,
        )
        await interaction.followup.send(
            f"Select a slot to assign to **{member.display_name}**:",
            view=view,
            ephemeral=True,
        )

    @app_commands.command(
        name='post-event',
        description='Post an event announcement with mission name and start time (Admin only)',
    )
    @app_commands.describe(
        channel='Channel to post in (defaults to current channel)',
        mission_name='Mission name — defaults to the active operation name',
        event_time='Event start time, e.g. 25/06/2025 19:00 — defaults to the active operation time',
    )
    @app_commands.default_permissions(manage_guild=True)
    async def post_event(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel = None,
        mission_name: str = None,
        event_time: str = None,
    ):
        await interaction.response.defer(ephemeral=True)

        target = channel or interaction.channel

        # Resolve mission name and event time from the active operation if not provided
        op = await database.get_active_operation(str(interaction.guild_id))

        if mission_name is None:
            if op:
                mission_name = op['name']
            else:
                await interaction.followup.send(
                    "❌ No active operation and no `mission_name` provided. "
                    "Pass a mission name or run `/setup-slots` first.",
                    ephemeral=True,
                )
                return

        parsed_time = None
        if event_time:
            try:
                tz_name = await database.get_guild_timezone(str(interaction.guild_id))
                parsed_time = _parse_event_time(event_time, tz_name)
            except ValueError as e:
                await interaction.followup.send(f"❌ {e}", ephemeral=True)
                return
        elif op:
            parsed_time = op['event_time']

        embed = await build_announcement_embed(
            interaction.guild, mission_name, parsed_time,
            interaction.user.display_name,
        )

        try:
            await target.send(embed=embed)
        except discord.Forbidden:
            await interaction.followup.send(
                f"❌ I don't have permission to post in {target.mention}.", ephemeral=True
            )
            return

        await interaction.followup.send(
            f"✅ Event posted in {target.mention}.", ephemeral=True
        )

    @app_commands.command(
        name='archive-old-approvals',
        description='Move already-approved messages from #slot-approvals to #approval-archive (Admin only)',
    )
    @app_commands.default_permissions(manage_guild=True)
    async def archive_old_approvals(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        approvals_channel = await guild_channel(
            interaction.guild, 'approvals', create=False
        )
        if not approvals_channel:
            await interaction.followup.send(
                "❌ No `#slot-approvals` channel found.", ephemeral=True
            )
            return

        # Creates the archive when it is missing; None means it could not be.
        archive_channel = await guild_channel(interaction.guild, 'archive')
        if archive_channel is None:
            await interaction.followup.send(
                "❌ Cannot create `#approval-archive` — grant me **Manage Channels**.",
                ephemeral=True,
            )
            return

        moved = 0
        skipped = 0
        bot_id = self.bot.user.id

        async for message in approvals_channel.history(limit=500, oldest_first=True):
            if message.author.id != bot_id:
                continue
            if not message.embeds:
                continue
            embed = message.embeds[0]
            if embed.color is None:
                continue

            color_val = embed.color.value
            field_names = [f.name or '' for f in embed.fields]

            is_approved = (
                color_val == discord.Color.green().value
                and any('approved' in n.lower() for n in field_names)
            )
            is_denied = (
                color_val in (discord.Color.red().value, discord.Color.dark_gray().value)
                and any('denied' in n.lower() for n in field_names)
            )

            if not (is_approved or is_denied):
                continue
            try:
                await archive_channel.send(embed=embed)
                await message.delete()
                moved += 1
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                skipped += 1

        await interaction.followup.send(
            f"✅ Moved **{moved}** message(s) to {archive_channel.mention}."
            + (f"\n⚠️ **{skipped}** could not be moved (permissions or already deleted)." if skipped else ""),
            ephemeral=True,
        )

    @app_commands.command(
        name='restart',
        description='Restart the bot container on Railway (Admin only)',
    )
    @app_commands.default_permissions(manage_guild=True)
    async def restart(self, interaction: discord.Interaction):
        if not (
            interaction.user.guild_permissions.manage_guild
            or interaction.user.guild_permissions.administrator
        ):
            await interaction.response.send_message(
                "🚫 You need **Manage Server** permissions to restart the bot.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        print(f"Restart requested by {interaction.user} ({interaction.user.id}) in guild {interaction.guild_id}")

        note = ''
        if os.getenv('RAILWAY_API_TOKEN'):
            try:
                deployment_id = await _railway_restart()
                await interaction.followup.send(
                    f"🔄 Restart triggered via the Railway API "
                    f"(deployment `{deployment_id[:8]}`). "
                    "The bot should be back online in ~30–60 seconds.",
                    ephemeral=True,
                )
                return
            except Exception as e:
                note = f"⚠️ Railway API restart failed (`{e}`) — falling back to a process restart.\n"

        await interaction.followup.send(
            f"{note}🔄 Restarting the bot process. "
            "Railway will bring it back automatically in ~30–60 seconds.",
            ephemeral=True,
        )
        # Give Discord a moment to deliver the response, then exit non-zero so
        # Railway's ON_FAILURE restart policy relaunches the container.
        await asyncio.sleep(1)
        os._exit(1)

    @app_commands.command(
        name='sync',
        description='Force-sync slash commands with Discord and refresh ORBAT (Admin only)',
    )
    @app_commands.default_permissions(manage_guild=True)
    async def sync(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        synced = await self.bot.tree.sync(guild=interaction.guild)

        # Repair any pending requests with stale sheet_col, then refresh the ORBAT.
        # The repair is a sheet-only concern: inserting a row in a spreadsheet
        # moves every cell below it, while a slot id never goes stale.
        op = await database.get_active_operation(str(interaction.guild_id))
        orbat_note = ""
        if op:
            repair_notes = []
            try:
                if roster.is_db_backed(op):
                    raise _NothingToRepair
                data = await roster.load_available(op)
                label_to_slot = {s['label']: s for s in data['slots']}
                active = await database.get_active_requests(op['id'])
                repaired = 0
                for req in active:
                    correct = label_to_slot.get(req['slot_label'])
                    if correct and (
                        req['sheet_row'] != correct['row']
                        or req['sheet_col'] != correct.get('col')
                    ):
                        await database.update_request_sheet_col(
                            req['id'], correct['row'], correct['col']
                        )
                        repaired += 1
                if repaired:
                    repair_notes.append(f"Repaired **{repaired}** pending request(s).")
            except _NothingToRepair:
                pass
            except Exception as e:
                repair_notes.append(f"⚠️ Repair step failed: `{e}`")

            # Refresh ORBAT directly (awaited so errors surface)
            try:
                await _update_orbat(self.bot, interaction.guild, op, raise_errors=True)
                orbat_note = "\n📋 ORBAT refreshed."
            except Exception as e:
                orbat_note = f"\n⚠️ ORBAT refresh failed: `{e}`"

            if repair_notes:
                orbat_note += " " + " ".join(repair_notes)

        await interaction.followup.send(
            f"✅ Synced **{len(synced)}** command(s) to this server.{orbat_note}",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(AdminCog(bot))

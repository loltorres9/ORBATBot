"""Bulk message deletion — `/purge`.

Deletes messages in the channel the command is run in, either the last *N* of
them or everything posted since a given point in time. Both can be combined,
in which case the count is a cap on the window.

Two Discord rules shape everything here:

* **Bulk deletion only works on messages younger than 14 days.** Anything older
  has to be deleted one at a time, which is rate limited to roughly one message
  a second — so old messages are capped at `MAX_SLOW_DELETES` per run rather
  than silently keeping the command busy for half an hour.
* **Nothing deleted can be recovered**, which is why the command previews the
  exact set first and only touches it after a confirmation click.
"""

import asyncio
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord
from discord import app_commands
from discord.ext import commands

from utils import database
from cogs.admin import _parse_event_time

# Discord refuses to bulk-delete anything older than 14 days. The margin keeps a
# message that crosses the line between the preview and the delete call out of
# the bulk batch, since one message too old makes Discord reject the whole batch.
BULK_MAX_AGE = timedelta(days=14)
BULK_MARGIN = timedelta(minutes=2)

# Upper bound on one run. Bulk batches make the recent ones cheap; the real
# reason for a limit is the confirmation preview, which has to fit a summary.
MAX_MESSAGES = 1000

# Messages older than 14 days go one REST call at a time. 200 already takes a
# couple of minutes; more would risk outliving the interaction token, so the
# rest is left for another run and reported.
MAX_SLOW_DELETES = 200

# Channel types with both a history and a bulk delete. Forum channels have
# neither — their messages live in the threads underneath.
PURGEABLE_CHANNELS = (
    discord.TextChannel, discord.Thread, discord.VoiceChannel, discord.StageChannel
)

_RELATIVE_PATTERN = re.compile(r'^(\d+)\s*([mhdw])$', re.IGNORECASE)
_RELATIVE_UNITS = {'m': 'minutes', 'h': 'hours', 'd': 'days', 'w': 'weeks'}

# Date-only input, i.e. midnight in the guild's timezone. Dates with a time are
# left to _parse_event_time(), so `/purge` and `/event-create` read them alike.
_DATE_FORMATS = ['%d/%m/%Y', '%Y-%m-%d', '%d-%m-%Y']


def _zone(tz_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        return ZoneInfo('UTC')


def _parse_since(raw: str, tz_name: str = 'UTC') -> datetime:
    """Resolve the `since` argument to an aware UTC datetime.

    Accepts an age (`30m`, `2h`, `7d`, `1w`), a date and time in the guild's
    timezone, or a bare date, which means midnight local.
    """
    raw = raw.strip()

    match = _RELATIVE_PATTERN.match(raw)
    if match:
        amount = int(match.group(1))
        if amount <= 0:
            raise ValueError("That age has to be more than zero, e.g. `2h`.")
        unit = _RELATIVE_UNITS[match.group(2).lower()]
        return datetime.now(timezone.utc) - timedelta(**{unit: amount})

    try:
        return _parse_event_time(raw, tz_name).replace(tzinfo=timezone.utc)
    except ValueError:
        pass

    tz = _zone(tz_name)
    for fmt in _DATE_FORMATS:
        try:
            local = datetime.strptime(raw, fmt).replace(tzinfo=tz)
        except ValueError:
            continue
        return local.astimezone(timezone.utc)

    raise ValueError(
        f"Could not read `{raw}` as a point in time.\n"
        "Use an age like `30m` / `2h` / `7d`, a date like `25/06/2025`, "
        "or a date and time like `25/06/2025 19:00`."
    )


async def _collect(channel, limit: int, since: datetime | None) -> list[discord.Message]:
    """The newest `limit` messages, stopping at `since` when one is given.

    Walking newest-first and breaking at the boundary means a `since` an hour
    back costs one page, however much history sits underneath it.
    """
    messages = []
    async for message in channel.history(limit=None, oldest_first=False):
        if since is not None and message.created_at <= since:
            break
        messages.append(message)
        if len(messages) >= limit:
            break
    return messages


async def _bulk_delete(channel, messages: list[discord.Message], reason: str) -> tuple[int, int, bool]:
    """Delete messages younger than 14 days in batches.

    Returns (deleted, failed, blocked); `blocked` means Manage Messages was lost
    part-way and the run gave up rather than retrying every remaining message.
    """
    deleted = failed = 0
    for start in range(0, len(messages), 100):
        chunk = messages[start:start + 100]
        try:
            await channel.delete_messages(chunk, reason=reason)
            deleted += len(chunk)
        except discord.NotFound:
            # Already gone — the outcome the command was after either way.
            deleted += len(chunk)
        except discord.Forbidden:
            # Checked before the preview, so this is permission taken away
            # mid-run. Every further call would fail the same way.
            return deleted, failed, True
        except discord.HTTPException:
            # Discord rejects the whole batch when a single message in it is
            # too old or already deleted, so salvage the rest one at a time.
            # Forbidden is a subclass of HTTPException, which is why it is
            # caught above — otherwise it would land in this fallback.
            ok, bad, blocked = await _slow_delete(chunk)
            deleted += ok
            failed += bad
            if blocked:
                return deleted, failed, True
        if start + 100 < len(messages):
            await asyncio.sleep(1)
    return deleted, failed, False


async def _slow_delete(messages: list[discord.Message], progress=None) -> tuple[int, int, bool]:
    """Delete messages one at a time. Returns (deleted, failed, blocked).

    `Message.delete()` takes no audit-log reason, so unlike the bulk path these
    show up in the audit log without one.
    """
    deleted = failed = 0
    for index, message in enumerate(messages, 1):
        try:
            await message.delete()
            deleted += 1
        except discord.NotFound:
            deleted += 1
        except discord.Forbidden:
            return deleted, failed, True
        except discord.HTTPException:
            failed += 1
        if progress is not None and index % 25 == 0:
            await progress(index)
    return deleted, failed, False


def _may_purge(channel, member: discord.Member) -> bool:
    """Channel permissions, so an overwrite that grants or denies it is honoured."""
    perms = channel.permissions_for(member)
    return perms.manage_messages or perms.manage_guild


def _describe(messages: list[discord.Message]) -> str:
    oldest = int(messages[-1].created_at.timestamp())
    newest = int(messages[0].created_at.timestamp())
    if oldest == newest:
        return f"Posted <t:{newest}:F>"
    return f"From <t:{oldest}:F> to <t:{newest}:F>"


class ConfirmPurgeView(discord.ui.View):
    """The confirmation in front of an irreversible delete.

    It holds the exact messages the preview was built from, so what is deleted
    is what was shown — anything posted in between is left alone.
    """

    def __init__(self, channel, recent, old, skipped, reason: str):
        super().__init__(timeout=120)
        self.channel = channel
        self.recent = recent
        self.old = old
        self.skipped = skipped
        self.reason = reason
        self.message = None

    async def on_timeout(self):
        if self.message is None:
            return
        try:
            await self.message.edit(
                content="⌛ Confirmation timed out — nothing was deleted.",
                embed=None, view=None,
            )
        except discord.HTTPException:
            pass

    @discord.ui.button(label='Yes, delete them', style=discord.ButtonStyle.danger, emoji='🧹')
    async def confirm(self, btn: discord.Interaction, _button: discord.ui.Button):
        self.stop()
        total = len(self.recent) + len(self.old)
        await btn.response.edit_message(
            content=f"🧹 Deleting **{total}** message(s)…", embed=None, view=None
        )

        async def progress(done: int):
            try:
                await btn.edit_original_response(
                    content=(
                        f"🧹 Removing **{len(self.old)}** message(s) older than 14 days "
                        f"one at a time — **{done}** done."
                    )
                )
            except discord.HTTPException:
                pass

        deleted, failed, blocked = await _bulk_delete(self.channel, self.recent, self.reason)
        if self.old and not blocked:
            await progress(0)
            ok, bad, blocked = await _slow_delete(self.old, progress)
            deleted += ok
            failed += bad

        notes = []
        if blocked:
            notes.append("❌ I lost **Manage Messages** here — the rest was left alone.")
        if failed:
            notes.append(f"⚠️ **{failed}** message(s) could not be deleted.")
        if self.skipped:
            notes.append(
                f"ℹ️ **{len(self.skipped)}** message(s) older than 14 days were left — "
                "they can only go one at a time, so run `/purge` again to continue."
            )

        summary = (
            f"✅ Deleted **{deleted}** message(s) in {self.channel.mention}."
            if deleted == total else
            f"✅ Deleted **{deleted}** of **{total}** message(s) in {self.channel.mention}."
        )
        if notes:
            summary += "\n" + "\n".join(notes)
        try:
            await btn.edit_original_response(content=summary)
        except discord.HTTPException:
            pass
        print(f"[purge] {self.reason} → deleted {deleted}, failed {failed}")

    @discord.ui.button(label='Cancel', style=discord.ButtonStyle.secondary, emoji='✖️')
    async def cancel(self, btn: discord.Interaction, _button: discord.ui.Button):
        self.stop()
        await btn.response.edit_message(
            content="Nothing deleted.", embed=None, view=None
        )


class PurgeCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name='purge',
        description='Delete messages in this channel (Manage Messages)',
    )
    @app_commands.describe(
        amount=f'How many of the most recent messages to delete (1–{MAX_MESSAGES})',
        since='Delete everything posted since then: `2h`, `7d`, `25/06/2025`, `25/06/2025 19:00`',
    )
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.guild_only()
    async def purge(
        self,
        interaction: discord.Interaction,
        amount: app_commands.Range[int, 1, MAX_MESSAGES] = None,
        since: str = None,
    ):
        channel = interaction.channel
        if not isinstance(channel, PURGEABLE_CHANNELS):
            await interaction.response.send_message(
                "❌ `/purge` only works in a server text channel, thread or voice chat.",
                ephemeral=True,
            )
            return

        if not _may_purge(channel, interaction.user):
            await interaction.response.send_message(
                "🚫 You need **Manage Messages** in this channel to purge it.",
                ephemeral=True,
            )
            return

        my_perms = channel.permissions_for(interaction.guild.me)
        if not (my_perms.manage_messages and my_perms.read_message_history):
            await interaction.response.send_message(
                "❌ I need **Manage Messages** and **Read Message History** in this channel.",
                ephemeral=True,
            )
            return

        if amount is None and since is None:
            await interaction.response.send_message(
                "❌ Give me `amount`, `since`, or both.\n"
                "· `/purge amount: 50` — the last 50 messages\n"
                "· `/purge since: 2h` — everything from the last two hours\n"
                "· `/purge since: 25/06/2025` — everything since that date\n"
                "· `/purge amount: 100 since: 7d` — at most 100, and nothing older than a week",
                ephemeral=True,
            )
            return

        since_dt = None
        if since is not None:
            try:
                tz_name = await database.get_guild_timezone(str(interaction.guild_id))
                since_dt = _parse_since(since, tz_name)
            except ValueError as e:
                await interaction.response.send_message(f"❌ {e}", ephemeral=True)
                return
            if since_dt >= datetime.now(timezone.utc):
                await interaction.response.send_message(
                    "❌ That time is in the future — there is nothing to delete since then.",
                    ephemeral=True,
                )
                return

        await interaction.response.defer(ephemeral=True)

        try:
            messages = await _collect(channel, amount or MAX_MESSAGES, since_dt)
        except discord.Forbidden:
            await interaction.followup.send(
                "❌ I can't read this channel's history.", ephemeral=True
            )
            return
        except discord.HTTPException as e:
            await interaction.followup.send(
                f"❌ Could not read this channel's history: `{e}`", ephemeral=True
            )
            return

        print(
            f"[purge] {interaction.user} ({interaction.user.id}) in "
            f"#{channel} / {interaction.guild.name}: "
            f"amount={amount} since={since} matched {len(messages)}"
        )

        if not messages:
            await interaction.followup.send(
                "ℹ️ Nothing matched — there is nothing to delete.", ephemeral=True
            )
            return

        cutoff = datetime.now(timezone.utc) - BULK_MAX_AGE + BULK_MARGIN
        recent = [m for m in messages if m.created_at > cutoff]
        old = [m for m in messages if m.created_at <= cutoff]
        # Newest-first, so the cap keeps the newest of the old ones and leaves
        # the oldest for a follow-up run.
        skipped = old[MAX_SLOW_DELETES:]
        old = old[:MAX_SLOW_DELETES]
        to_delete = recent + old
        pinned = [m for m in to_delete if m.pinned]

        # The span of what is actually going, not of everything that matched —
        # the two differ as soon as the 14-day cap leaves something behind.
        lines = [_describe(to_delete)]
        if old:
            minutes = max(1, round(len(old) / 60))
            lines.append(
                f"⏳ **{len(old)}** of them are older than 14 days and can only be "
                f"deleted one at a time — roughly {minutes} minute(s)."
            )
        if skipped:
            lines.append(
                f"✂️ **{len(skipped)}** even older message(s) are left for a second run."
            )
        if pinned:
            lines.append(f"📌 Includes **{len(pinned)}** pinned message(s).")

        total = len(to_delete)
        embed = discord.Embed(
            title='🧹 Purge — Confirmation',
            description=(
                f"Delete **{total}** message(s) in {channel.mention}?\n\n"
                + "\n".join(lines)
                + "\n\n**This cannot be undone.** Anything posted after this "
                  "preview is left alone."
            ),
            color=discord.Color.red(),
        )

        reason = f"/purge by {interaction.user} ({interaction.user.id})"
        view = ConfirmPurgeView(channel, recent, old, skipped, reason)
        view.message = await interaction.followup.send(
            embed=embed, view=view, ephemeral=True, wait=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(PurgeCog(bot))

import asyncio
import os
from datetime import timezone

import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv

from utils import database
from cogs.slots import ApprovalView, OrbatRequestButton, guild_channel
from cogs.gameroles import GameRolePanelView
from cogs.events import EventRsvpView, load_responses

load_dotenv()


class ORBATBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        # Join and leave events are privileged. Requesting the intent while
        # "Server Members Intent" is unticked in the Developer Portal makes login
        # fail outright, which would take the whole bot down — so it is opt-in:
        # tick it there first, then set MEMBER_EVENTS=1. Bans and unbans are not
        # privileged and are logged either way.
        self.member_events = (os.getenv('MEMBER_EVENTS') or '').strip().lower() in (
            '1', 'true', 'yes', 'on'
        )
        if self.member_events:
            intents.members = True
        super().__init__(
            command_prefix='!',
            intents=intents,
            description='Arma 3 ORBAT Slot Management Bot',
        )
        # The optional web UI. Stays None unless it is configured — see _start_web().
        self.web = None

    async def setup_hook(self):
        import traceback
        print("--- setup_hook start ---")

        await database.init_db()
        print("✅ Database initialised.")

        # Voice intervals left open by a crash are closed at their last
        # heartbeat before anything new is recorded, so a restart can't leave a
        # session that looks like it has been running for days.
        try:
            dangling = await database.close_dangling_voice_sessions()
            if dangling:
                print(f"✅ Closed {dangling} voice session(s) left open by a restart.")
        except Exception:
            print("❌ Failed to close dangling voice sessions:")
            traceback.print_exc()

        try:
            await self.load_extension('cogs.slots')
            print("✅ Loaded cogs.slots")
        except Exception:
            print("❌ Failed to load cogs.slots:")
            traceback.print_exc()

        try:
            await self.load_extension('cogs.admin')
            print("✅ Loaded cogs.admin")
        except Exception:
            print("❌ Failed to load cogs.admin:")
            traceback.print_exc()

        try:
            await self.load_extension('cogs.gameroles')
            print("✅ Loaded cogs.gameroles")
        except Exception:
            print("❌ Failed to load cogs.gameroles:")
            traceback.print_exc()

        try:
            await self.load_extension('cogs.events')
            print("✅ Loaded cogs.events")
        except Exception:
            print("❌ Failed to load cogs.events:")
            traceback.print_exc()

        try:
            await self.load_extension('cogs.voicelog')
            print("✅ Loaded cogs.voicelog")
        except Exception:
            print("❌ Failed to load cogs.voicelog:")
            traceback.print_exc()

        try:
            await self.load_extension('cogs.memberlog')
            print("✅ Loaded cogs.memberlog"
                  + ("" if self.member_events else
                     " (joins/leaves off — set MEMBER_EVENTS=1 once the "
                     "Server Members Intent is enabled in the Developer Portal)"))
        except Exception:
            print("❌ Failed to load cogs.memberlog:")
            traceback.print_exc()

        try:
            await self.load_extension('cogs.purge')
            print("✅ Loaded cogs.purge")
        except Exception:
            print("❌ Failed to load cogs.purge:")
            traceback.print_exc()

        try:
            await self.load_extension('cogs.redditfeed')
            print("✅ Loaded cogs.redditfeed")
        except Exception:
            print("❌ Failed to load cogs.redditfeed:")
            traceback.print_exc()

        registered = [c.name for c in self.tree.get_commands()]
        print(f"Commands registered in tree: {registered}")

        # Re-register approval views for all pending requests so buttons
        # continue to work after a bot restart.
        # Persistent ORBAT request button — one instance covers all guilds
        self.add_view(OrbatRequestButton(bot=self))
        # Persistent game-role panel button — likewise global
        self.add_view(GameRolePanelView(bot=self))

        pending = await database.get_all_pending_requests()
        for req in pending:
            self.add_view(ApprovalView(request_id=req['id'], bot=self))

        print(f"{len(pending)} pending view(s) restored.")

        # Re-register RSVP buttons for every event that is still open. The custom
        # ids encode each event's own response keys, so they have to be loaded.
        live_events = await database.get_live_events()
        for event in live_events:
            self.add_view(EventRsvpView(
                event_id=event['id'], bot=self,
                responses=await load_responses(event['id']),
            ))

        print(f"{len(live_events)} event view(s) restored.")

        self.reminder_task.start()
        print("✅ Reminder task started.")

        await self._start_web()
        print("--- setup_hook end ---")

    async def _start_web(self):
        """Start the browser UI, if it is configured.

        It shares this process and event loop, so a page can post a message or
        register a view through `self` directly. Every failure path here is
        non-fatal: the bot's own job must not depend on the website.
        """
        import traceback

        try:
            from web import WebServer, load_config
        except Exception:
            print("ℹ️ Web UI dependencies missing (pip install -r requirements.txt) — skipped.")
            return

        try:
            config = load_config()
        except Exception:
            print("❌ Web UI configuration is invalid — skipped:")
            traceback.print_exc()
            return
        if config.disabled:
            print("ℹ️ Web UI switched off with WEB_ENABLED=0.")
            return
        if not config.ready:
            print(
                "ℹ️ Web UI not configured — set "
                f"{', '.join(config.missing)} to enable it. Skipped."
            )
            return

        server = WebServer(self, config)
        try:
            await server.start()
        except Exception:
            print("❌ Web UI failed to start:")
            traceback.print_exc()
            return

        self.web = server
        print(f"✅ Web UI listening on {config.host}:{config.port}"
              + (f" ({config.base_url})" if config.base_url else ""))

    async def close(self):
        # Record the time of everyone still in voice before the connection goes
        # down — a redeploy is the common case and must not lose it.
        cog = self.get_cog('VoiceLogCog')
        if cog is not None:
            try:
                await cog.flush_open_sessions()
            except Exception:
                pass
        if self.web is not None:
            await self.web.stop()
            self.web = None
        await super().close()

    @tasks.loop(minutes=1)
    async def reminder_task(self):
        # discord.ext.tasks stops a loop for good on an unhandled exception, so
        # one transient failure — a database blip during a redeploy, say — would
        # silently kill every operation reminder until the next restart.
        # Swallow per-operation so the rest of this tick still runs.
        try:
            ops = await database.get_operations_needing_reminder()
        except Exception as e:
            print(f"reminder_task: could not load due reminders: {e!r}")
            return

        for op in ops:
            try:
                await self._send_operation_reminder(op)
            except Exception as e:
                print(f"reminder_task: reminder for operation {op['id']} failed: {e!r}")

    async def _send_operation_reminder(self, op):
        await database.mark_reminder_fired(op['id'])
        members = await database.get_approved_member_ids(op['id'])
        if not members:
            return

        event_dt = op['event_time']
        if event_dt.tzinfo is None:
            event_dt = event_dt.replace(tzinfo=timezone.utc)
        event_ts = int(event_dt.timestamp())

        guild = discord.utils.get(self.guilds, id=int(op['guild_id']))
        if not guild:
            return

        # DM each approved member
        for member_id, slot_label in members:
            try:
                member = await guild.fetch_member(int(member_id))
                await member.send(
                    f"⏰ **Operation Reminder — {op['name']}**\n"
                    f"Your operation starts <t:{event_ts}:R> (<t:{event_ts}:F>).\n"
                    f"Your slot: **{slot_label}**\n"
                    f"Get ready!"
                )
            except (discord.Forbidden, discord.NotFound):
                pass

        # Ping all approved members where the board lives — #orbat unless an
        # admin pointed it somewhere else. Not created just to send a reminder.
        orbat_channel = await guild_channel(guild, 'orbat', create=False)
        if orbat_channel:
            mentions = ' '.join(
                f'<@{member_id}>' for member_id, _ in members
            )
            try:
                await orbat_channel.send(
                    f"⏰ **Operation reminder — {op['name']}** starts <t:{event_ts}:R>!\n"
                    f"{mentions}"
                )
            except (discord.Forbidden, discord.HTTPException):
                pass

    @reminder_task.before_loop
    async def before_reminder_task(self):
        await self.wait_until_ready()

    async def on_ready(self):
        print(f"on_ready fired. Guilds: {[g.name for g in self.guilds]}")
        # Copy global commands into each guild and sync — this is instant,
        # unlike global sync which can take up to an hour to propagate.
        for guild in self.guilds:
            try:
                self.tree.copy_global_to(guild=guild)
                synced = await self.tree.sync(guild=guild)
                print(f"✅ Guild sync '{guild.name}': {len(synced)} command(s).")
            except Exception as e:
                print(f"❌ Guild sync failed for '{guild.name}': {e}")


    async def on_guild_join(self, guild: discord.Guild):
        """Sync commands when the bot is added to a new server while already running."""
        try:
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            print(f"✅ Joined '{guild.name}' — synced {len(synced)} command(s).")
        except Exception as e:
            print(f"❌ Guild sync failed for '{guild.name}': {e}")


def main():
    token = os.getenv('DISCORD_TOKEN')
    if not token:
        raise RuntimeError("DISCORD_TOKEN is not set. Check your .env file or Railway variables.")

    bot = ORBATBot()
    asyncio.run(bot.start(token))


if __name__ == '__main__':
    main()

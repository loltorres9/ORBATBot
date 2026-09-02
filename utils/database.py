import asyncpg
import os

DATABASE_URL = os.getenv('DATABASE_URL')

_pool = None


async def get_pool():
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(DATABASE_URL)
    return _pool


async def init_db():
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS operations (
                id SERIAL PRIMARY KEY,
                guild_id TEXT NOT NULL,
                name TEXT NOT NULL,
                sheet_url TEXT NOT NULL,
                sheet_id TEXT NOT NULL,
                squad_col INTEGER,
                role_col INTEGER,
                status_col INTEGER,
                assigned_col INTEGER,
                is_active INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS requests (
                id SERIAL PRIMARY KEY,
                guild_id TEXT NOT NULL,
                operation_id INTEGER NOT NULL,
                member_id TEXT NOT NULL,
                member_name TEXT NOT NULL,
                slot_label TEXT NOT NULL,
                sheet_row INTEGER NOT NULL,
                sheet_col INTEGER,
                status TEXT DEFAULT 'pending',
                approval_message_id TEXT,
                approval_channel_id TEXT,
                approved_by TEXT,
                denial_reason TEXT,
                unit_role TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS orbat_messages (
                guild_id TEXT PRIMARY KEY,
                channel_id TEXT NOT NULL,
                message_id TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        # Reserved for a planned secondary "open slots" message. Nothing reads or
        # writes it today — the accessors were removed as dead code. Kept so an
        # existing deployment's table isn't orphaned; drop it here if the idea
        # is abandoned for good.
        await db.execute('''
            CREATE TABLE IF NOT EXISTS open_slots_messages (
                guild_id TEXT PRIMARY KEY,
                channel_id TEXT NOT NULL,
                message_id TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS guild_settings (
                guild_id TEXT PRIMARY KEY,
                timezone TEXT NOT NULL DEFAULT 'UTC'
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS game_roles (
                id SERIAL PRIMARY KEY,
                guild_id TEXT NOT NULL,
                role_id TEXT NOT NULL,
                name TEXT NOT NULL,
                emoji TEXT,
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (guild_id, role_id)
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS game_role_panels (
                guild_id TEXT PRIMARY KEY,
                channel_id TEXT NOT NULL,
                message_id TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS events (
                id SERIAL PRIMARY KEY,
                guild_id TEXT NOT NULL,
                channel_id TEXT,
                message_id TEXT,
                title TEXT NOT NULL,
                description TEXT,
                event_time TIMESTAMP NOT NULL,
                duration_minutes INTEGER,
                location TEXT,
                image_url TEXT,
                mention_role_id TEXT,
                created_by TEXT NOT NULL,
                created_by_name TEXT,
                reminder_minutes INTEGER DEFAULT 30,
                reminder_fired INTEGER DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'scheduled',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS event_signups (
                id SERIAL PRIMARY KEY,
                event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
                member_id TEXT NOT NULL,
                member_name TEXT NOT NULL,
                response TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (event_id, member_id)
            )
        ''')
        await db.execute('''
            CREATE INDEX IF NOT EXISTS idx_events_guild_status
                ON events (guild_id, status, event_time)
        ''')
        # Per-event custom sign-up responses. No rows means the event uses the
        # built-in Accepted / Tentative / Declined set.
        await db.execute('''
            CREATE TABLE IF NOT EXISTS event_responses (
                id SERIAL PRIMARY KEY,
                event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
                key TEXT NOT NULL,
                label TEXT NOT NULL,
                emoji TEXT,
                is_decline INTEGER NOT NULL DEFAULT 0,
                sort_order INTEGER NOT NULL DEFAULT 0,
                UNIQUE (event_id, key)
            )
        ''')
        # Reusable rich messages built in the web UI. `message_id` is NULL until
        # the embed is posted; keeping it lets an already-sent message be edited
        # in place rather than reposted.
        await db.execute('''
            CREATE TABLE IF NOT EXISTS embeds (
                id SERIAL PRIMARY KEY,
                guild_id TEXT NOT NULL,
                name TEXT NOT NULL,
                channel_id TEXT,
                message_id TEXT,
                content TEXT,
                title TEXT,
                description TEXT,
                url TEXT,
                color TEXT,
                author_name TEXT,
                author_icon_url TEXT,
                thumbnail_url TEXT,
                image_url TEXT,
                footer_text TEXT,
                footer_icon_url TEXT,
                show_timestamp INTEGER NOT NULL DEFAULT 0,
                created_by TEXT,
                created_by_name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS embed_fields (
                id SERIAL PRIMARY KEY,
                embed_id INTEGER NOT NULL REFERENCES embeds(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                value TEXT NOT NULL,
                inline INTEGER NOT NULL DEFAULT 0,
                sort_order INTEGER NOT NULL DEFAULT 0
            )
        ''')
        # Where member joins/leaves/kicks/bans are announced, and which of them.
        await db.execute('''
            CREATE TABLE IF NOT EXISTS log_settings (
                guild_id TEXT PRIMARY KEY,
                channel_id TEXT,
                log_join INTEGER NOT NULL DEFAULT 1,
                log_leave INTEGER NOT NULL DEFAULT 1,
                log_kick INTEGER NOT NULL DEFAULT 1,
                log_ban INTEGER NOT NULL DEFAULT 1,
                log_unban INTEGER NOT NULL DEFAULT 1,
                track_invites INTEGER NOT NULL DEFAULT 1,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        # Where each invite link was published, so a join can say "came in through
        # the Steam group" instead of only naming a code.
        await db.execute('''
            CREATE TABLE IF NOT EXISTS invite_labels (
                guild_id TEXT NOT NULL,
                code TEXT NOT NULL,
                label TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (guild_id, code)
            )
        ''')
        # Time spent in voice channels. One row per counted interval: a member's
        # visit is split whenever the rules stop applying (they end up alone, or
        # move to an excluded channel), so the sum is time that actually counted.
        await db.execute('''
            CREATE TABLE IF NOT EXISTS voice_sessions (
                id SERIAL PRIMARY KEY,
                guild_id TEXT NOT NULL,
                member_id TEXT NOT NULL,
                member_name TEXT,
                channel_id TEXT NOT NULL,
                channel_name TEXT,
                started_at TIMESTAMP NOT NULL,
                heartbeat_at TIMESTAMP,
                ended_at TIMESTAMP,
                seconds INTEGER
            )
        ''')
        await db.execute('''
            CREATE INDEX IF NOT EXISTS idx_voice_guild_started
                ON voice_sessions (guild_id, started_at)
        ''')
        await db.execute('''
            CREATE INDEX IF NOT EXISTS idx_voice_open
                ON voice_sessions (guild_id) WHERE ended_at IS NULL
        ''')
        # ORBATs — the slot roster held here rather than read out of a Google
        # Sheet. A squad and a slot carry no booking of their own: who holds a
        # slot lives in `requests`, keyed by (operation_id, slot_id), which is
        # what makes an ORBAT a reusable template rather than one night's board.
        await db.execute('''
            CREATE TABLE IF NOT EXISTS orbats (
                id SERIAL PRIMARY KEY,
                guild_id TEXT NOT NULL,
                name TEXT NOT NULL,
                description TEXT,
                -- The text as its author typed it. The squads and slots below
                -- are the source of truth; this is kept alongside so comments,
                -- blank lines and their own spacing survive a reload, which
                -- regenerating the text from the structure would flatten.
                source_text TEXT,
                -- The net list as its author typed it, for the same reason
                -- source_text exists: regenerating it would flatten comments,
                -- blank lines and their own alignment.
                nets_text TEXT,
                created_by TEXT,
                created_by_name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS orbat_squads (
                id SERIAL PRIMARY KEY,
                orbat_id INTEGER NOT NULL REFERENCES orbats(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                column_side INTEGER NOT NULL DEFAULT 0,
                exclude_from_count INTEGER NOT NULL DEFAULT 0,
                reserved_unit TEXT,
                radio TEXT,
                sort_order INTEGER NOT NULL DEFAULT 0
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS orbat_slots (
                id SERIAL PRIMARY KEY,
                squad_id INTEGER NOT NULL REFERENCES orbat_squads(id) ON DELETE CASCADE,
                role_name TEXT NOT NULL,
                sort_order INTEGER NOT NULL DEFAULT 0
            )
        ''')
        # The long-range nets the whole operation shares, as against the
        # short-range channel on orbat_squads.radio. A flat list with no
        # identity of its own, so a save replaces it wholesale.
        await db.execute('''
            CREATE TABLE IF NOT EXISTS orbat_nets (
                id SERIAL PRIMARY KEY,
                orbat_id INTEGER NOT NULL REFERENCES orbats(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                channel TEXT,
                inactive INTEGER NOT NULL DEFAULT 0,
                sort_order INTEGER NOT NULL DEFAULT 0
            )
        ''')
        await db.execute('''
            CREATE INDEX IF NOT EXISTS idx_orbat_nets
                ON orbat_nets (orbat_id, sort_order)
        ''')
        await db.execute('''
            CREATE INDEX IF NOT EXISTS idx_orbat_squads
                ON orbat_squads (orbat_id, sort_order)
        ''')
        await db.execute('''
            CREATE INDEX IF NOT EXISTS idx_orbat_slots
                ON orbat_slots (squad_id, sort_order)
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS voice_settings (
                guild_id TEXT PRIMARY KEY,
                enabled INTEGER NOT NULL DEFAULT 0,
                channel_id TEXT,
                min_log_minutes INTEGER NOT NULL DEFAULT 5,
                count_afk INTEGER NOT NULL DEFAULT 0,
                count_solo INTEGER NOT NULL DEFAULT 0,
                excluded_channels TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        # The self-updating top-10 board, added after voice tracking shipped.
        for column, definition in (
            ('board_enabled', 'INTEGER NOT NULL DEFAULT 0'),
            ('board_channel_id', 'TEXT'),
            ('board_message_id', 'TEXT'),
            ('board_period', "TEXT NOT NULL DEFAULT '7'"),
            ('board_hour', 'INTEGER NOT NULL DEFAULT 9'),
            ('board_updated_on', 'DATE'),
        ):
            await db.execute(
                f'ALTER TABLE voice_settings ADD COLUMN IF NOT EXISTS {column} {definition}'
            )
        # Recurrence, added after the events tables shipped
        await db.execute('''
            ALTER TABLE events ADD COLUMN IF NOT EXISTS recurrence TEXT
        ''')
        await db.execute('''
            ALTER TABLE events ADD COLUMN IF NOT EXISTS recurrence_until TIMESTAMP
        ''')
        # The first occurrence's start time, carried unchanged down the series so
        # monthly repeats keep anchoring on the original day instead of drifting
        # once a short month clamps the date.
        await db.execute('''
            ALTER TABLE events ADD COLUMN IF NOT EXISTS recurrence_anchor TIMESTAMP
        ''')
        # Add event scheduling columns to existing operations tables
        await db.execute('''
            ALTER TABLE operations ADD COLUMN IF NOT EXISTS
                event_time TIMESTAMP
        ''')
        await db.execute('''
            ALTER TABLE operations ADD COLUMN IF NOT EXISTS
                reminder_minutes INTEGER DEFAULT 30
        ''')
        await db.execute('''
            ALTER TABLE operations ADD COLUMN IF NOT EXISTS
                reminder_fired INTEGER DEFAULT 0
        ''')
        # The unit an ORBAT squad belongs to. It started out on the slot, which
        # meant repeating the same tag on every line of a squad that belongs to
        # one unit as a whole; these three statements lift any values already
        # entered up to their squad and then retire the slot column. All three
        # are no-ops once they have run.
        await db.execute('''
            ALTER TABLE orbat_squads ADD COLUMN IF NOT EXISTS reserved_unit TEXT
        ''')
        if await db.fetchval(
            """SELECT 1 FROM information_schema.columns
                WHERE table_name = 'orbat_slots' AND column_name = 'reserved_unit'"""
        ):
            # The squad takes the first unit any of its slots named, in slot
            # order — with one unit per squad there is nothing to choose between.
            await db.execute('''
                UPDATE orbat_squads q SET reserved_unit = (
                    SELECT s.reserved_unit FROM orbat_slots s
                     WHERE s.squad_id = q.id AND s.reserved_unit IS NOT NULL
                     ORDER BY s.sort_order, s.id LIMIT 1)
                 WHERE q.reserved_unit IS NULL
            ''')
            await db.execute('ALTER TABLE orbat_slots DROP COLUMN reserved_unit')

        # The squad's own radio channel and the shared net list, both added
        # after the ORBAT tables shipped.
        await db.execute('''
            ALTER TABLE orbat_squads ADD COLUMN IF NOT EXISTS radio TEXT
        ''')
        await db.execute('''
            ALTER TABLE orbats ADD COLUMN IF NOT EXISTS nets_text TEXT
        ''')
        # An operation is backed either by a Google Sheet or by an ORBAT held
        # here. Both stay possible: the sheet columns lose their NOT NULL so a
        # DB-backed operation can exist without one, and orbat_id is NULL on
        # every sheet-backed operation.
        await db.execute('''
            ALTER TABLE operations ADD COLUMN IF NOT EXISTS orbat_id INTEGER
        ''')
        await db.execute('ALTER TABLE operations ALTER COLUMN sheet_url DROP NOT NULL')
        await db.execute('ALTER TABLE operations ALTER COLUMN sheet_id DROP NOT NULL')
        # A request against an ORBAT slot has no sheet row.
        await db.execute('ALTER TABLE requests ALTER COLUMN sheet_row DROP NOT NULL')

        # Which ORBAT slot a request is for. NULL on every sheet-backed request,
        # which is all of them until slots can be booked from the board — the
        # column exists now so an ORBAT knows who its slots are promised to.
        await db.execute('''
            ALTER TABLE requests ADD COLUMN IF NOT EXISTS slot_id INTEGER
        ''')


async def get_active_operation(guild_id: str):
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetchrow(
            'SELECT * FROM operations WHERE guild_id = $1 AND is_active = 1 ORDER BY created_at DESC LIMIT 1',
            guild_id,
        )


async def get_operation(operation_id: int):
    """One operation by id, active or not.

    A request keeps pointing at the operation it was made for. Deciding it has
    to read *that* one rather than whatever is running now, or a request left
    over from last week is written into this week's roster.
    """
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetchrow(
            'SELECT * FROM operations WHERE id = $1', operation_id
        )


async def create_operation(guild_id: str, name: str, sheet_url: str = None,
                           sheet_id: str = None, squad_col: int = None,
                           role_col: int = None, status_col: int = None,
                           assigned_col: int = None, orbat_id: int = None) -> int:
    """Start an operation, deactivating whatever was running before.

    Either sheet_url/sheet_id or orbat_id identifies the roster; `utils/roster.py`
    is what decides which, and nothing above it needs to care.
    """
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            'UPDATE operations SET is_active = 0 WHERE guild_id = $1',
            guild_id,
        )
        row = await db.fetchrow(
            '''INSERT INTO operations
               (guild_id, name, sheet_url, sheet_id, squad_col, role_col,
                status_col, assigned_col, orbat_id)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
               RETURNING id''',
            guild_id, name, sheet_url, sheet_id, squad_col, role_col,
            status_col, assigned_col, orbat_id,
        )
        return row['id']


async def get_pending_slots(operation_id: int) -> list:
    """The slot identifiers of every pending request, in `utils/roster.py`'s
    key form so a sheet-backed and an ORBAT-backed operation read alike."""
    from utils.roster import request_key
    pool = await get_pool()
    async with pool.acquire() as db:
        rows = await db.fetch(
            """SELECT slot_id, sheet_row, sheet_col FROM requests
                WHERE operation_id = $1 AND status = 'pending'""",
            operation_id,
        )
        return [request_key(row) for row in rows]


async def get_approved_slots(operation_id: int) -> list:
    """Same, for approved requests."""
    from utils.roster import request_key
    pool = await get_pool()
    async with pool.acquire() as db:
        rows = await db.fetch(
            """SELECT slot_id, sheet_row, sheet_col FROM requests
                WHERE operation_id = $1 AND status = 'approved'""",
            operation_id,
        )
        return [request_key(row) for row in rows]


async def get_member_active_request(guild_id: str, operation_id: int, member_id: str):
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetchrow(
            """SELECT * FROM requests
               WHERE guild_id = $1 AND operation_id = $2 AND member_id = $3
               AND status IN ('pending', 'approved')""",
            guild_id, operation_id, member_id,
        )


async def create_request(guild_id: str, operation_id: int, member_id: str,
                         member_name: str, slot_label: str, sheet_row: int = None,
                         sheet_col: int = None, unit_role: str = None,
                         slot_id: int = None) -> int:
    """One booking. `sheet_row`/`sheet_col` identify the slot on a sheet-backed
    operation, `slot_id` on an ORBAT-backed one — never both."""
    pool = await get_pool()
    async with pool.acquire() as db:
        row = await db.fetchrow(
            '''INSERT INTO requests
               (guild_id, operation_id, member_id, member_name, slot_label,
                sheet_row, sheet_col, unit_role, slot_id)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
               RETURNING id''',
            guild_id, operation_id, member_id, member_name, slot_label,
            sheet_row, sheet_col, unit_role, slot_id,
        )
        return row['id']


async def update_request_sheet_col(request_id: int, sheet_row: int, sheet_col: int):
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            'UPDATE requests SET sheet_row = $1, sheet_col = $2 WHERE id = $3',
            sheet_row, sheet_col, request_id,
        )


async def update_request_message(request_id: int, message_id: str, channel_id: str):
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            'UPDATE requests SET approval_message_id = $1, approval_channel_id = $2 WHERE id = $3',
            message_id, channel_id, request_id,
        )


async def get_request_by_id(request_id: int):
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetchrow('SELECT * FROM requests WHERE id = $1', request_id)


async def get_all_pending_requests() -> list:
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetch("SELECT * FROM requests WHERE status = 'pending'")


async def cancel_member_request(guild_id: str, operation_id: int, member_id: str) -> bool:
    pool = await get_pool()
    async with pool.acquire() as db:
        result = await db.execute(
            """UPDATE requests SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP
               WHERE guild_id = $1 AND operation_id = $2 AND member_id = $3 AND status = 'pending'""",
            guild_id, operation_id, member_id,
        )
        return int(result.split()[-1]) > 0


async def clear_pending_requests(operation_id: int) -> int:
    pool = await get_pool()
    async with pool.acquire() as db:
        result = await db.execute(
            """UPDATE requests SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP
               WHERE operation_id = $1 AND status = 'pending'""",
            operation_id,
        )
        return int(result.split()[-1])


async def get_active_requests(operation_id: int) -> list:
    """Return all pending and approved requests for an operation."""
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetch(
            "SELECT * FROM requests WHERE operation_id = $1 AND status IN ('pending', 'approved') ORDER BY status DESC, created_at",
            operation_id,
        )


async def cancel_request_by_id(request_id: int) -> bool:
    """Cancel one request, whether it was approved or still waiting.

    Both are cleared the same way — `/clear-slot` and the web queue offer it on
    a pending request too — so restricting this to `approved` left the pending
    ones cancelled everywhere except in the database: the member was DMed and
    the approval message went grey, while the row stayed pending and the board
    kept showing it as 🟡 forever.
    """
    pool = await get_pool()
    async with pool.acquire() as db:
        result = await db.execute(
            """UPDATE requests SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP
               WHERE id = $1 AND status IN ('pending', 'approved')""",
            request_id,
        )
        return int(result.split()[-1]) > 0


async def approve_request(request_id: int, approved_by: str):
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            """UPDATE requests
               SET status = 'approved', approved_by = $1, updated_at = CURRENT_TIMESTAMP
               WHERE id = $2""",
            approved_by, request_id,
        )


async def deny_request(request_id: int, denied_by: str, reason: str = None):
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            """UPDATE requests
               SET status = 'denied', approved_by = $1, denial_reason = $2, updated_at = CURRENT_TIMESTAMP
               WHERE id = $3""",
            denied_by, reason, request_id,
        )


async def save_orbat_message(guild_id: str, channel_id: str, message_id: str):
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            '''INSERT INTO orbat_messages (guild_id, channel_id, message_id)
               VALUES ($1, $2, $3)
               ON CONFLICT (guild_id) DO UPDATE SET
                   channel_id = EXCLUDED.channel_id,
                   message_id = EXCLUDED.message_id,
                   updated_at = CURRENT_TIMESTAMP''',
            guild_id, channel_id, message_id,
        )


async def get_orbat_message(guild_id: str):
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetchrow(
            'SELECT channel_id, message_id FROM orbat_messages WHERE guild_id = $1',
            guild_id,
        )


async def get_guild_timezone(guild_id: str) -> str:
    pool = await get_pool()
    async with pool.acquire() as db:
        row = await db.fetchrow(
            'SELECT timezone FROM guild_settings WHERE guild_id = $1', guild_id
        )
        return row['timezone'] if row else 'UTC'


async def set_guild_timezone(guild_id: str, timezone: str):
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            '''INSERT INTO guild_settings (guild_id, timezone)
               VALUES ($1, $2)
               ON CONFLICT (guild_id) DO UPDATE SET timezone = EXCLUDED.timezone''',
            guild_id, timezone,
        )


async def set_event_time(operation_id: int, event_time, reminder_minutes: int):
    # Store as naive UTC — the column is TIMESTAMP WITHOUT TIME ZONE
    if hasattr(event_time, 'tzinfo') and event_time.tzinfo is not None:
        event_time = event_time.replace(tzinfo=None)
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            '''UPDATE operations
               SET event_time = $1, reminder_minutes = $2, reminder_fired = 0
               WHERE id = $3''',
            event_time, reminder_minutes, operation_id,
        )


async def get_operations_needing_reminder():
    """Return active operations whose reminder window has arrived but not yet fired."""
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetch(
            '''SELECT * FROM operations
               WHERE is_active = 1
               AND event_time IS NOT NULL
               AND reminder_fired = 0
               AND event_time - (reminder_minutes * INTERVAL '1 minute') <= CURRENT_TIMESTAMP
               AND event_time > CURRENT_TIMESTAMP'''
        )


async def mark_reminder_fired(operation_id: int):
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            'UPDATE operations SET reminder_fired = 1 WHERE id = $1',
            operation_id,
        )


async def get_competing_requests(operation_id: int, key: str, exclude_request_id: int) -> list:
    """Every other pending request for the same slot.

    Matched on the roster key rather than in SQL, because the key is derived
    from either a slot id or a pair of sheet coordinates and there are only ever
    a handful of open requests on one operation.
    """
    from utils.roster import request_key
    pool = await get_pool()
    async with pool.acquire() as db:
        rows = await db.fetch(
            """SELECT * FROM requests
               WHERE operation_id = $1 AND id != $2 AND status = 'pending'""",
            operation_id, exclude_request_id,
        )
    return [row for row in rows if request_key(row) == key]


async def add_game_role(guild_id: str, role_id: str, name: str,
                        emoji: str = None, description: str = None):
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            '''INSERT INTO game_roles (guild_id, role_id, name, emoji, description)
               VALUES ($1, $2, $3, $4, $5)
               ON CONFLICT (guild_id, role_id) DO UPDATE SET
                   name = EXCLUDED.name,
                   emoji = EXCLUDED.emoji,
                   description = EXCLUDED.description''',
            guild_id, role_id, name, emoji, description,
        )


async def remove_game_role(guild_id: str, role_id: str) -> bool:
    pool = await get_pool()
    async with pool.acquire() as db:
        result = await db.execute(
            'DELETE FROM game_roles WHERE guild_id = $1 AND role_id = $2',
            guild_id, role_id,
        )
        return int(result.split()[-1]) > 0


async def get_game_roles(guild_id: str) -> list:
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetch(
            'SELECT * FROM game_roles WHERE guild_id = $1 ORDER BY name',
            guild_id,
        )


async def save_game_role_panel(guild_id: str, channel_id: str, message_id: str):
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            '''INSERT INTO game_role_panels (guild_id, channel_id, message_id)
               VALUES ($1, $2, $3)
               ON CONFLICT (guild_id) DO UPDATE SET
                   channel_id = EXCLUDED.channel_id,
                   message_id = EXCLUDED.message_id,
                   updated_at = CURRENT_TIMESTAMP''',
            guild_id, channel_id, message_id,
        )


async def get_game_role_panel(guild_id: str):
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetchrow(
            'SELECT channel_id, message_id FROM game_role_panels WHERE guild_id = $1',
            guild_id,
        )


# ---------------------------------------------------------------------------
# Events
#
# event_time is naive UTC, like operations.event_time. These queries compare it
# against NOW() AT TIME ZONE 'UTC' rather than CURRENT_TIMESTAMP so the result
# is correct regardless of the database session's timezone setting.
# ---------------------------------------------------------------------------

def _naive(dt):
    """Strip tzinfo so values match the naive-UTC columns."""
    if dt is not None and hasattr(dt, 'tzinfo') and dt.tzinfo is not None:
        return dt.replace(tzinfo=None)
    return dt


async def create_event(guild_id: str, title: str, event_time, created_by: str,
                       created_by_name: str = None, description: str = None,
                       duration_minutes: int = None, location: str = None,
                       image_url: str = None, mention_role_id: str = None,
                       reminder_minutes: int = 30, recurrence: str = None,
                       recurrence_until=None, recurrence_anchor=None) -> int:
    event_time = _naive(event_time)
    # A new series anchors on its own first start time.
    if recurrence and recurrence_anchor is None:
        recurrence_anchor = event_time
    pool = await get_pool()
    async with pool.acquire() as db:
        row = await db.fetchrow(
            '''INSERT INTO events
               (guild_id, title, event_time, created_by, created_by_name, description,
                duration_minutes, location, image_url, mention_role_id, reminder_minutes,
                recurrence, recurrence_until, recurrence_anchor)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
               RETURNING id''',
            guild_id, title, event_time, created_by, created_by_name, description,
            duration_minutes, location, image_url, mention_role_id, reminder_minutes,
            recurrence, _naive(recurrence_until), _naive(recurrence_anchor),
        )
        return row['id']


async def set_event_recurrence(event_id: int, recurrence: str = None,
                               recurrence_until=None, recurrence_anchor=None):
    """Set the recurrence outright. Unlike update_event this can clear it, which
    is how a series is stopped."""
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            '''UPDATE events
               SET recurrence = $2, recurrence_until = $3, recurrence_anchor = $4,
                   updated_at = CURRENT_TIMESTAMP
               WHERE id = $1''',
            event_id, recurrence, _naive(recurrence_until), _naive(recurrence_anchor),
        )


async def get_event(event_id: int):
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetchrow('SELECT * FROM events WHERE id = $1', event_id)


async def save_event_message(event_id: int, channel_id: str, message_id: str):
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            'UPDATE events SET channel_id = $1, message_id = $2 WHERE id = $3',
            channel_id, message_id, event_id,
        )


async def update_event(event_id: int, title: str = None, description: str = None,
                       event_time=None, duration_minutes: int = None,
                       location: str = None, image_url: str = None,
                       reminder_minutes: int = None):
    """Update only the fields that were passed. Changing the time re-arms the reminder."""
    if event_time is not None and hasattr(event_time, 'tzinfo') and event_time.tzinfo is not None:
        event_time = event_time.replace(tzinfo=None)
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            '''UPDATE events SET
                   title = COALESCE($2, title),
                   description = COALESCE($3, description),
                   event_time = COALESCE($4, event_time),
                   duration_minutes = COALESCE($5, duration_minutes),
                   location = COALESCE($6, location),
                   image_url = COALESCE($7, image_url),
                   reminder_minutes = COALESCE($8, reminder_minutes),
                   reminder_fired = CASE WHEN $4::timestamp IS NOT NULL
                                         THEN 0 ELSE reminder_fired END,
                   updated_at = CURRENT_TIMESTAMP
               WHERE id = $1''',
            event_id, title, description, event_time, duration_minutes,
            location, image_url, reminder_minutes,
        )


# Optional event columns that may legitimately be emptied again. update_event()
# uses COALESCE and so can only ever set a value, never remove one — this is its
# counterpart, the same way set_event_mentions() is for the ping roles.
_CLEARABLE_EVENT_FIELDS = frozenset({
    'description', 'location', 'image_url', 'duration_minutes', 'reminder_minutes',
})


async def clear_event_fields(event_id: int, fields: list):
    """Set the given optional columns back to NULL."""
    unknown = [name for name in fields if name not in _CLEARABLE_EVENT_FIELDS]
    if unknown:
        raise ValueError(f"Not a clearable event field: {', '.join(unknown)}")
    if not fields:
        return
    assignments = ', '.join(f'{name} = NULL' for name in fields)
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            f'UPDATE events SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE id = $1',
            event_id,
        )


async def set_event_mentions(event_id: int, role_ids: str = None):
    """Set the ping roles outright. update_event() uses COALESCE and so cannot
    clear them, which is what passing None here does."""
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            '''UPDATE events SET mention_role_id = $2, updated_at = CURRENT_TIMESTAMP
               WHERE id = $1''',
            event_id, role_ids,
        )


async def set_event_status(event_id: int, status: str) -> bool:
    pool = await get_pool()
    async with pool.acquire() as db:
        result = await db.execute(
            "UPDATE events SET status = $1, updated_at = CURRENT_TIMESTAMP WHERE id = $2",
            status, event_id,
        )
        return int(result.split()[-1]) > 0


async def get_upcoming_events(guild_id: str, limit: int = 25) -> list:
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetch(
            """SELECT * FROM events
               WHERE guild_id = $1 AND status = 'scheduled'
               ORDER BY event_time
               LIMIT $2""",
            guild_id, limit,
        )


async def delete_event(event_id: int) -> bool:
    """Remove an event outright. Sign-ups and custom responses go with it via
    ON DELETE CASCADE."""
    pool = await get_pool()
    async with pool.acquire() as db:
        result = await db.execute('DELETE FROM events WHERE id = $1', event_id)
        return int(result.split()[-1]) > 0


async def get_guild_events(guild_id: str, limit: int = 25) -> list:
    """Recent and upcoming events of any status — for picking one to delete."""
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetch(
            '''SELECT * FROM events WHERE guild_id = $1
               ORDER BY event_time DESC LIMIT $2''',
            guild_id, limit,
        )


async def get_live_events() -> list:
    """Every event whose message still needs working buttons — used to restore
    persistent views after a restart."""
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetch(
            "SELECT * FROM events WHERE status = 'scheduled' AND message_id IS NOT NULL"
        )


async def set_event_signup(event_id: int, member_id: str, member_name: str, response: str):
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            '''INSERT INTO event_signups (event_id, member_id, member_name, response)
               VALUES ($1, $2, $3, $4)
               ON CONFLICT (event_id, member_id) DO UPDATE SET
                   member_name = EXCLUDED.member_name,
                   response = EXCLUDED.response,
                   updated_at = CURRENT_TIMESTAMP''',
            event_id, member_id, member_name, response,
        )


async def remove_event_signup(event_id: int, member_id: str) -> bool:
    pool = await get_pool()
    async with pool.acquire() as db:
        result = await db.execute(
            'DELETE FROM event_signups WHERE event_id = $1 AND member_id = $2',
            event_id, member_id,
        )
        return int(result.split()[-1]) > 0


async def get_event_signup(event_id: int, member_id: str):
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetchrow(
            'SELECT * FROM event_signups WHERE event_id = $1 AND member_id = $2',
            event_id, member_id,
        )


async def get_event_signups(event_id: int) -> list:
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetch(
            'SELECT * FROM event_signups WHERE event_id = $1 ORDER BY created_at',
            event_id,
        )


async def get_event_responses(event_id: int) -> list:
    """Custom responses for an event, in display order. Empty means defaults."""
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetch(
            'SELECT * FROM event_responses WHERE event_id = $1 ORDER BY sort_order',
            event_id,
        )


async def set_event_responses(event_id: int, responses: list):
    """Replace an event's response set. An empty list restores the defaults."""
    pool = await get_pool()
    async with pool.acquire() as db:
        async with db.transaction():
            await db.execute('DELETE FROM event_responses WHERE event_id = $1', event_id)
            for order, item in enumerate(responses):
                await db.execute(
                    '''INSERT INTO event_responses
                       (event_id, key, label, emoji, is_decline, sort_order)
                       VALUES ($1, $2, $3, $4, $5, $6)''',
                    event_id, item['key'], item['label'], item.get('emoji'),
                    int(item.get('is_decline', 0)), order,
                )


async def drop_signups_not_in(event_id: int, valid_keys: list) -> int:
    """Remove sign-ups whose response no longer exists, so a changed response set
    can't leave rows that render nowhere."""
    pool = await get_pool()
    async with pool.acquire() as db:
        result = await db.execute(
            'DELETE FROM event_signups WHERE event_id = $1 AND NOT (response = ANY($2::text[]))',
            event_id, list(valid_keys),
        )
        return int(result.split()[-1])


async def get_events_needing_reminder() -> list:
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetch(
            """SELECT * FROM events
               WHERE status = 'scheduled'
                 AND reminder_fired = 0
                 AND reminder_minutes IS NOT NULL
                 AND event_time - (reminder_minutes * INTERVAL '1 minute')
                     <= (NOW() AT TIME ZONE 'UTC')
                 AND event_time > (NOW() AT TIME ZONE 'UTC')"""
        )


async def mark_event_reminder_fired(event_id: int):
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute('UPDATE events SET reminder_fired = 1 WHERE id = $1', event_id)


async def get_finished_events() -> list:
    """Scheduled events whose start time (plus duration) has passed."""
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetch(
            """SELECT * FROM events
               WHERE status = 'scheduled'
                 AND event_time + (COALESCE(duration_minutes, 0) * INTERVAL '1 minute')
                     < (NOW() AT TIME ZONE 'UTC')"""
        )


async def get_approved_member_ids(operation_id: int) -> list:
    pool = await get_pool()
    async with pool.acquire() as db:
        rows = await db.fetch(
            "SELECT member_id, slot_label FROM requests WHERE operation_id = $1 AND status = 'approved'",
            operation_id,
        )
        return [(row['member_id'], row['slot_label']) for row in rows]


# ---------------------------------------------------------------------------
# Embeds
#
# Every column the builder can set is written outright rather than through
# COALESCE: the form always submits the whole embed, and emptying a field has to
# be able to clear it.
# ---------------------------------------------------------------------------

_EMBED_COLUMNS = (
    'name', 'channel_id', 'content', 'title', 'description', 'url', 'color',
    'author_name', 'author_icon_url', 'thumbnail_url', 'image_url',
    'footer_text', 'footer_icon_url', 'show_timestamp',
)


async def create_embed(guild_id: str, created_by: str, created_by_name: str,
                       values: dict) -> int:
    columns = [name for name in _EMBED_COLUMNS if name in values]
    placeholders = ', '.join(f'${i}' for i in range(4, 4 + len(columns)))
    pool = await get_pool()
    async with pool.acquire() as db:
        row = await db.fetchrow(
            f'''INSERT INTO embeds (guild_id, created_by, created_by_name, {', '.join(columns)})
                VALUES ($1, $2, $3, {placeholders})
                RETURNING id''',
            guild_id, created_by, created_by_name, *[values[name] for name in columns],
        )
        return row['id']


async def update_embed(embed_id: int, values: dict):
    columns = [name for name in _EMBED_COLUMNS if name in values]
    if not columns:
        return
    assignments = ', '.join(f'{name} = ${i}' for i, name in enumerate(columns, start=2))
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            f'UPDATE embeds SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE id = $1',
            embed_id, *[values[name] for name in columns],
        )


async def get_embed(embed_id: int):
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetchrow('SELECT * FROM embeds WHERE id = $1', embed_id)


async def get_guild_embeds(guild_id: str) -> list:
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetch(
            'SELECT * FROM embeds WHERE guild_id = $1 ORDER BY updated_at DESC', guild_id
        )


async def save_embed_message(embed_id: int, channel_id: str, message_id: str):
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            '''UPDATE embeds SET channel_id = $2, message_id = $3,
                   updated_at = CURRENT_TIMESTAMP
               WHERE id = $1''',
            embed_id, channel_id, message_id,
        )


async def clear_embed_message(embed_id: int):
    """Forget where the embed was posted — used when its message is gone, so the
    next send posts a new one instead of failing to edit a deleted message."""
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            'UPDATE embeds SET message_id = NULL WHERE id = $1', embed_id
        )


async def delete_embed(embed_id: int) -> bool:
    pool = await get_pool()
    async with pool.acquire() as db:
        result = await db.execute('DELETE FROM embeds WHERE id = $1', embed_id)
        return int(result.split()[-1]) > 0


async def get_embed_fields(embed_id: int) -> list:
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetch(
            'SELECT * FROM embed_fields WHERE embed_id = $1 ORDER BY sort_order', embed_id
        )


async def set_embed_fields(embed_id: int, fields: list):
    """Replace an embed's fields. An empty list removes them all."""
    pool = await get_pool()
    async with pool.acquire() as db:
        async with db.transaction():
            await db.execute('DELETE FROM embed_fields WHERE embed_id = $1', embed_id)
            for order, item in enumerate(fields):
                await db.execute(
                    '''INSERT INTO embed_fields (embed_id, name, value, inline, sort_order)
                       VALUES ($1, $2, $3, $4, $5)''',
                    embed_id, item['name'], item['value'],
                    int(item.get('inline', 0)), order,
                )


# ---------------------------------------------------------------------------
# Member logging
# ---------------------------------------------------------------------------

_LOG_COLUMNS = (
    'channel_id', 'log_join', 'log_leave', 'log_kick', 'log_ban', 'log_unban',
    'track_invites',
)


async def get_log_settings(guild_id: str):
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetchrow('SELECT * FROM log_settings WHERE guild_id = $1', guild_id)


async def save_log_settings(guild_id: str, values: dict):
    columns = [name for name in _LOG_COLUMNS if name in values]
    if not columns:
        return
    updates = ', '.join(f'{name} = EXCLUDED.{name}' for name in columns)
    placeholders = ', '.join(f'${i}' for i in range(2, 2 + len(columns)))
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            f'''INSERT INTO log_settings (guild_id, {', '.join(columns)})
                VALUES ($1, {placeholders})
                ON CONFLICT (guild_id) DO UPDATE SET
                    {updates}, updated_at = CURRENT_TIMESTAMP''',
            guild_id, *[values[name] for name in columns],
        )


# ---------------------------------------------------------------------------
# Voice time
#
# `seconds` is filled when an interval closes. `heartbeat_at` is refreshed while
# one is open, so a hard crash costs at most one heartbeat of unrecorded time
# instead of the whole session — and so a live total can include the interval
# somebody is in right now.
# ---------------------------------------------------------------------------

# The elapsed time of one row, closed or still running.
_VOICE_SECONDS = """
    COALESCE(seconds, GREATEST(0, EXTRACT(EPOCH FROM
        (COALESCE(heartbeat_at, started_at) - started_at))))
"""


async def get_voice_settings(guild_id: str):
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetchrow('SELECT * FROM voice_settings WHERE guild_id = $1', guild_id)


_VOICE_SETTING_COLUMNS = (
    'enabled', 'channel_id', 'min_log_minutes', 'count_afk', 'count_solo',
    'excluded_channels', 'board_enabled', 'board_channel_id', 'board_period',
    'board_hour',
)


async def save_voice_settings(guild_id: str, values: dict):
    columns = [name for name in _VOICE_SETTING_COLUMNS if name in values]
    if not columns:
        return
    updates = ', '.join(f'{name} = EXCLUDED.{name}' for name in columns)
    placeholders = ', '.join(f'${i}' for i in range(2, 2 + len(columns)))
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            f'''INSERT INTO voice_settings (guild_id, {', '.join(columns)})
                VALUES ($1, {placeholders})
                ON CONFLICT (guild_id) DO UPDATE SET
                    {updates}, updated_at = CURRENT_TIMESTAMP''',
            guild_id, *[values[name] for name in columns],
        )


async def start_voice_session(guild_id: str, member_id: str, member_name: str,
                              channel_id: str, channel_name: str, started_at) -> int:
    started_at = _naive(started_at)
    pool = await get_pool()
    async with pool.acquire() as db:
        row = await db.fetchrow(
            '''INSERT INTO voice_sessions
               (guild_id, member_id, member_name, channel_id, channel_name,
                started_at, heartbeat_at)
               VALUES ($1, $2, $3, $4, $5, $6, $6)
               RETURNING id''',
            guild_id, member_id, member_name, channel_id, channel_name, started_at,
        )
        return row['id']


async def end_voice_session(session_id: int, ended_at) -> int:
    """Close one interval and return how many seconds it lasted."""
    ended_at = _naive(ended_at)
    pool = await get_pool()
    async with pool.acquire() as db:
        row = await db.fetchrow(
            '''UPDATE voice_sessions
               SET ended_at = $2, heartbeat_at = $2,
                   seconds = GREATEST(0, EXTRACT(EPOCH FROM ($2 - started_at))::int)
               WHERE id = $1 AND ended_at IS NULL
               RETURNING seconds''',
            session_id, ended_at,
        )
        return row['seconds'] if row else 0


async def heartbeat_voice_sessions(session_ids: list, moment) -> None:
    if not session_ids:
        return
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            'UPDATE voice_sessions SET heartbeat_at = $2 WHERE id = ANY($1::int[])',
            list(session_ids), _naive(moment),
        )


async def close_dangling_voice_sessions() -> int:
    """Close intervals left open by a crash, at their last heartbeat.

    Never invents time: without a heartbeat the interval closes at its start and
    counts zero.
    """
    pool = await get_pool()
    async with pool.acquire() as db:
        result = await db.execute(
            '''UPDATE voice_sessions
               SET ended_at = COALESCE(heartbeat_at, started_at),
                   seconds = GREATEST(0, EXTRACT(EPOCH FROM
                       (COALESCE(heartbeat_at, started_at) - started_at))::int)
               WHERE ended_at IS NULL'''
        )
        return int(result.split()[-1])


async def get_voice_leaderboard(guild_id: str, since=None, limit: int = 25) -> list:
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetch(
            f'''SELECT member_id,
                       MAX(member_name) AS member_name,
                       SUM({_VOICE_SECONDS})::bigint AS total_seconds,
                       COUNT(*) AS sessions,
                       MAX(COALESCE(ended_at, heartbeat_at)) AS last_seen
                FROM voice_sessions
                WHERE guild_id = $1 AND ($2::timestamp IS NULL OR started_at >= $2)
                GROUP BY member_id
                HAVING SUM({_VOICE_SECONDS}) > 0
                ORDER BY total_seconds DESC
                LIMIT $3''',
            guild_id, _naive(since), limit,
        )


async def get_voice_member_total(guild_id: str, member_id: str, since=None):
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetchrow(
            f'''SELECT SUM({_VOICE_SECONDS})::bigint AS total_seconds,
                       COUNT(*) AS sessions
                FROM voice_sessions
                WHERE guild_id = $1 AND member_id = $2
                  AND ($3::timestamp IS NULL OR started_at >= $3)''',
            guild_id, member_id, _naive(since),
        )


async def get_voice_channel_totals(guild_id: str, since=None, limit: int = 10) -> list:
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetch(
            f'''SELECT channel_id,
                       MAX(channel_name) AS channel_name,
                       SUM({_VOICE_SECONDS})::bigint AS total_seconds
                FROM voice_sessions
                WHERE guild_id = $1 AND ($2::timestamp IS NULL OR started_at >= $2)
                GROUP BY channel_id
                HAVING SUM({_VOICE_SECONDS}) > 0
                ORDER BY total_seconds DESC
                LIMIT $3''',
            guild_id, _naive(since), limit,
        )


# ---------------------------------------------------------------------------
# Invite labels
# ---------------------------------------------------------------------------

async def get_invite_labels(guild_id: str) -> dict:
    """{invite code: label} for one guild."""
    pool = await get_pool()
    async with pool.acquire() as db:
        rows = await db.fetch(
            'SELECT code, label FROM invite_labels WHERE guild_id = $1', guild_id
        )
        return {row['code']: row['label'] for row in rows}


async def save_invite_labels(guild_id: str, labels: dict, remove: list = None):
    """Store the labels that were filled in and drop the ones that were emptied."""
    pool = await get_pool()
    async with pool.acquire() as db:
        async with db.transaction():
            for code, label in labels.items():
                await db.execute(
                    '''INSERT INTO invite_labels (guild_id, code, label)
                       VALUES ($1, $2, $3)
                       ON CONFLICT (guild_id, code) DO UPDATE SET
                           label = EXCLUDED.label,
                           updated_at = CURRENT_TIMESTAMP''',
                    guild_id, code, label,
                )
            if remove:
                await db.execute(
                    'DELETE FROM invite_labels WHERE guild_id = $1 AND code = ANY($2::text[])',
                    guild_id, list(remove),
                )


async def get_voice_boards() -> list:
    """Every guild whose self-updating leaderboard is switched on."""
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetch(
            """SELECT * FROM voice_settings
               WHERE board_enabled = 1 AND board_channel_id IS NOT NULL"""
        )


async def set_voice_board_state(guild_id: str, message_id: str = None,
                                updated_on=None, channel_id: str = None):
    """Remember which message the board is, and which local day it last showed.

    Passing message_id=None clears it, which is how a deleted message makes the
    next refresh post a fresh one.
    """
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            """UPDATE voice_settings
               SET board_message_id = $2,
                   board_channel_id = COALESCE($4, board_channel_id),
                   board_updated_on = COALESCE($3, board_updated_on),
                   updated_at = CURRENT_TIMESTAMP
               WHERE guild_id = $1""",
            guild_id, message_id, updated_on, channel_id,
        )


# ---------------------------------------------------------------------------
# ORBATs
# ---------------------------------------------------------------------------

async def get_guild_orbats(guild_id: str) -> list:
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetch(
            '''SELECT o.*,
                      (SELECT COUNT(*) FROM orbat_squads q WHERE q.orbat_id = o.id)
                        AS squad_count,
                      (SELECT COUNT(*) FROM orbat_slots s
                         JOIN orbat_squads q ON q.id = s.squad_id
                        WHERE q.orbat_id = o.id) AS slot_count
                 FROM orbats o
                WHERE o.guild_id = $1
                ORDER BY o.updated_at DESC''',
            guild_id,
        )


async def get_orbat(orbat_id: int):
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetchrow('SELECT * FROM orbats WHERE id = $1', orbat_id)


async def create_orbat(guild_id: str, name: str, description: str,
                       created_by: str, created_by_name: str) -> int:
    pool = await get_pool()
    async with pool.acquire() as db:
        row = await db.fetchrow(
            '''INSERT INTO orbats (guild_id, name, description, created_by, created_by_name)
               VALUES ($1, $2, $3, $4, $5) RETURNING id''',
            guild_id, name, description, created_by, created_by_name,
        )
        return row['id']


async def rename_orbat(orbat_id: int, name: str, description: str = None):
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            '''UPDATE orbats SET name = $2, description = $3,
                                 updated_at = CURRENT_TIMESTAMP
               WHERE id = $1''',
            orbat_id, name, description,
        )


async def orbat_operations(orbat_id: int) -> list:
    """The operations running on this ORBAT, newest first."""
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetch(
            '''SELECT * FROM operations WHERE orbat_id = $1
               ORDER BY is_active DESC, created_at DESC''',
            orbat_id,
        )


async def delete_orbat(orbat_id: int) -> bool:
    """Delete an ORBAT, taking everyone booked into it off the roster first.

    The cascade removes the squads and slots; `requests.slot_id` carries no
    foreign key, so without releasing the bookings here an approved request
    would survive pointing at a slot that no longer exists — the same orphan
    `apply_orbat_structure()` goes out of its way to avoid on an edit.
    """
    pool = await get_pool()
    async with pool.acquire() as db:
        async with db.transaction():
            await db.execute(
                """UPDATE requests
                      SET status = 'cancelled',
                          slot_id = NULL,
                          denial_reason = COALESCE(denial_reason,
                                                   'ORBAT deleted'),
                          updated_at = CURRENT_TIMESTAMP
                    WHERE status IN ('pending', 'approved')
                      AND slot_id IN (
                            SELECT s.id FROM orbat_slots s
                              JOIN orbat_squads q ON q.id = s.squad_id
                             WHERE q.orbat_id = $1)""",
                orbat_id,
            )
            result = await db.execute('DELETE FROM orbats WHERE id = $1', orbat_id)
        return result.endswith('1')


async def get_orbat_structure(orbat_id: int, operation_id: int = None) -> list:
    """Squads with their slots, in order, each slot carrying its bookings.

    A booking is a row in `requests` pointing at the slot. They are read here
    rather than stored on the slot so the same ORBAT can back several operations
    at once — and so an edit can say who it would unseat.

    `bookings` is always every operation's, which is what the editor's
    confirmation page needs. `booking` and `pending` are the one operation's
    when *operation_id* is given, so a board shows that night and no other.
    """
    pool = await get_pool()
    async with pool.acquire() as db:
        squads = [
            dict(row) for row in await db.fetch(
                'SELECT * FROM orbat_squads WHERE orbat_id = $1 ORDER BY sort_order, id',
                orbat_id,
            )
        ]
        if not squads:
            return []
        by_id = {}
        for squad in squads:
            squad['slots'] = []
            by_id[squad['id']] = squad

        slots = await db.fetch(
            '''SELECT * FROM orbat_slots WHERE squad_id = ANY($1::int[])
               ORDER BY sort_order, id''',
            list(by_id),
        )
        bookings: dict = {}
        if slots:
            rows = await db.fetch(
                '''SELECT r.id AS request_id, r.slot_id, r.member_name, r.unit_role,
                          r.status, r.operation_id, o.name AS operation_name
                     FROM requests r
                     JOIN operations o ON o.id = r.operation_id
                    WHERE r.slot_id = ANY($1::int[])
                      AND r.status IN ('pending', 'approved')''',
                [row['id'] for row in slots],
            )
            for row in rows:
                bookings.setdefault(row['slot_id'], []).append(dict(row))

        for row in slots:
            slot = dict(row)
            here = bookings.get(slot['id'], [])
            slot['bookings'] = here
            mine = ([b for b in here if b['operation_id'] == operation_id]
                    if operation_id is not None else here)
            slot['booking'] = next((b for b in mine if b['status'] == 'approved'), None)
            slot['pending'] = any(b['status'] == 'pending' for b in mine)
            by_id[slot['squad_id']]['slots'].append(slot)

        return squads


async def apply_orbat_structure(orbat_id: int, parsed_squads: list, diff,
                                source_text: str = None):
    """Write the parsed structure, keeping the ids the diff matched.

    Order matters: removals go first, so a squad name freed by this edit can be
    reused by another squad in the same edit.
    """
    squad_of_new, slot_of_new = {}, {}
    remove_squads, remove_slots = [], []

    for change in diff.squads:
        if change.kind == 'removed':
            remove_squads.append(change.old['id'])
            continue
        squad_of_new[id(change.new)] = change.old['id'] if change.old else None
        for slot_change in change.slots:
            if slot_change.kind == 'removed':
                remove_slots.append(slot_change.old['id'])
            elif slot_change.new is not None:
                slot_of_new[id(slot_change.new)] = (
                    slot_change.old['id'] if slot_change.old else None
                )

    pool = await get_pool()
    async with pool.acquire() as db:
        async with db.transaction():
            if remove_squads:
                # ON DELETE CASCADE takes the squad's slots with it, so their
                # bookings need releasing here just as much as a single removed
                # slot's do.
                await db.execute(
                    """UPDATE requests
                          SET status = 'cancelled',
                              slot_id = NULL,
                              denial_reason = COALESCE(denial_reason,
                                                       'Slot removed from the ORBAT'),
                              updated_at = CURRENT_TIMESTAMP
                        WHERE status IN ('pending', 'approved')
                          AND slot_id IN (SELECT id FROM orbat_slots
                                           WHERE squad_id = ANY($1::int[]))""",
                    remove_squads,
                )
                await db.execute(
                    'DELETE FROM orbat_squads WHERE id = ANY($1::int[])', remove_squads
                )
            if remove_slots:
                # Take the people on those slots off the roster first. The
                # confirmation page promises exactly that, and `slot_id` carries
                # no foreign key — without this the request would survive as an
                # approved booking pointing at a slot that no longer exists,
                # invisible on every board.
                await db.execute(
                    """UPDATE requests
                          SET status = 'cancelled',
                              slot_id = NULL,
                              denial_reason = COALESCE(denial_reason,
                                                       'Slot removed from the ORBAT'),
                              updated_at = CURRENT_TIMESTAMP
                        WHERE slot_id = ANY($1::int[])
                          AND status IN ('pending', 'approved')""",
                    remove_slots,
                )
                await db.execute(
                    'DELETE FROM orbat_slots WHERE id = ANY($1::int[])', remove_slots
                )

            for order, squad in enumerate(parsed_squads):
                squad_id = squad_of_new.get(id(squad))
                values = (squad.name, squad.column, int(squad.exclude_from_count),
                          squad.reserved_unit, squad.radio, order)
                if squad_id:
                    await db.execute(
                        '''UPDATE orbat_squads
                              SET name = $2, column_side = $3, exclude_from_count = $4,
                                  reserved_unit = $5, radio = $6, sort_order = $7
                            WHERE id = $1''',
                        squad_id, *values,
                    )
                else:
                    row = await db.fetchrow(
                        '''INSERT INTO orbat_squads
                           (orbat_id, name, column_side, exclude_from_count,
                            reserved_unit, radio, sort_order)
                           VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING id''',
                        orbat_id, *values,
                    )
                    squad_id = row['id']

                for slot_order, slot in enumerate(squad.slots):
                    slot_id = slot_of_new.get(id(slot))
                    fields = (slot.role_name, slot_order, squad_id)
                    if slot_id:
                        await db.execute(
                            '''UPDATE orbat_slots
                                  SET role_name = $2, sort_order = $3, squad_id = $4
                                WHERE id = $1''',
                            slot_id, *fields,
                        )
                    else:
                        await db.execute(
                            '''INSERT INTO orbat_slots
                               (role_name, sort_order, squad_id)
                               VALUES ($1, $2, $3)''',
                            *fields,
                        )

            await db.execute(
                '''UPDATE orbats SET updated_at = CURRENT_TIMESTAMP,
                                     source_text = COALESCE($2, source_text)
                   WHERE id = $1''',
                orbat_id, source_text,
            )


async def get_orbat_nets(orbat_id: int) -> list:
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetch(
            'SELECT * FROM orbat_nets WHERE orbat_id = $1 ORDER BY sort_order, id',
            orbat_id,
        )


async def set_orbat_nets(orbat_id: int, nets: list, nets_text: str = None):
    """Replace the net list. Nothing hangs off a net, so unlike the squads and
    slots there is nothing to match up — the old rows go and the new ones land."""
    pool = await get_pool()
    async with pool.acquire() as db:
        async with db.transaction():
            await db.execute('DELETE FROM orbat_nets WHERE orbat_id = $1', orbat_id)
            for order, net in enumerate(nets):
                await db.execute(
                    '''INSERT INTO orbat_nets (orbat_id, name, channel, inactive, sort_order)
                       VALUES ($1, $2, $3, $4, $5)''',
                    orbat_id, net.name, net.channel, int(net.inactive), order,
                )
            await db.execute(
                '''UPDATE orbats SET nets_text = $2, updated_at = CURRENT_TIMESTAMP
                   WHERE id = $1''',
                orbat_id, nets_text,
            )


async def duplicate_orbat(orbat_id: int, name: str, created_by: str,
                          created_by_name: str) -> int:
    """Copy the structure, never the bookings — that is what a template is."""
    source = await get_orbat(orbat_id)
    squads = await get_orbat_structure(orbat_id)
    nets = await get_orbat_nets(orbat_id)
    new_id = await create_orbat(
        source['guild_id'], name, source['description'], created_by, created_by_name
    )
    pool = await get_pool()
    async with pool.acquire() as db:
        async with db.transaction():
            for squad in squads:
                row = await db.fetchrow(
                    '''INSERT INTO orbat_squads
                       (orbat_id, name, column_side, exclude_from_count,
                        reserved_unit, radio, sort_order)
                       VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING id''',
                    new_id, squad['name'], squad['column_side'],
                    squad['exclude_from_count'], squad['reserved_unit'],
                    squad['radio'], squad['sort_order'],
                )
                for slot in squad['slots']:
                    await db.execute(
                        '''INSERT INTO orbat_slots (squad_id, role_name, sort_order)
                           VALUES ($1, $2, $3)''',
                        row['id'], slot['role_name'], slot['sort_order'],
                    )
            for order, net in enumerate(nets):
                await db.execute(
                    '''INSERT INTO orbat_nets (orbat_id, name, channel, inactive, sort_order)
                       VALUES ($1, $2, $3, $4, $5)''',
                    new_id, net['name'], net['channel'], net['inactive'], order,
                )
            await db.execute(
                'UPDATE orbats SET source_text = $2, nets_text = $3 WHERE id = $1',
                new_id, source['source_text'], source['nets_text'],
            )
    return new_id

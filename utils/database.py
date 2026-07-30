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


async def get_active_operation(guild_id: str):
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetchrow(
            'SELECT * FROM operations WHERE guild_id = $1 AND is_active = 1 ORDER BY created_at DESC LIMIT 1',
            guild_id,
        )


async def create_operation(guild_id: str, name: str, sheet_url: str, sheet_id: str,
                           squad_col: int, role_col: int, status_col: int, assigned_col: int) -> int:
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            'UPDATE operations SET is_active = 0 WHERE guild_id = $1',
            guild_id,
        )
        row = await db.fetchrow(
            '''INSERT INTO operations
               (guild_id, name, sheet_url, sheet_id, squad_col, role_col, status_col, assigned_col)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
               RETURNING id''',
            guild_id, name, sheet_url, sheet_id, squad_col, role_col, status_col, assigned_col,
        )
        return row['id']


async def get_pending_slots(operation_id: int) -> list:
    pool = await get_pool()
    async with pool.acquire() as db:
        rows = await db.fetch(
            "SELECT sheet_row, sheet_col FROM requests WHERE operation_id = $1 AND status = 'pending'",
            operation_id,
        )
        return [(row['sheet_row'], row['sheet_col']) for row in rows]


async def get_approved_slots(operation_id: int) -> list:
    pool = await get_pool()
    async with pool.acquire() as db:
        rows = await db.fetch(
            "SELECT sheet_row, sheet_col FROM requests WHERE operation_id = $1 AND status = 'approved'",
            operation_id,
        )
        return [(row['sheet_row'], row['sheet_col']) for row in rows]


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
                         member_name: str, slot_label: str, sheet_row: int,
                         sheet_col: int = None, unit_role: str = None) -> int:
    pool = await get_pool()
    async with pool.acquire() as db:
        row = await db.fetchrow(
            '''INSERT INTO requests
               (guild_id, operation_id, member_id, member_name, slot_label, sheet_row, sheet_col, unit_role)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
               RETURNING id''',
            guild_id, operation_id, member_id, member_name, slot_label, sheet_row, sheet_col, unit_role,
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


async def get_approved_requests(operation_id: int) -> list:
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetch(
            "SELECT * FROM requests WHERE operation_id = $1 AND status = 'approved'",
            operation_id,
        )


async def get_active_requests(operation_id: int) -> list:
    """Return all pending and approved requests for an operation."""
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetch(
            "SELECT * FROM requests WHERE operation_id = $1 AND status IN ('pending', 'approved') ORDER BY status DESC, created_at",
            operation_id,
        )


async def cancel_request_by_id(request_id: int) -> bool:
    pool = await get_pool()
    async with pool.acquire() as db:
        result = await db.execute(
            """UPDATE requests SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP
               WHERE id = $1 AND status = 'approved'""",
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


async def save_open_slots_message(guild_id: str, channel_id: str, message_id: str):
    pool = await get_pool()
    async with pool.acquire() as db:
        await db.execute(
            '''INSERT INTO open_slots_messages (guild_id, channel_id, message_id)
               VALUES ($1, $2, $3)
               ON CONFLICT (guild_id) DO UPDATE SET
                   channel_id = EXCLUDED.channel_id,
                   message_id = EXCLUDED.message_id,
                   updated_at = CURRENT_TIMESTAMP''',
            guild_id, channel_id, message_id,
        )


async def get_open_slots_message(guild_id: str):
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetchrow(
            'SELECT channel_id, message_id FROM open_slots_messages WHERE guild_id = $1',
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


async def get_competing_requests(operation_id: int, sheet_row: int, sheet_col: int, exclude_request_id: int) -> list:
    """Return all other pending requests for the same slot cell (row + col)."""
    pool = await get_pool()
    async with pool.acquire() as db:
        return await db.fetch(
            """SELECT * FROM requests
               WHERE operation_id = $1 AND sheet_row = $2 AND sheet_col = $3
               AND id != $4 AND status = 'pending'""",
            operation_id, sheet_row, sheet_col, exclude_request_id,
        )


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

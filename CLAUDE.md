# CLAUDE.md — ORBATBot / orbat-platform

This file gives full context on the project. Read it before making any changes.

---

## What This Is

A Discord bot for managing Arma 3 operation slot requests across multiple military simulation units. Members request slots; Unit Leaders and admins approve or deny via Discord buttons. The Google Sheet is updated automatically on approval.

**Current state:** Fully operational bot deployed on Railway, plus an optional web UI (`web/`) with Discord OAuth2 login for managing events from a browser.
**Next phase:** the web UI now covers events, game roles, embeds, the member log and voice time. Slots and the ORBAT board are deliberately still Discord- and Sheets-only — see [Web UI](#web-ui-web) at the bottom of this file.

---

## Repository Structure

```
bot.py                  # Entry point — ORBATBot class, startup, reminder task
cogs/
  slots.py              # All member-facing commands + approval/denial flow + views
  admin.py              # All admin/unit-leader commands
  gameroles.py          # Self-assignable game roles (Minecraft, DCS, …) + panel
  events.py             # Standalone events with RSVP buttons + reminder loop
  memberlog.py          # Join/leave/kick/ban announcements + invite attribution
  voicelog.py           # Time spent in voice channels, counted in intervals
utils/
  database.py           # All PostgreSQL queries (asyncpg)
  sheets.py             # Google Sheets read/write (gspread)
  embeds.py             # Builder-made rich messages → discord.Embed, post and edit
web/                    # Optional browser UI — Discord OAuth2 login + event management
  config.py             # Env-driven config; the feature is off until it is complete
  server.py             # uvicorn driven from inside the bot's event loop
  app.py                # FastAPI routes, session plumbing, template rendering
  auth.py               # OAuth2 flow, signed-cookie sessions, CSRF, flash messages
  guilds.py             # Signed-in user → discord.Member, and what they may do
  service.py            # Create/edit/cancel/delete/RSVP, on top of cogs/events.py
  roles.py              # Game roles, on top of cogs/gameroles.py
  embeds.py             # Embed builder forms, on top of utils/embeds.py
  voice.py              # Voice leaderboard shaping, the settings form and posting
  invites.py            # Invite labels — where each link was published
  helpers.py            # Guild-timezone formatting and datetime-local parsing
  templates/ static/    # Jinja2 templates and one stylesheet — no build step
requirements.txt
Dockerfile
docker-compose.yml      # Bot + PostgreSQL 16
Procfile                # Railway: python bot.py
railway.json            # Railway build + ON_FAILURE restart policy (backs /restart)
.env.example            # Template for local/Docker runs — keep in sync with the table below
.python-version         # 3.11
README.md               # User-facing docs — commands, setup, deployment
CLAUDE.md               # This file
```

There is no test suite, CI or linter config. The date logic in `cogs/events.py`
(`_next_occurrence()`, `_weekday_day()`, `_add_months()`, `_nth_occurrence()`) is
pure and Discord-free, so it is the obvious first thing to cover if that changes.

---

## Environment Variables

| Variable | Description |
|---|---|
| `DISCORD_TOKEN` | Bot token from Discord Developer Portal |
| `GOOGLE_CREDENTIALS` | Full JSON content of the service account key file |
| `DB_PASSWORD` | PostgreSQL password (docker-compose only) |
| `DATABASE_URL` | Full connection string — injected automatically by Railway; constructed by docker-compose; set by hand for local development |
| `RAILWAY_API_TOKEN` | **Optional.** Account or team token that lets `/restart` trigger a clean deployment restart via the Railway GraphQL API. Without it `/restart` still works, by exiting non-zero so Railway's `ON_FAILURE` policy relaunches the container. Project tokens do **not** work — `_railway_restart()` authenticates with a `Bearer` header |
| `DISCORD_CLIENT_ID` / `DISCORD_CLIENT_SECRET` | **Optional.** OAuth2 credentials for the web UI. Missing → no web server is started |
| `WEB_SECRET_KEY` | **Optional.** Signs the session, OAuth-state and flash cookies. Changing it signs everyone out |
| `WEB_BASE_URL` | **Optional.** Public origin, no trailing slash. Must match the redirect URI registered in the Developer Portal; also decides whether cookies are marked `Secure`. Empty → derived from the request, which is only meant for local runs |
| `WEB_HOST` / `WEB_PORT` | **Optional.** Listen address. `PORT` (injected by Railway) wins over `WEB_PORT` |
| `WEB_ENABLED` | **Optional.** `0` keeps the site off even when everything else is set |
| `WEB_BRAND` | **Optional.** Site name in the header, tab title and footer. Defaults to `TFP BOT` |
| `MEMBER_EVENTS` | **Optional.** `1` requests the privileged members intent, which `cogs/memberlog.py` needs for joins and leaves. **Only set it once "Server Members Intent" is ticked in the Developer Portal** — requesting an ungranted privileged intent makes login fail, taking the whole bot down. Bans and unbans need no intent |

Railway injects these at runtime; nothing sets them manually. `_railway_restart()` reads them to find the deployment to restart:

| Variable | Used for |
|---|---|
| `RAILWAY_DEPLOYMENT_ID` | The running container's own deployment — preferred, because it guarantees the bot restarts *itself* |
| `RAILWAY_SERVICE_ID` / `RAILWAY_ENVIRONMENT_ID` | Fallback when the deployment id is absent: query the service's latest `SUCCESS` deployment |
| `RAILWAY_PROJECT_ID` | Narrows that fallback query when present |

---

## Database Schema

All tables live in PostgreSQL. Managed via `utils/database.py`. Schema is created/migrated in `init_db()` using `CREATE TABLE IF NOT EXISTS` and `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`.

### `operations`
| Column | Type | Notes |
|---|---|---|
| `id` | SERIAL PK | |
| `guild_id` | TEXT | Discord guild (server) ID |
| `name` | TEXT | Operation name (from sheet) |
| `sheet_url` | TEXT | Full Google Sheets URL |
| `sheet_id` | TEXT | Extracted sheet ID |
| `squad_col` | INTEGER | **Always NULL** — see [Google Sheets Integration](#google-sheets-integration-utilssheetspy) |
| `role_col` | INTEGER | **Always NULL** |
| `status_col` | INTEGER | **Always NULL** |
| `assigned_col` | INTEGER | **Always NULL** |
| `is_active` | INTEGER | 1 = active, 0 = archived. Only one active per guild |
| `event_time` | TIMESTAMP | Naive UTC. NULL if not set |
| `reminder_minutes` | INTEGER | Default 30 |
| `reminder_fired` | INTEGER | 0/1 — prevents double-firing |
| `created_at` | TIMESTAMP | |

### `requests`
| Column | Type | Notes |
|---|---|---|
| `id` | SERIAL PK | |
| `guild_id` | TEXT | |
| `operation_id` | INTEGER | FK → operations.id |
| `member_id` | TEXT | Discord user ID |
| `member_name` | TEXT | Display name at time of request |
| `slot_label` | TEXT | Human-readable label e.g. "1-2 (TFP) – Rifleman" |
| `sheet_row` | INTEGER | Row index in the sheet |
| `sheet_col` | INTEGER | Column index (ORBAT-style sheets) |
| `status` | TEXT | `pending` / `approved` / `denied` / `cancelled` |
| `approval_message_id` | TEXT | Discord message ID in #slot-approvals |
| `approval_channel_id` | TEXT | Discord channel ID for above |
| `approved_by` | TEXT | Display name of approver/denier |
| `denial_reason` | TEXT | Optional reason text |
| `unit_role` | TEXT | Unit role of the requester at submission time |
| `created_at` | TIMESTAMP | |
| `updated_at` | TIMESTAMP | |

### `orbat_messages`
| Column | Type | Notes |
|---|---|---|
| `guild_id` | TEXT PK | |
| `channel_id` | TEXT | Channel where ORBAT embed lives |
| `message_id` | TEXT | Message ID of the live ORBAT embed |
| `updated_at` | TIMESTAMP | |

### `open_slots_messages`
Same structure as `orbat_messages`. Reserved for a planned secondary "open slots" message. **Nothing reads or writes it** — the table is still created so existing deployments aren't orphaned, but the accessors were removed as dead code. Re-add them if the feature is picked up.

### `guild_settings`
| Column | Type | Notes |
|---|---|---|
| `guild_id` | TEXT PK | |
| `timezone` | TEXT | IANA timezone string, default `UTC` |

### `game_roles`
Self-assignable cosmetic roles (Minecraft, DCS, …) — see Game Roles below.

| Column | Type | Notes |
|---|---|---|
| `id` | SERIAL PK | |
| `guild_id` | TEXT | |
| `role_id` | TEXT | Discord role ID |
| `name` | TEXT | Label shown in the picker |
| `emoji` | TEXT | Optional emoji shown on the select option |
| `description` | TEXT | Optional one-liner shown under the option |
| `created_at` | TIMESTAMP | |

`UNIQUE (guild_id, role_id)` — re-adding the same role updates it in place.

### `game_role_panels`
Same structure as `orbat_messages`. Tracks the live game-role self-assign panel message.

### `events`
Standalone events, independent of `operations` and Google Sheets — see Events below.

| Column | Type | Notes |
|---|---|---|
| `id` | SERIAL PK | Shown to users as `#id` |
| `guild_id` | TEXT | |
| `channel_id` | TEXT | Channel the event message lives in |
| `message_id` | TEXT | The event message; NULL until posted |
| `title` | TEXT | |
| `description` | TEXT | Optional |
| `event_time` | TIMESTAMP | **Naive UTC**, like `operations.event_time` |
| `duration_minutes` | INTEGER | Optional; drives the end time and "finished" detection |
| `location` | TEXT | Optional free text |
| `image_url` | TEXT | Optional banner |
| `mention_role_id` | TEXT | **Comma-separated** role ids pinged on post and on the reminder. Singular name kept: a row written before multi-role support holds one id, which parses as a one-item list — which is why this needed no migration |
| `created_by` | TEXT | Discord user ID of the organiser |
| `created_by_name` | TEXT | Display name at creation time |
| `reminder_minutes` | INTEGER | Default 30; NULL means no reminder |
| `reminder_fired` | INTEGER | 0/1 — reset to 0 when `event_time` changes |
| `status` | TEXT | `scheduled` / `cancelled` / `completed` |
| `recurrence` | TEXT | NULL, or `daily` / `weekly` / `biweekly` / `monthly` / `monthly_nth` / `monthly_last` / `weekly_not_last` |
| `recurrence_until` | TIMESTAMP | Optional end of the series (naive UTC) |
| `recurrence_anchor` | TIMESTAMP | The **first** occurrence's start, carried unchanged down the series |
| `created_at` / `updated_at` | TIMESTAMP | |

Index `idx_events_guild_status` on `(guild_id, status, event_time)` backs the upcoming-events lookup.

### `event_signups`
| Column | Type | Notes |
|---|---|---|
| `id` | SERIAL PK | |
| `event_id` | INTEGER | FK → `events.id` **ON DELETE CASCADE** |
| `member_id` | TEXT | |
| `member_name` | TEXT | Display name at signup time |
| `response` | TEXT | A key from the event's response set — `accepted` / `tentative` / `declined` unless the event defines its own |
| `created_at` / `updated_at` | TIMESTAMP | |

`UNIQUE (event_id, member_id)` — one row per member, changing your answer updates it in place. Withdrawing deletes the row entirely, so "no response" and "declined" stay distinct.

### `event_responses`
Custom sign-up options for one event. **No rows means the event uses `DEFAULT_RESPONSES`** — that fallback is what keeps every event created before this feature working unchanged.

| Column | Type | Notes |
|---|---|---|
| `id` | SERIAL PK | |
| `event_id` | INTEGER | FK → `events.id` **ON DELETE CASCADE** |
| `key` | TEXT | Slug of the label; goes into the button `custom_id` and into `event_signups.response` |
| `label` | TEXT | Button text |
| `emoji` | TEXT | Optional |
| `is_decline` | INTEGER | 1 = "not coming"; excluded from reminders and cancellation DMs |
| `sort_order` | INTEGER | Button and embed-field order |

`UNIQUE (event_id, key)`. The default keys are deliberately `accepted` / `tentative` / `declined` so existing `event_signups` rows keep resolving.

### `embeds`
Rich messages built in the web UI — see [Embeds](#embeds-utilsembedspy--webembedspy).

| Column | Type | Notes |
|---|---|---|
| `id` | SERIAL PK | |
| `guild_id` | TEXT | |
| `name` | TEXT | Internal label; only used to find it again in the list |
| `channel_id` / `message_id` | TEXT | Where it was posted. `message_id` NULL = still a draft |
| `content` | TEXT | Plain text above the embed — the only part where a mention pings |
| `title` / `description` / `url` | TEXT | `url` makes the title a link |
| `color` | TEXT | `#rrggbb`, normalised on save |
| `author_name` / `author_icon_url` | TEXT | |
| `thumbnail_url` / `image_url` | TEXT | |
| `footer_text` / `footer_icon_url` | TEXT | |
| `show_timestamp` | INTEGER | 0/1 — stamps the message with the time it was posted |
| `created_by` / `created_by_name` | TEXT | |
| `created_at` / `updated_at` | TIMESTAMP | |

### `embed_fields`
| Column | Type | Notes |
|---|---|---|
| `id` | SERIAL PK | |
| `embed_id` | INTEGER | FK → `embeds.id` **ON DELETE CASCADE** |
| `name` / `value` | TEXT | Heading and body of one field |
| `inline` | INTEGER | 0/1 — up to three inline fields sit side by side |
| `sort_order` | INTEGER | |

### `log_settings`
One row per guild, written by the web UI. No row, or a NULL `channel_id`, means
nothing is announced — see [Member log](#member-log-cogsmemberlogpy).

| Column | Type | Notes |
|---|---|---|
| `guild_id` | TEXT PK | |
| `channel_id` | TEXT | Where announcements go. NULL = logging off |
| `log_join` / `log_leave` / `log_kick` / `log_ban` / `log_unban` | INTEGER | 0/1 per event type |
| `track_invites` | INTEGER | 0/1 — whether to work out which invite a join used |
| `updated_at` | TIMESTAMP | |

### `invite_labels`
Where each invite link was published, so a join can name the source.

| Column | Type | Notes |
|---|---|---|
| `guild_id` / `code` | TEXT | Composite primary key |
| `label` | TEXT | Free text — *Steam*, *Website*, *Reddit* |
| `updated_at` | TIMESTAMP | |

Rows outlive the invite: a code that has expired keeps its label, because old
join messages still refer to it.

### `voice_sessions`
One row per **counted interval**, not per visit — see [Voice time](#voice-time-cogsvoicelogpy).

| Column | Type | Notes |
|---|---|---|
| `id` | SERIAL PK | |
| `guild_id` / `member_id` / `channel_id` | TEXT | |
| `member_name` / `channel_name` | TEXT | Copied at the time, so a rename or a deleted channel doesn't erase history |
| `started_at` | TIMESTAMP | Naive UTC |
| `heartbeat_at` | TIMESTAMP | Refreshed every 5 min while open; bounds what a crash can lose |
| `ended_at` | TIMESTAMP | NULL = still running |
| `seconds` | INTEGER | Filled when the interval closes |

Indexes on `(guild_id, started_at)` and a partial one on the open rows.

### `voice_settings`
| Column | Type | Notes |
|---|---|---|
| `guild_id` | TEXT PK | |
| `enabled` | INTEGER | 0/1 — **off by default**, nothing is recorded until an admin turns it on |
| `board_enabled` | INTEGER | 0/1 — the self-updating top-10 message |
| `board_channel_id` / `board_message_id` | TEXT | Where the board is and which message it is |
| `board_period` | TEXT | Which window it shows — a key from `PERIODS` |
| `board_hour` | INTEGER | Local hour it refreshes at |
| `board_updated_on` | DATE | The local day it last refreshed — what makes it once-a-day |
| `channel_id` | TEXT | Where finished visits are announced. NULL = statistics only |
| `min_log_minutes` | INTEGER | Visits shorter than this are not announced |
| `count_afk` / `count_solo` | INTEGER | 0/1 — both off by default |
| `excluded_channels` | TEXT | Comma-separated voice channel ids |
| `updated_at` | TIMESTAMP | |

---

## Discord Channels (auto-created)

| Channel | Created by | Purpose |
|---|---|---|
| `#orbat` | `/setup-slots` | Live ORBAT embed with **📋 Request a Slot** button |
| `#slot-approvals` | First slot request | Pending approval embeds with Approve/Deny buttons |
| `#approval-archive` | First approval or denial | Compact record of every actioned request |

---

## Unit Roles & Access Control

**Unit roles** (defined in `cogs/slots.py` as `UNIT_ROLES`):
`2nd USC`, `CNTO`, `PXG`, `TFP`, `SKUA`

**Unit Leader role name:** `Unit Leader` (defined in `cogs/admin.py` as `UNIT_LEADER_ROLE`)

### Access matrix

| Action | Member | Unit Leader | Admin |
|---|---|---|---|
| `/request-slot`, `/cancel-request`, `/change-slot`, `/leave-operation` | ✅ | ✅ | ✅ |
| `/clear-slot` | ❌ | ✅ own unit | ✅ |
| `/assign-slot` | ❌ | ✅ own unit | ✅ |
| Approve / Deny in `#slot-approvals` | ❌ | ✅ own unit | ✅ |
| `/clear-requests`, `/post-orbat`, `/set-event-time`, `/set-timezone`, `/post-event` | ❌ | ❌ | ✅ |
| `/setup-slots`, `/current-operation`, `/sync`, `/restart`, `/debug-slots`, `/archive-old-approvals` | ❌ | ❌ | ✅ |
| `/game-roles`, `/game-role-list` | ✅ | ✅ | ✅ |
| `/game-role-add`, `/game-role-remove`, `/game-role-panel` | ❌ | ❌ | ✅ |
| `/event-list`, RSVP buttons on an event | ✅ | ✅ | ✅ |
| `/event-create` | ❌ | ✅ | ✅ |
| `/event-edit`, `/event-cancel`, `/event-delete` | ❌ | ✅ own events | ✅ |

**Admin** = `manage_guild` or `administrator` Discord permission.
**Unit gating:** `_can_action_request()` in `slots.py` — admins bypass all unit checks; Unit Leaders must share the requester's unit role; requests with no unit role can be actioned by anyone.

**Unit roles and `Unit Leader` can never become game roles** — `PROTECTED_ROLE_NAMES` in `cogs/gameroles.py` blocks it, so members can't self-assign their way into approval rights.

---

## Game Roles (`cogs/gameroles.py`)

Self-assignable cosmetic roles for games (Minecraft, DCS, …), separate from the slot system. Members opt in themselves; the roles exist purely so people can @mention everyone who plays a given game.

### Guarantee: no permissions

Roles the bot creates are created with `discord.Permissions.none()` and `mentionable=True`. Registering a pre-existing role runs `_role_rejection()`, which refuses the role if it:
- grants any permission at all (`role.permissions.value != 0`)
- is `@everyone`, or is `managed` (bot/integration/booster role)
- is a unit role or `Unit Leader` (`PROTECTED_ROLE_NAMES`)
- sits at or above the bot's top role, which Discord won't let the bot assign

### Commands

**`/game-role-add <name> [emoji] [description]`** (Admin)
Creates a permission-free, mentionable role named `name` and registers it. If a role with that **exact name** already exists it is reused instead of duplicated, after passing `_role_rejection()`. Re-running with the same name updates the emoji/description in place. Capped at 25 roles per guild (Discord's select-menu limit). Refreshes the panel.

**`/game-role-remove <role> [delete_role]`** (Admin)
Unregisters the role. By default the Discord role survives and members keep it; `delete_role: True` deletes it outright. Refreshes the panel.

**`/game-role-list`** (everyone)
Ephemeral list of every registered game role.

**`/game-role-panel [channel]`** (Admin)
Posts the persistent panel: an embed listing the roles plus a **🎮 Choose your game roles** button. One panel per guild (`game_role_panels`, keyed on `guild_id`); it self-updates whenever a role is added or removed via `_update_game_role_panel()`.

**`/game-roles`** (everyone)
Opens the picker directly — same flow as the panel button.

### Self-assign flow

The panel button and `/game-roles` both call `_send_role_picker()`, which sends an ephemeral `GameRoleSelectView`: one multi-select listing every game role, with the member's current roles **pre-ticked** (`SelectOption.default`) and `min_values=0` so deselecting everything is a valid submission.

On submit the member's game roles are set to exactly what's ticked — the view diffs the selection against their current roles and issues only the needed `add_roles` / `remove_roles` calls, then reports what changed. Selecting no change makes no API calls at all.

**Removal has two paths.** Unticking an option in that select already removes the role, but deselecting a pre-ticked option is easy to miss in Discord's UI, so the picker also carries a **➖ Remove a role** button — added only when the member holds at least one game role. It swaps in `GameRoleRemoveView`: a select listing *only* the roles they currently hold, `min_values=1`, nothing pre-ticked, so selecting is unambiguously "remove this". Both views share `_apply_role_changes()` (add/remove plus Forbidden/HTTPException handling) and `_change_summary()`.

Both paths re-check `interaction.user.roles` at submit time, so a role removed by someone else in the meantime results in a no-op with an explanation rather than a failed API call.

### Notes for future changes

- **Persistence:** `GameRolePanelView` has one static `custom_id` (`game_roles_open`) and is registered once in `setup_hook()`, so the panel button keeps working after a restart with no per-guild bookkeeping. This is why the panel is a button opening an ephemeral picker rather than a select menu directly on the public message — a shared message can't pre-tick per-viewer state.
- **No new intents.** `interaction.user.roles` comes from the interaction payload and role add/remove are REST calls, so `Intents.default()` still suffices. Member *counts* are deliberately not displayed anywhere: without the privileged members intent `role.members` is unreliable.
- **Emoji validation:** `_is_renderable_emoji()` guards both input and render. `PartialEmoji.from_str()` silently accepts plain text like `"minecraft"`, and Discord then rejects the whole component — which would break the picker for everyone — so anything containing ASCII letters is rejected on input and skipped at render time.
- **`guild_only` is applied per command, not on the cog class.** A class-level `@app_commands.guild_only()` is only honoured on `commands.GroupCog`; on a plain `Cog` it is silently ignored (see `ext/commands/cog.py`, the `__cog_is_app_commands_group__` branch).
- Registrations whose Discord role was deleted are pruned from the DB by `_resolve_game_roles()` on next read.

---

## All Slash Commands

### Member commands (`cogs/slots.py`)

**`/request-slot`**
Opens a two-step ephemeral squad → slot picker. Validates no existing active request. Submits request to DB, posts embed to `#slot-approvals`, DMs the member.

**`/cancel-request`**
Cancels the member's pending request. Voids the approval message (grey embed, buttons removed via `_void_approval_message()`).

**`/change-slot`**
Cancels current slot (pending or approved — if approved, clears the sheet cell) then opens the squad → slot picker for a new selection.

**`/leave-operation`**
Shows a confirmation button. On confirm: cancels the request, clears sheet if approved, DMs the member.

**`/post-orbat [channel]`** (Admin — `default_permissions(manage_guild=True)`)
Posts a fresh ORBAT embed to the specified channel (defaults to current). Saves
message ID to DB. **It lives in `slots.py`, not `admin.py`**, next to
`_build_orbat_embed()` and `OrbatRequestButton` — the only admin command that does.

### Admin/Unit Leader commands (`cogs/admin.py`)

**`/setup-slots <sheet_url> [event_time] [reminder_minutes]`**
- Deactivates previous operation (`is_active = 0`)
- Creates new operation record
- Parses event time in the guild's configured timezone → stores as naive UTC
- Auto-posts live ORBAT embed to `#orbat`
- Reminder options: 15 / 30 / 60 minutes

**`/assign-slot @member`**
Direct assignment — bypasses approval flow. Writes to sheet immediately. Blocked if member already holds a slot.

**`/clear-slot`**
Dropdown of active (pending + approved) slots. On select: clears sheet cell (approved only), cancels DB record, DMs member. Unit Leaders scoped to own unit.

**`/clear-requests`**
Cancels all pending requests for the active operation.

**`/set-timezone <tz>`**
Stores IANA timezone in `guild_settings`. Used when parsing all event time inputs.

**`/set-event-time <time> [reminder_minutes]`**
Updates `event_time` and `reminder_minutes` on the active operation, resets `reminder_fired = 0`, refreshes ORBAT.

**`/post-event [channel] [mission_name] [event_time]`**
Posts a formatted event announcement embed. Defaults to active operation name and event time. "Sign up" field links to `#orbat` channel mention. Footer shows who posted it.

**`/archive-old-approvals`**
One-time migration. Scans up to 500 messages in `#slot-approvals` for old bot-posted embeds that were actioned before the delete-and-archive flow existed. Detects:
- Green embed + "Approved" field → approved
- Red or dark-gray embed + "Denied" field → denied
Copies each to `#approval-archive`, deletes from `#slot-approvals`.

**`/current-operation`**
Shows active operation name and sheet link.

**`/debug-slots [squad]`**
Shows raw slot data from the sheet as the bot sees it. Useful for diagnosing missing slots.

**`/sync`**
Force-syncs slash commands with Discord. Also repairs stale `sheet_col` values on pending requests and refreshes the ORBAT.

**`/restart`**
Restarts the bot. Two paths, in order:
1. **Railway GraphQL API** — used when `RAILWAY_API_TOKEN` is set. `_railway_restart()` prefers `RAILWAY_DEPLOYMENT_ID` so the bot restarts *itself* rather than whatever a list query happens to return, falling back to the service's latest `SUCCESS` deployment. Returns the deployment id, which the reply shows truncated.
2. **Process exit** — the fallback, also used when the API call raises. `os._exit(1)` after a 1 s pause (so Discord can deliver the ephemeral reply) trips Railway's `ON_FAILURE` restart policy.

Nothing is lost either way: state lives in PostgreSQL and every view is re-registered by `setup_hook()`. Every invocation is printed with the requesting user and guild.

---

## Approval & Denial Flow

### Approval
1. Member submits → `requests` row created with `status = pending`
2. Embed posted to `#slot-approvals` — description: `**Op Name**  ·  @UnitRole\n@Member → **Slot**`. Footer: `Request ID: {id}`. Unit role is a Discord role mention (pings Unit Leaders).
3. Approver clicks **✅ Approve**:
   - `_can_action_request()` checks unit gating
   - DB updated to `approved`
   - Google Sheet written via `sheets.assign_slot()`
   - If sheet write fails → DB rolled back to `denied`, error shown
   - Approval message deleted from `#slot-approvals`
   - Compact green embed posted to `#approval-archive`
   - Member DMed
   - Competing requests for same slot auto-denied (their messages edited grey in `#slot-approvals`, competitors DMed)
   - ORBAT refreshed (fire-and-forget)

### Denial
1. Approver clicks **❌ Deny** → `DenialModal` shown (optional reason, max 200 chars)
2. On submit:
   - DB updated to `denied`
   - Message deleted from `#slot-approvals`
   - Compact red embed posted to `#approval-archive` (includes reason)
   - Member DMed
   - ORBAT refreshed

### Cancellation
`_void_approval_message()` — edits the approval message to grey with "📋 Slot Request — Cancelled" title, removes buttons. Does not delete.

### Persistence after restart
`bot.py` `setup_hook()` re-registers `ApprovalView` for every `pending` request and `OrbatRequestButton` as a global persistent view. custom_ids: `orbat_approve:{id}`, `orbat_deny:{id}`, `orbat_request_slot`.

---

## Google Sheets Integration (`utils/sheets.py`)

**One format is implemented: the ORBAT-style sheet**, where slots are cell values
rather than rows under column headers. There is no header detection anywhere in
this module — a sheet without `<Insert Name>` markers makes `load_slots()` raise
"No available slots found", whatever its columns are called.

- Only `sheet1` is read; the operation name is the **spreadsheet title**
- A **slot entry** is a cell starting with `1.` or `1-` (`_SLOT_PREFIX`; a digit
  after the hyphen means `1-1 Rangers`, a squad id, not a slot)
- A **squad header** is the nearest non-slot cell above in the same column
  (`_is_squad_header()` rejects radio frequencies, `Slots:`-style headings,
  sentences and anything under 3 characters)
- **Available:** the assignment cell contains `<Insert Name>`
- **Filled:** `[TAG] Name`, `[] Name`, `Role — Name`, or a plain name in a cell
  to the right
- **On assign:** `<Insert Name>` → the member's name, `[]` → `[UNIT_TAG]`, cell bolded
- **On clear:** restored to `[] <Insert Name>` (strips the unit tag, unbolds)

**Split cells:** when a slot entry is found, up to 4 columns to the right in the
same row are searched for the assignment cell, stopping at the next slot entry so
one squad's column can't steal the neighbouring one's cell.

`load_slots()` returns available slots only. `load_all_slots()` returns everything
plus `assigned_to` and `col_idx` (used for the ORBAT display and its two-column
layout). Both run in a thread executor — gspread blocks.

**`squad_col` / `role_col` / `status_col` / `assigned_col` on `operations` are
always NULL.** `load_slots()` returns them as `None` because per-cell updates make
them meaningless here; they are the vestige of a tabular reader that no longer
exists. Nothing reads them back. Re-add header detection before giving them
meaning again.

---

## ORBAT Embed

Built by `_build_orbat_embed()` in `slots.py`:
- Title: `🗺️ ORBAT — {operation_name}`
- Description: open / pending / filled counts + optional event timestamp
- Two-column layout (left/right squads by sheet column position) with spacer fields
- Slot indicators: 🟢 open, 🟡 pending, 🔴 filled
- Max 25 embed fields (Discord limit); capped at 8 rows in two-column layout
- Updated by `_update_orbat()` — fetches stored message ID, re-reads sheet, edits message

**A squad named `Reservists` is displayed but left out of every count.** The
header line counts the slots people are expected to fill, and a reserve bench
would otherwise make the operation look permanently under-strength. The match is
case-insensitive on the exact squad name.

---

## Event Reminders

`bot.py` runs `reminder_task` every 60 seconds. Fires when:
`event_time - reminder_minutes <= NOW < event_time` and `reminder_fired = 0`

On fire: sets `reminder_fired = 1`, DMs all approved members, posts mention in `#orbat`.

---

## Deployment

### Railway (production)
- Bot service: `python -u bot.py` (`railway.json` `startCommand`; the `Procfile`
  declares the same as a `worker` process)
- PostgreSQL service: `DATABASE_URL` injected automatically
- Auto-deploys on push to `main` — the single deployment branch. There is no
  `master`; an earlier one was folded into `main` and deleted, so never
  recreate it or target a PR at it.

### Docker (self-hosted)
- `docker-compose.yml`: bot + postgres:16-alpine containers
- Named volume `postgres_data` persists DB across restarts
- `.env` file: `DISCORD_TOKEN`, `GOOGLE_CREDENTIALS`, `DB_PASSWORD`
- **Every optional variable has to be listed in the compose `environment:` block
  to reach the container.** A variable that only exists in `.env` is read by
  compose for interpolation, not passed through — so anything added to
  `.env.example` needs a line here too, or it silently does nothing under Docker.

### Startup sequence
1. `init_db()`, then `close_dangling_voice_sessions()` — schema first, then voice
   intervals left open by a crash are closed at their last heartbeat before
   anything new is recorded
2. Load `cogs.slots`, `cogs.admin`, `cogs.gameroles`, `cogs.events`, `cogs.voicelog`
   and `cogs.memberlog`. Each is wrapped in its own `try`, so one cog failing to
   import doesn't take the others down
3. Re-register persistent views:
   - `OrbatRequestButton` and `GameRolePanelView` — one global instance each
   - one `ApprovalView` per `pending` request
   - one `EventRsvpView` per event from `get_live_events()`, each built with **that event's own responses** via `load_responses()` — a restored view carrying the default keys wouldn't match the message's `custom_id`s
4. Start `reminder_task` (operations). `EventsCog.event_task` starts separately, from the cog's `__init__`
5. `_start_web()` — starts the web UI when it is configured. Every failure here is non-fatal and only printed: missing dependencies, missing variables, `WEB_ENABLED=0` or a crash in `WebServer.start()` all leave the bot running normally. `close()` stops the server before the Discord connection goes down
6. `on_ready`: `copy_global_to` + guild sync for each guild (instant, vs up-to-1-hour global sync)
7. `on_guild_join`: sync immediately when bot joins a new server

---

## Events (`cogs/events.py`)

Apollo-style standalone events. **Deliberately independent of the slot/ORBAT system** — no Google Sheet, no `operations` row, no `requests`. An event is a message with Accept / Tentative / Decline buttons and a live attendee list.

Staged build toward Apollo parity. Done: sign-ups, reminders, editing, cancelling, auto close-out, **recurring events**, **custom responses**. Still to come: **sign-up roles with per-role caps**, then waitlist, templates and a calendar view.

### Commands

**`/event-create <title> <start_time> [description] [duration] [location] [channel] [mention] [reminder] [image_url] [repeat] [repeat_until] [responses]`** (Admin or Unit Leader)
Parses `start_time` with `_parse_event_time()` from `admin.py` in the guild's timezone, rejects times in the past, posts the event and registers its view. If the send fails (`Forbidden`, or `HTTPException` from a bad `image_url`) the row is set to `cancelled` so it can't linger as a phantom event.

**`/event-edit <event> [title] [start_time] [description] [duration] [location] [reminder] [repeat] [repeat_until] [responses] [mention]`** (organiser or admin)
Only the passed fields change — `update_event()` uses `COALESCE`, so omitted fields keep their value. Changing the time resets `reminder_fired` to 0 so the reminder fires again for the new time.

**`/event-cancel <event> [reason] [stop_series]`** (organiser or admin)
Sets status `cancelled`, re-renders the message in red without buttons, and DMs everyone who accepted or was tentative. On a recurring event, `stop_series` defaults to **False** — cancelling one occurrence posts the next one, since "this week is off, next week isn't" is the common case. `stop_series: True` clears the recurrence first, so nothing can spawn a successor afterwards.

**`/event-delete <event>`** (organiser or admin)
Deletes the row and its message for good; sign-ups and custom responses go with it via `ON DELETE CASCADE`. Behind a confirmation button because it can't be undone. Its autocomplete uses `get_guild_events()` — **cancelled and completed events included**, since those are the ones being cleaned up, unlike the other `event` params which only offer scheduled ones.

The confirmation states the sign-up count and, when someone is signed up to a scheduled event, warns they will **not** be told and points at `/event-cancel` instead. That is the split: cancel keeps the record and DMs everyone, delete removes it silently.

**`/event-list`** (everyone) — upcoming events with per-response counts and a jump link.

`event` params use autocomplete over the guild's upcoming events, so users pick a title rather than typing an ID.

### RSVP flow

`EventRsvpView` builds one button per response, with `custom_id` `event_rsvp:{event_id}:{key}`. Pressing **the response you already gave withdraws it** (deletes the signup row) — that toggle is stated in the embed footer, because it isn't otherwise discoverable. Pressing a different one updates in place via the `UNIQUE (event_id, member_id)` upsert.

Every change re-reads the event and refreshes the message through `_refresh_event_message()`, fire-and-forget.

### Ping roles

`mention` on `/event-create` and `/event-edit` is a **string**, not a `discord.Role` — the app-command system has no multi-role option type, and a string field still lets the client turn `@Role` into a proper `<@&id>` token.

`_parse_mention_roles()` collects `<@&id>` tokens, then **strips every `<…>` token** before scanning for bare snowflakes. Without that strip, the digits inside a user mention `<@123>` are matched by the bare-id pattern and get stored as a role, which then renders as `<@&123>` and silently pings nobody. If nothing mention-shaped is found it falls back to comma-separated role *names*.

Capped at `MAX_MENTION_ROLES = 10`. Unresolvable entries are reported rather than dropped silently, and roles that aren't `mentionable` produce a warning, since the bot needs **Mention All Roles** to actually notify through those.

`/event-edit mention:none` clears the pings via `set_event_mentions()` — `update_event()` uses `COALESCE` and so cannot write NULL, the same reason `set_event_recurrence()` exists.

### Custom responses

`responses` on `/event-create` and `/event-edit` takes `✅ Coming | ❓ Maybe | -❌ Can't`. `_parse_responses()` splits on `|`, treats a leading `-` as `is_decline`, and pulls a leading emoji off the front when `_is_emoji()` accepts it — the same ASCII-letter guard as `gameroles._is_renderable_emoji`, since a word Discord rejects as an emoji would break the whole button row.

Keys come from `_response_key()` (slug of the label, deduplicated with a numeric suffix). They land in both the `custom_id` and `event_signups.response`, so they must be stable and unique per event.

**`load_responses(event_id)` is the single entry point** — it returns the stored set or a copy of `DEFAULT_RESPONSES`. Anything that renders buttons, groups sign-ups, or decides who gets a reminder goes through it, including `bot.py`'s view restoration: a restored view has to carry that event's own keys or its `custom_id`s won't match.

`_attending()` replaces the old hard-coded `response in ('accepted', 'tentative')` test — reminders and cancellation DMs now go to everyone whose response is not flagged `is_decline`.

Two limits: at least 2 responses, at most `MAX_RESPONSES = 10` (two button rows), labels under 40 characters. A set where *every* option is a decline is rejected, since nobody could sign up.

**Changing the set on a live event clears sign-ups whose key no longer exists** (`drop_signups_not_in()`), and `/event-edit` reports how many. Leaving them would keep rows that render nowhere and silently skew nothing visible.

A recurring event copies its response set to each new occurrence in `_spawn_next_occurrence()`.

### Recurrence

Stored key → what the user picks:

| Key | Choice | Pattern |
|---|---|---|
| `daily` | Daily | |
| `weekly` | Weekly | |
| `biweekly` | Every 2 weeks | |
| `monthly` | Monthly — same date | The 15th every month, clamped in short months |
| `monthly_last` | Monthly — last weekday | **Last Saturday of the month** |
| `monthly_nth` | Monthly — same weekday | 2nd Saturday of the month |
| `weekly_not_last` | Weekly — except the last one | Every Saturday **except** the last of the month |

`_RECURRENCE_LABELS` holds the mapping and `_REPEAT_CHOICES` the command choices; the choice values are exactly those keys plus `none`.

**The weekday variants take both weekday and position from the anchor** — there is no separate column for them. `monthly_last` uses the anchor's weekday with position `-1`; `monthly_nth` uses `_weekday_position(anchor)`, i.e. `(day - 1) // 7 + 1`. So a series created on Saturday 13 June means "2nd Saturday" under `monthly_nth`, and "last Saturday" under `monthly_last` regardless of where the anchor sits — `/event-create` warns when the first date isn't itself the last weekday, so the jump doesn't surprise anyone a month later.

`_weekday_day()` returns None when a month has no such day — there is no 5th Saturday in June. That is why `_next_occurrence()` **skips** a None candidate and keeps walking instead of returning None: a 5th-Saturday series must jump over months that lack one rather than end. The only thing that ends the walk is an invalid recurrence key, which is checked before the loop.

**`weekly_not_last` is the one exclusion pattern, and it reuses that same skip.** `_nth_occurrence()` advances weekly and returns None whenever the candidate is the last weekday of its month, so the walk steps over it. No extra machinery: adding it was three lines because the skip already existed for the 5th-Saturday case.

It is exactly complementary to `monthly_last` on the same weekday — over an 81-Saturday window the two sets never collide and their union is every Saturday. That is the intended pairing: a monthly op on the last Saturday plus weekly ops on the others.

`_DAY_NAMES` is a fixed English tuple rather than `calendar.day_name`, which follows the process locale and would leak into user-facing text.

`_recurrence_text(event)` renders the human description ("Monthly · last Saturday of the month") and is used by the embed, `/event-list` and the create/edit replies. It accepts any mapping with `recurrence`, `recurrence_anchor` and `event_time`, so callers can pass a plain dict before a row exists.

`repeat` on `/event-create` and `/event-edit` sets `recurrence`; `repeat_until` bounds the series. `/event-edit repeat:none` stops it — that path goes through `set_event_recurrence()` rather than `update_event()`, because `update_event()` uses `COALESCE` and therefore cannot write NULL.

**One live occurrence at a time.** There is no pre-generated calendar of rows. When `event_task` finishes an occurrence it calls `_spawn_next_occurrence()`, which creates and posts the next one and registers its view. Sign-ups deliberately do **not** carry over — each occurrence is answered fresh.

**`recurrence_anchor` is why monthly repeats don't drift.** `_next_occurrence()` always measures from the anchor via `_nth_occurrence(anchor, recurrence, n)`, never from the previous occurrence. Measuring from the previous one would turn a 31 January series into 28 Feb → 28 Mar, permanently losing the 31st; anchoring gives 28 Feb → 31 Mar. `_add_months()` clamps to the last valid day of the target month.

The same property fixes catch-up: `_next_occurrence()` walks `n` upward until the candidate is past the cutoff, so a bot that was offline for ten weeks posts **one** occurrence in the future rather than one per missed week. The walk is bounded at 4000 iterations.

If the next occurrence can't be posted (channel gone, `Forbidden`), the freshly created row is set to `cancelled` so the series stops cleanly instead of retrying every minute.

### Background loop

`EventsCog.event_task` runs every 60 s (separate from `bot.py`'s `reminder_task`, which stays operation-only):

1. **Reminders** — `reminder_fired` is set *before* sending, so a failure can't cause a retry storm. DMs go to everyone `_attending()` returns, i.e. every response not flagged `is_decline`; the channel ping adds `mention_role_id` if set.
2. **Finishing** — events past `event_time + duration` flip to `completed` and their message is re-rendered grey with buttons removed.
3. **Handover** — a completed event with a `recurrence` spawns its next occurrence. Because the source is already `completed` by then, it is out of `get_finished_events()` and cannot spawn twice.

`cog_unload()` cancels the loop; verified not to leak past unload.

**Both stages catch per-item.** `discord.ext.tasks` stops a loop permanently on an unhandled exception — it logs and never runs again — so one transient failure (a database blip during a redeploy, say) would silently kill every reminder and close-out until the next restart. Each item is therefore wrapped individually, and the two fetch queries separately, so a single bad event can't take the tick down. `bot.py`'s `reminder_task` does the same, which is why its body lives in `_send_operation_reminder()`. **Anything added to either loop needs the same treatment.**

### Notes for future changes

- **Time comparisons.** `event_time` is naive UTC. The event queries compare against `NOW() AT TIME ZONE 'UTC'`, not `CURRENT_TIMESTAMP` — the latter is a `timestamptz` and would be cast using the *session* timezone, which is only correct while the server runs in UTC. The older `operations` queries still use `CURRENT_TIMESTAMP`; prefer the new form for anything added.
- **Per-user timezones come free for display.** All times render as Discord timestamps (`<t:…:F>`), which every client localises automatically. Only *input* is guild-timezone based, so a per-user input timezone is the only part still missing.
- **`publish_event(bot, channel, event_id)` is the only place an event reaches Discord.** The slash command, the recurrence hand-over and the web UI all call it, so all three produce an identical message and all three register the persistent view. It deliberately does not catch `Forbidden` / `HTTPException` — each caller discards the event differently (the command explains it, the hand-over stops the series, the web form shows it on the page).
- **Persistence** mirrors `ApprovalView`: `setup_hook()` calls `get_live_events()` and re-adds one `EventRsvpView` per open event. Events without a `message_id` are skipped.
- `_format_attendees()` trims mention lists to Discord's 1024-char field limit and appends "…and N more", while the field *name* keeps the true total.
- `_group_signups()` ignores unrecognised `response` values rather than raising, so a stale key can't break an embed.

---

## Web UI (`web/`)

An optional browser interface for **events**. It is off until configured: `bot.py`'s
`_start_web()` prints which of `DISCORD_CLIENT_ID` / `DISCORD_CLIENT_SECRET` /
`WEB_SECRET_KEY` is missing and opens no listener. Missing dependencies,
`WEB_ENABLED=0` and a crash in `WebServer.start()` are all handled the same way —
**the bot's own job must never depend on the website**.

### It runs inside the bot process

`web/server.py` drives uvicorn programmatically on the bot's own event loop, so a
request handler can call `channel.send()`, `bot.add_view()` and
`guild.fetch_member()` directly. That is the whole reason the earlier plan's
outbox/polling bridge isn't here: there is no second process to bridge to, one
Railway service still runs everything, and a web-created event is posted and
persistent-view-registered before the response is written.

Two consequences to keep in mind:

- `_BotOwnedServer` overrides `install_signal_handlers()` to a no-op. uvicorn
  installs SIGINT/SIGTERM handlers in `serve()`, which would take the process
  down out from under discord.py.
- `ORBATBot.close()` stops the server first. A slow HTTP handler must not
  outlive the Discord connection it is calling into.

### Auth and permissions

OAuth2 with the **`identify` scope only**. The access token is used once to read
the profile and then discarded — guild membership and roles come from the bot's
own connection instead, so the site can't be tricked by a stale token and needs
no `guilds.members.read` consent.

**The session is a signed cookie** (`itsdangerous`), holding only user id, display
name, avatar hash and a CSRF token. No session table, and a redeploy signs nobody
out. Rotating `WEB_SECRET_KEY` invalidates every session, OAuth state and flash
message at once.

Every permission decision is re-made per request from a live `discord.Member`:

| Action | Who | Same check as |
|---|---|---|
| View events, RSVP, pick own game roles | any member of the guild | — |
| Create an event | Unit Leader or admin | `_is_unit_leader_or_admin()` |
| Edit / cancel / delete | organiser or admin | `_is_organiser()` |
| Add / remove game roles, post the panel | admin | `default_permissions(manage_guild=True)` on the cog's commands |
| Build and post embeds, configure the member log and voice tracking | admin | `is_admin()` — `manage_guild` or `administrator` |
| See the voice leaderboard | any member of the guild | — |

Those are the cog's own functions, imported by `web/guilds.py` — the web UI and
the slash commands cannot drift apart on access control.

`resolve_member()` caches for 60 s because `Intents.default()` has no members
intent, so `guild.get_member()` is usually empty and each check would otherwise
cost a REST call. `POST /g/{id}/refresh` calls `forget_member()` so someone who
was just given a role doesn't have to wait it out.

### CSRF and redirects

Cookies are `SameSite=Lax`, and every POST additionally carries the session's CSRF
token as a hidden field, checked by `auth.check_csrf()`. The OAuth `state` is
signed *and* mirrored into a short-lived cookie, so a callback this site didn't
start is rejected. `safe_next()` only ever redirects to a path on this origin.

### Routes

```
GET  /                                  login page, or the guild picker
GET  /login  ·  GET /auth/callback  ·  POST /logout
GET  /g/{guild}                         upcoming + recently closed events
GET  /g/{guild}/events/new              create form      POST to create
GET  /g/{guild}/events/{id}             detail + RSVP
GET  /g/{guild}/events/{id}/edit        edit form        POST to save
POST /g/{guild}/events/{id}/cancel      reason, optional stop_series
GET  /g/{guild}/events/{id}/delete      confirmation     POST to delete
POST /g/{guild}/events/{id}/rsvp        toggle, exactly like the buttons
GET  /g/{guild}/roles                   game roles: pick your own, admins manage
POST /g/{guild}/roles                   set your game roles to what is ticked
POST /g/{guild}/roles/add               admin — register/create a game role
POST /g/{guild}/roles/remove            admin — unregister, optionally delete
POST /g/{guild}/roles/panel             admin — post the self-assign panel
GET  /g/{guild}/embeds                  admin — saved embeds
GET  /g/{guild}/embeds/new              builder          POST to save a draft
GET  /g/{guild}/embeds/{id}             preview, send, delete
GET  /g/{guild}/embeds/{id}/edit        builder          POST to save
POST /g/{guild}/embeds/{id}/send        post as a new message
POST /g/{guild}/embeds/{id}/delete      optionally deletes the Discord message
GET  /g/{guild}/logs                    admin — member log settings, POST to save
GET  /g/{guild}/voice                   voice leaderboard; admins also get the settings
POST /g/{guild}/voice                   admin — save the voice settings
POST /g/{guild}/voice/post              admin — post the top 10 into a channel
POST /g/{guild}/logs/invites            admin — label the invite links
POST /g/{guild}/refresh                 drop the cached member
GET  /healthz                           'ok' once the bot is ready
```

### Where the logic lives

`web/service.py` owns no rules of its own — it translates form fields and calls
into `cogs/events.py` (`publish_event`, `_parse_responses`, `_refresh_event_message`,
`_spawn_next_occurrence`, `_attending`, the recurrence helpers) and
`utils/database.py`. A `ValueError` raised in there is a message for the user and
is rendered back on the form. **Anything added to the event model belongs in the
cog, not here.**

Two behaviours worth knowing before changing that file:

- **The start time is only passed to `update_event()` when it actually moved.**
  That call re-arms `reminder_fired` whenever it is given a time, and an
  unchanged form submission must not make a reminder fire twice.
- **Emptied fields are cleared through `database.clear_event_fields()`.**
  `update_event()` uses `COALESCE` and so can only ever set a value — the same
  reason `set_event_mentions()` and `set_event_recurrence()` exist. The
  clearable columns are whitelisted in `_CLEARABLE_EVENT_FIELDS`.

`web/roles.py` does the same for game roles, on top of `cogs/gameroles.py`
(`_resolve_game_roles`, `_role_rejection`, `_parse_emoji`, `_apply_role_changes`,
`_update_game_role_panel`, `_build_panel_embed`). Three things there are worth
knowing:

- **`_apply_role_changes()` takes a `discord.Member`, not an `Interaction`** —
  that is why the web can reuse it. Changing it back would fork the one place
  that hands out a game role.
- **Submitted role ids are intersected with the registered game roles** before
  anything is added or removed, so a hand-edited form can't grant an arbitrary
  role. This is the security boundary of that page.
- **`forget_member()` runs after a self-assign**, otherwise the 60 s member cache
  would render the page from the roles the member held *before* the change.

`_plain()` strips `**` and backticks off the cog's messages: they are written for
a Discord message and would otherwise show their markdown literally on a page.

Times are entered and shown in the **guild timezone**: `web/helpers.py` parses
`<input type="datetime-local">` through the cog's own `_parse_event_time()`, so
"local time" means the same thing on the web as in a slash command. The Discord
messages keep using `<t:…>` timestamps and localise themselves.

### Front end

Server-rendered Jinja2 plus one hand-written stylesheet. No build step, no CDN,
no JavaScript — the page has to work from a fresh container with nothing but the
bot's own dependencies installed.

**Branding is data, not markup.** The name comes from `config.brand` (`WEB_BRAND`,
default `TFP BOT`) and the logo from `_logo_url()`, which looks for
`web/static/logo.*` at startup and returns `''` when there is none — every
template guards on that, so a deployment without a logo file renders the name
alone instead of a broken image. The URL carries the file's mtime so a replaced
logo isn't served from a browser cache. Both are Jinja globals; they don't vary
per request.

### Deliberately not covered yet

- **Slots and the ORBAT board** — still Discord- and Sheets-only. This is the
  natural next step: `squads` / `slots` tables, a visual ORBAT, and slot requests
  that post to `#slot-approvals`.
- **Approving slot requests** — still Discord-side.
- **Moving an event to another channel** — the message would have to be deleted
  and reposted, losing the sign-up history's continuity; cancel and recreate.
- **Per-user input timezones** — display is already per-user via Discord
  timestamps, but everything typed in is guild-timezone based.

### Slots on the web — the shape it was planned in

Kept from the original plan, still the intended direction:

- New table `squads (id, operation_id, name, sort_order)`
- New table `slots (id, squad_id, role_name, sort_order, assigned_request_id)`
- Make the Google Sheets columns on `operations` optional rather than required,
  so sheet-backed and DB-backed operations can coexist during the migration
- Read-only visual ORBAT first, then click-a-slot-to-request (posting to
  `#slot-approvals` as today), then approve/deny from the web, then an ORBAT
  builder and an operation archive
- Live updates were sketched as Server-Sent Events; running in-process makes that
  straightforward, since the request handler already sees every change

---

## Embeds (`utils/embeds.py` + `web/embeds.py`)

Rich messages built in the browser — the server-info and rules posts that would
otherwise be written by hand. There are no slash commands for these: composing an
embed in a Discord modal is unpleasant, and the web form already exists.

**A stored embed is a description, not a message.** `embeds` holds the columns and
`embed_fields` the fields; `build_embed()` turns a row into a `discord.Embed`.
`message_id` is NULL until it is posted, which is the whole point of the split:

- `post()` sends a new message and records where it landed.
- `edit_posted()` updates that message. Saving an edit in the web UI calls it, so
  a pinned server-info post is updated **in place** instead of being reposted.
- A message deleted in Discord makes `edit_posted()` clear `message_id`, turning
  the embed back into a draft rather than failing the same way forever.

**Discord's limits are enforced on save, not on send** (`validate()`). A rejected
send would otherwise leave a stored embed that can never be posted, and the person
who wrote it is no longer looking at the form by then. The same function refuses an
embed with no title, description, image or fields, which Discord rejects outright.

`clean_color()` normalises to `#rrggbb` on input; `parse_color()` is deliberately
forgiving on read, since a stored value can only have come from `clean_color()`.

The builder offers `MAX_FIELDS = 10` fixed field slots. Discord allows 25 — the
limit here is the form, which has no JavaScript to add rows with.

---

## Member log (`cogs/memberlog.py`)

Announces joins, leaves, kicks, bans and unbans in a channel chosen per guild
(`log_settings`). Nothing is posted until a channel is set; each event type has its
own flag.

### The intent is opt-in, and that is deliberate

`on_member_join` and `on_member_remove` are **privileged**. Requesting an intent
that isn't ticked in the Developer Portal makes `bot.start()` raise
`PrivilegedIntentsRequired` — the bot would not come up at all. So `bot.py` only
sets `intents.members` when `MEMBER_EVENTS` is truthy, and the cog loads either
way: bans and unbans are not privileged and work without it.

**Never turn the intent on unconditionally.** A deploy that lands before the portal
switch is flipped would take the whole bot down, slots and all.

### Telling a kick from a leave

`on_member_remove` fires for a voluntary leave, a kick and a ban alike. The audit
log is the only way to tell them apart:

- The listener waits `AUDIT_DELAY` seconds first, because the audit entry appears a
  moment after the event.
- A **ban** entry means `on_member_ban` will report it, so this path stays silent —
  otherwise every ban is logged twice. Checking the audit log for both actions makes
  it independent of which gateway event arrives first.
- `AUDIT_WINDOW` guards against an old kick of the same user being mistaken for
  this leave. Anything older is treated as unrelated.

Without **View Audit Log** every kick reads as a plain leave and bans have no
moderator — deliberately not an error, just less detail.

### Invite attribution

`_invites` caches each guild's invite use counts; a join re-reads them and the
code whose counter went up is the one that was used. `_invite_lock` serialises
that, or two joins seconds apart would each credit the other's increment. Two
joins in the *same instant* still can't be told apart — the counter is corrected
either way, only that one attribution may be wrong.

Reading invites needs **Manage Server**. Without it, and for members added by
another bot, no link is shown. A guild with `VANITY_URL` falls back to
`vanity_invite()` when no counter moved — `_used_invite()` returns
`(code, inviter, kind)` so the vanity case can be labelled as such rather than
looked up like an ordinary code.

**`invite_labels` is what makes the code useful.** The join message shows the
label next to the code, which is the whole point: the alternative is keeping a
spreadsheet of which link was posted where and consulting it by hand.

Every listener body is wrapped in a `try`/`except` that prints and moves on: a
failure to *log* an event must never propagate into the gateway handler.

---

## Voice time (`cogs/voicelog.py`)

Records how long members spend in voice channels. **Off per guild until an admin
switches it on** (`voice_settings.enabled`), and no privileged intent or extra
permission is involved — `Intents.default()` already carries voice states.

### A visit is not a session

A *visit* is one stay in one channel, held in memory. It is split into **counted
intervals**, and only intervals become rows:

- Counting **pauses** when the rules stop applying — the member is left alone
  (unless `count_solo`), the channel is the AFK channel (unless `count_afk`), or
  the channel is excluded.
- It **resumes** when they apply again, as a new row.

So `SUM(seconds)` is time that actually counted, not time connected. `_sync_channel()`
is what enforces this: after every voice event it re-evaluates *everyone* in the
affected channels, because one person arriving or leaving changes whether everybody
else is "alone".

**Mute, deafen and stream toggles fire `on_voice_state_update` with the channel
unchanged** and are ignored — following them would shred every visit into fragments.

### Restarts and crashes

Two mechanisms, and both matter:

- `ORBATBot.close()` calls `flush_open_sessions()` **before** the Discord
  connection goes down, so a redeploy — the common case — records everything.
- Open rows carry `heartbeat_at`, refreshed every `HEARTBEAT_SECONDS`. On the next
  start, `close_dangling_voice_sessions()` closes anything still open **at its last
  heartbeat**, so a hard crash costs at most one heartbeat. An interval with no
  heartbeat counts zero — the time is never rounded up.

`on_ready` re-scans every voice channel and picks up whoever is already in one. It
runs on every reconnect, so it is written to be idempotent.

### Notes for future changes

- **The settings are cached for `SETTINGS_TTL` seconds**, because they are read on
  every voice event. `forget_settings()` drops that, and the web route calls it
  after a save so a change applies immediately.
- **`_lock` serialises the whole event path.** Two people leaving a channel at once
  would otherwise both compute "is anyone still here" from the same stale view.
- The leaderboard query counts open intervals too, via
  `COALESCE(heartbeat_at, started_at)`, so somebody currently in voice appears
  within one heartbeat rather than only after they leave.
- **`build_leaderboard_embed()` and the `PERIODS` helpers live in the cog**, not in
  `web/`: the daily board needs them too, and `web/` may depend on `cogs/` but not
  the other way round. `web/voice.py` re-exports them, which is what its `__all__`
  is for.
- **The daily board is driven by "has today's refresh happened", not by firing at
  a minute.** `board_updated_on` holds the local day it last ran, so a restart or
  an outage across the chosen hour still produces one refresh — late, but never
  skipped and never doubled. `daily_board` therefore only has to run often enough
  to bound the lateness (`BOARD_CHECK_MINUTES`).
- **The board message is edited, not reposted**, so it keeps its place and can be
  pinned. A `NotFound` clears `board_message_id` and the next refresh posts a
  fresh one.
  It names members instead of mentioning them — a leaderboard that pings ten
  people every time it is posted would be worse than useless.
- `member_name` and `channel_name` are stored per row on purpose: a leaderboard
  should not need a REST call per row, and a deleted channel should still show up
  in the history under its old name.

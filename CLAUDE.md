# CLAUDE.md — ORBATBot / orbat-platform

This file gives full context on the project. Read it before making any changes.

---

## What This Is

A Discord bot for managing Arma 3 operation slot requests across multiple military simulation units. Members request slots; Unit Leaders and admins approve or deny via Discord buttons. The Google Sheet is updated automatically on approval.

**Current state:** Fully operational bot deployed on Railway.
**Next phase:** Web app (see bottom of this file) — Discord OAuth2 login, visual ORBAT, slot requests from the browser, no Google Sheets dependency.

---

## Repository Structure

```
bot.py                  # Entry point — ORBATBot class, startup, reminder task
cogs/
  slots.py              # All member-facing commands + approval/denial flow + views
  admin.py              # All admin/unit-leader commands
  gameroles.py          # Self-assignable game roles (Minecraft, DCS, …) + panel
  events.py             # Standalone events with RSVP buttons + reminder loop
utils/
  database.py           # All PostgreSQL queries (asyncpg)
  sheets.py             # Google Sheets read/write (gspread)
requirements.txt
Dockerfile
docker-compose.yml      # Bot + PostgreSQL 16
Procfile                # Railway: python bot.py
railway.json
```

---

## Environment Variables

| Variable | Description |
|---|---|
| `DISCORD_TOKEN` | Bot token from Discord Developer Portal |
| `GOOGLE_CREDENTIALS` | Full JSON content of the service account key file |
| `DB_PASSWORD` | PostgreSQL password (docker-compose only) |
| `DATABASE_URL` | Full connection string — injected automatically by Railway; constructed by docker-compose |

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
| `squad_col` | INTEGER | Column index of squad header |
| `role_col` | INTEGER | Column index of role header |
| `status_col` | INTEGER | Column index of status header |
| `assigned_col` | INTEGER | Column index of assigned-to header |
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
Same structure as `orbat_messages`. Tracks a secondary "open slots" message (currently unused in active commands but schema exists).

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
| `mention_role_id` | TEXT | Role pinged on post and on the reminder |
| `created_by` | TEXT | Discord user ID of the organiser |
| `created_by_name` | TEXT | Display name at creation time |
| `reminder_minutes` | INTEGER | Default 30; NULL means no reminder |
| `reminder_fired` | INTEGER | 0/1 — reset to 0 when `event_time` changes |
| `status` | TEXT | `scheduled` / `cancelled` / `completed` |
| `created_at` / `updated_at` | TIMESTAMP | |

Index `idx_events_guild_status` on `(guild_id, status, event_time)` backs the upcoming-events lookup.

### `event_signups`
| Column | Type | Notes |
|---|---|---|
| `id` | SERIAL PK | |
| `event_id` | INTEGER | FK → `events.id` **ON DELETE CASCADE** |
| `member_id` | TEXT | |
| `member_name` | TEXT | Display name at signup time |
| `response` | TEXT | `accepted` / `tentative` / `declined` |
| `created_at` / `updated_at` | TIMESTAMP | |

`UNIQUE (event_id, member_id)` — one row per member, changing your answer updates it in place. Withdrawing deletes the row entirely, so "no response" and "declined" stay distinct.

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
| `/setup-slots`, `/current-operation`, `/sync`, `/debug-slots`, `/archive-old-approvals` | ❌ | ❌ | ✅ |
| `/game-roles`, `/game-role-list` | ✅ | ✅ | ✅ |
| `/game-role-add`, `/game-role-remove`, `/game-role-panel` | ❌ | ❌ | ✅ |
| `/event-list`, RSVP buttons on an event | ✅ | ✅ | ✅ |
| `/event-create` | ❌ | ✅ | ✅ |
| `/event-edit`, `/event-cancel` | ❌ | ✅ own events | ✅ |

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

**`/post-orbat [channel]`**
Posts a fresh ORBAT embed to the specified channel (defaults to current). Saves message ID to DB.

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

Two sheet formats supported:

**Tabular format** — columns with recognisable headers:
- Squad/Unit: header contains squad, unit, element, group, platoon, team, section, callsign
- Role/Position: header contains role, position, slot, job, rank, billet
- Status (optional): row hidden if not Available/Open/Free/blank
- Assigned To (optional): row hidden if has a value

**ORBAT-style format** — slots as cell values:
- Available: cell contains `<Insert Name>`
- Filled: `[TAG] Name` or `[] Name`
- On assign: writes `[UNIT_TAG] MemberName`
- On clear: restores to `[] <Insert Name>` (strips unit tag)

`load_slots()` returns available slots only. `load_all_slots()` returns everything (used for ORBAT display). Both run in a thread executor (blocking gspread calls).

---

## ORBAT Embed

Built by `_build_orbat_embed()` in `slots.py`:
- Title: `🗺️ ORBAT — {operation_name}`
- Description: open / pending / filled counts + optional event timestamp
- Two-column layout (left/right squads by sheet column position) with spacer fields
- Slot indicators: 🟢 open, 🟡 pending, 🔴 filled
- Max 25 embed fields (Discord limit); capped at 8 rows in two-column layout
- Updated by `_update_orbat()` — fetches stored message ID, re-reads sheet, edits message

---

## Event Reminders

`bot.py` runs `reminder_task` every 60 seconds. Fires when:
`event_time - reminder_minutes <= NOW < event_time` and `reminder_fired = 0`

On fire: sets `reminder_fired = 1`, DMs all approved members, posts mention in `#orbat`.

---

## Deployment

### Railway (production)
- Bot service: `python bot.py` (via Procfile)
- PostgreSQL service: `DATABASE_URL` injected automatically
- Auto-deploys on push to `main` — the single deployment branch. There is no
  `master`; an earlier one was folded into `main` and deleted, so never
  recreate it or target a PR at it.

### Docker (self-hosted)
- `docker-compose.yml`: bot + postgres:16 containers
- Named volume `postgres_data` persists DB across restarts
- `.env` file: `DISCORD_TOKEN`, `GOOGLE_CREDENTIALS`, `DB_PASSWORD`

### Startup sequence
1. `init_db()` — creates/migrates schema
2. Load `cogs.slots`, `cogs.admin`, and `cogs.gameroles`
3. Re-register persistent views: `OrbatRequestButton`, `GameRolePanelView`, and one `ApprovalView` per pending request
4. Start `reminder_task`
5. `on_ready`: `copy_global_to` + guild sync for each guild (instant, vs up-to-1-hour global sync)
6. `on_guild_join`: sync immediately when bot joins a new server

---

## Events (`cogs/events.py`)

Apollo-style standalone events. **Deliberately independent of the slot/ORBAT system** — no Google Sheet, no `operations` row, no `requests`. An event is a message with Accept / Tentative / Decline buttons and a live attendee list.

This is stage 1 of a staged build toward Apollo parity. Still to come, in priority order: **sign-up roles with per-role caps**, **recurring events**, then waitlist, templates and a calendar view.

### Commands

**`/event-create <title> <start_time> [description] [duration] [location] [channel] [mention] [reminder] [image_url]`** (Admin or Unit Leader)
Parses `start_time` with `_parse_event_time()` from `admin.py` in the guild's timezone, rejects times in the past, posts the event and registers its view. If the send fails (`Forbidden`, or `HTTPException` from a bad `image_url`) the row is set to `cancelled` so it can't linger as a phantom event.

**`/event-edit <event> [title] [start_time] [description] [duration] [location] [reminder]`** (organiser or admin)
Only the passed fields change — `update_event()` uses `COALESCE`, so omitted fields keep their value. Changing the time resets `reminder_fired` to 0 so the reminder fires again for the new time.

**`/event-cancel <event> [reason]`** (organiser or admin)
Sets status `cancelled`, re-renders the message in red without buttons, and DMs everyone who accepted or was tentative.

**`/event-list`** (everyone) — upcoming events with per-response counts and a jump link.

`event` params use autocomplete over the guild's upcoming events, so users pick a title rather than typing an ID.

### RSVP flow

`EventRsvpView` has three buttons with `custom_id` `event_rsvp:{event_id}:{response}`. Pressing **the response you already gave withdraws it** (deletes the signup row) — that toggle is stated in the embed footer, because it isn't otherwise discoverable. Pressing a different one updates in place via the `UNIQUE (event_id, member_id)` upsert.

Every change re-reads the event and refreshes the message through `_refresh_event_message()`, fire-and-forget.

### Background loop

`EventsCog.event_task` runs every 60 s (separate from `bot.py`'s `reminder_task`, which stays operation-only):

1. **Reminders** — `reminder_fired` is set *before* sending, so a failure can't cause a retry storm. DMs go to `accepted` and `tentative` only; the channel ping adds `mention_role_id` if set.
2. **Finishing** — events past `event_time + duration` flip to `completed` and their message is re-rendered grey with buttons removed.

`cog_unload()` cancels the loop; verified not to leak past unload.

### Notes for future changes

- **Time comparisons.** `event_time` is naive UTC. The event queries compare against `NOW() AT TIME ZONE 'UTC'`, not `CURRENT_TIMESTAMP` — the latter is a `timestamptz` and would be cast using the *session* timezone, which is only correct while the server runs in UTC. The older `operations` queries still use `CURRENT_TIMESTAMP`; prefer the new form for anything added.
- **Per-user timezones come free for display.** All times render as Discord timestamps (`<t:…:F>`), which every client localises automatically. Only *input* is guild-timezone based, so a per-user input timezone is the only part still missing.
- **Persistence** mirrors `ApprovalView`: `setup_hook()` calls `get_live_events()` and re-adds one `EventRsvpView` per open event. Events without a `message_id` are skipped.
- `_format_attendees()` trims mention lists to Discord's 1024-char field limit and appends "…and N more", while the field *name* keeps the true total.
- `_group_signups()` ignores unrecognised `response` values rather than raising, so a future response type can't break old embeds.

---

## Planned Web App (next phase)

### Goal
A web interface that replaces/supplements the Discord bot for slot management:
- No Google Sheets dependency — ORBAT structure stored directly in PostgreSQL
- Discord OAuth2 login — verify server membership and roles
- Visual ORBAT — click a slot to request it
- Slot requests trigger Discord notifications (bot posts to `#slot-approvals`)
- Approvals can stay in Discord (MVP) or also be web-based (full version)
- ORBAT builder — create operations and slots in the web UI

### Agreed tech stack
- **Backend:** FastAPI (Python, async — fits alongside existing code)
- **Frontend:** HTMX for simplicity, or React for richer UI
- **Auth:** Discord OAuth2 via `authlib`
- **DB:** Same PostgreSQL, schema extended (drop `sheet_url`/`sheet_id` dependency)
- **Real-time ORBAT updates:** Server-Sent Events (SSE)
- **Deploy:** Railway (second service) or same Docker Compose

### MVP scope
1. Discord OAuth2 login — verify user is in the guild
2. Read-only ORBAT view
3. Click slot → request submitted → bot posts to `#slot-approvals`
4. Approvals still happen in Discord

### Full version additions
- Web-based ORBAT builder (create operation, add squads, add roles in UI)
- Approve/deny from web as well as Discord
- Operation history and archive page
- Admin dashboard

### Key DB changes needed for web
- New table: `squads (id, operation_id, name, sort_order)`
- New table: `slots (id, squad_id, role_name, sort_order, assigned_request_id)`
- Remove Google Sheets columns from `operations` (or make optional for backwards compat)
- Add `session` table or use JWT for web auth

### Architecture note
Web app and bot share the same PostgreSQL instance. Web app writes requests → bot detects via polling or DB trigger and posts to Discord. This keeps the two surfaces decoupled.

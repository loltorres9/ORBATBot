# CLAUDE.md — ORBATBot / orbat-platform

This file gives full context on the project. Read it before making any changes.

---

## What This Is

A Discord bot for managing Arma 3 operation slot requests across multiple military simulation units. Members request slots; Unit Leaders and admins approve or deny via Discord buttons.

**An operation runs on one of two rosters**, and `utils/roster.py` is the only
thing that knows which: an **ORBAT** held in this bot's own database and edited
in the browser, or a **Google Sheet**, which is where it all started. The sheet
is written on approval; an ORBAT has nothing to write, because the approved
request *is* the booking. See
[the roster provider](#the-roster-provider-utilsrosterpy).

**Current state:** Fully operational bot deployed on Railway, plus an optional
web UI (`web/`) with Discord OAuth2 login for events, game roles, embeds, the
member log, voice time, the ORBAT editor and the slot-approval queue.
**Next phase:** *requesting* a slot is still Discord-side; approving one is not.
The remaining steps are listed under
[Slots on the web](#slots-on-the-web--what-is-done-and-what-is-left).

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
  purge.py              # /purge — bulk message deletion, by count or back to a date
  redditfeed.py         # Watches a Reddit user or subreddit and announces new posts
utils/
  database.py           # All PostgreSQL queries (asyncpg)
  sheets.py             # Google Sheets read/write (gspread)
  roster.py             # One normalised slot, from a sheet or from an ORBAT
  orbat.py              # DB-held ORBATs: the text format, the safe edit, the board
  embeds.py             # Builder-made rich messages → discord.Embed, post and edit
  reddit.py             # One Reddit feed, read and rendered — no Discord, no database
web/                    # Optional browser UI — Discord OAuth2 login, events, roster, approvals
  config.py             # Env-driven config; the feature is off until it is complete
  server.py             # uvicorn driven from inside the bot's event loop
  app.py                # FastAPI routes, session plumbing, template rendering
  auth.py               # OAuth2 flow, signed-cookie sessions, CSRF, flash messages
  guilds.py             # Signed-in user → discord.Member, and what they may do
  service.py            # Create/edit/cancel/delete/RSVP, on top of cogs/events.py
  roles.py              # Game roles, on top of cogs/gameroles.py
  embeds.py             # Embed builder forms, on top of utils/embeds.py
  orbat.py              # ORBAT editor forms, on top of utils/orbat.py
  slots.py              # The approval queue, on top of cogs/slots.py
  operations.py         # Starting and steering an operation, on top of cogs/admin.py
  reddit.py             # The Reddit watches, on top of cogs/redditfeed.py
  nav.py                # The two-level tab bar, built once rather than per template
  voice.py              # Voice leaderboard shaping, the settings form and posting
  invites.py            # Invite labels — where each link was published
  helpers.py            # Guild-timezone formatting and datetime-local parsing
  templates/ static/    # Jinja2 templates and one stylesheet — no build step
lab/                    # Standalone ORBAT-editor playground — no Discord, no Postgres
tests/                  # pytest — utils/reddit.py, and cogs/redditfeed.check_feed()
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

There is no CI or linter config. The tests are `python -m pytest tests lab/tests`
(89 cases): `lab/tests` covers `utils/orbat.py`'s parser and diff — the two
places where a bug silently deletes somebody's slot — and `tests/` covers
`utils/reddit.py`'s feed parsing, templating and how a refusal is handled, plus
what `check_feed()` promises about announcing a post exactly once. The date logic in
`cogs/events.py` (`_next_occurrence()`, `_weekday_day()`, `_add_months()`,
`_nth_occurrence()`) is pure and Discord-free, so it is the obvious next thing
to cover.

---

## Environment Variables

| Variable | Description |
|---|---|
| `DISCORD_TOKEN` | Bot token from Discord Developer Portal |
| `GOOGLE_CREDENTIALS` | **Optional.** Full JSON content of the service account key file. Only read when a sheet-backed operation is actually touched — `sheets.get_client()` raises on the first call, not at import — so the bot starts and runs without it as long as every operation is ORBAT-backed |
| `DB_PASSWORD` | PostgreSQL password (docker-compose only) |
| `DATABASE_URL` | Full connection string — injected automatically by Railway; constructed by docker-compose; set by hand for local development |
| `RAILWAY_API_TOKEN` | **Optional.** Account or team token that lets `/restart` trigger a clean deployment restart via the Railway GraphQL API. Without it `/restart` still works, by exiting non-zero so Railway's `ON_FAILURE` policy relaunches the container. Project tokens do **not** work — `_railway_restart()` authenticates with a `Bearer` header |
| `DISCORD_CLIENT_ID` / `DISCORD_CLIENT_SECRET` | **Optional.** OAuth2 credentials for the web UI. Missing → no web server is started |
| `WEB_SECRET_KEY` | **Optional.** Signs the session, OAuth-state and flash cookies. Changing it signs everyone out |
| `WEB_BASE_URL` | **Optional.** Public origin, no trailing slash. Must match the redirect URI registered in the Developer Portal; also decides whether cookies are marked `Secure`. Empty → derived from the request, which is only meant for local runs |
| `WEB_HOST` / `WEB_PORT` | **Optional.** Listen address. `PORT` (injected by Railway) wins over `WEB_PORT` |
| `WEB_ENABLED` | **Optional.** `0` keeps the site off even when everything else is set |
| `WEB_BRAND` | **Optional.** Site name in the header, tab title and footer. Defaults to `TFP BOT` |
| `REDDIT_USER_AGENT` | **Optional.** How the bot identifies itself to Reddit when reading a watched feed. Reddit answers 429 to a client that doesn't say who it is, so a descriptive value naming your own Reddit account is best; empty falls back to `utils/reddit.DEFAULT_USER_AGENT`. There is nothing else to configure — the feeds are public RSS, so no API registration, OAuth or client secret is involved |
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
| `name` | TEXT | Operation name — from the ORBAT or the spreadsheet title, or overridden on `/setup-slots` |
| `sheet_url` | TEXT | Full Google Sheets URL. **NULL on an ORBAT-backed operation** |
| `sheet_id` | TEXT | Extracted sheet ID. NULL likewise |
| `orbat_id` | INTEGER | → `orbats.id`. NULL on a sheet-backed operation. Exactly one of this and `sheet_url` is set, and `utils/roster.py` is the only thing that looks |
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
| `sheet_row` | INTEGER | Row index in the sheet. NULL on an ORBAT-backed request |
| `sheet_col` | INTEGER | Column index (ORBAT-style sheets) |
| `status` | TEXT | `pending` / `approved` / `denied` / `cancelled` |
| `approval_message_id` | TEXT | Discord message ID in #slot-approvals |
| `approval_channel_id` | TEXT | Discord channel ID for above |
| `approved_by` | TEXT | Display name of approver/denier |
| `denial_reason` | TEXT | Optional reason text |
| `unit_role` | TEXT | Unit role of the requester at submission time |
| `slot_id` | INTEGER | → `orbat_slots.id` on an ORBAT-backed operation; NULL on a sheet-backed one, where `sheet_row`/`sheet_col` identify the slot instead. No foreign key: `apply_orbat_structure()` releases the bookings itself when a slot goes, so the promise the confirmation page makes is kept |
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

### `orbats`
The slot roster held here rather than read out of a Google Sheet — see
[ORBATs](#orbats-utilsorbatpy--weborbatpy).

| Column | Type | Notes |
|---|---|---|
| `id` | SERIAL PK | |
| `guild_id` | TEXT | |
| `name` | TEXT | Shown in the list |
| `description` | TEXT | Optional |
| `nets_text` | TEXT | The net list as its author typed it, for the same reason as below |
| `source_text` | TEXT | The roster as its author typed it. The squads and slots below are the source of truth; this is kept alongside so comments, blank lines and their own spacing survive a reload, which regenerating the text from the structure would flatten |
| `created_by` / `created_by_name` | TEXT | |
| `created_at` / `updated_at` | TIMESTAMP | |

### `orbat_squads`
| Column | Type | Notes |
|---|---|---|
| `id` | SERIAL PK | |
| `orbat_id` | INTEGER | FK → `orbats.id` **ON DELETE CASCADE** |
| `name` | TEXT | The embed field's heading |
| `column_side` | INTEGER | 0 = left, 1 = right. What `_build_orbat_embed()` infers from sheet geometry is stated outright here |
| `exclude_from_count` | INTEGER | 0/1 — replaces the case-insensitive `Reservists` name match |
| `reserved_unit` | TEXT | The unit the whole squad belongs to, matched by name against `UNIT_ROLES`. It began on the slot; `init_db()` lifts any values entered there up to their squad and drops the slot column |
| `radio` | TEXT | The channel the squad talks on internally, e.g. `343 CHN:3`. Free text — every unit writes these slightly differently |
| `sort_order` | INTEGER | |

### `orbat_slots`
| Column | Type | Notes |
|---|---|---|
| `id` | SERIAL PK | The stable slot identity a booking hangs off |
| `squad_id` | INTEGER | FK → `orbat_squads.id` **ON DELETE CASCADE** |
| `role_name` | TEXT | |
| `sort_order` | INTEGER | |

**A slot carries no booking.** Who holds one lives in `requests`, keyed by
`(operation_id, slot_id)`, which is what makes an ORBAT a reusable template
rather than one night's board.

### `orbat_nets`
The long-range nets the whole operation shares — platoon, logistics, air, high
command — as against `orbat_squads.radio`, which is one squad's internal channel.

| Column | Type | Notes |
|---|---|---|
| `id` | SERIAL PK | |
| `orbat_id` | INTEGER | FK → `orbats.id` **ON DELETE CASCADE** |
| `name` | TEXT | *Platoon Net*, *Logi*, *High Com Net* |
| `channel` | TEXT | Free text, e.g. `152 CHN : 1`. May be NULL for a net whose frequency isn't decided |
| `inactive` | INTEGER | 0/1 — struck through on the board: planned, but not in use this time |
| `sort_order` | INTEGER | |

**Nothing hangs off a net**, so unlike the squads and slots a save replaces the
list wholesale — there is no identity for an edit to lose.

### `reddit_feeds`
One row is one watch — see [Reddit announcements](#reddit-announcements-utilsredditpy--cogsredditfeedpy--webredditpy).

| Column | Type | Notes |
|---|---|---|
| `id` | SERIAL PK | |
| `guild_id` | TEXT | |
| `kind` | TEXT | `user` or `subreddit` |
| `source` | TEXT | The name, without the `u/` or `r/` |
| `channel_id` | TEXT | Where posts are announced. NULL = the watch has nowhere to post, so it is skipped |
| `template` | TEXT | The announcement text. NULL = `reddit.DEFAULT_TEMPLATE` |
| `mention_role_id` | TEXT | **Comma-separated** role ids, the same convention as `events` |
| `mention_user_id` | TEXT | Comma-separated user ids — the "tag these people" half |
| `enabled` | INTEGER | 0/1 |
| `seen_ids` | TEXT | The post ids already announced, newest first, capped at `MAX_SEEN`. **NULL means the watch has never been read**, which is what makes the first read seed instead of announcing 25 old posts |
| `last_checked_at` / `last_post_at` | TIMESTAMP | Naive UTC |
| `last_error` | TEXT | What the last read went wrong with, shown on the list page. NULL after a clean read |
| `retry_at` | TIMESTAMP | Don't read this watch again before then — see [being refused](#being-refused-is-about-us-not-about-the-feed). NULL, and cleared by any read that gets through |
| `created_by` / `created_by_name` | TEXT | |
| `created_at` / `updated_at` | TIMESTAMP | |

`UNIQUE (guild_id, kind, lower(source))` — two rows for the same author would
announce every post twice, which reads as the bot being broken. Both writers turn
the violation into a `ValueError`, so it is a message rather than a 500.

### `guild_settings`
| Column | Type | Notes |
|---|---|---|
| `guild_id` | TEXT PK | |
| `timezone` | TEXT | IANA timezone string, default `UTC` |
| `orbat_channel_id` | TEXT | Where the live board and the reminder ping go |
| `approvals_channel_id` | TEXT | Where a new request goes to be decided |
| `archive_channel_id` | TEXT | Where every decided request is recorded |

**All three channel columns are NULL until an admin picks something**, and NULL
means the channel *named* `#orbat` / `#slot-approvals` / `#approval-archive`,
created when it is missing — which is exactly what every guild did before the
columns existed, so an upgrade changes nothing. `cogs/slots.guild_channel()` is
the only thing that reads them; `database.CHANNEL_KINDS` maps each kind to its
column and its fallback name.

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

These are the **defaults**, not the only option: an admin can point each one at
an existing channel from the Operation page. `guild_channel(guild, kind)` in
`cogs/slots.py` is the single resolver — a stored id wins, a stored id whose
channel has since been deleted falls back to the name rather than posting
nowhere, and nothing stored behaves exactly as it always did. **Anything that
needs one of these three channels must go through it**; a fresh
`discord.utils.get(..., name='orbat')` would ignore the admin's choice.

---

## Unit Roles & Access Control

**Unit roles** (defined in `cogs/slots.py` as `UNIT_ROLES`):
`2nd USC`, `CNTO`, `PXG`, `TFP`, `SKUA`

**Unit Leader role name:** `Unit Leader` (defined in `cogs/admin.py` as `UNIT_LEADER_ROLE`)

### Access matrix

| Action | Member | Unit Leader | Admin |
|---|---|---|---|
| `/request-slot`, `/cancel-request`, `/change-slot`, `/leave-operation` | ✅ | ✅ | ✅ |
| `/clear-slot` — or **Release** / **Withdraw** on the web | ❌ | ✅ own unit | ✅ |
| `/assign-slot` — or **Assign** on the web | ❌ | ✅ own unit | ✅ |
| Approve / Deny — in `#slot-approvals` or on the web | ❌ | ✅ own unit | ✅ |
| `/clear-requests`, `/post-orbat`, `/set-event-time`, `/set-timezone`, `/post-event` — all on the web's **Operation** tab | ❌ | ❌ | ✅ |
| `/setup-slots`, `/current-operation`, `/debug-slots` — likewise | ❌ | ❌ | ✅ |
| Choosing the ORBAT / approvals / archive channels — web only | ❌ | ❌ | ✅ |
| Watching a Reddit feed — web only | ❌ | ❌ | ✅ |
| `/sync`, `/restart`, `/archive-old-approvals` — Discord only, bot maintenance rather than slot work | ❌ | ❌ | ✅ |
| `/purge` | ❌ | ❌ | ✅ — anyone with **Manage Messages** in that channel |
| `/game-roles`, `/game-role-list` | ✅ | ✅ | ✅ |
| `/game-role-add`, `/game-role-remove`, `/game-role-panel` | ❌ | ❌ | ✅ |
| `/event-list`, RSVP buttons on an event | ✅ | ✅ | ✅ |
| `/event-create` | ❌ | ✅ | ✅ |
| `/event-edit`, `/event-cancel`, `/event-delete` | ❌ | ✅ own events | ✅ |

**Admin** = `manage_guild` or `administrator` Discord permission.
**Unit gating:** `_can_action_request()` in `slots.py` — admins bypass all unit checks; anyone else needs the `Unit Leader` role **and** the requester's unit role; a request with no unit role can be actioned by any Unit Leader. The Unit Leader half is not optional: the buttons in `#slot-approvals` are visible to everyone who can read the channel, so without it any member of a unit could approve their own request by pressing the button on it.

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
Cancels the current slot (pending or approved) then opens the squad → slot picker again. An approved slot goes through `roster.clear()`, which clears the sheet cell on a sheet-backed operation and does nothing on an ORBAT.

**`/leave-operation`**
Shows a confirmation button. On confirm: cancels the request, releases the slot through `roster.clear()` if it was approved, DMs the member.

**`/post-orbat [channel]`** (Admin — `default_permissions(manage_guild=True)`)
Posts a fresh ORBAT embed to the specified channel (defaults to current). Saves
message ID to DB. **It lives in `slots.py`, not `admin.py`**, next to
`_build_orbat_embed()` and `OrbatRequestButton` — the only admin command that does.

### Admin/Unit Leader commands (`cogs/admin.py`)

**`/setup-slots [orbat] [sheet_url] [event_time] [reminder_minutes] [name]`**
Parses the time, then hands everything to `start_operation()` — see
[Running an operation](#running-an-operation-cogsadminpy).
- Takes **either** an `orbat` (autocompleted over the guild's ORBATs) **or** a
  `sheet_url` — giving both or neither is refused
- Deactivates previous operation (`is_active = 0`)
- Creates the operation record, with `orbat_id` or with the sheet columns
- Parses event time in the guild's configured timezone → stores as naive UTC
- Auto-posts the live ORBAT embed to `#orbat`
- Reminder options: 15 / 30 / 60 minutes
- `name` overrides the operation's name, which otherwise comes from the ORBAT
  or from the spreadsheet's title

**`/assign-slot @member`**
Direct assignment — bypasses the approval flow, recording the request as already
approved. The picker is the command's own; the decision itself is
`assign_slot_request()`, shared with the web. `roster.assign()` writes the sheet
on a sheet-backed operation. Blocked if the member already holds a slot.

**`check_can_assign()` is stricter than `_can_action_request()`, on purpose.** A
Unit Leader needs a unit of their own *and* the member must share it, so a
member with no unit cannot be assigned by just any Unit Leader — unlike deciding
a request that carries no unit, which any of them may do. Choosing who goes on
the roster is not the same act as answering someone who asked.

**`/clear-slot`**
Dropdown of active (pending + approved) slots. On select it calls
`clear_slot_request()`, the same function the web queue's Release and Withdraw
buttons call: `roster.clear()` for an approved one, cancels the DB record, DMs
the member, greys out a pending request's approval message, refreshes the board.
Unit Leaders scoped to own unit.

**`/clear-requests`**
Cancels all pending requests for the active operation, through
`clear_pending_queue()`. Nobody is DMed and nothing is archived — this empties a
queue that was never going to be decided rather than turning anyone down — but
the messages in `#slot-approvals` are greyed out, or a request cancelled here
would keep its buttons and read as actionable to whoever found it next.

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
Shows the active operation and where its roster comes from — the ORBAT's name, or a link to the sheet.

**`/debug-slots [squad]`**
Shows the raw slot data as the bot sees it, whichever roster is in use, keyed by
`db:…` or `sheet:…`. Useful for diagnosing missing slots.

**`/sync`**
Force-syncs slash commands with Discord, and refreshes the ORBAT. It also repairs
stale `sheet_col` values on pending requests — a sheet-only concern, since
inserting a row in a spreadsheet moves every cell below it while a slot id never
goes stale, so the repair is skipped outright on an ORBAT-backed operation.

**`/restart`**
Restarts the bot. Two paths, in order:
1. **Railway GraphQL API** — used when `RAILWAY_API_TOKEN` is set. `_railway_restart()` prefers `RAILWAY_DEPLOYMENT_ID` so the bot restarts *itself* rather than whatever a list query happens to return, falling back to the service's latest `SUCCESS` deployment. Returns the deployment id, which the reply shows truncated.
2. **Process exit** — the fallback, also used when the API call raises. `os._exit(1)` after a 1 s pause (so Discord can deliver the ephemeral reply) trips Railway's `ON_FAILURE` restart policy.

Nothing is lost either way: state lives in PostgreSQL and every view is re-registered by `setup_hook()`. Every invocation is printed with the requesting user and guild.

---

## Approval & Denial Flow

**`approve_slot_request()`, `deny_slot_request()` and `clear_slot_request()` in
`cogs/slots.py` are the whole implementation.** The Discord buttons, the
`/clear-slot` dropdown and the web page are all thin callers: each defers, calls
one of the three, and reports what came back. That
is what keeps the two surfaces from drifting — a decision made in the browser
does exactly what a decision made in `#slot-approvals` does, down to the
competitor denials and the archive record.

Both raise `ActionError` for everything a person can get wrong (the request is
gone, someone already actioned it, the approver's unit does not match). The
button renders it as an ephemeral `⚠️`, the web route as a flash message on the
queue page. They return a dict, so the caller can say how many competing
requests went with the approval without re-querying.

`_dm()`, `_archive_channel()`, `_drop_approval_message()` and
`_void_approval_message()` came out of the button and dropdown callbacks in the
same move. **None of them takes an `Interaction`** —
they take a `discord.Guild` and ids, which is the only reason the web can reach
them at all.

**A decision reads the request's own operation** (`database.get_operation(
req['operation_id'])`), not the guild's active one. `/setup-slots` deactivates
its predecessor but leaves that operation's pending requests alone, and
`setup_hook()` re-registers an `ApprovalView` for **every** pending request, so
an old message in `#slot-approvals` stays clickable indefinitely. Approving one
against the active operation wrote the old request's row and column into the
*new* operation's sheet, named the wrong operation in the archive and the DM,
and looked for competitors under the wrong operation id — so the loser of a
contested slot stayed pending. The board is only refreshed when that operation
is the active one; a decision on a finished one must not redraw it.

### Approval
1. Member submits → `requests` row created with `status = pending`
2. Embed posted to `#slot-approvals` — description: `**Op Name**  ·  @UnitRole\n@Member → **Slot**`. Footer: `Request ID: {id}`. Unit role is a Discord role mention (pings Unit Leaders).
3. Approver clicks **✅ Approve** in Discord, or on `/g/{guild}/slots`:
   - `_can_action_request()` checks the Unit Leader role and the unit
   - DB updated to `approved`
   - `roster.assign()` — writes the sheet on a sheet-backed operation, and does
     nothing on an ORBAT-backed one
   - If that write fails → DB rolled back to `denied`, error shown. Only
     reachable on a sheet: a failed network call is the only thing this ever
     protected against
   - Approval message deleted from `#slot-approvals`
   - Compact green embed posted to `#approval-archive`
   - Member DMed
   - Competing requests for same slot auto-denied (their messages edited grey in `#slot-approvals`, competitors DMed)
   - ORBAT refreshed (fire-and-forget)

### Denial
1. Approver clicks **❌ Deny** → `DenialModal` shown (optional reason, max 200
   chars). On the web the reason is a text field next to the button, with the
   same cap
2. On submit:
   - DB updated to `denied`
   - Message deleted from `#slot-approvals`
   - Compact red embed posted to `#approval-archive` (includes reason)
   - Member DMed
   - ORBAT refreshed

### Cancellation
`_void_approval_message()` — edits the approval message to grey with "📋 Slot Request — Cancelled" title, removes buttons. Does not delete. Its footer names
who did it: the member withdrew, or a Unit Leader cleared it out from under them.

### Clearing
`clear_slot_request()` — the third decision, reached from `/clear-slot` and from
**Release** / **Withdraw** on the web queue. It gives the slot back
(`roster.clear()`, which is nothing at all on an ORBAT), cancels the row, DMs the
member naming who removed them, and refreshes the board when that operation is
the live one. A **pending** request also has its `#slot-approvals` message voided,
so it stops being clickable; an **approved** one had that message deleted when it
was approved, and its `#approval-archive` record is left alone — the archive says
what was decided, and this is a later decision rather than a correction. A failed
sheet write removes nobody: the `ActionError` comes back before anything is
cancelled, so the sheet and the roster still agree.

**`database.cancel_request_by_id()` matches `pending` as well as `approved`.**
It used to be `approved`-only, which meant clearing a pending request DMed the
member and greyed out the approval message while the row stayed pending — the
board kept showing 🟡 and nobody could action the request any more.

### Persistence after restart
`bot.py` `setup_hook()` re-registers `ApprovalView` for every `pending` request and `OrbatRequestButton` as a global persistent view. custom_ids: `orbat_approve:{id}`, `orbat_deny:{id}`, `orbat_request_slot`.

---

## Running an operation (`cogs/admin.py`)

`start_operation()`, `set_operation_time()` and `build_announcement_embed()` are
to `/setup-slots`, `/set-event-time` and `/post-event` what
`approve_slot_request()` is to the ✅ button: the command parses its arguments
and calls one of them, and so does the web's Operation page. They raise
`ActionError` for everything a person can get wrong and know nothing about
interactions or forms.

**Neither surface parses a date in here.** `start_operation()` takes
`event_time` already parsed to naive UTC, because a slash command reads
`25/06/2025 19:00` and a browser sends `2026-06-25T19:00` — two different
readers of the same guild timezone, and the shared function should see neither.

Three details worth keeping:

- **The board post is non-fatal.** A guild where the bot cannot create or post
  to the ORBAT channel still gets its operation; `start_operation()` returns
  `channel=None` and the caller says so. Failing the whole thing over a message
  would leave the roster loaded and the admin thinking it wasn't.
- **The operation is re-read before the board is drawn.** `create_operation()`
  deactivates the predecessor and `set_event_time()` may have written a time, so
  the row that goes to `publish_board()` has to be the one that now exists.
- **`publish_board()` in `cogs/slots.py` is the only place a board is first
  posted** — `/post-orbat`, the auto-post above, and the web button all call it,
  so all three produce the same message with the same persistent button.
  `_update_orbat()` is the other half: it edits the message this one recorded.

`/setup-slots` and `/set-event-time` used to render their timestamps with
`int(naive.timestamp())`, which reads a naive datetime as **process-local** time
— correct only because Railway runs in UTC. Both now stamp `timezone.utc` first.

---

## Google Sheets Integration (`utils/sheets.py`)

**This is one of two roster backends now** — see
[the roster provider](#the-roster-provider-utilsrosterpy). An operation only
comes here when it was created with a `sheet_url` rather than an ORBAT.

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

## The roster provider (`utils/roster.py`)

**An operation is backed either by a Google Sheet or by an ORBAT.** This module
is the only thing that knows which. Everything above it — `/request-slot`, the
approval buttons, the live board, `/assign-slot`, `/clear-slot` — works on one
normalised slot and never branches on the source.

### The key is the slot's identity

`db:412` for an ORBAT slot, `sheet:r12c4` for a spreadsheet cell. That replaced
the `(sheet_row, sheet_col)` pair the whole flow used to compare on, and it is
why one `requests` row can point at either kind without anything downstream
caring. `slot_key()` builds it; `request_key(row)` reads it off a request.

`get_pending_slots()`, `get_approved_slots()` and `get_competing_requests()`
therefore all speak keys. The last one matches in Python rather than SQL,
because the key is derived from two different columns and an operation only
ever has a handful of open requests.

### The normalised slot

| Field | |
|---|---|
| `key` / `value` | the identity above; `value` is what a select menu returns |
| `slot_id` | the ORBAT slot, or None |
| `row`, `col` | the sheet cell, or None |
| `squad`, `role`, `label` | what it is; `label` is what a request records |
| `assigned_to` | who holds it, or None |
| `col_idx` | layout hint — the sheet column, or the ORBAT's 0/1 |
| `excluded` | left out of the counts: a `Reservists` squad, or `nocount` |
| `unit`, `radio` | the squad's unit tag and internal channel, ORBAT only |

`col_idx` is what lets `_build_orbat_embed()` draw both: the midpoint split it
already did on sheet geometry works unchanged on a 0/1 column side.

### Writing is nothing on the ORBAT side

`assign()` and `clear()` write to the sheet for a sheet-backed operation and do
**nothing** for an ORBAT-backed one — the approved `requests` row *is* the
booking, and there is no second copy to keep in step.

That is what removes the approve path's rollback. `ApprovalView._approve_callback`
still has the branch that resets a request to `denied` when the write fails, but
it can now only fire on a sheet: a failed network call is the only thing it was
ever protecting against. The same goes for the 30 s sheet read that used to run
on **every** board refresh.

---

## ORBAT Embed

Built by `_build_orbat_embed()` in `slots.py`, from `utils/roster.py`'s
normalised slots — so a sheet-backed and an ORBAT-backed operation render
identically:

- Title: `🗺️ ORBAT — {operation_name}`
- Description: open / pending / filled counts + optional event timestamp
- Two-column layout by `col_idx`, with spacer fields
- The squad's unit in the field name (`1-1 Alpha  [TFP]`) and its internal
  channel as the first line of the value (`📻 343 CHN:3`), both ORBAT only
- Slot indicators: 🟢 open, 🟡 pending, 🔴 filled
- The shared net list as one final field, struck through where a net is not in
  use — which is why the squads get **7** rows instead of 8 when there is one:
  7 × 3 + 1 = 22, and 8 × 3 + 1 = 25 is the ceiling
- Updated by `_update_orbat()` — fetches the stored message id, re-reads the
  roster through `roster.load_all()`, edits the message

**A squad left out of the counts is displayed anyway.** The header counts the
slots people are expected to fill, and a reserve bench would otherwise make the
operation look permanently under-strength. An ORBAT says so with `nocount`; on a
sheet it is still the case-insensitive `Reservists` name match, which is all
that side has to go on.

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
2. Load `cogs.slots`, `cogs.admin`, `cogs.gameroles`, `cogs.events`, `cogs.voicelog`,
   `cogs.memberlog`, `cogs.purge` and `cogs.redditfeed`. Each is wrapped in its own
   `try`, so one cog failing to import doesn't take the others down
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
| Build and edit ORBATs | admin | `is_admin()` |
| Watch a Reddit feed | admin | `is_admin()` |
| Start an operation, move its time, post the board or the announcement, empty the queue, choose the channels, set the timezone | admin | `is_admin()` |
| Assign somebody to a slot outright | Unit Leader (own unit, and must have one) or admin | `check_can_assign()` |
| Approve, deny or release slot requests | Unit Leader (own unit) or admin | `_can_action_request()` |
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
GET  /g/{guild}/slots                   the approval queue for the live operation
POST /g/{guild}/slots/{id}/approve      exactly what the ✅ button does
POST /g/{guild}/slots/{id}/deny         optional reason, exactly what ❌ does
POST /g/{guild}/slots/{id}/clear        exactly what /clear-slot does
POST /g/{guild}/slots/assign            admin/UL — exactly what /assign-slot does
GET  /g/{guild}/operation               admin — the running operation and its actions
GET  /g/{guild}/operation/settings      admin — the channels and the timezone
POST /g/{guild}/operation/start         /setup-slots
POST /g/{guild}/operation/time          /set-event-time
POST /g/{guild}/operation/timezone      /set-timezone
POST /g/{guild}/operation/board         /post-orbat
POST /g/{guild}/operation/announce      /post-event
POST /g/{guild}/operation/clear-requests  /clear-requests
POST /g/{guild}/operation/channels      which channels the bot posts into
POST /g/{guild}/operation/slots         /debug-slots — rendered in place
GET  /g/{guild}/orbats                  admin — ORBAT list, POST to create
GET  /g/{guild}/orbats/{id}             the roster editor
POST /g/{guild}/orbats/{id}             action=preview | save | confirm
POST /g/{guild}/orbats/{id}/duplicate   copy the structure, not the bookings
POST /g/{guild}/orbats/{id}/export      admin — write it into a new sheet tab
POST /g/{guild}/orbats/{id}/delete      cascades to squads and slots
GET  /g/{guild}/embeds                  admin — saved embeds
GET  /g/{guild}/embeds/new              builder          POST to save a draft
GET  /g/{guild}/embeds/{id}             preview, send, delete
GET  /g/{guild}/embeds/{id}/edit        builder          POST to save
POST /g/{guild}/embeds/{id}/send        post as a new message
POST /g/{guild}/embeds/{id}/delete      optionally deletes the Discord message
GET  /g/{guild}/reddit                  admin — the Reddit watches
GET  /g/{guild}/reddit/new              add form           POST to create
GET  /g/{guild}/reddit/{id}             edit form          POST to save
POST /g/{guild}/reddit/{id}/preview     the newest post as it would be announced
POST /g/{guild}/reddit/{id}/check       the scheduled check, run now
GET  /g/{guild}/reddit/{id}/posts       what the feed still carries, to catch one up
POST /g/{guild}/reddit/{id}/announce    post one of them by hand
POST /g/{guild}/reddit/{id}/delete      stop watching
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

Server-rendered Jinja2 plus one hand-written stylesheet. No build step, no CDN
and no script files — the page has to work from a fresh container with nothing
but the bot's own dependencies installed. The one exception is a handful of
inline `confirm()`s — the ORBAT delete button, and Release/Withdraw on the slot
queue — each of which degrades to acting without the prompt if scripting is off;
nothing else on the site depends on JavaScript. Keep them free of interpolated
data: a display name with an apostrophe in it would end the JavaScript string,
and Jinja's HTML escaping does not reach inside an attribute the browser hands
to the script parser.

**Branding is data, not markup.** The name comes from `config.brand` (`WEB_BRAND`,
default `TFP BOT`) and the logo from `_logo_url()`, which looks for
`web/static/logo.*` at startup and returns `''` when there is none — every
template guards on that, so a deployment without a logo file renders the name
alone instead of a broken image. The URL carries the file's mtime so a replaced
logo isn't served from a browser cache. Both are Jinja globals; they don't vary
per request.

### The approval queue (`web/slots.py`)

`/g/{guild}/slots` — the **Slot Approvals** tab — lists the live operation's
requests: the pending ones with **Approve**, a denial-reason field and
**Withdraw**, the approved ones with **Release**. It is the same decision as the
buttons in `#slot-approvals` and as `/clear-slot`, taken through the same three
functions — see [Approval & Denial Flow](#approval--denial-flow).

Three things about the page are deliberate:

- **A Unit Leader sees the whole queue but can only act on their own unit.**
  `may_action` is re-computed per row from `_can_action_request()`, and a row
  they may not decide shows *"CNTO only"* instead of the buttons. Hiding those
  rows entirely would make the page lie about how many people are waiting.
- **Competing requests are marked `contested`.** Two people wanting the same
  slot is the one case where approving is a choice rather than a rubber stamp,
  and the request rows give no hint of it on their own — in Discord they are
  simply two messages. Approving one still auto-denies the other, as it always
  did.
- **Releasing is offered on both lists, and is one action, not two.**
  **Release** on an approved row and **Withdraw** on a pending one are the same
  `POST …/clear` and the same `clear_slot_request()`; they read differently
  because giving a booking back and taking an undecided request out of the queue
  feel like different things to whoever clicks. That is exactly the pair
  `/clear-slot` already offered in one dropdown. Both carry a `confirm()`,
  because neither can be undone and both DM the member.

`queue()` returns the source line (*ORBAT: Zug-ORBAT*, or *Google Sheet*), so it
is obvious which backend the operation runs on without opening the ORBAT tab.

**Assign is on the same page**, because it is the same audience: it puts
somebody on a slot with no request and no approval, exactly as `/assign-slot`
does. Two things about it are worth knowing:

- **Who** is a free-text box taking a Discord ID, a mention or a name, because
  `Intents.default()` cannot list a guild's members and so there is no dropdown
  to offer. An id or a mention is a `fetch_member()`; a name goes through
  `guild.query_members()`, which is a gateway *search* and needs no privileged
  intent (only listing everybody does). More than one match is reported with
  their ids rather than guessed at — assigning the wrong person means a DM to
  the wrong person and a slot held by somebody who does not know they hold it.
- **The free-slot list is loaded with the queue, and its failure is contained.**
  On a sheet-backed operation that is a network read; if it fails, the assign
  form says so and the queue itself still renders. The queue is the reason to be
  on this page.

`approve()`, `deny()`, `clear()` and `assign()` translate `ActionError` into `ValueError`,
which is the convention the rest of `web/` already uses for "this is a message for the user";
the route renders it as a flash on the same page. The denial reason is capped at
`MAX_REASON = 200`, matching `DenialModal`.

### The tab bar (`web/nav.py`)

Eight flat tabs stopped saying anything: nothing showed that Operation, Slot
Approvals and ORBATs are three views of the same evening. The bar is now two
levels, and `build()` returns the whole structure — the top row, the second row
per group, and the page-key → group map, so a page names only itself and
`_nav.html` works out where that sits.

Three things about it are deliberate:

- **Permissions decide what is in the structure, not what the template hides.**
  A group whose every page is out of reach is not built at all, and the group's
  own link is its *first reachable page* — an admin lands on Operation, a Unit
  Leader on the queue.
- **A group of one renders no second row**, where it would only repeat the tab
  above it. That is exactly the Unit Leader's view.
- **It is built in Python.** The shape of the bar — which groups exist, who sees
  what, where each lands — is precisely what goes wrong when it is spread across
  template conditionals in nine files.

### The Operation page (`web/operations.py`)

`/g/{guild}/operation` — admin only, under the **Operations** group. What is
running now, its start time, and then one collapsed panel each for posting the
board, posting the announcement, emptying the queue, reading the raw roster, and
starting a different operation.

**The panels are `<details>`.** Native, so they work with scripting off like
everything else here, and they are what keeps the page short: the status and the
start time are the only things anybody looks at most weeks. `operation_page()`
takes a `panel` argument so a form that comes back with an error re-opens the
section it came from, rather than folding away with what the person typed still
in it.

**The channels and the timezone are on `/operation/settings` instead.** They
belong to the server rather than to an operation and are set once; having them
here put three channel dropdowns on one screen, which is what made the page read
as repeating itself. `overview()` therefore no longer returns them.

Like the approval queue, the page owns **no rules** — every form calls into
`cogs/admin.py` or `cogs/slots.py` (see
[Running an operation](#running-an-operation-cogsadminpy)) and turns an
`ActionError` into the `ValueError` the route flashes. `operation_action()` in
`web/app.py` is the shape every form shares: check CSRF, run one service call,
flash what came back or re-render with the error.

`/debug-slots` is the one exception to that shape — it renders **in place**
rather than redirecting, because a flash message is the wrong container for
forty lines of output.

**The channel form's empty option is "Default", not blank.** Nothing chosen and
"chosen, and it happens to be #orbat" mean different things the day somebody
renames a channel, so the page says which one is in force; a stored channel that
has since been deleted is called out rather than quietly showing the default.

### Deliberately not covered yet

- **Booking a slot from the web** — an ORBAT can back a live operation, and the
  whole admin half is on the web now, but the *requesting* itself is still
  Discord-side (`/request-slot`, `/change-slot`, `/cancel-request`,
  `/leave-operation` and the board's button), and is meant to stay there.
- **`/sync`, `/restart` and `/archive-old-approvals`** — bot maintenance and a
  one-time migration rather than slot work, so they stayed slash commands.
- **Moving an event to another channel** — the message would have to be deleted
  and reposted, losing the sign-up history's continuity; cancel and recreate.
- **Per-user input timezones** — display is already per-user via Discord
  timestamps, but everything typed in is guild-timezone based.

### Slots on the web — what is done and what is left

Done: the tables, the editor at `/g/{guild}/orbats`, `operations.orbat_id`, and
the provider that makes `cogs/slots.py` blind to where the roster came from. An
operation runs on an ORBAT today.

What remains, in the order it makes sense to do it:

- Booking someone who is not on Discord — `requests.member_id` is read as a
  snowflake in six places: the two archive-embed mentions in `cogs/slots.py`,
  the `fetch_member()` in `_dm()`, the one in `/clear-slot`, and the fetch plus
  the `<@…>` ping in `bot.py`'s reminder loop. It needs to become nullable with
  each of those guarded (pulling the button callbacks into `_dm()` already
  removed three of them)
- Click-a-slot-to-request on the web, posting to `#slot-approvals` as today —
  approve/deny from the web is done, see
  [the approval queue](#the-approval-queue-webslotspy)
- Importing an existing sheet into an ORBAT once, via `sheets.load_all_slots()`,
  mapping live requests onto the new slots by `slot_label`
- An operation archive
- Live updates were sketched as Server-Sent Events; running in-process makes that
  straightforward, since the request handler already sees every change

---

## ORBATs (`utils/orbat.py` + `web/orbat.py`)

The slot roster, built and maintained in the browser instead of in a Google
Sheet. Admin-only, one tab on the guild page.

**An operation can be run on one.** `/setup-slots orbat:<name>` starts an
operation against an ORBAT instead of a sheet, and from there the whole flow —
requesting, approving, the live board, `/assign-slot`, `/clear-slot` — works
against the database. See
[the roster provider](#the-roster-provider-utilsrosterpy) for how the two
backends meet.

### Why a text field and not a slot editor

`web/` has no JavaScript and no build step, which rules out drag-and-drop. The
alternative — an up/down button per row — is worse than the sheet it replaces at
forty slots. So the editor is one indented-text field, which is how ORBATs get
written down anyway:

```
1-1 Alpha  | right, unit:TFP, radio:343 CHN:3
  Squad Leader
  Rifleman

Reservists  | right, nocount
  Reserve
```

Squad lines start at the left margin, slots are indented (space or tab). Options
after a pipe, all on the squad: `left` / `right`, `unit:TAG`, `radio:…`,
`nocount`. `#` starts a comment.

Options are **separated by commas**, and that is what the help text tells people
to write. `_peel_options()` also reads them without one: it splits before a
keyword that takes a value (`unit:`, `radio:`, `net:`) and peels the standalone
keywords off either end, so `| left unit:CNTO` is two options rather than one
unknown one. Both rules key on names this module already knows, which is what
keeps a value containing spaces whole — `unit:2nd USC` and `radio:152 CHN : 1`
are followed by neither. Without this the whole chunk read as one unknown
option and the column and the unit were lost together, in silence.

**`_split_options()` keeps an option's case** and lower-cases only the keyword
when matching. It used to lower-case the whole list, which turned a channel
written `343 CHN:3` into `343 chn:3` on the way in.

**The unit tag is stored as typed** and spelled by `web/orbat.py` against the
real `UNIT_ROLES` — `unit:tfp` becomes `TFP`, `unit:2nd usc` becomes `2nd USC`.
Upper-casing it in the parser would have rendered a unit genuinely called
`2nd USC` as `2ND USC`.

**The unit is per squad, not per slot.** A squad belongs to a unit as a whole —
that is how the rosters are actually organised — so tagging every line of it
would be the same tag repeated six times. `utils/orbat.py` takes the tag as free
text and knows nothing about units; `web/orbat.py` warns when it matches none of
`UNIT_ROLES`, as a warning rather than an error because a unit could be renamed
in Discord tomorrow and a roster that stops saving over that would be worse. A
`unit:` written on a slot line produces a warning naming the squad it belongs on,
rather than being dropped in silence. A leading `1.` / `2)` / `3 -` is stripped, so lines pasted out
of a sheet land clean — the number is load-bearing there (it keeps two "Rifleman"
cells apart) and noise here, where every slot has an id.

The price is that there is no live preview: **Preview** is a button. That is the
one thing to weigh if this ever gets reconsidered.

`assign_columns()` splits the squads down the middle when nobody wrote `left` or
`right`, reproducing what the sheet reader infers from column geometry. One
explicit marker turns the guessing off for the whole ORBAT.

### Which ORBAT is live

An ORBAT is a template and a guild can hold several, so **which one backs the
operation running now** is the first thing you need from the list.
`get_guild_orbats()` carries `live_operation` — the active operation's name, or
NULL — as a correlated subquery, and only one row can ever have it, because a
guild has one active operation. It is said in three places, all of which are
"you are about to act on tonight's board":

- the ORBAT list, as a 🔴 chip naming the operation
- the editor, as a banner, because an edit here changes the live board
- the Operation page's start dropdown, so restarting on the same ORBAT is a
  deliberate choice rather than a surprise

`orbat_live_operation(orbat_id)` is the single-row form the editor uses.
`web/orbat.delete()` already refused a live ORBAT; the banner means you know
before you get there rather than after.

### The edit must never unseat anybody silently

A slot's id is what a booking hangs off, so re-parsing the text cannot drop every
slot and recreate it. `build_diff()` matches in three passes, most to least
confident: squads by name, then leftover squads pairwise by position (a rename);
inside a matched squad, slots by role name in order, then leftover slots pairwise
by position (a rename). A rename therefore keeps the id, and with it the booking.

Two properties fall out and are worth keeping:

- **Reordering is free.** Moving lines around matches every slot by name in
  pass 1, so nothing is added or removed.
- **Duplicate role names pair up in order.** Three `Rifleman` lines cut to two
  keep the *first two* existing slots, so the booked one is not the casualty.

`needs_confirmation` is deliberately wider than `destructive`. Removing a booked
slot is destructive. Renaming one is not — nobody is unseated — but the person's
role changes under them, which is right when the edit was a typo fix and wrong
when it was meant as a replacement. The text cannot tell those apart, so both
stop at a confirmation page naming the people affected. Everything else saves
straight away: asking on every edit trains people to click through the one that
matters.

**Applying a removal releases the booking.** `apply_orbat_structure()` sets those
requests to `cancelled` with `slot_id = NULL` before the `DELETE`, and does the
same for the slots a deleted squad cascades away. `slot_id` carries no foreign
key, so without that the request would survive as an approved booking pointing at
a slot that no longer exists — invisible on every board, and a contradiction of
what the confirmation page just promised.

**Deleting the whole ORBAT obeys the same rule, and refuses outright while it is
live.** `web/orbat.delete()` checks `database.orbat_operations()` first: an ORBAT
running the guild's **active** operation cannot be deleted, because the cascade
would take tonight's entire board with it and no confirmation prompt makes that
recoverable. Once that operation is over, deleting is allowed and
`database.delete_orbat()` releases the bookings in the same transaction as the
`DELETE`, exactly as an edit does — `operations.orbat_id` and `requests.slot_id`
both carry no foreign key, so nothing else would.

### The net list is a second field, not part of the roster

The shared nets are their own textarea and their own parser (`parse_nets()`),
one net per line as `Platoon Net | 152 CHN : 1`. A leading `-` marks a net that
is planned but not in use, rendered struck through — the same convention
`cogs/events.py` uses for a decline response.

They are kept out of the roster grammar deliberately. A net has no identity for
an edit to lose, so it needs none of the diff machinery above and is simply
replaced on save; folding it into the roster text would put a second grammar
into the one parser that must not get slots wrong. Their problems are also
reported in their own panel, so a line number means something: line 3 of the
roster and line 3 of the nets are different places.

### Exporting to a sheet

`sheets.export_orbat()` writes the roster into a **new tab** of a spreadsheet
and never touches an existing one — that is the whole safety story, because an
export must not be able to overwrite the sheet another operation is running on.
A title collision gets a `(2)` suffix rather than replacing anything.

The layout is the one `load_slots()` reads: a squad header, then `N. Role`
beside `[] <Insert Name>` or `[TAG] Name`. Two consequences worth knowing:

- Moved to first position, an exported tab reads back **exactly** like a sheet
  the bot had been filling in itself — `assign_slot()` writes `[TAG] Name` into
  the cell the same way, and `_extract_role()` keeps the number prefix on
  purpose so two `Rifleman` slots stay apart.
- It is still one-way. `load_slots()` only ever reads `sheet1`, so an exported
  tab is never picked up on its own; nothing is stored about the export either.

The unit rides in the squad header (`1-1 Alpha [TFP]`) and the radio goes on its
own line below it, where `_is_squad_header()` skips it as a frequency — so the
squad name above stays the header for the slots underneath.

### The board, and Discord's limits

`build_board()` groups squads into the same left/right columns
`_build_orbat_embed()` uses, with the same 🟢/🟡/🔴 line per slot and the same
counted header. The difference is where the layout comes from: the cog infers it
from the sheet's geometry, this reads `column_side` off the squad.

`check_limits()` has no counterpart today. An ORBAT built in a browser can
outgrow what Discord will render — 25 fields, 1024 characters per field value,
6000 per embed — and finding that out when the board silently loses its last
three squads is too late. The editor says so while it can still be changed.

The net list rides along as **one more field**, which is what makes eight rows
the cap rather than eight-and-a-bit: 8 × 3 + 1 is exactly 25.

### Notes for future changes

- **`utils/orbat.py` imports nothing.** No discord.py, no FastAPI, no asyncpg —
  which is why the parser and the diff are the only tested code in this repo
  (`lab/tests`, 47 cases). Keep it that way: these are the two places where a
  bug silently deletes slots.
- **`lab/` is the same code.** The standalone playground that prototyped this
  re-exports `utils/orbat.py` rather than keeping a second copy; a drifting
  parser would be the worst possible bug here. It has no other reason to exist
  and can be deleted whenever.
- **The editor page steps outside the 900px column** via `.widepage`, because
  the text field and the preview do not fit side by side inside it. It is the
  only page that does.

---

## Reddit announcements (`utils/reddit.py` + `cogs/redditfeed.py` + `web/reddit.py`)

A watch on a Reddit user or a subreddit that announces every new post in a
channel, with the guild's own wording and whoever it wants pinged. Admin-only,
one tab on the guild page, and no slash command — the text and the ping list are
a form, not something to type into a modal.

### It announces. It does not ask for votes.

A Discord message asking people to go and upvote a post is **vote manipulation**
under Reddit's content policy, and it is one of the few things Reddit acts on
hard: not only the posting account but the accounts that reliably answer the
call, because the same group voting minutes after the same author posts is
exactly what the voting timeline shows. Nothing in the wording avoids that, so
there is no vote wording anywhere here: not in `DEFAULT_TEMPLATE`, not in
`TEMPLATE_EXAMPLES`, and the help text under the field says so. Asking people to
read and comment is fine, and weighs more in Reddit's own ranking anyway.

The post's score is deliberately never read, rendered or referred to — a feature
that displays it is one step from a feature that asks people to change it.

### No API registration

`utils/reddit.py` reads the public Atom feed — `/user/<name>/submitted.rss` or
`/r/<name>/new.rss` — which is the same page a browser gets, not the OAuth API
surface. So there is no client id, no secret and nothing to register. It does
want `REDDIT_USER_AGENT`: Reddit rate-limits a client that doesn't identify
itself down to nothing.

### Being refused is about us, not about the feed

Identifying ourselves is not always enough, and this is the thing to know before
changing any of it. **Reddit also refuses the address the request comes from.**
A hosting provider's IP — Railway's included — is turned away from the public
feeds with 429 however politely it asks and however rarely it asks, so this is
not a frequency that can be tuned down into working. One watch polled every five
minutes is 288 requests a day, which is nothing; the refusal is not about that.

Two things follow, and both are load-bearing:

- **`fetch()` tries both hosts.** `www.reddit.com` carries the bot detection;
  `old.reddit.com` is the legacy renderer and is markedly less fussy about who
  is asking. The second one is only ever asked on a check that has already been
  refused, so the normal case is still one request. A 404 or a 403 is *not*
  retried there — those are about the feed and say the same thing from either
  host.
- **A refusal stands the watch down.** `RateLimited` carries a wait (Reddit's
  own `Retry-After` where it gives one, bounded, otherwise
  `DEFAULT_RETRY_AFTER`), `check_feed()` writes it to `retry_at`, and
  `get_due_reddit_feeds()` skips the row until it passes. Asking again on the
  next tick is what turns a passing throttle into a standing one. **Check now**
  ignores the wait, because a person pressing a button may try; the loop may
  not.

If a watch is refused from both hosts every time, the feed is not the way in
from that host and the OAuth API (a script app, `oauth.reddit.com`) is — which
is a different fetcher, not a different frequency.

`utils/reddit.py` imports **nothing but the standard library** — `aiohttp` is
imported inside `fetch()`, the same trick `utils/sheets.py` plays with its
credentials — which is what makes the parsing and the templating testable
(`tests/test_reddit.py`).

### What comes back is not always a feed

`fetch()` hands `parse_feed()` the **raw bytes**. An XML document declares its
own encoding and that declaration is the authority — not the HTTP header, and
not a guess made from the bytes. Decoding first (`response.text()`) put a third
party in charge of it, which is how one accented character in a post title turns
into `not well-formed (invalid token): line 20, column 195`.

Three kinds of not-a-feed, told apart because they need different answers:

- **An HTML body is a refusal in disguise.** Reddit serves its block page with a
  200, so `parse_feed()` sniffs for one and raises `RateLimited` — the same
  thing a 429 means, handled the same way. (It is worth knowing that such a page
  fails XML parsing as *mismatched tag*, not *invalid token*, so the two
  symptoms are genuinely different problems.)
- **Characters XML forbids are dropped.** No valid feed can contain a control
  character, so removing them cannot change a well-formed document — and one
  stray byte in a title must not cost the whole feed.
- **Anything else raises `NotAFeed`**, which quotes the fragment it choked on as
  `repr()`. The line and column mean nothing to whoever reads the flash message;
  the characters themselves separate a stray `&`, an invisible byte and a
  mis-encoded one at a glance. `NotAFeed` is also retried on the other host,
  because a body that isn't the feed is usually about who is asking — but it
  does not stand the watch down, since it is not a refusal.

Two details in the parsing are load-bearing:

- **`published` beats `updated`.** Editing a post moves `updated` and leaves
  `published` alone; reading `updated` would make an edit look like a new post.
- **An entry with no id or no link is skipped, not fatal.** One malformed entry
  must not cost the rest of the feed.
- **The category's `term` beats its `label`.** Reddit writes them as
  `term="arma" label="r/arma"`, and for a post made on the author's own profile
  as `term="u_Name" label="u/Name"`. The term is already what belongs after an
  `r/` in both cases — a profile post really does live in `r/u_Name` — while the
  label would render as `r/u/Name`, which is not a place. A user watch carries
  both kinds, since `/user/<name>/submitted.rss` is everything that author
  submits anywhere.

`render()` substitutes `{title}`, `{url}`, `{author}` and `{subreddit}` with one
literal replace each rather than `str.format()`: a template is text somebody
typed, so a stray `{` in it has to be harmless.

### The first read announces nothing

`seen_ids` is NULL until a watch has been read once, and that is the whole
difference between the first read and every later one: the first records what is
already on the feed and posts nothing. Without it, switching a watch on would
dump the author's last 25 posts into the channel.

It is a rolling set of post ids rather than a high-water timestamp for the same
reason `published` is preferred above — a timestamp moves when a post is edited,
an id doesn't.

`MAX_PER_CHECK` caps one check at three announcements. Nothing is dropped: the
ids of the posts that did go out are the only ones marked seen, so the rest
follow on the next check.

### `check_feed()` is the whole implementation

The polling loop calls it and so does the **Check now** button, exactly as the
approval buttons and the web queue share `approve_slot_request()`. It takes a
feed row and the bot, and it knows nothing about interactions or forms; both
`reddit.FeedError` and `ValueError` carry a message meant for a person, which
the loop prints and the web page flashes.

Its two send-failure paths differ on purpose:

- **`Forbidden`** leaves the post unannounced. A permission problem is fixable,
  and the post goes out on the next check once it is.
- **Any other `HTTPException`** marks the post announced anyway. Retrying a
  message Discord refuses, every five minutes for ever, would wedge the watch
  behind one bad post.

**`allowed_mentions` names exactly the roles and people the watch stores.** The
post title goes into the message verbatim, so a title containing `@everyone`
would otherwise ping the whole server — the announcement is quoting Reddit, not
speaking for the admin who set the watch up.

### The page

`/g/{guild}/reddit` lists the watches; the form adds and edits one. Three things
about it are worth knowing:

- **People are a free-text box of user ids**, for the same reason the assign form
  on the slot queue takes one: `Intents.default()` cannot list a guild's members,
  so there is no dropdown to offer. Roles are checkboxes, as everywhere else.
- **Preview posts nothing and marks nothing as seen**, and renders what is
  *in the form* rather than what is stored — it is meant to be pressed while
  working on the text. **Check now** is the opposite: it is the scheduled check,
  run early, and it does announce.
- **Pointing a watch at a different source resets `seen_ids`**
  (`reset_reddit_feed_seen()`), so the new feed is seeded on its next read
  instead of announcing its history. The page says so.

### Catching a post up by hand

`/g/{guild}/reddit/{id}/posts` lists what the feed still carries, says which of
them have been announced, and gives each an **Announce** button —
`announce_post()`, which is the second thing besides `check_feed()` that sends
one of these messages. It exists because there are three ways a post ends up
never reaching the channel and none of them heals itself: the bot was down when
it went up, Discord refused that one message (which `check_feed()` marks as
announced *on purpose*, so one bad post can't wedge the watch for ever), or the
watch was pointed somewhere and seeded past it.

Two things about it are load-bearing:

- **It marks the post seen**, so the next scheduled check doesn't announce it
  again — and on a watch that has **never been read** it seeds the rest of the
  feed at the same time, exactly as a first read would. Without that, catching
  one post up by hand would make the next check announce the other twenty-four.
- **A send that fails records nothing.** The point of the button is to get the
  post out; if it didn't go out, nothing should be stored as though it had.

It is its own page rather than a panel on the watch form, because opening it
reads the feed — and the form has to open without touching Reddit, not least
when Reddit is refusing us.

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

**`_used_invite()` returns an `Attribution`, and it always explains itself.**
When it can't name a link it sets `reason`, and the join embed prints that
instead of dropping the field: a missing line looked identical whether the bot
lacked **Manage Server**, had never read the invite list, or genuinely saw no
counter move, which made the feature impossible to debug from Discord. The
reasons are in `REASONS`; `off` is the only one that prints nothing, since an
admin who unticked the box does not need telling on every join.

Reading invites needs **Manage Server**. `_fetch_invites()` returns **None**
rather than an empty list when it may not read them — the two must stay
distinct, because diffing a join against an empty snapshot reads every existing
invite as freshly incremented and credits the first one seen. The same reason
`before is None` reports `nocache` rather than guessing.

A guild with `VANITY_URL` falls back to `vanity_invite()` when no counter moved,
labelled `vanity` so it isn't looked up like an ordinary code.

**Naming the link and naming who made it are two lookups against two different
permissions**, which is why `_used_invite()` keeps them apart: `_match_invite()`
needs **Manage Server**, and `_invite_creator()` needs **View Audit Log**. Either
can succeed without the other. A live invite already carries its `inviter`, so
the audit-log walk only runs for a link that is already gone — the single-use one
an admin made for one person, where who sent it is the whole answer. It is
skipped for a vanity URL, which is a guild setting and has no `invite_create`
entry to find. One walk caches every code it passes, misses included, so a code
the log can no longer reach doesn't cost a scan on every join; a walk that
*fails* is not cached, so a permission granted afterwards takes effect at once.

**A single-use link never shows its increment.** Discord deletes an invite the
moment it hits `max_uses`, so the code that let the member in is simply gone.
Two paths find it, because `INVITE_DELETE` and `GUILD_MEMBER_ADD` race: if the
delete arrived first the code is in `_deleted`, and if it hasn't the code is
still in the cached snapshot but already absent from the freshly fetched list.
Both are bounded by `CONSUMED_WINDOW` — an unbounded "gone since the last
snapshot" would credit a link an admin tidied up yesterday to the next person
through the door. Exactly one candidate is credited (kind `consumed`); two is
ambiguous and reported as unknown. `_inviters`, filled by `on_invite_create`,
is how such a join can still name who made the link; the ordinary path reads
`inviter` off the live invite object.

**`invite_labels` is what makes the code useful.** The join message shows the
label next to the code, which is the whole point: the alternative is keeping a
spreadsheet of which link was posted where and consulting it by hand. A shared
link reads *created by*, a consumed one *invited by* — the first is used by many
people, the second was made for the one who just walked through it.

Every listener body is wrapped in a `try`/`except` that prints and moves on: a
failure to *log* an event must never propagate into the gateway handler.

---

## Purge (`cogs/purge.py`)

`/purge [amount] [since]` deletes messages in the channel it is run in. `amount`
takes the newest 1–`MAX_MESSAGES` (1000); `since` takes everything posted after a
point in time. Both together means "at most this many, and nothing older than
that" — at least one is required, since a bare `/purge` has no sensible default.

`_parse_since()` accepts an age (`30m`, `2h`, `7d`, `1w`), a bare date, which
means midnight in the guild's timezone, or a date and time, which goes through
`admin._parse_event_time()` so `/purge` and `/event-create` read the same
formats. It returns an **aware** UTC datetime, unlike `event_time` elsewhere in
this codebase, because it is compared against `Message.created_at`.

### The 14-day rule shapes the whole command

Discord's bulk endpoint refuses messages older than 14 days, and rejects the
**entire batch** if one of them is — hence `BULK_MARGIN`, which keeps a message
that crosses the line between the preview and the delete call out of the batch.
Older messages are deleted one REST call at a time, roughly one a second, so
they are capped at `MAX_SLOW_DELETES` (200) per run and the reply says how many
were left for the next one. The cap is applied to a newest-first list, so a
second run continues where the first stopped.

`discord.Forbidden` is a subclass of `discord.HTTPException`, which is why both
delete helpers catch it **first** and return a `blocked` flag: without that,
permission taken away mid-run would fall into the "salvage the batch one at a
time" path and spend 100 doomed REST calls per chunk.

### Other things worth knowing

- **The confirmation holds the messages, not the query.** `ConfirmPurgeView` is
  built from the exact list the preview counted, so anything posted while the
  admin reads it is left alone — deletion can't be undone, and widening the set
  after the preview would be the one way to delete something nobody saw listed.
- **`_collect()` walks newest-first and breaks at `since`**, rather than passing
  `after=` to `history()`. discord.py would keep paging through older history
  until the limit is exhausted, so a `since` of two hours would cost ten pages in
  a busy channel instead of one.
- **Permissions are read off the channel**, not `guild_permissions`, so a
  channel overwrite that grants or denies Manage Messages is honoured. The check
  is re-made for the bot too, before anything is previewed.
- Pinned messages are deleted like any other, but the confirmation says how many
  are in the set, so a pinned rules post can't go silently.
- `Message.delete()` takes no audit-log reason, so only the bulk path names the
  admin in the audit log. Every run is printed either way.

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

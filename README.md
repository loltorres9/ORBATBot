# ORBATBot

A Discord bot for managing Arma 3 operation slot requests. Members request slots via a two-step squad → slot picker or the **📋 Request a Slot** button on the ORBAT embed; admins and Unit Leaders approve or deny requests with a button click, and the Google Sheet is updated automatically.

It also manages **self-assignable game roles** — permission-free tag roles for games like Minecraft or DCS that members opt into themselves, so you can `@mention` everyone who plays a given game. See [Game Roles](#game-roles).

And it runs **standalone events** with sign-ups — trainings, movie nights, anything — where members answer Accepted / Tentative / Declined on a button, or whatever options the organiser defined, and get reminded before the start. No Google Sheet involved. See [Events](#events).

Events can also be managed from a **browser** instead of slash commands: an optional web interface with Discord login, running inside the same bot process. See [Web UI](#web-ui).

---

## Features

Three independent feature areas plus maintenance commands. **ORBAT & Slots** is the Arma operation system backed by a Google Sheet; **Events** and **Game Roles** work on their own with no sheet involved.

In the **Who** column, *Unit Leader+* means Unit Leaders and Admins, and Unit Leaders are scoped to their own unit. See [Role-Based Access](#role-based-access) for the full matrix.

### 🗺️ ORBAT & Slots

Operation slot requests, approvals and the live ORBAT board, driven from a Google Sheet.

| Command | Who | What |
|---|---|---|
| `/request-slot` | Everyone | Open the squad → slot picker for the current operation |
| `/cancel-request` | Everyone | Cancel your pending slot request |
| `/change-slot` | Everyone | Forfeit your current slot and pick a new one |
| `/leave-operation` | Everyone | Remove yourself from the operation entirely (pending or approved) |
| `/assign-slot <member>` | Unit Leader+ | Assign a member to a slot directly, bypassing approval |
| `/clear-slot` | Unit Leader+ | Remove a member from a slot; restores the sheet cell and strips the unit tag |
| `/setup-slots <url>` | Admin | Load a Google Sheet as the current operation; optional event time and reminder; auto-posts the ORBAT to `#orbat` |
| `/post-orbat [channel]` | Admin | Post or re-post the live ORBAT board |
| `/set-event-time <time>` | Admin | Update the operation's start time and reminder |
| `/post-event [channel] [mission_name] [event_time]` | Admin | Post an announcement embed for the operation, pointing at `#orbat` for sign-ups |
| `/clear-requests` | Admin | Cancel all pending requests for the current operation |
| `/current-operation` | Admin | Show which operation is active and link to its sheet |

> `/post-event` only *announces* the active operation — sign-up still happens through ORBAT slots. For a standalone event with its own attendee list, use [`/event-create`](#events) instead.

**How it behaves**

- **Two-step slot picker** — squad first, then slot. Used by `/request-slot`, `/change-slot`, `/assign-slot` and the ORBAT button alike
- **📋 Request a Slot** button — persistent button on the live ORBAT embed, so no command is needed
- Slots show 🟢 available, 🟡 pending (also requested — compete for it) or 🔴 filled, in real time
- Several members can request the same slot; the approver picks, and the rest are auto-denied and notified
- Approvals happen in `#slot-approvals` with **Approve / Deny** buttons and a denial modal for an optional reason
- Actioned requests leave `#slot-approvals` and are archived as a compact embed in `#approval-archive`
- Cancelling voids the approval message automatically (greyed out, buttons removed)
- Members are DMed on submission, approval and denial
- Operation reminders DM every approved member and ping `#orbat` before the start
- Availability is re-validated at selection time, so two people can't take the same slot
- A squad called **Reservists** is shown on the ORBAT but left out of the open / pending / filled counts, so a reserve bench doesn't make the operation look under-strength

### 📅 Events

Standalone events with their own sign-ups — trainings, movie nights, campaign sessions. No Google Sheet, no operation required. Full detail in [Events](#events).

| Command | Who | What |
|---|---|---|
| `/event-list` | Everyone | Upcoming events with sign-up counts and jump links |
| `/event-create <title> <start_time>` | Unit Leader+ | Create an event; optional description, duration, location, channel, ping role, reminder, banner image, **repeat interval** and **custom sign-up options** |
| `/event-edit <event>` | Organiser or Admin | Change any field including the sign-up options; moving the start time re-arms the reminder. `repeat:none` stops a series |
| `/event-cancel <event> [reason] [stop_series]` | Organiser or Admin | Cancel it and DM everyone who signed up; the message stays as a record |
| `/event-delete <event>` | Organiser or Admin | Delete it and its message for good, after a confirmation |

**How it behaves**

- Sign-up defaults to three buttons — **✅ Accepted**, **❓ Tentative**, **❌ Declined** — with the attendee list updating live for everyone
- **Custom sign-up options** — replace those three with your own, e.g. `🚁 Pilot | 🔫 Infantry | -❌ Can't`
- Pressing the button you already chose **withdraws** you, which is not the same as declining
- **Repeating events** — seven patterns: daily, weekly, every 2 weeks, monthly by date, monthly by weekday (*last Saturday* or *2nd Saturday*), and weekly-except-the-last-of-the-month; the next one posts itself when the current one ends
- Reminders DM everyone who signed up as coming, plus a channel ping and the event's ping role
- Times render as Discord timestamps, so everyone sees the start in their own local time
- Finished events close themselves out — greyed out, buttons removed, no stray sign-ups
- `event` parameters autocomplete over upcoming events, so nobody types IDs

### 🎮 Game Roles

Permission-free tag roles for games (Minecraft, DCS, …) that members opt into themselves. Full detail in [Game Roles](#game-roles).

| Command | Who | What |
|---|---|---|
| `/game-roles` | Everyone | Pick your own game roles, with the ones you have pre-ticked |
| `/game-role-list` | Everyone | List every game role on the server |
| `/game-role-add <name> [emoji] [description]` | Admin | Create a permission-free game role and make it self-assignable |
| `/game-role-remove <role> [delete_role]` | Admin | Stop a role being self-assignable, optionally deleting it |
| `/game-role-panel [channel]` | Admin | Post the self-assign panel; it updates itself when roles change |

**How it behaves**

- **🎮 Choose your game roles** button — persistent panel button, so members need no command
- Roles the bot creates grant **no permissions** and are mentionable; pre-existing roles are refused if they grant any
- Unit roles and `Unit Leader` can never become game roles, so nobody self-assigns approval rights
- Drop a role by unticking it, or via the **➖ Remove a role** button for a list of only what you have

### ⚙️ Server & Maintenance

| Command | Who | What |
|---|---|---|
| `/set-timezone <tz>` | Admin | Server timezone used when reading any time you type — operations and events alike (default UTC) |
| `/sync` | Admin | Force-sync slash commands with Discord and refresh the ORBAT embed |
| `/restart` | Admin | Restart the bot container on Railway |
| `/debug-slots [squad]` | Admin | Show the raw slot data the bot reads from the sheet, for diagnosing missing slots |
| `/archive-old-approvals` | Admin | One-time migration of pre-existing approved messages into `#approval-archive` |
| `/purge [amount] [since]` | Manage Messages | Delete messages in the channel it is run in — the last *N*, everything since a date or an age, or both |

### Across the whole bot

- **PostgreSQL** — every operation, request, event, sign-up and game role survives restarts and redeployments
- **Buttons survive restarts** — approval buttons, the ORBAT request button, the game-role panel and event sign-ups are all persistent views
- **Commands sync automatically** on startup and when the bot joins a server; `/sync` is only for when something looks missing
- **Role-based access control** — Unit Leaders get extra commands scoped to their own unit
- **Optional [web interface](#web-ui)** — manage events from the browser, signed in with Discord, running inside the same process

---

## Role-Based Access

Grouped by the same four feature areas as [Features](#features) above, so the two sections line up.

### 🗺️ ORBAT & Slots

| Command | Members | Unit Leaders | Admins |
|---|---|---|---|
| `/request-slot`, `/cancel-request`, `/change-slot`, `/leave-operation` | ✅ | ✅ | ✅ |
| `/assign-slot`, `/clear-slot` | ❌ | ✅ (own unit only) | ✅ |
| Approve / Deny in `#slot-approvals` | ❌ | ✅ (own unit only) | ✅ |
| `/setup-slots`, `/post-orbat`, `/set-event-time`, `/post-event`, `/clear-requests`, `/current-operation` | ❌ | ❌ | ✅ |

### 📅 Events

| Command | Members | Unit Leaders | Admins |
|---|---|---|---|
| `/event-list`, signing up to an event | ✅ | ✅ | ✅ |
| `/event-create` | ❌ | ✅ | ✅ |
| `/event-edit`, `/event-cancel`, `/event-delete` | ❌ | ✅ (own events only) | ✅ |

Editing and cancelling go by **who created the event**, not by rank — one Unit Leader cannot change another's event. Admins can change any.

### 🎮 Game Roles

| Command | Members | Unit Leaders | Admins |
|---|---|---|---|
| `/game-roles`, `/game-role-list` | ✅ | ✅ | ✅ |
| `/game-role-add`, `/game-role-remove`, `/game-role-panel` | ❌ | ❌ | ✅ |

### ⚙️ Server & Maintenance

| Command | Members | Unit Leaders | Admins |
|---|---|---|---|
| `/set-timezone`, `/sync`, `/restart`, `/debug-slots`, `/archive-old-approvals` | ❌ | ❌ | ✅ |
| `/purge` | ❌ | ❌ | ✅ (anyone with **Manage Messages** in the channel) |

**Unit roles:** `2nd USC`, `CNTO`, `PXG`, `TFP`, `SKUA`

A **Unit Leader** is any member with the `Unit Leader` Discord role. They can approve/deny requests, assign slots, and manage slots for members who share their unit role. Admins (Manage Server permission) have unrestricted access.

The unit roles and `Unit Leader` can never be turned into game roles — the bot refuses, so members can't self-assign their way into approval rights.

---

## Sheet Format

The bot reads **ORBAT-style sheets**, where each slot is a cell rather than a row
under column headers. It reads the **first tab only**, and the operation name is
the spreadsheet's title.

|   | A                              | B |
|---|--------------------------------|---|
| 1 | **1-1 Alpha**                  |   |
| 2 | 1. Squad Leader                | `[] <Insert Name>` |
| 3 | 2. Rifleman (AR)               | `[TFP] Panzer` |
| 4 | 3. Medic - `[] <Insert Name>`  |   |

- **Squad headers** — any cell that isn't a slot line, e.g. `1-1 Alpha` or
  `Command`. Every slot below it in the same column belongs to it, until the next
  header. Radio-frequency cells (`152 CHN : 1`), headings ending in `:`, and
  sentences are skipped, so they don't become squads by accident.
- **Slot lines** start with a number and a `.` or `-`, e.g. `1. Squad Leader`.
  (`1-1 Alpha` is *not* a slot — a digit after the hyphen means it's a squad id.)
- **Available slots** contain **`<Insert Name>`** — that exact text is what the bot
  looks for. It can be in the same cell as the role, or in a cell up to four
  columns to its right.
- **Filled slots** are `[TAG] Name`, `[] Name`, `Role — Name`, or just a name in
  the cell to the right.
- **On approval** the bot writes `[UNIT] MemberName` in bold; **on clearing** it
  restores `[] <Insert Name>` and removes the unit tag and the bold.

> There is no header-row/column layout — a sheet of `Squad | Role | Status`
> columns is not read. If `/setup-slots` says *"No available slots found"*, the
> sheet has no `<Insert Name>` cells; that exact text is what marks a slot as open.
> `/debug-slots` shows every slot the bot found, with its cell reference.
>
> Share the sheet with your service account email before running `/setup-slots`.

---

## Setup

### 1. Discord Bot

1. Go to [Discord Developer Portal](https://discord.com/developers/applications) → **New Application**
2. Go to **Bot** → **Add Bot** → copy the **Token**
3. Go to **OAuth2 → URL Generator**:
   - Scopes: `bot`, `applications.commands`
   - Bot permissions: `Send Messages`, `Embed Links`, `Read Message History`, `Manage Channels`, `Use Slash Commands`, `Manage Roles`
4. Paste the generated URL in your browser and invite the bot to your server

> **`Manage Roles` is only needed for the game roles feature.** Without it, everything else works fine and the game role commands will tell you what's missing. If you add the permission later, you don't have to re-invite the bot — grant it to the bot's role in **Server Settings → Roles**.

> **Important — command visibility:** After the bot joins, go to **Server Settings → Integrations → ORBATBot → Manage**. Make sure `@everyone` is set to ✅ (allow). If it is set to ❌, all commands will be hidden from regular members regardless of what the bot configures. Admin-only commands are restricted automatically by the bot — you do not need to configure those manually.

### 2. Google Sheets API

1. Go to [Google Cloud Console](https://console.cloud.google.com) → **New Project**
2. Enable the **Google Sheets API** and **Google Drive API**
3. Go to **Credentials → Create Credentials → Service Account**
4. Under the service account → **Keys → Add Key → JSON** — download the file
5. Share each ORBAT sheet with the service account email (found inside the JSON as `client_email`) — give it **Editor** access

### 3. Environment Variables

Copy `.env.example` to `.env` and fill in the three required values:

```
DISCORD_TOKEN=your_bot_token
GOOGLE_CREDENTIALS={...paste entire JSON key file contents here...}
DB_PASSWORD=choose_a_secure_password
```

> `DATABASE_URL` is constructed automatically by docker-compose from `DB_PASSWORD`. On Railway it is injected automatically — you do not set it manually in either case. The only time you fill it in yourself is [running the bot outside Docker](#local-development), which is why `.env.example` carries a commented example of it.

The optional [web interface](#web-ui) adds `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET`, `WEB_SECRET_KEY` and `WEB_BASE_URL`. Leave them empty and the bot behaves exactly as before — no web server is started at all.

---

### 4a. Deploy to Railway

1. Push this repo to GitHub
2. Go to [Railway](https://railway.app) → **New Project → Deploy from GitHub** → select this repo
3. Add a **Postgres** service to your project (Railway dashboard → **+ New** → **Database → PostgreSQL**)
4. In your bot service → **Variables** — add `DISCORD_TOKEN` and `GOOGLE_CREDENTIALS`
   - `DATABASE_URL` is injected automatically from the Postgres service — no manual entry needed
   - *(optional)* `RAILWAY_API_TOKEN` — an account or team token from **Account Settings → Tokens**; lets `/restart` trigger a clean restart via the Railway API. Without it, `/restart` still works by exiting the process so Railway's restart policy relaunches it. Project tokens do not work — the bot authenticates with a Bearer header.
5. Railway will auto-deploy on every push. The `Procfile` tells it to run `python bot.py`

> The database lives in PostgreSQL and persists across all restarts and redeployments. No volume configuration needed.

---

### 4b. Deploy to a VPS with Docker

This is the recommended self-hosted option. You need a Linux VPS with SSH access (Ubuntu 22.04 or similar).

#### Install Docker

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
newgrp docker
```

Verify:

```bash
docker --version
docker compose version
```

#### Clone the repo

```bash
git clone https://github.com/loltorres9/orbatbot.git
cd orbatbot
```

#### Configure environment

```bash
cp .env.example .env
nano .env
```

Fill in `DISCORD_TOKEN`, `GOOGLE_CREDENTIALS`, and `DB_PASSWORD`. Save and exit (`Ctrl+X → Y → Enter`).

#### Start the bot

```bash
docker compose up -d
```

This builds the bot image, starts a PostgreSQL 16 container, and launches the bot. Both containers restart automatically if the VPS reboots.

#### Useful commands

```bash
# View live logs
docker compose logs -f bot

# Stop the bot
docker compose down

# Update to the latest version
git pull
docker compose up -d --build

# Restart the bot only
docker compose restart bot
```

> Bot data is stored in a named Docker volume (`postgres_data`) and survives container restarts, rebuilds, and updates.

---

## Usage

### Members

Available to all server members.

```
/request-slot
```

Opens a squad picker — select your squad first, then choose your slot. You can also click the **📋 Request a Slot** button directly on the ORBAT embed for the same flow. You can only hold one slot per operation.

```
/cancel-request
```

Cancels your pending slot request and frees it for others.

```
/change-slot
```

Forfeits your current slot (pending or approved) and lets you pick a new one via the squad → slot picker. If your slot was approved, it is also cleared from the sheet.

```
/leave-operation
```

Removes you from the operation entirely. Works for both pending and approved slots. If you were approved, your slot is also cleared from the sheet. Shows a confirmation prompt before acting.

```
/game-roles
```

Pick which game roles you want (Minecraft, DCS, …). Unrelated to operation slots — see [Game Roles](#game-roles).

```
/event-list
```

Show upcoming events and sign up to them with the buttons on each event. Unrelated to operation slots — see [Events](#events).

---

### Unit Leaders

Available to members with the **Unit Leader** Discord role. Scoped to their own unit only.

```
/assign-slot @member
```

Directly assigns a member of your unit to a slot — no approval message, no waiting. Uses the same squad → slot picker. The sheet is updated immediately and the member gets a DM. Blocked if the member already holds a slot; use `/clear-slot` first to reassign.

```
/clear-slot
```

Presents a dropdown of active slots. Select one to remove the member and free the slot. The sheet cell is restored to `[] <Insert Name>` (unit tag removed). The member receives a DM.

Unit Leaders only see slots belonging to members of their own unit.

Unit Leaders can also **Approve / Deny** requests in `#slot-approvals` for members of their own unit.

---

### Admins

Available to members with the **Manage Server** permission. Full access with no unit restrictions.

```
/assign-slot @member
```

Directly assigns any member to any slot — no approval message, no waiting. Uses the same squad → slot picker. The sheet is updated immediately and the member gets a DM. Blocked if the member already holds a slot; use `/clear-slot` first to reassign.

```
/setup-slots https://docs.google.com/spreadsheets/d/.../edit
```

Run this once per operation. The previous operation is archived automatically. A live ORBAT embed is posted to `#orbat` (created if it doesn't exist). Optional parameters:

- `event_time` — operation start time in `DD/MM/YYYY HH:MM` or `YYYY-MM-DD HH:MM` format (uses the server's configured timezone)
- `reminder_minutes` — how many minutes before the event to send reminders (default: 30)

```
/set-timezone Europe/Berlin
```

Sets the server's local timezone so event times you type are interpreted correctly. Only needs to be set once. Default is UTC.

```
/set-event-time 25/06/2025 20:00
```

Updates the event time for the current operation without re-running `/setup-slots`. The ORBAT embed and reminder are updated immediately.

```
/post-orbat [#channel]
```

Manually post or re-post the live ORBAT board. Defaults to the current channel.

```
/clear-requests
```

Cancels all pending requests for the current operation (e.g. to reset before an op).

```
/post-event [#channel] [mission_name] [event_time]
```

Posts a formatted event announcement embed with the mission name, start time (as a Discord timestamp with countdown), and a pointer to `#orbat` for sign-ups. All parameters are optional: the channel defaults to the current one, and the mission name and event time default to the active operation's values.

```
/archive-old-approvals
```

One-time migration command. Scans `#slot-approvals` for old bot-posted approved messages (green embeds with an Approved field) and moves them to `#approval-archive`. Creates the archive channel if it doesn't exist. Use this once after upgrading from a version that edited approval messages in place.

```
/debug-slots [squad]
```

Shows the raw slot data the bot reads from the current sheet. Useful for diagnosing why a slot isn't appearing in the picker. Optionally filter by squad name.

```
/current-operation
```

Shows which sheet is currently loaded and links to it.

```
/sync
```

Force-syncs slash commands with Discord and refreshes the live ORBAT embed. Only needed if commands appear missing after a deployment.

```
/purge amount: 50
/purge since: 2h
/purge since: 25/06/2025
/purge amount: 100 since: 7d
```

Deletes messages **in the channel the command is run in**. Give it a count, a
point in time, or both — with both, the count is a cap on the window.

- `amount` — the newest 1–1000 messages
- `since` — an age (`30m`, `2h`, `7d`, `1w`), a date (`25/06/2025`), or a date and time (`25/06/2025 19:00`), read in the server's configured timezone

Nothing is deleted until you confirm: the bot first shows how many messages
match, the time span they cover, and whether any of them are pinned. **Deleted
messages cannot be recovered.**

Discord only allows bulk deletion of messages younger than **14 days**. Anything
older has to be removed one at a time, which is slow, so at most 200 of those go
per run — the reply says how many were left over, and running `/purge` again
continues where it stopped.

Needs **Manage Messages** in that channel, both for you and for the bot. Every
run is logged with the requesting user, and the bulk deletions carry your name
in the server's audit log.

```
/restart
```

Restarts the bot container on Railway — useful if the bot hangs or misbehaves. Two modes:

- **Railway API** (preferred) — if `RAILWAY_API_TOKEN` is set in the service variables, the bot triggers a clean deployment restart via the Railway API
- **Process exit** (fallback) — without a token, the bot exits with a non-zero code and Railway's `ON_FAILURE` restart policy relaunches the container

Either way the bot is back online in ~30–60 seconds. Slots, buttons, and data survive the restart (PostgreSQL + persistent views). The confirmation is ephemeral and every restart is logged with the requesting user.

---

### Approval flow

1. Requested slots appear in `#slot-approvals` (created automatically if it doesn't exist)
2. An admin or Unit Leader from the same unit clicks **✅ Approve** or **❌ Deny**
3. On approval:
   - The Google Sheet is updated
   - The request is deleted from `#slot-approvals`
   - A compact record is posted to `#approval-archive` (created automatically if it doesn't exist)
   - The ORBAT board refreshes
   - The member gets a DM
   - If other members had requested the same slot, they are automatically denied and notified
4. On denial: admin optionally provides a reason; member gets a DM and can request again
5. If a member cancels their request, the approval message is automatically updated to show it was cancelled (greyed out, buttons removed)

**Unit role gating:** Unit Leaders (and admins with a unit role) can only approve/deny requests from members of their own unit. Admins without a unit role can approve any request.

### Approval archive

Every approved slot request is logged to `#approval-archive` as a compact embed showing the operation, unit, member, slot, and approver. The channel is created automatically the first time an approval goes through. To migrate old approved messages that were posted before this feature existed, run `/archive-old-approvals`.

### Event reminders

When an event time is set, the bot automatically:
- DMs every approved member with their slot name and a countdown timestamp
- Posts a ping in `#orbat` tagging all approved members

Reminders fire at the configured window before the event (default 30 minutes). The reminder fires once and will not repeat.

---

## Events

Standalone events with sign-ups — weekly trainings, movie nights, campaign sessions. **Completely separate from the ORBAT slot system:** no Google Sheet, no operation required, and signing up to an event does not touch anyone's slot.

An event is a message with three buttons. Members press one and the attendee list updates live for everyone.

> Built in stages toward full Apollo-style functionality. Already in: sign-ups, reminders, editing, cancelling, automatic close-out, **repeating events** and **custom sign-up options**. Coming next: **sign-up roles with per-role limits**, then waitlist, templates and a calendar view.

### Creating an event (Admins and Unit Leaders)

```
/event-create title:Weekly Training start_time:25/06/2025 19:00
```

Start times use the same format as the rest of the bot — `DD/MM/YYYY HH:MM` or `YYYY-MM-DD HH:MM` — and are read in the server timezone you set with `/set-timezone`. Times in the past are rejected.

Everything else is optional:

| Option | What it does |
|---|---|
| `description` | What the event is about |
| `duration` | Length in minutes; shows an end time and decides when the event counts as finished |
| `location` | Free text — a voice channel, a server name, a map |
| `channel` | Where to post it (defaults to the current channel) |
| `mention` | Role(s) to ping — type `@` and pick as many as you need |
| `reminder` | 15 / 30 / 60 min, 2 h, 24 h before, or no reminder at all |
| `image_url` | A banner image shown on the event |
| `repeat` | Daily, weekly, fortnightly, or monthly by date or weekday — see [Repeating events](#repeating-events) |
| `repeat_until` | Stop repeating after this date |
| `responses` | Your own sign-up buttons — see [Custom sign-up options](#custom-sign-up-options) |

```
/event-edit event:#3 start_time:26/06/2025 20:00
```

Changes only what you pass — everything else keeps its value. Moving the start time re-arms the reminder, so it fires again for the new time. Only the organiser or an admin can edit.

```
/event-cancel event:#3 reason:Server maintenance
```

Marks the event cancelled, greys out the message, removes the buttons and DMs everyone who signed up as coming. The event stays visible as a record rather than vanishing. With custom sign-up options that means every option except the ones marked *not coming*.

On a **repeating** event this cancels only that one occurrence and posts the next one — "this week is off, next week isn't" is the usual case. Add `stop_series: True` to end the whole series instead.

```
/event-delete event:#3
```

Removes the event and its message **for good**, together with every sign-up on it. There's a confirmation step first, because it can't be undone.

**Cancel or delete?**

| | `/event-cancel` | `/event-delete` |
|---|---|---|
| The message | Stays, greyed out as a record | Removed from the channel |
| People who signed up | Get a DM | **Are not told** |
| Sign-ups | Kept | Deleted with the event |
| Good for | An event that was real but isn't happening | Test events, typos, clutter |

If anyone is signed up to a scheduled event, the confirmation says so and points you at `/event-cancel` instead. Its autocomplete also lists cancelled and finished events — those are usually the ones you want to tidy away.

All these commands autocomplete: start typing and pick the event from the list instead of remembering its number.

### Repeating events

```
/event-create title:Weekly Training start_time:25/06/2025 19:00 repeat:Weekly
```

Seven patterns to choose from. Optionally add `repeat_until:31/12/2025 23:59` to end the series on a date.

| `repeat` | Meaning | Example series |
|---|---|---|
| **Daily** | Every day | |
| **Weekly** | Same weekday every week | |
| **Every 2 weeks** | Same weekday, fortnightly | |
| **Monthly — same date** | Same day number each month | 15 Jun → 15 Jul → 15 Aug |
| **Monthly — last weekday** | **Last <weekday> of each month** | 27 Jun → 25 Jul → 29 Aug → 26 Sep |
| **Monthly — same weekday** | Same weekday *position* each month | 2nd Sat: 13 Jun → 11 Jul → 8 Aug |
| **Weekly — except the last one of the month** | Every week, but **skipping** the last `<weekday>` | 6 Jun → 13 Jun → 20 Jun → *(skips 27)* → 4 Jul |

The weekday and the position both come from **the start time you give it**. So for a monthly op on the last Saturday:

```
/event-create title:Monthly Op start_time:27/06/2026 19:00 repeat:Monthly — last weekday
```

27 June 2026 is a Saturday, so the series runs on the last Saturday of every month from then on — 25 Jul, 29 Aug, 26 Sep, 31 Oct, and so on, always at 19:00. If the date you pick is *not* itself the last Saturday of its month, the bot says so when creating the event, so the shift doesn't surprise you later.

#### Weekly ops around a monthly one

**Weekly — except the last one of the month** is the counterpart: every Saturday *apart from* the last one. Pair the two and you get a monthly op on the last Saturday with weekly ops on all the others, with no double-booking:

```
/event-create title:Monthly Op start_time:27/06/2026 19:00 repeat:Monthly — last weekday
/event-create title:Weekly Op  start_time:06/06/2026 19:00 repeat:Weekly — except the last one of the month
```

The weekly series runs 6, 13, 20 June, skips the 27th, then 4, 11, 18 July, skips the 25th, and so on. It adapts to the month: in a month with four Saturdays it runs three times, in a month with five it runs four times — always every Saturday but the last.

Note this is *not* the same as picking the 1st through 4th Saturdays separately. In a four-Saturday month the 4th Saturday **is** the last one, so that approach would double-book your monthly op.

Only ever **one occurrence exists at a time**. When the current one finishes, the bot posts the next automatically in the same channel, with the same description, duration, location, ping role and reminder. Sign-ups start fresh each time — nobody is carried over, so an "Accepted" always means someone answered for *that* date.

To change or stop a series, use `/event-edit`:

```
/event-edit event:#3 repeat:Monthly      # change the interval
/event-edit event:#3 repeat:Don't repeat # stop after this occurrence
```

Three details worth knowing:

- **Monthly keeps its day.** A "same date" series on the 31st becomes the 28th in February and then goes *back* to the 31st in March — it doesn't get stuck on the 28th. The same applies to leap years.
- **A "5th weekday" series skips months that don't have one.** Pick the 5th Saturday and you get only the months that actually have five — the series doesn't end and doesn't silently slide to the 4th.
- **Downtime doesn't produce a backlog.** If the bot is offline for a month, it does not post the missed occurrences on startup. It posts the next one that is actually still ahead.

### Signing up (Members)

Press one of the buttons on the event. Unless the organiser set [custom options](#custom-sign-up-options), they are:

- **✅ Accepted** — you're coming
- **❓ Tentative** — you might be
- **❌ Declined** — you can't make it

Pressing a different button changes your answer. **Pressing the button you already chose withdraws you** and takes you off the list entirely — which is not the same as declining. The footer on every event says so.

`/event-list` shows all upcoming events with sign-up counts and a jump link to each one.

### Pinging roles

`mention` takes **as many roles as you need**, not just one:

```
/event-create title:Joint Op start_time:25/06/2026 19:00 mention:@2nd USC @CNTO @TFP
```

Type `@` in the field and pick them from the list — Discord turns each into a proper mention. All of them get pinged when the event is posted and again when the reminder fires. Up to 10 roles.

If you'd rather type them, comma-separated names work too: `mention:2nd USC, CNTO`. Anything the bot can't find is reported back instead of being quietly ignored.

To change or remove them later:

```
/event-edit event:#3 mention:@2nd USC @PXG   # replace the list
/event-edit event:#3 mention:none            # stop pinging anyone
```

> A role only actually notifies people if it's **mentionable**, or if the bot has **Mention All Roles**. The bot warns you when you pick a role that isn't.

### Custom sign-up options

The three default buttons don't suit every event. Give `responses` your own list, separated by `|`:

```
/event-create title:Air Assault start_time:25/06/2026 19:00 responses:🚁 Pilot | 🔫 Infantry | ❓ Maybe | -❌ Can't make it
```

That event gets four buttons instead of three, and the attendee list on the message is grouped by exactly those options.

- **An emoji at the start of an entry** becomes the button's icon. It's optional — `Pilot | Infantry` works too.
- **A leading `-` marks "not coming".** Those people are left out of reminders and cancellation DMs, the way *Declined* always has been. At least one option has to mean *coming*.
- Between **2 and 10** options, labels under 40 characters.
- Leave `responses` out and you keep the usual **✅ Accepted / ❓ Tentative / ❌ Declined**.

Everything else behaves the same: pressing the button you already picked withdraws you, and a repeating event carries its options to every future occurrence.

To change the options later:

```
/event-edit event:#3 responses:✅ In | -❌ Out
```

If anyone had already signed up with an option you removed, their answer is cleared and the bot tells you how many — they need to answer again. Options you keep are unaffected.

### Reminders and close-out

When the reminder window is reached, everyone who signed up as coming gets a DM, and the event's channel gets a ping — including the `mention` roles if any were set. People who declined are left alone, and so is anyone who picked a custom option marked *not coming* with a leading `-`. The reminder fires once.

Times always display as Discord timestamps, so **everyone sees the start in their own local time** without configuring anything.

Once an event's start time — plus its duration, if set — has passed, the bot marks it finished, greys out the message and removes the buttons, so old events can't collect stray sign-ups. If the event repeats, that is also the moment the next one goes up.

---

## Game Roles

Self-assignable tag roles for games — Minecraft, DCS, Squad, whatever your members play. They are completely separate from the slot and ORBAT system, and they exist for one purpose: so anyone can `@mention` everyone who plays a given game.

**These roles never grant permissions.** Roles the bot creates are created with no permissions at all and are mentionable by everyone. If you point the bot at a role that already exists, it checks it first and refuses when the role:

- grants any server permission at all
- is `@everyone`, or is managed by an integration (bot roles, the Nitro booster role)
- is a unit role or `Unit Leader` — otherwise a member could self-assign their way into slot-approval rights
- sits at or above the bot's own role, which Discord won't let it assign

### Setting them up (Admins)

```
/game-role-add name:Minecraft emoji:⛏️ description:Vanilla and modded
```

Creates a permission-free, mentionable role called `Minecraft` and makes it self-assignable. `emoji` and `description` are optional and only affect how the role looks in the picker.

If a role with that **exact name** already exists, it is reused rather than duplicated — so you can make your existing game roles self-assignable without recreating them. Running the command again for the same name just updates the emoji and description. You can have up to **25** game roles, which is as many as a Discord menu can show.

```
/game-role-panel #game-roles
```

Posts the panel: an embed listing every game role plus a **🎮 Choose your game roles** button. This is the normal way members opt in — no command to remember. There is one panel per server, and it updates itself whenever you add or remove a game role. The button keeps working after a bot restart.

```
/game-role-remove role:Minecraft
```

Stops the role being self-assignable. By default the Discord role itself stays and members who have it keep it — pass `delete_role: True` to delete it outright, which removes it from everyone. If you delete a game role in Discord directly, the bot drops it from its own list the next time it reads them.

### Picking them (Members)

Click **🎮 Choose your game roles** on the panel, or run:

```
/game-roles
```

Both open the same private menu listing every game role, with the ones you already have **already ticked**. Tick the games you play, untick the ones you don't, and submit — your game roles are set to exactly what you left selected. Deselecting everything is valid and removes all of them.

**To drop a role,** either untick it in that menu, or press **➖ Remove a role** for a shorter list containing only the roles you currently have — pick one or several and they are removed. The button only appears when you actually have a game role to give up.

Only the roles that actually changed are touched, and you get a short summary of what was added and removed. `/game-role-list` shows the available roles without changing anything.

### Requirements

The bot needs the **Manage Roles** permission, and its own role must sit **above** the game roles in **Server Settings → Roles** — Discord does not let a bot hand out roles ranked at or above its own. If either is missing, the bot says so with the specific fix instead of failing silently.

No privileged intents are needed for this feature.

---

## Web UI

An optional browser interface for **events**: create them, edit them, cancel them, delete them and see who signed up — without touching a slash command. You sign in with your Discord account, and everything you do produces the same message, with the same buttons, in the same channel as `/event-create` would.

It is **off until you configure it**. With `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET` and `WEB_SECRET_KEY` unset, the bot starts exactly as it always did and opens no HTTP port. The startup log says which of the three is missing.

### What it does

| Page | What you get |
|---|---|
| Sign-in | **Continue with Discord** — OAuth2, `identify` scope only |
| Server picker | Every server you and the bot are both in (skipped when there is only one) |
| Events | Upcoming events with live sign-up counts, plus recently finished and cancelled ones |
| Event | Full details, the attendee list per response, and RSVP buttons for yourself |
| New / Edit | Title, start, duration, description, location, channel, ping roles, reminder, repeat pattern, custom sign-up buttons, banner image |
| Cancel | Reason field, DMs everyone attending, optionally stops the whole series |
| Delete | Confirmation page stating the sign-up count, then removes the event and its message |
| ORBATs | Build and edit the slot roster in the browser — squads, slots, and a preview of the board |
| Game roles | Tick the games you play; admins add and remove roles and post the self-assign panel |
| Embeds | Build rich messages, post them, and edit the posted message in place |
| Member log | Announce joins, leaves, kicks, bans and unbans in a channel |
| Voice time | Leaderboard of time spent in voice channels; admins configure what counts |

Who may do what is **read live from your Discord roles**, not from the login:

- **Any member of the server** — view events, RSVP, pick their own game roles, see the voice leaderboard
- **Unit Leader or Manage Server** — create events
- **The organiser, or an admin** — edit, cancel and delete that event
- **Manage Server** — add and remove game roles, post the self-assign panel, build embeds, build ORBATs, configure the member log and voice tracking

That is the same rule set the slash commands use; it is literally the same code. Roles are cached for a minute, so if you have just been given a role, the **“Changed your roles on Discord? Re-read them”** link at the bottom of the event list picks it up immediately.

Times are entered and displayed in the **server timezone** (`/set-timezone`), the same as every time you type into a slash command.

### Setting it up

**1. Create the OAuth2 credentials.** In the [Discord Developer Portal](https://discord.com/developers/applications) → your application → **OAuth2**:

- copy the **Client ID** and generate a **Client Secret**
- under **Redirects**, add `https://your-domain/auth/callback` — it must match `WEB_BASE_URL` exactly, including `https://` and with no trailing slash

> **Already have a Discord OAuth2 app?** Reuse it. Take its existing client ID and secret, and just add `https://your-domain/auth/callback` as an *additional* redirect URI — Discord allows several per application and the existing ones keep working. Do **not** regenerate the client secret if that app is used elsewhere; that would break the other integration. It doesn't even have to be the bot's own application: membership and roles are read through the bot's connection, not through the user's token, so any application works — only the name on the consent screen changes. Using the bot's application is still the tidiest.

**2. Generate a session key.**

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Changing this key later signs everybody out; nothing else is lost.

**3. Set the variables** on Railway (service → **Variables**) or in `.env` for Docker:

```
DISCORD_CLIENT_ID=your_application_id
DISCORD_CLIENT_SECRET=your_client_secret
WEB_SECRET_KEY=the_random_string_from_step_2
WEB_BASE_URL=https://orbat.example.com
```

**4. Expose it.**

- **Railway** — service → **Settings → Networking → Generate Domain**. Railway injects `PORT` and the app listens on it; use that domain as `WEB_BASE_URL`.
- **Docker** — port `8080` is published by `docker-compose.yml`; put it behind a reverse proxy that terminates TLS (Caddy or nginx), and point `WEB_BASE_URL` at the public name.

Restart the bot. The log line `✅ Web UI listening on …` means it is up; `/healthz` answers `ok` once the bot is connected to Discord.

### Name and logo

The site is called **TFP BOT**. Set `WEB_BRAND` to rename it — that string is the header, the browser tab title and the footer.

For the logo, commit an image to **`web/static/logo.png`** (`.webp`, `.svg`, `.jpg` also work). It is picked up on the next start and appears next to the name in the header, large on the sign-in page, as the browser-tab icon, and — when `WEB_BASE_URL` is set — as the preview image when the site's link is pasted into Discord or Slack. Square images look best; anything else is fitted rather than squashed. With no such file, the name shows on its own and the tab falls back to a 🛡️ emoji — nothing breaks.

### How it is wired

The site runs **inside the bot process**, on the same event loop. That is why a page can post a message, register a persistent button and read a member's roles directly — there is no second service, no polling and no queue table, and one Railway service still runs everything.

The session is a signed cookie, so there is no session table and a redeploy doesn't sign anyone out. It holds nothing but your user id, display name, avatar hash and a CSRF token; the Discord access token is used once to read your profile and then discarded. Every permission decision is made fresh from the bot's live view of the guild.

### Game roles in the browser

The **🎮 Game roles** tab does everything the slash commands do. Members get one page with every game role, the ones they already have ticked; saving sets their roles to exactly what is ticked, and unticking removes. Admins additionally get an add form (name, emoji, description — an existing role with that exact name is reused, never duplicated), a remove button per role with an optional *delete the Discord role too*, and a channel picker for the self-assign panel.

Every rule the commands enforce applies unchanged: roles with permissions, `@everyone`, integration-managed roles, the unit roles and `Unit Leader` are all refused, the cap is 25, and the panel message refreshes itself after every change. Roles submitted that aren't registered game roles are ignored, so the form can't be used to hand yourself something else. If the bot is missing **Manage Roles**, the page says so and the controls are disabled rather than failing on submit.

### Embeds

**📝 Embeds** is a builder for the rich messages you'd otherwise write by hand — server info, rules, announcements. Title, description, colour, up to ten fields (optionally side by side), author line, thumbnail, image, footer, timestamp, and plain text above the embed for pings. A preview shows roughly what Discord will make of it.

Each embed is saved with a name and starts as a draft. Posting it records which message it became, and **saving an edit afterwards updates that message in place** — no delete-and-repost, so pinned server info stays pinned. Posting again sends a new message and stops tracking the old one, which is how you move an embed to another channel. Deleting offers to remove the Discord message with it.

Discord's limits are checked when you save, not when you post: an over-long title or a 6000-character total is refused on the form rather than becoming a saved embed that can never be sent. Image and icon fields must be full `https://` URLs.

### Member log

**📋 Member log** posts an announcement when someone joins, leaves, is kicked, banned or unbanned. Pick a channel, tick which events you want, save.

- **Joins** show the account's age (a fresh account is worth a second look), the member count, and **which invite link was used** — including who created that link and, if you've labelled it, **where that link was published**.
- **Leaves** show how long the member was around and which roles they had.
- **Kicks** are told apart from voluntary leaves through the audit log, and name the moderator and reason. Bans and unbans do the same.

Three prerequisites, and the page tells you which are missing:

1. **Joins and leaves need Discord's privileged Server Members Intent.** Tick it under your application → **Bot → Privileged Gateway Intents** in the Developer Portal, then set `MEMBER_EVENTS=1` in the bot's variables and redeploy. **In that order** — asking for the intent before it is granted stops the bot from starting at all, which is why it is off by default. Bans and unbans work without it.
2. **View Audit Log**, or a kick can't be told from someone leaving and bans won't name the moderator.
3. **Manage Server**, or the invite list can't be read and joins won't say which link was used.

**Label your invite links** at the bottom of the same page: the invite list is shown with its use counts, and next to each one a free-text field — *Steam*, *Website*, *Reddit*, whatever you use it for. A join through that link then reads `rnPAfscGbE · Steam` instead of just the code, so nobody has to look it up in a spreadsheet. Labels for links that have since expired are kept and stay editable, because old joins still refer to them.

Invite attribution works by comparing each invite's use counter before and after a join. Two people joining in the same second can't be told apart that way, and a member added by another bot has no invite at all — those simply show no link.

### Voice time

**🔊 Voice time** records how long members spend in voice channels and shows a leaderboard for the last 24 hours, 7, 30 or 90 days, or all time, plus which channels see the most use. Every member can see it; only admins can change the settings.

**It is off until an admin switches it on.** Nothing is recorded before that.

What counts is deliberately narrow by default: time is only counted while **at least two people share a channel**, and the AFK channel is skipped. Counting pauses the moment somebody is left alone and resumes when somebody joins them, so what you measure is time actually spent together rather than time connected. Both rules can be switched off, and individual voice channels can be excluded.

Optionally, a finished visit is announced in a channel of your choice — with a minimum length, so quick drop-ins don't fill it up. Leave the channel unset to keep the statistics without any messages.

**A daily board.** Switch on *Keep a daily top-10 message up to date*, pick a channel, a period and an hour: the bot posts one message and **edits that same message once a day**, so you can pin it and it always shows current standings. It never posts twice in a day, and if the bot was down at the chosen hour it catches up as soon as it is back rather than skipping. Moving it to another channel posts a fresh message there.

**Posting once.** Separately, *Post the top 10 once* sends the leaderboard as a one-off message for a period you choose. Either way members are named rather than mentioned, so nobody gets pinged.

Two details worth knowing about the numbers:

- **A redeploy doesn't lose time.** Open sessions are closed cleanly when the bot shuts down, so a normal restart records everything up to that moment.
- **A hard crash costs at most five minutes.** Open sessions carry a heartbeat that is refreshed every five minutes; on the next start, anything left open is closed at its last heartbeat. The time is never rounded up — an interval with no heartbeat counts zero.

Voice state updates need no privileged intent and no extra permission, so unlike the member log this works the moment you switch it on.

### ORBATs

The slot roster, written out on a page instead of kept in a Google Sheet. **Manage Server** only, under the **🗺️ ORBATs** tab.

An ORBAT is a **template**: the same one is meant to back as many operation nights as you like, which is what duplicating the sheet used to be for. **Copy** takes the structure and none of the bookings.

You write the roster as indented text — squad at the left margin, its slots indented under it:

```
1-0 Platoon HQ  | left
  Platoon Leader
  Platoon Sergeant

1-1 Alpha  | left, unit:TFP, radio:343 CHN:3
  Squad Leader
  Team Leader
  Automatic Rifleman
  Rifleman

Reservists  | right, nocount
  Reserve
```

Everything after the pipe belongs to the **squad**:

- `left` / `right` put it in that column of the board
- `unit:TFP` marks the whole squad as one unit's — the tag is the unit's role name, and you get a warning if it matches none of them
- `radio:343 CHN:3` is the channel that squad talks on internally, shown under its name on the board
- `nocount` leaves it out of the open/filled counts (what `Reservists` gets today)

A line starting with `#` is a comment, and a leading number — `1. Rifleman` — is removed, so lines pasted straight out of a sheet work.

Under the roster there is a second, smaller box for the **radio nets** everyone shares — the platoon net, logistics, air, high command. One per line:

```
Platoon Net   | 152 CHN : 1
Logi          | 152 CHN : 2
-Air Net      | 152 CHN : 3
High Com Net  | 152 CHN : 4
```

A line starting with `-` is a net that exists in the plan but is not in use this time; it shows struck through, the way you would cross it out on paper.

**Preview** shows the board exactly as Discord would render it, without saving. It also warns you before you hit a limit Discord enforces silently: more than 8 rows of squads, a squad too long for one field, or an embed over 6000 characters. **Save** writes it.

Editing an ORBAT never quietly drops anyone. Renaming a slot keeps whoever is booked into it; reordering lines changes nothing at all. Anything that would take someone off the roster — or move them onto a differently named role — stops at a confirmation page that names them and which operation they are in, and you have to click **Save anyway**.

> **Not connected to slot requests yet.** You can build and maintain ORBATs here, but `/request-slot` and the live board in `#orbat` still read the Google Sheet. Wiring the two together is the next step.

### Limits

- ORBATs can be built here, but booking a slot into one is not wired up — `/request-slot` and the `#orbat` board still use the sheet
- An event can't be moved to another channel after posting; cancel it and create a new one
- Approving slot requests is unchanged and still happens in `#slot-approvals`

---

## Local Development

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # fill in your values
python bot.py
```

You will need a PostgreSQL instance running locally and `DATABASE_URL` set in your `.env` — `.env.example` has an entry for it, since this is the one setup where neither Railway nor docker-compose provides it. No manual command sync is needed — the bot syncs slash commands to all guilds automatically on startup.

To work on the [web UI](#web-ui) locally, add `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET` and `WEB_SECRET_KEY` to your `.env`, leave `WEB_BASE_URL` empty so the callback URL is taken from the request, and register `http://localhost:8080/auth/callback` as a redirect URI in the Developer Portal. `WEB_PORT` changes the port. The bot has to be in a server with you for anything to show up.

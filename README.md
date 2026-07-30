# ORBATBot

A Discord bot for managing Arma 3 operation slot requests. Members request slots via a two-step squad → slot picker or the **📋 Request a Slot** button on the ORBAT embed; admins and Unit Leaders approve or deny requests with a button click, and the Google Sheet is updated automatically.

It also manages **self-assignable game roles** — permission-free tag roles for games like Minecraft or DCS that members opt into themselves, so you can `@mention` everyone who plays a given game. See [Game Roles](#game-roles).

And it runs **standalone events** with sign-ups — trainings, movie nights, anything — where members answer Accepted / Tentative / Declined on a button and get reminded before the start. No Google Sheet involved. See [Events](#events).

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

### 📅 Events

Standalone events with their own sign-ups — trainings, movie nights, campaign sessions. No Google Sheet, no operation required. Full detail in [Events](#events).

| Command | Who | What |
|---|---|---|
| `/event-list` | Everyone | Upcoming events with sign-up counts and jump links |
| `/event-create <title> <start_time>` | Unit Leader+ | Create an event; optional description, duration, location, channel, ping role, reminder and banner image |
| `/event-edit <event>` | Organiser or Admin | Change any field; moving the start time re-arms the reminder |
| `/event-cancel <event> [reason]` | Organiser or Admin | Cancel it and DM everyone who signed up |

**How it behaves**

- Sign-up is three buttons — **✅ Accepted**, **❓ Tentative**, **❌ Declined** — with the attendee list updating live for everyone
- Pressing the button you already chose **withdraws** you, which is not the same as declining
- Reminders DM everyone who accepted or was tentative, plus a channel ping and the event's ping role
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

### Across the whole bot

- **PostgreSQL** — every operation, request, event, sign-up and game role survives restarts and redeployments
- **Buttons survive restarts** — approval buttons, the ORBAT request button, the game-role panel and event sign-ups are all persistent views
- **Commands sync automatically** on startup and when the bot joins a server; `/sync` is only for when something looks missing
- **Role-based access control** — Unit Leaders get extra commands scoped to their own unit

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
| `/event-edit`, `/event-cancel` | ❌ | ✅ (own events only) | ✅ |

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

**Unit roles:** `2nd USC`, `CNTO`, `PXG`, `TFP`, `SKUA`

A **Unit Leader** is any member with the `Unit Leader` Discord role. They can approve/deny requests, assign slots, and manage slots for members who share their unit role. Admins (Manage Server permission) have unrestricted access.

The unit roles and `Unit Leader` can never be turned into game roles — the bot refuses, so members can't self-assign their way into approval rights.

---

## Sheet Format

Your Google Sheet needs at minimum **two columns** with recognisable headers:

| Squad / Unit | Role / Position | Status    | Assigned To |
|--------------|-----------------|-----------|-------------|
| Squad 1      | Squad Lead      | Available |             |
| Squad 1      | Rifleman (AR)   | Available |             |
| Squad 1      | Medic           | Available |             |
| Squad 2      | Squad Lead      | Available |             |

- **Squad / Unit** — the group name (header must contain: squad, unit, element, group, platoon, team, section, or callsign)
- **Role / Position** — the slot name (header must contain: role, position, slot, job, rank, or billet)
- **Status** *(optional)* — rows where this is not `Available`, `Open`, `Free`, or blank are hidden from the menu
- **Assigned To** *(optional)* — rows with a value here are treated as already taken

The bot also supports **ORBAT-style sheets** where slots appear as cell values (e.g. `1. Squad Lead`). Available slots contain `<Insert Name>`; filled slots use formats like `[TAG] Name` or `[] Name`. When a slot is cleared the unit tag is removed and the cell is restored to `[] <Insert Name>`.

> You don't have to rename your columns exactly — the bot looks for keywords anywhere in the header cell.
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

> `DATABASE_URL` is constructed automatically by docker-compose from `DB_PASSWORD`. On Railway it is injected automatically — you do not set it manually in either case.

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

> This is the first stage of a staged build toward full Apollo-style functionality. Already in: sign-ups, reminders, editing, cancelling, automatic close-out. Coming next, in this order: **sign-up roles with per-role limits**, **recurring events**, then waitlist, templates and a calendar view.

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
| `mention` | A role to ping when the reminder fires |
| `reminder` | 15 / 30 / 60 min, 2 h, 24 h before, or no reminder at all |
| `image_url` | A banner image shown on the event |

```
/event-edit event:#3 start_time:26/06/2025 20:00
```

Changes only what you pass — everything else keeps its value. Moving the start time re-arms the reminder, so it fires again for the new time. Only the organiser or an admin can edit.

```
/event-cancel event:#3 reason:Server maintenance
```

Marks the event cancelled, greys out the message, removes the buttons and DMs everyone who accepted or was tentative. The event stays visible as a record rather than vanishing.

Both commands autocomplete: start typing and pick the event from the list instead of remembering its number.

### Signing up (Members)

Press one of the three buttons on the event:

- **✅ Accepted** — you're coming
- **❓ Tentative** — you might be
- **❌ Declined** — you can't make it

Pressing a different button changes your answer. **Pressing the button you already chose withdraws you** and takes you off the list entirely — which is not the same as declining. The footer on every event says so.

`/event-list` shows all upcoming events with sign-up counts and a jump link to each one.

### Reminders and close-out

When the reminder window is reached, everyone who accepted or was tentative gets a DM, and the event's channel gets a ping — including the `mention` role if one was set. People who declined are left alone. The reminder fires once.

Times always display as Discord timestamps, so **everyone sees the start in their own local time** without configuring anything.

Once an event's start time — plus its duration, if set — has passed, the bot marks it finished, greys out the message and removes the buttons, so old events can't collect stray sign-ups.

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

## Local Development

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # fill in your values
python bot.py
```

You will need a PostgreSQL instance running locally and `DATABASE_URL` set in your `.env`. No manual command sync is needed — the bot syncs slash commands to all guilds automatically on startup.

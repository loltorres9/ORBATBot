# ORBATBot

A Discord bot for managing Arma 3 operation slot requests. Members request slots via a two-step squad → slot picker or the **📋 Request a Slot** button on the ORBAT embed; admins and Unit Leaders approve or deny requests with a button click.

The roster an operation runs on lives **either in the bot's own database** — built and edited in the browser under [ORBATs](#orbats) — **or in a Google Sheet**, the way it always did. You pick one per operation; everything else behaves the same.

It also manages **self-assignable game roles** — permission-free tag roles for games like Minecraft or DCS that members opt into themselves, so you can `@mention` everyone who plays a given game. See [Game Roles](#game-roles).

And it runs **standalone events** with sign-ups — trainings, movie nights, anything — where members answer Accepted / Tentative / Declined on a button, or whatever options the organiser defined, and get reminded before the start. No Google Sheet involved. See [Events](#events).

**Looking for how to do one particular thing?** [How-tos](#how-tos) has a short recipe for each — and the bot serves the same text at **`/help`**, behind the **?** in the site header.

Much of this can also be done from a **browser** instead of slash commands — events, the slot roster, slot approvals, game roles, embeds and the logs: an optional web interface with Discord login, running inside the same bot process. See [Web UI](#web-ui).

---

## Features

Three independent feature areas plus maintenance commands. **ORBAT & Slots** is the Arma operation system, running on an ORBAT you build in the browser or on a Google Sheet; **Events** and **Game Roles** work on their own and never involve either.

In the **Who** column, *Unit Leader+* means Unit Leaders and Admins, and Unit Leaders are scoped to their own unit. See [Role-Based Access](#role-based-access) for the full matrix.

### 🗺️ ORBAT & Slots

Operation slot requests, approvals and the live ORBAT board.

| Command | Who | What |
|---|---|---|
| `/request-slot` | Everyone | Open the squad → slot picker for the current operation |
| `/cancel-request` | Everyone | Cancel your pending slot request |
| `/change-slot` | Everyone | Forfeit your current slot and pick a new one |
| `/leave-operation` | Everyone | Remove yourself from the operation entirely (pending or approved) |
| `/assign-slot <member>` | Unit Leader+ | Assign a member to a slot directly, bypassing approval |
| `/clear-slot` | Unit Leader+ | Remove a member from a slot; on a sheet-backed operation the cell is restored and the unit tag stripped |
| `/setup-slots` | Admin | Start an operation from an ORBAT or a Google Sheet; optional event time and reminder; auto-posts the ORBAT to `#orbat` |
| `/post-orbat [channel]` | Admin | Post or re-post the live ORBAT board |
| `/set-event-time <time>` | Admin | Update the operation's start time and reminder |
| `/post-event [channel] [mission_name] [event_time]` | Admin | Post an announcement embed for the operation, pointing at `#orbat` for sign-ups |
| `/clear-requests` | Admin | Cancel all pending requests for the current operation |
| `/current-operation` | Admin | Show which operation is active, and which ORBAT or sheet it runs on |

> `/post-event` only *announces* the active operation — sign-up still happens through ORBAT slots. For a standalone event with its own attendee list, use [`/event-create`](#events) instead.

**How it behaves**

- **Two-step slot picker** — squad first, then slot. Used by `/request-slot`, `/change-slot`, `/assign-slot` and the ORBAT button alike
- **📋 Request a Slot** button — persistent button on the live ORBAT embed, so no command is needed
- Slots show 🟢 available, 🟡 pending (also requested — compete for it) or 🔴 filled, in real time
- Several members can request the same slot; the approver picks, and the rest are auto-denied and notified
- Approvals happen in `#slot-approvals` with **Approve / Deny** buttons and a denial modal for an optional reason —
  or on the [web interface](#slot-approvals), which does exactly the same thing
- Actioned requests leave `#slot-approvals` and are archived as a compact embed in `#approval-archive`
- Those three channel names are the **defaults** — an admin can point each one somewhere else on the [Operation page](#channels)
- Cancelling voids the approval message automatically (greyed out, buttons removed)
- Members are DMed on submission, approval and denial
- Operation reminders DM every approved member and ping `#orbat` before the start
- Availability is re-validated at selection time, so two people can't take the same slot
- A squad left out of the counts is still shown on the board, so a reserve bench doesn't make the operation look under-strength. On an ORBAT that is the `nocount` option; on a sheet it is a squad called **Reservists**
- On an ORBAT-backed operation the board also shows each squad's unit and radio channel, and the shared radio nets underneath

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

### 🗺️ Tactical maps

Draw the plan on the terrain and share it with a link — unit symbols, movement lines, objectives and boundaries, in the browser. No slash command: the whole thing lives in the [web interface](#tactical-maps).

- **Proper APP-6 symbols** — a blue rectangle for friendly, a red diamond for hostile, a green square for neutral, a yellow quatrefoil for unknown, with infantry, recon, armour, mechanised, mortars, artillery, medical, engineers, supply, aircraft and vehicles on them, **size marks above the frame** (Ø team · • squad · ••• platoon · I company · II battalion) and `(+)` / `(-)` beside them — plus Arma's own `mil_*` task markers for objectives, rally points and the rest
- **The frame says where and whether, not just whose** — an open-bottomed frame for anything in the air, an open-topped one for subsurface, and a **dashed** frame for what is only planned, with the mobility indicator (wheeled, tracked, towed) under it and the parent unit beside the size mark
- **Movement lines, areas, markers and free text** — drawn by dragging, the way Arma's own map does it, in any of ten colours; an arrow head goes on the lines that mean a direction rather than on all of them
- **Your actual terrain as the background** — upload the same tile archive you prepare for [OCAP](https://github.com/OCAP2/OCAP) (zip or 7z) and the bot serves it itself, or point it at a running OCAP server; either way the Arma coordinates come along with the terrain
- **A share link** anyone can open without signing in, read-only or with drawing rights, and **replaceable** the moment it has travelled further than intended
- **Straight into Arma 3** — the whole plan as markers in a running mission, pasted into the debug console, no mod on either side. Two scripts side by side: one whose markers stay put, and one whose markers can be dragged and deleted in game, in whichever map channel you pick
- **Post it in a channel** as a link, so it stays current while the plan is still being drawn
- Works read-only with JavaScript off, because the map is rendered on the server as well

### 📣 Reddit announcements

Watch a Reddit user or a subreddit and announce every new post in a Discord channel, in your own words, pinging the roles and people you choose. Set up entirely in the [web interface](#reddit-announcements) — there is no slash command, and no Reddit API registration either. Full detail in [Reddit announcements](#reddit-announcements).

- Watches a **user's posts** (`u/TaskForcePhalanx`) or a **subreddit's** new posts (`r/arma`)
- Your own message text, with `{title}`, `{url}`, `{author}` and `{subreddit}` filled in
- Pings any roles you tick, plus any members you list
- Checked every 5 minutes; the first check notes what is already there so switching a watch on never dumps old posts into the channel
- Handles Reddit turning a cloud-hosted bot away: retries on `old.reddit.com`, then waits it out instead of hammering
- **Announcements only** — the bot will not ask anyone to upvote, because [organised voting gets accounts banned](#a-word-on-upvotes)

### ⚙️ Server & Maintenance

| Command | Who | What |
|---|---|---|
| `/set-timezone <tz>` | Admin | Server timezone used when reading any time you type — operations and events alike (default UTC) |
| `/sync` | Admin | Force-sync slash commands with Discord and refresh the ORBAT embed |
| `/restart` | Admin | Restart the bot container on Railway |
| `/debug-slots [squad]` | Admin | Show the raw slot data the bot reads, whichever roster is in use, for diagnosing missing slots |
| `/archive-old-approvals` | Admin | One-time migration of pre-existing approved messages into `#approval-archive` |
| `/purge [amount] [since]` | Manage Messages | Delete messages in the channel it is run in — the last *N*, everything since a date or an age, or both |

### Across the whole bot

- **PostgreSQL** — every operation, request, event, sign-up and game role survives restarts and redeployments
- **Buttons survive restarts** — approval buttons, the ORBAT request button, the game-role panel and event sign-ups are all persistent views
- **Commands sync automatically** on startup and when the bot joins a server; `/sync` is only for when something looks missing
- **Role-based access control** — Unit Leaders get extra commands scoped to their own unit
- **Optional [web interface](#web-ui)** — manage events, the ORBAT and slot approvals from the browser, signed in with Discord, running inside the same process
- **A built-in help page** — every how-to in [How-tos](#how-tos) is also served at `/help`, with a **?** in the header and another beside each page's tabs that opens that page's own instructions

---

## How-tos

<!-- help:start -->

<!-- Generated from utils/help.py by scripts/gen_help.py.
     Edit the topics there, not this section: the bot serves the same
     text at /help and the two must not drift apart. -->

Short how-tos for everything the bot does, one per thing you might want to do. The bot serves the same text at **`/help`** — the **?** in the site header, and next to each page’s heading.

**Slots and operations** — [Request a slot](#request-a-slot) · [Change, cancel or leave](#change-cancel-or-leave) · [Approve or deny a request](#approve-or-deny-a-request) · [Put somebody on a slot directly](#put-somebody-on-a-slot-directly) · [Take somebody off a slot](#take-somebody-off-a-slot) · [Start an operation](#start-an-operation) · [Set the start time and reminder](#set-the-start-time-and-reminder) · [Post or re-post the ORBAT board](#post-or-re-post-the-orbat-board) · [Announce the operation](#announce-the-operation) · [Empty the approval queue](#empty-the-approval-queue) · [Check what the bot is reading](#check-what-the-bot-is-reading)

**ORBATs and rosters** — [Build an ORBAT](#build-an-orbat) · [Edit an ORBAT without unseating anybody](#edit-an-orbat-without-unseating-anybody) · [Rename, copy or delete an ORBAT](#rename-copy-or-delete-an-orbat) · [Export an ORBAT to a Google Sheet](#export-an-orbat-to-a-google-sheet) · [Run the operation on an ORBAT](#run-the-operation-on-an-orbat) · [Run the operation on a Google Sheet](#run-the-operation-on-a-google-sheet)

**Events** — [Create an event](#create-an-event) · [Sign up for an event](#sign-up-for-an-event) · [Repeat an event](#repeat-an-event) · [Custom sign-up options](#custom-sign-up-options) · [Ping roles on an event](#ping-roles-on-an-event) · [Edit, cancel or delete an event](#edit-cancel-or-delete-an-event)

**Game roles** — [Pick your game roles](#pick-your-game-roles) · [Add and remove game roles](#add-and-remove-game-roles)

**Tactical maps** — [Draw the plan](#draw-the-plan) · [Use layers](#use-layers) · [Put a real terrain behind the plan](#put-a-real-terrain-behind-the-plan) · [Put the town names on the map](#put-the-town-names-on-the-map) · [Share, copy or post a map](#share-copy-or-post-a-map) · [Get the plan into Arma 3](#get-the-plan-into-arma-3)

**Reddit announcements** — [Watch a Reddit user or subreddit](#watch-a-reddit-user-or-subreddit) · [Catch a post up, or skip a backlog](#catch-a-post-up-or-skip-a-backlog) · [Follow someone whose profile hides their posts](#follow-someone-whose-profile-hides-their-posts)

**Server and members** — [Log joins, leaves, kicks and bans](#log-joins-leaves-kicks-and-bans) · [Welcome new members](#welcome-new-members) · [Label your invite links](#label-your-invite-links) · [Track time in voice](#track-time-in-voice) · [Build a rich message](#build-a-rich-message) · [Delete messages in bulk](#delete-messages-in-bulk) · [Choose the channels and the timezone](#choose-the-channels-and-the-timezone) · [Sync, restart and the one-time migration](#sync-restart-and-the-one-time-migration)

### Slots and operations

_Asking for a slot, deciding who gets it, and running the evening._

#### Request a slot

**Who:** Everyone · **Where:** Discord — the ORBAT board, or `/request-slot`

Ask for one of the slots on tonight’s board and wait for a Unit Leader to decide.

1. Go to the **#orbat** channel and press **📋 Request a Slot** on the board, or run `/request-slot` anywhere.
2. Pick the squad from the first menu, then the slot from the second.
3. That is it — the request goes to the approvers, and you get a DM confirming it.
4. Watch the board: your slot turns 🟡 while you wait, and 🔴 once you have it.

> Somebody else may ask for the same slot. Both requests stand, the approver picks one, and the other person is told — so a 🟡 slot is worth asking for rather than avoiding.
>
> You can only hold one request at a time. Use `/change-slot` to move rather than requesting twice.

See also: [Change, cancel or leave](#change-cancel-or-leave) · [Approve or deny a request](#approve-or-deny-a-request)

#### Change, cancel or leave

**Who:** Everyone · **Where:** Discord — `/change-slot`, `/cancel-request`, `/leave-operation`

Move to a different slot, withdraw a request you have not had answered yet, or drop out of the operation.

1. To swap: `/change-slot`. It gives up what you hold and opens the picker again.
2. To withdraw a request nobody has answered: `/cancel-request`.
3. To drop out entirely, approved or not: `/leave-operation`, then confirm.

> `/change-slot` frees your old slot the moment you run it, so somebody else can take it while you are choosing. If you only want a different squad, that is the trade.
>
> All three release the slot on the board straight away, so nobody is planning around a seat you have left.

See also: [Request a slot](#request-a-slot)

#### Approve or deny a request

**Who:** Unit Leader or Admin · **Where:** Discord — **#slot-approvals**, or the **Slot Approvals** tab

Decide who gets the slot, in the channel or in the browser — both do exactly the same thing.

1. In Discord: open **#slot-approvals** and press **✅ Approve** or **❌ Deny** on the request. Denying opens a box for an optional reason.
2. In the browser: **🎖️ Operations → Slot Approvals**, where the same two buttons sit next to each request with a reason field.
3. The member is DMed either way, the message leaves the queue, and a record goes to **#approval-archive**.

> Approving a contested slot denies the other requests for it automatically and tells those members why.
>
> A Unit Leader may only decide requests from their own unit. The web page still lists the rest — it says *CNTO only* instead of the buttons, so the queue never lies about how many people are waiting.
>
> Old requests stay clickable after a new operation starts, and are always decided against the operation they were made for.

See also: [Take somebody off a slot](#take-somebody-off-a-slot) · [Put somebody on a slot directly](#put-somebody-on-a-slot-directly)

#### Put somebody on a slot directly

**Who:** Unit Leader or Admin · **Where:** Discord — `/assign-slot`, or the **Slot Approvals** tab

Book a member onto a slot without them asking and without an approval step.

1. In Discord: `/assign-slot @member`, then pick the squad and the slot.
2. In the browser: the **Assign somebody** panel at the bottom of **Slot Approvals**. Type a Discord ID, a mention or a name.
3. The member is DMed that they now hold it.

> This is stricter than approving: you need a unit of your own and the member must share it. Choosing who goes on the roster is not the same act as answering somebody who asked.
>
> Members are searched rather than listed, because the bot does not read your member list. More than one match is reported with IDs rather than guessed at.
>
> It is refused if the member already holds a slot — clear the old one first.

See also: [Take somebody off a slot](#take-somebody-off-a-slot)

#### Take somebody off a slot

**Who:** Unit Leader or Admin · **Where:** Discord — `/clear-slot`, or **Release** / **Withdraw** on the web

Give a booked slot back, or take an undecided request out of the queue.

1. In Discord: `/clear-slot` and pick from the dropdown of who is currently on the operation.
2. In the browser: **Release** on a booked row, **Withdraw** on a pending one — the same action under two names.
3. Confirm. The member is DMed and told who removed them.

> Neither can be undone, which is why both ask first.
>
> Clearing a pending request greys out its approval message, so nobody finds it later and presses Approve on a request that is gone.
>
> The archive record of an earlier approval is left alone. The archive says what was decided; this is a later decision, not a correction of that one.

#### Start an operation

**Who:** Admin · **Where:** Discord — `/setup-slots`, or the **Operation** tab

Load a roster, set the start time, and put the board up.

1. Decide where the roster comes from: an **ORBAT** you built here, or a **Google Sheet**.
2. In Discord: `/setup-slots orbat:<name>` **or** `/setup-slots sheet_url:<link>` — one or the other, never both.
3. Add `event_time` and `reminder_minutes` if you know them; both can be set later.
4. In the browser: **🎖️ Operations → Operation → Start a different operation**, which is the same thing with a dropdown.
5. The board is posted to **#orbat** automatically, with the request button on it.

> Starting an operation archives the previous one. Its pending requests are left alone and stay decidable.
>
> Times are read in the server timezone — set that once with `/set-timezone`.
>
> If the bot cannot post in the ORBAT channel the operation is still created and you are told. Post the board yourself afterwards.

See also: [Set the start time and reminder](#set-the-start-time-and-reminder) · [Post or re-post the ORBAT board](#post-or-re-post-the-orbat-board) · [Build an ORBAT](#build-an-orbat)

#### Set the start time and reminder

**Who:** Admin · **Where:** Discord — `/set-event-time`, or the **Operation** tab

Move tonight’s start, and choose how long before it everybody is reminded.

1. `/set-event-time time:25/06/2025 19:00 reminder_minutes:30`.
2. Or the **Start time** field on the **Operation** page.
3. The board updates itself, and the reminder is re-armed for the new time.

> The reminder DMs every approved member and pings **#orbat**. It fires once — moving the time arms it again.
>
> The time renders as a Discord timestamp, so everyone sees it in their own local time whatever the server timezone is.

See also: [Choose the channels and the timezone](#choose-the-channels-and-the-timezone)

#### Post or re-post the ORBAT board

**Who:** Admin · **Where:** Discord — `/post-orbat`, or the **Operation** tab

Put a fresh board up when the old message has scrolled away or been deleted.

1. `/post-orbat` posts it in the channel you run it in; `/post-orbat channel:#orbat` sends it elsewhere.
2. Or press **Post the board** on the **Operation** page.
3. The new message becomes the one the bot keeps up to date.

> There is only ever one live board. Posting a new one means the old message stops updating — delete it, or it will mislead somebody.
>
> The board refreshes itself on every approval, denial and release, so you should rarely need this.

#### Announce the operation

**Who:** Admin · **Where:** Discord — `/post-event`, or the **Operation** tab

Post an announcement embed pointing people at the board to sign up.

1. `/post-event channel:#announcements`, optionally overriding the mission name and time.
2. Or the **Post the announcement** panel on the **Operation** page.
3. It names the operation, its start, and links **#orbat** for sign-ups.

> This only announces. Sign-up still happens through the board — for an event with its own attendee list, use `/event-create` instead.

See also: [Create an event](#create-an-event)

#### Empty the approval queue

**Who:** Admin · **Where:** Discord — `/clear-requests`, or the **Operation** tab

Cancel every pending request for the operation at once.

1. Run `/clear-requests`, or press it on the **Operation** page.
2. Every pending request is cancelled and its approval message greyed out.

> Nobody is DMed and nothing is archived. This empties a queue that was never going to be answered — it is not the same as turning people down, which is what Deny is for.
>
> Approved bookings are untouched.

#### Check what the bot is reading

**Who:** Admin · **Where:** Discord — `/current-operation`, `/debug-slots`

Find out which operation is live and what the bot sees on its roster, when a slot is missing.

1. `/current-operation` names the operation and says whether it runs on an ORBAT or a sheet.
2. `/debug-slots` prints the raw slots as the bot reads them; `/debug-slots squad:<name>` narrows it.
3. On the web the same output is on the **Operation** page, under **Read the raw roster**, rendered in place.

> Each slot is keyed `db:412` on an ORBAT or `sheet:r12c4` on a sheet. A slot missing here is missing in the roster, not in the board.
>
> On a sheet, a slot the bot cannot see usually means the cell does not start with `1.` or `1-`, or its `<Insert Name>` marker is gone.

### ORBATs and rosters

_Where the slots themselves come from — built here, or in a sheet._

#### Build an ORBAT

**Who:** Admin · **Where:** Web — **🎖️ Operations → ORBATs**

Write the roster as indented text and let the bot turn it into squads and slots.

1. Go to **ORBATs**, give the new one a name, and press **Create**.
2. Write the roster in the big text field: squad names at the left margin, slots indented under them.
3. Put a squad’s options after a `|`, separated by commas: `left` or `right` for the column, `unit:TFP`, `radio:343 CHN:3`, and `nocount` for a bench that should not count against the numbers.
4. Fill in the **Nets** field underneath for the shared channels, one per line as `Platoon Net | 152 CHN : 1`.
5. Press **Preview** to see the board, then **Save**.

> A line starting with `#` is a comment. A leading `1.` or `2)` on a slot is stripped, so lines pasted out of a sheet land clean.
>
> Leave the column out entirely and the squads are split down the middle for you. One explicit `left` or `right` turns that guessing off for the whole ORBAT.
>
> Discord will not render an unlimited board. The editor warns you before you save when the ORBAT outgrows 25 fields or the character limits — eight squads plus a net list is the practical ceiling.

See also: [Edit an ORBAT without unseating anybody](#edit-an-orbat-without-unseating-anybody) · [Run the operation on an ORBAT](#run-the-operation-on-an-orbat)

#### Edit an ORBAT without unseating anybody

**Who:** Admin · **Where:** Web — **🎖️ Operations → ORBATs → the ORBAT**

Change the roster while it is in use, and see who an edit would affect before it happens.

1. Open the ORBAT and change the text.
2. Press **Preview**. The diff lists what would be added, removed and renamed.
3. Press **Save**. An edit that would remove or rename a slot *somebody holds* stops at a confirmation page naming them.
4. Read that page, then confirm or go back and fix the text.

> Reordering lines is free — slots are matched by name first, so moving a squad changes nothing.
>
> Renaming keeps the booking. The person stays on the slot under its new name, which is why a rename asks: right for a typo, wrong if you meant to replace the role.
>
> Cutting three `Rifleman` lines to two keeps the first two, so a booked one is not the casualty.
>
> A red banner at the top means this ORBAT is backing the operation running right now, and an edit changes tonight’s board.

See also: [Build an ORBAT](#build-an-orbat)

#### Rename, copy or delete an ORBAT

**Who:** Admin · **Where:** Web — **🎖️ Operations → ORBATs**

Keep a library of rosters: give one a clearer name, copy it as the basis for the next one, or throw it away.

1. **Rename**: open the ORBAT and use the **Name and description** form under the editor. The description is only ever shown in the list, to tell two similar rosters apart.
2. **Duplicate**: press **Duplicate** on the ORBAT. You get a copy of the squads, slots and nets — and none of the bookings.
3. **Delete**: press **Delete** and confirm.

> Duplicating is the way to build next week’s roster from this week’s without touching the one an operation is running on.
>
> An ORBAT backing the **active** operation cannot be deleted — the delete would take tonight’s whole board with it. Finish or replace the operation first.
>
> Deleting an ORBAT that ran an *old* operation is allowed, and releases those bookings in the same move rather than leaving records pointing at slots that no longer exist.

#### Export an ORBAT to a Google Sheet

**Who:** Admin · **Where:** Web — **🎖️ Operations → ORBATs → the ORBAT**

Write the roster into a spreadsheet, for people who want it there as well.

1. Open the ORBAT and find the **Export to a sheet** panel.
2. Paste the URL of the spreadsheet to write into.
3. Press **Export**. A **new tab** is added holding the roster.

> It never touches an existing tab. A title collision gets a `(2)` suffix rather than overwriting anything, so an export can never damage the sheet another operation is running on.
>
> It is one-way. Nothing is recorded about the export, and the bot reads only the *first* tab — so an exported tab is not picked up on its own.
>
> Needs `GOOGLE_CREDENTIALS` configured, and the service account invited to the spreadsheet as an editor.

See also: [Run the operation on a Google Sheet](#run-the-operation-on-a-google-sheet)

#### Run the operation on an ORBAT

**Who:** Admin · **Where:** Discord — `/setup-slots orbat:<name>`

Use a roster held here instead of a spreadsheet — the default choice, and the simpler one.

1. Build the ORBAT first (see **Build an ORBAT**).
2. Run `/setup-slots orbat:<name>` — the name autocompletes — or pick it from the dropdown on the **Operation** page.
3. Everything else behaves identically: requests, approvals, the board, assigning and clearing.

> No Google account, no credentials and no network call is involved, so nothing about the evening depends on a third party being up.
>
> The approved request *is* the booking. There is no second copy to fall out of step with the board.
>
> The board additionally shows each squad’s unit and radio channel and the shared nets, which a sheet has nowhere to put.

See also: [Run the operation on a Google Sheet](#run-the-operation-on-a-google-sheet) · [Start an operation](#start-an-operation)

#### Run the operation on a Google Sheet

**Who:** Admin · **Where:** Discord — `/setup-slots sheet_url:<link>`

Use an ORBAT-style spreadsheet as the roster, the way it worked before ORBATs existed.

1. Share the spreadsheet with the service account in `GOOGLE_CREDENTIALS` as an **Editor**.
2. Lay the first tab out ORBAT-style: a squad name, then slot cells starting `1.` or `1-`, each with `[] <Insert Name>` beside it.
3. Run `/setup-slots sheet_url:<link>`.
4. Approving writes the member’s name and unit tag into the cell; clearing restores `[] <Insert Name>`.

> Only the **first tab** is read, and the operation is named after the spreadsheet unless you pass `name:`.
>
> A sheet with no `<Insert Name>` markers has no free slots as far as the bot is concerned, whatever its columns are called.
>
> Inserting a row moves every cell below it. Run `/sync` afterwards to repair pending requests that now point at the wrong row.
>
> If the sheet write fails on approval the request is rolled back, so the sheet and the board never disagree.

See also: [Run the operation on an ORBAT](#run-the-operation-on-an-orbat) · [Export an ORBAT to a Google Sheet](#export-an-orbat-to-a-google-sheet)

### Events

_Standalone sign-ups: trainings, movie nights, campaign sessions._

#### Create an event

**Who:** Unit Leader or Admin · **Where:** Discord — `/event-create`, or the **📅 Events** tab

Post a training, a movie night or anything else with its own sign-up buttons and attendee list.

1. In Discord: `/event-create title:<name> start_time:25/06/2025 19:00`.
2. Add what you need: `description`, `duration`, `location`, `channel`, `mention`, `reminder`, `image_url`.
3. In the browser: **📅 Events → New event**, which is the same fields as a form.
4. The message goes up with the sign-up buttons on it and a live attendee list.

> A start time in the past is refused.
>
> `duration` is what lets the event close itself out afterwards — without it the message keeps its buttons indefinitely.
>
> Events are entirely separate from operations and ORBAT slots. Nothing here touches a roster.

See also: [Sign up for an event](#sign-up-for-an-event) · [Repeat an event](#repeat-an-event) · [Custom sign-up options](#custom-sign-up-options)

#### Sign up for an event

**Who:** Everyone · **Where:** Discord — the event message, or the **📅 Events** tab

Answer on the buttons; the attendee list updates for everyone immediately.

1. Press **✅ Accepted**, **❓ Tentative** or **❌ Declined** on the event message — or whatever options that event defines.
2. Changing your mind: press a different button.
3. Press the button you already chose to **withdraw** entirely.
4. `/event-list` shows what is coming up with counts and jump links.

> Withdrawing is not the same as declining. Declining says you are not coming; withdrawing takes you off the list altogether.
>
> Start times render as Discord timestamps, so you see them in your own local time.

#### Repeat an event

**Who:** Unit Leader or Admin · **Where:** Discord — `repeat:` on `/event-create` or `/event-edit`

Make it a series: the next occurrence posts itself when this one finishes.

1. Pass `repeat:` when creating, or add it later with `/event-edit event:<name> repeat:weekly`.
2. Pick a pattern: daily, weekly, every 2 weeks, monthly by date, monthly by weekday (*last Saturday* or *2nd Saturday*), or weekly except the last of the month.
3. Bound it with `repeat_until:` if the series should stop on a date.
4. Use `repeat_delay:` to hold the next post back a number of hours after this one ends, instead of posting it the moment it closes.
5. Stop a series with `/event-edit event:<name> repeat:none`.

> The weekday patterns take both the weekday and the position from the **first** occurrence. A series created on Saturday the 13th means *2nd Saturday* under *monthly by weekday*.
>
> Only one occurrence is live at a time — there is no pre-generated calendar, and sign-ups deliberately do not carry over.
>
> Dates are measured from the first occurrence, so a series on the 31st gives 28 Feb → 31 Mar rather than drifting to the 28th for good.
>
> A bot that was offline for ten weeks posts **one** occurrence in the future, not one per missed week.

See also: [Edit, cancel or delete an event](#edit-cancel-or-delete-an-event)

#### Custom sign-up options

**Who:** Unit Leader or Admin · **Where:** Discord — `responses:` on `/event-create` or `/event-edit`

Replace Accepted / Tentative / Declined with your own buttons.

1. Pass `responses:` with the options separated by `|`, for example `🚁 Pilot | 🔫 Infantry | -❌ Can’t`.
2. A leading `-` marks an option as *not coming*: those people are left out of reminders and cancellation DMs.
3. A leading emoji is put on the button.

> Between 2 and 10 options, labels under 40 characters, and at least one that is not a decline.
>
> Changing the options on a live event drops the sign-ups whose answer no longer exists, and tells you how many. Everyone else keeps theirs.
>
> A repeating event copies its options to each new occurrence.

#### Ping roles on an event

**Who:** Unit Leader or Admin · **Where:** Discord — `mention:` on `/event-create` or `/event-edit`

Notify one or more roles when the event goes up and again on the reminder.

1. Pass `mention:@Infantry @Armour` — type the `@` and let Discord turn it into a role token.
2. Role names in a comma-separated list work too, if the tokens are awkward to type.
3. On the web, the roles are checkboxes on the event form.
4. Clear them again with `/event-edit event:<name> mention:none`.

> Up to ten roles. Anything that cannot be resolved is reported rather than dropped quietly.
>
> A role that is not **mentionable** needs the bot to have *Mention All Roles*, or the ping notifies nobody. You are warned when that is the case.

#### Edit, cancel or delete an event

**Who:** Organiser or Admin · **Where:** Discord — `/event-edit`, `/event-cancel`, `/event-delete`

Change the details, call it off while keeping the record, or remove it for good.

1. **Edit**: `/event-edit event:<name>` and pass only the fields that change. The rest keep their values.
2. **Cancel**: `/event-cancel event:<name> reason:<why>`. The message turns red and loses its buttons, and everyone attending is DMed.
3. **Delete**: `/event-delete event:<name>`, then confirm. The event and its message are gone.
4. All three are on the event’s page in the browser as well.

> Moving the start time re-arms the reminder, so it fires again for the new time.
>
> Cancel tells people; delete does not. If anybody has signed up, cancel is almost always what you want — the confirmation page says so.
>
> On a repeating event, cancelling one occurrence still posts the next one. `stop_series:True` ends the series instead.
>
> Moving an event to a different channel is not possible — the sign-up history belongs to the message. Cancel and recreate.

### Game roles

_Tag roles members give themselves, so you can ping everyone who plays a game._

#### Pick your game roles

**Who:** Everyone · **Where:** Discord — the panel button or `/game-roles`, or the **🎮 Game roles** tab

Give yourself the tag for the games you play, so people can ping everyone who plays them.

1. Press **🎮 Choose your game roles** on the panel, or run `/game-roles`.
2. The roles you already have are ticked. Tick what you want, untick what you don’t, and submit.
3. Or press **➖ Remove a role** for a list of only the ones you currently hold.
4. In the browser it is the **🎮 Game roles** tab — same list, same result.

> These roles grant no permissions at all. They exist to be mentioned.
>
> `/game-role-list` shows every game role on the server without changing yours.
>
> Unticking everything is a valid answer and removes all of them.

#### Add and remove game roles

**Who:** Admin · **Where:** Discord — `/game-role-add`, `/game-role-remove`, `/game-role-panel`

Decide which games are on the list, and put the self-assign panel in a channel.

1. `/game-role-add name:Minecraft emoji:⛏️ description:Survival server` creates a permission-free role and lists it.
2. A Discord role with that **exact name** already exists? It is reused, not duplicated — running the command again just updates the emoji and description.
3. `/game-role-remove role:<role>` takes it off the list. Add `delete_role:True` to delete the Discord role as well.
4. `/game-role-panel channel:#roles` posts the panel with the self-assign button.
5. The same three live on the **🎮 Game roles** tab for admins.

> Up to 25 roles — as many as a Discord menu can show.
>
> A pre-existing role is refused if it grants **any** permission, is `@everyone`, is managed by an integration, or sits above the bot in the role list.
>
> Unit roles and `Unit Leader` can never be made self-assignable, so nobody can tick their way into approval rights.
>
> There is one panel per server and it updates itself whenever the list changes.

### Tactical maps

_Drawing the plan on the terrain, sharing it, and getting it into the mission._

#### Draw the plan

**Who:** Unit Leader or Admin · **Where:** Web — **🎖️ Operations → Maps**

Put unit symbols, movement lines and objectives on the terrain.

1. Go to **Maps**, name the new map, and press **Create**.
2. Pick a symbol from the palette and click the sheet to place it.
3. Select a symbol to open the inspector: side, what it is, size, mobility, parent unit, whether it is only planned, and its label.
4. **Drag** with the Line or Area tool to draw — or hold **Ctrl** and drag from any tool, which is the gesture Arma’s own map uses.
5. Press **Save**. Everyone looking at the map sees the saved version.

> The frame says whose it is: rectangle friendly, diamond hostile, square neutral, quatrefoil unknown. Open at the bottom means it is in the air; **dashed** means it is planned rather than there.
>
> Lines get an arrow head only when you ask for one — most lines on a plan are boundaries and phase lines, which point nowhere.
>
> Lines and areas take a colour of their own; symbols do not, because their colour is what says which side they are.
>
> Two people drawing at once will overwrite each other’s save. Agree who has the pen.

See also: [Use layers](#use-layers) · [Put a real terrain behind the plan](#put-a-real-terrain-behind-the-plan) · [Share, copy or post a map](#share-copy-or-post-a-map)

#### Use layers

**Who:** Unit Leader or Admin · **Where:** Web — the layer panel on a map

Keep phase 1, phase 2 and the enemy picture apart, and show them one at a time.

1. Add a layer in the layer panel and name it.
2. Whatever you place next lands on the layer that is selected.
3. Use each layer’s switch to show and hide it while briefing.

> Hiding a layer is not deleting it. The items stay in the document and in every save.
>
> The switches work for whoever is looking, including somebody who only has a share link — toggling changes their view and nothing else.
>
> Deleting a layer moves its items to the first layer rather than taking them with it.
>
> A hidden layer is left out of the Arma export: markers you have switched off are not part of the plan being handed over.

#### Put a real terrain behind the plan

**Who:** Admin · **Where:** Web — **Terrains**, or the map editor’s background panels

Draw on the actual terrain instead of a blank sheet.

1. **Best: upload it.** Go to **Terrains** (linked from the Maps page) and upload the same tile archive you prepare for OCAP — a zip or 7z with the numbered folders `0/ 1/ 2/ …` in it.
2. Then open a map and use **🗻 Put it on a terrain** to pick it.
3. **Or point at a running OCAP server**: in the editor, press **List the terrains**, pick one, and import it.
4. Either way the terrain’s Arma coordinates come along, which is what makes the export land in the right place.

> Prefer the upload for anything shared outside the unit. A share link is for people who do not sign in here, and expecting them to reach your OCAP instance as well is how a link ends up showing an empty sheet.
>
> If the archive has no `map.json`, you are asked for the world size in metres. Without it the tiles would draw but the Arma export would be nonsense.
>
> Levels deeper than a planning sheet needs are dropped, and the reply says how many — the terrain is meant to be smaller than the file.
>
> A terrain cannot be deleted while a map is drawn on it, and the refusal names the maps.

See also: [Draw the plan](#draw-the-plan)

#### Put the town names on the map

**Who:** Admin · **Where:** Web — the map’s background panel, or **Terrains**

A terrain arrives with roads and contours and no labels. This is how it learns what its towns are called.

1. **On an OCAP terrain:** open the map, go to *Terrain and background* → **Place names from OCAP**, and press **Import place names**. It reads the server the map points at.
2. **On a terrain uploaded here:** the archive’s own `locations` files are read on upload, if it has them.
3. Either way it is once per **terrain** — every map drawn on it gains the names.
4. Too many? The editor’s background panel has a switch per group and a size slider.
5. **If it finds nothing**, it is that one terrain that carries no location data — other terrains on the same server can still import fine. The same panel then has a script and a paste box: run it in a mission on that terrain, press LOCAL EXEC, paste the clipboard in. **Once for the terrain** — not once per map, not once per operation.

> The names are in the OCAP data, just not in `map.json`. OCAP builds a terrain from a **grad_meh** export, which writes them beside the tiles as `geojson/locations/<type>.geojson.gz` — one file per Arma location type, and the file name is the type.
>
> Whether they are there is decided **per terrain**, not per server. A terrain built the older way — Arma’s map export through gdal2tiles — carries no vector data at all, so there is nothing to find for it however hard the import looks, while the terrain next to it on the same OCAP imports in one press. A failure names **every address it tried**, so you can tell that apart from a broken feature.
>
> The tiles genuinely have no labels in them: they come out of Arma’s own map export as pure topography, and the game draws the names over that from its config afterwards.
>
> Place names are never exported to Arma — the game already draws its own.

See also: [Put a real terrain behind the plan](#put-a-real-terrain-behind-the-plan) · [Draw the plan](#draw-the-plan)

#### Share, copy or post a map

**Who:** Unit Leader or Admin · **Where:** Web — the map’s **Share** and **Post** panels

Give people a link that needs no login, copy a plan to start the next one, or announce it in a channel.

1. **Share**: open the **Share** panel and pick **view** or **edit**. You get a `/m/…` link that works without signing in.
2. **Post**: pick a channel and press **Post**. The bot sends an embed with the link and the counts.
3. **Duplicate**: press **Duplicate** for a copy of the plan — the share link deliberately does not come with it.
4. **Rename**: the **Name and description** form on the map page.
5. Turn sharing off again by setting it to **off**, or press **New link** to replace the token.

> The link *is* the credential. Anybody who has it can open the map, and with **edit** can draw on it. **New link** is the only way to un-share a map that has travelled further than intended.
>
> Changing view↔edit keeps the same token, so tightening the rights does not break every copy of the link already out there.
>
> A map posted in Discord keeps the link it was posted with. Post it again if the site’s address changes.
>
> It posts a link rather than a picture, which is what keeps it current while the plan is still being drawn.

#### Get the plan into Arma 3

**Who:** Unit Leader or Admin · **Where:** Web — the map’s **Arma** panel

Paste the plan into a running mission as markers, with no mod on either side.

1. Make sure the map sits on a terrain, or that its Arma corners are filled in — the export needs to know where the sheet is in the world.
2. Open the **Arma** panel and copy one of the two scripts.
3. In the mission, open the **debug console** as a logged-in admin and paste it.
4. Press **LOCAL EXEC**. `createMarker` is global, so one machine running it draws the plan for everybody.

> Two scripts, side by side. The first draws markers that stay put. The second draws markers players can drag and delete in game — handy, and the same click that fixes a misplaced objective also deletes it, with nothing asking first.
>
> Both are **LOCAL EXEC**. GLOBAL EXEC is wasted on the first and actively wrong on the second, where the marker names carry the client that made them.
>
> Running it again **replaces** the markers from the last run rather than laying a second set over them. Two maps can never delete each other’s.
>
> Symbol sizes and mobility have no counterpart in Arma, so they are written into the marker text: `1-1 Alpha / A Coy (planned Plt twd)`.
>
> Dashed is lost — Arma has no dashed marker. The debug console has to be enabled in the mission’s `description.ext`.

### Reddit announcements

_Watching a user or a subreddit and announcing new posts._

#### Watch a Reddit user or subreddit

**Who:** Admin · **Where:** Web — **📣 Reddit**

Announce every new post in a channel, in your own words.

1. Go to **📣 Reddit → Add a watch**.
2. Pick **user** or **subreddit** and type the name without the `u/` or `r/`.
3. Choose the channel, write the announcement text, and tick the roles and list the people to ping.
4. Press **Preview** to see the newest post rendered with your text — it posts nothing.
5. Save. The first check notes what is already on the feed and announces none of it.

> `{title}`, `{url}`, `{author}` and `{subreddit}` are filled in. Everything else is literal.
>
> Checked every five minutes. **Check now** runs that check early and does announce.
>
> No Reddit account, API registration or secret is needed — it reads the public feed.
>
> The bot will not ask anybody to upvote anything, and there is no wording for it. Organised voting is what gets accounts banned, and the post score is deliberately never read or shown.
>
> Pointing a watch at a different source resets what it has seen, so the new feed is seeded rather than announced from scratch.

See also: [Catch a post up, or skip a backlog](#catch-a-post-up-or-skip-a-backlog)

#### Catch a post up, or skip a backlog

**Who:** Admin · **Where:** Web — **📣 Reddit → the watch → Recent posts**

Post something the watch missed, or clear a queue you do not want announced.

1. Open the watch and go to **Recent posts**. It reads the feed and says which posts have already been announced.
2. Press **Announce** on one to post it by hand.
3. To skip instead: tick the posts you do not want and press **Mark ticked as announced**, or **Mark all** for the whole list.

> Marking posts nothing. It just takes them out of the queue so the next check does not work through them three at a time.
>
> This is the way past a flood — an account that un-hid its posts, or a subreddit that suddenly woke up.
>
> Marking needs no working connection to Reddit, which matters: a backlog most needs clearing exactly when Reddit is refusing us.

#### Follow someone whose profile hides their posts

**Who:** Admin · **Where:** Web — **Only these authors** on a subreddit watch

Watch the subreddit instead, and keep only what that account wrote.

1. Reddit lets an account hide its posts from its own profile, which is what a **user** watch reads. If a user watch stays empty, that is usually why.
2. Make a **subreddit** watch on the subreddit they post in.
3. Put their account name in **Only these authors** — several, comma-separated, if you like.

> The filter is applied before anything else looks at the posts, so the other authors’ posts never take up the watch’s memory.
>
> A very busy subreddit can push a post out of its own feed between two checks. Nothing can be done about that from here.
>
> Widening the filter later makes old posts look new. Use **Recent posts → Mark all** afterwards.

### Server and members

_The log, the welcome, voice time, embeds and the housekeeping._

#### Log joins, leaves, kicks and bans

**Who:** Admin · **Where:** Web — **📋 Member log**

Announce who came and went in a channel your staff watches.

1. Go to **📋 Member log**, pick the channel, tick the events you want, and save.
2. Joins show the account’s age, the member count and which invite link was used.
3. Leaves show how long they were around and which roles they had.
4. Kicks are told apart from voluntary leaves, and name the moderator and reason. Bans and unbans do the same.

> Joins and leaves need Discord’s privileged **Server Members Intent**. Tick it in the Developer Portal **first**, then set `MEMBER_EVENTS=1` and redeploy — asking for the intent before it is granted stops the bot from starting at all.
>
> Bans and unbans need no intent and work without that.
>
> Without **View Audit Log** every kick reads as a plain leave and bans have no moderator. Without **Manage Server** the invite list cannot be read. The page tells you which are missing.

See also: [Welcome new members](#welcome-new-members) · [Label your invite links](#label-your-invite-links)

#### Welcome new members

**Who:** Admin · **Where:** Web — **📋 Member log → Welcome message**

Greet somebody who joins, in a public channel, by DM, or both.

1. Go to **📋 Member log** and scroll to **Welcome message**.
2. Pick the channel the greeting is posted in — or leave it on *Nowhere* if you only want the DM.
3. Tick **Also send it as a DM** to send the same text to the member directly.
4. Write the text. Leave it empty to use the default.
5. Save. The next person through the door gets it.

> Four placeholders: `{member}` pings them, `{name}` is their name as plain text, `{server}` is the server name and `{member_count}` is how many members there now are. Anything else is literal, so a stray `{` is harmless.
>
> This is **separate from the join log**. It has its own channel, it does not need the log to be switched on, and it does not care about the *Announce joins* tick — the log is written for staff, the welcome for the member.
>
> It does use the same join event, so it needs the same **Server Members Intent** as the join log.
>
> A member with DMs closed simply does not get the DM. Nothing fails, and the channel message still goes out.

See also: [Log joins, leaves, kicks and bans](#log-joins-leaves-kicks-and-bans)

#### Label your invite links

**Who:** Admin · **Where:** Web — **📋 Member log → Invite links**

Say where each link was published, so a join names the source instead of a code.

1. Go to **📋 Member log** and find the invite list at the bottom, with each link’s use count.
2. Type what that link is for next to it — *Steam*, *Website*, *Reddit*.
3. Save. Joins then read `rnPAfscGbE · Steam` rather than the code alone.

> Needs **Manage Server**, or the invite list cannot be read at all.
>
> Labels for links that have since expired are kept and stay editable, because old join messages still refer to them.
>
> A join through a single-use link can still be attributed: Discord deletes the link immediately, and the bot works out which one went missing.
>
> Two people joining in the same second cannot be told apart, and a member added by another bot has no invite at all. Those say so rather than guessing.

See also: [Log joins, leaves, kicks and bans](#log-joins-leaves-kicks-and-bans)

#### Track time in voice

**Who:** Admin · **Where:** Web — **🔊 Voice time**

Record how long members spend in voice, and show a leaderboard.

1. Go to **🔊 Voice time**. It is **off** until an admin switches it on, and nothing is recorded before that.
2. Tick **Enabled** and save. Everybody can then see the leaderboard for 24 hours, 7, 30 or 90 days, or all time.
3. Optionally pick a channel to announce finished visits in, with a minimum length so quick drop-ins do not fill it.
4. For a self-updating board, tick **Keep a daily top-10 message up to date**, pick a channel, a period and an hour.
5. **Post the top 10 once** sends a one-off message instead.

> By default time only counts while **at least two people** share a channel, and the AFK channel is skipped — so what you measure is time spent together, not time connected. Both rules can be switched off, and channels can be excluded.
>
> The daily board **edits the same message** once a day, so you can pin it. It never posts twice in a day, and catches up if the bot was down at the chosen hour.
>
> Members are named, not mentioned. A leaderboard that pings ten people every day would be worse than useless.
>
> A normal restart loses nothing; a hard crash costs at most five minutes, and time is never rounded up.
>
> No privileged intent and no extra permission — it works the moment you switch it on.

#### Build a rich message

**Who:** Admin · **Where:** Web — **📝 Embeds**

Compose the server-info and rules posts you would otherwise write by hand, and edit them in place afterwards.

1. Go to **📝 Embeds → New embed** and give it a name, which is only used to find it again.
2. Fill in title, description, colour, up to ten fields, author line, thumbnail, image, footer — and the plain text above the embed, which is the only part where a mention pings.
3. Save it as a draft and check the preview.
4. Press **Send** and pick a channel.
5. To change it later: edit and save. The **posted message is updated in place**, so a pinned post stays pinned.

> Discord’s limits are checked when you **save**, not when you send — an over-long title is refused on the form rather than becoming an embed that can never be posted.
>
> Sending again posts a **new** message and stops tracking the old one. That is how you move an embed to another channel.
>
> If the message was deleted in Discord, the embed quietly becomes a draft again rather than failing forever.
>
> Image and icon fields must be full `https://` URLs.

#### Delete messages in bulk

**Who:** Manage Messages · **Where:** Discord — `/purge`

Clear the last *N* messages in a channel, everything since a point in time, or both.

1. `/purge amount:50` — the newest 50 messages.
2. `/purge since:2h` — everything from the last two hours. `30m`, `7d` and `1w` work too, as does a date like `25/06/2025`.
3. `/purge amount:100 since:7d` — at most 100, and nothing older than a week.
4. A preview says what would go. Confirm to delete.

> It deletes in the channel you run it in, and only for people with **Manage Messages** there.
>
> The confirmation holds the exact messages it counted, so anything posted while you read it is left alone.
>
> Discord will not bulk-delete anything older than 14 days. Those go one at a time, roughly one a second, capped at 200 per run — the reply says how many were left, and running it again continues where it stopped.
>
> Pinned messages are deleted like any other, but the confirmation says how many are in the set.

#### Choose the channels and the timezone

**Who:** Admin · **Where:** Web — **🎖️ Operations → Settings**, or `/set-timezone`

Point the bot at the channels you actually use, and tell it what time you mean.

1. Go to **🎖️ Operations → Settings**.
2. Pick the channel for the **board**, the **approval queue** and the **archive**. *Default* means the bot uses `#orbat`, `#slot-approvals` and `#approval-archive`, creating them if they are missing.
3. Set the **timezone** to an IANA name like `Europe/Berlin`, or run `/set-timezone`.

> The timezone is how every time you *type* is read — operations, events and `/purge` alike. Times the bot *shows* are Discord timestamps and localise themselves per viewer.
>
> The empty option is called *Default* rather than left blank on purpose: nothing chosen and "chosen, and it happens to be #orbat" behave differently the day somebody renames a channel.
>
> A chosen channel that has since been deleted is called out rather than quietly falling back.

#### Sync, restart and the one-time migration

**Who:** Admin · **Where:** Discord — `/sync`, `/restart`, `/archive-old-approvals`

The three commands that are about the bot rather than about an operation.

1. `/sync` re-registers the slash commands with Discord and refreshes the board. Use it when a command looks missing.
2. `/restart` restarts the bot. Nothing is lost — everything lives in the database and every button is re-registered on start.
3. `/archive-old-approvals` is a **one-time** migration that moves approval messages decided before the archive existed into `#approval-archive`.

> Commands sync automatically on start and when the bot joins a server, so `/sync` is for when something looks wrong rather than routine.
>
> On a sheet-backed operation `/sync` also repairs pending requests whose rows moved because somebody inserted a row. On an ORBAT there is nothing to repair.
>
> `/archive-old-approvals` scans up to 500 messages and is not meant to be run twice.

<!-- help:end -->

---

## Role-Based Access

Grouped by the same feature areas as [Features](#features) above, so the two sections line up.

### 🗺️ ORBAT & Slots

| Command | Members | Unit Leaders | Admins |
|---|---|---|---|
| `/request-slot`, `/cancel-request`, `/change-slot`, `/leave-operation` | ✅ | ✅ | ✅ |
| `/assign-slot`, `/clear-slot` — or **Assign** / **Release** on the web | ❌ | ✅ (own unit only) | ✅ |
| Approve / Deny — in `#slot-approvals` or on the web | ❌ | ✅ (own unit only) | ✅ |
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

### 🗺️ Tactical maps

| Action | Members | Unit Leaders | Admins |
|---|---|---|---|
| Open and read a map — web only | ✅ | ✅ | ✅ |
| Draw on one, create, copy or delete one — web only | ❌ | ✅ | ✅ |
| Upload a terrain, or delete one — web only | ❌ | ❌ | ✅ |
| Share it with a link, or post it in a channel — web only | ❌ | ✅ | ✅ |
| Open a map through a share link | anyone holding the link | | |

### 📣 Reddit announcements

| Action | Members | Unit Leaders | Admins |
|---|---|---|---|
| Add, edit or remove a watch — web only | ❌ | ❌ | ✅ |

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

Only relevant for **sheet-backed** operations. If your roster is an
[ORBAT](#orbats), skip this section.

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

**None of this applies if you use an ORBAT.** Building the roster in the browser under [ORBATs](#orbats) needs no spreadsheet, no service account and no particular cell format — see [Running an operation on an ORBAT](#running-an-operation-on-an-orbat).

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

### 2. Google Sheets API — optional

Only needed if you want to run operations from a Google Sheet. If you build your rosters as [ORBATs](#orbats) in the browser instead, skip this step entirely — the bot starts and runs without any Google credentials, and only complains if you actually pass a `sheet_url` to `/setup-slots`.

1. Go to [Google Cloud Console](https://console.cloud.google.com) → **New Project**
2. Enable the **Google Sheets API** and **Google Drive API**
3. Go to **Credentials → Create Credentials → Service Account**
4. Under the service account → **Keys → Add Key → JSON** — download the file
5. Share each ORBAT sheet with the service account email (found inside the JSON as `client_email`) — give it **Editor** access

### 3. Environment Variables

Copy `.env.example` to `.env` and fill in the two required values:

```
DISCORD_TOKEN=your_bot_token
DB_PASSWORD=choose_a_secure_password
```

`GOOGLE_CREDENTIALS` is optional and only needed for sheet-backed operations:

```
GOOGLE_CREDENTIALS={...paste entire JSON key file contents here...}
```

> `DATABASE_URL` is constructed automatically by docker-compose from `DB_PASSWORD`. On Railway it is injected automatically — you do not set it manually in either case. The only time you fill it in yourself is [running the bot outside Docker](#local-development), which is why `.env.example` carries a commented example of it.

The optional [web interface](#web-ui) adds `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET`, `WEB_SECRET_KEY` and `WEB_BASE_URL`. Leave them empty and the bot behaves exactly as before — no web server is started at all.

`REDDIT_USER_AGENT` is optional and only matters if you use [Reddit announcements](#reddit-announcements): it is how the bot identifies itself when it reads a feed, and Reddit throttles clients that don't. Something like `orbatbot/1.0 (by /u/YourRedditName)` is ideal. Note that it does not on its own guarantee Reddit will serve the feed to a cloud-hosted bot — see [Reddit announcements](#reddit-announcements) for what happens when it doesn't.

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

Forfeits your current slot (pending or approved) and lets you pick a new one via the squad → slot picker. An approved slot is released either way; on a sheet-backed operation the cell is cleared too.

```
/leave-operation
```

Removes you from the operation entirely. Works for both pending and approved slots, and on a sheet-backed operation the cell is cleared as well. Shows a confirmation prompt before acting.

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

Directly assigns a member of your unit to a slot — no approval message, no waiting. Uses the same squad → slot picker. On a sheet-backed operation the sheet is updated immediately; the member gets a DM either way. Blocked if the member already holds a slot; use `/clear-slot` first to reassign. Also on the [**Slot Approvals** page](#slot-approvals).

```
/clear-slot
```

Presents a dropdown of active slots. Select one to remove the member and free the slot. On a sheet-backed operation the cell is restored to `[] <Insert Name>` and the unit tag removed. The member receives a DM.

Unit Leaders only see slots belonging to members of their own unit.

The same thing is on the **📋 Slot Approvals** page of the [web interface](#slot-approvals), as **Release** next to a booked slot and **Withdraw** next to a request nobody has decided.

Unit Leaders can also **Approve / Deny** requests for members of their own unit — in `#slot-approvals`, or on that same page.

---

### Admins

Available to members with the **Manage Server** permission. Full access with no unit restrictions.

```
/assign-slot @member
```

Directly assigns any member to any slot — no approval message, no waiting. Uses the same squad → slot picker. On a sheet-backed operation the sheet is updated immediately; the member gets a DM either way. Blocked if the member already holds a slot; use `/clear-slot` first to reassign.

```
/setup-slots orbat:Platoon ORBAT
/setup-slots sheet_url:https://docs.google.com/spreadsheets/d/.../edit
```

Run this once per operation. Give **either** an `orbat` — one you built under the **🗺️ ORBATs** tab on the website, autocompleted as you type — **or** a `sheet_url`, never both. The previous operation is archived automatically, and a live ORBAT embed is posted to the [ORBAT channel](#channels) — `#orbat` unless you have moved it, created if it doesn't exist. The whole thing is also a form on the [Operation page](#operation). Optional parameters:

- `event_time` — operation start time in `DD/MM/YYYY HH:MM` or `YYYY-MM-DD HH:MM` format (uses the server's configured timezone)
- `reminder_minutes` — how many minutes before the event to send reminders (default: 30)
- `name` — overrides the operation name, which otherwise comes from the ORBAT or the spreadsheet title

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

Shows the raw slot data the bot reads from the current roster, each slot with the identifier it is booked against — `db:412` for an ORBAT slot, `sheet:r12c4` for a spreadsheet cell. Useful for diagnosing why a slot isn't appearing in the picker. Optionally filter by squad name.

```
/current-operation
```

Shows the active operation and where its roster comes from — the ORBAT's name, or a link to the sheet.

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
   - On a sheet-backed operation the Google Sheet is updated. On an ORBAT there is nothing to write — the approved request *is* the booking, which is why an approval there can't fail halfway and need repeating
   - The request is deleted from `#slot-approvals`
   - A compact record is posted to `#approval-archive` (created automatically if it doesn't exist)
   - The ORBAT board refreshes
   - The member gets a DM
   - If other members had requested the same slot, they are automatically denied and notified
4. On denial: admin optionally provides a reason; member gets a DM and can request again
5. If a member cancels their request, the approval message is automatically updated to show it was cancelled (greyed out, buttons removed)

**Unit role gating:** deciding a request needs the **Unit Leader** role, and Unit Leaders can only approve or deny requests from members of their own unit. Admins can action any request, with or without a unit role. Having a unit role alone is not enough — the buttons sit in a channel everybody can read, and without that rule any member of a unit could approve their own request.

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

## Reddit announcements

Watch a Reddit account or a subreddit, and every time something new is posted, the bot announces it in a Discord channel — with your wording and your pings. It is set up in the [web interface](#web-ui) under **📣 Reddit**, admin only.

**No Reddit account, API key or app registration is needed.** Every user and every subreddit has a public RSS feed, and that is all the bot reads. Do set `REDDIT_USER_AGENT`, so Reddit can see who is asking — clients that don't say get throttled.

That may not be enough on its own, and it is worth knowing why before you go looking for a setting to change: **Reddit also refuses requests based on where they come from.** A hosting provider's address — Railway's included — gets turned away from the public feeds with `429 Too Many Requests` however politely it identifies itself, and however rarely it asks. One watch checked every five minutes is 288 requests a day, which is nothing at all; being refused is not about that number and cannot be fixed by lowering it.

So when a check is refused, the bot retries the same feed on `old.reddit.com`, which is the older renderer and is much less fussy about who is asking. If both turn it down, the watch **stands down** for half an hour (or for whatever `Retry-After` Reddit asked for) rather than asking again every five minutes, since hammering a refusal is what makes a passing throttle into a permanent one. The list page says when the next attempt is due, and **Check now** ignores the wait, so you can always try immediately by hand.

### A word on upvotes

The bot **announces** posts. It will not ask anyone to upvote them, and there is deliberately no wording for that anywhere in it.

Asking a Discord server to go and upvote a post is *vote manipulation* under [Reddit's content policy](https://support.reddithelp.com/hc/en-us/articles/360043066412), and it is one of the few things Reddit enforces hard — not only against the account that posted, but against the accounts that keep answering the call. It is easy to spot: the same handful of accounts voting within minutes of the same author posting is exactly what shows up in the voting timeline, however the Discord message is worded.

Asking people to **read and comment** is fine, and comments and real discussion weigh more in Reddit's own ranking than a few early votes do. That is what the suggested templates say.

### Setting one up

1. Open the web UI, pick your server, go to **📣 Reddit** and press **+ Watch a feed**.
2. Choose **Reddit user** or **Subreddit** and give the name. The bare name (`TaskForcePhalanx`), the `u/` or `r/` form, or the page URL pasted straight from the address bar all work.

   > A **Reddit user** watch covers *everything that account submits*, in any subreddit — plus anything it posts to its own profile page. It is not limited to one community. A **Subreddit** watch is the other way round: every new post in that subreddit, whoever writes it.
3. Pick the channel to announce in.
4. Optionally, fill in **Only these accounts** to announce just those authors' posts. See [following someone whose profile hides their posts](#following-someone-whose-profile-hides-their-posts).
5. Write the message. Four placeholders are filled in for you:

   | Placeholder | Becomes |
   |---|---|
   | `{title}` | The post's title |
   | `{url}` | A link to the post |
   | `{author}` | The Reddit account that posted it |
   | `{subreddit}` | The subreddit it was posted in — for a post made on the author's own profile, that is `u_TheirName`, which is where such posts actually live |

   Leave it empty and the default is used. Discord unfurls the link on its own, so the title and thumbnail appear underneath the message without you doing anything.
6. Tick the **roles** to ping, and list any **people** by user ID (right-click a member → *Copy User ID*, with Developer Mode on — there is no dropdown, because the bot cannot list your members without the privileged members intent).
7. Save.

### Following someone whose profile hides their posts

Reddit lets an account hide its posts from its own profile page (Settings → **Curate your profile** → **Content and activities**). A **Reddit user** watch reads exactly that profile listing, so when the setting is on it comes back nearly empty — the bot sees what a logged-out visitor sees.

The posts themselves are not hidden: the subreddit they were written in still lists them. So watch the **subreddit** instead, and put the account's name in **Only these accounts**. Every new post in that subreddit is read, and only that author's are announced. Several names are allowed, separated by spaces.

Two limits worth knowing:

- A very busy subreddit can push a post out of its own feed between two checks — the feed carries roughly the last 25 posts, and the bot looks every five minutes.
- Adding an account to the filter makes their posts *new* to the watch, so the next check would announce whatever of theirs is still on the feed. The save warns about it; open **Recent posts** and mark what you don't want first.

The first check after that notes down what is already on the feed and announces **nothing** — otherwise switching a watch on would post the author's last 25 submissions at once. From then on, every new post is announced, within about five minutes of going up.

### The buttons

- **Preview newest post** — shows the newest post rendered exactly as it would be announced, using whatever is currently in the form. It posts nothing and marks nothing as seen, so press it as often as you like while working on the text.
- **Check now** — runs the scheduled check immediately. This one *does* announce anything it hasn't announced before.
- **Recent posts** — lists everything the feed still carries, saying which have already been announced, with an **Announce** button on each.

### Catching a post up

If a post never made it into the channel — the bot was down when it went up, Discord refused that one message, or you only set the watch up afterwards — open **Recent posts** and press **Announce** on it. It goes out exactly as the scheduled check would have posted it, pings included, and is then marked as announced so the next check doesn't repeat it. Each row can be expanded to see the message first.

The one limit is that only what the feed still lists can be caught up: Reddit's feed carries roughly the last 25 submissions, and anything older than that is out of reach.

### Stopping a flood

Tick the posts you *don't* want announced and press **Mark ticked as announced** — or **Mark all** for the whole list. Nothing is posted; the posts simply leave the queue.

That is what to do when a feed suddenly fills up — an account that un-hid its posts, a subreddit that was quiet for a month. Without it the bot works through the backlog three posts at a time, every five minutes, until all of it has been announced. Mark them, and only what comes *after* that point reaches the channel.

Marking needs no connection to Reddit: the page has already read the feed, so ticking and marking works even while Reddit is refusing the server — which is exactly when a backlog is waiting.

The page also names **the exact address it read and how many entries came back**, with a link to open it yourself. That is the answer to "why hasn't it announced X": if a post is missing from that page, it is missing from the feed, not being skipped by the bot. Each post says where it lives too — `r/arma`, or *the author's own profile* for one posted to `u/TheirName`.

### Good to know

- **Pings are limited to what you ticked.** A post title containing `@everyone` cannot ping your server: the announcement quotes Reddit, so only the roles and people on the watch are allowed to be mentioned.
- **A burst is spread out.** At most three posts are announced per check; the rest follow on the next one. Nothing is skipped.
- **Errors are visible.** A misspelled name, a private or suspended account, a channel the bot lost access to — the reason for the last failed check is shown next to the watch on the list. If Reddit ever answers with something that isn't a feed, the message quotes the part it choked on, so the cause is visible rather than only its line number.
- **Repointing a watch resets it.** Change which user or subreddit it follows and it starts fresh from that feed's current posts, rather than announcing its back catalogue.
- **A watch with no channel is simply idle** — untick *Check this feed* to park one without losing its text.
- **Persistent `429`s are a hosting problem, not a settings problem** — see above. If a watch is refused from both hosts every single time, the public feed is not usable from that server's address, and the way through is Reddit's OAuth API with a registered script app rather than any change to how often it checks.

---

## Tactical maps

The mission plan, drawn on the terrain and shared with a link. It lives entirely in the [web interface](#web-ui) — there is no slash command, because dragging a symbol onto a hillside is not something a Discord modal can do.

Open **Operations → Maps**. Every member of the server can read the maps; creating one and drawing on it needs the `Unit Leader` role or Manage Server.

### Drawing

| Tool | What it places | Key |
|---|---|---|
| **Select** | Pick something up and move it; drag the background to pan | `V` |
| **Unit** | An APP-6 symbol — infantry, armour, mortars, air, medical, supply… — with its size mark | `U` |
| **Marker** | One of Arma's task markers — objective, destroy, flag, warning, pick-up… with optional text | `M` |
| **Line** | A route, a boundary or a phase line | `L` |
| **Area** | A shaded boundary or a suspected position | `A` |
| **Text** | A free label — phase names, timings | `T` |

Pick the side (friendly, hostile, neutral, civilian, unknown), the layer, the symbol, the size mark, whether it is in the air and whether it is there yet or only planned, in the toolbar before you place something; everything can be changed afterwards in the panel on the right, which also holds the mobility indicator and the parent unit.

**Drawing a line is like the game:** press the left button and drag. With the **Line** or **Area** tool that is all there is to it; from any other tool — including **Select** — hold `Ctrl` while you drag. Letting go finishes the line, `Esc` throws the stroke away, and the **Area** tool closes the shape into an area.

Lines and areas can be **red, orange, yellow, green, blue, cyan, pink, purple, white or black**, picked from the swatches in the toolbar before you draw or in the panel afterwards — or any other colour from the colour field beside them. A unit symbol keeps its side's colour, because that is what says whose it is. A line gets **no arrowhead unless you tick one**: most lines on a plan are boundaries and phase lines, not directions of attack.

The symbols follow APP-6, the way the planning tools and pocket cards do: the frame's **shape** says whose a unit is, so the plan still reads when it is printed, projected, or looked at by somebody who is colour-blind.

- **Scroll** to zoom, **drag the background** to pan, **⛶ Full screen** gives the map the whole window
- `Del` removes what is selected, `Ctrl`+`Z` undoes, `Ctrl`+`S` saves
- **Nothing is saved until you press Save** — the page warns you if you try to leave with unsaved work
- Tick **HQ** on a unit to give it the headquarters staff
- Symbols are deliberately small — twenty of them on one sheet still has to be readable — and the **Size** slider goes a long way further down for a crowded area

### Layers

A map holds up to twelve named layers, each with its own switch: *Phase 1*, *Phase 2*, *Feindlage*. Add one in the **Layers** panel, pick which layer you are drawing on in the toolbar, and move anything between layers from its properties.

- **Hiding a layer hides nothing permanently** — what is on it stays in the map and stays saved. The switch only decides what is drawn.
- **Anyone you send the share link to gets the same switches**, so one map covers the whole operation instead of one map per phase.
- What is on a hidden layer **stays out of the Arma export** as well: markers you switched off are not part of the plan you are handing over, and Arma has no switch to turn them off again.
- Areas are drawn under lines, lines under symbols, so nothing gets buried under a boundary somebody drew later. **Bring to front** decides between two of the same kind.

### The background

Best: **upload the terrain**. The tile archives you already prepare for OCAP work as they are — a zip or 7z holding the numbered folders `0/ 1/ 2/ …` and, if it came from OCAP, a `map.json`. Go to **Terrains** (linked from the Maps page), upload it once, and from then on every map can be put on it from the editor's **🗻 Put it on a terrain** panel.

Uploading is worth the storage for one reason: the bot then serves the tiles itself, so **nothing outside has to be reachable** — not for your members, and not for whoever you send a share link to.

- The name and the world size are read out of `map.json`, so usually you only pick the file. Without one, type the world size (Altis 30720, Tanoa 15360, Stratis 8192).
- **Deepest level to keep** decides the size: each step is four times the tiles. Level 4 is around 7 MB per terrain and is plenty for planning; deeper levels in the archive are simply left out, and the reply tells you how many.
- A terrain can't be deleted while a map is drawn on it — the reply names the maps.

Alternative: **point at a running OCAP server**. Open **🛰️ Load a terrain from OCAP** — the directory field starts filled in, so for most people it is just **List the terrains** — or give it the folder OCAP keeps its terrains in — e.g. `https://ocap.your-unit.net/images/maps` — and press **List the terrains**: everything in it comes back as a dropdown, so you never have to remember that Cham is `tem_cham`. The address is saved for the server, so it is typed once. Same terrain, same automatic Arma coordinates, nothing stored here; the catch is that everyone opening the map loads the tiles from that server, so it has to be reachable for them too. If your OCAP does not list its directory, **Paste an address instead** takes one map folder's URL as before.

Or paste the URL of **any image** the browser can load — a terrain screenshot, a map export — under **Map settings**. It is stretched across the sheet, so pick the sheet shape (square, landscape, portrait) that matches it, and set the Arma corners yourself if you want the export.

However the background got there, **Detail** under *Map settings* picks how deep into the tile pyramid to draw, an optional grid can be laid over the top, and the map works with no background at all: a plain dark sheet with a grid is enough for a schematic.

### Town names

A terrain arrives with **no labels on it** — the tiles come out of Arma's own map
export as pure topography, and the game draws the names over that afterwards.
Where they can be read from depends on how the terrain was built. OCAP's
**current** maptool builds one from a [grad_meh](https://github.com/gruppe-adler/grad_meh)
export, which writes the locations beside the tiles as
`geojson/locations/<type>.geojson.gz`, one file per Arma location type — so on
such a server this is a read, not a trip into the game. Its **older** route is
raster tiles only and carries none.

**On an OCAP terrain:** open the map → *Terrain and background* → **Place names
from OCAP** → **Import place names**. It reads the server the map points at.

**On a terrain uploaded here:** the archive's own `locations` files are read on
upload if it has them.

**It is decided per terrain, not per server.** The `.emf` → gdal2tiles route in
OCAP's own tile guide produces raster tiles and no vector data at all, so no
amount of probing will turn up locations for a terrain built that way — while
the terrain beside it on the same OCAP, imported from a grad_meh export, gives
up its names in one press. That is a property of how each terrain was built, not
of the server and not of the import. A failure therefore names the terrain and
**every address it tried**, so you can tell it apart from a broken feature.

**When there is nothing to import**, the same panel carries a script and a paste
box. Run the script in a mission on that terrain — debug console, LOCAL EXEC —
and paste what lands on your clipboard into the box. That is **once for the
terrain**: not once per map, not once per operation, and every plan drawn on it
afterwards has them.

The box is on the map rather than only on the Terrains page because a map backed
by an OCAP server has no terrain row there to paste into — which for a while
made the one remaining path unreachable for exactly the people who needed it.

Names belong to the **terrain**, not to one map, so this is once per terrain and
every plan drawn on it gains them — including maps on an OCAP server, which have
no terrain record here but are matched by the folder's world name.

They are drawn **under the plan**, so a symbol you place is never hidden behind a
village name, and they scale by rank the way a paper map does — a capital larger
and letter-spaced, a rock small. Nameless entries are dropped: Arma's terrains
are full of `FlatArea` helpers that exist to position things and have nothing to
say.

Because you may well want all of them on one terrain and almost none on a
briefing sheet, the editor's background panel carries a **switch per group** —
towns and villages, hills and landmarks, water and coast, places of interest,
everything else — plus a size slider. That setting belongs to the map; the names
themselves belong to the terrain.

Place names are never exported to Arma: the game draws its own.

### Sharing it

A map is private to people who can sign in here until you give it a link. Under **Share link**, choose:

- **Not shared** — only people signed in here can open it
- **Anyone with the link can look at it** — for the people who just need the plan
- **Anyone with the link can draw on it** — for co-planning with somebody who is not on the server

The link looks like `https://your-domain/m/xxxxxxxx`, works without a Discord login, and **Replace the link** issues a new one — the old link stops opening the map immediately, for everyone who has it. That is how you take a plan back once it has been forwarded further than you meant.

If the site answers on more than one name, the link always carries the **first** one in `WEB_BASE_URL` — even if you made it while browsing the other. That is deliberate: a share link is copied to somebody who was never on the site, so it has to be the name you want people to see. The same goes for the link the bot posts into a channel. A map **already announced** in Discord keeps the link its message was posted with; post it again to refresh that.

**Copying a plan.** **Duplicate** gives you the whole plan again under a new name — every symbol, line and layer, on the same terrain. The share link deliberately does *not* come with it, so a copy made to try something out is not reachable by everyone who already has the original's link. **Rename** is the **Name and description** form on the same page.

### Posting it in a channel

**Post in a channel** sends an embed with the map's name, what is on it and a link. It posts a link rather than a picture on purpose: the plan usually keeps changing after the briefing is announced, and a link is always current where an image is not. If the map has a share link, that is what gets posted; otherwise the link only opens for people who can sign in here.

### Putting the plan into Arma 3

The map can be dropped into a **running mission** as ordinary Arma markers, with **no mod** on the server or the client. Arma cannot fetch anything from outside without one, so the way in is the debug console:

1. Make sure the coordinates are set. Loading the terrain from OCAP does this for you; otherwise pick the terrain under **Map settings → Arma 3 coordinates**, or type the corner coordinates if your background image is a crop of the map. Get this right before the first export, or everything lands on the wrong terrain.
2. Save the map, then open **🎯 Put it into Arma 3**. There are **two scripts** there, side by side — copy one, or download it as a `.sqf`:
   - **1 · Fixed markers** — nobody can move or delete them in game. This is the one for a plan that should stay as briefed, and the one to use unless you specifically want the other.
   - **2 · Markers you can move and delete** — the same plan, but named the way Arma names a player's own markers, so whoever ran the script can drag a symbol or delete it with `DEL`. For planning in game, at the map table. The same click that fixes a misplaced objective deletes it, and nothing asks first. This one also lets you pick which **map channel** the markers sit in, so a plan meant for one side need not be in Global.
3. In game, log in as admin (`#login <password>`), open the debug console, paste it and press **LOCAL EXEC** — for both scripts, once, on one machine.

**LOCAL EXEC, not GLOBAL**, even though every player sees the markers: `createMarker` is global by itself, so one machine running the script draws them for everybody. GLOBAL EXEC runs the script on *every* machine instead, which is wasted on the first script and actively wrong on the second, where each marker's name carries the id of whoever ran it — you would get one plan per player.

Pasting a script again after changing the plan **replaces** that map's markers instead of adding a second copy — each map owns its own marker names.

The mission has to allow the debug console: `enableDebugConsole = 1;` (admins) or `2` (everyone) in its `description.ext`. Lines and areas need Arma **2.00 or newer**, which is anything current.

What comes across:

| On the map | In Arma |
|---|---|
| Unit symbols | The matching NATO marker, in the side's colour — `b_inf`, `o_armor`, `n_med` … |
| Anything in the air | That side's `air` marker, whatever branch the symbol was |
| Headquarters | The `hq` marker of that side |
| Size, strength, mobility, parent unit, planned | Written into the marker's name: `1-1 Alpha / A Coy (planned Plt twd)` |
| Markers (OBJ, TGT, RP …) | `mil_objective`, `mil_destroy`, `mil_dot` and friends, with the text |
| Lines and areas | Polyline markers; an area closes itself, a line's arrow becomes a `mil_arrow` |
| Text labels | An empty marker carrying the text |

Anti-tank, snipers and a few others have no marker in vanilla Arma, so they come across as the nearest one that exists. Dashed lines arrive solid, and so does a planned symbol's dashed frame — Arma has no dashed marker, which is why "planned" is written into the name instead.

### Good to know

- **One map holds 400 items**, a line up to 120 points, labels up to 48 characters
- **Two people drawing at the same time will overwrite each other** when they save — agree who has the pen, or share a read-only link and keep the drawing to one person
- **Copy** is how you build phase 2: same plan, new name, no share link of its own
- The map renders **without JavaScript** as well, so a read-only link opens on anything

---

## Web UI

An optional browser interface for most of what the bot does — events, the slot roster and its approvals, tactical maps, game roles, embeds and the logs — without touching a slash command. You sign in with your Discord account, and everything you do goes through the same code as the slash commands, so it produces the same messages, in the same channels, with the same buttons.

It is **off until you configure it**. With `DISCORD_CLIENT_ID`, `DISCORD_CLIENT_SECRET` and `WEB_SECRET_KEY` unset, the bot starts exactly as it always did and opens no HTTP port. The startup log says which of the three is missing.

### What it does

| Page | What you get |
|---|---|
| Sign-in | **Continue with Discord** — OAuth2, `identify` scope only |
| Help | Every how-to, searchable — reachable signed out, at `/help` |
| Server picker | Every server you and the bot are both in (skipped when there is only one) |
| Events | Upcoming events with live sign-up counts, plus recently finished and cancelled ones |
| Event | Full details, the attendee list per response, and RSVP buttons for yourself |
| New / Edit | Title, start, duration, description, location, channel, ping roles, reminder, repeat pattern, custom sign-up buttons, banner image |
| Cancel | Reason field, DMs everyone attending, optionally stops the whole series |
| Delete | Confirmation page stating the sign-up count, then removes the event and its message |
| **Operations** | One group holding the three pages that are always about the same evening: |
| · Operation | Start an operation, set its start time, post the board and the announcement, empty the queue, read the raw roster |
| · Slot Approvals | Approve, deny or withdraw the pending requests, release a booked slot, or put somebody on one outright |
| · ORBATs | Build and edit the slot roster |
| · Maps | Draw the tactical plan, share it with a link, post it in a channel |
| · Terrains | Upload the tile archives the maps are drawn on, and see what each one costs (linked from Maps) |
| · Settings | Which channels the bot posts into, and the server timezone |
| Game roles | Tick the games you play; admins add and remove roles and post the self-assign panel |
| Embeds | Build rich messages, post them, and edit the posted message in place |
| Member log | Announce joins, leaves, kicks, bans and unbans in a channel, and welcome new members in a channel or by DM |
| Reddit | Watch a Reddit user or subreddit and announce new posts, with your own text and pings |
| Voice time | Leaderboard of time spent in voice channels; admins configure what counts |

Who may do what is **read live from your Discord roles**, not from the login:

- **Any member of the server** — view events, RSVP, pick their own game roles, see the voice leaderboard, read the tactical maps
- **Unit Leader or Manage Server** — create events; draw, share and post tactical maps; approve and deny slot requests (Unit Leaders for their own unit only)
- **The organiser, or an admin** — edit, cancel and delete that event
- **Manage Server** — add and remove game roles, post the self-assign panel, build embeds, build ORBATs, watch Reddit feeds, upload and delete terrains, configure the member log and voice tracking

That is the same rule set the slash commands use; it is literally the same code. Roles are cached for a minute, so if you have just been given a role, the **“Changed your roles on Discord? Re-read them”** link at the bottom of the event list picks it up immediately.

Times are entered and displayed in the **server timezone** (`/set-timezone`), the same as every time you type into a slash command.

### Setting it up

**1. Create the OAuth2 credentials.** In the [Discord Developer Portal](https://discord.com/developers/applications) → your application → **OAuth2**:

- copy the **Client ID** and generate a **Client Secret**
- under **Redirects**, add `https://your-domain/auth/callback` — it must match `WEB_BASE_URL` exactly, including `https://` and with no trailing slash. **Serving the site on two names?** Put both in `WEB_BASE_URL`, separated by a space, and add a redirect URI here for each — the site then keeps whoever opened one of them on that one

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

- **Railway** — service → **Settings → Networking → Generate Domain**. Railway injects `PORT` and the app listens on it; use that domain as `WEB_BASE_URL`. A domain of your own goes in the same place (**Custom Domain**, then the CNAME it gives you at your DNS provider); put both names in `WEB_BASE_URL` if the old one should keep working — **your own domain first**, because the first name is the one share links carry. Each name also needs its own redirect URI in the Developer Portal.
- **Docker** — port `8080` is published by `docker-compose.yml`; put it behind a reverse proxy that terminates TLS (Caddy or nginx), and point `WEB_BASE_URL` at the public name.

Restart the bot. The log line `✅ Web UI listening on …` means it is up; `/healthz` answers `ok` once the bot is connected to Discord.

### Name and logo

The site is called **TFP BOT**. Set `WEB_BRAND` to rename it — that string is the header, the browser tab title and the footer.

For the logo, commit an image to **`web/static/logo.png`** (`.webp`, `.svg`, `.jpg` also work). It is picked up on the next start and appears next to the name in the header, large on the sign-in page, as the browser-tab icon, and — when `WEB_BASE_URL` is set — as the preview image when the site's link is pasted into Discord or Slack. Square images look best; anything else is fitted rather than squashed. With no such file, the name shows on its own and the tab falls back to a 🛡️ emoji — nothing breaks.

### The help pages

Every how-to in [How-tos](#how-tos) is also a page on the site, at **`/help`**. There are two ways in:

- the round **?** in the site header, which opens the index — searchable, and grouped the same way as this README;
- a **?** at the end of the tab row on every page, which opens *that page's* how-to directly. On **📋 Member log** it lands on *Log joins, leaves, kicks and bans*, on **🔊 Voice time** on *Track time in voice*, and so on.

Both work **signed out**, which is the point of putting them outside the server pages: somebody who cannot get past the sign-in screen is exactly the person who needs to read one.

**There is only one copy of this text.** The topics live in `utils/help.py`, the site renders them, and `python scripts/gen_help.py` writes the same thing into this README between its `help:start` and `help:end` markers — so the two cannot drift apart. `tests/test_help.py` goes further and reads the command names straight out of `cogs/`: adding a slash command without writing a how-to for it makes the test suite fail. Run `python scripts/gen_help.py --check` to find out whether the README has fallen behind.

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

#### Welcome messages

The same page also **greets the member**, which is a different job from the log above and is configured separately. The log is written for staff in whatever channel they watch; the welcome is written for the person who just arrived.

- **Pick a channel** for the greeting — a public one, usually — or leave it on *Nowhere* if you only want the DM.
- **Tick *Also send it as a DM*** to send the member the same text directly. Both, either or neither is fine.
- **Write the text**, or leave it empty for the default.

Four placeholders are filled in: `{member}` pings them, `{name}` is their name as plain text, `{server}` is the server name, and `{member_count}` is how many members there now are. Everything else is literal, so a stray `{` in your wording is harmless.

Neither destination depends on the join log being switched on, or on *Announce joins* being ticked — but the welcome does use the same join event, so it needs the same **Server Members Intent** as the join log. A member with DMs closed simply doesn't get the DM; nothing fails, and the channel message still goes out.

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

### Slot Approvals

**📋 Slot Approvals** shows the pending requests for the operation that is running right now, with **✅ Approve** and **❌ Deny** next to each one and a field for an optional denial reason. Below them, who already holds a slot.

Deciding here does **exactly** what the buttons in `#slot-approvals` do — it is the same code: the member is DMed, the request leaves `#slot-approvals` and is archived in `#approval-archive`, the live ORBAT board refreshes, and anyone else who wanted that slot is denied and told. You can work through a queue in Discord and finish it in the browser without anything noticing.

Two things to know:

- **Unit Leaders see every request but can only decide their own unit's.** A row you may not action says so (*“CNTO only”*) instead of showing the buttons — you can still see how many people are waiting. Admins can action everything.
- **A slot two people want is marked `contested`.** That is the case where approving is a choice rather than a formality, and it is not otherwise visible: in Discord it is simply two separate messages.

**Taking somebody off a slot is on this page too.** A booked slot has a **🧹 Release** button and a request nobody has decided yet has **🧹 Withdraw** — the same two halves `/clear-slot` offers in one dropdown, and the same code again. The slot goes back on the board immediately, the member is DMed and told who removed them, and on a sheet-backed operation the cell is restored to `[] <Insert Name>`. Neither can be undone, so both ask first.

Withdrawing is not a denial: nothing is written to `#approval-archive` and the member is not told a reason. Use **❌ Deny** when the answer is no, and **🧹 Withdraw** when the request should simply stop being in the queue.

**Putting somebody on a slot outright** is the box at the top — the same as `/assign-slot`. Type who (a name, an `@mention`, or a Discord ID), pick a free slot, and they are on the roster with no request and no approval. There is no member dropdown because the bot deliberately does not hold a copy of your member list; a name is searched for when you submit, and if it matches more than one person you are told who, with their IDs.

A Unit Leader can only assign members of their own unit, and needs a unit role themselves — slightly stricter than deciding a request, because choosing who goes on the roster is not the same as answering somebody who asked.

Requesting a slot is still Discord-side — this page is for deciding requests, not making them.

---

### Operation

**🎖️ Operations** is a group of four pages, with a second row of tabs under the main one. **Operation** is the admin half of the slot system, and everything on it has a slash command behind it doing exactly the same thing.

The page opens on what matters most weeks — which operation is running, how full it is, and when it starts. Everything else is a row you click to unfold, so the page stays short:

| On the page | Same as | What it does |
|---|---|---|
| **Start an operation** | `/setup-slots` | Archives the operation running now, loads an ORBAT or a Google Sheet as the new one, and posts the live board |
| **Start time** | `/set-event-time` | Moves the start and re-arms the reminder, so it fires again for the new time |
| **Post the live board** | `/post-orbat` | A fresh board in the channel you pick. It becomes the one that updates; the previous one stops |
| **Post an announcement** | `/post-event` | The "we play at 19:00, sign up here" message, linking to the ORBAT channel |
| **Empty the queue** | `/clear-requests` | Cancels every request still waiting. Nobody is DMed and nothing is archived — this resets a queue, it does not turn people down |
| **The roster as the bot reads it** | `/debug-slots` | Every slot with the key it is booked against — for when one is missing from the board |

The header is `/current-operation`: which operation is live, whether it runs on an ORBAT or a sheet, and how many slots are open, pending and filled.

### Settings

The last tab in the group, for the two things you set once rather than every week.

**Timezone** — `/set-timezone`. What a time typed anywhere on this site or into a command means. The Discord messages localise themselves for each reader either way, so this is about *input* only.

#### Channels

**Where the bot posts is now yours to choose.** Three channels, each with a **Default** option that is selected until you change it:

| Channel | Default | Used for |
|---|---|---|
| ORBAT board | `#orbat` | The live board, and the reminder ping before an operation starts |
| Slot approvals | `#slot-approvals` | Where a new request goes to be decided |
| Approval archive | `#approval-archive` | The record of every request that was decided |

Leaving one on **Default** means exactly what the bot did before this setting existed: it uses the channel of that name and creates it if it is missing. Nothing changes for a server that never opens this form. If you pick a channel and later delete it, the bot falls back to the default name rather than posting nowhere — and the form says so.

`/sync`, `/restart` and `/archive-old-approvals` stayed slash commands: they are bot maintenance and a one-off migration rather than parts of running an operation.

---

### ORBATs

The slot roster, written out on a page instead of kept in a Google Sheet. **Manage Server** only, under the **🗺️ ORBATs** tab.

An ORBAT is a **template**: the same one is meant to back as many operation nights as you like, which is what duplicating the sheet used to be for. **Copy** takes the structure and none of the bookings.

**Which ORBAT is live** is marked in the list with a red 🔴 chip naming the operation running on it — only one can carry it, because a server has one active operation at a time. Opening that ORBAT in the editor says the same thing at the top, since an edit there changes tonight's board, and the **Start an operation** dropdown on the Operation tab marks it too.

**Deleting one is refused while an operation is running on it** — say so with `/setup-slots` for the next operation first. Once it is no longer the live one, deleting takes its squads and slots with it and everybody booked into it comes off the roster, the same as deleting their slot in the editor would.

**Renaming and copying.** The **Name and description** form under the editor renames an ORBAT; the description is only ever shown in the list, to tell two similar rosters apart. **Duplicate** gives you a copy of the squads, slots and nets — and none of the bookings, which is the point: it is how you build next week's roster from this week's without touching the one tonight's operation is running on.

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

Everything after the pipe belongs to the **squad**, and several options are **separated by commas** — `| left, unit:TFP, radio:343 CHN:3`:

- `left` / `right` put it in that column of the board
- `unit:TFP` marks the whole squad as one unit's — the tag is the unit's role name, and you get a warning if it matches none of them
- `radio:343 CHN:3` is the channel that squad talks on internally, shown under its name on the board

Leaving a comma out still works — `| left unit:TFP` is read as both — but the comma is the form to rely on.
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

### Exporting to a Google Sheet

At the bottom of the editor there is an **Export to a Google Sheet** form. Paste the sheet's URL and the roster is written into a **new tab**, named after the ORBAT and the time. An existing tab is never touched, so you cannot overwrite a sheet another operation is running on; a second export with the same name gets a `(2)` suffix instead of replacing the first.

Tick **Include who is booked in** to export the current operation's board rather than the empty roster. That only works while the active operation actually runs on this ORBAT.

The layout is the one the bot itself understands — squad header, `1. Squad Leader` beside `[] <Insert Name>` — so the tab looks like the sheets you already keep by hand, and if you ever move it to first position the bot can read it as a sheet-backed operation again.

It is strictly one-way: nothing is read back, no link is stored, and there is no sync. The sheet has to be shared with the service account as an editor, which means this is the one ORBAT feature that does need `GOOGLE_CREDENTIALS`.

### Running an operation on an ORBAT

Once the roster is written, start an operation on it from Discord:

```
/setup-slots orbat:Platoon ORBAT event_time:14/09/2026 19:00
```

`orbat` autocompletes over the ORBATs on your server. From there everything works as it always did — `/request-slot`, the **📋 Request a Slot** button, the approval buttons in `#slot-approvals`, `/assign-slot`, `/clear-slot` — except that no Google Sheet is involved anywhere. The board in `#orbat` shows each squad's unit and radio channel and the shared net list underneath.

The old way still works: give `sheet_url` instead of `orbat` and the operation is sheet-backed exactly as before. You give one or the other, never both.

Two things get better on the ORBAT side. Approving is instant, because there is no spreadsheet to write to and therefore nothing that can fail halfway and need re-approving. And the board no longer re-reads a Google Sheet on every refresh.

> **Still Discord-side:** requesting and approving slots. Clicking a slot on the web page is not built yet, and neither is entering somebody who is not on your Discord.

### Limits

- ORBATs can be built here and an operation can run on one, but requesting and approving slots is still Discord-side
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

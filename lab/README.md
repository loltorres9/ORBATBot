# ORBAT Lab — stage A

A standalone prototype of the idea behind lifting the ORBAT out of Google
Sheets and building it in the browser. **It changes nothing about the bot.** No
file outside `lab/` was touched, nothing in here is imported by `bot.py` or
`web/`, and `lab/` imports neither `cogs/` nor `web/` nor discord.py.

Its purpose was to answer the one open question — can an ORBAT be built and
maintained on one page **without JavaScript**? Everything else (Discord, OAuth,
permissions, Sheets) is deliberately absent.

## Running it

```bash
pip install fastapi uvicorn jinja2 python-multipart
python -m uvicorn lab.devserver:app --reload --port 8081
# → http://127.0.0.1:8081
```

On the first start `lab/seed.py` creates an example ORBAT with an operation and
a few bookings, so the page is not empty. It is stored in `lab/orbat_lab.db`
(SQLite, covered by `.gitignore`). Deleting that file resets it. `LAB_DB=/path/to.db`
puts it somewhere else.

Tests:

```bash
pip install pytest && python -m pytest lab/tests -q
```

## What is in here

| File | What it is |
|---|---|
| `parser.py` | The text format → squads and slots. Pure, no dependencies. |
| `diff.py` | Compares the new text against what is stored, so slot ids — and with them the bookings — survive an edit. |
| `render.py` | Building the board, plus the check against Discord's embed limits. |
| `store.py` | SQLite in the shape the PostgreSQL schema would have. |
| `devserver.py` | The pages. |
| `seed.py` | The example ORBAT. |

## The text format

```
1-1 Alpha  | right
  Squad Leader  | unit:TFP
  Rifleman

Reservists  | right, nocount
  Reserve
```

Squad lines start at the left margin, slots are indented. Options after `|`:
`left` / `right` / `nocount` on a squad, `unit:TAG` on a slot. `#` at the start
of a line is a comment. A leading number ("1. Rifleman") is stripped, so lines
pasted out of a sheet land clean.

## The three decisions this was built to test

**One text field rather than a slot editor.** `web/` has no JavaScript and no
build step, so the alternative would have been an up/down button per row. Text
can be reordered freely without any of that, and it is how ORBATs get written
down anyway. The price: there is no live update — the preview is a button.

**A slot carries no booking.** Who holds one lives in `lab_bookings`, keyed on
`(operation, slot)` — which is where production already keeps it, in
`requests`. That is what makes an ORBAT a template carrying any number of
operations without anything having to be reset.

**An edit must never unseat anybody silently.** `diff.py` matches squads and
slots by name first and by position second, so a rename keeps the id and with
it the booking. Anything that takes somebody off a slot *or* moves them to a
different role goes through a confirmation page that names the people affected.

## Where the lab is behind the real editor

The ORBAT editor in `web/` is the real implementation now. The lab shares its
parser (`utils/orbat.py`) but has **no net list** — the shared radio nets exist
only in the real editor. A squad's own channel (`radio:`) is stored here.

## What stage A does not answer

Permissions and OAuth, the approval flow against real unit roles, view
persistence across restarts, and migrating requests that are already running.
Those need stage B (the lab inside the bot process behind `ORBAT_LAB=1`) and
stage C (a test bot of its own).

## Cleaning up

`rm -rf lab/` — nothing else.

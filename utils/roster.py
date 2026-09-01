"""One roster, whichever side it came from.

An operation is backed either by a Google Sheet (`operations.sheet_url`) or by
an ORBAT held in the database (`operations.orbat_id`). Everything above this
module — the request flow, the approval buttons, the live board — works on the
normalised slot below and never asks which it was.

The slot's identity is its **key**: `db:412` for an ORBAT slot, `sheet:r12c4`
for a spreadsheet cell. That is what replaced the `(sheet_row, sheet_col)` pair
the whole flow used to compare on, and it is why a request row can point at
either kind without anything downstream branching.

A normalised slot:

    key          'db:412' | 'sheet:r12c4' — hashable, stable, unique
    slot_id      the ORBAT slot, or None
    row, col     the sheet cell, or None
    squad, role  what it is
    label        "1-1 Alpha – Rifleman", what a request records
    value        == key; the select menus use it as the option value
    assigned_to  who holds it, or None
    col_idx      layout hint: the sheet column, or 0/1 from the ORBAT
    excluded     left out of the counts (Reservists, `nocount`)
    unit, radio  the squad's unit tag and internal channel, ORBAT only
"""

import asyncio

from utils import database, sheets

# Reading a sheet is a network round trip; the same 30 s ceiling the callers
# used before this module existed.
SHEET_TIMEOUT = 30

# A squad the counts leave out. On a sheet this is all we have to go on; an
# ORBAT says so outright with `exclude_from_count`.
_EXCLUDED_SQUAD = 'reservists'


def is_db_backed(op) -> bool:
    """Whether this operation's roster lives in the database."""
    return bool(op and op['orbat_id'])


def slot_key(slot_id=None, sheet_row=None, sheet_col=None) -> str:
    """The stable identity of one slot, from either side."""
    if slot_id:
        return f'db:{slot_id}'
    if sheet_row is None:
        return ''
    return f'sheet:r{sheet_row}c{sheet_col}' if sheet_col else f'sheet:r{sheet_row}'


def request_key(req) -> str:
    """The same, read off a `requests` row."""
    return slot_key(req['slot_id'], req['sheet_row'], req['sheet_col'])


def _label(squad: str, role: str) -> str:
    label = f"{squad} – {role}"
    return label[:97] + '...' if len(label) > 100 else label


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

async def _load_sheet(op, everything: bool) -> dict:
    loader = sheets.load_all_slots if everything else sheets.load_slots
    loop = asyncio.get_event_loop()
    data = await asyncio.wait_for(
        loop.run_in_executor(None, loader, op['sheet_url']),
        timeout=SHEET_TIMEOUT,
    )
    slots = []
    for slot in data['slots']:
        key = slot_key(None, slot['row'], slot.get('col'))
        slots.append({
            'key': key, 'value': key, 'slot_id': None,
            'row': slot['row'], 'col': slot.get('col'),
            'squad': slot['squad'], 'role': slot['role'],
            'label': slot.get('label') or _label(slot['squad'], slot['role']),
            'assigned_to': slot.get('assigned_to'),
            'col_idx': slot.get('col_idx', 0),
            'excluded': slot['squad'].lower() == _EXCLUDED_SQUAD,
            'unit': None, 'radio': None,
        })
    return {'operation_name': data['operation_name'], 'slots': slots, 'nets': []}


async def _load_orbat(op) -> dict:
    squads = await database.get_orbat_structure(op['orbat_id'], operation_id=op['id'])
    slots = []
    for squad in squads:
        for slot in squad['slots']:
            booking = slot['booking']
            key = slot_key(slot['id'])
            slots.append({
                'key': key, 'value': key, 'slot_id': slot['id'],
                'row': None, 'col': None,
                'squad': squad['name'], 'role': slot['role_name'],
                'label': _label(squad['name'], slot['role_name']),
                'assigned_to': booking['member_name'] if booking else None,
                'col_idx': squad['column_side'],
                'excluded': bool(squad['exclude_from_count']),
                'unit': squad['reserved_unit'], 'radio': squad['radio'],
            })
    nets = await database.get_orbat_nets(op['orbat_id'])
    return {'operation_name': op['name'], 'slots': slots, 'nets': nets}


async def load_all(op) -> dict:
    """Every slot, filled ones included — what the board is drawn from."""
    if is_db_backed(op):
        return await _load_orbat(op)
    return await _load_sheet(op, everything=True)


async def load_available(op) -> dict:
    """Only the slots nobody holds — what the pickers offer.

    Approved requests are subtracted as well as whatever the source already
    reports as taken: on a sheet the two can briefly disagree, and the database
    is the one that decides.
    """
    if is_db_backed(op):
        data = await _load_orbat(op)
    else:
        data = await _load_sheet(op, everything=False)
    approved = set(await database.get_approved_slots(op['id']))
    data['slots'] = [
        s for s in data['slots'] if not s['assigned_to'] and s['key'] not in approved
    ]
    return data


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------
#
# On an ORBAT-backed operation these are deliberately nothing: the booking is
# the `requests` row and there is no second copy to keep in step. That is what
# removes the approve path's rollback — the write that used to fail is gone.

async def assign(op, slot, member_name: str, unit_role: str = None) -> None:
    """Record the assignment wherever the roster lives."""
    if is_db_backed(op):
        return
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None, sheets.assign_slot,
        op['sheet_id'], slot['row'], slot.get('col'), member_name, unit_role,
    )


async def clear(op, req) -> None:
    """Release the slot a request held."""
    if is_db_backed(op):
        return
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None, sheets.clear_slot,
        op['sheet_id'], req['sheet_row'], req['sheet_col'], req['member_name'],
    )

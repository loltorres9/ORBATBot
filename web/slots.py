"""Deciding slot requests from the browser: approve, deny, and release.

Every rule about who may decide what, and everything a decision entails — the
roster write, clearing #slot-approvals, the archive record, the DMs, the
competing requests, the board refresh — lives in `cogs/slots.py` and is called
from here. Releasing a slot is the same story with a third function: this page
and `/clear-slot` both go through `clear_slot_request()`. This module only reads
the queue and translates errors, the way `web/service.py` does for events.

That matters more here than anywhere else on the site: an approval is the one
action with consequences in four places at once, and a second implementation of
it for the web would be exactly where the two surfaces drift apart.
"""

from cogs.slots import (
    ActionError,
    _can_action_request,
    approve_slot_request,
    clear_slot_request,
    deny_slot_request,
)
from utils import database, roster

MAX_REASON = 200


async def queue(guild, member) -> dict:
    """The active operation and its requests, annotated for *member*.

    Pending first, because those are the ones asking for a decision; the
    approved list is there so you can see who already holds what.
    """
    operation = await database.get_active_operation(str(guild.id))
    if operation is None:
        return {'operation': None, 'pending': [], 'approved': [], 'source': None}

    rows = await database.get_active_requests(operation['id'])
    pending, approved = [], []
    for row in rows:
        entry = {
            'id': row['id'],
            'member_id': row['member_id'],
            'member_name': row['member_name'],
            'unit_role': row['unit_role'],
            'slot_label': row['slot_label'],
            'created_at': row['created_at'],
            'key': roster.request_key(row),
            # Re-checked per row: a Unit Leader sees the whole queue but can
            # only act on their own unit, and the buttons say so.
            'may_action': _can_action_request(member, row['unit_role']),
        }
        (pending if row['status'] == 'pending' else approved).append(entry)

    # Two people can want the same slot. Marking that on the row is what turns
    # "approve" from a decision about one request into a choice between several.
    counts = {}
    for entry in pending:
        counts[entry['key']] = counts.get(entry['key'], 0) + 1
    for entry in pending:
        entry['contested'] = counts[entry['key']] > 1

    pending.sort(key=lambda e: (e['slot_label'], e['created_at']))
    approved.sort(key=lambda e: e['slot_label'])

    if roster.is_db_backed(operation):
        record = await database.get_orbat(operation['orbat_id'])
        source = f"ORBAT: {record['name']}" if record else 'ORBAT (deleted)'
    else:
        source = 'Google Sheet'

    return {'operation': operation, 'pending': pending,
            'approved': approved, 'source': source}


async def clear(bot, guild, member, request_id: int) -> str:
    """Take somebody off a slot — the page's half of `/clear-slot`.

    Offered on the approved rows *and* on the pending ones, because both are
    what that command lists: a request nobody wants to decide is withdrawn the
    same way a booking is given back.
    """
    try:
        result = await clear_slot_request(bot, guild, request_id, member)
    except ActionError as e:
        raise ValueError(str(e))
    req = result['request']
    if result['was_approved']:
        return f"Released {req['slot_label']} — {req['member_name']} is off the roster."
    return f"Withdrew {req['member_name']}'s request for {req['slot_label']}."


async def approve(bot, guild, member, request_id: int) -> str:
    try:
        result = await approve_slot_request(bot, guild, request_id, member)
    except ActionError as e:
        raise ValueError(str(e))
    note = (f", and {result['competitors']} competing request(s) were denied"
            if result['competitors'] else '')
    return f"Approved {result['request']['member_name']} for "\
           f"{result['request']['slot_label']}{note}."


async def deny(bot, guild, member, request_id: int, reason: str) -> str:
    reason = (reason or '').strip()
    if len(reason) > MAX_REASON:
        raise ValueError(f'Keep the reason under {MAX_REASON} characters.')
    try:
        result = await deny_slot_request(bot, guild, request_id, member, reason)
    except ActionError as e:
        raise ValueError(str(e))
    return f"Denied {result['request']['member_name']} for "\
           f"{result['request']['slot_label']}."

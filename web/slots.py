"""Deciding slot requests from the browser: approve, deny, release, assign.

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

import asyncio
import re

import discord

from cogs.slots import (
    ActionError,
    _can_action_request,
    approve_slot_request,
    assign_slot_request,
    check_can_assign,
    clear_slot_request,
    deny_slot_request,
)
from utils import database, roster

MAX_REASON = 200

# `<@123>`, `<@!123>` or a bare snowflake — what you get from copying a member
# out of Discord, either as an ID or by right-click → Mention.
_MEMBER_ID = re.compile(r'^<@!?(\d{15,25})>$|^(\d{15,25})$')

# Members matched by name come back over the gateway, which can be slow and, on
# a big server, ambiguous. More than this and the answer is "be more specific".
MEMBER_SEARCH_LIMIT = 10


async def queue(guild, member) -> dict:
    """The active operation and its requests, annotated for *member*.

    Pending first, because those are the ones asking for a decision; the
    approved list is there so you can see who already holds what.
    """
    operation = await database.get_active_operation(str(guild.id))
    if operation is None:
        return {'operation': None, 'pending': [], 'approved': [], 'source': None,
                'free_slots': [], 'slots_error': None}

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

    # The Assign form lives on this page, so the free slots come with it. On a
    # sheet-backed operation that is a network read, and one that fails must
    # cost the assign form only — the queue itself is the reason to be here.
    slots, slots_error = [], None
    try:
        slots = await free_slots(operation)
    except Exception as e:
        slots_error = f"Could not read the roster: {e}"

    return {'operation': operation, 'pending': pending, 'approved': approved,
            'source': source, 'free_slots': slots, 'slots_error': slots_error}


async def free_slots(operation) -> list:
    """The slots nobody holds yet, as `(value, label)` pairs for a <select>.

    Ordered by squad and then by the roster's own order, so the list reads down
    the ORBAT the way the board does rather than alphabetically.
    """
    if operation is None:
        return []
    data = await roster.load_available(operation)
    taken = set(await database.get_approved_slots(operation['id']))
    pending = set(await database.get_pending_slots(operation['id']))
    options = []
    for slot in data['slots']:
        if slot['key'] in taken:
            continue
        note = ' — someone has asked for this' if slot['key'] in pending else ''
        options.append((slot['value'], f"{slot['squad']} · {slot['role']}{note}"))
    return options


async def resolve_assignee(guild, raw: str):
    """A member of *guild* from what somebody typed into the Assign box.

    An ID or a mention is fetched directly, which works with no privileged
    intent. Anything else is a name, and goes through `query_members()` — a
    gateway search, which unlike listing the members does not need the intent
    either. Ambiguity is reported rather than guessed at: assigning the wrong
    person is a message to the wrong person and a slot held by somebody who
    does not know they hold it.
    """
    raw = (raw or '').strip()
    if not raw:
        raise ValueError('Type who to assign — a Discord ID, a mention, or a name.')

    match = _MEMBER_ID.match(raw)
    if match:
        member_id = int(match.group(1) or match.group(2))
        try:
            return await guild.fetch_member(member_id)
        except discord.NotFound:
            raise ValueError('Nobody with that ID is in this server.')
        except (discord.Forbidden, discord.HTTPException) as e:
            raise ValueError(f"Could not look that member up: {e}")

    try:
        found = await asyncio.wait_for(
            guild.query_members(query=raw.lstrip('@'), limit=MEMBER_SEARCH_LIMIT),
            timeout=15,
        )
    except asyncio.TimeoutError:
        raise ValueError('Timed out searching for that name — try the Discord ID.')
    except Exception as e:
        raise ValueError(f"Could not search for that name: {e}. Try the Discord ID.")

    if not found:
        raise ValueError(f"No member of this server matches \u201c{raw}\u201d.")
    if len(found) > 1:
        exact = [m for m in found if raw.lower() in (m.name.lower(), (m.nick or '').lower())]
        if len(exact) != 1:
            names = ', '.join(f"{m.display_name} ({m.id})" for m in found[:5])
            raise ValueError(
                f"\u201c{raw}\u201d matches several people: {names}. "
                'Paste the ID of the one you mean.'
            )
        found = exact
    return found[0]


async def assign(bot, guild, actor, request_form) -> str:
    """`/assign-slot` — put somebody on a slot with no request and no approval.

    The member is resolved first and the unit check runs before the slot is even
    looked at, so "you can't assign that person" is not something you find out
    after choosing where to put them.
    """
    operation = await database.get_active_operation(str(guild.id))
    if operation is None:
        raise ValueError('No operation is running.')

    member = await resolve_assignee(guild, request_form.get('member'))
    try:
        check_can_assign(actor, member)
    except ActionError as e:
        raise ValueError(str(e))

    wanted = (request_form.get('slot') or '').strip()
    if not wanted:
        raise ValueError('Pick a slot.')
    data = await roster.load_available(operation)
    slot = next((s for s in data['slots'] if s['value'] == wanted), None)
    if slot is None:
        raise ValueError('That slot is gone — the page has moved on, try again.')

    try:
        await assign_slot_request(bot, guild, actor, member, slot)
    except ActionError as e:
        raise ValueError(str(e))
    return f"Assigned {member.display_name} to {slot['label']}."


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

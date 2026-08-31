"""Turning squads plus bookings into the board, and checking Discord's limits.

The board here is deliberately built the same way `_build_orbat_embed()` builds
the real one in cogs/slots.py: squads grouped into a left and a right column,
one line per slot with a status dot, and a header line counting open, pending
and filled. The one difference is where the layout comes from -- the cog infers
it from the sheet's column geometry, this reads `column_side` off the squad,
which is the whole point of moving the ORBAT into the database.

`check_limits()` is the part with no counterpart today: an ORBAT built in a
browser can outgrow what Discord will render, and finding that out when the
board silently loses its last three squads is too late. Everything Discord
enforces on an embed is checked here while the ORBAT is being edited.
"""

from __future__ import annotations

# Discord's own numbers.
MAX_FIELDS = 25
MAX_FIELD_VALUE = 1024
MAX_FIELD_NAME = 256
MAX_EMBED_CHARS = 6000

# The two-column layout spends three fields per row (left, right, spacer), so
# eight rows is what fits -- the same cap cogs/slots.py applies.
MAX_ROWS = 8


def _line(slot: dict) -> dict:
    booking = slot.get('booking')
    if booking:
        return {'state': 'filled', 'dot': '\U0001F534',
                'text': f"{slot['role_name']} — {booking['member_name']}"}
    if slot.get('pending'):
        return {'state': 'pending', 'dot': '\U0001F7E1',
                'text': f"{slot['role_name']} (pending)"}
    reserved = f" · nur {slot['reserved_unit']}" if slot.get('reserved_unit') else ''
    return {'state': 'open', 'dot': '\U0001F7E2', 'text': f"{slot['role_name']}{reserved}"}


def build_board(squads: list) -> dict:
    """Squads (each with 'slots', each slot optionally carrying a 'booking')
    into everything the board page needs."""
    rendered = []
    for squad in squads:
        lines = [_line(slot) for slot in squad['slots']]
        value = '\n'.join(f"{line['dot']} {line['text']}" for line in lines)
        rendered.append({
            'id': squad.get('id'),
            'name': squad['name'],
            'column': squad['column_side'],
            'excluded': bool(squad['exclude_from_count']),
            'slots': squad['slots'],
            'lines': lines,
            'value_len': len(value),
        })

    counted = [s for s in rendered if not s['excluded']]
    slots = [slot for squad in counted for slot in squad['slots']]
    filled = sum(1 for s in slots if s.get('booking'))
    pending = sum(1 for s in slots if not s.get('booking') and s.get('pending'))

    left = [s for s in rendered if s['column'] == 0]
    right = [s for s in rendered if s['column'] == 1]
    rows = [
        (left[i] if i < len(left) else None, right[i] if i < len(right) else None)
        for i in range(max(len(left), len(right)))
    ]

    return {
        'squads': rendered,
        'rows': rows,
        'counts': {
            'open': len(slots) - filled - pending,
            'pending': pending,
            'filled': filled,
            'total': len(slots),
        },
        'warnings': check_limits(rendered, rows),
    }


def check_limits(rendered: list, rows: list) -> list:
    """What Discord would do to this board, said out loud while it can still be
    changed."""
    warnings = []

    if len(rows) > MAX_ROWS:
        dropped = sum(1 for index, row in enumerate(rows) if index >= MAX_ROWS
                      for squad in row if squad)
        warnings.append(
            f'{len(rows)} Zeilen im Zwei-Spalten-Layout — Discord zeigt nur {MAX_ROWS} '
            f'({MAX_ROWS * 3} von {MAX_FIELDS} Feldern). {dropped} Squad(s) würden auf '
            'dem Discord-Board fehlen. Squads zusammenlegen oder auf eine zweite '
            'Nachricht aufteilen.'
        )

    for squad in rendered:
        if squad['value_len'] > MAX_FIELD_VALUE:
            warnings.append(
                f'Squad "{squad["name"]}" ist {squad["value_len"]} Zeichen lang — Discord '
                f'schneidet ein Feld bei {MAX_FIELD_VALUE} ab.'
            )
        if len(squad['name']) > MAX_FIELD_NAME:
            warnings.append(f'Squad-Name "{squad["name"][:40]}…" ist zu lang für ein Embed-Feld.')

    total = sum(len(s['name']) + s['value_len'] for s in rendered)
    if total > MAX_EMBED_CHARS:
        warnings.append(
            f'Das ganze Embed wäre {total} Zeichen groß — Discords Maximum sind '
            f'{MAX_EMBED_CHARS}. Die Nachricht würde abgelehnt.'
        )

    return warnings

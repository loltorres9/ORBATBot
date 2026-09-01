"""Building and maintaining ORBATs from the browser.

Every rule about what an ORBAT is — the text format, what an edit does to a slot
somebody is booked into, what Discord will do to the board — lives in
`utils/orbat.py`. This module only translates between HTML form fields and those
helpers, the way `web/service.py` does for events. A `ValueError` raised here is
a message meant for the user and is shown on the form.
"""

from utils import database, orbat

MAX_NAME = 120
MAX_DESCRIPTION = 300

# What a new ORBAT starts with, so the first thing an admin sees is the shape of
# the format rather than an empty box.
STARTER_TEXT = """\
# Squad lines start at the left margin, slots are indented.
# Options after the pipe: left / right / nocount, and unit:TAG on a slot.

1-1 Alpha  | left
  Squad Leader
  Team Leader
  Automatic Rifleman
  Grenadier
  Rifleman

1-2 Bravo  | right
  Squad Leader
  Team Leader
  Automatic Rifleman
  Grenadier
  Rifleman

Reservists  | right, nocount
  Reserve
"""


def _clean(raw, limit: int, what: str) -> str:
    value = (raw or '').strip()
    if len(value) > limit:
        raise ValueError(f'{what} is too long — {limit} characters at most.')
    return value


async def create(guild, member, name, description) -> int:
    name = _clean(name, MAX_NAME, 'The name')
    if not name:
        raise ValueError('Give the ORBAT a name.')
    return await database.create_orbat(
        str(guild.id), name, _clean(description, MAX_DESCRIPTION, 'The description') or None,
        str(member.id), member.display_name,
    )


async def duplicate(orbat_id: int, member, name) -> int:
    name = _clean(name, MAX_NAME, 'The name')
    if not name:
        raise ValueError('Give the copy a name.')
    return await database.duplicate_orbat(
        orbat_id, name, str(member.id), member.display_name
    )


async def editor_text(record) -> str:
    """What the editor opens with: the author's own text when there is one, the
    structure rendered back into it otherwise, and the starter for a new ORBAT."""
    if record['source_text']:
        return record['source_text']
    squads = await database.get_orbat_structure(record['id'])
    return orbat.to_text(squads) if squads else STARTER_TEXT


def _as_board_input(parsed_squads: list) -> list:
    """Parsed squads in the shape build_board() wants, with nothing booked.

    The preview deliberately shows the empty roster: bookings belong to an
    operation, and the editor is looking at the template.
    """
    return [
        {'id': None, 'name': squad.name, 'column_side': squad.column,
         'exclude_from_count': squad.exclude_from_count,
         'slots': [{'role_name': slot.role_name, 'reserved_unit': slot.reserved_unit,
                    'booking': None, 'pending': False} for slot in squad.slots]}
        for squad in parsed_squads
    ]


async def review(orbat_id: int, text: str) -> dict:
    """Parse the submitted text and work out what saving it would do."""
    result = orbat.parse(text)
    if not result.ok:
        return {'result': result, 'diff': None, 'board': None, 'summary': None}

    stored = await database.get_orbat_structure(orbat_id)
    diff = orbat.build_diff(stored, result.squads)
    return {
        'result': result,
        'diff': diff,
        'board': orbat.build_board(_as_board_input(result.squads)),
        'summary': orbat.summarise(diff),
    }


async def apply(orbat_id: int, text: str, checked: dict) -> str:
    await database.apply_orbat_structure(
        orbat_id, checked['result'].squads, checked['diff'], source_text=text
    )
    return f"Saved — {checked['summary']}."


async def stored_board(orbat_id: int):
    """The board as stored, with whatever is actually booked into it."""
    squads = await database.get_orbat_structure(orbat_id)
    return orbat.build_board(squads) if squads else None

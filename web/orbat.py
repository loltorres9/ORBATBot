"""Building and maintaining ORBATs from the browser.

Every rule about what an ORBAT is — the text format, what an edit does to a slot
somebody is booked into, what Discord will do to the board — lives in
`utils/orbat.py`. This module only translates between HTML form fields and those
helpers, the way `web/service.py` does for events. A `ValueError` raised here is
a message meant for the user and is shown on the form.
"""

from cogs.slots import UNIT_ROLES
from utils import database, orbat

MAX_NAME = 120
MAX_DESCRIPTION = 300

# What a new ORBAT starts with, so the first thing an admin sees is the shape of
# the format rather than an empty box.
STARTER_TEXT = """\
# Squad lines start at the left margin, slots are indented.
# Options after the pipe: left / right, unit:TAG, nocount.

1-1 Alpha  | left, unit:TFP
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

# The shared nets a new ORBAT starts with, so the format is visible rather than
# described. A leading "-" is a net that exists but is not in use this time.
STARTER_NETS = """\
Platoon Net   | 152 CHN : 1
Logi          | 152 CHN : 2
-Air Net      | 152 CHN : 3
High Com Net  | 152 CHN : 4
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


async def editor_nets_text(record) -> str:
    """The same, for the net list."""
    if record['nets_text']:
        return record['nets_text']
    rows = await database.get_orbat_nets(record['id'])
    return orbat.nets_to_text(rows) if rows else STARTER_NETS


def _as_board_input(parsed_squads: list) -> list:
    """Parsed squads in the shape build_board() wants, with nothing booked.

    The preview deliberately shows the empty roster: bookings belong to an
    operation, and the editor is looking at the template.
    """
    return [
        {'id': None, 'name': squad.name, 'column_side': squad.column,
         'exclude_from_count': squad.exclude_from_count,
         'reserved_unit': squad.reserved_unit, 'radio': squad.radio,
         'slots': [{'role_name': slot.role_name, 'booking': None, 'pending': False}
                   for slot in squad.slots]}
        for squad in parsed_squads
    ]


def _as_net_input(parsed_nets: list) -> list:
    return [{'name': net.name, 'channel': net.channel, 'inactive': int(net.inactive)}
            for net in parsed_nets]


def _check_units(result) -> None:
    """Warn about a unit tag that is not one of the unit roles.

    `utils/orbat.py` knows nothing about units on purpose, and this is a warning
    rather than an error: a unit could be renamed in Discord tomorrow, and a
    roster that stops saving because of it would be worse than a typo. The point
    is that a tag which matches nothing silently means nothing.
    """
    for squad in result.squads:
        if squad.reserved_unit and squad.reserved_unit not in _UNIT_TAGS:
            result.warnings.append((
                squad.line,
                f'"{squad.reserved_unit}" is not one of the unit roles '
                f'({", ".join(sorted(UNIT_ROLES))}).',
            ))


# Matched case-insensitively, since the editor upper-cases what is typed.
_UNIT_TAGS = {role.upper() for role in UNIT_ROLES}


async def review(orbat_id: int, text: str, nets_text: str) -> dict:
    """Parse both fields and work out what saving them would do.

    The two are reported separately so a line number means something: line 3 of
    the roster and line 3 of the net list are different places.
    """
    result = orbat.parse(text)
    _check_units(result)
    nets = orbat.parse_nets(nets_text)
    checked = {'result': result, 'nets': nets, 'diff': None, 'board': None,
               'summary': None, 'ok': result.ok and nets.ok}
    if not result.ok:
        return checked

    stored = await database.get_orbat_structure(orbat_id)
    checked['diff'] = orbat.build_diff(stored, result.squads)
    checked['board'] = orbat.build_board(
        _as_board_input(result.squads), _as_net_input(nets.nets)
    )
    checked['summary'] = orbat.summarise(checked['diff'])
    return checked


async def apply(orbat_id: int, text: str, nets_text: str, checked: dict) -> str:
    await database.apply_orbat_structure(
        orbat_id, checked['result'].squads, checked['diff'], source_text=text
    )
    await database.set_orbat_nets(orbat_id, checked['nets'].nets, nets_text=nets_text)
    return f"Saved — {checked['summary']}."


async def stored_board(orbat_id: int):
    """The board as stored, with whatever is actually booked into it."""
    squads = await database.get_orbat_structure(orbat_id)
    if not squads:
        return None
    return orbat.build_board(squads, await database.get_orbat_nets(orbat_id))

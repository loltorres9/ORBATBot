"""ORBATs held in the database: the text format, the safe edit, and the board.

The slot roster lived only in a Google Sheet until now — `utils/sheets.py` reads
it live on every refresh and a slot's identity is a spreadsheet coordinate. This
module is the database-backed alternative: an ORBAT is a stored structure with
real slot ids, edited from the browser.

Three parts, deliberately free of discord.py, FastAPI and asyncpg so they can be
tested on their own (see tests in lab/tests):

  parse() / to_text()   the editor's text format
  build_diff()          what an edit does to slots that people are booked into
  build_board()         squads plus bookings, rendered the way the embed is

The web UI has no JavaScript and no build step, which rules out a drag-and-drop
slot editor; reordering forty slots through per-row buttons would be worse than
the sheet it replaces. So the editor is a single indented-text field, which is
how ORBATs get written down anyway.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# The text format
# ---------------------------------------------------------------------------

# Longer names are refused. Discord caps an embed field name at 256, but a name
# anywhere near that is unreadable on the board long before it is invalid.
MAX_SQUAD_NAME = 100
MAX_SLOT_NAME = 100
MAX_SQUADS = 40
MAX_SLOTS = 400

# "1. Squad Leader", "2) Rifleman", "3 - Medic" — the enumeration people carry
# over when pasting out of a sheet. There the number is load-bearing (it is what
# keeps two "Rifleman" cells apart); here every slot has its own id, so it is
# noise and gets stripped.
_ENUM = re.compile(r'^\d+\s*[.)\-]\s*')

# An option list after a pipe: "1-1 Alpha | right, nocount"
_OPTIONS = re.compile(r'\s*\|\s*(.+)$')

# Options that are a word on their own, with no value after them.
_BARE_OPTIONS = ('left', 'right', 'nocount', 'reserve')

# The options that carry a value. Splitting on whitespace before one of these is
# what lets "unit:TFP radio:343 CHN:3" be read as two options while leaving the
# spaces inside a value alone — "unit:2nd USC" is not followed by a keyword, so
# it stays whole. See _peel_options().
_VALUED_SPLIT = re.compile(r'\s+(?=(?:unit|radio|net):)', re.IGNORECASE)


@dataclass
class ParsedSlot:
    role_name: str
    line: int = 0


@dataclass
class ParsedSquad:
    name: str
    column: int = 0                     # 0 = left, 1 = right
    exclude_from_count: bool = False
    explicit_column: bool = False       # was left/right actually written down?
    # The unit the whole squad belongs to. A squad is a unit's squad — that is
    # how the rosters are actually organised — so this sits here rather than on
    # every slot, where it would have to be repeated line after line.
    reserved_unit: str | None = None
    # The radio and channel the squad talks on internally, e.g. "343 CHN:3".
    # Free text: every unit writes these slightly differently, and a format
    # this code enforced would be one more thing to fight.
    radio: str | None = None
    slots: list = field(default_factory=list)
    line: int = 0


@dataclass
class ParseResult:
    squads: list = field(default_factory=list)
    errors: list = field(default_factory=list)      # (line|None, message)
    warnings: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def slot_count(self) -> int:
        return sum(len(s.slots) for s in self.squads)


def _strip_enumeration(name: str) -> str:
    """Drop a leading "1." / "2)" / "3 -" unless that is the whole name.

    The guard matters: a squad-style label like "1-1" would otherwise be eaten
    down to nothing, and a slot really called "2" should stay "2".
    """
    stripped = _ENUM.sub('', name).strip()
    if not stripped or not re.search(r'[a-zA-Z]', stripped):
        return name
    return stripped


def _peel_options(chunk: str) -> list:
    """Separate options inside one comma-separated chunk.

    The commas between options are easy to leave out — "| left unit:CNTO" is
    how people write it — and without this the whole chunk reads as one unknown
    option, losing the column and the unit together and in silence.

    Two rules, both keyed on names this module already knows, so a value that
    contains spaces survives: split before a keyword that takes a value, and
    peel the keywords that stand alone off either end. "unit:2nd USC" is
    followed by neither, so it stays whole.
    """
    found = []
    for piece in _VALUED_SPLIT.split(chunk):
        piece = piece.strip()
        leading, trailing = [], []
        while True:
            head, _, rest = piece.partition(' ')
            if head.lower() in _BARE_OPTIONS and rest.strip():
                leading.append(head)
                piece = rest.strip()
            else:
                break
        while True:
            rest, space, last = piece.rpartition(' ')
            if space and last.lower() in _BARE_OPTIONS and rest.strip():
                trailing.insert(0, last)
                piece = rest.strip()
            else:
                break
        found.extend(leading + ([piece] if piece else []) + trailing)
    return found


def _split_options(raw: str) -> tuple[str, list]:
    """Split "Name | one, two:value" into the name and its options.

    Options come back with their case intact — a radio channel is written
    "343 CHN:3" and lower-casing it on the way in would hand that back as
    "343 chn:3". Keywords are matched case-insensitively at the point of use.
    """
    match = _OPTIONS.search(raw)
    if not match:
        return raw.strip(), []
    name = raw[:match.start()].strip()
    options = []
    for chunk in match.group(1).split(','):
        options.extend(_peel_options(chunk.strip()))
    return name, options


def parse(text: str) -> ParseResult:
    """Parse the editor's text into squads and slots.

    The grammar, in full:
      - a blank line, or one starting with '#', is ignored
      - a line with no leading whitespace is a squad header
      - an indented line is a slot, belonging to the squad above it
      - either kind may end with '| option, option'
          squad options: left | right | nocount
          slot  options: unit:<TAG>
    """
    result = ParseResult()
    current: ParsedSquad | None = None
    seen_squads: dict = {}

    for number, raw_line in enumerate(text.splitlines(), start=1):
        if not raw_line.strip() or raw_line.strip().startswith('#'):
            continue

        indented = raw_line[:1] in (' ', '\t')
        name, options = _split_options(raw_line)

        if not indented:
            if not name:
                result.errors.append((number, 'Squad line without a name.'))
                continue
            if len(name) > MAX_SQUAD_NAME:
                result.errors.append(
                    (number, f'Squad name is {len(name)} characters; the limit is {MAX_SQUAD_NAME}.')
                )
                continue
            key = name.casefold()
            if key in seen_squads:
                result.errors.append(
                    (number, f'Squad "{name}" already exists on line {seen_squads[key]}.')
                )
                continue
            seen_squads[key] = number

            squad = ParsedSquad(name=name, line=number)
            for option in options:
                keyword = option.lower()
                if keyword == 'left':
                    squad.column, squad.explicit_column = 0, True
                elif keyword == 'right':
                    squad.column, squad.explicit_column = 1, True
                elif keyword in ('nocount', 'reserve'):
                    squad.exclude_from_count = True
                elif keyword.startswith('unit:'):
                    # Kept as typed. Upper-casing it would turn a unit really
                    # called "2nd USC" into "2ND USC"; web/orbat.py matches it
                    # against the actual roles and fixes the spelling there.
                    squad.reserved_unit = option.split(':', 1)[1].strip() or None
                elif keyword.startswith('radio:') or keyword.startswith('net:'):
                    squad.radio = option.split(':', 1)[1].strip() or None
                else:
                    result.warnings.append(
                        (number, f'Unknown squad option "{option}" — ignored.')
                    )
            result.squads.append(squad)
            current = squad
            continue

        # -- slot line ------------------------------------------------------
        if current is None:
            result.errors.append(
                (number, f'"{name}" is indented but comes before any squad.')
            )
            continue
        role = _strip_enumeration(name)
        if not role:
            result.errors.append((number, 'Slot line without a name.'))
            continue
        if len(role) > MAX_SLOT_NAME:
            result.errors.append(
                (number, f'Slot name is {len(role)} characters; the limit is {MAX_SLOT_NAME}.')
            )
            continue

        slot = ParsedSlot(role_name=role, line=number)
        for option in options:
            if option.lower().startswith('unit:'):
                # Said on a slot this used to mean something. Saying so beats
                # silently dropping it for anyone whose roster predates the move.
                result.warnings.append(
                    (number, f'A unit belongs on the squad line now — '
                             f'put "{option}" after "{current.name}".')
                )
            else:
                result.warnings.append(
                    (number, f'Unknown slot option "{option}" — ignored.')
                )
        current.slots.append(slot)

    for squad in result.squads:
        if not squad.slots:
            result.errors.append((squad.line, f'Squad "{squad.name}" has no slots.'))

    if len(result.squads) > MAX_SQUADS:
        result.errors.append((None, f'More than {MAX_SQUADS} squads.'))
    if result.slot_count > MAX_SLOTS:
        result.errors.append((None, f'More than {MAX_SLOTS} slots.'))
    if not result.squads and not result.errors:
        result.errors.append((None, 'Empty ORBAT — at least one squad with one slot.'))

    assign_columns(result.squads)
    return result


def assign_columns(squads: list) -> None:
    """Fill in the left/right column for squads that did not say.

    When nobody wrote left or right the list is split down the middle, which
    reproduces what the sheet reader infers from column geometry today. As soon
    as one squad is explicit the rest default to the left column — guessing on
    top of an explicit choice would be surprising.
    """
    if not squads or any(s.explicit_column for s in squads):
        return
    half = (len(squads) + 1) // 2
    for index, squad in enumerate(squads):
        squad.column = 0 if index < half else 1


def to_text(squads: list) -> str:
    """Render stored squads back into the editor format.

    Round-trips with parse(). Only used when an ORBAT has no stored source text
    — normally the editor shows what its author actually typed, comments and
    blank lines included, which regenerating from the structure would flatten.
    """
    lines = []
    explicit = len(squads) > 1
    for squad in squads:
        options = []
        if explicit:
            options.append('right' if squad['column_side'] else 'left')
        if squad.get('reserved_unit'):
            options.append(f"unit:{squad['reserved_unit']}")
        if squad.get('radio'):
            options.append(f"radio:{squad['radio']}")
        if squad.get('exclude_from_count'):
            options.append('nocount')
        suffix = f"  | {', '.join(options)}" if options else ''
        lines.append(f"{squad['name']}{suffix}")
        for slot in squad['slots']:
            lines.append(f"  {slot['role_name']}")
        lines.append('')
    return '\n'.join(lines).strip() + '\n'


# ---------------------------------------------------------------------------
# Radio nets
# ---------------------------------------------------------------------------
#
# The long-range nets the whole operation shares — platoon, logistics, air, high
# command — as against the short-range channel each squad talks on internally,
# which is the squad's own `radio`. They are a flat list with no identity of
# their own, so unlike squads and slots they are simply replaced on save; there
# is nothing hanging off a net that an edit could unseat.

MAX_NETS = 25
MAX_NET_NAME = 60
MAX_NET_CHANNEL = 40


@dataclass
class ParsedNet:
    name: str
    channel: str | None = None
    # Struck through on the board: the net exists in the plan but is not in use
    # for this operation. The leading "-" that marks it is the same convention
    # `cogs/events.py` uses for a decline response.
    inactive: bool = False
    line: int = 0


@dataclass
class NetsResult:
    nets: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def parse_nets(text: str) -> NetsResult:
    """One net per line: "Platoon Net | 152 CHN : 1".

    A leading "-" marks a net as not in use. The channel is free text and may be
    left off entirely, for a net whose frequency has not been decided yet.
    """
    result = NetsResult()
    for number, raw_line in enumerate((text or '').splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith('#'):
            continue

        inactive = line.startswith('-')
        if inactive:
            line = line[1:].strip()

        name, _, channel = line.partition('|')
        name, channel = name.strip(), channel.strip()

        if not name:
            result.errors.append((number, 'Net without a name.'))
            continue
        if len(name) > MAX_NET_NAME:
            result.errors.append(
                (number, f'Net name is {len(name)} characters; the limit is {MAX_NET_NAME}.')
            )
            continue
        if len(channel) > MAX_NET_CHANNEL:
            result.errors.append(
                (number, f'Channel is {len(channel)} characters; the limit is '
                         f'{MAX_NET_CHANNEL}.')
            )
            continue

        result.nets.append(ParsedNet(name=name, channel=channel or None,
                                     inactive=inactive, line=number))

    if len(result.nets) > MAX_NETS:
        result.errors.append((None, f'More than {MAX_NETS} nets.'))
    return result


def nets_to_text(rows: list) -> str:
    lines = []
    for row in rows:
        prefix = '-' if row['inactive'] else ''
        suffix = f"  | {row['channel']}" if row['channel'] else ''
        lines.append(f"{prefix}{row['name']}{suffix}")
    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# Applying an edit without unseating anybody
# ---------------------------------------------------------------------------
#
# Slots carry an id and a member's booking hangs off that id, so re-parsing the
# text must not drop every slot and recreate it — that would empty the board on
# every edit. The matcher works in three passes, most to least confident:
#
#   1. squads by name, then leftover squads pairwise by position (a rename)
#   2. inside a matched squad, slots by role name, in order
#   3. leftover slots inside that squad pairwise by position (a rename)
#
# Anything still unmatched is a real addition or removal.


@dataclass
class SquadChange:
    kind: str                  # 'kept' | 'renamed' | 'added' | 'removed'
    old: dict | None = None
    new: object | None = None
    slots: list = field(default_factory=list)


@dataclass
class SlotChange:
    kind: str                  # 'kept' | 'renamed' | 'added' | 'removed'
    squad: str
    old: dict | None = None
    new: object | None = None

    @property
    def bookings(self) -> list:
        return (self.old or {}).get('bookings', [])


@dataclass
class OrbatDiff:
    squads: list = field(default_factory=list)

    def _slots(self, kind: str) -> list:
        return [s for squad in self.squads for s in squad.slots if s.kind == kind]

    @property
    def added(self) -> list:
        return self._slots('added')

    @property
    def removed(self) -> list:
        return self._slots('removed')

    @property
    def renamed(self) -> list:
        return self._slots('renamed')

    @property
    def kept(self) -> list:
        return self._slots('kept')

    @property
    def losing_bookings(self) -> list:
        """Removed slots somebody is currently booked into."""
        return [s for s in self.removed if s.bookings]

    @property
    def moving_bookings(self) -> list:
        """Renamed slots somebody is booked into.

        Nobody is unseated, but their role changes under them — right when the
        edit was a typo fix, wrong when it was meant as a replacement. The text
        alone cannot tell those apart, so it is put to the person editing.
        """
        return [s for s in self.renamed if s.bookings]

    @property
    def destructive(self) -> bool:
        """Somebody would lose their slot outright."""
        return bool(self.losing_bookings)

    @property
    def needs_confirmation(self) -> bool:
        return bool(self.losing_bookings or self.moving_bookings)


def _pair_leftovers(old_items: list, new_items: list) -> tuple[list, list, list]:
    """Pair what is left over positionally: (renamed_pairs, old_only, new_only)."""
    pairs = list(zip(old_items, new_items))
    tail = len(pairs)
    return pairs, old_items[tail:], new_items[tail:]


def _diff_slots(squad_name: str, old_slots: list, new_slots: list) -> list:
    changes = []
    remaining_old = list(old_slots)
    remaining_new = list(new_slots)

    # Pass 1 — exact role name, first come first served, so two identical
    # "Rifleman" lines stay pinned to the two existing Rifleman slots in order.
    for new in list(remaining_new):
        match = next(
            (o for o in remaining_old if o['role_name'].casefold() == new.role_name.casefold()),
            None,
        )
        if match is not None:
            remaining_old.remove(match)
            remaining_new.remove(new)
            changes.append(SlotChange('kept', squad_name, old=match, new=new))

    # Pass 2 — whatever is left, paired by position, counts as a rename.
    renames, only_old, only_new = _pair_leftovers(remaining_old, remaining_new)
    for old, new in renames:
        changes.append(SlotChange('renamed', squad_name, old=old, new=new))
    for old in only_old:
        changes.append(SlotChange('removed', squad_name, old=old))
    for new in only_new:
        changes.append(SlotChange('added', squad_name, new=new))
    return changes


def build_diff(old_squads: list, new_squads: list) -> OrbatDiff:
    """Compare stored squads (dicts, with 'slots' and per-slot 'bookings')
    against freshly parsed ones."""
    diff = OrbatDiff()
    remaining_old = list(old_squads)
    remaining_new = list(new_squads)
    matched = []

    for new in list(remaining_new):
        match = next(
            (o for o in remaining_old if o['name'].casefold() == new.name.casefold()),
            None,
        )
        if match is not None:
            remaining_old.remove(match)
            remaining_new.remove(new)
            matched.append(('kept', match, new))

    renames, only_old, only_new = _pair_leftovers(remaining_old, remaining_new)
    for old, new in renames:
        matched.append(('renamed', old, new))

    # Follow the new text, so the confirmation reads top to bottom the way the
    # editor does.
    order = {id(new): index for index, new in enumerate(new_squads)}
    matched.sort(key=lambda item: order.get(id(item[2]), 0))

    for kind, old, new in matched:
        change = SquadChange(kind, old=old, new=new)
        change.slots = _diff_slots(new.name, old['slots'], new.slots)
        diff.squads.append(change)

    for old in only_old:
        change = SquadChange('removed', old=old)
        change.slots = [SlotChange('removed', old['name'], old=slot) for slot in old['slots']]
        diff.squads.append(change)

    for new in only_new:
        change = SquadChange('added', new=new)
        change.slots = [SlotChange('added', new.name, new=slot) for slot in new.slots]
        diff.squads.append(change)

    return diff


def summarise(diff: OrbatDiff) -> str:
    parts = []
    if diff.added:
        parts.append(f'{len(diff.added)} added')
    if diff.renamed:
        parts.append(f'{len(diff.renamed)} renamed')
    if diff.removed:
        parts.append(f'{len(diff.removed)} removed')
    if not parts:
        return 'no structural changes'
    return ', '.join(parts)


# ---------------------------------------------------------------------------
# The board, and what Discord would do to it
# ---------------------------------------------------------------------------

MAX_FIELDS = 25
MAX_FIELD_VALUE = 1024
MAX_FIELD_NAME = 256
MAX_EMBED_CHARS = 6000

# The two-column layout spends three fields per row (left, right, spacer), so
# eight rows is what fits — the same cap `_build_orbat_embed()` applies.
MAX_ROWS = 8


def _line(slot: dict) -> dict:
    booking = slot.get('booking')
    if booking:
        return {'state': 'filled', 'dot': '\U0001F534',
                'text': f"{slot['role_name']} — {booking['member_name']}"}
    if slot.get('pending'):
        return {'state': 'pending', 'dot': '\U0001F7E1',
                'text': f"{slot['role_name']} (pending)"}
    return {'state': 'open', 'dot': '\U0001F7E2', 'text': slot['role_name']}


def build_board(squads: list, nets: list = None) -> dict:
    """Squads (each with 'slots', each slot optionally carrying a 'booking')
    into everything a board page or embed needs.

    Built the way `_build_orbat_embed()` builds the live one, with a left and a
    right column and a counted header. The one difference is where the layout
    comes from: the cog infers it from the sheet's geometry, this reads
    `column_side` off the squad — which is the point of moving the ORBAT into
    the database.
    """
    rendered = []
    for squad in squads:
        lines = [_line(slot) for slot in squad['slots']]
        value = '\n'.join(f"{line['dot']} {line['text']}" for line in lines)
        rendered.append({
            'id': squad.get('id'),
            'name': squad['name'],
            'unit': squad.get('reserved_unit'),
            'radio': squad.get('radio'),
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

    nets = list(nets or [])
    return {
        'squads': rendered,
        'rows': rows,
        'nets': nets,
        'counts': {
            'open': len(slots) - filled - pending,
            'pending': pending,
            'filled': filled,
            'total': len(slots),
        },
        'warnings': check_limits(rendered, rows, nets),
    }


def check_limits(rendered: list, rows: list, nets: list = None) -> list:
    """What Discord would do to this board, said out loud while it can still be
    changed. There is no equivalent today: a board that outgrows the embed
    silently loses its last squads."""
    warnings = []

    # The nets ride along as one more field, which is what makes eight rows the
    # limit rather than eight-and-a-bit: 8 x 3 + 1 is exactly 25.
    fields = min(len(rows), MAX_ROWS) * 3 + (1 if nets else 0)
    if fields > MAX_FIELDS:
        warnings.append(
            f'{fields} embed fields — Discord allows {MAX_FIELDS}. Drop a squad '
            'row or the net list.'
        )

    if len(rows) > MAX_ROWS:
        dropped = sum(1 for index, row in enumerate(rows) if index >= MAX_ROWS
                      for squad in row if squad)
        warnings.append(
            f'{len(rows)} rows in the two-column layout — Discord shows only {MAX_ROWS} '
            f'({MAX_ROWS * 3} of {MAX_FIELDS} fields), so {dropped} squad(s) would be '
            'missing from the Discord board. Merge squads, or split them across a '
            'second message.'
        )

    for squad in rendered:
        if squad['value_len'] > MAX_FIELD_VALUE:
            warnings.append(
                f'Squad "{squad["name"]}" is {squad["value_len"]} characters — Discord '
                f'truncates a field at {MAX_FIELD_VALUE}.'
            )
        if len(squad['name']) > MAX_FIELD_NAME:
            warnings.append(f'Squad name "{squad["name"][:40]}…" is too long for an embed field.')

    total = sum(len(s['name']) + s['value_len'] for s in rendered)
    total += sum(len(n['name']) + len(n['channel'] or '') + 4 for n in (nets or []))
    if total > MAX_EMBED_CHARS:
        warnings.append(
            f'The whole embed would be {total} characters — Discord\'s maximum is '
            f'{MAX_EMBED_CHARS}, so the message would be rejected.'
        )

    return warnings

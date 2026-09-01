"""The ORBAT text format: plain text in, squads and slots out.

This module is the heart of the experiment. The web UI has no JavaScript and no
build step (see CLAUDE.md), so a drag-and-drop slot editor is not on the table --
and reordering forty slots through up/down buttons would be miserable. The bet
made here is that a single indented-text field is a *better* editor for this
shape of data anyway, because an ORBAT is a list of lists and people already
write them that way.

Nothing in here imports discord.py, FastAPI or the database, so it is directly
testable -- see lab/tests/test_parser.py.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Names longer than this are refused. Discord caps an embed field *name* at 256
# and the roles are rendered inside a field value, but anything approaching
# those numbers is unreadable on the board long before it is invalid.
MAX_SQUAD_NAME = 100
MAX_SLOT_NAME = 100
MAX_SQUADS = 40
MAX_SLOTS = 400

# "1. Squad Leader", "2) Rifleman", "3 - Medic" -- the enumeration people carry
# over when pasting out of a sheet. In the sheet the number is load-bearing
# (it is what keeps two "Rifleman" cells apart); here every slot has its own id,
# so the number is noise and gets stripped.
_ENUM = re.compile(r'^\d+\s*[.)\-]\s*')

# An option list after a pipe: "1-1 Alpha | right, nocount"
_OPTIONS = re.compile(r'\s*\|\s*(.+)$')


@dataclass
class ParsedSlot:
    role_name: str
    reserved_unit: str | None = None
    line: int = 0


@dataclass
class ParsedSquad:
    name: str
    column: int = 0                     # 0 = left, 1 = right
    exclude_from_count: bool = False
    explicit_column: bool = False       # was left/right actually written down?
    slots: list = field(default_factory=list)
    line: int = 0


@dataclass
class ParseResult:
    squads: list = field(default_factory=list)
    errors: list = field(default_factory=list)      # (line, message)
    warnings: list = field(default_factory=list)    # (line|None, message)

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


def _split_options(raw: str) -> tuple[str, list]:
    match = _OPTIONS.search(raw)
    if not match:
        return raw.strip(), []
    name = raw[:match.start()].strip()
    options = [o.strip().lower() for o in match.group(1).split(',') if o.strip()]
    return name, options


def parse(text: str) -> ParseResult:
    """Parse the editor's text into squads and slots.

    Grammar, in full:
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
                result.errors.append((number, 'Squad-Zeile ohne Namen.'))
                continue
            if len(name) > MAX_SQUAD_NAME:
                result.errors.append(
                    (number, f'Squad-Name ist {len(name)} Zeichen lang, erlaubt sind {MAX_SQUAD_NAME}.')
                )
                continue
            key = name.casefold()
            if key in seen_squads:
                result.errors.append(
                    (number, f'Squad "{name}" gibt es schon in Zeile {seen_squads[key]}.')
                )
                continue
            seen_squads[key] = number

            squad = ParsedSquad(name=name, line=number)
            for option in options:
                if option == 'left':
                    squad.column, squad.explicit_column = 0, True
                elif option == 'right':
                    squad.column, squad.explicit_column = 1, True
                elif option in ('nocount', 'reserve'):
                    squad.exclude_from_count = True
                else:
                    result.warnings.append(
                        (number, f'Unbekannte Squad-Option "{option}" -- ignoriert.')
                    )
            result.squads.append(squad)
            current = squad
            continue

        # -- slot line ------------------------------------------------------
        if current is None:
            result.errors.append(
                (number, f'"{name}" ist eingerückt, steht aber vor jedem Squad.')
            )
            continue
        role = _strip_enumeration(name)
        if not role:
            result.errors.append((number, 'Slot-Zeile ohne Namen.'))
            continue
        if len(role) > MAX_SLOT_NAME:
            result.errors.append(
                (number, f'Slot-Name ist {len(role)} Zeichen lang, erlaubt sind {MAX_SLOT_NAME}.')
            )
            continue

        slot = ParsedSlot(role_name=role, line=number)
        for option in options:
            if option.startswith('unit:'):
                slot.reserved_unit = option.split(':', 1)[1].strip().upper() or None
            else:
                result.warnings.append(
                    (number, f'Unbekannte Slot-Option "{option}" -- ignoriert.')
                )
        current.slots.append(slot)

    for squad in result.squads:
        if not squad.slots:
            result.errors.append(
                (squad.line, f'Squad "{squad.name}" hat keine Slots.')
            )

    if len(result.squads) > MAX_SQUADS:
        result.errors.append((None, f'Mehr als {MAX_SQUADS} Squads.'))
    if result.slot_count > MAX_SLOTS:
        result.errors.append((None, f'Mehr als {MAX_SLOTS} Slots.'))
    if not result.squads and not result.errors:
        result.errors.append((None, 'Leeres ORBAT -- mindestens ein Squad mit einem Slot.'))

    assign_columns(result.squads)
    return result


def assign_columns(squads: list) -> None:
    """Fill in the left/right column for squads that did not say.

    When nobody wrote left or right the list is simply split down the middle,
    which reproduces what the sheet reader infers from column geometry today.
    As soon as one squad is explicit, the rest default to the left column --
    guessing on top of an explicit choice would be surprising.
    """
    if not squads:
        return
    if any(s.explicit_column for s in squads):
        return
    half = (len(squads) + 1) // 2
    for index, squad in enumerate(squads):
        squad.column = 0 if index < half else 1


def to_text(squads: list) -> str:
    """Render stored squads back into the editor format.

    Round-trips with parse(): what comes out of here, parsed again, is the same
    structure. That is what lets the editor be the *only* way to change an ORBAT.
    """
    lines = []
    explicit = len(squads) > 1
    for squad in squads:
        options = []
        if explicit:
            options.append('right' if squad['column_side'] else 'left')
        if squad['exclude_from_count']:
            options.append('nocount')
        suffix = f"  | {', '.join(options)}" if options else ''
        lines.append(f"{squad['name']}{suffix}")
        for slot in squad['slots']:
            slot_suffix = f"  | unit:{slot['reserved_unit']}" if slot['reserved_unit'] else ''
            lines.append(f"  {slot['role_name']}{slot_suffix}")
        lines.append('')
    return '\n'.join(lines).strip() + '\n'

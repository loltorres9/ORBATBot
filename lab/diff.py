"""Applying an edited ORBAT text without destroying slot identity.

This is the part that decides whether a text editor is safe for a live ORBAT.
Slots carry an id, and a member's booking hangs off that id -- so re-parsing the
text must not simply drop every slot and recreate it, or every edit would empty
the board.

The matcher therefore works in three passes, from most to least confident:

  1. squads by name, then leftover squads pairwise by position (a rename)
  2. inside a matched squad, slots by role name, in order
  3. leftover slots inside that squad pairwise by position (a rename)

Anything still unmatched is a genuine addition or removal. Renames keep the id,
and therefore keep whoever is booked into that slot -- which is the behaviour
people expect when they fix a typo or turn a Rifleman into a Grenadier.
"""

from __future__ import annotations

from dataclasses import dataclass, field


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
    def assignments(self) -> list:
        return (self.old or {}).get('assignments', [])


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
        """Removed slots that somebody is currently booked into."""
        return [s for s in self.removed if s.assignments]

    @property
    def moving_bookings(self) -> list:
        """Renamed slots that somebody is booked into.

        Nobody is unseated here, but their role changes under them -- which is
        right when the edit was a typo fix and wrong when it was meant as a
        replacement. The text alone cannot tell those apart, so it is put to the
        person editing rather than guessed at.
        """
        return [s for s in self.renamed if s.assignments]

    @property
    def destructive(self) -> bool:
        """Somebody would lose their slot outright."""
        return bool(self.losing_bookings)

    @property
    def needs_confirmation(self) -> bool:
        return bool(self.losing_bookings or self.moving_bookings)

    @property
    def touches_anything(self) -> bool:
        return bool(self.added or self.removed or self.renamed
                    or any(s.kind in ('added', 'removed', 'renamed') for s in self.squads))


def _pair_leftovers(old_items: list, new_items: list) -> tuple[list, list, list]:
    """Pair what is left over positionally: (renamed_pairs, old_only, new_only)."""
    pairs = list(zip(old_items, new_items))
    tail = len(pairs)
    return pairs, old_items[tail:], new_items[tail:]


def _diff_slots(squad_name: str, old_slots: list, new_slots: list) -> list:
    changes = []
    remaining_old = list(old_slots)
    remaining_new = list(new_slots)

    # Pass 1 -- exact role name, first come first served, so two identical
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

    # Pass 2 -- whatever is left, paired by position, counts as a rename.
    renames, only_old, only_new = _pair_leftovers(remaining_old, remaining_new)
    for old, new in renames:
        changes.append(SlotChange('renamed', squad_name, old=old, new=new))
    for old in only_old:
        changes.append(SlotChange('removed', squad_name, old=old))
    for new in only_new:
        changes.append(SlotChange('added', squad_name, new=new))
    return changes


def build_diff(old_squads: list, new_squads: list) -> OrbatDiff:
    """Compare stored squads (dicts, with 'slots' and per-slot 'assignments')
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

    # Re-order to follow the new text, so the confirmation page reads top to
    # bottom the way the editor does.
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
        parts.append(f'{len(diff.added)} neu')
    if diff.renamed:
        parts.append(f'{len(diff.renamed)} umbenannt')
    if diff.removed:
        parts.append(f'{len(diff.removed)} entfernt')
    if not parts:
        return 'Keine strukturellen Änderungen.'
    return ', '.join(parts)

"""What the diff has to get right: an edit must not silently unseat anybody."""

from lab import diff as diffing
from lab import parser


def stored(*squads):
    """Build the shape store.load_squads() returns."""
    out, slot_id = [], 0
    for name, roles in squads:
        entry = {'id': len(out) + 1, 'name': name, 'column_side': 0,
                 'exclude_from_count': 0, 'sort_order': len(out), 'slots': []}
        for role in roles:
            slot_id += 1
            booked = role.endswith('*')
            role = role.rstrip('*')
            entry['slots'].append({
                'id': slot_id, 'role_name': role, 'reserved_unit': None,
                'sort_order': len(entry['slots']),
                'bookings': ([{'member_name': 'Panz', 'op_name': 'Op', 'unit': 'TFP'}]
                                if booked else []),
            })
        out.append(entry)
    return out


def diff_of(old, text):
    result = parser.parse(text)
    assert result.ok, result.errors
    return diffing.build_diff(old, result.squads)


def test_unchanged_text_changes_nothing():
    old = stored(('Alpha', ['Squad Leader', 'Rifleman']))
    changes = diff_of(old, "Alpha\n  Squad Leader\n  Rifleman\n")
    assert not changes.added and not changes.removed and not changes.renamed
    assert len(changes.kept) == 2


def test_reordering_slots_keeps_every_id():
    old = stored(('Alpha', ['Squad Leader', 'Rifleman', 'Medic']))
    changes = diff_of(old, "Alpha\n  Medic\n  Squad Leader\n  Rifleman\n")
    assert len(changes.kept) == 3
    assert not changes.added and not changes.removed


def test_adding_a_slot_is_only_an_addition():
    old = stored(('Alpha', ['Squad Leader']))
    changes = diff_of(old, "Alpha\n  Squad Leader\n  Rifleman\n")
    assert len(changes.added) == 1
    assert changes.added[0].new.role_name == 'Rifleman'
    assert not changes.removed


def test_renaming_a_slot_keeps_its_booking_but_asks_first():
    old = stored(('Alpha', ['Rifleman*']))
    changes = diff_of(old, "Alpha\n  Grenadier\n")
    assert len(changes.renamed) == 1
    assert not changes.removed
    assert changes.renamed[0].old['id'] == 1
    # Nobody is unseated, so this is not destructive -- but Panz's role changed
    # under him, which is worth a confirmation.
    assert not changes.destructive
    assert changes.needs_confirmation


def test_renaming_an_empty_slot_needs_no_confirmation():
    old = stored(('Alpha', ['Rifleman']))
    changes = diff_of(old, "Alpha\n  Grenadier\n")
    assert len(changes.renamed) == 1
    assert not changes.needs_confirmation


def test_renaming_a_squad_keeps_its_slots():
    old = stored(('1-1 Alpha', ['Squad Leader*', 'Rifleman']))
    changes = diff_of(old, "1-1 Assault\n  Squad Leader\n  Rifleman\n")
    assert not changes.removed and not changes.added
    assert len(changes.kept) == 2
    assert not changes.destructive


def test_removing_an_occupied_slot_is_flagged_destructive():
    old = stored(('Alpha', ['Squad Leader', 'Rifleman*']))
    changes = diff_of(old, "Alpha\n  Squad Leader\n")
    assert len(changes.removed) == 1
    assert changes.destructive
    assert changes.losing_bookings[0].bookings[0]['member_name'] == 'Panz'


def test_removing_an_empty_slot_is_not_destructive():
    old = stored(('Alpha', ['Squad Leader', 'Rifleman']))
    changes = diff_of(old, "Alpha\n  Squad Leader\n")
    assert len(changes.removed) == 1
    assert not changes.destructive
    assert not changes.needs_confirmation


def test_duplicate_role_names_pair_up_in_order():
    old = stored(('Alpha', ['Rifleman*', 'Rifleman', 'Rifleman']))
    changes = diff_of(old, "Alpha\n  Rifleman\n  Rifleman\n")
    assert len(changes.kept) == 2
    assert len(changes.removed) == 1
    # The two that stay are the first two, so the booked one is untouched.
    assert {c.old['id'] for c in changes.kept} == {1, 2}
    assert not changes.destructive


def test_swapping_roles_between_squads_reads_as_two_renames():
    # Slot identity is per squad, so this is not seen as one slot moving house.
    # Nobody loses their place, but Panz ends up on a differently named role --
    # so it stops for a confirmation rather than applying silently.
    old = stored(('Alpha', ['Medic*']), ('Bravo', ['Rifleman']))
    changes = diff_of(old, "Alpha\n  Rifleman\nBravo\n  Medic\n")
    assert len(changes.renamed) == 2
    assert not changes.destructive
    assert changes.needs_confirmation


def test_deleting_a_whole_squad_reports_each_slot():
    old = stored(('Alpha', ['Squad Leader*', 'Rifleman*']), ('Bravo', ['Medic']))
    changes = diff_of(old, "Bravo\n  Medic\n")
    assert len(changes.removed) == 2
    assert len(changes.losing_bookings) == 2


def test_new_orbat_from_nothing_is_all_additions():
    changes = diff_of([], "Alpha\n  Squad Leader\n  Rifleman\n")
    assert len(changes.added) == 2
    assert not changes.removed and not changes.destructive

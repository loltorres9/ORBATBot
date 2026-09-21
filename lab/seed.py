"""A realistic ORBAT to open the lab with, so the first page is not empty."""

from lab import diff as diffing
from lab import parser, store

DEMO_TEXT = """\
# Squad lines start at the left margin, slots are indented.
# Optionen nach dem |: left / right, unit:TAG, nocount

1-0 Platoon HQ  | left
  Platoon Leader
  Platoon Sergeant
  Forward Observer

1-1 Alpha  | left, unit:TFP
  Squad Leader
  Team Leader
  Automatic Rifleman
  Grenadier
  Rifleman
  Combat Life Saver

1-2 Bravo  | left
  Squad Leader
  Team Leader
  Automatic Rifleman
  Grenadier
  Rifleman
  Combat Life Saver

2-1 Weapons  | right
  Section Leader
  MMG Gunner
  MMG Assistant
  AT Gunner
  AT Assistant

3-1 Vehicle Crew  | right
  Vehicle Commander
  Driver
  Gunner

Zeus / Support  | right, nocount
  Zeus
  Mission Maker

Reservists  | right, nocount
  Reserve
  Reserve
"""


def seed_if_empty() -> None:
    if store.list_orbats():
        return
    orbat_id = store.create_orbat(
        'Operation Iron Tide — platoon ORBAT',
        'An example roster, created the first time the lab is started.',
    )
    result = parser.parse(DEMO_TEXT)
    changes = diffing.build_diff([], result.squads)
    store.apply_structure(orbat_id, result.squads, changes, source_text=DEMO_TEXT)

    op_id = store.create_op(orbat_id, 'Iron Tide — Sunday 19:00')
    squads = store.load_squads(orbat_id, op_id=op_id)
    people = [
        ('Panz', 'TFP'), ('Ravioli', 'TFP'), ('Hawk', 'CNTO'),
        ('Sledge', '2nd USC'), ('Nomad', 'PXG'), ('Ghost', 'SKUA'),
    ]
    flat = [slot for squad in squads for slot in squad['slots']]
    for (name, unit), slot in zip(people, flat):
        store.book(op_id, slot['id'], name, unit, 'approved')
    if len(flat) > len(people):
        store.book(op_id, flat[len(people)]['id'], 'Kiwi', 'TFP', 'pending')

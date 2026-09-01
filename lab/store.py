"""Storage for the lab -- SQLite, so the prototype runs anywhere.

The production model would be Postgres alongside the bot's other tables, and the
schema below is written in the shape it would take there:

    orbats        the reusable roster template
    orbat_squads  → CASCADE
    orbat_slots   → CASCADE
    operations    one op night, pointing at an orbat
    requests      the booking, keyed (operation_id, slot_id)

The one thing worth reading carefully is that a *slot carries no booking*. Who
holds a slot lives in the bookings table, keyed by (op_id, slot_id) -- exactly
where `requests` already keeps it today. That is what makes an ORBAT reusable:
the same template backs next week's operation without anything to reset, and
approving a request becomes one UPDATE instead of a database write plus a
Google Sheets write that has to be rolled back when the network fails.

SQLite is a lab choice, not a proposal: it needs no server, so the editor can be
tried out with `uvicorn lab.devserver:app` and nothing else.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(os.getenv('LAB_DB') or Path(__file__).parent / 'orbat_lab.db')


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


@contextmanager
def _conn():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA foreign_keys = ON')
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def init() -> None:
    with _conn() as db:
        db.executescript('''
            CREATE TABLE IF NOT EXISTS lab_orbats (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL,
                description TEXT,
                -- The text as it was typed. The squads and slots below are the
                -- source of truth; this is kept alongside so comments, blank
                -- lines and the author's own ordering survive a reload, which
                -- regenerating the text from the structure would flatten.
                source_text TEXT,
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS lab_squads (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                orbat_id           INTEGER NOT NULL REFERENCES lab_orbats(id) ON DELETE CASCADE,
                name               TEXT NOT NULL,
                column_side        INTEGER NOT NULL DEFAULT 0,
                exclude_from_count INTEGER NOT NULL DEFAULT 0,
                reserved_unit      TEXT,
                radio              TEXT,
                sort_order         INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS lab_slots (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                squad_id      INTEGER NOT NULL REFERENCES lab_squads(id) ON DELETE CASCADE,
                role_name     TEXT NOT NULL,
                sort_order    INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS lab_ops (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                orbat_id   INTEGER NOT NULL REFERENCES lab_orbats(id) ON DELETE CASCADE,
                name       TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS lab_bookings (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                op_id       INTEGER NOT NULL REFERENCES lab_ops(id) ON DELETE CASCADE,
                slot_id     INTEGER NOT NULL REFERENCES lab_slots(id) ON DELETE CASCADE,
                member_name TEXT NOT NULL,
                unit        TEXT,
                status      TEXT NOT NULL DEFAULT 'approved',
                created_at  TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_lab_bookings ON lab_bookings(op_id, slot_id);
        ''')


# -- ORBAT templates --------------------------------------------------------

def list_orbats() -> list:
    with _conn() as db:
        rows = db.execute('''
            SELECT o.*,
                   (SELECT COUNT(*) FROM lab_squads q WHERE q.orbat_id = o.id) AS squad_count,
                   (SELECT COUNT(*) FROM lab_slots s
                      JOIN lab_squads q ON q.id = s.squad_id
                     WHERE q.orbat_id = o.id) AS slot_count,
                   (SELECT COUNT(*) FROM lab_ops p WHERE p.orbat_id = o.id) AS op_count
              FROM lab_orbats o ORDER BY o.updated_at DESC
        ''').fetchall()
        return [dict(r) for r in rows]


def get_orbat(orbat_id: int):
    with _conn() as db:
        row = db.execute('SELECT * FROM lab_orbats WHERE id = ?', (orbat_id,)).fetchone()
        return dict(row) if row else None


def create_orbat(name: str, description: str = None) -> int:
    with _conn() as db:
        cursor = db.execute(
            'INSERT INTO lab_orbats (name, description, created_at, updated_at)'
            ' VALUES (?, ?, ?, ?)',
            (name, description, _now(), _now()),
        )
        return cursor.lastrowid


def rename_orbat(orbat_id: int, name: str, description: str = None) -> None:
    with _conn() as db:
        db.execute(
            'UPDATE lab_orbats SET name = ?, description = ?, updated_at = ? WHERE id = ?',
            (name, description, _now(), orbat_id),
        )


def delete_orbat(orbat_id: int) -> None:
    with _conn() as db:
        db.execute('DELETE FROM lab_orbats WHERE id = ?', (orbat_id,))


def duplicate_orbat(orbat_id: int, name: str) -> int:
    """Copy the structure, not the bookings -- the point of a template."""
    squads = load_squads(orbat_id)
    new_id = create_orbat(name, (get_orbat(orbat_id) or {}).get('description'))
    with _conn() as db:
        for squad in squads:
            cursor = db.execute(
                'INSERT INTO lab_squads (orbat_id, name, column_side, exclude_from_count,'
                ' reserved_unit, radio, sort_order) VALUES (?, ?, ?, ?, ?, ?, ?)',
                (new_id, squad['name'], squad['column_side'],
                 squad['exclude_from_count'], squad['reserved_unit'], squad['radio'],
                 squad['sort_order']),
            )
            for slot in squad['slots']:
                db.execute(
                    'INSERT INTO lab_slots (squad_id, role_name, sort_order) VALUES (?, ?, ?)',
                    (cursor.lastrowid, slot['role_name'], slot['sort_order']),
                )
    return new_id


# -- structure --------------------------------------------------------------

def load_squads(orbat_id: int, op_id: int = None) -> list:
    """Squads with their slots, in order.

    With *op_id*, every slot also carries the booking made for that operation --
    which is how the same template renders a different board per op.
    """
    with _conn() as db:
        squads = [dict(r) for r in db.execute(
            'SELECT * FROM lab_squads WHERE orbat_id = ? ORDER BY sort_order, id',
            (orbat_id,),
        ).fetchall()]
        by_id = {}
        for squad in squads:
            squad['slots'] = []
            by_id[squad['id']] = squad
        if not squads:
            return []

        placeholders = ','.join('?' * len(squads))
        slots = db.execute(
            f'SELECT * FROM lab_slots WHERE squad_id IN ({placeholders}) ORDER BY sort_order, id',
            tuple(by_id),
        ).fetchall()

        bookings: dict = {}
        every: dict = {}
        rows = db.execute('''
            SELECT b.*, p.name AS op_name FROM lab_bookings b
              JOIN lab_ops p ON p.id = b.op_id
             WHERE p.orbat_id = ?
        ''', (orbat_id,)).fetchall()
        for row in rows:
            every.setdefault(row['slot_id'], []).append(dict(row))
            if op_id is not None and row['op_id'] == op_id:
                bookings.setdefault(row['slot_id'], []).append(dict(row))

        for slot in slots:
            slot = dict(slot)
            here = bookings.get(slot['id'], [])
            slot['booking'] = next((b for b in here if b['status'] == 'approved'), None)
            slot['pending'] = any(b['status'] == 'pending' for b in here)
            # Every booking across every operation -- what the confirmation page
            # needs in order to say what deleting this slot would cost.
            slot['bookings'] = every.get(slot['id'], [])
            by_id[slot['squad_id']]['slots'].append(slot)

        return squads


def apply_structure(orbat_id: int, parsed_squads: list, diff, source_text: str = None) -> None:
    """Write the parsed structure, keeping the ids the diff matched.

    Order matters: removals go first so a squad name freed in this edit can be
    reused by another squad in the same edit.
    """
    squad_of_new = {}
    slot_of_new = {}
    remove_squads, remove_slots = [], []

    for change in diff.squads:
        if change.kind == 'removed':
            remove_squads.append(change.old['id'])
            continue
        squad_of_new[id(change.new)] = change.old['id'] if change.old else None
        for slot_change in change.slots:
            if slot_change.kind == 'removed':
                remove_slots.append(slot_change.old['id'])
            elif slot_change.new is not None:
                slot_of_new[id(slot_change.new)] = (
                    slot_change.old['id'] if slot_change.old else None
                )

    with _conn() as db:
        for squad_id in remove_squads:
            db.execute('DELETE FROM lab_squads WHERE id = ?', (squad_id,))
        for slot_id in remove_slots:
            db.execute('DELETE FROM lab_slots WHERE id = ?', (slot_id,))

        for order, squad in enumerate(parsed_squads):
            squad_id = squad_of_new.get(id(squad))
            values = (squad.name, squad.column, int(squad.exclude_from_count),
                      squad.reserved_unit, squad.radio, order)
            if squad_id:
                db.execute(
                    'UPDATE lab_squads SET name = ?, column_side = ?, exclude_from_count = ?,'
                    ' reserved_unit = ?, radio = ?, sort_order = ? WHERE id = ?',
                    values + (squad_id,),
                )
            else:
                squad_id = db.execute(
                    'INSERT INTO lab_squads (name, column_side, exclude_from_count,'
                    ' reserved_unit, radio, sort_order, orbat_id)'
                    ' VALUES (?, ?, ?, ?, ?, ?, ?)',
                    values + (orbat_id,),
                ).lastrowid

            for slot_order, slot in enumerate(squad.slots):
                slot_id = slot_of_new.get(id(slot))
                fields = (slot.role_name, slot_order, squad_id)
                if slot_id:
                    db.execute(
                        'UPDATE lab_slots SET role_name = ?, sort_order = ?, squad_id = ?'
                        ' WHERE id = ?',
                        fields + (slot_id,),
                    )
                else:
                    db.execute(
                        'INSERT INTO lab_slots (role_name, sort_order, squad_id)'
                        ' VALUES (?, ?, ?)',
                        fields,
                    )

        db.execute('UPDATE lab_orbats SET updated_at = ?, source_text = ? WHERE id = ?',
                   (_now(), source_text, orbat_id))


# -- operations and bookings ------------------------------------------------

def list_ops(orbat_id: int) -> list:
    with _conn() as db:
        rows = db.execute('''
            SELECT p.*, (SELECT COUNT(*) FROM lab_bookings b WHERE b.op_id = p.id) AS booked
              FROM lab_ops p WHERE p.orbat_id = ? ORDER BY p.created_at DESC, p.id DESC
        ''', (orbat_id,)).fetchall()
        return [dict(r) for r in rows]


def get_op(op_id: int):
    with _conn() as db:
        row = db.execute('SELECT * FROM lab_ops WHERE id = ?', (op_id,)).fetchone()
        return dict(row) if row else None


def create_op(orbat_id: int, name: str) -> int:
    with _conn() as db:
        return db.execute(
            'INSERT INTO lab_ops (orbat_id, name, created_at) VALUES (?, ?, ?)',
            (orbat_id, name, _now()),
        ).lastrowid


def delete_op(op_id: int) -> None:
    with _conn() as db:
        db.execute('DELETE FROM lab_ops WHERE id = ?', (op_id,))


def book(op_id: int, slot_id: int, member_name: str, unit: str = None,
         status: str = 'approved') -> None:
    with _conn() as db:
        db.execute('DELETE FROM lab_bookings WHERE op_id = ? AND slot_id = ?', (op_id, slot_id))
        db.execute(
            'INSERT INTO lab_bookings (op_id, slot_id, member_name, unit, status, created_at)'
            ' VALUES (?, ?, ?, ?, ?, ?)',
            (op_id, slot_id, member_name, unit, status, _now()),
        )


def unbook(op_id: int, slot_id: int) -> None:
    with _conn() as db:
        db.execute('DELETE FROM lab_bookings WHERE op_id = ? AND slot_id = ?', (op_id, slot_id))

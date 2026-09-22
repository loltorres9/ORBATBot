"""Terrains uploaded as tile archives, and kept by this bot.

The other way to get a terrain onto a map is `web/tacmap.import_ocap()`, which
reads an OCAP server's map folder over HTTP. This is the same tile pyramid
arriving as a file instead: a unit that already prepares these archives for its
own OCAP hands the bot the same one, and then **nothing outside has to be
reachable** for a map to render — not for a member opening the editor, and not
for whoever was sent a share link.

The reading is `utils/tiles.py`, the storing is `utils/database.py`, and the
rules about what a background is are `utils/tacmap.py`. What is left here is
the shape of the form and the messages a person gets back.
"""

import asyncio
import json

from utils import database, tacmap, tiles

MAX_NAME = 80

# What may be asked for as the deepest level to keep. Below 3 a terrain is too
# coarse to plan on; above what the editor draws it would be storage nobody
# ever sees. Each step is four times the tiles of the one before.
ZOOM_CHOICES = (3, 4, 5)
DEFAULT_ZOOM = tacmap.DEFAULT_TILE_ZOOM


def megabytes(count) -> str:
    return f'{(count or 0) / (1024 * 1024):.1f} MB'


def _name(raw, archive, fallback: str) -> str:
    name = (raw or '').strip()
    if not name and isinstance(archive.map_json, dict):
        name = (str(archive.map_json.get('name') or '').strip()
                or str(archive.map_json.get('worldName') or '').strip())
    return (name or fallback or 'Terrain')[:MAX_NAME]


def _world_size(raw, archive):
    """The terrain's edge in metres — from the form, or from its own map.json.

    Without it the tiles would draw and the Arma export would be nonsense, so
    this is the one thing an upload cannot go ahead without.
    """
    typed = (raw or '').strip()
    if typed:
        try:
            size = float(typed.replace(',', '.'))
        except ValueError:
            raise ValueError('The world size has to be a number of metres, e.g. 15360.')
        if size <= 0:
            raise ValueError('The world size has to be a number of metres, e.g. 15360.')
        return size
    if isinstance(archive.map_json, dict):
        size = archive.map_json.get('worldSize')
        try:
            if size and float(size) > 0:
                return float(size)
        except (TypeError, ValueError):
            pass
    raise ValueError(
        "That archive has no map.json to read the terrain's size from, so type "
        "it in — Altis is 30720, Tanoa 15360, Stratis 8192."
    )


def _places(pasted, archive):
    """The terrain's place names, from the paste box or from the archive.

    Both, in that order, because they answer the same question with different
    reach: an archive in the Gruppe Adler format carries a `locations` list and
    an OCAP one does not, so the paste box is the general way in and the
    archive is the free win when it happens to have them.

    Returns `(json_or_none, warnings)`. Nothing here is fatal — a terrain whose
    names could not be read is still a terrain, and the alternative is refusing
    an upload of thirty megabytes over a stray comma.
    """
    warnings = []
    places, problems = tacmap.parse_places(pasted)
    if pasted and problems and not places:
        warnings.append('The place names were not stored: ' + problems[0])
    else:
        warnings.extend(problems)

    if not places and isinstance(archive.map_json, dict):
        places, problems = tacmap.parse_places(archive.map_json)
        # An OCAP map.json simply has no locations list; saying so on every
        # upload would be noise about a thing nobody asked for.
        if places:
            warnings.extend(problems)

    if not places:
        return None, warnings
    counts = tacmap.place_counts(places)
    warnings.append(
        f'{len(places)} place names stored — '
        + ', '.join(f'{counts[key]} {label.lower()}'
                    for key, label in tacmap.PLACE_GROUPS if counts.get(key))
        + '.'
    )
    return json.dumps(places, separators=(',', ':')), warnings


async def set_places(record, pasted: str, member_name: str = None) -> str:
    """Replace a terrain's place names with what was pasted in.

    An empty box clears them, which is the only way back from a paste that was
    the wrong terrain's.
    """
    scope = f"t:{record['id']}"
    if not (pasted or '').strip():
        await database.set_tac_terrain_places(record['id'], None)
        await database.set_tac_places(record['guild_id'], scope, None)
        return f"Cleared the place names on {record['name']}."

    places, problems = tacmap.parse_places(pasted)
    if not places:
        raise ValueError(problems[0] if problems else 'No place names in that.')

    stored = json.dumps(places, separators=(',', ':'))
    await database.set_tac_terrain_places(record['id'], stored)
    await database.set_tac_places(
        record['guild_id'], scope, stored, label=record['name'],
        source='paste', updated_by_name=member_name)
    counts = tacmap.place_counts(places)
    note = (f"{len(places)} place names stored on {record['name']} — "
            + ', '.join(f'{counts[key]} {label.lower()}'
                        for key, label in tacmap.PLACE_GROUPS if counts.get(key))
            + '.')
    if problems:
        note += ' ' + problems[0]
    return note


def load_places(record) -> list:
    """A terrain row's place names, ready to render.

    Parsed on the way out as well as in, the same as `tacmap.parse()` treats a
    document: the column is text, and nothing downstream should have to trust
    it.
    """
    if not record or not record['places']:
        return []
    places, _ = tacmap.parse_places(record['places'])
    return places


async def upload(guild, member, handle, filename: str, name: str,
                 world_size: str, max_zoom: str, places: str = '') -> str:
    try:
        deepest = int(max_zoom)
    except (TypeError, ValueError):
        deepest = DEFAULT_ZOOM
    if deepest not in ZOOM_CHOICES:
        deepest = DEFAULT_ZOOM

    # Decompressing tens of megabytes is seconds of CPU, and this event loop is
    # also holding the Discord connection open — so it happens off it, the same
    # way `web/orbat.py` treats a sheet export. A ValueError from in there is
    # still a message for the person.
    archive = await asyncio.get_event_loop().run_in_executor(
        None, lambda: tiles.read_archive(handle, max_zoom=deepest)
    )
    stored_name = _name(name, archive, (filename or '').rsplit('.', 1)[0])
    size = _world_size(world_size, archive)
    place_json, place_notes = _places(places, archive)

    terrain_id = await database.create_tac_terrain(
        str(guild.id), stored_name, size, archive.deepest, archive.tiles,
        str(member.id), member.display_name, place_json,
    )
    # The column above is this terrain's own copy; the row below is what a map
    # actually reads, because an OCAP-backed map has no terrain row and the two
    # kinds have to meet somewhere. See `tacmap.place_scope()`.
    if place_json:
        await database.set_tac_places(
            str(guild.id), f't:{terrain_id}', place_json, label=stored_name,
            source='upload', updated_by_name=member.display_name)
    note = (f'Stored {stored_name} — {len(archive.tiles)} tiles up to level '
            f'{archive.deepest}, {megabytes(archive.bytes)}, '
            f'{int(size)} m across.')
    if archive.skipped_deeper:
        note += (f' {archive.skipped_deeper} tiles from deeper levels were left '
                 f'out, which is what choosing level {deepest} means.')
    for line in place_notes:
        note += ' ' + line
    return note


async def delete(record) -> str:
    """Remove a terrain, unless a map is drawn on it.

    The maps would not break loudly: the tiles would simply stop answering and
    every plan drawn on that terrain would render on an empty sheet. Naming the
    maps is more use than a confirmation prompt.
    """
    used_by = await database.tac_terrain_usage(record['id'])
    if used_by:
        names = ', '.join(f"“{row['name']}”" for row in used_by[:5])
        more = f' and {len(used_by) - 5} more' if len(used_by) > 5 else ''
        raise ValueError(
            f"{names}{more} {'is' if len(used_by) == 1 else 'are'} drawn on "
            f"{record['name']}. Point those maps at another terrain first."
        )
    await database.delete_tac_terrain(record['id'])
    return f"Deleted {record['name']} and its tiles."


async def apply_to_map(map_record, terrain, member_name: str = None) -> str:
    """Put this terrain under that map, corners and all."""
    settings = tacmap.terrain_settings(
        terrain['id'], terrain['name'], terrain['world_size'], terrain['max_zoom']
    )
    doc = tacmap.parse(map_record['doc']).doc
    doc['background'] = settings['background']
    doc['arma'] = settings['arma']
    # The pyramid is square, so the sheet has to be.
    doc['width'] = doc['height'] = tacmap.DEFAULT_WIDTH
    checked = tacmap.parse(doc)
    await database.save_tac_map_doc(
        map_record['id'], tacmap.dumps(checked.doc), member_name
    )
    return (f"{terrain['name']} is the background now, and the Arma corners are "
            f"0–{int(terrain['world_size'])} m.")

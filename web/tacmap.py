"""The tactical map behind the browser pages.

What a map *is* — the symbols, the document, the drawing — lives in
`utils/tacmap.py`, which imports nothing. This module is the part that knows
about guilds, share links and Discord: it translates form fields and saved
documents into that library's calls and back, and a `ValueError` raised here is
a message meant for the person looking at the page.

### The share link is the whole credential

`/m/{token}` is reachable without signing in, which is the point — the people
who need tonight's plan are in Discord, not necessarily on this site, and
plan-ops-style links are what everyone already expects. So the token is long,
random and revocable: regenerating it is what un-shares a map that has been
forwarded further than intended, and `share_mode` decides whether that link may
draw or only look.
"""

import json
import secrets

import discord

from utils import database, tacmap
from web.guilds import postable_channels

MAX_NAME = 120
MAX_DESCRIPTION = 300

SHARE_MODES = {
    'off': 'Not shared — only people signed in here can open it',
    'view': 'Anyone with the link can look at it',
    'edit': 'Anyone with the link can draw on it',
}

# Long enough that guessing one is not a thing anybody attempts.
TOKEN_BYTES = 24


def _clean(raw, limit: int, what: str) -> str:
    value = (raw or '').strip()
    if len(value) > limit:
        raise ValueError(f'{what} is too long — {limit} characters at most.')
    return value


def _name(raw) -> str:
    name = _clean(raw, MAX_NAME, 'The name')
    if not name:
        raise ValueError('Give the map a name.')
    return name


async def create(guild, member, name, description, background) -> int:
    doc = tacmap.blank_doc()
    doc['background']['url'] = (background or '').strip()
    checked = tacmap.parse(doc)
    return await database.create_tac_map(
        str(guild.id), _name(name),
        _clean(description, MAX_DESCRIPTION, 'The description') or None,
        tacmap.dumps(checked.doc), str(member.id), member.display_name,
    )


async def rename(record, name, description) -> None:
    await database.rename_tac_map(
        record['id'], _name(name),
        _clean(description, MAX_DESCRIPTION, 'The description') or None,
    )


async def duplicate(record, member, name) -> int:
    return await database.duplicate_tac_map(
        record['id'], _name(name), str(member.id), member.display_name
    )


def load(record) -> dict:
    """The stored document, checked on the way out as well as in.

    A row written by a newer version, or by hand, is still only allowed to
    produce something this renderer is willing to draw — the parse is what makes
    that true, so nothing else has to trust the column.
    """
    return tacmap.parse(record['doc']).doc


async def places_for(guild_id, doc: dict) -> list:
    """The place names to draw under this map, or an empty list.

    They belong to the terrain rather than to the map, because every plan drawn
    on Tanoa wants the same ones. `tacmap.place_scope()` is what turns "which
    terrain is this" into one key for both kinds — a row we store and somebody
    else's OCAP folder alike — so an OCAP-backed map is no longer the one that
    cannot have names.
    """
    scope = tacmap.place_scope(doc)
    if not scope:
        return []
    record = await database.get_tac_places(str(guild_id), scope)
    if record is None:
        return []
    places, _ = tacmap.parse_places(record['places'])
    return places


async def save(record, raw_doc: str, member_name: str = None) -> list:
    """Store what the editor sent. Returns the notes worth telling the person."""
    checked = tacmap.parse(raw_doc)
    if not checked.ok:
        raise ValueError(' '.join(checked.errors))
    await database.save_tac_map_doc(
        record['id'], tacmap.dumps(checked.doc), member_name
    )
    return checked.warnings


# Long enough for a home server on a slow line, short enough that a page does
# not sit on a request nobody is going to answer.
OCAP_TIMEOUT = 15


# The listing a directory answers with can be a page of links; cut it off
# well before anything that is not one could arrive.
MAX_INDEX_BYTES = 512 * 1024

# What the directory field is filled in with before anybody has saved one.
# Only a suggestion — it is not read until somebody presses the button, and
# the first directory that actually answers replaces it for that guild. Change
# it for a unit that runs its OCAP somewhere else.
DEFAULT_OCAP_BASE = 'https://ocap.taskforcephalanx.com/images/maps'


def clean_base_url(raw: str) -> str:
    """An OCAP address, tidied — or a message saying why it is not one."""
    url = (raw or '').strip().rstrip('/')
    if not url:
        return ''
    if not url.startswith(('http://', 'https://')):
        raise ValueError(
            'The OCAP address has to start with https:// — for example '
            'https://ocap.example.com/images/maps'
        )
    return url


async def list_ocap_maps(base_url: str) -> list:
    """The terrains that OCAP directory carries, by name.

    Pointing a map at a terrain used to mean knowing its world name and
    typing the whole URL out, which is the one thing nobody remembers —
    `tem_cham` is not what anybody calls Cham. So the folder is read and
    what is in it is offered as a list.

    Read here and not in the browser for exactly the reason `import_ocap()`
    is: the OCAP server is somebody else's origin, and a fetch from the page
    would need CORS headers nobody has set.
    """
    base = clean_base_url(base_url)
    if not base:
        raise ValueError('Say where OCAP serves its terrains first.')

    import aiohttp                      # as utils/sheets.py does with its own

    try:
        timeout = aiohttp.ClientTimeout(total=OCAP_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(f'{base}/') as response:
                if response.status != 200:
                    raise ValueError(
                        f'{base}/ answered {response.status}. That should be the '
                        'folder holding one directory per terrain.'
                    )
                body = await response.content.read(MAX_INDEX_BYTES)
    except ValueError:
        raise
    except Exception as e:
        raise ValueError(f'Could not read {base}/ — {e}')

    names = tacmap.parse_map_index(body)
    if not names:
        raise ValueError(
            f'{base}/ answered, but nothing in it looks like a terrain folder. '
            'Some servers do not list a directory at all — paste one map\'s '
            'address instead.'
        )
    return names


# How big one locations file may be. Tanoa's largest is a few hundred KB
# gzipped; a megabyte is generous and still bounds a bad answer.
MAX_PLACES_BYTES = 4 * 1024 * 1024

# How many files one import may fetch. A directory listing usually names a
# dozen or so; the cap is what stops a hand-written index from turning one
# button press into a hundred requests.
MAX_PLACE_FILES = 30


async def _get(session, url: str, limit: int):
    """One GET, as (status, body). A failure is a status of 0 and the reason."""
    try:
        async with session.get(url) as response:
            if response.status != 200:
                return response.status, b''
            return 200, await response.content.read(limit)
    except Exception as e:                       # noqa: BLE001 — reported, not raised
        return 0, str(e).encode('utf-8', 'replace')


async def import_ocap_places(guild_id, doc: dict, member_name: str = None) -> str:
    """Read this map's terrain's town names off the OCAP server it sits on.

    The names really are in the OCAP data, just not in `map.json`: OCAP builds
    a terrain from a **grad_meh** export, which writes the locations beside the
    tiles as `geojson/locations/<type>.geojson.gz` — one file per Arma location
    type, Point geometry in Arma's own CRS, the name in `properties.name`. So
    this is a read, not a trip into the game.

    What a given server serves is the one thing that cannot be known from here,
    so it **probes and says what it found**: the candidate folders in
    `tacmap.GEOJSON_DIRS`, each listed if the server lists directories and
    otherwise asked for `PROBE_KINDS` by name. A server that carries none of it
    gets a message naming every address tried, which is the difference between
    "this feature is broken" and "your OCAP does not publish that export".
    """
    base = (doc.get('background') or {}).get('url', '').strip().rstrip('/')
    scope = tacmap.place_scope(doc)
    if not base or not scope:
        raise ValueError('Put this map on a terrain first.')
    if base.startswith('/'):
        raise ValueError(
            'This map is on a terrain uploaded here, not on an OCAP server. '
            'Paste its names on the Terrains page instead.'
        )

    import aiohttp                      # as utils/sheets.py does with its own

    tried = []
    groups = []
    notes = []
    timeout = aiohttp.ClientTimeout(total=OCAP_TIMEOUT * 3)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for folder in tacmap.GEOJSON_DIRS:
            root = f'{base}/{folder}'
            # A listing gets every type the terrain has, including the ones
            # PROBE_KINDS leaves out. It is tried first for exactly that.
            status, body = await _get(session, f'{root}/', MAX_INDEX_BYTES)
            names = tacmap.parse_map_index(body) if status == 200 else []
            # Only the location types. A grad_meh `geojson/` folder also
            # holds roads, houses and objects — the whole vector map, tens of
            # megabytes, and carrying no names at all.
            files = [name for name in names
                     if tacmap.is_location_file(name)][:MAX_PLACE_FILES]
            if not files:
                tried.append(f'{root}/ ({status or "unreachable"})')
                files = [f'{kind}.geojson.gz' for kind in tacmap.PROBE_KINDS]
                listed = False
            else:
                listed = True

            found_here = 0
            for name in files:
                status, body = await _get(session, f'{root}/{name}',
                                          MAX_PLACES_BYTES)
                if status != 200 or not body:
                    continue
                places, problems = tacmap.places_from_geojson(
                    body, tacmap.geojson_kind(name))
                if places:
                    groups.append(places)
                    found_here += len(places)
                elif problems and listed:
                    notes.append(f'{name}: {problems[0]}')

            if found_here:
                notes.insert(0, f'Read from {root}/.')
                break
            if listed:
                tried.append(f'{root}/ (listed, no usable locations)')

    if not groups:
        raise ValueError(
            'No place names on that server. Tried: ' + '; '.join(tried) +
            '. That terrain was built the older way — Arma\u2019s map export '
            'through gdal2tiles — which carries no location data at all, so '
            'there is nothing there to find. Use the script and the box just '
            'below this button: once for this terrain, and every map on it has '
            'them.'
        )

    places, capped = tacmap.merge_places(groups)
    counts = tacmap.place_counts(places)
    await database.set_tac_places(
        str(guild_id), scope, json.dumps(places, separators=(',', ':')),
        label=(doc.get('arma') or {}).get('terrain') or scope,
        source='ocap', updated_by_name=member_name,
    )
    summary = ', '.join(f'{counts[key]} {label.lower()}'
                        for key, label in tacmap.PLACE_GROUPS if counts.get(key))
    noun = 'place name' if len(places) == 1 else 'place names'
    return (f'{len(places)} {noun} imported — {summary}. '
            + ' '.join(notes[:3] + capped))


async def set_places(guild_id, doc: dict, pasted: str,
                     member_name: str = None) -> str:
    """Store place names pasted in for whatever terrain this map is on.

    This exists because the Terrains page could not serve the case it was being
    pointed at. Its paste box hangs off an uploaded terrain row, and a map on an
    OCAP server has none — so the one remaining way to get names onto such a
    map, the debug-console script, had nowhere to be pasted. The refusal even
    named that page. Here the box sits on the map, and the scope comes from the
    map itself, so it works for both kinds.
    """
    scope = tacmap.place_scope(doc)
    if not scope:
        raise ValueError('Put this map on a terrain first.')

    label = (doc.get('arma') or {}).get('terrain') or scope
    if not (pasted or '').strip():
        await database.set_tac_places(str(guild_id), scope, None)
        return f'Cleared the place names for {label}.'

    places, problems = tacmap.parse_places(pasted)
    if not places:
        raise ValueError(problems[0] if problems else 'No place names in that.')

    await database.set_tac_places(
        str(guild_id), scope, json.dumps(places, separators=(',', ':')),
        label=label, source='paste', updated_by_name=member_name,
    )
    counts = tacmap.place_counts(places)
    noun = 'place name' if len(places) == 1 else 'place names'
    note = (f'{len(places)} {noun} stored for {label} — '
            + ', '.join(f'{counts[key]} {name.lower()}'
                        for key, name in tacmap.PLACE_GROUPS if counts.get(key))
            + '. Every map on this terrain has them now.')
    if problems:
        note += ' ' + problems[0]
    return note


async def import_ocap(record, raw_url: str, member_name: str = None) -> str:
    """Point this map at an OCAP terrain, and take its calibration with it.

    OCAP renders the terrains a unit actually plays on and serves them as a
    tile folder with a `map.json` beside it, so one address settles both halves
    of setting a map up: what it looks like, and where its corners are in
    Arma's world. Reading it here rather than in the browser is not a
    preference — the tile server is somebody else's origin and a fetch from the
    page would need CORS headers nobody has set.
    """
    base = (raw_url or '').strip().rstrip('/')
    if base.endswith('/map.json'):
        base = base[:-len('/map.json')]
    if not base.startswith(('http://', 'https://')):
        raise ValueError(
            'Paste the address of the OCAP map folder, starting with https://'
        )

    import aiohttp                      # as utils/sheets.py does with its own

    try:
        timeout = aiohttp.ClientTimeout(total=OCAP_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(f'{base}/map.json') as response:
                if response.status != 200:
                    raise ValueError(
                        f'{base}/map.json answered {response.status}. The address '
                        'should be the folder holding map.json and the numbered '
                        'tile folders.'
                    )
                # OCAP serves it as text/plain often enough that the content
                # type is not worth failing over.
                payload = await response.json(content_type=None)
    except ValueError:
        raise
    except Exception as e:
        raise ValueError(f'Could not read {base}/map.json — {e}')

    settings = tacmap.ocap_settings(payload, base)
    doc = load(record)
    doc['background'] = settings['background']
    doc['arma'] = settings['arma']
    # An OCAP pyramid is square, so the sheet has to be: a landscape sheet would
    # stretch the terrain sideways.
    doc['width'] = doc['height'] = tacmap.DEFAULT_WIDTH
    checked = tacmap.parse(doc)
    await database.save_tac_map_doc(record['id'], tacmap.dumps(checked.doc), member_name)
    return (f"Loaded {settings['name']} — the terrain is the background, and the "
            f"Arma corners are 0–{int(settings['arma']['right'])} m.")


async def share(record, mode: str) -> str:
    """Turn sharing on, change what the link may do, or take it away."""
    if mode not in SHARE_MODES:
        raise ValueError('Pick what the link may do.')
    if mode == 'off':
        await database.set_tac_map_share(record['id'], None, 'off')
        return 'The share link is off — it stops working straight away.'

    # Keep the link stable while only the rights change, so a mode change does
    # not silently break every copy of it somebody has already sent out.
    token = record['share_token'] or secrets.token_urlsafe(TOKEN_BYTES)
    await database.set_tac_map_share(record['id'], token, mode)
    return ('Anyone with the link can draw on it now.' if mode == 'edit'
            else 'Anyone with the link can look at it now.')


async def regenerate(record) -> str:
    if record['share_mode'] == 'off':
        raise ValueError('This map is not shared, so there is no link to replace.')
    await database.set_tac_map_share(
        record['id'], secrets.token_urlsafe(TOKEN_BYTES), record['share_mode']
    )
    return 'New link — the old one no longer opens this map.'


def arma_prefix(record) -> str:
    """The marker prefix this map owns, and no other map does.

    Every exported marker is named after it and the script deletes that set
    before it draws, so pasting a corrected plan replaces the old one in the
    running mission instead of laying a second copy over it. Two maps sharing a
    prefix would delete each other's markers, hence the row id.
    """
    return f"map{record['id']}"


def sqf(record, editable: bool = False, channel: int = None) -> str:
    return tacmap.to_sqf(load(record), prefix=arma_prefix(record),
                         title=record['name'], editable=editable,
                         channel=tacmap.DEFAULT_CHANNEL if channel is None
                         else channel)


def share_path(record) -> str:
    return f"/m/{record['share_token']}" if record['share_token'] else ''


def map_url(origin: str, guild_id, record) -> str:
    """Where to send somebody: the share link when there is one, the page otherwise."""
    path = share_path(record) or f'/g/{guild_id}/maps/{record["id"]}'
    return f"{origin.rstrip('/')}{path}"


def _channel(guild: discord.Guild, channel_id: str):
    for channel in postable_channels(guild):
        if str(channel.id) == str(channel_id):
            return channel
    raise ValueError('Pick a channel I can post in.')


async def post(guild: discord.Guild, record, channel_id: str, url: str,
               member) -> str:
    """Announce the map in a channel, as a link.

    A link rather than a picture: the map is an SVG built in the browser, and
    Discord renders neither SVG attachments nor anything this bot could turn one
    into without a drawing library it does not have. The link is also the thing
    people actually want — it stays current while the plan is edited.
    """
    channel = _channel(guild, channel_id)
    doc = load(record)
    embed = discord.Embed(
        title=f"🗺️ {record['name']}",
        description=record['description'] or None,
        url=url,
        colour=discord.Colour.blurple(),
    )
    embed.add_field(name='On the map', value=tacmap.summarise(doc), inline=True)
    embed.add_field(
        name='Link',
        value=('Anyone with it can draw' if record['share_mode'] == 'edit'
               else 'Anyone with it can look' if record['share_mode'] == 'view'
               else 'Sign-in needed'),
        inline=True,
    )
    embed.set_footer(text=f'Posted by {member.display_name}')

    try:
        message = await channel.send(embed=embed)
    except discord.Forbidden:
        raise ValueError(f"I'm not allowed to post in #{channel.name}.")
    except discord.HTTPException as e:
        raise ValueError(f'Discord rejected the message: {e}.')

    await database.save_tac_map_message(record['id'], str(channel.id), str(message.id))
    return f'Posted in #{channel.name}.'


async def delete(record) -> str:
    await database.delete_tac_map(record['id'])
    return f"Deleted “{record['name']}”."

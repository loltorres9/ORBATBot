"""One tactical map: what can be drawn on it, and how it draws.

A map is a **document** — a background image, a grid and a list of items — and
this module is the only thing that knows what an item is. `web/tacmap.py`
translates forms and HTTP into it, `web/app.py` serves it, and the browser
editor in `web/static/tacmap.js` manipulates the same document client-side.

**It imports nothing but the standard library**, for the same reason
`utils/orbat.py` does: the symbol geometry and the validation are the two places
where a bug either loses somebody's planning or puts unescaped text into a page,
and both need to be testable without a database, a Discord connection or a web
server (`tests/test_tacmap.py`).

### The geometry lives here, not in the JavaScript

`defs()` emits every frame, icon and arrowhead once, as `<g>` elements in a
`<defs>` block. Both renderers then only ever place a `<use href="#tmf-friend">`
— so the browser and the server draw the *same* infantry symbol, and adding one
means adding it in one place. Only the composition (which `<use>`, at which
coordinates) is written twice, and it is three attributes long on purpose.

Referenced content is `<g>` rather than `<symbol>` deliberately: a `<symbol>`
establishes a viewport and clips, which would cut the staff off a headquarters
and the arrowhead off a line.

### Everything is in document units

An item's coordinates are in the document's own space (`width` × `height`,
1000 × 1000 by default) and the SVG carries that as its `viewBox`, so the map
scales to whatever it is drawn into without a single number changing. The
background image is stretched across the same box: the document *is* the map
sheet, and nothing here knows about pixels or zoom levels.
"""

import json
import math
from xml.sax.saxutils import escape, quoteattr

DEFAULT_WIDTH = 1000
DEFAULT_HEIGHT = 1000

# Limits. Generous enough for a company plan and small enough that one document
# stays a reasonable row, a reasonable POST body and a page a browser can draw.
MAX_ITEMS = 400
MAX_POINTS = 120
MAX_LABEL = 48
MAX_NOTE = 240
MAX_GLYPH = 4
MIN_SIZE = 0.4
MAX_SIZE = 4.0

# The box a size-1 symbol occupies, in document units.
UNIT_BOX = 54
POINT_BOX = 34

KINDS = ('unit', 'point', 'line', 'area', 'text')

# APP-6-ish: the frame says whose it is, the icon says what it is. The fill is
# the tint the frame is painted with, the stroke everything drawn on top of it.
AFFILIATIONS = {
    'friend': {'label': 'Friendly', 'fill': '#9dc2ff', 'stroke': '#14418f'},
    'hostile': {'label': 'Hostile', 'fill': '#ff9f9f', 'stroke': '#8f1414'},
    'neutral': {'label': 'Neutral', 'fill': '#9fe3a0', 'stroke': '#146b28'},
    'unknown': {'label': 'Unknown', 'fill': '#ffdf8a', 'stroke': '#8a5c00'},
}
DEFAULT_SIDE = 'friend'

# The frames, drawn in a 100 × 100 box centred on (50, 50). They carry no paint
# of their own so the `<use>` that places one decides the colours.
_FRAMES = {
    'friend': '<path d="M8,26 H92 V74 H8 Z"/>',
    'hostile': '<path d="M50,6 L94,50 L50,94 L6,50 Z"/>',
    'neutral': '<path d="M14,14 H86 V86 H14 Z"/>',
    # A quatrefoil: four half-circles bulging out of a square.
    'unknown': ('<path d="M30,30 A20,20 0 0 1 70,30 A20,20 0 0 1 70,70 '
                'A20,20 0 0 1 30,70 A20,20 0 0 1 30,30 Z"/>'),
}

# The icon sits in x 25–75, y 33–67, which fits inside every frame above —
# including the diamond, which is narrowest where the icon is tallest.
#
# An icon inherits `stroke` and `fill="none"` from the `<use>` that places it;
# a shape that is meant to be solid says `fill="currentColor"` itself, and the
# `<use>` sets `color` to the same stroke colour.
SYMBOLS = {
    'generic': {'label': 'Unspecified', 'group': 'Infantry', 'icon': ''},
    'inf': {'label': 'Infantry', 'group': 'Infantry',
            'icon': '<path d="M27,34 L73,66 M73,34 L27,66"/>'},
    'mech': {'label': 'Mechanised infantry', 'group': 'Infantry',
             'icon': '<ellipse cx="50" cy="50" rx="25" ry="16"/>'
                     '<path d="M27,34 L73,66 M73,34 L27,66"/>'},
    'motor': {'label': 'Motorised infantry', 'group': 'Infantry',
              'icon': '<path d="M27,34 L73,66 M73,34 L27,66"/>'
                      '<circle cx="50" cy="50" r="7" fill="currentColor"/>'},
    'armor': {'label': 'Armour', 'group': 'Vehicles',
              'icon': '<ellipse cx="50" cy="50" rx="25" ry="16"/>'},
    'recon': {'label': 'Reconnaissance', 'group': 'Infantry',
              'icon': '<path d="M27,66 L73,34"/>'},
    'sniper': {'label': 'Sniper / marksman', 'group': 'Infantry',
               'icon': '<circle cx="50" cy="50" r="13"/>'
                       '<path d="M50,30 V70 M30,50 H70"/>'},
    'mg': {'label': 'Machine gun', 'group': 'Weapons',
           'icon': '<path d="M50,33 V57 M33,67 L50,57 L67,67"/>'},
    'at': {'label': 'Anti-tank', 'group': 'Weapons',
           'icon': '<path d="M27,67 L50,34 L73,67"/>'},
    'aa': {'label': 'Air defence', 'group': 'Weapons',
           'icon': '<path d="M27,64 A26,26 0 0 1 73,64"/>'},
    'arty': {'label': 'Artillery', 'group': 'Weapons',
             'icon': '<circle cx="50" cy="50" r="11" fill="currentColor"/>'},
    'mortar': {'label': 'Mortar', 'group': 'Weapons',
               'icon': '<path d="M50,32 V68"/><circle cx="50" cy="50" r="10"/>'},
    'veh': {'label': 'Wheeled vehicle', 'group': 'Vehicles',
            'icon': '<path d="M30,40 H70 V57 H30 Z"/>'
                    '<circle cx="39" cy="63" r="5" fill="currentColor"/>'
                    '<circle cx="61" cy="63" r="5" fill="currentColor"/>'},
    'air': {'label': 'Fixed wing', 'group': 'Air',
            'icon': '<path d="M26,38 L50,50 L74,38 L74,62 L50,50 L26,62 Z"/>'},
    'heli': {'label': 'Rotary wing', 'group': 'Air',
             'icon': '<path d="M26,42 L50,52 L74,42 L74,64 L50,54 L26,64 Z"/>'
                     '<path d="M26,34 H74"/>'},
    'uav': {'label': 'UAV', 'group': 'Air',
            'icon': '<path d="M26,42 L50,52 L74,42 L74,64 L50,54 L26,64 Z"/>'
                    '<circle cx="50" cy="34" r="5" fill="currentColor"/>'},
    'engr': {'label': 'Engineers', 'group': 'Support',
             'icon': '<path d="M28,66 V44 H72 V66"/>'},
    'med': {'label': 'Medical', 'group': 'Support',
            'icon': '<path d="M50,34 V66 M31,50 H69"/>'},
    'logi': {'label': 'Logistics', 'group': 'Support',
             'icon': '<path d="M30,38 H70 V62 H30 Z M30,62 L70,38"/>'},
    'signal': {'label': 'Signals', 'group': 'Support',
               'icon': '<path d="M30,66 L50,34 L70,66 M38,54 H62"/>'},
}
DEFAULT_SYMBOL = 'inf'

# The staff of a headquarters, hanging off the frame's lower-left corner. It is
# a modifier rather than its own symbol because any unit can be the one in
# charge — an HQ that is also a medical company is a medical icon on a staff.
_HQ_STAFF = '<path d="M8,74 V116" fill="none" stroke-linecap="square"/>'

LINE_STYLES = ('solid', 'dashed')

# ---------------------------------------------------------------------------
# Backgrounds
# ---------------------------------------------------------------------------
#
# Two kinds. `image` is one picture stretched across the sheet, which is what a
# screenshot or a map export is. `tiles` is the pyramid of 256 px squares that
# GDAL2Tiles produces and that **OCAP already ships for every terrain a unit
# plays on** — `{z}/{x}/{y}.png` under one folder per world. Pointing at that
# folder is worth the second kind on its own: it is a real map of the terrain,
# at a resolution one image could not carry, that the unit is already hosting.
BACKGROUND_KINDS = ('image', 'tiles')

# The pyramid doubles each level, so a level holds 4^z tiles: zoom 4 is 256
# requests and 256 elements, which is a lot already, and 6 would be 4096.
TILE_SIZE = 256
MAX_TILE_ZOOM = 5
DEFAULT_TILE_ZOOM = 4

# Tiles are counted from the **top** left, the way GDAL2Tiles writes them and
# the way OCAP reads them back — checked against OCAP's own Tanoa set, which
# only assembles into Tanoa this way up. There is deliberately no switch for
# the other convention: nothing we have seen uses it.
#
# Neighbouring tiles share an edge exactly, and a browser scaling each one to a
# fractional pixel size anti-aliases both sides of that edge — which reads as a
# grid of bright hairlines drawn over the terrain. So every tile is drawn
# slightly over its neighbour, whose own edge then covers the seam.
#
# The overlap is a fraction of the **sheet**, not of a tile: the seam is about
# one device pixel wide whatever zoom level is showing, so the fix has to be the
# same width too. It costs each tile that much stretch — about fifteen metres on
# a 15 km terrain, and always less than the seam it replaces.
TILE_BLEED = 0.0012

# What the editor offers as ready-made markers. The glyph is stored, not the
# preset, so renaming one here never changes a map that was already drawn.
POINT_PRESETS = (
    ('OBJ', 'Objective'),
    ('RP', 'Rally point'),
    ('LZ', 'Landing zone'),
    ('PZ', 'Pickup zone'),
    ('CCP', 'Casualty collection'),
    ('SUP', 'Supply / cache'),
    ('OP', 'Observation post'),
    ('CP', 'Checkpoint'),
    ('TGT', 'Target'),
    ('IED', 'IED / mines'),
    ('SP', 'Start point'),
    ('WP', 'Waypoint'),
)

# ---------------------------------------------------------------------------
# Arma 3
# ---------------------------------------------------------------------------
#
# A map drawn here can be put into a running mission as ordinary Arma markers,
# with no mod on either side — see `to_sqf()` for why that is a script somebody
# pastes rather than something this bot sends.

# Terrain sizes in metres, for the preset that fills the extent in. The list is
# a convenience only: any terrain works, because what the export actually needs
# is the four corner coordinates below.
ARMA_TERRAINS = (
    ('Altis', 30720),
    ('Stratis', 8192),
    ('Malden', 12800),
    ('Tanoa', 15360),
    ('Livonia', 12800),
    ('Chernarus', 15360),
    ('Takistan', 12800),
    ('Sahrani', 20480),
)
DEFAULT_EXTENT = {'terrain': '', 'left': 0.0, 'bottom': 0.0,
                  'right': 30720.0, 'top': 30720.0}

# Which side a frame is, in Arma's terms: the marker-type prefix and the colour
# used for everything that is not an icon.
ARMA_SIDES = {
    'friend': ('b', 'ColorWEST'),
    'hostile': ('o', 'ColorEAST'),
    'neutral': ('n', 'ColorGUER'),
    'unknown': ('u', 'ColorUNKNOWN'),
}

# Our symbols against the NATO markers vanilla Arma 3 ships. Several have no
# counterpart — there is no anti-tank or sniper marker — so they land on the
# nearest thing that is in the game rather than on nothing.
ARMA_TYPES = {
    'generic': 'unknown', 'inf': 'inf', 'mech': 'mech_inf', 'motor': 'motor_inf',
    'armor': 'armor', 'recon': 'recon', 'sniper': 'recon', 'mg': 'inf',
    'at': 'support', 'aa': 'antiair', 'arty': 'art', 'mortar': 'mortar',
    'veh': 'motor_inf', 'air': 'plane', 'heli': 'air', 'uav': 'uav',
    'engr': 'maint', 'med': 'med', 'logi': 'service', 'signal': 'support',
}

# The few markers a glyph obviously means; everything else is a plain dot with
# its text, which is what the glyph was anyway.
ARMA_POINTS = {
    'OBJ': 'mil_objective', 'TGT': 'mil_destroy', 'SP': 'mil_start',
    'RP': 'mil_end', 'IED': 'mil_warning', 'CP': 'mil_triangle',
    'LZ': 'mil_pickup', 'PZ': 'mil_pickup',
}
ARMA_POINT_DEFAULT = 'mil_dot'

# Text drawn over a satellite image needs a halo or it disappears into the
# terrain; white on a dark outline is what every map tool ends up at.
_LABEL_FILL = '#ffffff'
_LABEL_HALO = '#11161c'


# ---------------------------------------------------------------------------
# The document
# ---------------------------------------------------------------------------


def blank_doc(width: int = DEFAULT_WIDTH, height: int = DEFAULT_HEIGHT) -> dict:
    """An empty map — what a freshly created one opens with."""
    return {
        'width': width,
        'height': height,
        'background': {'kind': 'image', 'url': '', 'opacity': 1.0,
                       'zoom': DEFAULT_TILE_ZOOM, 'max_zoom': MAX_TILE_ZOOM,
                       'name': ''},
        'grid': {'show': False, 'cols': 10, 'rows': 10},
        # Where the sheet's corners are in Arma's world, in metres. Only the
        # export reads it, and only the person exporting can know it — which
        # image of which terrain this is, and whether it is the whole map.
        'arma': dict(DEFAULT_EXTENT),
        'items': [],
    }


class ParseResult:
    """A document, and what was wrong with the thing it was parsed from.

    Mirrors `orbat.ParseResult`: errors mean the document is not saveable,
    warnings mean something was silently straightened out and the person should
    know. An item this version does not understand is a warning rather than an
    error — an older deployment reading a newer map should lose that item, not
    refuse the whole plan.
    """

    def __init__(self, doc: dict):
        self.doc = doc
        self.errors = []
        self.warnings = []

    @property
    def ok(self) -> bool:
        return not self.errors


def _number(value, fallback=None):
    """A finite float, or None. NaN and infinity are not coordinates."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    if math.isnan(number) or math.isinf(number):
        return fallback
    return number


def _clamp(value, low, high):
    return low if value < low else (high if value > high else value)


def _text(value, limit: int) -> str:
    return (value or '').strip()[:limit] if isinstance(value, str) else ''


def _safe_url(raw) -> str:
    """A background URL we are willing to put in an `href`.

    Only http(s), because `javascript:` and `data:` in an image href are how an
    editable map becomes a way to run script in somebody else's session.
    """
    url = (raw or '').strip() if isinstance(raw, str) else ''
    if not url:
        return ''
    return url if url.lower().startswith(('http://', 'https://')) else ''


def parse(raw) -> ParseResult:
    """Read a document from JSON text or a plain dict, and check every field.

    Nothing that comes out of here can be unsafe to render: the coordinates are
    finite and bounded, the strings are cut to length, the enumerations are
    known values, and the background URL is http(s) or empty.
    """
    if isinstance(raw, (str, bytes)):
        try:
            raw = json.loads(raw or '{}')
        except (ValueError, TypeError) as e:
            result = ParseResult(blank_doc())
            result.errors.append(f'That is not a map document ({e}).')
            return result
    if not isinstance(raw, dict):
        result = ParseResult(blank_doc())
        result.errors.append('That is not a map document.')
        return result

    width = int(_clamp(_number(raw.get('width'), DEFAULT_WIDTH), 100, 20000))
    height = int(_clamp(_number(raw.get('height'), DEFAULT_HEIGHT), 100, 20000))
    doc = blank_doc(width, height)
    result = ParseResult(doc)

    background = raw.get('background')
    if isinstance(background, dict):
        url = _safe_url(background.get('url'))
        if background.get('url') and not url:
            result.warnings.append(
                'The background has to be an http:// or https:// link — '
                'that one was dropped.'
            )
        kind = (background.get('kind') if background.get('kind') in BACKGROUND_KINDS
                else 'image')
        # What the tile set actually has, which bounds what may be asked for:
        # a zoom above it is a folder of 404s where the map should be.
        max_zoom = int(_clamp(_number(background.get('max_zoom'), MAX_TILE_ZOOM),
                              0, MAX_TILE_ZOOM))
        doc['background'] = {
            'kind': kind,
            'url': url.rstrip('/') if kind == 'tiles' else url,
            'opacity': round(_clamp(_number(background.get('opacity'), 1.0), 0.1, 1.0), 2),
            'zoom': int(_clamp(_number(background.get('zoom'), DEFAULT_TILE_ZOOM),
                               0, max_zoom)),
            'max_zoom': max_zoom,
            'name': _text(background.get('name'), 80),
        }

    grid = raw.get('grid')
    if isinstance(grid, dict):
        doc['grid'] = {
            'show': bool(grid.get('show')),
            'cols': int(_clamp(_number(grid.get('cols'), 10), 1, 100)),
            'rows': int(_clamp(_number(grid.get('rows'), 10), 1, 100)),
        }

    arma = raw.get('arma')
    if isinstance(arma, dict):
        corners = {name: _number(arma.get(name), DEFAULT_EXTENT[name])
                   for name in ('left', 'bottom', 'right', 'top')}
        if corners['right'] <= corners['left'] or corners['top'] <= corners['bottom']:
            # A zero-width or inside-out sheet would divide by zero on export
            # and put every marker in the same spot; the default is at least a
            # map somebody can correct.
            result.warnings.append(
                'The Arma corners have to go left → right and bottom → top — '
                'they were put back to the default.'
            )
            corners = {name: DEFAULT_EXTENT[name]
                       for name in ('left', 'bottom', 'right', 'top')}
        doc['arma'] = {'terrain': _text(arma.get('terrain'), 60), **corners}

    items = raw.get('items')
    if items is None:
        items = []
    if not isinstance(items, list):
        result.errors.append('The items are not a list.')
        return result
    if len(items) > MAX_ITEMS:
        result.errors.append(
            f'{len(items)} items — a map holds {MAX_ITEMS} at most.'
        )
        return result

    for index, raw_item in enumerate(items, start=1):
        item = _parse_item(raw_item, doc, result, index)
        if item is not None:
            doc['items'].append(item)
    return result


def _parse_item(raw, doc: dict, result: ParseResult, index: int):
    if not isinstance(raw, dict):
        result.warnings.append(f'Item {index} is not an item and was dropped.')
        return None

    kind = raw.get('kind') if raw.get('kind') in KINDS else None
    if kind is None:
        result.warnings.append(
            f'Item {index} is a "{raw.get("kind")}", which this version does not '
            'know — it was dropped.'
        )
        return None

    side = raw.get('side') if raw.get('side') in AFFILIATIONS else DEFAULT_SIDE
    item = {
        'kind': kind,
        'side': side,
        'label': _text(raw.get('label'), MAX_LABEL),
        'note': _text(raw.get('note'), MAX_NOTE),
        'size': round(_clamp(_number(raw.get('size'), 1.0), MIN_SIZE, MAX_SIZE), 2),
    }

    if kind in ('unit', 'point', 'text'):
        point = _parse_point(raw.get('x'), raw.get('y'), doc)
        if point is None:
            result.warnings.append(f'Item {index} has no position and was dropped.')
            return None
        item['x'], item['y'] = point

    if kind == 'unit':
        symbol = raw.get('symbol')
        if symbol not in SYMBOLS:
            if symbol:
                result.warnings.append(
                    f'Item {index} uses the unknown symbol "{symbol}" — drawn as '
                    'an empty frame instead.'
                )
            symbol = 'generic' if symbol else DEFAULT_SYMBOL
        item['symbol'] = symbol
        item['hq'] = bool(raw.get('hq'))
        item['rotation'] = round(_clamp(_number(raw.get('rotation'), 0.0), -360, 360), 1)
    elif kind == 'point':
        item['glyph'] = _text(raw.get('glyph'), MAX_GLYPH).upper()
    elif kind in ('line', 'area'):
        points = _parse_points(raw.get('points'), doc)
        least = 2 if kind == 'line' else 3
        if len(points) < least:
            result.warnings.append(
                f'Item {index} is a {kind} with fewer than {least} points and was '
                'dropped.'
            )
            return None
        item['points'] = points
        item['style'] = raw.get('style') if raw.get('style') in LINE_STYLES else 'solid'
        if kind == 'line':
            item['arrow'] = bool(raw.get('arrow'))
    return item


def _parse_point(raw_x, raw_y, doc: dict):
    x, y = _number(raw_x), _number(raw_y)
    if x is None or y is None:
        return None
    # A quarter of the sheet outside it: far enough that dragging something off
    # the edge keeps it, near enough that a broken client cannot put an item
    # a million units away and make the whole map unreadable.
    return (round(_clamp(x, -doc['width'] / 4, doc['width'] * 1.25), 2),
            round(_clamp(y, -doc['height'] / 4, doc['height'] * 1.25), 2))


def _parse_points(raw, doc: dict) -> list:
    if not isinstance(raw, list):
        return []
    points = []
    for pair in raw[:MAX_POINTS]:
        if isinstance(pair, (list, tuple)) and len(pair) == 2:
            point = _parse_point(pair[0], pair[1], doc)
        elif isinstance(pair, dict):
            point = _parse_point(pair.get('x'), pair.get('y'), doc)
        else:
            point = None
        if point is not None:
            points.append(list(point))
    return points


def dumps(doc: dict) -> str:
    """The document as it is stored — compact, and with the keys in one order."""
    return json.dumps(doc, separators=(',', ':'), sort_keys=True)


def json_payload(value) -> str:
    """JSON safe to drop inside a `<script>` element.

    The content of a script tag is raw text, so a label containing `</script>`
    would otherwise end the element and everything after it would be markup —
    which, with labels being text anybody with the share link can type, is the
    one way this page could be made to run somebody else's script. `<`, `>` and
    `&` go out as escapes, which JSON readers accept and a parser never ends a
    tag on.
    """
    return (json.dumps(value, separators=(',', ':'))
            .replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026'))


def summarise(doc: dict) -> str:
    """"12 units · 3 lines · 1 area" — the list page and the Discord post."""
    counts = {}
    for item in doc.get('items') or []:
        counts[item['kind']] = counts.get(item['kind'], 0) + 1
    names = {'unit': 'unit', 'point': 'marker', 'line': 'line', 'area': 'area',
             'text': 'label'}
    parts = [
        f"{counts[kind]} {names[kind]}{'' if counts[kind] == 1 else 's'}"
        for kind in KINDS if counts.get(kind)
    ]
    return ' · '.join(parts) if parts else 'empty'


def catalog() -> dict:
    """What the browser editor is allowed to draw, as one JSON-able structure.

    The palette, the colours and the limits all come from here rather than being
    written out again in the JavaScript, so the two sides cannot end up offering
    different symbols or disagreeing about what a valid map is.
    """
    return {
        'sides': [
            {'key': key, 'label': value['label'], 'fill': value['fill'],
             'stroke': value['stroke']}
            for key, value in AFFILIATIONS.items()
        ],
        'symbols': [
            {'key': key, 'label': value['label'], 'group': value['group'],
             'hasIcon': bool(value['icon'])}
            for key, value in SYMBOLS.items()
        ],
        'points': [{'glyph': glyph, 'label': label} for glyph, label in POINT_PRESETS],
        'terrains': [{'name': name, 'size': size} for name, size in ARMA_TERRAINS],
        'lineStyles': list(LINE_STYLES),
        'unitBox': UNIT_BOX,
        'pointBox': POINT_BOX,
        'tileBleed': TILE_BLEED,
        'maxTileZoom': MAX_TILE_ZOOM,
        'limits': {
            'items': MAX_ITEMS, 'points': MAX_POINTS, 'label': MAX_LABEL,
            'note': MAX_NOTE, 'glyph': MAX_GLYPH,
            'minSize': MIN_SIZE, 'maxSize': MAX_SIZE,
        },
    }


def symbol_groups() -> list:
    """The catalogue for the editor's palette, grouped as it is displayed."""
    groups = {}
    for key, symbol in SYMBOLS.items():
        groups.setdefault(symbol['group'], []).append({'key': key, **symbol})
    return [{'name': name, 'symbols': symbols} for name, symbols in groups.items()]


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------


def _attrs(**values) -> str:
    return ''.join(
        f' {name.replace("_", "-")}={quoteattr(str(value))}'
        for name, value in values.items() if value is not None and value != ''
    )


def defs() -> str:
    """Every frame, icon and arrowhead, once.

    Emitted into the editor page and into any standalone render, so a `<use>`
    is all either renderer needs. Adding a symbol here adds it to both.
    """
    parts = ['<defs>']
    for side, frame in _FRAMES.items():
        parts.append(f'<g id="tmf-{side}">{frame}</g>')
    parts.append(f'<g id="tmf-hq">{_HQ_STAFF}</g>')
    for key, symbol in SYMBOLS.items():
        parts.append(f'<g id="tmi-{key}">{symbol["icon"]}</g>')
    for side, colours in AFFILIATIONS.items():
        parts.append(
            f'<marker id="tma-{side}" viewBox="0 0 10 10" refX="8" refY="5" '
            f'markerWidth="5" markerHeight="5" orient="auto-start-reverse">'
            f'<path d="M0,0 L10,5 L0,10 Z" fill="{colours["stroke"]}"/></marker>'
        )
    parts.append('</defs>')
    return ''.join(parts)


def _label_svg(text: str, x: float, y: float, size: float, anchor: str = 'middle') -> str:
    if not text:
        return ''
    return (
        f'<text{_attrs(x=round(x, 2), y=round(y, 2), text_anchor=anchor)} '
        f'class="tm-label" font-size="{round(14 * size, 1)}" '
        f'font-family="system-ui, -apple-system, Segoe UI, Roboto, sans-serif" '
        f'font-weight="600" fill="{_LABEL_FILL}" stroke="{_LABEL_HALO}" '
        f'stroke-width="{round(3 * size, 1)}" paint-order="stroke" '
        f'stroke-linejoin="round">{escape(text)}</text>'
    )


def item_svg(item: dict) -> str:
    """One item, drawn. The browser composes the same three or four elements."""
    kind = item['kind']
    if kind == 'unit':
        return _unit_svg(item)
    if kind == 'point':
        return _point_svg(item)
    if kind == 'text':
        return _text_svg(item)
    return _shape_svg(item)


def _unit_svg(item: dict) -> str:
    colours = AFFILIATIONS[item['side']]
    scale = UNIT_BOX * item['size'] / 100
    # The symbol is drawn in its own 100 × 100 box and then moved onto the map,
    # so a rotation turns the symbol about its own centre rather than the sheet.
    transform = (f"translate({round(item['x'], 2)},{round(item['y'], 2)}) "
                 f"rotate({item.get('rotation', 0)}) scale({round(scale, 4)}) "
                 f"translate(-50,-50)")
    parts = [
        f'<use href="#tmf-{item["side"]}" fill="{colours["fill"]}" '
        f'stroke="{colours["stroke"]}" stroke-width="4"/>'
    ]
    if item.get('hq'):
        parts.append(f'<use href="#tmf-hq" stroke="{colours["stroke"]}" stroke-width="4"/>')
    if SYMBOLS[item['symbol']]['icon']:
        parts.append(
            f'<use href="#tmi-{item["symbol"]}" fill="none" '
            f'stroke="{colours["stroke"]}" color="{colours["stroke"]}" '
            f'stroke-width="7" stroke-linecap="round" stroke-linejoin="round"/>'
        )
    # The label sits under the frame, and under the staff when there is one —
    # a headquarters would otherwise have its own name drawn over its staff.
    drop = UNIT_BOX * item['size'] * (0.8 if item.get('hq') else 0.62) + 6
    label = _label_svg(item['label'], item['x'], item['y'] + drop, item['size'])
    return f'<g transform="{transform}">{"".join(parts)}</g>{label}'


def _point_svg(item: dict) -> str:
    colours = AFFILIATIONS[item['side']]
    radius = POINT_BOX * item['size'] / 2
    parts = [
        f'<circle cx="{round(item["x"], 2)}" cy="{round(item["y"], 2)}" '
        f'r="{round(radius, 2)}" fill="{colours["fill"]}" '
        f'stroke="{colours["stroke"]}" stroke-width="{round(3 * item["size"], 2)}"/>'
    ]
    if item.get('glyph'):
        parts.append(
            f'<text x="{round(item["x"], 2)}" y="{round(item["y"] + radius * 0.36, 2)}" '
            f'text-anchor="middle" font-size="{round(radius * 0.95, 1)}" '
            f'font-family="system-ui, -apple-system, Segoe UI, Roboto, sans-serif" '
            f'font-weight="700" fill="{colours["stroke"]}">'
            f'{escape(item["glyph"])}</text>'
        )
    parts.append(_label_svg(item['label'], item['x'], item['y'] + radius + 16 * item['size'],
                            item['size']))
    return ''.join(parts)


def _text_svg(item: dict) -> str:
    return _label_svg(item['label'] or ' ', item['x'], item['y'], item['size'] * 1.6)


def _centroid(points: list) -> tuple:
    return (sum(point[0] for point in points) / len(points),
            sum(point[1] for point in points) / len(points))


def _shape_svg(item: dict) -> str:
    colours = AFFILIATIONS[item['side']]
    path = ' '.join(f'{round(x, 2)},{round(y, 2)}' for x, y in item['points'])
    width = round(4 * item['size'], 2)
    dash = f' stroke-dasharray="{round(14 * item["size"], 1)} {round(9 * item["size"], 1)}"' \
        if item['style'] == 'dashed' else ''
    if item['kind'] == 'area':
        shape = (
            f'<polygon points="{path}" fill="{colours["fill"]}" fill-opacity="0.3" '
            f'stroke="{colours["stroke"]}" stroke-width="{width}"{dash} '
            f'stroke-linejoin="round"/>'
        )
        centre = _centroid(item['points'])
        return shape + _label_svg(item['label'], centre[0], centre[1], item['size'])
    marker = f' marker-end="url(#tma-{item["side"]})"' if item.get('arrow') else ''
    shape = (
        f'<polyline points="{path}" fill="none" stroke="{colours["stroke"]}" '
        f'stroke-width="{width}"{dash} stroke-linecap="round" '
        f'stroke-linejoin="round"{marker}/>'
    )
    if not item['label']:
        return shape
    middle = item['points'][len(item['points']) // 2]
    return shape + _label_svg(item['label'], middle[0], middle[1] - 10 * item['size'],
                              item['size'])


def tile_url(background: dict, zoom: int, column: int, row: int) -> str:
    """One tile of the pyramid. Row counts from the top — see TILE_BLEED above."""
    return f"{background['url']}/{zoom}/{column}/{row}.png"


def _empty_sheet(doc: dict) -> str:
    return (f'<rect x="0" y="0" width="{doc["width"]}" height="{doc["height"]}" '
            f'fill="#20262e"/>')


def _tiles_svg(doc: dict) -> str:
    background = doc['background']
    zoom = background['zoom']
    per_side = 2 ** zoom
    width = doc['width'] / per_side
    height = doc['height'] / per_side
    bleed_x = doc['width'] * TILE_BLEED
    bleed_y = doc['height'] * TILE_BLEED
    tiles = []
    for column in range(per_side):
        for row in range(per_side):
            tiles.append(
                f'<image href={quoteattr(tile_url(background, zoom, column, row))} '
                f'x="{round(column * width, 3)}" y="{round(row * height, 3)}" '
                f'width="{round(width + bleed_x, 3)}" '
                f'height="{round(height + bleed_y, 3)}" '
                f'preserveAspectRatio="none"/>'
            )
    # The dark sheet stays underneath: a tile that 404s draws nothing at all in
    # SVG, and a hole in the terrain should read as terrain we have not got.
    return (f'{_empty_sheet(doc)}<g opacity="{background["opacity"]}">'
            f'{"".join(tiles)}</g>')


def _background_svg(doc: dict) -> str:
    background = doc['background']
    if not background['url']:
        return _empty_sheet(doc)
    if background['kind'] == 'tiles':
        return _tiles_svg(doc)
    # preserveAspectRatio="none" because the document's own proportions are the
    # authority: the person sized the sheet to the image, not the other way round.
    return (
        f'<image href={quoteattr(background["url"])} x="0" y="0" '
        f'width="{doc["width"]}" height="{doc["height"]}" '
        f'opacity="{background["opacity"]}" preserveAspectRatio="none"/>'
    )


def _grid_svg(doc: dict) -> str:
    grid = doc['grid']
    if not grid['show']:
        return ''
    lines = []
    for column in range(1, grid['cols']):
        x = round(doc['width'] * column / grid['cols'], 2)
        lines.append(f'<line x1="{x}" y1="0" x2="{x}" y2="{doc["height"]}"/>')
    for row in range(1, grid['rows']):
        y = round(doc['height'] * row / grid['rows'], 2)
        lines.append(f'<line x1="0" y1="{y}" x2="{doc["width"]}" y2="{y}"/>')
    return (f'<g stroke="#ffffff" stroke-opacity="0.28" stroke-width="1.5">'
            f'{"".join(lines)}</g>')


def render(doc: dict, *, standalone: bool = False, extra_class: str = '') -> str:
    """The whole map as one `<svg>` element.

    This is what the read-only page, the share link and the no-JavaScript
    fallback show — the editor's canvas is the same markup, kept up to date in
    the browser instead of re-rendered here.
    """
    classes = ('tacmap ' + extra_class).strip()
    namespace = ' xmlns="http://www.w3.org/2000/svg"' if standalone else ''
    body = ''.join(item_svg(item) for item in doc.get('items') or [])
    # The sheet and the plan are separate groups so the editor can redraw either
    # on its own — changing the background must not touch what is drawn on it.
    return (
        f'<svg{namespace} viewBox="0 0 {doc["width"]} {doc["height"]}" '
        f'class={quoteattr(classes)} preserveAspectRatio="xMidYMid meet">'
        f'{defs()}<g id="tm-back">{_background_svg(doc)}{_grid_svg(doc)}</g>'
        f'<g class="tm-items">{body}</g></svg>'
    )


# ---------------------------------------------------------------------------
# Putting the plan into a running mission
# ---------------------------------------------------------------------------


def ocap_settings(payload: dict, base_url: str) -> dict:
    """An OCAP `map.json` turned into a background and an Arma extent.

    OCAP already renders every terrain its users play on, and its `map.json`
    carries the one number the export could not work out for itself:
    `worldSize`, the terrain's edge in metres. So pointing at an OCAP map
    folder settles the background **and** the calibration in one step — which
    is the whole reason this import exists rather than a tile-URL field.

        {"name": "Cham", "worldName": "tem_cham", "worldSize": 8192,
         "imageSize": 16384, "multiplier": 2, "maxZoom": 6, ...}

    `imageSize` is the pyramid's edge in pixels; the bottom level therefore
    holds `imageSize / 256` tiles per side, and `maxZoom` is its level. Both are
    read, and the smaller is believed: asking for a level the folder does not
    have is a screenful of missing tiles.
    """
    world = _number(payload.get('worldSize')) if isinstance(payload, dict) else None
    if not world or world <= 0:
        raise ValueError(
            "That map.json has no usable worldSize — it may not be an OCAP map folder."
        )

    image = _number(payload.get('imageSize'), 0) or 0
    from_image = int(math.log2(image / TILE_SIZE)) if image >= TILE_SIZE else MAX_TILE_ZOOM
    stated = _number(payload.get('maxZoom'))
    max_zoom = min(int(stated) if stated and stated > 0 else from_image, from_image)

    name = (_text(payload.get('name'), 80) or _text(payload.get('worldName'), 80))
    return {
        'background': {
            'kind': 'tiles',
            'url': base_url.rstrip('/'),
            'opacity': 1.0,
            'zoom': min(DEFAULT_TILE_ZOOM, max_zoom),
            'max_zoom': max_zoom,
            'name': name,
        },
        # The pyramid covers the whole terrain, so the sheet's corners are the
        # world's corners — which is exactly what the Arma export needs.
        'arma': {'terrain': name, 'left': 0.0, 'bottom': 0.0,
                 'right': round(world, 2), 'top': round(world, 2)},
        'name': name,
    }


def to_world(doc: dict, x: float, y: float) -> tuple:
    """A point on the sheet, in Arma's world metres.

    The sheet's y grows downward and Arma's grows north, so the vertical axis
    is flipped here. Everything else is a straight stretch between the corners
    the person gave, which is what makes a cropped map work as well as a whole
    one.
    """
    arma = doc.get('arma') or DEFAULT_EXTENT
    world_x = arma['left'] + (x / doc['width']) * (arma['right'] - arma['left'])
    world_y = arma['top'] - (y / doc['height']) * (arma['top'] - arma['bottom'])
    return (round(world_x, 2), round(world_y, 2))


def _sqf_string(value: str) -> str:
    """SQF quotes a quote by doubling it, and has no escape for a newline."""
    flat = ' '.join((value or '').split())
    return '"' + flat.replace('"', '""') + '"'


def _arma_type(item: dict) -> str:
    side = ARMA_SIDES.get(item['side'], ARMA_SIDES['unknown'])[0]
    if item['kind'] == 'point':
        return ARMA_POINTS.get(item.get('glyph', ''), ARMA_POINT_DEFAULT)
    if item['kind'] == 'text':
        return 'Empty'
    # A headquarters is the one modifier Arma has a marker of its own for, and
    # it says more about the unit than its branch does.
    if item.get('hq'):
        return f'{side}_hq'
    return f"{side}_{ARMA_TYPES.get(item.get('symbol'), 'unknown')}"


def _marker_text(item: dict) -> str:
    if item['kind'] == 'point' and item.get('glyph'):
        parts = [item['glyph']]
        if item['label']:
            parts.append(item['label'])
        return ' '.join(parts)
    return item['label']


def _bearing(start: tuple, end: tuple) -> float:
    """Compass degrees from one world point to the next — 0 is north."""
    return round(math.degrees(math.atan2(end[0] - start[0], end[1] - start[1])) % 360, 1)


def to_sqf(doc: dict, *, prefix: str, title: str = '') -> str:
    """The map as a script that puts these markers into a running mission.

    A script somebody pastes, rather than something this bot sends: vanilla
    Arma has no way to fetch anything from outside, so the way in without a mod
    is the debug console, where a logged-in admin can run it globally.
    `createMarker` is global by nature, so every player sees the result.

    Running it a second time **replaces** these markers instead of doubling
    them — every marker is named after this map, and the script deletes that
    set before it draws. Which is also why the prefix has to be this map's
    alone: a prefix two maps shared would have them deleting each other.
    """
    prefix = ''.join(character for character in (prefix or 'tacmap')
                     if character.isalnum() or character == '_')
    prefix = (prefix or 'tacmap') + '_'

    items = doc.get('items') or []
    lines = [
        f'// {title or "Tactical map"} — {summarise(doc)}',
        '// Paste into the Arma 3 debug console and press GLOBAL EXEC as a',
        '// logged-in admin. Running it again replaces these markers.',
        f'private _p = {_sqf_string(prefix)};',
        '{ if (_x select [0, count _p] == _p) then { deleteMarker _x } } '
        'forEach allMapMarkers;',
        'private _m = "";',
    ]

    for index, item in enumerate(items, start=1):
        colour = ARMA_SIDES.get(item['side'], ARMA_SIDES['unknown'])[1]
        name = f'_p + "{index}"'
        text = _marker_text(item)

        if item['kind'] in ('line', 'area'):
            points = [to_world(doc, x, y) for x, y in item['points']]
            if item['kind'] == 'area':
                points.append(points[0])       # Arma has no closed polyline
            flat = ', '.join(f'{point[0]}, {point[1]}' for point in points)
            lines.append(f'_m = createMarker [{name}, '
                         f'[{points[0][0]}, {points[0][1]}]];')
            lines.append('_m setMarkerShape "POLYLINE";')
            lines.append(f'_m setMarkerColor "{colour}";')
            lines.append(f'_m setMarkerPolyline [{flat}];')
            if text:
                lines.append(f'_m setMarkerText {_sqf_string(text)};')
            if item['kind'] == 'line' and item.get('arrow') and len(points) > 1:
                lines.append(f'_m = createMarker [_p + "{index}a", '
                             f'[{points[-1][0]}, {points[-1][1]}]];')
                lines.append('_m setMarkerType "mil_arrow";')
                lines.append(f'_m setMarkerColor "{colour}";')
                lines.append(f'_m setMarkerDir {_bearing(points[-2], points[-1])};')
            continue

        world = to_world(doc, item['x'], item['y'])
        lines.append(f'_m = createMarker [{name}, [{world[0]}, {world[1]}]];')
        lines.append(f'_m setMarkerType "{_arma_type(item)}";')
        lines.append(f'_m setMarkerColor "{colour}";')
        if text:
            lines.append(f'_m setMarkerText {_sqf_string(text)};')
        if item['size'] != 1:
            size = round(item['size'], 2)
            lines.append(f'_m setMarkerSize [{size}, {size}];')
        if item['kind'] == 'unit' and item.get('rotation'):
            lines.append(f"_m setMarkerDir {round(item['rotation'] % 360, 1)};")

    if not items:
        lines.append('// Nothing is drawn on this map yet.')
    return '\n'.join(lines) + '\n'

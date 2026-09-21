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

import html as _html
import json
import math
import re
from urllib.parse import unquote
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
# Named layers, each switchable on its own, so one map holds phase 1, phase 2
# and the enemy picture instead of being copied three times.
MAX_LAYERS = 12
MAX_LAYER_NAME = 40
DEFAULT_LAYER = {'id': 'plan', 'name': 'Plan', 'visible': True}
MIN_SIZE = 0.15
MAX_SIZE = 4.0

# The box a size-1 symbol occupies, in document units. Small on purpose: a
# platoon plan puts twenty of these on one sheet, and a symbol that reads as
# comfortable with three on screen is a wall of colour with twenty.
UNIT_BOX = 40
POINT_BOX = 26

# However small a symbol is dragged, its name has to stay readable — the label
# is what the plan is for.
MIN_LABEL = 9

KINDS = ('unit', 'point', 'line', 'area', 'text')

# What gets drawn over what. A symbol under an area was the one thing people
# lost on a busy sheet, so the order is fixed by kind rather than left to
# whatever was added last; `sort_order` only breaks ties within a kind.
KIND_ORDER = {'area': 0, 'line': 1, 'text': 2, 'point': 3, 'unit': 4}

# APP-6, drawn the way the planning tools and the pocket cards this hobby
# already uses draw it: a pale frame with the pictogram in dark line work on
# it. The frame says whose a unit is before the colour does — a rectangle is
# friendly, a diamond hostile, a square neutral, a quatrefoil unknown — which
# is what keeps a plan readable when it is printed, projected or looked at by
# somebody who is colour-blind.
#
# `fill` is the frame, `edge` its outline, `glyph` the pictogram and every
# modifier drawn around it.
AFFILIATIONS = {
    'friend': {'label': 'Friendly', 'fill': '#8cbde2', 'edge': '#16344c',
               'glyph': '#0d2438'},
    'hostile': {'label': 'Hostile', 'fill': '#dd8b8b', 'edge': '#4a1616',
                'glyph': '#3a1010'},
    'neutral': {'label': 'Neutral', 'fill': '#8fd3a1', 'edge': '#14401f',
                'glyph': '#0e3018'},
    'civ': {'label': 'Civilian', 'fill': '#c9a6e0', 'edge': '#3a1c4d',
            'glyph': '#2c1339'},
    'unknown': {'label': 'Unknown', 'fill': '#f0dc8a', 'edge': '#4a3d0d',
                'glyph': '#3a2f08'},
}
DEFAULT_SIDE = 'friend'

# What dimension an object is in. APP-6 says it with the frame itself: "A
# closed frame is used to denote the Land and Sea Surface Dimensions, a frame
# open at the bottom to denote the Air and Space Dimensions and a frame open at
# the top to denote the Sea Subsurface Dimension." So an air symbol is not a
# different icon, it is the same icon in a frame that is missing its floor.
DIMENSIONS = {
    'land': {'label': 'Land / sea surface'},
    'air': {'label': 'Air'},
    'sub': {'label': 'Subsurface'},
}
DEFAULT_DIMENSION = 'land'

# Present, or planned. The other thing APP-6 says with the frame rather than
# with an icon: a solid line is what is there now, a dashed one what is
# anticipated or planned. On a planning map that is the distinction the whole
# sheet turns on, so it rides on the frame here exactly as it does on paper.
STATUSES = {
    'present': {'label': 'Present', 'dash': ''},
    'planned': {'label': 'Planned / anticipated', 'dash': '13 9'},
}
DEFAULT_STATUS = 'present'

# The frames, drawn in a 100 x 100 box centred on (50, 50). They carry no
# paint of their own so the `<use>` that places one decides the colours.
#
# One record per side *and* dimension, because everything hung off a frame is
# measured from the frame's own edges: `top` is where the echelon marks go,
# `bottom` is where a mobility indicator hangs and how far the label drops, and
# `staff` is where a headquarters staff comes off. A diamond reaches half the
# box higher than a rectangle and an air frame has no floor at all, so none of
# the three can be a constant.
_FRAMES = {
    'friend': {
        'land': {'path': '<path d="M10,30 H90 V70 H10 Z"/>',
                 'top': 30, 'bottom': 70, 'staff': (10, 70)},
        # A dome: the sides run up and the top closes over them, with nothing
        # along the bottom. The arc is r=40 about the centre, so at the top of
        # the icon box (y 33) it is still wider than the icon.
        'air': {'path': '<path d="M10,72 V50 A40,40 0 0 1 90,50 V72"/>',
                'top': 10, 'bottom': 72, 'staff': (10, 72)},
        'sub': {'path': '<path d="M10,28 V50 A40,40 0 0 0 90,50 V28"/>',
                'top': 28, 'bottom': 90, 'staff': (10, 28)},
    },
    # Wider than the frame it circumscribes: a diamond is narrowest exactly
    # where the pictogram is tallest, so a diamond sized like the rectangle
    # clips the X off an infantry symbol.
    'hostile': {
        'land': {'path': '<path d="M50,0 L100,50 L50,100 L0,50 Z"/>',
                 'top': 0, 'bottom': 100, 'staff': (25, 75)},
        # Not half a diamond: the pictogram box runs y 33-67 and half a
        # diamond stops at y 50, so the icon hung out of its own frame. A
        # roof on short walls is what APP-6 draws and it encloses the box.
        'air': {'path': '<path d="M2,72 V50 L50,8 L98,50 V72"/>',
                'top': 8, 'bottom': 72, 'staff': (2, 72)},
        'sub': {'path': '<path d="M2,28 V50 L50,92 L98,50 V28"/>',
                'top': 28, 'bottom': 92, 'staff': (2, 28)},
    },
    'neutral': {
        'land': {'path': '<path d="M14,14 H86 V86 H14 Z"/>',
                 'top': 14, 'bottom': 86, 'staff': (14, 86)},
        'air': {'path': '<path d="M14,86 V14 H86 V86"/>',
                'top': 14, 'bottom': 86, 'staff': (14, 86)},
        'sub': {'path': '<path d="M14,14 V86 H86 V14"/>',
                'top': 14, 'bottom': 86, 'staff': (14, 14)},
    },
    'civ': {
        'land': {'path': '<path d="M10,30 H90 V70 H10 Z"/>',
                 'top': 30, 'bottom': 70, 'staff': (10, 70)},
        'air': {'path': '<path d="M10,72 V50 A40,40 0 0 1 90,50 V72"/>',
                'top': 10, 'bottom': 72, 'staff': (10, 72)},
        'sub': {'path': '<path d="M10,28 V50 A40,40 0 0 0 90,50 V28"/>',
                'top': 28, 'bottom': 90, 'staff': (10, 28)},
    },
    # A quatrefoil: four half-circles bulging out of a square. Its air and
    # subsurface forms are the same lobes with one side of them left off.
    'unknown': {
        'land': {'path': ('<path d="M26,26 A22,22 0 0 1 74,26 A22,22 0 0 1 74,74 '
                          'A22,22 0 0 1 26,74 A22,22 0 0 1 26,26 Z"/>'),
                 'top': 15, 'bottom': 85, 'staff': (21, 79)},
        'air': {'path': ('<path d="M26,74 A22,22 0 0 1 26,26 A22,22 0 0 1 74,26 '
                         'A22,22 0 0 1 74,74"/>'),
                'top': 15, 'bottom': 74, 'staff': (26, 74)},
        'sub': {'path': ('<path d="M74,26 A22,22 0 0 1 74,74 A22,22 0 0 1 26,74 '
                         'A22,22 0 0 1 26,26"/>'),
                'top': 26, 'bottom': 85, 'staff': (26, 26)},
    },
}


def frame_of(side: str, dimension: str = DEFAULT_DIMENSION) -> dict:
    """The frame record for one side in one dimension, never missing."""
    shapes = _FRAMES.get(side) or _FRAMES[DEFAULT_SIDE]
    return shapes.get(dimension) or shapes[DEFAULT_DIMENSION]


def item_frame(item: dict) -> dict:
    """The frame the item is drawn in."""
    return frame_of(item.get('side', DEFAULT_SIDE),
                    item.get('dimension', DEFAULT_DIMENSION))

# The size marks that sit above the frame. A symbol without one is a unit of
# unsaid size, which is what most things on a plan are — so this is opt-in and
# empty by default.
ECHELONS = {
    'team': {'label': 'Team / crew', 'short': 'Tm',
             'icon': '<circle cx="50" cy="50" r="9" fill="none"/>'
                     '<path d="M42,58 L58,42"/>'},
    'squad': {'label': 'Squad', 'short': 'Sqd',
              'icon': '<circle cx="50" cy="50" r="7" fill="currentColor" '
                      'stroke="none"/>'},
    'section': {'label': 'Section', 'short': 'Sect',
                'icon': '<circle cx="38" cy="50" r="7" fill="currentColor" '
                        'stroke="none"/>'
                        '<circle cx="62" cy="50" r="7" fill="currentColor" '
                        'stroke="none"/>'},
    'platoon': {'label': 'Platoon', 'short': 'Plt',
                'icon': '<circle cx="28" cy="50" r="7" fill="currentColor" '
                        'stroke="none"/>'
                        '<circle cx="50" cy="50" r="7" fill="currentColor" '
                        'stroke="none"/>'
                        '<circle cx="72" cy="50" r="7" fill="currentColor" '
                        'stroke="none"/>'},
    'company': {'label': 'Company', 'short': 'Coy',
                'icon': '<path d="M50,36 V64"/>'},
    'battalion': {'label': 'Battalion', 'short': 'Bn',
                  'icon': '<path d="M40,36 V64 M60,36 V64"/>'},
    'regiment': {'label': 'Regiment', 'short': 'Regt',
                 'icon': '<path d="M30,36 V64 M50,36 V64 M70,36 V64"/>'},
    'brigade': {'label': 'Brigade', 'short': 'Bde',
                'icon': '<path d="M38,36 L62,64 M62,36 L38,64"/>'},
    'division': {'label': 'Division', 'short': 'Div',
                 'icon': '<path d="M18,36 L42,64 M42,36 L18,64'
                         ' M58,36 L82,64 M82,36 L58,64"/>'},
}

# Reinforced and reduced, written beside the echelon exactly as the cards do.
STRENGTHS = {
    'reinforced': {'label': 'Reinforced (+)', 'text': '(+)'},
    'reduced': {'label': 'Reduced (-)', 'text': '(-)'},
    'both': {'label': 'Reinforced and reduced (\u00b1)', 'text': '(\u00b1)'},
}

# APP-6 field R, the mobility indicator: what the thing moves on, drawn as a
# short chassis hung under the frame. Arma tells a towed gun from a
# self-propelled one by which vehicle is parked next to it and a plan cannot,
# so this is the one amplifier that changes what a battery on the sheet means.
#
# Each icon is drawn in the 100-box on a baseline of y=0 and is moved under
# whatever the frame's own bottom happens to be, so it hangs off a diamond's
# tip and off a rectangle's edge alike.
MOBILITY = {
    'wheeled': {'label': 'Wheeled', 'short': 'whl',
                'icon': '<path d="M30,0 H70"/>'
                        '<circle cx="36" cy="8" r="7" fill="none"/>'
                        '<circle cx="64" cy="8" r="7" fill="none"/>'},
    'crosscountry': {'label': 'Wheeled, cross-country', 'short': 'x-c',
                     'icon': '<path d="M28,0 H72"/>'
                             '<circle cx="34" cy="8" r="7" fill="none"/>'
                             '<circle cx="50" cy="8" r="7" fill="none"/>'
                             '<circle cx="66" cy="8" r="7" fill="none"/>'},
    'tracked': {'label': 'Tracked', 'short': 'trk',
                'icon': '<rect x="28" y="0" width="44" height="16" rx="8" '
                        'fill="none"/>'},
    # A tow bar with a tongue on it, not two bars: two lines this close
    # merge into a capsule once each is drawn twice for contrast.
    'towed': {'label': 'Towed', 'short': 'twd',
              'icon': '<path d="M12,15 L26,1 H74"/>'
                      '<circle cx="36" cy="9" r="7" fill="none"/>'
                      '<circle cx="66" cy="9" r="7" fill="none"/>'},
    'amphib': {'label': 'Amphibious', 'short': 'amph',
               'icon': '<path d="M26,3 Q37,-7 48,3 Q59,13 70,3" fill="none"/>'
                       '<path d="M26,15 Q37,5 48,15 Q59,25 70,15" fill="none"/>'},
}

# How far under the frame's own bottom the mobility chassis hangs, and how
# much room it then takes up. The drop clears the frame's own stroke, which
# is 5 wide and drawn centred on the path.
MOBILITY_DROP = 13
MOBILITY_DEPTH = 26

# The icon sits in x 25-75, y 33-67, which is the largest box that fits inside
# every frame — the diamond is narrowest exactly where the icon is tallest.
#
# An icon inherits `stroke` and `fill="none"` from the `<use>` that places it;
# a shape that is meant to be solid says `fill="currentColor"` itself, and the
# `<use>` sets `color` to the same colour as the stroke.
#
# The unit symbols are the left-hand column of every pocket card (infantry,
# recon, armour, mechanised, mortars, artillery, medical, engineers, supply);
# the equipment ones are the right-hand column, drawn the same way so a
# weapons det or a single vehicle can go on the plan as itself.
SYMBOLS = {
    'generic': {'label': 'Unspecified', 'group': 'Infantry', 'icon': ''},
    'inf': {'label': 'Infantry', 'group': 'Infantry',
            'icon': '<path d="M27,34 L73,66 M73,34 L27,66"/>'},
    'mech': {'label': 'Mechanised infantry', 'group': 'Infantry',
             'icon': '<ellipse cx="50" cy="50" rx="25" ry="16"/>'
                     '<path d="M29,36 L71,64 M71,36 L29,64"/>'},
    'motor': {'label': 'Motorised infantry', 'group': 'Infantry',
              'icon': '<path d="M27,34 L73,66 M73,34 L27,66"/>'
                      '<circle cx="50" cy="50" r="8" fill="currentColor"/>'},
    'recon': {'label': 'Reconnaissance', 'group': 'Infantry',
              'icon': '<path d="M27,66 L73,34"/>'},
    'sniper': {'label': 'Sniper / marksman', 'group': 'Infantry',
               'icon': '<circle cx="50" cy="50" r="12"/>'
                       '<path d="M50,31 V69 M31,50 H69"/>'},
    'armor': {'label': 'Armour', 'group': 'Vehicles',
              'icon': '<ellipse cx="50" cy="50" rx="25" ry="16"/>'},
    'veh': {'label': 'Wheeled vehicle', 'group': 'Vehicles',
            'icon': '<path d="M30,39 H70 V58 H30 Z"/>'
                    '<circle cx="39" cy="63" r="5" fill="currentColor"/>'
                    '<circle cx="61" cy="63" r="5" fill="currentColor"/>'},
    'truck': {'label': 'Truck', 'group': 'Equipment',
              'icon': '<path d="M30,38 V52 A20,20 0 0 0 70,52 V38"/>'
                      '<circle cx="34" cy="64" r="6"/><circle cx="66" cy="64" r="6"/>'},
    'apc': {'label': 'APC', 'group': 'Equipment',
            'icon': '<path d="M28,66 V44 L50,33 L72,44 V66"/>'},
    'ifv': {'label': 'IFV', 'group': 'Equipment',
            'icon': '<path d="M28,66 V44 L50,33 L72,44 V66"/>'
                    '<path d="M36,42 L64,60 M64,42 L36,60"/>'},
    'tank': {'label': 'Tank', 'group': 'Equipment',
             'icon': '<path d="M30,33 V67 M70,33 V67 M30,50 H70"/>'},
    'mg': {'label': 'Machine gun', 'group': 'Weapons',
           'icon': '<path d="M50,67 V36 M40,45 L50,34 L60,45" fill="none"/>'
                   '<path d="M40,58 H60"/>'},
    'hmg': {'label': 'Heavy machine gun', 'group': 'Weapons',
            'icon': '<path d="M50,67 V36 M40,45 L50,34 L60,45" fill="none"/>'
                    '<path d="M40,58 H60 M40,50 H60"/>'},
    'gl': {'label': 'Grenade launcher', 'group': 'Weapons',
           'icon': '<path d="M50,67 V44"/><circle cx="50" cy="38" r="6"/>'
                   '<path d="M40,55 H60"/>'},
    'at': {'label': 'Anti-tank', 'group': 'Weapons',
           'icon': '<path d="M30,52 L50,33 L70,52 M30,67 L50,48 L70,67"/>'},
    'aa': {'label': 'Air defence', 'group': 'Weapons',
           'icon': '<path d="M27,64 A26,26 0 0 1 73,64"/>'},
    'arty': {'label': 'Artillery', 'group': 'Weapons',
             'icon': '<circle cx="50" cy="50" r="11" fill="currentColor"/>'},
    'mortar': {'label': 'Mortar', 'group': 'Weapons',
               'icon': '<path d="M50,67 V44"/><circle cx="50" cy="38" r="6"/>'},
    'air': {'label': 'Fixed wing', 'group': 'Air',
            'icon': '<path d="M26,38 L50,50 L74,38 L74,62 L50,50 L26,62 Z"/>'},
    'heli': {'label': 'Rotary wing', 'group': 'Air',
             'icon': '<path d="M26,42 L50,52 L74,42 L74,64 L50,54 L26,64 Z"/>'
                     '<path d="M26,34 H74"/>'},
    'uav': {'label': 'UAV', 'group': 'Air',
            'icon': '<path d="M26,42 L50,52 L74,42 L74,64 L50,54 L26,64 Z"/>'
                    '<circle cx="50" cy="34" r="5" fill="currentColor"/>'},
    'naval': {'label': 'Naval', 'group': 'Air',
              'icon': '<circle cx="50" cy="35" r="6"/>'
                      '<path d="M50,41 V67 M38,46 H62"/>'
                      '<path d="M30,55 A20,20 0 0 0 70,55"/>'},
    'med': {'label': 'Medical', 'group': 'Support',
            'icon': '<path d="M50,34 V66 M31,50 H69"/>'},
    'engr': {'label': 'Engineers', 'group': 'Support',
             'icon': '<path d="M28,66 V44 H72 V66"/>'},
    'maint': {'label': 'Maintenance', 'group': 'Support',
              'icon': '<path d="M36,34 A12,12 0 1 0 50,52 L66,68"/>'
                      '<path d="M28,42 L40,42"/>'},
    'logi': {'label': 'Supply / service', 'group': 'Support',
             'icon': '<path d="M30,37 H70 V63 H30 Z M30,63 L70,37"/>'},
    'support': {'label': 'Combat support', 'group': 'Support',
                'icon': '<path d="M42,33 A22,22 0 0 0 42,67"/>'
                        '<path d="M58,33 A22,22 0 0 1 58,67"/>'
                        '<circle cx="50" cy="50" r="4" fill="currentColor"/>'},
    'installation': {'label': 'Installation', 'group': 'Support',
                     'icon': '<path d="M34,33 V67"/>'
                             '<path d="M34,35 H68 V51 H34 Z" fill="currentColor"/>'},
    'signal': {'label': 'Signals', 'group': 'Support',
               'icon': '<path d="M30,66 L50,34 L70,66 M38,54 H62"/>'},
    'unknown': {'label': 'Unknown', 'group': 'Support',
                'icon': '<path d="M38,43 A12,12 0 0 1 62,43 C62,53 50,53 50,60"/>'
                        '<circle cx="50" cy="67" r="4" fill="currentColor"/>'},
}
DEFAULT_SYMBOL = 'inf'

# The staff of a headquarters, hanging off the frame. It is a modifier rather
# than its own symbol because any unit can be the one in charge — an HQ that
# is also a medical company is a medical icon on a staff. Where it hangs from
# differs per frame, which is what `staff` in `_FRAMES` says.
def _hq_staff(side: str, dimension: str = DEFAULT_DIMENSION) -> str:
    x, y = frame_of(side, dimension)['staff']
    return f'<path d="M{x},{y} V{y + 46}" fill="none" stroke-linecap="square"/>'


# How far above the frame the echelon marks sit, and how much of the 100-box
# the in-frame abbreviation may take.
ECHELON_LIFT = 18
MAX_MOD = 5

# The markers Arma ships beside the NATO icons — `mil_dot`, `mil_objective`,
# `mil_destroy` and the rest. They are drawn here the way the game draws them:
# line art in the marker's colour rather than a filled block, which is what
# tells a task marker from a unit at a glance. `arma` is the marker's name in
# the game, so the export is a lookup rather than a guess about what somebody
# meant by typing "TGT".
#
# `text` says whether the marker has room for the glyph inside it; where it
# does not, the glyph is written in front of the label instead of being lost.
MARKERS = {
    'dot': {'label': 'Dot', 'arma': 'mil_dot', 'text': True,
            'icon': '<circle cx="50" cy="50" r="16" fill="currentColor"/>'},
    'circle': {'label': 'Circle', 'arma': 'mil_circle', 'text': True,
               'icon': '<circle cx="50" cy="50" r="24"/>'},
    'box': {'label': 'Box', 'arma': 'mil_box', 'text': True,
            'icon': '<path d="M26,26 H74 V74 H26 Z"/>'},
    'triangle': {'label': 'Triangle', 'arma': 'mil_triangle', 'text': True,
                 'icon': '<path d="M50,24 L76,70 H24 Z"/>'},
    'objective': {'label': 'Objective', 'arma': 'mil_objective', 'text': False,
                  'icon': '<circle cx="50" cy="56" r="22"/>'
                          '<path d="M50,34 V12"/>'},
    'destroy': {'label': 'Destroy', 'arma': 'mil_destroy', 'text': False,
                'icon': '<path d="M26,26 L74,74 M74,26 L26,74"/>'},
    'warning': {'label': 'Warning', 'arma': 'mil_warning', 'text': False,
                'icon': '<path d="M50,22 L78,72 H22 Z"/>'
                        '<path d="M50,40 V56"/>'
                        '<circle cx="50" cy="64" r="3.5" fill="currentColor"/>'},
    'unknown': {'label': 'Question', 'arma': 'mil_unknown', 'text': False,
                'icon': '<path d="M34,40 A16,16 0 0 1 66,40 C66,54 50,54 50,64"/>'
                        '<circle cx="50" cy="75" r="4.5" fill="currentColor"/>'},
    'flag': {'label': 'Flag', 'arma': 'mil_flag', 'text': False,
             'icon': '<path d="M30,20 V80"/>'
                     '<path d="M30,22 H72 L62,38 L72,54 H30 Z" fill="currentColor"/>'},
    'arrow': {'label': 'Arrow', 'arma': 'mil_arrow', 'text': False,
              'icon': '<path d="M50,20 L74,72 L50,60 L26,72 Z" fill="currentColor"/>'},
    'start': {'label': 'Start', 'arma': 'mil_start', 'text': False,
              'icon': '<path d="M28,24 L76,50 L28,76 Z" fill="currentColor"/>'},
    'end': {'label': 'End', 'arma': 'mil_end', 'text': False,
            'icon': '<path d="M28,28 H72 V72 H28 Z" fill="currentColor"/>'},
    'pickup': {'label': 'Pick-up / LZ', 'arma': 'mil_pickup', 'text': False,
               'icon': '<circle cx="50" cy="50" r="24"/>'
                       '<path d="M34,54 L50,36 L66,54 M50,36 V68"/>'},
    'join': {'label': 'Join', 'arma': 'mil_join', 'text': False,
             'icon': '<path d="M26,24 L50,50 L74,24 M50,50 V76"/>'},
    'marker': {'label': 'Cross', 'arma': 'mil_marker', 'text': False,
               'icon': '<path d="M50,24 V76 M24,50 H76"/>'},
}
DEFAULT_MARKER = 'dot'

LINE_STYLES = ('solid', 'dashed')

# What a line or an area can be painted, beyond its side's own colour.
#
# A symbol may not take one of these — whose a unit is has to stay readable
# from its colour — but a line is a route, a boundary, a phase line or a fire
# control measure, and those have been told apart by colour on every paper map
# there has ever been. One plan wants more than the five sides can say.
#
# Picked to hold up over terrain that is bright sand in one corner and dark
# jungle in the other, which rules out anything pale or muddy; the free colour
# field is still there for anything else.
LINE_COLOURS = (
    ('#e03b3b', 'Red'),
    ('#f07f2a', 'Orange'),
    ('#f2c832', 'Yellow'),
    ('#3fb950', 'Green'),
    ('#3d8ee8', 'Blue'),
    ('#35c9c2', 'Cyan'),
    ('#e060b8', 'Pink'),
    ('#a06ee8', 'Purple'),
    ('#f5f5f5', 'White'),
    ('#1b1f24', 'Black'),
)

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
# grid of bright hairlines drawn over the terrain. The fix is a coarse copy of
# the same terrain laid *under* the grid, so a hairline shows blurry terrain
# instead of the dark sheet.
#
# It used to be an overlap — each tile drawn slightly over its neighbour — and
# that was the wrong fix, because making a tile bigger than its cell stretches
# what is inside it. Measured against a ruler pyramid, a feature drifted from
# 0 at a tile's left edge to 2.35px at its right and then snapped back: a
# sawtooth at every boundary, which is what made roads jump. Everything in an
# SVG scales, so zooming in scaled that error up while the seam it was covering
# stayed one device pixel wide. Leaflet, which OCAP's own viewer uses, places
# tiles at exact positions and never stretches one; this does the same.
#
# Level 0 is one tile over the whole sheet, so the backdrop costs one request.
# Its blur does not matter: it is only ever seen through a hairline.
BACKDROP_ZOOM = 0

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
# Arma has four NATO marker prefixes and five marker colours we use, so the
# civilian side shares the `u_` icons and keeps its own colour.
ARMA_SIDES = {
    'friend': ('b', 'ColorWEST'),
    'hostile': ('o', 'ColorEAST'),
    'neutral': ('n', 'ColorGUER'),
    'civ': ('u', 'ColorCIV'),
    'unknown': ('u', 'ColorUNKNOWN'),
}

# Our symbols against the NATO markers vanilla Arma 3 ships. Every icon the
# game has is in the palette now, so this is a straight mapping; only the four
# symbols Arma has no marker for (sniper, machine gun, anti-tank, signals)
# land on the nearest thing that is in the game rather than on nothing.
ARMA_TYPES = {
    'generic': 'unknown', 'inf': 'inf', 'mech': 'mech_inf', 'motor': 'motor_inf',
    'armor': 'armor', 'recon': 'recon', 'sniper': 'recon', 'mg': 'inf',
    'at': 'support', 'aa': 'antiair', 'arty': 'art', 'mortar': 'mortar',
    'veh': 'motor_inf', 'air': 'plane', 'heli': 'air', 'uav': 'uav',
    'naval': 'naval', 'med': 'med', 'engr': 'maint', 'maint': 'maint',
    'logi': 'service', 'support': 'support', 'installation': 'installation',
    'signal': 'support', 'unknown': 'unknown',
}

# What a glyph means when the point was drawn before the marker shapes
# existed and so carries none: the same guesses as before, so an older map
# exports as it always did.
ARMA_POINTS = {
    'OBJ': 'objective', 'TGT': 'destroy', 'SP': 'start',
    'RP': 'end', 'IED': 'warning', 'CP': 'triangle',
    'LZ': 'pickup', 'PZ': 'pickup',
}

# Text drawn over a satellite image needs a halo or it disappears into the
# terrain; white on a dark outline is what every map tool ends up at.
_FONT = 'system-ui, -apple-system, Segoe UI, Roboto, sans-serif'
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
        'layers': [dict(DEFAULT_LAYER)],
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


_HEX_COLOUR = re.compile(r'^#[0-9a-fA-F]{6}$')
_SLUG = re.compile(r'[^a-z0-9_-]+')


def _colour(raw) -> str:
    """A colour somebody picked, or '' meaning "use the side's".

    Only `#rrggbb`: it goes into a `stroke` attribute, and a colour value is
    one of the places CSS lets a string be more than a colour.
    """
    value = (raw or '').strip() if isinstance(raw, str) else ''
    return value.lower() if _HEX_COLOUR.match(value) else ''


def _slug(raw) -> str:
    return _SLUG.sub('', (raw or '').strip().lower() if isinstance(raw, str) else '')[:24]


# A terrain this bot serves itself, from an uploaded archive. It is a path
# rather than an address so that moving the site — a new domain, a local run —
# does not leave every map pointing at the old one.
LOCAL_TILES = re.compile(r'^/t/\d+$')


def _safe_url(raw) -> str:
    """A background URL we are willing to put in an `href`.

    Only http(s) or one of our own terrain paths, because `javascript:` and
    `data:` in an image href are how an editable map becomes a way to run
    script in somebody else's session.
    """
    url = (raw or '').strip() if isinstance(raw, str) else ''
    if not url:
        return ''
    if LOCAL_TILES.match(url):
        return url
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

    doc['layers'] = _parse_layers(raw.get('layers'))

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
    # An item on a layer this document does not have would be invisible and
    # unreachable, so it lands on the first one rather than nowhere.
    layers = {layer['id'] for layer in doc['layers']}
    layer = raw.get('layer') if raw.get('layer') in layers else doc['layers'][0]['id']
    item = {
        'kind': kind,
        'side': side,
        'layer': layer,
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
        echelon = raw.get('echelon')
        item['echelon'] = echelon if echelon in ECHELONS else ''
        strength = raw.get('strength')
        item['strength'] = strength if strength in STRENGTHS else ''
        mobility = raw.get('mobility')
        item['mobility'] = mobility if mobility in MOBILITY else ''
        dimension = raw.get('dimension')
        item['dimension'] = dimension if dimension in DIMENSIONS else DEFAULT_DIMENSION
        item['text'] = _text(raw.get('text'), MAX_MOD)
        item['higher'] = _text(raw.get('higher'), MAX_MOD)
    elif kind == 'point':
        item['glyph'] = _text(raw.get('glyph'), MAX_GLYPH).upper()
        # A map drawn before the marker shapes existed carries none. Its
        # glyph is the only thing that says what the point was, so it is read
        # the same way the export used to read it — a point typed `OBJ`
        # becomes the objective marker rather than a nameless dot.
        marker = raw.get('marker')
        if marker not in MARKERS:
            marker = ARMA_POINTS.get(item['glyph'], DEFAULT_MARKER)
        item['marker'] = marker

    if kind in ('unit', 'point'):
        # Present or planned rides on the frame of a unit and on the shape of
        # a task alike; a line or an area already says it with `style`, so
        # giving those a status too would be two switches for one fact.
        status = raw.get('status')
        item['status'] = status if status in STATUSES else DEFAULT_STATUS

    if kind in ('line', 'area', 'text'):
        # The side says whose a symbol is and must keep saying it, but a line
        # is a route or a boundary — those are told apart by colour on every
        # paper map there has ever been.
        item['color'] = _colour(raw.get('color'))

    if kind in ('line', 'area'):
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


def _parse_layers(raw) -> list:
    """The document's layers, always at least one.

    A layer is only a name and a switch — items point at it by id. Nothing
    hangs off a layer, so a document that arrives without them, or with
    nonsense in them, gets the default one rather than an error: losing the
    plan because its layer list is broken would be the worse failure.
    """
    if not isinstance(raw, list):
        return [dict(DEFAULT_LAYER)]
    layers, seen = [], set()
    for entry in raw[:MAX_LAYERS]:
        if not isinstance(entry, dict):
            continue
        key = _slug(entry.get('id'))
        if not key or key in seen:
            continue
        seen.add(key)
        layers.append({
            'id': key,
            'name': _text(entry.get('name'), MAX_LAYER_NAME) or key,
            'visible': bool(entry.get('visible', True)),
        })
    return layers or [dict(DEFAULT_LAYER)]


def ordered_items(doc: dict, *, include_hidden: bool = False) -> list:
    """Every item that should be drawn, in the order it should be drawn.

    Layer by layer, and inside a layer by kind — areas, then lines, then
    labels, then markers, then symbols. Leaving the order to whatever was
    added last is what buried a platoon under a boundary somebody drew after
    it; within one kind the document's own order still decides, which is what
    **Bring to front** moves.
    """
    layers = doc.get('layers') or [dict(DEFAULT_LAYER)]
    rank = {layer['id']: index for index, layer in enumerate(layers)}
    hidden = {layer['id'] for layer in layers if not layer.get('visible', True)}
    items = [item for item in (doc.get('items') or [])
             if include_hidden or item.get('layer') not in hidden]
    return sorted(items, key=lambda item: (rank.get(item.get('layer'), 0),
                                           KIND_ORDER.get(item['kind'], 0)))


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
             'edge': value['edge'], 'glyph': value['glyph']}
            for key, value in AFFILIATIONS.items()
        ],
        'symbols': [
            {'key': key, 'label': value['label'], 'group': value['group'],
             'hasIcon': bool(value['icon'])}
            for key, value in SYMBOLS.items()
        ],
        'echelons': [
            {'key': key, 'label': value['label']}
            for key, value in ECHELONS.items()
        ],
        'strengths': [
            {'key': key, 'label': value['label'], 'text': value['text']}
            for key, value in STRENGTHS.items()
        ],
        'mobility': [
            {'key': key, 'label': value['label']}
            for key, value in MOBILITY.items()
        ],
        'dimensions': [
            {'key': key, 'label': value['label']}
            for key, value in DIMENSIONS.items()
        ],
        'statuses': [
            {'key': key, 'label': value['label'], 'dash': value['dash']}
            for key, value in STATUSES.items()
        ],
        'markers': [
            {'key': key, 'label': value['label'], 'text': value['text']}
            for key, value in MARKERS.items()
        ],
        'points': [{'glyph': glyph, 'label': label} for glyph, label in POINT_PRESETS],
        'terrains': [{'name': name, 'size': size} for name, size in ARMA_TERRAINS],
        'lineStyles': list(LINE_STYLES),
        'lineColours': [{'value': value, 'label': label}
                        for value, label in LINE_COLOURS],
        'unitBox': UNIT_BOX,
        'pointBox': POINT_BOX,
        'backdropZoom': BACKDROP_ZOOM,
        'maxTileZoom': MAX_TILE_ZOOM,
        'limits': {
            'items': MAX_ITEMS, 'points': MAX_POINTS, 'label': MAX_LABEL,
            'note': MAX_NOTE, 'glyph': MAX_GLYPH, 'mod': MAX_MOD,
            'minSize': MIN_SIZE, 'maxSize': MAX_SIZE,
            'layers': MAX_LAYERS, 'layerName': MAX_LAYER_NAME,
        },
        'kindOrder': dict(KIND_ORDER),
        'minLabel': MIN_LABEL,
        'defaultMarker': DEFAULT_MARKER,
        'echelonLift': ECHELON_LIFT,
        'mobilityDrop': MOBILITY_DROP,
        'mobilityDepth': MOBILITY_DEPTH,
        'defaultDimension': DEFAULT_DIMENSION,
        'defaultStatus': DEFAULT_STATUS,
        # Keyed side → dimension, the same two steps the renderer takes, so
        # the browser reads a frame's geometry rather than assuming any of it.
        'frames': {
            side: {
                dimension: {'top': frame['top'], 'bottom': frame['bottom'],
                            'staff': list(frame['staff'])}
                for dimension, frame in shapes.items()
            }
            for side, shapes in _FRAMES.items()
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
    for side, shapes in _FRAMES.items():
        for dimension, frame in shapes.items():
            parts.append(f'<g id="tmf-{side}-{dimension}">{frame["path"]}</g>')
            parts.append(
                f'<g id="tmh-{side}-{dimension}">{_hq_staff(side, dimension)}</g>'
            )
    for key, echelon in ECHELONS.items():
        parts.append(f'<g id="tmx-{key}">{echelon["icon"]}</g>')
    for key, mobility in MOBILITY.items():
        parts.append(f'<g id="tmv-{key}">{mobility["icon"]}</g>')
    for key, symbol in SYMBOLS.items():
        parts.append(f'<g id="tmi-{key}">{symbol["icon"]}</g>')
    for key, marker in MARKERS.items():
        parts.append(f'<g id="tmm-{key}">{marker["icon"]}</g>')
    parts.append('</defs>')
    return ''.join(parts)


def label_size(size: float) -> float:
    """How big a label is drawn at that item size — never below MIN_LABEL.

    A symbol shrunk to nothing is still a symbol; its name shrunk to nothing is
    a smudge, and the name is what the plan is read for.
    """
    return round(max(MIN_LABEL, 14 * size), 1)


def _label_svg(text: str, x: float, y: float, size: float, anchor: str = 'middle',
               fill: str = '') -> str:
    if not text:
        return ''
    font = label_size(size)
    return (
        f'<text{_attrs(x=round(x, 2), y=round(y, 2), text_anchor=anchor)} '
        f'class="tm-label" font-size="{font}" '
        f'font-family="system-ui, -apple-system, Segoe UI, Roboto, sans-serif" '
        f'font-weight="600" fill="{fill or _LABEL_FILL}" stroke="{_LABEL_HALO}" '
        f'stroke-width="{round(font * 0.22, 2)}" paint-order="stroke" '
        f'stroke-linejoin="round">{escape(text)}</text>'
    )


def item_colour(item: dict) -> str:
    """What an item is drawn in: its own colour, or its side's."""
    return item.get('color') or AFFILIATIONS[item['side']]['fill']


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
    """One unit symbol: the frame, what is in it, and what is written round it."""
    colours = AFFILIATIONS[item['side']]
    side = item['side']
    dimension = item.get('dimension', DEFAULT_DIMENSION)
    frame = item_frame(item)
    scale = UNIT_BOX * item['size'] / 100
    # The symbol is drawn in its own 100 × 100 box and then moved onto the map,
    # so a rotation turns the symbol about its own centre rather than the sheet.
    transform = (f"translate({round(item['x'], 2)},{round(item['y'], 2)}) "
                 f"rotate({item.get('rotation', 0)}) scale({round(scale, 4)}) "
                 f"translate(-50,-50)")
    # An air or subsurface frame has no floor, so the fill would leak out of
    # the open side; those paths carry `fill="none"` of their own and the
    # attribute here is what the closed ones use.
    dash = STATUSES[item.get('status') or DEFAULT_STATUS]['dash']
    parts = [
        f'<use href="#tmf-{side}-{dimension}" fill="{colours["fill"]}" '
        f'stroke="{colours["edge"]}" stroke-width="5"'
        f'{_attrs(stroke_dasharray=dash)}/>'
    ]
    if item.get('hq'):
        parts.append(f'<use href="#tmh-{side}-{dimension}" '
                     f'stroke="{colours["edge"]}" stroke-width="5"/>')
    if SYMBOLS[item['symbol']]['icon']:
        parts.append(
            f'<use href="#tmi-{item["symbol"]}" fill="none" '
            f'stroke="{colours["glyph"]}" color="{colours["glyph"]}" '
            f'stroke-width="7" stroke-linecap="round" stroke-linejoin="round"/>'
        )
    parts.extend(_modifier_svg(item, colours))
    # The label sits under everything the symbol hangs downwards: the frame
    # itself, the mobility chassis below it, and the staff of a headquarters.
    # The old fixed drop is kept as a floor so a symbol that grew none of
    # those sits exactly where it always did.
    depth = frame['bottom']
    if item.get('mobility') in MOBILITY:
        depth += MOBILITY_DROP + MOBILITY_DEPTH
    depth = max(depth, 135 if item.get('hq') else 112)
    drop = UNIT_BOX * item['size'] * (depth - 50) / 100 + 6
    label = _label_svg(item['label'], item['x'], item['y'] + drop, item['size'])
    return f'<g transform="{transform}">{"".join(parts)}</g>{label}'


def _modifier_svg(item: dict, colours: dict) -> list:
    """The echelon above the frame, the strength beside it, the text alongside.

    All three are drawn in the symbol's own 100 × 100 box, so they scale and
    rotate with it. Two things they must survive that a pocket card never has
    to: the echelon is lifted clear of whatever the frame's top edge happens
    to be — a diamond reaches much higher than a rectangle — and everything
    outside the frame is drawn twice, dark over a pale outline, because it
    sits on terrain rather than on paper and has to read over both.
    """
    parts = []
    frame = item_frame(item)
    top = frame['top']
    line = top - ECHELON_LIFT
    echelon = item.get('echelon')
    strength = item.get('strength')
    if echelon in ECHELONS:
        shift = f'translate(0,{round(line - 50, 2)})'
        for colour, width in ((colours['fill'], 15), (colours['glyph'], 7)):
            parts.append(
                f'<use href="#tmx-{echelon}" fill="none" stroke="{colour}" '
                f'color="{colour}" stroke-width="{width}" stroke-linecap="round" '
                f'transform="{shift}"/>'
            )
    if strength in STRENGTHS:
        # Beside the echelon when there is one, and where it would have been
        # when there is not — the two are read as one line either way.
        x = 103 if echelon in ECHELONS else 50
        parts.append(_chrome_text(STRENGTHS[strength]['text'], x, line + 7, 20,
                                  colours))
    higher = item.get('higher', '')
    if higher:
        # APP-6 field M, on the same line as the size marks and on the other
        # side of them: read together they say which unit this is and whose
        # it is, which is the pair the designation is useless without.
        parts.append(_chrome_text(higher, -3, line + 7, 20, colours, anchor='end'))
    mobility = item.get('mobility')
    if mobility in MOBILITY:
        shift = f'translate(0,{round(frame["bottom"] + MOBILITY_DROP, 2)})'
        for colour, width in ((colours['fill'], 11), (colours['glyph'], 4.5)):
            parts.append(
                f'<use href="#tmv-{mobility}" fill="none" stroke="{colour}" '
                f'color="{colour}" stroke-width="{width}" stroke-linecap="round" '
                f'stroke-linejoin="round" transform="{shift}"/>'
            )
    text = item.get('text', '')
    if text:
        # An empty frame has room for it; a frame with a pictogram in it does
        # not, so it goes beside the symbol the way APP-6 puts free text in a
        # field of its own rather than over the icon.
        if SYMBOLS[item['symbol']]['icon']:
            parts.append(_chrome_text(text, 102, 57, 22, colours, anchor='start'))
        else:
            parts.append(
                f'<text x="50" y="61" text-anchor="middle" font-size="30" '
                f'font-weight="700" font-family="{_FONT}" '
                f'fill="{colours["glyph"]}">{escape(text)}</text>'
            )
    return parts


def _chrome_text(text: str, x: float, y: float, size: float, colours: dict,
                 anchor: str = 'middle') -> str:
    """One piece of writing outside the frame: dark, over a pale outline."""
    return (
        f'<text x="{round(x, 2)}" y="{round(y, 2)}" text-anchor="{anchor}" '
        f'font-size="{size}" font-weight="700" font-family="{_FONT}" '
        f'fill="{colours["glyph"]}" stroke="{colours["fill"]}" '
        f'stroke-width="{round(size * 0.3, 2)}" paint-order="stroke" '
        f'stroke-linejoin="round">{escape(text)}</text>'
    )


def _point_svg(item: dict) -> str:
    """One task marker — Arma's `mil_*` shape, in the side's colour.

    Unlike a unit symbol this is line art rather than a filled block, which is
    how the game tells a task from a unit, and it is drawn twice: once thick in
    the side's dark edge and once on top in its colour, so the shape holds its
    own over a satellite image without an SVG filter.
    """
    colours = AFFILIATIONS[item['side']]
    marker = MARKERS.get(item.get('marker'), MARKERS[DEFAULT_MARKER])
    scale = POINT_BOX * item['size'] / 100 * 1.55
    transform = (f"translate({round(item['x'], 2)},{round(item['y'], 2)}) "
                 f"scale({round(scale, 4)}) translate(-50,-50)")
    # A planned task is dashed exactly as a planned unit's frame is, and both
    # passes carry the same pattern or the solid one shows through the gaps.
    dash = _attrs(stroke_dasharray=STATUSES[item.get('status')
                                            or DEFAULT_STATUS]['dash'])
    shape = (
        f'<g transform="{transform}">'
        f'<use href="#tmm-{marker_key(item)}" fill="none" '
        f'stroke="{colours["edge"]}" color="{colours["edge"]}" stroke-width="15" '
        f'stroke-linecap="round" stroke-linejoin="round"{dash}/>'
        f'<use href="#tmm-{marker_key(item)}" fill="none" '
        f'stroke="{colours["fill"]}" color="{colours["fill"]}" stroke-width="7" '
        f'stroke-linecap="round" stroke-linejoin="round"{dash}/>'
        f'</g>'
    )
    reach = POINT_BOX * item['size'] * 0.8
    glyph = item.get('glyph', '')
    parts = [shape]
    if glyph and marker['text']:
        parts.append(_label_svg(glyph, item['x'],
                                item['y'] + label_size(item['size']) * 0.36,
                                item['size'] * 0.9))
    label = item['label']
    if glyph and not marker['text']:
        # Nowhere to write it on the shape, so it goes in front of the name
        # rather than being dropped.
        label = f'{glyph} {label}'.strip()
    parts.append(_label_svg(label, item['x'], item['y'] + reach + 8, item['size']))
    return ''.join(parts)


def marker_key(item: dict) -> str:
    """Which `mil_*` shape a point is drawn as — the dot when it says nothing."""
    marker = item.get('marker')
    return marker if marker in MARKERS else DEFAULT_MARKER


def _text_svg(item: dict) -> str:
    return _label_svg(item['label'] or ' ', item['x'], item['y'], item['size'] * 1.6,
                      fill=item.get('color') or _LABEL_FILL)


def _centroid(points: list) -> tuple:
    return (sum(point[0] for point in points) / len(points),
            sum(point[1] for point in points) / len(points))


def arrow_head(points: list, size: float) -> list:
    """The three corners of the head on the end of a line, or [].

    Drawn as a polygon rather than an SVG `marker`, because a marker cannot
    take its colour from the line it sits on — `context-stroke` is not
    everywhere — and a line with a colour of its own would have kept the
    side's arrow.
    """
    (x1, y1), (x2, y2) = points[-2], points[-1]
    length = math.hypot(x2 - x1, y2 - y1)
    if not length:
        return []
    along = ((x2 - x1) / length, (y2 - y1) / length)
    back, wide = 13 * size, 5.5 * size
    return [
        (round(x2 + along[0] * 2 * size, 2), round(y2 + along[1] * 2 * size, 2)),
        (round(x2 - along[0] * back - along[1] * wide, 2),
         round(y2 - along[1] * back + along[0] * wide, 2)),
        (round(x2 - along[0] * back + along[1] * wide, 2),
         round(y2 - along[1] * back - along[0] * wide, 2)),
    ]


def _shape_svg(item: dict) -> str:
    colour = item_colour(item)
    path = ' '.join(f'{round(x, 2)},{round(y, 2)}' for x, y in item['points'])
    width = round(4 * item['size'], 2)
    dash = f' stroke-dasharray="{round(14 * item["size"], 1)} {round(9 * item["size"], 1)}"' \
        if item['style'] == 'dashed' else ''
    if item['kind'] == 'area':
        shape = (
            f'<polygon points="{path}" fill="{colour}" fill-opacity="0.22" '
            f'stroke="{colour}" stroke-width="{width}"{dash} '
            f'stroke-linejoin="round"/>'
        )
        centre = _centroid(item['points'])
        return shape + _label_svg(item['label'], centre[0], centre[1], item['size'])

    parts = [
        f'<polyline points="{path}" fill="none" stroke="{colour}" '
        f'stroke-width="{width}"{dash} stroke-linecap="round" '
        f'stroke-linejoin="round"/>'
    ]
    if item.get('arrow'):
        head = arrow_head(item['points'], item['size'])
        if head:
            corners = ' '.join(f'{x},{y}' for x, y in head)
            parts.append(f'<polygon points="{corners}" fill="{colour}"/>')
    if item['label']:
        middle = item['points'][len(item['points']) // 2]
        parts.append(_label_svg(item['label'], middle[0], middle[1] - 10 * item['size'],
                                item['size']))
    return ''.join(parts)


def tile_url(background: dict, zoom: int, column: int, row: int) -> str:
    """One tile of the pyramid. Row counts from the top — see BACKDROP_ZOOM above."""
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
    parts = []
    if zoom > BACKDROP_ZOOM:
        parts.append(
            f'<image href='
            f'{quoteattr(tile_url(background, BACKDROP_ZOOM, 0, 0))} '
            f'x="0" y="0" width="{doc["width"]}" height="{doc["height"]}" '
            f'preserveAspectRatio="none"/>'
        )
    for column in range(per_side):
        for row in range(per_side):
            parts.append(
                f'<image href={quoteattr(tile_url(background, zoom, column, row))} '
                f'x="{round(column * width, 3)}" y="{round(row * height, 3)}" '
                f'width="{round(width, 3)}" height="{round(height, 3)}" '
                f'preserveAspectRatio="none"/>'
            )
    # The dark sheet stays under both: a tile that 404s draws nothing at all in
    # SVG, and a terrain whose backdrop is missing too should read as terrain
    # we have not got rather than as an empty plan.
    return (f'{_empty_sheet(doc)}<g opacity="{background["opacity"]}">'
            f'{"".join(parts)}</g>')


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
    body = ''.join(item_svg(item) for item in ordered_items(doc))
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


# How many terrains one directory listing may offer. A unit's OCAP folder
# holds a few dozen; a listing far longer than that is not a map directory
# and there is no reason to render it into a dropdown.
MAX_INDEX_ENTRIES = 300

# What a folder name may be. OCAP names them after the terrain's world name
# (`tem_cham`, `tanoa`), and anything outside this is either a file or
# something that should not be pasted back into a URL.
_INDEX_NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]*$')
_HREF = re.compile(r'<a\s[^>]*?href=["\']([^"\'>]+)["\']', re.I)


def parse_map_index(body) -> list:
    """The terrain folders a directory listing offers, as plain names.

    Typing out an OCAP map URL by hand means knowing the world name of a
    terrain, which is exactly the thing nobody remembers — so the folder is
    read and its contents offered as a list. What comes back differs per web
    server, and none of them is worth requiring:

    * nginx with `autoindex_format json`, a list of `{"name", "type"}`
    * a plain JSON list of names, which is what a hand-written index gives
    * nginx or Apache's HTML autoindex, which is a page of `<a href>`

    All three are read the same way, because the answer wanted from each is
    the same: the subdirectory names. Anything that is not a directory name
    is dropped rather than guessed at — a listing is somebody else's page
    and its links are not a place this bot should follow blindly.
    """
    text = body.decode('utf-8', 'replace') if isinstance(body, bytes) else str(body or '')
    names = _index_from_json(text)
    if names is None:
        names = _index_from_html(text)
    keep = []
    for name in names:
        name = unquote(name).strip().strip('/')
        # A listing links back to where it came from and sideways to itself;
        # neither is a terrain.
        if not name or name in ('.', '..') or not _INDEX_NAME.match(name):
            continue
        if name not in keep:
            keep.append(name)
        if len(keep) >= MAX_INDEX_ENTRIES:
            break
    keep.sort(key=str.lower)
    return keep


def _index_from_json(text: str):
    """The names a JSON listing carries, or None when it is not JSON."""
    try:
        payload = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, list):
        return None
    names = []
    for entry in payload:
        if isinstance(entry, str):
            names.append(entry)
        elif isinstance(entry, dict):
            # nginx says `"type": "directory"`; a listing that says nothing
            # about type is taken at its word, since a file would not have
            # passed the name test anyway.
            if entry.get('type') not in (None, 'directory'):
                continue
            name = entry.get('name') or entry.get('path') or ''
            if isinstance(name, str):
                names.append(name)
    return names


def _index_from_html(text: str) -> list:
    """The hrefs an HTML autoindex carries, unescaped and made relative."""
    names = []
    for href in _HREF.findall(text):
        href = _html.unescape(href)
        # Only a link into this folder is a terrain in it: an absolute URL,
        # a scheme, a query or a fragment all point somewhere else.
        if href.startswith(('http://', 'https://', '//', '/', '?', '#', 'mailto:')):
            continue
        names.append(href.split('?')[0].split('#')[0])
    # nginx and Apache both write a directory with a trailing slash, which is
    # the only thing on the page that tells a terrain from the readme sitting
    # next to it. A hand-written index may link without one, so the rule is
    # "prefer the folders when the page marks any" rather than "require it".
    folders = [name for name in names if name.endswith('/')]
    return folders or names


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


def terrain_settings(terrain_id: int, name: str, world_size: float,
                     max_zoom: int) -> dict:
    """The same as `ocap_settings()`, for a terrain this bot holds itself.

    Which is the point of holding it: an uploaded archive carries the same
    `map.json` an OCAP folder does, so a map set up from a file knows exactly
    what a map set up from a URL knows — including where its corners are in
    Arma's world.
    """
    return {
        'background': {
            'kind': 'tiles',
            'url': f'/t/{int(terrain_id)}',
            'opacity': 1.0,
            'zoom': min(DEFAULT_TILE_ZOOM, max_zoom),
            'max_zoom': max_zoom,
            'name': name,
        },
        'arma': {'terrain': name, 'left': 0.0, 'bottom': 0.0,
                 'right': round(float(world_size), 2),
                 'top': round(float(world_size), 2)},
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
        return MARKERS[marker_key(item)]['arma']
    if item['kind'] == 'text':
        return 'Empty'
    # A headquarters is the one modifier Arma has a marker of its own for, and
    # it says more about the unit than its branch does.
    if item.get('hq'):
        return f'{side}_hq'
    # Arma says the air dimension with a marker of its own, and it says more
    # about a symbol drawn in an open-bottomed frame than its branch does.
    if item.get('dimension') == 'air' and item.get('symbol') not in ('air', 'heli',
                                                                     'uav'):
        return f'{side}_air'
    return f"{side}_{ARMA_TYPES.get(item.get('symbol'), 'unknown')}"


def _marker_text(item: dict) -> str:
    if item['kind'] == 'point' and item.get('glyph'):
        parts = [item['glyph']]
        if item['label']:
            parts.append(item['label'])
        return ' '.join(parts)
    if item['kind'] != 'unit':
        return item['label']
    # Arma's markers carry no echelon and no strength, so what the symbol says
    # around its frame is written into the marker's text instead of being lost
    # on the way into the mission.
    extra = []
    if item.get('status') == 'planned':
        # Arma has no dashed marker, so the one thing a dashed frame says has
        # to be said in words or it is lost on the way into the mission.
        extra.append('planned')
    if item.get('text'):
        extra.append(item['text'])
    if item.get('echelon') in ECHELONS:
        extra.append(ECHELONS[item['echelon']]['short'])
    if item.get('strength') in STRENGTHS:
        # Without its brackets: the whole tail is already in brackets, and
        # "Alpha (Plt (+))" reads as a typo.
        extra.append(STRENGTHS[item['strength']]['text'].strip('()'))
    if item.get('mobility') in MOBILITY:
        extra.append(MOBILITY[item['mobility']]['short'])
    name = item['label']
    if item.get('higher'):
        # "1-1 Alpha / A Coy" — the pair APP-6 draws as two fields, written
        # the way a unit writes it when it only has one line to write on.
        name = f'{name} / {item["higher"]}' if name else item['higher']
    if not extra:
        return name
    tail = ' '.join(extra)
    return f'{name} ({tail})' if name else tail


def _bearing(start: tuple, end: tuple) -> float:
    """Compass degrees from one world point to the next — 0 is north."""
    return round(math.degrees(math.atan2(end[0] - start[0], end[1] - start[1])) % 360, 1)


# The name Arma gives a marker somebody placed themselves, and the only shape
# it lets that person move or delete: `_USER_DEFINED #owner/index/channel`.
# The owner is the machine's own `clientOwner`, which is why an editable export
# is run once, locally, rather than broadcast — see `to_sqf()`.
USER_MARKER = '_USER_DEFINED #'

# Which channel an editable marker belongs to. Arma's own ids, and the reason
# this is a choice: markers placed in Global are seen by everybody in the
# server, Side by one side, Group by one group.
ARMA_CHANNELS = (
    (0, 'Global'),
    (1, 'Side'),
    (2, 'Command'),
    (3, 'Group'),
    (4, 'Vehicle'),
    (5, 'Direct'),
)
DEFAULT_CHANNEL = 0


def _map_number(prefix: str) -> str:
    """A number of this map's own, to keep two maps' markers apart.

    An editable marker's index has to be a number — the engine parses the
    name — so the prefix cannot ride along in it as text. The map's own digits
    do the same job, and a prefix carrying none falls back to something stable
    rather than colliding with every other such map.
    """
    digits = ''.join(character for character in prefix if character.isdigit())
    return digits or str(sum(ord(character) for character in prefix) % 900 + 100)


def to_sqf(doc: dict, *, prefix: str, title: str = '',
           editable: bool = False, channel: int = DEFAULT_CHANNEL) -> str:
    """The map as a script that puts these markers into a running mission.

    A script somebody pastes, rather than something this bot sends: vanilla
    Arma has no way to fetch anything from outside, so the way in without a mod
    is the debug console. `createMarker` is global by nature — one machine
    running it draws the plan for everybody — so LOCAL EXEC is enough.

    Running it a second time **replaces** these markers instead of doubling
    them. The read-only export does that by name: every marker is named after
    this map and the script deletes that set before it draws, which is why the
    prefix has to be this map's alone.

    **`editable` decides whether the plan can be touched in game**, and it
    changes how the markers are named. Arma only lets somebody move or delete
    a marker they own, and it works out who owns one by parsing the name it
    gives its own: `_USER_DEFINED #owner/index/channel`, all three numeric.
    Anything else — including our prefix with `_USER_DEFINED ` merely stuck in
    front of it, which is what this first tried — is read-only on the map.

    Two things follow from that name, and both are load-bearing:

    - **The owner is `clientOwner`, so the script is run once, locally.**
      GLOBAL EXEC runs the code on every machine, and each would fill its own
      id into the name and create its own set — the same plan three times over
      on a three-player server. With a read-only export the names match and
      the duplicates collapse; here they do not.
    - **The map's prefix cannot ride in the name**, since the index is
      numeric. So an editable export remembers what it drew in a public
      mission variable and deletes that on the next run, which is the same
      promise by a different route.

    `channel` is the last part of that name — see `ARMA_CHANNELS`.
    """
    prefix = ''.join(character for character in (prefix or 'tacmap')
                     if character.isalnum() or character == '_')
    prefix = (prefix or 'tacmap') + '_'
    channel = channel if channel in dict(ARMA_CHANNELS) else DEFAULT_CHANNEL
    number = _map_number(prefix)
    store = f'tacmap_{prefix}'.rstrip('_')

    # Only what is on a visible layer: a marker somebody switched off is not
    # part of the plan they are handing over, and Arma has no way to switch it
    # off again once it is drawn.
    items = ordered_items(doc)
    lines = [f'// {title or "Tactical map"} — {summarise(doc)}']
    if editable:
        channel_name = dict(ARMA_CHANNELS)[channel]
        lines += [
            '// Paste into the Arma 3 debug console and press LOCAL EXEC as a',
            '// logged-in admin — once, on one machine. The markers are global',
            '// either way, and GLOBAL EXEC would draw one set per machine.',
            '// Click a marker and press DEL to remove it, or drag it to move',
            '// it. Lines and areas are polyline markers, which the map may',
            '// not let you pick up. Running this again replaces the set.',
            f'private _chan = {channel};  '
            f'// {" · ".join(f"{key} {name}" for key, name in ARMA_CHANNELS)}',
            'private _own = format ["' + USER_MARKER + '%1/", clientOwner];',
            "private _c = '/' + str _chan;",
            f'{{ deleteMarker _x }} forEach (missionNamespace getVariable '
            f'[{_sqf_string(store)}, []]);',
            'private _n = [];',
            'private _m = "";',
        ]
    else:
        lines += [
            '// Paste into the Arma 3 debug console and press LOCAL EXEC as a',
            '// logged-in admin. The markers are global, so one machine',
            '// running this draws them for everybody. Running it again',
            '// replaces these markers.',
            f'private _p = {_sqf_string(prefix)};',
            '{ if (_x select [0, count _p] == _p) then { deleteMarker _x } } '
            'forEach allMapMarkers;',
            'private _m = "";',
        ]

    drawn = [0]

    def marker_name(index: int, suffix: str = '') -> str:
        """What goes in the `createMarker` call, as SQF.

        The two exports name a marker differently on purpose — see `to_sqf`.
        An editable one is numbered per marker rather than per item, because
        the engine parses that part of the name as a number and an arrow's
        "2a" is not one.
        """
        if not editable:
            return f'_p + "{index}{suffix}"'
        drawn[0] += 1
        return f'_own + "{number}{drawn[0]:03d}" + _c'

    for index, item in enumerate(items, start=1):
        colour = ARMA_SIDES.get(item['side'], ARMA_SIDES['unknown'])[1]
        name = marker_name(index)
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
            if editable:
                lines.append('_n pushBack _m;')
            if item['kind'] == 'line' and item.get('arrow') and len(points) > 1:
                lines.append(f'_m = createMarker [{marker_name(index, "a")}, '
                             f'[{points[-1][0]}, {points[-1][1]}]];')
                lines.append('_m setMarkerType "mil_arrow";')
                lines.append(f'_m setMarkerColor "{colour}";')
                lines.append(f'_m setMarkerDir {_bearing(points[-2], points[-1])};')
                if editable:
                    lines.append('_n pushBack _m;')
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
        if editable:
            lines.append('_n pushBack _m;')

    if editable:
        # What was drawn, so the next run can take exactly it away again —
        # public, so it is the same list on every machine and for whoever
        # pastes the corrected plan next.
        lines.append(f'missionNamespace setVariable [{_sqf_string(store)}, _n, true];')
        lines.append('hint format ["%1 markers drawn", count _n];')

    if not items:
        lines.append('// Nothing is drawn on this map yet.')
    return '\n'.join(lines) + '\n'

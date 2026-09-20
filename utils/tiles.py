"""A terrain's tile pyramid, read out of an archive somebody uploaded.

The same GDAL2Tiles layout `utils/tacmap.py` draws — `{z}/{x}/{y}.png` under one
folder — but arriving as a file rather than as an address. That is the whole
point of this module: a unit that already prepares these archives for OCAP can
hand the same file to the bot, and then nothing has to be reachable from the
outside for a map to render, not even for somebody opening a share link.

Only `zipfile` is needed for the common case, so this module stays importable
with nothing installed. **7z is read through `py7zr`, imported where it is
used** the way `utils/reddit.py` treats `aiohttp` — the archives that get passed
around in this hobby are as often 7z as zip, and making somebody repack one
every time is the kind of friction that ends with the feature unused.

**The names are read before any body is.** An archive prepared for OCAP goes as
deep as OCAP's own viewer zooms, which is far deeper than a planning sheet ever
draws, so the levels to keep are picked from the name list and only those are
pulled out — otherwise a 4096-tile level would be decompressed in full and then
thrown away.
"""

import json
import re
import zipfile

# A tile's place in the pyramid, wherever in the archive it happens to sit. The
# bounds are what a level can legitimately hold — zoom 8 would be 65536 tiles a
# side, and a path claiming that is not a tile set anybody made.
TILE_PATH = re.compile(r'(?:^|/)(\d{1,2})/(\d{1,5})/(\d{1,5})\.png$', re.IGNORECASE)
MAP_JSON = re.compile(r'(?:^|/)map\.json$', re.IGNORECASE)

PNG_MAGIC = b'\x89PNG\r\n\x1a\n'
ZIP_MAGIC = b'PK\x03\x04'
SEVENZIP_MAGIC = b'7z\xbc\xaf\x27\x1c'

# A level holds 4^z tiles, so zoom 5 is 1365 tiles counting everything above it.
# The byte cap is what stops one upload filling the database on its own.
MAX_TILES = 1400
MAX_BYTES = 150 * 1024 * 1024


class Tile:
    __slots__ = ('zoom', 'x', 'y', 'image')

    def __init__(self, zoom: int, x: int, y: int, image: bytes):
        self.zoom, self.x, self.y, self.image = zoom, x, y, image


class Archive:
    """What came out of one upload."""

    def __init__(self):
        self.tiles = []
        self.map_json = None
        self.skipped_deeper = 0     # levels this editor would never draw
        self.ignored = 0            # entries that were not tiles at all

    @property
    def bytes(self) -> int:
        return sum(len(tile.image) for tile in self.tiles)

    @property
    def levels(self) -> dict:
        counts = {}
        for tile in self.tiles:
            counts[tile.zoom] = counts.get(tile.zoom, 0) + 1
        return counts

    @property
    def deepest(self) -> int:
        return max(self.levels) if self.tiles else 0


def _py7zr():
    try:
        import py7zr
        import py7zr.io                        # noqa: F401 — the writer factory
    except ImportError:
        raise ValueError(
            'This deployment cannot read 7z archives. Zip the tile folder '
            'instead and upload that.'
        )
    return py7zr


def _tile_zoom(name: str):
    """The zoom level a path belongs to, or None when it is not a tile."""
    match = TILE_PATH.search(name)
    return int(match.group(1)) if match else None


def _names(handle) -> tuple:
    """(which format, every entry in it). No body is read."""
    handle.seek(0)
    magic = handle.read(8)
    handle.seek(0)
    if magic.startswith(ZIP_MAGIC):
        with zipfile.ZipFile(handle) as archive:
            return 'zip', [info.filename for info in archive.infolist()
                           if not info.is_dir()]
    if magic.startswith(SEVENZIP_MAGIC):
        with _py7zr().SevenZipFile(handle) as archive:
            return '7z', list(archive.getnames())
    raise ValueError('That is not a zip or a 7z archive.')


def _bodies(handle, kind: str, names: list):
    handle.seek(0)
    if kind == 'zip':
        with zipfile.ZipFile(handle) as archive:
            for name in names:
                yield name, archive.read(name)
        return
    py7zr = _py7zr()
    with py7zr.SevenZipFile(handle) as archive:
        # 7z has no per-member seek, so py7zr decompresses the whole selection
        # in one pass. The factory keeps it in memory rather than on disk,
        # which matters on a container whose filesystem is a temporary thing.
        factory = py7zr.io.BytesIOFactory(limit=MAX_BYTES)
        archive.extract(targets=list(names), factory=factory)
        for name in names:
            body = factory.get(name)
            if body is not None:
                yield name, body.read()


def read_archive(handle, *, max_zoom: int) -> Archive:
    """Every tile in the archive up to `max_zoom`, plus its `map.json` if any."""
    kind, names = _names(handle)

    result = Archive()
    wanted = []
    for name in names:
        if MAP_JSON.search(name):
            wanted.append(name)
            continue
        zoom = _tile_zoom(name)
        if zoom is None:
            result.ignored += 1
        elif zoom > max_zoom:
            result.skipped_deeper += 1
        else:
            wanted.append(name)

    if len(wanted) > MAX_TILES:
        raise ValueError(
            f'{len(wanted)} tiles up to level {max_zoom} — more than one terrain '
            f'should be. Choose a shallower deepest level.'
        )

    total = 0
    for name, data in _bodies(handle, kind, wanted):
        if MAP_JSON.search(name):
            if result.map_json is None:
                try:
                    result.map_json = json.loads(data.decode('utf-8-sig'))
                except (ValueError, UnicodeDecodeError):
                    result.map_json = None
            continue
        total += len(data)
        if total > MAX_BYTES:
            raise ValueError(
                f'That terrain is over {MAX_BYTES // (1024 * 1024)} MB at level '
                f'{max_zoom}. Choose a shallower deepest level.'
            )
        if not data.startswith(PNG_MAGIC):
            result.ignored += 1
            continue
        zoom, x, y = (int(part) for part in TILE_PATH.search(name).groups())
        result.tiles.append(Tile(zoom, x, y, data))

    if not result.tiles:
        raise ValueError(
            'No tiles in that archive. It should hold numbered folders — '
            '0/, 1/, 2/ … each with {x}/{y}.png inside.'
        )
    return result

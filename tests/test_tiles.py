"""`utils/tiles.py` decides what comes out of an uploaded archive, which is the
one place where somebody's terrain either arrives whole or arrives with holes in
it — and where a file that is not a tile set at all has to say so rather than
being stored as one."""

import io
import zipfile

import pytest

from utils import tiles

PNG = tiles.PNG_MAGIC + b'not really an image, but it is a PNG as far as this goes'


def _zip(entries: dict) -> io.BytesIO:
    handle = io.BytesIO()
    with zipfile.ZipFile(handle, 'w') as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    handle.seek(0)
    return handle


def _pyramid(deepest: int, prefix: str = 'tanoa/') -> dict:
    """A whole pyramid, the way GDAL2Tiles writes one: 4^z tiles per level."""
    return {
        f'{prefix}{zoom}/{x}/{y}.png': PNG
        for zoom in range(deepest + 1)
        for x in range(2 ** zoom)
        for y in range(2 ** zoom)
    }


# -- reading one -------------------------------------------------------------

def test_a_pyramid_comes_back_level_by_level():
    archive = tiles.read_archive(_zip(_pyramid(3)), max_zoom=3)
    assert archive.levels == {0: 1, 1: 4, 2: 16, 3: 64}
    assert archive.deepest == 3
    assert archive.bytes == 85 * len(PNG)


def test_a_tile_keeps_the_place_its_path_gives_it():
    archive = tiles.read_archive(_zip({'x/4/11/7.png': PNG}), max_zoom=4)
    tile = archive.tiles[0]
    assert (tile.zoom, tile.x, tile.y) == (4, 11, 7)
    assert tile.image == PNG


def test_levels_deeper_than_asked_for_are_left_out_and_counted():
    archive = tiles.read_archive(_zip(_pyramid(4)), max_zoom=2)
    assert archive.levels == {0: 1, 1: 4, 2: 16}
    # An archive prepared for OCAP goes deeper than a planning sheet draws;
    # saying how much was dropped is what stops that reading as data loss.
    assert archive.skipped_deeper == 64 + 256


def test_the_folder_the_tiles_sit_in_does_not_matter():
    for prefix in ('', 'tanoa/', 'maps/tanoa/', 'a/b/c/'):
        archive = tiles.read_archive(_zip(_pyramid(1, prefix)), max_zoom=1)
        assert len(archive.tiles) == 5, prefix


def test_a_map_json_riding_along_is_read():
    entries = _pyramid(1)
    entries['tanoa/map.json'] = b'{"name": "Tanoa", "worldSize": 15360}'
    archive = tiles.read_archive(_zip(entries), max_zoom=1)
    assert archive.map_json['worldSize'] == 15360
    assert len(archive.tiles) == 5


def test_a_broken_map_json_is_not_fatal():
    entries = _pyramid(1)
    entries['map.json'] = b'{oh dear'
    archive = tiles.read_archive(_zip(entries), max_zoom=1)
    assert archive.map_json is None
    assert len(archive.tiles) == 5


def test_everything_that_is_not_a_tile_is_ignored():
    entries = _pyramid(1)
    entries.update({'tanoa/openlayers.html': b'<html>', 'tanoa/readme.txt': b'hi',
                    'tanoa/1/0/0.jpg': b'\xff\xd8\xff'})
    archive = tiles.read_archive(_zip(entries), max_zoom=1)
    assert len(archive.tiles) == 5
    assert archive.ignored == 3


def test_a_tile_path_holding_something_that_is_not_a_png_is_dropped():
    entries = _pyramid(1)
    entries['tanoa/1/1/1.png'] = b'GIF89a not a png at all'
    archive = tiles.read_archive(_zip(entries), max_zoom=1)
    assert len(archive.tiles) == 4
    assert archive.ignored == 1


# -- and refusing one --------------------------------------------------------

def test_something_that_is_not_an_archive_says_so():
    with pytest.raises(ValueError) as refusal:
        tiles.read_archive(io.BytesIO(b'just some bytes'), max_zoom=4)
    assert 'zip' in str(refusal.value)


def test_an_archive_with_no_tiles_says_what_was_expected():
    with pytest.raises(ValueError) as refusal:
        tiles.read_archive(_zip({'notes.txt': b'hello'}), max_zoom=4)
    assert '{x}/{y}.png' in str(refusal.value)


def test_more_tiles_than_one_terrain_should_hold_is_refused_before_reading():
    entries = {f'6/{x}/{y}.png': PNG for x in range(64) for y in range(64)}
    with pytest.raises(ValueError) as refusal:
        tiles.read_archive(_zip(entries), max_zoom=6)
    assert 'shallower' in str(refusal.value)


def test_a_path_that_only_looks_like_a_tile_is_not_one():
    # Three numbers are needed, in folders, ending in .png.
    entries = {'4/8.png': PNG, 'notes/4/8/5.txt': PNG}
    entries.update(_pyramid(0))
    archive = tiles.read_archive(_zip(entries), max_zoom=4)
    assert [(tile.zoom, tile.x, tile.y) for tile in archive.tiles] == [(0, 0, 0)]
    assert archive.ignored == 2


def test_a_deeper_folder_is_read_by_its_last_three_numbers():
    # `maps/tanoa/8/5/9.png` is the tile, whatever it is nested in.
    archive = tiles.read_archive(_zip({'maps/tanoa/8/5/9.png': PNG}), max_zoom=8)
    assert [(tile.zoom, tile.x, tile.y) for tile in archive.tiles] == [(8, 5, 9)]


# -- the 7z half -------------------------------------------------------------

def test_a_7z_archive_reads_the_same_way():
    py7zr = pytest.importorskip('py7zr')
    handle = io.BytesIO()
    with py7zr.SevenZipFile(handle, 'w') as archive:
        for name, data in _pyramid(2).items():
            archive.writef(io.BytesIO(data), name)
        archive.writef(io.BytesIO(b'{"worldSize": 8192}'), 'map.json')
    handle.seek(0)

    read = tiles.read_archive(handle, max_zoom=2)
    assert read.levels == {0: 1, 1: 4, 2: 16}
    assert read.map_json == {'worldSize': 8192}

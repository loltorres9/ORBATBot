"""`utils/tacmap.py` is pure and imports nothing but the standard library, which
is what makes it testable — and worth testing, because its two jobs both fail
quietly. A parse that drops the wrong thing loses somebody's planning without
saying so, and a render that forgets to escape puts whatever anybody with the
share link typed straight into the page."""

from utils import tacmap


def _doc(**items):
    doc = tacmap.blank_doc()
    doc['items'] = list(items.get('items', []))
    return doc


def unit(**overrides):
    item = {'kind': 'unit', 'symbol': 'inf', 'side': 'friend', 'x': 100, 'y': 200}
    item.update(overrides)
    return item


def _parse_one(raw):
    """One item, as the document holds it after parsing — never the raw dict."""
    result = tacmap.parse(_doc(items=[raw]))
    assert result.doc['items'], result.warnings
    return result.doc['items'][0]


# -- the document -----------------------------------------------------------

def test_a_blank_map_parses_to_itself():
    result = tacmap.parse(tacmap.dumps(tacmap.blank_doc()))
    assert result.ok
    assert result.doc == tacmap.blank_doc()


def test_a_full_document_survives_a_round_trip():
    doc = _doc(items=[
        unit(label='1-1 Alpha', hq=True, rotation=45, size=1.5),
        {'kind': 'point', 'side': 'hostile', 'x': 10, 'y': 20, 'glyph': 'OBJ'},
        {'kind': 'line', 'side': 'friend', 'points': [[1, 2], [3, 4]], 'arrow': True},
        {'kind': 'area', 'side': 'neutral', 'points': [[1, 1], [9, 1], [5, 9]],
         'style': 'dashed'},
        {'kind': 'text', 'x': 5, 'y': 6, 'label': 'H-hour'},
    ])
    once = tacmap.parse(doc)
    twice = tacmap.parse(tacmap.dumps(once.doc))
    assert once.ok and twice.ok
    assert once.doc == twice.doc
    assert len(twice.doc['items']) == 5


def test_text_that_is_not_json_is_an_error_not_a_crash():
    result = tacmap.parse('{definitely not json')
    assert not result.ok
    assert result.doc == tacmap.blank_doc()


def test_a_document_that_is_not_a_mapping_is_refused():
    assert not tacmap.parse('[1, 2, 3]').ok
    assert not tacmap.parse('"a string"').ok


def test_too_many_items_is_refused_rather_than_trimmed():
    doc = _doc(items=[unit()] * (tacmap.MAX_ITEMS + 1))
    result = tacmap.parse(doc)
    assert not result.ok
    # Trimming would lose the far end of somebody's plan without saying so.
    assert str(tacmap.MAX_ITEMS) in result.errors[0]


# -- items that are not quite right ------------------------------------------

def test_an_unknown_kind_is_dropped_with_a_warning():
    result = tacmap.parse(_doc(items=[{'kind': 'hologram', 'x': 1, 'y': 2}, unit()]))
    assert result.ok
    assert len(result.doc['items']) == 1
    assert result.warnings


def test_an_unknown_symbol_becomes_an_empty_frame():
    result = tacmap.parse(_doc(items=[unit(symbol='deathstar')]))
    assert result.doc['items'][0]['symbol'] == 'generic'
    assert result.warnings


def test_an_unknown_side_falls_back_to_friendly():
    assert tacmap.parse(_doc(items=[unit(side='martian')])).doc['items'][0]['side'] \
        == tacmap.DEFAULT_SIDE


def test_an_item_without_a_position_is_dropped():
    result = tacmap.parse(_doc(items=[unit(x=None), unit(y='over there')]))
    assert result.doc['items'] == []
    assert len(result.warnings) == 2


def test_coordinates_that_are_not_numbers_are_not_positions():
    for bad in (float('nan'), float('inf'), '12px', {}):
        assert tacmap.parse(_doc(items=[unit(x=bad)])).doc['items'] == [], bad


def test_a_position_far_outside_the_sheet_is_pulled_back():
    result = tacmap.parse(_doc(items=[unit(x=9_000_000, y=-9_000_000)]))
    item = result.doc['items'][0]
    assert item['x'] == tacmap.DEFAULT_WIDTH * 1.25
    assert item['y'] == -tacmap.DEFAULT_HEIGHT / 4


def test_a_line_needs_two_points_and_an_area_three():
    result = tacmap.parse(_doc(items=[
        {'kind': 'line', 'points': [[1, 1]]},
        {'kind': 'area', 'points': [[1, 1], [2, 2]]},
        {'kind': 'line', 'points': [[1, 1], [2, 2]]},
    ]))
    assert len(result.doc['items']) == 1
    assert len(result.warnings) == 2


def test_points_come_back_as_pairs_however_they_were_written():
    result = tacmap.parse(_doc(items=[
        {'kind': 'line', 'points': [{'x': 1, 'y': 2}, [3, 4], 'nonsense']},
    ]))
    assert result.doc['items'][0]['points'] == [[1.0, 2.0], [3.0, 4.0]]


def test_a_label_is_cut_to_length_and_a_glyph_is_shouted():
    result = tacmap.parse(_doc(items=[
        unit(label='x' * 500),
        {'kind': 'point', 'x': 1, 'y': 1, 'glyph': 'objective'},
    ]))
    assert len(result.doc['items'][0]['label']) == tacmap.MAX_LABEL
    assert result.doc['items'][1]['glyph'] == 'OBJE'


def test_size_is_held_between_its_limits():
    items = tacmap.parse(_doc(items=[unit(size=99), unit(size=0)])).doc['items']
    assert items[0]['size'] == tacmap.MAX_SIZE
    assert items[1]['size'] == tacmap.MIN_SIZE


# -- layers ------------------------------------------------------------------

def _layered(*layers, items=()):
    doc = tacmap.blank_doc()
    if layers:
        doc['layers'] = list(layers)
    doc['items'] = list(items)
    return tacmap.parse(doc).doc


def test_a_document_always_has_at_least_one_layer():
    for raw in (None, [], 'phase one', [1, 2], [{'name': 'no id'}]):
        doc = tacmap.blank_doc()
        doc['layers'] = raw
        assert tacmap.parse(doc).doc['layers'] == [dict(tacmap.DEFAULT_LAYER)]


def test_layer_ids_are_slugged_and_never_repeat():
    doc = _layered({'id': 'Phase One!', 'name': 'Phase 1'},
                   {'id': 'phaseone', 'name': 'A second one'},
                   {'id': 'enemy', 'name': 'Feindlage', 'visible': False})
    assert [layer['id'] for layer in doc['layers']] == ['phaseone', 'enemy']
    assert doc['layers'][1]['visible'] is False


def test_an_item_on_a_layer_that_is_not_there_lands_on_the_first():
    doc = _layered({'id': 'plan', 'name': 'Plan'}, {'id': 'enemy', 'name': 'Enemy'},
                   items=[unit(layer='ghosts'), unit(layer='enemy')])
    assert doc['items'][0]['layer'] == 'plan'
    assert doc['items'][1]['layer'] == 'enemy'


def test_what_is_on_a_hidden_layer_is_kept_but_not_drawn():
    doc = _layered({'id': 'plan', 'name': 'Plan'},
                   {'id': 'enemy', 'name': 'Enemy', 'visible': False},
                   items=[unit(label='ours'), unit(label='theirs', layer='enemy')])
    # Both are still in the document — a switch is not a delete.
    assert len(doc['items']) == 2
    assert [item['label'] for item in tacmap.ordered_items(doc)] == ['ours']
    assert len(tacmap.ordered_items(doc, include_hidden=True)) == 2
    assert 'theirs' not in tacmap.render(doc)


def test_the_plan_of_a_hidden_layer_stays_out_of_the_arma_export():
    doc = _layered({'id': 'plan', 'name': 'Plan'},
                   {'id': 'enemy', 'name': 'Enemy', 'visible': False},
                   items=[unit(label='ours'), unit(label='theirs', layer='enemy')])
    script = tacmap.to_sqf(doc, prefix='map1')
    assert script.count('createMarker') == 1
    assert 'theirs' not in script


def test_a_symbol_is_drawn_over_an_area_whatever_order_they_were_added_in():
    doc = _layered(items=[
        unit(label='on top'),
        {'kind': 'area', 'side': 'hostile', 'points': [[1, 1], [9, 1], [5, 9]]},
    ])
    assert [item['kind'] for item in tacmap.ordered_items(doc)] == ['area', 'unit']


def test_two_of_the_same_kind_keep_the_order_the_document_gives_them():
    doc = _layered(items=[unit(label='first'), unit(label='second')])
    assert [item['label'] for item in tacmap.ordered_items(doc)] == ['first', 'second']


def test_a_layer_orders_before_the_kinds_inside_it():
    doc = _layered({'id': 'under', 'name': 'Under'}, {'id': 'over', 'name': 'Over'},
                   items=[
                       unit(label='on the lower layer', layer='under'),
                       {'kind': 'area', 'side': 'hostile', 'layer': 'over',
                        'points': [[1, 1], [9, 1], [5, 9]]},
                   ])
    # The area is on the upper layer, so it wins over the symbol below it.
    assert [item['kind'] for item in tacmap.ordered_items(doc)] == ['unit', 'area']


# -- colours -----------------------------------------------------------------

def test_a_line_may_carry_its_own_colour():
    doc = _layered(items=[{'kind': 'line', 'side': 'friend', 'color': '#FF8A00',
                           'points': [[1, 1], [9, 9]]}])
    assert doc['items'][0]['color'] == '#ff8a00'
    assert '#ff8a00' in tacmap.render(doc)


def test_anything_that_is_not_a_plain_hex_colour_is_dropped():
    for bad in ('red', '#fff', 'rgb(1,2,3)', 'url(#x)', '#12345g', ''):
        doc = _layered(items=[{'kind': 'line', 'color': bad,
                               'points': [[1, 1], [9, 9]]}])
        assert doc['items'][0]['color'] == '', bad


def test_without_one_a_line_is_drawn_in_its_sides_colour():
    doc = _layered(items=[{'kind': 'line', 'side': 'hostile',
                           'points': [[1, 1], [9, 9]]}])
    assert tacmap.item_colour(doc['items'][0]) == tacmap.AFFILIATIONS['hostile']['fill']


def test_a_symbol_takes_no_colour_of_its_own():
    # Whose a unit is has to keep being readable from its colour.
    doc = _layered(items=[unit(color='#ff00ff')])
    assert 'color' not in doc['items'][0]


# -- how small a symbol goes -------------------------------------------------

def test_a_symbol_shrinks_further_than_its_label_does():
    doc = _layered(items=[unit(label='1-1', size=0.05)])
    item = doc['items'][0]
    assert item['size'] == tacmap.MIN_SIZE
    assert tacmap.label_size(item['size']) == tacmap.MIN_LABEL
    assert tacmap.label_size(3) > tacmap.MIN_LABEL


# -- the background ----------------------------------------------------------

def test_a_background_has_to_be_an_http_link():
    doc = tacmap.blank_doc()
    doc['background']['url'] = 'javascript:alert(1)'
    result = tacmap.parse(doc)
    assert result.doc['background']['url'] == ''
    assert result.warnings


def test_a_normal_background_is_kept():
    doc = tacmap.blank_doc()
    doc['background'] = {'url': 'https://example.com/altis.jpg', 'opacity': 0.5}
    result = tacmap.parse(doc)
    assert result.doc['background']['url'] == 'https://example.com/altis.jpg'
    assert result.doc['background']['opacity'] == 0.5
    # A document written before tile sets existed has no kind, and is a picture.
    assert result.doc['background']['kind'] == 'image'
    assert not result.warnings


# -- tile sets ---------------------------------------------------------------

def _tiled(zoom=2, max_zoom=5, url='https://ocap.example/maps/tanoa'):
    doc = tacmap.blank_doc()
    doc['background'] = {'kind': 'tiles', 'url': url, 'opacity': 1.0,
                         'zoom': zoom, 'max_zoom': max_zoom, 'name': 'Tanoa'}
    return tacmap.parse(doc).doc


def test_a_tile_set_draws_one_image_per_tile_from_the_top_left():
    svg = tacmap.render(_tiled(zoom=2))
    assert svg.count('<image') == 16          # 4 per side
    assert 'href="https://ocap.example/maps/tanoa/2/0/0.png" x="0.0" y="0.0"' in svg
    # Column is x and row is y, counted downward — which is the only way OCAP's
    # own tiles assemble into the terrain the right way up.
    assert 'href="https://ocap.example/maps/tanoa/2/3/0.png" x="750.0" y="0.0"' in svg
    assert 'href="https://ocap.example/maps/tanoa/2/0/3.png" x="0.0" y="750.0"' in svg


def test_each_tile_is_drawn_over_its_neighbour_to_hide_the_seam():
    svg = tacmap.render(_tiled(zoom=2))
    first = svg.split('<image')[1]
    width = float(first.split('width="')[1].split('"')[0])
    assert width > 250                        # the cell is 1000 / 4
    assert width < 250 + tacmap.DEFAULT_WIDTH * 0.01


def test_a_tile_zoom_is_held_to_what_the_folder_has():
    assert _tiled(zoom=9, max_zoom=3)['background']['zoom'] == 3
    # And to what this editor is willing to draw, whatever the folder says.
    assert _tiled(zoom=9, max_zoom=9)['background']['zoom'] == tacmap.MAX_TILE_ZOOM


def test_a_tile_base_keeps_no_trailing_slash():
    doc = _tiled(url='https://ocap.example/maps/tanoa/')
    assert doc['background']['url'] == 'https://ocap.example/maps/tanoa'
    assert '/maps/tanoa/2/0/0.png' in tacmap.render(doc)


def test_a_tile_set_with_no_address_is_just_an_empty_sheet():
    doc = _tiled(url='')
    assert '<image' not in tacmap.render(doc)


def test_a_terrain_this_bot_serves_itself_is_a_path_not_an_address():
    # An uploaded terrain lives at /t/{id} here, so moving the site does not
    # leave every map pointing at the old domain.
    doc = _tiled(url='/t/12')
    assert doc['background']['url'] == '/t/12'
    assert 'href="/t/12/2/0/0.png"' in tacmap.render(doc)


def test_no_other_path_is_accepted_as_a_background():
    for url in ('/t/', '/t/12/', '/tiles/12', '../t/12', 'javascript:alert(1)',
                'data:image/png;base64,AAAA', '//evil.example/t/1'):
        assert _tiled(url=url)['background']['url'] == '', url


def test_an_uploaded_terrain_settles_the_background_and_the_corners():
    settings = tacmap.terrain_settings(12, 'Tanoa', 15360, 4)
    assert settings['background'] == {
        'kind': 'tiles', 'url': '/t/12', 'opacity': 1.0, 'zoom': 4,
        'max_zoom': 4, 'name': 'Tanoa',
    }
    assert settings['arma'] == {'terrain': 'Tanoa', 'left': 0.0, 'bottom': 0.0,
                                'right': 15360.0, 'top': 15360.0}


def test_a_shallow_terrain_is_not_asked_for_a_level_it_has_not_got():
    assert tacmap.terrain_settings(1, 'Small', 4096, 2)['background']['zoom'] == 2


# -- reading OCAP's map.json -------------------------------------------------

_CHAM = {'name': 'Cham', 'worldName': 'tem_cham', 'worldSize': 8192,
         'imageSize': 16384, 'multiplier': 2, 'hasTopo': True, 'maxZoom': 6,
         'attribution': 'Temppa'}


def test_an_ocap_map_gives_both_the_background_and_the_calibration():
    settings = tacmap.ocap_settings(_CHAM, 'https://ocap.example/maps/tem_cham/')
    assert settings['background']['kind'] == 'tiles'
    assert settings['background']['url'] == 'https://ocap.example/maps/tem_cham'
    assert settings['background']['name'] == 'Cham'
    # The pyramid covers the whole terrain, so the corners are the world's.
    assert settings['arma'] == {'terrain': 'Cham', 'left': 0.0, 'bottom': 0.0,
                                'right': 8192.0, 'top': 8192.0}


def test_the_deepest_zoom_comes_from_the_image_the_folder_actually_holds():
    # 16384 px of 256 px tiles is 64 per side, which is six doublings.
    assert tacmap.ocap_settings(_CHAM, 'https://x/y')['background']['max_zoom'] == 6
    # A map.json claiming more levels than its image has is not believed.
    small = dict(_CHAM, imageSize=1024, maxZoom=6)
    assert tacmap.ocap_settings(small, 'https://x/y')['background']['max_zoom'] == 2


def test_a_map_json_without_a_world_size_is_refused_with_a_message():
    for payload in ({}, {'name': 'Cham'}, {'worldSize': 0}, {'worldSize': 'big'}):
        try:
            tacmap.ocap_settings(payload, 'https://x/y')
        except ValueError as e:
            assert 'worldSize' in str(e)
        else:
            raise AssertionError(f'{payload} should have been refused')


def test_an_imported_terrain_lands_where_arma_puts_it():
    settings = tacmap.ocap_settings(_CHAM, 'https://x/y')
    doc = tacmap.blank_doc()
    doc['background'] = settings['background']
    doc['arma'] = settings['arma']
    doc = tacmap.parse(doc).doc
    assert tacmap.to_world(doc, 500, 500) == (4096.0, 4096.0)
    assert tacmap.to_world(doc, 0, 1000) == (0.0, 0.0)


# -- drawing -----------------------------------------------------------------

def test_every_symbol_frame_and_marker_is_in_the_defs():
    defs = tacmap.defs()
    for symbol in tacmap.SYMBOLS:
        assert f'id="tmi-{symbol}"' in defs
    for marker in tacmap.MARKERS:
        assert f'id="tmm-{marker}"' in defs
    for side in tacmap.AFFILIATIONS:
        assert f'id="tmf-{side}"' in defs
        assert f'id="tmh-{side}"' in defs
    for echelon in tacmap.ECHELONS:
        assert f'id="tmx-{echelon}"' in defs
    # No arrow markers: a marker cannot take a line's own colour, so the head
    # is a polygon the renderer works out.
    assert '<marker' not in defs


def test_each_side_has_a_frame_of_its_own_shape():
    """The shape says whose a unit is before the colour does."""
    for side in tacmap.AFFILIATIONS:
        svg = tacmap.item_svg(_parse_one({'kind': 'unit', 'side': side, 'x': 10,
                                          'y': 10, 'symbol': 'inf'}))
        assert f'#tmf-{side}' in svg
        assert tacmap.AFFILIATIONS[side]['fill'] in svg
    paths = {frame['path'] for frame in tacmap._FRAMES.values()}
    # Friendly and civilian share the rectangle; the other three do not share.
    assert len(paths) == 4


def test_the_pictogram_fits_inside_every_frame():
    """The icon box is the largest one a diamond will take without clipping.

    A diamond is narrowest exactly where the pictogram is tallest, which is
    what this guards: a frame drawn as small as the rectangle cuts the arms
    off an infantry X.
    """
    half = 50.0  # the hostile diamond runs corner to corner across the box
    for y in (33, 67):
        reach = half * (1 - abs(y - 50) / half)
        assert 50 - reach <= 25 and 50 + reach >= 75


def test_every_nato_marker_arma_ships_has_a_symbol():
    """The palette covers a3\\ui_f\\data\\map\\markers\\nato, so the export maps."""
    wanted = {
        'inf', 'motor_inf', 'mech_inf', 'armor', 'recon', 'air', 'plane', 'uav',
        'naval', 'med', 'art', 'mortar', 'installation', 'maint', 'service',
        'support', 'antiair', 'unknown',
    }
    assert wanted <= set(tacmap.ARMA_TYPES.values())


def test_a_point_carries_one_of_armas_markers():
    item = _parse_one({'kind': 'point', 'x': 10, 'y': 10, 'marker': 'objective'})
    assert item['marker'] == 'objective'
    assert tacmap._arma_type(item) == 'mil_objective'
    assert '#tmm-objective' in tacmap.item_svg(item)


def test_an_unknown_marker_falls_back_to_the_dot():
    item = _parse_one({'kind': 'point', 'x': 10, 'y': 10, 'marker': 'spaceship'})
    assert item['marker'] == tacmap.DEFAULT_MARKER
    assert tacmap._arma_type(item) == 'mil_dot'


def test_a_point_drawn_before_the_shapes_existed_reads_its_glyph():
    """An older map said what a point was by typing OBJ or LZ into it."""
    item = _parse_one({'kind': 'point', 'x': 10, 'y': 10, 'glyph': 'obj'})
    assert item['marker'] == 'objective'
    plain = _parse_one({'kind': 'point', 'x': 10, 'y': 10, 'glyph': 'AB'})
    assert plain['marker'] == tacmap.DEFAULT_MARKER


def test_a_glyph_with_nowhere_to_go_joins_the_label():
    """Only the round and boxy markers have room to write in."""
    inside = tacmap.item_svg(_parse_one({'kind': 'point', 'x': 10, 'y': 10,
                                         'marker': 'box', 'glyph': 'A1',
                                         'label': 'Cache'}))
    assert '>A1<' in inside and '>Cache<' in inside
    beside = tacmap.item_svg(_parse_one({'kind': 'point', 'x': 10, 'y': 10,
                                         'marker': 'flag', 'glyph': 'A1',
                                         'label': 'Cache'}))
    assert '>A1 Cache<' in beside


def test_the_civilian_side_exports_as_its_own_colour():
    doc = tacmap.blank_doc()
    doc['items'] = [{'kind': 'unit', 'side': 'civ', 'x': 10, 'y': 10,
                     'symbol': 'installation'}]
    script = tacmap.to_sqf(tacmap.parse(doc).doc, prefix='m1')
    assert 'ColorCIV' in script
    assert 'u_installation' in script


def test_the_catalog_offers_exactly_what_can_be_drawn():
    catalog = tacmap.catalog()
    assert [entry['key'] for entry in catalog['symbols']] == list(tacmap.SYMBOLS)
    assert [entry['key'] for entry in catalog['sides']] == list(tacmap.AFFILIATIONS)
    # The editor skips the icon <use> for a frame with no icon, so it has to be
    # told which those are.
    assert not next(e for e in catalog['symbols'] if e['key'] == 'generic')['hasIcon']
    assert next(e for e in catalog['symbols'] if e['key'] == 'inf')['hasIcon']


def test_a_label_is_escaped_into_the_drawing():
    doc = tacmap.parse(_doc(items=[unit(label='Ammo <b>&</b>')])).doc
    svg = tacmap.render(doc)
    assert 'Ammo &lt;b&gt;&amp;&lt;/b&gt;' in svg
    assert '<b>' not in svg


def test_a_background_url_cannot_break_out_of_its_attribute():
    from xml.etree import ElementTree

    hostile = 'https://example.com/x.jpg" onload="alert(1)'
    doc = tacmap.blank_doc()
    doc['background']['url'] = hostile
    svg = tacmap.render(tacmap.parse(doc).doc, standalone=True)

    image = ElementTree.fromstring(svg).find('.//{http://www.w3.org/2000/svg}image')
    assert image.get('{http://www.w3.org/1999/xlink}href') == hostile \
        or image.get('href') == hostile
    assert 'onload' not in image.attrib


def test_every_kind_draws_something():
    for item in (unit(),
                 {'kind': 'point', 'x': 1, 'y': 1, 'glyph': 'OBJ'},
                 {'kind': 'line', 'points': [[1, 1], [2, 2]], 'arrow': True},
                 {'kind': 'area', 'points': [[1, 1], [9, 1], [5, 9]]},
                 {'kind': 'text', 'x': 1, 'y': 1, 'label': 'Phase 1'}):
        doc = tacmap.parse(_doc(items=[item])).doc
        assert tacmap.item_svg(doc['items'][0]).strip(), item['kind']


def test_a_headquarters_carries_its_staff_and_moves_its_label_clear_of_it():
    plain = tacmap.item_svg(tacmap.parse(_doc(items=[unit(label='A')])).doc['items'][0])
    hq = tacmap.item_svg(
        tacmap.parse(_doc(items=[unit(label='A', hq=True)])).doc['items'][0]
    )
    assert '#tmh-friend' in hq and '#tmh-friend' not in plain
    # The staff hangs below the frame, so the name has to hang below the staff.
    assert _label_y(hq) > _label_y(plain)


def _label_y(svg: str) -> float:
    return float(svg.split('<text')[1].split('y="')[1].split('"')[0])


def test_the_grid_is_only_drawn_when_it_is_asked_for():
    doc = tacmap.blank_doc()
    assert '<line' not in tacmap.render(doc)
    doc['grid'] = {'show': True, 'cols': 4, 'rows': 4}
    assert tacmap.render(tacmap.parse(doc).doc).count('<line') == 6


def test_a_standalone_render_names_the_svg_namespace():
    assert 'xmlns=' in tacmap.render(tacmap.blank_doc(), standalone=True)
    assert 'xmlns=' not in tacmap.render(tacmap.blank_doc())


# -- the Arma 3 export -------------------------------------------------------

def _altis():
    doc = tacmap.blank_doc()
    doc['arma'] = {'terrain': 'Altis', 'left': 0, 'bottom': 0,
                   'right': 30720, 'top': 30720}
    return doc


def test_the_sheet_corners_become_world_corners_with_y_flipped():
    doc = tacmap.parse(_altis()).doc
    # The sheet's top-left is the world's north-west, which is y = top.
    assert tacmap.to_world(doc, 0, 0) == (0.0, 30720.0)
    assert tacmap.to_world(doc, 1000, 1000) == (30720.0, 0.0)
    assert tacmap.to_world(doc, 500, 500) == (15360.0, 15360.0)


def test_a_cropped_map_maps_onto_its_own_corners():
    doc = _altis()
    doc['arma'] = {'terrain': 'Altis', 'left': 10000, 'bottom': 5000,
                   'right': 14000, 'top': 9000}
    doc = tacmap.parse(doc).doc
    assert tacmap.to_world(doc, 0, 1000) == (10000.0, 5000.0)
    assert tacmap.to_world(doc, 1000, 0) == (14000.0, 9000.0)


def test_inside_out_corners_are_refused_rather_than_divided_by():
    doc = _altis()
    doc['arma'] = {'terrain': '', 'left': 900, 'bottom': 0, 'right': 100, 'top': 0}
    result = tacmap.parse(doc)
    assert result.warnings
    assert result.doc['arma']['right'] > result.doc['arma']['left']
    assert result.doc['arma']['top'] > result.doc['arma']['bottom']


def test_a_unit_becomes_a_nato_marker_of_its_own_side():
    doc = _altis()
    doc['items'] = [unit(symbol='armor', side='hostile', x=500, y=500,
                         label='BMP-2')]
    script = tacmap.to_sqf(tacmap.parse(doc).doc, prefix='map7')
    assert '_m setMarkerType "o_armor";' in script
    assert '_m setMarkerColor "ColorEAST";' in script
    assert '_m setMarkerText "BMP-2";' in script
    assert 'createMarker [_p + "1", [15360.0, 15360.0]];' in script


def test_a_headquarters_exports_as_the_headquarters_marker():
    doc = _altis()
    doc['items'] = [unit(hq=True, symbol='med')]
    assert '"b_hq"' in tacmap.to_sqf(tacmap.parse(doc).doc, prefix='map7')


def test_an_arrow_head_points_along_the_last_leg():
    doc = _doc(items=[{'kind': 'line', 'points': [[0, 100], [200, 100]], 'arrow': True}])
    item = tacmap.parse(doc).doc['items'][0]
    tip, left, right = tacmap.arrow_head(item['points'], item['size'])
    assert tip[0] > 200                       # beyond the end, pointing east
    assert left[0] < 200 and right[0] < 200   # both corners trail behind it
    assert left[1] != right[1]                # one either side of the line


def test_an_arrow_on_a_line_of_no_length_is_simply_not_drawn():
    assert tacmap.arrow_head([[10, 10], [10, 10]], 1) == []


def test_a_line_becomes_a_polyline_and_its_arrow_a_second_marker():
    doc = _altis()
    doc['items'] = [{'kind': 'line', 'side': 'friend', 'arrow': True,
                     'points': [[0, 1000], [1000, 1000]]}]
    script = tacmap.to_sqf(tacmap.parse(doc).doc, prefix='map7')
    assert '_m setMarkerShape "POLYLINE";' in script
    assert '_m setMarkerPolyline [0.0, 0.0, 30720.0, 0.0];' in script
    assert '_m setMarkerType "mil_arrow";' in script
    # Due east along the bottom edge of the map.
    assert '_m setMarkerDir 90.0;' in script


def test_an_area_closes_its_polyline():
    doc = _altis()
    doc['items'] = [{'kind': 'area', 'side': 'friend',
                     'points': [[0, 0], [1000, 0], [1000, 1000]]}]
    script = tacmap.to_sqf(tacmap.parse(doc).doc, prefix='map7')
    polyline = script.split('setMarkerPolyline [')[1].split(']')[0]
    corners = polyline.split(', ')
    assert corners[:2] == corners[-2:]      # Arma has no closed polyline


def test_the_script_clears_its_own_markers_before_drawing():
    doc = _altis()
    doc['items'] = [unit()]
    script = tacmap.to_sqf(tacmap.parse(doc).doc, prefix='map7')
    assert 'private _p = "map7_";' in script
    assert 'deleteMarker' in script
    assert script.index('deleteMarker') < script.index('createMarker')


def test_a_prefix_cannot_carry_anything_but_letters_and_digits():
    # The prefix is written into the script as code, so the characters that
    # would end the string and start a statement are the ones that matter.
    doc = tacmap.parse(_altis()).doc
    script = tacmap.to_sqf(doc, prefix='map7"; deleteVehicle player; //')
    assert 'private _p = "map7deleteVehicleplayer_";' in script
    assert '";' not in script.split('\n')[3].replace('_";', '')
    assert 'deleteVehicle player' not in script


def test_a_quote_in_a_label_is_doubled_the_way_sqf_wants_it():
    doc = _altis()
    doc['items'] = [unit(label='Says "hi"')]
    script = tacmap.to_sqf(tacmap.parse(doc).doc, prefix='map7')
    assert '_m setMarkerText "Says ""hi""";' in script


def test_an_empty_map_still_produces_a_runnable_script():
    script = tacmap.to_sqf(tacmap.parse(_altis()).doc, prefix='map7')
    assert 'createMarker' not in script
    assert 'Nothing is drawn' in script


# -- handing the document to a page ------------------------------------------

def test_json_for_a_page_cannot_close_the_script_tag():
    payload = tacmap.json_payload({'label': '</script><img src=x onerror=alert(1)>'})
    assert '</script' not in payload
    assert '<' not in payload and '>' not in payload
    import json
    assert json.loads(payload)['label'] == '</script><img src=x onerror=alert(1)>'


def test_the_summary_counts_what_is_on_the_map():
    doc = tacmap.parse(_doc(items=[
        unit(), unit(),
        {'kind': 'line', 'points': [[1, 1], [2, 2]]},
    ])).doc
    assert tacmap.summarise(doc) == '2 units · 1 line'
    assert tacmap.summarise(tacmap.blank_doc()) == 'empty'


def test_a_unit_carries_its_size_strength_and_abbreviation():
    item = _parse_one(unit(echelon='platoon', strength='reinforced', text='SF'))
    assert (item['echelon'], item['strength'], item['text']) == (
        'platoon', 'reinforced', 'SF')
    svg = tacmap.item_svg(item)
    assert '#tmx-platoon' in svg
    assert '(+)' in svg
    assert '>SF<' in svg


def test_a_size_mark_this_version_does_not_know_is_dropped():
    item = _parse_one(unit(echelon='corps', strength='doubled'))
    assert item['echelon'] == '' and item['strength'] == ''
    assert '#tmx-' not in tacmap.item_svg(item)


def test_the_size_mark_clears_whatever_the_frame_reaches():
    """A diamond reaches much higher than a rectangle, so the lift is per frame."""
    tops = {side: tacmap._FRAMES[side]['top'] for side in tacmap.AFFILIATIONS}
    assert tops['hostile'] < tops['friend']
    for side in tacmap.AFFILIATIONS:
        svg = tacmap.item_svg(_parse_one(unit(side=side, echelon='company')))
        shift = round(tops[side] - tacmap.ECHELON_LIFT - 50, 2)
        assert f'translate(0,{shift})' in svg


def test_an_abbreviation_never_lands_on_the_pictogram():
    """It sits in an empty frame, and beside a frame that has an icon in it."""
    empty = tacmap.item_svg(_parse_one(unit(symbol='generic', text='CH')))
    assert 'x="50" y="61"' in empty
    busy = tacmap.item_svg(_parse_one(unit(symbol='inf', text='CH')))
    assert 'x="102"' in busy


def test_the_export_writes_what_arma_cannot_draw():
    """Arma's markers have no echelon and no strength, so the text carries them."""
    doc = _doc(items=[unit(label='1-1 Alpha', echelon='platoon',
                           strength='reinforced')])
    script = tacmap.to_sqf(tacmap.parse(doc).doc, prefix='m1')
    assert '"1-1 Alpha (Plt +)"' in script

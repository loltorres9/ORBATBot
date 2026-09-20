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
    assert result.doc['background'] == {'url': 'https://example.com/altis.jpg',
                                        'opacity': 0.5}
    assert not result.warnings


# -- drawing -----------------------------------------------------------------

def test_every_symbol_and_frame_is_in_the_defs():
    defs = tacmap.defs()
    for side in tacmap.AFFILIATIONS:
        assert f'id="tmf-{side}"' in defs
        assert f'id="tma-{side}"' in defs
    for symbol in tacmap.SYMBOLS:
        assert f'id="tmi-{symbol}"' in defs
    assert 'id="tmf-hq"' in defs


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
    assert '#tmf-hq' in hq and '#tmf-hq' not in plain
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

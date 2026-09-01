"""The parser and the diff are pure, so they are the one part of this project
that is cheap to test -- and the part where a bug silently deletes slots."""

from lab import diff as diffing
from lab import parser


def parse(text):
    return parser.parse(text)


def test_squads_and_indented_slots():
    result = parse("Alpha\n  Squad Leader\n  Rifleman\n")
    assert result.ok, result.errors
    assert [s.name for s in result.squads] == ['Alpha']
    assert [s.role_name for s in result.squads[0].slots] == ['Squad Leader', 'Rifleman']


def test_blank_lines_and_comments_are_ignored():
    result = parse("# header\n\nAlpha\n\n  Rifleman\n\n# trailing\n")
    assert result.ok
    assert result.slot_count == 1


def test_tab_counts_as_indentation():
    result = parse("Alpha\n\tRifleman\n")
    assert result.ok and result.slot_count == 1


def test_squad_options():
    result = parse("Reservists | right, unit:tfp, nocount\n  Reserve\n")
    squad = result.squads[0]
    assert squad.column == 1
    assert squad.exclude_from_count
    assert squad.reserved_unit == 'TFP'     # upper-cased on the way in


def test_a_unit_on_a_slot_says_where_it_belongs_now():
    # The unit used to sit on the slot. Saying so beats dropping it silently for
    # anyone whose roster predates the move.
    result = parse("Alpha\n  Rifleman | unit:TFP\n")
    assert result.ok
    line, message = result.warnings[0]
    assert line == 2 and 'squad line' in message and 'Alpha' in message


def test_squad_radio_keeps_its_case():
    # "343 CHN:3" must come back as written — the option list used to be
    # lower-cased wholesale, which turned it into "343 chn:3".
    result = parse("1-1 Alpha | right, radio:343 CHN:3\n  Squad Leader\n")
    assert result.ok, result.errors
    assert result.squads[0].radio == '343 CHN:3'
    assert result.squads[0].column == 1


def test_squad_options_are_matched_case_insensitively():
    result = parse("Alpha | RIGHT, Unit:tfp, Radio:152 CHN : 4\n  S\n")
    squad = result.squads[0]
    assert result.ok, result.errors
    assert squad.column == 1 and squad.reserved_unit == 'TFP'
    assert squad.radio == '152 CHN : 4'


def test_unknown_option_warns_but_parses():
    result = parse("Alpha | sideways\n  Rifleman\n")
    assert result.ok
    assert any('sideways' in message for _, message in result.warnings)


def test_enumeration_is_stripped_from_slots():
    result = parse("Alpha\n  1. Squad Leader\n  2) Rifleman\n  3 - Medic\n")
    assert [s.role_name for s in result.squads[0].slots] == [
        'Squad Leader', 'Rifleman', 'Medic',
    ]


def test_enumeration_stripping_leaves_a_numeric_name_alone():
    # "1-1" is a squad identifier, not "1." followed by "1" -- stripping it
    # would leave an empty slot name.
    result = parse("Alpha\n  1-1\n")
    assert result.squads[0].slots[0].role_name == '1-1'


def test_squad_identifiers_survive_as_headers():
    result = parse("1-1 Alpha\n  Rifleman\n")
    assert result.squads[0].name == '1-1 Alpha'


def test_slot_before_any_squad_is_an_error():
    result = parse("  Rifleman\nAlpha\n  Squad Leader\n")
    assert not result.ok
    assert result.errors[0][0] == 1


def test_duplicate_squad_names_are_refused():
    result = parse("Alpha\n  Rifleman\nalpha\n  Medic\n")
    assert not result.ok
    assert 'already exists' in result.errors[0][1]


def test_squad_without_slots_is_an_error():
    result = parse("Alpha\nBravo\n  Rifleman\n")
    assert not result.ok
    assert any('has no slots' in message for _, message in result.errors)


def test_empty_text_is_an_error():
    assert not parse('').ok
    assert not parse('\n\n# nur ein Kommentar\n').ok


def test_columns_split_in_half_when_nothing_is_marked():
    result = parse('\n'.join(f"S{i}\n  Rifleman" for i in range(4)))
    assert [s.column for s in result.squads] == [0, 0, 1, 1]


def test_an_explicit_marker_stops_the_guessing():
    result = parse("A | right\n  R\nB\n  R\nC\n  R\nD\n  R\n")
    assert [s.column for s in result.squads] == [1, 0, 0, 0]


def test_round_trip_through_to_text():
    text = ("1-1 Alpha  | left, unit:TFP, radio:343 CHN:3\n  Squad Leader\n  Rifleman\n\n"
            "Reservists  | right, nocount\n  Reserve\n")
    first = parse(text)
    assert first.ok, first.errors
    stored = [
        {'name': s.name, 'column_side': s.column,
         'exclude_from_count': int(s.exclude_from_count),
         'reserved_unit': s.reserved_unit, 'radio': s.radio,
         'slots': [{'role_name': slot.role_name} for slot in s.slots]}
        for s in first.squads
    ]
    second = parse(parser.to_text(stored))
    assert second.ok, second.errors
    assert [(s.name, s.column, s.exclude_from_count, s.reserved_unit, s.radio)
            for s in second.squads] == \
           [(s.name, s.column, s.exclude_from_count, s.reserved_unit, s.radio)
            for s in first.squads]
    assert [[x.role_name for x in s.slots] for s in second.squads] == \
           [[x.role_name for x in s.slots] for s in first.squads]


def test_name_length_limits():
    long_name = 'x' * (parser.MAX_SLOT_NAME + 1)
    result = parse(f"Alpha\n  {long_name}\n")
    assert not result.ok


# -- radio nets --------------------------------------------------------------

def nets(text):
    return parser.parse_nets(text)


def test_nets_name_and_channel():
    result = nets("Platoon Net | 152 CHN : 1\nLogi | 152 CHN : 2\n")
    assert result.ok
    assert [(n.name, n.channel, n.inactive) for n in result.nets] == [
        ('Platoon Net', '152 CHN : 1', False),
        ('Logi', '152 CHN : 2', False),
    ]


def test_a_leading_dash_marks_a_net_out_of_use():
    result = nets("-Air Net | 152 CHN : 3\n")
    assert result.nets[0].name == 'Air Net'
    assert result.nets[0].inactive


def test_a_net_may_have_no_channel_yet():
    result = nets("Reserve Net\n")
    assert result.ok
    assert result.nets[0].channel is None


def test_net_blank_lines_and_comments_are_ignored():
    result = nets("# nets\n\nPlatoon Net | 1\n\n")
    assert len(result.nets) == 1


def test_a_net_without_a_name_is_an_error():
    result = nets("Platoon | 1\n- | 2\n")
    assert not result.ok
    assert result.errors[0][0] == 2


def test_empty_net_list_is_fine():
    assert nets('').ok and nets('').nets == []


def test_nets_round_trip():
    text = "Platoon Net  | 152 CHN : 1\n-Air Net  | 152 CHN : 3\nReserve"
    first = nets(text)
    stored = [{'name': n.name, 'channel': n.channel, 'inactive': n.inactive}
              for n in first.nets]
    second = nets(parser.nets_to_text(stored))
    assert [(n.name, n.channel, n.inactive) for n in second.nets] == \
           [(n.name, n.channel, n.inactive) for n in first.nets]

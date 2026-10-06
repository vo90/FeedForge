"""Independent retained-field checks do not invent musical instructions."""
from copy import deepcopy

import pytest

from feedback_converter.song_import.songsterr import parse
from feedback_converter.song_import.timeline import render
from feedback_converter.song_import.verify_source import _legacy_beat_effects, songsterr
from feedback_converter.song_import.verify_timeline import expected
from test_song_import_score import beat, measure, raw_score


LOCATION = 'parts/0/measures/0/voices/0/beats/0'
MALFORMED = [0, 1, 0.0, 1.0, '', 'true', 'natural', [], [True], {}, {'enabled': True}]


def first(document, part=0):
    return document['parts'][part]['measures'][0]['voices'][0]['beats'][0]


def played(document):
    producer = render(parse(document))
    oracle = expected(songsterr(document), {'offset': 0, 'scale': 1})
    return producer['tracks'], producer['duration'], oracle


@pytest.mark.parametrize('field', ['harmonic', 'fadeIn'])
@pytest.mark.parametrize('value', MALFORMED)
@pytest.mark.parametrize('rest', [False, True])
def test_exact_flag_structure_is_checked_before_inactive_or_rest_handling(field, value, rest):
    node = {'duration': [1, 1], 'type': 1, 'rest': True, 'notes': [{'rest': True}]} if rest else beat()
    node[field] = value
    document = raw_score([measure(node)])
    original = deepcopy(document)
    with pytest.raises(ValueError):
        _legacy_beat_effects(node, LOCATION)
    for reader in (songsterr, parse):
        with pytest.raises(ValueError):
            reader(document)
    assert document == original


@pytest.mark.parametrize('harmonic', [None, False])
@pytest.mark.parametrize('fade', [None, False, True])
@pytest.mark.parametrize('rest', [False, True])
def test_inactive_harmonic_and_boolean_fade_do_not_change_music(harmonic, fade, rest):
    node = {'duration': [1, 1], 'type': 1, 'rest': True, 'notes': [{'rest': True}]} if rest else beat()
    control = raw_score([measure(node), measure(beat(fret=7))])
    document = deepcopy(control)
    first(document).update(harmonic=harmonic, fadeIn=fade)
    original = deepcopy(document)
    assert _legacy_beat_effects(first(document), LOCATION) == {'harmonic', 'fadeIn'}
    assert {'harmonic', 'fadeIn'} <= songsterr(document).ignored
    assert played(document) == played(control)
    assert document == original


@pytest.mark.parametrize('fret,touch', [(4, None), (5, 5), (7, 7), (12, None),
                                      (2, 2.4), (15, 14.7), (15, 15), (22, 21.7)])
@pytest.mark.parametrize('fade', [None, False, True])
def test_active_harmonic_only_retains_already_explicit_natural_targets(fret, touch, fade):
    node = beat(fret=fret, harmonic='natural')
    if touch is not None:
        node['notes'][0]['harmonicFret'] = touch
    control = raw_score([measure(node)])
    control['parts'][0]['capo'] = 4
    document = deepcopy(control)
    first(document).update(harmonic=True, fadeIn=fade)
    original = deepcopy(document)
    assert played(document) == played(control)
    assert document == original
    atom = songsterr(document).parts[0].bars[0][0]
    assert atom.effects['hm'] is True


def test_natural_chord_and_rest_slot_have_no_beat_fanout():
    node = beat(fret=12, harmonic='natural')
    node['notes'].extend([{'string': 1, 'fret': 12, 'harmonic': 'natural'}, {'rest': True}])
    control = raw_score([measure(node)])
    document = deepcopy(control)
    first(document)['harmonic'] = True
    assert played(document) == played(control)
    assert len(songsterr(document).parts[0].bars[0]) == 2


@pytest.mark.parametrize('notes,rest', [
    ([], False), ([{'rest': True}], False), ([{'rest': True}], True),
    ([{'string': 0, 'fret': 12, 'harmonic': 'natural'}], True),
    ([{'string': 0, 'fret': 12}], False),
    ([{'string': 0, 'fret': 12, 'harmonic': 'pinch'}], False),
    ([{'string': 0, 'fret': 12, 'harmonic': True}], False),
    ([{'string': 0, 'fret': 12, 'harmonic': 'natural'}, {'string': 1, 'fret': 12}], False),
    ([{'string': 0, 'fret': 12, 'harmonic': 'natural'}, {'string': 1, 'fret': 12, 'harmonic': 'pinch'}], False),
    ([{'string': 0, 'fret': 12, 'harmonic': 'natural', 'dead': True}], False),
    ([{'string': 0, 'fret': 12, 'harmonic': 'natural', 'pickScrape': 'up'}], False),
    ([{'string': 0, 'fret': 12, 'harmonic': 'natural', 'harmonicData': {}}], False),
    ([{'string': 0, 'fret': 12, 'harmonic': 'natural', 'harmonicFret': 7}], False),
    ([{'string': 0, 'fret': 6, 'harmonic': 'natural'}], False),
    ([{'string': 0, 'fret': 12, 'harmonic': 'natural', 'harmonicFret': '12'}], False),
    ([{'string': 0, 'fret': True, 'harmonic': 'natural'}], False),
    ([{'string': 0, 'fret': float('inf'), 'harmonic': 'natural'}], False),
    ([{'string': 0, 'fret': 12, 'harmonic': 'natural', 'harmonicFret': float('nan')}], False),
])
def test_active_harmonic_cannot_infer_missing_mixed_conflicting_or_invalid_targets(notes, rest):
    node = {'duration': [1, 1], 'notes': notes, 'harmonic': True}
    if rest:
        node['rest'] = True
    document = raw_score([measure(node)])
    for reader in (songsterr, parse):
        with pytest.raises(ValueError):
            reader(document)


def excluded_part(document, node):
    document['tracks'].append({'id': 1, 'name': 'Excluded percussion', 'instrumentId': 128})
    part = deepcopy(document['parts'][0])
    part['measures'][0]['voices'][0]['beats'][0] = node
    document['parts'].append(part)


@pytest.mark.parametrize('field', ['harmonic', 'fadeIn'])
@pytest.mark.parametrize('value', MALFORMED)
def test_excluded_instrument_cannot_hide_malformed_retained_flags(field, value):
    document = raw_score([measure(beat())])
    excluded_part(document, {'duration': [1, 1], 'notes': [{'midi': 38}], field: value})
    for reader in (songsterr, parse):
        with pytest.raises(ValueError):
            reader(document)


@pytest.mark.parametrize('value', [None, False, True])
def test_excluded_instrument_keeps_its_own_note_vocabulary_without_harmonic_mapping(value):
    document = raw_score([measure(beat())])
    excluded_part(document, {'duration': [1, 1], 'notes': [{'midi': 38}], 'harmonic': value, 'fadeIn': value})
    original = deepcopy(document)
    checked = songsterr(document)
    assert len(checked.parts) == 1
    assert checked.excluded == [{'id': '1', 'name': 'Excluded percussion', 'reason': 'non_playable_instrument'}]
    assert len(parse(document).tracks) == 1
    assert document == original


@pytest.mark.parametrize('value', [None, False, True])
def test_duplicate_excluded_drum_id_does_not_make_it_a_playable_harmonic_source(value):
    from feedback_converter.song_import.compatibility import inspect_songsterr

    document = raw_score([measure(beat())])
    excluded_part(document, {'duration': [1, 1], 'notes': [{'midi': 38}], 'harmonic': value, 'fadeIn': value})
    document['tracks'][1]['id'] = document['tracks'][0]['id']
    checked = songsterr(document)
    assert len(checked.parts) == len(parse(document).tracks) == 1
    assert checked.parts[0].id == checked.excluded[0]['id'] == '0'
    assert 'hm' not in checked.parts[0].bars[0][0].effects
    findings = [row for row in inspect_songsterr(document)['findings'] if row['feature'] in ('beat.harmonic', 'beat.fadeIn')]
    assert len(findings) == 2
    assert all(row['location'].startswith('parts/1/') and row['category'] == 'source_metadata'
               and row['impact'] == 'source_retained' for row in findings)


@pytest.mark.parametrize('rest_voice', [
    {'rest': True}, {'rest': True, 'beats': None}, {'rest': True, 'beats': 0},
    {'rest': True, 'beats': ['unrelated', 0]},
])
def test_new_flag_scan_does_not_validate_unrelated_excluded_rest_structure(rest_voice):
    document = raw_score([measure(beat())])
    excluded_part(document, beat())
    document['parts'][1]['measures'][0]['voices'] = [rest_voice]
    for reader in (songsterr, parse):
        selected = reader(document)
        assert len(selected.parts if reader is songsterr else selected.tracks) == 1


@pytest.mark.parametrize('field', ['harmonic', 'fadeIn'])
@pytest.mark.parametrize('value', [None, False, True, 0])
def test_present_flags_inside_excluded_rest_voice_still_receive_shape_validation(field, value):
    document = raw_score([measure(beat())])
    excluded_part(document, beat())
    document['parts'][1]['measures'][0]['voices'] = [
        {'rest': True, 'beats': [0, {field: value}]},
    ]
    for reader in (songsterr, parse):
        if type(value) is int:
            with pytest.raises(ValueError):
                reader(document)
        else:
            selected = reader(document)
            assert len(selected.parts if reader is songsterr else selected.tracks) == 1


@pytest.mark.parametrize('value', [0, None, False, True])
def test_diagnostic_selection_checks_shape_but_does_not_qualify_unselected_music(value):
    document = raw_score([measure(beat())])
    excluded_part(document, {'duration': [1, 1], 'notes': [{'fret': 'unqualified'}], 'harmonic': value})
    document['tracks'][1]['instrumentId'] = 29
    for reader in (songsterr, parse):
        if type(value) is int:
            with pytest.raises(ValueError):
                reader(document, track_indices={0})
        else:
            selected = reader(document, track_indices={0})
            assert len(selected.parts if reader is songsterr else selected.tracks) == 1


def test_fade_in_does_not_remove_an_existing_finger_bend():
    node = beat(fret=5, bend={'points': [{'position': 0, 'tone': 0}, {'position': 60, 'tone': 100}]})
    control = raw_score([measure(node)])
    document = deepcopy(control)
    first(document)['fadeIn'] = True
    assert played(document) == played(control)
    assert songsterr(document).parts[0].bars[0][0].bends


def test_retained_fade_does_not_admit_other_negative_dead_frets():
    document = raw_score([measure(beat(fret=-2, dead=True))])
    first(document)['fadeIn'] = True
    for reader in (songsterr, parse):
        with pytest.raises(ValueError):
            reader(document)


def test_retained_fade_keeps_admitted_negative_mute_music_and_authored_source():
    control = raw_score([measure(beat(fret=-1, dead=True))])
    document = deepcopy(control)
    first(document)['fadeIn'] = True
    original = deepcopy(document)
    assert played(document) == played(control)
    assert parse(document).tracks[0].bars[0][0].authored_fret == -1
    assert songsterr(document).parts[0].bars[0][0].authored_fret == -1
    assert document == original


def test_retained_fade_does_not_admit_differing_bend_origin_tie():
    origin = beat(fret=5, duration=(1, 2), bend={'points': [{'position': 0, 'tone': 0}, {'position': 60, 'tone': 100}]})
    origin['fadeIn'] = True
    document = raw_score([measure(origin, beat(fret=0, duration=(1, 2), tie=True))])
    with pytest.raises(ValueError):
        render(parse(document))
    with pytest.raises(ValueError):
        expected(songsterr(document), {'offset': 0, 'scale': 1})


def test_independent_validation_does_not_call_the_producer_helper(monkeypatch):
    from feedback_converter.song_import import songsterr_legacy_effects

    def producer_must_not_run(*args, **kwargs):
        raise AssertionError('producer helper invoked by independent reader')

    monkeypatch.setattr(songsterr_legacy_effects, 'validate_legacy_beat_effect', producer_must_not_run)
    document = raw_score([measure(beat(fret=12, harmonic='natural'))])
    first(document).update(harmonic=True, fadeIn=True)
    assert songsterr(document).parts[0].bars[0][0].effects['hm']

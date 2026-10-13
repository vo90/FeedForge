"""Independent rest context, route and incremental continuation boundaries."""
from copy import deepcopy

import pytest

from feedback_converter.song_import.verify_source import songsterr
from feedback_converter.song_import.verify_timeline import expected
from feedback_converter.song_import.verify_rest_ties import plain_beat
from test_song_import_score import beat, measure, raw_score


ALIGNMENT = {'offset': 0, 'scale': 1}


def rest(duration=(1, 1), **fields):
    return {'duration': list(duration), 'rest': True, 'notes': [], **fields}


def whole_voice_source(**voice_fields):
    silent = measure()
    silent['voices'][0].update(rest=True, **voice_fields)
    return raw_score([measure(beat(7)), silent, measure(beat(0, tie=True))])


def test_empty_explicit_rest_voice_is_evidence_without_invented_notation():
    source = songsterr(whole_voice_source(hasSameRhythm=False))
    result = expected(source, ALIGNMENT)
    row, = result['rest_ties']
    assert row['rests'] == [{
        'sourceId': 'songsterr:0:1:0', 'location': 'parts/0/measures/1/voices/0',
        'occurrence': 2, 'start': 2.0, 'end': 4.0,
        'quarters': {'start': '4', 'end': '8'}}]
    assert row['start'] == 4.0 and row['used'] == {'fret': 7}
    assert len(result['parts'][0]['notes']) == 1
    assert not source.parts[0].beats[1]
    assert not [b for b in result['parts'][0]['notation_beats'] if b['measure'] == 2]
    assert not expected(source, ALIGNMENT, rest_bridge_ties=False)['rest_ties']


def test_empty_voice_unsupported_context_is_not_assumed_plain():
    # Even a retained field does not widen the deliberately explicit policy.
    with pytest.raises(ValueError):
        expected(songsterr(whole_voice_source(id='unqualified-rest-metadata')), ALIGNMENT)


@pytest.mark.parametrize('field,value', [
    ('vibrato', True), ('sustainPedal', True), ('fadeIn', True),
    ('tremoloBar', {'points': [{'position': 0, 'tone': 0}, {'position': 60, 'tone': 100}]}),
])
def test_control_on_intervening_rest_is_not_lost_when_rest_has_no_atoms(field, value):
    document = raw_score([measure(beat(7)), measure(rest(**{field: value})),
                          measure(beat(0, tie=True))])
    with pytest.raises(ValueError):
        expected(songsterr(document), ALIGNMENT)


def test_other_string_expression_in_same_beat_prevents_plain_bridge():
    first = beat(7)
    first['notes'].append({'string': 1, 'fret': 5, 'vibrato': True})
    document = raw_score([measure(first), measure(rest()), measure(beat(0, tie=True))])
    with pytest.raises(ValueError):
        expected(songsterr(document), ALIGNMENT)


@pytest.mark.parametrize('field,value', [('vibrato', True), ('accentuated', True), ('slide', 'upwards')])
def test_later_continuation_must_remain_plain_after_first_rest_bridge(field, value):
    document = raw_score([measure(beat(7)), measure(rest()),
        measure(beat(0, duration=(1, 2), tie=True),
                beat(7, duration=(1, 2), tie=True, **{field: value}))])
    with pytest.raises(ValueError):
        expected(songsterr(document), ALIGNMENT)


@pytest.mark.parametrize('order,tie_bar,rest_bar', [([0, 1, 3], 3, 1), ([0, 2, 1], 1, 2)])
def test_rest_bridge_cannot_jump_over_written_bars_or_backward(monkeypatch, order, tie_bar, rest_bar):
    bars = [measure(beat(7))] + [measure(rest()) for _ in range(3)]
    bars[tie_bar] = measure(beat(0, tie=True))
    source = songsterr(raw_score(bars))
    monkeypatch.setattr('feedback_converter.song_import.verify_timeline.visits', lambda _: order)
    with pytest.raises(ValueError):
        expected(source, ALIGNMENT)


def test_repeat_before_new_origin_does_not_preclude_later_consecutive_bridge(monkeypatch):
    source = songsterr(raw_score([measure(beat(7)), measure(rest()), measure(beat(0, tie=True))]))
    monkeypatch.setattr('feedback_converter.song_import.verify_timeline.visits', lambda _: [0, 1, 0, 1, 2])
    row, = expected(source, ALIGNMENT)['rest_ties']
    assert row['originOccurrence'] == 3 and row['occurrence'] == 5
    assert row['rests'][0]['occurrence'] == 4


def test_literal_boolean_bar_marker_is_only_a_beat_noop():
    assert plain_beat(rest(tremoloBar=True))
    assert plain_beat(rest(tremoloBar=False))
    assert not plain_beat(rest(tremoloBar=1))
    note_marker = beat(7, tremoloBar=True)
    assert not plain_beat(note_marker)
    original = deepcopy(note_marker)
    plain_beat(note_marker)
    assert note_marker == original


@pytest.mark.parametrize('bridge', [False, True])
def test_long_tie_chains_do_not_recheck_growing_history_for_every_segment(monkeypatch, bridge):
    from feedback_converter.song_import import verify_rest_ties
    bars = [measure(beat(7))] + [measure(beat(7, tie=True)) for _ in range(255)]
    if bridge:
        bars.insert(128, measure(rest()))
    calls = 0
    original = verify_rest_ties._plain
    def counted(atom):
        nonlocal calls
        calls += 1
        return original(atom)
    monkeypatch.setattr(verify_rest_ties, '_plain', counted)
    result = expected(songsterr(raw_score(bars)), ALIGNMENT)
    assert len(result['parts'][0]['notes']) == 1
    assert bool(result['rest_ties']) is bridge
    # Qualify history once at the first bridge, then each new segment once.
    # An ordinary tie chain must not trigger repeated history reconstruction.
    assert calls <= 2 * len(bars)

from copy import deepcopy

import pytest

from test_song_import_score import beat, import_json, measure, raw_score
from feedback_converter.song_import.verify_source import songsterr
from feedback_converter.song_import.verify_timeline import expected


def test_tie_extends_a_short_written_note_through_unfilled_bar_time(tmp_path):
    source = raw_score([measure(beat(0, duration=(1, 8))), measure(beat(0, duration=(1, 8), tie=True))])
    original = deepcopy(source)
    actual = import_json(tmp_path, source)['tracks'][0]
    wanted = expected(songsterr(source), {'offset': 0, 'scale': 1})['parts'][0]['notes']
    assert len(actual['notes']) == len(wanted) == 1
    assert actual['notes'][0]['sus'] == wanted[0]['note']['sus'] == 2.25
    written = actual['notation']['measures']
    assert written[0]['staves']['staff']['voices'][0]['beats'][0]['end_time'] == .25
    assert written[1]['staves']['staff']['voices'][0]['beats'][0]['notes'][0]['tied']
    assert source == original


def test_simultaneous_tied_segments_in_independent_voices_are_not_new_attacks(tmp_path):
    first = measure(beat(3, duration=(1, 4)), beat(5, duration=(1, 4)))
    first['voices'].append({'beats': [beat(7, duration=(1, 2), tie=False)]})
    second = measure(beat(5, tie=True))
    second['voices'].append({'beats': [beat(7, tie=True)]})
    # Two different attacks in separate voices must stay separate; only their
    # simultaneous hidden continuations share the later onset/string.
    first['voices'][1]['beats'].insert(0, {'duration': [1, 8], 'notes': [{'rest': True}]})
    source = raw_score([first, second])
    tracks = import_json(tmp_path, source)['tracks']
    parts = expected(songsterr(source), {'offset': 0, 'scale': 1})['parts']
    actual=sorted([n for t in tracks for n in t['notes']],key=lambda n:n['t'])
    assert len(tracks)==len(parts)==2
    assert len(actual)==sum(len(p['notes']) for p in parts)==3
    assert [n['t'] for n in actual] == [0, .25, .5]
    assert sorted(n['t'] + n['sus'] for n in actual) == [.5, 4, 4]


def test_plain_stored_fret_difference_keeps_origin_through_unfilled_bar(tmp_path):
    source = raw_score([measure(beat(3, duration=(1, 8))), measure(beat(4, tie=True))])
    original = deepcopy(source)
    actual = import_json(tmp_path, source)
    wanted = expected(songsterr(source), {'offset': 0, 'scale': 1})
    note, = actual['tracks'][0]['notes']
    assert (note['f'], note['t'], note['sus']) == (3, 0, 4)
    assert actual['plainTieIdentityEvidence'] == wanted['plain_tie_identities']
    row, = actual['plainTieIdentityEvidence']
    assert row['authored'] == {'fret': 4} and row['used'] == {'fret': 3}
    assert (row['attack'], row['start'], row['end']) == (0, 2, 4)
    assert row['originSourceId'] == note['source_ids'][0]
    notation = actual['tracks'][0]['notation']['measures'][1]['staves']['staff']['voices'][0]['beats'][0]['notes'][0]
    assert notation['tied'] and notation['fret'] == 3 and notation['midi'] == 67
    assert source == original


@pytest.mark.parametrize('fault', ['gesture', 'voice', 'missing'])
def test_tie_never_invents_an_origin_or_changes_its_fret(tmp_path, fault):
    source = raw_score([measure(beat(3, duration=(1, 8))), measure(beat(3, tie=True))])
    if fault == 'gesture': source['parts'][0]['measures'][1]['voices'][0]['beats'][0]['notes'][0].update(fret=4, slide='upwards')
    elif fault == 'missing': source['parts'][0]['measures'][0]['voices'][0]['beats'][0]['notes'] = [{'rest': True}]
    else: source['parts'][0]['measures'][1]['voices'].insert(0, {'beats': []})
    with pytest.raises(Exception, match='tie'): import_json(tmp_path, source)
    with pytest.raises(Exception, match='tie'): expected(songsterr(source), {'offset': 0, 'scale': 1})

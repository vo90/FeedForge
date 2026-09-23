from copy import deepcopy

import pytest

from test_song_import_score import beat, import_json, measure, raw_score
from feedback_converter.song_import.verify_source import songsterr
from feedback_converter.song_import.verify_timeline import expected


def chord(*, direction='down', shift=100, tie=False, staccato=False, duration=(1, 1)):
    return {'duration': list(duration), 'brushStroke': {'direction': direction, 'duration': 120, 'shift': shift},
            'notes': [{'string': s, 'fret': f, 'tie': tie, 'staccato': staccato} for s, f in [(5, 3), (4, 5)]]}


def checked(tmp_path, source):
    saved = deepcopy(source)
    track = import_json(tmp_path, source)['tracks'][0]
    actual = sorted(track['notes'] + [{**n, 't': c['t']} for c in track['chords'] for n in c['notes']], key=lambda n: (n['t'], n['s']))
    independent = expected(songsterr(source), {'offset': 0, 'scale': 1})['parts'][0]['notes']
    wanted = sorted([n['note'] for n in independent], key=lambda n: (n['t'], n['s']))
    for a, b in zip(actual, wanted):
        assert a['t'] == pytest.approx(b['t'], abs=1e-6)
        assert a['sus'] == pytest.approx(b['sus'], abs=1e-6)
        assert (a['s'], a['f'], a.get('pkd')) == (b['s'], b['f'], b.get('pkd'))
    assert len(actual) == len(wanted)
    assert source == saved
    return actual, track


def test_tied_strum_preserves_two_attacks_and_full_written_continuity(tmp_path):
    source = raw_score([measure(chord()), measure(chord(direction='up', shift=12.5, tie=True))])
    notes, _ = checked(tmp_path, source)
    assert [n['t'] for n in notes] == [0, .0625]
    assert [n['t'] + n['sus'] for n in notes] == [4, 4]
    assert [n['pkd'] for n in notes] == [0, 0]  # Hidden continuation is not a new pick.
    assert all(len(n['source_ids']) == 2 for n in notes)


def test_fractional_shift_can_start_in_the_preceding_measure(tmp_path):
    notes, track = checked(tmp_path, raw_score([measure(beat()), measure(chord(shift=12.5))]))
    assert [n['t'] for n in notes] == [0, 1.9453125, 2.0078125]
    assert [n['t'] + n['sus'] for n in notes] == pytest.approx([2, 4, 4], abs=1e-6)
    assert track['notation']['measures'][1]['t'] == 2


def test_staccato_follows_shifted_attack_after_the_tie_chain_is_folded(tmp_path):
    notes, _ = checked(tmp_path, raw_score([measure(chord(staccato=True)), measure(chord(tie=True))]))
    assert [n['t'] for n in notes] == [0, .0625]
    assert [n['sus'] for n in notes] == [2, 1.96875]


def test_strum_does_not_repair_fret_mismatched_ties(tmp_path):
    continuation = chord(tie=True)
    continuation['notes'][0]['fret'] = 9
    source = raw_score([measure(chord()), measure(continuation)])
    with pytest.raises(Exception, match='tie'):
        import_json(tmp_path, source)
    with pytest.raises(Exception, match='tie'):
        expected(songsterr(source), {'offset': 0, 'scale': 1})

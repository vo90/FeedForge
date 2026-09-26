from copy import deepcopy

from test_song_import_score import raw_score, measure, beat
from feedback_converter.song_import.compatibility import inspect_songsterr
from feedback_converter.song_import.diagnostics import diagnose_arrangements


def test_valid_extended_fret_is_a_located_approved_gameplay_omission():
    source = raw_score([measure(beat(fret=29))]); original = deepcopy(source)
    report = inspect_songsterr(source)
    finding = report['findings'][0]
    assert finding['feature'] == 'note.fret_range'
    assert 'decisionId' not in finding and finding['workStatus'] == 'gameplay_omission'
    assert finding['location'] == 'parts/0/measures/0/voices/0/beats/0/notes/0/fret'
    assert finding['value'] == 29 and source == original
    diagnose_arrangements(source, report)
    assert report['arrangements'][0]['status'] == 'score_ready'
    assert report['arrangements'][0]['omissions']['omittedNotes'] == 1


def test_distinct_simultaneous_voices_are_retained_as_a_playing_choice():
    bar = measure(beat(fret=5));bar['voices'].append({'beats':[beat(fret=7)]})
    source = raw_score([bar]); original = deepcopy(source)
    report = inspect_songsterr(source);diagnose_arrangements(source,report)
    finding = report['findings'][-1]
    assert finding['feature'] == 'arrangement.simultaneous_voices'
    assert finding['decisionId'] == 'D8' and finding['measure'] == 1
    assert [n['fret'] for n in finding['value']] == [5,7]
    assert [n['voice'] for n in finding['value']] == ['0','1']
    assert source == original and report['arrangementSummary']['scoreReady'] == 0

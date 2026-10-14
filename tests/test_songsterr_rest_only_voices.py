"""Compact explicit silence has the same music as an explicit empty beat list."""
from copy import deepcopy
import json

import pytest

from feedback_converter.song_import.model import ScoreImportError
from feedback_converter.song_import.songsterr import parse
from feedback_converter.song_import.timeline import render
from feedback_converter.song_import.verify_source import songsterr, UnverifiedFeature
from feedback_converter.song_import.verify_timeline import expected
from test_song_import_score import beat, measure, raw_score
from test_songsterr_rest_tie_verification import package, verify_files


def explicit(document):
    result = deepcopy(document)
    for part in result['parts']:
        for bar in part['measures']:
            for voice in bar['voices']:
                if 'beats' not in voice:
                    assert voice.get('rest') is True
                    voice['beats'] = []
    return result


def equivalent(raw):
    before = deepcopy(raw)
    expanded = explicit(raw)
    compact_score, expanded_score = parse(raw), parse(expanded)
    assert compact_score.source_document['document'] == raw
    # Only the archived source encoding differs; musical notation must match.
    compact_score.source_document = expanded_score.source_document
    compact_score.feature_inventory = expanded_score.feature_inventory
    assert compact_score == expanded_score
    compact, full = render(parse(raw)), render(parse(expanded))
    for key in ('sourceScore', 'featureInventory'):
        compact.pop(key)
        full.pop(key)
    assert compact == full
    assert expected(songsterr(raw), {'offset': 0, 'scale': 1}) == expected(
        songsterr(expanded), {'offset': 0, 'scale': 1})
    assert raw == before


@pytest.mark.parametrize('voice_index', [0, 1])
@pytest.mark.parametrize('feel', ['off', '8th', '16th', 'dotted8th', 'scottish16th'])
@pytest.mark.parametrize('pickup', [False, True])
def test_voice_indices_timing_pickups_and_swing(voice_index, feel, pickup):
    raw = raw_score([measure(beat(3, duration=(1, 8)), beat(5, duration=(1, 8)), tripletFeel=feel),
                     measure(beat(7))])
    raw['parts'][0]['anacrusis'] = pickup
    for bar in raw['parts'][0]['measures']:
        bar['voices'].insert(voice_index, {'rest': True})
    equivalent(raw)


@pytest.mark.parametrize('gap', [1, 4])
@pytest.mark.parametrize('repeat', [False, True])
def test_whole_bar_rests_bridge_ties_and_repeats(gap, repeat):
    raw = raw_score([measure(beat(7))] + [measure() for _ in range(gap)] + [measure(beat(0, tie=True))])
    for bar in raw['parts'][0]['measures'][1:-1]:
        bar['voices'] = [{'rest': True}]
    if repeat:
        raw['parts'][0]['measures'][0]['repeatStart'] = True
        raw['parts'][0]['measures'][-1]['repeat'] = 2
    equivalent(raw)
    assert render(parse(raw))['restTieEvidence']


def test_primary_lyric_voice_rest_does_not_consume_a_syllable():
    from test_songsterr_lyrics import document, timeline
    raw = document('One two')
    raw['parts'][1]['measures'][0]['voices'] = [{'rest': True}]
    equivalent(raw)
    assert timeline(raw) == timeline(explicit(raw))
    assert timeline(raw)['events']


def test_sound_automation_crosses_silent_voice():
    from test_songsterr_tones import document, checked
    raw = document()
    raw['parts'][0]['measures'][0]['voices'] = [{'rest': True}]
    equivalent(raw)
    assert checked(raw)[1:] == checked(explicit(raw))[1:]


@pytest.mark.parametrize('voice', [{}, {'rest': False}, {'rest': None}, {'rest': 1}, {'rest': 'true'},
    {'rest': True, 'beats': None}, {'rest': True, 'beats': {}}, {'rest': True, 'beats': '[]'},
    {'rest': True, 'beats': False}, {'rest': True, 'unknownActiveTechnique': True}])
def test_missing_music_malformed_lists_and_unknown_active_fields_stay_rejected(voice):
    raw = raw_score([measure(beat())])
    raw['parts'][0]['measures'][0]['voices'].append(voice)
    with pytest.raises(ScoreImportError):
        parse(raw)
    with pytest.raises((UnverifiedFeature, ValueError)):
        songsterr(raw)


@pytest.mark.parametrize('hybrid', [False, True])
def test_package_preserves_original_source_and_detects_removed_rest(tmp_path, hybrid):
    raw = raw_score([measure(beat(7)), measure(), measure(beat(0, tie=True))])
    raw['parts'][0]['measures'][1]['voices'] = [{'rest': True}]
    files, manifest, alignment, path, performance, options = package(tmp_path, raw, contract=125, hybrid=hybrid)
    assert json.loads(path.read_bytes()) == raw
    assert files['import/source.json'] == path.read_bytes()
    assert json.loads(files['import/source.json']) == raw
    mutated = deepcopy(raw)
    mutated['parts'][0]['measures'][1]['voices'][0] = {}
    path.write_text(json.dumps(mutated), encoding='utf8')
    result = verify_files(tmp_path, files, path, alignment, **({'hybrid_options': options} if hybrid else {}))
    assert result['status'] != 'passed'

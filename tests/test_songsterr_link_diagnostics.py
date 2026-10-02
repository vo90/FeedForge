"""Located link failures must not grant a new musical interpretation."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from test_song_import_score import beat, measure, raw_score
from feedback_converter.song_import.compatibility import new_report
from feedback_converter.song_import.diagnostics import diagnose_arrangements
from feedback_converter.song_import.link_diagnostics import coordinates
from feedback_converter.song_import.model import ScoreImportError
from feedback_converter.song_import.songsterr import parse
from feedback_converter.song_import.timeline import render
from feedback_converter.song_import.verify_source import songsterr
from feedback_converter.song_import.verify_timeline import expected


def failure(doc):
    original = deepcopy(doc)
    with pytest.raises(ScoreImportError) as caught:
        render(parse(doc))
    # Reporting does not relax the separate source evaluator either.
    with pytest.raises(ValueError):
        expected(songsterr(doc), {'offset': 0, 'scale': 1})
    assert doc == original
    return caught.value


@pytest.mark.parametrize('gesture', [{'slide': 'shift'}, {'hp': True}, {'slide': 'shift', 'dead': True, 'fret': None}])
def test_repeat_boundary_keeps_origin_and_current_traversal(gesture):
    doc = raw_score([measure(beat(**gesture), repeatStart=True, repeat=2)])
    e = failure(doc)
    assert e.source_feature == 'note.linked_destination'
    assert e.source_value['reason'] == 'repeat_jump'
    assert e.source_value['links'][0]['occurrence'] == 1
    assert e.source_value['boundary'] == {'fromMeasure': 1, 'toMeasure': 1, 'occurrence': 2, 'time': 2}


def test_resolved_origins_do_not_leak_into_later_missing_target():
    doc = raw_score([measure(beat(duration=(1, 4), slide='shift'), beat(fret=7, duration=(1, 4)),
                             beat(fret=12, duration=(1, 2), hp=True))])
    e = failure(doc)
    assert e.source_value['reason'] == 'end_of_score'
    assert e.source_value['linkCount'] == 1
    assert e.source_value['links'][0]['kind'] == 'hopo'
    assert e.source_value['links'][0]['sourceId'] == 'songsterr:0:0:0:2:0'


def test_muted_shift_to_muted_destination_has_separate_reason():
    doc = raw_score([measure(beat(fret=None, dead=True, slide='shift', duration=(1, 2)),
                             beat(fret=7, dead=True, duration=(1, 2)))])
    e = failure(doc)
    assert e.source_value['reason'] == 'muted_destination'
    assert e.source_value['links'][0]['kind'] == 'muted_shift'


def test_multistring_boundary_details_are_bounded():
    b = beat(slide='shift')
    b['notes'] = [{'string': s, 'fret': 3, 'slide': 'shift'} for s in range(6)]
    e = failure(raw_score([measure(b)]))
    assert e.source_value['linkCount'] == 6
    assert len(e.source_value['links']) == 4
    assert e.source_value['linksTruncated'] is True


def test_unresolved_hopo_still_keeps_located_arrangement_failure():
    doc = raw_score([measure(beat(hp=True, duration=(1, 2)),
                             beat(fret=None, dead=True, duration=(1, 2)))])
    report = new_report()
    diagnose_arrangements(doc, report)
    assert report['arrangementSummary']['scoreReady'] == 0
    finding = next(f for f in report['findings'] if f['feature'] == 'note.linked_destination')
    assert finding['ruleId'] == 'technique.linked_targets'
    assert finding['measure'] == 1 and finding['beat'] == 2
    assert finding['value']['reason'] == 'unpitched_hopo'
    assert finding['value']['links'][0]['sourceId'] == 'songsterr:0:0:0:0:0'
    assert finding['value']['destination']['sourceId'] == 'songsterr:0:0:0:1:0'
    assert finding['valueTruncated'] is False and finding['impact'] == 'blocking'


@pytest.mark.parametrize('source_id', ['', 'gp:0:0', 'songsterr:abc:1:2:3:4'])
def test_unrelated_source_ids_do_not_invent_songsterr_coordinates(source_id):
    assert coordinates(source_id) == {}


@pytest.mark.parametrize('slide', ['shift', 'legato'])
def test_valid_link_keeps_pitch_attack_and_timing(slide):
    doc = raw_score([measure(beat(fret=3, duration=(1, 2), slide=slide), beat(fret=7, duration=(1, 2)))])
    notes = render(parse(doc))['tracks'][0]['notes']
    assert [(n['f'], n['t'], n['sus']) for n in notes] == [(3, 0, 1), (7, 1, 1)]
    assert notes[0]['sl'] == 7
    assert bool(notes[0].get('ln')) == (slide == 'legato')
    assert not any('diagnostic' in k for n in notes for k in n)


REFERENCE = json.loads((Path(__file__).parent / 'fixtures/songsterr_linked_target_reference.json').read_text())


@pytest.mark.parametrize('case', REFERENCE['cases'], ids=lambda row: row['id'])
def test_native_synthesis_fallback_does_not_grant_a_pitched_game_target(case):
    doc = case['source']
    before = deepcopy(doc)
    raw_target = doc['parts'][0]['measures'][0]['voices'][0]['beats'][-1]['notes'][0]
    for profile in case['profiles']:
        assert profile['profile'] in {'authored', 'player-defaults'}
        for stage in ('authored', 'final'):
            target = profile[stage][-1]
            if case['target'] == 'unpitched':
                assert 'fret' not in raw_target and raw_target['dead']
                assert target['fret'] == 0 and 'sourceFret' not in target
            else:
                assert target['fret'] == target['sourceFret'] == raw_target['fret']
    if case['target'] == 'unpitched':
        performance = render(parse(doc))
        notes = performance['tracks'][0]['notes']
        assert len(notes) == 2 and notes[1]['f'] == 127 and notes[1]['mt']
        assert 'sl' not in notes[0] and 'ln' not in notes[0]
        assert performance['undefinedSlideEvidence'][0]['target']['fret'] is None
        oracle = expected(songsterr(doc), {'offset': 0, 'scale': 1})
        assert performance['undefinedSlideEvidence'] == oracle['undefined_slides']
    else:
        performance = render(parse(doc))
        notes = performance['tracks'][0]['notes']
        assert len(notes) == 2
        assert notes[0]['sl'] == notes[1]['f'] == raw_target['fret']
        assert notes[1]['t'] == (1 if case['tied'] else .5)
        assert notes[0]['sus'] == notes[1]['t']
        assert len(notes[0]['source_ids']) == (2 if case['tied'] else 1)
    assert doc == before


def test_reference_identity_is_pinned():
    path = Path(__file__).parents[1] / 'tools/songsterr_compatibility/reference-manifest.json'
    assert REFERENCE['referenceSha256'] == json.loads(path.read_text())['sha256']
    assert len(REFERENCE['cases']) == 24

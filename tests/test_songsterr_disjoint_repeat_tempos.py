from copy import deepcopy
import json
from pathlib import Path

import pytest

from feedback_converter.song_import.audio import ImportFailure
from feedback_converter.song_import.synchronization import align_from_songsterr, map_source_time
from feedback_converter.song_import.verification import Check
from feedback_converter.song_import.verify_source import songsterr
from feedback_converter.song_import.verify_synchronization import verify_source_timing
from test_song_import_score import raw_score, import_json

FIXTURE = json.loads((Path(__file__).parent / 'fixtures/songsterr_disjoint_repeat_tempo_reference.json').read_text())
META = {'songId': '123', 'revisionId': '456', 'approval': 'approved'}
VIDEO = 'abcdefghijk'


def document(case):
    doc = raw_score([])
    doc['parts'][0] = deepcopy(case['input'])
    doc['tracks'][0].update(instrumentId=case['input']['instrumentId'], tuning=case['input']['tuning'])
    return doc


def timing(case):
    return {'version': 1, 'source': 'songsterr-video-points', 'songId': '123', 'revisionId': '456',
            'status': 'done', 'feature': None, 'videoId': VIDEO, 'points': case['recording']}


def align(performance, case):
    return align_from_songsterr(performance, {'source': {'kind': 'youtube', 'videoId': VIDEO},
                               'duration': case['recording'][-1] + 1}, timing(case), META)


def independent(doc, alignment):
    check = Check()
    recipe = {'audioSource': {'kind': 'youtube', 'videoId': VIDEO}, 'alignment': alignment}
    verify_source_timing(songsterr(doc), alignment, recipe, alignment['sourceTiming'], check)
    return check


@pytest.mark.parametrize('case', FIXTURE['cases'], ids=lambda c: c['id'])
def test_constant_repeats_with_external_tempos_match_native_video_clock(tmp_path, case):
    assert FIXTURE['reference'] == [
        {'id': 'vendor', 'sha256': '1d2186332ee71014decea7e97c96bb4c98b9a46d9be32b89e2711c07e1b867d2'},
        {'id': 'common', 'sha256': '8b9267cd39f7f3b0511bade44de01cf3fe7c8025d5a7d534f8a6de448c940e17'}]
    doc = document(case)
    original = deepcopy(doc)
    performance = import_json(tmp_path, doc)
    measures = performance['scoreTimeline']['measures']
    assert [m['writtenIndex'] for m in measures] == case['order']
    assert [m['start'] for m in measures] + [performance['duration']] == pytest.approx(case['boundaries'], abs=1e-9)
    alignment = align(performance, case)
    assert alignment['provenance']['repeatTempoPolicy'] == 'constant-repeat-with-external-tempos-v1'
    for point in case['samples']:
        assert map_source_time(alignment, point['score']) == pytest.approx(point['audio'], abs=.00000051)
    assert not independent(doc, alignment).errors
    assert doc == original


@pytest.mark.parametrize('fault', ['no_source', 'identity', 'bar_order', 'bar_time', 'tempo_time', 'tempo_bpm', 'tempo_missing'])
def test_new_allowance_requires_retained_identity_order_and_exact_clock(tmp_path, fault):
    case = FIXTURE['cases'][0]
    performance = import_json(tmp_path, document(case))
    if fault == 'no_source': performance.pop('sourceScore')
    elif fault == 'identity': performance['sourceScore']['document']['revisionId'] = 999
    elif fault == 'bar_order': performance['scoreTimeline']['measures'][2]['writtenIndex'] = 0
    elif fault == 'bar_time': performance['scoreTimeline']['measures'][2]['end'] += .01
    elif fault == 'tempo_time': performance['scoreTimeline']['tempoPoints'][1]['time'] += .01
    elif fault == 'tempo_bpm': performance['scoreTimeline']['tempoPoints'][1]['bpm'] += 1
    elif fault == 'tempo_missing': performance['scoreTimeline']['tempoPoints'].pop()
    with pytest.raises(ImportFailure) as error:
        align(performance, case)
    assert error.value.diagnostics['sourceSyncReason'] == 'unverified_repeat_tempo_inheritance'


@pytest.mark.parametrize('position', [0, 480, 1920])
def test_tempo_event_inside_repeat_stays_blocked_by_both_checks(tmp_path, position):
    case = FIXTURE['cases'][0]
    doc = document(case)
    good = align(import_json(tmp_path, doc), case)
    doc['parts'][0]['automations']['tempo'].append({'measure': 2, 'position': position, 'bpm': 160, 'type': 4})
    with pytest.raises(ImportFailure):
        align(import_json(tmp_path, doc), case)
    check = independent(doc, good)
    assert any('not qualified' in e['message'] for e in check.errors)


@pytest.mark.parametrize('fault', ['missing_policy', 'wrong_policy', 'changed_anchor', 'changed_recording'])
def test_archive_checker_rejects_timing_receipt_mutations(tmp_path, fault):
    case = FIXTURE['cases'][0]
    doc = document(case)
    alignment = align(import_json(tmp_path, doc), case)
    if fault == 'missing_policy': alignment['provenance'].pop('repeatTempoPolicy')
    elif fault == 'wrong_policy': alignment['provenance']['repeatTempoPolicy'] = 'unchecked'
    elif fault == 'changed_anchor': alignment['anchors'][2]['score'] += .01
    elif fault == 'changed_recording': alignment['sourceTiming']['points'][2] += .01
    assert independent(doc, alignment).errors


@pytest.mark.parametrize('nested', [False, True])
def test_unqualified_navigation_cannot_bypass_clock_proof_with_flags(tmp_path, nested):
    case = FIXTURE['cases'][0]
    doc = document(case)
    bars = doc['parts'][0]['measures']
    if nested:
        bars[1]['repeat'] = 2
        bars[0]['repeatStart'] = True
    else:
        bars[2]['alternateEnding'] = [1]
        bars[3]['alternateEnding'] = [2]
    performance = import_json(tmp_path, doc)
    performance['scoreTimeline']['maxRepeatDepth'] = 1
    performance['scoreTimeline']['hasAlternateEndings'] = False
    with pytest.raises(ImportFailure) as error:
        align(performance, case)
    assert error.value.diagnostics['sourceSyncReason'] == 'unverified_repeat_tempo_inheritance'

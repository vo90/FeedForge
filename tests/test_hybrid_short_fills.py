from copy import deepcopy
import json
from zipfile import ZipFile

import pytest
from test_hybrid_gap_variants import source, variants
from test_song_import_score import beat, measure, raw_score
from test_songsterr_hybrid_lead import build, rest


def test_short_fill_preserves_complete_gestures_and_original_threshold():
    parts = source([(0, 1), (1, 2), (2, 3), (3, 5)])
    before = deepcopy(parts)
    assert variants(parts, [(1, 3.75)])[0] == []
    result, info = variants(parts, [(1, 3.75)], min_active_quarters=2)
    assert result and info['minActiveQuarterBeats'] == 2
    assert all({r['index'] for r in p['events']} == {1, 2} for p in result)
    assert parts == before


@pytest.mark.parametrize('spans', [[(0, 1), (1, 1.5), (2, 2.5), (3, 5)],
                                  [(0, 1), (1, 3), (3, 5)]])
def test_short_fill_still_rejects_sparse_or_single_attack(spans):
    assert not variants(source(spans), [(1, 3)], min_active_quarters=2)[0]


def test_short_fill_cannot_split_connected_slide_or_written_slot():
    parts = source([(0, 1), (1, 2), (2, 3), (3, 5)])
    for r in parts[1][1:]:
        r['start'], r['end'] = 1, 5
    assert not variants(parts, [(1, 3.75)], min_active_quarters=2)[0]
    parts = source([(0, 1), (1, 2), (2, 3), (3, 5)])
    parts[2]['beats'][1]['end'] = 5
    assert not variants(parts, [(1, 3.75)], min_active_quarters=2)[0]


@pytest.mark.parametrize('budget', [{'max_variants': 0}, {'max_window_checks': 0}])
def test_short_fill_respects_existing_budgets(budget):
    parts = source([(0, 1), (1, 2), (2, 3), (3, 5)])
    result, report = variants(parts, [(1, 3.75)], min_active_quarters=2, **budget)
    assert not result and report['budgetLimited']


def test_new_contract_adds_rhythm_in_rest_without_replacing_lead(tmp_path):
    # Guarded gap leaves two full quarter-note attacks from one longer phrase.
    doc = raw_score([measure(beat(12, duration=(1, 8)), rest((1, 2)), rest((1, 4)), rest((1, 8))),
                     measure(beat(15))])
    doc['tracks'].append({**deepcopy(doc['tracks'][0]), 'id': 1, 'name': 'Rhythm Guitar'})
    doc['parts'].append({'measures': [measure(*(beat(3 + i % 2, duration=(1, 4)) for i in range(4))), measure(rest())]})
    charts = []
    for contract in (112, 113):
        folder = tmp_path / str(contract); folder.mkdir()
        *_, archive, report = build(folder, doc, contract=contract,
                                    overrides={'mainTrackId': '0', 'roles': {'1': 'accompaniment'}})
        assert report['status'] == 'passed', report
        with ZipFile(archive) as z:
            receipt = json.loads(z.read('import/hybrid-lead.json'))
            hybrid = json.loads(z.read('arrangements/' + receipt['arrangementId'] + '.json'))
            main = json.loads(z.read('arrangements/0.json'))
            assert not receipt['removedMain']
            assert all(n in hybrid['notes'] for n in main['notes'])
            charts.append(receipt)
    assert not charts[0]['passages']
    assert charts[1]['passages']
    assert all(p['priority'] == 'accompaniment' for p in charts[1]['passages'])


def test_missing_ranges_exclude_selected_events_and_keep_disjoint_omissions():
    from feedback_converter.song_import.hybrid_reporting import missing_ranges
    limitation = {'trackId': '1', 'start': 0, 'end': 10, 'reason': 'conflicting_primary'}
    refs = [{'kind': 'notes', 'index': i} for i in range(3)]
    receipt = {'obligations': [{**limitation, 'events': refs}], 'coverage': {'events': [
        {**r, 'trackId': '1', 'status': 'included' if i == 1 else 'unused_accompaniment',
         'recordingStart': i * 3, 'recordingEnd': i * 3 + 2} for i, r in enumerate(refs)]}}
    assert missing_ranges(receipt, limitation) == [{'start': 0, 'end': 2}, {'start': 6, 'end': 8}]

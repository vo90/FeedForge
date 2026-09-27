"""Primary search must retain whole musical obligations through refinements."""
from copy import deepcopy

import pytest

from feedback_converter.song_import import hybrid_regional as regional
from feedback_converter.song_import.hybrid_context import Clock


def event(index, onset, end, *, start=None):
    return {'kind': 'notes', 'index': index, 'start': onset if start is None else start, 'end': end,
            'available': True, 'sourceIds': [f'n:{index}'], 'occurrences': [1],
            'notes': [{'t': onset / 2, 'sus': (end - onset) / 2, 's': index % 6, 'f': 7}]}


def evidence(ident, track, start, end, kind='named_soloist', **fields):
    return {'id': ident, 'trackId': track, 'start': start, 'end': end, 'evidence': kind,
            'confidence': 'high', 'priority': 'solo', 'score': 3 if kind == 'named_soloist' else 2, **fields}


def clock():
    return Clock({'tempoPoints': [{'quarter': 0, 'time': 0, 'bpm': 120}]})


def test_complete_event_set_gets_whole_credit_despite_shorter_attack_window():
    item = evidence('solo', '1', 0, 8)
    rows = [event(0, 0, 8)]
    passage = regional._passage(item, rows, clock(), 0, 4)
    assert regional._score(passage, item, '0', [], frozenset({('notes', 0)}))[0] == 1


@pytest.mark.parametrize('budget', ['MAX_PRIMARY_EDGES', 'MAX_PRIMARY_CANDIDATES'])
def test_primary_budget_keeps_late_nonconflicting_named_solos(monkeypatch, budget):
    items = [evidence(str(i), str(i + 1), i * 4, i * 4 + 2) for i in range(3)]
    candidates = [regional._passage(c, [event(0, c['start'], c['end'])], clock(), c['start'], c['end']) for c in items]
    monkeypatch.setattr(regional, budget, 0)
    selected, limited = regional._select(candidates, items, '0', [])
    assert limited
    assert [p['trackId'] for p in selected] == ['1', '2', '3']


def test_named_semantics_survive_identical_dedicated_candidate_deduplication(tmp_path, monkeypatch):
    from test_hybrid_lead_priority import solo_song
    from test_songsterr_hybrid_lead import prepared
    from feedback_converter.song_import import hybrid_selection
    _, performance, options = prepared(tmp_path, solo_song(), overrides={'mainTrackId': '0'})
    candidates = [evidence('d', '1', 4, 12, 'dedicated_solo'), evidence('n', '1', 4, 12)]
    monkeypatch.setattr(hybrid_selection, 'regional_candidates', lambda *args, **kwargs: deepcopy(candidates))
    result = regional.backbone(performance, options, '0', {'offset': 0, 'scale': 1}, 8)
    assert result['primaryEpisodes'][0]['evidence'] == 'named_soloist'
    assert len(result['primaryEpisodes']) == 1


def test_primary_source_polyphony_keeps_every_owned_attack_and_complete_tail():
    item = evidence('solo', '1', 4, 10, ownedStart=4, ownedEnd=8)
    rows = [event(0, 4, 10), event(1, 5, 7), event(2, 8.5, 9)]
    passage = regional._passage(item, rows, clock(), item['ownedStart'], item['ownedEnd'])
    assert [r['index'] for r in passage['events']] == [0, 1]
    assert (passage['start'], passage['end']) == (4, 10)
    assert (passage['ownedStart'], passage['ownedEnd']) == (4, 8)


def test_shared_technique_closure_keeps_the_pickup_before_ownership():
    item = evidence('solo', '1', 4, 8)
    rows = [event(0, 3.75, 8, start=3.75), event(1, 4, 8, start=3.75), event(2, 8.5, 9)]
    passage = regional._passage(item, rows, clock(), 4, 8)
    assert [r['index'] for r in passage['events']] == [0, 1]
    assert passage['start'] == 3.75


@pytest.mark.parametrize('bounded', [False, True])
def test_labelled_solo_preserves_whole_opening_over_longer_inferred_chorus_tail(monkeypatch, bounded):
    chorus = evidence('chorus', 'backing', 0, 10, 'regional_lead', ownedStart=0, ownedEnd=8)
    solo = evidence('solo', 'lead', 7.5, 16, 'regional_lead', ownedStart=8, ownedEnd=16, labelledSolo=True)
    chorus_rows = [event(0, 0, 6), event(1, 6, 10)]
    # The pickup and first solo attack are one connected bend gesture.
    solo_rows = [event(0, 7.5, 10), event(1, 8, 10, start=7.5), event(2, 10, 16)]
    complete_chorus = regional._passage(chorus, chorus_rows, clock(), 0, 8)
    complete_solo = regional._passage(solo, solo_rows, clock(), 8, 16)
    late_solo = regional._passage(solo, solo_rows, clock(), 10, 16)
    required = {'chorus': frozenset((r['kind'], r['index']) for r in chorus_rows),
                'solo': frozenset((r['kind'], r['index']) for r in solo_rows)}
    if bounded:
        monkeypatch.setattr(regional, 'MAX_PRIMARY_EDGES', 0)
    selected, _ = regional._select([complete_chorus, complete_solo, late_solo], [chorus, solo], 'lead', [], required)
    assert [p['trackId'] for p in selected] == ['lead']
    assert selected[0]['start'] == 7.5
    assert {r['index'] for r in selected[0]['events']} == {0, 1, 2}


def test_zero_optional_variant_budget_keeps_full_known_primary_candidates(tmp_path, monkeypatch):
    from test_hybrid_lead_priority import solo_song
    from test_songsterr_hybrid_lead import prepared
    from feedback_converter.song_import import hybrid_selection
    _, performance, options = prepared(tmp_path, solo_song(), overrides={'mainTrackId': '0'})
    items = [evidence('first', '1', 4, 8), evidence('later', '2', 8, 12)]
    monkeypatch.setattr(hybrid_selection, 'regional_candidates', lambda *args, **kwargs: deepcopy(items))
    monkeypatch.setattr(regional, 'MAX_PRIMARY_VARIANTS', 0)
    result = regional.backbone(performance, options, '0', {'offset': 0, 'scale': 1}, 8)
    assert [(p['trackId'], p['start']) for p in result['primaryEpisodes']] == [('1', 4), ('2', 8)]


def test_overlapping_solos_keep_both_nonconflicting_unique_fragments(tmp_path, monkeypatch):
    from test_hybrid_lead_priority import solo_song
    from test_songsterr_hybrid_lead import prepared
    from test_song_import_score import beat, measure
    from feedback_converter.song_import import hybrid_selection
    doc = solo_song()
    doc['parts'][2]['measures'][3] = measure(beat(10))
    _, performance, options = prepared(tmp_path, doc, overrides={'mainTrackId': '0'})
    items = [evidence('first', '1', 4, 12), evidence('second', '2', 8, 16)]
    monkeypatch.setattr(hybrid_selection, 'regional_candidates', lambda *args, **kwargs: deepcopy(items))
    result = regional.backbone(performance, options, '0', {'offset': 0, 'scale': 1}, 8)
    episodes = result['primaryEpisodes']
    assert any(p['trackId'] == '1' and p['start'] <= 4 and p['end'] >= 8 for p in episodes)
    assert any(p['trackId'] == '2' and p['start'] <= 12 and p['end'] >= 16 for p in episodes)


def test_removing_long_main_gesture_keeps_independent_retained_main_notation():
    from feedback_converter.song_import.hybrid_materialize import notation_for
    held_id, retained_id = 'songsterr:0:0:0:0:0', 'songsterr:0:0:1:1:0'
    held = {'t': 0, 'duration_seconds': 4, 'source_id': held_id.rsplit(':', 1)[0],
            'notes': [{'source_id': held_id}]}
    retained = {'t': .5, 'duration_seconds': .5, 'source_id': retained_id.rsplit(':', 1)[0],
                'notes': [{'source_id': retained_id}]}
    notation = {'staves': [{'label': 'Lead'}], 'measures': [{'idx': 1, 'staves': {'staff': {'voices': [
        {'v': 0, 'beats': [held]}, {'v': 1, 'beats': [retained]}]}}}]}
    plan = {'mainTrackId': '0', 'passages': [],
            'mainEvents': [{'kind': 'notes', 'index': 1, 'sourceIds': [retained_id], 'occurrences': [1]}],
            'removedMain': [{'kind': 'notes', 'index': 0, 'sourceIds': [held_id], 'occurrences': [1],
                             'recordingStart': 0, 'recordingEnd': 4}]}
    result, _ = notation_for(plan, {'0': {'notation': notation}})
    beats = [b for m in result['measures'] for staff in m['staves'].values() for voice in staff['voices'] for b in voice['beats']]
    assert retained in beats
    assert held not in beats


def test_unsupported_other_voice_does_not_erase_an_independent_supported_solo(tmp_path):
    from test_hybrid_lead_priority import solo_song
    from test_songsterr_hybrid_lead import build, rest
    from test_song_import_score import beat, measure
    doc = solo_song()
    doc['parts'][1]['measures'][2] = measure(beat(12, tie=True))
    doc['parts'][1]['measures'][1]['voices'].append({
        'beats': [rest((1, 4)), beat(30, string=2, duration=(1, 4)), rest((1, 2))]})
    *_, report = build(tmp_path, doc)
    assert report['status'] == 'passed', report


def test_shared_ensemble_identity_cannot_hide_named_clean_guitar_solo(tmp_path):
    import json
    from zipfile import ZipFile
    from test_hybrid_lead_priority import solo_song
    from test_songsterr_hybrid_lead import build
    from test_song_import_score import beat, measure
    doc = solo_song()
    for track, name in zip(doc['tracks'], ['Kirk Hammett | Lead Guitar',
                                          'James Hetfield | Clean Guitar',
                                          'Kirk & James | Extra Guitars']):
        track['name'] = name
    doc['parts'][0]['measures'][1] = measure(beat(3))
    for part in doc['parts']:
        part['measures'][1]['marker'] = {'text': 'Solo (James)', 'width': 40}
        part['measures'][3]['marker'] = {'text': 'Verse', 'width': 40}
    *_, archive, report = build(tmp_path, doc)
    assert report['status'] == 'passed', report
    with ZipFile(archive) as package:
        receipt = json.loads(package.read('import/hybrid-lead.json'))
    solo = next(p for p in receipt['primaryEpisodes'] if p['evidence'] == 'named_soloist')
    assert solo['trackId'] == '1'
    assert (solo['start'], solo['end']) == (4, 12)
    assert receipt['coverage']['status'] == 'complete'


def test_named_obligation_footprint_includes_owned_ghost_pickup_before_core(tmp_path):
    import json
    from zipfile import ZipFile
    from test_hybrid_lead_priority import solo_song
    from test_songsterr_hybrid_lead import build
    from test_song_import_score import beat, measure
    doc = solo_song()
    doc['tracks'][0]['name'] = 'Kirk Hammett | Lead Guitar'
    doc['tracks'][1]['name'] = 'James Hetfield | Lead Guitar'
    doc['parts'][1]['measures'][1] = measure(beat(12, duration=(1, 4), ghost=True),
                                           beat(14, duration=(3, 4)))
    for part in doc['parts']:
        part['measures'][1]['marker'] = {'text': 'Solo (James)', 'width': 40}
        part['measures'][2]['marker'] = {'text': 'Verse', 'width': 40}
    *_, archive, report = build(tmp_path, doc)
    assert report['status'] == 'passed', report
    with ZipFile(archive) as package:
        receipt = json.loads(package.read('import/hybrid-lead.json'))
    obligation = next(p for p in receipt['obligations'] if p['evidence'] == 'named_soloist')
    assert obligation['start'] == 4
    assert obligation['end'] == 8
    assert len(obligation['events']) == 2


@pytest.mark.parametrize('edge_budget', [None, 0])
def test_adjacent_named_sections_keep_same_source_polyphony_under_a_held_tail(tmp_path, monkeypatch, edge_budget):
    import json
    from zipfile import ZipFile
    from test_hybrid_lead_priority import solo_song
    from test_songsterr_hybrid_lead import build, rest
    from test_song_import_score import beat, measure
    if edge_budget is not None:
        monkeypatch.setattr(regional, 'MAX_PRIMARY_EDGES', edge_budget)
    doc = solo_song()
    doc['tracks'][0]['name'] = 'Kirk Hammett | Lead Guitar'
    doc['tracks'][1]['name'] = 'James Hetfield | Lead Guitar'
    doc['parts'][1]['measures'][1] = measure(beat(12))
    doc['parts'][1]['measures'][2] = measure(beat(12, tie=True))
    doc['parts'][1]['measures'][2]['voices'].append({
        'beats': [beat(14, string=2, duration=(1, 2)), rest((1, 2))]})
    for part in doc['parts']:
        part['measures'][1]['marker'] = {'text': 'Solo I (James)', 'width': 40}
        part['measures'][2]['marker'] = {'text': 'Solo II (James)', 'width': 40}
        part['measures'][3]['marker'] = {'text': 'Verse', 'width': 40}
    *_, archive, report = build(tmp_path, doc)
    assert report['status'] == 'passed', report
    with ZipFile(archive) as package:
        receipt = json.loads(package.read('import/hybrid-lead.json'))
    copied = [(p['trackId'], e['kind'], e['index']) for p in receipt['passages'] for e in p['events']]
    assert len(copied) == len(set(copied)), 'Complete source components must be copied only once.'
    chosen = set(copied)
    assert ('1', 'notes', 0) in chosen
    assert ('1', 'notes', 1) in chosen


def test_same_source_composite_cannot_bridge_an_unselected_source_event():
    first = evidence('a', '1', 0, 6, ownedStart=0, ownedEnd=4)
    second = evidence('b', '1', 4, 7, ownedStart=4, ownedEnd=8)
    rows = [event(0, 0, 6), event(1, 2, 3), event(2, 4, 7)]
    a = regional._passage(first, [rows[0]], clock(), 0, 4)
    b = regional._passage(second, [rows[2]], clock(), 4, 8)
    assert regional._coalesced([a, b], {'1': rows}, clock(), {'a': first, 'b': second}, '0', [], {}) == []


def test_extra_lead_does_not_reserve_a_gap_before_optional_optimization(tmp_path, monkeypatch):
    from test_songsterr_hybrid_lead import song, prepared
    from feedback_converter.song_import import hybrid_selection
    from feedback_converter.song_import.hybrid_lead import plan
    doc = song()
    doc['tracks'][1]['name'] = 'Additional Lead Guitar'
    _, performance, options = prepared(tmp_path, doc, overrides={'mainTrackId': '0'})
    monkeypatch.setattr(hybrid_selection, 'regional_candidates', lambda *args, **kwargs: [])
    primary = regional.backbone(performance, options, '0', {'offset': 0, 'scale': 1}, 8)
    assert primary['passages'] == []
    result = plan(performance, options, '0', {'offset': 0, 'scale': 1}, 8)
    assert any(p['trackId'] == '1' and p['priority'] == 'accompaniment' for p in result['passages'])
    assert result['selection']['algorithm'] == 'bounded-whole-phrase-dag-v2'


def test_named_half_beat_solo_is_not_rejected_by_optional_fill_costs(tmp_path, monkeypatch):
    from test_songsterr_hybrid_lead import song, prepared, rest
    from test_song_import_score import beat, measure
    from feedback_converter.song_import import hybrid_selection
    from feedback_converter.song_import.hybrid_lead import plan
    doc = song()
    doc['parts'][1]['measures'][1] = measure(beat(12, duration=(1, 8)), rest((7, 8)))
    _, performance, options = prepared(tmp_path, doc, overrides={'mainTrackId': '0'})
    mandatory = [evidence('tiny-solo', '1', 4, 4.5)]
    monkeypatch.setattr(hybrid_selection, 'regional_candidates', lambda *args, **kwargs: deepcopy(mandatory))
    result = plan(performance, options, '0', {'offset': 0, 'scale': 1}, 8)
    solo = next(p for p in result['passages'] if p['trackId'] == '1')
    assert solo['evidence'] == 'named_soloist'
    assert solo['start'] == 4 and solo['end'] == 4.5
    assert solo['priority'] == 'solo'


def test_optional_lead_phrase_cannot_be_sliced_at_a_base_gap(tmp_path, monkeypatch):
    from test_songsterr_hybrid_lead import song, prepared, rest
    from test_song_import_score import beat, measure
    from feedback_converter.song_import import hybrid_selection
    from feedback_converter.song_import.hybrid_lead import plan
    doc = song()
    doc['tracks'][1]['name'] = 'Additional Lead Guitar'
    doc['parts'][1]['measures'] = [measure(beat(5, duration=(1, 4)), beat(7, duration=(1, 4)), rest((1, 2))),
                                    measure(rest()), measure(rest()), measure(rest())]
    _, performance, options = prepared(tmp_path, doc, overrides={'mainTrackId': '0'})
    monkeypatch.setattr(hybrid_selection, 'regional_candidates', lambda *args, **kwargs: [])
    result = plan(performance, options, '0', {'offset': 0, 'scale': 1}, 8)
    assert not any(p['trackId'] == '1' for p in result['passages'])
    assert any(p['trackId'] == '1' and p['start'] == 0 and p['end'] == 2
               and p['reason'] == 'crosses_primary_material' for p in result['candidateDecisions'])

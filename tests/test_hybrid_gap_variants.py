"""Long safe source portions survive small boundary conflicts without clipping."""
from copy import deepcopy

import pytest

from feedback_converter.song_import.hybrid_variants import generate
from feedback_converter.song_import.hybrid_search import select_passages


class QuarterClock:
    def quarter(self, seconds):
        return seconds


def source(spans):
    rows = [{'kind': 'notes', 'index': i, 'start': a, 'end': b,
             'sourceIds': [str(i)], 'occurrences': [1],
             'notes': [{'t': a, 'sus': b - a, 's': 0, 'f': 3 + i % 3}]}
            for i, (a, b) in enumerate(spans)]
    context = {'beats': [{'start': a, 'end': b, 'rest': False} for a, b in spans]}
    timeline = {'measures': [{'quarter': q, 'quarters': 4} for q in range(0, 40, 4)]}
    parent = {'trackId': 'rhythm', 'start': min(a for a, _ in spans), 'end': max(b for _, b in spans),
              'boundaryQuarters': [min(a for a, _ in spans), max(b for _, b in spans)],
              'boundaries': ['section', 'section'],
              'events': [{k: deepcopy(row[k]) for k in ('kind', 'index', 'sourceIds', 'occurrences')} for row in rows]}
    return parent, rows, context, timeline


def variants(parts, gaps, **budget):
    parent, rows, context, timeline = parts
    return generate([parent], rows, context, timeline, QuarterClock(), gaps, **budget)


def test_long_chorus_survives_guard_at_its_first_chord():
    parts = source([(q / 2, q / 2 + .5) for q in range(8, 56)])
    candidates, info = variants(parts, [(4.25, 28)])
    chosen = select_passages(candidates, lambda *_: .25)
    best = candidates[chosen['indices'][0]]
    assert (best['start'], best['end']) == (4.5, 28)
    assert {r['index'] for r in best['events']} == set(range(1, 48))
    assert best['boundaries'] == ['gesture', 'section']
    assert best['variant']['parentStart'] == 4
    assert best['variant']['parentEnd'] == 28
    assert info['candidateCount'] <= 4
    assert not info['budgetLimited']


def test_bridge_prefix_stops_before_protected_solo_pickup():
    parts = source([(q / 2, q / 2 + .5) for q in range(0, 64)])
    candidates, _ = variants(parts, [(0, 31.25)])
    assert candidates
    assert all(p['end'] <= 31.25 for p in candidates)
    best = max(candidates, key=lambda p: p['activeQuarterBeats'])
    assert (best['start'], best['end']) == (0, 31)
    assert {r['index'] for r in best['events']} == set(range(62))
    assert all(r['index'] not in {62, 63} for p in candidates for r in p['events'])


def test_syncopated_ties_crossing_every_bar_still_have_complete_group_cuts():
    # Each tied event crosses an integer bar boundary; legal cuts are offbeat.
    parts = source([(.5 + q, 1.5 + q) for q in range(24)])
    candidates, _ = variants(parts, [(.75, 24.5)])
    best = max(candidates, key=lambda p: p['activeQuarterBeats'])
    assert (best['start'], best['end']) == (1.5, 24.5)
    assert best['boundaries'] == ['gesture', 'section']
    assert {r['index'] for r in best['events']} == set(range(1, 24))


def test_entire_pull_off_slide_group_is_excluded_when_guard_crosses_it():
    parts = source([(q, q + 1) for q in range(16)])
    parent, rows, context, _ = parts
    for i in (3, 4, 5):
        rows[i]['start'], rows[i]['end'] = 3, 6
    rows[3]['notes'][0]['ln'] = True
    rows[4]['notes'][0].update(po=True, sl=7, ln=True)
    rows[5]['notes'][0]['po'] = True
    before = deepcopy(parts)
    candidates, _ = variants(parts, [(4.25, 16)])
    assert candidates and all(p['start'] >= 6 for p in candidates)
    assert all(not {3, 4, 5}.intersection(r['index'] for r in p['events']) for p in candidates)
    assert parts == before, 'Generating variants must not edit any source event or footprint.'


def test_written_slot_stays_hard_even_when_sound_is_staccato():
    parts = source([(q, q + .1) for q in range(16)])
    for beat in parts[2]['beats']:
        beat['end'] = beat['start'] + 1
    parts[2]['beats'][4].update(start=4, end=8)
    candidates, _ = variants(parts, [(4.25, 15.1)])
    assert candidates
    assert min(p['start'] for p in candidates) >= 8


@pytest.mark.parametrize('spans,gap', [
    ([(0, .5), (1, 1.5), (2, 2.5)], (.25, 2.5)),
    ([(0, 1), (4, 4.5), (8, 8.5)], (1.25, 8.5)),
    ([(0, 1), (4, 12)], (1.25, 12)),
])
def test_tiny_sparse_or_single_attack_fragments_do_not_become_variants(spans, gap):
    candidates, _ = variants(source(spans), [gap])
    assert not candidates


def test_all_members_inside_a_safe_variant_are_kept_not_a_dense_subset():
    parts = source([(q / 2, q / 2 + .5) for q in range(32)])
    candidates, _ = variants(parts, [(1.25, 14.75)])
    for p in candidates:
        expected = {(r['kind'], r['index']) for r in parts[1]
                    if p['boundaryQuarters'][0] <= r['start'] and r['end'] <= p['boundaryQuarters'][1]}
        assert {(r['kind'], r['index']) for r in p['events']} == expected


def test_zero_and_small_generation_budgets_are_explicit_and_repeatable():
    parts = source([(q / 2, q / 2 + .5) for q in range(64)])
    for budget in ({'max_variants': 0}, {'max_variants': 1}, {'max_window_checks': 0}):
        first = variants(parts, [(1.25, 30.75)], **budget)
        assert first == variants(parts, [(1.25, 30.75)], **budget)
        assert first[1]['budgetLimited']
        if budget.get('max_variants') == 0 or budget.get('max_window_checks') == 0:
            assert first[0] == []


def test_boundary_quality_cost_is_paid_by_exact_and_bounded_search():
    parts = source([(q / 2, q / 2 + .5) for q in range(32)])
    candidates, _ = variants(parts, [(1.25, 14.75)])
    one = candidates[0]
    other = {**deepcopy(one), 'trackId': 'z-cleaner', 'boundaryCostQuarterBeats': 0}
    for budget in ({}, {'max_states': 0}, {'max_operations': 0}):
        result = select_passages([one, other], lambda *_: .25, **budget)
        assert result['indices'] == [1]
        assert result['boundaryCostQuarterBeats'] == 0
        assert result['utilityQuarterBeats'] == other['activeQuarterBeats']


def test_variant_generation_never_changes_single_track_planning(tmp_path):
    from test_songsterr_hybrid_lead import song, prepared
    from feedback_converter.song_import.hybrid_lead import plan
    doc = song()
    doc['tracks'] = doc['tracks'][:1]
    doc['parts'] = doc['parts'][:1]
    _, performance, options = prepared(tmp_path, doc)
    result = plan(performance, options, '0', {'status': 'validated', 'offset': 0, 'scale': 1}, 8)
    assert not result['passages']
    assert result['selection']['variantGeneration']['candidateCount'] == 0
    assert len(result['mainEvents']) == len(performance['tracks'][0]['notes'])


@pytest.mark.parametrize('repeating', [False, True])
def test_gap_variant_materializes_exact_source_events_and_verifies(tmp_path, repeating):
    import json
    from zipfile import ZipFile
    from test_song_import_score import beat, measure, raw_score
    from test_songsterr_hybrid_lead import rest, build
    doc = raw_score([measure(beat(3)), measure(rest()), measure(rest()),
                     measure(rest((3, 4)), beat(12, duration=(1, 4)))])
    doc['tracks'].append({**deepcopy(doc['tracks'][0]), 'id': 1, 'name': 'Rhythm Guitar'})
    bars = []
    for bar in range(4):
        beats = [beat(3 + (0 if repeating else bar) + i % 2, duration=(1, 4)) for i in range(4)]
        for b in beats:
            b['notes'].append({'string': 1, 'fret': b['notes'][0]['fret'] + 2})
        bars.append(measure(*beats))
    doc['parts'].append({'measures': bars})
    *_, archive, report = build(tmp_path, doc, overrides={'mainTrackId': '0', 'roles': {'1': 'accompaniment'}})
    assert report['status'] == 'passed', report
    with ZipFile(archive) as z:
        receipt = json.loads(z.read('import/hybrid-lead.json'))
        selected = [p for p in receipt['passages'] if 'variant' in p]
        assert selected and all(p['trackId'] == '1' for p in selected)
        if repeating:
            assert any(p['variant'].get('parentPhraseCount', 0) > 1 for p in selected)
        assert min(p['start'] for p in selected) >= 4.25
        assert max(p['end'] for p in selected) <= 14.75
        original = json.loads(z.read('arrangements/1.json'))
        hybrid = json.loads(z.read(next(s for s in z.namelist() if s.startswith('arrangements/hybrid-'))))
        for passage in selected:
            for ref in passage['events']:
                event = original[ref['kind']][ref['index']]
                assert any({k: v for k, v in copied.items() if k != 'id'} == {k: v for k, v in event.items() if k != 'id'}
                           for copied in hybrid[ref['kind']])


def split_parents(parts):
    parent, rows, _, _ = parts
    return [{**deepcopy(parent), 'start': q, 'end': q + 4,
             'boundaryQuarters': [q, q + 4], 'boundaries': ['repeat', 'repeat'],
             'events': [ref for ref in parent['events'] if q <= rows[ref['index']]['start'] < q + 4]}
            for q in range(0, round(parent['end']), 4)]


def test_neighboring_repeat_fragments_combine_without_lowering_duration_floor():
    parts = source([(q / 2, q / 2 + .5) for q in range(16)])
    parents = split_parents(parts)
    result, info = generate(parents, *parts[1:], QuarterClock(), [(.5, 7.5)])
    best = max(result, key=lambda p: p['activeQuarterBeats'])
    assert (best['start'], best['end'], best['activeQuarterBeats']) == (.5, 7.5, 7)
    assert {r['index'] for r in best['events']} == set(range(1, 15))
    assert best['variant']['parentPhraseCount'] == 2
    assert not info['budgetLimited']
    assert all(p['activeQuarterBeats'] >= 4 for p in result)


@pytest.mark.parametrize('barrier', ['section', 'missing_parent', 'different_source'])
def test_repeat_union_never_crosses_an_unsafe_or_unrelated_parent(barrier):
    parts = source([(q / 2, q / 2 + .5) for q in range(24 if barrier == 'missing_parent' else 16)])
    parents = split_parents(parts)
    if barrier == 'section':
        parents[0]['boundaries'][1] = parents[1]['boundaries'][0] = 'section'
    elif barrier == 'missing_parent':
        parents.pop(1)
    else:
        parents[1]['trackId'] = 'other'
    result, _ = generate(parents, *parts[1:], QuarterClock(), [(.5, parts[0]['end'] - .5)])
    assert result == []


def test_repeat_union_can_include_a_whole_eligible_middle_bar():
    parts = source([(q / 2, q / 2 + .5) for q in range(24)])
    parents = split_parents(parts)
    result, _ = generate([parents[0], parents[2]], *parts[1:], QuarterClock(), [(.5, 11.5)],
                         adjacent_parents=parents)
    best = max(result, key=lambda p: p['activeQuarterBeats'])
    assert (best['start'], best['end']) == (.5, 11.5)
    assert best['variant']['parentPhraseCount'] == 3


def test_repeat_union_budget_is_deterministic_and_never_bridges_protected_gaps():
    parts = source([(q / 2, q / 2 + .5) for q in range(24)])
    parents = split_parents(parts)
    gaps = [(.5, 5.5), (6.5, 11.5)]
    result, info = generate(parents, *parts[1:], QuarterClock(), gaps, max_variants=1)
    assert result and info['budgetLimited']
    assert all(any(a <= p['start'] and p['end'] <= b for a, b in gaps) for p in result)
    assert (result, info) == generate(parents, *parts[1:], QuarterClock(), gaps, max_variants=1)

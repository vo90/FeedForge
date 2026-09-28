"""An extra optional choice must not delete the plan it is refining."""
from copy import deepcopy

import pytest

from feedback_converter.song_import.hybrid_optional import refine_neighbors, references
from feedback_converter.song_import.hybrid_search import select_passages
from test_hybrid_gap_variants import QuarterClock, source


def fixture():
    result, sources = [], {}
    for tid, spans, cuts in [('a', [(q, q + 1) for q in range(16)], [(0, 16)]),
                             ('b', [(q, q + 1) for q in range(12, 32)], [(12, 24), (24, 32)])]:
        parent, rows, context, timeline = source(spans)
        parents = []
        for lo, hi in cuts:
            p = {**deepcopy(parent), 'trackId': tid, 'start': lo, 'end': hi,
                 'activeQuarterBeats': hi - lo, 'ghostOnly': False,
                 'boundaryQuarters': [lo, hi],
                 'events': [r for r in parent['events'] if lo <= rows[r['index']]['start'] < hi]}
            parents.append(p)
        result.extend(parents)
        sources[tid] = {'parents': parents, 'rows': rows, 'context': context, 'unsupported': []}
    return result, sources, timeline


def run_case(candidates=None, sources=None, timeline=None, guard=lambda *_: .25, **limits):
    if candidates is None:
        candidates, sources, timeline = fixture()
    before = select_passages(candidates, guard)
    settings = dict(validate=lambda *_: 'eligible_phrase', max_variants=4096,
                    max_window_checks=100000, max_candidates=20000, max_states=10000, max_operations=500000)
    settings.update(limits)
    expanded, after, info = refine_neighbors(candidates, before, sources, timeline, QuarterClock(), (0, 32), guard, **settings)
    return candidates, before, expanded, after, info


def test_suffix_continues_next_same_source_without_losing_prior_material():
    old, before, expanded, after, info = run_case()
    selected = [expanded[i] for i in after['indices']]
    assert references(old[i] for i in before['indices']) < references(selected)
    assert [(p['trackId'], p['start'], p['end']) for p in selected] == [('a', 0, 16), ('b', 17, 24), ('b', 24, 32)]
    assert info['accepted'] and not info['budgetLimited']
    assert after['switches'] == before['switches'] == 1
    assert after['utilityQuarterBeats'] > before['utilityQuarterBeats']
    assert after['transitions'][0]['fixedBoundary'] is False
    assert after['transitions'][0]['costQuarterBeats'] == .5


def test_source_and_incumbent_are_never_mutated():
    candidates, sources, timeline = fixture()
    original = deepcopy((candidates, sources, timeline))
    run_case(candidates, sources, timeline)
    assert (candidates, sources, timeline) == original


def test_real_two_sided_guard_rejects_too_close_candidates():
    old, before, expanded, after, info = run_case(guard=lambda q, direction: 1.2 if direction == 1 else .25)
    selected = [expanded[i] for i in after['indices']]
    assert next(p for p in selected if p.get('variant'))['start'] == 18
    assert references(old[i] for i in before['indices']) <= references(selected)


def test_original_parent_is_used_when_existing_candidate_is_already_variant():
    candidates, sources, timeline = fixture()
    p = candidates[1]
    p['start'] = 13
    p['events'] = p['events'][1:]
    p['activeQuarterBeats'] = 11
    p['boundaryQuarters'][0] = 13
    p['variant'] = {'kind': 'gap_safe_subphrase', 'parentStart': 12, 'parentEnd': 24,
                    'parentBoundaryQuarters': [12, 24], 'parentBoundaries': ['section', 'section']}
    # Keep the real original parent independently of the offered variant.
    _, originals, _ = fixture()
    sources['b']['parents'] = originals['b']['parents']
    _, _, expanded, after, info = run_case(candidates, sources, timeline)
    p = next(expanded[i] for i in after['indices'] if expanded[i].get('variant'))
    assert p['variant']['parentStart'] == 12
    assert info['accepted']


def test_source_omission_or_external_validation_cannot_be_bypassed():
    old, before, expanded, after, info = run_case(validate=lambda *_: 'source_omissions')
    assert expanded == old and after == before and not info['accepted']


@pytest.mark.parametrize('budget', [{'max_variants': 0}, {'max_window_checks': 0}, {'max_candidates': 3}])
def test_zero_generation_budget_retains_exact_feasible_incumbent(budget):
    old, before, expanded, after, info = run_case(**budget)
    assert expanded == old and after == before
    assert info['budgetLimited'] and not info['accepted']


def test_window_work_stays_inside_shared_remaining_budget():
    first = run_case(max_window_checks=2)
    second = run_case(max_window_checks=2)
    assert first == second
    assert first[-1]['windowChecks'] <= 2
    assert first[-1]['budgetLimited']


def test_better_utility_that_discards_an_old_optional_event_is_rejected(monkeypatch):
    from feedback_converter.song_import import hybrid_search
    candidates, sources, timeline = fixture()
    before = select_passages(candidates, lambda *_: .25)
    def discarding(nodes, *args, **kwargs):
        return {**before, 'indices': [len(nodes) - 1], 'utilityQuarterBeats': 1000,
                'states': 2, 'operations': 1}
    monkeypatch.setattr(hybrid_search, 'select_passages', discarding)
    expanded, after, info = refine_neighbors(candidates, before, sources, timeline, QuarterClock(), (0, 32), lambda *_: .25,
        validate=lambda *_: 'eligible_phrase', max_variants=4096, max_window_checks=100000,
        max_candidates=20000, max_states=10000, max_operations=500000)
    assert len(expanded) > len(candidates)
    assert after == before and not info['accepted']
    assert info['rejectedReason'] == 'incumbent_material_changed'


def test_candidate_order_does_not_change_source_selection():
    candidates, sources, timeline = fixture()
    a = run_case(candidates, sources, timeline)
    b = run_case(list(reversed(candidates)), sources, timeline)
    assert references(a[2][i] for i in a[3]['indices']) == references(b[2][i] for i in b[3]['indices'])


def test_fully_restored_edge_is_reported_for_removing_stale_omission_claim():
    old, before, expanded, after, info = run_case(edge_omissions=[{'trackId': 'b', 'events': [{'kind': 'notes', 'index': 5}]}])
    assert info['accepted'] and info['restoredEdgeOmissionIndices'] == [0]
    assert ('b', 'notes', 5) in references(expanded[i] for i in after['indices'])


def test_partial_edge_restoration_keeps_the_known_valid_incumbent():
    old, before, expanded, after, info = run_case(edge_omissions=[{'trackId': 'b', 'events': [
        {'kind': 'notes', 'index': 0}, {'kind': 'notes', 'index': 5}]}])
    assert not info['accepted'] and after == before
    assert info['rejectedReason'] == 'partial_omission_restoration'
    assert info['operations'] > 0 and info['states'] > 0, 'Rejected attempts still consume shared search work.'


@pytest.mark.parametrize('proof', ['retained_boundary', 'edge_omission', None])
def test_neighbor_cannot_resegment_an_exact_proof_bearing_foreground(proof):
    candidates, sources, timeline = fixture()
    guard = lambda *_: .25
    incumbent = select_passages(candidates, guard)
    episode = {k: deepcopy(candidates[0][k]) for k in ('trackId', 'start', 'end', 'events')}
    omissions = []
    if proof == 'retained_boundary':
        candidates[2]['variant'] = {'kind': 'retained_optional_boundary', 'foregroundEpisode': episode}
    elif proof == 'edge_omission':
        omissions = [{'trackId': 'b', 'events': [{'kind': 'notes', 'index': 0}], 'foregroundEpisode': episode}]
    # A previously unchosen longer version contains all the foreground notes.
    # The real neighbor optimizer prefers it once a new suffix is offered.
    parent, rows, context, _ = source([*[(q, q + 1) for q in range(16)], (16, 16.5)])
    parent.update(trackId='a', activeQuarterBeats=16.5, ghostOnly=False)
    sources['a'] = {'parents': [parent], 'rows': rows, 'context': context, 'unsupported': []}
    candidates.append(parent)
    expanded, after, info = refine_neighbors(candidates, incumbent, sources, timeline, QuarterClock(), (0, 32), guard,
        validate=lambda *_: 'eligible_phrase', max_variants=4096, max_window_checks=100000,
        max_candidates=20000, max_states=10000, max_operations=500000, edge_omissions=omissions)
    if proof:
        assert not info['accepted'] and after == incumbent
        assert info['rejectedReason'] == 'foreground_episode_resegmented'
    else:
        assert info['accepted']
        assert any(expanded[i]['trackId'] == 'a' and expanded[i]['end'] == 16.5 for i in after['indices'])
        assert references(candidates[i] for i in incumbent['indices']) <= references(expanded[i] for i in after['indices'])
    assert info['operations'] > 0 and info['states'] > 0

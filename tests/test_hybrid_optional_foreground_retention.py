"""Foreground replacement is local; nearby incumbent music stays playable."""
from copy import deepcopy

import pytest

from feedback_converter.song_import.hybrid_optional import refine_foreground, references
from feedback_converter.song_import.hybrid_search import select_passages
from test_hybrid_gap_variants import QuarterClock, source


def setup_case(end=24):
    candidates, sources = [], {}
    for tid, lo, hi in [('backing', 0, end), ('melody', 8, 20)]:
        parent, rows, context, timeline = source([(q, q + 1) for q in range(lo, hi)])
        parent.update(trackId=tid, activeQuarterBeats=hi-lo, ghostOnly=False)
        candidates.append(parent)
        sources[tid] = {'parents': [deepcopy(parent)], 'rows': rows, 'context': context, 'unsupported': []}
    return candidates, sources, timeline


def run(monkeypatch, *, end=24, sources_edit=None, preferred=(), **limits):
    from feedback_converter.song_import import hybrid_optional_foreground
    candidates, sources, timeline = setup_case(end)
    if sources_edit:
        sources_edit(candidates, sources)
    incumbent = select_passages(candidates, lambda *_: .25, preferred)
    def recognized(*args, **kwargs):
        return {'candidateCount': 2, 'operations': 5, 'budgetLimited': False, 'reason': None,
                'episodes': [{'candidateIndex': 1, 'candidateId': 'melody', 'trackId': 'melody',
                              'start': 8, 'end': 20, 'competingCandidateIndices': [0],
                              'evidence': {'reason': 'test_prevalidated_foreground'}}]}
    monkeypatch.setattr(hybrid_optional_foreground, 'recognize', recognized)
    settings = dict(options={}, main_id='base', validate=lambda *_: 'eligible_phrase',
                    max_variants=4096, max_window_checks=100000, max_candidates=20000,
                    max_states=10000, max_operations=500000, max_recognition_candidates=4096,
                    max_recognition_operations=200000, max_episodes=128)
    settings.update(limits)
    expanded, result, report = refine_foreground(candidates, incumbent, sources, {}, timeline,
        QuarterClock(), (0, end), lambda *_: .25, preferred, **settings)
    return candidates, incumbent, expanded, result, report


def test_foreground_keeps_short_existing_tail_without_global_minimum_relaxation(monkeypatch):
    old, before, expanded, result, report = run(monkeypatch)
    chosen = [expanded[i] for i in result['indices']]
    assert report['accepted'] and not report['budgetLimited']
    assert [(p['trackId'], p['start'], p['end']) for p in chosen] == [('backing', 0, 7), ('melody', 8, 20), ('backing', 21, 24)]
    tail = chosen[-1]
    assert tail['activeQuarterBeats'] == 3
    assert tail['variant']['kind'] == 'retained_optional_boundary'
    assert tail['variant']['retentionSide'] == 'after'
    assert tail['variant']['retainedParent']['events'] == old[0]['events']
    assert tail['variant']['foregroundEpisode']['events'] == old[1]['events']
    assert report['outsidePreservedEventCount'] == 10
    assert result['switches'] == 2
    assert all(not t['fixedBoundary'] and t['costQuarterBeats'] >= .5 for t in result['transitions'])


def test_one_beat_one_attack_edge_is_disclosed_not_fabricated_as_tiny_fill(monkeypatch):
    def immediate(candidates, sources):
        s = sources['backing']
        s['rows'].pop(); s['context']['beats'].pop()
        s['rows'][20].update(start=20.25, end=21.25)
        s['rows'][20]['notes'][0]['t'] = 20.25
        s['context']['beats'][20].update(start=20.25, end=21.25)
        for parent in (candidates[0], s['parents'][0]):
            parent['events'].pop()
            parent.update(end=21.25, activeQuarterBeats=21)
            parent['boundaryQuarters'][1] = 21.25
    old, before, expanded, after, report = run(monkeypatch, end=22, sources_edit=immediate)
    assert report['accepted']
    assert len(report['allowedEdgeOmissions']) == 1
    edge = report['allowedEdgeOmissions'][0]
    assert (edge['start'], edge['end'], edge['quarterBeats'], edge['pitchedAttackCount']) == (20.25, 21.25, 1, 1)
    assert edge['events'][0]['index'] == 20
    assert edge['incumbentParent']['events'] == old[0]['events']
    assert not any(expanded[i].get('variant', {}).get('retentionSide') == 'after' for i in after['indices'])


def test_one_attack_longer_than_one_beat_is_not_an_allowed_edge_loss(monkeypatch):
    def long(candidates, sources):
        source = sources['backing']
        source['rows'][21]['end'] = 23
        source['rows'][21]['notes'][0]['sus'] = 2
        source['rows'].pop()
        source['context']['beats'][21]['end'] = 23
        source['context']['beats'].pop()
        candidates[0]['events'].pop()
        source['parents'][0]['events'].pop()
    old, before, expanded, after, report = run(monkeypatch, end=23, sources_edit=long)
    assert not report['accepted'] and after == before
    assert report['rejectedReason'] == 'outside_incumbent_material_changed'


def test_explicit_source_priority_cannot_be_demoted_even_if_classifier_proposes_it(monkeypatch):
    old, before, expanded, after, report = run(monkeypatch, preferred=('backing',))
    assert not report['accepted'] and after == before
    assert report['rejectedReason'] == 'explicit_source_priority_regression'


def test_connected_group_crossing_guard_is_removed_whole_not_sliced(monkeypatch):
    def linked(candidates, sources):
        for i in (6, 7):
            sources['backing']['rows'][i].update(start=6, end=8)
        sources['backing']['rows'][6]['notes'][0]['ln'] = True
        sources['backing']['rows'][7]['notes'][0]['po'] = True
    old, before, expanded, after, report = run(monkeypatch, sources_edit=linked)
    assert report['accepted']
    keys = references(expanded[i] for i in after['indices'])
    assert ('backing', 'notes', 6) not in keys and ('backing', 'notes', 7) not in keys
    assert ('backing', 'notes', 5) in keys and ('backing', 'notes', 21) in keys


@pytest.mark.parametrize('limits', [{'max_variants': 0}, {'max_window_checks': 0}, {'max_states': 0}])
def test_foreground_budget_exhaustion_returns_original_full_plan(monkeypatch, limits):
    old, before, expanded, after, report = run(monkeypatch, **limits)
    assert expanded == old and after == before
    assert report['budgetLimited'] and not report['accepted']


def test_nested_retained_fragment_cannot_be_used_as_a_new_parent(monkeypatch):
    def nested(candidates, sources):
        candidates[0]['variant'] = {'kind': 'retained_optional_boundary'}
    old, before, expanded, after, report = run(monkeypatch, sources_edit=nested)
    assert not report['accepted'] and expanded == old and after == before


def test_source_omission_validator_also_applies_to_retained_edges(monkeypatch):
    old, before, expanded, after, report = run(monkeypatch, validate=lambda p, _: 'source_omissions' if p['start'] > 20 else 'eligible_phrase')
    assert not report['accepted'] and after == before


def test_foreground_transition_does_not_mutate_source_rows_or_refs(monkeypatch):
    saved = {}
    def capture(candidates, sources):
        saved['live'] = candidates, sources
        saved['copy'] = deepcopy(saved['live'])
    run(monkeypatch, sources_edit=capture)
    assert saved['live'] == saved['copy']


@pytest.mark.parametrize('failure', ['both_edges_exceed_one_beat', 'not_corroborated', 'ghost', 'twenty_percent'])
def test_local_edge_exception_cannot_expand_into_general_outside_note_loss(failure):
    from feedback_converter.song_import.hybrid_optional import _edge_omissions
    lo, hi = (8, 12) if failure == 'twenty_percent' else (8, 20)
    spans = [(lo - 1.25, lo - .5), (hi + .5, hi + 1.25)]
    parent, rows, context, timeline = source(spans)
    parent['trackId'] = 'backing'
    melody = {'trackId': 'melody', 'start': lo, 'end': hi, 'events': []}
    proposal = {'candidateIndex': 1, 'competingCandidateIndices': [] if failure == 'not_corroborated' else [0]}
    if failure != 'both_edges_exceed_one_beat':
        rows[0]['end'] = rows[0]['start'] + .25
        rows[0]['notes'][0]['sus'] = .25
    if failure == 'ghost':
        rows[0]['notes'][0]['ghost'] = True
    if failure == 'twenty_percent':
        # Total one beat is legal under the absolute cap, but exceeds 20% of
        # this four-beat foreground episode.
        assert sum(r['end'] - r['start'] for r in rows) == 1
    missing = references([parent])
    permitted, evidence = _edge_omissions(missing, [parent], [proposal], [parent, melody],
                                         {'backing': {'rows': rows}}, lambda *_: .25)
    assert not missing <= permitted
    if failure in ('both_edges_exceed_one_beat', 'not_corroborated', 'twenty_percent'):
        assert not evidence

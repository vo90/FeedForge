"""A joined primary passage must judge answers in their own musical section."""
from copy import deepcopy

import pytest

from feedback_converter.song_import.hybrid_context import Clock
from feedback_converter.song_import import hybrid_handover as handover
from feedback_converter.song_import import hybrid_regional as regional


def row(index, start, end, *, onset=None, fret=7, **techniques):
    onset = start if onset is None else onset
    return {'kind': 'notes', 'index': index, 'start': start, 'end': end,
            'available': True, 'sourceIds': [f'n:{index}'], 'occurrences': [1],
            'notes': [dict(t=onset / 2, sus=(end - onset) / 2,
                           s=2, f=fret, **techniques)]}


def response(start, first=0):
    return [row(first + i, start + i * .25, start + (i + 1) * .25,
                fret=fret, vb=i in {0, 3})
            for i, fret in enumerate((3, 5, 8, 10, 7, 12))]


def backing(start):
    return [row(50 + i, start + i * .25, start + (i + 1) * .25,
                fret=3 + i % 4, pm=True) for i in range(16)]


def fixture(*, response_section='early', representative='late'):
    clock = Clock({'tempoPoints': [{'quarter': 0, 'time': 0, 'bpm': 120}]})
    tracks = {tid: {'id': tid, 'name': name, 'instrument': 'guitar',
                    'tuning': [40, 45, 50, 55, 59, 64], 'capo': 0}
              for tid, name in [('owner', 'Main Lead Guitar'), ('peer', 'Other Fills')]}
    owner = [row(0, 0, 4, fret=15, vb=True), row(1, 8, 16, fret=17, vb=True),
             row(2, 14, 16, fret=15, vb=True), row(3, 20, 24, fret=19, vb=True)]
    local = response(4.25 if response_section == 'early' else 16.25)
    distant = backing(12 if response_section == 'early' else 0)
    rows = {'owner': owner, 'peer': sorted(local + distant, key=lambda r: r['start'])}
    evidence = [{'id': name, 'trackId': 'owner', 'start': start, 'end': end,
                 'ownedStart': start, 'ownedEnd': end, 'priority': 'solo',
                 'evidence': 'regional_lead', 'confidence': 'high', 'eligible': True,
                 'sectionName': f'{name.title()} Guitar Solo', 'labelledSolo': True}
                for name, start, end in [('early', 0, 12), ('late', 12, 24)]]
    parent = next(c for c in evidence if c['id'] == representative)
    candidates = [regional._passage(c, owner, clock, c['ownedStart'], c['ownedEnd'])
                  for c in evidence]
    passage, = regional._coalesced(candidates, rows, clock, {c['id']: c for c in evidence},
                                   'owner', [], {})
    # Either semantic obligation can be the strongest representative after
    # ranking; neither choice may alter the actual available lead response.
    passage.update(obligationId=parent['id'], sectionName=parent['sectionName'])
    context = {'tracks': {tid: {'beats': []} for tid in tracks}}
    context['tracks']['owner']['beats'] = [
        {'start': r['start'], 'end': r['end'], 'rest': False} for r in owner]
    return [[passage], evidence, rows, tracks, context, {}, 'owner', clock, []]


def events(passages):
    return {(p['trackId'], r['kind'], r['index']) for p in passages for r in p['events']}


def assert_owner_preserved(case, result):
    original = events(case[0])
    selected = events(result[0])
    assert original <= selected
    assert {event for event in selected if event[0] == 'owner'} == original
    original_refs = {(r['kind'], r['index']): r for p in case[0] for r in p['events']}
    selected_refs = {(r['kind'], r['index']): r for p in result[0]
                     if p['trackId'] == 'owner' for r in p['events']}
    assert selected_refs == original_refs


@pytest.mark.parametrize('local', ['early', 'late'])
@pytest.mark.parametrize('representative', ['early', 'late'])
def test_joined_solo_uses_local_context_without_changing_owner_notes(local, representative):
    case = fixture(response_section=local, representative=representative)
    before = deepcopy(case[:-2])
    result = handover.refine_handovers(*case)
    assert events(result[0]) == events(case[0]) | {('peer', 'notes', i) for i in range(6)}
    assert_owner_preserved(case, result)
    assert len(result[1]) == 1 and result[2] == case[0]
    assert not result[3]['budgetLimited']
    assert case[:-2] == before


def test_input_order_and_representative_do_not_change_selected_music():
    case = fixture()
    expected = events(handover.refine_handovers(*case)[0])
    changed = deepcopy(case)
    changed[1].reverse()
    changed[0][0]['obligationIds'].reverse()
    changed[3] = dict(reversed(list(changed[3].items())))
    changed[0][0]['obligationId'] = 'early'
    actual = handover.refine_handovers(*changed)
    assert events(actual[0]) == expected
    assert_owner_preserved(changed, actual)


@pytest.mark.parametrize('fault', ['excluded', 'tuning', 'capo', 'accompaniment',
                                  'effect', 'harmony', 'backing', 'unavailable',
                                  'nonfeatured', 'ineligible'])
def test_a_distant_solo_does_not_override_local_safety(fault):
    case = fixture()
    if fault == 'excluded':
        case[5]['excludedTrackIds'] = ['peer']
    elif fault == 'tuning':
        case[3]['peer']['tuning'][0] -= 2
    elif fault == 'capo':
        case[3]['peer']['capo'] = 1
    elif fault == 'accompaniment':
        case[5]['roles'] = {'peer': 'accompaniment'}
    elif fault in {'effect', 'harmony'}:
        case[3]['peer']['name'] = 'Other ' + ('FX Guitar' if fault == 'effect' else 'Harmony Lead')
    elif fault == 'backing':
        case[2]['peer'] = backing(4)
    elif fault == 'unavailable':
        for r in case[2]['peer']:
            r['available'] = False
    elif fault == 'nonfeatured':
        case[1][0].update(priority='accompaniment', labelledSolo=False)
    else:
        case[1][0]['eligible'] = False
    result = handover.refine_handovers(*case)
    assert result[0] == case[0] and not result[1] and not result[2]
    assert_owner_preserved(case, result)


def boundary_fixture():
    case = fixture()
    owner = [row(0, 0, 8, fret=15, vb=True), row(1, 16, 24, fret=17, vb=True)]
    case[2]['owner'] = owner
    case[4]['tracks']['owner']['beats'] = [
        {'start': r['start'], 'end': r['end'], 'rest': False} for r in owner]
    case[0][0] = {**regional._passage(case[1][1], owner, case[7], 0, 24),
                  'obligationIds': ['early', 'late']}
    return case


def test_cross_boundary_rest_accepts_only_complete_local_phrases():
    case = boundary_fixture()
    case[2]['peer'] = response(8.5) + [
        # The complete linked gesture crosses the section boundary. Neither
        # its first nor its second attack may be selected as a fragment.
        row(20, 11.5, 12.5, fret=12, ln=True),
        row(21, 11.5, 12.5, onset=12, fret=10, po=True),
    ] + response(13, first=30)
    result = handover.refine_handovers(*case)
    assert events(result[0]) == events(case[0]) | {
        ('peer', 'notes', i) for i in [*range(6), *range(30, 36)]}
    assert len(result[1]) == 2
    assert_owner_preserved(case, result)


def test_later_expression_does_not_qualify_an_unexpressive_earlier_response():
    case = boundary_fixture()
    early = response(8.5)
    for r in early:
        r['notes'][0].pop('vb')
    case[2]['peer'] = early + response(13, first=30)
    result = handover.refine_handovers(*case)
    assert events(result[0]) == events(case[0]) | {('peer', 'notes', i) for i in range(30, 36)}
    assert len(result[1]) == 1
    assert_owner_preserved(case, result)


@pytest.mark.parametrize('named_section', ['early', 'late'])
def test_named_peer_permission_belongs_to_the_actual_section(named_section):
    case = fixture()
    case[3]['peer']['name'] = 'Alex | Fills'
    next(c for c in case[1] if c['id'] == named_section)['sectionName'] = 'Solo (Alex & Blake)'
    # Three attacks can be a known player's answer but are deliberately too
    # weak to infer an unknown soloist. Later naming must not license them.
    early = response(4.25)[:3]
    case[2]['peer'] = early + response(12.25, first=30)
    result = handover.refine_handovers(*case)
    expected = {('peer', 'notes', i) for i in range(3)} if named_section == 'early' else set()
    assert events(result[0]) == events(case[0]) | expected
    assert_owner_preserved(case, result)


def test_noncoalesced_passage_retains_existing_handover_behavior():
    case = fixture(representative='early')
    case[1] = [case[1][0]]
    case[2]['owner'] = [row(0, 0, 4, fret=15, vb=True), row(1, 8, 12, fret=17, vb=True)]
    case[0] = [regional._passage(case[1][0], case[2]['owner'], case[7], 0, 12)]
    case[4]['tracks']['owner']['beats'] = [
        {'start': r['start'], 'end': r['end'], 'rest': False} for r in case[2]['owner']]
    result = handover.refine_handovers(*case)
    assert events(result[0]) == events(case[0]) | {('peer', 'notes', i) for i in range(6)}
    assert_owner_preserved(case, result)
    assert len(result[1]) == 1 and result[2] == case[0]


@pytest.mark.parametrize('budget,expected_calls', [('MAX_HANDOVER_WINDOWS', 0),
                                                   ('MAX_HANDOVER_CANDIDATES', 1),
                                                   ('MAX_HANDOVER_GROUP_CHECKS', 1)])
def test_exhausted_budget_stops_before_another_semantic_context(monkeypatch, budget, expected_calls):
    case = boundary_fixture()
    case[2]['peer'] = response(8.5) + response(13, first=30)
    calls = []
    original_credible = handover._credible

    def counted(*args):
        calls.append((args[2]['id'], args[4], args[5]))
        return original_credible(*args)

    monkeypatch.setattr(handover, '_credible', counted)
    monkeypatch.setattr(handover, budget, 0)
    result = handover.refine_handovers(*case)
    assert result[0] == case[0] and not result[1] and not result[2]
    assert result[3]['budgetLimited']
    assert len(calls) == expected_calls
    assert_owner_preserved(case, result)


def test_completed_local_response_survives_next_context_budget_limit(monkeypatch):
    case = boundary_fixture()
    case[2]['peer'] = response(8.5) + response(13, first=30)
    monkeypatch.setattr(handover, 'MAX_HANDOVER_WINDOWS', 1)
    result = handover.refine_handovers(*case)
    assert events(result[0]) == events(case[0]) | {('peer', 'notes', i) for i in range(6)}
    assert len(result[1]) == 1 and result[3]['budgetLimited']
    assert result[3]['reason'] == 'handover_window_budget'
    assert_owner_preserved(case, result)


def test_overlapping_contexts_do_not_spend_candidate_budget_twice(monkeypatch):
    case = fixture()
    alias = {**deepcopy(case[1][0]), 'id': 'early-overlapping', 'end': 10, 'ownedEnd': 10}
    case[1].append(alias)
    case[0][0]['obligationIds'].append(alias['id'])
    monkeypatch.setattr(handover, 'MAX_HANDOVER_CANDIDATES', 1)
    result = handover.refine_handovers(*case)
    assert events(result[0]) == events(case[0]) | {('peer', 'notes', i) for i in range(6)}
    assert len(result[1]) == 1
    assert result[3]['candidateCount'] == 1 and not result[3]['budgetLimited']
    assert sum(len(p['events']) for p in result[0] if p['trackId'] == 'peer') == 6
    assert_owner_preserved(case, result)


@pytest.mark.parametrize('proof', ['complete', 'wrong_note', 'wrong_occurrence'])
def test_unsupported_gap_uses_retained_nonrepresentative_obligation(proof):
    case = fixture()
    # A previous refinement split the physical passage; both pieces retain
    # its semantic identities even though the representative is the later one.
    case[0] = [{**regional._passage(case[1][1], case[2]['owner'], case[7], lo, hi),
                'obligationIds': ['early', 'late']} for lo, hi in ((0, 4), (8, 24))]
    case[3]['peer']['name'] = 'Other Lead Guitar'
    case[4]['tracks']['owner']['beats'].append(
        {'start': 4, 'end': 8, 'rest': False, 'sourceId': 'omitted-beat',
         'noteIds': ['omitted-note'], 'occurrence': 2})
    case[8].append({'trackId': 'owner', 'start': 4, 'end': 8,
                    'reason': 'unsupported_source_gesture',
                    'sourceIds': ['other-note' if proof == 'wrong_note' else 'omitted-note'],
                    'occurrences': [1 if proof == 'wrong_occurrence' else 2]})
    result = handover.refine_handovers(*case)
    expected = {('peer', 'notes', i) for i in range(6)} if proof == 'complete' else set()
    assert events(result[0]) == events(case[0]) | expected
    assert not result[2]
    assert_owner_preserved(case, result)
    if proof == 'complete':
        assert len(result[1]) == 1
        assert result[1][0]['selectionEvidence']['unsupportedOwnerFallback']
    else:
        assert not result[1]

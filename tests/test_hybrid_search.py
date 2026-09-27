from itertools import combinations
import random

import pytest

from feedback_converter.song_import.hybrid_search import select_passages


def phrase(track, start, end, active=None, fret=5):
    return {'trackId': track, 'start': start, 'end': end,
            'activeQuarterBeats': end - start if active is None else active,
            'entryFret': fret, 'exitFret': fret, 'boundaries': ['rest', 'rest'],
            'events': [{'kind': 'notes', 'index': round(start * 100)}]}


def guard(_quarter, _direction):
    return .25


def test_useful_activity_beats_a_long_sparse_interval():
    candidates = [phrase('sparse', 0, 8, active=1), phrase('useful', 1, 5, active=3)]
    result = select_passages(candidates, guard)
    assert result['indices'] == [1]
    assert result['activeQuarterBeats'] == 3


def test_explicit_priority_survives_shorter_activity():
    candidates = [phrase('long', 0, 8), phrase('preferred', 1, 2)]
    result = select_passages(candidates, guard, ['preferred', 'long'])
    assert result['indices'] == [1]
    assert result['preferredQuarterBeats'] == [1, 0]


def test_a_tiny_extra_fragment_does_not_pay_for_a_source_change():
    candidates = [phrase('clean', 0, 2), phrase('rhythm', 3, 3.125)]
    assert select_passages(candidates, guard)['indices'] == [0]
    candidates[1]['trackId'] = 'clean'
    assert select_passages(candidates, guard)['indices'] == [0, 1]


def test_complete_phrase_reserves_internal_rests_and_transition_space():
    candidates = [phrase('clean', 0, 6, active=4), phrase('rhythm', 2, 3),
                  phrase('rhythm', 6.125, 7), phrase('rhythm', 7.25, 9)]
    selected = select_passages(candidates, guard)['indices']
    assert selected == [0, 3]


@pytest.mark.parametrize('budget', [dict(max_states=0), dict(max_operations=0), dict(max_candidates=1)])
def test_budget_keeps_a_complete_feasible_incumbent(budget):
    candidates = [phrase('a', 0, 2), phrase('a', 4, 6), phrase('b', 7, 10)]
    result = select_passages(candidates, guard, **budget)
    assert result['budgetLimited']
    assert result['reason']
    assert result['indices'] == [0, 1, 2]
    assert result['activeQuarterBeats'] == 7
    assert result == select_passages(candidates, guard, **budget)


def test_zero_candidate_budget_keeps_only_the_existing_primary():
    result = select_passages([phrase('a', 0, 2)], guard, max_candidates=0)
    assert result['indices'] == []
    assert result['budgetLimited']
    assert result['reason'] == 'candidate_limit'


def test_input_order_does_not_change_the_selected_sources():
    candidates = [phrase('b', 0, 2), phrase('a', 0, 2), phrase('a', 4, 6), phrase('c', 4, 6)]
    expected = [('a', 0, 2), ('a', 4, 6)]
    for values in (candidates, list(reversed(candidates))):
        result = select_passages(values, guard)
        assert [(values[i]['trackId'], values[i]['start'], values[i]['end']) for i in result['indices']] == expected


def independent_score(path, preferred, left_anchor=None, right_anchor=None):
    """The documented utility, evaluated independently for exhaustive checks."""
    activity = sum(round(p['activeQuarterBeats'] * 1_000_000) for p in path)
    priority = tuple(sum(round(p['activeQuarterBeats'] * 1_000_000) for p in path if p['trackId'] == tid)
                     for tid in preferred)
    switches = 0
    movement = 0
    anchored_cost = 0
    for left, right in zip(path, path[1:]):
        same = left['trackId'] == right['trackId']
        if left['end'] + (0 if same else .25) > right['start'] + 1e-7:
            return None
        switches += int(not same)
        if not same:
            movement += round(abs(left['exitFret'] - right['entryFret']) * 10_000)
    if path:
        for left, right, sign in ((left_anchor, path[0], 1), (path[-1], right_anchor, 1),
                                  (left_anchor, right_anchor, -1)):
            if left is None or right is None or left['trackId'] == right['trackId']:
                continue
            relief = max(1, right['start'] - left['end'])
            distance = abs(left['exitFret'] - right['entryFret']) * 10_000
            # Internal motion is in movement; anchored movement is included in
            # anchored_cost so it is not subtracted twice from total utility.
            anchored_cost += sign * round((500_000 + distance) / relief)
            movement += sign * round(distance / relief)
            switches += sign
    internal_switches = sum(left['trackId'] != right['trackId'] for left, right in zip(path, path[1:]))
    internal_movement = sum(round(abs(left['exitFret'] - right['entryFret']) * 10_000)
                            for left, right in zip(path, path[1:]) if left['trackId'] != right['trackId'])
    return (priority, activity - 500_000 * internal_switches - internal_movement - anchored_cost, -switches, activity,
            -len(path), len(path) * 2, -movement)


def test_bounded_dag_matches_exhaustive_small_phrase_plans():
    rng = random.Random(40119)
    for _ in range(80):
        candidates = []
        for i in range(8):
            start = rng.randrange(0, 32) / 4
            end = start + rng.randrange(1, 13) / 4
            candidates.append(phrase(str(rng.randrange(3)), start, end,
                                     active=(end - start) * rng.choice((.25, .5, 1)), fret=rng.randrange(3, 13)))
        preferred = ['1', '0'] if rng.randrange(2) else []
        possible = []
        for length in range(len(candidates) + 1):
            for subset in combinations(candidates, length):
                path = sorted(subset, key=lambda p: (p['end'], p['start'], p['trackId']))
                score = independent_score(path, preferred)
                if score is not None:
                    possible.append(score)
        result = select_passages(candidates, guard, preferred)
        actual = independent_score([candidates[i] for i in result['indices']], preferred)
        assert actual == max(possible)
        assert not result['budgetLimited']
        assert result['states'] == len(candidates)
        assert result['operations'] <= len(candidates) * (len(candidates) - 1) // 2


@pytest.mark.parametrize('budget', [{}, {'max_states': 0}, {'max_operations': 0}])
def test_fixed_entry_and_return_reject_a_tiny_low_fret_detour(budget):
    left = phrase('main', 0, 3.75, fret=18)
    right = phrase('main', 4.75, 8, fret=17)
    result = select_passages([phrase('extra', 4, 4.5, fret=5)], guard,
                             left_anchor=left, right_anchor=right, **budget)
    assert result['indices'] == []
    assert result['utilityQuarterBeats'] == 0


@pytest.mark.parametrize('budget', [{}, {'max_states': 0}, {'max_operations': 0}, {'max_candidates': 1}])
def test_fixed_return_movement_beats_alphabetical_source_order(budget):
    candidates = [phrase('a-low', 1, 4, fret=2), phrase('z-near', 1, 4, fret=14)]
    left = phrase('main', -1, 0, fret=15)
    right = phrase('main', 5, 6, fret=16)
    for values in (candidates, list(reversed(candidates))):
        result = select_passages(values, guard, left_anchor=left, right_anchor=right, **budget)
        assert [values[i]['trackId'] for i in result['indices']] == ['z-near']
        assert len(result['transitions']) == 2
        assert all(edge['fixedBoundary'] for edge in result['transitions'])


def test_long_authored_rests_allow_a_useful_one_beat_fill():
    result = select_passages([phrase('rhythm', 4, 5, fret=5)], guard,
                             left_anchor=phrase('main', -1, 0, fret=9),
                             right_anchor=phrase('main', 9, 10, fret=12))
    assert result['indices'] == [0]
    assert result['utilityQuarterBeats'] > .7


def test_same_source_fixed_continuation_does_not_pay_a_source_switch():
    result = select_passages([phrase('lead', 1, 1.125, fret=8)], guard,
                             left_anchor=phrase('lead', -1, 0, fret=9),
                             right_anchor=phrase('lead', 2, 3, fret=12))
    assert result['indices'] == [0]
    assert result['switches'] == 0
    assert result['utilityQuarterBeats'] == .125


def test_fixed_anchor_uses_the_ending_of_a_complete_connected_gesture():
    from feedback_converter.song_import.hybrid_lead import _fixed_events
    rows = [{'kind': 'notes', 'index': i, 'start': 0, 'end': 4,
             'notes': [{'t': i, 'f': fret, 's': 1}]} for i, fret in enumerate((5, 17))]
    primary = {'rows': {'main': rows}, 'mainEvents': [{'kind': 'notes', 'index': i} for i in range(2)],
               'passages': []}
    anchors = _fixed_events(primary, {'id': 'main'}, {}, None)
    assert anchors == [{'trackId': 'main', 'start': 0, 'end': 4, 'entryFret': 5, 'exitFret': 17}]


def test_anchor_aware_dag_matches_independent_exhaustive_plans():
    rng = random.Random(729103)
    for _ in range(60):
        candidates = []
        for i in range(7):
            start = rng.randrange(1, 25) / 4
            end = start + rng.randrange(1, 9) / 4
            candidates.append(phrase(str(rng.randrange(3)), start, end,
                                     active=(end - start) * rng.choice((.25, .5, 1)), fret=rng.randrange(1, 23)))
        left = phrase(str(rng.randrange(3)), -2, -1, fret=rng.randrange(1, 23))
        right = phrase(str(rng.randrange(3)), 9, 10, fret=rng.randrange(1, 23))
        preferred = ['2'] if rng.randrange(2) else []
        possible = []
        for length in range(len(candidates) + 1):
            for subset in combinations(candidates, length):
                path = sorted(subset, key=lambda p: (p['end'], p['start'], p['trackId']))
                score = independent_score(path, preferred, left, right)
                if score is not None:
                    possible.append(score)
        result = select_passages(candidates, guard, preferred, left_anchor=left, right_anchor=right)
        actual = independent_score([candidates[i] for i in result['indices']], preferred, left, right)
        assert actual == max(possible)


def test_local_omissions_reserve_the_written_slot_only():
    from feedback_converter.song_import.hybrid_context import Clock
    from feedback_converter.song_import.hybrid_lead import omission_spans
    clock = Clock({'tempoPoints': [{'quarter': 0, 'time': 0, 'bpm': 120}]})
    context = {'beats': [{'start': 0, 'end': 4, 'rest': False, 'sourceId': 'early', 'noteIds': ['early:0']},
                         {'start': 4, 'end': 8, 'rest': False, 'sourceId': 'late', 'noteIds': ['late:0']} ]}
    performance = {'hybridSourceOmissions': [{'trackId': 'solo', 'scoreStart': 2, 'scoreDuration': .1,
                                             'sourceIds': ['late:0']}]}
    assert omission_spans(performance, 'solo', context, clock) == [[4, 8]]
    assert omission_spans(performance, 'other', context, clock) == []


@pytest.mark.parametrize('rounding', [-.0000005, .0000005])
def test_omission_recording_rounding_does_not_block_an_adjacent_phrase(rounding):
    from feedback_converter.song_import.hybrid_context import Clock
    from feedback_converter.song_import.hybrid_lead import omission_spans
    clock = Clock({'tempoPoints': [{'quarter': 0, 'time': 0, 'bpm': 180}]})
    context = {'beats': [{'start': a, 'end': a + 4, 'rest': False, 'sourceId': str(a), 'noteIds': [str(a) + ':0']}
                         for a in (0, 4, 8)]}
    performance = {'hybridSourceOmissions': [{'trackId': 'solo', 'scoreStart': 4 / 3 + rounding,
                                             'scoreDuration': 4 / 3}]}
    assert omission_spans(performance, 'solo', context, clock) == [[4, 8]]


def test_unsupported_donor_slot_keeps_its_other_complete_phrase(tmp_path):
    from test_songsterr_hybrid_lead import song, prepared, rest
    from test_song_import_score import beat, measure
    from feedback_converter.song_import.hybrid_lead import plan
    doc = song()
    doc['parts'][1]['measures'][2] = measure(beat(10, duration=(1, 2)), rest((1, 2)))
    _, performance, options = prepared(tmp_path, doc)
    performance['hybridOmittedTracks'] = ['1']
    performance['hybridSourceOmissions'] = [{'trackId': '1', 'scoreStart': 4, 'scoreDuration': .1}]
    result = plan(performance, options, '0', {'status': 'validated', 'offset': 0, 'scale': 1}, 8)
    assert any(p['trackId'] == '1' and p['start'] == 4 for p in result['passages'])
    assert not any(p['trackId'] == '1' and p['start'] == 8 for p in result['passages'])
    assert any(p['trackId'] == '1' and p['start'] == 8 and p['reason'] == 'source_omissions'
               for p in result['candidateDecisions'])


def test_automatic_base_selection_retains_its_provenance(tmp_path):
    from test_songsterr_hybrid_lead import prepared
    _, performance, options = prepared(tmp_path)
    selection = performance['hybridBaseSelection']
    assert selection['mainTrackId'] == options['mainTrackId']
    assert selection['reason'] == 'dominant_tuning_then_base'
    assert selection['setup']['tuning'] == performance['tracks'][0]['tuning']


def test_explicit_source_review_still_prompts_with_an_existing_base(tmp_path):
    from test_songsterr_hybrid_lead import prepared
    from feedback_converter.song_import.audio import ImportFailure
    from feedback_converter.song_import.hybrid_lead import choose_main
    _, performance, options = prepared(tmp_path)
    with pytest.raises(ImportFailure) as error:
        choose_main(performance, {**options, 'reviewSources': True}, options['sourceSha256'])
    assert error.value.code == 'awaiting_main_choice'
    assert error.value.diagnostics['suggestedMainTrackId'] == options['mainTrackId']


@pytest.mark.parametrize('ghost', [False, True])
def test_a_lead_source_can_fill_other_regions_but_not_with_ghost_only_fades(tmp_path, ghost):
    from test_songsterr_hybrid_lead import song, prepared
    from feedback_converter.song_import.hybrid_context import Clock
    from feedback_converter.song_import.hybrid_lead import _fill_gaps, occupied
    doc = song()
    doc['tracks'][1]['name'] = 'Additional Lead Guitar'
    doc['parts'][1]['measures'][1]['voices'][0]['beats'][0]['notes'][0]['ghost'] = ghost
    _, performance, options = prepared(tmp_path, doc, overrides={'mainTrackId': '0'})
    context = performance['compositionContext']
    protected = occupied(performance['tracks'][0], context['tracks']['0'], Clock(context['timeline']))
    primary = {'protected': protected, 'roles': {'0': 'main', '1': 'lead', '2': 'accompaniment'}}
    result = _fill_gaps(performance, options, '0', {'status': 'validated', 'offset': 0, 'scale': 1}, 8, primary)
    assert any(p['trackId'] == '1' for p in result['passages']) is not ghost

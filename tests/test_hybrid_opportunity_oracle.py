"""Independent regression checks for lead handovers and safe fill warnings."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from feedback_converter.song_import.verification import Check
from feedback_converter.song_import.verify_hybrid_opportunities import local_lead_audit, optional_fill_audit
from feedback_converter.song_import.verify_hybrid_priority import _verified_reservations, _supported_gestures


def event(start, end, fret=5, **technique):
    return {'start': start, 'end': end, 'onset': start,
            'notes': [{'t': start, 's': 2, 'f': fret, 'sus': end-start, **technique}],
            'sourceIds': [f'event-{start}'], 'occurrences': [1]}


def fixture():
    rows = {'a': {('notes', 0): event(0, 2), ('notes', 1): event(7, 8)},
            'b': {('notes', 0): event(2, 3, 7), ('notes', 1): event(3, 4, 9),
                  ('notes', 2): event(4, 7.25, 11)}}
    requirement = {'evidence': 'named_soloist', 'sectionName': 'Solo (Don & Joe)',
                   'start': 0, 'end': 8, 'owners': {tid: set(values) for tid, values in rows.items()}}
    selected = {('a', *key) for key in rows['a']}
    return rows, [requirement], selected


def test_complete_selected_voice_cannot_hide_peer_gestures_in_its_rest():
    rows, requirements, selected = fixture()
    result = local_lead_audit(rows, requirements, selected, {'a', 'b'}, lambda q: q)
    assert result['unresolvedCount'] == 1
    gap = result['unresolved'][0]
    assert (gap['start'], gap['end']) == (2, 7)
    assert gap['candidateTrackIds'] == ['b']
    assert gap['candidateEventCounts'] == {'b': 2}, 'The crossing sustain is not a recoverable whole gesture.'


def test_recovered_whole_gestures_satisfy_handover_without_cutting_crossing_tail():
    rows, requirements, selected = fixture()
    selected.update({('b', 'notes', 0), ('b', 'notes', 1)})
    assert local_lead_audit(rows, requirements, selected, {'a', 'b'}, lambda q: q)['unresolvedCount'] == 0


@pytest.mark.parametrize('case', ['incompatible', 'excluded', 'parallel', 'only_crossing', 'muted', 'tiny'])
def test_no_obligation_for_unavailable_simultaneous_or_unplayable_peer(case):
    rows, requirements, selected = fixture()
    available = {'a', 'b'}
    if case in {'incompatible', 'excluded'}:
        available.remove('b')
    elif case == 'parallel':
        rows['a'][('notes', 0)]['end'] = 7
    elif case == 'only_crossing':
        rows['b'] = {('notes', 0): event(1, 8)}
    elif case == 'muted':
        for row in rows['b'].values():
            row['notes'][0]['mt'] = True
    else:
        rows['a'][('notes', 0)]['end'] = 6.5
        rows['b'] = {('notes', 0): event(6.5, 6.75), ('notes', 1): event(6.75, 7)}
    assert local_lead_audit(rows, requirements, selected, available, lambda q: q)['unresolvedCount'] == 0


def test_one_complete_expressive_held_lead_can_be_meaningful():
    rows, requirements, selected = fixture()
    rows['b'] = {('notes', 0): event(3, 5, vb=True)}
    assert local_lead_audit(rows, requirements, selected, {'a', 'b'}, lambda q: q)['unresolvedCount'] == 1


def test_backing_activity_cannot_hide_missing_named_lead():
    rows, requirements, selected = fixture()
    rows['backing'] = {('chords', 0): event(2, 7)}
    selected.add(('backing', 'chords', 0))
    assert local_lead_audit(rows, requirements, selected, set(rows), lambda q: q)['unresolvedCount'] == 1


def test_local_audit_discloses_operation_limit_without_claiming_complete_search():
    rows, requirements, selected = fixture()
    result = local_lead_audit(rows, requirements, selected, {'a', 'b'}, lambda q: q, max_group_checks=1)
    assert result['budgetLimited'] is True
    assert result['groupChecks'] == 1
    assert result['maxGroupChecks'] == 1


def expressive_fixture():
    rows = {'owner': {('notes', i): event(start, start+.5, 12+i, vb=True)
                      for i, start in enumerate((0, .5, 1, 1.5, 7, 7.5, 8, 8.5))},
            'fills': {('notes', i): event(2+i*.5, 2.5+i*.5, 3+i,
                                         **({'ho': True} if i in (1, 2) else {})) for i in range(8)}}
    rows['fills'][('notes', 8)] = event(6, 7.25, 12, vb=True)
    selected = {('owner', *key) for key in rows['owner']}
    options = {'featured_sections': [{'start': 0, 'end': 9, 'sectionName': 'Guitar Solo 2'}],
               'featured_sources': set(rows), 'unknown_peers': {'fills'}}
    return rows, selected, options


def test_generic_solo_requires_complete_expressive_unknown_peer_answer():
    rows, selected, options = expressive_fixture()
    result = local_lead_audit(rows, [], selected, set(rows), lambda q: q, **options)
    assert result['unresolvedCount'] == 1
    assert result['unresolved'][0]['reason'] == 'unfilled_expressive_peer_response'
    assert result['unresolved'][0]['candidateEventCounts'] == {'fills': 8}
    # A complete response satisfies the oracle; its overlapping final sustain
    # never becomes mandatory merely because it is expressive.
    selected.update(('fills', 'notes', i) for i in range(8))
    assert local_lead_audit(rows, [], selected, set(rows), lambda q: q, **options)['unresolvedCount'] == 0


def test_generic_solo_accepts_an_equivalent_complete_peer_instead():
    rows, selected, options = expressive_fixture()
    rows['alternative'] = deepcopy(rows['fills'])
    options['featured_sources'].add('alternative')
    options['unknown_peers'].add('alternative')
    selected.update(('alternative', 'notes', i) for i in range(8))
    assert local_lead_audit(rows, [], selected, set(rows), lambda q: q, **options)['unresolvedCount'] == 0


@pytest.mark.parametrize('later_fill_selected', [False, True])
def test_plain_selected_lead_is_a_valid_alternative_to_expressive_parallel_fill(later_fill_selected):
    rows = {'owner': {('notes', i): event(i, i+1, 12+i) for i in range(8)},
            'fills': {('notes', i): event(2+i*.5, 2.5+i*.5, 3+i, vb=True) for i in range(4)}}
    rows['fills'][('notes', 9)] = event(20, 21, 9)
    selected = {('owner', *key) for key in rows['owner']}
    if later_fill_selected:
        selected.add(('fills', 'notes', 9))
    result = local_lead_audit(rows, [], selected, set(rows), lambda q: q,
                             featured_sections=[{'start': 0, 'end': 8, 'sectionName': 'Guitar Solo'}],
                             featured_sources=set(rows), unknown_peers={'fills'})
    assert result['unresolvedCount'] == 0


def test_selected_event_outside_solo_cannot_establish_selected_owner_inside_it():
    rows, _, options = expressive_fixture()
    rows['fills'][('notes', 20)] = event(20, 21, 9)
    selected = {('fills', 'notes', 20)}
    assert local_lead_audit(rows, [], selected, set(rows), lambda q: q, **options)['unresolvedCount'] == 0


def test_a_real_plain_lead_rest_still_requires_a_strong_complete_answer():
    rows, selected, options = expressive_fixture()
    for row in rows['owner'].values():
        row['notes'][0].pop('vb')
    result = local_lead_audit(rows, [], selected, set(rows), lambda q: q, **options)
    assert result['unresolvedCount'] == 1
    assert result['unresolved'][0]['candidateEventCounts'] == {'fills': 8}


def test_two_held_notes_and_selected_response_are_continuous_play_not_silence():
    rows = {'owner': {('notes', 0): event(0, 3, 12), ('notes', 1): event(3, 6, 14)},
            'fills': {('notes', i): event(t, t+.5, 3+i, vb=True)
                      for i, t in enumerate((2, 2.5, 3, 3.5, 6, 6.5, 7, 7.5))}}
    selected = {('owner', *key) for key in rows['owner']} | {('fills', 'notes', i) for i in range(4, 8)}
    result = local_lead_audit(rows, [], selected, set(rows), lambda q: q,
                             featured_sections=[{'start': 0, 'end': 8, 'sectionName': 'Guitar Solo'}],
                             featured_sources=set(rows), unknown_peers={'fills'})
    assert result['unresolvedCount'] == 0


def test_generic_unknown_oracle_requires_insertable_rest_not_replacing_selected_backing():
    rows, selected, options = expressive_fixture()
    rows['backing'] = {('chords', 0): event(2, 7, 3)}
    selected.add(('backing', 'chords', 0))
    assert local_lead_audit(rows, [], selected, set(rows), lambda q: q, **options)['unresolvedCount'] == 0


@pytest.mark.parametrize('owner_kind', ['labelled', 'dedicated', 'manual_layer'])
@pytest.mark.parametrize('filled', [False, True])
def test_held_lead_archive_incumbent_is_independent_of_compulsory_peer_thresholds(tmp_path, monkeypatch, owner_kind, filled):
    from test_song_import_score import beat, measure, raw_score
    from test_songsterr_hybrid_lead import rest, build
    from feedback_converter.song_import import hybrid_selection

    doc = raw_score([
        measure(beat(12, duration=(3, 4)), beat(14, duration=(3, 4)),
                signature=[6, 4], marker={'text': 'Guitar Solo'}),
        measure(rest((1, 2)), signature=[2, 4])])
    doc['tracks'][0]['name'] = {'labelled': 'Lead Guitar', 'dedicated': 'Guitar Solo',
                               'manual_layer': 'Player | Echo Guitar'}[owner_kind]
    doc['tracks'].append({**deepcopy(doc['tracks'][0]), 'id': 1, 'name': 'Gary - Fills'})
    doc['parts'].append({'measures': [
        measure(rest((1, 2)), *[beat(f, duration=(1, 8), vibrato=True) for f in (3, 4, 5, 6)],
                rest((1, 2)), signature=[6, 4]),
        measure(*[beat(f, duration=(1, 8), vibrato=True) for f in (7, 8, 9, 10)], signature=[2, 4])]})
    owner = {'id': 'held-owner', 'trackId': '0', 'start': 0, 'end': 6,
             'ownedStart': 0, 'ownedEnd': 6, 'priority': 'solo', 'evidence': 'regional_lead',
             'confidence': 'medium', 'eligible': True, 'score': 10,
             'sectionName': 'Guitar Solo', 'labelledSolo': True}
    evidence = [owner]
    if filled:
        evidence.append({**owner, 'id': 'response', 'trackId': '1', 'start': 6, 'end': 8,
                         'ownedStart': 6, 'ownedEnd': 8})
    monkeypatch.setattr(hybrid_selection, 'regional_candidates', lambda *args, **kwargs: deepcopy(evidence))
    overrides = {'mainTrackId': '0'}
    if owner_kind == 'manual_layer':
        overrides['roles'] = {'0': 'lead'}
    *_, report = build(tmp_path, doc, overrides=overrides)
    if filled:
        assert report['status'] == 'passed', report
    else:
        errors = [e for e in report['errors'] if e['code'] == 'hybrid_local_lead_coverage']
        assert len(errors) == 1, report
        assert errors[0]['expected']['candidateEventCounts'] == {'1': 4}
        assert errors[0]['expected']['start'] == pytest.approx(6, abs=1e-5)


def test_slide_destination_zero_is_expressive_source_evidence():
    rows, selected, options = expressive_fixture()
    for row in rows['fills'].values():
        row['notes'][0].pop('ho', None)
        row['notes'][0].pop('vb', None)
    for index in (1, 2):
        rows['fills'][('notes', index)]['notes'][0]['sl'] = 0
    assert local_lead_audit(rows, [], selected, set(rows), lambda q: q, **options)['unresolvedCount'] == 1


def test_multiple_fingerings_of_one_pitch_do_not_prove_melodic_variety():
    rows, selected, options = expressive_fixture()
    options['source_tunings'] = {'fills': [40, 45, 50, 55, 59, 64]}
    for i, row in enumerate(rows['fills'].values()):
        row['notes'][0].update(s=i%4, f=60-options['source_tunings']['fills'][i%4])
    assert local_lead_audit(rows, [], selected, set(rows), lambda q: q, **options)['unresolvedCount'] == 0


@pytest.mark.parametrize('case', ['scattered', 'overlapping_crossing_voice'])
def test_unknown_response_requires_contiguous_complete_copyable_source(case):
    rows, selected, options = expressive_fixture()
    if case == 'scattered':
        rows['fills'] = {('notes', i): event(2+i*1.2, 2.1+i*1.2, 3+i, vb=True) for i in range(4)}
    else:
        rows['fills'][('notes', 20)] = event(1, 6, 22)
    assert local_lead_audit(rows, [], selected, set(rows), lambda q: q, **options)['unresolvedCount'] == 0


@pytest.mark.parametrize('fault', ['plain', 'repeating', 'chordal', 'layer', 'unavailable', 'no_solo', 'no_owner', 'crossing'])
def test_generic_unknown_peer_evidence_cannot_promote_backing_or_unsafe_source(fault):
    rows, selected, options = expressive_fixture()
    available = set(rows)
    if fault == 'plain':
        for row in rows['fills'].values():
            row['notes'][0].pop('ho', None)
            row['notes'][0].pop('vb', None)
    elif fault == 'repeating':
        rows['fills'] = {('notes', i): event(2+i*.25, 2.25+i*.25, 3+i%4, vb=True) for i in range(16)}
    elif fault == 'chordal':
        for row in rows['fills'].values():
            row['notes'].append({**row['notes'][0], 's': 3})
    elif fault == 'layer':
        options['featured_sources'].remove('fills')
    elif fault == 'unavailable':
        available.remove('fills')
    elif fault == 'no_solo':
        options['featured_sections'] = []
    elif fault == 'no_owner':
        selected.clear()
    elif fault == 'crossing':
        for row in rows['fills'].values():
            row.update(start=1, end=8)
    assert local_lead_audit(rows, [], selected, available, lambda q: q, **options)['unresolvedCount'] == 0


def test_generic_solo_context_scans_share_the_explicit_audit_budget():
    rows, selected, options = expressive_fixture()
    result = local_lead_audit(rows, [], selected, set(rows), lambda q: q, max_group_checks=1, **options)
    assert result['budgetLimited'] and result['groupChecks'] == 1


def unsupported_fixture():
    rows, selected, options = expressive_fixture()
    options['unknown_peers'] = set()
    options.update(unsupported_spans={'owner': [(2, 7)]}, fallback_sources={'fills'},
                   incumbent_eligible=lambda tid, start, end: tid == 'owner', quarter_at=lambda q: q)
    return rows, selected, options


def test_proven_unsupported_owner_rest_requires_compatible_complete_known_lead():
    rows, selected, options = unsupported_fixture()
    result = local_lead_audit(rows, [], selected, set(rows), lambda q: q, **options)
    assert result['unresolvedCount'] == 1
    missing = result['unresolved'][0]
    assert missing['reason'] == 'unfilled_unsupported_lead_fallback'
    assert missing['ownerTrackId'] == 'owner'
    assert missing['candidateEventCounts'] == {'fills': 7}, 'Left switch guard excludes only the first complete attack.'
    selected.update(('fills', 'notes', i) for i in range(8))
    assert local_lead_audit(rows, [], selected, set(rows), lambda q: q, **options)['unresolvedCount'] == 0
    selected.difference_update(('fills', 'notes', i) for i in range(8))
    assert local_lead_audit(rows, [], selected, set(rows), lambda q: q, **options)['unresolvedCount'] == 1


@pytest.mark.parametrize('case', ['no_proven_omission', 'nonfeatured', 'no_selected_owner', 'excluded',
                                 'not_known_lead', 'plain_backing', 'crossing', 'physical_owner', 'supported_owner'])
def test_unsupported_fallback_is_not_a_general_written_rest_or_backing_relaxation(case):
    rows, selected, options = unsupported_fixture()
    available = set(rows)
    if case == 'no_proven_omission':
        options['unsupported_spans'] = {}
    elif case == 'nonfeatured':
        options['featured_sections'] = []
    elif case == 'no_selected_owner':
        selected.clear()
    elif case == 'excluded':
        available.remove('fills')
    elif case == 'not_known_lead':
        options['fallback_sources'] = set()
    elif case == 'plain_backing':
        for row in rows['fills'].values():
            row['notes'][0].pop('ho', None)
            row['notes'][0].pop('vb', None)
    elif case == 'crossing':
        rows['fills'][('notes', 20)] = event(1, 8, 22)
    else:
        rows['owner'][('notes', 20)] = event(2, 7, 5)
        if case == 'physical_owner':
            selected.add(('owner', 'notes', 20))
            options['supported_rows'] = {**rows, 'owner': {}}
    assert local_lead_audit(rows, [], selected, available, lambda q: q, **options)['unresolvedCount'] == 0


def test_projected_low_endpoint_does_not_mask_a_proven_unsupported_whole_gesture():
    rows, selected, options = unsupported_fixture()
    options['supported_rows'] = deepcopy(rows)
    rows['owner'][('notes', 20)] = event(2, 7, 22, sl=29)
    assert local_lead_audit(rows, [], selected, set(rows), lambda q: q, **options)['unresolvedCount'] == 1


def test_supported_parallel_owner_blocks_even_when_local_owner_label_is_ambiguous():
    rows, selected, options = unsupported_fixture()
    rows['owner'][('notes', 20)] = event(2, 7, 5)
    options['peer_eligible'] = lambda *args: False
    assert local_lead_audit(rows, [], selected, set(rows), lambda q: q, **options)['unresolvedCount'] == 0


def test_raw_unsupported_peer_endpoints_are_not_mandatory_fallback_notes():
    rows, selected, options = unsupported_fixture()
    bad = [{'sourceIds': set(row['sourceIds']), 'occurrences': {1}} for row in rows['fills'].values()]
    options['supported_rows'] = _supported_gestures(rows, {'fills': bad})
    assert local_lead_audit(rows, [], selected, set(rows), lambda q: q, **options)['unresolvedCount'] == 0
    # Uncopyable projected fragments remain physical blockers. A complete
    # parallel voice cannot bypass a half-retained raw hard gesture.
    for i in range(8):
        row = event(2+i*.5, 2.5+i*.5, 4+i, vb=True)
        row['sourceIds'] = [f'other-voice-{i}']
        rows['fills'][('notes', 30+i)] = row
    options['supported_rows'] = _supported_gestures(rows, {'fills': bad})
    assert local_lead_audit(rows, [], selected, set(rows), lambda q: q, **options)['unresolvedCount'] == 0
    # But an unrelated complete voice outside that fragment's actual footprint
    # stays available: the whole raw unsupported span is not a blanket veto.
    rows['fills'] = {('notes', 0): event(2, 3, 22, sl=29)}
    bad = [{'sourceIds': set(rows['fills'][('notes', 0)]['sourceIds']), 'occurrences': {1}, 'start': 2, 'end': 7}]
    for i in range(6):
        row = event(3.5+i*.5, 4+i*.5, 4+i, vb=True)
        row['sourceIds'] = [f'other-voice-{i}']
        rows['fills'][('notes', 30+i)] = row
    options['supported_rows'] = _supported_gestures(rows, {'fills': bad})
    assert local_lead_audit(rows, [], selected, set(rows), lambda q: q, **options)['unresolvedCount'] == 1


def test_unsupported_gesture_identity_does_not_release_another_repeat_or_voice():
    rows = {'a': {('notes', 0): event(1, 2), ('notes', 1): event(5, 6), ('notes', 2): event(1, 2)}}
    rows['a'][('notes', 0)].update(sourceIds=['same-written-note'], occurrences=[1])
    rows['a'][('notes', 1)].update(sourceIds=['same-written-note'], occurrences=[2])
    rows['a'][('notes', 2)].update(sourceIds=['other-voice'], occurrences=[1])
    groups = {'a': [{'sourceIds': {'same-written-note'}, 'occurrences': {1}}]}
    assert set(_supported_gestures(rows, groups)['a']) == {('notes', 1), ('notes', 2)}


def test_known_expressive_lead_can_repeat_a_motif_when_replacing_unsupported_owner():
    rows, selected, options = unsupported_fixture()
    rows['fills'] = {('notes', i): event(2.5+i*.5, 3+i*.5, f, vb=True)
                     for i, f in enumerate((3, 5, 7, 8, 3, 5, 7, 9))}
    assert local_lead_audit(rows, [], selected, set(rows), lambda q: q, **options)['unresolvedCount'] == 1


def test_unsupported_fallback_scans_share_the_operation_budget():
    rows, selected, options = unsupported_fixture()
    result = local_lead_audit(rows, [], selected, set(rows), lambda q: q, max_group_checks=1, **options)
    assert result['budgetLimited'] and result['groupChecks'] == 1


@pytest.mark.parametrize('case', ['missing', 'recovered', 'unsupported_slide', 'accompaniment', 'excluded',
                                 'tuning', 'harmony', 'fx', 'rhythm', 'ordinary_rest'])
def test_unsupported_owner_archive_fallback_requires_raw_source_proof(tmp_path, monkeypatch, case):
    from test_song_import_score import beat, measure, raw_score
    from test_songsterr_hybrid_lead import rest, build
    from feedback_converter.song_import import hybrid_handover, hybrid_selection
    melody = lambda: measure(*[beat(f, duration=(1, 4), vibrato=True) for f in (12, 14, 17, 15)])
    first = melody()
    first['marker'] = {'text': 'Duane - Slide Solo'}
    middle = measure(beat(29))
    if case == 'unsupported_slide':
        middle = measure(beat(22, duration=(1, 4), slide='shift'), beat(29, duration=(3, 4)))
    elif case == 'ordinary_rest':
        middle = measure(rest())
    doc = raw_score([first, middle, melody()])
    doc['tracks'][0]['name'] = 'Lead & Slide Solo | Duane | Guitar Model'
    doc['tracks'].append({**deepcopy(doc['tracks'][0]), 'id': 1, 'name': {
        'harmony': 'Eric | Lead Harmony', 'fx': 'Eric | Echo Guitar', 'rhythm': 'Eric | Rhythm Guitar'
    }.get(case, 'Eric | Lead Guitar')})
    doc['parts'].append({'measures': [measure(rest()), measure(*[
        beat(f, duration=(1, 8), vibrato=True) for f in (3, 5, 8, 7, 10, 9, 12, 11)]), measure(rest())]})
    overrides = {'mainTrackId': '0'}
    if case == 'accompaniment':
        overrides['roles'] = {'1': 'accompaniment'}
    elif case == 'excluded':
        overrides['excludedTrackIds'] = ['1']
    elif case == 'tuning':
        doc['tracks'][1]['tuning'] = [pitch-2 for pitch in doc['tracks'][1]['tuning']]
    parent = {'id': 'owner-solo', 'trackId': '0', 'start': 0, 'end': 12,
              'ownedStart': 0, 'ownedEnd': 12, 'priority': 'solo', 'evidence': 'regional_lead',
              'confidence': 'medium', 'eligible': True, 'score': 10,
              'sectionName': 'Duane - Slide Solo', 'labelledSolo': True}
    evidence = [parent]
    monkeypatch.setattr(hybrid_selection, 'regional_candidates', lambda *a, **kw: deepcopy(evidence))
    if case != 'recovered':
        monkeypatch.setattr(hybrid_handover, 'refine_handovers', lambda selected, *a: (selected, [], [], {'budgetLimited': False}))
    *_, report = build(tmp_path, doc, overrides=overrides)
    errors = [e for e in report['errors'] if e['code'] == 'hybrid_local_lead_coverage'
              and e['expected'].get('reason') == 'unfilled_unsupported_lead_fallback']
    if case in {'missing', 'unsupported_slide'}:
        assert len(errors) == 1, report
        assert errors[0]['expected']['candidateTrackIds'] == ['1']
    else:
        assert not errors, report
        if case == 'recovered':
            assert report['status'] == 'passed', report
            assert report['omissions']['omittedNotes'] == 1


@pytest.mark.parametrize('case', ['recovered', 'missing', 'accompaniment', 'excluded', 'tuning',
                                 'harmony', 'fx', 'effects', 'feedback', 'rhythm', 'non_guitar_solo'])
def test_generic_solo_archive_oracle_uses_source_context_and_respects_exclusions(tmp_path, monkeypatch, case):
    from test_song_import_score import beat, measure, raw_score
    from test_songsterr_hybrid_lead import rest, build
    from feedback_converter.song_import import hybrid_handover

    label = 'Piano Solo' if case == 'non_guitar_solo' else 'Guitar Solo 2'
    melody = lambda: measure(*[beat(fret, duration=(1, 4), vibrato=True) for fret in (12, 14, 17, 15)])
    first = melody()
    first['marker'] = {'text': label}
    doc = raw_score([first, measure(rest()), melody()])
    doc['tracks'].append({**deepcopy(doc['tracks'][0]), 'id': 1, 'name': {
        'harmony': 'Harmony Guitar', 'fx': 'Echo Guitar', 'effects': 'Player | Effects Guitar',
        'feedback': 'Player | Feedback Guitar', 'rhythm': 'Rhythm Guitar'}.get(case, 'Gary - Fills')})
    doc['parts'].append({'measures': [measure(rest()), measure(*[
        beat(fret, duration=(1, 4), vibrato=True) for fret in (3, 5, 8, 7)]), measure(rest())]})
    overrides = {'mainTrackId': '0'}
    if case == 'accompaniment':
        overrides['roles'] = {'1': 'accompaniment'}
    elif case == 'excluded':
        overrides['excludedTrackIds'] = ['1']
    elif case == 'tuning':
        doc['tracks'][1]['tuning'] = [pitch-2 for pitch in doc['tracks'][1]['tuning']]
    if case != 'recovered':
        # Model the old complete-owner-but-silent output, without fabricating
        # source notes or allowing the oracle to consult producer candidates.
        monkeypatch.setattr(hybrid_handover, 'refine_handovers',
                            lambda selected, *args: (selected, [], [], {'budgetLimited': False}))
    *_, report = build(tmp_path, doc, overrides=overrides)
    codes = {error['code'] for error in report['errors']}
    if case == 'missing':
        assert 'hybrid_local_lead_coverage' in codes
        missing = report['hybridLead']['musicalAudit']['localLeadAudit']['unresolved']
        assert missing[0]['candidateTrackIds'] == ['1']
    else:
        assert 'hybrid_local_lead_coverage' not in codes, report
        if case == 'recovered':
            assert report['status'] == 'passed', report


def test_optional_safe_long_opportunity_is_warning_not_lead_obligation():
    rows = {'main': {('notes', 0): event(0, 1), ('notes', 1): event(11, 12)},
            'rhythm': {('notes', i): event(i, i+1) for i in range(2, 10)}}
    selected = {('main', *key) for key in rows['main']}
    parts = {tid: {'source': SimpleNamespace(name=tid), 'notation_beats': []} for tid in rows}
    result = optional_fill_audit(rows, parts, selected, set(rows), set(), 12, lambda q: q, lambda q: q)
    assert result['opportunityCount'] == 1
    assert result['opportunities'][0]['activeQuarterBeats'] == {'rhythm': 8}
    assert local_lead_audit(rows, [], selected, set(rows), lambda q: q)['unresolvedCount'] == 0
    assert optional_fill_audit(rows, parts, selected, {'main'}, set(), 12, lambda q: q, lambda q: q)['opportunityCount'] == 0
    limited = optional_fill_audit(rows, parts, selected, set(rows), set(), 12, lambda q: q, lambda q: q, max_group_checks=1)
    assert limited['budgetLimited'] is True
    assert limited['groupChecks'] == 1


def test_optional_warning_does_not_count_silent_hard_gesture_bridges_as_activity():
    rows = {'main': {('notes', 0): event(0, 1), ('notes', 1): event(11, 12)},
            'rhythm': {('notes', 0): event(2, 2.25), ('notes', 1): event(9, 9.25)}}
    for row in rows['rhythm'].values():
        row['start'], row['end'] = 2, 9.25
    selected = {('main', *key) for key in rows['main']}
    parts = {tid: {'source': SimpleNamespace(name=tid), 'notation_beats': []} for tid in rows}
    assert optional_fill_audit(rows, parts, selected, set(rows), set(), 12, lambda q: q, lambda q: q)['opportunityCount'] == 0


@pytest.mark.parametrize('fault', [None, 'missing_original_event', 'fake_bounds', 'lost_dependency', 'wrong_setup'])
def test_reservation_requires_retained_complete_original_owner(fault):
    rows, _, selected = fixture()
    refs = [{'kind': kind, 'index': index, 'sourceIds': row['sourceIds'], 'occurrences': row['occurrences']}
            for (kind, index), row in rows['a'].items()]
    reservation = {'trackId': 'a', 'start': 0, 'end': 8, 'ownedStart': 0, 'ownedEnd': 8, 'events': refs}
    compatible = {'a', 'b'}
    if fault == 'missing_original_event':
        selected.remove(('a', 'notes', 1))
    elif fault == 'fake_bounds':
        reservation['end'] = 9
    elif fault == 'lost_dependency':
        reservation['events'].pop()
    elif fault == 'wrong_setup':
        compatible.remove('a')
    check = Check()
    result = _verified_reservations({'primaryReservations': [reservation]}, rows, selected, compatible, set(), 12, check)
    assert bool(result) == (fault is None)
    assert bool(check.errors) == (fault is not None)


@pytest.mark.parametrize('edge,rounding_only', [('left', True), ('right', True),
                                              ('left', False), ('right', False)])
def test_subphrase_parent_enclosure_tolerates_only_numerical_drift(tmp_path, edge, rounding_only):
    import json
    import math
    from zipfile import ZipFile
    from test_song_import_score import beat, measure, raw_score
    from test_songsterr_hybrid_lead import rest, build
    from feedback_converter.song_import.verification import verify_import

    doc = raw_score([measure(beat(3)), measure(rest()), measure(rest()),
                     measure(rest((3, 4)), beat(12, duration=(1, 4)))])
    doc['tracks'].append({**deepcopy(doc['tracks'][0]), 'id': 1, 'name': 'Rhythm Guitar'})
    doc['parts'].append({'measures': [measure(*[
        beat(3 + bar + i % 2, duration=(1, 4)) for i in range(4)]) for bar in range(4)]})
    source, _, options, alignment, original, first = build(
        tmp_path, doc, overrides={'mainTrackId': '0', 'roles': {'1': 'accompaniment'}})
    assert first['status'] == 'passed', first
    with ZipFile(original) as old:
        files = {name: old.read(name) for name in old.namelist()}
    receipt = json.loads(files['import/hybrid-lead.json'])
    variants = [p['variant'] for p in receipt['passages'] if 'variant' in p]
    assert variants
    for variant in variants:
        index, key, direction = (0, 'parentStart', -1) if edge == 'left' else (1, 'parentEnd', 1)
        boundary = variant['parentBoundaryQuarters'][index]
        variant[key] = (math.nextafter(boundary, direction * math.inf) if rounding_only
                        else boundary + direction * .001)
    files['import/hybrid-lead.json'] = json.dumps(receipt).encode()
    target = tmp_path / 'parent-enclosure.feedpak'
    with ZipFile(target, 'w') as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    result = verify_import(source, target, alignment, hybrid_options=options)
    if rounding_only:
        assert result['status'] == 'passed', result
    else:
        assert 'hybrid_subphrase' in {error['code'] for error in result['errors']}

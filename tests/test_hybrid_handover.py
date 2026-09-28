"""A chosen soloist's written rest can hold a complete peer lead response."""
from copy import deepcopy

import pytest

from feedback_converter.song_import.hybrid_context import Clock
from feedback_converter.song_import import hybrid_handover as handover
from feedback_converter.song_import import hybrid_regional as regional


STANDARD = [40, 45, 50, 55, 59, 64]


def row(index, start, end, *, onset=None, fret=7, **techniques):
    onset = start if onset is None else onset
    return {'kind': 'notes', 'index': index, 'start': start, 'end': end, 'available': True,
            'sourceIds': [f'n:{index}'], 'occurrences': [1],
            'notes': [dict(t=onset/2, sus=(end-onset)/2, s=2, f=fret, **techniques)]}


def fixture(peer_is_base=True):
    peer = '0' if peer_is_base else '2'
    tracks = {tid: {'id': tid, 'name': name, 'instrument': 'guitar', 'tuning': list(STANDARD), 'capo': 0}
              for tid, name in [('0', 'Alex | Lead Guitar' if peer_is_base else 'Rhythm Guitar'),
                                ('1', 'Blake | Lead Guitar'), ('2', 'Alex | Lead Guitar')]}
    owner_rows = [row(0, 0, 4, fret=15, vb=True), row(1, 8, 12, fret=17, vb=True)]
    peers = [row(0, 4.5, 5, fret=12), row(1, 5, 6, fret=11, ln=True),
             row(2, 5, 6, onset=5.5, fret=9, po=True), row(3, 7, 9, fret=7, vb=True)]
    rows = {'0': peers if peer_is_base else [row(0, 6.25, 6.5, fret=3)],
            '1': owner_rows, '2': [] if peer_is_base else peers}
    context = {'tracks': {tid: {'beats': []} for tid in tracks}}
    context['tracks']['1']['beats'] = [{'start': r['start'], 'end': r['end'], 'rest': False} for r in owner_rows]
    clock = Clock({'tempoPoints': [{'quarter': 0, 'time': 0, 'bpm': 120}]})
    evidence = {'id': 'owner', 'trackId': '1', 'start': 0, 'end': 12, 'ownedStart': 0, 'ownedEnd': 12,
                'priority': 'solo', 'evidence': 'named_soloist', 'confidence': 'high', 'eligible': True,
                'sectionName': 'Solo (Alex & Blake)', 'labelledSolo': True}
    selected = [regional._passage(evidence, owner_rows, clock, 0, 12)]
    return peer, selected, [evidence], rows, tracks, context, {}, '0', clock, []


def run(case):
    return handover.refine_handovers(*case[1:])


@pytest.mark.parametrize('peer_is_base', [True, False])
def test_complete_response_preserves_owner_and_linked_peer_but_not_crossing_tail(peer_is_base):
    case = fixture(peer_is_base)
    selected, added, reservations, audit = run(case)
    assert {(p['trackId'], r['index']) for p in selected for r in p['events']} == {
        ('1', 0), ('1', 1), (case[0], 0), (case[0], 1), (case[0], 2)}
    assert [(p['trackId'], p['start'], p['end']) for p in selected] == [
        ('1', 0, 4), (case[0], 4.5, 6), ('1', 8, 12)]
    assert reservations == case[1]
    assert added[0]['selectionEvidence']['reason'] == 'local_lead_handover'
    assert added[0]['selectionEvidence']['holeStart'] == 4
    assert added[0]['selectionEvidence']['holeEnd'] == 8
    assert not audit['budgetLimited']


@pytest.mark.parametrize('fault', ['tuning', 'capo', 'excluded', 'accompaniment', 'harmony', 'extra', 'fx', 'unavailable'])
def test_nonforeground_or_unplayable_peer_is_not_a_handover(fault):
    case = fixture()
    tid, tracks, options = case[0], case[4], case[6]
    if fault == 'tuning':
        # The peer is the base, so change the incumbent setup to preserve the
        # comparison's immutable base signature rather than changing the base.
        tracks['1']['tuning'][0] -= 2
        case = (case[0], *case[1:7], '1', *case[8:])
    elif fault == 'capo':
        tracks[tid]['capo'] = 1
        case = (case[0], *case[1:7], '1', *case[8:])
    elif fault == 'excluded':
        options['excludedTrackIds'] = [tid]
    elif fault == 'accompaniment':
        options['roles'] = {tid: 'accompaniment'}
    elif fault in {'harmony', 'extra', 'fx'}:
        tracks[tid]['name'] = 'Alex | ' + {'harmony': 'Harmony Lead Guitar', 'extra': 'Extra Lead Guitar', 'fx': 'Solo FX Guitar'}[fault]
    else:
        for r in case[3][tid]:
            r['available'] = False
    selected, added, reservations, _ = run(case)
    assert selected == case[1]
    assert not added and not reservations


def test_written_slot_protects_sounding_rest():
    case = fixture()
    case[5]['tracks']['1']['beats'].append({'start': 4, 'end': 8, 'rest': False})
    selected, added, reservations, _ = run(case)
    assert selected == case[1] and not added and not reservations


def test_hard_owner_component_protects_internal_gap():
    case = fixture()
    for r in case[3]['1']:
        r['start'], r['end'] = 0, 12
    selected, added, reservations, _ = run(case)
    assert selected == case[1] and not added and not reservations


def unsupported_owner_fixture(split=True, owner_is_base=True):
    case = list(fixture(owner_is_base))
    # The high-fret owner is the base in Layla; its compatible peer is source0.
    if owner_is_base:
        case[7] = '1'
    beat = {'sourceId': 'owner:bad', 'noteIds': ['owner:bad:n'], 'occurrence': 2,
            'start': 4, 'end': 8, 'rest': False}
    case[5]['tracks']['1']['beats'].append(beat)
    case[9].append({'trackId': '1', 'start': 4, 'end': 8, 'reason': 'unsupported_source_gesture',
                    'sourceIds': ['owner:bad:n'], 'occurrences': [2]})
    if split:
        case[1] = [regional._passage(case[2][0], case[3]['1'], case[8], lo, hi) for lo, hi in ((0, 4), (8, 12))]
    return case


@pytest.mark.parametrize('split,owner_is_base', [(True, True), (False, True), (True, False)])
def test_proven_unsupported_owner_slots_allow_complete_peer_lead_without_erasing_owner(split, owner_is_base):
    case = unsupported_owner_fixture(split, owner_is_base)
    selected, added, reservations, audit = run(case)
    assert {(p['trackId'], r['index']) for p in selected for r in p['events']} == {
        ('1', 0), ('1', 1), (case[0], 0), (case[0], 1), (case[0], 2)}
    assert len(added) == 1 and added[0]['selectionEvidence']['unsupportedOwnerFallback']
    assert not any(not p['events'] or '_unsupportedFallback' in p for p in selected + reservations)
    assert not reservations if split else reservations == case[1]
    assert not audit['budgetLimited']


@pytest.mark.parametrize('fault', ['unproven', 'wrong_id', 'wrong_occurrence', 'partial_beat', 'short_proof',
                                  'supported_parallel_slot', 'retained_owner', 'retained_base', 'no_selected_owner',
                                  'nonfeatured', 'excluded', 'tuning', 'capo', 'accompaniment', 'effect',
                                  'harmony', 'unknown', 'rhythm'])
def test_unsupported_substitution_requires_exact_omission_and_empty_compatible_lead_window(fault):
    case = unsupported_owner_fixture()
    beat, bad = case[5]['tracks']['1']['beats'][-1], case[9][0]
    if fault == 'unproven':
        case[9].clear()
    elif fault == 'wrong_id':
        bad['sourceIds'] = ['some_other_note']
    elif fault == 'wrong_occurrence':
        bad['occurrences'] = [1]
    elif fault == 'partial_beat':
        beat['noteIds'].append('owner:supported:n')
    elif fault == 'short_proof':
        bad['end'] = 7
    elif fault == 'supported_parallel_slot':
        case[5]['tracks']['1']['beats'].append({**beat, 'sourceId': 'other_voice', 'noteIds': ['other_voice:n']})
    elif fault in {'retained_owner', 'retained_base'}:
        # A ghost pickup or held event stays occupied even beside omitted notes.
        retained = row(2, 4, 8, ghost=True)
        case[3]['1'].append(retained)
        if fault == 'retained_owner':
            case[1].append(regional._passage(case[2][0], [retained], case[8], 4, 8))
    elif fault == 'no_selected_owner':
        case[1].clear()
    elif fault == 'nonfeatured':
        case[2][0].update(priority='lead', evidence='regional_lead', labelledSolo=False)
    elif fault == 'excluded':
        case[6]['excludedTrackIds'] = [case[0]]
    elif fault == 'tuning':
        case[4][case[0]]['tuning'][0] -= 2
    elif fault == 'capo':
        case[4][case[0]]['capo'] = 1
    elif fault == 'accompaniment':
        case[6]['roles'] = {case[0]: 'accompaniment'}
    else:
        case[4][case[0]]['name'] = {'effect': 'Alex | Lead FX', 'harmony': 'Alex | Harmony Lead',
                                     'unknown': 'Alex | Fills', 'rhythm': 'Alex | Rhythm Guitar'}[fault]
    selected, added, reservations, _ = run(case)
    assert selected == sorted(case[1], key=lambda p: (p['start'], p['end'], p['trackId']))
    assert not added and not reservations


def test_zero_unsupported_substitution_budget_retains_owner_and_reports_limit(monkeypatch):
    case = unsupported_owner_fixture()
    monkeypatch.setattr(handover, 'MAX_HANDOVER_WINDOWS', 0)
    selected, added, reservations, audit = run(case)
    assert selected == case[1] and not added and not reservations and audit['budgetLimited']


def test_isolated_plain_peer_attack_does_not_create_chatter():
    case = fixture()
    case[3]['0'][:] = [row(0, 5, 5.5)]
    selected, added, reservations, _ = run(case)
    assert selected == case[1] and not added and not reservations


def test_expressive_held_peer_response_is_not_rejected_for_low_note_count():
    case = fixture()
    case[3]['0'][:] = [row(0, 5, 7, vb=True)]
    selected, added, _, _ = run(case)
    assert len(added) == 1
    assert any(p['trackId'] == '0' for p in selected)


def test_named_expressive_peer_can_live_on_rhythm_track():
    case = fixture()
    case[4]['0']['name'] = 'Alex | Rhythm Guitar'
    selected, added, _, _ = run(case)
    assert len(added) == 1
    assert any(p['trackId'] == '0' for p in selected)


def unknown_fills_fixture(peer_is_base=True):
    case = fixture(peer_is_base)
    peer = case[0]
    case[4][peer]['name'] = 'Gary Rossington - Fills'
    case[2][0].update(sectionName='Guitar Solo 2', evidence='regional_lead')
    frets = [3, 5, 7, 8, 10, 12, 11, 9, 6, 4, 2, 5]
    case[3][peer][:] = [row(i, 4.25+i*.25, 4.5+i*.25, fret=fret, vb=i in {2, 5, 8})
                         for i, fret in enumerate(frets)]
    # A few dyads do not invalidate a mostly single-note melodic response.
    for i in (3, 7):
        case[3][peer][i]['notes'].append({**case[3][peer][i]['notes'][0], 's': 3, 'f': frets[i]-2})
    # This final harmonic crosses the incumbent's return and must stay out.
    case[3][peer].append(row(12, 7.5, 9, fret=7, ph=True))
    return case


@pytest.mark.parametrize('peer_is_base', [True, False])
def test_unknown_expressive_fills_answer_generic_solo_without_losing_owner(peer_is_base):
    case = unknown_fills_fixture(peer_is_base)
    assert handover.parse_track(case[4][case[0]])['priorRole'] == 'unknown'
    selected, added, reservations, audit = run(case)
    assert {(p['trackId'], r['index']) for p in selected for r in p['events']} == {
        ('1', 0), ('1', 1), *((case[0], i) for i in range(12))}
    assert len(added) == 1 and reservations == case[1]
    assert (added[0]['start'], added[0]['end']) == (4.25, 7.25)
    assert not audit['budgetLimited']
    # Local inference must not rewrite the source's role or the base choice.
    assert handover.parse_track(case[4][case[0]])['priorRole'] == 'unknown'
    assert case[7] == '0'


@pytest.mark.parametrize('fault', ['accompaniment', 'fx', 'harmony', 'extra', 'rhythm',
                                  'unexpressive', 'one_ornament', 'repetitive', 'chordal',
                                  'parent_backing', 'excluded', 'tuning', 'capo', 'nonfeatured'])
def test_unknown_fills_require_local_foreground_and_safe_featured_context(fault):
    case = unknown_fills_fixture(False)
    peer, peers = case[0], case[3][case[0]]
    if fault == 'accompaniment':
        case[6]['roles'] = {peer: 'accompaniment'}
    elif fault in {'fx', 'harmony', 'extra', 'rhythm'}:
        case[4][peer]['name'] = 'Gary | ' + {'fx': 'FX Fills', 'harmony': 'Harmony Fills',
                                          'extra': 'Extra Fills', 'rhythm': 'Rhythm Guitar'}[fault]
    elif fault in {'unexpressive', 'one_ornament'}:
        for r in peers:
            for note in r['notes']:
                note['vb'] = fault == 'one_ornament' and r['index'] == 2
    elif fault == 'repetitive':
        peers[:] = [row(i, 4+i*.25, 4.25+i*.25, fret=3+i%4, vb=i%4 == 0) for i in range(16)]
    elif fault == 'chordal':
        for r in peers:
            r['notes'].append({**r['notes'][0], 's': 4})
    elif fault == 'parent_backing':
        peers.extend(row(20+i, i/16, (i+1)/16, fret=3+i%4, lr=True) for i in range(64))
    elif fault == 'excluded':
        case[6]['excludedTrackIds'] = [peer]
    elif fault == 'tuning':
        case[4][peer]['tuning'][0] -= 2
    elif fault == 'capo':
        case[4][peer]['capo'] = 1
    else:
        case[2][0].update(priority='accompaniment', labelledSolo=False)
        case[1][0]['priority'] = 'accompaniment'
    selected, added, reservations, _ = run(case)
    assert selected == case[1] and not added and not reservations


def test_separated_unknown_fragments_cannot_pool_foreground_evidence():
    case = unknown_fills_fixture()
    case[3]['0'][:] = [row(i, t, t+.25, fret=3+i*2, vb=i % 2 == 0)
                       for i, t in enumerate((4.25, 4.5, 6.5, 6.75))]
    assert handover._credible(case[4]['0'], handover.parse_track(case[4]['0']),
                              case[2][0], case[3]['0'], 4, 8, case[8], case[6])
    selected, added, reservations, _ = run(case)
    assert selected == case[1] and not added and not reservations


def test_unknown_strong_phrase_does_not_admit_a_separate_weak_fragment():
    case = unknown_fills_fixture()
    case[3]['0'][:] = [row(i, t, t+.25, fret=3+i*2, vb=i % 2 == 0)
                       for i, t in enumerate((4, 4.25, 4.5, 4.75, 6.5, 6.75))]
    selected, added, _, _ = run(case)
    assert len(added) == 1
    assert {r['index'] for p in selected if p['trackId'] == '0' for r in p['events']} == {0, 1, 2, 3}


def test_unknown_response_needs_four_pitched_attacks_not_an_unpitched_filler():
    case = unknown_fills_fixture()
    case[3]['0'][:] = [row(i, 4+i*.25, 4.25+i*.25, fret=fret, vb=i < 2)
                       for i, fret in enumerate((3, 5, 7, -1))]
    case[3]['0'][2]['notes'].append({**case[3]['0'][2]['notes'][0], 's': 3, 'f': 8})
    selected, added, reservations, _ = run(case)
    assert selected == case[1] and not added and not reservations


@pytest.mark.parametrize('evidence', ['named', 'manual'])
def test_known_peer_short_responses_do_not_need_unknown_inference(evidence):
    case = unknown_fills_fixture()
    case[3]['0'][:] = [row(i, t, t+.25, fret=3+i*2, vb=i % 2 == 0)
                       for i, t in enumerate((4.25, 4.5, 6.5, 6.75))]
    if evidence == 'named':
        case[4]['0']['name'] = 'Alex | Fills'
        case[2][0]['sectionName'] = 'Solo (Alex & Blake)'
    else:
        case[6]['roles'] = {'0': 'lead'}
    selected, added, _, _ = run(case)
    assert len(added) == 2
    assert {r['index'] for p in selected if p['trackId'] == '0' for r in p['events']} == {0, 1, 2, 3}


def test_unknown_complete_melodic_prefix_keeps_slide_without_crossing_expressive_tail():
    case = unknown_fills_fixture()
    case[3]['0'][:] = [row(i, 4+i*.25, 4.25+i*.25, fret=3+i*2, sl=5 if i == 0 else None)
                       for i in range(4)] + [row(4, 7.5, 9, fret=12, vb=True)]
    selected, added, _, _ = run(case)
    assert len(added) == 1
    assert {r['index'] for p in selected if p['trackId'] == '0' for r in p['events']} == {0, 1, 2, 3}


def test_repeating_backing_with_lead_label_is_not_a_peer_response():
    case = fixture()
    case[3]['0'][:] = [row(i, 4 + i*.25, 4.25 + i*.25, fret=3+i%4, pm=True) for i in range(16)]
    selected, added, reservations, _ = run(case)
    assert selected == case[1] and not added and not reservations


@pytest.mark.parametrize('budget', ['MAX_HANDOVER_WINDOWS', 'MAX_HANDOVER_CANDIDATES', 'MAX_HANDOVER_GROUP_CHECKS'])
def test_bounded_handover_keeps_original_owner(monkeypatch, budget):
    case = fixture()
    monkeypatch.setattr(handover, budget, 0)
    selected, added, reservations, audit = run(case)
    assert selected == case[1] and not added and not reservations
    assert audit['budgetLimited']


def test_track_iteration_order_does_not_change_handover():
    case = fixture(False)
    first = run(case)
    reversed_case = list(deepcopy(case))
    reversed_case[4] = dict(reversed(list(reversed_case[4].items())))
    assert run(reversed_case) == first


@pytest.mark.parametrize('peer_is_base', [True, False])
def test_backbone_only_restores_accepted_base_response_not_unrelated_backing(monkeypatch, peer_is_base):
    from feedback_converter.song_import import hybrid_selection
    case = fixture(peer_is_base)
    _, _, evidence, rows, tracks, _, options, main, _, _ = case
    timeline = {'tempoPoints': [{'quarter': 0, 'time': 0, 'bpm': 120}],
                'measures': [{'quarter': 0, 'quarters': 12}]}
    context = {'version': 1, 'timeline': timeline, 'tracks': {}}
    for tid, track in tracks.items():
        track.update(notes=[], chords=[], templates=[], role='lead')
        beats = []
        for i, source_row in enumerate(rows[tid]):
            note = {**source_row['notes'][0], 'source_ids': [f'{tid}:{i}:n']}
            track['notes'].append(note)
            beats.append({'sourceId': f'{tid}:{i}', 'noteIds': [f'{tid}:{i}:n'], 'occurrence': 1,
                          'start': note['t']*2, 'end': (note['t']+note['sus'])*2,
                          'rest': False, 'grace': False, 'voice': 0})
        context['tracks'][tid] = {'beats': beats}
    performance = {'tracks': list(tracks.values()), 'compositionContext': context,
                   'scoreTimeline': timeline, 'duration': 6, 'sections': [],
                   'hybridBaseSelection': {'mainTrackId': main}}
    monkeypatch.setattr(hybrid_selection, 'regional_candidates', lambda *args, **kwargs: deepcopy(evidence))
    result = regional.backbone(performance, options, main, {'offset': 0, 'scale': 1}, 6)
    assert result['primaryReservations']
    assert result['protected'] == [[0, 12]]
    if peer_is_base:
        assert {r['index'] for r in result['mainEvents']} == {0, 1, 2}
        assert {r['index'] for r in result['removedMain']} == {3}
    else:
        assert result['mainEvents'] == []
        assert result['removedMain'][0]['supersededBy'] == ['1']
        assert any(p['trackId'] == '2' for p in result['passages'])

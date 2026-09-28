"""Optional foreground is local musical evidence, not a source-wide override."""
from copy import deepcopy

import pytest

from feedback_converter.song_import.hybrid_context import Clock
from feedback_converter.song_import.hybrid_optional_foreground import recognize


STANDARD = [40, 45, 50, 55, 59, 64]
CLOCK = Clock({'tempoPoints': [{'quarter': 0, 'time': 0, 'bpm': 60}]})


def row(index, q, duration=.5, *, frets=(2,), strings=None, **effects):
    strings = strings or tuple(range(3, 3 + len(frets)))
    return {'kind': 'notes' if len(frets) == 1 else 'chords', 'index': index,
            'start': q, 'end': q + duration, 'sourceIds': [str(index)], 'occurrences': [1],
            'notes': [dict(t=q, sus=duration, f=f, s=s, **effects) for f, s in zip(frets, strings)]}


def line(count=16, start=8, duration=.5):
    values = [row(i, start + i * duration, duration, frets=(2 + i % 7,),
                  **({'sl': 4} if i % 4 == 0 else {'vb': True} if i % 4 == 3 else {}))
              for i in range(count)]
    for i in range(0, count - 1, 4):
        values[i]['end'] = values[i + 1]['end']
        values[i + 1]['start'] = values[i]['start']
    # A final slide endpoint is inside the last written slot for fixtures.
    if count % 4 == 1:
        values[-1]['notes'][0].pop('sl', None)
    return values


def arpeggio(count=64, start=0, duration=.5):
    return [row(i, start + i * duration, duration, frets=((0, 2, 2, 1)[i % 4],),
                strings=((4, 3, 2, 4)[i % 4],), lr=True) for i in range(count)]


def part(tid, values, start=None, end=None):
    return {'trackId': tid, 'start': min(r['start'] for r in values) if start is None else start,
            'end': max(r['end'] for r in values) if end is None else end,
            'activeQuarterBeats': sum(n['sus'] for r in values for n in r['notes']),
            'events': [{'kind': r['kind'], 'index': r['index']} for r in values]}


def case(melody=None, backing=None):
    melody = line() if melody is None else melody
    backing = arpeggio() if backing is None else backing
    rows = {'m': melody, 'b': backing, 'base': []}
    tracks = {tid: {'id': tid, 'name': 'unlabelled', 'instrument': 'guitar',
                    'tuning': list(STANDARD), 'capo': 0} for tid in rows}
    return [part('m', melody), part('b', backing)], rows, tracks


def run(fixture, **kwargs):
    return recognize(*fixture, CLOCK, main_id='base', **kwargs)


def test_inactive_base_clean_line_gets_exact_local_episode_without_mutation():
    fixture = case()
    original = deepcopy(fixture)
    result = run(fixture)
    assert not result['budgetLimited']
    assert fixture == original
    episode, = result['episodes']
    assert (episode['trackId'], episode['start'], episode['end']) == ('m', 8, 16)
    assert episode['candidateIndex'] == 0
    assert episode['competingCandidateIndices'] == [1]
    assert episode['evidence']['local']['expressionFamilies'] == ['slide', 'vibrato']
    assert 'pitch' not in episode['evidence']['local']
    assert episode['evidence']['competingAccompaniment'][0]['trackId'] == 'b'
    # Returning an 8-quarter episode never grants whole-track preference or
    # asks integration to discard the accompaniment at q0–8 or q16–32.
    assert fixture[0][1]['start'] == 0 and fixture[0][1]['end'] == 32


@pytest.mark.parametrize('count,start,step', [(59, 25, 35 / 59), (11, 416.25, 5.75 / 11)])
def test_complete_intro_and_outro_shaped_clean_phrases_qualify(count, start, step):
    fixture = case(line(count, start, step), arpeggio(128, start - 8))
    episode, = run(fixture)['episodes']
    assert episode['evidence']['local']['attacks'] == count
    assert episode['start'] == start
    assert episode['end'] == pytest.approx(start + count * step)


def test_names_register_and_input_order_do_not_create_preference():
    fixture = case()
    first, = run(fixture)['episodes']
    changed = deepcopy(fixture)
    changed[2]['m']['name'] = 'Distant Player | Rhythm Guitar'
    changed[2]['b']['name'] = 'Famous Player | Lead Solo'
    for track in changed[2].values():
        track['tuning'] = [p + 5 for p in track['tuning']]
    changed[0].reverse()
    second, = run(changed)['episodes']
    assert second['candidateId'] == first['candidateId']
    assert second['evidence']['local'] == first['evidence']['local']
    assert second['candidateIndex'] == 1 and second['competingCandidateIndices'] == [0]


@pytest.mark.parametrize('options', [
    {'excludedTrackIds': ['m']}, {'roles': {'m': 'accompaniment'}},
    {'roles': {'b': 'lead'}}, {'roles': {'b': 'solo'}},
    {'preferredTrackIds': ['b']}, {'preferredTrackIds': ['b', 'm']},
])
def test_explicit_source_choices_win(options):
    assert not run(case(), options=options)['episodes']


def test_preferred_melodic_candidate_can_still_be_recognized():
    assert run(case(), options={'preferredTrackIds': ['m', 'b']})['episodes']


@pytest.mark.parametrize('options', [{'preferredTrackIds': ['peer']}, {'roles': {'peer': 'lead'}}])
def test_even_small_overlap_preserves_explicit_preference_or_role(options):
    fixture = case()
    fixture[1]['peer'] = arpeggio(8, 6)
    fixture[2]['peer'] = {**fixture[2]['b'], 'id': 'peer'}
    fixture[0].append(part('peer', fixture[1]['peer']))  # Only q8–10 overlaps.
    assert not run(fixture, options=options)['episodes']


def test_nonoverlapping_preferred_source_does_not_become_global_veto():
    fixture = case()
    fixture[1]['peer'] = arpeggio(8, 0)
    fixture[2]['peer'] = {**fixture[2]['b'], 'id': 'peer'}
    fixture[0].append(part('peer', fixture[1]['peer']))
    assert run(fixture, options={'preferredTrackIds': ['peer']})['episodes']


def test_a_single_source_does_not_spend_foreground_budget():
    fixture = case()
    fixture[0].pop()
    result = run(fixture, max_operations=0)
    assert result['episodes'] == [] and result['operations'] == 0
    assert not result['budgetLimited'] and result['candidateCount'] == 1


@pytest.mark.parametrize('field', ['capo', 'tuning'])
def test_incompatible_foreground_is_not_proposed(field):
    fixture = case()
    fixture[2]['m'][field] = 1 if field == 'capo' else [38, 45, 50, 55, 59, 64]
    assert not run(fixture)['episodes']


def test_unforgiven_style_repeated_expressive_melody_is_not_demoted_as_repetition():
    values = line(64)
    for i, r in enumerate(values):
        r['notes'][0]['f'] = (2, 4, 5, 7)[i % 4]
    fixture = case(values, arpeggio(100))
    episode, = run(fixture)['episodes']
    assert episode['evidence']['local']['motifRepeat'] == 1


def test_jungle_style_repeated_palm_muted_single_note_riff_is_not_backing():
    backing = arpeggio()
    for r in backing:
        r['notes'][0].pop('lr')
        r['notes'][0]['pm'] = True
    assert not run(case(backing=backing))['episodes']


def test_free_bird_style_expressive_double_stops_are_a_valid_competing_solo():
    backing = [row(i, i * .5, frets=(12 + i % 2 * 2, 15), strings=(3, 4),
                   **({'bn': 2} if i % 12 == 0 else {})) for i in range(64)]
    assert not run(case(backing=backing))['episodes']


def test_plain_repeated_double_stops_are_not_enough_to_prove_backing():
    backing = [row(i, i * .5, frets=(12 + i % 2 * 2, 15), strings=(3, 4)) for i in range(64)]
    assert not run(case(backing=backing))['episodes']


def test_stationary_unexpressive_held_drone_does_not_veto_a_clear_melody():
    fixture = case()
    drone = [row(i, i * 4, 4, frets=(2, 2), strings=(2, 3)) for i in range(8)]
    fixture[1]['drone'] = drone
    fixture[2]['drone'] = {**fixture[2]['b'], 'id': 'drone'}
    fixture[0].extend(part('drone', [r]) for r in drone)
    assert run(fixture)['episodes']


def test_a_held_dyad_with_expression_remains_a_competing_voice():
    backing = [row(i, i * 4, 4, frets=(2, 2), strings=(2, 3), vb=True) for i in range(8)]
    assert not run(case(backing=backing))['episodes']


def test_short_arpeggio_variants_can_use_bounded_backing_context():
    fixture = case()
    fixture[0].extend(part('b', fixture[1]['b'][i:i + 8]) for i in range(16, 32, 8))
    episode, = run(fixture)['episodes']
    assert episode['competingCandidateIndices'] == [1, 2, 3]


def test_repeated_unexpressive_broad_chords_are_supported_backing():
    backing = [row(i, i * .5, frets=(2, 2, 1), strings=(2, 3, 4)) for i in range(64)]
    assert run(case(backing=backing))['episodes']


def test_genuine_competing_melodic_alternative_keeps_existing_search():
    fixture = case()
    fixture[1]['peer'] = line()
    fixture[2]['peer'] = {**fixture[2]['m'], 'id': 'peer'}
    fixture[0].append(part('peer', fixture[1]['peer']))
    assert not run(fixture)['episodes']


@pytest.mark.parametrize('dyads', [False, True])
def test_short_overlap_with_real_alternative_voice_cannot_be_clipped_away(dyads):
    fixture = case()
    peer = line(start=2)
    if dyads:
        peer = [row(i, 6 + i * .5, frets=(12 + i % 2 * 2, 15), strings=(3, 4),
                    **({'bn': 2} if i % 2 == 0 else {})) for i in range(8)]
    fixture[1]['peer'] = peer
    fixture[2]['peer'] = {**fixture[2]['m'], 'id': 'peer'}
    fixture[0].append(part('peer', peer))  # q8–10 overlap cannot disappear.
    assert not run(fixture)['episodes']


def test_physically_duplicate_expressive_voice_is_not_missing_foreground():
    fixture = case()
    fixture[1]['b'] = deepcopy(fixture[1]['m'])
    fixture[0][1] = part('b', fixture[1]['b'])
    assert not run(fixture)['episodes']


def test_killing_style_one_connected_slide_is_not_many_independent_phrases():
    melody = line(32)
    for r in melody:
        r['start'], r['end'] = 8, 24
        r['notes'][0]['sl'] = 0  # Destination zero is still expression.
    assert not run(case(melody=melody))['episodes']


def test_raw_expressive_points_outside_selectable_membership_cannot_qualify():
    fixture = case()
    for r in fixture[1]['m']:
        r['notes'][0].pop('sl', None)
        r['notes'][0].pop('vb', None)
    fixture[1]['m'].extend(line(24, 32))
    assert not run(fixture)['episodes']


@pytest.mark.parametrize('fault', ['missing_member', 'crossing_group', 'unavailable'])
def test_incomplete_or_unavailable_gestures_never_qualify(fault):
    fixture = case()
    if fault == 'missing_member':
        fixture[0][0]['events'].pop(1)
    elif fault == 'crossing_group':
        fixture[1]['m'][0]['start'] = 7
    else:
        fixture[1]['m'][0]['available'] = False
    assert not run(fixture)['episodes']


def test_single_ornament_cannot_borrow_other_context_expression():
    fixture = case()
    for i, r in enumerate(fixture[1]['m']):
        if i:
            r['notes'][0].pop('sl', None)
            r['notes'][0].pop('vb', None)
    assert not run(fixture)['episodes']


@pytest.mark.parametrize('flag', ['ghost', 'mt'])
def test_quiet_or_muted_articulation_cannot_supply_foreground_evidence(flag):
    fixture = case()
    for r in fixture[1]['m']:
        n = r['notes'][0]
        if n.get('sl') is not None or n.get('vb'):
            n[flag] = True
    assert not run(fixture)['episodes']


def test_zero_destination_slide_is_real_candidate_expression():
    fixture = case()
    for r in fixture[1]['m']:
        if 'sl' in r['notes'][0]:
            r['notes'][0]['sl'] = 0
    episode, = run(fixture)['episodes']
    assert 'slide' in episode['evidence']['local']['expressionFamilies']


def test_sparse_ornaments_cannot_replace_continuous_accompaniment():
    fixture = case()
    for r in fixture[1]['m']:
        r['notes'][0]['sus'] = .1
    assert not run(fixture)['episodes']


def test_same_source_nested_variants_return_one_complete_longest_episode():
    fixture = case()
    subset = fixture[1]['m'][:8]
    fixture[0].append(part('m', subset))
    episodes = run(fixture)['episodes']
    assert len(episodes) == 1
    assert episodes[0]['start'] == 8 and episodes[0]['end'] == 16


@pytest.mark.parametrize('limits,reason', [
    ({'max_candidates': 1}, 'candidate_limit'),
    ({'max_operations': 0}, 'operation_limit'),
    ({'max_operations': 300}, 'operation_limit'),
    ({'max_episodes': 0}, 'episode_limit'),
])
def test_budget_exhaustion_is_explicit_and_proposes_no_partial_replacement(limits, reason):
    result = run(case(), **limits)
    assert result['episodes'] == [] and result['budgetLimited']
    assert result['reason'] == reason
    assert result['operations'] <= limits.get('max_operations', 200000)

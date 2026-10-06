"""Legacy dead -1 keeps an unpitched strike and its authored provenance."""
from copy import deepcopy

import pytest

from feedback_converter.song_import.model import ScoreImportError
from feedback_converter.song_import.songsterr import parse
from feedback_converter.song_import.timeline import render
from test_song_import_score import beat, measure, raw_score

POLICY = 'songsterr-negative-fret-mute-v1'


def muted(fret=-1, duration=(1, 2), **fields):
    result = beat(fret, duration=duration, dead=True, **fields)
    if fret == 'absent':
        result['notes'][0].pop('fret')
    return result


def imported(document, *, selected=None):
    original = deepcopy(document)
    score = parse(document, track_indices=selected)
    performance = render(score)
    assert document == original
    assert score.source_document['document'] == original
    assert performance['sourceScore']['document'] == original
    return score, performance


def flat(track):
    return sorted(track['notes'] + [{**n, 't': c['t']} for c in track['chords'] for n in c['notes']],
                  key=lambda n: (n['t'], n['s']))


@pytest.mark.parametrize('program,tuning', [(29, [64, 59, 55, 50, 45, 40]), (33, [43, 38, 33, 28])])
@pytest.mark.parametrize('control_fret', ['absent', None])
@pytest.mark.parametrize('fields', [{}, {'ghost': True}, {'accentuated': True}, {'staccato': True},
                                   {'tremolo': True}, {'leftFingering': '2'}])
def test_existing_mute_controls_keep_exact_string_attack_duration_and_flags(program, tuning, control_fret, fields):
    document = raw_score([measure(muted(-1, (1, 4), **fields), beat(7, duration=(3, 4)))])
    document['tracks'][0].update(instrumentId=program, tuning=tuning)
    score, performance = imported(document)
    control = deepcopy(document)
    target = control['parts'][0]['measures'][0]['voices'][0]['beats'][0]['notes'][0]
    if control_fret == 'absent':
        target.pop('fret')
    else:
        target['fret'] = control_fret
    control_score, reference = imported(control)
    assert performance['tracks'] == reference['tracks']
    assert performance['beats'] == reference['beats'] and performance['duration'] == reference['duration']
    assert performance['source']['negativeFretMutePolicy'] == POLICY
    assert 'negativeFretMutePolicy' not in reference['source']
    note = score.tracks[0].bars[0][0]
    assert note.fret == 127 and note.authored_fret == -1 and note.effects['mt'] is True
    assert control_score.tracks[0].bars[0][0].authored_fret is None
    assert 'notation' not in performance['tracks'][0]
    child = flat(performance['tracks'][0])[0]
    assert child['f'] == 127 and child['s'] == len(tuning) - 1 and child['mt'] is True
    assert child['t'] == 0 and child['sus'] == (.25 if fields.get('staccato') else .5)


@pytest.mark.parametrize('fret', [-1.0, '-1', '-1/1', [-1, 1], [-2, 2], {'value': -1},
                                  -2, -1.5, True, False, float('nan'), float('inf')])
def test_only_literal_integer_negative_one_can_select_alias(fret):
    document = raw_score([measure(muted(fret, (1, 1)))])
    with pytest.raises(ScoreImportError):
        parse(document)


@pytest.mark.parametrize('dead', [None, False, 0, 1, 'true', [], {}])
def test_negative_one_requires_exact_dead_true(dead):
    document = raw_score([measure(muted(-1, (1, 1)))])
    document['parts'][0]['measures'][0]['voices'][0]['beats'][0]['notes'][0]['dead'] = dead
    with pytest.raises(ScoreImportError, match='Invalid authored fret'):
        parse(document)


@pytest.mark.parametrize('fret', [0, 7, 24, 48])
def test_positioned_dead_frets_remain_positioned_and_have_no_alias_marker(fret):
    score = parse(raw_score([measure(muted(fret, (1, 1)))]))
    note = score.tracks[0].bars[0][0]
    assert note.fret == fret and note.authored_fret is None and note.effects['mt'] is True
    assert 'negativeFretMutePolicy' not in score.source


@pytest.mark.parametrize('all_muted', [False, True])
def test_chord_template_distinguishes_present_mute_from_omitted_string(all_muted):
    first = muted(-1, (1, 1))
    first['notes'].append({'string': 1, 'fret': -1 if all_muted else 5, **({'dead': True} if all_muted else {})})
    _, performance = imported(raw_score([measure(first)]))
    track = performance['tracks'][0]
    assert not track['notes'] and len(track['chords']) == 1
    assert track['templates'][0]['frets'] == [-1, -1, -1, -1, 127 if all_muted else 5, 127]
    assert track['chords'][0]['notes'][-1]['f'] == 127
    assert all(n.get('mt') is True for n in track['chords'][0]['notes'] if n['f'] == 127)
    assert 'notation' not in track


@pytest.mark.parametrize('first,second,used', [(0, -1, 0), (-1, 0, None)])
def test_existing_muted_identity_retains_negative_authored_fret_and_effective_used_meaning(first, second, used):
    _, performance = imported(raw_score([measure(muted(first), muted(second, tie=True))]))
    note, = performance['tracks'][0]['notes']
    assert note['f'] == (127 if first == -1 else first) and note['mt'] is True
    assert note['t'] == 0 and note['sus'] == 2 and len(note['source_ids']) == 2
    row, = performance['mutedTieIdentityEvidence']
    assert row['authored'] == {'dead': True, 'fret': second}
    assert row['used'] == {'dead': True, 'fret': used}
    assert row['sourceId'] == 'songsterr:0:0:0:1:0' and row['originSourceId'] == 'songsterr:0:0:0:0:0'
    assert row['start'] == 1 and row['end'] == 2 and row['attack'] == 0


def test_chain_provenance_does_not_replace_absent_fret_with_negative_encoding():
    bars = [measure(muted(0, (1, 4)), muted(-1, (1, 4), tie=True),
                    muted(None, (1, 4), tie=True), muted(0, (1, 4), tie=True))]
    _, performance = imported(raw_score(bars))
    note, = performance['tracks'][0]['notes']
    assert note['f'] == 0 and note['sus'] == 2 and len(note['source_ids']) == 4
    rows = performance['mutedTieIdentityEvidence']
    assert [row['authored']['fret'] for row in rows] == [-1, None]
    assert all(row['used']['fret'] == 0 for row in rows)


@pytest.mark.parametrize('first,second,target', [(0, -1, 0), (-1, 0, 127)])
def test_repeat_visits_keep_identity_duration_and_authored_provenance(first, second, target):
    bars = [measure(muted(first, (1, 1)), repeatStart=True),
            measure(muted(second, (1, 1), tie=True), repeat=2)]
    _, performance = imported(raw_score(bars))
    notes = performance['tracks'][0]['notes']
    assert [(n['t'], n['f'], n['sus']) for n in notes] == [(0, target, 4), (4, target, 4)]
    rows = performance['mutedTieIdentityEvidence']
    assert [r['occurrence'] for r in rows] == [2, 4]
    assert all(r['authored']['fret'] == second and r['used']['fret'] == (None if target == 127 else target) for r in rows)
    assert [r['attack'] for r in rows] == [0, 4]


def test_same_target_alias_ties_extend_existing_attack_without_identity_rewrite():
    _, performance = imported(raw_score([measure(muted(-1), muted(-1, tie=True))]))
    note, = performance['tracks'][0]['notes']
    assert note['f'] == 127 and note['sus'] == 2 and len(note['source_ids']) == 2
    assert not performance.get('mutedTieIdentityEvidence')


@pytest.mark.parametrize('fault', ['rest', 'gap', 'orphan', 'voice', 'string', 'duplicate', 'pitched_origin', 'late_gesture'])
def test_alias_does_not_repair_unsafe_ties_or_slots(fault):
    first, second = muted(0), muted(-1, tie=True)
    document = raw_score([measure(first, second)])
    if fault in ('rest', 'gap'):
        first['duration'] = [1, 4]
        document['parts'][0]['measures'][0]['voices'][0]['beats'].insert(1,
            {'duration': [1, 4], 'notes': [{'rest': True}]} if fault == 'rest' else beat(7, string=1, duration=(1, 4)))
    elif fault == 'orphan':
        document['parts'][0]['measures'][0]['voices'][0]['beats'].pop(0)
    elif fault == 'voice':
        document['parts'][0]['measures'][0]['voices'][0]['beats'].pop(1)
        document['parts'][0]['measures'][0]['voices'].append({'beats': [second]})
    elif fault == 'string':
        second['notes'][0]['string'] = 1
    elif fault == 'duplicate':
        first['notes'].append(deepcopy(first['notes'][0]))
    elif fault == 'pitched_origin':
        first['notes'][0].pop('dead')
    elif fault == 'late_gesture':
        document['parts'][0]['measures'][0]['voices'][0]['beats'] = [muted(0, (1, 4)),
            muted(-1, (1, 4), tie=True), muted(0, (1, 2), tie=True, slide='upwards')]
    with pytest.raises(ScoreImportError):
        render(parse(document))


@pytest.mark.parametrize('fields', [{'hp': True}, {'bend': {'points': [{'position': 0, 'tone': 0}, {'position': 60, 'tone': 100}]}},
    {'harmonic': 'pinch'}, {'vibrato': True}, {'wideVibrato': True}, {'leftHandVibrato': 'slight'},
    {'slide': 'legato'}, {'slide': 'above'}, {'whammy': True},
    {'trill': {'auxiliaryFret': 2, 'speed': 480}}])
def test_existing_unpitched_pitch_gesture_guards_still_reject_alias(fields):
    with pytest.raises(ScoreImportError):
        parse(raw_score([measure(muted(-1, (1, 1), **fields))]))


@pytest.mark.parametrize('field,value', [('vibrato', True), ('wideVibrato', True),
    ('tremoloBar', {'points': [{'position': 0, 'tone': 0}, {'position': 60, 'tone': 100}]}),
    ('vibratoWithTremoloBar', 'slight')])
def test_inherited_pitch_expression_does_not_expand_new_alias_scope(field, value):
    first = muted(-1, (1, 1))
    first[field] = value
    with pytest.raises(ScoreImportError, match='inherited pitch expression'):
        parse(raw_score([measure(first)]))
    first['notes'][0]['fret'] = None
    # Preserve the old parser boundary for absent/null and positioned notes.
    assert parse(raw_score([measure(first)])).tracks[0].bars[0][0].fret == 127
    first['notes'][0]['fret'] = 7
    assert parse(raw_score([measure(first)])).tracks[0].bars[0][0].fret == 7


@pytest.mark.parametrize('field,value', [('vibrato', None), ('vibrato', False),
    ('wideVibrato', None), ('wideVibrato', False), ('tremoloBar', None), ('vibratoWithTremoloBar', None)])
def test_inactive_inherited_expression_flags_do_not_block_plain_alias(field, value):
    first = muted(-1, (1, 1))
    first[field] = value
    score, performance = imported(raw_score([measure(first)]))
    assert score.tracks[0].bars[0][0].authored_fret == -1
    assert performance['tracks'][0]['notes'][0]['f'] == 127


@pytest.mark.parametrize('field', ['tremoloBar', 'vibratoWithTremoloBar'])
@pytest.mark.parametrize('fret', [-1, None, 7])
def test_false_is_not_a_valid_inactive_whammy_shape_on_any_fret_encoding(field, fret):
    first = muted(fret, (1, 1))
    first[field] = False
    with pytest.raises(ScoreImportError, match='Unknown'):
        parse(raw_score([measure(first)]))


def test_pitched_hopo_cannot_gain_target_from_negative_mute():
    document = raw_score([measure(beat(7, duration=(1, 2), hp=True), muted(-1))])
    with pytest.raises(ScoreImportError, match='unpitched'):
        render(parse(document))


def test_policy_does_not_mark_rest_only_negative_note():
    document = raw_score([measure(beat(7, duration=(1, 2)), muted(-1, rest=True))])
    score, performance = imported(document)
    assert 'negativeFretMutePolicy' not in score.source and 'negativeFretMutePolicy' not in performance['source']


@pytest.mark.parametrize('program,selected', [(128, None), (0, None), (29, {0})])
def test_policy_counts_only_admitted_selected_playable_notes(program, selected):
    document = raw_score([measure(beat(7))])
    document['tracks'].append({'id': 'context', 'name': 'Context', 'instrumentId': program,
                                'tuning': [64, 59, 55, 50, 45, 40]})
    document['parts'].append({'measures': [measure(muted(-1, (1, 1)))]})
    score, performance = imported(document, selected=selected)
    assert 'negativeFretMutePolicy' not in score.source and 'negativeFretMutePolicy' not in performance['source']

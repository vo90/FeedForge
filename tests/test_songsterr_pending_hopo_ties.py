"""An outgoing HP mark consumed by a hidden tie cannot target a later attack."""
from copy import deepcopy
from fractions import Fraction
import hashlib
import json

import pytest

from feedback_converter.song_import.model import ScoreImportError
from feedback_converter.song_import.songsterr import parse
from feedback_converter.song_import.timeline import render
from test_song_import_score import beat, import_json, measure, raw_score
from test_songsterr_plain_tie_identity import flat, written


PROGRAMS = [(24, [64, 59, 55, 50, 45, 40]),
            (29, [64, 59, 55, 50, 45, 40]), (33, [43, 38, 33, 28])]
POLICY = 'songsterr-pending-hopo-immediate-plain-tie-v1'
RULE = 'pending-hopo-consumed-by-immediate-plain-tie'
RISE = {'points': [{'position': 0, 'tone': 0}, {'position': 60, 'tone': 100}]}


def source(origin=2, stored=0, destination=0, *, terminal=False):
    beats = [beat(origin, duration=(1, 4), hp=True),
             beat(stored, duration=(1, 4), tie=True)]
    if terminal:
        beats[0]['duration'] = beats[1]['duration'] = [1, 2]
    else:
        beats.append(beat(destination, duration=(1, 2)))
    return raw_score([measure(*beats)])


def produced(document):
    original = deepcopy(document)
    score = parse(document)
    before = deepcopy(score)
    result = render(score)
    assert score == before and document == original
    assert render(score) == result
    return result


def control_without_marker(document, marker=(0, 0, 0, 0)):
    control = deepcopy(document)
    bar, voice, index, note = marker
    control['parts'][0]['measures'][bar]['voices'][voice]['beats'][index]['notes'][note].pop('hp')
    return produced(control)


def musical_track(track):
    return {key: track[key] for key in ('notes', 'chords', 'templates', 'notation')}


@pytest.mark.parametrize('origin,stored,destination', [(2, 0, 0), (2, 2, 0), (3, 5, 7),
                                                      (0, 9, 3), (12, 4, 12), (24, 48, 1)])
@pytest.mark.parametrize('program,tuning', PROGRAMS)
def test_immediate_tie_keeps_target_and_next_fresh_attack_is_ordinary(origin, stored, destination, program, tuning):
    document = source(origin, stored, destination)
    document['tracks'][0].update(instrumentId=program, tuning=tuning)
    document.update(title='Unrelated HP continuation', songId=901234, revisionId=890123)
    p = produced(document)
    held, fresh = flat(p['tracks'][0])
    assert (held['t'], held['f'], held['sus']) == (0, origin, 1)
    assert (fresh['t'], fresh['f'], fresh['sus']) == (1, destination, 1)
    assert held['source_ids'] == ['songsterr:0:0:0:0:0', 'songsterr:0:0:0:1:0']
    assert fresh['source_ids'] == ['songsterr:0:0:0:2:0']
    assert all(not n.get(key) for n in (held, fresh) for key in ('ho', 'po', 'ln', 'hp'))
    assert musical_track(p['tracks'][0]) == musical_track(control_without_marker(document)['tracks'][0])
    row, = p['pendingHopoTieEvidence']
    assert row == {'trackId': '0', 'sourceId': held['source_ids'][1],
        'originSourceId': held['source_ids'][0], 'markerSourceId': held['source_ids'][0],
        'location': 'parts/0/measures/0/voices/0/beats/1/notes/0',
        'markerLocation': 'parts/0/measures/0/voices/0/beats/0/notes/0',
        'occurrence': 1, 'markerOccurrence': 1, 'voice': 0, 'string': len(tuning)-1,
        'attack': 0, 'markerStart': 0, 'markerEnd': .5, 'start': .5, 'end': 1,
        'authored': {'hp': True, 'tie': True, 'fret': stored},
        'used': {'fret': origin, 'outgoingHopo': False}, 'rule': RULE}
    assert bool(p.get('plainTieIdentityEvidence')) == (stored != origin)
    first, tied, last = written(p['tracks'][0])
    assert first['fret'] == tied['fret'] == origin and last['fret'] == destination
    assert first['midi'] == tied['midi'] == tuning[0]+origin
    assert tied['tied'] and tied['source_id'] == row['sourceId']


@pytest.mark.parametrize('origin,stored', [(2, 0), (2, 2), (0, 9)])
@pytest.mark.parametrize('program,tuning', PROGRAMS)
def test_terminal_plain_tie_consumes_marker_without_inventing_destination(origin, stored, program, tuning):
    document = source(origin, stored, terminal=True)
    document['tracks'][0].update(instrumentId=program, tuning=tuning)
    p = produced(document)
    held, = flat(p['tracks'][0])
    assert (held['t'], held['f'], held['sus']) == (0, origin, 2)
    assert all(not held.get(key) for key in ('ho', 'po', 'ln'))
    row, = p['pendingHopoTieEvidence']
    assert (row['markerEnd'], row['start'], row['end']) == (1, 1, 2)


@pytest.mark.parametrize('field,value,intensity', [('vibrato', True, 'slight'),
    ('wideVibrato', True, 'wide'), ('leftHandVibrato', 'slight', 'slight'),
    ('leftHandVibrato', 'wide', 'wide')])
@pytest.mark.parametrize('program,tuning', PROGRAMS)
def test_explicit_origin_vibrato_keeps_its_complete_held_interval(field, value, intensity, program, tuning):
    document = source(3, 0)
    document['tracks'][0].update(instrumentId=program, tuning=tuning)
    bs = document['parts'][0]['measures'][0]['voices'][0]['beats']
    bs[0]['notes'][0][field] = value
    bs[0]['vibrato'] = True  # Retained annotation cannot own another controller.
    p = produced(document)
    held, fresh = flat(p['tracks'][0])
    assert held['vb'] is True
    assert held['vibrato_marks'] == [{'start': 0., 'end': 1., 'intensity': intensity}]
    assert not fresh.get('vb') and 'vibrato_marks' not in fresh
    assert musical_track(p['tracks'][0]) == musical_track(control_without_marker(document)['tracks'][0])


@pytest.mark.parametrize('earlier,origin,incoming', [(1, 3, 'ho'), (7, 3, 'po')])
@pytest.mark.parametrize('stored', [0, 3])
def test_consuming_new_outgoing_marker_preserves_genuine_incoming_link(earlier, origin, incoming, stored):
    document = raw_score([measure(beat(earlier, duration=(1, 4), hp=True),
        beat(origin, duration=(1, 4), hp=True), beat(stored, duration=(1, 4), tie=True),
        beat(0, duration=(1, 4)))])
    p = produced(document)
    first, held, fresh = flat(p['tracks'][0])
    assert first['ln'] is True and first['sus'] == .5
    assert held[incoming] is True and held['sus'] == 1 and not held.get('ln')
    assert not held.get('po' if incoming == 'ho' else 'ho')
    assert all(not fresh.get(key) for key in ('ho', 'po', 'ln'))
    assert musical_track(p['tracks'][0]) == musical_track(control_without_marker(document, (0, 0, 1, 0))['tracks'][0])
    row, = p['pendingHopoTieEvidence']
    assert (row['attack'], row['markerStart'], row['markerEnd'], row['start'], row['end']) == (.5, .5, 1, 1, 1.5)
    assert row['originSourceId'] == row['markerSourceId'] == 'songsterr:0:0:0:1:0'


@pytest.mark.parametrize('destination,kind', [(5, 'ho'), (0, 'po')])
def test_direct_fresh_hopo_resolution_is_unchanged(destination, kind):
    document = raw_score([measure(beat(2, duration=(1, 2), hp=True),
                                   beat(destination, duration=(1, 2)))])
    p = produced(document)
    origin, fresh = flat(p['tracks'][0])
    assert origin['ln'] is True and fresh[kind] is True
    assert not p.get('pendingHopoTieEvidence') and not p.get('plainTieIdentityEvidence')


def test_next_fresh_attack_can_author_a_separate_real_hopo_link():
    document = raw_score([measure(beat(2, duration=(1, 4), hp=True),
        beat(0, duration=(1, 4), tie=True), beat(3, duration=(1, 4), hp=True),
        beat(5, duration=(1, 4)))])
    p = produced(document)
    held, origin, target = flat(p['tracks'][0])
    assert held['sus'] == 1 and not held.get('ln')
    assert origin['f'] == 3 and origin['t'] == 1 and origin['ln'] is True
    assert not origin.get('ho') and not origin.get('po')
    assert target['f'] == 5 and target['ho'] is True and target['t'] == 1.5
    assert len(p['pendingHopoTieEvidence']) == 1


def test_bare_dangling_hp_is_not_reclassified_as_consumed():
    with pytest.raises(ScoreImportError, match='no destination'):
        produced(raw_score([measure(beat(2, hp=True))]))


def test_multiple_plain_continuations_keep_first_target_and_onset_instructions():
    document = raw_score([measure(beat(2, duration=(1, 4), hp=True, leftFingering='1'),
        beat(0, duration=(1, 4), tie=True, leftFingering='3'),
        beat(8, duration=(1, 4), tie=True, leftFingering='4'), beat(0, duration=(1, 4)))])
    bs = document['parts'][0]['measures'][0]['voices'][0]['beats']
    bs[0]['pickStroke'] = 'down';bs[1]['pickStroke'] = 'up'
    p = produced(document)
    held, fresh = flat(p['tracks'][0])
    assert (held['f'], held['sus'], held['fg'], held['pkd']) == (2, 1.5, 1, 0)
    assert len(held['source_ids']) == 3 and not fresh.get('po')
    assert len(p['pendingHopoTieEvidence']) == 1 and len(p['plainTieIdentityEvidence']) == 2
    assert [n['fret'] for n in written(p['tracks'][0])] == [2, 2, 2, 0]


@pytest.mark.parametrize('stored', [0, 2])
def test_literal_marker_on_a_later_plain_tie_keeps_distinct_attack_lineage(stored):
    document = raw_score([measure(beat(2, duration=(1, 4)),
        beat(2, duration=(1, 4), tie=True, hp=True),
        beat(stored, duration=(1, 4), tie=True), beat(0, duration=(1, 4)))])
    p = produced(document)
    held, fresh = flat(p['tracks'][0])
    assert held['f'] == 2 and held['sus'] == 1.5 and len(held['source_ids']) == 3
    assert not held.get('ln') and not fresh.get('po')
    row, = p['pendingHopoTieEvidence']
    assert row['originSourceId'] == 'songsterr:0:0:0:0:0'
    assert row['markerSourceId'] == 'songsterr:0:0:0:1:0'
    assert row['sourceId'] == 'songsterr:0:0:0:2:0'
    assert (row['attack'], row['markerStart'], row['markerEnd'], row['start'], row['end']) == (0, .5, 1, 1, 1.5)
    assert musical_track(p['tracks'][0]) == musical_track(control_without_marker(document, (0, 0, 1, 0))['tracks'][0])


@pytest.mark.parametrize('gesture', ['vibrato', 'harmonic', 'slide'])
def test_later_same_fret_continuations_keep_existing_separate_gesture_policies(gesture):
    document = raw_score([measure(beat(2, duration=(1, 4), hp=True),
        beat(0, duration=(1, 4), tie=True), beat(2, duration=(1, 4), tie=True),
        beat(5, duration=(1, 4)))])
    n = document['parts'][0]['measures'][0]['voices'][0]['beats'][2]['notes'][0]
    if gesture == 'vibrato': n['leftHandVibrato'] = 'wide'
    elif gesture == 'harmonic': n.update(harmonic='artificial', harmonicFret=12)
    else: n['slide'] = 'shift'
    p = produced(document)
    assert len(p['pendingHopoTieEvidence']) == 1
    assert musical_track(p['tracks'][0]) == musical_track(control_without_marker(document)['tracks'][0])
    held, fresh = flat(p['tracks'][0])
    assert held['f'] == 2 and held['sus'] == 1.5 and not fresh.get('po')
    if gesture == 'vibrato':
        assert held['vibrato_marks'] == [{'start': 1., 'end': 1.5, 'intensity': 'wide'}]
    elif gesture == 'slide':
        assert held['sl'] == 5


def test_two_strings_use_cumulative_meter_clock_without_delayed_pull_offs():
    # A three-quarter prelude makes the held timing independent of bar number.
    prelude = measure({'duration': [3, 4], 'notes': [{'rest': True}]})
    prelude['signature'] = [3, 4]
    pattern = measure(beat(0, string=1, duration=(1, 8)), beat(2, string=3, duration=(1, 16)),
        beat(2, string=2, duration=(1, 16), hp=True), beat(0, string=2, duration=(1, 8), tie=True),
        beat(0, string=2, duration=(1, 8)), beat(2, string=3, duration=(1, 16)),
        beat(1, string=1, duration=(3, 16), hp=True), beat(0, string=1, duration=(1, 8), tie=True),
        beat(0, string=1, duration=(1, 8)))
    document = raw_score([prelude, pattern])
    document['parts'][0]['automations']['tempo'][0]['bpm'] = 72
    p = produced(document)
    notes = flat(p['tracks'][0])
    assert len(notes) == 7 and all(not n.get(key) for n in notes for key in ('ho', 'po', 'ln'))
    g = next(n for n in notes if n['f'] == 2 and n['s'] == 3)
    b = next(n for n in notes if n['f'] == 1)
    assert (g['t'], g['sus']) == pytest.approx((3.125, .625))
    assert (b['t'], b['sus']) == pytest.approx((4.375, 25/24))
    assert [r['start'] for r in p['pendingHopoTieEvidence']] == pytest.approx([10/3, 5])
    assert [r['occurrence'] for r in p['pendingHopoTieEvidence']] == [2, 2]


def test_mixed_chord_children_keep_boxes_endpoints_and_sibling_techniques():
    first = {'duration': [1, 4], 'notes': [{'string': 0, 'fret': 2, 'hp': True},
        {'string': 1, 'fret': 3, 'vibrato': True}, {'string': 2, 'fret': 4}]}
    tied = {'duration': [1, 4], 'notes': [{'string': 0, 'fret': 0, 'tie': True},
        {'string': 1, 'fret': 3, 'tie': True}, {'string': 2, 'fret': 4, 'tie': True}]}
    fresh = {'duration': [1, 2], 'notes': [{'string': s, 'fret': 0} for s in range(3)]}
    document = raw_score([measure(first, tied, fresh)])
    p = produced(document)
    notes = flat(p['tracks'][0])
    assert len(notes) == 6 and len(p['tracks'][0]['chords']) == 2
    assert sorted(n['f'] for n in notes if n['t'] == 0) == [2, 3, 4]
    assert all(n['t']+n['sus'] == 1 for n in notes if n['t'] == 0)
    assert all(not n.get('po') for n in notes if n['t'] == 1)
    assert next(n for n in notes if n['f'] == 3)['vibrato_marks'] == [{'start': 0., 'end': 1., 'intensity': 'slight'}]
    assert len(p['pendingHopoTieEvidence']) == 1
    assert musical_track(p['tracks'][0]) == musical_track(control_without_marker(document)['tracks'][0])


@pytest.mark.parametrize('terminal', [False, True])
def test_consumed_groups_are_independent_on_repeated_visits(terminal):
    document = source(2, 0, terminal=terminal)
    document['parts'][0]['measures'][0].update(repeatStart=True, repeat=2)
    p = produced(document)
    held = [n for n in flat(p['tracks'][0]) if n['f'] == 2]
    assert [n['t'] for n in held] == [0, 2]
    assert [r['occurrence'] for r in p['pendingHopoTieEvidence']] == [1, 2]
    assert [r['markerOccurrence'] for r in p['pendingHopoTieEvidence']] == [1, 2]
    assert all(not n.get('ln') for n in held)


def test_tempo_change_preserves_exact_marker_and_consumption_clocks():
    document = source()
    document['parts'][0]['automations']['tempo'].append({'measure': 0, 'position': 960, 'bpm': 60, 'type': 4})
    p = produced(document)
    held, fresh = flat(p['tracks'][0])
    assert (held['t'], held['sus'], fresh['t'], fresh['sus']) == (0, 1.5, 1.5, 2)
    row, = p['pendingHopoTieEvidence']
    assert (row['markerStart'], row['markerEnd'], row['start'], row['end']) == (0, .5, .5, 1.5)


def test_contiguous_cross_bar_consumption_keeps_distinct_marker_occurrence():
    first = measure(beat(2, duration=(1, 2), hp=True))
    following = measure(beat(0, duration=(1, 4), tie=True), beat(0, duration=(1, 4)))
    first['signature'] = following['signature'] = [2, 4]
    p = produced(raw_score([first, following]))
    held, fresh = flat(p['tracks'][0])
    assert (held['t'], held['sus'], fresh['t'], fresh['sus']) == (0, 1.5, 1.5, .5)
    row, = p['pendingHopoTieEvidence']
    assert (row['markerOccurrence'], row['occurrence']) == (1, 2)
    assert (row['markerStart'], row['markerEnd'], row['start'], row['end']) == (0, 1, 1, 1.5)
    assert row['markerSourceId'] == 'songsterr:0:0:0:0:0' and row['sourceId'] == 'songsterr:0:1:0:0:0'


def test_source_bound_archive_envelope_retains_original_bytes_and_returns_copy(tmp_path):
    from feedback_converter.song_import.pending_hopo_ties import archive_evidence
    document = source()
    path = tmp_path/'source.json'
    path.write_text(json.dumps(document, indent=2, ensure_ascii=False), encoding='utf-8')
    original = path.read_bytes()
    p = produced(document)
    receipt = archive_evidence(p, path)
    assert receipt == {'version': 1, 'policy': POLICY, 'timeDomain': 'score_seconds',
        'sourceSha256': hashlib.sha256(original).hexdigest(), 'consumptions': p['pendingHopoTieEvidence']}
    receipt['consumptions'][0]['authored']['fret'] = 9
    assert p['pendingHopoTieEvidence'][0]['authored']['fret'] == 0
    assert path.read_bytes() == original and document['parts'][0]['measures'][0]['voices'][0]['beats'][0]['notes'][0]['hp'] is True


@pytest.mark.parametrize('program,tuning', PROGRAMS)
def test_hidden_synth_articulation_is_retained_and_disclosed_without_new_targets(tmp_path, program, tuning):
    document = source()
    document['tracks'][0].update(instrumentId=program, tuning=tuning)
    original = deepcopy(document)
    performance = import_json(tmp_path, document)
    assert document == original
    assert json.loads((tmp_path/'score.json').read_bytes()) == original
    findings = [f for f in performance['compatibilityReport']['findings'] if f['feature'] == 'note.pending_hopo_tie']
    finding, = findings
    row, = performance['pendingHopoTieEvidence']
    assert (finding['category'], finding['impact'], finding['retained']) == ('game_limitation', 'display_or_expression', 'original_source')
    assert finding['location'] == row['location']+'@visit1'
    assert finding['value'] == {k: row[k] for k in ('authored', 'used', 'rule', 'markerSourceId', 'originSourceId')}
    assert finding['value']['authored']['hp'] is True and finding['value']['used']['outgoingHopo'] is False
    assert len(flat(performance['tracks'][0])) == 2


@pytest.mark.parametrize('stored', [0, 2])
@pytest.mark.parametrize('fault', ['duplicate-origin', 'duplicate-tie', 'orphan', 'voice', 'string',
                                  'gap', 'explicit-rest', 'overlap', 'rest-inside-hold', 'repeat-jump'])
def test_unsafe_continuity_is_not_repaired(stored, fault):
    document = source(2, stored)
    bs = document['parts'][0]['measures'][0]['voices'][0]['beats']
    if fault == 'duplicate-origin':
        bs[0]['notes'].append(deepcopy(bs[0]['notes'][0]))
    elif fault == 'duplicate-tie':
        bs[1]['notes'].append(deepcopy(bs[1]['notes'][0]))
    elif fault == 'orphan':
        bs.pop(0)
    elif fault == 'voice':
        document['parts'][0]['measures'][0]['voices'].append({'beats': [bs.pop(1)]})
    elif fault == 'string':
        bs[1]['notes'][0]['string'] = 1
    elif fault == 'gap':
        document['parts'][0]['measures'] = [measure(bs[0]), measure(bs[1], bs[2])]
    elif fault in ('explicit-rest', 'rest-inside-hold'):
        bs[2]['duration'] = [1, 4]
        bs.insert(1, {'duration': [1, 4], 'notes': [{'rest': True}]})
    elif fault == 'repeat-jump':
        document = raw_score([measure(beat(2)), measure(beat(stored, tie=True), repeatStart=True),
                               measure(beat(2, hp=True), repeat=2)])
    score = parse(document)
    if fault == 'overlap':
        score.tracks[0].bars[0][1].position = Fraction(1, 2)
    elif fault == 'rest-inside-hold':
        score.tracks[0].bars[0][0].duration = Fraction(2)
    with pytest.raises(ScoreImportError):
        render(score)


@pytest.mark.parametrize('stored', [0, 2])
@pytest.mark.parametrize('field', ['hp', 'tie'])
@pytest.mark.parametrize('value', [1, 1.0, 'true', [True], {'active': True}])
def test_truthy_coercion_is_not_a_literal_consumption_instruction(stored, field, value):
    document = source(2, stored)
    bs = document['parts'][0]['measures'][0]['voices'][0]['beats']
    bs[0 if field == 'hp' else 1]['notes'][0][field] = value
    with pytest.raises(ScoreImportError):
        produced(document)


@pytest.mark.parametrize('value', [None, False, 0, ''])
def test_inactive_hp_has_no_consumption_receipt(value):
    document = source()
    document['parts'][0]['measures'][0]['voices'][0]['beats'][0]['notes'][0]['hp'] = value
    p = produced(document)
    assert not p.get('pendingHopoTieEvidence')
    assert [n['f'] for n in flat(p['tracks'][0])] == [2, 0]


@pytest.mark.parametrize('stored', [0, 2])
@pytest.mark.parametrize('fault', ['new-hp', 'bend', 'vibrato', 'wide-vibrato', 'modern-vibrato',
    'natural', 'artificial', 'mute', 'hopo', 'incoming-slide', 'outgoing-slide', 'terminal-slide',
    'trill', 'staccato', 'whammy', 'bar-vibrato'])
def test_consuming_continuation_cannot_introduce_an_expression_or_other_link(stored, fault):
    document = source(2, stored)
    bs = document['parts'][0]['measures'][0]['voices'][0]['beats']
    n = bs[1]['notes'][0]
    if fault == 'new-hp': n['hp'] = True
    elif fault == 'bend': n['bend'] = deepcopy(RISE)
    elif fault == 'vibrato': n['vibrato'] = True
    elif fault == 'wide-vibrato': n['wideVibrato'] = True
    elif fault == 'modern-vibrato': n['leftHandVibrato'] = 'slight'
    elif fault == 'natural':
        n.update(fret=12, harmonic='natural')
        if stored == 2: bs[0]['notes'][0]['fret'] = 12
    elif fault == 'artificial': n.update(harmonic='artificial', harmonicFret=12)
    elif fault == 'mute': n['dead'] = True
    elif fault == 'incoming-slide': n['slide'] = 'above'
    elif fault == 'outgoing-slide': n['slide'] = 'shift'
    elif fault == 'terminal-slide': n['slide'] = 'downwards'
    elif fault == 'trill': n['trill'] = {'auxiliaryFret': 4, 'speed': 120}
    elif fault == 'staccato': n['staccato'] = True
    elif fault == 'whammy': bs[1]['tremoloBar'] = deepcopy(RISE)
    elif fault == 'bar-vibrato': bs[1]['vibratoWithTremoloBar'] = 'slight'
    if fault == 'hopo':
        score = parse(document)
        score.tracks[0].bars[0][1].hopo = True
        with pytest.raises(ScoreImportError):
            render(score)
    else:
        with pytest.raises(ScoreImportError):
            produced(document)


@pytest.mark.parametrize('stored', [0, 2])
@pytest.mark.parametrize('fault', ['bend', 'harmonic', 'mute', 'outgoing-slide', 'incoming-slide',
                                  'whammy', 'trill', 'staccato', 'displaced-attack'])
def test_compound_origin_or_competing_link_stays_guarded(stored, fault):
    document = source(2, stored)
    bs = document['parts'][0]['measures'][0]['voices'][0]['beats']
    n = bs[0]['notes'][0]
    if fault == 'bend': n['bend'] = deepcopy(RISE)
    elif fault == 'harmonic': n.update(harmonic='artificial', harmonicFret=12)
    elif fault == 'mute': n['dead'] = True
    elif fault == 'outgoing-slide': n['slide'] = 'shift'
    elif fault == 'incoming-slide': n['slide'] = 'below'
    elif fault == 'whammy': bs[0]['tremoloBar'] = deepcopy(RISE)
    elif fault == 'trill': n['trill'] = {'auxiliaryFret': 4, 'speed': 120}
    elif fault == 'staccato': n['staccato'] = True
    score = parse(document)
    if fault == 'displaced-attack':
        score.tracks[0].bars[0][0].attack_offset = Fraction(1, 8)
    with pytest.raises(ScoreImportError):
        render(score)


@pytest.mark.parametrize('fault', ['earlier-gap', 'coerced-earlier-tie', 'resolved-incoming-slide'])
def test_immediate_boundary_does_not_repair_unqualified_held_history(fault):
    if fault == 'earlier-gap':
        document = raw_score([measure(beat(2, duration=(1, 4))),
            measure(beat(2, duration=(1, 4), tie=True, hp=True),
                    beat(0, duration=(1, 4), tie=True), beat(0, duration=(1, 2)))])
    elif fault == 'coerced-earlier-tie':
        document = raw_score([measure(beat(2, duration=(1, 4)),
            beat(2, duration=(1, 4), tie=1, hp=True), beat(0, duration=(1, 4), tie=True),
            beat(0, duration=(1, 4)))])
    else:
        document = raw_score([measure(beat(1, duration=(1, 4), slide='legato'),
            beat(2, duration=(1, 4), hp=True), beat(0, duration=(1, 4), tie=True),
            beat(0, duration=(1, 4)))])
    with pytest.raises(ScoreImportError):
        produced(document)


def test_separate_pending_shift_slide_placeholder_remains_guarded():
    document = source()
    n = document['parts'][0]['measures'][0]['voices'][0]['beats'][0]['notes'][0]
    n.pop('hp');n['slide'] = 'shift'
    with pytest.raises(ScoreImportError, match='Unresolved tie'):
        produced(document)

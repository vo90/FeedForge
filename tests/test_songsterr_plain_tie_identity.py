"""Producer continuation identity, source lineage and per-visit notation."""
from copy import deepcopy
from fractions import Fraction
import hashlib
import json

import pytest

from feedback_converter.song_import.model import ScoreImportError
from feedback_converter.song_import.plain_ties import archive_evidence
from feedback_converter.song_import.songsterr import parse
from feedback_converter.song_import.timeline import render
from test_song_import_score import beat, measure, raw_score, write_gp


def source(origin=7, stored=0, **fields):
    return raw_score([measure(beat(origin, duration=(1, 2)),
                              beat(stored, duration=(1, 2), tie=True, **fields))])


def produced(document):
    raw = deepcopy(document)
    score = parse(document)
    parsed = deepcopy(score)
    result = render(score)
    assert document == raw
    assert score == parsed
    assert render(score) == result
    return result


def flat(track):
    result = list(track['notes'])
    for chord in track['chords']:
        result.extend({'t': chord['t'], **n} for n in chord['notes'])
    return sorted(result, key=lambda n: (n['t'], n['s']))


def written(track):
    return [n for m in track['notation']['measures']
            for v in m['staves']['staff']['voices'] for b in v['beats']
            for n in b.get('notes', [])]


@pytest.mark.parametrize('origin,stored', [(7, 0), (7, 8), (0, 9), (12, 4), (48, 0)])
@pytest.mark.parametrize('program,tuning', [(29, [64, 59, 55, 50, 45, 40]), (33, [43, 38, 33, 28])])
@pytest.mark.parametrize('version', [None, 5, 8])
def test_general_pitched_continuation_keeps_attack_target(origin, stored, program, tuning, version):
    document = source(origin, stored)
    document.update(title='Unrelated held note', songId=999999, revisionId=765432)
    document['tracks'][0].update(instrumentId=program, tuning=tuning)
    if version is not None:
        document['parts'][0]['version'] = version
    p = produced(document)
    track = p['tracks'][0]
    n, = flat(track)
    assert (n['t'], n['f'], n['sus']) == (0, origin, 2)
    assert n['source_ids'] == ['songsterr:0:0:0:0:0', 'songsterr:0:0:0:1:0']
    row, = p['plainTieIdentityEvidence']
    assert row == {'trackId': '0', 'sourceId': n['source_ids'][1], 'originSourceId': n['source_ids'][0],
                   'location': 'parts/0/measures/0/voices/0/beats/1/notes/0',
                   'occurrence': 1, 'voice': 0, 'string': len(tuning) - 1,
                   'attack': 0, 'start': 1, 'end': 2, 'authored': {'fret': stored},
                   'used': {'fret': origin}, 'rule': 'plain-tie-keeps-attack-target'}
    a, b = written(track)
    assert a['fret'] == b['fret'] == origin
    assert a['midi'] == b['midi'] == tuning[0] + origin
    assert b['tied'] is True and b['source_id'] == row['sourceId']


def test_evidence_envelope_uses_original_bytes_and_independent_copy(tmp_path):
    document = source()
    raw_path = tmp_path / 'source.json'
    raw_path.write_text(json.dumps(document, indent=2), encoding='utf-8')
    original = raw_path.read_bytes()
    p = produced(document)
    receipt = archive_evidence(p, raw_path)
    assert receipt == {'version': 1, 'policy': 'songsterr-plain-tie-identity-v1',
                       'timeDomain': 'score_seconds', 'sourceSha256': hashlib.sha256(original).hexdigest(),
                       'continuations': p['plainTieIdentityEvidence']}
    receipt['continuations'][0]['used']['fret'] = 13
    assert p['plainTieIdentityEvidence'][0]['used']['fret'] == 7
    assert raw_path.read_bytes() == original


def test_tie_chain_keeps_first_attack_identity_and_onset_instructions():
    document = raw_score([measure(beat(7, duration=(1, 4), leftFingering='2'),
                                 beat(0, duration=(1, 4), tie=True, leftFingering='3'),
                                 beat(8, duration=(1, 2), tie=True, leftFingering='4'))])
    for b, direction in zip(document['parts'][0]['measures'][0]['voices'][0]['beats'], ('down', 'up', 'up')):
        b['pickStroke'] = direction
    p = produced(document)
    n, = flat(p['tracks'][0])
    assert (n['f'], n['sus'], n['pkd'], n['fg']) == (7, 2, 0, 2)
    assert len(n['source_ids']) == 3
    assert [r['originSourceId'] for r in p['plainTieIdentityEvidence']] == [n['source_ids'][0]] * 2
    assert [r['start'] for r in p['plainTieIdentityEvidence']] == [.5, 1]
    assert [n['fret'] for n in written(p['tracks'][0])] == [7, 7, 7]


@pytest.mark.parametrize('brush', [{'brushStroke': {'direction': 'down', 'duration': 30, 'shift': 0}}, {'upStroke': 6}])
def test_brushed_chord_ties_keep_offsets_written_end_and_effective_notation(brush):
    first = {'duration': [1, 2], **brush,
             'notes': [{'string': s, 'fret': f} for s, f in enumerate((3, 4, 5))]}
    tied = {'duration': [1, 2], 'notes': [{'string': s, 'fret': 0, 'tie': True} for s in range(3)]}
    p = produced(raw_score([measure(first, tied)]))
    notes = flat(p['tracks'][0])
    assert len(notes) == 3 and len({n['t'] for n in notes}) == 3
    assert all(n['t'] + n['sus'] == pytest.approx(2) for n in notes)
    assert len(p['plainTieIdentityEvidence']) == 3
    assert all(r['start'] == 1 and r['end'] == 2 for r in p['plainTieIdentityEvidence'])
    assert sorted(n['f'] for n in notes) == [3, 4, 5]
    assert sorted(n['fret'] for n in written(p['tracks'][0]) if n.get('tied')) == [3, 4, 5]


def test_unfilled_same_voice_gap_retains_existing_continuation_policy():
    document = raw_score([measure(beat(7, duration=(1, 4))),
                          measure(beat(8, string=1, duration=(1, 4)), beat(0, tie=True, duration=(3, 4)))])
    p = produced(document)
    held = next(n for n in flat(p['tracks'][0]) if n['f'] == 7)
    assert held['t'] == 0 and held['sus'] == 4
    row, = p['plainTieIdentityEvidence']
    assert (row['start'], row['end'], row['occurrence']) == (2.5, 4, 2)


@pytest.mark.parametrize('fault', ['rest', 'orphan', 'voice', 'string', 'overlap', 'duplicates'])
def test_missing_or_ambiguous_continuity_is_not_repaired(fault):
    document = source()
    bs = document['parts'][0]['measures'][0]['voices'][0]['beats']
    if fault == 'rest':
        bs[0]['duration'] = bs[1]['duration'] = [1, 4]
        bs.insert(1, {'duration': [1, 4], 'notes': [{'rest': True}]})
    elif fault == 'orphan':
        bs.pop(0)
    elif fault == 'voice':
        document['parts'][0]['measures'][0]['voices'].append({'beats': [bs.pop(1)]})
    elif fault == 'string':
        bs[1]['notes'][0]['string'] = 1
    elif fault == 'duplicates':
        bs[1]['notes'].append(deepcopy(bs[1]['notes'][0]))
    score = parse(document)
    if fault == 'overlap':
        score.tracks[0].bars[0][1].position = Fraction(1)
    with pytest.raises(ScoreImportError, match='Unresolved tie'):
        render(score)


@pytest.mark.parametrize('tie', [1, 'true', [True], {'active': True}])
def test_coerced_tie_flag_does_not_admit_new_fret_identity(tie):
    document = source()
    document['parts'][0]['measures'][0]['voices'][0]['beats'][1]['notes'][0]['tie'] = tie
    with pytest.raises(ScoreImportError):
        produced(document)


@pytest.mark.parametrize('kind', ['vibrato', 'wideVibrato', 'leftHandVibrato'])
def test_origin_vibrato_keeps_target_and_timed_instruction(kind):
    document = source()
    document['parts'][0]['measures'][0]['voices'][0]['beats'][0]['notes'][0][kind] = 'wide' if kind == 'leftHandVibrato' else True
    p = produced(document)
    n, = flat(p['tracks'][0])
    assert n['f'] == 7 and n['sus'] == 2 and n['vb']
    assert n['vibrato_marks'] == [{'start': 0, 'end': 2, 'intensity': 'wide' if kind != 'vibrato' else 'slight'}]
    assert written(p['tracks'][0])[-1]['fret'] == 7


def test_resolved_incoming_shift_preserves_destination_attack_and_link():
    document = raw_score([measure(beat(3, duration=(1, 4), slide='shift'),
                                 beat(7, duration=(1, 4)), beat(0, duration=(1, 2), tie=True))])
    p = produced(document)
    a, b = flat(p['tracks'][0])
    assert (a['f'], a['sl'], a['sus']) == (3, 7, .5)
    assert (b['t'], b['f'], b['sus']) == (.5, 7, 1.5)
    assert p['plainTieIdentityEvidence'][0]['originSourceId'] == b['source_ids'][0]
    assert written(p['tracks'][0])[-1]['fret'] == 7


GESTURES = [
    {'vibrato': True}, {'harmonic': 'pinch'}, {'dead': True}, {'slide': 'upwards'},
    {'slide': 'below'}, {'slide': 'shift'}, {'hp': True},
    {'bend': {'points': [{'position': 0, 'tone': 0}, {'position': 60, 'tone': 100}]}},
    {'trill': {'auxiliaryFret': 9, 'speed': 60}},
]


@pytest.mark.parametrize('gesture', GESTURES)
def test_explicit_continuation_pitch_or_mute_gestures_stay_guarded(gesture):
    with pytest.raises(ScoreImportError):
        produced(source(**gesture))


@pytest.mark.parametrize('gesture', [{**g, **({'vibrato': True} if 'bend' in g else {})}
                                    for g in GESTURES if 'vibrato' not in g and g.get('slide') != 'below'])
def test_unqualified_origin_pitch_or_mute_gestures_stay_guarded(gesture):
    document = source()
    document['parts'][0]['measures'][0]['voices'][0]['beats'][0]['notes'][0].update(gesture)
    with pytest.raises(ScoreImportError):
        produced(document)


@pytest.mark.parametrize('first,last,stored', [(7, 9, 0), (7, 9, 8), (0, 9, 8), (7, 0, 9)])
def test_repeat_entrance_qualifies_effective_identity_for_each_visit(first, last, stored):
    document = raw_score([measure(beat(first)), measure(beat(stored, tie=True), repeatStart=True),
                          measure(beat(last), repeat=2), measure(beat(stored, tie=True))])
    p = produced(document)
    notes = flat(p['tracks'][0])
    assert [(n['t'], n['f'], n['sus']) for n in notes] == [(0, first, 4), (4, last, 4), (8, last, 4)]
    rows = p['plainTieIdentityEvidence']
    assert [(r['occurrence'], r['used']['fret']) for r in rows] == [(2, first), (4, last), (6, last)]
    assert rows[0]['sourceId'] == rows[1]['sourceId']
    assert rows[0]['originSourceId'] != rows[1]['originSourceId']
    notation = p['tracks'][0]['notation']['measures']
    assert [m['staves']['staff']['voices'][0]['beats'][0]['notes'][0]['fret'] for m in notation] == [first, first, last, last, last, last]
    assert [m['source_measure'] for m in notation] == [1, 2, 3, 2, 3, 4]


@pytest.mark.parametrize('fault', ['orphan-first', 'rest-before', 'duplicate-entrance'])
def test_repeat_does_not_recover_invalid_tie_entrance(fault):
    document = raw_score([measure(beat(7)), measure(beat(0, tie=True), repeatStart=True),
                          measure(beat(9), repeat=2)])
    if fault == 'orphan-first':
        document['parts'][0]['measures'].pop(0)
    elif fault == 'rest-before':
        document['parts'][0]['measures'][0]['voices'][0]['beats'] = [{'duration': [1, 1], 'notes': [{'rest': True}]}]
    else:
        bs = document['parts'][0]['measures'][1]['voices'][0]['beats']
        bs[0]['notes'].append(deepcopy(bs[0]['notes'][0]))
    with pytest.raises(ScoreImportError, match='Unresolved tie'):
        produced(document)


def test_equal_fret_continuation_and_separate_attacks_keep_existing_behavior():
    p = produced(source(7, 7))
    assert not p.get('plainTieIdentityEvidence')
    assert flat(p['tracks'][0])[0]['sus'] == 2
    document = source()
    document['parts'][0]['measures'][0]['voices'][0]['beats'][1]['notes'][0].pop('tie')
    p = produced(document)
    assert not p.get('plainTieIdentityEvidence')
    assert [n['f'] for n in flat(p['tracks'][0])] == [7, 0]


@pytest.mark.parametrize('origin_staccato,continuation_staccato', [(True, False), (True, True), (False, True)])
@pytest.mark.parametrize('bass', [False, True])
def test_differing_plain_tie_preserves_native_qualified_staccato_release(origin_staccato, continuation_staccato, bass):
    document = source(7, 0, staccato=continuation_staccato)
    document['parts'][0]['measures'][0]['voices'][0]['beats'][0]['notes'][0]['staccato'] = origin_staccato
    if bass:
        document['tracks'][0].update(instrumentId=33, tuning=[43, 38, 33, 28])
    p = produced(document)
    n, = flat(p['tracks'][0])
    assert (n['t'], n['f'], n['sus']) == (0, 7, 1 if origin_staccato else 2)
    row, = p['plainTieIdentityEvidence']
    assert row['start'] == 1 and row['end'] == 2
    assert written(p['tracks'][0])[-1]['fret'] == 7


def test_gpif_differing_tie_keeps_existing_format_specific_guard(tmp_path):
    from feedback_converter.song_import.gpif import parse as parse_gpif
    path = write_gp(tmp_path, measures=[{'beats': [{'fret': 7, 'value': 'Half'},
                                                  {'fret': 0, 'value': 'Half', 'tie': True}]}])
    with pytest.raises(ScoreImportError, match='Unresolved tie'):
        render(parse_gpif(path))


def test_separate_voice_arrangements_preserve_identity_in_correct_projection():
    document = source()
    document['parts'][0]['measures'][0]['voices'].append({'beats': [beat(12, duration=(1, 2)), beat(4, duration=(1, 2), tie=True)]})
    p = produced(document)
    assert len(p['tracks']) == 2
    assert [flat(t)[0]['f'] for t in p['tracks']] == [7, 12]
    assert [(r['trackId'], r['voice'], r['used']['fret']) for r in p['plainTieIdentityEvidence']] == [(p['tracks'][0]['id'], 0, 7), (p['tracks'][1]['id'], 1, 12)]
    assert [written(t)[-1]['fret'] for t in p['tracks']] == [7, 12]


def test_compatible_voices_keep_separate_lineage_in_combined_arrangement():
    document = source()
    document['parts'][0]['measures'][0]['voices'].append({'beats': [beat(12, string=1, duration=(1, 2)), beat(4, string=1, duration=(1, 2), tie=True)]})
    p = produced(document)
    track, = p['tracks']
    assert sorted(n['f'] for n in flat(track)) == [7, 12]
    assert [(r['trackId'], r['voice'], r['used']['fret']) for r in p['plainTieIdentityEvidence']] == [('0', 1, 12), ('0', 0, 7)]
    assert [n['fret'] for n in written(track) if n.get('tied')] == [7, 12]


@pytest.mark.parametrize('chain', [False, True])
def test_rest_inside_held_origin_cannot_admit_new_identity(chain):
    # Exercise the timeline boundary directly: a sounding span may overlap a
    # written rest, but a new identity rule must not recover through that rest.
    bs = [beat(7, duration=(1, 4)), {'duration': [1, 4], 'notes': [{'rest': True}]}]
    bs.extend([beat(7, duration=(1, 4), tie=True), beat(0, duration=(1, 4), tie=True)] if chain
              else [beat(0, duration=(1, 2), tie=True)])
    score = parse(raw_score([measure(*bs)]))
    score.tracks[0].bars[0][0].duration = Fraction(2)
    with pytest.raises(ScoreImportError, match='Unresolved tie'):
        render(score)


@pytest.mark.parametrize('origin', [False, True])
def test_unqualified_tremolo_bar_gesture_stays_guarded(origin):
    document = source()
    bs = document['parts'][0]['measures'][0]['voices'][0]['beats']
    bs[0 if origin else 1]['tremoloBar'] = {'points': [{'position': 0, 'tone': 0}, {'position': 60, 'tone': -100}]}
    with pytest.raises(ScoreImportError):
        produced(document)

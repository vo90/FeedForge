"""Note ownership, historical compatibility and existing timed-vibrato semantics."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from test_song_import_score import beat, measure, raw_score
from feedback_converter.song_import.compatibility import inspect_songsterr
from feedback_converter.song_import.model import ScoreImportError
from feedback_converter.song_import.songsterr import (NOTE_VIBRATO_POLICY, WRITTEN_BEAT_VIBRATO_POLICY, parse)
from feedback_converter.song_import.timeline import render
from feedback_converter.song_import.voices import flat


def notes(performance):
    return flat(performance['tracks'][0])


def identity(performance):
    return [(n['t'], n['s'], n['f'], n['sus'], n['source_ids']) for n in notes(performance)]


def written_notes(performance):
    return [n for m in performance['tracks'][0]['notation']['measures']
            for v in m['staves']['staff']['voices'] for b in v['beats'] for n in b.get('notes', [])]


@pytest.mark.parametrize('field', ['vibrato', 'wideVibrato'])
@pytest.mark.parametrize('owners', [0, 1, 2])
def test_beat_annotation_never_fans_out_to_unmarked_chord_notes(field, owners):
    b = beat(3, duration=(1, 1))
    b['notes'].append({'string': 1, 'fret': 7})
    for n in b['notes'][:owners]:
        n['leftHandVibrato'] = 'slight'
    raw = raw_score([measure(b)])
    before = deepcopy(raw)
    plain = render(parse(raw))
    b[field] = True
    marked_raw = deepcopy(raw)
    marked = render(parse(raw))
    assert identity(marked) == identity(plain)
    assert [bool(n.get('vb')) for n in notes(marked)] == [bool(n.get('vb')) for n in notes(plain)]
    assert [n.get('vibrato_marks') for n in notes(marked)] == [n.get('vibrato_marks') for n in notes(plain)]
    score = parse(raw)
    assert all('__beat_vibrato' not in n.effects for n in score.tracks[0].bars[0])
    written = marked['tracks'][0]['notation']['measures'][0]['staves']['staff']['voices'][0]['beats'][0]
    assert written['vib' if field == 'vibrato' else 'vibw'] is True
    assert [bool(n.get('vib')) for n in written['notes']] == [i < owners for i in range(2)]
    finding = next(f for f in inspect_songsterr(raw)['findings'] if f['feature'] == 'beat.' + field)
    assert finding['location'] == 'parts/0/measures/0/voices/0/beats/0/' + field
    assert finding['value'] is True and finding['impact'] == 'display_or_expression'
    assert finding['retained'] == 'original_source' and finding['category'] == 'game_limitation'
    assert 'does not add a note controller' in finding['message']
    assert raw['parts'][0]['measures'][0]['voices'][0]['beats'][0][field] is True
    assert before['parts'][0]['measures'][0]['voices'][0]['beats'][0].get(field) is None
    assert raw == marked_raw


@pytest.mark.parametrize('field', ['vibrato', 'wideVibrato'])
@pytest.mark.parametrize('placement', ['initial', 'continuation', 'both', 'rest'])
def test_beat_only_flags_preserve_tie_attack_and_end_without_a_note_controller(field, placement):
    first, tail = beat(7, duration=(1, 2)), beat(7, duration=(1, 2), tie=True)
    if placement in ('initial', 'both'): first[field] = True
    if placement in ('continuation', 'both'): tail[field] = True
    if placement == 'rest': tail = {'duration': [1, 2], 'rest': True, 'notes': [], field: True}
    raw = raw_score([measure(first, tail)])
    snapshot = deepcopy(raw)
    p = render(parse(raw))
    assert len(notes(p)) == 1
    n = notes(p)[0]
    assert n['f'] == 7 and n['t'] == 0 and n['sus'] == (1 if placement == 'rest' else 2)
    assert not n.get('vb') and 'vibrato_marks' not in n
    assert all(not n.get('vib') and not n.get('vibw') for n in written_notes(p))
    assert raw == snapshot
    flags = [f for f in inspect_songsterr(raw)['findings'] if f['feature'] == 'beat.' + field]
    assert len(flags) == (2 if placement == 'both' else 1)


@pytest.mark.parametrize('beat_field', ['vibrato', 'wideVibrato'])
@pytest.mark.parametrize('note_fields,kind', [({'leftHandVibrato': 'slight'}, 'slight'),
    ({'leftHandVibrato': 'wide'}, 'wide'), ({'vibrato': True}, 'slight'), ({'wideVibrato': True}, 'wide'),
    ({'leftHandVibrato': 'slight', 'wideVibrato': True}, 'slight')])
def test_explicit_note_precedence_is_independent_of_beat_annotation(beat_field, note_fields, kind):
    raw = raw_score([measure(beat(9, **note_fields))])
    before = render(parse(raw))
    raw['parts'][0]['measures'][0]['voices'][0]['beats'][0][beat_field] = True
    score = parse(raw)
    after = render(score)
    assert notes(before) == notes(after)
    assert notes(after)[0]['vibrato_marks'] == [{'start': 0., 'end': 2., 'intensity': kind}]
    assert score.tracks[0].bars[0][0].effects['__finger_vibrato'] == kind
    assert '__beat_vibrato' not in score.tracks[0].bars[0][0].effects


@pytest.mark.parametrize('kinds,intervals', [
    (('slight', None, None), [(0, 2, 'slight')]),
    ((None, 'wide', None), [(.5, 1, 'wide')]),
    (('wide', 'slight', None), [(0, .5, 'wide'), (.5, 1, 'slight')]),
    (('slight', 'wide', 'slight'), [(0, .5, 'slight'), (.5, 1, 'wide'), (1, 2, 'slight')]),
])
def test_genuine_note_controllers_keep_initial_and_late_reset_schedule(kinds, intervals):
    bs = [beat(7, duration=d, **({'tie': True} if i else {}),
               **({'leftHandVibrato': kind} if kind else {}))
          for i, (d, kind) in enumerate(zip(((1, 4), (1, 4), (1, 2)), kinds))]
    for b in bs: b['vibrato'] = True
    n = notes(render(parse(raw_score([measure(*bs)]))))[0]
    assert n['vibrato_marks'] == [{'start': a, 'end': b, 'intensity': kind} for a, b, kind in intervals]
    assert n['f'] == 7 and n['sus'] == 2 and len(n['source_ids']) == 3


def actual_window():
    rest = {'duration': [1, 2], 'rest': True, 'notes': []}
    bs = [deepcopy(rest)]
    for i, fret in enumerate((4, 2, 0, 2)):
        bs.append({'type': 16, 'duration': [1, 16], 'notes': [
            {'string': 3, 'fret': fret}, {'string': 4, 'fret': 3 if i == 0 else 0,
                                        **({'tie': True} if i else {})}]})
    bs.append({'type': 4, 'duration': [1, 4], 'vibrato': True,
               'notes': [{'string': 3, 'fret': 2, 'vibrato': True}, {'string': 4, 'fret': 0, 'tie': True}]})
    next_bar = {'type': 2, 'duration': [1, 2], 'notes': [
        {'string': 3, 'fret': 0, 'tie': True}, {'string': 4, 'fret': 0}]}
    raw = raw_score([measure(*bs), measure(next_bar, deepcopy(rest))])
    raw['parts'][0]['automations']['tempo'][0]['bpm'] = 72
    return raw


def test_actual_mixed_beat_window_preserves_a_hold_and_explicit_d_vibrato():
    raw = actual_window()
    before = deepcopy(raw)
    score = parse(raw)
    model_before = deepcopy(score)
    p = render(score)
    ns = notes(p)
    assert len(ns) == 7
    a = next(n for n in ns if n['s'] == 1 and n['f'] == 3)
    assert a['t'] == pytest.approx(5/3) and a['sus'] == pytest.approx(5/3)
    assert len(a['source_ids']) == 5 and not a.get('vb') and 'vibrato_marks' not in a
    d = next(n for n in ns if n['s'] == 2 and n['t'] == 2.5)
    assert d['f'] == 2 and d['sus'] == 2.5 and len(d['source_ids']) == 2
    assert d['vibrato_marks'] == [{'start': 0., 'end': 2.5, 'intensity': 'slight'}]
    assert len(p['plainTieIdentityEvidence']) == 5
    late = next(n for n in written_notes(p) if n['source_id'] == 'songsterr:0:0:0:5:1')
    assert late['tied'] is True and (late['fret'], late['midi']) == (3, 48)
    assert 'vib' not in late
    assert raw == before and score == model_before


@pytest.mark.parametrize('field', ['vibrato', 'wideVibrato'])
@pytest.mark.parametrize('bend', [False, True])
def test_metadata_only_continuation_uses_existing_plain_target_rule(field, bend):
    first = beat(7, duration=(1, 2))
    if bend: first['notes'][0]['bend'] = {'points': [{'position': 0, 'tone': 0}, {'position': 60, 'tone': 100}]}
    tail = beat(0, duration=(1, 2), tie=True)
    tail[field] = True
    p = render(parse(raw_score([measure(first, tail)])))
    assert [(n['f'], n['sus']) for n in notes(p)] == [(7, 2)]
    assert not notes(p)[0].get('vb') and 'vibrato_marks' not in notes(p)[0]
    row = p['plainTieIdentityEvidence'][0]
    assert row['authored'] == {'fret': 0} and row['used'] == {'fret': 7}
    assert row['rule'] == ('plain-tie-keeps-bent-attack-target' if bend else 'plain-tie-keeps-attack-target')
    if bend: assert p['fingerBendTimingEvidence'][0]['status'] == 'resolved'


@pytest.mark.parametrize('gesture', ['vibrato', 'wideVibrato', 'leftHandVibrato', 'hp', 'slide', 'bend', 'harmonic'])
def test_metadata_policy_does_not_admit_real_continuation_pitch_gestures(gesture):
    first, tail = beat(7, duration=(1, 2)), beat(0, duration=(1, 2), tie=True)
    tail['vibrato'] = True
    n = tail['notes'][0]
    n[gesture] = {'leftHandVibrato': 'slight', 'slide': 'below', 'harmonic': 'natural',
                  'bend': {'points': [{'position': 0, 'tone': 0}, {'position': 60, 'tone': 100}]}}.get(gesture, True)
    with pytest.raises(ScoreImportError): render(parse(raw_score([measure(first, tail)])))


@pytest.mark.parametrize('beat_field', ['vibrato', 'wideVibrato', 'tremoloBar', 'vibratoWithTremoloBar'])
def test_conservative_negative_fret_mute_expression_guard_is_unchanged(beat_field):
    b = beat(-1, dead=True)
    b[beat_field] = {'tremoloBar': {'points': [{'position': 0, 'tone': 0}, {'position': 60, 'tone': 100}]},
                     'vibratoWithTremoloBar': 'slight'}.get(beat_field, True)
    with pytest.raises(ScoreImportError, match='negative-fret mute'): parse(raw_score([measure(b)]))


def test_historical_mode_retains_named_fallback_and_feature_accounting():
    raw = raw_score([measure(beat(3))])
    raw['parts'][0]['measures'][0]['voices'][0]['beats'][0]['wideVibrato'] = True
    new, old = parse(raw), parse(raw, vibrato_policy=WRITTEN_BEAT_VIBRATO_POLICY)
    assert new.source['fingerVibratoPolicy'] == NOTE_VIBRATO_POLICY
    assert 'fingerVibratoPolicy' not in old.source
    assert old.tracks[0].bars[0][0].effects == {'vb': True, '__wide_vibrato': True,
                                             '__finger_vibrato': 'wide', '__beat_vibrato': True}
    assert new.tracks[0].bars[0][0].effects == {}
    a = next(e for e in new.feature_inventory if e['scope'] == 'Songsterr beat' and e['field'] == 'wideVibrato')
    b = next(e for e in old.feature_inventory if e['scope'] == 'Songsterr beat' and e['field'] == 'wideVibrato')
    assert a['handling'] == 'notation' and a['representations'] == ['notation', 'source']
    assert b['handling'] == 'playable' and b['representations'] == ['notation', 'playable', 'source']
    warning = next(f for f in inspect_songsterr(raw, note_owned_vibrato=False)['findings'] if f['feature'] == 'beat.wideVibrato')
    assert warning['message'] == 'Beat-level wide vibrato is retained as a written instruction; its playback timing is not confirmed by the source player.'


REFERENCE = json.loads((Path(__file__).parent/'fixtures/songsterr_beat_vibrato_bends_reference.json').read_bytes())


@pytest.mark.parametrize('case', REFERENCE['cases'], ids=lambda c: c['id'])
def test_historical_bend_receipts_and_notes_match_immutable_reference(case):
    raw = deepcopy(case['source'])
    p = render(parse(raw, vibrato_policy=WRITTEN_BEAT_VIBRATO_POLICY))
    assert notes(p)[0] == case['note']
    assert p['fingerBendTimingEvidence'][0]['terminalSlideOut']['beatVibrato']['policy'] == 'independent-written-instruction'
    assert raw == case['source']


@pytest.mark.parametrize('policy', [None, True, False, 94, '', 'unknown', [], {}])
def test_unknown_parser_policy_cannot_silently_select_historical_mode(policy):
    with pytest.raises(ScoreImportError, match='finger-vibrato policy'):
        parse(raw_score([measure(beat(3))]), vibrato_policy=policy)

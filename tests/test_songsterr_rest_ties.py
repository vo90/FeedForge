"""Plain source ties hold the preceding attack through explicit written rests."""
from copy import deepcopy
from fractions import Fraction as F
import hashlib
import json

import pytest

from feedback_converter.song_import.model import ScoreImportError
from feedback_converter.song_import.rest_ties import archive_evidence
from feedback_converter.song_import.songsterr import parse
from feedback_converter.song_import.timeline import render
from test_song_import_score import beat, measure, raw_score
from test_songsterr_plain_tie_identity import flat, produced, written


def rest(duration=(1, 4), **fields):
    return {'duration': list(duration), 'notes': [{'rest': True}], **fields}


def source(origin=7, stored=0, follow=False):
    beats = [beat(origin, duration=(1, 4)), rest(), beat(stored, duration=(1, 4), tie=True)]
    if follow:
        beats.append(beat(origin, duration=(1, 4), tie=True))
    return raw_score([measure(*beats)])


@pytest.mark.parametrize('origin,stored', [(7, 0), (0, 11), (7, 7), (3, 3), (2, 2), (48, 0)])
@pytest.mark.parametrize('bass', [False, True])
@pytest.mark.parametrize('version', [None, 5, 8])
def test_rest_bridge_preserves_attack_pitch_and_all_later_continuations(origin, stored, bass, version):
    raw = source(origin, stored, follow=True)
    raw.update(songId=987654, title='Any title')
    if bass:
        raw['tracks'][0].update(instrumentId=33, tuning=[43, 38, 33, 28])
    if version is not None:
        raw['parts'][0]['version'] = version
    p = produced(raw)
    track, = p['tracks']
    note, = flat(track)
    assert (note['t'], note['f'], note['sus']) == (0, origin, 2)
    assert len(note['source_ids']) == 3
    bridge, later = p['restTieEvidence']
    assert (bridge['previousEnd'], bridge['start'], bridge['end']) == (.5, 1, 1.5)
    assert bridge['authored'] == {'fret': stored} and bridge['used'] == {'fret': origin}
    assert bridge['bridgesRest'] is True
    assert bridge['rests'] == [{'sourceId': 'songsterr:0:0:0:1',
        'location': 'parts/0/measures/0/voices/0/beats/1', 'occurrence': 1,
        'start': .5, 'end': 1, 'quarters': {'start': '1', 'end': '2'}}]
    assert later['bridgesRest'] is False and later['rests'] == []
    assert (later['previousEnd'], later['start'], later['end']) == (1.5, 1.5, 2)
    assert bridge['originSourceId'] == later['originSourceId'] == note['source_ids'][0]
    assert [n['fret'] for n in written(track)] == [origin] * 3
    assert not p.get('plainTieIdentityEvidence')


def test_original_bytes_and_receipt_are_independent(tmp_path):
    raw = source(follow=True)
    path = tmp_path / 'source.json'
    path.write_text(json.dumps(raw, indent=2), encoding='utf-8')
    before = path.read_bytes()
    p = produced(raw)
    receipt = archive_evidence(p, path)
    assert receipt == {'version': 1, 'policy': 'songsterr-rest-tie-continuation-v1',
        'timeDomain': 'score_seconds', 'sourceSha256': hashlib.sha256(before).hexdigest(),
        'continuations': p['restTieEvidence']}
    receipt['continuations'][0]['rests'][0]['quarters']['start'] = '99'
    assert p['restTieEvidence'][0]['rests'][0]['quarters']['start'] == '1'
    assert path.read_bytes() == before


@pytest.mark.parametrize('kind', ['other-string', 'latest-attack', 'two-gaps', 'note-rest', 'beat-rest', 'bool-bar'])
def test_qualified_gap_variations(kind):
    raw = source()
    bs = raw['parts'][0]['measures'][0]['voices'][0]['beats']
    expected_fret, expected_end, expected_rows = 7, 1.5, 1
    if kind == 'other-string':
        bs[0]['duration'] = bs[1]['duration'] = [1, 8]
        bs.insert(2, beat(3, string=1, duration=(1, 4)))
    elif kind == 'latest-attack':
        bs[0]['duration'] = [1, 8]
        bs.insert(1, beat(5, duration=(1, 8)))
        expected_fret = 5
    elif kind == 'two-gaps':
        bs.extend([rest(duration=(1, 8)), beat(7, duration=(1, 8), tie=True)])
        expected_end, expected_rows = 2, 2
    elif kind == 'beat-rest':
        bs[1] = {'duration': [1, 4], 'notes': [], 'rest': True}
    elif kind == 'bool-bar':
        for b in bs:
            b['tremoloBar'] = True
    p = produced(raw)
    n = next(n for n in flat(p['tracks'][0]) if len(n['source_ids']) > 1)
    assert n['f'] == expected_fret and n['t'] + n['sus'] == expected_end
    assert len(p['restTieEvidence']) == expected_rows


@pytest.mark.parametrize('whole_voice', [False, True])
@pytest.mark.parametrize('gap', [1, 16])
def test_whole_bars_and_long_gaps_have_no_duration_cutoff(whole_voice, gap):
    raw = raw_score([measure(beat(7))] + [measure(rest((1, 1))) for _ in range(gap)]
                    + [measure(beat(0, tie=True))])
    if whole_voice:
        for bar in raw['parts'][0]['measures'][1:-1]:
            bar['voices'][0] = {'beats': [], 'rest': True}
    p = produced(raw)
    n, = flat(p['tracks'][0])
    assert (n['f'], n['sus']) == (7, (gap + 2) * 2)
    row, = p['restTieEvidence']
    assert len(row['rests']) == gap and row['occurrence'] == gap + 2
    assert row['rests'][0]['sourceId'] == ('songsterr:0:1:0' if whole_voice else 'songsterr:0:1:0:0')


def test_tempo_changes_across_gap_use_exact_performed_endpoints():
    raw = raw_score([measure(beat(0)), measure(rest((1, 1))), measure(beat(11, tie=True))])
    raw['parts'][0]['automations']['tempo'].append({'measure': 1, 'position': [0, 1], 'bpm': 60, 'type': 4})
    p = produced(raw)
    n, = flat(p['tracks'][0])
    row, = p['restTieEvidence']
    assert n['sus'] == 10
    assert (row['previousEnd'], row['start'], row['end']) == (2, 6, 10)
    assert row['rests'][0]['quarters'] == {'start': '4', 'end': '8'}


@pytest.mark.parametrize('voice_conflict', [False, True])
def test_voice_projection_retains_exact_lineage(voice_conflict):
    raw = source(7, 0, follow=True)
    raw['parts'][0]['measures'][0]['voices'].append({'beats': [
        beat(12, string=0 if voice_conflict else 1, duration=(1, 4)), rest(),
        beat(4, string=0 if voice_conflict else 1, duration=(1, 2), tie=True)]})
    p = produced(raw)
    assert len(p['tracks']) == (2 if voice_conflict else 1)
    assert sorted(n['f'] for t in p['tracks'] for n in flat(t)) == [7, 12]
    for row in p['restTieEvidence']:
        track = next(t for t in p['tracks'] if t['id'] == row['trackId'])
        n = next(n for n in flat(track) if row['sourceId'] in n['source_ids'])
        assert n['f'] == row['used']['fret']
        assert n['source_ids'][0] == row['originSourceId']


@pytest.mark.parametrize('kind', ['orphan', 'other-voice', 'other-string', 'duplicate-origin', 'duplicate-tie',
                                 'duplicate-history', 'numeric-tie', 'string-tie'])
def test_missing_or_ambiguous_origin_stays_guarded(kind):
    raw = source(follow=True)
    voices = raw['parts'][0]['measures'][0]['voices']
    bs = voices[0]['beats']
    if kind == 'orphan':
        bs.pop(0)
    elif kind == 'other-voice':
        voices.append({'beats': [bs.pop(0)]})
    elif kind == 'other-string':
        bs[2]['notes'][0]['string'] = 1
    elif kind == 'duplicate-origin':
        bs[0]['notes'].append(deepcopy(bs[0]['notes'][0]))
    elif kind == 'duplicate-tie':
        bs[2]['notes'].append(deepcopy(bs[2]['notes'][0]))
    elif kind == 'duplicate-history':
        first_tie = beat(7, duration=(1, 8), tie=True)
        first_tie['notes'].append(deepcopy(first_tie['notes'][0]))
        bs[0]['duration'] = [1, 8]
        bs.insert(1, first_tie)
    else:
        bs[2]['notes'][0]['tie'] = 1 if kind == 'numeric-tie' else 'true'
    with pytest.raises(ScoreImportError):
        produced(raw)


NOTE_GESTURES = [{'vibrato': True}, {'dead': True}, {'harmonic': 'pinch'}, {'staccato': True},
                 {'slide': 'upwards'}, {'slide': 'shift'}, {'hp': True}, {'leftFingering': '2'},
                 {'bend': {'points': [{'position': 0, 'tone': 0}, {'position': 60, 'tone': 100}]}},
                 {'trill': {'auxiliaryFret': 9, 'speed': 60}}]


@pytest.mark.parametrize('where', ['origin', 'continuation', 'later-same-fret', 'earlier-tie'])
@pytest.mark.parametrize('fields', NOTE_GESTURES)
def test_every_segment_in_held_chain_must_remain_plain(where, fields):
    raw = source(follow=True)
    bs = raw['parts'][0]['measures'][0]['voices'][0]['beats']
    target = {'origin': 0, 'continuation': 2, 'later-same-fret': 3}.get(where)
    if where == 'earlier-tie':
        bs[0]['duration'] = [1, 8]
        bs.insert(1, beat(7, duration=(1, 8), tie=True))
        target = 1
    bs[target]['notes'][0].update(fields)
    with pytest.raises(ScoreImportError):
        produced(raw)


@pytest.mark.parametrize('where', ['origin', 'rest', 'continuation', 'later-same-fret', 'other-string-in-gap'])
@pytest.mark.parametrize('fields', [{'vibrato': True}, {'wah': 'open'}, {'pickStroke': 'up'},
    {'tremoloBar': {'points': [{'position': 0, 'tone': 0}, {'position': 60, 'tone': -100}]}},
    {'brushStroke': {'direction': 'down', 'duration': 30, 'shift': 0}}])
def test_beat_controllers_anywhere_in_history_stay_guarded(where, fields):
    raw = source(follow=True)
    bs = raw['parts'][0]['measures'][0]['voices'][0]['beats']
    if where == 'other-string-in-gap':
        bs[1]['duration'] = [1, 8]
        bs.insert(2, beat(2, string=1, duration=(1, 8)))
        target = 2
    else:
        target = {'origin': 0, 'rest': 1, 'continuation': 2, 'later-same-fret': 3}[where]
    bs[target].update(fields)
    with pytest.raises(ScoreImportError):
        produced(raw)


@pytest.mark.parametrize('place', ['rest-note', 'other-string-note'])
def test_nonplain_note_payload_in_gap_cannot_be_silently_lost(place):
    raw = source()
    bs = raw['parts'][0]['measures'][0]['voices'][0]['beats']
    if place == 'other-string-note':
        bs[1]['duration'] = [1, 8]
        bs.insert(2, beat(2, string=1, duration=(1, 8)))
    bs[1 if place == 'rest-note' else 2]['notes'][0]['vibrato'] = True
    with pytest.raises(ScoreImportError):
        produced(raw)


@pytest.mark.parametrize('kind', ['logical-overlap', 'rest-overlap', 'tie-history-overlap'])
def test_overlapping_logical_spans_do_not_qualify(kind):
    score = parse(source(follow=True))
    track = score.tracks[0]
    if kind == 'logical-overlap':
        track.bars[0][0].duration = F(3)
    elif kind == 'rest-overlap':
        track.written_bars[0][0].beats[1].duration = F(2)
    else:
        track.bars[0][-1].position = F(5, 2)
    with pytest.raises(ScoreImportError):
        render(score)


def test_backward_jump_does_not_continue_bridged_attack():
    raw = raw_score([measure(beat(7)), measure(rest((1, 2)), beat(0, duration=(1, 2), tie=True), repeatStart=True),
                     measure(rest((1, 1)), repeat=2)])
    with pytest.raises(ScoreImportError):
        produced(raw)


def test_forward_skipped_ending_does_not_bridge_a_written_route_gap():
    raw = raw_score([
        measure(beat(7, duration=(1, 4)), rest((3, 4)), repeatStart=True),
        measure(beat(3, string=1), repeat=2, alternateEnding=1),
        measure(rest((1, 1)), alternateEnding=2),
        measure(beat(0, tie=True))])
    with pytest.raises(ScoreImportError):
        produced(raw)


@pytest.mark.parametrize('slide', ['shift', 'legato'])
def test_preceding_slide_link_does_not_make_a_plain_origin(slide):
    raw = raw_score([measure(beat(3, duration=(1, 4), slide=slide),
        beat(7, duration=(1, 4)), rest(), beat(0, duration=(1, 4), tie=True))])
    with pytest.raises(ScoreImportError):
        produced(raw)


def test_ordinary_and_unfilled_ties_keep_existing_policy():
    ordinary = produced(raw_score([measure(beat(7, duration=(1, 2)), beat(7, duration=(1, 2), tie=True))]))
    unfilled = produced(raw_score([measure(beat(7, duration=(1, 4))), measure(beat(0, tie=True))]))
    assert not ordinary.get('restTieEvidence') and not unfilled.get('restTieEvidence')
    assert unfilled['plainTieIdentityEvidence'][0]['used'] == {'fret': 7}

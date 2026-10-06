"""Native held-target evidence; raw model frets remain source data."""
from copy import deepcopy
from fractions import Fraction as F
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from feedback_converter.song_import.songsterr import parse
from feedback_converter.song_import.timeline import render
from feedback_converter.song_import.verify_source import songsterr
from feedback_converter.song_import.verify_timeline import expected
from tools.songsterr_compatibility.audit import _strum_tick_adjustment

FIXTURE = json.loads((Path(__file__).parent / 'fixtures/songsterr_plain_tie_native.json').read_text())
ROWS = FIXTURE['cases']


def producer_notes(performance):
    track = performance['tracks'][0]
    return sorted(track['notes'] + [{**n, 't': c['t']} for c in track['chords'] for n in c['notes']],
                  key=lambda n: (n['t'], n['s']))


def native_source_clock(row):
    profile = row.get('referenceProfile', 'authored')
    native = row['reference'][profile]
    resolution = native['tpqn']
    strings = len(row['source']['parts'][0]['tuning'])
    wanted = []
    events = native['events'] if row.get('nativeClockStage') == 'final' else native['heldEvents']
    for note in events:
        if note['hidden']:
            continue
        mi, vi, bi, ni = map(int, note['id'].split(':'))
        beat = row['source']['parts'][0]['measures'][mi]['voices'][vi]['beats'][bi]
        # Correct only the independently known native tick floor. This is not
        # a broad native/audio tolerance or permission to normalize raw frets.
        floor = _strum_tick_adjustment(beat, ni, resolution, profile)
        attack = (F(note['attackTick']) - floor) / resolution
        endpoint = F(str(note['endTick'] + 1)) / resolution
        # Package clock seconds are serialized to six decimal places. The
        # independent reader retains floats before that transport projection.
        wanted.append({'t': round(float(attack * F(12, 13)), 6), 's': strings - 1 - note['string'],
                       'f': note['fret'], 'endpoint': round(float(endpoint * F(12, 13)), 6)})
    return sorted(wanted, key=lambda n: (n['t'], n['s']))


@pytest.mark.parametrize('row', ROWS, ids=lambda r: r['id'])
def test_plain_tie_held_identity_matches_reviewed_native_source_clock(row):
    source = deepcopy(row['source']); before = deepcopy(source)
    # The pinned contract90 fixture retains its original guarded disposition.
    # Contract93 separately qualifies this ordinary initial bend's plain tie.
    newly_qualified_bend = row['id'] == 'plain-tie/guard/ancestor-bend'
    if newly_qualified_bend:
        assert row['expectedDisposition'] == 'blocked'
    if row['expectedDisposition'] == 'blocked' and not newly_qualified_bend:
        for reader in (lambda: render(parse(source)),
                       lambda: expected(songsterr(source), {'offset': 0, 'scale': 1})):
            with pytest.raises(ValueError):
                reader()
        assert source == before
        return
    score = parse(source)
    raw_model = [(n.source_id, n.fret) for t in score.tracks for b in t.bars for n in b]
    actual = render(score)
    independent_model = songsterr(source)
    original_atoms = [(n.location, n.fret) for p in independent_model.parts for b in p.bars for n in b]
    independent = expected(independent_model, {'offset': 0, 'scale': 1})
    if newly_qualified_bend:
        for rows in (actual['plainTieIdentityEvidence'],independent['plain_tie_identities']):
            identity, = rows
            assert identity['rule'] == 'plain-tie-keeps-bent-attack-target'
            assert identity['authored'] == {'fret':0} and identity['used'] == {'fret':7}
        assert actual['fingerBendTimingEvidence'][0]['status'] == 'resolved'
    candidates = [producer_notes(actual),
                  sorted((n['note'] for n in independent['parts'][0]['notes']), key=lambda n: (n['t'], n['s']))]
    wanted = native_source_clock(row)
    for notes in candidates:
        assert len(notes) == len(wanted)
        for note, reference in zip(notes, wanted):
            assert (note['s'], note['f']) == (reference['s'], reference['f'])
            assert round(note['t'], 6) == reference['t']
            assert round(note['t'] + note['sus'], 6) == reference['endpoint']
    assert raw_model == [(n.source_id, n.fret) for t in score.tracks for b in t.bars for n in b]
    assert original_atoms == [(n.location, n.fret) for p in independent_model.parts for b in p.bars for n in b]
    assert source == before


def test_repeat_entrance_uses_the_performed_visit_target():
    row = next(r for r in ROWS if r['id'] == 'plain-tie/repeat/visits/7/9/0')
    native = row['reference']['authored']
    ties = [n for n in native['authoredEvents'] if n['id'] == '1:0:0:0']
    assert [(n['occurrence'], n['sourceFret'], n['fret']) for n in ties] == [(1, 0, 7), (3, 0, 9)]
    performance = render(parse(row['source']))
    receipts = performance['plainTieIdentityEvidence']
    entrance = [r for r in receipts if r['sourceId'].endswith(':1:0:0:0')]
    assert [(r['occurrence'], r['authored']['fret'], r['used']['fret']) for r in entrance] == [(2, 0, 7), (4, 0, 9)]


def test_profile_and_native_preparation_scope_are_reviewable():
    assert FIXTURE['policy'] == 'songsterr-plain-tie-identity-v1'
    assert FIXTURE['referenceSha256'] == '4219f9dbf0952af83be36c8b590f10fea7a0f36d72bd1f92297a3d768f93f5ab'
    assert {b['name'] for b in FIXTURE['bindings']} == {'fc', 'ks', 'za', 'Y', 'ro', 'no'}
    assert FIXTURE['profileOptions']['authored']['autoFixJson'] is False
    assert FIXTURE['profileOptions']['legacy-brush-authored-v1']['autoFixJson'] is True
    for row in ROWS:
        profiles = row['reference']
        assert [(n['id'], n['occurrence'], n['fret']) for n in profiles['authored']['authoredEvents']] == [
            (n['id'], n['occurrence'], n['fret']) for n in profiles['legacy-brush-authored-v1']['authoredEvents']]


def test_hidden_explicit_slide_retains_raw_fret_dependent_events():
    row = next(r for r in ROWS if r['id'] == 'plain-tie/guard/continuation-slide')
    native = row['reference']['authored']
    continuation = next(n for n in native['generatedFinal'] if n['tie'])
    assert continuation['hidden'] is True
    assert any(e['name'] == 'NOTE_ON' for e in continuation['events'])
    assert row['expectedDisposition'] == 'blocked'


@pytest.mark.parametrize('field', ['attackTick', 'endTick', 'fret', 'missing'])
def test_native_held_corruption_exceeds_the_transport_projection(field):
    row = deepcopy(next(r for r in ROWS if r['id'] == 'plain-tie/basic/guitar/5/7/0'))
    before = native_source_clock(row)
    events = row['reference']['authored']['heldEvents']
    if field == 'missing':
        events.pop(0)
    elif field == 'fret':
        events[0][field] += 1
    else:
        events[0][field] += .5
    assert native_source_clock(row) != before


def test_later_same_fret_gestures_keep_their_own_source_metadata():
    harmonic = next(r for r in ROWS if r['id'] == 'plain-tie/after-plain/harmonic')
    slide = next(r for r in ROWS if r['id'] == 'plain-tie/after-plain/slide')
    a = render(parse(harmonic['source']))
    b = expected(songsterr(harmonic['source']), {'offset': 0, 'scale': 1})
    for note in (producer_notes(a)[0], b['parts'][0]['notes'][0]['note']):
        assert note['f'] == 9
        event = note['harmonic_changes']['events'][0]
        assert event['source_id'].endswith(':0:0:0:2:0')
        assert event['target']['node'] == 7 and event['target']['interval'] == 19
    for notes in (producer_notes(render(parse(slide['source']))),
                  [n['note'] for n in expected(songsterr(slide['source']), {'offset': 0, 'scale': 1})['parts'][0]['notes']]):
        assert notes[0]['f'] == 9 and notes[0]['sl'] == 12
        assert notes[1]['f'] == 12

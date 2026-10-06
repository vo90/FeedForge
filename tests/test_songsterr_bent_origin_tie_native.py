"""First-origin bend identity preserves native holding and rational source curves."""
from copy import deepcopy
from fractions import Fraction as F
import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from feedback_converter.song_import.songsterr import parse
from feedback_converter.song_import.timeline import render
from feedback_converter.song_import.verify_source import songsterr
from feedback_converter.song_import.verify_timeline import expected
from tools.songsterr_compatibility.audit import canonical_hash, compare_preparation, evaluate

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads((Path(__file__).parent / 'fixtures/songsterr_bent_origin_tie_native.json').read_text())
ROWS = FIXTURE['cases']
PROFILES = ('authored', 'legacy-brush-authored-v1', 'player-defaults')


def source_seconds(row, q):
    if row['id'] == 'bent-origin-tie/tempo/across-bars':
        return min(q, F(2)) * F(5, 6) + min(max(q-F(2), F(0)), F(4)) * F(5, 8) + max(q-F(6), F(0)) * F(1, 2)
    if row['id'] == 'bent-origin-tie/tempo/within-group':
        return min(q, F(2)) * F(5, 6) + max(q-F(2), F(0)) * F(5, 8)
    return q * F(5, 6)


def producer_notes(performance):
    return sorted([n for t in performance['tracks'] for n in t['notes']] + [
        {'t': c['t'], **n} for t in performance['tracks'] for c in t['chords'] for n in c['notes']], key=lambda n: (n['t'], n['s']))


def clock(notes):
    return [(round(n['t'], 6), n['s'], n['f'], round(n['t'] + n['sus'], 6)) for n in notes]


def raw_origin(row, native):
    mi, vi, bi, ni = map(int, native['id'].split(':'))
    return row['source']['parts'][0]['measures'][mi]['voices'][vi]['beats'][bi]['notes'][ni]


def ideal_curve(row, native, resolution):
    start, stop = F(native['attackTick'], resolution), F(native['endTick'] + 1, resolution)
    raw = raw_origin(row, native)
    controls = [(start + (stop-start)*F(p['position'], 60), F(p['tone'], 50)) for p in raw['bend']['points']]
    knots = set(q for q, _ in controls)
    # A repeat jump restores written tempo inheritance, so it supplies a clock
    # knot even when the inherited BPM happens to equal the previous BPM.
    order = row['reference']['authored']['traversal']
    knots |= {F(4 * k) for k in range(1, len(order))
              if order[k] != order[k-1] + 1 and start < F(4 * k) < stop}
    if '/tempo/' in row['id']:
        knots |= {q for q in (F(2), F(6)) if start < q < stop}
    result = []
    for q in sorted(knots):
        value = controls[-1][1]
        for a, b in zip(controls, controls[1:]):
            if a[0] <= q <= b[0]:
                value = a[1] + (b[1]-a[1])*(q-a[0])/(b[0]-a[0]); break
        result.append((float(source_seconds(row, q)-source_seconds(row, start)), float(value)))
    return result


@pytest.mark.parametrize('row', ROWS, ids=lambda row: row['id'])
def test_bent_origin_identity_uses_native_held_clock_and_independent_source_curve(row):
    source = deepcopy(row['source']); before = deepcopy(source)
    assert canonical_hash(source) == row['sourceSha256']
    if row['expectedDisposition'] == 'blocked':
        for read in (lambda: render(parse(source)), lambda: expected(songsterr(source), {'offset': 0, 'scale': 1})):
            with pytest.raises(ValueError): read()
        assert row['guardOnly'] is True and source == before
        return
    parsed = parse(source)
    raw_model = [(n.source_id, n.fret) for t in parsed.tracks for b in t.bars for n in b]
    independent = songsterr(source)
    raw_atoms = [(n.location, n.fret) for p in independent.parts for b in p.bars for n in b]
    actual, checked = render(parsed), expected(independent, {'offset': 0, 'scale': 1})
    candidates = [producer_notes(actual), sorted((r['note'] for p in checked['parts'] for r in p['notes']), key=lambda n: (n['t'], n['s']))]
    assessment = evaluate(source)
    strings = len(source['parts'][0]['tuning'])
    for profile in PROFILES:
        native = row['reference'][profile]
        assert compare_preparation(assessment, {'parts': [{'index': 0, 'status': 'executed', **native}]}) == []
        held = sorted((n for n in native['heldEvents'] if not n['hidden']), key=lambda n: (n['attackTick'], strings-1-n['string']))
        wanted = [(round(float(source_seconds(row, F(n['attackTick'], native['tpqn']))), 6), strings-1-n['string'], n['fret'],
                   round(float(source_seconds(row, F(n['endTick']+1, native['tpqn']))), 6)) for n in held]
        for notes in candidates:
            assert clock(notes) == wanted
            for note, origin in zip(notes, held):
                curve = ideal_curve(row, origin, native['tpqn'])
                assert [p['v'] for p in note['bnv']] == [v for _, v in curve]
                # The independent wire evaluator subtracts separately rounded
                # absolute endpoints. Only this documented microsecond transport
                # precision applies here; native pitch updates remain exact.
                assert [p['t'] for p in note['bnv']] == pytest.approx([t for t, _ in curve], abs=1e-6, rel=0)
        if row.get('sameFretControl'):
            assert row['sameFret'][profile]['emittedScheduleMatched'] is True
            assert native['scheduledSha256'] == row['sameFret'][profile]['scheduledSha256']
    def receipt_times(rows):
        rows = deepcopy(rows)
        for receipt in rows:
            for field in ('attack', 'start', 'end'):
                receipt[field] = round(receipt[field], 6)
        return rows
    assert receipt_times(actual.get('plainTieIdentityEvidence', [])) == receipt_times(checked['plain_tie_identities'])
    assert all(r['status'] == 'resolved' for r in actual['fingerBendTimingEvidence'])
    assert raw_model == [(n.source_id, n.fret) for t in parsed.tracks for b in t.bars for n in b]
    assert raw_atoms == [(n.location, n.fret) for p in independent.parts for b in p.bars for n in b]
    assert source == before


def test_repeat_continuation_identity_is_specific_to_each_visit():
    row = next(r for r in ROWS if r['id'] == 'bent-origin-tie/repeat/per-visit-origin')
    ties = [n for n in row['reference']['authored']['heldEvents'] if n['id'] == '1:0:0:0']
    assert [(n['occurrence'], n['sourceFret'], n['fret']) for n in ties] == [(1, 0, 2), (3, 0, 5)]
    receipts = render(parse(row['source']))['plainTieIdentityEvidence']
    assert [(r['occurrence'], r['authored']['fret'], r['used']['fret'], r['rule']) for r in receipts] == [
        (2, 0, 2, 'plain-tie-keeps-bent-attack-target'), (4, 0, 5, 'plain-tie-keeps-bent-attack-target')]


def test_native_integer_emission_is_distinct_from_the_rational_game_curve():
    row = next(r for r in ROWS if r['id'] == 'bent-origin-tie/basic/release/stored0')
    authored, player = row['reference']['authored'], row['reference']['player-defaults']
    native = authored['bends'][0]['points']
    assert native[0] == [1, 8874] and native[-1] == [30719, 8192]
    assert player['bends'][0]['points'][0] == [1, 9557]
    note = producer_notes(render(parse(row['source'])))[0]
    assert note['bnv'] == [{'t': 0.0, 'v': 2.0}, {'t': 5/9, 'v': 2.0},
                           {'t': 10/9, 'v': 0.0}, {'t': 5/3, 'v': 0.0}]
    # Native emission has integer tick/tone/controller quantization. Source
    # controls remain rational; no generic pitch/audio tolerance asserts equality.
    assert authored['tpqn'] == 15360
    assert next(time for time, value in native if value == 8192) == 20480


def test_worker_profiles_and_matching_error_counts_are_explicit():
    assert FIXTURE['family'] == 'ordinary-first-bent-origin-plain-tie'
    assert FIXTURE['referenceSha256'] == '4219f9dbf0952af83be36c8b590f10fea7a0f36d72bd1f92297a3d768f93f5ab'
    assert hashlib.sha256((ROOT/'tools/songsterr_compatibility/reference-manifest.json').read_bytes()).hexdigest() == FIXTURE['manifestSha256']
    assert FIXTURE['qualification'] == {'cases': 50, 'guardCases': 24, 'comparisons': 222,
                                        'matched': 222, 'matchingReferenceErrors': 0}
    assert {b['name'] for b in FIXTURE['bindings']} == {'fc', 'uc', 'ks', 'As', 'xo', 'uo'}
    assert FIXTURE['profileOptions']['authored'] == {'synth': 'fluidsynth', 'useRSE': False, 'autoFixJson': False, 'humanize': False}
    assert FIXTURE['profileOptions']['legacy-brush-authored-v1'] == {'synth': 'fluidsynth', 'useRSE': False, 'autoFixJson': True, 'humanize': False}
    assert all(set(r['reference']) == set(PROFILES) for r in ROWS)

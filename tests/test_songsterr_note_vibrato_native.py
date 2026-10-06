"""Pinned full-worker controls establish note ownership and hidden tie intervals."""
from copy import deepcopy
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from feedback_converter.song_import.songsterr import parse
from feedback_converter.song_import.timeline import render
from feedback_converter.song_import.verify_source import songsterr
from feedback_converter.song_import.verify_timeline import expected
from feedback_converter.song_import.voices import flat
from tools.songsterr_compatibility.audit import canonical_hash

FIXTURE = json.loads((Path(__file__).parent / 'fixtures/songsterr_note_vibrato_native.json').read_bytes())
PROFILES = ('authored', 'legacy-brush-authored-v1', 'player-defaults')


def raw_note(source, identity):
    m, v, b, n = map(int, identity.split(':'))
    return source['parts'][0]['measures'][m]['voices'][v]['beats'][b]['notes'][n]


def kind(note):
    return note.get('leftHandVibrato') or ('wide' if note.get('wideVibrato') else 'slight' if note.get('vibrato') else None)


def seconds(ticks, resolution):
    # All portable controls use an exact constant 72 BPM source clock.
    return float(Fraction(ticks, resolution) * Fraction(5, 6))


def music(notes):
    return [(round(n['t'], 6), n['s'], n['f'], round(n['t'] + n['sus'], 6)) for n in notes]


@pytest.mark.parametrize('row', FIXTURE['cases'], ids=lambda row: row['id'])
def test_current_ownership_matches_native_held_targets_and_controller_owners(row):
    source = deepcopy(row['source']); before = deepcopy(source)
    assert canonical_hash(source) == row['sourceSha256']
    if row['guardedOriginal'] is not None:
        for read in (lambda: render(parse(row['guardedOriginal'])),
                     lambda: expected(songsterr(row['guardedOriginal']), {'offset': 0, 'scale': 1})):
            with pytest.raises(ValueError): read()
    parsed = parse(source); model = deepcopy(parsed)
    independent = songsterr(source); atoms = deepcopy(independent)
    chart = render(parsed)
    oracle = expected(independent, {'offset': 0, 'scale': 1})
    candidates = [sorted(flat(chart['tracks'][0]), key=lambda n: (n['t'], n['s'])),
                  sorted((r['note'] for p in oracle['parts'] for r in p['notes']), key=lambda n: (n['t'], n['s']))]
    strings = len(source['parts'][0]['tuning'])
    for profile in PROFILES:
        native = row['reference'][profile]; resolution = native['tpqn']
        held = sorted((n for n in native['heldEvents'] if not n['hidden']),
                      key=lambda n: (n['attackTick'], strings - 1 - n['string']))
        wanted = [(round(seconds(n['attackTick'], resolution), 6), strings - 1 - n['string'], n['fret'],
                   round(seconds(n['endTick'] + 1, resolution), 6)) for n in held]
        for notes in candidates:
            assert music(notes) == wanted
            for note, origin in zip(notes, held):
                intervals = []
                for owner in native['heldEvents']:
                    controller_kind = kind(raw_note(source, owner['id']))
                    if owner['string'] != origin['string'] or not controller_kind:
                        continue
                    if origin['attackTick'] <= owner['attackTick'] <= origin['endTick']:
                        intervals.append({'start': seconds(owner['attackTick'] - origin['attackTick'], resolution),
                                          'end': seconds(owner['endTick'] + 1 - origin['attackTick'], resolution),
                                          'intensity': controller_kind})
                if intervals:
                    marks = note['vibrato_marks']
                    assert len(marks) == len(intervals)
                    for actual, wanted_interval in zip(marks, intervals):
                        assert actual['intensity'] == wanted_interval['intensity']
                        # Independent wire rows round absolute times separately
                        # to microseconds; this is not a native tick tolerance.
                        assert [actual[k] for k in ('start', 'end')] == pytest.approx(
                            [wanted_interval[k] for k in ('start', 'end')], abs=1e-6, rel=0)
                else:
                    assert 'vibrato_marks' not in note
                assert bool(note.get('vb')) is bool(intervals)
        for owner in native['controllerOwners']:
            controller_kind = kind(raw_note(source, owner['id']))
            # Baseline RSE CC1/25/32 instructions are excluded using the exact
            # same-profile no-vibrato control, qualified by the full worker.
            events = owner['controllerDelta']
            if controller_kind is None:
                assert events == []  # Beat flags never create a native note controller.
                continue
            if profile == 'player-defaults' and source['parts'][0]['instrumentId'] == 29:
                positive = {'slight': (35, 70, 45), 'wide': (110, 127, 90)}[controller_kind]
                assert sorted((e['time'], e['controller'], e['value']) for e in events) == sorted([
                    *[(owner['attackTick'], cc, value) for cc, value in zip((27, 28, 29), positive)],
                    *[(owner['endTick'] - 1, cc, 0) for cc in (27, 28, 29)]])
            else:
                intensity = 127 if controller_kind == 'wide' else (100 if profile == 'player-defaults' and source['parts'][0]['instrumentId'] == 24 else 64)
                assert [(e['time'], e['controller'], e['value']) for e in events] == [
                    (owner['attackTick'], 1, intensity), (owner['endTick'] - 1, 1, 0)]
    assert parsed == model and independent == atoms and source == before


def test_pinned_qualification_and_profile_scope_are_explicit():
    assert FIXTURE['qualification'] == {'cases': 22, 'comparisons': 66, 'matched': 66, 'matchingReferenceErrors': 0}
    assert FIXTURE['referenceSha256'] == '4219f9dbf0952af83be36c8b590f10fea7a0f36d72bd1f92297a3d768f93f5ab'
    assert hashlib.sha256((ROOT / 'tools/songsterr_compatibility/reference-manifest.json').read_bytes()).hexdigest() == FIXTURE['manifestSha256']
    assert FIXTURE['policy'] == 'songsterr-note-vibrato-v1'
    assert all(set(row['reference']) == set(PROFILES) and row['normalizedNativeScheduleExact'] for row in FIXTURE['cases'])
    assert FIXTURE['profileOptions']['authored'] == {'synth': 'fluidsynth', 'useRSE': False, 'autoFixJson': False, 'humanize': False}
    assert FIXTURE['profileOptions']['legacy-brush-authored-v1'] == {'synth': 'fluidsynth', 'useRSE': False, 'autoFixJson': True, 'humanize': False}
    assert {b['name'] for b in FIXTURE['bindings']} == {'fc', 'ks', 'As', 'Ko', 'Go', 'Ho', 'Wo', 'Uo'}

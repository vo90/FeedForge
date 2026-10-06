"""Pinned native clocks qualify a game interpretation, not sample-pitch repair."""
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
FIXTURE_PATH = Path(__file__).parent / 'fixtures/songsterr_negative_fret_mute_native.json'
FIXTURE = json.loads(FIXTURE_PATH.read_text())
ROWS = FIXTURE['cases']
PROFILES = ('authored', 'legacy-brush-authored-v1', 'player-defaults')


def clock(notes):
    return sorted((round(n['t'], 6), n['s'], round(n['t'] + n.get('sus', 0), 6)) for n in notes)


def native_held_clock(part, strings):
    # Native authored offTick is inclusive. Its later mute synthesis truncates
    # envelopes and can reorder chords by nominal pitch; neither is game timing.
    return sorted((round(float(F(n['attackTick'], part['tpqn']) * F(5, 6)), 6),
                   strings - 1 - n['string'],
                   round(float(F(n['endTick'] + 1, part['tpqn']) * F(5, 6)), 6))
                  for n in part['heldSlots'] if not n['hidden'])


@pytest.mark.parametrize('row', ROWS, ids=lambda row: row['id'])
def test_native_slots_match_both_readers_with_explicit_guard_dispositions(row):
    raw = deepcopy(row['source'])
    before = deepcopy(raw)
    assert canonical_hash(raw) == row['sourceSha256']
    actual = evaluate(raw)
    assert actual['converter']['status'] == actual['independent']['status'] == row['expectedDisposition'], actual
    if row['expectedDisposition'] == 'rendered':
        produced = render(parse(raw))
        checked = expected(songsterr(raw), {'offset': 0, 'scale': 1})
        notes = [n for t in produced['tracks'] for n in t['notes']] + [
            {'t': c['t'], **n} for t in produced['tracks'] for c in t['chords'] for n in c['notes']]
        independent_notes = [r['note'] for p in checked['parts'] for r in p['notes']]
        strings = len(raw['parts'][0]['tuning'])
        for profile in PROFILES:
            reference = row['reference'][profile]
            assert compare_preparation(actual, {'parts': [{'index': 0, 'status': 'executed', **reference}]}) == []
            wanted = native_held_clock(reference, strings)
            assert clock(notes) == wanted
            assert clock(independent_notes) == wanted
    else:
        assert row['guardOnly'] is True
    assert raw == before


@pytest.mark.parametrize('row', [r for r in ROWS if r.get('nullContrast')], ids=lambda row: row['id'])
def test_null_controls_preserve_source_clock_but_have_different_native_pitch(row):
    for profile in PROFILES:
        contrast = row['nullContrast'][profile]
        assert contrast['sameAuthoredAndHeldClock'] is True
        assert contrast['pitchDifferences']
        assert all(r['nullPitch'] - r['rawPitch'] == 1 for r in contrast['pitchDifferences'])
        # Preserve the observed exception rather than weakening the comparison:
        # mixed chord synthesis changes its final attack order with native pitch.
        if row['id'] == 'negative-fret-mute/chord/mixed' and profile != 'player-defaults':
            assert contrast['sameFinalClock'] is False
            assert contrast['sameNonPitchNoteEvents'] is False
        elif row['id'].startswith(('negative-fret-mute/guitar/', 'negative-fret-mute/bass/')):
            assert contrast['sameFinalClock'] is True
            assert contrast['sameNonPitchNoteEvents'] is True


@pytest.mark.parametrize('row', [r for r in ROWS if r['id'].startswith(
    ('negative-fret-mute/guitar/', 'negative-fret-mute/bass/', 'negative-fret-mute/version/',
     'negative-fret-mute/electric/'))], ids=lambda row: row['id'])
def test_literal_alias_is_unpitched_without_mutating_authored_fret(row):
    source = deepcopy(row['source'])
    parsed = parse(source)
    atom = parsed.tracks[0].bars[0][0]
    assert atom.fret == 127 and atom.authored_fret == -1 and atom.effects['mt'] is True
    assert parsed.source['negativeFretMutePolicy'] == FIXTURE['policy']
    track = render(parsed)['tracks'][0]
    assert track['notes'][0]['f'] == 127 and track['notes'][0]['mt'] is True
    assert 'notation' not in track
    written = expected(songsterr(source), {'offset': 0, 'scale': 1})['parts'][0]['notation_beats'][0]['notes'][0]
    assert written['fret'] == 127 and written['dead'] is True and 'midi' not in written
    assert source == row['source']


def test_worker_identity_profiles_and_fixture_scope_remain_pinned():
    assert FIXTURE['policy'] == 'songsterr-negative-fret-mute-v1'
    assert FIXTURE['referenceSha256'] == '4219f9dbf0952af83be36c8b590f10fea7a0f36d72bd1f92297a3d768f93f5ab'
    manifest = ROOT / 'tools/songsterr_compatibility/reference-manifest.json'
    assert hashlib.sha256(manifest.read_bytes()).hexdigest() == FIXTURE['manifestSha256']
    assert FIXTURE['qualification'] == {'cases': 49, 'guardCases': 21, 'comparisons': 204,
                                        'matched': 204, 'matchingReferenceErrors': 0}
    assert FIXTURE['profileOptions']['authored'] == {
        'synth': 'fluidsynth', 'useRSE': False, 'autoFixJson': False, 'humanize': False}
    assert FIXTURE['profileOptions']['legacy-brush-authored-v1'] == {
        'synth': 'fluidsynth', 'useRSE': False, 'autoFixJson': True, 'humanize': False}
    for row in ROWS:
        assert set(row['reference']) == set(PROFILES)
        assert row['reference']['legacy-brush-authored-v1']['normalization']['policy'] == 'songsterr-legacy-brush-direction-swap-v1'

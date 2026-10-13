"""Source-derived boundary matrix; native synthesis defaults do not grant support."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from feedback_converter.song_import.songsterr import parse
from feedback_converter.song_import.songsterr_harmonics import source_target
from feedback_converter.song_import.timeline import render
from feedback_converter.song_import.verify_source import songsterr
from feedback_converter.song_import.verify_timeline import expected


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = Path(__file__).parent / 'fixtures/songsterr_harmonic_fallback_reference.json'
FIXTURE = json.loads(FIXTURE_PATH.read_text(encoding='utf-8'))
ROWS = FIXTURE['cases']
BY_ID = {row['id']: row for row in ROWS}
PROFILES = ('authored', 'legacy-brush-authored-v1', 'player-defaults')


def source_for(note, flag=None):
    tuning = [64, 59, 55, 50, 45, 40]
    beat = {'duration': [1, 1], 'type': 1, 'notes': [deepcopy(note)]}
    if flag is not None:
        beat['artificialHarmonic'] = flag
    return {'format': 'songsterr', 'songId': 1, 'revisionId': 1,
            'title': 'Harmonic fallback oracle', 'artist': 'Synthetic',
            'tracks': [{'id': 0, 'name': 'Guitar', 'instrumentId': 30, 'tuning': tuning}],
            'parts': [{'version': 8, 'name': 'Guitar', 'instrumentId': 30,
                       'tuning': tuning, 'strings': 6, 'frets': 24, 'capo': 0,
                       'automations': {'tempo': [{'measure': 0, 'position': 0, 'bpm': 120, 'type': 4}]},
                       'measures': [{'signature': [4, 4], 'voices': [{'beats': [beat]}]}]}]}


def test_oracle_identity_and_coverage_are_bounded():
    assert FIXTURE['policy'] == 'songsterr-harmonic-fallback-audit-v1'
    manifest = json.loads((ROOT / 'tools/songsterr_compatibility/reference-manifest.json').read_text())
    assert FIXTURE['pinnedWorkerSha256'] == manifest['sha256']
    assert FIXTURE['currentWorkerSha256'] == 'a7f191f19196af972bd61ed5f9c67a6b63151fc8b0b8a1f665061a0cb493c980'
    assert FIXTURE['summary'] == {'cases': 73, 'currentPinnedMatches': 73, 'wholeScoreCases': 11,
                                  'comparisons': 66, 'matched': 66, 'matchingReferenceErrors': 0}
    assert len(BY_ID) == len(ROWS) == 73
    assert len([r for r in ROWS if r['disposition'] == 'canonical-contact']) == 21
    assert len([r for r in ROWS if r['disposition'] == 'playback-fallback']) == 8
    # Derived evidence is immutable until the explicit offline oracle is replayed.
    assert hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest() == '2721902adfc322a3a3d74fc01cbc47d479b47b7efcc557516cc96bd762e01536'


@pytest.mark.parametrize('row', ROWS, ids=lambda row: row['id'])
def test_reviewed_current_and_pinned_workers_agree_on_each_observed_branch(row):
    assert row['current'] == row['pinned']
    if row['wholeScore']:
        assert set(row['completeWorker']) == set(PROFILES)
        for parts in row['completeWorker'].values():
            assert len(parts) == 1 and parts[0]['status'] == 'executed'
            assert len(parts[0]['scheduledSha256']) == 64
            assert len(parts[0]['generatedFinalSha256']) == 64


@pytest.mark.parametrize('row', [r for r in ROWS if r['disposition'] in
                               ('canonical-contact', 'playback-fallback')], ids=lambda row: row['id'])
def test_supported_targets_match_native_pitch_without_inventing_a_touch(row):
    target = source_target(row['note'])
    native = row['current']
    assert native['applied'] is True and len(native['notes']) == 1
    assert target['interval'] == native['notes'][0]['interval']
    if row['disposition'] == 'playback-fallback':
        assert native['lookup'] == 0
        assert target == {'kind': 'artificial', 'node': None, 'interval': 24,
                          'policy': 'harmonic', 'basis': 'source-playback-fallback'}
    else:
        assert target['node'] == row['note']['harmonicFret']
        assert native['lookup'] == target['interval']
        assert 'basis' not in target
    source = source_for(row['note'])
    before = deepcopy(source)
    produced = render(parse(source))['tracks'][0]
    verified = expected(songsterr(source), {'offset': 0, 'scale': 1})['parts'][0]
    assert len(produced['notes']) == len(verified['notes']) == 1
    assert produced['notes'][0]['harmonic_target'] == verified['notes'][0]['note']['harmonic_target'] == target
    assert produced['notes'][0]['f'] == verified['notes'][0]['note']['f'] == 10
    written = produced['notation']['measures'][0]['staves']['staff']['voices'][0]['beats'][0]['notes'][0]
    assert written['midi'] == native['notes'][0]['pitch']
    assert source == before


@pytest.mark.parametrize('row', [r for r in ROWS if r['disposition'] == 'guarded'], ids=lambda row: row['id'])
def test_unqualified_source_defaults_never_become_a_fretted_target(row):
    # Do not accept missing contact positions, unknown harmonic kinds, near-node
    # tolerance matches, opaque legacy data or JS-coercible malformed values merely
    # because the synth produces a deterministic sound for them.
    assert source_target(row['note']) is None


@pytest.mark.parametrize('row', [r for r in ROWS if r['id'].startswith('artificial/near-node/')], ids=lambda row: row['id'])
def test_near_node_pitch_is_not_the_unknown_node_fallback(row):
    assert row['current']['lookup'] in (12, 19, 34)
    assert row['current']['notes'][0]['interval'] != 24
    assert source_target(row['note']) is None


@pytest.mark.parametrize('row', [r for r in ROWS if r['wholeScore'] and r['disposition'] in
                               ('canonical-contact', 'playback-fallback')], ids=lambda row: row['id'])
def test_redundant_summary_keeps_the_full_worker_target_and_imported_music(row):
    plain = source_for(row['note'])
    flagged = source_for(row['note'], True)
    assert render(parse(plain))['tracks'] == render(parse(flagged))['tracks']
    for profile in ('authored', 'legacy-brush-authored-v1'):
        target = row['completeWorker'][profile][0]['targets'][0]
        assert target['pitch'] == row['current']['notes'][0]['pitch']
        assert target['fret'] == row['note']['fret']
        assert target['hidden'] is False and target['tie'] is False
        assert target['endTick'] > target['attackTick']


def test_a_missing_semi_node_is_polyphonic_and_cannot_use_the_artificial_fallback():
    omitted = BY_ID['semi/missing']['current']
    explicit_null = BY_ID['semi/null']['current']
    assert [n['interval'] for n in omitted['notes']] == [0, 12]
    assert [n['interval'] for n in explicit_null['notes']] == [24]
    assert source_target(BY_ID['semi/missing']['note']) is None


def test_opaque_legacy_data_can_override_or_break_the_apparently_safe_fallback():
    assert BY_ID['legacy-data/ah-pitch']['current']['notes'][0]['interval'] == 15
    assert BY_ID['legacy-data/ah-shift']['current']['notes'][0]['interval'] == 12
    assert BY_ID['legacy-data/ah-empty']['current']['notes'][0]['pitch'] == 'NaN'
    assert BY_ID['legacy-data/tapped']['current']['notes'][0]['interval'] == 16


def test_catalog_exposes_new_fallback_evidence_separately_from_historical_oracles():
    from tools.songsterr_compatibility.catalog import inventory
    catalog = inventory()
    harmonic = catalog['rules']['technique.harmonics']
    legacy = catalog['rules']['source.legacy_beat_effects']
    assert harmonic['reference']['harmonicFallbackPolicy'] == FIXTURE['policy']
    assert harmonic['testFixture'] == FIXTURE_PATH.relative_to(ROOT).as_posix()
    assert harmonic['testFixture'] in legacy['additionalFixtures']
    assert legacy['testFixture'] == 'tests/fixtures/songsterr_legacy_beat_effects_native.json'
    assert 'beat.artificialHarmonic' in legacy['sourceFields']

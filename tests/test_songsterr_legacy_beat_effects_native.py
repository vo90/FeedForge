"""Qualified retained beat summaries, distinct from active note harmonics."""
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
from tools.songsterr_compatibility.audit import canonical_hash, evaluate, compare_preparation, compare_authored_events
from tools.songsterr_compatibility.catalog import inventory

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = Path(__file__).parent / 'fixtures/songsterr_legacy_beat_effects_native.json'
FIXTURE = json.loads(FIXTURE_PATH.read_text())
ROWS = FIXTURE['cases']
PROFILES = ('authored', 'legacy-brush-authored-v1', 'player-defaults')


def without_flags(source):
    result = deepcopy(source)
    for part in result['parts']:
        for measure in part['measures']:
            for voice in measure['voices']:
                for beat in voice.get('beats', []):
                    beat.pop('harmonic', None)
                    beat.pop('fadeIn', None)
    return result


def independent_music(source):
    result = expected(songsterr(source), {'offset': 0, 'scale': 1})
    return [{key: value for key, value in part.items() if key != 'source'} for part in result['parts']]


def produced_pitches(track):
    result = {}
    for occurrence, measure in enumerate(track['notation']['measures']):
        for voice in measure['staves']['staff']['voices']:
            for beat in voice['beats']:
                for note in beat.get('notes', []):
                    coordinates = tuple(map(int, note['source_id'].split(':')[2:5]))
                    key = (occurrence, *coordinates, note['str'])
                    assert key not in result
                    result[key] = note['midi']
    return result


def independent_pitches(part):
    result = {}
    for beat in part['notation_beats']:
        path = beat['location'].split('/')
        for note in beat['notes']:
            key = (beat['measure'] - 1, int(path[3]), int(path[5]), int(path[7]), note['str'])
            assert key not in result
            result[key] = note['midi']
    return result


@pytest.mark.parametrize('row', ROWS, ids=lambda row: row['id'])
def test_retained_beat_effects_use_the_qualified_native_clock_and_note_effects(row):
    source = deepcopy(row['source'])
    before = deepcopy(source)
    assert row['sourceSha256'] == canonical_hash(source)
    actual = evaluate(source)
    # Keep the contract91 guard-only fixture as captured. Contract93 now
    # qualifies this ordinary held bend; its dedicated new fixture covers
    # current curves and notation independently of historical naturalTargets.
    newly_qualified_bend = row['id'] == 'legacy-beat-effects/guard/fade/bent-zero-tie'
    disposition = 'rendered' if newly_qualified_bend else row['expectedDisposition']
    if newly_qualified_bend:
        assert row['guardOnly'] is True and row['expectedDisposition'] == 'blocked'
    assert actual['converter']['status'] == actual['independent']['status'] == disposition, actual
    for profile in PROFILES:
        native = row['reference'][profile]
        assert all(part['status'] == 'executed' and part['profile'] == profile for part in native['parts'])
        assert row['metadataRemoval'][profile]['matched'] is True
        for part in native['parts']:
            assert part['scheduledSha256'] == canonical_hash(part['scheduled'])
            assert part['generatedFinalSha256'] == canonical_hash(part['generatedFinal'])
        if disposition == 'rendered':
            assert compare_preparation(actual, native) == []
            # Player synthesis/humanization is separately observed, without
            # interpreting its final attacks as authored gameplay timing.
            if profile != 'player-defaults' and not newly_qualified_bend:
                assert compare_authored_events(actual, native, source) == []
    if disposition == 'rendered':
        absent = without_flags(source)
        produced = render(parse(source))['tracks']
        checked = independent_music(source)
        assert produced == render(parse(absent))['tracks']
        assert checked == independent_music(absent)
        if newly_qualified_bend:
            assert render(parse(source))['plainTieIdentityEvidence'][0]['rule'] == 'plain-tie-keeps-bent-attack-target'
        else:
            strings = len(source['parts'][0]['tuning'])
            wanted = {(note['occurrence'], note['bar'], note['voiceIndex'], note['beatIndex'], strings - 1 - note['string']):
                      note['pitch'] for note in row['reference']['authored']['parts'][0]['naturalTargets']}
            # Check every written target, including tied notes, rest slots, multiple
            # voices and repeated occurrences. Final synth envelopes remain scoped.
            assert produced_pitches(produced[0]) == independent_pitches(checked[0]) == wanted
    else:
        assert row['guardOnly'] is True
    assert source == before


@pytest.mark.parametrize('row', [row for row in ROWS if row.get('noteInstructionControl')], ids=lambda row: row['id'])
def test_natural_note_pitches_and_markings_survive_summary_retention(row):
    source = row['source']
    part = source['parts'][0]
    raw = part['measures'][0]['voices'][0]['beats'][0]['notes'][0]
    fret = raw['fret']
    interval = {4: 28, 5: 24, 7: 19, 9: 28, 12: 12, 16: 28, 19: 19}[fret]
    wanted = part['tuning'][raw['string']] + part['capo'] + interval
    plain = part['tuning'][raw['string']] + part['capo'] + fret
    for profile in PROFILES:
        native = row['reference'][profile]['parts'][0]['naturalTargets'][0]
        control = row['positiveControl'][profile]['naturalTargets'][0]
        assert native['pitch'] == wanted
        assert control['pitch'] == plain
        assert native['harmonic'] == 'natural'
        if profile != 'player-defaults':
            assert native['disallowOpenString'] is (fret != 19)
        # The twelfth/nineteenth-fret harmonics have the same numeric pitch
        # as plain notes; these are not useful pitch-only positive controls.
        if wanted != plain:
            assert row['positiveControl'][profile]['differentScheduledEvents'] is True
    produced = render(parse(source))['tracks'][0]
    note = produced['notes'][0]
    written = produced['notation']['measures'][0]['staves']['staff']['voices'][0]['beats'][0]['notes'][0]
    checked = expected(songsterr(source), {'offset': 0, 'scale': 1})['parts'][0]
    assert note['hm'] is True and note['f'] == written['fret'] == fret
    assert written['midi'] == checked['notation_beats'][0]['notes'][0]['midi'] == wanted


def test_fret_nineteen_same_pitch_does_not_claim_an_applied_native_harmonic():
    rows = [row for row in ROWS if row.get('noteInstructionControl')
            and row['source']['parts'][0]['measures'][0]['voices'][0]['beats'][0]['notes'][0]['fret'] == 19]
    assert len(rows) == 6
    for row in rows:
        assert row['nativeEffectScope'] == 'Same pitch; native harmonic lookup does not apply.'
        for profile in PROFILES:
            target = row['reference'][profile]['parts'][0]['naturalTargets'][0]
            assert target['disallowOpenString'] is False
            assert row['positiveControl'][profile]['differentScheduledEvents'] is False


def test_worker_identity_profiles_and_guard_scope_are_explicit():
    assert FIXTURE['policy'] == 'songsterr-legacy-beat-effects-v1'
    assert FIXTURE['referenceSha256'] == '4219f9dbf0952af83be36c8b590f10fea7a0f36d72bd1f92297a3d768f93f5ab'
    manifest = ROOT / 'tools/songsterr_compatibility/reference-manifest.json'
    assert FIXTURE['manifestSha256'] == hashlib.sha256(manifest.read_bytes()).hexdigest()
    assert FIXTURE['qualification'] == {'comparisons': 708, 'matched': 708,
                                        'matchingReferenceErrors': 0, 'metadataEquivalences': 291}
    assert FIXTURE['profileOptions']['authored'] == {
        'synth': 'fluidsynth', 'useRSE': False, 'autoFixJson': False, 'humanize': False}
    assert FIXTURE['profileOptions']['legacy-brush-authored-v1'] == {
        'synth': 'fluidsynth', 'useRSE': False, 'autoFixJson': True, 'humanize': False}
    assert len(ROWS) == 97 and sum(row.get('guardOnly', False) for row in ROWS) == 29
    assert {binding['name'] for binding in FIXTURE['bindings']} == {
        'Qo', 'Harmonics', 'HarmonicsForElectricity', 'Ui', 'fc', 'ks', 'ro', 'no'}
    for row in ROWS:
        assert set(row['reference']) == set(PROFILES)
        assert row['reference']['legacy-brush-authored-v1']['parts'][0]['normalization']['policy'] == 'songsterr-legacy-brush-direction-swap-v1'


def test_catalog_maps_only_the_scoped_legacy_beat_fields():
    catalog = inventory()
    fields = {row['field']: row for row in catalog['fields']}
    assert fields['beat.harmonic']['rules'] == fields['beat.fadeIn']['rules'] == ['source.legacy_beat_effects']
    assert fields['note.harmonic']['rules'] == ['technique.harmonics']
    rule = catalog['rules']['source.legacy_beat_effects']
    assert rule['sourceFields'] == ['beat.harmonic', 'beat.fadeIn']
    assert rule['reference']['legacyBeatEffectsPolicy'] == FIXTURE['policy']
    assert rule['testFixture'] == FIXTURE_PATH.relative_to(ROOT).as_posix()
    assert all(row['candidateRules'] == ['source.legacy_beat_effects'] for row in catalog['guards']
               if row['file'] == 'songsterr_legacy_effects.py')


def test_a_beat_summary_does_not_fan_out_a_missing_note_instruction():
    rows = {row['id']: row for row in ROWS}
    for suffix in ('unmarked', 'mixed'):
        row = rows['legacy-beat-effects/guard/harmonic/' + suffix]
        native = row['reference']['authored']['parts'][0]['naturalTargets']
        raw = row['source']['parts'][0]['measures'][0]['voices'][0]['beats'][0]['notes']
        for result, written in zip(native, raw):
            if 'harmonic' not in written:
                assert result['pitch'] == row['source']['parts'][0]['tuning'][written['string']] + written['fret']
                assert 'harmonic' not in result
        assert row['guardOnly'] is True


def test_historical_bent_zero_tie_guard_is_now_qualified_as_plain_held_bend():
    row = next(row for row in ROWS if row['id'] == 'legacy-beat-effects/guard/fade/bent-zero-tie')
    assert row['guardOnly'] is True and row['expectedDisposition'] == 'blocked'
    source = deepcopy(row['source']); before = deepcopy(source)
    produced = render(parse(source))
    checked = expected(songsterr(source), {'offset':0,'scale':1})
    note, = produced['tracks'][0]['notes']
    independent_note, = [entry['note'] for entry in checked['parts'][0]['notes']]
    for profile in PROFILES:
        part = row['reference'][profile]['parts'][0]
        assert part['heldEvents'][0]['fret'] == 5
        assert part['heldEvents'][1]['fret'] == 5 and part['heldEvents'][1]['sourceFret'] == 0
        assert part['heldEvents'][1]['hidden'] is True
        assert part['heldEvents'][0]['endTick'] == part['authoredEvents'][1]['endTick']
        held, = [event for event in part['heldEvents'] if not event['hidden']]
        attack = float(F(held['attackTick'],part['tpqn'])*F(5,6))
        end = float(F(held['endTick']+1,part['tpqn'])*F(5,6))
        for candidate in (note,independent_note):
            assert (candidate['s'],candidate['f']) == (len(source['parts'][0]['tuning'])-1-held['string'],held['fret'])
            assert round(candidate['t'],6) == round(attack,6)
            assert round(candidate['t']+candidate['sus'],6) == round(end,6)
    actual = evaluate(source)
    assert actual['converter']['status'] == actual['independent']['status'] == 'rendered'
    for rows in (produced['plainTieIdentityEvidence'],checked['plain_tie_identities']):
        identity, = rows
        assert identity['rule'] == 'plain-tie-keeps-bent-attack-target'
        assert identity['authored'] == {'fret':0} and identity['used'] == {'fret':5}
    assert produced['fingerBendTimingEvidence'][0]['status'] == 'resolved'
    assert len(note['source_ids']) == 2 and source == before

"""A directional-tail exception requires source identity and real audio evidence."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import zipfile

import numpy as np
import pytest
import soundfile as sf
import yaml

from test_songsterr_recording_end import recording, META
from feedback_converter.song_import.audio import ImportFailure
from feedback_converter.song_import.builder import build_feedpak
from feedback_converter.song_import.ending_cutoff import authorize
from feedback_converter.song_import.recording_sync import digest
from feedback_converter.song_import.score import load_performance
from feedback_converter.song_import.synchronization import align_from_songsterr
from feedback_converter.song_import.terminal_sustains import trim_held_note, slides_allowed
from feedback_converter.song_import.verification import verify_import


def tail(direction='down'):
    return {'t': 10., 'sus': 2., 'f': 19, 's': 1, 'vb': True, 'slide_out': direction,
            'slide_out_marks': [{'direction': direction, 'start': .75, 'end': 2.}]}


@pytest.mark.parametrize('direction', ['up', 'down'])
def test_only_end_changes_and_input_is_immutable(direction):
    note = tail(direction)
    before = deepcopy(note)
    with pytest.raises(ImportFailure):
        trim_held_note(note, 11.5)
    changed, detail = trim_held_note(note, 11.5, allow_directional_slides=True)
    assert note == before
    assert changed == {**note, 'sus': 1.5, 'slide_out_marks': [{'direction': direction, 'start': .75, 'end': 1.5}]}
    assert detail['slideOuts'] == [{'index': 0, 'direction': direction, 'start': .75, 'originalEnd': 2., 'exportedEnd': 1.5}]


@pytest.mark.parametrize('effect', [
    {'sl': 12}, {'slu': 0}, {'bn': 2},
    {'bn': 2, 'bnv': [{'t': 0, 'v': 0}, {'t': 2, 'v': 2}]},
    {'slide_in_marks': [{'direction': 'up', 'time': 1.75}]},
    {'harmonic_changes': {'events': [{'start': 1.5, 'end': 2}]}},
    {'slide_out_marks': [{'direction': 'down', 'start': 1.5, 'end': 2}]},
    {'slide_out_marks': [{'direction': 'down', 'start': 1.6, 'end': 2}]},
    {'slide_out_marks': [{'direction': 'sideways', 'start': .5, 'end': 2}]},
    {'slide_out_marks': [{'direction': 'down', 'start': True, 'end': 2}]},
    {'slide_out_marks': [{'direction': 'down', 'start': .5, 'end': float('nan')}]},
    {'slide_out_marks': [{'direction': 'down', 'start': .5, 'end': 1}, {'direction': 'up', 'start': .8, 'end': 2}]},
    {'slide_out_marks': []},
    {'whammy': {'version': 1, 'segments': [{'start': 0, 'end': 2, 'source_id': 's', 'group': 'g',
                                          'curve': [{'t': 0, 'v': 0}, {'t': 2, 'v': -2}]}]}},
])
def test_does_not_erase_or_accelerate_other_gestures(effect):
    with pytest.raises(ImportFailure):
        trim_held_note({**tail(), **effect}, 11.5, allow_directional_slides=True)


def test_earlier_segments_and_completed_bend_are_preserved():
    note = tail()
    note['slide_out_marks'].insert(0, {'direction': 'up', 'start': .1, 'end': .5})
    note.update(bn=1, bnv=[{'t': 0, 'v': 0}, {'t': .5, 'v': 1}, {'t': 2, 'v': 1}])
    changed, detail = trim_held_note(note, 11.5000009, allow_directional_slides=True)
    assert changed['t'] + changed['sus'] <= 11.5000009
    assert changed['slide_out_marks'][0] == note['slide_out_marks'][0]
    assert detail['slideOuts'][0]['index'] == 1
    assert changed['bnv'] == [{'t': 0, 'v': 0}, {'t': .5, 'v': 1}, {'t': 1.5, 'v': 1}]


def source_case(recording, root, *, late=False, direction='down', chord=False):
    _, original, _, audio, timing, _ = recording
    raw = json.loads(original.read_text())
    if late:
        raw['tracks'].append({**deepcopy(raw['tracks'][0]), 'id': 1, 'name': 'Second guitar'})
        raw['parts'].append(deepcopy(raw['parts'][0]))
    beats = raw['parts'][0]['measures'][-1]['voices'][0]['beats']
    notes = [{'string': 5, 'fret': 7, 'slide': direction + 'wards', 'leftHandVibrato': 'slight'}]
    if chord:
        notes.append({'string': 4, 'fret': 5, 'slide': direction + 'wards'})
        # Both chord strings are tied into the final marked segment.
        beats[3]['notes'] = [{k: v for k, v in n.items() if k != 'slide'} for n in notes]
        for n in notes:
            n['tie'] = True
    # A single half-note spans 51–52; audio ends at 51.5. No late attack in
    # this part: its directional tail must trigger the acoustic check itself.
    beats[4:] = [{'duration': [1, 2], 'notes': notes}]
    source = root / 'score.json'
    source.write_text(json.dumps(raw), encoding='utf8')
    performance = load_performance(source, META)
    candidate = align_from_songsterr(performance, audio, timing, META, allow_ending_candidate=True)
    return source, performance, audio, timing, candidate


@pytest.mark.parametrize('late,direction,chord', [(False, 'up', False), (True, 'down', False), (False, 'down', True)])
def test_candidate_and_independent_package_with_or_without_late_attacks(recording, tmp_path, late, direction, chord):
    source, performance, audio, _, candidate = source_case(recording, tmp_path, late=late, direction=direction, chord=chord)
    assert candidate['status'] == 'needs_ending_check'
    assert candidate['endingCandidate']['lateNotes'] == (2 if late else 0)
    assert candidate['endingCandidate']['directionalSlides'] == (2 if chord else 1)
    assert not slides_allowed(candidate, audio['duration'])
    with pytest.raises(ImportFailure, match='synchronization'):
        build_feedpak(performance, audio, candidate, tmp_path / 'unauthorized', output_dir=tmp_path, source_path=source)
    alignment = authorize(performance, audio, candidate)
    assert slides_allowed(alignment, audio['duration'])
    built = build_package(source, performance, audio, alignment, tmp_path)
    report = verify_import(source, built, alignment, META)
    assert report['status'] == 'passed', report
    assert report['adjustments']['terminalSlideOuts'] == (2 if chord else 1)
    assert report['adjustments']['omittedEndingNotes'] == (2 if late else 0)
    assert report['timing']['independentAudioMatchAssessed']
    with zipfile.ZipFile(built) as z:
        manifest = yaml.safe_load(z.read('manifest.yaml'))
        assert z.read(manifest['song_import']['sourceFile']) == source.read_bytes()
        ledger = json.loads(z.read(manifest['song_import']['adjustmentsFile']))
        assert ledger['directionalSlides'] == alignment['terminalSlides']
        assert sum(len(n.get('slideOuts', [])) for n in ledger['notes']) == (2 if chord else 1)


def build_package(source, performance, audio, alignment, root):
    job = root / 'job'
    job.mkdir()
    built = build_feedpak(performance, audio, alignment, job, output_dir=root / 'out', source_path=source,
        compatibility=performance['compatibilityReport'], recipe={
            'preservationContract': 33, 'audioSource': audio['source'],
            'sourceMetadata': dict(performance.get('source') or {}),
            'alignment': {'method': alignment['method'], 'provenance': alignment['provenance']}})
    return Path(built['stagingPath'])


@pytest.fixture(scope='module')
def completed_slide(recording, tmp_path_factory):
    root = tmp_path_factory.mktemp('terminal-slide')
    source, performance, audio, _, candidate = source_case(recording, root)
    alignment = authorize(performance, audio, candidate)
    return source, performance, audio, alignment, build_package(source, performance, audio, alignment, root)


@pytest.mark.parametrize('change', ['fret', 'attack', 'start', 'direction', 'end', 'ledger', 'missing_policy',
                                  'missing_ledger', 'missing_sync', 'old_contract', 'forged_pass'])
def test_tampered_package_cannot_pass(completed_slide, tmp_path, change):
    source, _, audio, original_alignment, built = completed_slide
    alignment = deepcopy(original_alignment)
    with zipfile.ZipFile(built) as z:
        files = {n: z.read(n) for n in z.namelist()}
    manifest = yaml.safe_load(files['manifest.yaml'])
    recipe = manifest['song_import']
    chart_path = manifest['arrangements'][0]['file']
    chart = json.loads(files[chart_path])
    note = chart['notes'][-1]
    ledger_path = recipe['adjustmentsFile']
    ledger = json.loads(files[ledger_path])
    if change == 'fret': note['f'] += 1
    elif change == 'attack': note['t'] -= .1
    elif change in {'start', 'end'}: note['slide_out_marks'][0][change] -= .1
    elif change == 'direction': note['slide_out_marks'][0]['direction'] = 'up'
    elif change == 'ledger': ledger['notes'][-1]['slideOuts'][0]['originalEnd'] += .1
    elif change == 'missing_policy': recipe.pop('terminalSlides')
    elif change == 'missing_ledger': ledger['notes'][-1].pop('slideOuts')
    elif change == 'missing_sync': recipe.pop('recordingSyncFile')
    elif change == 'old_contract': recipe['preservationContract'] = 32
    elif change == 'forged_pass':
        path = tmp_path / 'silent.ogg'
        with sf.SoundFile(path, 'w', samplerate=22050, channels=1, format='OGG', subtype='VORBIS') as writer:
            remaining = round(audio['duration'] * 22050)
            while remaining:
                size = min(32768, remaining)
                writer.write(np.zeros(size, dtype='float32'))
                remaining -= size
        files['audio/full.ogg'] = path.read_bytes()
        stored = alignment['recordingSync']
        stored['audioSha256'] = hashlib.sha256(files['audio/full.ogg']).hexdigest()
        alignment['recordingEnd']['syncEvidenceHash'] = digest(stored)
        recipe['recordingEnd'] = deepcopy(alignment['recordingEnd'])
        files[recipe['recordingSyncFile']] = json.dumps(stored).encode()
        ending = json.loads(files[recipe['endingOmissionsFile']])
        ending.update(alignment['recordingEnd'])
        files[recipe['endingOmissionsFile']] = json.dumps(ending).encode()
    files[chart_path] = json.dumps(chart).encode()
    files[ledger_path] = json.dumps(ledger).encode()
    files['manifest.yaml'] = yaml.safe_dump(manifest).encode()
    target = tmp_path / 'tampered.feedpak'
    with zipfile.ZipFile(target, 'w') as z:
        for name, data in files.items(): z.writestr(name, data)
    result = verify_import(source, target, alignment, META)
    assert result['status'] == 'failed', result
    if change == 'forged_pass':
        assert any(e['code'] == 'ending_audio_sync' for e in result['errors']), result


def test_no_late_attack_cutoff_still_requires_acoustic_check_and_never_falls_back(recording, tmp_path, monkeypatch):
    from feedback_converter.song_import import worker, ending_cutoff
    _, performance, audio, timing, _ = source_case(recording, tmp_path)
    def refuse(*args):
        raise ImportFailure('alignment_failed', 'Timing remains inconclusive.')
    monkeypatch.setattr(ending_cutoff, 'authorize', refuse)
    monkeypatch.setattr(worker, 'align_audio', lambda *a, **kw: pytest.fail('must not bypass failed acoustic check'))
    with pytest.raises(ImportFailure, match='inconclusive'):
        worker._choose_alignment(performance, audio, {'synchronization': timing, 'metadata': META}, allow_padding=False)


@pytest.mark.parametrize('missing', ['recordingEnd', 'recordingSync', 'terminalSlides'])
def test_saved_flag_without_authority_cannot_enable_trimming(completed_slide, missing):
    from feedback_converter.song_import.builder import _retime_note
    _, performance, audio, original, _ = completed_slide
    alignment = deepcopy(original)
    alignment.pop(missing)
    assert not slides_allowed(alignment, audio['duration'])
    with pytest.raises(ImportFailure):
        _retime_note(performance['tracks'][0]['notes'][-1], alignment, audio['duration'])


def test_old_contract_cannot_build_directional_cutoff(completed_slide, tmp_path):
    source, performance, audio, alignment, _ = completed_slide
    with pytest.raises(ImportFailure, match='contract 33'):
        build_feedpak(performance, audio, alignment, tmp_path, output_dir=tmp_path, source_path=source,
                      recipe={'preservationContract': 32})


@pytest.mark.parametrize('change', ['early_end', 'wrong_video', 'wrong_revision', 'long_final_bar', 'large_overrun'])
def test_candidate_must_stay_inside_existing_identity_and_final_bar_guards(recording, tmp_path, change):
    _, performance, original_audio, original_timing, _ = source_case(recording, tmp_path)
    audio, timing = deepcopy(original_audio), deepcopy(original_timing)
    if change == 'early_end': audio['duration'] = 49.9
    elif change == 'wrong_video': audio['source']['videoId'] = 'zyxwvutsrqp'
    elif change == 'wrong_revision': timing['revisionId'] = '35'
    elif change == 'long_final_bar': timing['points'][-1] = 60
    elif change == 'large_overrun': timing['points'][-1] = 56
    with pytest.raises(ImportFailure):
        align_from_songsterr(performance, audio, timing, META, allow_ending_candidate=True)

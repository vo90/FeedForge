"""Complete optional phrases survive an already authorized held-tail cutoff."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

import numpy as np
import pytest
import soundfile as sf
import yaml

from test_song_import_score import beat, measure, raw_score
from test_songsterr_hybrid_lead import rest, prepared
from feedback_converter.song_import.audio import prepare_audio
from feedback_converter.song_import.builder import build_feedpak
from feedback_converter.song_import.synchronization import align_from_songsterr
from feedback_converter.song_import.verification import verify_import


def fixture(tmp_path, *, chord=False, duration=3.5, conflict=False, excluded=False, incompatible=False,
            nonlinear=False, preparation=False):
    doc = raw_score([measure(beat(3, duration=(1, 4)), rest((3, 4))), measure(rest())])
    final = beat(5, vibrato=True)
    if chord:
        final['notes'].append({'string': 1, 'fret': 7})
    donor = {**deepcopy(doc['tracks'][0]), 'id': 1, 'name': 'Acoustic Guitar'}
    if incompatible:
        donor['tuning'] = [x - 2 for x in donor['tuning']]
    doc['tracks'].append(donor)
    doc['parts'].append({'measures': [measure(rest()), measure(final)]})
    if conflict:
        doc['parts'][0]['measures'][1] = measure(beat(9))
    source, performance, options = prepared(tmp_path, doc)
    options['mainTrackId'] = '0'
    if excluded:
        options['excludedTrackIds'] = ['1']
    wav = tmp_path / 'recording.wav'
    sf.write(wav, .1 * np.sin(np.arange(round(22050 * duration)) * .15), 22050)
    media = tmp_path / 'media'; media.mkdir()
    audio = prepare_audio({'kind': 'file', 'path': str(wav)}, media)
    video = 'abcdefghijk'
    audio['source'].update(kind='youtube', videoId=video)
    meta = {'songId': '123', 'revisionId': '456', 'approval': 'approved'}
    sync = {'version': 1, 'source': 'songsterr-video-points', **meta, 'videoId': video,
            'status': 'done', 'feature': None, 'points': [0, 2.25, 4] if nonlinear else [0, 2, 4]}
    alignment = align_from_songsterr(performance, audio, sync, meta)
    if preparation:
        from feedback_converter.song_import.preparation import finalize
        audio, alignment = finalize(performance, audio, alignment, media)
    job = tmp_path / 'job'; job.mkdir()
    built = build_feedpak(performance, audio, alignment, job, output_dir=tmp_path / 'out',
        source_path=source, compatibility=performance['compatibilityReport'],
        recipe={'preservationContract': 58, 'hybridLead': options, 'scoreHash': options['sourceSha256'],
                'audioHash': audio['hash'], 'audioSource': audio['source'], 'alignment': alignment,
                **({'preparation': alignment['preparation']} if preparation else {})},
        hybrid_lead={'enabled': True, 'mainTrackId': '0', 'options': options})
    archive = Path(built['stagingPath'])
    report = verify_import(source, archive, alignment, hybrid_options=options)
    with ZipFile(archive) as z:
        manifest = yaml.safe_load(z.read('manifest.yaml'))
        charts = {a['id']: json.loads(z.read(a['file'])) for a in manifest['arrangements']}
        receipt = json.loads(z.read('import/hybrid-lead.json'))
    return source, alignment, archive, report, charts, receipt


def test_nonuniform_recording_map_and_preparation_keep_the_same_accepted_end(tmp_path):
    _, alignment, _, report, charts, receipt = fixture(tmp_path, chord=True, nonlinear=True, preparation=True)
    assert report['status'] == 'passed', report
    p = next(p for p in receipt['passages'] if p['trackId'] == '1')
    assert alignment['preparation']['seconds'] == 2
    assert p['recordingStart'] == 4.25
    assert p['recordingEnd'] == pytest.approx(5.5, abs=1.1e-6, rel=0)
    assert p['acceptedEnding']['sourceRecordingEnd'] == 6
    assert charts['1']['chords'][0]['notes'][0]['sus'] == 1.25


@pytest.mark.parametrize('missing', ['member', 'event', 'late_attack', 'authority', 'sustain'])
def test_only_complete_retained_attacks_and_actual_accepted_tails_allow_projection(tmp_path, missing):
    source, alignment, _, _, charts, _ = fixture(tmp_path, chord=True)
    from feedback_converter.song_import.score import load_performance
    from feedback_converter.song_import.hybrid_context import Clock
    from feedback_converter.song_import.hybrid_lead import event_rows, passages
    from feedback_converter.song_import.hybrid_endings import project
    p = load_performance(source, composition_context=True)
    track = p['tracks'][1]
    context = p['compositionContext']['tracks']['1']
    clock = Clock(p['compositionContext']['timeline'])
    phrase = passages(track, context, p['compositionContext']['timeline'], clock)[0]
    original = {'chart': charts['1'], 'eventIndices': {('chords', 0): 0}}
    if missing == 'member':
        original['chart']['chords'][0]['notes'].pop()
    elif missing == 'event':
        original['eventIndices'].clear()
    elif missing == 'late_attack':
        track['chords'][0]['notes'][0].update(t=3.6, sus=.4)
        original['chart']['chords'][0]['notes'][0].update(t=3.6, sus=0)
    elif missing == 'authority':
        alignment.pop('terminalSustains')
    elif missing == 'sustain':
        for n in track['chords'][0]['notes']:
            n['sus'] = 1.5  # Only the written slot extends, not a trimmed sustain.
    donor = {'track': track, 'context': context, 'rows': event_rows(track, context, clock)}
    before = deepcopy((phrase, donor, original, alignment))
    result = project(phrase, donor, original, clock, alignment, 3.5)
    assert 'acceptedEnding' not in result
    assert (phrase, donor, original, alignment) == before


@pytest.mark.parametrize('chord', [False, True])
@pytest.mark.parametrize('duration', [3.5, 4, 4.5])
def test_tail_only_ending_copies_all_accepted_events_and_keeps_notation(tmp_path, chord, duration):
    _, _, archive, report, charts, receipt = fixture(tmp_path, chord=chord, duration=duration)
    assert report['status'] == 'passed', report
    selected = [p for p in receipt['passages'] if p['trackId'] == '1']
    assert len(selected) == 1
    assert ('acceptedEnding' in selected[0]) == (duration < 4)
    hybrid = charts[receipt['arrangementId']]
    for kind in ('notes', 'chords'):
        for event in charts['1'][kind]:
            # Chord template indices are local to each arrangement.
            assert any({k:v for k,v in event.items() if k != 'id'} ==
                       {k:v for k,v in copied.items() if k != 'id'} for copied in hybrid[kind])
    with ZipFile(archive) as z:
        manifest = yaml.safe_load(z.read('manifest.yaml'))
        arr = next(a for a in manifest['arrangements'] if a.get('derived'))
        assert arr.get('notation')
        notation = json.loads(z.read(arr['notation']))
        beats = [b for m in notation['measures'] for s in m['staves'].values()
                 for v in s['voices'] for b in v['beats'] if b.get('notes')]
        # The final written whole note is retained; its chart sustain ends sooner.
        assert any(b['t'] == 2 and b['duration_seconds'] == 2 for b in beats)


@pytest.mark.parametrize('blocked', ['conflict', 'excluded', 'incompatible'])
def test_endpoint_acceptance_does_not_relax_other_selection_guards(tmp_path, blocked):
    *_, report, charts, receipt = fixture(tmp_path, **{blocked: True})
    assert report['status'] == 'passed', report
    assert not any(p['trackId'] == '1' for p in receipt['passages'])


@pytest.mark.parametrize('tamper', ['duration', 'source_end', 'authority', 'contract', 'missing_attack'])
def test_independent_verifier_rejects_false_ending_proof(tmp_path, tamper):
    source, alignment, archive, report, _, receipt = fixture(tmp_path, chord=True)
    assert report['status'] == 'passed', report
    with ZipFile(archive) as z:
        entries = {n: z.read(n) for n in z.namelist()}
    manifest = yaml.safe_load(entries['manifest.yaml'])
    p = next(p for p in receipt['passages'] if p['trackId'] == '1')
    if tamper == 'duration':
        p['recordingEnd'] -= .2
        p['end'] -= .4
    elif tamper == 'source_end':
        p['acceptedEnding'].update(sourceEnd=9, sourceRecordingEnd=4.5)
    elif tamper == 'authority':
        manifest['song_import'].pop('terminalSustains')
    elif tamper == 'contract':
        manifest['song_import']['preservationContract'] = 57
    else:
        donor = next(a for a in manifest['arrangements'] if a['id'] == '1')
        chart = json.loads(entries[donor['file']])
        chart['chords'][0]['notes'].pop()
        entries[donor['file']] = json.dumps(chart).encode()
        next(s for s in receipt['sources'] if s['trackId'] == '1')['chartSha256'] = hashlib.sha256(entries[donor['file']]).hexdigest()
    entries['import/hybrid-lead.json'] = json.dumps(receipt).encode()
    entries['manifest.yaml'] = yaml.safe_dump(manifest).encode()
    changed = tmp_path / 'tampered.feedpak'
    with ZipFile(changed, 'w', ZIP_DEFLATED) as z:
        for name, data in entries.items():
            z.writestr(name, data)
    bad = verify_import(source, changed, alignment)
    assert bad['status'] == 'failed', bad

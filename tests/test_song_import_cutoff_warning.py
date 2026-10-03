"""Imperfect source timing can import; conversion errors cannot hide behind it."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import zipfile

import numpy as np
import pytest
import soundfile as sf
import yaml

from feedback_converter.song_import import cutoff_warning, ending_cutoff
from feedback_converter.song_import.audio import ImportFailure, prepare_audio
from feedback_converter.song_import.builder import build_feedpak
from feedback_converter.song_import.preparation import finalize
from feedback_converter.song_import.score import load_performance
from feedback_converter.song_import.synchronization import align_from_songsterr
from feedback_converter.song_import.verification import verify_import
from test_songsterr_recording_end import META, VIDEO


@pytest.fixture(scope='module')
def warned(tmp_path_factory):
    root = tmp_path_factory.mktemp('cutoff-warning')
    rng = np.random.default_rng(849)
    frets = rng.integers(0, 13, 424).tolist()
    measures = [{'signature': [4, 4], 'voices': [{'beats': [
        {'duration': [1, 8], 'notes': [{'string': 5, 'fret': f if i+j < 320 else f+5}]}
        for j, f in enumerate(frets[i:i+8])]}]} for i in range(0, len(frets), 8)]
    raw = {'format': 'songsterr', 'songId': 12, 'revisionId': 34, 'title': 'Imperfect tab', 'artist': 'Test',
        'tracks': [{'id': 0, 'name': 'Guitar', 'instrumentId': 29, 'tuning': [64, 59, 55, 50, 45, 40]}],
        'parts': [{'measures': measures, 'automations': {'tempo': [
            {'measure': 0, 'position': [0, 1], 'bpm': 120, 'type': 4}]}}]}
    source = root/'source.json'; source.write_text(json.dumps(raw))
    rate, duration = 22050, 97.5
    signal = np.zeros(round(rate*duration))
    for i, fret in enumerate(frets):
        start = round(i*.25*rate); length = min(round(.23*rate), len(signal)-start)
        if length <= 0: break
        t = np.arange(length)/rate
        hz = 440*2**((40+fret-69)/12)
        signal[start:start+length] += .15*np.minimum(t/.002,1)*np.exp(-t*8)*sum(np.sin(2*np.pi*hz*h*t)/h for h in range(1,6))
    wav = root/'truth.wav'; sf.write(wav,signal,rate)
    media=root/'media'; media.mkdir()
    audio=prepare_audio({'kind':'file','path':str(wav)},media)
    audio['source'].update(kind='youtube',videoId=VIDEO)
    sync={'version':1,'source':'songsterr-video-points',**META,'videoId':VIDEO,
          'status':'done','feature':None,'points':list(range(0,107,2))}
    performance=load_performance(source,META)
    candidate=align_from_songsterr(performance,audio,sync,META,allow_ending_candidate=True)
    alignment=ending_cutoff.authorize(performance,audio,candidate)
    assert alignment['recordingSync']['status'] != 'supported'
    assert alignment['recordingEnd']['timingWarning']['status']=='accepted_with_warning'
    return root,source,performance,audio,sync,alignment


def build(root, source, performance, audio, alignment, contract=57):
    root.mkdir()
    return Path(build_feedpak(performance,audio,alignment,root,output_dir=root/'out',source_path=source,
        compatibility=performance['compatibilityReport'],recipe={'preservationContract':contract,
        'audioSource':audio['source'],'alignment':{'provenance':alignment['provenance']},
        **({'preparation':alignment['preparation']} if alignment.get('preparation') else {})})['stagingPath'])


@pytest.fixture(scope='module')
def completed(warned):
    root,source,performance,audio,_,alignment=warned
    archive=build(root/'job',source,performance,audio,alignment)
    return archive


def test_imperfect_source_imports_faithfully_with_warning_and_cutoff(warned,completed):
    _,source,performance,audio,_,alignment=warned
    report=verify_import(source,completed,alignment,META)
    assert report['status']=='passed', report
    assert report['timing']['recordingSyncStatus']!='supported'
    assert report['timing']['timingWarning']==alignment['recordingEnd']['timingWarning']
    assert report['adjustments']['omittedEndingNotes']==34
    with zipfile.ZipFile(completed) as z:
        manifest=yaml.safe_load(z.read('manifest.yaml'))
        recipe=manifest['song_import']
        chart=json.loads(z.read(manifest['arrangements'][0]['file']))
        original=performance['tracks'][0]['notes'][:390]
        assert [(n['t'],n['f'],n['s']) for n in chart['notes']]==[(n['t'],n['f'],n['s']) for n in original]
        assert z.read(recipe['sourceFile'])==source.read_bytes()
        assert json.loads(z.read(recipe['recordingSyncFile']))['status']!='supported'
        assert recipe['recordingEnd']['timingWarning']['everyNoteVerified'] is False


def test_warning_is_recreated_after_encoding_and_preparation(warned,tmp_path):
    _,source,performance,audio,_,alignment=warned
    prepared,shifted=finalize(performance,audio,deepcopy(alignment),tmp_path)
    assert shifted['preparation']['seconds']==2
    assert shifted['recordingEnd']['timingWarning']['uncertainRanges']==alignment['recordingEnd']['timingWarning']['uncertainRanges']
    archive=build(tmp_path/'job',source,performance,prepared,shifted)
    report=verify_import(source,archive,shifted,META)
    assert report['status']=='passed',report
    assert report['adjustments']['omittedEndingNotes']==34


@pytest.mark.parametrize('change',['old_contract','removed_warning','false_ranges','early_note','missing_note','false_ledger','wrong_recording','forged_audio'])
def test_independent_verifier_rejects_forged_or_inaccurate_warning_import(warned,completed,tmp_path,change):
    _,source,_,_,_,original=warned
    alignment=deepcopy(original)
    with zipfile.ZipFile(completed) as z: files={n:z.read(n) for n in z.namelist()}
    manifest=yaml.safe_load(files['manifest.yaml']);recipe=manifest['song_import']
    chart_path=manifest['arrangements'][0]['file'];chart=json.loads(files[chart_path])
    ledger_path=recipe['endingOmissionsFile'];ledger=json.loads(files[ledger_path])
    if change=='old_contract':recipe['preservationContract']=56
    elif change=='removed_warning':
        alignment['recordingEnd'].pop('timingWarning');ledger.pop('timingWarning');recipe['recordingEnd'].pop('timingWarning')
    elif change=='false_ranges':
        alignment['recordingEnd']['timingWarning']['uncertainRanges'][0]['start']+=2
        recipe['recordingEnd']=deepcopy(alignment['recordingEnd']);ledger.update(alignment['recordingEnd'])
    elif change=='early_note':chart['notes'][50]['t']+=.03
    elif change=='missing_note':chart['notes'].pop(50)
    elif change=='false_ledger':ledger['notes'].pop()
    elif change=='wrong_recording':recipe['audioSource']['videoId']='different'
    elif change=='forged_audio':
        target=tmp_path/'silent.ogg'
        with sf.SoundFile(target,'w',samplerate=22050,channels=1,format='OGG',subtype='VORBIS') as stream:
            remaining=22050*97+11025
            while remaining:
                count=min(32768,remaining)
                stream.write(np.zeros(count,dtype=np.float32));remaining-=count
        files['audio/full.ogg']=target.read_bytes()
        stored=alignment['recordingSync'];stored['audioSha256']=hashlib.sha256(files['audio/full.ogg']).hexdigest()
        alignment['recordingEnd']['syncEvidenceHash']=ending_cutoff.digest(stored)
        recipe['recordingEnd']=deepcopy(alignment['recordingEnd']);ledger.update(alignment['recordingEnd'])
        files[recipe['recordingSyncFile']]=json.dumps(stored).encode()
    files['manifest.yaml']=yaml.safe_dump(manifest).encode();files[chart_path]=json.dumps(chart).encode();files[ledger_path]=json.dumps(ledger).encode()
    target=tmp_path/'changed.feedpak'
    with zipfile.ZipFile(target,'w') as z:
        for name,data in files.items():z.writestr(name,data)
    report=verify_import(source,target,alignment,META)
    assert report['status']=='failed',report
    if change=='forged_audio':assert any(e['code']=='ending_audio_warning' for e in report['errors']),report


def test_builder_requires_contract_for_warning(warned,tmp_path):
    _,source,performance,audio,_,alignment=warned
    with pytest.raises(ImportFailure,match='contract 57'):
        build(tmp_path/'job',source,performance,audio,alignment,56)


@pytest.mark.parametrize('change',['no_supported_passages','wrong_map','wrong_revision','nonfinite_window','gap','alternative_recording'])
def test_warning_cannot_bypass_missing_recording_evidence(warned,change):
    alignment=deepcopy(warned[-1]);report=alignment['recordingSync']
    if change=='no_supported_passages':
        for w in report['windows']:w['status']='inconclusive'
    elif change=='wrong_map':report['mapHash']='wrong'
    elif change=='wrong_revision':alignment['sourceTiming']['revisionId']='wrong'
    elif change=='nonfinite_window':report['windows'][-1]['end']=float('nan')
    elif change=='gap':report['windows'][2]['start']=report['windows'][1]['end']+.1
    elif change=='alternative_recording':alignment['sourceTiming']['feature']='alternative'
    assert cutoff_warning.acceptance(report,alignment) is None


@pytest.mark.parametrize('uncertain_status',['inconclusive','suspected_mismatch'])
def test_warning_policy_is_not_limited_to_an_ending(warned,uncertain_status):
    alignment=deepcopy(warned[-1]);report=alignment['recordingSync']
    report['status']=uncertain_status
    for i,w in enumerate(report['windows']):
        w['status']=uncertain_status if i==4 else 'supported'
        w.pop('jointEvidence',None)
    accepted=cutoff_warning.acceptance(report,alignment)
    assert accepted and accepted['uncertainRanges']==[{'start':32.,'end':48.}]

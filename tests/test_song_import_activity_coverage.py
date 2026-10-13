"""Uncharted spans are not failed timing checks; hidden activity still is."""
from copy import deepcopy
import json
import zipfile

import numpy as np
import pytest
import soundfile as sf
import yaml

from feedback_converter.song_import import activity_coverage as ac, cutoff_warning, ending_cutoff
from feedback_converter.song_import.audio import ImportFailure, prepare_audio
from feedback_converter.song_import.synchronization import align_from_songsterr
from feedback_converter.song_import.verification import verify_import
from test_song_import_cutoff_warning import warned, build
from test_songsterr_recording_end import META, VIDEO


def report():
    return {'audioDuration': 100., 'windows': [
        {'start': 20., 'end': 36., 'status': 'supported'},
        {'start': 36., 'end': 52., 'status': 'supported'},
        {'start': 60., 'end': 76., 'status': 'inconclusive'},
        {'start': 76., 'end': 92., 'status': 'supported'}]}


def tracks():
    return [{'events': [{'t': 20., 'end': 52.}, {'t': 60., 'end': 92.}]}]


def test_empty_intro_break_and_outro_are_proven_without_acoustic_claim():
    proof = ac.receipt(report(), tracks())
    assert proof['unchartedRanges'] == [{'start': 0., 'end': 20.},
        {'start': 52., 'end': 60.}, {'start': 92., 'end': 100.}]
    enriched = ac.attach(report(), tracks())
    assert ac.declared(enriched)
    assert enriched['windows'] == report()['windows']


@pytest.mark.parametrize('start,end', [(2.,2.), (19.,20.), (52.,52.), (51.,53.),
    (55.,55.), (58.,62.), (93.,94.), (1.,90.)])
def test_attacks_mutes_and_sustains_cannot_hide_in_gaps(start,end):
    t = tracks(); t.append({'events':[{'t':start,'end':end,'midi':None,'effects':{'mt':True}}]})
    assert ac.receipt(report(),t) is None


@pytest.mark.parametrize('field,value', [('t',float('nan')), ('end',float('inf')),
                                       ('t',-1.), ('end',19.)])
def test_invalid_source_timing_is_not_empty_space(field,value):
    t = tracks(); t[0]['events'][0][field] = value
    assert ac.receipt(report(),t) is None


def test_empty_source_and_missing_or_reordered_windows_fail_closed():
    assert ac.receipt(report(),[]) is None
    r = report(); r['windows'].reverse()
    assert ac.receipt(r,tracks()) is None
    r = report(); r['windows']=[]
    assert ac.receipt(r,tracks()) is None


def test_preparation_does_not_shift_the_evidence_domain():
    r = report(); r.update(audioDuration=102.,analysisOriginSeconds=2.)
    t = tracks()
    for n in t[0]['events']: n['t']+=2; n['end']+=2
    assert ac.receipt(r,t) == ac.receipt(report(),tracks())
    # First covered instant, including a zero-duration attack, is valid.
    t[0]['events'].append({'t':62.,'end':62.})
    assert ac.receipt(r,t)


@pytest.fixture(scope='module')
def delayed(warned,tmp_path_factory):
    _,source,performance,original,sync,_ = warned
    root=tmp_path_factory.mktemp('delayed-source')
    samples,rate=sf.read(original['path'],always_2d=True)
    path=root/'recording.wav'
    sf.write(path,np.concatenate([np.zeros((20*rate,samples.shape[1])),samples]),rate)
    media=root/'media';media.mkdir()
    audio=prepare_audio({'kind':'file','path':str(path)},media)
    audio['source'].update(kind='youtube',videoId=VIDEO)
    sync=deepcopy(sync);sync['points']=[p+20 for p in sync['points']]
    candidate=align_from_songsterr(performance,audio,sync,META,allow_ending_candidate=True)
    alignment=ending_cutoff.authorize(performance,audio,candidate)
    assert alignment['recordingEnd']['timingWarning']['version']==2
    assert alignment['recordingEnd']['timingWarning'][ac.KEY]['unchartedRanges']==[{'start':0.,'end':8.}]
    assert alignment['recordingSync']['status']!='supported'
    contract=119 if alignment['sourceTiming'].get('feature') == 'alternative' else 100
    archive=build(root/'job',source,performance,audio,alignment,contract)
    return source,performance,audio,alignment,archive


def test_delayed_source_imports_with_warning_and_independent_audio_check(delayed):
    source,_,_,alignment,archive=delayed
    checked=verify_import(source,archive,alignment,META)
    assert checked['status']=='passed',checked
    assert checked['timing']['independentAudioMatchAssessed'] is True
    assert checked['timing']['timingWarning']['version']==2
    assert checked['adjustments']['omittedEndingNotes']==34


def test_new_coverage_requires_new_contract(delayed,tmp_path):
    source,performance,audio,alignment,_=delayed
    minimum=119 if alignment['sourceTiming'].get('feature') == 'alternative' else 100
    with pytest.raises(ImportFailure,match=f'contract {minimum}'):
        build(tmp_path/'job',source,performance,audio,alignment,99)


@pytest.mark.parametrize('change',['fake_gap','old_contract','hidden_sustain','all_uncertain'])
def test_forged_coverage_or_evidence_fails_independent_verification(delayed,tmp_path,change):
    source,_,_,original,archive=delayed
    alignment=deepcopy(original)
    with zipfile.ZipFile(archive) as z: files={n:z.read(n) for n in z.namelist()}
    manifest=yaml.safe_load(files['manifest.yaml']);recipe=manifest['song_import']
    stored=alignment['recordingSync']
    if change=='old_contract': recipe['preservationContract']=99
    elif change=='fake_gap': stored[ac.KEY]['unchartedRanges'][0]['end']=21.
    elif change=='hidden_sustain':
        # Make an artificial hole entirely inside a held note. Rewrite all
        # producer claims consistently; raw-source reconstruction must reject.
        stored['windows'][0]['end']=20.05
        stored['windows'][1]['start']=20.15
        stored[ac.KEY]['unchartedRanges']=ac.gaps(stored)
    elif change=='all_uncertain':
        for w in stored['windows']:w['status']='inconclusive'
    warning=cutoff_warning.acceptance(stored,alignment)
    if warning: alignment['recordingEnd']['timingWarning']=warning
    alignment['recordingEnd']['syncEvidenceHash']=ending_cutoff.digest(stored)
    recipe['recordingEnd']=deepcopy(alignment['recordingEnd'])
    ledger=json.loads(files[recipe['endingOmissionsFile']]);ledger.update(alignment['recordingEnd'])
    files[recipe['endingOmissionsFile']]=json.dumps(ledger).encode()
    files[recipe['recordingSyncFile']]=json.dumps(stored).encode()
    files['manifest.yaml']=yaml.safe_dump(manifest).encode()
    target=tmp_path/'forged.feedpak'
    with zipfile.ZipFile(target,'w') as z:
        for name,data in files.items():z.writestr(name,data)
    checked=verify_import(source,target,alignment,META)
    assert checked['status']=='failed',checked
    if change=='hidden_sustain':
        assert any(e['code']=='ending_activity_coverage' for e in checked['errors']),checked

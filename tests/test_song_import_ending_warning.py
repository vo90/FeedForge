"""Source timing may be accepted with a bounded warning, never certified by it."""
from copy import deepcopy
import json
from zipfile import ZipFile

import numpy as np
import pytest
import soundfile as sf
import yaml

from feedback_converter.song_import import ending_padding as ep, ending_warning as ew
from feedback_converter.song_import.audio import ImportFailure, prepare_audio
from feedback_converter.song_import.builder import build_feedpak
from feedback_converter.song_import.preparation import finalize
from feedback_converter.song_import.score import load_performance
from feedback_converter.song_import.synchronization import align_from_songsterr
from feedback_converter.song_import.verification import verify_import
from test_songsterr_recording_end import META, VIDEO


def policy_inputs():
    timing={'source':'songsterr-video-points', 'status':'done', 'feature':None, **META, 'videoId':VIDEO}
    alignment={'method':'songsterr-video-points-v1', 'mapping':'piecewise-linear',
               'sourceTiming':timing, 'provenance':{**META, 'videoId':VIDEO, 'mapHash':'map'}}
    report={'version':'recording-clock-v4', 'status':'inconclusive', 'outroSupported':False,
            'suspectedMismatchWindows':0, 'audioDuration':100, 'mapHash':'map',
            'windows':[{'start':i,'end':min(i+16,100),'status':'supported' if i<80 else 'inconclusive'}
                       for i in range(0,96,8)]}
    return report, alignment, {'kind':'youtube','videoId':VIDEO}


def test_body_support_allows_warning_without_upgrading_evidence():
    report, alignment, source=policy_inputs(); before=deepcopy(report)
    accepted=ew.acceptance(report,alignment,source)
    assert accepted['status']=='accepted_with_warning'
    assert accepted['uncertainStart']==80
    assert accepted['supportedBodyWindows']==5
    assert report==before and report['status']=='inconclusive'


@pytest.mark.parametrize('fault', ['mismatch','hidden_mismatch','middle','whole_song','long_ending','missing_end',
    'missing_start','window_gap','duplicate_windows','no_body','alternative','recording','manual_audio','revision','map','old_clock'])
def test_warning_is_not_a_general_override(fault):
    r,a,s=policy_inputs()
    if fault=='mismatch':r['windows'][-1]['status']='suspected_mismatch'
    elif fault=='hidden_mismatch':r['windows'][-1]['jointEvidence']={'status':'suspected_mismatch'}
    elif fault=='middle':r['windows'][3]['status']='inconclusive'
    elif fault=='whole_song':
        for w in r['windows']:w['status']='inconclusive'
    elif fault=='long_ending':r['windows'][9]['status']='inconclusive'
    elif fault=='missing_end':r['windows'].pop()
    elif fault=='missing_start':r['windows'].pop(0)
    elif fault=='window_gap':r['windows'][3]['end']=25
    elif fault=='duplicate_windows':r['windows'].insert(1,deepcopy(r['windows'][0]))
    elif fault=='no_body':r['windows']=r['windows'][-3:]
    elif fault=='alternative':a['sourceTiming']['feature']='alternative'
    elif fault=='recording':s['videoId']='other-video'
    elif fault=='manual_audio':s['kind']='file'
    elif fault=='revision':a['provenance']['revisionId']=-1
    elif fault=='map':r['mapHash']='different'
    elif fault=='old_clock':r['version']='recording-clock-v3'
    assert ew.acceptance(r,a,s) is None


@pytest.fixture(scope='module')
def warning_package(tmp_path_factory):
    root=tmp_path_factory.mktemp('ending-warning')
    rate,duration=22050,103.9
    frets=np.random.default_rng(901).integers(0,17,416).tolist()
    frets[-64:]=[4]*64
    bars=[{'signature':[4,4],'voices':[{'beats':[
        {'duration':[1,8],'notes':[{'string':5,'fret':f}]} for f in frets[i:i+8]]}]} for i in range(0,416,8)]
    # A real sustained ending supplies too few distinct attacks to certify its
    # clock. It remains authored and audible; we do not mock acoustic evidence.
    for i in range(44,52):
        bars[i]['voices'][0]['beats']=[{'duration':[1,1],'notes':[
            {'string':5,'fret':4,**({'tie':True} if i>44 else {})}]}]
    raw={'format':'songsterr',**META,'title':'Ending warning fixture','artist':'Synthetic',
         'tracks':[{'id':0,'name':'Guitar','instrumentId':29,'tuning':[64,59,55,50,45,40]}],
         'parts':[{'measures':bars,'automations':{'tempo':[{'measure':0,'position':[0,1],'bpm':120,'type':4}]}}]}
    source=root/'source.json';source.write_text(json.dumps(raw))
    perf=load_performance(source,META)
    samples=np.zeros(round(rate*duration))
    for i,fret in enumerate(frets[:352]):
        begin=round(i*.25*rate);size=min(round(.23*rate),len(samples)-begin);t=np.arange(size)/rate
        hz=440*2**((40+fret-69)/12)
        samples[begin:begin+size]+=.15*np.minimum(t/.002,1)*np.exp(-t*8)*sum(np.sin(2*np.pi*hz*h*t)/h for h in range(1,6))
    begin=88*rate;t=np.arange(len(samples)-begin)/rate;hz=440*2**((44-69)/12)
    samples[begin:]+=.15*np.minimum(t/.002,1)*np.exp(-t*.1)*sum(np.sin(2*np.pi*hz*h*t)/h for h in range(1,6))
    wav=root/'truth.wav';sf.write(wav,samples,rate,subtype='FLOAT')
    job=root/'job';job.mkdir()
    audio=prepare_audio({'kind':'file','path':str(wav)},job,defer_encoding=True)
    audio['source']={'kind':'youtube','videoId':VIDEO}
    sync={'version':1,'source':'songsterr-video-points',**META,'videoId':VIDEO,'status':'done','feature':None,'points':list(range(0,105,2))}
    alignment=align_from_songsterr(perf,audio,sync,META,allow_padding_candidate=True)
    alignment=ep.authorize(perf,audio,alignment)
    assert alignment['endingPaddingSync']['status']=='inconclusive'
    assert alignment['endingPadding']['timingWarning']['status']=='accepted_with_warning'
    prepared,alignment=finalize(perf,audio,alignment,job)
    recipe={'preservationContract':39,'audioSource':audio['source'],'preparation':alignment['preparation'],
            'alignment':{'provenance':alignment['provenance'],'endingPadding':alignment['endingPadding']}}
    built=build_feedpak(perf,prepared,alignment,job,output_dir=root/'out',source_path=source,
                        recipe=recipe,compatibility=perf['compatibilityReport'])
    return source,built['stagingPath'],alignment,perf,audio,sync


def test_encoded_warning_package_remains_source_verified(warning_package):
    source,path,alignment,*_=warning_package
    result=verify_import(source,path,alignment,META)
    assert result['status']=='passed',result
    assert any(w.get('code')=='ending_sync_unconfirmed' for w in result['warnings'])
    assert alignment['endingPaddingSync']['status']=='inconclusive'
    assert alignment['endingPadding']['seconds']==pytest.approx(.1)


@pytest.mark.parametrize('fault',['missing_warning','forged_body','claimed_support','old_contract','alternate_recording'])
def test_forged_or_hidden_warning_is_rejected(warning_package,tmp_path,fault):
    source,path,original,*_=warning_package;alignment=deepcopy(original)
    with ZipFile(path) as z:files={name:z.read(name) for name in z.namelist()}
    manifest=yaml.safe_load(files['manifest.yaml']);recipe=manifest['song_import'];receipt=alignment['endingPadding']
    if fault=='missing_warning':receipt.pop('timingWarning')
    elif fault=='forged_body':receipt['timingWarning']['supportedBodyWindows']+=1
    elif fault=='claimed_support':alignment['endingPaddingSync']['status']='supported'
    elif fault=='old_contract':recipe['preservationContract']=38
    elif fault=='alternate_recording':recipe['audioSource']['videoId']='wrong-video'
    receipt['syncEvidenceHash']=ep.rs.digest(alignment['endingPaddingSync'])
    files[recipe['endingPaddingSyncFile']]=json.dumps(alignment['endingPaddingSync']).encode()
    files[recipe['endingPaddingFile']]=json.dumps(receipt).encode()
    recipe['alignment']['endingPadding']=deepcopy(receipt)
    files['manifest.yaml']=yaml.safe_dump(manifest).encode()
    target=tmp_path/'forged.feedpak'
    with ZipFile(target,'w') as z:
        for name,data in files.items():z.writestr(name,data)
    assert verify_import(source,target,alignment,META)['status']=='failed'


@pytest.mark.parametrize('fault',['shifted','wrong_music','silence'])
def test_clear_recording_conflicts_cannot_use_warning(warning_package,tmp_path,fault):
    _,_,_,perf,original,sync=warning_package
    data,rate=sf.read(original['path'])
    if fault=='shifted':data=np.concatenate([np.zeros(round(.7*rate)),data])[:len(data)]
    elif fault=='wrong_music':data=data[::-1]
    else:data=np.zeros_like(data)
    path=tmp_path/'wrong.wav';sf.write(path,data,rate,subtype='FLOAT')
    audio={**original,'path':path}
    alignment=align_from_songsterr(perf,audio,sync,META,allow_padding_candidate=True)
    with pytest.raises(ImportFailure):ep.authorize(perf,audio,alignment)

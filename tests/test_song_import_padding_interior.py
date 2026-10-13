from copy import deepcopy
import json
from zipfile import ZipFile

import numpy as np
import pytest
import soundfile as sf
import yaml

from feedback_converter.song_import import ending_warning as ew, ending_padding as ep
from feedback_converter.song_import.audio import prepare_audio, ImportFailure
from feedback_converter.song_import.builder import build_feedpak
from feedback_converter.song_import.preparation import finalize
from feedback_converter.song_import.score import load_performance
from feedback_converter.song_import.synchronization import align_from_songsterr
from feedback_converter.song_import.verification import verify_import
from test_song_import_ending_warning import policy_inputs
from test_songsterr_recording_end import META, VIDEO


def inputs(feature=None):
    r,a,s=policy_inputs(feature)
    for w in r['windows']: w['status']='supported'
    r['windows'][4]['status']='inconclusive'
    r['outroSupported']=True
    t=[{'events':[{'t':1.,'end':2.}, {'t':30.,'end':60.}, {'t':99.,'end':100.1}]}]
    return r,a,s,t


@pytest.mark.parametrize('feature', [None, 'alternative'])
def test_interior_uncertainty_preserves_the_acoustic_verdict(feature):
    r,a,s,t=inputs(feature);before=deepcopy(r)
    w=ew.acceptance(r,a,s,tracks=t)
    assert w['version']==2 and w['uncertainRanges']==[{'start':32,'end':48}]
    assert r==before


@pytest.mark.parametrize('feature', [None, 'alternative'])
def test_gap_retains_held_and_zero_duration_notes_as_uncertain(feature):
    r,a,s,t=inputs(feature);r['windows']=[w for w in r['windows'] if not 40<=w['start']<=64]
    t[0]['events'].append({'t':65.,'end':65.})
    w=ew.acceptance(r,a,s,tracks=t)
    assert w['sourceCoverage']['unverifiedSourceRanges']==[{'start':48,'end':60},{'start':65,'end':65}]
    assert w['uncertainRanges']==[{'start':32,'end':60},{'start':65,'end':65}]


@pytest.mark.parametrize('fault',['mismatch','hidden','all_unknown','wrong_recording','feature_backing','feature_solo','feature_unknown','bad_map','nan','late_attack','empty','reversed','duplicate'])
@pytest.mark.parametrize('feature', [None, 'alternative'])
def test_general_warning_keeps_rejection_guards(fault, feature):
    r,a,s,t=inputs(feature)
    if fault=='mismatch':r['windows'][0]['status']='suspected_mismatch'
    elif fault=='hidden':r['windows'][0]['jointEvidence']={'status':'suspected_mismatch'}
    elif fault=='all_unknown':
        for w in r['windows']:w['status']='inconclusive'
    elif fault=='wrong_recording':s['videoId']='different'
    elif fault.startswith('feature_'):a['sourceTiming']['feature']=fault.removeprefix('feature_')
    elif fault=='bad_map':r['mapHash']='other'
    elif fault=='nan':t[0]['events'][0]['end']=float('nan')
    elif fault=='late_attack':t[0]['events'][0]['t']=101.
    elif fault=='empty':t=[]
    elif fault=='reversed':r['windows'][0]['end']=-1
    elif fault=='duplicate':r['windows'].insert(1,deepcopy(r['windows'][0]))
    assert ew.acceptance(r,a,s,tracks=t) is None


@pytest.fixture(scope='module', params=[None, 'alternative'], ids=['primary', 'alternative-full-mix'])
def package(tmp_path_factory, request):
    root=tmp_path_factory.mktemp('interior-padding');rate=22050;duration=95.9
    frets=np.random.default_rng(911).integers(0,17,384).tolist()
    bars=[{'signature':[4,4],'voices':[{'beats':[{'duration':[1,8],'notes':[{'string':5,'fret':f}]} for f in frets[i:i+8]]}]} for i in range(0,384,8)]
    for i in range(16,26):
        bars[i]['voices'][0]['beats']=[{'duration':[1,1],'notes':[{'string':5,'fret':4,**({'tie':True} if i>16 else {})}]}]
    for i in range(26,32):bars[i]['voices'][0]['beats']=[{'duration':[1,1],'rest':True,'notes':[]}]
    raw={'format':'songsterr',**META,'title':'Interior warning','artist':'Synthetic',
         'tracks':[{'id':0,'name':'Guitar','instrumentId':29,'tuning':[64,59,55,50,45,40]}],
         'parts':[{'measures':bars,'automations':{'tempo':[{'measure':0,'position':[0,1],'bpm':120,'type':4}]}}]}
    source=root/'source.json';source.write_text(json.dumps(raw));perf=load_performance(source,META)
    samples=np.zeros(round(rate*duration))
    for i,fret in enumerate(frets):
        if 128<=i<256:continue
        begin=round(i*.25*rate);size=min(round(.23*rate),len(samples)-begin);t=np.arange(size)/rate
        hz=440*2**((40+fret-69)/12)
        samples[begin:begin+size]+=.15*np.minimum(t/.002,1)*np.exp(-t*8)*sum(np.sin(2*np.pi*hz*h*t)/h for h in range(1,6))
    t=np.arange(20*rate)/rate;hz=440*2**((44-69)/12)
    samples[32*rate:52*rate]+=.15*np.minimum(t/.002,1)*np.exp(-t*.1)*sum(np.sin(2*np.pi*hz*h*t)/h for h in range(1,6))
    wav=root/'truth.wav';sf.write(wav,samples,rate,subtype='FLOAT');job=root/'job';job.mkdir()
    audio=prepare_audio({'kind':'file','path':str(wav)},job,defer_encoding=True);audio['source']={'kind':'youtube','videoId':VIDEO}
    sync={'version':1,'source':'songsterr-video-points',**META,'videoId':VIDEO,'status':'done','feature':request.param,'points':list(range(0,97,2))}
    alignment=ep.authorize(perf,audio,align_from_songsterr(perf,audio,sync,META,allow_padding_candidate=True))
    assert alignment['endingPadding']['timingWarning']['version']==2
    prepared,alignment=finalize(perf,audio,alignment,job)
    recipe={'preservationContract':119 if request.param == 'alternative' else 101,'audioSource':audio['source'],'preparation':alignment['preparation'],
            'alignment':{'provenance':alignment['provenance'],'endingPadding':alignment['endingPadding']}}
    built=build_feedpak(perf,prepared,alignment,job,output_dir=root/'out',source_path=source,recipe=recipe,compatibility=perf['compatibilityReport'])
    return source,built['stagingPath'],alignment


def test_actual_audio_and_packaged_source_are_independently_checked(package):
    source,path,alignment=package
    result=verify_import(source,path,alignment,META)
    assert result['status']=='passed',result
    assert alignment['endingPadding']['seconds']==pytest.approx(.1)
    assert alignment['endingPadding']['timingWarning']['sourceCoverage']['unverifiedSourceRanges']


@pytest.mark.parametrize('fault',['hidden_hold','fake_support','old_contract','wrong_recording','alternative_old_contract'])
def test_forged_package_is_rejected(package,tmp_path,fault):
    source,path,original=package;alignment=deepcopy(original)
    if fault=='alternative_old_contract' and alignment['sourceTiming'].get('feature') != 'alternative':
        pytest.skip('Contract 119 is required only for alternative-recording warnings.')
    with ZipFile(path) as z:files={n:z.read(n) for n in z.namelist()}
    manifest=yaml.safe_load(files['manifest.yaml']);recipe=manifest['song_import'];receipt=alignment['endingPadding']
    if fault=='hidden_hold':receipt['timingWarning']['sourceCoverage']['unverifiedSourceRanges']=[]
    elif fault=='fake_support':receipt['timingWarning']['supportedPassages']+=1
    elif fault=='old_contract':recipe['preservationContract']=100
    elif fault=='alternative_old_contract':recipe['preservationContract']=118
    elif fault=='wrong_recording':recipe['audioSource']['videoId']='wrong'
    files[recipe['endingPaddingFile']]=json.dumps(receipt).encode();recipe['alignment']['endingPadding']=deepcopy(receipt)
    files['manifest.yaml']=yaml.safe_dump(manifest).encode();target=tmp_path/'forged.feedpak'
    with ZipFile(target,'w') as z:
        for name,data in files.items():z.writestr(name,data)
    assert verify_import(source,target,alignment,META)['status']=='failed'


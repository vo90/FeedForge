from copy import deepcopy
import io
import json
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import pytest
import soundfile as sf
import yaml

from feedback_converter.song_import.preparation import finalize, recording_view
from feedback_converter.song_import.audio import prepare_audio
from feedback_converter.song_import.score import load_performance
from feedback_converter.song_import.builder import build_feedpak
from feedback_converter.song_import.verification import verify_import
from feedback_converter.song_import.synchronization import align_from_songsterr


@pytest.fixture
def package(tmp_path):
    from test_songsterr_pickup import document
    raw=document()
    source=tmp_path/'source.json';source.write_text(json.dumps(raw),encoding='utf8')
    performance=load_performance(source)
    rate=22050;time=np.arange(rate*4)/rate
    samples=.15*np.sin(2*np.pi*220*time)
    input=tmp_path/'source.wav';sf.write(input,samples,rate,subtype='FLOAT')
    job=tmp_path/'job';job.mkdir()
    audio=prepare_audio({'kind':'file','path':str(input)},job,defer_encoding=True)
    audio['source'].update(kind='youtube',videoId='abcdefghijk')
    metadata={'songId':'123','revisionId':'456','approval':'approved'}
    timing={'version':1,'source':'songsterr-video-points',**metadata,'status':'done','feature':None,
            'videoId':'abcdefghijk','points':[.17,.92,2.92]}
    alignment=align_from_songsterr(performance,audio,timing,metadata)
    audio,alignment=finalize(performance,audio,alignment,job)
    recipe={'preservationContract':35,'audioSource':audio['source'],'alignment':{'provenance':alignment['provenance']},
            'preparation':alignment['preparation']}
    result=build_feedpak(performance,audio,alignment,job,output_dir=tmp_path/'out',recipe=recipe,
                         source_path=source,compatibility=performance['compatibilityReport'])
    return source,Path(result['stagingPath']),alignment,input


def test_preparation_package_preserves_pickup_and_audio(package):
    source,path,alignment,original=package
    result=verify_import(source,path,alignment)
    assert result['status']=='passed',result
    with ZipFile(path) as z:
        m=yaml.safe_load(z.read('manifest.yaml'))
        full=io.BytesIO(z.read('audio/full.ogg'))
        data,rate=sf.read(recording_view(full,alignment['preparation']),always_2d=True)
        before,_=sf.read(original,always_2d=True)
        assert data.shape==before.shape
        assert np.corrcoef(data[:,0],before[:,0])[0,1]>.999
        assert m['duration']==pytest.approx(4+alignment['preparation']['seconds'])
        chart=json.loads(z.read(m['arrangements'][0]['file']))
        assert chart['notes'][0]['t']==pytest.approx(2,abs=1/rate)
        pickup=json.loads(z.read(m['song_import']['pickupTimelineFile']))
        assert pickup['measures'][0]['anchors'][0]['time']==pytest.approx(2,abs=1/rate)


@pytest.mark.parametrize('fault',['note','notation','pickup','beat','duration','receipt','audible_prefix','later_anchor'])
def test_preparation_mutations_fail(package,tmp_path,fault):
    source,path,alignment,_=package
    alignment=deepcopy(alignment)
    with ZipFile(path) as z:files={n:z.read(n) for n in z.namelist()}
    manifest=yaml.safe_load(files['manifest.yaml'])
    arr=manifest['arrangements'][0]
    if fault in {'note','beat'}:
        chart=json.loads(files[arr['file']]);key='notes' if fault=='note' else 'beats'
        chart[key][0]['t' if fault=='note' else 'time']-=.1
        files[arr['file']]=json.dumps(chart).encode()
    if fault=='notation':
        notation=json.loads(files[arr['notation']]);notation['measures'][0]['t']-=.1
        files[arr['notation']]=json.dumps(notation).encode()
    if fault=='pickup':
        name=manifest['song_import']['pickupTimelineFile'];p=json.loads(files[name]);p['measures'][0]['anchors'][0]['time']-=.1
        files[name]=json.dumps(p).encode()
    if fault=='duration':manifest['duration']+=.1
    if fault=='receipt':manifest['song_import']['preparation']['frames']-=100
    if fault=='later_anchor':alignment['anchors'][-1]['audio']+=.1
    if fault=='audible_prefix':
        data,rate=sf.read(io.BytesIO(files['audio/full.ogg']),always_2d=True)
        data[:rate//2]=.1;out=io.BytesIO();sf.write(out,data,rate,format='OGG',subtype='VORBIS')
        files['audio/full.ogg']=out.getvalue()
        import hashlib
        # Even a matching forged hash must not excuse audible padding.
        alignment['preparation']['audioSha256']=hashlib.sha256(out.getvalue()).hexdigest()
        manifest['song_import']['preparation']=alignment['preparation']
    files['manifest.yaml']=yaml.safe_dump(manifest).encode()
    changed=tmp_path/'changed.feedpak'
    with ZipFile(changed,'w') as z:
        for name,data in files.items():z.writestr(name,data)
    result=verify_import(source,changed,alignment)
    assert result['status']=='failed',result


@pytest.mark.parametrize('first',[0,.17,1.99999,2,7])
def test_minimum_across_arrangements_and_no_unneeded_padding(tmp_path,first):
    rate=22050;path=tmp_path/'audio.wav';sf.write(path,np.ones(rate*10)*.05,rate)
    job=tmp_path/'job';job.mkdir()
    audio=prepare_audio({'kind':'file','path':str(path)},job,defer_encoding=True)
    perf={'tracks':[{'notes':[{'t':first+1,'sus':1}]},{'chords':[{'t':first,'notes':[{'s':0,'f':3,'sus':1}]}]}]}
    audio,alignment=finalize(perf,audio,{'offset':0,'scale':1,'status':'validated'},job)
    shift=alignment['preparation']['seconds']
    assert max(0,2-first)<=shift<max(0,2-first)+1/rate
    assert audio['duration']==pytest.approx(10+shift)


@pytest.mark.parametrize('rate,original_frames,ending_frames', [
    (44100, 10320897, 3795), (22050, 66151, 0), (48000, 144001, 29),
])
def test_prepared_duration_matches_exact_encoded_frame_count(tmp_path, monkeypatch, rate, original_frames, ending_frames):
    from feedback_converter.song_import import ending_padding
    from feedback_converter.song_import.local_sync import _hash
    source=tmp_path/'source.wav'
    sf.write(source,np.zeros(original_frames,dtype='float32'),rate,subtype='FLOAT')
    audio={'path':str(source),'duration':original_frames/rate}
    alignment={'offset':0,'scale':1,'status':'validated'}
    if ending_frames:
        alignment['endingPadding']={'frames':ending_frames,'sampleRate':rate,
            'originalFrames':original_frames,'sourceSamplesSha256':_hash(source)}
    # This unit exercises encoding/duration arithmetic; musical authorization
    # of the supplied ending is covered by the ending-padding tests.
    monkeypatch.setattr(ending_padding,'confirm_encoded',lambda *args:None)
    performance={'tracks':[{'notes':[{'t':0,'sus':1}]}]}
    prepared,shifted=finalize(performance,audio,alignment,tmp_path)
    info=sf.info(prepared['path'])
    assert info.frames==original_frames+2*rate+ending_frames
    assert prepared['duration']==info.frames/info.samplerate
    assert shifted['preparation']['seconds']==2
    if rate==44100:
        assert prepared['duration']==236.12
        from feedback_converter.chart_guidance import finalize as guidance
        from feedback_converter.verify_chart_guidance import validate
        chart={'tuning':[0]*6,'capo':0,'notes':[],
            'templates':[{'name':'','frets':[3,5,-1,-1,-1,-1]}],
            'chords':[{'t':229.4475,'id':0,'notes':[
                {'s':0,'f':3,'sus':6.6725},{'s':1,'f':5,'sus':6.6725}]}]}
        guidance(chart)
        assert validate(chart,duration=prepared['duration'])==[]
        assert validate(chart,duration=prepared['duration']-.001)

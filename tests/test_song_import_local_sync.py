from copy import deepcopy
import numpy as np
import pytest
import soundfile as sf

from feedback_converter.song_import import local_sync as ls, recording_sync as rs


def fixture():
    # Score times deliberately differ from the target recording clock.
    times=[0,.5,1,2,2.5,3,4,4.5,5,5.5,6,6.5]
    midis=[45,49,52,47,50,54,45,49,52,47,50,54]
    tracks=[{'id':'lead','instrument':'guitar','events':[
        {'t':t,'end':t+.25,'midi':m,'effects':{}} for t,m in zip(times,midis)]}]
    boundaries=[0,1.5,3.5,8]
    points=[-.2,1.4,3.4,8]
    real=[.2,1.4,3.4,8]
    mapped=np.interp(times,boundaries,real)
    frames=round(9/rs.DT)
    pitch=np.zeros((frames,len(rs.MIDIS)))
    flux=np.full((frames,4),.15)
    x=np.arange(frames)*rs.DT
    for t,m in zip(mapped,midis):
        pitch[:,m-28]+=np.exp(-.5*((x-t-.085)/.045)**2)
        flux[:,1]+=5*np.exp(-.5*((x-t)/.018)**2)
    return tracks,boundaries,points,pitch,flux


def test_opening_fit_preserves_later_map_and_validates_unused_notes():
    args=fixture()
    result=ls.propose(*args)
    assert result['status']=='supported',result
    assert result['replacementBoundaries']==pytest.approx([.2,1.4],abs=.02)
    assert result['lockedBoundary']==3.4
    assert len(result['heldOutTimes'])==3
    assert args[2]==[-.2,1.4,3.4,8]


@pytest.mark.parametrize('damage',['missing_first','missing_context','wrong_pitch','drum_only','outside_bounds'])
def test_unsafe_openings_do_not_get_repaired(damage):
    tracks,bounds,points,pitch,flux=fixture()
    if damage=='missing_first':pitch[:round(.4/rs.DT)]=0;flux[:round(.4/rs.DT)]=.15
    if damage=='missing_context':pitch[round(1.4/rs.DT):round(3.4/rs.DT)]=0
    if damage=='wrong_pitch':pitch=np.roll(pitch,2,axis=1)
    if damage=='drum_only':pitch[:]=0
    if damage=='outside_bounds':points[0]=-2
    assert ls.propose(tracks,bounds,points,pitch,flux)['status']=='inconclusive'


@pytest.mark.parametrize('reference',[440,450,430])
def test_audio_only_tuning_does_not_need_score(tmp_path,reference):
    rate=22050;t=np.arange(rate*4)/rate
    signal=sum(np.sin(2*np.pi*reference*2**((m-69)/12)*t)*.08 for m in [45,52,57])
    path=tmp_path/'test.wav';sf.write(path,signal,rate,subtype='FLOAT')
    result=ls.tuning(path)
    expected=(1200*np.log2(reference/440)+50)%100-50
    assert result['status']=='supported'
    assert result['cents']==pytest.approx(expected,abs=2)


def test_empty_diagnostic_never_claims_verified():
    report=ls.assess_features([],np.zeros((100,len(rs.MIDIS))),np.zeros((100,4)),1)
    assert report['status']=='inconclusive' and not report['everyNoteVerified']


def test_diagnostic_rounding_never_excuses_changed_status_or_clock():
    from feedback_converter.song_import.verification import Check
    fresh={'status':'inconclusive','bestOffset':.26,'jointBest':.42,'jointContrast':2.3997}
    stored={**fresh,'jointBest':.42036,'jointContrast':2.4019}
    check=Check();ls.compare_assessment(fresh,stored,check);assert not check.errors
    for change in ({'status':'supported'},{'bestOffset':0},{'jointBest':.5}):
        check=Check();ls.compare_assessment(fresh,{**stored,**change},check);assert check.errors


def test_competing_repeated_opening_is_not_chosen_arbitrarily():
    tracks,bounds,points,pitch,flux=fixture()
    delay=round(.2/rs.DT)
    pitch[delay:]+=pitch[:-delay].copy()
    flux[delay:]+=flux[:-delay].copy()
    assert ls.propose(tracks,bounds,points,pitch,flux)['status']=='inconclusive'


def test_worker_repair_uses_real_encoded_audio_and_independent_source(tmp_path,monkeypatch):
    import json
    from pathlib import Path
    from feedback_converter.song_import import worker, audio as audio_module
    frets=[5,9,12,7,3,10,2,8]*2
    raw={'format':'songsterr','songId':12,'revisionId':34,'title':'Opening fixture','artist':'Synthetic',
         'tracks':[{'id':0,'name':'Lead','instrumentId':29,'tuning':[64,59,55,50,45,40]}],
         'parts':[{'automations':{'tempo':[{'measure':0,'position':0,'bpm':120}]},'measures':[
             {'signature':[4,4],'voices':[{'beats':[{'duration':[1,4],'notes':[{'string':5,'fret':f}]} for f in frets[i:i+4]]}]}
             for i in range(0,16,4)]}]}
    source=tmp_path/'source.json';source.write_text(json.dumps(raw),encoding='utf8')
    rate=22050;samples=np.zeros(rate*10)
    for i,f in enumerate(frets):
        start=.2+i*.45 if i<4 else 2+(i-4)*.5
        t=np.arange(round(.32*rate))/rate
        frequency=440*2**((40+f-69)/12)
        signal=sum(np.sin(2*np.pi*frequency*h*t)/h for h in range(1,5))
        envelope=np.minimum(1,t/.004)*np.exp(-t*7)
        at=round(start*rate);samples[at:at+len(t)]+=.15*signal*envelope
    recording=tmp_path/'recording.wav';sf.write(recording,samples,rate,subtype='FLOAT')
    monkeypatch.setattr(audio_module,'_public_url',lambda url:url)
    monkeypatch.setattr(audio_module,'_download_youtube',lambda *a,**kw:(recording,{'kind':'youtube','videoId':'abcdefghijk'}))
    monkeypatch.setattr(worker,'align_audio',lambda *a,**kw:pytest.fail('A uniquely identified opening should use the bounded repair.'))
    request={'scorePath':str(source),'audio':{'kind':'url','url':'https://youtu.be/abcdefghijk'},'artworkLookup':False,
             'metadata':{'songId':'12','revisionId':'34','approval':'approved'},
             'synchronization':{'version':1,'source':'songsterr-video-points','songId':'12','revisionId':'34',
                               'videoId':'abcdefghijk','status':'done','feature':None,'points':[-.2,2,4,6,8]},
             'workDir':str(tmp_path/'work'),'outputDir':str(tmp_path/'out')}
    result=worker.run_import(request)
    assert result['ok'],result
    assert result['verification']['status']=='passed'
    assert result['alignment']['openingRepair']['status']=='supported'
    assert result['verification']['preparationSeconds']>1.7
    from zipfile import ZipFile
    import yaml
    from feedback_converter.song_import.verification import verify_import
    with ZipFile(result['stagingPath']) as z:original={n:z.read(n) for n in z.namelist()}
    record_path=Path(result['evidence']['recordPath']) if result['evidence'].get('recordPath') else None
    # Read the complete applied map from retained content-addressed evidence.
    evidence_root=tmp_path/'song-import-evidence'
    record=json.loads((evidence_root/'records'/f"{result['evidence']['id']}.json").read_text())
    alignment=json.loads((evidence_root/'objects'/record['objects']['appliedAlignment']).read_text())
    for fault in ('invented_receipt','later_anchor','missing_audio_attack'):
        files=dict(original);changed=deepcopy(alignment)
        manifest=yaml.safe_load(files['manifest.yaml'])
        if fault=='invented_receipt':
            changed['openingRepair']['fitTimes'][0]+=.2
            files[manifest['song_import']['openingRepairFile']]=json.dumps(changed['openingRepair']).encode()
        elif fault=='later_anchor':changed['anchors'][2]['audio']+=.1
        else:
            import io
            data,rate=sf.read(io.BytesIO(files['audio/full.ogg']),always_2d=True)
            data[round(1.95*rate):round(2.4*rate)]=0
            output=io.BytesIO();sf.write(output,data,rate,format='OGG',subtype='VORBIS')
            files['audio/full.ogg']=output.getvalue()
        archive=tmp_path/(fault+'.feedpak')
        with ZipFile(archive,'w') as z:
            for name,data in files.items():z.writestr(name,data)
        assert verify_import(source,archive,changed)['status']=='failed'


def test_worker_audit_maps_rational_terminal_boundary_only_once(tmp_path,monkeypatch):
    import json
    from feedback_converter.song_import import worker, audio as audio_module
    raw={'format':'songsterr','songId':12,'revisionId':34,'title':'Rational ending','artist':'Synthetic',
         'tracks':[{'id':0,'name':'Lead','instrumentId':29,'tuning':[64,59,55,50,45,40]}],
         'parts':[{'automations':{'tempo':[{'measure':0,'position':0,'bpm':115}]},'measures':[
             {'signature':[4,4],'voices':[{'beats':[{'duration':[1,1],'notes':[{'string':5,'fret':5}]}]}]}
             for _ in range(3)]}]}
    source=tmp_path/'source.json';source.write_text(json.dumps(raw),encoding='utf8')
    rate=22050;recording=tmp_path/'recording.wav'
    sf.write(recording,.15*np.sin(2*np.pi*110*np.arange(rate*8)/rate),rate,subtype='FLOAT')
    monkeypatch.setattr(audio_module,'_public_url',lambda url:url)
    monkeypatch.setattr(audio_module,'_download_youtube',lambda *a,**kw:(recording,{'kind':'youtube','videoId':'abcdefghijk'}))
    result=worker.run_import({'scorePath':str(source),'audio':{'kind':'url','url':'https://youtu.be/abcdefghijk'},
        'artworkLookup':False,'metadata':{'songId':'12','revisionId':'34','approval':'approved'},
        'synchronization':{'version':1,'source':'songsterr-video-points','songId':'12','revisionId':'34',
            'videoId':'abcdefghijk','status':'done','feature':None,'points':[0,2.1,4.2,6.3]},
        'workDir':str(tmp_path/'work'),'outputDir':str(tmp_path/'out')})
    # 720/115 seconds rounds upwards past the exact last score boundary.
    # This must not be rejected by the diagnostic audit's second interpolation.
    assert result['ok'],result
    assert result['verification']['status']=='passed'
    assert result['verification']['timingAssessment']['everyNoteVerified'] is False


@pytest.mark.parametrize('offset',[0,.26,-.26])
def test_clock_audit_distinguishes_supported_from_displaced_patterns(offset):
    duration=50
    times=np.arange(.5,48,.41)
    midis=[45+((i*7+i//3)%14) for i in range(len(times))]
    x=np.arange(round(duration/rs.DT))*rs.DT
    pitch=np.zeros((len(x),len(rs.MIDIS)));flux=np.full((len(x),4),.12)
    events=[]
    for t,m in zip(times,midis):
        pitch[:,m-28]+=np.exp(-.5*((x-t-.085)/.045)**2)
        flux[:,1]+=4*np.exp(-.5*((x-t)/.018)**2)
        events.append({'t':t+offset,'end':t+offset+.2,'midi':m,'effects':{}})
    report=ls.assess_features([{'id':'g','instrument':'guitar','events':events}],pitch,flux,duration)
    if offset==0:assert report['status']=='supported'
    else:assert report['status']=='inconclusive' and report['suspectedMismatchWindows']>=3

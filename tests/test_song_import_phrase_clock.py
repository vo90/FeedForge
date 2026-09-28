"""Independent cue ambiguity, source coverage, and wrong-clock controls."""
from copy import deepcopy
import json
from zipfile import ZipFile

import numpy as np
import pytest
import soundfile as sf
import yaml

from feedback_converter.song_import import phrase_clock as pc, recording_sync as rs


def evidence(times, midis, duration=40, distractors=False, held=False):
    size=int(np.ceil(duration/rs.DT))+1
    pitch=np.zeros((size,len(rs.MIDIS)))
    flux=np.zeros((size,4))
    events=[]
    for i,(at,midi) in enumerate(zip(times,midis)):
        length=1.3 if held else .25
        events.append({'t':at,'end':at+length,'midi':midi,'effects':{}})
        copies=[at]+([at+[.6,-.8,1.1,-.5][i%4]] if distractors else [])
        for time in copies:
            grid=np.arange(size)*rs.DT
            pitch[:,midi-28]=np.maximum(pitch[:,midi-28],np.exp(-((grid-time-.085)/(.4 if held else .07))**2))
            pulse=3*np.exp(-((grid-time)/.035)**2)
            flux[:,:]=np.maximum(flux,pulse[:,None])
    return [{'id':'g','instrument':'guitar','events':events}],pitch,flux


def test_individually_ambiguous_cues_establish_one_sequence():
    tracks,pitch,flux=evidence([20,23,27,30,34,38],[44,49,53,47,56,51],distractors=True)
    selected=pc.groups(tracks[0],16,40)
    # Every cue has a competing copy, at different offsets. The sequence has one
    # coherent clock. No individual-cue uniqueness requirement can establish it.
    report=pc.measure(selected,pitch,flux,'guitar')
    assert report['status']=='supported',report
    full=pc.assess(tracks,pitch,flux,40,20)
    assert full['status']=='supported',full


def test_complementary_parts_form_one_ordered_phrase():
    tracks,pitch,flux=evidence([20,23,27,30,34,38],[44,49,53,47,56,51],distractors=True)
    notes=tracks[0]['events']
    tracks=[{'id':str(i),'instrument':'guitar','events':notes[i*2:i*2+2]} for i in range(3)]
    result=pc.assess(tracks,pitch,flux,40,20)
    assert result['status']=='supported',result
    assert {p['trackId'] for p in result['phrases'] if p['status']=='supported'}=={'ensemble'}


@pytest.mark.parametrize('fault',['shift','drift','final_fixed','middle','silent','wrong_pitch','competing_phrase'])
def test_sequence_cannot_hide_wrong_or_indistinguishable_clock(fault):
    tracks,pitch,flux=evidence([20,23,27,30,34,38],[44,49,53,47,56,51],distractors=True)
    altered=deepcopy(tracks)
    for i,n in enumerate(altered[0]['events']):
        delta=.5 if fault=='shift' or fault=='final_fixed' and i<5 else (i*.16 if fault=='drift' else (.5 if fault=='middle' and 1<=i<=4 else 0))
        n['t']+=delta;n['end']+=delta
        if fault=='wrong_pitch':n['midi']+=1
    if fault=='silent':pitch[:]=0;flux[:]=0
    if fault=='competing_phrase':
        shift=round(.5/rs.DT)
        pitch=np.maximum(pitch,np.roll(pitch,shift,axis=0))
        flux=np.maximum(flux,np.roll(flux,shift,axis=0))
    assert pc.assess(altered,pitch,flux,40,20)['status']!='supported'


def test_final_chord_alone_cannot_cover_unassessed_earlier_attacks():
    tracks,pitch,flux=evidence([20,23,27,30,34,38],[44,49,53,47,56,51])
    pitch[:round(37/rs.DT)]=0;flux[:round(37/rs.DT)]=0
    assert pc.assess(tracks,pitch,flux,40,20)['status']=='inconclusive'


def test_dense_early_run_cannot_outvote_wrong_later_chords():
    times=list(np.arange(20,26,.1))+[30,34,38]
    midis=np.random.default_rng(847).integers(40,65,len(times))
    tracks,pitch,flux=evidence(times,midis)
    assert pc.assess(tracks,pitch,flux,40,20)['status']=='supported'
    for n in tracks[0]['events']:
        if 30<=n['t']<38:n['t']+=.5;n['end']+=.5
    assert pc.assess(tracks,pitch,flux,40,20)['status']=='inconclusive'


def test_coverage_uses_actual_cues_not_the_overlapping_window_label():
    tracks=[{'events':[{'t':t} for t in [22,25,29,33,38]]}]
    p=[{'status':'supported','start':22,'end':38,'offset':0}]
    # The first eligible cue occurs after the old four-second deadline. There
    # are no earlier source attacks; requiring a nonexistent cue is meaningless.
    assert pc.coverage(p,tracks,16,40)['status']=='supported'
    tracks[0]['events'].insert(0,{'t':18})
    assert pc.coverage(p,tracks,16,40)['uncoveredAttackTimes']==[18]
    assert pc.coverage(p,tracks,16,40)['status']=='inconclusive'


def test_coverage_reports_holes_even_when_outer_cues_match():
    tracks=[{'events':[{'t':t} for t in [18,22,26,30,34,38]]}]
    p=[{'status':'supported','start':18,'end':22,'offset':0},
       {'status':'supported','start':30,'end':38,'offset':0}]
    assert pc.coverage(p,tracks,16,40)['uncoveredAttackTimes']==[26]
    p.insert(1,{'status':'supported','start':22,'end':30,'offset':.01})
    assert pc.coverage(p,tracks,16,40)['status']=='supported'
    p[-1]['offset']=.25
    assert pc.coverage(p,tracks,16,40)['status']=='inconclusive'


def test_tremolo_and_held_pitch_cannot_supply_attack_authority_alone():
    tracks,pitch,flux=evidence([20,23,27,30,34,38],[44,49,53,47,56,51],held=True)
    for n in tracks[0]['events']:n['effects']['tr']=True
    assert pc.assess(tracks,pitch,flux,40,20)['status']=='inconclusive'


def test_duplicate_arrangements_do_not_turn_ambiguous_cues_into_support():
    tracks,pitch,flux=evidence([20,23,27,30,34,38],[44,49,53,47,56,51])
    shift=round(.5/rs.DT)
    pitch=np.maximum(pitch,np.roll(pitch,shift,axis=0))
    flux=np.maximum(flux,np.roll(flux,shift,axis=0))
    tracks=tracks*10
    before=deepcopy(tracks)
    assert pc.assess(tracks,pitch,flux,40,20)['status']=='inconclusive'
    assert tracks==before


def test_fast_note_pitch_samples_stay_within_the_note():
    tracks,pitch,flux=evidence([20,23,27,30,34,38],[44,49,53,47,56,51])
    for n in tracks[0]['events']:n['end']=n['t']+.06
    pitch[:]=0
    grid=np.arange(len(pitch))*rs.DT
    for n in tracks[0]['events']:
        pitch[(grid>=n['t'])&(grid<n['end']),n['midi']-28]=1
        pitch[(grid>=n['end'])&(grid<n['end']+.08),n['midi']-27]=1
    # Sampling 85 ms after these attacks observes the next, different pitch.
    before=deepcopy(tracks)
    result=pc.assess(tracks,pitch,flux,40,20)
    assert result['status']=='supported',result
    assert tracks==before and result['everyNoteVerified'] is False


@pytest.fixture(scope='module')
def phrase_package(tmp_path_factory):
    """Raw tab + independently synthesized recording, through the real worker."""
    from unittest.mock import patch
    from feedback_converter.song_import.audio import prepare_audio
    from feedback_converter.song_import.worker import run_import
    root=tmp_path_factory.mktemp('phrase-package')
    rng=np.random.default_rng(729)
    times=list(np.arange(0,40,.25))+[42,45,49,53,58,61.5]
    frets=rng.integers(0,17,len(times))
    bars=[{'signature':[4,4],'voices':[{'beats':[{'duration':[1,8],'notes':[{'rest':True}],'rest':True} for _ in range(8)]}]} for _ in range(31)]
    rate=22050;duration=61.9;audio=np.zeros(round(rate*duration))
    def tone(at,fret,length):
        begin=round(at*rate);size=min(round(length*rate),len(audio)-begin)
        if size<=0:return
        t=np.arange(size)/rate;hz=440*2**((40+int(fret)-69)/12)
        audio[begin:begin+size]+=.15*np.minimum(t/.002,1)*np.exp(-t*8)*sum(np.sin(2*np.pi*hz*h*t)/h for h in range(1,6))
    for i,(at,fret) in enumerate(zip(times,frets)):
        index=round(at*4);length=.5 if at==61.5 else .25
        for b in range(round(length*4)):
            note={'string':5,'fret':int(fret),**({'tie':True} if b else {})}
            bars[(index+b)//8]['voices'][0]['beats'][(index+b)%8]={'duration':[1,8],'notes':[note]}
        tone(at,fret,length)
        if 40<=at<60:
            tone(at+[.6,-.8,1.1,-.5][(i-160)%4],fret,length)
    raw={'format':'songsterr','songId':12,'revisionId':34,'title':'Phrase evidence','artist':'Test',
         'tracks':[{'id':0,'name':'Guitar','instrumentId':29,'tuning':[64,59,55,50,45,40]}],
         'parts':[{'measures':bars,'automations':{'tempo':[{'measure':0,'position':[0,1],'bpm':120,'type':4}]}}]}
    source=root/'source.json';source.write_text(json.dumps(raw))
    wav=root/'truth.wav';sf.write(wav,audio,rate,subtype='FLOAT')
    media=root/'media';media.mkdir()
    prepared=prepare_audio({'kind':'file','path':str(wav)},media,defer_encoding=True)
    prepared['source'].update(kind='youtube',videoId='abcdefghijk')
    meta={'songId':'12','revisionId':'34','approval':'approved'}
    sync={'version':1,'source':'songsterr-video-points',**meta,'videoId':'abcdefghijk',
          'status':'done','feature':None,'points':list(range(0,64,2))}
    with patch('feedback_converter.song_import.worker.prepare_audio',lambda *a,**k:deepcopy(prepared)):
        result=run_import({'scorePath':str(source),'metadata':meta,'synchronization':sync,
                          'workDir':str(root/'work'),'outputDir':str(root/'out'),'auditDir':str(root/'evidence'),'artworkLookup':False})
    return root,source,result,meta


def test_real_phrase_audio_survives_worker_encoding_and_independent_verification(phrase_package):
    root,source,result,meta=phrase_package
    assert result['ok'],result
    record=json.loads((root/'evidence/records'/f"{result['evidence']['id']}.json").read_text())
    alignment=json.loads((root/'evidence/objects'/record['objects']['appliedAlignment']).read_text())
    report=alignment['endingPaddingSync']
    assert report['version']==rs.VERSION
    assert report['phraseEvidence']['status']=='supported',report
    assert any(w.get('method')=='ending-phrase-clock' for w in report['windows'])
    assert result['verification']['endingSilenceSeconds']==pytest.approx(.1)


def test_forged_phrase_receipt_is_remeasured(phrase_package,tmp_path):
    from feedback_converter.song_import.verification import verify_import
    root,source,result,meta=phrase_package
    assert result['ok'],result
    record=json.loads((root/'evidence/records'/f"{result['evidence']['id']}.json").read_text())
    alignment=json.loads((root/'evidence/objects'/record['objects']['appliedAlignment']).read_text())
    with ZipFile(result['stagingPath']) as z:files={n:z.read(n) for n in z.namelist()}
    manifest=yaml.safe_load(files['manifest.yaml']);recipe=manifest['song_import']
    alignment['endingPaddingSync']['phraseEvidence']['coverage']['intervals']=[[0,61.9]]
    alignment['endingPadding']['syncEvidenceHash']=rs.digest(alignment['endingPaddingSync'])
    files[recipe['endingPaddingSyncFile']]=json.dumps(alignment['endingPaddingSync']).encode()
    files[recipe['endingPaddingFile']]=json.dumps(alignment['endingPadding']).encode()
    recipe['alignment']['endingPadding']=deepcopy(alignment['endingPadding'])
    files['manifest.yaml']=yaml.safe_dump(manifest).encode()
    target=tmp_path/'forged.feedpak'
    with ZipFile(target,'w') as z:
        for name,data in files.items():z.writestr(name,data)
    report=verify_import(source,target,alignment,meta)
    assert report['status']=='failed',report
    assert any('timing_assessment' in e['code'] for e in report['errors']),report

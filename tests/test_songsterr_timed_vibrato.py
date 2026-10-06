"""Timed source instructions survive playback, retiming and archive verification."""
from copy import deepcopy
import json
from pathlib import Path
from zipfile import ZipFile
import pytest
import yaml
from test_song_import_score import beat, measure, raw_score, import_json
from test_songsterr_bend_timing import checked
from feedback_converter.song_import.builder import build_feedpak, _retime_note
from feedback_converter.song_import.verification import verify_import
from feedback_converter.song_import.verify_source import songsterr
from feedback_converter.song_import.verify_timeline import expected
from feedback_converter.song_import.terminal_sustains import trim_held_note

REFERENCE=json.loads((Path(__file__).parent/'fixtures/songsterr_timed_vibrato_reference.json').read_text())


@pytest.mark.parametrize('case',REFERENCE['cases'],ids=lambda c:c['id'])
def test_qualified_native_controls_match_activation_and_strength_before_synthetic_slide(case):
    assert REFERENCE['referenceSha256']=='4219f9dbf0952af83be36c8b590f10fea7a0f36d72bd1f92297a3d768f93f5ab'
    p=checked(case['source']);n=p['tracks'][0]['notes'][0]
    for profile in case['native'].values():
        controls=profile['vibratoControls']
        assert profile['visibleAuthoredAttacks']==1
        if case['id'].startswith('beat'):
            assert not controls
            e=p['fingerBendTimingEvidence'][0]
            assert e['status']=='resolved'
            assert 'beatVibrato' not in e['terminalSlideOut']
            assert not n.get('vb') and 'vibrato_marks' not in n
            continue
        # Probe inside intervals, away from sub-tick synth resets. At the
        # terminal slide, its arbitrary synth duration is deliberately excluded.
        for t in (.04,.24,.54,.74,1.04):
            prior=[c for c in controls if c['elapsed']<=t]
            native=prior[-1]['value'] if prior else 0
            active=next((m for m in n['vibrato_marks'] if m['start']<=t<m['end']),None)
            assert (127 if active and active['intensity']=='wide' else 64 if active else 0)==native
        # Pitch remains the independently qualified bend, never its modulation.
        assert n['bn']==2


def source(kinds=(None, 'slight', 'slight'), slide=True):
    notes = [beat(fret=14, duration=d, **({'tie':True} if i else {}),
                  **({'leftHandVibrato':k} if k else {}))
             for i,(d,k) in enumerate(zip(((1,4),(1,4),(1,2)), kinds))]
    notes[0]['notes'][0]['bend'] = {'points':[{'position':0,'tone':0},
        {'position':15,'tone':100},{'position':60,'tone':100}]}
    if slide: notes[-1]['notes'][0]['slide'] = 'downwards'
    return raw_score([measure(*notes)])


@pytest.mark.parametrize('kinds,marks', [
    ((None,'slight','slight'), [(.5,2,'slight')]),
    (('slight',None,None), [(0,2,'slight')]),
    ((None,'wide',None), [(.5,1,'wide')]),
    (('wide','slight',None), [(0,.5,'wide'),(.5,1,'slight')]),
    (('slight','wide','slight'), [(0,.5,'slight'),(.5,1,'wide'),(1,2,'slight')])])
def test_source_tie_controls_do_not_invent_attacks_or_resume_old_modulation(kinds,marks):
    doc=source(kinds);p=checked(doc);n=p['tracks'][0]['notes'][0]
    assert len(p['tracks'][0]['notes'])==1
    assert n['vibrato_marks']==[{'start':a,'end':b,'intensity':k} for a,b,k in marks]
    assert p['fingerBendTimingEvidence'][0]['status']=='resolved'
    v=expected(songsterr(doc), {'offset':0,'scale':1})
    assert v['parts'][0]['notes'][0]['note']['vibrato_marks']==n['vibrato_marks']
    assert n['f']==14 and n['sus']==2 and n['bn']==2 and 'sl' not in n


def test_modern_intensity_precedes_legacy_flags_and_beat_only_stays_diagnosed(tmp_path):
    doc=source((None,'slight',None));beats=doc['parts'][0]['measures'][0]['voices'][0]['beats']
    beats[1]['notes'][0]['wideVibrato']=True
    p=import_json(tmp_path,doc)
    assert p['tracks'][0]['notes'][0]['vibrato_marks'][0]['intensity']=='slight'
    del beats[1]['notes'][0]['leftHandVibrato'];del beats[1]['notes'][0]['wideVibrato']
    beats[1]['wideVibrato']=True
    p=import_json(tmp_path,doc)
    assert p['fingerBendTimingEvidence'][0]['status']=='resolved'
    assert not p['tracks'][0]['notes'][0].get('vb') and 'vibrato_marks' not in p['tracks'][0]['notes'][0]
    assert any(f['feature']=='beat.wideVibrato' for f in p['compatibilityReport']['findings'])
    assert not any(f['feature']=='note.bend_timing' for f in p['compatibilityReport']['findings'])


def test_piecewise_mapping_clipping_and_empty_authoritative_intervals():
    n=checked(source())['tracks'][0]['notes'][0]
    alignment={'offset':0,'scale':1,'mapping':'piecewise-linear',
               'anchors':[{'score':0,'audio':1},{'score':1,'audio':2},{'score':2,'audio':4}]}
    mapped=_retime_note(n,alignment,5)
    assert mapped['vibrato_marks']==[{'start':.5,'end':3.,'intensity':'slight'}]
    # Test a plain held note: pitch/slide clipping stays independently guarded.
    hold={k:v for k,v in mapped.items() if k not in ('bn','bnv','slide_out','slide_out_marks')}
    cut,_=trim_held_note(hold,2.5)
    assert cut['vibrato_marks']==[{'start':.5,'end':1.5,'intensity':'slight'}]
    cut,_=trim_held_note(hold,1.25)
    assert cut['vibrato_marks']==[] and cut['vb']


@pytest.mark.parametrize('marks', [None,{},[{'start':0,'end':True,'intensity':'wide'}],
    [{'start':0,'end':float('inf'),'intensity':'slight'}],
    [{'start':0,'end':1,'intensity':'wide'}]*2,
    [{'start':1,'end':1.000000001,'intensity':'slight'}]])
def test_malformed_or_unrepresentable_marks_cannot_be_packaged(marks):
    from feedback_converter.song_import.audio import ImportFailure
    with pytest.raises(ImportFailure):
        _retime_note({'t':0,'sus':2,'vibrato_marks':marks},{'offset':0,'scale':1},3)


def test_tempos_repeats_chords_and_trill_notes_match_independent_timing():
    for trill in (False, True):
        doc=source((None,'wide',None),slide=False)
        bar=doc['parts'][0]['measures'][0];bar.update(repeatStart=True,repeat=2)
        doc['parts'][0]['automations']['tempo'].append({'measure':0,'position':960,'bpm':60,'type':4})
        if trill:
            first=bar['voices'][0]['beats'][0]['notes'][0]
            del first['bend'];first['trill']={'auxiliaryFret':16,'speed':120}
        else:
            for b in bar['voices'][0]['beats']:
                other=deepcopy(b['notes'][0]);other.update(string=1,fret=9);b['notes'].append(other)
        checked(doc)


@pytest.mark.parametrize('piecewise',[False,True])
def test_completed_package_verifier_rejects_lost_shifted_or_fabricated_marks(tmp_path,piecewise):
    from test_song_import_builder import inputs
    _,audio,_,job=inputs(tmp_path)
    doc=source();p=import_json(tmp_path,doc);path=tmp_path/'score.json'
    alignment={'status':'validated','offset':.25,'scale':1}
    if piecewise:
        alignment.update(mapping='piecewise-linear', anchors=[{'score':0,'audio':.25},
            {'score':.75,'audio':1},{'score':4,'audio':4.9}],tempos=[{'time':.25,'bpm':120},{'time':1,'bpm':100}])
    result=build_feedpak(p,audio,alignment,job,output_dir=tmp_path/'out',source_path=path,
        compatibility=p['compatibilityReport'],recipe={'preservationContract':62})
    archive=Path(result['stagingPath']);checked_result=verify_import(path,archive,alignment)
    assert checked_result['status']=='passed', checked_result['errors']
    with ZipFile(archive) as z: original={name:z.read(name) for name in z.namelist()}
    manifest=yaml.safe_load(original['manifest.yaml']);cp=manifest['arrangements'][0]['file']
    assert original[manifest['song_import']['sourceFile']]==path.read_bytes()
    for fault in ('missing','empty','early','long','wide','boolean','overlap','extra','contract'):
        files=dict(original);chart=json.loads(files[cp]);note=chart['notes'][0]
        m=deepcopy(manifest)
        if fault=='missing':del note['vibrato_marks']
        elif fault=='empty':note['vibrato_marks']=[]
        elif fault=='early':note['vibrato_marks'][0]['start']=0
        elif fault=='long':note['vibrato_marks'][0]['end']+=1
        elif fault=='wide':note['vibrato_marks'][0]['intensity']='wide'
        elif fault=='boolean':note['vibrato_marks'][0]['start']=False
        elif fault=='overlap':note['vibrato_marks']*=2
        elif fault=='extra':note['vibrato_marks'][0]['future']=1
        elif fault=='contract':m['song_import']['preservationContract']=61
        files[cp]=json.dumps(chart).encode();files['manifest.yaml']=yaml.safe_dump(m).encode()
        dest=tmp_path/f'{fault}.feedpak'
        with ZipFile(dest,'w') as z:
            for name,data in files.items():z.writestr(name,data)
        assert verify_import(path,dest,alignment)['status']=='failed',fault

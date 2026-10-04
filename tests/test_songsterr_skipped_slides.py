"""Skip only a slide whose ending destination disappears before a rest."""
from copy import deepcopy
import json
from pathlib import Path
from zipfile import ZipFile
import pytest
import yaml
from test_song_import_score import beat, measure, raw_score
from test_songsterr_undefined_slides import compare
from feedback_converter.song_import.timeline import render
from feedback_converter.song_import.songsterr import parse
from feedback_converter.song_import.verify_source import songsterr
from feedback_converter.song_import.verify_timeline import expected


def document(slide='shift'):
    rest = {'duration':[1,1], 'rest':True, 'notes':[{'rest':True}]}
    return raw_score([
        measure(beat(duration=(7,8)), beat(fret=12,slide=slide,duration=(1,8)), repeatStart=True),
        measure(beat(fret=2),alternateEnding=[1]),
        measure(rest,alternateEnding=[2],repeat=3),
        measure(beat(fret=2),alternateEnding=[3]),
    ])


@pytest.mark.parametrize('slide', ['shift','legato','aboveshift','abovelegato'])
def test_only_second_visit_omits_path_and_retains_note(slide):
    p=compare(document(slide)); notes=[n for n in p['tracks'][0]['notes'] if n['f']==12]
    assert [(n['t'],n['sus'],n.get('sl')) for n in notes]==[(1.75,.25,2),(5.75,.25,None),(9.75,.25,2)]
    assert bool(notes[0].get('ln')) == bool(notes[2].get('ln')) == ('legato' in slide)
    assert 'ln' not in notes[1]
    if slide.startswith('above'):assert all(n['slide_in_marks']==[{'direction':'down','time':0.}] for n in notes)
    r,=p['undefinedSlideEvidence']
    assert r['occurrence']==3 and r['start']==5.75 and r['fret']==12
    assert r['target']=={'sourceId':'songsterr:0:1:0:0:0','measure':2,'fret':2,'performed':False}
    assert r['interruption']=={'location':'parts/0/measures/2/voices/0/beats/0','occurrence':4,'time':6.,'kind':'rest'}
    assert r['transition']=={'fromMeasure':1,'toMeasure':3,'occurrence':4}


def test_actual_destination_before_rest_is_kept():
    doc=document(); doc['parts'][0]['measures'][2]['voices'][0]['beats']=[beat(fret=7,duration=(1,2)),{'duration':[1,2],'rest':True,'notes':[{'rest':True}]}]
    p=compare(doc)
    assert [n['sl'] for n in p['tracks'][0]['notes'] if n['f']==12]==[2,7,2]
    assert 'undefinedSlideEvidence' not in p


def test_other_string_does_not_supply_destination():
    doc=document(); doc['parts'][0]['measures'][2]['voices'][0]['beats'].insert(0,beat(fret=7,string=1,duration=(1,2)))
    doc['parts'][0]['measures'][2]['voices'][0]['beats'][1]['duration']=[1,2]
    assert len(compare(doc)['undefinedSlideEvidence'])==1


@pytest.mark.parametrize('fault',['no_rest','other_voice_rest','unfilled_gap','source_not_at_boundary','missing_written_target','written_target_muted','written_target_tied','hopo'])
def test_no_general_permission_to_drop_unresolved_links(fault):
    doc=document();bars=doc['parts'][0]['measures']
    if fault=='no_rest':bars[2]['voices'][0]['beats']=[beat(string=1)]
    if fault=='other_voice_rest':bars[2]['voices'].append(deepcopy(bars[2]['voices'][0]));bars[2]['voices'][0]['beats']=[beat(string=1)]
    if fault=='unfilled_gap':bars[2]['voices'][0]['beats']=[beat(string=1,duration=(1,4))]
    if fault=='source_not_at_boundary':bars[0]['voices'][0]['beats'][0]['duration']=[3,4]
    if fault=='missing_written_target':bars[1]['voices'][0]['beats'][0]['notes'][0]['string']=1
    if fault=='written_target_muted':bars[1]['voices'][0]['beats'][0]['notes'][0]['dead']=True
    if fault=='written_target_tied':bars[1]['voices'][0]['beats'][0]['notes'][0]['tie']=True
    if fault=='hopo':bars[0]['voices'][0]['beats'][1]['notes'][0]['hp']=True
    with pytest.raises(ValueError):render(parse(doc))
    with pytest.raises(ValueError):expected(songsterr(doc),{'offset':0,'scale':1})


def test_tied_origin_retains_attack_and_marks_only_final_segment():
    doc=document();doc['parts'][0]['measures'][0]['voices'][0]['beats'][0]['notes'][0]['fret']=12
    doc['parts'][0]['measures'][0]['voices'][0]['beats'][1]['notes'][0]['tie']=True
    p=compare(doc);r,=p['undefinedSlideEvidence']
    assert r['attack']==4 and r['start']==5.75
    assert [(n['t'],n['sus']) for n in p['tracks'][0]['notes'] if n['f']==12]==[(0,2),(4,2),(8,2)]


@pytest.mark.parametrize('direction',['upwards','downwards'])
def test_directional_slides_are_not_omitted(direction):
    p=compare(document(direction))
    assert 'undefinedSlideEvidence' not in p
    assert all('slide_out' in n for n in p['tracks'][0]['notes'] if n['f']==12)


def test_mixed_chord_retains_other_resolved_slide():
    doc=document();bars=doc['parts'][0]['measures']
    bars[0]['voices'][0]['beats'][1]['notes'].append({'string':1,'fret':10,'slide':'shift'})
    for i in (1,3):bars[i]['voices'][0]['beats'][0]['notes'].append({'string':1,'fret':0})
    bars[2]['voices'][0]['beats']=[beat(fret=0,string=1,duration=(1,2)),{'duration':[1,2],'rest':True,'notes':[{'rest':True}]}]
    p=compare(doc)
    assert len(p['undefinedSlideEvidence'])==1
    for chord in p['tracks'][0]['chords']:
        for n in chord['notes']:
            if n['f']==10:assert n['sl']==0


def test_gpif_does_not_gain_omission_policy():
    score=parse(document());score.source['format']='gpif'
    with pytest.raises(ValueError,match='repeat jump'):render(score)


def test_voice_projection_and_tempo_keep_independent_occurrence_evidence():
    doc=document()
    for bar in doc['parts'][0]['measures']:
        voice=deepcopy(bar['voices'][0])
        for b in voice['beats']:
            for n in b['notes']:
                if n.get('fret') is not None:n['fret']+=2
        bar['voices'].append(voice)
    doc['parts'][0]['automations']['tempo'].append({'measure':2,'position':[0,1],'bpm':90,'type':4})
    p=compare(doc)
    assert len(p['tracks'])==2
    assert len(p['undefinedSlideEvidence'])==2
    assert {r['trackId'] for r in p['undefinedSlideEvidence']}=={t['id'] for t in p['tracks']}
    assert {r['sourceId'].split(':')[3] for r in p['undefinedSlideEvidence']}=={'0','1'}
    assert all(r['occurrence']==3 for r in p['undefinedSlideEvidence'])


@pytest.mark.parametrize('fault',[None,'missing','empty','visit','target','rest','time','transition','report','contract','pitch','legato','note_deleted','good_slide_removed'])
def test_independent_package_check_rejects_corrupted_omissions(tmp_path,fault):
    from test_song_import_builder import inputs
    from feedback_converter.song_import import load_performance
    from feedback_converter.song_import.builder import build_feedpak
    from feedback_converter.song_import.verification import verify_import
    _,audio,_,job=inputs(tmp_path)
    source=tmp_path/'score.json';source.write_text(json.dumps(document()))
    p=load_performance(source);alignment={'status':'validated','offset':.2,'scale':.15}
    assert any(f['feature']=='note.slide_skipped_ending' for f in p['compatibilityReport']['findings'])
    built=build_feedpak(p,audio,alignment,job,output_dir=tmp_path/'out',source_path=source,
                       compatibility=p['compatibilityReport'],recipe={'preservationContract':80})
    archive=Path(built['stagingPath'])
    if fault:
        with ZipFile(archive) as z:files={n:z.read(n) for n in z.namelist()}
        receipt=json.loads(files['import/undefined-slides.json']);r=receipt['gestures'][0]
        if fault=='empty':receipt['gestures']=[]
        if fault=='visit':r['occurrence']=1
        if fault=='target':r['target']['fret']=5
        if fault=='rest':r['interruption']['location']='parts/0/measures/1/voices/0/beats/0'
        if fault=='time':r['interruption']['time']+=.01
        if fault=='transition':r['transition']['toMeasure']=2
        files['import/undefined-slides.json']=json.dumps(receipt).encode()
        if fault=='missing':del files['import/undefined-slides.json']
        if fault=='report':
            report=json.loads(files['import/compatibility.json']);report['findings']=[f for f in report['findings'] if f['feature']!='note.slide_skipped_ending'];report['findingCount']=len(report['findings']);files['import/compatibility.json']=json.dumps(report).encode()
        manifest=yaml.safe_load(files['manifest.yaml']);name=manifest['arrangements'][0]['file'];chart=json.loads(files[name]);notes=[n for n in chart['notes'] if n['f']==12]
        if fault=='pitch':notes[1]['sl']=5
        if fault=='legato':notes[1]['ln']=True
        if fault=='note_deleted':chart['notes'].remove(notes[1])
        if fault=='good_slide_removed':notes[0].pop('sl')
        files[name]=json.dumps(chart).encode()
        if fault=='contract':manifest['song_import']['preservationContract']=54;files['manifest.yaml']=yaml.safe_dump(manifest).encode()
        archive=tmp_path/'changed.feedpak'
        with ZipFile(archive,'w') as z:
            for n,v in files.items():z.writestr(n,v)
    checked=verify_import(source,archive,alignment)
    assert checked['status']==('failed' if fault else 'passed'),checked

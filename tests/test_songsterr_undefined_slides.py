"""An undefined slide may be omitted, never its explicit destination X."""
from copy import deepcopy
import json
from pathlib import Path
from zipfile import ZipFile
import pytest
import yaml
from test_song_import_score import beat, measure, raw_score
from feedback_converter.song_import.songsterr import parse
from feedback_converter.song_import.timeline import render
from feedback_converter.song_import.verify_source import songsterr
from feedback_converter.song_import.verify_timeline import expected
from feedback_converter.song_import.verification import Check, _notes, _flatten


def document(slide='legato', fret=3, dead=False):
    return raw_score([measure(beat(fret=fret, dead=dead, slide=slide, duration=(1, 2)),
                              beat(fret=None, dead=True, duration=(1, 2)))])


def compare(doc):
    before = deepcopy(doc)
    p = render(parse(doc)); v = expected(songsterr(doc), {'offset': 0, 'scale': 1})
    assert p.get('undefinedSlideEvidence', []) == v['undefined_slides']
    check = Check()
    for track, part in zip(p['tracks'], v['parts']):
        _notes(part['notes'], _flatten(track), check, part['source'], p['duration'])
    assert not check.errors, check.errors
    assert doc == before
    return p


@pytest.mark.parametrize('slide', ['shift', 'legato'])
@pytest.mark.parametrize('fret', [0, 3, 8, 12])
@pytest.mark.parametrize('dead', [False, True])
def test_keeps_both_attacks_with_no_invented_path_or_target(slide, fret, dead):
    p = compare(document(slide, fret, dead)); a, b = p['tracks'][0]['notes']
    assert [(n['f'], n['t'], n['sus']) for n in (a,b)] == [(fret,0,1), (127,1,1)]
    assert bool(a.get('mt')) == dead and b['mt'] is True
    assert not any(k in n for n in (a,b) for k in ('sl','ln','ho','po','slide_out','slide_out_marks'))
    r, = p['undefinedSlideEvidence']
    assert r['sourceId'] == 'songsterr:0:0:0:0:0' and r['authored'] == {'slide':slide}
    assert r['target'] == {'sourceId':'songsterr:0:0:0:1:0','occurrence':1,'time':1,'fret':None,'muted':True}


def test_tied_segment_and_destination_tie_preserve_attacks_and_segment_location():
    doc = raw_score([measure(beat(duration=(1,4)), beat(tie=True,slide='shift',duration=(1,4)),
                             beat(fret=None,dead=True,duration=(1,4)), beat(fret=None,dead=True,tie=True,duration=(1,4)))])
    p = compare(doc)
    assert [(n['f'],n['t'],n['sus']) for n in p['tracks'][0]['notes']] == [(3,0,1),(127,1,1)]
    r, = p['undefinedSlideEvidence']
    assert r['sourceId'] == 'songsterr:0:0:0:1:0' and r['start'] == .5 and r['attack'] == 0


def test_mute_to_mute_shift_retains_both_written_mutes():
    p = compare(document('shift', None, True))
    assert all(n['mt'] and n['f']==127 for n in p['tracks'][0]['notes'])
    assert p['mutedSlideEvidence'][0]['used']['rule'] == 'omitted-shift-to-unpitched-mute'
    assert p['undefinedSlideEvidence'][0]['fret'] is None


def test_mixed_chord_repeat_keeps_defined_slide_and_each_omission():
    doc = document(); bar=doc['parts'][0]['measures'][0]
    bar.update(repeatStart=True,repeat=2)
    bar['voices'][0]['beats'][0]['notes'].append({'string':1,'fret':5,'slide':'shift'})
    bar['voices'][0]['beats'][1]['notes'].append({'string':1,'fret':7})
    p=compare(doc)
    assert [r['occurrence'] for r in p['undefinedSlideEvidence']] == [1,2]
    for c in p['tracks'][0]['chords'][::2]:
        assert next(n for n in c['notes'] if n['f']==5)['sl']==7
        assert 'sl' not in next(n for n in c['notes'] if n['f']==3)


def test_successive_links_do_not_connect_across_muted_target():
    doc = raw_score([measure(beat(slide='shift',duration=(1,4)),beat(fret=None,dead=True,duration=(1,4)),
                             beat(fret=5,slide='shift',duration=(1,4)),beat(fret=7,duration=(1,4)))])
    p=compare(doc); a,b,c,d=p['tracks'][0]['notes']
    assert 'sl' not in a and 'sl' not in b and c['sl']==7
    assert len(p['undefinedSlideEvidence'])==1


def test_voice_projection_keeps_omissions_on_the_correct_arrangements():
    doc=document(); bar=doc['parts'][0]['measures'][0]
    other=deepcopy(bar['voices'][0]); other['beats'][0]['notes'][0]['fret']=7
    bar['voices'].append(other)
    p=compare(doc)
    assert len(p['tracks'])==2
    assert {r['trackId'] for r in p['undefinedSlideEvidence']}=={t['id'] for t in p['tracks']}
    assert {r['sourceId'].split(':')[3] for r in p['undefinedSlideEvidence']}=={'0','1'}


def test_separate_incoming_direction_survives_omitted_outgoing_link():
    p=compare(document('abovelegato'))
    assert p['tracks'][0]['notes'][0]['slide_in_marks']==[{'direction':'down','time':0.}]
    assert 'sl' not in p['tracks'][0]['notes'][0]


@pytest.mark.parametrize('fault', ['no_dead','missing_target','repeat_jump','hopo','other_string','other_voice','tied_x'])
def test_ambiguous_or_unapproved_cases_stay_blocked(fault):
    doc=document(); bar=doc['parts'][0]['measures'][0]; beats=bar['voices'][0]['beats']
    if fault=='no_dead': beats[1]['notes'][0].pop('dead')
    if fault=='missing_target': beats.pop()
    if fault=='repeat_jump':
        doc['parts'][0]['measures'].append(measure(beats.pop()))
        bar.update(repeatStart=True,repeat=2)
    if fault=='hopo': beats[0]['notes'][0].update(hp=True)
    if fault=='other_string': beats[1]['notes'][0]['string']=1
    if fault=='other_voice': bar['voices'].append({'beats':[beats.pop()]})
    if fault=='tied_x': beats[1]['notes'][0]['tie']=True
    with pytest.raises(ValueError):render(parse(doc))
    with pytest.raises(ValueError):expected(songsterr(doc), {'offset':0,'scale':1})


def test_rule_is_songsterr_only():
    score=parse(document()); score.source['format']='gpif'
    with pytest.raises(ValueError,match='unpitched mute'):render(score)


@pytest.mark.parametrize('direction', ['upwards','downwards'])
def test_explicit_direction_keeps_existing_slide_out(direction):
    doc=document(direction); p=compare(doc)
    assert 'undefinedSlideEvidence' not in p
    assert p['tracks'][0]['notes'][0]['slide_out']==('up' if direction=='upwards' else 'down')


@pytest.mark.parametrize('kind', ['pitched','muted'])
@pytest.mark.parametrize('fault', [None,'missing','empty','extra','source','target','time','rule','report','pitch','mute','delete_x','shift_x','duplicate_x','legato','contract'])
def test_package_checks_omission_receipt_and_preserved_x_independently(tmp_path,kind,fault):
    from test_song_import_builder import inputs
    from feedback_converter.song_import import load_performance
    from feedback_converter.song_import.builder import build_feedpak
    from feedback_converter.song_import.verification import verify_import
    _, audio, _, job=inputs(tmp_path)
    doc=document() if kind=='pitched' else document('shift',None,True)
    source=tmp_path/'source.json'; source.write_text(json.dumps(doc),encoding='utf-8')
    p=load_performance(source); alignment={'status':'validated','offset':.2,'scale':1.1}
    assert p['compatibilityReport']['status']=='limitations'
    r=next(r for r in p['compatibilityReport']['findings'] if r['feature']=='note.undefined_slide_to_mute')
    assert r['impact']=='display_or_expression' and not r['valueTruncated']
    built=build_feedpak(p,audio,alignment,job,output_dir=tmp_path/'out',source_path=source,
                       compatibility=p['compatibilityReport'],recipe={'preservationContract':54})
    archive=Path(built['stagingPath'])
    if fault:
        with ZipFile(archive) as z:files={n:z.read(n) for n in z.namelist()}
        receipt=json.loads(files['import/undefined-slides.json'])
        if fault=='empty':receipt['gestures']=[]
        if fault=='extra':receipt['gestures']*=2
        if fault=='source':receipt['sourceSha256']='0'*64
        if fault=='target':receipt['gestures'][0]['target']['fret']=0
        if fault=='time':receipt['gestures'][0]['target']['time']+=.01
        if fault=='rule':receipt['gestures'][0]['used']['rule']='invent-slide'
        files['import/undefined-slides.json']=json.dumps(receipt).encode()
        if fault=='missing':del files['import/undefined-slides.json']
        if fault=='report':
            report=json.loads(files['import/compatibility.json'])
            report['findings']=[r for r in report['findings'] if r['feature']!='note.undefined_slide_to_mute']
            report['findingCount']=len(report['findings']); files['import/compatibility.json']=json.dumps(report).encode()
        manifest=yaml.safe_load(files['manifest.yaml']); name=manifest['arrangements'][0]['file']
        chart=json.loads(files[name])
        if fault=='pitch':chart['notes'][0]['sl']=0
        if fault=='legato':chart['notes'][0]['ln']=True
        if fault=='mute':chart['notes'][1]['mt']=False
        if fault=='delete_x':chart['notes'].pop()
        if fault=='shift_x':chart['notes'][1]['t']+=.01
        if fault=='duplicate_x':chart['notes'].append(deepcopy(chart['notes'][1]))
        files[name]=json.dumps(chart).encode()
        if fault=='contract':
            manifest['song_import']['preservationContract']=53; files['manifest.yaml']=yaml.safe_dump(manifest).encode()
        archive=tmp_path/'changed.feedpak'
        with ZipFile(archive,'w') as z:
            for n,v in files.items():z.writestr(n,v)
    result=verify_import(source,archive,alignment)
    assert result['status']==('failed' if fault else 'passed'),result

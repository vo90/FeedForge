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


def tied_source(first, second):
    return raw_score([measure(beat(duration=(1,2), fret=9, **first),
                              beat(duration=(1,2), fret=9, tie=True, **second))])


@pytest.mark.parametrize('first,second,rule', [
    ({'harmonic':'pinch','harmonicFret':5}, {'harmonic':'pinch','harmonicFret':12}, 'initial-target-continued'),
    ({'harmonic':'tapped','harmonicFret':7}, {'harmonic':'artificial','harmonicFret':7}, 'same-pitch'),
    ({}, {'harmonic':'feedback','harmonicFret':3.2}, 'feedback-optional'),
    ({}, {'harmonic':'artificial','harmonicFret':7}, 'initial-target-continued'),
])
def test_fallback_preserves_attack_bend_timing_and_literal_source(first,second,rule):
    source=tied_source(first, {**second, 'bend':{'points':[{'position':0,'tone':0},{'position':60,'tone':100}]}})
    original=deepcopy(source)
    actual=render(parse(source))
    verified=expected(songsterr(source),{'offset':0,'scale':1})
    a=actual['tracks'][0]['notes']; b=verified['parts'][0]['notes']
    assert len(a)==len(b)==1 and a[0]['sus']==2
    for key in ('f','s','t','sus','harmonic_target','hp','bnv','bn'):
        assert a[0].get(key)==b[0]['note'].get(key)
    assert a[0]['bnv']==[{'t':1,'v':0},{'t':2,'v':2}]
    assert actual['harmonicTieEvidence']==verified['harmonic_ties']
    assert actual['harmonicTieEvidence'][0]['rule']==rule
    assert source==original


def test_omitted_harmonic_inherits_and_untied_note_really_rearticulates():
    source=tied_source({'harmonic':'pinch','harmonicFret':5},{})
    assert 'harmonicTieEvidence' not in render(parse(source))
    raw=source['parts'][0]['measures'][0]['voices'][0]['beats'][1]['notes'][0]
    raw.update(tie=False,harmonic='pinch',harmonicFret=12)
    actual=render(parse(source))
    assert len(actual['tracks'][0]['notes'])==2
    assert [n['harmonic_target']['node'] for n in actual['tracks'][0]['notes']]==[5,12]


def test_fallback_does_not_repair_a_broken_tie():
    source=tied_source({'harmonic':'pinch','harmonicFret':5},{'harmonic':'pinch','harmonicFret':12})
    source['parts'][0]['measures'][0]['voices'][0]['beats'][1]['notes'][0]['fret']=10
    with pytest.raises(ValueError,match='tie'): render(parse(source))
    with pytest.raises(ValueError,match='tie'): expected(songsterr(source),{'offset':0,'scale':1})


@pytest.mark.parametrize('fault',[None,'missing','source','target','time','report','attack','shape','extra'])
def test_archive_requires_independent_tie_evidence(tmp_path,fault):
    from test_song_import_builder import inputs
    from feedback_converter.song_import import load_performance
    from feedback_converter.song_import.builder import build_feedpak
    from feedback_converter.song_import.verification import verify_import
    _,audio,_,job=inputs(tmp_path)
    source=tied_source({'harmonic':'pinch','harmonicFret':5},{'harmonic':'pinch','harmonicFret':12})
    path=tmp_path/'source.json'; path.write_text(json.dumps(source),encoding='utf-8')
    performance=load_performance(path)
    alignment={'status':'validated','offset':.25,'scale':1.25}
    built=build_feedpak(performance,audio,alignment,job,output_dir=tmp_path/'out',source_path=path,
        compatibility=performance['compatibilityReport'],recipe={'preservationContract':21})
    archive=Path(built['stagingPath'])
    assert verify_import(path,archive,alignment)['status']=='passed'
    with ZipFile(archive) as z: files={n:z.read(n) for n in z.namelist()}
    evidence=json.loads(files['import/tied-harmonics.json'])
    if fault=='missing': files.pop('import/tied-harmonics.json')
    if fault=='source': evidence['sourceSha256']='0'*64
    if fault=='target': evidence['continuations'][0]['used']['harmonic_target']['node']=12
    if fault=='time': evidence['continuations'][0]['start']+=.1
    if fault=='shape': evidence['continuations'][0]=None
    if fault=='extra': evidence['continuations'][0]['unexpected']=True
    if fault in ('source','target','time','shape','extra'): files['import/tied-harmonics.json']=json.dumps(evidence).encode()
    if fault=='report':
        report=json.loads(files['import/compatibility.json']);report['findings']=[];report['findingCount']=0;report['status']='compatible'
        files['import/compatibility.json']=json.dumps(report).encode()
    if fault=='attack':
        manifest=yaml.safe_load(files['manifest.yaml']); name=manifest['arrangements'][0]['file']
        chart=json.loads(files[name]);chart['notes'].append(deepcopy(chart['notes'][0]));files[name]=json.dumps(chart).encode()
    mutated=tmp_path/'checked.feedpak'
    with ZipFile(mutated,'w') as z:
        for name,data in files.items():z.writestr(name,data)
    result=verify_import(path,mutated,alignment)
    json.dumps(result)
    assert result['status']==('passed' if fault is None else 'failed'),result

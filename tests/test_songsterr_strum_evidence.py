from copy import deepcopy
import json
from pathlib import Path
from zipfile import ZipFile
import pytest
import yaml
from test_song_import_score import beat,measure,raw_score
from feedback_converter.song_import import load_performance
from feedback_converter.song_import.builder import build_feedpak
from feedback_converter.song_import.verification import verify_import
from feedback_converter.song_import.verify_source import songsterr
from feedback_converter.song_import.verify_timeline import expected


def document(direction='down',shift=100):
    b=beat(fret=3);b['notes'] += [{'string':1,'fret':5},{'string':2,'fret':0}]
    b['brushStroke']={'direction':direction,'duration':30,'shift':shift}
    return raw_score([measure(beat()),measure(b,repeatStart=True,repeat=2)])


@pytest.mark.parametrize('direction',['up','down'])
@pytest.mark.parametrize('shift',[0,50,100])
def test_repeated_shifted_strums_match_independent_source_clock(tmp_path,direction,shift):
    raw=document(direction,shift);path=tmp_path/'source.json';path.write_text(json.dumps(raw),encoding='utf-8')
    p=load_performance(path);v=expected(songsterr(raw),{'offset':0,'scale':1})
    assert p['strumEvidence']==v['strums']
    assert len(v['strums'])==2 and [r['occurrence'] for r in v['strums']]==[2,3]
    assert all(r['direction']==direction and len(r['notes'])==3 for r in v['strums'])


@pytest.mark.parametrize('fault',[None,'rounding_early','rounding_late','note_time','time','direction','missing','member','count','source','display_group','missing_group'])
def test_piecewise_archive_requires_source_bound_exact_members(tmp_path,fault):
    from test_song_import_builder import inputs
    _,audio,_,job=inputs(tmp_path)
    raw=document();path=tmp_path/'source.json';path.write_text(json.dumps(raw),encoding='utf-8')
    p=load_performance(path)
    alignment={'status':'validated','mapping':'piecewise-linear','anchors':[{'score':0,'audio':0},{'score':2,'audio':2.1},{'score':6,'audio':7}]}
    alignment['tempos']=[{'time':0,'bpm':120/1.05},{'time':2.1,'bpm':120/1.225}]
    built=build_feedpak(p,audio,alignment,job,output_dir=tmp_path/'out',source_path=path,compatibility=p['compatibilityReport'],recipe={'preservationContract':26})
    archive=Path(built['stagingPath']);assert verify_import(path,archive,alignment)['status']=='passed'
    with ZipFile(archive) as z:files={n:z.read(n) for n in z.namelist()}
    m=yaml.safe_load(files['manifest.yaml']);chart_file=m['arrangements'][0]['file']
    chart=json.loads(files[chart_file])
    grouped=[n for n in chart['notes'] if 'ch' in n]
    assert len(grouped)==6 and len({n['ch'] for n in grouped})==2
    assert len({n['t'] for n in grouped})==6, 'Per-string attack times must stay staggered.'
    if fault=='display_group':grouped[0]['ch']=999
    if fault=='missing_group':grouped[0].pop('ch')
    if fault in {'rounding_early','rounding_late','note_time'}:
        delta={'rounding_early':-.000001,'rounding_late':.000001,'note_time':.000002}[fault]
        grouped[0]['t']=round(grouped[0]['t']+delta,6)
        # Keep the integrity receipt consistent with the serialized chart.
        # Independent raw-source comparisons must still reject a real change.
        from feedback_converter.chart_guidance import finalize
        finalize(chart,regenerate=True)
    files[chart_file]=json.dumps(chart).encode()
    r=json.loads(files['import/strums.json'])
    if fault=='time':r['groups'][0]['notes'][1]['t']+=.01
    if fault=='direction':r['groups'][0]['direction']='up'
    if fault=='member':r['groups'][0]['notes'][1]['f']=99
    if fault=='count':r['groups'].pop()
    if fault=='source':r['sourceSha256']='0'*64
    files['import/strums.json']=json.dumps(r).encode()
    if fault=='missing':
        m=yaml.safe_load(files['manifest.yaml']);m['song_import'].pop('strumsFile');files['manifest.yaml']=yaml.safe_dump(m).encode()
    changed=tmp_path/'changed.feedpak'
    with ZipFile(changed,'w') as z:
        for name,data in files.items():z.writestr(name,data)
    report=verify_import(path,changed,alignment)
    assert report['status']==('passed' if fault in {None,'rounding_early','rounding_late'} else 'failed'), report['errors']
    if fault in {'display_group','missing_group'}:
        assert any(e['code']=='strum_group' for e in report['errors'])


def test_only_complete_authored_brushes_get_display_groups():
    from feedback_converter.song_import.strum_groups import attach
    notes=[{'t':1,'s':0,'f':0,'sus':.4},{'t':1.02,'s':1,'f':3,'sus':.38}]
    group={'trackId':'a','kind':'brush','notes':deepcopy(notes)}
    for kind in ['brush','arpeggio',None]:
        chart={'notes':deepcopy(notes)}
        attach(chart,'a',[dict(group,kind=kind)],{'offset':0,'scale':1})
        assert all(('ch' in n)==(kind=='brush') for n in chart['notes'])
        assert [{k:v for k,v in n.items() if k!='ch'} for n in chart['notes']]==notes
    for selected in [notes[:1],notes+[deepcopy(notes[0])]]:
        chart={'notes':deepcopy(selected)}
        attach(chart,'a',[group],{'offset':0,'scale':1})
        assert not any('ch' in n for n in chart['notes'])


@pytest.mark.parametrize('fault',[None,'ambiguous','wrong_string','wrong_fret','missing','reused','chord_child'])
def test_strum_rounding_matching_is_unique_and_source_bound(fault):
    from feedback_converter.song_import.verification import Check, _strum_groups
    from feedback_converter.song_import.strum_groups import POLICY
    # Two genuinely different brushes can have the same shape. Their members
    # must remain distinct even when each timestamp differs by one microsecond.
    groups=[{'trackId':'a','kind':'brush','notes':[
        {'t':start,'s':0,'f':3},{'t':start+.02,'s':1,'f':5}]} for start in (1.,1.04)]
    chart={'notes':[dict(n,t=round(n['t']+.000001,6),ch=i)
                    for i,g in enumerate(groups) for n in g['notes']], 'chords':[]}
    if fault=='ambiguous':chart['notes'].append(dict(chart['notes'][0],t=1.))
    if fault=='wrong_string':chart['notes'][0]['s']=2
    if fault=='wrong_fret':chart['notes'][0]['f']=4
    if fault=='missing':chart['notes'].pop(0)
    if fault=='reused':groups.append(deepcopy(groups[0]))
    if fault=='chord_child':
        # A chord member cannot borrow the identity of an unrelated loose note.
        chart['chords']=[{'t':chart['notes'][0]['t'],'notes':[deepcopy(chart['notes'][0])]}]
    check=Check()
    _strum_groups(groups,chart,check,'a',{'offset':0,'scale':1},POLICY)
    assert bool(check.errors)==(fault is not None)

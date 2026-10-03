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
from feedback_converter.harmonic_changes import validate_changes


def document(extra=None):
    return raw_score([measure(beat(duration=(1,4),fret=14),
        beat(duration=(1,4),fret=14,tie=True,harmonic='artificial',harmonicFret=7,**(extra or {})),
        beat(duration=(1,2),fret=14,tie=True))])


def test_worker_advertises_the_contact_to_consumers(tmp_path, monkeypatch):
    from test_song_import_builder import inputs
    from feedback_converter.song_import import worker
    inputs(tmp_path)
    path=tmp_path/'source.json';path.write_text(json.dumps(document()),encoding='utf-8')
    # Synthetic fixture: synchronization itself is exercised by the real-song
    # acceptance run, while this checks the full worker's published contract.
    monkeypatch.setattr(worker, '_choose_alignment', lambda *args: {'status':'validated','offset':0,'scale':1})
    result=worker.run_import({'scorePath':str(path),'workDir':str(tmp_path/'work'),
        'outputDir':str(tmp_path/'out'),'audio':{'kind':'file','path':str(tmp_path/'input.wav')},'artworkLookup':False})
    assert result['ok'],result
    with ZipFile(result['stagingPath']) as z:
        recipe=yaml.safe_load(z.read('manifest.yaml'))['song_import']
    assert 'harmonic_changes' in recipe['compatibility']['extensions']
    assert recipe['compatibility']['status']=='requires_consumer_support'


def test_contact_owns_its_remaining_sustain_not_the_initial_attack():
    source=document({'bend':{'points':[{'position':0,'tone':0},{'position':60,'tone':100}]}})
    before=deepcopy(source); p=render(parse(source));v=expected(songsterr(source),{'offset':0,'scale':1})
    n=p['tracks'][0]['notes'][0];check=v['parts'][0]['notes'][0]['note']
    assert len(p['tracks'][0]['notes'])==len(v['parts'][0]['notes'])==1
    assert 'harmonic_target' not in n and not n.get('hp')
    assert n['harmonic_changes']==check['harmonic_changes']
    event=n['harmonic_changes']['events'][0]
    assert (event['start'],event['end'],n['f']+event['target']['node'])==(.5,2,21)
    assert n['bnv']==check['bnv']==[{'t':0,'v':0},{'t':.5,'v':0},{'t':1,'v':2}]
    assert validate_changes(n)==n['harmonic_changes'] and source==before


@pytest.mark.parametrize('intervening',[{'harmonic':'feedback','harmonicFret':12}, {'harmonic':'pinch','harmonicFret':5}])
def test_conflicting_continuation_prevents_later_touch_inference(intervening):
    source=raw_score([measure(beat(duration=(1,4),fret=14),beat(duration=(1,4),fret=14,tie=True,**intervening),
        beat(duration=(1,2),fret=14,tie=True,harmonic='artificial',harmonicFret=7))])
    assert 'harmonic_changes' not in render(parse(source))['tracks'][0]['notes'][0]
    assert 'harmonic_changes' not in expected(songsterr(source),{'offset':0,'scale':1})['parts'][0]['notes'][0]['note']


def test_later_ambiguous_pitch_keeps_established_contact():
    source=document();raw=source['parts'][0]['measures'][0]['voices'][0]['beats'][2]['notes'][0]
    raw.update(harmonic='pinch',harmonicFret=12)
    p=render(parse(source));v=expected(songsterr(source),{'offset':0,'scale':1})
    assert p['tracks'][0]['notes'][0]['harmonic_changes']['events'][0]['target']['node']==7
    assert p['harmonicTieEvidence']==v['harmonic_ties']
    assert p['harmonicTieEvidence'][-1]['used']['harmonic_target']['node']==7


def test_repeats_chords_and_fractional_contact_nodes_keep_performed_occurrences():
    source=document();bar=source['parts'][0]['measures'][0];bar.update(repeatStart=True,repeat=2)
    for index,b in enumerate(bar['voices'][0]['beats']):
        second=deepcopy(b['notes'][0]);second['string']=1;second['fret']=0
        if index==1:second['harmonicFret']=3.2
        b['notes'].append(second)
    p=render(parse(source));v=expected(songsterr(source),{'offset':0,'scale':1})
    track=p['tracks'][0];notes=track['notes']+[{**n,'t':c['t']} for c in track['chords'] for n in c['notes']]
    assert len(notes)==4
    assert sum(n.get('harmonic_changes',{}).get('events',[{}])[0].get('target',{}).get('node')==3.2 for n in notes)==2
    assert {r['occurrence'] for r in p['harmonicTieEvidence']}=={1,2}
    assert p['harmonicTieEvidence']==v['harmonic_ties']


def test_audio_end_can_trim_a_held_harmonic_but_cannot_erase_its_contact():
    from feedback_converter.song_import.terminal_sustains import trim_held_note
    from feedback_converter.song_import.audio import ImportFailure
    n=render(parse(document()))['tracks'][0]['notes'][0];before=deepcopy(n)
    out,_=trim_held_note(n,1.5)
    assert out['sus']==out['harmonic_changes']['events'][0]['end']==1.5
    assert out['harmonic_changes']['events'][0]['start']==.5 and n==before
    with pytest.raises(ImportFailure,match='harmonic contact'):trim_held_note(n,.4)


@pytest.mark.parametrize('where',[1,2])
def test_unpositioned_contact_during_slide_uses_documented_fallback(where):
    source=document();source['parts'][0]['measures'][0]['voices'][0]['beats'][where]['notes'][0]['slide']='downwards'
    p=render(parse(source));v=expected(songsterr(source),{'offset':0,'scale':1})
    assert 'harmonic_changes' not in p['tracks'][0]['notes'][0]
    assert p['harmonicTieEvidence']==v['harmonic_ties']
    assert p['harmonicTieEvidence'][0]['rule']=='initial-target-continued'


@pytest.mark.parametrize('fault',[None,'removed','initial','time','target','duplicate','source'])
def test_nonlinear_contact_archive_and_corruption(tmp_path,fault):
    from test_song_import_builder import inputs
    from feedback_converter.song_import import load_performance
    from feedback_converter.song_import.builder import build_feedpak
    from feedback_converter.song_import.verification import verify_import
    _,audio,_,job=inputs(tmp_path)
    path=tmp_path/'source.json';path.write_text(json.dumps(document()),encoding='utf-8')
    p=load_performance(path)
    alignment={'status':'validated','mapping':'piecewise-linear','anchors':[
        {'score':0,'audio':.25},{'score':.25,'audio':.75},{'score':2,'audio':6}],
        'tempos':[{'time':.25,'bpm':60},{'time':.75,'bpm':40}]}
    built=build_feedpak(p,audio,alignment,job,output_dir=tmp_path/'out',source_path=path,
        compatibility=p['compatibilityReport'],recipe={'preservationContract':22})
    archive=Path(built['stagingPath']);verified=verify_import(path,archive,alignment)
    assert verified['status']=='passed',verified
    with ZipFile(archive) as z:files={n:z.read(n) for n in z.namelist()}
    manifest=yaml.safe_load(files['manifest.yaml']);file=manifest['arrangements'][0]['file'];chart=json.loads(files[file]);n=chart['notes'][0]
    assert n['harmonic_changes']['events'][0]['start']==1.25
    if fault=='removed':n.pop('harmonic_changes')
    if fault=='initial':n['harmonic_target']=deepcopy(n['harmonic_changes']['events'][0]['target'])
    if fault=='time':n['harmonic_changes']['events'][0]['start']+=.1
    if fault=='target':n['harmonic_changes']['events'][0]['target']['interval']=12
    if fault=='duplicate':n['harmonic_changes']['events'].append(deepcopy(n['harmonic_changes']['events'][0]))
    if fault=='source':n['harmonic_changes']['events'][0]['source_id']='invented'
    files[file]=json.dumps(chart).encode();mutated=tmp_path/'result.feedpak'
    with ZipFile(mutated,'w') as z:
        for name,data in files.items():z.writestr(name,data)
    checked=verify_import(path,mutated,alignment)
    assert checked['status']==('passed' if fault is None else 'failed'),checked

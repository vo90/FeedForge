from copy import deepcopy
from fractions import Fraction as F
import json
from pathlib import Path
import sys
from zipfile import ZipFile

import pytest
import yaml

from feedback_converter.song_import import load_performance
from feedback_converter.song_import.compatibility import inspect_songsterr
from feedback_converter.song_import.songsterr import parse
from feedback_converter.song_import.verify_source import songsterr
from feedback_converter.song_import.verify_timeline import expected
from feedback_converter.song_import.verification import verify_import
from feedback_converter.song_import.builder import build_feedpak
from test_song_import_score import raw_score, measure, beat

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.songsterr_compatibility.audit import evaluate, compare_preparation, compare_authored_events

FIXTURE=json.loads(Path(__file__).with_name('fixtures').joinpath('songsterr_legacy_dotted_reference.json').read_text())


@pytest.mark.parametrize('case',FIXTURE['cases'],ids=lambda c:c['id'])
def test_legacy_dots_match_pinned_player_clock_and_notation(case):
    source=deepcopy(case['source']); original=deepcopy(source)
    actual=evaluate(source)
    assert actual['converter']['status']==actual['independent']['status']=='rendered',actual
    assert compare_preparation(actual,case['reference'])==[]
    assert compare_authored_events(actual,case['reference'],source)==[]
    written=parse(source).tracks[0].written_bars[0][0].beats[0]
    assert written.dots==case['dots']
    assert written.written_duration==F(*source['parts'][0]['measures'][0]['voices'][0]['beats'][0]['duration'])*4
    assert written.tuplet==((3,2) if case['shape']=='tuplet' else None)
    independent=songsterr(source)
    assert independent.parts[0].beats[0][0]['notation']['dot']==case['dots']
    assert source==original


@pytest.mark.parametrize('fields',[
    {'dotted':1},{'dotted':'true'},{'dotted':[]},{'dots':True},{'dots':'1'},
    {'dots':-1},{'dots':1.5},{'dots':5},{'dots':float('inf')},
    {'dots':1,'dotted':'false'},{'dots':-1,'dotted':True},
])
def test_malformed_dot_fields_never_become_silent_fallbacks(fields):
    b={**beat(), 'type':1, **fields}; source=raw_score([measure(b)])
    report=inspect_songsterr(source)
    assert report['status']=='blocked'
    assert any(f['category']=='source_structure' and f['feature'] in ('beat.dotted','beat.dots') for f in report['findings'])
    with pytest.raises(ValueError):parse(source)
    with pytest.raises(ValueError):songsterr(source)


@pytest.mark.parametrize('initial',[False,True])
def test_legacy_before_beat_grace_keeps_exact_budget(initial):
    grace={**beat(5,duration=(3,4)),'type':2,'dotted':True,'graceNote':'beforeBeat'}
    bars=[measure(grace,{**beat(7),'type':1})]
    if not initial:bars.insert(0,measure({**beat(3),'type':1}))
    source=raw_score(bars); modern=deepcopy(source)
    target=modern['parts'][0]['measures'][-1]['voices'][0]['beats'][0]
    del target['dotted'];target['dots']=1
    old,new=evaluate(source),evaluate(modern)
    assert old['converter']['status']==old['independent']['status']=='rendered'
    assert old['sourceTrace']==new['sourceTrace']
    for stage in ('converter','independent'):
        # Source inventory differs, so compare musical expected events directly.
        assert old[stage]['events']==new[stage]['events']
    assert expected(songsterr(source),{'offset':0,'scale':1})['parts'][0]['notes']==expected(songsterr(modern),{'offset':0,'scale':1})['parts'][0]['notes']


def test_dotted_rest_is_not_an_undotted_whole_bar_or_pickup():
    r={'rest':True,'type':1,'dotted':True,'duration':[1,1],'notes':[{'rest':True}]}
    source=raw_score([measure(r,signature=[3,4]),measure(beat())])
    with pytest.raises(ValueError):parse(source)
    with pytest.raises(ValueError):songsterr(source)
    source['parts'][0]['anacrusis']=True
    with pytest.raises(ValueError):parse(source)
    with pytest.raises(ValueError):songsterr(source)


@pytest.mark.parametrize('corruption',[None,'attack','sustain','notation'])
def test_archive_keeps_source_and_independently_rejects_dot_corruption(tmp_path,corruption):
    from test_song_import_builder import inputs
    _,audio,alignment,job=inputs(tmp_path)
    source=raw_score([measure({**beat(5,duration=(3,8)),'type':4,'dotted':True},
                              beat(7,duration=(1,8)),beat(9,duration=(1,2)))])
    path=tmp_path/'source.json'; path.write_text(json.dumps(source))
    p=load_performance(path)
    built=build_feedpak(p,audio,alignment,job,output_dir=tmp_path/'out',source_path=path,
                       compatibility=p['compatibilityReport'],recipe={'preservationContract':85})
    archive=Path(built['stagingPath'])
    with ZipFile(archive) as z:files={n:z.read(n) for n in z.namelist()}
    manifest=yaml.safe_load(files['manifest.yaml']);arrangement=manifest['arrangements'][0]
    assert files[manifest['song_import']['sourceFile']]==path.read_bytes()
    if corruption:
        name=arrangement['notation'] if corruption=='notation' else arrangement['file']
        value=json.loads(files[name])
        if corruption=='notation':value['measures'][0]['staves']['staff']['voices'][0]['beats'][0]['dot']=0
        elif corruption=='attack':value['notes'][1]['t']+=.1
        else:value['notes'][0]['sus']*=1.5
        files[name]=json.dumps(value).encode();archive=tmp_path/'corrupt.feedpak'
        with ZipFile(archive,'w') as z:
            for name,data in files.items():z.writestr(name,data)
    report=verify_import(path,archive,alignment)
    assert report['status']==('failed' if corruption else 'passed'),report

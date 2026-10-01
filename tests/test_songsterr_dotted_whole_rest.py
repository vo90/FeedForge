from copy import deepcopy
from fractions import Fraction as F
import json
import sys
from pathlib import Path
from zipfile import ZipFile

import pytest
import yaml

from feedback_converter.song_import import ScoreImportError, load_performance
from feedback_converter.song_import.verify_source import songsterr
from feedback_converter.song_import.verify_timeline import expected
from feedback_converter.song_import.builder import build_feedpak
from feedback_converter.song_import.verification import verify_import
from test_song_import_score import beat, measure, raw_score, import_json

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.songsterr_compatibility.audit import (evaluate, compare_preparation,
    classify_preparation_differences, compare_authored_events)

REFERENCE = json.loads(Path(__file__).with_name('fixtures').joinpath('songsterr_rest_reference.json').read_text())


@pytest.mark.parametrize('case', REFERENCE['cases'], ids=[c['id'] for c in REFERENCE['cases']])
def test_rest_preparation_and_following_note_match_external_reference(case):
    actual = evaluate(case['source'])
    assert actual['converter']['status'] == actual['independent']['status'] == 'rendered'
    differences = compare_preparation(actual,case['reference'])
    assert classify_preparation_differences(case['source'],differences)[1] == []
    assert compare_authored_events(actual,case['reference'],case['source']) == []


def rest(dots=1):
    d = F(2) - F(1, 2**dots)
    return {'rest': True, 'type': 1, 'dots': dots, 'duration': [d.numerator,d.denominator], 'notes': [{'rest': True}]}


@pytest.mark.parametrize('dots', [1, 2, 3, 4])
@pytest.mark.parametrize('meter', [(2,4),(3,4),(4,4),(6,4),(8,4)])
def test_single_dotted_whole_rest_retains_glyph_and_correct_following_attack(tmp_path, dots, meter):
    source=raw_score([measure(rest(dots),signature=list(meter)),measure(beat())]); before=deepcopy(source)
    p=import_json(tmp_path,source); v=expected(songsterr(source),{'offset':0,'scale':1})
    assert source==before
    assert p['tracks'][0]['notes'][0]['t']==pytest.approx(meter[0]/meter[1]*2)
    assert p['tracks'][0]['notes'][0]['t']==v['parts'][0]['notes'][0]['note']['t']
    if dots <= 2:
        b=p['tracks'][0]['notation']['measures'][0]['staves']['staff']['voices'][0]['beats'][0]
        assert b['rest'] and b['dur']==1 and b['dot']==dots
        assert b['end_time']-b['t']==pytest.approx(float(min(F(4*meter[0],meter[1]), (F(2)-F(1,2**dots))*4))/2)
    else:
        assert not p['tracks'][0].get('notation')
        assert any('outside FeedPak notation v1' in w for w in p['warnings'])


@pytest.mark.parametrize('bad', ['note','additional_rest','inconsistent_dots','tuplet','grace','wrong_type'])
def test_overflow_exception_is_not_a_general_truncation_rule(tmp_path,bad):
    r=rest(); beats=[r]
    if bad=='note': r.pop('rest');r['notes']=[{'string':0,'fret':5}]
    elif bad=='additional_rest': beats.append(deepcopy(r))
    elif bad=='inconsistent_dots': r['duration']=[1,1]
    elif bad=='tuplet': r['tuplet']=3
    elif bad=='grace': r['graceNote']='onBeat'
    else:r['type']=2
    source=raw_score([measure(*beats,signature=[2,4])])
    with pytest.raises((ScoreImportError,ValueError)):import_json(tmp_path,source)
    with pytest.raises(ValueError):songsterr(source)


def test_repeat_and_other_voice_keep_their_notes(tmp_path):
    m=measure(rest(),signature=[3,4],repeatStart=True,repeat=2)
    m['voices'].append({'beats':[beat(duration=(3,4))]})
    source=raw_score([m,measure(beat())])
    p=import_json(tmp_path,source)
    assert [n['t'] for n in p['tracks'][0]['notes']]==[0,1.5,3]
    assert [n['note']['t'] for n in expected(songsterr(source),{'offset':0,'scale':1})['parts'][0]['notes']]==[0,1.5,3]


@pytest.mark.parametrize('corrupt', [False, True])
def test_archive_independently_checks_next_attack(tmp_path,corrupt):
    from test_song_import_builder import inputs
    _,audio,alignment,job=inputs(tmp_path)
    source=raw_score([measure(rest()),measure(beat())]); path=tmp_path/'source.json';path.write_text(json.dumps(source))
    p=load_performance(path)
    built=build_feedpak(p,audio,alignment,job,output_dir=tmp_path/'out',source_path=path,compatibility=p['compatibilityReport'],recipe={'preservationContract':42})
    archive=Path(built['stagingPath'])
    if corrupt:
        with ZipFile(archive) as z: files={n:z.read(n) for n in z.namelist()}
        m=yaml.safe_load(files['manifest.yaml']); chartfile=m['arrangements'][0]['file'];chart=json.loads(files[chartfile])
        chart['notes'][0]['t']+=.1
        files[chartfile]=json.dumps(chart).encode();archive=tmp_path/'corrupt.feedpak'
        with ZipFile(archive,'w') as z:
            for n,data in files.items():z.writestr(n,data)
    report=verify_import(path,archive,alignment)
    assert report['status']==('failed' if corrupt else 'passed'),report.get('errors')

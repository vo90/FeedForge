from copy import deepcopy

import pytest

from test_song_import_score import beat, measure, raw_score
from feedback_converter.song_import.songsterr import parse
from feedback_converter.song_import.timeline import render
from feedback_converter.song_import.verify_source import songsterr
from feedback_converter.song_import.verify_timeline import expected
from feedback_converter.song_import.compatibility import inspect_songsterr
from feedback_converter.harmonic_target import valid_note_target


@pytest.mark.parametrize('kind,policy', [('pinch','harmonic'),('artificial','harmonic'),
    ('tapped','harmonic'),('semi','mixed'),('feedback','attack_either')])
@pytest.mark.parametrize('fret,node,interval', [(12,7,19),(7,12,12),(9,5,24),(5,2.4,36)])
def test_explicit_fretted_harmonics_preserve_pitch_and_source(kind,policy,fret,node,interval):
    source=raw_score([measure(beat(fret=fret,harmonic=kind,harmonicFret=node))])
    before=deepcopy(source)
    assert inspect_songsterr(source)['status'] != 'blocked'
    actual=render(parse(source))['tracks'][0]
    checked=expected(songsterr(source),{'offset':0,'scale':1})['parts'][0]['notes'][0]['note']
    note=actual['notes'][0]
    target={'kind':kind,'node':node,'interval':interval,'policy':policy}
    assert note['harmonic_target'] == checked['harmonic_target'] == target
    assert note['f'] == fret and valid_note_target(note)
    assert bool(note.get('hp')) == (kind in ('pinch','semi'))
    assert not note.get('hm') and 'hn' not in note and 'hps' not in note
    written=actual['notation']['measures'][0]['staves']['staff']['voices'][0]['beats'][0]['notes'][0]
    assert written['midi'] == 64 + fret + interval
    assert before == source


def test_approved_natural_alias_keeps_authored_position_and_effects():
    source=raw_score([measure(beat(fret=15,harmonic='natural',harmonicFret=15,
                                    ghost=True,slide='downwards'))])
    before=deepcopy(source)
    actual=render(parse(source))['tracks'][0]['notes'][0]
    checked=expected(songsterr(source),{'offset':0,'scale':1})['parts'][0]['notes'][0]['note']
    for key in ('f','hn','hps','ghost','harmonic_alias','slide_out_marks'):
        assert actual[key] == checked[key]
    assert (actual['f'],actual['hn'],actual['hps']) == (15,14.7,34)
    assert actual['harmonic_alias'] == 'songsterr-natural-15'
    assert inspect_songsterr(source)['status'] == 'limitations'
    assert source == before


@pytest.mark.parametrize('title', ['November Rain', 'Unrelated song'])
@pytest.mark.parametrize('fret,node,pitch', [(3,3.2,31),(15,14.7,34),(15,15,34),(22,21.7,34)])
def test_precise_positions_and_verified_aliases_are_song_independent(title,fret,node,pitch):
    source=raw_score([measure(beat(fret=fret,harmonic='natural',harmonicFret=node))])
    source['title']=title
    note=render(parse(source))['tracks'][0]['notes'][0]
    assert note['f']==fret
    assert note['hn']==(14.7 if node==15 else node)
    assert note['hps']==pitch


@pytest.mark.parametrize('kind', ['pinch','artificial','tapped','semi','feedback'])
def test_ties_do_not_create_extra_harmonic_attacks(kind):
    source=raw_score([measure(beat(duration=(1,2),fret=7,harmonic=kind,harmonicFret=12),
                              beat(duration=(1,2),fret=7,tie=True,harmonic=kind,harmonicFret=12))])
    actual=render(parse(source))['tracks'][0]['notes']
    checked=expected(songsterr(source),{'offset':0,'scale':1})['parts'][0]['notes']
    assert len(actual) == len(checked) == 1
    assert actual[0]['sus'] == checked[0]['note']['sus']
    source['parts'][0]['measures'][0]['voices'][0]['beats'][1]['notes'][0]['harmonicFret']=7
    kept=render(parse(source))
    verified=expected(songsterr(source),{'offset':0,'scale':1})
    assert kept['tracks'][0]['notes'][0]['harmonic_target']['node']==12
    assert verified['parts'][0]['notes'][0]['note']['harmonic_target']['node']==12
    assert kept['harmonicTieEvidence'][0]['authored']['harmonic_target']['node']==7


@pytest.mark.parametrize('kind,node', [('natural',15.1),('artificial',15),('semi',None),('feedback',3.1)])
def test_unverified_positions_are_not_guessed(kind,node):
    source=raw_score([measure(beat(fret=15,harmonic=kind,harmonicFret=node))])
    with pytest.raises(ValueError): parse(source)
    with pytest.raises(ValueError): songsterr(source)


@pytest.mark.parametrize('field,value', [('interval',True),('policy','attack_either'),('node',15),('kind','feedback')])
def test_contract_rejects_inconsistent_policy_or_pitch(field,value):
    note={'f':7,'harmonic_target':{'kind':'artificial','node':12,'interval':12,'policy':'harmonic'}}
    assert valid_note_target(note)
    note['harmonic_target'][field]=value
    assert not valid_note_target(note)


@pytest.mark.parametrize('kind,policy', [('pinch','harmonic'),('artificial','harmonic'),
    ('tapped','harmonic'),('semi','mixed'),('feedback','attack_either')])
@pytest.mark.parametrize('fault', [None,'lost','pitch','node','policy','kind','time','duration'])
def test_known_answer_archive_rejects_harmonic_corruption(tmp_path,kind,policy,fault):
    from test_song_import_verification import example,verify
    source,package=example()
    raw=source['parts'][0]['measures'][0]['voices'][0]['beats'][2]['notes'][0]
    raw.update(harmonic=kind,harmonicFret=12)
    note=package['chart.json']['notes'][2]
    note['harmonic_target']={'kind':kind,'node':12,'interval':12,'policy':policy}
    if kind in ('pinch','semi'):note['hp']=True
    written=package['notation.json']['measures'][0]['staves']['staff']['voices'][0]['beats'][2]['notes'][0]
    written['midi']=59
    assert verify(tmp_path,source,package)['status']=='passed'
    if fault=='lost':note.pop('harmonic_target')
    if fault=='pitch':note['harmonic_target']['interval']=24
    if fault=='node':note['harmonic_target']['node']=7
    if fault=='policy':note['harmonic_target']['policy']='unscored'
    if fault=='kind':note['harmonic_target']['kind']='natural'
    if fault=='time':note['t']+=.1
    if fault=='duration':note['sus']+=.1
    result=verify(tmp_path,source,package)
    assert result['status']==('passed' if fault is None else 'failed'),result


@pytest.mark.parametrize('kind', ['pinch','artificial','tapped','semi','feedback','natural'])
def test_built_package_retains_the_interpretation_and_verifies(tmp_path,kind):
    import json
    from pathlib import Path
    from test_song_import_builder import inputs
    from feedback_converter.song_import import load_performance
    from feedback_converter.song_import.builder import build_feedpak
    from feedback_converter.song_import.verification import verify_import
    _,audio,_,job=inputs(tmp_path)
    source=raw_score([measure(beat(fret=15 if kind=='natural' else 7,
                                   harmonic=kind,harmonicFret=15 if kind=='natural' else 12))])
    path=tmp_path/'source.json';path.write_text(json.dumps(source),encoding='utf-8')
    performance=load_performance(path);alignment={'status':'validated','offset':0,'scale':1}
    built=build_feedpak(performance,audio,alignment,job,output_dir=tmp_path/'out',source_path=path,
        compatibility=performance['compatibilityReport'],recipe={'preservationContract':17})
    result=verify_import(path,Path(built['stagingPath']),alignment)
    assert result['status']=='passed',result

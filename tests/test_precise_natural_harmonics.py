from copy import deepcopy
import pytest
from test_song_import_score import beat, measure, raw_score
from feedback_converter.song_import.songsterr import parse
from feedback_converter.song_import.timeline import render
from feedback_converter.song_import.verify_source import songsterr
from feedback_converter.song_import.verify_timeline import expected
from feedback_converter.song_import.compatibility import inspect_songsterr


@pytest.mark.parametrize('fret,node,pitch', [(2,2.4,36),(3,2.7,34),(3,3.2,31),
    (6,5.8,34),(8,8.2,36),(10,9.6,34),(15,14.7,34),(17,17,36),(22,21.7,34),(24,24,24)])
@pytest.mark.parametrize('capo,drop', [(0,0),(2,-2)])
def test_precise_target_preserves_source_and_independently_verifies(fret,node,pitch,capo,drop):
    source = raw_score([measure(beat(fret=fret,harmonic='natural',harmonicFret=node))],
                       tuning=[64+drop,59+drop,55+drop,50+drop,45+drop,40+drop])
    source['parts'][0]['capo'] = capo
    original = deepcopy(source)
    assert inspect_songsterr(source)['status'] != 'blocked'
    actual = render(parse(source))['tracks'][0]
    checked = expected(songsterr(source), {'offset':0,'scale':1})['parts'][0]
    n = actual['notes'][0]
    assert (n['f'],n['hn'],n['hps']) == (fret,node,pitch)
    for key in ('f','hn','hps','hm','t','sus'):
        assert n[key] == checked['notes'][0]['note'][key]
    written = actual['notation']['measures'][0]['staves']['staff']['voices'][0]['beats'][0]['notes'][0]
    assert written['midi'] == 64 + drop + capo + pitch
    assert source == original


def test_only_float_noise_is_normalized():
    source=raw_score([measure(beat(harmonic='natural',harmonicFret=3.1999999999999993))])
    assert render(parse(source))['tracks'][0]['notes'][0]['hn'] == 3.2
    assert source['parts'][0]['measures'][0]['voices'][0]['beats'][0]['notes'][0]['harmonicFret'] != 3.2
    for wrong in (3.1,3.19,3.21,float('nan'),True,'3.2'):
        source['parts'][0]['measures'][0]['voices'][0]['beats'][0]['notes'][0]['harmonicFret']=wrong
        with pytest.raises(ValueError): parse(source)


@pytest.mark.parametrize('lost', ['hn','hps'])
def test_archive_verification_rejects_lost_node_or_pitch(tmp_path,lost):
    from test_song_import_verification import example,verify
    source,package=example()
    raw=source['parts'][0]['measures'][0]['voices'][0]['beats'][2]['notes'][0]
    raw.pop('ghost');raw.update(fret=3,harmonic='natural',harmonicFret=3.2)
    n=package['chart.json']['notes'][2]
    n.pop('ghost');n.update(f=3,hm=True,hn=3.2,hps=31)
    written=package['notation.json']['measures'][0]['staves']['staff']['voices'][0]['beats'][2]['notes'][0]
    written.pop('ghost');written.update(fret=3,midi=71)
    assert verify(tmp_path,source,package)['status']=='passed'
    n.pop(lost)
    assert verify(tmp_path,source,package)['status']=='failed'


@pytest.mark.parametrize('second_node', [3.2,2.7])
def test_ties_keep_one_target_and_cannot_silently_change_it(second_node):
    source=raw_score([measure(beat(duration=(1,2),harmonic='natural',harmonicFret=3.2),
                             beat(duration=(1,2),tie=True,harmonic='natural',harmonicFret=second_node))])
    if second_node==3.2:
        actual=render(parse(source))['tracks'][0]['notes']
        assert len(actual)==1 and actual[0]['hn']==3.2 and actual[0]['hps']==31
        assert len(expected(songsterr(source),{'offset':0,'scale':1})['parts'][0]['notes'])==1
    else:
        actual=render(parse(source))
        verified=expected(songsterr(source),{'offset':0,'scale':1})
        assert actual['tracks'][0]['notes'][0]['hn']==3.2
        assert verified['parts'][0]['notes'][0]['note']['hn']==3.2
        assert actual['harmonicTieEvidence'][0]['authored']['hn']==2.7
        assert actual['harmonicTieEvidence'][0]['rule']=='initial-target-continued'

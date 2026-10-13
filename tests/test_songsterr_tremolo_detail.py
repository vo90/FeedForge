from copy import deepcopy
import json
import pytest
from feedback_converter.song_import.songsterr import parse
from feedback_converter.song_import.timeline import render
from feedback_converter.song_import.builder import _retime_note
from feedback_converter.song_import.verify_source import read_source
from feedback_converter.song_import.verify_timeline import expected
from feedback_converter.song_import.verify_tremolo import attach
from feedback_converter.song_import.verification import Check, _notes
from test_song_import_score import raw_score, measure, beat


def compare(tmp_path, doc, mapping=None):
    mapping = mapping or {'scale':1, 'offset':0}
    path = tmp_path/'source.json'; path.write_text(json.dumps(doc), encoding='utf8')
    perf = render(parse(doc)); source = read_source(path)
    wanted = expected(source, mapping); attach(source, wanted, mapping, 100)
    actual = []
    for track, part in zip(perf['tracks'], wanted['parts']):
        notes = track['notes'] + [{**n,'t':n.get('t',c['t'])} for c in track['chords'] for n in c['notes']]
        notes = sorted([_retime_note(n,mapping,100) for n in notes], key=lambda n:(n['t'],n['s']))
        check = Check(); _notes(part['notes'],[(n,str(i)) for i,n in enumerate(notes)],check,part['source'],100)
        assert not check.errors, check.errors
        actual.extend(notes)
    return actual, wanted


@pytest.mark.parametrize('rate,period', [([1,8],.25),([1,16],.125),([1,32],.0625),([2,32],.125),(True,None)])
def test_rate_does_not_add_attacks_or_change_duration(tmp_path,rate,period):
    notes,_ = compare(tmp_path,raw_score([measure(beat(tremolo=rate))]))
    assert len(notes)==1 and notes[0]['sus']==2
    mark=notes[0]['tremolo_marks'][0]
    assert mark['start']==0 and mark['end']==2 and mark['period']==period
    assert mark['mode']==('unspecified' if rate is True else 'specified')


@pytest.mark.parametrize('first,last,start,modes', [([1,16],None,0,['specified']),
    (None,[1,16],1,['specified']),([1,16],[1,16],0,['specified','specified']),
    ([1,16],[1,32],0,['specified','conflicting'])])
def test_tied_instruction_ownership(tmp_path,first,last,start,modes):
    doc=raw_score([measure(beat(duration=(1,2),tremolo=first),beat(duration=(1,2),tie=True,tremolo=last))])
    ns,_=compare(tmp_path,doc)
    assert len(ns)==1 and ns[0]['sus']==2
    marks=ns[0]['tremolo_marks']
    assert marks[0]['start']==start and marks[-1]['end']==2
    assert [m['mode'] for m in marks]==modes


def test_tempo_and_recording_map_split_rate_without_new_attacks(tmp_path):
    doc=raw_score([measure(beat(tremolo=[1,16])),measure(beat(tie=True))])
    doc['parts'][0]['automations']['tempo'].append({'measure':1,'position':[0,1],'bpm':60,'type':4})
    mapping={'mapping':'piecewise-linear','anchors':[{'score':0,'audio':2},{'score':1,'audio':3},
              {'score':2,'audio':5},{'score':6,'audio':9}]}
    ns,_=compare(tmp_path,doc,mapping)
    assert len(ns)==1 and ns[0]['t']==2 and ns[0]['sus']==7
    assert [m['period'] for m in ns[0]['tremolo_marks']]==[.125,.25,.25]


@pytest.mark.parametrize('fault',['missing','start','end','rate','phase','period','mode'])
def test_independent_check_rejects_changed_detail(tmp_path,fault):
    notes,wanted=compare(tmp_path,raw_score([measure(beat(tremolo=[1,16]))]))
    if fault=='missing':notes[0].pop('tremolo_marks')
    else:
        m=notes[0]['tremolo_marks'][0]
        m[fault]={'start':.1,'end':1.9,'rate':[1,8],'phase':.25,'period':.25,'mode':'conflicting'}[fault]
    check=Check(); p=wanted['parts'][0]
    _notes(p['notes'],[(n,str(i)) for i,n in enumerate(notes)],check,p['source'],100)
    assert check.errors


def test_repeated_source_uses_each_occurrence(tmp_path):
    ns,_=compare(tmp_path,raw_score([measure(beat(tremolo=[1,32]),repeatStart=True,repeat=2)]))
    assert len(ns)==2 and ns[0]['tremolo_marks']==ns[1]['tremolo_marks']


def test_beat_instruction_has_source_precedence(tmp_path):
    b=beat(tremolo=[1,16]); b['tremolo']=[1,32]
    ns,_=compare(tmp_path,raw_score([measure(b)]))
    assert ns[0]['tremolo_marks'][0]['rate']==[1,32]


def test_package_hybrid_and_difficulties_keep_detail(tmp_path):
    import yaml
    from zipfile import ZipFile
    from test_songsterr_hybrid_lead import build,song
    from feedback_converter.song_import.evidence import CONTRACT_VERSION
    doc=song();doc['parts'][1]['measures'][1]['voices'][0]['beats'][0]['notes'][0]['tremolo']=[1,16]
    # Exercise current full packaging rather than declaring an older lyrics policy.
    *_,archive,report=build(tmp_path,doc,mapped=True,difficulty=True,contract=CONTRACT_VERSION)
    assert report['status']=='passed',report
    with ZipFile(archive) as z:
        manifest=yaml.safe_load(z.read('manifest.yaml'))
        chart=json.loads(z.read(manifest['arrangements'][-1]['file']))
        notes=chart['notes']+[n for c in chart['chords'] for n in c['notes']]
        assert any(n.get('tremolo_marks') for n in notes)


@pytest.mark.parametrize('rate,words', [([1,16],'rates and active spans'),(True,'without an explicit'),([1,64],'cannot be engraved')])
def test_report_only_qualifies_supported_detail(tmp_path,rate,words):
    from test_song_import_score import import_json
    p=import_json(tmp_path,raw_score([measure(beat(tremolo=rate))]))
    assert any(words in f['message'] for f in p['compatibilityReport']['findings'])


def test_ending_crop_preserves_rate_and_phase():
    from feedback_converter.song_import.terminal_sustains import trim_held_note
    note={'t':0,'sus':2,'s':0,'f':5,'tr':True,'tremolo_marks':[
        dict(start=0,end=2,rate=[1,16],period=.125,phase=.3,mode='specified')]}
    adjusted,_=trim_held_note(note,1.5)
    assert adjusted['sus']==1.5 and adjusted['tremolo_marks'][0]['end']==1.5
    assert adjusted['tremolo_marks'][0]['phase']==.3


@pytest.mark.parametrize('bpm',[117,133,137,193])
def test_nonbinary_clock_rounding_does_not_lose_instruction_owners(tmp_path,bpm):
    bars=[measure(beat()) for _ in range(17)]
    bars.append(measure(beat(duration=(1,4),tremolo=[1,32]),beat(duration=(1,4),tremolo=[1,16]),
                        beat(duration=(1,2),tie=True)))
    doc=raw_score(bars);doc['parts'][0]['automations']['tempo'][0]['bpm']=bpm
    ns,_=compare(tmp_path,doc)
    marked=[n for n in ns if n.get('tr')]
    assert len(marked)==2 and all(n['tremolo_marks'] for n in marked)


@pytest.mark.parametrize('bpm', [108, 117, 133, 137, 193])
@pytest.mark.parametrize('bars', [1, 18])
def test_nonbinary_terminal_tremolo_uses_unrounded_source_endpoint(tmp_path, bpm, bars):
    doc = raw_score([measure(beat()) for _ in range(bars-1)] +
                    [measure(beat(tremolo=[1,16]))])
    doc['parts'][0]['automations']['tempo'][0]['bpm'] = bpm
    score_end = bars * 240 / bpm
    mapping = {'mapping':'piecewise-linear', 'anchors':[
        {'score':0, 'audio':2}, {'score':score_end, 'audio':2+score_end*1.13}]}
    notes, _ = compare(tmp_path, doc, mapping)
    note = notes[-1]
    assert note['tremolo_marks'][-1]['end'] == pytest.approx(note['sus'], abs=1.1e-6)


def test_real_out_of_map_tremolo_endpoint_is_still_rejected(tmp_path):
    doc = raw_score([measure(beat(tremolo=[1,16]))])
    mapping = {'mapping':'piecewise-linear', 'anchors':[
        {'score':0, 'audio':2}, {'score':1.99999, 'audio':4}]}
    with pytest.raises(ValueError, match='outside the supplied timing map'):
        compare(tmp_path, doc, mapping)


@pytest.mark.parametrize('period', [.0625, .25, 2])
@pytest.mark.parametrize('seconds,passed', [(.0000005, True), (-.0000005, True),
                                          (.000003, False), (-.000003, False)])
def test_phase_precision_is_measured_in_time_across_cycle_boundary(period, seconds, passed):
    from feedback_converter.song_import.verify_tremolo import compare as compare_marks
    marks = [dict(start=0,end=2,rate=[1,16],period=period,phase=0,mode='specified')]
    changed = deepcopy(marks)
    changed[0]['phase'] = (seconds/period) % 1
    check = Check()
    compare_marks(marks, changed, 2, check, 'test')
    assert (not check.errors) == passed


def test_shared_reference_and_no_tremolo_do_not_recompute_full_score(tmp_path,monkeypatch):
    from feedback_converter.song_import import verify_tremolo
    from types import SimpleNamespace
    def forbidden(*args,**kwargs):raise AssertionError('Redundant full score reconstruction')
    monkeypatch.setattr(verify_tremolo,'expected',forbidden)
    verify_tremolo.attach(SimpleNamespace(format='songsterr'),{'parts':[{'notes':[]}]},{},100)
    path=tmp_path/'source.json';path.write_text(json.dumps(raw_score([measure(beat(tremolo=[1,16]))])))
    source=read_source(path);raw=expected(source,{'scale':1,'offset':0});result=deepcopy(raw)
    verify_tremolo.attach(source,result,{'scale':1,'offset':0},100,raw=raw)
    assert result['parts'][0]['notes'][0]['note']['tremolo_marks']
    assert 'tremolo_marks' not in raw['parts'][0]['notes'][0]['note']

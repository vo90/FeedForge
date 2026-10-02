"""Native event fixtures and independent verification for tied trills."""
from copy import deepcopy
from pathlib import Path
import json
import pytest

from test_songsterr_trills import compare
from feedback_converter.song_import.songsterr import parse
from feedback_converter.song_import.timeline import render
from feedback_converter.song_import.model import ScoreImportError
from feedback_converter.song_import.verify_source import songsterr, UnverifiedFeature
from feedback_converter.song_import.verify_timeline import expected

FIXTURE = json.loads((Path(__file__).parent/'fixtures/songsterr_tied_trill_reference.json').read_text())


@pytest.mark.parametrize('case', FIXTURE['cases'], ids=lambda c:c['id'])
def test_native_event_sequence_or_explicit_ambiguity(case):
    source, wanted = case['source'], case['expected']
    if wanted['issues']:
        with pytest.raises(ScoreImportError, match='trill'):
            render(parse(source))
        with pytest.raises(UnverifiedFeature, match='trill'):
            expected(songsterr(source), {'offset':0,'scale':1})
        return
    perf = compare(source)
    notes = perf['tracks'][0]['notes']
    assert len(notes) == len(wanted['attacks'])
    for note,(tick,fret) in zip(notes,wanted['attacks']):
        assert note['t']*2*wanted['tpqn'] == pytest.approx(tick,abs=1e-7)
        assert note['f'] == fret
    assert (notes[-1]['t']+notes[-1]['sus'])*2*wanted['tpqn'] == pytest.approx(wanted['endTick'])
    assert all(n.get('ho') or n.get('po') for n in notes[1:])
    assert not notes[0].get('ho') and not notes[0].get('po')
    assert perf['trillEvidence'][0]['mode'] == 'native-tied-segments-v1'


def sample():
    return deepcopy(next(c['source'] for c in FIXTURE['cases'] if c['id']=='38/31/8/32/15/false'))


def test_no_new_attack_at_tie_boundary_and_written_notation_is_preserved():
    source = sample(); before = deepcopy(source)
    perf = compare(source)
    assert source == before
    assert len(perf['tracks'][0]['notes']) == 8
    assert not any(n['t']==.25 for n in perf['tracks'][0]['notes'])
    receipt = perf['trillEvidence'][0]
    assert receipt['segments'][1]['tied'] is True
    assert receipt['segments'][1]['speed']=='31'
    assert len(receipt['sourceIds'])==2


@pytest.mark.parametrize('extra',[{'staccato':True},{'harmonic':'pinch'},{'slide':'downwards'},{'tap':True}])
def test_previous_unmarked_segment_cannot_hide_an_unsupported_gesture(extra):
    source=sample();notes=source['parts'][0]['measures'][0]['voices'][0]['beats'][0]['notes']
    notes[0].pop('trill');notes[0].update(extra)
    with pytest.raises(ScoreImportError):render(parse(source))
    with pytest.raises(UnverifiedFeature):expected(songsterr(source),{'offset':0,'scale':1})


@pytest.mark.parametrize('fret,auxiliary,string',[(0,3,2),(5,2,3),(21,24,1)])
def test_transposed_pitches_strings_repeats_and_tempo_change(fret,auxiliary,string):
    source=sample();part=source['parts'][0];bar=part['measures'][0]
    bar.update(repeatStart=True,repeat=2)
    for beat in bar['voices'][0]['beats']:
        note=beat['notes'][0];note.update(fret=fret,string=string)
        note['trill']['auxiliaryFret']=auxiliary
    part['automations']['tempo'].append({'measure':0,'position':480,'bpm':60,'type':4})
    perf=compare(source);notes=perf['tracks'][0]['notes']
    assert len(notes)==16
    assert [n['f'] for n in notes]==[fret,auxiliary]*8
    assert len(perf['trillEvidence'])==2
    assert not notes[8].get('ho') and not notes[8].get('po')
    assert all(n['s']==5-string for n in notes)


@pytest.mark.parametrize('fault',[None,'pitch','time','missing','extra','legato','segment','contract'])
def test_tied_trill_archive_preservation_and_mutation(tmp_path,fault):
    from zipfile import ZipFile
    import yaml
    from test_song_import_builder import inputs
    from feedback_converter.song_import import load_performance
    from feedback_converter.song_import.builder import build_feedpak
    from feedback_converter.song_import.verification import verify_import
    _,audio,_,job=inputs(tmp_path)
    source=tmp_path/'source.json';source.write_text(json.dumps(sample()),encoding='utf8')
    perf=load_performance(source)
    alignment={'status':'validated','offset':0.5,'scale':1.1}
    result=build_feedpak(perf,audio,alignment,job,output_dir=tmp_path/'out',source_path=source,
        compatibility=perf['compatibilityReport'],recipe={'preservationContract':49})
    original=Path(result['stagingPath'])
    check=verify_import(source,original,alignment)
    assert check['status']=='passed',check
    with ZipFile(original) as z: files={name:z.read(name) for name in z.namelist()}
    assert files['import/source.json']==source.read_bytes()
    manifest=yaml.safe_load(files['manifest.yaml']);filename=manifest['arrangements'][0]['file']
    chart=json.loads(files[filename]);notes=chart['notes'];receipt=json.loads(files['import/trills.json'])
    assert receipt['version']==2 and receipt['policy']=='songsterr-trill-hopo-v2'
    if fault=='pitch':notes[-1]['f']-=1
    if fault=='time':notes[-1]['t']+=.01
    if fault=='missing':notes.pop()
    if fault=='extra':notes.append(deepcopy(notes[-1]))
    if fault=='legato':notes[-1].pop('ho')
    if fault=='segment':receipt['trills'][0]['segments'][1]['speed']='38'
    if fault=='contract':manifest['song_import']['preservationContract']=48
    files[filename]=json.dumps(chart).encode();files['import/trills.json']=json.dumps(receipt).encode()
    files['manifest.yaml']=yaml.safe_dump(manifest).encode()
    changed=tmp_path/'changed.feedpak'
    with ZipFile(changed,'w') as z:
        for name,data in files.items():z.writestr(name,data)
    check=verify_import(source,changed,alignment)
    assert check['status']==('passed' if fault is None else 'failed'),check

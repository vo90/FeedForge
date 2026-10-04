"""Independent bend clocks retain written beat vibrato and its limitation."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

import pytest
import yaml

from test_song_import_score import import_json
from test_songsterr_bend_timing import checked
from feedback_converter.song_import.audio import ImportFailure
from feedback_converter.song_import.builder import build_feedpak
from feedback_converter.song_import.hybrid_lead import normalize_options, choose_main
from feedback_converter.song_import.score import load_performance
from feedback_converter.song_import.verification import verify_import

REFERENCE = json.loads((Path(__file__).parent/'fixtures/songsterr_beat_vibrato_bends_reference.json').read_text())


def document():
    return deepcopy(REFERENCE['cases'][0]['source'])


@pytest.mark.parametrize('case', REFERENCE['cases'], ids=lambda c:c['id'])
def test_independent_clock_preserves_written_controls(case, tmp_path):
    d = deepcopy(case['source']); p = checked(d)
    assert d == case['source']
    e = p['fingerBendTimingEvidence'][0]; n = p['tracks'][0]['notes'][0]
    assert e['status'] == 'resolved' and n == case['note']
    control = e['terminalSlideOut']['beatVibrato']
    assert control['policy'] == 'independent-written-instruction' and control['segments']
    assert {k:v for k,v in n.items() if k not in ('bn','bnv')} == {k:v for k,v in case['before'].items() if k not in ('bn','bnv')}
    assert len(p['tracks'][0]['notes']) == 2 and 'sl' not in n and 'slu' not in n
    assert REFERENCE['referenceSha256'] == '4219f9dbf0952af83be36c8b590f10fea7a0f36d72bd1f92297a3d768f93f5ab'
    assert len(case['profiles']) == 2 and all(v['beatFlagsDoNotAlterBendOrScheduledEvents'] for v in case['profiles'])
    imported = import_json(tmp_path,d)
    findings = imported['compatibilityReport']['findings']
    assert any(f['feature'] in ('beat.vibrato','beat.wideVibrato') for f in findings)
    assert not any(f['feature'] == 'note.bend_timing' for f in findings)


@pytest.mark.parametrize('variant', ['repeat', 'tempo', 'triplet', 'chord', 'following-slide', 'terminal-bend', 'renamed'])
def test_general_source_clock_contexts(variant):
    d = document(); bar = d['parts'][0]['measures'][0]; bs = bar['voices'][0]['beats']
    if variant == 'repeat': bar.update(repeatStart=True,repeat=2)
    elif variant == 'tempo': d['parts'][0]['automations']['tempo'].append({'measure':0,'position':480,'bpm':60,'type':4})
    elif variant == 'triplet':
        for b in bs[:3]: b['duration'] = [1,6]
        bs[3]['duration'] = [1,2]
    elif variant == 'chord':
        for b in bs[:3]:
            n = deepcopy(b['notes'][0]); n.update(string=1,fret=9); b['notes'].append(n)
    elif variant == 'following-slide': bs[3]['notes'][0]['slide'] = 'below'
    elif variant == 'terminal-bend':
        # Adjacent explicit controllers have no overlapping clocks.
        bs[1]['notes'][0]['bend'] = deepcopy(bs[0]['notes'][0]['bend'])
        bs[2]['notes'][0]['bend'] = {'points':[{'position':0,'tone':100},{'position':60,'tone':0}]}
    else: d.update(title='Unrelated song',songId=887766,revisionId=9)
    p = checked(d)
    assert all(e['status'] == 'resolved' and e['terminalSlideOut']['beatVibrato'] for e in p['fingerBendTimingEvidence'])
    if variant == 'repeat': assert len(p['fingerBendTimingEvidence']) == 2
    if variant == 'tempo':
        assert p['tracks'][0]['notes'][0]['bnv'] == [{'t':0.,'v':0.},{'t':.25,'v':2/3},{'t':1.25,'v':2.},{'t':2.75,'v':2.}]


@pytest.mark.parametrize('fault', ['pinch','natural','artificial','whammy','bar-vibrato','targeted-slide',
                                  'initial-slide','earlier-out','hopo','palm-mute','let-ring','overlap','strum'])
def test_other_compositions_remain_guarded(fault,tmp_path):
    d = document(); bs = d['parts'][0]['measures'][0]['voices'][0]['beats']; first = bs[0]['notes'][0]; tail = bs[2]['notes'][0]
    if fault in ('pinch','natural','artificial'): first.update(harmonic=fault,harmonicFret=7 if fault == 'natural' else 12)
    elif fault == 'whammy': bs[0]['tremoloBar'] = {'points':[{'position':0,'tone':0},{'position':60,'tone':-50}]}
    elif fault == 'bar-vibrato': bs[0]['vibratoWithTremoloBar'] = 'wide'
    elif fault == 'targeted-slide': tail['slide'] = 'shift'
    elif fault == 'initial-slide': first['slide'] = 'above'
    elif fault == 'earlier-out': first['slide'] = 'downwards'
    elif fault == 'hopo': tail['hp'] = True
    elif fault == 'palm-mute': bs[0]['palmMute'] = True
    elif fault == 'let-ring': bs[0]['letRing'] = True
    elif fault == 'overlap': tail['bend'] = {'points':[{'position':0,'tone':100},{'position':60,'tone':0}]}
    else:
        tail['bend'] = first.pop('bend')
        bs[0]['brushStroke'] = {'direction':'down','duration':30,'shift':100}
        bs[0]['notes'].append({'string':1,'fret':5})
    e = checked(d)['fingerBendTimingEvidence'][0]
    assert e['status'] == 'deferred' and 'terminalSlideOut' not in e
    assert any(f['feature'] == 'note.bend_timing' for f in import_json(tmp_path,d)['compatibilityReport']['findings'])


@pytest.mark.parametrize('piecewise',[False,True])
@pytest.mark.parametrize('hybrid',[False,True])
def test_package_keeps_source_warnings_and_rejects_mutations(tmp_path,piecewise,hybrid):
    from test_song_import_builder import inputs
    _,audio,_,job = inputs(tmp_path)
    p = import_json(tmp_path,document()); source = tmp_path/'score.json'
    if hybrid: p = load_performance(source,composition_context=True)
    alignment = {'status':'validated','offset':.25,'scale':1.}
    if piecewise:
        alignment.update(mapping='piecewise-linear',anchors=[{'score':0,'audio':.25},{'score':.5,'audio':.75},{'score':4,'audio':4.95}],tempos=[{'time':.25,'bpm':120},{'time':.75,'bpm':100}])
    options = normalize_options({'enabled':hybrid}); sha = hashlib.sha256(source.read_bytes()).hexdigest()
    if hybrid: options.update(mainTrackId=choose_main(p,options,sha),sourceSha256=sha)
    recipe = {'preservationContract':77,'scoreHash':sha,'audioHash':audio['hash'],**({'hybridLead':options} if hybrid else {})}
    old = tmp_path/'old'; old.mkdir()
    with pytest.raises(ImportFailure,match='contract 77'):
        build_feedpak(p,audio,alignment,old,output_dir=tmp_path/'old-out',source_path=source,compatibility=p['compatibilityReport'],recipe={'preservationContract':76})
    result = build_feedpak(p,audio,alignment,job,output_dir=tmp_path/'out',source_path=source,compatibility=p['compatibilityReport'],recipe=recipe,
                          hybrid_lead={'enabled':hybrid,'mainTrackId':options.get('mainTrackId'),'options':options})
    archive = Path(result['stagingPath']); verify = lambda f:verify_import(source,f,alignment,hybrid_options=options if hybrid else None)
    assert verify(archive)['status'] == 'passed'
    with ZipFile(archive) as z: original = {name:z.read(name) for name in z.namelist()}
    m = yaml.safe_load(original['manifest.yaml']); ep = m['song_import']['fingerBendTimingFile']
    assert json.loads(original[ep])['version'] == 17 and original[m['song_import']['sourceFile']] == source.read_bytes()
    if hybrid: assert any(a['name'] == 'Hybrid Lead' for a in m['arrangements'])
    for fault in ('bend','slide-time','slide-direction','vibrato','attack','fret','duration','extra-attack',
                  'evidence-missing','evidence-source','evidence-policy','version','contract','warning'):
        files = dict(original); manifest = deepcopy(m); cp = manifest['arrangements'][0]['file']
        chart = json.loads(files[cp]); n = chart['notes'][0]; ev = json.loads(files[ep])
        if fault == 'bend': n['bnv'][1]['t'] /= 2
        elif fault == 'slide-time': n['slide_out_marks'][0]['start'] += .1
        elif fault == 'slide-direction': n['slide_out_marks'][0]['direction'] = 'down'
        elif fault == 'vibrato': del n['vibrato_marks']
        elif fault == 'attack': n['t'] += .1
        elif fault == 'fret': n['f'] += 1
        elif fault == 'duration': n['sus'] -= .1
        elif fault == 'extra-attack': chart['notes'].append(deepcopy(n))
        elif fault == 'evidence-missing': del ev['gestures'][0]['terminalSlideOut']['beatVibrato']
        elif fault == 'evidence-source': ev['gestures'][0]['terminalSlideOut']['beatVibrato']['segments'][0]['sourceId'] += ':wrong'
        elif fault == 'evidence-policy': ev['gestures'][0]['terminalSlideOut']['beatVibrato']['policy'] = 'native-playback'
        elif fault == 'version': ev.update(version=16,policy='songsterr-finger-bend-timing-v16')
        elif fault == 'contract': manifest['song_import']['preservationContract'] = 76
        else:
            rp = manifest['song_import']['compatibilityFile']; report = json.loads(files[rp])
            report['findings'] = [f for f in report['findings'] if f['feature'] not in ('beat.vibrato','beat.wideVibrato')]
            report['findingCount'] = len(report['findings']); files[rp] = json.dumps(report).encode()
        files[cp] = json.dumps(chart).encode(); files[ep] = json.dumps(ev).encode(); files['manifest.yaml'] = yaml.safe_dump(manifest).encode()
        target = tmp_path/'mutated.feedpak'
        with ZipFile(target,'w') as z:
            for name,data in files.items(): z.writestr(name,data)
        assert verify(target)['status'] == 'failed',fault

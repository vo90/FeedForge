"""One pinch attack retains authored bend timing and its final direction cue."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

import pytest
import yaml

from test_song_import_score import beat, import_json
from test_songsterr_bend_timing import checked
from feedback_converter.song_import.audio import ImportFailure
from feedback_converter.song_import.builder import build_feedpak
from feedback_converter.song_import.hybrid_lead import normalize_options, choose_main
from feedback_converter.song_import.score import load_performance
from feedback_converter.song_import.verification import verify_import

REFERENCE = json.loads((Path(__file__).parent/'fixtures/songsterr_pinch_bend_slide_reference.json').read_text())


def document():
    return deepcopy(REFERENCE['cases'][0]['source'])


@pytest.mark.parametrize('case', REFERENCE['cases'], ids=lambda c: c['id'])
def test_qualified_native_composition_preserves_target_and_authored_clock(case):
    d = deepcopy(case['source']); p = checked(d)
    assert d == case['source']
    e = p['fingerBendTimingEvidence'][0]; n = p['tracks'][0]['notes'][0]
    assert e['status'] == 'resolved' and n == case['notes'][0]
    assert e['terminalSlideOut']['continuedPinchHarmonic'] == {
        'initialTarget': n['harmonic_target'], 'policy': 'initial-target-continued'}
    assert len(p['tracks'][0]['notes']) == 2  # One tied gesture and the following attack.
    assert 'sl' not in n and 'slu' not in n and 'harmonic_changes' not in n
    assert REFERENCE['referenceSha256'] == '4219f9dbf0952af83be36c8b590f10fea7a0f36d72bd1f92297a3d768f93f5ab'
    assert len(case['profiles']) == 2 and all(v['harmonicIndependentWithinNativeQuantization'] for v in case['profiles'])
    control = deepcopy(d)
    del control['parts'][0]['measures'][0]['voices'][0]['beats'][1]['notes'][0]['slide']
    other = checked(control)
    assert {k:v for k,v in n.items() if k not in ('slide_out', 'slide_out_marks')} == other['tracks'][0]['notes'][0]
    assert p.get('harmonicTieEvidence') == other.get('harmonicTieEvidence')


@pytest.mark.parametrize('variant', ['repeat', 'tempo', 'triplet', 'chord', 'following-slide', 'renamed'])
def test_source_clock_contexts_are_general(variant):
    d = document(); bar = d['parts'][0]['measures'][0]; bs = bar['voices'][0]['beats']
    if variant == 'repeat': bar.update(repeatStart=True, repeat=2)
    elif variant == 'tempo': d['parts'][0]['automations']['tempo'].append({'measure':0,'position':480,'bpm':60,'type':4})
    elif variant == 'triplet':
        for b in bs[:2]: b['duration'] = [1,6]
        bs[2]['duration'] = [2,3]
    elif variant == 'chord':
        for b in bs[:2]:
            n = deepcopy(b['notes'][0]); n['string'] = 1; n['fret'] = 9; b['notes'].append(n)
    elif variant == 'following-slide': bs[2]['notes'][0]['slide'] = 'below'
    else: d.update(title='Any title', songId=998765, revisionId=42)
    p = checked(d)
    assert all(e['status'] == 'resolved' and e['terminalSlideOut']['continuedPinchHarmonic'] for e in p['fingerBendTimingEvidence'])
    if variant == 'repeat': assert len(p['fingerBendTimingEvidence']) == 2
    if variant == 'tempo':
        assert p['tracks'][0]['notes'][0]['bnv'] == [{'t':0.,'v':0.},{'t':.25,'v':1.},{'t':.75,'v':2.},{'t':1.75,'v':2.}]


@pytest.mark.parametrize('fault', ['later-kind', 'missing-initial', 'beat-vibrato',
                                  'whammy', 'bar-vibrato', 'targeted-slide', 'initial-slide', 'earlier-out',
                                  'hopo', 'palm-mute', 'let-ring', 'changing-overlap', 'settled-overlap', 'strum'])
def test_unqualified_gestures_keep_their_warning(fault, tmp_path):
    d = document(); bs = d['parts'][0]['measures'][0]['voices'][0]['beats']; first = bs[0]['notes'][0]; tail = bs[1]['notes'][0]
    if fault == 'later-kind': tail.update(harmonic='artificial', harmonicFret=12)
    elif fault == 'missing-initial':
        del first['harmonic']; del first['harmonicFret']; tail.update(harmonic='pinch', harmonicFret=5)
    elif fault == 'beat-vibrato':
        first.pop('leftHandVibrato', None); bs[0]['wideVibrato'] = True
    elif fault == 'whammy': bs[0]['tremoloBar'] = {'points':[{'position':0,'tone':0},{'position':60,'tone':-50}]}
    elif fault == 'bar-vibrato': bs[0]['vibratoWithTremoloBar'] = 'wide'
    elif fault == 'targeted-slide': tail['slide'] = 'shift'
    elif fault == 'initial-slide': first['slide'] = 'above'
    elif fault == 'earlier-out': first['slide'] = 'downwards'
    elif fault == 'hopo': tail['hp'] = True
    elif fault == 'palm-mute': bs[0]['palmMute'] = True
    elif fault == 'let-ring': bs[0]['letRing'] = True
    elif fault in ('changing-overlap', 'settled-overlap'):
        bs.insert(1, beat(fret=7,duration=(1,4),tie=True)); bs[-1]['duration'] = [1,4]
        tail['bend'] = {'points':[{'position':0,'tone':100},{'position':60,'tone':0}]}
        first['bend']['points'][1]['position'] = 60 if fault == 'changing-overlap' else 10
    else:
        # A bend on the strummed beat consumes spreading in Songsterr. Put
        # the bend on the tie so this case really has a displaced attack.
        tail['bend'] = first.pop('bend')
        bs[0]['brushStroke'] = {'direction':'down','duration':30,'shift':100}
        bs[0]['notes'].append({'string':1,'fret':5})
    e = checked(d)['fingerBendTimingEvidence'][0]
    assert e['status'] == 'deferred' and 'terminalSlideOut' not in e
    assert any(f['feature'] == 'note.bend_timing' for f in import_json(tmp_path,d)['compatibilityReport']['findings'])


def test_changed_tied_pinch_retains_separate_ambiguity_finding(tmp_path):
    d = document(); tail = d['parts'][0]['measures'][0]['voices'][0]['beats'][1]['notes'][0]
    tail.update(harmonic='pinch', harmonicFret=5)
    p = import_json(tmp_path,d)
    assert p['fingerBendTimingEvidence'][0]['status'] == 'resolved'
    assert p['tracks'][0]['notes'][0]['harmonic_target']['node'] == 3.2
    control = deepcopy(d); del control['parts'][0]['measures'][0]['voices'][0]['beats'][1]['notes'][0]['slide']
    folder = tmp_path/'control'; folder.mkdir(); other = import_json(folder,control)
    findings = [f for f in p['compatibilityReport']['findings'] if 'harmonic' in f['feature']]
    assert findings and findings == [f for f in other['compatibilityReport']['findings'] if 'harmonic' in f['feature']]
    assert not any(f['feature'] == 'note.bend_timing' for f in p['compatibilityReport']['findings'])


@pytest.mark.parametrize('piecewise', [False, True])
@pytest.mark.parametrize('hybrid', [False, True])
def test_package_contract_and_independent_mutation_rejection(tmp_path, piecewise, hybrid):
    from test_song_import_builder import inputs
    _, audio, _, job = inputs(tmp_path)
    p = import_json(tmp_path,document()); path = tmp_path/'score.json'
    if hybrid: p = load_performance(path,composition_context=True)
    alignment = {'status':'validated','offset':.25,'scale':1.}
    if piecewise:
        alignment.update(mapping='piecewise-linear',anchors=[{'score':0,'audio':.25},{'score':.5,'audio':.75},{'score':4,'audio':4.95}],tempos=[{'time':.25,'bpm':120},{'time':.75,'bpm':100}])
    options = normalize_options({'enabled':hybrid}); sha = hashlib.sha256(path.read_bytes()).hexdigest()
    if hybrid: options.update(mainTrackId=choose_main(p,options,sha),sourceSha256=sha)
    recipe = {'preservationContract':76,'scoreHash':sha,'audioHash':audio['hash'],**({'hybridLead':options} if hybrid else {})}
    old = tmp_path/'old'; old.mkdir()
    with pytest.raises(ImportFailure,match='contract 76'):
        build_feedpak(p,audio,alignment,old,output_dir=tmp_path/'old-out',source_path=path,compatibility=p['compatibilityReport'],recipe={'preservationContract':75})
    result = build_feedpak(p,audio,alignment,job,output_dir=tmp_path/'out',source_path=path,compatibility=p['compatibilityReport'],recipe=recipe,
                          hybrid_lead={'enabled':hybrid,'mainTrackId':options.get('mainTrackId'),'options':options})
    archive = Path(result['stagingPath']); verify = lambda file: verify_import(path,file,alignment,hybrid_options=options if hybrid else None)
    assert verify(archive)['status'] == 'passed'
    with ZipFile(archive) as z: original = {name:z.read(name) for name in z.namelist()}
    m = yaml.safe_load(original['manifest.yaml']); ep = m['song_import']['fingerBendTimingFile']
    assert json.loads(original[ep])['version'] == 16 and original[m['song_import']['sourceFile']] == path.read_bytes()
    if hybrid: assert any(a['name'] == 'Hybrid Lead' for a in m['arrangements'])
    for fault in ('bend','harmonic-target','harmonic-missing','slide-time','slide-direction','vibrato','attack','fret','duration','extra-attack',
                  'evidence-target','evidence-absent','evidence-policy','version','contract'):
        files = dict(original); manifest = deepcopy(m); cp = manifest['arrangements'][0]['file']
        chart = json.loads(files[cp]); n = chart['notes'][0]; ev = json.loads(files[ep])
        if fault == 'bend': n['bnv'][1]['t'] /= 2
        elif fault == 'harmonic-target': n['harmonic_target'].update(node=5,interval=24)
        elif fault == 'harmonic-missing': del n['harmonic_target']
        elif fault == 'slide-time': n['slide_out_marks'][0]['start'] += .1
        elif fault == 'slide-direction': n['slide_out_marks'][0]['direction'] = 'down'
        elif fault == 'vibrato': del n['vibrato_marks']
        elif fault == 'attack': n['t'] += .1
        elif fault == 'fret': n['f'] += 1
        elif fault == 'duration': n['sus'] -= .1
        elif fault == 'extra-attack': chart['notes'].append(deepcopy(n))
        elif fault == 'evidence-target': ev['gestures'][0]['terminalSlideOut']['continuedPinchHarmonic']['initialTarget']['node'] = 5
        elif fault == 'evidence-absent': del ev['gestures'][0]['terminalSlideOut']['continuedPinchHarmonic']
        elif fault == 'evidence-policy': ev['gestures'][0]['terminalSlideOut']['continuedPinchHarmonic']['policy'] = 'latest-target'
        elif fault == 'version': ev.update(version=15,policy='songsterr-finger-bend-timing-v15')
        else: manifest['song_import']['preservationContract'] = 75
        files[cp] = json.dumps(chart).encode(); files[ep] = json.dumps(ev).encode(); files['manifest.yaml'] = yaml.safe_dump(manifest).encode()
        target = tmp_path/'mutated.feedpak'
        with ZipFile(target,'w') as z:
            for name,data in files.items(): z.writestr(name,data)
        assert verify(target)['status'] == 'failed', fault

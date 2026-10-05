"""Semi-harmonic bend/slide composition, independent proof and archive guards."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile
import pytest
import yaml
from test_song_import_score import beat, measure, raw_score, import_json
from test_songsterr_bend_timing import checked
from feedback_converter.song_import.builder import build_feedpak
from feedback_converter.song_import.audio import ImportFailure
from feedback_converter.song_import.hybrid_lead import choose_main, normalize_options
from feedback_converter.song_import.score import load_performance
from feedback_converter.song_import.verification import verify_import

REFERENCE = json.loads((Path(__file__).parent/'fixtures/songsterr_semi_bend_slide_reference.json').read_text())


def document():
    return deepcopy(REFERENCE['cases'][0]['source'])


@pytest.mark.parametrize('case', REFERENCE['cases'], ids=lambda c: c['id'])
def test_captured_tie_clock_preserves_mixed_target_and_slide(case):
    d = deepcopy(case['source']); p = checked(d)
    assert d == case['source']
    n = p['tracks'][0]['notes'][0]; e = p['fingerBendTimingEvidence'][0]
    assert n == case['note'] and e['status'] == 'resolved'
    assert e['terminalSlideOut']['continuedSemiHarmonic'] == {
        'initialTarget': n['harmonic_target'], 'policy': 'fixed-or-omitted-tied-target'}
    assert n['hp'] is True and n['harmonic_target']['policy'] == 'mixed'
    assert n['harmonic_target']['kind'] == 'semi' and not n.get('harmonic_changes')
    assert next(p['t'] for p in n['bnv'] if p['v'] == 2) == case['nativePeakSeconds']


def test_written_terminal_release_is_not_compressed_to_native_slide_sample():
    d = raw_score([measure(
        beat(13, duration=(1, 2), harmonic='semi', harmonicFret=5,
             bend={'points':[{'position':0,'tone':0},{'position':15,'tone':50},{'position':60,'tone':50}]}),
        beat(13, duration=(1, 4), tie=True, slide='downwards',
             bend={'points':[{'position':0,'tone':50},{'position':20,'tone':0},{'position':60,'tone':0}]}),
        {'duration':[1,4], 'notes':[{'rest':True}]})])
    p = checked(d); n = p['tracks'][0]['notes'][0]
    assert p['fingerBendTimingEvidence'][0]['terminalSlideOut']['bendTiming'] == 'authored-segment'
    assert n['bnv'] == [{'t':0.,'v':0.},{'t':.25,'v':1.},{'t':1.,'v':1.},
                         {'t':pytest.approx(7/6),'v':0.},{'t':1.5,'v':0.}]
    assert n['slide_out_marks'] == [{'direction':'down','start':1.,'end':1.5}]


def test_existing_pinch_fixture_can_preserve_semi_policy_and_note_vibrato():
    from test_songsterr_pinch_bend_slide import document as pinch
    d = pinch(); before = checked(d)['tracks'][0]['notes'][0]
    d['parts'][0]['measures'][0]['voices'][0]['beats'][0]['notes'][0]['harmonic'] = 'semi'
    p = checked(d); n = p['tracks'][0]['notes'][0]
    assert p['fingerBendTimingEvidence'][0]['terminalSlideOut']['continuedSemiHarmonic']
    assert n['harmonic_target']['node'] == 3.2 and n['harmonic_target']['policy'] == 'mixed'
    assert n['bnv'] == before['bnv'] and n['vibrato_marks'] == before['vibrato_marks']


@pytest.mark.parametrize('variant', ['repeat', 'tempo', 'renamed', 'chord', 'following-slide', 'triplet'])
def test_general_source_clocks_and_contexts(variant):
    d = document(); part = d['parts'][0]; bar = part['measures'][0]; bs = bar['voices'][0]['beats']
    if variant == 'repeat': bar.update(repeatStart=True, repeat=2)
    elif variant == 'tempo': part['automations']['tempo'].append({'measure':0,'position':480,'bpm':60,'type':4})
    elif variant == 'renamed': d.update(songId=998877, title='Unrelated title', artist='Another artist')
    elif variant == 'triplet':
        bs[0]['duration'] = [1,6]; bs[1]['duration'] = [1,6]; bs[2]['duration'] = [2,3]
    elif variant == 'following-slide': part['measures'][1]['voices'][0]['beats'][0]['notes'][0]['slide'] = 'below'
    else:
        for b in bs:
            n = deepcopy(b['notes'][0]); n['string'] = 1; n['fret'] = 9; b['notes'].append(n)
    p = checked(d)
    assert all(e['status'] == 'resolved' and e['terminalSlideOut']['continuedSemiHarmonic'] for e in p['fingerBendTimingEvidence'])
    if variant == 'repeat': assert len(p['fingerBendTimingEvidence']) == 2


@pytest.mark.parametrize('fault', ['changed-target', 'changed-kind', 'missing-initial', 'whammy', 'beat-vibrato',
                                  'incoming', 'intermediate', 'targeted', 'hopo', 'palm', 'let-ring',
                                  'settled-overlap', 'changing-overlap', 'strum'])
def test_other_expressions_remain_guarded(fault, tmp_path):
    d = document(); bs = d['parts'][0]['measures'][0]['voices'][0]['beats']
    if fault == 'changed-target': bs[1]['notes'][0]['harmonicFret'] = 7
    elif fault == 'changed-kind': bs[1]['notes'][0]['harmonic'] = 'pinch'
    elif fault == 'missing-initial':
        for k in ('harmonic', 'harmonicFret'): bs[0]['notes'][0].pop(k, None)
    elif fault == 'whammy': bs[0]['tremoloBar'] = {'points':[{'position':0,'tone':0},{'position':60,'tone':-50}]}
    elif fault == 'beat-vibrato': bs[0]['vibrato'] = True
    elif fault == 'incoming': bs[0]['notes'][0]['slide'] = 'below'
    elif fault == 'intermediate': bs[1]['notes'][0]['slide'] = 'downwards'
    elif fault == 'targeted': bs[-1]['notes'][0]['slide'] = 'shift'
    elif fault == 'hopo': bs[-1]['notes'][0]['hp'] = True
    elif fault == 'palm': bs[0]['palmMute'] = True
    elif fault == 'let-ring': bs[0]['letRing'] = True
    elif fault in ('settled-overlap', 'changing-overlap'):
        bs[-1]['notes'][0]['bend'] = {'points':[{'position':0,'tone':100},{'position':60,'tone':0}]}
        bs[0]['notes'][0]['bend']['points'][1]['position'] = 10 if fault == 'settled-overlap' else 60
    else:
        bs[1]['notes'][0]['bend'] = bs[0]['notes'][0].pop('bend')
        bs[0]['brushStroke'] = {'direction':'down','duration':30,'shift':100}
        bs[0]['notes'].append({'string':1,'fret':5})
    p = checked(d); e = p['fingerBendTimingEvidence'][0]
    assert e['status'] == 'deferred' and 'continuedSemiHarmonic' not in e.get('terminalSlideOut', {})
    p = import_json(tmp_path, d)
    assert any(f['feature'] == 'note.bend_timing' for f in p['compatibilityReport']['findings'])


@pytest.mark.parametrize('piecewise', [False, True])
@pytest.mark.parametrize('hybrid', [False, True])
def test_archive_and_independent_tamper_detection(tmp_path, piecewise, hybrid):
    from test_song_import_builder import inputs
    _, audio, _, job = inputs(tmp_path)
    p = import_json(tmp_path, document()); source = tmp_path/'score.json'
    if hybrid: p = load_performance(source, composition_context=True)
    alignment = {'status':'validated','offset':.25,'scale':1.}
    if piecewise: alignment.update(mapping='piecewise-linear', anchors=[{'score':0,'audio':.25},{'score':.75,'audio':1.},{'score':4,'audio':4.9}], tempos=[{'time':.25,'bpm':120},{'time':1.,'bpm':100}])
    sha = hashlib.sha256(source.read_bytes()).hexdigest(); options = normalize_options({'enabled':hybrid})
    if hybrid: options.update(mainTrackId=choose_main(p, options, sha), sourceSha256=sha)
    recipe = {'preservationContract':83,'scoreHash':sha,'audioHash':audio['hash'], **({'hybridLead':options} if hybrid else {})}
    old = tmp_path/'old'; old.mkdir()
    with pytest.raises(ImportFailure, match='contract 83'):
        build_feedpak(p, audio, alignment, old, output_dir=tmp_path/'old-out', source_path=source, compatibility=p['compatibilityReport'], recipe={'preservationContract':82})
    built = build_feedpak(p, audio, alignment, job, output_dir=tmp_path/'out', source_path=source, compatibility=p['compatibilityReport'], recipe=recipe,
                         hybrid_lead={'enabled':hybrid,'mainTrackId':options.get('mainTrackId'),'options':options})
    verify = lambda path: verify_import(source, path, alignment, hybrid_options=options if hybrid else None)
    assert verify(built['stagingPath'])['status'] == 'passed'
    with ZipFile(built['stagingPath']) as z: original = {k:z.read(k) for k in z.namelist()}
    manifest = yaml.safe_load(original['manifest.yaml']); ep = manifest['song_import']['fingerBendTimingFile']
    assert json.loads(original[ep])['version'] == 22
    assert original[manifest['song_import']['sourceFile']] == source.read_bytes()
    if hybrid: assert any(a['name'] == 'Hybrid Lead' for a in manifest['arrangements'])
    for fault in ('bend', 'target', 'policy', 'target-absent', 'slide-start', 'slide-end', 'slide-direction', 'slide-absent',
                  'attack', 'fret', 'duration', 'extra-attack', 'evidence-target', 'evidence-policy', 'evidence-absent', 'version', 'contract'):
        files = dict(original); m = deepcopy(manifest); cp = m['arrangements'][0]['file']; chart = json.loads(files[cp]); n = chart['notes'][0]; ev = json.loads(files[ep])
        if fault == 'bend': n['bnv'][1]['t'] /= 4
        elif fault == 'target': n['harmonic_target'].update(node=7, interval=19)
        elif fault == 'policy': n['harmonic_target'].update(kind='pinch', policy='harmonic')
        elif fault == 'target-absent': del n['harmonic_target']
        elif fault.startswith('slide-'):
            field = fault.split('-')[1]
            if field == 'absent': del n['slide_out_marks']
            elif field == 'direction': n['slide_out_marks'][0][field] = 'down'
            else: n['slide_out_marks'][0][field] -= .1
        elif fault == 'attack': n['t'] += .1
        elif fault == 'fret': n['f'] += 1
        elif fault == 'duration': n['sus'] -= .1
        elif fault == 'extra-attack': chart['notes'].append(deepcopy(n))
        elif fault == 'evidence-target': ev['gestures'][0]['terminalSlideOut']['continuedSemiHarmonic']['initialTarget']['policy'] = 'harmonic'
        elif fault == 'evidence-policy': ev['gestures'][0]['terminalSlideOut']['continuedSemiHarmonic']['policy'] = 'replace-on-tie'
        elif fault == 'evidence-absent': del ev['gestures'][0]['terminalSlideOut']['continuedSemiHarmonic']
        elif fault == 'version': ev.update(version=21, policy='songsterr-finger-bend-timing-v21')
        else: m['song_import']['preservationContract'] = 82
        files[cp] = json.dumps(chart).encode(); files[ep] = json.dumps(ev).encode(); files['manifest.yaml'] = yaml.safe_dump(m).encode()
        target = tmp_path/'mutated.feedpak'
        with ZipFile(target, 'w') as z:
            for name, data in files.items(): z.writestr(name, data)
        assert verify(target)['status'] == 'failed', fault

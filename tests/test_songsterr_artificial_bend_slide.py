"""Captured combined expressions, source guards and independent archive checks."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile
import pytest
import yaml
from test_songsterr_bend_timing import checked
from test_song_import_score import import_json
from feedback_converter.song_import.builder import build_feedpak
from feedback_converter.song_import.audio import ImportFailure
from feedback_converter.song_import.hybrid_lead import choose_main, normalize_options
from feedback_converter.song_import.score import load_performance
from feedback_converter.song_import.verification import verify_import
from feedback_converter.song_import.songsterr import parse, WRITTEN_BEAT_VIBRATO_POLICY
from feedback_converter.song_import.timeline import render

REFERENCE = json.loads((Path(__file__).parent/'fixtures/songsterr_artificial_bend_slide_reference.json').read_text())


def document():
    return deepcopy(next(c['source'] for c in REFERENCE['cases'] if c['id'].endswith('upwards/settled-fixed')))


def test_precise_artificial_target_is_independent_of_bend_timing():
    from test_songsterr_pinch_bend_slide import document as pinch_document
    d = pinch_document(); previous = checked(d)['tracks'][0]['notes'][0]
    d['parts'][0]['measures'][0]['voices'][0]['beats'][0]['notes'][0]['harmonic'] = 'artificial'
    p = checked(d); n = p['tracks'][0]['notes'][0]
    assert p['fingerBendTimingEvidence'][0]['status'] == 'resolved'
    assert n['harmonic_target']['node'] == previous['harmonic_target']['node'] == 3.2
    assert n['harmonic_target']['kind'] == 'artificial' and not n.get('hp')
    assert n['bnv'] == previous['bnv'] and n['slide_out_marks'] == previous['slide_out_marks']


@pytest.mark.parametrize('constituent', ['beat-vibrato', 'harmonic-handoff'])
def test_previously_separate_rules_now_compose(constituent):
    if constituent == 'beat-vibrato':
        from test_songsterr_beat_vibrato_bends import document as source
        d = source(); before = checked(d)['tracks'][0]['notes'][0]
        d['parts'][0]['measures'][0]['voices'][0]['beats'][0]['notes'][0].update(harmonic='artificial', harmonicFret=12)
    else:
        from test_songsterr_harmonic_bend_handoff import document as source
        d = source(vibrato=True); before = checked(d)['tracks'][0]['notes'][0]
        d['parts'][0]['measures'][0]['voices'][0]['beats'][-1]['notes'][0]['slide'] = 'downwards'
    p = checked(d); n = p['tracks'][0]['notes'][0]
    assert p['fingerBendTimingEvidence'][0]['terminalSlideOut']['continuedArtificialHarmonic']
    assert n['bnv'] == before['bnv'] and n.get('vibrato_marks') == before.get('vibrato_marks')
    if constituent == 'beat-vibrato': assert not n.get('vb') and 'vibrato_marks' not in n
    assert n['harmonic_target']['kind'] == 'artificial' and n['slide_out_marks']


@pytest.mark.parametrize('case', REFERENCE['cases'], ids=lambda c: c['id'])
def test_captured_composition_keeps_all_authored_layers(case):
    d = deepcopy(case['source']); p = checked(d)
    assert d == case['source']
    e = p['fingerBendTimingEvidence'][0]; n = p['tracks'][0]['notes'][0]
    assert e['status'] == 'resolved' and n == case['note']
    assert e['terminalSlideOut']['continuedArtificialHarmonic'] == {
        'initialTarget': n['harmonic_target'], 'policy': 'fixed-or-omitted-tied-target'}
    assert not n.get('harmonic_changes') and 'sl' not in n
    for layer in ('slide', 'harmonic'):
        control = deepcopy(d)
        for b in control['parts'][0]['measures'][0]['voices'][0]['beats']:
            for note in b['notes']:
                for key in (('slide',) if layer == 'slide' else ('harmonic', 'harmonicFret')):
                    note.pop(key, None)
        stripped = checked(control)['tracks'][0]['notes'][0]
        # Missing tied targets may keep the older non-slide overlap guard.
        if layer == 'harmonic': assert stripped['bnv'] == n['bnv']
        field = 'harmonic_target' if layer == 'slide' else 'slide_out_marks'
        assert stripped[field] == n[field]


@pytest.mark.parametrize('variant', ['repeat', 'tempo', 'renamed', 'chord', 'beat-only-vibrato'])
def test_general_clock_and_contexts(variant):
    d = document(); part = d['parts'][0]; bar = part['measures'][0]; bs = bar['voices'][0]['beats']
    if variant == 'repeat': bar.update(repeatStart=True, repeat=2)
    elif variant == 'tempo': part['automations']['tempo'].append({'measure':0,'position':480,'bpm':60,'type':4})
    elif variant == 'renamed': d.update(songId=998877, title='Unrelated title', artist='Another artist')
    elif variant == 'beat-only-vibrato':
        for b in bs:
            for n in b['notes']: n.pop('vibrato', None); n.pop('leftHandVibrato', None)
    else:
        for b in bs:
            n = deepcopy(b['notes'][0]); n['string'] = 1; n['fret'] = 9; b['notes'].append(n)
    p = checked(d)
    assert all(e['status'] == 'resolved' and e['terminalSlideOut']['continuedArtificialHarmonic'] for e in p['fingerBendTimingEvidence'])
    if variant == 'repeat': assert len(p['fingerBendTimingEvidence']) == 2
    if variant == 'beat-only-vibrato':
        assert 'beatVibrato' not in p['fingerBendTimingEvidence'][0]['terminalSlideOut']
        assert not p['tracks'][0]['notes'][0].get('vb') and 'vibrato_marks' not in p['tracks'][0]['notes'][0]


@pytest.mark.parametrize('position,status', [(29, 'resolved'), (30, 'resolved'), (31, 'deferred'), (60, 'deferred')])
def test_handoff_boundary_has_no_tolerance_that_accepts_moving_pitch(position, status):
    d = document(); bs = d['parts'][0]['measures'][0]['voices'][0]['beats']
    for b in bs: b['duration'] = [1, 4]
    bs[0]['notes'][0]['bend']['points'][1]['position'] = position
    e = checked(d)['fingerBendTimingEvidence'][0]
    assert e['status'] == status


@pytest.mark.parametrize('fault', ['changed-target', 'missing-initial', 'pinch', 'natural', 'whammy',
                                  'incoming', 'intermediate', 'targeted', 'hopo', 'palm', 'let-ring'])
def test_other_expressions_stay_guarded(fault):
    d = document(); bs = d['parts'][0]['measures'][0]['voices'][0]['beats']
    if fault == 'changed-target': bs[1]['notes'][0]['harmonicFret'] = 7
    elif fault == 'missing-initial':
        for k in ('harmonic', 'harmonicFret'): bs[0]['notes'][0].pop(k, None)
    elif fault in ('pinch', 'natural'):
        bs[1]['notes'][0]['harmonic'] = fault
        if fault == 'natural': bs[1]['notes'][0].pop('harmonicFret')
    elif fault == 'whammy': bs[0]['tremoloBar'] = {'points':[{'position':0,'tone':0},{'position':60,'tone':-50}]}
    elif fault == 'incoming': bs[0]['notes'][0]['slide'] = 'below'
    elif fault == 'intermediate': bs[1]['notes'][0]['slide'] = 'downwards'
    elif fault in ('targeted', 'hopo'):
        if fault == 'targeted': bs[-1]['notes'][0]['slide'] = 'shift'
        else: bs[-1]['notes'][0]['hp'] = True
        bs[-1]['duration'] = [1, 4]
        bs.append({'duration':[1, 4], 'notes':[{'string':0, 'fret':9}]})
    elif fault == 'palm': bs[0]['palmMute'] = True
    else: bs[0]['letRing'] = True
    p = checked(d); e = p['fingerBendTimingEvidence'][0]
    assert e['status'] == 'deferred'
    assert not e.get('terminalSlideOut', {}).get('continuedArtificialHarmonic')


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
    recipe = {'preservationContract':94,'scoreHash':sha,'audioHash':audio['hash'], **({'hybridLead':options} if hybrid else {})}
    old = tmp_path/'old'; old.mkdir()
    historical = render(parse(document(), vibrato_policy=WRITTEN_BEAT_VIBRATO_POLICY))
    with pytest.raises(ImportFailure, match='contract 82'):
        build_feedpak(historical, audio, alignment, old, output_dir=tmp_path/'old-out', source_path=source, compatibility=p['compatibilityReport'], recipe={'preservationContract':81})
    built = build_feedpak(p, audio, alignment, job, output_dir=tmp_path/'out', source_path=source, compatibility=p['compatibilityReport'], recipe=recipe,
                         hybrid_lead={'enabled':hybrid,'mainTrackId':options.get('mainTrackId'),'options':options})
    verify = lambda path: verify_import(source, path, alignment, hybrid_options=options if hybrid else None)
    assert verify(built['stagingPath'])['status'] == 'passed'
    with ZipFile(built['stagingPath']) as z: original = {k:z.read(k) for k in z.namelist()}
    manifest = yaml.safe_load(original['manifest.yaml']); ep = manifest['song_import']['fingerBendTimingFile']
    assert json.loads(original[ep])['version'] == 21
    assert original[manifest['song_import']['sourceFile']] == source.read_bytes()
    if hybrid: assert any(a['name'] == 'Hybrid Lead' for a in manifest['arrangements'])
    for fault in ('bend', 'target', 'target-absent', 'vibrato', 'slide-start', 'slide-end', 'slide-direction', 'slide-absent',
                  'attack', 'fret', 'duration', 'extra-attack', 'evidence-target', 'evidence-policy', 'evidence-absent', 'version', 'contract'):
        files = dict(original); m = deepcopy(manifest); cp = m['arrangements'][0]['file']; chart = json.loads(files[cp]); n = chart['notes'][0]; ev = json.loads(files[ep])
        if fault == 'bend': n['bnv'][1]['t'] /= 4
        elif fault == 'target': n['harmonic_target'].update(node=7, interval=19)
        elif fault == 'target-absent': del n['harmonic_target']
        elif fault == 'vibrato': n['vibrato_marks'][0]['start'] += .1
        elif fault.startswith('slide-'):
            field = fault.split('-')[1]
            if field == 'absent': del n['slide_out_marks']
            elif field == 'direction': n['slide_out_marks'][0][field] = 'down'
            else: n['slide_out_marks'][0][field] -= .1
        elif fault == 'attack': n['t'] += .1
        elif fault == 'fret': n['f'] += 1
        elif fault == 'duration': n['sus'] -= .1
        elif fault == 'extra-attack': chart['notes'].append(deepcopy(n))
        elif fault == 'evidence-target': ev['gestures'][0]['terminalSlideOut']['continuedArtificialHarmonic']['initialTarget']['interval'] = 19
        elif fault == 'evidence-policy': ev['gestures'][0]['terminalSlideOut']['continuedArtificialHarmonic']['policy'] = 'replace-on-tie'
        elif fault == 'evidence-absent': del ev['gestures'][0]['terminalSlideOut']['continuedArtificialHarmonic']
        elif fault == 'version': ev.update(version=20, policy='songsterr-finger-bend-timing-v20')
        else: m['song_import']['preservationContract'] = 81
        files[cp] = json.dumps(chart).encode(); files[ep] = json.dumps(ev).encode(); files['manifest.yaml'] = yaml.safe_dump(m).encode()
        target = tmp_path/'mutated.feedpak'
        with ZipFile(target, 'w') as z:
            for name, data in files.items(): z.writestr(name, data)
        assert verify(target)['status'] == 'failed', fault

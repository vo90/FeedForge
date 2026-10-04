"""Separate finger clocks and explicit bar controls; no invented combined pitch."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from test_songsterr_bend_timing import checked
from feedback_converter.song_import.verification import _flatten

REFERENCE = json.loads((Path(__file__).parent/'fixtures/songsterr_explicit_bar_bend_reference.json').read_text())


def document():
    return deepcopy(REFERENCE['cases'][0]['source'])


@pytest.mark.parametrize('case', REFERENCE['cases'], ids=lambda c: c['id'])
def test_explicit_bar_does_not_change_finger_clock(case):
    source = deepcopy(case['source']); p = checked(source)
    assert source == case['source']
    evidence = p['fingerBendTimingEvidence']
    assert len(evidence) == len(case['notes'])
    for e, known in zip(evidence, case['notes']):
        assert e['status'] == 'resolved'
        assert e['barCurve']['policy'] == 'independent-explicit-control'
        n = next(n for n, _ in _flatten(p['tracks'][0]) if n['t'] == e['start'] and n['s'] == e['string'])
        assert n == known  # All non-bend output matches the captured pre-fix note.
        assert 'barVibrato' not in e
    plain = deepcopy(source)
    for m in plain['parts'][0]['measures']:
        for v in m['voices']:
            for b in v['beats']:
                b.pop('tremoloBar', None); b.pop('vibratoWithTremoloBar', None)
    no_bar = checked(plain)
    assert [e['curve'] for e in evidence] == [e['curve'] for e in no_bar['fingerBendTimingEvidence']]
    assert all('barCurve' not in e for e in no_bar['fingerBendTimingEvidence'])
    assert REFERENCE['referenceSha256'] == '4219f9dbf0952af83be36c8b590f10fea7a0f36d72bd1f92297a3d768f93f5ab'
    assert len(case['profiles']) == 2
    for profile in case['profiles']:
        assert profile['bendEventsAndTimingIndependent']
        assert profile['controllerCount']
        for sample in profile['samples']:
            n = next(n for n, _ in _flatten(p['tracks'][0])
                     if n['t'] == sample['start'] and n['s'] == sample['string'])
            curve = n['bnv']; t = sample['t']
            before = [q for q in curve if q['t'] <= t]
            after = [q for q in curve if q['t'] > t]
            a = before[-1] if before else curve[0]
            b = after[0] if after else a
            value = a['v'] if b['t'] == a['t'] else a['v'] + (b['v']-a['v'])*(t-a['t'])/(b['t']-a['t'])
            # Discrete native steps/MIDI quantization; no synth jitter imported.
            assert value == pytest.approx(sample['value'], abs=.06)


@pytest.mark.parametrize('fault', ['targeted-slide','initial-slide','later-slide','terminal-slide',
                                  'changing-overlap','settled-overlap','strum','harmonic','mute','palm-mute','beat-vibrato'])
def test_unqualified_compositions_stay_guarded(fault):
    d = document(); bs = d['parts'][0]['measures'][0]['voices'][0]['beats']
    if fault == 'targeted-slide': bs[2]['notes'][0]['slide'] = 'legato'
    elif fault == 'initial-slide': bs[0]['notes'][0]['slide'] = 'below'
    elif fault == 'later-slide': bs[1]['notes'][0]['slide'] = 'above'
    elif fault == 'terminal-slide': bs[2]['notes'][0]['slide'] = 'downwards'
    elif fault in ('changing-overlap','settled-overlap'):
        if fault == 'changing-overlap': bs[0]['notes'][0]['bend']['points'].pop(1)
        bs[2]['notes'][0]['bend'] = {'points':[{'position':0,'tone':100},{'position':60,'tone':0}]}
    elif fault == 'strum':
        bs[2]['notes'][0]['bend'] = bs[0]['notes'][0].pop('bend')
        bs[0]['brushStroke'] = {'direction':'down','duration':30,'shift':100}
        bs[0]['notes'].append({'string':1,'fret':5})
    elif fault == 'harmonic': bs[0]['notes'][0]['harmonic'] = 'pinch'
    elif fault == 'mute':
        for b in bs[:3]: b['notes'][0]['dead'] = True
    elif fault == 'palm-mute': bs[0]['palmMute'] = True
    else: bs[0]['vibrato'] = 'wide'
    e = checked(d)['fingerBendTimingEvidence'][0]
    assert e['status'] == 'deferred' and 'barCurve' not in e


def test_source_identity_is_not_a_qualification_and_ghost_is_preserved():
    d = document(); before = checked(d)
    d.update(title='Different song',songId=12345,revisionId=67)
    assert checked(d)['fingerBendTimingEvidence'] == before['fingerBendTimingEvidence']
    d['parts'][0]['measures'][0]['voices'][0]['beats'][0]['notes'][0]['ghost'] = True
    p = checked(d)
    assert p['fingerBendTimingEvidence'][0]['status'] == 'resolved'
    assert p['tracks'][0]['notes'][0]['ghost'] is True


def test_single_held_note_across_tempo_boundary_uses_same_clock():
    d = document(); part = d['parts'][0]
    beats = part['measures'][0]['voices'][0]['beats']
    beats[0]['duration'] = [3, 4]
    beats[1:3] = []
    part['automations']['tempo'].append({'measure':0,'position':480,'bpm':60,'type':4})
    p = checked(d)
    assert p['fingerBendTimingEvidence'][0]['status'] == 'resolved'
    assert len(p['fingerBendTimingEvidence'][0]['segments']) == 1
    del beats[0]['tremoloBar']
    assert p['tracks'][0]['notes'][0]['bnv'] == checked(d)['tracks'][0]['notes'][0]['bnv']

import hashlib
from zipfile import ZipFile
import yaml
from test_song_import_score import import_json
from feedback_converter.song_import.audio import ImportFailure
from feedback_converter.song_import.builder import build_feedpak
from feedback_converter.song_import.hybrid_lead import normalize_options, choose_main
from feedback_converter.song_import.score import load_performance
from feedback_converter.song_import.verification import verify_import

@pytest.mark.parametrize('piecewise', [False, True])
@pytest.mark.parametrize('hybrid', [False, True])
def test_archive_contract_and_independent_mutation_rejection(tmp_path, piecewise, hybrid):
    from test_song_import_builder import inputs
    _, audio, _, job = inputs(tmp_path)
    p = import_json(tmp_path, document()); path = tmp_path/'score.json'
    if hybrid: p = load_performance(path, composition_context=True)
    alignment = {'status':'validated', 'offset':.25, 'scale':1.}
    if piecewise:
        alignment.update(mapping='piecewise-linear', anchors=[{'score':0,'audio':.25},{'score':.5,'audio':.75},{'score':4,'audio':4.95}], tempos=[{'time':.25,'bpm':120},{'time':.75,'bpm':100}])
    options = normalize_options({'enabled':hybrid}); sha = hashlib.sha256(path.read_bytes()).hexdigest()
    if hybrid: options.update(mainTrackId=choose_main(p, options, sha), sourceSha256=sha)
    recipe = {'preservationContract':78, 'scoreHash':sha, 'audioHash':audio['hash'], **({'hybridLead':options} if hybrid else {})}
    old = tmp_path/'old'; old.mkdir()
    with pytest.raises(ImportFailure, match='contract 78'):
        build_feedpak(p, audio, alignment, old, output_dir=tmp_path/'old-out', source_path=path,
                      compatibility=p['compatibilityReport'], recipe={'preservationContract':77})
    built = build_feedpak(p, audio, alignment, job, output_dir=tmp_path/'out', source_path=path,
                         compatibility=p['compatibilityReport'], recipe=recipe,
                         hybrid_lead={'enabled':hybrid,'mainTrackId':options.get('mainTrackId'),'options':options})
    archive = Path(built['stagingPath'])
    verify = lambda f: verify_import(path, f, alignment, hybrid_options=options if hybrid else None)
    assert verify(archive)['status'] == 'passed'
    with ZipFile(archive) as z: original = {name:z.read(name) for name in z.namelist()}
    manifest = yaml.safe_load(original['manifest.yaml']); ep = manifest['song_import']['fingerBendTimingFile']
    assert json.loads(original[ep])['version'] == 18
    assert original[manifest['song_import']['sourceFile']] == path.read_bytes()
    if hybrid: assert any(a['name'] == 'Hybrid Lead' for a in manifest['arrangements'])
    for fault in ('bend','bar-time','bar-pitch','bar-intensity','bar-absent','attack','fret','duration','extra-attack',
                  'evidence-source','evidence-time','evidence-pitch','evidence-position','evidence-policy','evidence-absent','version','contract'):
        files = dict(original); m = deepcopy(manifest); cp = m['arrangements'][0]['file']
        chart = json.loads(files[cp]); n = chart['notes'][0]; ev = json.loads(files[ep])
        if fault == 'bend': n['bnv'][1]['t'] /= 3
        elif fault == 'bar-time': n['whammy']['segments'][0]['end'] += .1
        elif fault == 'bar-pitch': n['whammy']['segments'][0]['curve'] = [{'t':0,'v':1}]
        elif fault == 'bar-intensity': n['whammy']['segments'][0]['vibrato'] = 'wide'
        elif fault == 'bar-absent': del n['whammy']
        elif fault == 'attack': n['t'] += .1
        elif fault == 'fret': n['f'] += 1
        elif fault == 'duration': n['sus'] -= .1
        elif fault == 'extra-attack': chart['notes'].append(deepcopy(n))
        elif fault == 'evidence-source': ev['gestures'][0]['barCurve']['segments'][0]['sourceId'] = 'songsterr:0:0:0:3:0'
        elif fault == 'evidence-time': ev['gestures'][0]['barCurve']['segments'][0]['start'] += .1
        elif fault == 'evidence-pitch': ev['gestures'][0]['barCurve']['segments'][0]['curve'][1]['value'] += .25
        elif fault == 'evidence-position': ev['gestures'][0]['barCurve']['segments'][0]['curve'][1]['position'] = '3/4'
        elif fault == 'evidence-policy': ev['gestures'][0]['barCurve']['policy'] = 'invented-pitch'
        elif fault == 'evidence-absent': del ev['gestures'][0]['barCurve']
        elif fault == 'version': ev.update(version=17, policy='songsterr-finger-bend-timing-v17')
        else: m['song_import']['preservationContract'] = 77
        files[cp] = json.dumps(chart).encode(); files[ep] = json.dumps(ev).encode(); files['manifest.yaml'] = yaml.safe_dump(m).encode()
        target = tmp_path/'mutated.feedpak'
        with ZipFile(target, 'w') as z:
            for name, data in files.items(): z.writestr(name, data)
        assert verify(target)['status'] == 'failed', fault

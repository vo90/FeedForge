"""Qualitative bar modulation must not compress the authored finger bend."""
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

REFERENCE = json.loads((Path(__file__).parent/'fixtures/songsterr_bar_vibrato_bend_reference.json').read_text())


def document():
    return deepcopy(REFERENCE['cases'][0]['source'])


@pytest.mark.parametrize('case', REFERENCE['cases'], ids=lambda c: c['id'])
def test_independent_bend_clock_preserves_bar_controls(case):
    source = deepcopy(case['source'])
    p = checked(source)
    assert source == case['source']
    notes = p['tracks'][0]['notes']
    evidence = p['fingerBendTimingEvidence']
    assert len(evidence) == len(case['notes'])
    for e, known in zip(evidence, case['notes']):
        assert e['status'] == 'resolved'
        assert e['barVibrato']['policy'] == 'independent-qualitative-control'
        n = next(n for n in notes if n['t'] == e['start'])
        assert n == known  # Includes attack/tie, whammy regions and source identities.
        assert all(not s['curve'] for s in n['whammy']['segments'])
    plain = deepcopy(source)
    for m in plain['parts'][0]['measures']:
        for v in m['voices']:
            for b in v['beats']:
                b.pop('vibratoWithTremoloBar', None)
    assert [{k:v for k,v in n.items() if k != 'whammy'} for n in notes] == checked(plain)['tracks'][0]['notes']
    assert REFERENCE['referenceSha256'] == '4219f9dbf0952af83be36c8b590f10fea7a0f36d72bd1f92297a3d768f93f5ab'
    assert len(case['profiles']) == 2
    assert all(p['bendEventsIndependent'] and p['attackAndTieTimesIndependent'] and p['barControllerObserved'] for p in case['profiles'])


@pytest.mark.parametrize('fault', ['explicit-bar', 'held-bar', 'flat-bar', 'targeted-slide', 'initial-slide',
                                  'later-slide', 'terminal-slide', 'changing-overlap', 'settled-overlap', 'strum'])
def test_other_pitch_and_attack_interactions_remain_guarded(fault):
    d = document(); bs = d['parts'][0]['measures'][0]['voices'][0]['beats']
    if fault in ('explicit-bar', 'held-bar', 'flat-bar'):
        bs[0]['tremoloBar'] = {'points': [{'position': 0, 'tone': 0 if fault != 'held-bar' else -50},
                                        {'position': 60, 'tone': -100 if fault == 'explicit-bar' else -50 if fault == 'held-bar' else 0}]}
        if fault == 'held-bar': bs[2]['vibratoWithTremoloBar'] = 'wide'
    elif fault == 'targeted-slide': bs[2]['notes'][0]['slide'] = 'legato'
    elif fault == 'initial-slide': bs[0]['notes'][0]['slide'] = 'below'
    elif fault == 'later-slide': bs[1]['notes'][0]['slide'] = 'above'
    elif fault == 'terminal-slide': bs[2]['notes'][0]['slide'] = 'downwards'
    elif fault in ('changing-overlap', 'settled-overlap'):
        if fault == 'changing-overlap': bs[0]['notes'][0]['bend']['points'].pop(1)
        bs[2]['notes'][0]['bend'] = {'points': [{'position': 0, 'tone': 100}, {'position': 60, 'tone': 0}]}
    else:
        bs[2]['notes'][0]['bend'] = bs[0]['notes'][0].pop('bend')
        bs[0]['brushStroke'] = {'direction': 'down', 'duration': 30, 'shift': 100}
        bs[0]['notes'].append({'string': 1, 'fret': 5})
    e = checked(d)['fingerBendTimingEvidence'][0]
    assert e['status'] == 'deferred' and 'barVibrato' not in e
    assert e['reason'] == 'mixed-pitch-or-displaced-attack'


def test_no_bar_does_not_claim_rule_and_names_are_irrelevant():
    d = document(); before = checked(d)
    d.update(title='Unrelated song', songId=98765, revisionId=42)
    assert checked(d)['fingerBendTimingEvidence'] == before['fingerBendTimingEvidence']
    d['parts'][0]['measures'][0]['voices'][0]['beats'][0].pop('vibratoWithTremoloBar')
    assert 'barVibrato' not in checked(d)['fingerBendTimingEvidence'][0]


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
    recipe = {'preservationContract':75, 'scoreHash':sha, 'audioHash':audio['hash'], **({'hybridLead':options} if hybrid else {})}
    old = tmp_path/'old'; old.mkdir()
    with pytest.raises(ImportFailure, match='contract 75'):
        build_feedpak(p, audio, alignment, old, output_dir=tmp_path/'old-out', source_path=path,
                      compatibility=p['compatibilityReport'], recipe={'preservationContract':74})
    built = build_feedpak(p, audio, alignment, job, output_dir=tmp_path/'out', source_path=path,
                         compatibility=p['compatibilityReport'], recipe=recipe,
                         hybrid_lead={'enabled':hybrid,'mainTrackId':options.get('mainTrackId'),'options':options})
    archive = Path(built['stagingPath'])
    verify = lambda f: verify_import(path, f, alignment, hybrid_options=options if hybrid else None)
    assert verify(archive)['status'] == 'passed'
    with ZipFile(archive) as z: original = {name:z.read(name) for name in z.namelist()}
    manifest = yaml.safe_load(original['manifest.yaml']); ep = manifest['song_import']['fingerBendTimingFile']
    assert json.loads(original[ep])['version'] == 15
    assert original[manifest['song_import']['sourceFile']] == path.read_bytes()
    if hybrid: assert any(a['name'] == 'Hybrid Lead' for a in manifest['arrangements'])
    for fault in ('bend','bar-time','bar-pitch','bar-intensity','bar-absent','attack','fret','duration','extra-attack',
                  'evidence-source','evidence-time','evidence-policy','evidence-absent','version','contract'):
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
        elif fault == 'evidence-source': ev['gestures'][0]['barVibrato']['segments'][0]['sourceId'] = 'songsterr:0:0:0:3:0'
        elif fault == 'evidence-time': ev['gestures'][0]['barVibrato']['segments'][0]['start'] += .1
        elif fault == 'evidence-policy': ev['gestures'][0]['barVibrato']['policy'] = 'invented-pitch'
        elif fault == 'evidence-absent': del ev['gestures'][0]['barVibrato']
        elif fault == 'version': ev.update(version=14, policy='songsterr-finger-bend-timing-v14')
        else: m['song_import']['preservationContract'] = 74
        files[cp] = json.dumps(chart).encode(); files[ep] = json.dumps(ev).encode(); files['manifest.yaml'] = yaml.safe_dump(m).encode()
        target = tmp_path/'mutated.feedpak'
        with ZipFile(target, 'w') as z:
            for name, data in files.items(): z.writestr(name, data)
        assert verify(target)['status'] == 'failed', fault

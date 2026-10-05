"""Muted zero targets, per-occurrence omission proof and independent bend clocks."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile
import pytest
import yaml
from test_song_import_score import raw_score, measure, beat, import_json
from test_songsterr_bend_timing import checked
from feedback_converter.song_import.songsterr import parse
from feedback_converter.song_import.timeline import render
from feedback_converter.song_import.builder import build_feedpak
from feedback_converter.song_import.verification import verify_import
from feedback_converter.song_import.audio import ImportFailure
from feedback_converter.song_import.score import load_performance
from feedback_converter.song_import.hybrid_lead import normalize_options, choose_main


def document(kind='legato', fret=0, dead=True):
    return raw_score([measure(
        beat(9, duration=(1, 4), bend={'points': [{'position': 0, 'tone': 0}, {'position': 60, 'tone': 100}]}),
        beat(9, duration=(1, 4), tie=True, slide=kind,
             bend={'points': [{'position': 0, 'tone': 100}, {'position': 60, 'tone': 0}]}),
        beat(fret=fret, dead=dead, ghost=True, duration=(1, 2)))])


@pytest.mark.parametrize('kind', ['shift', 'legato'])
@pytest.mark.parametrize('fret', [None, 0])
def test_omission_keeps_exact_x_and_bend_without_inventing_a_target(kind, fret, tmp_path):
    doc = document(kind, fret); original = deepcopy(doc); p = checked(doc)
    a, b = p['tracks'][0]['notes']; receipt, = p['undefinedSlideEvidence']
    assert doc == original
    assert (b['t'], b['sus'], b['f'], b['mt'], b['ghost']) == (1, 1, 127 if fret is None else 0, True, True)
    assert not any(k in a for k in ('sl', 'ln', 'slide_interval', 'slide_out', 'slide_out_marks'))
    assert a['bnv'] == [{'t':0.,'v':0.},{'t':.5,'v':2.},{'t':1.,'v':0.}]
    assert receipt['target']['fret'] == fret and receipt['target']['time'] == 1
    e, = p['fingerBendTimingEvidence']
    assert e['status'] == 'resolved' and e['omittedTerminalSlide'] == receipt
    findings = import_json(tmp_path, doc)['compatibilityReport']['findings']
    assert any(f['feature'] == 'note.undefined_slide_to_mute' for f in findings)
    assert not any(f['feature'] == 'note.bend_timing' for f in findings)


@pytest.mark.parametrize('kind', ['shift', 'legato'])
@pytest.mark.parametrize('dead,fret', [(False, 0), (False, 7), (True, 7)])
def test_open_strings_and_nonzero_muted_positions_remain_real_links(kind, dead, fret):
    p = checked(document(kind, fret, dead)); a, b = p['tracks'][0]['notes']
    assert a['sl'] == fret and bool(a.get('ln')) == (kind == 'legato')
    assert bool(b.get('mt')) == dead and 'undefinedSlideEvidence' not in p
    e, = p['fingerBendTimingEvidence']
    assert 'omittedTerminalSlide' not in e
    assert e['status'] == ('deferred' if dead else 'resolved')


@pytest.mark.parametrize('kind', ['upwards', 'downwards'])
def test_explicit_direction_to_a_following_mute_is_preserved(kind):
    p = checked(document(kind)); a = p['tracks'][0]['notes'][0]
    assert a['slide_out'] == ('up' if kind == 'upwards' else 'down')
    assert 'undefinedSlideEvidence' not in p
    assert 'omittedTerminalSlide' not in p['fingerBendTimingEvidence'][0]


def test_repeated_mixed_chord_tracks_each_link_and_keeps_pitched_neighbor():
    doc = document(); m = doc['parts'][0]['measures'][0]; m.update(repeatStart=True, repeat=2)
    for i, b in enumerate(m['voices'][0]['beats']):
        b['notes'].append({'string':1,'fret':5 if i < 2 else 7,
                           **({'tie':True,'slide':'shift'} if i == 1 else {})})
    p = checked(doc)
    assert [r['occurrence'] for r in p['undefinedSlideEvidence']] == [1, 2]
    assert [e['omittedTerminalSlide']['target']['occurrence'] for e in p['fingerBendTimingEvidence']] == [1, 2]
    for c in p['tracks'][0]['chords'][::2]:
        assert next(n for n in c['notes'] if n['f'] == 5)['sl'] == 7
        assert 'sl' not in next(n for n in c['notes'] if n['f'] == 9)


def test_each_voice_retains_its_own_omitted_link():
    doc = document(); m = doc['parts'][0]['measures'][0]
    other = deepcopy(m['voices'][0])
    for b in other['beats'][:2]: b['notes'][0]['fret'] = 12
    m['voices'].append(other)
    p = checked(doc)
    assert len(p['tracks']) == 2
    assert {r['trackId'] for r in p['undefinedSlideEvidence']} == {t['id'] for t in p['tracks']}
    assert {e['fret'] for e in p['fingerBendTimingEvidence']} == {9, 12}


@pytest.mark.parametrize('field', ['attack', 'time'])
def test_omission_clock_comparison_allows_only_existing_numeric_tolerance(field):
    from feedback_converter.song_import.verification import Check
    from feedback_converter.song_import.verify_bend_timing import check_evidence
    for delta, valid in [(1e-12, True), (.001, False)]:
        check = Check()
        check_evidence({field: 169.39630203797157}, {field: 169.39630203797157 + delta}, check)
        assert (not check.errors) == valid


@pytest.mark.parametrize('fault', ['settled-overlap','changing-overlap','incoming','harmonic','whammy','vibrato','palm','gap'])
def test_omitted_slide_does_not_remove_other_bend_guards(fault):
    doc = document(); bs = doc['parts'][0]['measures'][0]['voices'][0]['beats']
    if fault.endswith('overlap'):
        bs.insert(1, beat(9, tie=True, duration=(1, 8)))
        bs[0]['duration'] = [1, 8]
        bs[0]['notes'][0]['bend']['points'][-1]['position'] = 10 if fault.startswith('settled') else 60
    elif fault == 'incoming': bs[0]['notes'][0]['slide'] = 'below'
    elif fault == 'harmonic':
        for b in bs[:2]: b['notes'][0].update(harmonic='pinch',harmonicFret=12)
    elif fault == 'whammy': bs[0]['tremoloBar'] = {'points':[{'position':0,'tone':0},{'position':60,'tone':-50}]}
    elif fault == 'vibrato': bs[0]['notes'][0]['vibrato'] = True
    elif fault == 'palm': bs[0]['palmMute'] = True
    else:
        bs[-1]['duration'] = [1, 4]
        bs.insert(2, {'rest':True,'duration':[1,4],'type':4,'notes':[{'rest':True}]})
    p = checked(doc); e = p['fingerBendTimingEvidence'][0]
    assert e['status'] == 'deferred' and 'omittedTerminalSlide' not in e
    assert p['undefinedSlideEvidence']


@pytest.mark.parametrize('fret', [None, 0])
def test_muted_shift_origin_keeps_the_destination_fret_encoding(fret):
    doc = raw_score([measure(beat(fret=None,dead=True,slide='shift',duration=(1,2)),
                             beat(fret=fret,dead=True,duration=(1,2)))])
    p = checked(doc)
    assert p['mutedSlideEvidence'][0]['target']['fret'] == fret
    assert p['undefinedSlideEvidence'][0]['target']['fret'] == fret
    assert all(n['mt'] and 'sl' not in n for n in p['tracks'][0]['notes'])


@pytest.mark.parametrize('piecewise', [False, True])
@pytest.mark.parametrize('hybrid', [False, True])
@pytest.mark.parametrize('fret', [None, 0])
def test_package_independent_proof_and_tamper_guards(tmp_path, piecewise, hybrid, fret):
    from test_song_import_builder import inputs
    _, audio, _, job = inputs(tmp_path)
    p = import_json(tmp_path, document(fret=fret)); source = tmp_path/'score.json'
    if hybrid: p = load_performance(source, composition_context=True)
    alignment = {'status':'validated','offset':.25,'scale':1.}
    if piecewise: alignment.update(mapping='piecewise-linear', anchors=[{'score':0,'audio':.25},{'score':.75,'audio':1.},{'score':4,'audio':4.9}], tempos=[{'time':.25,'bpm':120},{'time':1.,'bpm':100}])
    sha = hashlib.sha256(source.read_bytes()).hexdigest(); options = normalize_options({'enabled':hybrid})
    if hybrid: options.update(mainTrackId=choose_main(p, options, sha), sourceSha256=sha)
    recipe = {'preservationContract':84,'scoreHash':sha,'audioHash':audio['hash'], **({'hybridLead':options} if hybrid else {})}
    old = tmp_path/'old'; old.mkdir()
    with pytest.raises(ImportFailure, match='contract 84'):
        build_feedpak(p,audio,alignment,old,output_dir=tmp_path/'old-out',source_path=source,
                      compatibility=p['compatibilityReport'],recipe={'preservationContract':83})
    built = build_feedpak(p,audio,alignment,job,output_dir=tmp_path/'out',source_path=source,
                         compatibility=p['compatibilityReport'],recipe=recipe,
                         hybrid_lead={'enabled':hybrid,'mainTrackId':options.get('mainTrackId'),'options':options})
    verify = lambda archive: verify_import(source,archive,alignment,hybrid_options=options if hybrid else None)
    assert verify(built['stagingPath'])['status'] == 'passed'
    with ZipFile(built['stagingPath']) as z: original = {n:z.read(n) for n in z.namelist()}
    manifest = yaml.safe_load(original['manifest.yaml']); ep = manifest['song_import']['fingerBendTimingFile']; op = manifest['song_import']['undefinedSlidesFile']
    assert json.loads(original[ep])['version'] == 23
    assert json.loads(original[op])['version'] == (1 if fret is None else 3)
    assert original[manifest['song_import']['sourceFile']] == source.read_bytes()
    if hybrid: assert any(a['name'] == 'Hybrid Lead' for a in manifest['arrangements'])
    for fault in ('invent-slide','invent-legato','delete-x','move-x','mute','zero-encoding','bend','delete-proof','proof-target',
                  'proof-occurrence','delete-omission','receipt-target','receipt-fret','receipt-version','contract','omit-report'):
        files = dict(original); m = deepcopy(manifest); cp = m['arrangements'][0]['file']; chart = json.loads(files[cp])
        n,x = chart['notes'][:2]; ev = json.loads(files[ep]); omitted = json.loads(files[op])
        if fault == 'invent-slide': n['sl'] = 0
        elif fault == 'invent-legato': n['ln'] = True
        elif fault == 'delete-x': chart['notes'].pop(1)
        elif fault == 'move-x': x['t'] += .1
        elif fault == 'mute': x['mt'] = False
        elif fault == 'zero-encoding': x['f'] = 127 if fret == 0 else 0
        elif fault == 'bend': n['bnv'][1]['t'] /= 2
        elif fault == 'delete-proof': del ev['gestures'][0]['omittedTerminalSlide']
        elif fault == 'proof-target': ev['gestures'][0]['omittedTerminalSlide']['target']['sourceId'] += ':bad'
        elif fault == 'proof-occurrence': ev['gestures'][0]['omittedTerminalSlide']['occurrence'] += 1
        elif fault == 'receipt-target': omitted['gestures'][0]['target']['sourceId'] += ':bad'
        elif fault == 'receipt-fret': omitted['gestures'][0]['target']['fret'] = None if fret == 0 else 0
        elif fault == 'receipt-version': omitted['version'] = 0
        elif fault == 'contract': m['song_import']['preservationContract'] = 83
        elif fault == 'omit-report':
            rp = 'import/compatibility.json'; report = json.loads(files[rp])
            report['findings'] = [f for f in report['findings'] if f['feature'] != 'note.undefined_slide_to_mute']
            report['findingCount'] = len(report['findings']); files[rp] = json.dumps(report).encode()
        files[cp] = json.dumps(chart).encode(); files[ep] = json.dumps(ev).encode(); files[op] = json.dumps(omitted).encode()
        files['manifest.yaml'] = yaml.safe_dump(m).encode()
        if fault == 'delete-omission': del files[op]
        target = tmp_path/'mutated.feedpak'
        with ZipFile(target,'w') as z:
            for name,data in files.items(): z.writestr(name,data)
        assert verify(target)['status'] == 'failed', fault

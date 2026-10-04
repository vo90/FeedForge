from copy import deepcopy
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

import pytest
import yaml

from test_song_import_score import beat, measure, raw_score
from test_song_import_builder import inputs
from feedback_converter.song_import.model import ScoreImportError
from feedback_converter.song_import.songsterr import parse
from feedback_converter.song_import.timeline import render
from feedback_converter.song_import.score import load_performance
from feedback_converter.song_import.tone_timeline import export, hybrid, State
from feedback_converter.song_import.verify_source import songsterr
from feedback_converter.song_import.verify_tones import expected, compare
from feedback_converter.song_import.verification import Check, verify_import
from feedback_converter.song_import.builder import build_feedpak
from feedback_converter.song_import.audio import ImportFailure
from feedback_converter.song_import.preparation import finalize
from feedback_converter.song_import.hybrid_lead import normalize_options, choose_main

REFERENCE = json.loads((Path(__file__).parent / 'fixtures/songsterr_tone_reference.json').read_text())


@pytest.mark.parametrize('case', REFERENCE['cases'], ids=lambda c: c['id'])
def test_pinned_native_sound_schedule(case):
    assert REFERENCE['referenceSha256'] == '4219f9dbf0952af83be36c8b590f10fea7a0f36d72bd1f92297a3d768f93f5ab'
    p, _, _ = checked(case['source'])
    t = p['tracks'][0]['toneTimeline']
    names = {s['name']: s['instrumentId'] for s in t['sounds']}
    for profile in case['profiles']:
        assert t['tpqn'] == profile['tpqn']
        assert len(t['events']) == len(profile['soundAutomations'])
        for event, native in zip(t['events'], profile['soundAutomations']):
            n, d = event['quarter']
            assert n / d * profile['tpqn'] == pytest.approx(native['absoluteTick'], abs=1e-8)
            assert names[event['name']] == native['instrumentId']


def document():
    d = raw_score([measure(beat()), measure(beat(5))])
    d['tracks'][0]['instrumentId'] = 27
    d['parts'][0].update(instrumentId=27, sounds=[{'instrumentId': 27, 'label': 'Clean'},
        {'instrumentId': 30, 'label': 'Distortion'}, {'instrumentId': 29, 'label': 'Overdrive'}],
        trackAutomations={'trackSoundAutomations': [
            {'measure': 0, 'position': 960, 'soundId': 1},
            {'measure': 1, 'position': 0, 'soundId': 0},
            {'measure': 1, 'position': 1920, 'soundId': 2}]})
    return d


def checked(d, alignment=None, duration=20):
    alignment = alignment or {'offset': 0, 'scale': 1}
    original = deepcopy(d)
    performance = render(parse(d))
    source = songsterr(d)
    check = Check()
    for track, part in zip(performance['tracks'], source.parts):
        actual, proof = export(track['toneTimeline'], alignment, duration)
        wanted, independent = expected(part.tone_source, source, alignment, duration)
        compare(wanted, actual, check, 'tones')
        compare(independent, proof, check, 'proof')
    assert not check.errors, check.errors
    assert original == d
    return performance, actual, proof


@pytest.mark.parametrize('offset,scale', [(0, 1), (1.3, 1), (-.75, 1), (-2.1, 1), (.17, 1.02)])
def test_final_clock_and_initial_state(offset, scale):
    p, tones, proof = checked(document(), {'offset': offset, 'scale': scale})
    names = {s['soundId']: s['name'] for s in proof['sounds']}
    source = [(0, 0), (.5, 1), (2, 0), (3, 2)]
    initial = [sid for t, sid in source if t * scale + offset <= 0]
    assert tones['base'] == names[initial[-1] if initial else 0]
    assert tones['changes'] == [{'t': round(t * scale + offset, 6), 'name': names[sid]}
                                 for t, sid in source[1:] if t * scale + offset > 0]


def test_piecewise_positions_rest_changes_and_ending_clip():
    d = document()
    d['parts'][0]['measures'][1]['voices'][0]['beats'] = [{'duration': [1, 1], 'rest': True, 'notes': []}]
    a = {'mapping': 'piecewise-linear', 'anchors': [{'score': 0, 'audio': -1},
         {'score': 1, 'audio': 1}, {'score': 4, 'audio': 5.5}]}
    _, tones, proof = checked(d, a, duration=3)
    assert 'Distortion' in tones['base']
    assert [(x['t'], x['name'].split(' [')[0]) for x in tones['changes']] == [(2.5, 'Clean')]
    assert proof['events'][-1]['disposition'] == 'after_recording'


@pytest.mark.parametrize('pickup', [False, True])
def test_native_pickup_coordinate_known_answer(pickup):
    d = document()
    if pickup:
        d['parts'][0]['anacrusis'] = True
        d['parts'][0]['measures'][0]['voices'][0]['beats'][0]['duration'] = [1, 4]
    p, tones, _ = checked(d)
    assert tones['changes'][0]['t'] == (.125 if pickup else .5)
    assert p['tracks'][0]['toneTimeline']['events'][0]['quarter'] == ([1, 4] if pickup else [1, 1])


def test_repeat_state_does_not_reset_without_source_instruction():
    d = document()
    d['parts'][0]['measures'][0]['repeatStart'] = True
    d['parts'][0]['measures'][1]['repeat'] = 2
    d['parts'][0]['trackAutomations']['trackSoundAutomations'] = [{'measure': 1, 'position': 0, 'soundId': 1}]
    p, tones, proof = checked(d)
    assert [e['occurrence'] for e in proof['events']] == [2, 4]
    assert len(tones['changes']) == 1 and tones['changes'][0]['t'] == 2


def test_tied_boundary_uses_source_tick_guard():
    d = document()
    d['parts'][0]['measures'][0]['voices'][0]['beats'] = [beat(3, duration=(1, 4)), beat(3, duration=(3, 4), tie=True)]
    _, tones, proof = checked(d)
    assert proof['events'][0]['tieGuardTicks'] == 1
    assert tones['changes'][0]['t'] == round(.5 + .5 / proof['tpqn'], 6)


def test_same_time_order_duplicate_names_and_non_guitar_program():
    d = document()
    d['parts'][0]['sounds'][1] = {'instrumentId': 49, 'label': 'Clean'}
    d['parts'][0]['trackAutomations']['trackSoundAutomations'].insert(1, {'measure': 0, 'position': 960, 'soundId': 2})
    p, tones, proof = checked(d)
    assert p['tracks'][0]['instrument'] == 'guitar'
    assert len({s['name'] for s in proof['sounds']}) == 3
    assert tones['changes'][0]['name'].startswith('Overdrive')
    assert [r['disposition'] for r in proof['events']][:2] == ['transition', 'same_time_state']


@pytest.mark.parametrize('fault', ['program', 'missing-sound', 'position', 'measure', 'future-field', 'automation', 'nan', 'boolean'])
def test_malformed_sound_data_is_not_silently_guessed(fault):
    d = document(); p = d['parts'][0]; e = p['trackAutomations']['trackSoundAutomations'][0]
    if fault == 'program': p['sounds'][0]['instrumentId'] = 128
    elif fault == 'missing-sound': e['soundId'] = 3
    elif fault == 'position': e['position'] = -1
    elif fault == 'measure': e['measure'] = 99
    elif fault == 'future-field': e['fade'] = .3
    elif fault == 'automation': p['trackAutomations']['newSoundRule'] = 1
    elif fault == 'nan': e['position'] = float('nan')
    else: e['soundId'] = True
    with pytest.raises((ScoreImportError, ValueError)): parse(d)
    with pytest.raises((ScoreImportError, ValueError)): songsterr(d)


def test_split_voices_share_source_tone_identity(tmp_path):
    from test_songsterr_voices import document as voices
    d = voices('pitch'); d['parts'][0].update({k: v for k, v in document()['parts'][0].items() if k in ('sounds', 'instrumentId')})
    d['parts'][0]['trackAutomations'] = {'trackSoundAutomations': [{'measure': 0, 'position': 1920, 'soundId': 1}]}
    source = tmp_path / 'source.json'; source.write_text(json.dumps(d))
    p = load_performance(source)
    assert len(p['tracks']) == 2
    assert p['tracks'][0]['toneTimeline'] == p['tracks'][1]['toneTimeline']


def package(tmp_path, d=None, padding=False, piecewise=False, hybrid_on=False):
    d = d or document()
    path = tmp_path / 'score.json'; path.write_text(json.dumps(d), encoding='utf-8')
    p = load_performance(path, composition_context=hybrid_on)
    _, audio, _, job = inputs(tmp_path)
    alignment = {'status': 'validated', 'offset': 0, 'scale': 1}
    if piecewise:
        alignment.update(mapping='piecewise-linear', anchors=[{'score': 0, 'audio': 0},
            {'score': 1, 'audio': .7}, {'score': p['duration'], 'audio': p['duration']}],
            tempos=[{'time': 0, 'bpm': 120 / .7}, {'time': .7, 'bpm': 120 * (p['duration'] - 1) / (p['duration'] - .7)}])
    if padding: audio, alignment = finalize(p, audio, alignment, job)
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    options = normalize_options({'enabled': hybrid_on})
    if hybrid_on: options.update(mainTrackId=choose_main(p, options, sha), sourceSha256=sha)
    recipe = {'preservationContract': 73, 'scoreHash': sha, 'audioHash': audio['hash']}
    if padding: recipe['preparation'] = alignment['preparation']
    if hybrid_on: recipe['hybridLead'] = options
    result = build_feedpak(p, audio, alignment, job, output_dir=tmp_path / 'out', recipe=recipe,
        source_path=path, compatibility=p['compatibilityReport'],
        hybrid_lead={'enabled': hybrid_on, 'mainTrackId': options.get('mainTrackId'), 'options': options})
    return path, Path(result['stagingPath']), alignment, options


@pytest.mark.parametrize('padding,piecewise', [(False, False), (True, False), (True, True)])
def test_packaged_final_map_and_independent_corruption_checks(tmp_path, padding, piecewise):
    source, archive, alignment, _ = package(tmp_path, padding=padding, piecewise=piecewise)
    report = verify_import(source, archive, alignment)
    assert report['status'] == 'passed', report
    assert 'authored_tone_timelines' in report['scope']
    with ZipFile(archive) as z: files = {n: z.read(n) for n in z.namelist()}
    m = yaml.safe_load(files['manifest.yaml']); cp = m['arrangements'][0]['file']
    proof = json.loads(files['import/tone-timeline.json'])
    assert proof['tracks'][0]['events'][0]['audioTime'] == pytest.approx((.35 if piecewise else .5) + (2 if padding else 0))
    for fault in ('base', 'shift', 'missing-return', 'missing-tones', 'receipt', 'map-hash', 'audio-hash', 'no-receipt', 'contract'):
        bad = dict(files); chart = json.loads(bad[cp]); r = deepcopy(proof)
        if fault == 'base': chart['tones']['base'] = 'invented'
        elif fault == 'shift': chart['tones']['changes'][0]['t'] += .01
        elif fault == 'missing-return': del chart['tones']['changes'][1]
        elif fault == 'missing-tones': del chart['tones']
        elif fault == 'receipt': r['tracks'][0]['events'][0]['soundId'] = 0
        elif fault == 'map-hash': r['alignmentSha256'] = 'wrong'
        elif fault == 'audio-hash': r['audioSha256'] = 'wrong'
        elif fault == 'contract':
            manifest = deepcopy(m); manifest['song_import']['preservationContract'] = 72
            bad['manifest.yaml'] = yaml.safe_dump(manifest).encode()
        bad[cp] = json.dumps(chart).encode(); bad['import/tone-timeline.json'] = json.dumps(r).encode()
        if fault == 'no-receipt': bad.pop('import/tone-timeline.json')
        dest = tmp_path / (fault + '.feedpak')
        with ZipFile(dest, 'w') as z:
            for n, data in bad.items(): z.writestr(n, data)
        assert verify_import(source, dest, alignment)['status'] == 'failed', fault


@pytest.mark.parametrize('padding,piecewise', [(False, False), (True, True)])
def test_hybrid_enters_current_donor_state_and_restores_current_main(tmp_path, padding, piecewise):
    from test_songsterr_hybrid_lead import song
    d = song()
    for i, part in enumerate(d['parts']):
        part.update(instrumentId=27, sounds=deepcopy(document()['parts'][0]['sounds']))
        part['trackAutomations'] = {'trackSoundAutomations': [
            {'measure': 0, 'position': 1920, 'soundId': 1},
            {'measure': 1, 'position': 960, 'soundId': 2}]}
    source, archive, alignment, options = package(tmp_path, d, padding, piecewise, True)
    result = verify_import(source, archive, alignment, hybrid_options=options)
    assert result['status'] == 'passed', result
    with ZipFile(archive) as z:
        m = yaml.safe_load(z.read('manifest.yaml')); receipt = json.loads(z.read('import/hybrid-lead.json'))
        charts = {a['id']: json.loads(z.read(a['file'])) for a in m['arrangements']}
        state = State(charts[receipt['arrangementId']]['tones'])
        main = State(charts[receipt['mainTrackId']]['tones'])
        assert receipt['passages']
        for p in receipt['passages']:
            donor = State(charts[p['trackId']]['tones'])
            assert state.at(p['recordingStart']) == donor.at(p['recordingStart'])
            end = max(p['recordingEnd'], p.get('recordingOwnedEnd', p['recordingEnd']))
            if not any(q['recordingStart'] <= end < q['recordingEnd'] for q in receipt['passages']):
                assert state.at(end) == main.at(end)


def test_hybrid_detects_incompatible_overlapping_sustain():
    originals = {}
    for tid, name, program in [('main', 'Clean', 27), ('donor', 'Distortion', 30)]:
        originals[tid] = {'chart': {'tones': {'base': name, 'changes': []},
            'notes': [{'t': 0, 'sus': 3}] if tid == 'main' else [{'t': 1, 'sus': 1}], 'chords': []},
            'toneProof': {'sounds': [{'name': name, 'instrumentId': program}]}}
    plan = {'mainTrackId': 'main', 'passages': [{'trackId': 'donor', 'recordingStart': 1,
        'recordingEnd': 2, 'events': [{'kind': 'notes', 'index': 0}]}]}
    with pytest.raises(ImportFailure, match='incompatible sounds'): hybrid(plan, originals, 4)


def test_final_score_boundary_does_not_resurrect_a_change_in_silence():
    d = document()
    d['parts'][0]['trackAutomations']['trackSoundAutomations'].append({'measure': 1, 'position': 3840, 'soundId': 0})
    _, tones, proof = checked(d, duration=10)
    assert tones['changes'][-1]['name'].startswith('Overdrive')
    assert proof['events'][-1]['disposition'] == 'after_score'


def test_changes_in_skipped_ending_are_not_replayed():
    d = document(); p = d['parts'][0]
    p['measures'][0]['repeatStart'] = True
    p['measures'][1].update(repeat=2, alternateEnding=1)
    p['measures'].append(measure(beat(7), alternateEnding=2))
    _, tones, proof = checked(d)
    assert [(e['occurrence'], e['measure']) for e in proof['events']] == [(1, 0), (2, 1), (2, 1), (3, 0)]
    assert tones['changes'][-1]['t'] == 4.5


def test_quantization_collision_fails_and_constant_unknown_stays_explicit():
    d = document(); p = render(parse(d)); t = p['tracks'][0]['toneTimeline']
    with pytest.raises(ImportFailure, match='collapse'):
        export(t, {'offset': 1, 'scale': .00000001}, 10)
    d['parts'][0].pop('instrumentId'); d['tracks'][0].pop('instrumentId')
    d['tracks'][0]['instrument'] = 'Electric Guitar'
    d['parts'][0].pop('trackAutomations'); d['parts'][0].pop('sounds')
    _, tones, proof = checked(d)
    assert tones['base'].startswith('Unspecified') and tones['changes'] == []
    assert proof['initialProgram'] is None


def test_tone_export_preserves_other_chart_and_audio_payloads(tmp_path):
    source = tmp_path / 'source.json'; source.write_text(json.dumps(document()), encoding='utf-8')
    performance = load_performance(source)
    _, audio, alignment, directory = inputs(tmp_path)
    charts, recordings = [], []
    for contract in (72, 73):
        job = directory / str(contract); job.mkdir()
        built = build_feedpak(performance, audio, alignment, job, output_dir=tmp_path / 'unpublished',
                              source_path=source, compatibility=performance['compatibilityReport'],
                              recipe={'preservationContract': contract})
        with ZipFile(built['stagingPath']) as z:
            manifest = yaml.safe_load(z.read('manifest.yaml'))
            chart = json.loads(z.read(manifest['arrangements'][0]['file']))
            if contract == 73:
                assert chart.pop('tones')['changes']
            else:
                assert 'tones' not in chart
            charts.append(chart); recordings.append(z.read('audio/full.ogg'))
    assert charts[0] == charts[1]
    assert recordings[0] == recordings[1]


def test_preparation_uses_first_attack_and_keeps_earlier_tone_change(tmp_path):
    d = document()
    d['parts'][0]['measures'][0]['voices'][0]['beats'] = [
        {'duration': [1, 4], 'notes': [], 'rest': True}, beat(duration=(3, 4))]
    d['parts'][0]['trackAutomations']['trackSoundAutomations'][0]['position'] = 480
    p = render(parse(d))
    _, audio, _, job = inputs(tmp_path)
    audio, alignment = finalize(p, audio, {'status': 'validated', 'offset': .2, 'scale': 1}, job)
    tones, _ = export(p['tracks'][0]['toneTimeline'], alignment, audio['duration'])
    assert alignment['preparation']['seconds'] == pytest.approx(1.3)
    assert tones['changes'][0]['t'] == 1.75  # Before the first note at 2.0.


def test_integer_valued_json_numbers_have_the_same_sound_semantics():
    original = document(); d = deepcopy(original)
    part = d['parts'][0]; part['instrumentId'] = float(part['instrumentId'])
    for s in part['sounds']: s['instrumentId'] = float(s['instrumentId'])
    for e in part['trackAutomations']['trackSoundAutomations']:
        e.update({k: float(v) for k, v in e.items()})
    assert checked(d)[1:] == checked(original)[1:]

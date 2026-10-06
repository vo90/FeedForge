"""Independent bent attack identity, curve and source-bound package checks."""
from copy import deepcopy
import json
from pathlib import Path
from zipfile import ZipFile

import pytest
import yaml

from test_song_import_score import beat, measure, raw_score
from test_songsterr_plain_tie_verification import checked, make_package
from feedback_converter.song_import import load_performance
from feedback_converter.song_import.evidence import CONTRACT_VERSION
from feedback_converter.song_import.verification import TIME_TOLERANCE, verify_import
from feedback_converter.song_import.verify_source import songsterr
from feedback_converter.song_import.verify_timeline import expected

BEND_RULE = 'plain-tie-keeps-bent-attack-target'
RISE = {'points': [{'position': 0, 'tone': 0}, {'position': 60, 'tone': 100}]}
RELEASE = {'points': [{'position': 0, 'tone': 100}, {'position': 20, 'tone': 100},
                       {'position': 40, 'tone': 0}, {'position': 60, 'tone': 0}]}


def source(stored=0, curve=RELEASE):
    return raw_score([measure(beat(2, duration=(1, 2), bend=deepcopy(curve)),
                             beat(stored, duration=(1, 2), tie=True))])


def archived(tmp_path, files, name='changed.feedpak'):
    path = tmp_path / name
    with ZipFile(path, 'w') as stream:
        for entry, data in files.items():
            stream.writestr(entry, data)
    return path


@pytest.mark.parametrize('stored', [0, 8, 48])
@pytest.mark.parametrize('curve', [RISE, RELEASE,
    {'points': [{'position': 0, 'tone': 100}, {'position': 30, 'tone': 50}]}])
def test_independent_bent_identity_preserves_full_curve_and_written_target(tmp_path, stored, curve):
    p, v, read, _ = checked(tmp_path, source(stored, curve))
    wire = p['tracks'][0]['notes'][0]
    independently_derived = v['parts'][0]['notes'][0]['note']
    assert len(wire['bnv']) == len(independently_derived['bnv'])
    for authored, reconstructed in zip(wire['bnv'], independently_derived['bnv']):
        assert authored['t'] == pytest.approx(reconstructed['t'], abs=TIME_TOLERANCE)
        assert authored['v'] == pytest.approx(reconstructed['v'], abs=1e-9)
    assert wire['f'] == 2 and wire['sus'] == 2
    assert len(p['tracks'][0]['notes']) == 1
    assert v['plain_tie_identities'][0]['rule'] == BEND_RULE
    assert v['plain_tie_identities'][0]['authored'] == {'fret': stored}
    assert v['plain_tie_identities'][0]['used'] == {'fret': 2}
    assert read.parts[0].bars[0][1].fret == stored
    written = v['parts'][0]['notation_beats'][1]['notes'][0]
    assert (written['fret'], written['midi'], written['tied']) == (2, 66, True)
    receipt = v['finger_bends'][0]
    assert receipt['status'] == 'resolved'
    assert receipt['rule'] == 'tie-resolved-finger-bend'
    assert receipt['segments'][0]['end'] == 1
    assert receipt['segments'][0]['gestureEnd'] == 2
    assert receipt['segments'][1]['bend'] == []
    from feedback_converter.song_import.verification import Check
    from feedback_converter.song_import.verify_bend_timing import check_evidence
    check = Check()
    check_evidence(v['finger_bends'], p['fingerBendTimingEvidence'], check)
    assert check.errors == []


def test_bent_history_before_first_differing_tie_and_later_plain_ties(tmp_path):
    raw = raw_score([measure(beat(2, duration=(1, 4), bend=deepcopy(RISE)),
        beat(2, duration=(1, 4), tie=True), beat(0, duration=(1, 4), tie=True),
        beat(8, duration=(1, 4), tie=True))])
    p, v, _, _ = checked(tmp_path, raw)
    assert p['tracks'][0]['notes'][0]['bnv'] == [{'t': 0., 'v': 0.}, {'t': 2., 'v': 2.}]
    assert [r['rule'] for r in v['plain_tie_identities']] == [BEND_RULE, BEND_RULE]
    assert [r['authored']['fret'] for r in v['plain_tie_identities']] == [0, 8]


@pytest.mark.parametrize('stage,effect', [(stage, effect)
    for stage in ('origin', 'prior', 'current', 'later_equal')
    for effect in ('bend', 'vibrato', 'beat_vibrato', 'bar', 'slide', 'hopo', 'harmonic',
                   'dead', 'staccato', 'palm_mute', 'tremolo') if (stage, effect) != ('origin', 'bend')])
def test_bent_identity_does_not_qualify_extra_controllers(tmp_path, stage, effect):
    raw = raw_score([measure(beat(2, duration=(1, 4), bend=deepcopy(RISE)),
        beat(2, duration=(1, 4), tie=True), beat(0, duration=(1, 4), tie=True),
        beat(2, duration=(1, 4), tie=True))])
    beats = raw['parts'][0]['measures'][0]['voices'][0]['beats']
    target = beats[{'origin': 0, 'prior': 1, 'current': 2, 'later_equal': 3}[stage]]
    note = target['notes'][0]
    if effect == 'bend': note['bend'] = deepcopy(RELEASE)
    elif effect == 'beat_vibrato': target['vibrato'] = True
    elif effect == 'bar': target['tremoloBar'] = deepcopy(RISE)
    elif effect == 'slide': note['slide'] = 'below'
    elif effect == 'hopo': note['hp'] = True
    elif effect == 'harmonic': note['harmonic'] = 'pinch'
    elif effect == 'palm_mute': target['palmMute'] = True
    elif effect == 'tremolo': target['tremolo'] = True
    else: note[effect] = True
    if effect == 'beat_vibrato':
        control = deepcopy(raw)
        control['parts'][0]['measures'][0]['voices'][0]['beats'][
            {'origin': 0, 'prior': 1, 'current': 2, 'later_equal': 3}[stage]].pop('vibrato')
        current = expected(songsterr(raw), {'offset': 0, 'scale': 1})
        baseline = expected(songsterr(control), {'offset': 0, 'scale': 1})
        assert current['parts'][0]['notes'] == baseline['parts'][0]['notes']
        assert current['finger_bends'] == baseline['finger_bends']
        path = tmp_path / 'source.json'
        path.write_text(json.dumps(raw), encoding='utf-8')
        produced = load_performance(path)
        assert [{k: v for k, v in n.items() if k != 'source_ids'}
                for n in produced['tracks'][0]['notes']] == [r['note'] for r in current['parts'][0]['notes']]
        assert any(f['feature'] == 'beat.vibrato' for f in produced['compatibilityReport']['findings'])
        return
    with pytest.raises(ValueError):
        expected(songsterr(raw), {'offset': 0, 'scale': 1})
    path = tmp_path / 'source.json'
    path.write_text(json.dumps(raw), encoding='utf-8')
    with pytest.raises(ValueError):
        load_performance(path)


@pytest.mark.parametrize('fault', ['gap', 'rest', 'orphan', 'other_voice', 'other_string',
                                   'duplicate_origin', 'duplicate_tie', 'truthy_tie', 'truthy_history'])
def test_bent_identity_requires_literal_unique_continuous_held_origin(tmp_path, fault):
    raw = source()
    beats = raw['parts'][0]['measures'][0]['voices'][0]['beats']
    if fault == 'gap':
        beats[0]['duration'] = [1, 4]
        raw['parts'][0]['measures'].append(measure(beats.pop(1)))
    elif fault == 'rest':
        beats[0]['duration'] = beats[1]['duration'] = [1, 4]
        beats.insert(1, {'duration': [1, 4], 'rest': True, 'notes': []})
    elif fault == 'orphan': beats.pop(0)
    elif fault == 'other_voice': raw['parts'][0]['measures'][0]['voices'].append({'beats': [beats.pop(1)]})
    elif fault == 'other_string': beats[1]['notes'][0]['string'] = 1
    elif fault == 'duplicate_origin': beats[0]['notes'].append(deepcopy(beats[0]['notes'][0]))
    elif fault == 'duplicate_tie': beats[1]['notes'].append(deepcopy(beats[1]['notes'][0]))
    elif fault == 'truthy_tie': beats[1]['notes'][0]['tie'] = 1
    else:
        beats[0]['duration'] = beats[1]['duration'] = [1, 4]
        beats.insert(1, beat(2, duration=(1, 4), tie=1))
    with pytest.raises(ValueError): expected(songsterr(raw), {'offset': 0, 'scale': 1})
    path = tmp_path / 'source.json'
    path.write_text(json.dumps(raw), encoding='utf-8')
    with pytest.raises(ValueError): load_performance(path)


def test_resolved_incoming_hopo_is_not_a_plain_bent_origin(tmp_path):
    raw = raw_score([measure(beat(0, duration=(1, 4), hp=True),
        beat(2, duration=(1, 4), bend=deepcopy(RISE)), beat(0, duration=(1, 2), tie=True))])
    with pytest.raises(ValueError): expected(songsterr(raw), {'offset': 0, 'scale': 1})
    path = tmp_path / 'source.json'
    path.write_text(json.dumps(raw), encoding='utf-8')
    with pytest.raises(ValueError): load_performance(path)


def test_repeated_visits_resolve_each_bent_attack_and_source_occurrence(tmp_path):
    raw = raw_score([measure(beat(2, bend=deepcopy(RISE))),
        measure(beat(0, tie=True), repeatStart=True),
        measure(beat(7, bend=deepcopy(RELEASE)), repeat=2), measure(beat(0, tie=True))])
    p, v, _, _ = checked(tmp_path, raw)
    assert v['order'] == [0, 1, 2, 1, 2, 3]
    assert [(r['occurrence'], r['used']['fret'], r['rule']) for r in v['plain_tie_identities']] == [
        (2, 2, BEND_RULE), (4, 7, BEND_RULE), (6, 7, BEND_RULE)]
    assert [(n['t'], n['f'], n['sus']) for n in p['tracks'][0]['notes']] == [
        (0., 2, 4.), (4., 7, 4.), (8., 7, 4.)]
    from feedback_converter.song_import.verification import Check
    from feedback_converter.song_import.verify_bend_timing import check_evidence
    check = Check()
    check_evidence(v['finger_bends'], p['fingerBendTimingEvidence'], check)
    assert check.errors == []


def test_tempo_and_recording_clock_independently_map_the_complete_bent_hold(tmp_path):
    raw = source(curve=RISE)
    raw['parts'][0]['automations']['tempo'].append({'measure': 0, 'position': 960, 'bpm': 60, 'type': 4})
    alignment = {'offset': .75, 'scale': 1.25}
    path = tmp_path / 'source.json'
    path.write_text(json.dumps(raw), encoding='utf-8')
    p = load_performance(path)
    v = expected(songsterr(raw), alignment)
    from feedback_converter.song_import.builder import build_feedpak
    from test_song_import_builder import inputs
    _, audio, _, job = inputs(tmp_path)
    alignment['status'] = 'validated'
    built = build_feedpak(p, audio, alignment, job, output_dir=tmp_path / 'out', source_path=path,
        compatibility=p['compatibilityReport'], recipe={'preservationContract': CONTRACT_VERSION})
    archive = Path(built['stagingPath'])
    result = verify_import(path, archive, alignment)
    assert result['status'] == 'passed', result
    with ZipFile(archive) as stream:
        manifest = yaml.safe_load(stream.read('manifest.yaml'))
        chart = json.loads(stream.read(manifest['arrangements'][0]['file']))
    a, b = chart['notes'][0], v['parts'][0]['notes'][0]['note']
    assert (a['t'], a['f'], a['sus']) == (b['t'], b['f'], b['sus'])
    assert len(a['bnv']) == len(b['bnv'])
    for actual, wanted in zip(a['bnv'], b['bnv']):
        assert actual['t'] == pytest.approx(wanted['t'], abs=TIME_TOLERANCE)
        assert actual['v'] == pytest.approx(wanted['v'], abs=1e-9)
    assert p['plainTieIdentityEvidence'] == v['plain_tie_identities']


@pytest.mark.parametrize('fault', [None, 'ledger_rule', 'authored', 'used', 'origin', 'occurrence',
    'attack', 'ledger_clock', 'missing_curve', 'curve_value', 'curve_clock', 'bend_clock',
    'missing_bend_receipt', 'bend_source', 'bend_hash', 'missing_source', 'source_bytes',
    'extra_attack', 'notation_fret', 'notation_midi', 'contract92', 'contract_float', 'inventory92'])
def test_bent_archive_independently_binds_identity_curve_clock_notation_and_contract(tmp_path, fault):
    from feedback_converter.chart_guidance import finalize
    path, archive, alignment = make_package(tmp_path, source())
    result = verify_import(path, archive, alignment)
    assert result['status'] == 'passed', result
    assert 'bent_origin_tie_identity' in result['scope']
    assert 'finger_bend_timing' in result['scope']
    with ZipFile(archive) as stream: files = {name: stream.read(name) for name in stream.namelist()}
    manifest = yaml.safe_load(files['manifest.yaml'])
    assert manifest['song_import']['preservationContract'] == 94 == CONTRACT_VERSION
    if fault is None: return
    ledger = json.loads(files['import/plain-tie-identity.json'])
    row = ledger['continuations'][0]
    if fault == 'ledger_rule': row['rule'] = 'plain-tie-keeps-attack-target'
    if fault == 'authored': row['authored']['fret'] = 2
    if fault == 'used': row['used']['fret'] = 0
    if fault == 'origin': row['originSourceId'] = row['sourceId']
    if fault == 'occurrence': row['occurrence'] += 1
    if fault == 'attack': row['attack'] = row['start']
    if fault == 'ledger_clock': row['end'] += .01
    files['import/plain-tie-identity.json'] = json.dumps(ledger).encode()
    chart_path = manifest['arrangements'][0]['file']
    chart = json.loads(files[chart_path])
    if fault == 'missing_curve': chart['notes'][0].pop('bnv')
    if fault == 'curve_value': chart['notes'][0]['bnv'][1]['v'] -= .5
    if fault == 'curve_clock': chart['notes'][0]['bnv'][1]['t'] += .01
    if fault == 'extra_attack': chart['notes'].append({**deepcopy(chart['notes'][0]), 't': 1.})
    if fault in {'missing_curve', 'curve_value', 'curve_clock', 'extra_attack'}: finalize(chart, regenerate=True)
    files[chart_path] = json.dumps(chart).encode()
    bend_path = manifest['song_import']['fingerBendTimingFile']
    receipt = json.loads(files[bend_path])
    if fault == 'bend_clock': receipt['gestures'][0]['segments'][0]['gestureEnd'] = 1.
    if fault == 'bend_source': receipt['gestures'][0]['sourceId'] = row['sourceId']
    if fault == 'bend_hash': receipt['sourceSha256'] = '0' * 64
    files[bend_path] = json.dumps(receipt).encode()
    if fault == 'missing_bend_receipt': files.pop(bend_path)
    source_path = manifest['song_import']['sourceFile']
    if fault == 'missing_source': files.pop(source_path)
    if fault == 'source_bytes': files[source_path] = b'{}'
    if fault in {'notation_fret', 'notation_midi'}:
        notation_path = manifest['arrangements'][0]['notation']
        notation = json.loads(files[notation_path])
        note = notation['measures'][0]['staves']['staff']['voices'][0]['beats'][1]['notes'][0]
        note['fret' if fault == 'notation_fret' else 'midi'] = 0
        files[notation_path] = json.dumps(notation).encode()
    if fault == 'contract92': manifest['song_import']['preservationContract'] = 92
    if fault == 'contract_float': manifest['song_import']['preservationContract'] = 93.
    if fault == 'inventory92':
        report_path = manifest['song_import']['compatibilityFile']
        report = json.loads(files[report_path])
        report['version'] = 92
        files[report_path] = json.dumps(report).encode()
    files['manifest.yaml'] = yaml.safe_dump(manifest).encode()
    result = verify_import(path, archived(tmp_path, files), alignment)
    assert result['status'] == 'failed', result
    if fault == 'contract_float': assert 'verification_input' in {e['code'] for e in result['errors']}
    elif fault.startswith('contract'): assert 'bent_tie_contract' in {e['code'] for e in result['errors']}
    if fault == 'inventory92': assert 'bent_tie_inventory' in {e['code'] for e in result['errors']}


def test_prior92_without_new_bent_identity_remains_accepted(tmp_path):
    path, archive, alignment = make_package(tmp_path, source(stored=2))
    with ZipFile(archive) as stream: files = {name: stream.read(name) for name in stream.namelist()}
    manifest = yaml.safe_load(files['manifest.yaml'])
    manifest['song_import']['preservationContract'] = 92
    # Reconstruct the historical manifest rather than relabel a policy-94 one.
    manifest['song_import'].pop('fingerVibratoPolicy')
    report_path = manifest['song_import']['compatibilityFile']
    report = json.loads(files[report_path])
    report['version'] = 92
    files[report_path] = json.dumps(report).encode()
    files['manifest.yaml'] = yaml.safe_dump(manifest).encode()
    result = verify_import(path, archived(tmp_path, files), alignment)
    assert result['status'] == 'passed', result
    assert 'bent_origin_tie_identity' not in result['scope']


@pytest.mark.parametrize('contract,inventory,retained,ordinary,allowed', [
    (90, 93, True, False, False), (92, 93, True, False, False),
    (93., 93, True, False, False), (93, 92, True, False, False),
    (93, 93., True, False, False), (93, 93, False, False, False),
    (93, 93, True, False, True), (90, 90, True, True, True)])
def test_builder_new_rule_has_typed93_gate_and_preserves_ordinary90(tmp_path, contract, inventory, retained, ordinary, allowed):
    from feedback_converter.song_import.builder import build_feedpak
    from feedback_converter.song_import.audio import ImportFailure
    from test_song_import_builder import inputs
    raw = source(curve=None) if ordinary else source()
    performance, _wanted, _source, path = checked(tmp_path, raw)
    compatibility = deepcopy(performance['compatibilityReport'])
    compatibility['version'] = inventory
    _, audio, _, job = inputs(tmp_path)
    alignment = {'status': 'validated', 'offset': 0, 'scale': 1}
    arguments = dict(output_dir=tmp_path / 'out', source_path=path if retained else None,
        compatibility=compatibility, recipe={'preservationContract': contract})
    if not allowed:
        reason = 'Invalid preservation contract' if type(contract) is not int else (
            'Bent-origin tie identity' if retained else 'original')
        with pytest.raises(ImportFailure, match=reason):
            build_feedpak(performance, audio, alignment, job, **arguments)
        return
    built = build_feedpak(performance, audio, alignment, job, **arguments)
    result = verify_import(path, Path(built['stagingPath']), alignment)
    assert result['status'] == 'passed', result


@pytest.mark.parametrize('tamper', [False, True])
def test_hybrid_borrowed_bent_tie_preserves_original_target_and_curve(tmp_path, tamper):
    from test_songsterr_hybrid_lead import song, prepared
    from test_song_import_builder import inputs
    from feedback_converter.song_import.builder import build_feedpak
    raw = song()
    raw['parts'][1]['measures'][1]['voices'][0]['beats'] = [beat(7, duration=(1, 4), bend=deepcopy(RISE)),
        beat(0, duration=(1, 4), tie=True), {'duration': [1, 2], 'rest': True, 'notes': []}]
    path, performance, options = prepared(tmp_path, raw)
    _, audio, _, job = inputs(tmp_path)
    alignment = {'status': 'validated', 'offset': 0, 'scale': 1}
    built = build_feedpak(performance, audio, alignment, job, output_dir=tmp_path / 'out', source_path=path,
        compatibility=performance['compatibilityReport'],
        recipe={'preservationContract': CONTRACT_VERSION, 'source': 'songsterr', 'scoreHash': options['sourceSha256'],
                'audioHash': audio['hash'], 'hybridLead': options},
        hybrid_lead={'enabled': True, 'mainTrackId': options['mainTrackId'], 'options': options})
    archive = Path(built['stagingPath'])
    result = verify_import(path, archive, alignment, hybrid_options=options)
    assert result['status'] == 'passed', result
    with ZipFile(archive) as stream: files = {name: stream.read(name) for name in stream.namelist()}
    manifest = yaml.safe_load(files['manifest.yaml'])
    ledger = json.loads(files['import/plain-tie-identity.json'])
    assert ledger['continuations'][0]['trackId'] == '1'
    assert ledger['continuations'][0]['rule'] == BEND_RULE
    chart_path = manifest['arrangements'][-1]['file']
    chart = json.loads(files[chart_path])
    held = next(n for n in chart['notes'] if n['t'] == 2 and n['f'] == 7)
    assert held['bnv'] == [{'t': 0., 'v': 0.}, {'t': 1., 'v': 2.}]
    if tamper:
        held['bnv'][-1]['t'] = .5
        from feedback_converter.chart_guidance import finalize
        finalize(chart, regenerate=True)
        files[chart_path] = json.dumps(chart).encode()
        result = verify_import(path, archived(tmp_path, files), alignment, hybrid_options=options)
        assert result['status'] == 'failed', result
        assert 'hybrid_chart' in {e['code'] for e in result['errors']}

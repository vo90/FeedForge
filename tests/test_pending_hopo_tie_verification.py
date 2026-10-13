"""Independent pending outgoing HP consumption and source-bound archive checks."""
from copy import deepcopy
from fractions import Fraction as F
import hashlib
import io
import json
from pathlib import Path
from zipfile import ZipFile

import pytest
import yaml

from test_song_import_score import beat, measure, raw_score
from feedback_converter.song_import import load_performance
from feedback_converter.song_import.builder import build_feedpak
from feedback_converter.song_import.evidence import CONTRACT_VERSION
from feedback_converter.song_import.verification import verify_import
from feedback_converter.song_import.verify_source import songsterr
from feedback_converter.song_import.verify_timeline import expected

POLICY = 'songsterr-pending-hopo-immediate-plain-tie-v1'
SIDECAR = 'import/pending-hopo-ties.json'


def source(stored=0, *, incoming=False, vibrato=False, terminal=False):
    beats = [beat(2, string=2, duration=(1, 16), hp=True),
             beat(stored, string=2, duration=(1, 8), tie=True)]
    if not terminal:
        beats.append(beat(0, string=2, duration=(1, 8)))
    if incoming:
        beats.insert(0, beat(0, string=2, duration=(1, 16), hp=True))
    if vibrato:
        beats[int(incoming)]['notes'][0]['vibrato'] = True
    raw = raw_score([measure(*beats)])
    raw['tracks'][0]['instrumentId'] = 24
    raw['parts'][0].update(version=5, capo=4)
    return raw


def oracle(raw, **options):
    return expected(songsterr(raw), {'offset': 0, 'scale': 1}, **options)


@pytest.mark.parametrize('stored', [0, 2, 3])
@pytest.mark.parametrize('incoming,vibrato,terminal', [(False, False, False), (True, False, False),
                                                      (False, True, False), (False, False, True)])
def test_independent_consumption_keeps_target_and_real_incoming_link(stored, incoming, vibrato, terminal):
    raw = source(stored, incoming=incoming, vibrato=vibrato, terminal=terminal)
    original = deepcopy(raw)
    result = oracle(raw)
    notes = [item['note'] for item in result['parts'][0]['notes']]
    held = notes[int(incoming)]
    assert held['f'] == 2 and held['sus'] == .375
    assert 'ln' not in held
    if incoming:
        assert notes[0]['ln'] is True and held['ho'] is True
    if vibrato:
        assert held['vb'] is True
        assert held['vibrato_marks'][0]['intensity'] == 'slight'
    if not terminal:
        assert notes[-1]['f'] == 0
        assert not any(key in notes[-1] for key in ('ho', 'po', 'ln'))
    row = result['pending_hopo_ties'][0]
    marker = int(incoming)
    tie = marker + 1
    attack = .125 * marker
    assert row == {'trackId': '0', 'sourceId': f'songsterr:0:0:0:{tie}:0',
                   'originSourceId': f'songsterr:0:0:0:{marker}:0',
                   'markerSourceId': f'songsterr:0:0:0:{marker}:0',
                   'location': f'parts/0/measures/0/voices/0/beats/{tie}/notes/0',
                   'markerLocation': f'parts/0/measures/0/voices/0/beats/{marker}/notes/0',
                   'occurrence': 1, 'markerOccurrence': 1, 'voice': 0, 'string': 3,
                   'attack': attack, 'markerStart': attack, 'markerEnd': attack + .125,
                   'start': attack + .125, 'end': attack + .375,
                   'authored': {'hp': True, 'tie': True, 'fret': stored},
                   'used': {'fret': 2, 'outgoingHopo': False},
                   'rule': 'pending-hopo-consumed-by-immediate-plain-tie'}
    written = result['parts'][0]['notation_beats'][tie]['notes'][0]
    assert written == {'str': 3, 'fret': 2, 'tied': True, 'midi': 61}
    assert raw == original


@pytest.mark.parametrize('stored', [0, 2])
def test_historical_contract_uses_original_pending_interpretation(stored):
    raw = source(stored)
    if stored == 0:
        with pytest.raises(ValueError):
            oracle(raw, pending_hopo_ties=False)
    else:
        result = oracle(raw, pending_hopo_ties=False)
        notes = [item['note'] for item in result['parts'][0]['notes']]
        assert result['pending_hopo_ties'] == []
        assert notes[0]['ln'] is True and notes[-1]['po'] is True


@pytest.mark.parametrize('contract', [90, 93, 94, 95])
@pytest.mark.parametrize('controller', [False, True])
def test_opening_repair_oracle_respects_archive_tie_interpretation(contract, controller):
    from feedback_converter.song_import import local_sync
    raw = source(2)
    if controller:
        raw['parts'][0]['measures'][0]['voices'][0]['beats'][1]['notes'][0]['vibrato'] = True
    independent = songsterr(raw)
    if contract == 95 and controller:
        with pytest.raises(ValueError):
            local_sync.independent_tracks(independent)
        return
    tracks, boundaries = local_sync.independent_tracks(independent, pending_hopo_ties=contract >= 95)
    assert boundaries == [0., 2.]
    events = tracks[0]['events']
    assert len(events) == 2 and events[0]['midi'] == 61
    if contract < 95:
        assert events[0]['effects']['ln'] is True and events[1]['effects']['po'] is True
    else:
        assert not any(k in event['effects'] for event in events for k in ('ln', 'ho', 'po'))


@pytest.mark.parametrize('contract', [94, 95])
@pytest.mark.parametrize('controller', [False, True])
def test_verify_opening_repair_forwards_contract_semantics_before_audio_checks(monkeypatch, contract, controller):
    from feedback_converter.song_import import local_sync
    from feedback_converter.song_import.verification import Check
    raw = source(2)
    if controller:
        raw['parts'][0]['measures'][0]['voices'][0]['beats'][1]['notes'][0]['vibrato'] = True
    independent = songsterr(raw)
    calibration = {'referenceHz': 440.}
    stored = {'version': local_sync.REPAIR, 'status': 'supported', 'tuning': calibration,
              'audioSha256': hashlib.sha256(b'').hexdigest()}
    alignment = {'openingRepair': stored, 'sourceTiming': {'points': []}}
    seen = []
    monkeypatch.setattr(local_sync, 'tuning', lambda path: calibration)
    monkeypatch.setattr(local_sync, '_hash', lambda path: stored['audioSha256'])
    monkeypatch.setattr(local_sync.rs, 'features', lambda *args, **kwargs: (None, None, None))
    def propose(tracks, *args):
        seen.extend(tracks[0]['events'])
        return {'version': local_sync.REPAIR, 'status': 'supported'}
    monkeypatch.setattr(local_sync, 'propose', propose)
    check = Check()
    if contract == 95 and controller:
        with pytest.raises(ValueError):
            local_sync.verify_repair(independent, alignment, stored, io.BytesIO(b''), check)
        assert seen == []
        return
    local_sync.verify_repair(independent, alignment, stored, io.BytesIO(b''), check,
                             pending_hopo_ties=contract >= 95)
    assert not check.errors
    assert bool(seen[-1]['effects'].get('po')) is (contract < 95)


@pytest.mark.parametrize('field,value', [('hp', 1), ('hp', 'true'), ('tie', 1), ('tie', 'true')])
def test_truthy_nonliteral_flags_never_enter_new_consumption(field, value):
    raw = source(2)
    index = 0 if field == 'hp' else 1
    raw['parts'][0]['measures'][0]['voices'][0]['beats'][index]['notes'][0][field] = value
    try:
        result = oracle(raw)
    except ValueError:
        return
    assert result['pending_hopo_ties'] == []


@pytest.mark.parametrize('fault', ['rest', 'gap', 'overlap', 'duplicate_tie', 'cross_voice', 'cross_string',
                                  'origin_bend', 'tie_bend', 'origin_slide', 'tie_slide', 'origin_harmonic',
                                  'tie_harmonic', 'tie_vibrato', 'tie_hopo', 'tie_staccato'])
def test_independent_predicate_rejects_unqualified_immediate_consumption(fault):
    raw = source(2)
    beats = raw['parts'][0]['measures'][0]['voices'][0]['beats']
    origin, tie = beats[0]['notes'][0], beats[1]['notes'][0]
    if fault == 'rest':
        beats.insert(1, {'duration': [1, 16], 'rest': True, 'notes': []})
    if fault == 'duplicate_tie': beats[1]['notes'].append(deepcopy(tie))
    if fault == 'cross_string': tie['string'] = 1
    if fault == 'cross_voice':
        raw['parts'][0]['measures'][0]['voices'].append({'beats': [deepcopy(beats[0]), deepcopy(beats[1])]})
        raw['parts'][0]['measures'][0]['voices'][0]['beats'] = [beats[0], beats[2]]
        raw['parts'][0]['measures'][0]['voices'][1]['beats'][0] = {'duration': [1, 16], 'rest': True, 'notes': []}
    if fault.endswith('_bend'):
        (origin if fault.startswith('origin') else tie)['bend'] = {'points': [{'position': 0, 'tone': 0},
                                                                              {'position': 60, 'tone': 100}]}
    if fault.endswith('_slide'): (origin if fault.startswith('origin') else tie)['slide'] = 'shift'
    if fault.endswith('_harmonic'): (origin if fault.startswith('origin') else tie)['harmonic'] = 'pinch'
    if fault == 'tie_vibrato': tie['vibrato'] = True
    if fault == 'tie_hopo': tie['hp'] = True
    if fault == 'tie_staccato': tie['staccato'] = True
    independently_read = songsterr(raw)
    if fault in ('gap', 'overlap'):
        independently_read.parts[0].bars[0][1].q += F(1, 8) if fault == 'gap' else -F(1, 8)
    with pytest.raises(ValueError):
        expected(independently_read, {'offset': 0, 'scale': 1})


def test_marker_is_bound_to_last_held_segment_and_repeat_visit():
    raw = source()
    beats = raw['parts'][0]['measures'][0]['voices'][0]['beats']
    beats[0]['notes'][0].pop('hp')
    beats.insert(1, beat(2, string=2, duration=(1, 16), hp=True, tie=True))
    raw['parts'][0]['measures'][0].update(repeatStart=True, repeat=2)
    result = oracle(raw)
    rows = result['pending_hopo_ties']
    assert len(rows) == 2
    assert {r['markerSourceId'] for r in rows} == {'songsterr:0:0:0:1:0'}
    assert {r['originSourceId'] for r in rows} == {'songsterr:0:0:0:0:0'}
    assert [(r['occurrence'], r['markerOccurrence']) for r in rows] == [(1, 1), (2, 2)]
    assert [(r['attack'], r['markerStart'], r['start']) for r in rows] == [(0., .125, .25), (2., 2.125, 2.25)]


def test_pending_link_cannot_cross_repeat_jump():
    raw = source(terminal=True)
    beats = raw['parts'][0]['measures'][0]['voices'][0]['beats']
    raw['parts'][0]['measures'] = [measure(beats[1], repeatStart=True), measure(beats[0], repeat=2)]
    with pytest.raises(ValueError):
        oracle(raw)


@pytest.mark.parametrize('later', ['vibrato', 'harmonic', 'fresh_hopo'])
def test_later_continuations_keep_existing_separate_technique_rules(later):
    raw = source()
    beats = raw['parts'][0]['measures'][0]['voices'][0]['beats']
    fields = {'vibrato': {'vibrato': True}, 'harmonic': {'harmonic': 'pinch'}, 'fresh_hopo': {'hp': True}}[later]
    beats.insert(2, beat(2, string=2, duration=(1, 8), tie=True, **fields))
    result = oracle(raw)
    assert len(result['pending_hopo_ties']) == 1
    notes = [r['note'] for r in result['parts'][0]['notes']]
    assert len(notes) == 2 and notes[0]['sus'] == .625
    if later == 'vibrato':
        assert notes[0]['vibrato_marks'] == [{'start': .375, 'end': .625, 'intensity': 'slight'}]
    if later == 'harmonic': assert len(result['harmonic_ties']) == 1
    if later == 'fresh_hopo': assert notes[0]['ln'] is True and notes[1]['po'] is True


def package(tmp_path, raw):
    from test_song_import_builder import inputs
    path = tmp_path / 'source.json'
    path.write_text(json.dumps(raw), encoding='utf-8')
    performance = load_performance(path)
    independent = oracle(raw)
    assert performance.get('pendingHopoTieEvidence', []) == independent['pending_hopo_ties']
    _, audio, _, job = inputs(tmp_path)
    alignment = {'status': 'validated', 'offset': 0, 'scale': 1}
    built = build_feedpak(performance, audio, alignment, job, output_dir=tmp_path / 'out',
                         source_path=path, compatibility=performance['compatibilityReport'],
                         recipe={'preservationContract': CONTRACT_VERSION})
    return path, Path(built['stagingPath']), alignment


def read_archive(archive):
    with ZipFile(archive) as stream:
        files = {name: stream.read(name) for name in stream.namelist()}
    return files, yaml.safe_load(files['manifest.yaml'])


def write_archive(tmp_path, files, manifest):
    files['manifest.yaml'] = yaml.safe_dump(manifest).encode()
    changed = tmp_path / 'changed.feedpak'
    with ZipFile(changed, 'w') as stream:
        for name, data in files.items(): stream.writestr(name, data)
    return changed


def test_independent_verifier_does_not_call_producer_consumption_helpers(tmp_path, monkeypatch):
    from feedback_converter.song_import import pending_hopo_ties
    path, archive, alignment = package(tmp_path, source())
    def forbidden(*args, **kwargs):
        raise AssertionError('Verifier reused a producer consumption decision.')
    monkeypatch.setattr(pending_hopo_ties, 'admitted', forbidden)
    monkeypatch.setattr(pending_hopo_ties, 'record', forbidden)
    result = verify_import(path, archive, alignment)
    assert result['status'] == 'passed', result


def test_voice_projection_keeps_consumption_bound_to_original_source(tmp_path):
    raw = source()
    raw['parts'][0]['measures'][0]['voices'].append({'beats': [beat(8, string=2, duration=(1, 8))]})
    path, archive, alignment = package(tmp_path, raw)
    files, _manifest = read_archive(archive)
    row = json.loads(files[SIDECAR])['consumptions'][0]
    assert row['trackId'] == 'voice-' + hashlib.sha256(b'0').hexdigest()[:20] + '-1'
    assert row['voice'] == 0 and row['markerSourceId'] == 'songsterr:0:0:0:0:0'
    result = verify_import(path, archive, alignment)
    assert result['status'] == 'passed', result


@pytest.mark.parametrize('fault', [None, 'missing', 'reference', 'policy', 'hash', 'origin', 'marker',
                                  'source', 'markerLocation', 'markerOccurrence', 'markerStart', 'markerEnd',
                                  'occurrence', 'voice', 'string', 'attack', 'start', 'end', 'raw_target',
                                  'effective_target', 'raw_hp', 'raw_tie', 'outgoing_hopo', 'duplicate',
                                  'extra', 'inventory', 'compatibility_missing', 'compatibility_boolean',
                                  'compatibility_work_status', 'compatibility_track', 'invented_po', 'outgoing_ln',
                                  'incoming_hopo', 'incoming_ln', 'held_fret', 'held_sustain',
                                  'notation_fret', 'notation_midi'])
def test_archive_requires_typed_bound_receipt_and_correct_projection(tmp_path, fault):
    from feedback_converter.chart_guidance import finalize
    path, archive, alignment = package(tmp_path, source(incoming=True, vibrato=True))
    passed = verify_import(path, archive, alignment)
    assert passed['status'] == 'passed', passed
    assert 'pending_hopo_immediate_plain_tie' in passed['scope']
    files, manifest = read_archive(archive)
    assert manifest['song_import']['pendingHopoTieFile'] == SIDECAR
    assert manifest['song_import']['pendingHopoTiePolicy'] == POLICY
    assert files[manifest['song_import']['sourceFile']] == path.read_bytes()
    if fault is None:
        return
    receipt = json.loads(files[SIDECAR])
    row = receipt['consumptions'][0]
    if fault == 'hash': receipt['sourceSha256'] = '0' * 64
    if fault in ('origin', 'marker', 'source'):
        row[{'origin': 'originSourceId', 'marker': 'markerSourceId', 'source': 'sourceId'}[fault]] = 'invented'
    if fault == 'markerLocation': row[fault] = 'invented'
    if fault in ('markerOccurrence', 'occurrence', 'voice', 'string'): row[fault] += 1
    if fault in ('markerStart', 'markerEnd', 'attack', 'start', 'end'): row[fault] += .01
    if fault == 'raw_target': row['authored']['fret'] = 9
    if fault == 'effective_target': row['used']['fret'] = 9
    if fault == 'raw_hp': row['authored']['hp'] = 1
    if fault == 'raw_tie': row['authored']['tie'] = 1
    if fault == 'outgoing_hopo': row['used']['outgoingHopo'] = 0
    if fault == 'duplicate': receipt['consumptions'] *= 2
    if fault == 'extra': receipt['invented'] = True
    files[SIDECAR] = json.dumps(receipt).encode()
    if fault == 'missing': files.pop(SIDECAR)
    if fault == 'reference': manifest['song_import'].pop('pendingHopoTieFile')
    if fault == 'policy': manifest['song_import']['pendingHopoTiePolicy'] = 'invented'
    compatibility_path = manifest['song_import']['compatibilityFile']
    compatibility = json.loads(files[compatibility_path])
    limitation = next(r for r in compatibility['findings'] if r['feature'] == 'note.pending_hopo_tie')
    if fault == 'inventory': compatibility['version'] = 94
    if fault == 'compatibility_missing':
        compatibility['findings'].remove(limitation)
        compatibility['findingCount'] -= 1
    if fault == 'compatibility_boolean': limitation['value']['used']['outgoingHopo'] = 0
    if fault == 'compatibility_work_status': limitation['workStatus'] = 'done'
    if fault == 'compatibility_track': limitation['trackId'] = 'invented'
    files[compatibility_path] = json.dumps(compatibility).encode()
    chart_path = manifest['arrangements'][0]['file']
    chart = json.loads(files[chart_path])
    if fault == 'invented_po': chart['notes'][-1]['po'] = True
    if fault == 'outgoing_ln': chart['notes'][1]['ln'] = True
    if fault == 'incoming_hopo': chart['notes'][1].pop('ho')
    if fault == 'incoming_ln': chart['notes'][0].pop('ln')
    if fault == 'held_fret': chart['notes'][1]['f'] = 0
    if fault == 'held_sustain': chart['notes'][1]['sus'] = .125
    if fault in ('invented_po', 'outgoing_ln', 'incoming_hopo', 'incoming_ln', 'held_fret', 'held_sustain'):
        finalize(chart, regenerate=True)
    files[chart_path] = json.dumps(chart).encode()
    if fault in ('notation_fret', 'notation_midi'):
        notation_path = manifest['arrangements'][0]['notation']
        notation = json.loads(files[notation_path])
        written = notation['measures'][0]['staves']['staff']['voices'][0]['beats'][2]['notes'][0]
        written['fret' if fault == 'notation_fret' else 'midi'] = 0
        files[notation_path] = json.dumps(notation).encode()
    changed = write_archive(tmp_path, files, manifest)
    result = verify_import(path, changed, alignment)
    assert result['status'] == 'failed', result


@pytest.mark.parametrize('referenced', [False, True])
def test_empty_invented_receipt_cannot_opt_in_nonqualifying_source(tmp_path, referenced):
    raw = source(2)
    raw['parts'][0]['measures'][0]['voices'][0]['beats'][0]['notes'][0].pop('hp')
    from test_song_import_builder import inputs
    path = tmp_path / 'source.json'
    path.write_text(json.dumps(raw), encoding='utf-8')
    performance = load_performance(path)
    _, audio, _, job = inputs(tmp_path)
    alignment = {'status': 'validated', 'offset': 0, 'scale': 1}
    archive = Path(build_feedpak(performance, audio, alignment, job, output_dir=tmp_path / 'out',
                               source_path=path, compatibility=performance['compatibilityReport'],
                               recipe={'preservationContract': CONTRACT_VERSION})['stagingPath'])
    files, manifest = read_archive(archive)
    files[SIDECAR] = json.dumps({'version': 1, 'policy': POLICY, 'timeDomain': 'score_seconds',
                                'sourceSha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                                'consumptions': []}).encode()
    if referenced:
        manifest['song_import'].update(pendingHopoTieFile=SIDECAR, pendingHopoTiePolicy=POLICY)
    result = verify_import(path, write_archive(tmp_path, files, manifest), alignment)
    assert result['status'] == 'failed', result
    assert 'pending_hopo_tie_extra' in {r['code'] for r in result['errors']}


@pytest.mark.parametrize('key', ['pendingHopoTieFile', 'pendingHopoTiePolicy'])
@pytest.mark.parametrize('value', [None, False, '', 0])
def test_inactive_extra_label_without_consumption_is_rejected(tmp_path, key, value):
    raw = source(2)
    raw['parts'][0]['measures'][0]['voices'][0]['beats'][0]['notes'][0].pop('hp')
    path, archive, alignment = package(tmp_path, raw)
    files, manifest = read_archive(archive)
    assert SIDECAR not in files
    manifest['song_import'][key] = value
    result = verify_import(path, write_archive(tmp_path, files, manifest), alignment)
    assert result['status'] == 'failed', result
    assert 'pending_hopo_tie_extra' in {r['code'] for r in result['errors']}


def historical_package(tmp_path, contract):
    from feedback_converter.chart_guidance import finalize
    path, archive, alignment = package(tmp_path, source(2))
    files, manifest = read_archive(archive)
    recipe = manifest['song_import']
    recipe['preservationContract'] = contract
    recipe.pop('pendingHopoTiePolicy')
    recipe.pop('pendingHopoTieFile')
    if contract < 94: recipe.pop('fingerVibratoPolicy')
    files.pop(SIDECAR)
    compatibility = json.loads(files[recipe['compatibilityFile']])
    compatibility['version'] = contract
    compatibility['findings'] = [r for r in compatibility['findings'] if r['feature'] != 'note.pending_hopo_tie']
    compatibility['findingCount'] = len(compatibility['findings'])
    compatibility['status'] = 'limitations' if compatibility['findings'] else 'compatible'
    files[recipe['compatibilityFile']] = json.dumps(compatibility).encode()
    chart_path = manifest['arrangements'][0]['file']
    chart = json.loads(files[chart_path])
    chart['notes'][0]['ln'] = True
    chart['notes'][1]['po'] = True
    finalize(chart, regenerate=True)
    files[chart_path] = json.dumps(chart).encode()
    notation_path = manifest['arrangements'][0]['notation']
    notation = json.loads(files[notation_path])
    notation['measures'][0]['staves']['staff']['voices'][0]['beats'][2]['notes'][0]['po'] = True
    files[notation_path] = json.dumps(notation).encode()
    return path, write_archive(tmp_path, files, manifest), alignment


@pytest.mark.parametrize('contract', [90, 93, 94])
def test_historical_archive_with_delayed_pull_keeps_original_expectation(tmp_path, contract):
    path, archive, alignment = historical_package(tmp_path, contract)
    result = verify_import(path, archive, alignment)
    assert result['status'] == 'passed', result
    assert 'pending_hopo_immediate_plain_tie' not in result['scope']


@pytest.mark.parametrize('contract', [94, 95])
def test_archive_opening_repair_passes_declared_tie_contract(tmp_path, monkeypatch, contract):
    from feedback_converter.song_import import local_sync
    if contract < 95:
        path, archive, alignment = historical_package(tmp_path, contract)
    else:
        path, archive, alignment = package(tmp_path, source(2))
    files, manifest = read_archive(archive)
    receipt = {'version': local_sync.REPAIR, 'status': 'supported'}
    alignment = {**alignment, 'openingRepair': receipt}
    manifest['song_import']['openingRepairFile'] = 'import/opening-repair.json'
    files['import/opening-repair.json'] = json.dumps(receipt).encode()
    seen = []
    def check_route(independent, mapping, retained, recording, check, *, pending_hopo_ties=True,
                    pending_slide_ties=True, preservation_contract=0, rest_bridge_ties=None):
        seen.append(pending_hopo_ties)
        assert preservation_contract == manifest['song_import']['preservationContract']
        tracks, _ = local_sync.independent_tracks(independent, pending_hopo_ties=pending_hopo_ties,
            pending_slide_ties=pending_slide_ties,
            rest_bridge_ties=preservation_contract >= 122 if rest_bridge_ties is None else rest_bridge_ties)
        assert bool(tracks[0]['events'][-1]['effects'].get('po')) is (contract < 95)
    monkeypatch.setattr(local_sync, 'verify_repair', check_route)
    result = verify_import(path, write_archive(tmp_path, files, manifest), alignment)
    assert result['status'] == 'passed', result
    assert seen == [contract >= 95]


def test_contract_label_cannot_opt_94_archive_into_new_consumption(tmp_path):
    path, archive, alignment = package(tmp_path, source(2))
    files, manifest = read_archive(archive)
    manifest['song_import']['preservationContract'] = 94
    result = verify_import(path, write_archive(tmp_path, files, manifest), alignment)
    assert result['status'] == 'failed', result
    assert 'pending_hopo_tie_contract' in {r['code'] for r in result['errors']}

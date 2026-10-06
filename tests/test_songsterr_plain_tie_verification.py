"""Independent held-target reconstruction and source-bound plain tie receipts."""
from copy import deepcopy
from fractions import Fraction as F
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

POLICY = 'songsterr-plain-tie-identity-v1'


def source(origin=7, stored=0):
    return raw_score([measure(beat(origin, duration=(1, 2)), beat(stored, duration=(1, 2), tie=True))])


def checked(tmp_path, raw):
    original = deepcopy(raw)
    path = tmp_path / 'source.json'
    path.write_text(json.dumps(raw), encoding='utf-8')
    p = load_performance(path)
    independently_read = songsterr(raw)
    v = expected(independently_read, {'offset': 0, 'scale': 1})
    assert p.get('plainTieIdentityEvidence', []) == v['plain_tie_identities']
    actual = sorted([n for t in p['tracks'] for n in t['notes']] + [
        {**n, 't': c['t']} for t in p['tracks'] for c in t['chords'] for n in c['notes']],
        key=lambda n: (n['t'], n['s']))
    wanted = sorted([n['note'] for part in v['parts'] for n in part['notes']], key=lambda n: (n['t'], n['s']))
    assert len(actual) == len(wanted)
    for a, b in zip(actual, wanted):
        assert (a['s'], a['f']) == (b['s'], b['f'])
        assert a['t'] == pytest.approx(b['t'], abs=1e-6)
        assert a['sus'] == pytest.approx(b['sus'], abs=1e-6)
    assert raw == original
    return p, v, independently_read, path


@pytest.mark.parametrize('origin,stored', [(7, 0), (7, 8), (0, 7), (48, 0), (0, 0), (7, 7)])
@pytest.mark.parametrize('version', [None, 5, 8])
def test_origin_target_notation_and_raw_identity_are_independent(tmp_path, origin, stored, version):
    raw = source(origin, stored)
    if version is not None:
        raw['parts'][0]['version'] = version
    p, v, independent, _ = checked(tmp_path, raw)
    assert p['tracks'][0]['notes'][0]['f'] == origin
    assert len(p['tracks'][0]['notes']) == 1
    assert p['tracks'][0]['notes'][0]['sus'] == 2
    assert independent.parts[0].bars[0][1].fret == stored
    written = v['parts'][0]['notation_beats'][1]['notes'][0]
    assert written['fret'] == origin and written['tied'] is True
    assert written['midi'] == 64 + origin
    rows = v['plain_tie_identities']
    if origin == stored:
        assert rows == []
    else:
        assert rows == [{'trackId': '0', 'sourceId': 'songsterr:0:0:0:1:0', 'originSourceId': 'songsterr:0:0:0:0:0',
                         'location': 'parts/0/measures/0/voices/0/beats/1/notes/0', 'occurrence': 1, 'voice': 0,
                         'string': 5, 'attack': 0., 'start': 1., 'end': 2., 'authored': {'fret': stored},
                         'used': {'fret': origin}, 'rule': 'plain-tie-keeps-attack-target'}]


@pytest.mark.parametrize('stored', [0, 8])
@pytest.mark.parametrize('first,last', [(7, 9), (0, 9), (7, 0)])
def test_repeat_visits_resolve_same_source_id_to_each_live_origin(tmp_path, stored, first, last):
    raw = raw_score([measure(beat(first)), measure(beat(stored, tie=True), repeatStart=True),
                     measure(beat(last), repeat=2), measure(beat(stored, tie=True))])
    p, v, independent, _ = checked(tmp_path, raw)
    assert v['order'] == [0, 1, 2, 1, 2, 3]
    actual = p['tracks'][0]['notes']
    assert [(n['t'], n['f'], n['sus']) for n in actual] == [(0., first, 4.), (4., last, 4.), (8., last, 4.)]
    resolved = [b['notes'][0]['fret'] for b in v['parts'][0]['notation_beats']]
    assert resolved == [first, first, last, last, last, last]
    assert independent.parts[0].bars[1][0].fret == stored
    rows = v['plain_tie_identities']
    assert [(r['occurrence'], r['used']['fret']) for r in rows] == [
        (visit, fret) for visit, fret in ((2, first), (4, last), (6, last)) if fret != stored]


def test_unfilled_gap_keeps_existing_tied_endpoint(tmp_path):
    raw = raw_score([measure(beat(7, duration=(1, 8))), measure(beat(0, duration=(1, 8), tie=True))])
    p, v, _, _ = checked(tmp_path, raw)
    assert p['tracks'][0]['notes'][0]['sus'] == 2.25
    assert v['plain_tie_identities'][0]['start'] == 2
    assert v['plain_tie_identities'][0]['end'] == 2.25


def test_each_differing_continuation_keeps_the_first_attack_target(tmp_path):
    raw = raw_score([measure(beat(7, duration=(1, 4)), beat(0, duration=(1, 4), tie=True),
                             beat(7, duration=(1, 4), tie=True), beat(8, duration=(1, 4), tie=True))])
    p, v, _, _ = checked(tmp_path, raw)
    assert [(n['f'], n['sus']) for n in p['tracks'][0]['notes']] == [(7, 2.)]
    assert [b['notes'][0]['fret'] for b in v['parts'][0]['notation_beats']] == [7] * 4
    assert [r['authored']['fret'] for r in v['plain_tie_identities']] == [0, 8]
    assert {r['originSourceId'] for r in v['plain_tie_identities']} == {'songsterr:0:0:0:0:0'}


@pytest.mark.parametrize('expression', ['note_vibrato', 'beat_vibrato', 'incoming_slide'])
def test_origin_expression_and_completed_entry_survive(tmp_path, expression):
    raw = source()
    first = raw['parts'][0]['measures'][0]['voices'][0]['beats'][0]
    if expression == 'note_vibrato':
        first['notes'][0]['vibrato'] = True
    elif expression == 'beat_vibrato':
        first['vibrato'] = True
    else:
        first['notes'][0]['slide'] = 'below'
    p, v, _, _ = checked(tmp_path, raw)
    assert len(v['plain_tie_identities']) == 1
    assert p['tracks'][0]['notes'][0]['f'] == 7


@pytest.mark.parametrize('fault', ['missing', 'voice', 'string', 'rest', 'duplicate_origin', 'duplicate_tie',
                                   'not_boolean_int', 'not_boolean_string', 'continuation_bend', 'continuation_slide',
                                   'continuation_hopo', 'continuation_harmonic', 'continuation_dead', 'continuation_vibrato',
                                   'continuation_bar', 'continuation_trill', 'origin_bar', 'origin_trill',
                                   'origin_harmonic', 'origin_dead', 'origin_terminal_slide', 'origin_pending_slide', 'origin_pending_hopo'])
def test_new_interpretation_retains_unqualified_continuity_and_expression_guards(tmp_path, fault):
    raw = source()
    beats = raw['parts'][0]['measures'][0]['voices'][0]['beats']
    first, tied = beats[0], beats[1]
    if fault == 'missing':
        beats.pop(0)
    elif fault == 'voice':
        raw['parts'][0]['measures'][0]['voices'].append({'beats': [beats.pop(1)]})
    elif fault == 'string':
        tied['notes'][0]['string'] = 1
    elif fault == 'rest':
        first['duration'] = tied['duration'] = [1, 4]
        beats.insert(1, {'duration': [1, 4], 'notes': [{'rest': True}], 'rest': True})
    elif fault == 'duplicate_origin':
        first['notes'].append(deepcopy(first['notes'][0]))
    elif fault == 'duplicate_tie':
        tied['notes'].append(deepcopy(tied['notes'][0]))
    elif fault.startswith('not_boolean'):
        tied['notes'][0]['tie'] = 1 if fault.endswith('int') else 'true'
    else:
        destination = first if fault.startswith('origin') else tied
        kind = fault.removeprefix('origin_').removeprefix('continuation_')
        if kind == 'bend':
            destination['notes'][0]['bend'] = {'points': [{'position': 0, 'tone': 0}, {'position': 60, 'tone': 100}]}
        elif kind == 'bar':
            destination['tremoloBar'] = {'points': [{'position': 0, 'tone': 0}, {'position': 60, 'tone': 100}]}
        elif kind == 'trill':
            destination['notes'][0]['trill'] = {'auxiliaryFret': 9, 'speed': 60}
        else:
            fields = {'slide': ('slide', 'below'), 'hopo': ('hp', True), 'harmonic': ('harmonic', 'pinch'),
                      'dead': ('dead', True), 'vibrato': ('vibrato', True), 'terminal_slide': ('slide', 'upwards'),
                      'pending_slide': ('slide', 'shift'), 'pending_hopo': ('hp', True)}
            key, value = fields[kind]
            destination['notes'][0][key] = value
    with pytest.raises(ValueError):
        expected(songsterr(raw), {'offset': 0, 'scale': 1})
    path = tmp_path / 'source.json'
    path.write_text(json.dumps(raw), encoding='utf-8')
    with pytest.raises(ValueError):
        load_performance(path)


def test_overlap_stays_unresolved_without_source_repair(tmp_path):
    from feedback_converter.song_import.songsterr import parse
    from feedback_converter.song_import.timeline import render
    raw = source()
    parsed = parse(raw)
    parsed.tracks[0].bars[0][1].position = F(1)
    independently_read = songsterr(raw)
    independently_read.parts[0].bars[0][1].q = F(1)
    with pytest.raises(ValueError):
        render(parsed)
    with pytest.raises(ValueError):
        expected(independently_read, {'offset': 0, 'scale': 1})


def make_package(tmp_path, raw):
    from test_song_import_builder import inputs
    p, v, independent, path = checked(tmp_path, raw)
    _, audio, _, job = inputs(tmp_path)
    alignment = {'status': 'validated', 'offset': 0, 'scale': 1}
    built = build_feedpak(p, audio, alignment, job, output_dir=tmp_path / 'out', source_path=path,
                         compatibility=p['compatibilityReport'], recipe={'preservationContract': CONTRACT_VERSION})
    return path, Path(built['stagingPath']), alignment


@pytest.mark.parametrize('fault', [None, 'missing', 'reference', 'policy', 'hash', 'source', 'origin', 'target', 'authored',
                                   'occurrence', 'voice', 'string', 'time', 'boolean', 'extra', 'count', 'duplicate',
                                   'contract', 'attack', 'fret', 'sustain', 'notation_fret', 'notation_midi'])
def test_source_bound_receipt_and_notation_reject_corruption(tmp_path, fault):
    from feedback_converter.chart_guidance import finalize
    path, archive, alignment = make_package(tmp_path, source())
    passed = verify_import(path, archive, alignment)
    assert passed['status'] == 'passed', passed
    assert 'plain_tie_identity' in passed['scope']
    with ZipFile(archive) as stream:
        files = {name: stream.read(name) for name in stream.namelist()}
    manifest = yaml.safe_load(files['manifest.yaml'])
    assert manifest['song_import']['plainTieIdentityFile'] == 'import/plain-tie-identity.json'
    assert manifest['song_import']['plainTieIdentityPolicy'] == POLICY
    assert files[manifest['song_import']['sourceFile']] == path.read_bytes()
    if fault is None:
        return
    receipt = json.loads(files['import/plain-tie-identity.json'])
    row = receipt['continuations'][0]
    if fault == 'hash': receipt['sourceSha256'] = '0' * 64
    if fault == 'source': row['sourceId'] = 'invented'
    if fault == 'origin': row['originSourceId'] = 'invented'
    if fault == 'target': row['used']['fret'] = 0
    if fault == 'authored': row['authored']['fret'] = 9
    if fault == 'occurrence': row['occurrence'] += 1
    if fault == 'voice': row['voice'] += 1
    if fault == 'string': row['string'] -= 1
    if fault == 'time': row['start'] += .01
    if fault == 'boolean': row['authored']['fret'] = False
    if fault == 'extra': receipt['unexpected'] = True
    if fault == 'count': receipt['continuations'] = []
    if fault == 'duplicate': receipt['continuations'] *= 2
    files['import/plain-tie-identity.json'] = json.dumps(receipt).encode()
    if fault == 'missing': files.pop('import/plain-tie-identity.json')
    if fault == 'reference': manifest['song_import'].pop('plainTieIdentityFile')
    if fault == 'policy': manifest['song_import']['plainTieIdentityPolicy'] = 'wrong'
    if fault == 'contract': manifest['song_import']['preservationContract'] = 89
    chart_path = manifest['arrangements'][0]['file']
    chart = json.loads(files[chart_path])
    if fault == 'attack': chart['notes'].append(dict(deepcopy(chart['notes'][0]), t=1.))
    if fault == 'fret': chart['notes'][0]['f'] = 0
    if fault == 'sustain': chart['notes'][0]['sus'] = 1.
    if fault in {'attack', 'fret', 'sustain'}:
        finalize(chart, regenerate=True)
    files[chart_path] = json.dumps(chart).encode()
    if fault in {'notation_fret', 'notation_midi'}:
        notation_path = manifest['arrangements'][0]['notation']
        notation = json.loads(files[notation_path])
        written = notation['measures'][0]['staves']['staff']['voices'][0]['beats'][1]['notes'][0]
        written['fret' if fault == 'notation_fret' else 'midi'] = 0
        files[notation_path] = json.dumps(notation).encode()
    files['manifest.yaml'] = yaml.safe_dump(manifest).encode()
    changed = tmp_path / 'changed.feedpak'
    with ZipFile(changed, 'w') as stream:
        for name, data in files.items(): stream.writestr(name, data)
    result = verify_import(path, changed, alignment)
    assert result['status'] == 'failed', result
    codes = {error['code'] for error in result['errors']}
    if fault == 'contract': assert 'plain_tie_contract' in codes
    if fault in {'notation_fret', 'notation_midi'}: assert 'notation_note' in codes


@pytest.mark.parametrize('referenced', [False, True])
def test_unreferenced_invented_plain_sidecar_is_not_ignored(tmp_path, referenced):
    import hashlib
    path, archive, alignment = make_package(tmp_path, source(7, 7))
    with ZipFile(archive) as stream: files = {name: stream.read(name) for name in stream.namelist()}
    assert 'import/plain-tie-identity.json' not in files
    files['import/plain-tie-identity.json'] = json.dumps({
        'version': 1, 'policy': POLICY, 'timeDomain': 'score_seconds',
        'sourceSha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'continuations': []}).encode()
    if referenced:
        manifest = yaml.safe_load(files['manifest.yaml'])
        manifest['song_import']['plainTieIdentityPolicy'] = POLICY
        manifest['song_import']['plainTieIdentityFile'] = 'import/plain-tie-identity.json'
        files['manifest.yaml'] = yaml.safe_dump(manifest, sort_keys=False).encode()
    changed = tmp_path / 'changed.feedpak'
    with ZipFile(changed, 'w') as stream:
        for name, data in files.items(): stream.writestr(name, data)
    result = verify_import(path, changed, alignment)
    assert result['status'] == 'failed'
    assert 'plain_tie_extra' in {error['code'] for error in result['errors']}


def test_prior_brush_contract_remains_accepted_without_plain_identity_evidence(tmp_path):
    path, archive, alignment = make_package(tmp_path, source(7, 7))
    with ZipFile(archive) as stream: files = {name: stream.read(name) for name in stream.namelist()}
    manifest = yaml.safe_load(files['manifest.yaml'])
    manifest['song_import']['preservationContract'] = 89
    manifest['song_import'].pop('fingerVibratoPolicy')
    files['manifest.yaml'] = yaml.safe_dump(manifest, sort_keys=False).encode()
    changed = tmp_path / 'historical.feedpak'
    with ZipFile(changed, 'w') as stream:
        for name, data in files.items(): stream.writestr(name, data)
    result = verify_import(path, changed, alignment)
    assert result['status'] == 'passed', result


def test_prior_plain_tie_contract_and_inventory_remain_accepted(tmp_path):
    path, archive, alignment = make_package(tmp_path, source())
    with ZipFile(archive) as stream: files = {name: stream.read(name) for name in stream.namelist()}
    manifest = yaml.safe_load(files['manifest.yaml'])
    manifest['song_import']['preservationContract'] = 90
    manifest['song_import'].pop('fingerVibratoPolicy')
    files['manifest.yaml'] = yaml.safe_dump(manifest, sort_keys=False).encode()
    report_path = manifest['song_import']['compatibilityFile']
    report = json.loads(files[report_path])
    report['version'] = 90
    files[report_path] = json.dumps(report).encode()
    changed = tmp_path / 'historical-plain-tie.feedpak'
    with ZipFile(changed, 'w') as stream:
        for name, data in files.items(): stream.writestr(name, data)
    result = verify_import(path, changed, alignment)
    assert result['status'] == 'passed', result


@pytest.mark.parametrize('tamper', [False, True])
def test_hybrid_borrowed_tie_and_notation_share_the_original_target(tmp_path, tamper):
    from test_songsterr_hybrid_lead import song, prepared
    from test_song_import_builder import inputs
    from feedback_converter.chart_guidance import finalize
    raw = song()
    raw['parts'][1]['measures'][1]['voices'][0]['beats'] = [
        beat(7, duration=(1, 4)), beat(0, duration=(1, 4), tie=True), {'duration': [1, 2], 'rest': True, 'notes': []}]
    path, p, options = prepared(tmp_path, raw)
    _, audio, _, job = inputs(tmp_path)
    alignment = {'status': 'validated', 'offset': 0, 'scale': 1}
    built = build_feedpak(p, audio, alignment, job, output_dir=tmp_path / 'out', source_path=path,
                         compatibility=p['compatibilityReport'],
                         recipe={'preservationContract': CONTRACT_VERSION, 'source': 'songsterr', 'scoreHash': options['sourceSha256'],
                                 'audioHash': audio['hash'], 'hybridLead': options},
                         hybrid_lead={'enabled': True, 'mainTrackId': options['mainTrackId'], 'options': options})
    archive = Path(built['stagingPath'])
    result = verify_import(path, archive, alignment, hybrid_options=options)
    assert result['status'] == 'passed', result
    with ZipFile(archive) as stream: files = {name: stream.read(name) for name in stream.namelist()}
    manifest = yaml.safe_load(files['manifest.yaml'])
    receipt = json.loads(files['import/plain-tie-identity.json'])
    assert receipt['continuations'][0]['trackId'] == '1'
    derived_path = manifest['arrangements'][-1]['file']
    derived = json.loads(files[derived_path])
    held = next(n for n in derived['notes'] if n['t'] == 2 and n['f'] == 7)
    assert held['sus'] == 1
    assert receipt['continuations'][0]['originSourceId'] != receipt['continuations'][0]['sourceId']
    if tamper:
        held['f'] = 0
        finalize(derived, regenerate=True)
        files[derived_path] = json.dumps(derived).encode()
        changed = tmp_path / 'changed.feedpak'
        with ZipFile(changed, 'w') as stream:
            for name, data in files.items(): stream.writestr(name, data)
        result = verify_import(path, changed, alignment, hybrid_options=options)
        assert result['status'] == 'failed', result
        assert 'hybrid_chart' in {error['code'] for error in result['errors']}

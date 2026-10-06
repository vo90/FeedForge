"""Independent source facts and archive proof for the literal legacy mute alias."""
from copy import deepcopy
import hashlib
import json
from zipfile import ZipFile

import pytest
import yaml

from feedback_converter.song_import import load_performance
from feedback_converter.song_import.audio import ImportFailure
from feedback_converter.song_import.builder import build_feedpak
from feedback_converter.song_import.compatibility import inspect_songsterr
from feedback_converter.song_import.evidence import CONTRACT_VERSION
from feedback_converter.song_import.songsterr import parse
from feedback_converter.song_import.timeline import render
from feedback_converter.song_import.verification import Check, _chords, _flatten, _notes, verify_import
from feedback_converter.song_import.verify_source import songsterr
from feedback_converter.song_import.verify_timeline import expected
from test_song_import_builder import inputs
from test_song_import_score import beat, measure, raw_score

POLICY = 'songsterr-negative-fret-mute-v1'


def muted(fret=-1, duration=(1, 1), **fields):
    return beat(fret, duration=duration, dead=True, **fields)


def independent_check(raw):
    before = deepcopy(raw)
    source = songsterr(raw)
    wanted = expected(source, {'offset': 0, 'scale': 1})
    performance = render(parse(raw))
    check = Check()
    for track, part in zip(performance['tracks'], wanted['parts']):
        _notes(part['notes'], _flatten(track), check, part['source'], performance['duration'])
        _chords(part['notes'], track, check, part['source'])
    assert not check.errors, check.errors
    assert performance.get('mutedTieIdentityEvidence', []) == wanted['muted_tie_identities']
    assert raw == before
    return source, wanted, performance


def package(tmp_path, raw, contract=CONTRACT_VERSION):
    path = tmp_path / 'source.json'
    path.write_text(json.dumps(raw), encoding='utf-8')
    original = path.read_bytes()
    performance = load_performance(path)
    _, audio, _, job = inputs(tmp_path)
    alignment = {'status': 'validated', 'offset': .25, 'scale': 1.25}
    built = build_feedpak(performance, audio, alignment, job, output_dir=tmp_path / 'out',
        source_path=path, compatibility=performance['compatibilityReport'],
        recipe={'preservationContract': contract})
    with ZipFile(built['stagingPath']) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    manifest = yaml.safe_load(files['manifest.yaml'])
    assert files[manifest['song_import']['sourceFile']] == path.read_bytes() == original
    return path, files, manifest, alignment


def verify(tmp_path, path, files, alignment, **kwargs):
    target = tmp_path / 'checked.feedpak'
    with ZipFile(target, 'w') as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return verify_import(path, target, alignment, **kwargs)


@pytest.mark.parametrize('program,tuning', [(29, [64, 59, 55, 50, 45, 40]), (33, [43, 38, 33, 28])])
@pytest.mark.parametrize('slot', [0, 2])
@pytest.mark.parametrize('fields', [{}, {'ghost': True}, {'accentuated': True}, {'staccato': True},
                                   {'tremolo': True}, {'leftFingering': '2'}])
def test_independent_alias_keeps_instruction_without_fret_pitch_or_raw_mutation(program, tuning, slot, fields):
    raw = raw_score([measure(muted(string=slot, **fields))], tuning)
    raw['tracks'][0].update(instrumentId=program, capo=4)
    source, wanted, performance = independent_check(raw)
    atom = source.parts[0].bars[0][0]
    assert source.negative_fret_mutes and atom.authored_fret == -1
    assert atom.fret == 127 and atom.effects['mt'] is True
    written = wanted['parts'][0]['notation_beats'][0]['notes'][0]
    assert written['fret'] == 127 and written['dead'] is True and 'midi' not in written
    assert performance['source']['negativeFretMutePolicy'] == POLICY
    assert 'notation' not in performance['tracks'][0]
    row = next(r for r in inspect_songsterr(raw)['findings'] if r['feature'] == 'note.negative_fret_mute')
    assert type(row['value']) is int and row['value'] == -1
    assert row['impact'] == row['workStatus'] == 'source_retained'
    assert row['category'] == 'source_interpretation' and row['retained'] == 'original_source'


@pytest.mark.parametrize('fret', [-1.0, '-1', '-1/1', [-1, 1], [-2, 2], {'value': -1},
                                  -2, -1.5, True, False, 127])
def test_alias_does_not_coerce_fret_shapes(fret):
    raw = raw_score([measure(muted(fret))])
    with pytest.raises(ValueError):
        expected(songsterr(raw), {'offset': 0, 'scale': 1})
    with pytest.raises(ValueError):
        render(parse(raw))


@pytest.mark.parametrize('dead', [None, False, 0, 1, 'true', [], {}])
def test_negative_fret_requires_literal_dead_true(dead):
    raw = raw_score([measure(muted())])
    raw['parts'][0]['measures'][0]['voices'][0]['beats'][0]['notes'][0]['dead'] = dead
    with pytest.raises(ValueError): songsterr(raw)
    assert inspect_songsterr(raw)['status'] == 'blocked'


@pytest.mark.parametrize('slot', [-1, 6, True, None, .5, [], {}])
def test_alias_requires_a_valid_physical_string(slot):
    raw = raw_score([measure(muted(string=slot))])
    with pytest.raises(ValueError): expected(songsterr(raw), {'offset': 0, 'scale': 1})
    with pytest.raises(ValueError): render(parse(raw))
    assert inspect_songsterr(raw)['status'] == 'blocked'


@pytest.mark.parametrize('fields', [{'hp': True}, {'harmonic': 'pinch'}, {'vibrato': True},
    {'wideVibrato': True}, {'leftHandVibrato': 'wide'}, {'slide': 'above'},
    {'bend': {'points': [{'position': 0, 'tone': 0}, {'position': 60, 'tone': 100}]}}])
def test_note_pitch_gestures_remain_guarded(fields):
    raw = raw_score([measure(muted(**fields))])
    with pytest.raises(ValueError): expected(songsterr(raw), {'offset': 0, 'scale': 1})
    with pytest.raises(ValueError): render(parse(raw))
    assert inspect_songsterr(raw)['status'] == 'blocked'


@pytest.mark.parametrize('field,value', [('vibrato', True), ('wideVibrato', True),
    ('tremoloBar', {'points': [{'position': 0, 'tone': 0}, {'position': 60, 'tone': 100}]}),
    ('vibratoWithTremoloBar', 'slight')])
def test_only_new_alias_rejects_unqualified_inherited_beat_expression(field, value):
    first = muted()
    first[field] = value
    raw = raw_score([measure(first)])
    with pytest.raises(ValueError): expected(songsterr(raw), {'offset': 0, 'scale': 1})
    with pytest.raises(ValueError): render(parse(raw))
    assert inspect_songsterr(raw)['status'] == 'blocked'
    first['notes'][0]['fret'] = None
    assert not songsterr(raw).negative_fret_mutes
    assert 'negativeFretMutePolicy' not in parse(raw).source


@pytest.mark.parametrize('all_muted', [False, True])
def test_chord_presence_is_distinct_from_an_omitted_string(tmp_path, all_muted):
    first = muted()
    first['notes'].append({'string': 1, 'fret': -1 if all_muted else 7,
                           **({'dead': True} if all_muted else {})})
    _, _, performance = independent_check(raw_score([measure(first)]))
    track = performance['tracks'][0]
    chord = track['chords'][0]
    assert len(chord['notes']) == 2
    assert sorted(n['f'] for n in chord['notes']) == ([127, 127] if all_muted else [7, 127])
    path, files, manifest, alignment = package(tmp_path, raw_score([measure(first)]))
    chart = json.loads(files[manifest['arrangements'][0]['file']])
    frets = chart['templates'][0]['frets']
    assert frets.count(-1) == 4 and frets.count(127) == (2 if all_muted else 1)
    assert verify(tmp_path, path, files, alignment)['status'] == 'passed'


@pytest.mark.parametrize('origin,continuation,authored,used', [(0, -1, -1, 0), (-1, 0, 0, None),
                                                           (-1, -1, None, None), (-1, None, None, None)])
def test_muted_tie_retains_negative_authorship_separately_from_effective_target(origin, continuation, authored, used):
    raw = raw_score([measure(muted(origin, (1, 2)), muted(continuation, (1, 2), tie=True))])
    source, wanted, performance = independent_check(raw)
    assert source.negative_fret_mutes
    assert len(performance['tracks'][0]['notes']) == 1
    assert performance['tracks'][0]['notes'][0]['sus'] == 2
    rows = wanted['muted_tie_identities']
    if origin == 0 or continuation == 0:
        assert rows[0]['authored'] == {'dead': True, 'fret': authored}
        assert rows[0]['used'] == {'dead': True, 'fret': used}
    else:
        assert rows == []
    assert wanted['plain_tie_identities'] == []


@pytest.mark.parametrize('fault', ['rest', 'gap', 'orphan', 'voice', 'string', 'duplicate', 'pitched_origin', 'late_gesture'])
def test_independent_alias_does_not_repair_unsafe_muted_ties(fault):
    first, second = muted(0, (1, 2)), muted(-1, (1, 2), tie=True)
    raw = raw_score([measure(first, second)])
    beats = raw['parts'][0]['measures'][0]['voices'][0]['beats']
    if fault in ('rest', 'gap'):
        first['duration'] = [1, 4]
        beats.insert(1, {'duration': [1, 4], 'notes': [{'rest': True}]} if fault == 'rest'
                     else beat(7, string=1, duration=(1, 4)))
    elif fault == 'orphan': beats.pop(0)
    elif fault == 'voice':
        beats.pop(1)
        raw['parts'][0]['measures'][0]['voices'].append({'beats': [second]})
    elif fault == 'string': second['notes'][0]['string'] = 1
    elif fault == 'duplicate': first['notes'].append(deepcopy(first['notes'][0]))
    elif fault == 'pitched_origin': first['notes'][0].pop('dead')
    elif fault == 'late_gesture':
        beats[:] = [muted(0, (1, 4)), muted(-1, (1, 4), tie=True),
                    muted(0, (1, 2), tie=True, slide='upwards')]
    with pytest.raises(ValueError): expected(songsterr(raw), {'offset': 0, 'scale': 1})
    with pytest.raises(ValueError): render(parse(raw))


@pytest.mark.parametrize('origin,continuation', [(0, -1), (-1, 0)])
def test_independent_repeat_tie_provenance_uses_each_performed_visit(origin, continuation):
    raw = raw_score([measure(muted(origin), repeatStart=True),
                     measure(muted(continuation, tie=True), repeat=2)])
    _, wanted, _ = independent_check(raw)
    rows = wanted['muted_tie_identities']
    assert [r['occurrence'] for r in rows] == [2, 4]
    assert [r['attack'] for r in rows] == [0, 4]
    assert all(r['authored']['fret'] == continuation for r in rows)
    assert all(r['used']['fret'] == (None if origin == -1 else origin) for r in rows)


@pytest.mark.parametrize('continuation', [None, -1])
def test_duplicate_muted_origins_are_guarded_for_old_and_new_encodings(continuation):
    first = muted(0, (1, 2))
    first['notes'].append(deepcopy(first['notes'][0]))
    raw = raw_score([measure(first, muted(continuation, (1, 2), tie=True))])
    with pytest.raises(ValueError, match='Simultaneous'): expected(songsterr(raw), {'offset': 0, 'scale': 1})
    with pytest.raises(ValueError, match='Simultaneous'): render(parse(raw))


def test_same_string_attacks_in_distinct_authored_voices_remain_separate():
    raw = raw_score([measure(muted())])
    raw['parts'][0]['measures'][0]['voices'].append({'beats': [beat(7)]})
    _, wanted, performance = independent_check(raw)
    assert len(performance['tracks']) == len(wanted['parts']) == 2
    assert [t['notes'][0]['f'] for t in performance['tracks']] == [127, 7]


@pytest.mark.parametrize('mode', ['note_rest', 'drums', 'duplicate_drum_id', 'diagnostic_excluded'])
def test_alias_marker_and_findings_only_apply_to_admitted_selected_notes(mode):
    raw = raw_score([measure(beat(7))])
    candidate = raw_score([measure(muted())])['parts'][0]
    if mode == 'note_rest':
        candidate['measures'][0]['voices'][0]['beats'][0]['notes'][0]['rest'] = True
        raw['parts'][0] = candidate
    else:
        raw['tracks'].append({**deepcopy(raw['tracks'][0]), 'id': 0 if mode == 'duplicate_drum_id' else 1,
                              'instrumentId': 29 if mode == 'diagnostic_excluded' else 128})
        raw['parts'].append(candidate)
    selection = [0] if mode == 'diagnostic_excluded' else None
    source = songsterr(raw, track_indices=selection)
    assert not source.negative_fret_mutes
    assert not any(r['feature'] == 'note.negative_fret_mute'
                   for r in inspect_songsterr(raw, track_indices=selection)['findings'])
    assert 'negativeFretMutePolicy' not in parse(raw, track_indices=selection).source


@pytest.mark.parametrize('fault', ['none', 'contract', 'contract_bool', 'policy_missing', 'policy_wrong',
    'inventory', 'finding_missing', 'finding_duplicate', 'value', 'value_type', 'location',
    'category', 'impact', 'work_status', 'retention', 'source_bytes', 'fret', 'mute', 'string',
    'attack', 'duration', 'extra_attack', 'notation'])
def test_archive_requires_typed_alias_ledger_policy_source_and_actual_mute(tmp_path, fault):
    path, files, manifest, alignment = package(tmp_path, raw_score([measure(muted())]))
    assert manifest['song_import']['negativeFretMutePolicy'] == POLICY
    report_path = manifest['song_import']['compatibilityFile']
    report = json.loads(files[report_path])
    row = next(r for r in report['findings'] if r['feature'] == 'note.negative_fret_mute')
    if fault == 'contract': manifest['song_import']['preservationContract'] = 91
    elif fault == 'contract_bool': manifest['song_import']['preservationContract'] = True
    elif fault == 'policy_missing': manifest['song_import'].pop('negativeFretMutePolicy')
    elif fault == 'policy_wrong': manifest['song_import']['negativeFretMutePolicy'] = 'invented'
    elif fault == 'inventory': report['version'] = 91
    elif fault == 'finding_missing': report['findings'].remove(row)
    elif fault == 'finding_duplicate': report['findings'].append(deepcopy(row))
    elif fault == 'value': row['value'] = None
    elif fault == 'value_type': row['value'] = -1.0
    elif fault == 'location': row['location'] += '/invented'
    elif fault == 'category': row['category'] = 'source_metadata'
    elif fault == 'impact': row['impact'] = 'display_or_expression'
    elif fault == 'work_status': row['workStatus'] = 'display_limitation'
    elif fault == 'retention': row['retained'] = 'discarded'
    elif fault == 'source_bytes': files[manifest['song_import']['sourceFile']] = b'{}'
    arrangement = manifest['arrangements'][0]
    chart = json.loads(files[arrangement['file']])
    note = chart['notes'][0]
    if fault == 'fret': note['f'] = 0
    elif fault == 'mute': note.pop('mt')
    elif fault == 'string': note['s'] -= 1
    elif fault == 'attack': note['t'] += .1
    elif fault == 'duration': note['sus'] += .1
    elif fault == 'extra_attack': chart['notes'].append(deepcopy(note))
    elif fault == 'notation':
        arrangement['notation'] = 'notation/invented.json'
        files[arrangement['notation']] = b'{}'
    report['findingCount'] = len(report['findings'])
    files[report_path] = json.dumps(report).encode()
    files[arrangement['file']] = json.dumps(chart).encode()
    files['manifest.yaml'] = yaml.safe_dump(manifest).encode()
    result = verify(tmp_path, path, files, alignment)
    assert result['status'] == ('passed' if fault == 'none' else 'failed'), result


@pytest.mark.parametrize('fault', ['none', 'authored_null', 'authored_float', 'used_negative',
    'source_hash', 'source_id', 'origin', 'occurrence', 'missing', 'count', 'extra_row'])
def test_negative_continuation_receipt_cannot_lose_or_invent_authorship(tmp_path, fault):
    raw = raw_score([measure(muted(0, (1, 2)), muted(-1, (1, 2), tie=True))])
    path, files, manifest, alignment = package(tmp_path, raw)
    sidecar = manifest['song_import']['mutedTieIdentityFile']
    evidence = json.loads(files[sidecar])
    row = evidence['continuations'][0]
    assert row['authored']['fret'] == -1 and row['used']['fret'] == 0
    assert evidence['sourceSha256'] == hashlib.sha256(path.read_bytes()).hexdigest()
    if fault == 'authored_null': row['authored']['fret'] = None
    elif fault == 'authored_float': row['authored']['fret'] = -1.0
    elif fault == 'used_negative': row['used']['fret'] = -1
    elif fault == 'source_hash': evidence['sourceSha256'] = '0'*64
    elif fault == 'source_id': row['sourceId'] = 'invented'
    elif fault == 'origin': row['originSourceId'] = 'invented'
    elif fault == 'occurrence': row['occurrence'] += 1
    elif fault == 'count': evidence['continuations'] = []
    elif fault == 'extra_row': evidence['continuations'].append(deepcopy(row))
    files[sidecar] = json.dumps(evidence).encode()
    if fault == 'missing': files.pop(sidecar)
    result = verify(tmp_path, path, files, alignment)
    assert result['status'] == ('passed' if fault == 'none' else 'failed'), result


@pytest.mark.parametrize('fret', [None, 0, 7])
def test_historical_contract_and_inventory91_remain_valid_without_new_alias(tmp_path, fret):
    path, files, manifest, alignment = package(tmp_path, raw_score([measure(muted(fret))]), contract=91)
    assert 'negativeFretMutePolicy' not in manifest['song_import']
    report_path = manifest['song_import']['compatibilityFile']
    report = json.loads(files[report_path])
    report['version'] = 91
    files[report_path] = json.dumps(report).encode()
    result = verify(tmp_path, path, files, alignment)
    assert result['status'] == 'passed', result
    manifest['song_import']['negativeFretMutePolicy'] = POLICY
    files['manifest.yaml'] = yaml.safe_dump(manifest).encode()
    assert verify(tmp_path, path, files, alignment)['status'] == 'failed'


@pytest.mark.parametrize('scope', ['source', 'recipe'])
@pytest.mark.parametrize('value', [None, False, '', 'invented'])
def test_builder_rejects_unsupported_extra_policy_even_when_inactive(tmp_path, scope, value):
    path = tmp_path / 'source.json'
    path.write_text(json.dumps(raw_score([measure(beat(7))])), encoding='utf-8')
    performance = load_performance(path)
    recipe = {'preservationContract': CONTRACT_VERSION}
    (performance['source'] if scope == 'source' else recipe)['negativeFretMutePolicy'] = value
    _, audio, _, job = inputs(tmp_path)
    with pytest.raises(ImportFailure):
        build_feedpak(performance, audio, {'status': 'validated', 'offset': 0, 'scale': 1}, job,
            output_dir=tmp_path / 'out', source_path=path,
            compatibility=performance['compatibilityReport'], recipe=recipe)


def test_hybrid_borrowed_mute_keeps_unpitched_chord_member(tmp_path):
    from test_songsterr_hybrid_lead import prepared, song
    raw = song()
    donor = raw['parts'][1]['measures'][1]['voices'][0]['beats'][0]
    donor['notes'].append({'string': 1, 'fret': -1, 'dead': True})
    path, performance, options = prepared(tmp_path, raw)
    _, audio, _, job = inputs(tmp_path)
    alignment = {'status': 'validated', 'offset': 0, 'scale': 1}
    built = build_feedpak(performance, audio, alignment, job, output_dir=tmp_path / 'out',
        source_path=path, compatibility=performance['compatibilityReport'],
        recipe={'preservationContract': CONTRACT_VERSION, 'source': 'songsterr',
                'scoreHash': options['sourceSha256'], 'audioHash': audio['hash'], 'hybridLead': options},
        hybrid_lead={'enabled': True, 'mainTrackId': options['mainTrackId'], 'options': options})
    with ZipFile(built['stagingPath']) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    manifest = yaml.safe_load(files['manifest.yaml'])
    result = verify(tmp_path, path, files, alignment, hybrid_options=options)
    assert result['status'] == 'passed', result
    derived_path = manifest['arrangements'][-1]['file']
    chart = json.loads(files[derived_path])
    mute = next(n for c in chart['chords'] for n in c['notes'] if n.get('mt'))
    assert mute['f'] == 127
    mute['f'] = 0
    files[derived_path] = json.dumps(chart).encode()
    result = verify(tmp_path, path, files, alignment, hybrid_options=options)
    assert result['status'] == 'failed', result
    assert 'hybrid_chart' in {r['code'] for r in result['errors']}

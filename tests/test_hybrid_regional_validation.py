"""Independent regional expectations: a valid copy can choose the wrong lead."""
from copy import deepcopy
import json

import pytest

from test_song_import_score import beat, measure, raw_score
from test_songsterr_hybrid_lead import rest
from feedback_converter.song_import.verify_source import read_source
from feedback_converter.song_import.verify_timeline import expected
from feedback_converter.song_import.verify_hybrid_priority import audit, _events, regional_requirements, _closed_members, _unsupported_regions
from feedback_converter.song_import.verification import Check


def source_case(tmp_path, *, incompatible=False, dedicated=False, both=False, unsupported=False):
    doc = raw_score([measure(beat(3)), measure(beat(5), marker={'text': 'Solo (Adrian Smith)'}),
                     measure(beat(7), marker={'text': 'Solo (Dave Murray)'}), measure(beat(3))])
    doc['tracks'][0]['name'] = 'Dave Murray | Stratocaster | Lead Guitar'
    doc['tracks'].append({**deepcopy(doc['tracks'][0]), 'id': 1,
                          'name': 'Adrian Smith | Solo' if dedicated else 'Adrian Smith | Destroyer | Lead Guitar'})
    if incompatible:
        doc['tracks'][1]['tuning'] = [x-2 for x in doc['tracks'][1]['tuning']]
    doc['parts'].append({'measures': [measure(rest()), measure(beat(12)), measure(rest()), measure(rest())]})
    if unsupported:
        doc['parts'][1]['measures'][2] = measure(beat(30))
    if both:
        for part in doc['parts']:
            part['measures'][1]['marker'] = {'text': 'Solo (Adrian & Dave)'}
    path = tmp_path / 'source.json'
    path.write_text(json.dumps(doc), encoding='utf-8')
    source = read_source(path)
    facts = expected(source, {'offset': 0, 'scale': 1})
    if unsupported:
        for part in facts['parts']:
            part['sync_notes'] = deepcopy(part['notes'])
            part['notes'] = [n for n in part['notes'] if n['note']['f'] <= 24]
    parts = {p['source'].id: p for p in facts['parts']}
    charts = {tid: {'tuning': list(p['source'].tuning), 'capo': p['source'].capo,
                    'notes': [deepcopy(n['note']) for n in p['notes']], 'chords': []}
              for tid, p in parts.items()}
    rows = {tid: _events(charts[tid], p, lambda s: s*2, 8, 'songsterr') for tid, p in parts.items()}
    return source, facts, parts, charts, rows


def ref(key, row):
    return {'kind': key[0], 'index': key[1], 'sourceIds': row['sourceIds'], 'occurrences': row['occurrences']}


def receipt_for(rows, *, use_adrian=True, limitations=None, limited=False):
    donor = list(rows['1']) if use_adrian else []
    removed = [('notes', 1)] if donor else []
    kept = [key for key in rows['0'] if key not in removed]
    passages = []
    if donor:
        passages.append({'trackId': '1', 'start': 4, 'end': 8, 'ownedStart': 4, 'ownedEnd': 8,
                         'priority': 'solo', 'events': [ref(k, rows['1'][k]) for k in donor],
                         'boundaries': ['primary', 'primary'], 'boundaryQuarters': [4, 8]})
    selected = {('0', *k) for k in kept} | {('1', *k) for k in donor}
    ledger = []
    for tid, values in rows.items():
        for key, row in values.items():
            included = (tid, *key) in selected
            is_removed = tid == '0' and key in removed
            limit = next((l for l in limitations or [] if l['trackId'] == tid and l['reason'] in {'incompatible_setup', 'excluded_by_user'}), None)
            ledger.append({'trackId': tid, **ref(key, row), 'start': row['start'], 'end': row['end'],
                           'recordingStart': row['start']/2, 'recordingEnd': row['end']/2,
                           'status': 'included' if included else 'superseded' if is_removed else 'excluded' if limit else 'unused_accompaniment',
                           'reason': 'copied_source_event' if included else 'regional_primary' if is_removed else limit['reason'] if limit else 'phrase_plan_preference',
                           'supersededBy': ['1'] if is_removed else []})
    return {'mainEvents': [ref(k, rows['0'][k]) for k in kept],
            'removedMain': [{**ref(k, rows['0'][k]), 'start': rows['0'][k]['start'], 'end': rows['0'][k]['end'],
                             'recordingStart': rows['0'][k]['start']/2, 'recordingEnd': rows['0'][k]['end']/2,
                             'supersededBy': ['1']} for k in removed], 'passages': passages,
            'coverage': {'status': 'limited' if limited else 'complete', 'events': ledger},
            'obligations': [], 'regionalEvidence': [], 'limitations': limitations or []}


def inspect(source, facts, charts, receipt, **options):
    check = Check()
    audit(charts, receipt, facts, {'policy': 'hybrid-lead-v3', 'mainTrackId': '0', **options},
          lambda s: s*2, lambda q: q/2, 8, 'songsterr', check, source=source)
    return check


def test_independent_named_owner_overrides_base_and_receipt_role(tmp_path):
    source, facts, _, charts, rows = source_case(tmp_path)
    good = receipt_for(rows)
    assert not inspect(source, facts, charts, good).errors
    wrong = receipt_for(rows, use_adrian=False)
    wrong['roles'] = {'0': 'main', '1': 'accompaniment'}
    result = inspect(source, facts, charts, wrong)
    assert 'hybrid_primary_coverage' in {e['code'] for e in result.errors}


def test_incompatible_solo_requires_disclosure_and_preserves_base_setup(tmp_path):
    source, facts, _, charts, rows = source_case(tmp_path, incompatible=True)
    limitations = [{'trackId': '1', 'start': 4, 'end': 8, 'reason': 'incompatible_setup'}]
    receipt = receipt_for(rows, use_adrian=False, limitations=limitations, limited=True)
    assert not inspect(source, facts, charts, receipt).errors
    del receipt['limitations'][:]
    assert 'hybrid_primary_coverage' in {e['code'] for e in inspect(source, facts, charts, receipt).errors}
    forged = receipt_for(rows)
    assert 'hybrid_primary_setup' in {e['code'] for e in inspect(source, facts, charts, forged).errors}


def test_fake_setup_limitation_cannot_hide_a_compatible_solo(tmp_path):
    source, facts, _, charts, rows = source_case(tmp_path)
    receipt = receipt_for(rows, use_adrian=False, limited=True,
                          limitations=[{'trackId': '1', 'start': 4, 'end': 8, 'reason': 'incompatible_setup'}])
    codes = {e['code'] for e in inspect(source, facts, charts, receipt).errors}
    assert {'hybrid_regional_limitation', 'hybrid_primary_coverage'} <= codes


def test_explicit_accompaniment_override_requires_recorded_limitation(tmp_path):
    source, facts, _, charts, rows = source_case(tmp_path)
    receipt = receipt_for(rows, use_adrian=False, limited=True,
                          limitations=[{'trackId': '1', 'start': 4, 'end': 8, 'reason': 'explicit_accompaniment'}])
    assert not inspect(source, facts, charts, receipt, roles={'1': 'accompaniment'}).errors
    assert 'hybrid_regional_limitation' in {e['code'] for e in inspect(source, facts, charts, receipt).errors}


def test_dedicated_solo_required_without_any_section_label(tmp_path):
    source, facts, _, charts, rows = source_case(tmp_path, dedicated=True)
    for bar in source.bars:
        bar.section = ''
    result = inspect(source, facts, charts, receipt_for(rows, use_adrian=False))
    assert 'hybrid_primary_coverage' in {e['code'] for e in result.errors}


def test_named_simultaneous_owners_allow_one_complete_voice(tmp_path):
    source, facts, _, charts, rows = source_case(tmp_path, both=True)
    assert not inspect(source, facts, charts, receipt_for(rows, use_adrian=False)).errors


@pytest.mark.parametrize('omit_unique_tail', [False, True])
def test_short_parallel_solo_cannot_erase_another_solo_tail(tmp_path, monkeypatch, omit_unique_tail):
    from zipfile import ZipFile
    from test_songsterr_hybrid_lead import build
    from feedback_converter.song_import import hybrid_regional
    doc = raw_score([measure(beat(3)) for _ in range(4)])
    doc['tracks'][0]['name'] = 'Rhythm Guitar'
    for tid, name in [(1, 'Guitar Solo A'), (2, 'Guitar Solo B')]:
        doc['tracks'].append({**deepcopy(doc['tracks'][0]), 'id': tid, 'name': name})
    doc['parts'].append({'measures': [measure(rest()), measure(beat(12)), measure(beat(14)), measure(rest())]})
    doc['parts'].append({'measures': [measure(rest()), measure(beat(17)), measure(rest()), measure(rest())]})
    if omit_unique_tail:
        real = hybrid_regional.backbone
        def wrong_tail(*args, **kwargs):
            result = real(*args, **kwargs)
            result['passages'] = [p for p in result['passages'] if p['trackId'] != '1']
            result['primaryEpisodes'] = [p for p in result['primaryEpisodes'] if p['trackId'] != '1']
            tail = next(r for r in result['rows']['0'] if r['start'] == 8)
            result['mainEvents'].append(hybrid_regional.ref(tail))
            result['removedMain'] = [r for r in result['removedMain'] if r['start'] != 8]
            kept = {(r['kind'], r['index']) for r in result['mainEvents']}
            result['mainProtected'] = [(r['start'], r['end']) for r in result['rows']['0'] if (r['kind'], r['index']) in kept]
            result['protected'] = result['mainProtected'] + [(p['start'], p['end']) for p in result['passages']]
            result['regionalEvidence'] = []
            result['limitations'] = []
            return result
        monkeypatch.setattr(hybrid_regional, 'backbone', wrong_tail)
    *_, archive, report = build(tmp_path, doc, overrides={'mainTrackId': '0', 'preferredTrackIds': ['2']})
    if omit_unique_tail:
        assert report['status'] == 'failed'
        codes = {e['code'] for e in report['errors']}
        assert 'hybrid_primary_coverage' in codes
        assert 'hybrid_chart' not in codes, 'The wrong rhythm remains a structurally valid copied chart.'
    else:
        assert report['status'] == 'passed', report
        with ZipFile(archive) as z:
            receipt = json.loads(z.read('import/hybrid-lead.json'))
        assert [(p['trackId'], p['start'], p['end']) for p in receipt['passages']] == [('2', 4, 8), ('1', 8, 12)]
        assert receipt['coverage']['status'] == 'complete'


@pytest.mark.parametrize('label', ['Pre-Solo (Adrian Smith)', 'Post Solo (Adrian Smith)', 'Tenor Saxophone Solo (Adrian Smith)'])
def test_unrelated_solo_labels_do_not_create_guitar_owner_obligation(tmp_path, label):
    source, facts, _, charts, rows = source_case(tmp_path)
    source.bars[1].section = label
    assert not inspect(source, facts, charts, receipt_for(rows, use_adrian=False)).errors


def test_role_map_cannot_erase_independent_obligations(tmp_path):
    source, facts, parts, _, rows = source_case(tmp_path)
    baseline = regional_requirements(parts, rows, source, facts['order'], 16)
    assert any('1' in r['owners'] and r['evidence'] == 'named_soloist' for r in baseline)
    assert baseline == regional_requirements(dict(reversed(list(parts.items()))), rows, source, facts['order'], 16)


def test_instrument_model_word_harmony_does_not_erase_named_lead(tmp_path):
    source, facts, parts, _, rows = source_case(tmp_path)
    parts['1']['source'].name = 'Adrian Smith | Harmony Sovereign | Lead Guitar'
    requirements = regional_requirements(parts, rows, source, facts['order'], 16)
    assert any(r['evidence'] == 'named_soloist' and '1' in r['owners'] for r in requirements)


def test_mixed_lead_with_numbered_solo_annotation_is_not_whole_track_obligation(tmp_path):
    source, facts, parts, _, rows = source_case(tmp_path)
    parts['1']['source'].name = 'Adrian Smith | Gibson SG | Lead Guitar (Left Stereo) [Solo #1]'
    for bar in source.bars:
        bar.section = ''
    assert not regional_requirements(parts, rows, source, facts['order'], 16)


@pytest.mark.parametrize('linked', [False, True])
def test_named_solo_ghost_tail_requires_a_real_gesture_connection(tmp_path, linked):
    source, facts, parts, _, rows = source_case(tmp_path)
    rows['1'][('notes', 0)]['end'] = 8 if linked else 6
    rows['1'][('notes', 1)] = {'onset': 6, 'start': 4 if linked else 6, 'end': 8,
                              'notes': [{'t': 3, 'sus': 1, 's': 0, 'f': 14, 'ghost': True}]}
    required = next(r for r in regional_requirements(parts, rows, source, facts['order'], 16) if '1' in r['owners'])
    assert (('notes', 1) in required['owners']['1']) is linked


def test_hard_closure_keeps_pickup_tail_but_not_unrelated_attack_under_sustain():
    rows = {('notes', 0): {'onset': 3.75, 'start': 3.75, 'end': 9},
            ('notes', 1): {'onset': 4, 'start': 3.75, 'end': 9},
            ('notes', 2): {'onset': 8.5, 'start': 8.5, 'end': 10},
            ('notes', 3): {'onset': 9.5, 'start': 9.5, 'end': 11}}
    assert _closed_members(rows, 4, 8) == {('notes', 0), ('notes', 1)}


def test_planner_budget_cannot_excuse_missing_known_solo(tmp_path):
    source, facts, _, charts, rows = source_case(tmp_path)
    receipt = receipt_for(rows, use_adrian=False, limited=True,
                          limitations=[{'trackId': '1', 'start': 4, 'end': 8, 'reason': 'planning_limit'}])
    assert 'hybrid_primary_coverage' in {e['code'] for e in inspect(source, facts, charts, receipt).errors}


def test_unsupported_ending_is_local_and_does_not_erase_supported_solo(tmp_path):
    source, facts, _, charts, rows = source_case(tmp_path, dedicated=True, unsupported=True)
    limit = {'trackId': '1', 'start': 8, 'end': 12, 'reason': 'unsupported_source_gesture'}
    good = receipt_for(rows, limitations=[limit], limited=True)
    assert not inspect(source, facts, charts, good).errors
    wrong = receipt_for(rows, use_adrian=False, limitations=[limit], limited=True)
    assert 'hybrid_primary_coverage' in {e['code'] for e in inspect(source, facts, charts, wrong).errors}
    broad = receipt_for(rows, use_adrian=False, limitations=[{**limit, 'start': 4}], limited=True)
    assert 'hybrid_regional_limitation' in {e['code'] for e in inspect(source, facts, charts, broad).errors}


def test_unsupported_primary_cannot_claim_complete_even_after_projection(tmp_path):
    source, facts, _, charts, rows = source_case(tmp_path, dedicated=True, unsupported=True)
    wrong = receipt_for(rows)
    codes = {e['code'] for e in inspect(source, facts, charts, wrong).errors}
    assert {'hybrid_regional_limitation', 'hybrid_coverage'} <= codes


def test_unsupported_closure_recovers_written_link_removed_by_projection(tmp_path):
    doc = raw_score([measure(beat(5, duration=(1, 4), hp=True), beat(30, duration=(1, 4)), beat(7, duration=(1, 2)))])
    path = tmp_path/'linked-source.json'
    path.write_text(json.dumps(doc), encoding='utf-8')
    source = read_source(path)
    part = expected(source, {'offset': 0, 'scale': 1})['parts'][0]
    part['sync_notes'] = deepcopy(part['notes'])
    part['sync_notes'][0]['note'].pop('ln')
    part['notes'] = [n for n in part['notes'] if n['note']['f'] <= 24]
    assert _unsupported_regions(part, lambda s: s*2, 2) == [[0, 2]]


def test_unsupported_long_note_cannot_excuse_unrelated_supported_voice(tmp_path):
    source_case(tmp_path, dedicated=True)
    path = tmp_path/'source.json'
    doc = json.loads(path.read_text(encoding='utf-8'))
    bar = measure(beat(30))
    bar['voices'].append({'beats': [rest((1, 4)), beat(12, string=1, duration=(1, 2)), rest((1, 4))]})
    doc['parts'][1]['measures'][1] = bar
    path.write_text(json.dumps(doc), encoding='utf-8')
    source = read_source(path)
    facts = expected(source, {'offset': 0, 'scale': 1})
    charts, rows = {}, {}
    for part in facts['parts']:
        tid = part['source'].id
        part['sync_notes'] = deepcopy(part['notes'])
        part['notes'] = [n for n in part['notes'] if n['note']['f'] <= 24]
        charts[tid] = {'tuning': list(part['source'].tuning), 'capo': part['source'].capo,
                       'notes': [n['note'] for n in part['notes']], 'chords': []}
        rows[tid] = _events(charts[tid], part, lambda s: s*2, 8, 'songsterr')
    receipt = receipt_for(rows, use_adrian=False, limited=True,
                          limitations=[{'trackId': '1', 'start': 4, 'end': 8, 'reason': 'unsupported_source_gesture'}])
    result = inspect(source, facts, charts, receipt)
    assert 'hybrid_primary_coverage' in {e['code'] for e in result.errors}


def test_unsupported_hard_closure_preserves_atomic_authored_chord_connections(tmp_path):
    first = {'duration': [1, 4], 'notes': [{'string': 0, 'fret': 5}, {'string': 1, 'fret': 7, 'hp': True}]}
    second = {'duration': [1, 4], 'notes': [{'string': 0, 'fret': 6, 'hp': True}, {'string': 1, 'fret': 9}]}
    doc = raw_score([measure(first, second, beat(30, duration=(1, 4)), rest((1, 4)))])
    path = tmp_path/'chord-linked-source.json'
    path.write_text(json.dumps(doc), encoding='utf-8')
    source = read_source(path)
    part = expected(source, {'offset': 0, 'scale': 1})['parts'][0]
    assert _unsupported_regions(part, lambda s: s*2, 2) == [[0, 3]]


@pytest.mark.parametrize('field,value', [('sourceIds', ['invented']), ('occurrences', [99])])
def test_receipt_cannot_invent_main_lineage(tmp_path, field, value):
    source, facts, _, charts, rows = source_case(tmp_path)
    receipt = receipt_for(rows)
    receipt['mainEvents'][0][field] = value
    assert 'hybrid_event_lineage' in {e['code'] for e in inspect(source, facts, charts, receipt).errors}


def test_complete_archive_preserves_named_handover(tmp_path):
    from zipfile import ZipFile
    from test_songsterr_hybrid_lead import build
    source_case(tmp_path)
    doc = json.loads((tmp_path/'source.json').read_text(encoding='utf-8'))
    destination = tmp_path/'build'
    destination.mkdir()
    *_, archive, report = build(destination, doc)
    assert report['status'] == 'passed', report
    with ZipFile(archive) as z:
        receipt = json.loads(z.read('import/hybrid-lead.json'))
    assert receipt['policy'] == 'hybrid-lead-v3'
    assert any(p['trackId'] == '1' and p['priority'] == 'solo' for p in receipt['passages'])
    assert any(e['index'] == 2 for e in receipt['mainEvents']), 'Dave resumes ownership in his own solo.'


def test_complete_archive_discloses_incompatible_solo_and_keeps_base(tmp_path):
    from zipfile import ZipFile
    from test_songsterr_hybrid_lead import build
    source_case(tmp_path, incompatible=True)
    doc = json.loads((tmp_path/'source.json').read_text(encoding='utf-8'))
    destination = tmp_path/'build'
    destination.mkdir()
    *_, archive, report = build(destination, doc)
    assert report['status'] == 'passed', report
    with ZipFile(archive) as z:
        receipt = json.loads(z.read('import/hybrid-lead.json'))
    assert receipt['mainTrackId'] == '0'
    assert not receipt['passages']
    assert receipt['coverage']['status'] == 'limited'
    assert any(l['reason'] == 'incompatible_setup' and l['trackId'] == '1' for l in receipt['limitations'])


def test_self_consistent_wrong_guitar_archive_fails_even_after_diagnostics_erased(tmp_path, monkeypatch):
    from test_songsterr_hybrid_lead import build
    from feedback_converter.song_import import hybrid_regional
    source_case(tmp_path)
    doc = json.loads((tmp_path/'source.json').read_text(encoding='utf-8'))
    real = hybrid_regional.backbone
    def wrongly_keep_base(*args, **kwargs):
        result = real(*args, **kwargs)
        result['mainEvents'] = [hybrid_regional.ref(r) for r in result['rows']['0'] if r['available']]
        result['removedMain'] = []
        result['passages'] = []
        result['primaryEpisodes'] = []
        result['regionalEvidence'] = []
        result['limitations'] = []
        result['roles']['1'] = 'accompaniment'
        result['protected'] = result['mainProtected'] = [(r['start'], r['end']) for r in result['rows']['0']]
        return result
    monkeypatch.setattr(hybrid_regional, 'backbone', wrongly_keep_base)
    destination = tmp_path/'build'
    destination.mkdir()
    *_, report = build(destination, doc)
    codes = {e['code'] for e in report['errors']}
    assert report['status'] == 'failed'
    assert 'hybrid_primary_coverage' in codes
    assert 'hybrid_chart' not in codes, 'The forged chart remains a correct copy of the wrong guitar.'


def test_false_primary_attack_window_fails_archive_membership_check(tmp_path, monkeypatch):
    from test_songsterr_hybrid_lead import build
    from feedback_converter.song_import import hybrid_regional
    source_case(tmp_path)
    doc = json.loads((tmp_path/'source.json').read_text(encoding='utf-8'))
    real = hybrid_regional.backbone
    def wrong_window(*args, **kwargs):
        result = real(*args, **kwargs)
        result['passages'][0]['ownedStart'] = 6
        return result
    monkeypatch.setattr(hybrid_regional, 'backbone', wrong_window)
    destination = tmp_path/'build'
    destination.mkdir()
    *_, report = build(destination, doc)
    assert report['status'] == 'failed'
    assert 'hybrid_passage_membership' in {e['code'] for e in report['errors']}


@pytest.mark.parametrize('fault,code', [('recording_window', 'hybrid_time'), ('obligation_bounds', 'hybrid_regional_obligation'),
                                      ('empty_obligation', 'hybrid_regional_obligation')])
def test_regional_metadata_must_match_source_ownership(tmp_path, monkeypatch, fault, code):
    from test_songsterr_hybrid_lead import build
    from feedback_converter.song_import import hybrid_regional
    source_case(tmp_path)
    doc = json.loads((tmp_path/'source.json').read_text(encoding='utf-8'))
    real = hybrid_regional.finish
    def corrupt(*args, **kwargs):
        result = real(*args, **kwargs)
        if fault == 'recording_window':
            result['passages'][0]['recordingOwnedEnd'] += .125
        elif fault == 'obligation_bounds':
            result['obligations'][0]['start'] += .5
        else:
            result['obligations'][0]['events'] = []
        return result
    monkeypatch.setattr(hybrid_regional, 'finish', corrupt)
    destination = tmp_path/'build'
    destination.mkdir()
    *_, report = build(destination, doc)
    assert report['status'] == 'failed'
    assert code in {e['code'] for e in report['errors']}


def test_repeated_named_handover_keeps_performed_notation_occurrences(tmp_path):
    from zipfile import ZipFile
    import yaml
    from test_songsterr_hybrid_lead import build
    source_case(tmp_path)
    doc = json.loads((tmp_path/'source.json').read_text(encoding='utf-8'))
    for part in doc['parts']:
        for bar in part['measures']:
            bar['signature'] = [2, 4]
            for voice in bar['voices']:
                for item in voice['beats']:
                    item['duration'] = [1, 2]
        part['measures'][0]['repeatStart'] = True
        part['measures'][1]['repeat'] = 2
    destination = tmp_path/'build'
    destination.mkdir()
    *_, archive, report = build(destination, doc)
    assert report['status'] == 'passed', report
    with ZipFile(archive) as z:
        manifest = yaml.safe_load(z.read('manifest.yaml'))
        receipt = json.loads(z.read('import/hybrid-lead.json'))
        notation = json.loads(z.read(manifest['arrangements'][-1]['notation']))
    assert {o for r in receipt['removedMain'] for o in r['occurrences']} == {2, 4}
    assert {o for r in receipt['mainEvents'] for o in r['occurrences']} == {1, 3, 5, 6}
    assert len(notation['measures']) == 6


@pytest.mark.parametrize('inject_repeated_beat', [False, True])
def test_donor_notation_checks_performed_identity_inside_a_long_tail(inject_repeated_beat):
    from io import BytesIO
    from zipfile import ZipFile
    from feedback_converter.song_import.hybrid_materialize import notation_for
    from feedback_converter.song_import.verify_hybrid import _notation
    # A chart sustain can extend the selected footprint across a later
    # performance of the same written beat. Source IDs alone cannot select it.
    def notation(donor):
        measures = []
        for index in range(3):
            item = {'t': index*2, 'duration_seconds': 2, 'rest': not donor,
                    'source_id': 'repeated-beat' if donor else 'main-rest',
                    'notes': [{'source_id': 'repeated-note', 'fret': 12}] if donor else []}
            measures.append({'idx': index+1, 'staves': {'0': {'voices': [
                {'v': 0, 'source_id': 'donor-voice' if donor else 'main-voice', 'beats': [item]}]}}})
        return {'staves': [{'label': 'Lead Guitar'}], 'measures': measures}
    main, donor = notation(False), notation(True)
    receipt = {'policy': 'hybrid-lead-v3', 'mainTrackId': '0', 'mainEvents': [], 'removedMain': [],
               'passages': [{'trackId': '1', 'priority': 'solo', 'start': 0, 'end': 12,
                             'ownedStart': 0, 'ownedEnd': 4, 'recordingStart': 0, 'recordingEnd': 6,
                             'recordingOwnedStart': 0, 'recordingOwnedEnd': 2,
                             'events': [{'sourceIds': ['repeated-note'], 'occurrences': [1]}]}]}
    actual, problem = notation_for(receipt, {'0': {'notation': main}, '1': {'notation': donor}})
    assert problem is None
    donor_beats = [b for m in actual['measures'] for s in m['staves'].values()
                   for v in s['voices'] if v.get('source_id') == 'donor-voice' for b in v['beats']]
    assert [b['t'] for b in donor_beats] == [0]
    if inject_repeated_beat:
        forged_voice = deepcopy(donor['measures'][1]['staves']['0']['voices'][0])
        forged_voice['v'] = 1
        actual['measures'][1]['staves']['0']['voices'].append(forged_voice)
    data = BytesIO()
    with ZipFile(data, 'w') as z:
        for name, document in [('main.json', main), ('donor.json', donor), ('hybrid.json', actual)]:
            z.writestr(name, json.dumps(document))
    with ZipFile(data) as z:
        check = Check()
        _notation(z, {'notation': 'hybrid.json'}, receipt,
                  {'0': {'notationFile': 'main.json'}, '1': {'notationFile': 'donor.json'}}, check)
    assert ('hybrid_notation' in {e['code'] for e in check.errors}) is inject_repeated_beat
    if not inject_repeated_beat:
        assert not check.errors


def test_removed_main_sustain_does_not_hide_retained_other_voice_notation(tmp_path):
    from zipfile import ZipFile
    import yaml
    from test_songsterr_hybrid_lead import build
    doc = raw_score([measure(beat(3)),
                     measure(beat(3, tie=True), marker={'text': 'Verse'}),
                     measure(beat(5)), measure(beat(7))])
    doc['tracks'][0]['name'] = 'Dave Murray | Lead Guitar'
    doc['parts'][0]['measures'][1]['voices'].append({'beats': [rest((1, 2)), beat(9, string=1, duration=(1, 4)), rest((1, 4))]})
    doc['tracks'].append({**deepcopy(doc['tracks'][0]), 'id': 1, 'name': 'Adrian Smith | Solo'})
    doc['parts'].append({'measures': [measure(beat(12)), measure(rest()), measure(rest()), measure(rest())]})
    *_, archive, report = build(tmp_path, doc, overrides={'mainTrackId': '0'})
    assert report['status'] == 'passed', report
    with ZipFile(archive) as z:
        manifest = yaml.safe_load(z.read('manifest.yaml'))
        receipt = json.loads(z.read('import/hybrid-lead.json'))
        notation = json.loads(z.read(manifest['arrangements'][-1]['notation']))
    assert any(r['start'] == 0 and r['end'] == 8 for r in receipt['removedMain'])
    assert any(n['fret'] == 9 for m in notation['measures'] for s in m['staves'].values()
               for v in s['voices'] for b in v['beats'] for n in b.get('notes', []))


def adjacent_conflict_song():
    doc = raw_score([measure(beat(12), marker={'text': 'Solo (Adrian Smith)'}),
                     measure(beat(12, duration=(1, 2), tie=True), rest((1, 2)), marker={'text': 'Solo (Dave Murray)'}),
                     measure(beat(3), marker={'text': 'Verse'}), measure(beat(3))])
    doc['tracks'][0]['name'] = 'Adrian Smith | Lead Guitar'
    doc['tracks'].append({**deepcopy(doc['tracks'][0]), 'id': 1, 'name': 'Dave Murray | Lead Guitar'})
    doc['parts'].append({'measures': [measure(rest()),
                         measure(beat(17, duration=(1, 4)), rest((1, 4)), beat(19, duration=(1, 2))),
                         measure(rest()), measure(rest())]})
    return doc


def test_adjacent_named_whole_gesture_conflict_has_valid_limited_result(tmp_path):
    from zipfile import ZipFile
    from test_songsterr_hybrid_lead import build
    *_, archive, report = build(tmp_path, adjacent_conflict_song(), overrides={'mainTrackId': '0'})
    assert report['status'] == 'passed', report
    with ZipFile(archive) as z:
        receipt = json.loads(z.read('import/hybrid-lead.json'))
    assert receipt['coverage']['status'] == 'limited'
    assert any(x['reason'] == 'conflicting_primary' for x in receipt['limitations'])
    assert any(p['trackId'] == '1' and p['start'] == 6 for p in receipt['passages']), 'The nonconflicting second phrase must survive.'


def test_real_hard_conflict_cannot_excuse_missing_nonconflicting_phrase(tmp_path, monkeypatch):
    from test_songsterr_hybrid_lead import build
    from feedback_converter.song_import import hybrid_regional
    real = hybrid_regional.finish
    def remove_safe_phrase(*args, **kwargs):
        result = real(*args, **kwargs)
        result['passages'] = [p for p in result['passages'] if p['trackId'] != '1']
        for row in result['coverage']['events']:
            if row['trackId'] == '1' and row['status'] == 'included':
                row.update(status='unused_accompaniment', reason='phrase_plan_preference')
        return result
    monkeypatch.setattr(hybrid_regional, 'finish', remove_safe_phrase)
    *_, report = build(tmp_path, adjacent_conflict_song(), overrides={'mainTrackId': '0'})
    assert report['status'] == 'failed'
    assert 'hybrid_primary_coverage' in {e['code'] for e in report['errors']}
    assert 'hybrid_chart' not in {e['code'] for e in report['errors']}

"""Independent musical expectations, including the manually identified miss."""
from copy import deepcopy
from fractions import Fraction
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from feedback_converter.song_import.verify_hybrid_priority import (
    _credible_named_sources, regional_requirements,
)
from test_hybrid_regional_validation import inspect, receipt_for, source_case


def voice(notes):
    return {('notes', i): {'start': i, 'end': i + 1, 'onset': i, 'notes': group}
            for i, group in enumerate(notes)}


def parts_for(names):
    return {tid: {'source': SimpleNamespace(name=name)} for tid, name in names.items()}


def test_actual_james_source_seven_melody_is_required_over_clean_backing():
    fixture = json.loads((Path(__file__).parent/'fixtures/hybrid_master_named_solo.json').read_text())
    lo, hi = fixture['originalRecordingStart'], fixture['originalRecordingEnd']
    rows, parts = {}, {}
    for track in fixture['tracks']:
        tid = track['id']
        parts[tid] = {'source': SimpleNamespace(name=track['name'])}
        rows[tid] = {(event['kind'], event['index']): {
            'start': event['onset'] - lo, 'onset': event['onset'] - lo,
            'end': min(hi, max(n['t'] + n.get('sus', 0) for n in event['notes'])) - lo,
            'notes': event['notes'],
        } for event in track['events']}
    source = SimpleNamespace(bars=[SimpleNamespace(section='[A] Solo (James)', length=Fraction(str(hi-lo)), tempos={})])
    required = regional_requirements(parts, rows, source, [0], hi-lo)
    assert len(required) == 1
    assert set(required[0]['owners']) == {fixture['expectedOwner']} == {'7'}
    assert len(required[0]['owners']['7']) == fixture['expectedEventCount'] == 66
    wrong_original_selection = {('4', *key) for key in rows['4']}
    assert not any({(tid, *key) for key in members} <= wrong_original_selection
                   for tid, members in required[0]['owners'].items())


@pytest.mark.parametrize('solo_role', ['Rhythm Guitar', 'Extra Lead Guitar', 'Harmony Guitar'])
def test_self_consistent_clean_backing_receipt_does_not_hide_rhythm_labelled_solo(tmp_path, solo_role):
    from feedback_converter.song_import.verify_source import read_source
    from feedback_converter.song_import.verify_timeline import expected
    from feedback_converter.song_import.verify_hybrid_priority import _events
    from test_song_import_score import beat, measure
    from test_songsterr_hybrid_lead import rest
    source_case(tmp_path)
    path = tmp_path/'source.json'
    doc = json.loads(path.read_text(encoding='utf-8'))
    doc['tracks'][1]['name'] = 'Adrian Smith | Clean Guitar'
    doc['parts'][1]['measures'][1] = measure(*[{**beat(i%2, string=i%2, duration=(1, 8)), 'letRing': True} for i in range(8)])
    doc['tracks'].append({**deepcopy(doc['tracks'][1]), 'id': 2, 'name': 'Adrian Smith | ' + solo_role})
    doc['parts'].append({'measures': [measure(rest()),
        measure(*[beat(i%5, duration=(1, 8), vibrato=True) for i in range(8)]), measure(rest()), measure(rest())]})
    path.write_text(json.dumps(doc), encoding='utf-8')
    source = read_source(path)
    facts = expected(source, {'offset': 0, 'scale': 1})
    parts = {p['source'].id: p for p in facts['parts']}
    charts = {tid: {'tuning': list(p['source'].tuning), 'capo': p['source'].capo,
                   'notes': [deepcopy(n['note']) for n in p['notes']], 'chords': []} for tid, p in parts.items()}
    rows = {tid: _events(charts[tid], p, lambda s: s*2, 8, 'songsterr') for tid, p in parts.items()}
    wrong = receipt_for(rows)
    codes = {e['code'] for e in inspect(source, facts, charts, wrong).errors}
    assert 'hybrid_primary_coverage' in codes
    assert codes <= {'hybrid_primary_coverage', 'hybrid_coverage'}


@pytest.mark.parametrize('role', ['Rhythm Guitar', 'Clean Guitar', 'Acoustic Guitar', 'Guitar'])
def test_named_solo_label_is_not_a_rhythm_or_tone_veto(tmp_path, role):
    source, facts, parts, charts, rows = source_case(tmp_path)
    parts['1']['source'].name = 'Adrian Smith | ' + role
    assert not inspect(source, facts, charts, receipt_for(rows)).errors
    assert 'hybrid_primary_coverage' in {e['code'] for e in inspect(source, facts, charts, receipt_for(rows, use_adrian=False)).errors}


@pytest.mark.parametrize('low_register,chords', [(True, False), (False, True), (True, True)])
def test_expressive_lead_contrast_does_not_require_high_register_or_monophony(low_register, chords):
    offset = 0 if low_register else 12
    notes = [[{'s': 0, 'f': offset + i % 4, 'vb': True}] + ([{'s': 1, 'f': offset + (i + 2) % 4}] if chords else []) for i in range(12)]
    backing = [[{'s': i % 3, 'f': i % 3, 'lr': True}] for i in range(12)]
    rows = {'lead': voice(notes), 'backing': voice(backing)}
    parts = parts_for({'lead': 'James | Rhythm Guitar', 'backing': 'James | Lead Guitar'})
    assert _credible_named_sources(list(parts), parts, rows, 0, 12) == ['lead']


def test_plain_clean_melody_beats_recurrent_chords_without_technique_or_register_rule():
    rows = {'melody': voice([[{'s': 0, 'f': i % 5}] for i in range(12)]),
            'backing': voice([[{'s': 0, 'f': 12}, {'s': 1, 'f': 14}] for _ in range(12)])}
    parts = parts_for({'melody': 'James | Clean Guitar', 'backing': 'James | Lead Guitar'})
    assert _credible_named_sources(list(parts), parts, rows, 0, 12) == ['melody']


def test_unresolved_plain_parallel_lines_remain_alternatives_without_role_priority():
    rows = {tid: voice([[{'s': 0, 'f': offset+i%5}] for i in range(12)]) for tid, offset in [('a', 0), ('b', 12)]}
    parts = parts_for({'a': 'James | Rhythm Guitar', 'b': 'James | Lead Guitar'})
    assert set(_credible_named_sources(list(parts), parts, rows, 0, 12)) == {'a', 'b'}


@pytest.mark.parametrize('role', ['Delay', 'Harmony Guitar', 'Extra Guitars'])
def test_effect_or_harmony_layer_cannot_displace_regular_named_guitar(role):
    rows = {'regular': voice([[{'s': 0, 'f': 0}]]), 'layer': voice([[{'s': 1, 'f': 20, 'vb': True}]])}
    parts = parts_for({'regular': 'James | Rhythm Guitar', 'layer': 'James | ' + role})
    assert _credible_named_sources(list(parts), parts, rows, 0, 1) == ['regular']


def test_only_harmony_rendition_is_allowed_but_delay_is_not_a_solo_obligation():
    rows = {'a': voice([[{'s': 0, 'f': 5}]])}
    for role, expected in [('Harmony Guitar', ['a']), ('Delay', [])]:
        parts = parts_for({'a': 'James | ' + role})
        assert _credible_named_sources(['a'], parts, rows, 0, 1) == expected


@pytest.mark.parametrize('role', ['Extra Lead Guitar', 'Harmony Guitar', 'Background Guitar'])
def test_secondary_label_cannot_hide_clear_foreground_over_all_backing(role):
    rows = {'ordinary': voice([[{'s': i%2, 'f': i%2, 'lr': True}] for i in range(12)]),
            'secondary': voice([[{'s': 0, 'f': i%5, 'vb': True}] for i in range(12)])}
    parts = parts_for({'ordinary': 'James | Clean Guitar', 'secondary': 'James | ' + role})
    assert _credible_named_sources(list(parts), parts, rows, 0, 12) == ['secondary']


def test_secondary_plain_line_can_replace_recurrent_chords_but_fx_cannot():
    rows = {'ordinary': voice([[{'s': 0, 'f': 0}, {'s': 1, 'f': 2}] for _ in range(12)]),
            'secondary': voice([[{'s': 0, 'f': i%5}] for i in range(12)])}
    for role, expected in [('Extra Lead Guitar', ['secondary']), ('Delay', ['ordinary'])]:
        parts = parts_for({'ordinary': 'James | Rhythm Guitar', 'secondary': 'James | ' + role})
        assert _credible_named_sources(list(parts), parts, rows, 0, 12) == expected


def test_one_credible_ordinary_melody_keeps_precedence_over_secondary_expression():
    rows = {'ordinary': voice([[{'s': 0, 'f': i%5}] for i in range(12)]),
            'backing': voice([[{'s': 0, 'f': 0}, {'s': 1, 'f': 2}] for _ in range(12)]),
            'secondary': voice([[{'s': 0, 'f': i%5, 'vb': True}] for i in range(12)])}
    parts = parts_for({'ordinary': 'James | Clean Guitar', 'backing': 'James | Rhythm Guitar', 'secondary': 'James | Extra Lead Guitar'})
    assert _credible_named_sources(list(parts), parts, rows, 0, 12) == ['ordinary']


def availability_song(*, cross_performer=False):
    from test_song_import_score import beat, measure, raw_score
    doc = raw_score([measure(beat(3), **({'marker': {'text': 'Solo (Alex)'}} if bar == 0 else {})) for bar in range(4)])
    doc['tracks'][0]['name'] = 'Blake | Lead Guitar'
    backing = [measure(*[{**beat([0, 2, 2, 0][i], string=i, duration=(1, 4)), 'letRing': True} for i in range(4)]) for _ in range(4)]
    melody = [measure(*[beat([2, 5, 4, 7][i], duration=(1, 4), vibrato=True) for i in range(4)]) for _ in range(4)]
    unavailable = []
    for bar in range(4):
        beats = []
        for index in range(2):
            fret = bar*2 + index
            item = beat(fret, duration=(1, 2))
            item['notes'].append({'string': 1, 'fret': fret+2})
            beats.append(item)
        unavailable.append(measure(*beats))
    for ident, name, bars in [(1, 'Alex | Clean Guitar', backing),
                              (2, 'Blake | Rhythm Guitar' if cross_performer else 'Alex | Extra Lead Guitar', melody),
                              (3, 'Alex | Lead Guitar' if cross_performer else 'Alex | Clean Guitar', deepcopy(melody) if cross_performer else unavailable)]:
        doc['tracks'].append({**deepcopy(doc['tracks'][0]), 'id': ident, 'name': name})
        doc['parts'].append({'measures': bars})
    return doc


@pytest.mark.parametrize('constraint', ['tuning', 'capo', 'excluded', 'role'])
def test_unavailable_nonwinning_ordinary_voice_cannot_veto_extra_melody_in_real_build(tmp_path, constraint):
    from zipfile import ZipFile
    from test_songsterr_hybrid_lead import build
    doc, options = availability_song(), {'mainTrackId': '0'}
    if constraint == 'tuning':
        doc['tracks'][3]['tuning'] = [n-2 for n in doc['tracks'][3]['tuning']]
    elif constraint == 'capo':
        doc['tracks'][3]['capo'] = 2
    elif constraint == 'excluded':
        options['excludedTrackIds'] = ['3']
    else:
        options['roles'] = {'3': 'accompaniment'}
    *_, archive, report = build(tmp_path, doc, overrides=options)
    assert report['status'] == 'passed', report
    with ZipFile(archive) as z:
        receipt = json.loads(z.read('import/hybrid-lead.json'))
    solo = [p for p in receipt['passages'] if p.get('evidence') == 'named_soloist']
    assert solo and {p['trackId'] for p in solo} == {'2'}
    assert sum(len(p['events']) for p in solo) == 16


def test_unavailable_named_solo_preserves_disclosure_with_other_players_rhythm_labelled_fallback(tmp_path):
    from zipfile import ZipFile
    from test_songsterr_hybrid_lead import build
    doc = availability_song(cross_performer=True)
    doc['tracks'][3]['tuning'] = [n-2 for n in doc['tracks'][3]['tuning']]
    *_, archive, report = build(tmp_path, doc, overrides={'mainTrackId': '0'})
    assert report['status'] == 'passed', report
    with ZipFile(archive) as z:
        receipt = json.loads(z.read('import/hybrid-lead.json'))
    assert any(p['trackId'] == '2' for p in receipt['passages'])
    assert any(limit['trackId'] == '3' and limit['reason'] == 'incompatible_setup' for limit in receipt['limitations'])


def test_report_states_independent_scope_and_ambiguous_alternatives(tmp_path):
    from feedback_converter.song_import.verification import Check
    from feedback_converter.song_import.verify_hybrid_priority import audit
    source, facts, _, charts, rows = source_case(tmp_path, both=True)
    report, check = {}, Check()
    audit(charts, receipt_for(rows, use_adrian=False), facts,
          {'policy': 'hybrid-lead-v3', 'mainTrackId': '0'}, lambda s: s*2,
          lambda q: q/2, 8, 'songsterr', check, source=source, musical_audit=report)
    assert not check.errors
    assert report['scope'] == 'named_soloists_and_dedicated_solo_bodies'
    assert report['requirementCountScope'] == 'retained_playable_source_events'
    assert report['unsupportedNamedRequirementCount'] == 0
    assert report['alternativeVoiceCount'] == 1
    assert report['uniqueOwnerCount'] == 1
    assert report['requirementCount'] == report['namedSectionCount'] == 2
    assert 'Unlabelled musical choices' in report['unverifiedClaim']


@pytest.mark.parametrize('disclosed', [False, True])
def test_entirely_unsupported_named_rhythm_solo_cannot_silently_claim_complete(tmp_path, disclosed):
    from feedback_converter.song_import.verify_source import read_source
    from feedback_converter.song_import.verify_timeline import expected
    from feedback_converter.song_import.verify_hybrid_priority import _events
    from test_song_import_score import beat, measure
    source_case(tmp_path)
    path = tmp_path/'source.json'
    doc = json.loads(path.read_text(encoding='utf-8'))
    doc['tracks'][1]['name'] = 'Adrian Smith | Rhythm Guitar'
    doc['parts'][1]['measures'][1] = measure(beat(30))
    doc['parts'][0]['measures'][2]['marker'] = {'text': 'Verse'}
    path.write_text(json.dumps(doc), encoding='utf-8')
    source = read_source(path)
    facts = expected(source, {'offset': 0, 'scale': 1})
    for part in facts['parts']:
        part['sync_notes'] = deepcopy(part['notes'])
        part['notes'] = [n for n in part['notes'] if n['note']['f'] <= 24]
    parts = {p['source'].id: p for p in facts['parts']}
    charts = {tid: {'tuning': list(p['source'].tuning), 'capo': p['source'].capo,
                   'notes': [deepcopy(n['note']) for n in p['notes']], 'chords': []} for tid, p in parts.items()}
    rows = {tid: _events(charts[tid], p, lambda s: s*2, 8, 'songsterr') for tid, p in parts.items()}
    limits = [{'trackId': '1', 'start': 4, 'end': 8, 'reason': 'unsupported_source_gesture'}] if disclosed else []
    from feedback_converter.song_import.verification import Check
    from feedback_converter.song_import.verify_hybrid_priority import audit
    result, report = Check(), {}
    audit(charts, receipt_for(rows, use_adrian=False, limitations=limits, limited=disclosed), facts,
          {'policy': 'hybrid-lead-v3', 'mainTrackId': '0'}, lambda s: s*2,
          lambda q: q/2, 8, 'songsterr', result, source=source, musical_audit=report)
    assert report['requirementCountScope'] == 'retained_playable_source_events'
    assert report['requirementCount'] == 0  # All named-solo notes were projected out.
    assert report['unsupportedNamedRequirementCount'] == 1  # Adrian survives raw-source projection.
    if disclosed:
        assert not result.errors
    else:
        assert {'hybrid_regional_limitation', 'hybrid_coverage'} <= {e['code'] for e in result.errors}


@pytest.mark.parametrize('fault,code', [(None, None), ('revision', 'hybrid_selection_revision'),
                                      ('activity', 'hybrid_tab_activity'), ('scope', 'hybrid_coverage_scope')])
def test_contract_36_checks_activity_against_actual_chart_and_limits_claims(tmp_path, fault, code):
    from zipfile import ZipFile
    import yaml
    from test_songsterr_hybrid_lead import build
    from feedback_converter.song_import.verification import verify_import
    source, _, options, alignment, original, first = build(tmp_path)
    assert first['status'] == 'passed', first
    target = tmp_path/'edited.feedpak'
    with ZipFile(original) as old, ZipFile(target, 'w') as new:
        manifest = yaml.safe_load(old.read('manifest.yaml'))
        receipt = json.loads(old.read('import/hybrid-lead.json'))
        manifest['song_import']['preservationContract'] = 36
        summary = manifest['song_import']['hybridLeadResult']
        if fault == 'revision':
            receipt['selectionRevision'] = 1
            summary['selectionRevision'] = 1
        elif fault == 'activity':
            # Matching fabricated summaries are not source evidence.
            receipt['tabActivity']['hybridActiveSeconds'] += 1
            summary['tabActivity'] = deepcopy(receipt['tabActivity'])
        elif fault == 'scope':
            summary['coverageScope'] = 'all_musical_guitar_parts'
        for info in old.infolist():
            value = (yaml.safe_dump(manifest) if info.filename == 'manifest.yaml' else
                     json.dumps(receipt) if info.filename == 'import/hybrid-lead.json' else old.read(info.filename))
            new.writestr(info, value)
    result = verify_import(source, target, alignment, hybrid_options=options)
    if fault is None:
        assert result['status'] == 'passed', result
        assert result['hybridLead']['musicalAudit']['requirementCount'] == 0
    else:
        assert result['status'] == 'failed'
        assert code in {e['code'] for e in result['errors']}

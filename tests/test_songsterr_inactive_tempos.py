"""Unused clock instructions are accounted for; active clocks stay strict."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from feedback_converter.song_import.songsterr import parse
from feedback_converter.song_import.timeline import render
from feedback_converter.song_import.verify_source import songsterr
from feedback_converter.song_import.verify_timeline import expected
from feedback_converter.song_import.compatibility import inspect_songsterr
from test_song_import_score import raw_score, measure, beat
from test_songsterr_combined_tempo import clocks, rates
from test_song_import_compatibility_verification import reported_fixture
from test_song_import_verification import verify

REFERENCE = json.loads((Path(__file__).parent/'fixtures/songsterr_inactive_tempo_reference.json').read_text())


@pytest.mark.parametrize('case', REFERENCE['cases'])
def test_unattached_tempos_match_source_player_without_changing_notes(case):
    assert REFERENCE['referenceSha256'] == '4219f9dbf0952af83be36c8b590f10fea7a0f36d72bd1f92297a3d768f93f5ab'
    doc = raw_score([])
    doc['parts'][0] = deepcopy(case['input'])
    before = deepcopy(doc)
    want = rates([t for bar in case['expected']['clocks'] for t in bar])
    assert clocks(doc) == (want, want)
    control = deepcopy(doc)
    control['parts'][0]['automations']['tempo'] = control['parts'][0]['automations']['tempo'][:2]
    a, b = render(parse(doc)), render(parse(control))
    assert a['tracks'] == b['tracks']
    assert a['duration'] == b['duration']
    independently = expected(songsterr(doc), {'offset':0, 'scale':1})
    assert independently == expected(songsterr(control), {'offset':0, 'scale':1})
    assert a['duration'] == pytest.approx(independently['score_duration'])
    assert doc == before
    findings = [f for f in inspect_songsterr(doc)['findings'] if f['feature'] == 'tempo.outside_score']
    assert [f['value'] for f in findings] == doc['parts'][0]['automations']['tempo'][2:]


@pytest.mark.parametrize('case', REFERENCE['negative'])
def test_active_ramp_remains_blocked_because_native_preparation_changes(case):
    assert case['actual']['clocks'] != case['removed']['clocks']
    doc = raw_score([]); doc['parts'][0] = deepcopy(case['input'])
    for reader in (parse, songsterr):
        with pytest.raises(ValueError, match='ramp'): reader(doc)


@pytest.mark.parametrize('change', [
    {'bpm':0}, {'bpm':-3}, {'bpm':'NaN'}, {'type':0}, {'type':-1}, {'position':-1},
    {'measure':-1}, {'measure':True}, {'measure':1.5}, {'linear':1}, {'visible':1},
    {'dotted':1}, {'text':False}, {'unknownTempo':True},
])
def test_outside_entry_is_still_validated(change):
    doc = raw_score([measure(beat())])
    doc['parts'][0]['automations']['tempo'].append({'measure':8, 'position':0, 'bpm':90, **change})
    for reader in (parse, songsterr):
        with pytest.raises(ValueError): reader(doc)


@pytest.mark.parametrize('first', [{'measure':1}, {'position':960}])
def test_outside_event_cannot_supply_or_shift_initial_clock(first):
    doc = raw_score([measure(beat()), measure(beat())])
    auto = doc['parts'][0]['automations']
    auto['tempo'][0].update(first)
    auto['tempo'].append({'measure':3, 'bpm':90})
    for reader in (parse, songsterr):
        with pytest.raises(ValueError): reader(doc)


def test_inside_bar_end_coordinate_is_not_reclassified_as_unused():
    doc = raw_score([measure(beat())])
    doc['parts'][0]['automations']['tempo'].append({'measure':0, 'position':3840, 'bpm':90})
    for reader in (parse, songsterr):
        with pytest.raises(ValueError): reader(doc)


@pytest.mark.parametrize('fault', [None, 'missing_report', 'changed_value', 'wrong_note', 'wrong_time', 'downgrade', 'old_contract'])
def test_package_verifier_checks_retained_entry_and_music(tmp_path, fault):
    source, package = reported_fixture()
    source['parts'][0]['automations']['tempo'].append({'measure':8, 'bpm':50})
    report = package['import/compatibility.json']
    report['findings'].append({'feature':'tempo.outside_score', 'location':'parts/0/automations/tempo/1',
        'value':{'measure':8,'bpm':50}, 'valueTruncated':False, 'retained':'original_source',
        'impact':'display_or_expression', 'category':'source_interpretation'})
    report['findingCount'] += 1
    if fault == 'missing_report': report['findings'].pop(); report['findingCount'] -= 1
    if fault == 'changed_value': report['findings'][-1]['value']['bpm'] = 51
    if fault == 'wrong_note': package['chart.json']['notes'][0]['f'] += 1
    if fault == 'wrong_time': package['chart.json']['notes'][1]['t'] += .1
    if fault == 'downgrade':
        report['version'] = 47
        package['manifest.yaml']['song_import']['preservationContract'] = 48
    if fault == 'old_contract':
        report['version'] = 47
        report['findings'].pop(); report['findingCount'] -= 1
    result = verify(tmp_path, source, package)
    assert result['status'] == ('failed' if fault else 'passed'), result


def test_accounting_covers_duplicate_outside_entries_and_retains_all():
    doc = raw_score([measure(beat())])
    doc['parts'][0]['automations']['tempo'] += [{'measure':8, 'bpm':50}, {'measure':8, 'bpm':60}]
    findings = inspect_songsterr(doc)['findings']
    assert [f['feature'] for f in findings].count('tempo.outside_score') == 2
    assert [f['feature'] for f in findings].count('tempo.superseded') == 1
    assert clocks(doc) == clocks(raw_score([measure(beat())]))


def test_malformed_later_entry_is_not_masked_by_outside_context():
    doc = raw_score([measure(beat())])
    doc['parts'][0]['automations']['tempo'] += [{'measure':8, 'bpm':50}, None]
    for reader in (parse, songsterr):
        with pytest.raises(ValueError): reader(doc)

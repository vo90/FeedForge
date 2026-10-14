"""Prepared measure tempo is metadata; explicit automation remains authoritative."""
from copy import deepcopy
import json

import pytest

from feedback_converter.song_import.compatibility import inspect_songsterr
from feedback_converter.song_import.songsterr import parse
from feedback_converter.song_import.verify_source import songsterr
from test_song_import_score import beat, measure, raw_score
from test_song_import_verification import example
from test_songsterr_legacy_metadata import played, current_package, verify_files


def document():
    doc = raw_score([measure(beat(), repeatStart=True), measure(beat(fret=5), repeat=2)])
    doc['parts'][0]['automations']['tempo'].append(
        {'measure': 1, 'position': [0, 1], 'bpm': 90, 'type': 4})
    return doc


@pytest.mark.parametrize('value', [None, *({'type': unit, 'bpm': 47.5} for unit in (1, 2, 4, 8, 16, 32, 64)),
                                 {'type': 4, 'bpm': 90}])
def test_stale_or_matching_measure_metadata_never_changes_repeated_music(value):
    control = document()
    doc = deepcopy(control)
    for bar in doc['parts'][0]['measures']:
        bar['tempo'] = value
    original = deepcopy(doc)
    assert played(doc) == played(control)
    assert doc == original
    row = next(r for r in parse(doc).feature_inventory
               if r['scope'] == 'Songsterr measure' and r['field'] == 'tempo')
    assert row['count'] == 2 and row['representations'] == ['source']
    findings = inspect_songsterr(doc)['findings']
    assert len(findings) == 2
    assert all(r['feature'] == 'measure.tempo' and r['impact'] == 'source_retained'
               and r['category'] == 'source_metadata' and r['value'] == value for r in findings)
    doc['parts'][0]['automations']['tempo'][1]['bpm'] = 100
    assert played(doc) != played(control)


@pytest.mark.parametrize('location', ['playable', 'rest', 'excluded'])
@pytest.mark.parametrize('value', [False, True, 0, '', [], {}, {'bpm': 90}, {'type': 4},
    {'type': 4, 'bpm': 0}, {'type': 4, 'bpm': -1}, {'type': 4, 'bpm': True},
    {'type': 4, 'bpm': '90'}, {'type': 4, 'bpm': float('inf')},
    {'type': 4, 'bpm': float('nan')}, {'type': True, 'bpm': 90},
    {'type': 4.0, 'bpm': 90}, {'type': 3, 'bpm': 90},
    {'type': 4, 'bpm': 90, 'dotted': False}])
def test_malformed_values_do_not_hide_on_rests_or_excluded_parts(location, value):
    doc = document()
    part = doc['parts'][0]
    if location == 'excluded':
        part = deepcopy(part)
        doc['parts'].append(part)
        doc['tracks'].append({'id': 1, 'name': 'Piano', 'instrumentId': 0})
    if location == 'rest':
        part['measures'][1]['voices'][0]['beats'] = [
            {'duration': [1, 1], 'rest': True, 'notes': [{'rest': True}]}]
    part['measures'][1]['tempo'] = value
    for reader in (parse, songsterr):
        with pytest.raises(ValueError, match='tempo'):
            reader(doc)
    assert any(r['feature'] == 'measure.tempo' and r['impact'] == 'blocking'
               for r in inspect_songsterr(doc)['findings'])


def test_measure_metadata_cannot_replace_missing_or_invalid_automation():
    control = document()
    control['parts'][0]['automations']['tempo'] = []
    doc = deepcopy(control)
    doc['parts'][0]['measures'][0]['tempo'] = {'type': 4, 'bpm': 47}
    # The existing empty-map default still applies; local metadata is no fallback.
    assert played(doc) == played(control)
    doc['parts'][0]['automations']['tempo'] = [
        {'measure': 0, 'position': [0, 1], 'bpm': 0, 'type': 4}]
    for reader in (parse, songsterr):
        with pytest.raises(ValueError):
            reader(doc)


@pytest.mark.parametrize('fault', [None, 'missing', 'wrong_value', 'wrong_impact', 'missing_null'])
def test_archive_accounts_for_exact_retained_measure_tempo(tmp_path, fault):
    source, _ = example()
    source['parts'][0]['measures'][0]['tempo'] = None if fault == 'missing_null' else {'type': 4, 'bpm': 47}
    files, manifest, alignment, path = current_package(tmp_path, source)
    report = json.loads(files['import/compatibility.json'])
    row = next(r for r in report['findings'] if r['feature'] == 'measure.tempo')
    if fault == 'missing':
        report['findings'].remove(row)
        report['findingCount'] -= 1
    elif fault == 'wrong_value': row['value']['bpm'] = 120
    elif fault == 'wrong_impact': row['impact'] = 'display_or_expression'
    elif fault == 'missing_null': row.pop('value')
    files['import/compatibility.json'] = json.dumps(report).encode()
    result = verify_files(tmp_path, files, path, alignment)
    assert result['status'] == ('failed' if fault else 'passed'), result
    if fault:
        assert any(r['code'].startswith('compatibility') for r in result['errors'])

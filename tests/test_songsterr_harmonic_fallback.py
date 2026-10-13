"""Verified playback pitch does not invent a physical harmonic contact."""
from copy import deepcopy
import json
from pathlib import Path
from zipfile import ZipFile

import pytest
import yaml

from feedback_converter.harmonic_target import PLAYBACK_FALLBACK, valid_target
from feedback_converter.harmonic_changes import validate_changes
from feedback_converter.song_import import load_performance
from feedback_converter.song_import.builder import build_feedpak
from feedback_converter.song_import.compatibility import inspect_songsterr
from feedback_converter.song_import.songsterr import parse
from feedback_converter.song_import.timeline import render
from feedback_converter.song_import.verify_source import songsterr
from feedback_converter.song_import.verify_timeline import expected
from feedback_converter.song_import.verification import verify_import
from test_song_import_builder import inputs
from test_song_import_score import beat, measure, raw_score


def document(node=10, flag=True):
    item = beat(10, harmonic='artificial', harmonicFret=node)
    item['artificialHarmonic'] = flag
    return raw_score([measure(item)])


@pytest.mark.parametrize('node', [0, 1, 6, 10, 15, 25, 3.21])
@pytest.mark.parametrize('flag', [None, False, True])
def test_unknown_contact_uses_verified_pitch_without_guessing(node, flag):
    raw = document(node, flag)
    before = deepcopy(raw)
    actual = render(parse(raw))
    checked = expected(songsterr(raw), {'offset': 0, 'scale': 1})
    note, = actual['tracks'][0]['notes']
    independent, = checked['parts'][0]['notes']
    assert note['harmonic_target'] == independent['note']['harmonic_target'] == PLAYBACK_FALLBACK
    assert note['f'] == 10 and note['t'] == 0 and note['sus'] == 2
    written = actual['tracks'][0]['notation']['measures'][0]['staves']['staff']['voices'][0]['beats'][0]['notes'][0]
    assert written['midi'] == 98
    report = inspect_songsterr(raw)
    assert report['status'] == 'limitations'
    retained, = [r for r in report['findings'] if r['feature'] == 'beat.artificialHarmonic']
    warning, = [r for r in report['findings'] if r['feature'] == 'note.harmonicFret']
    assert retained['impact'] == 'source_retained' and retained['value'] is flag
    assert warning['value'] == node and warning['impact'] == 'display_or_expression'
    assert raw == before


@pytest.mark.parametrize('node', [None, '10', True, -1, 7.0001, 9.5999, float('inf'), float('nan')])
def test_unqualified_values_do_not_become_octave_fallback(node):
    raw = document(node)
    with pytest.raises(ValueError): parse(raw)
    with pytest.raises(ValueError): songsterr(raw)


@pytest.mark.parametrize('changes', [{'harmonic': 'semi'}, {'harmonic': 'tapped'},
    {'harmonic': 'feedback'}, {'harmonic': 'unknown'}, {'harmonicData': {}},
    {'harmonicData': {'type': 'ah', 'note': 10, 'shift': 2}}])
def test_other_harmonic_semantics_remain_guarded(changes):
    raw = document()
    raw['parts'][0]['measures'][0]['voices'][0]['beats'][0]['notes'][0].update(changes)
    with pytest.raises(ValueError): parse(raw)
    with pytest.raises(ValueError): songsterr(raw)


@pytest.mark.parametrize('fault', ['plain', 'natural', 'mixed', 'rest', 'dead', 'wrong_type'])
def test_beat_marker_requires_matching_explicit_notes(fault):
    raw = document(12)
    item = raw['parts'][0]['measures'][0]['voices'][0]['beats'][0]
    if fault == 'plain': item['notes'][0].pop('harmonic')
    elif fault == 'natural': item['notes'][0].update(harmonic='natural', fret=12)
    elif fault == 'mixed': item['notes'].append({'string': 1, 'fret': 5})
    elif fault == 'rest': item.update(rest=True, notes=[])
    elif fault == 'dead': item['notes'][0]['dead'] = True
    else: item['artificialHarmonic'] = 1
    assert inspect_songsterr(raw)['status'] == 'blocked'
    with pytest.raises(ValueError): parse(raw)
    with pytest.raises(ValueError): songsterr(raw)


@pytest.mark.parametrize('field,value', [('basis', 'guessed'), ('kind', 'tapped'),
    ('node', 0), ('interval', 24.0), ('interval', 12), ('policy', 'mixed'), ('extra', 1)])
def test_serialized_fallback_is_strict(field, value):
    target = deepcopy(PLAYBACK_FALLBACK)
    assert valid_target(target)
    target[field] = value
    assert not valid_target(target)


def test_fallback_never_becomes_a_delayed_physical_contact():
    raw = raw_score([measure(beat(10, duration=(1, 2)),
        beat(10, duration=(1, 2), tie=True, harmonic='artificial', harmonicFret=10))])
    actual = render(parse(raw))
    checked = expected(songsterr(raw), {'offset': 0, 'scale': 1})
    assert 'harmonic_changes' not in actual['tracks'][0]['notes'][0]
    assert 'harmonic_changes' not in checked['parts'][0]['notes'][0]['note']
    assert actual['harmonicTieEvidence'][0]['rule'] == 'initial-target-continued'
    with pytest.raises(ValueError):
        validate_changes({'f': 10, 'sus': 2, 'harmonic_changes': {'version': 1, 'events': [
            {'start': 1, 'end': 2, 'target': PLAYBACK_FALLBACK, 'source_id': 'test'}]}})


def package(tmp_path, *, contract=123, source=True):
    raw = document()
    path = tmp_path/'source.json'
    path.write_text(json.dumps(raw), encoding='utf-8')
    performance = load_performance(path)
    _, audio, _, job = inputs(tmp_path)
    alignment = {'status': 'validated', 'offset': 1, 'scale': 1}
    built = build_feedpak(performance, audio, alignment, job, output_dir=tmp_path/'out',
        source_path=path if source else None, compatibility=performance['compatibilityReport'],
        recipe={'preservationContract': contract})
    with ZipFile(built['stagingPath']) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    return path, files, yaml.safe_load(files['manifest.yaml']), alignment


@pytest.mark.parametrize('fault', [None, 'pitch', 'contact', 'basis', 'missing_warning',
    'source_value', 'missing_flag', 'flag_type', 'old_contract', 'old_inventory'])
def test_archive_independently_checks_fallback_and_retained_source(tmp_path, fault):
    path, files, manifest, alignment = package(tmp_path)
    arr = manifest['arrangements'][0]
    chart = json.loads(files[arr['file']])
    report_path = manifest['song_import']['compatibilityFile']
    report = json.loads(files[report_path])
    warning = next(r for r in report['findings'] if r['feature'] == 'note.harmonicFret')
    flag = next(r for r in report['findings'] if r['feature'] == 'beat.artificialHarmonic')
    if fault == 'pitch': chart['notes'][0]['harmonic_target']['interval'] = 12
    elif fault == 'contact': chart['notes'][0]['harmonic_target']['node'] = 5
    elif fault == 'basis': chart['notes'][0]['harmonic_target'].pop('basis')
    elif fault == 'missing_warning': report['findings'].remove(warning)
    elif fault == 'source_value': warning['value'] = 5
    elif fault == 'missing_flag': report['findings'].remove(flag)
    elif fault == 'flag_type': flag['value'] = 1
    elif fault == 'old_contract': manifest['song_import']['preservationContract'] = 122
    elif fault == 'old_inventory': report['version'] = 122
    report['findingCount'] = len(report['findings'])
    files[arr['file']] = json.dumps(chart).encode()
    files[report_path] = json.dumps(report).encode()
    files['manifest.yaml'] = yaml.safe_dump(manifest).encode()
    target = tmp_path/'checked.feedpak'
    with ZipFile(target, 'w') as archive:
        for name, content in files.items(): archive.writestr(name, content)
    result = verify_import(path, target, alignment)
    assert result['status'] == ('passed' if fault is None else 'failed'), result


@pytest.mark.parametrize('kwargs', [{'contract': 122}, {'source': False}])
def test_builder_requires_new_contract_and_original_source(tmp_path, kwargs):
    with pytest.raises(Exception, match='original tab.*123'):
        package(tmp_path, **kwargs)

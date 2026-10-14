"""Independent archive verification of natural-harmonic playback interpretation."""
from copy import deepcopy
import json
from zipfile import ZipFile

import pytest
import yaml

from feedback_converter.song_import import load_performance
from feedback_converter.song_import.builder import build_feedpak
from feedback_converter.song_import.songsterr import parse
from feedback_converter.song_import.timeline import render
from feedback_converter.song_import.verification import verify_import
from feedback_converter.song_import.verify_source import songsterr
from feedback_converter.song_import.verify_timeline import expected
from test_song_import_builder import inputs
from test_song_import_score import beat, measure, raw_score


def document(fret=18, **properties):
    item = beat(fret, harmonic='natural', **properties)
    item['harmonic'] = True
    return raw_score([measure(item)])


@pytest.mark.parametrize('fret', [1, 11, 13, 14, 18, 20, 21, 23])
@pytest.mark.parametrize('touch', ['omitted', 'null', 'same'])
def test_independent_fallback_is_ordinary_pitch_and_historical_guard(fret, touch):
    props = {} if touch == 'omitted' else {'harmonicFret': None if touch == 'null' else fret}
    raw = document(fret, **props)
    before = deepcopy(raw)
    checked = expected(songsterr(raw), {'offset': 0, 'scale': 1})
    note, = checked['parts'][0]['notes']
    assert note['note']['f'] == fret
    assert not any(key in note['note'] for key in ('hm', 'hn', 'hps', 'harmonic_alias', 'harmonic_target'))
    atom, = checked['parts'][0]['source'].bars[0]
    assert atom.pitch_offset == 0
    assert raw == before
    with pytest.raises(ValueError):
        songsterr(raw, natural_playback=False)


@pytest.mark.parametrize('fret,node,pitch', [(2, 2.4, 36), (3, 3.2, 31), (6, 5.8, 34),
    (8, 8.2, 36), (10, 9.6, 34), (15, 14.7, 34), (22, 21.7, 34)])
def test_independent_shorthand_has_precise_node_but_no_invented_alias(fret, node, pitch):
    checked = expected(songsterr(document(fret)), {'offset': 0, 'scale': 1})
    row, = checked['parts'][0]['notes']
    assert {key: row['note'][key] for key in ('hm', 'hn', 'hps')} == {'hm': True, 'hn': node, 'hps': pitch}
    assert 'harmonic_alias' not in row['note']
    with pytest.raises(ValueError):
        songsterr(document(fret), natural_playback=False)


def test_historical_explicit_alias_keeps_its_existing_semantics():
    checked = expected(songsterr(document(15, harmonicFret=15), natural_playback=False), {'offset': 0, 'scale': 1})
    row, = checked['parts'][0]['notes']
    assert row['note']['harmonic_alias'] == 'songsterr-natural-15'
    assert row['note']['hn'] == 14.7 and row['note']['hps'] == 34


@pytest.mark.parametrize('properties', [dict(harmonicFret=12), dict(harmonicFret=True),
    dict(harmonicFret='18'), dict(harmonicFret=18.001), dict(harmonicData={}),
    dict(harmonicData={'x': 1}), dict(dead=True), dict(dead=True, pickScrape='up')])
def test_independent_fallback_does_not_admit_unqualified_contacts(properties):
    with pytest.raises(ValueError):
        songsterr(document(**properties))


def package(tmp_path, fret):
    raw = document(fret)
    path = tmp_path/'source.json'
    path.write_text(json.dumps(raw), encoding='utf-8')
    performance = load_performance(path)
    _, audio, _, job = inputs(tmp_path)
    alignment = {'status': 'validated', 'offset': 1, 'scale': 1}
    built = build_feedpak(performance, audio, alignment, job, output_dir=tmp_path/'out',
        source_path=path, compatibility=performance['compatibilityReport'],
        recipe={'preservationContract': 125})
    with ZipFile(built['stagingPath']) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    return path, files, yaml.safe_load(files['manifest.yaml']), alignment


@pytest.mark.parametrize('fret', [18, 15])
@pytest.mark.parametrize('fault', [None, 'effect', 'fret', 'missing_warning', 'source_value',
    'impact', 'category', 'work_status', 'track', 'old_contract', 'old_inventory', 'notation_pitch'])
def test_archive_independently_checks_pitch_instruction_and_disclosure(tmp_path, fret, fault):
    path, files, manifest, alignment = package(tmp_path, fret)
    arr = manifest['arrangements'][0]
    chart = json.loads(files[arr['file']])
    report_path = manifest['song_import']['compatibilityFile']
    report = json.loads(files[report_path])
    name = 'note.natural_harmonic_fallback' if fret == 18 else 'note.natural_harmonic_shorthand'
    warning, = [row for row in report['findings'] if row['feature'] == name]
    if fault == 'effect':
        if fret == 18:
            chart['notes'][0]['hm'] = True
        else:
            chart['notes'][0]['hps'] = 19
    elif fault == 'fret': chart['notes'][0]['f'] = 12
    elif fault == 'missing_warning': report['findings'].remove(warning)
    elif fault == 'source_value': warning['value'] = 'artificial'
    elif fault == 'impact': warning['impact'] = 'gameplay_omission'
    elif fault == 'category': warning['category'] = 'source_metadata'
    elif fault == 'work_status': warning['workStatus'] = 'technical_work'
    elif fault == 'track': warning['trackId'] = 'not-a-track'
    elif fault == 'old_contract': manifest['song_import']['preservationContract'] = 124
    elif fault == 'old_inventory': report['version'] = 124
    elif fault == 'notation_pitch':
        notation = json.loads(files[arr['notation']])
        notation['measures'][0]['staves']['staff']['voices'][0]['beats'][0]['notes'][0]['midi'] += 1
        files[arr['notation']] = json.dumps(notation).encode()
    report['findingCount'] = len(report['findings'])
    report['status'] = 'limitations' if any(row['impact'] != 'source_retained' for row in report['findings']) else 'compatible'
    files[arr['file']] = json.dumps(chart).encode()
    files[report_path] = json.dumps(report).encode()
    files['manifest.yaml'] = yaml.safe_dump(manifest).encode()
    target = tmp_path/'checked.feedpak'
    with ZipFile(target, 'w') as archive:
        for name, content in files.items(): archive.writestr(name, content)
    result = verify_import(path, target, alignment)
    wanted = 'passed' if fault is None else 'unsupported' if fault == 'old_contract' else 'failed'
    assert result['status'] == wanted, result


@pytest.mark.parametrize('fret', [18, 15])
@pytest.mark.parametrize('fault', ['no_source', 'old_contract', 'float_contract', 'old_inventory',
    'float_inventory', 'missing_policy', 'wrong_policy'])
def test_builder_requires_original_source_and_current_harmonic_contract(tmp_path, fret, fault):
    path = tmp_path/'source.json'
    path.write_text(json.dumps(document(fret)), encoding='utf-8')
    performance = load_performance(path)
    report = performance['compatibilityReport']
    contract = 124 if fault == 'old_contract' else 125.0 if fault == 'float_contract' else 125
    if fault == 'old_inventory': report['version'] = 124
    elif fault == 'float_inventory': report['version'] = 125.0
    elif fault == 'missing_policy': performance['source'].pop('naturalHarmonicPlaybackPolicy')
    elif fault == 'wrong_policy': performance['source']['naturalHarmonicPlaybackPolicy'] = 'guessed'
    _, audio, _, job = inputs(tmp_path)
    with pytest.raises(Exception, match='original tab.*125'):
        build_feedpak(performance, audio, {'status': 'validated', 'offset': 1, 'scale': 1}, job,
            output_dir=tmp_path/'out', source_path=None if fault == 'no_source' else path,
            compatibility=report, recipe={'preservationContract': contract})


@pytest.mark.parametrize('fret', [18, 15])
def test_coordinated_policy_and_inventory_stripping_never_passes_independent_verifier(tmp_path, fret):
    path = tmp_path/'source.json'
    path.write_text(json.dumps(document(fret)), encoding='utf-8')
    performance = load_performance(path)
    performance['source'].pop('naturalHarmonicPlaybackPolicy')
    report = performance['compatibilityReport']
    report['findings'] = [r for r in report['findings'] if not r['feature'].startswith('note.natural_harmonic_')]
    report.update(version=124, findingCount=len(report['findings']), status='compatible')
    _, audio, _, job = inputs(tmp_path)
    alignment = {'status': 'validated', 'offset': 1, 'scale': 1}
    built = build_feedpak(performance, audio, alignment, job, output_dir=tmp_path/'out',
        source_path=path, compatibility=report, recipe={'preservationContract': 124})
    assert verify_import(path, built['stagingPath'], alignment)['status'] == 'unsupported'


@pytest.mark.parametrize('kind', ['artificial', 'pinch', 'semi', 'tapped', 'feedback'])
def test_fallback_cannot_silently_replace_an_active_tied_harmonic(kind):
    first = beat(18, duration=(1, 2), harmonic=kind, harmonicFret=12)
    second = beat(18, duration=(1, 2), harmonic='natural', tie=True)
    raw = raw_score([measure(first, second)])
    with pytest.raises(ValueError, match='harmonic'):
        render(parse(raw))
    with pytest.raises(ValueError, match='harmonic'):
        expected(songsterr(raw), {'offset': 0, 'scale': 1})


def test_fallback_cannot_replace_a_delayed_contact_either():
    raw = raw_score([measure(beat(18, duration=(1, 4)),
        beat(18, duration=(1, 4), harmonic='artificial', harmonicFret=12, tie=True),
        beat(18, duration=(1, 2), harmonic='natural', tie=True))])
    with pytest.raises(ValueError, match='harmonic'):
        render(parse(raw))
    with pytest.raises(ValueError, match='harmonic'):
        expected(songsterr(raw), {'offset': 0, 'scale': 1})


@pytest.mark.parametrize('first_kind', ['ordinary', 'fallback'])
def test_ordinary_target_can_continue_into_a_qualified_timed_artificial_contact(first_kind):
    first = beat(18, duration=(1, 2), **({'harmonic': 'natural'} if first_kind == 'fallback' else {}))
    raw = raw_score([measure(first,
        beat(18, duration=(1, 2), harmonic='artificial', harmonicFret=12, tie=True))])
    actual = render(parse(raw))
    checked = expected(songsterr(raw), {'offset': 0, 'scale': 1})
    assert len(actual['tracks'][0]['notes']) == len(checked['parts'][0]['notes']) == 1
    assert actual['tracks'][0]['notes'][0]['harmonic_changes'] == checked['parts'][0]['notes'][0]['note']['harmonic_changes']
    assert actual['harmonicTieEvidence'][0]['rule'] == 'timed-artificial-contact'


def test_unmarked_tie_keeps_existing_harmonic_inheritance():
    raw = raw_score([measure(beat(18, duration=(1, 2), harmonic='artificial', harmonicFret=12),
                             beat(18, duration=(1, 2), tie=True))])
    actual = render(parse(raw))
    checked = expected(songsterr(raw), {'offset': 0, 'scale': 1})
    note, = actual['tracks'][0]['notes']
    assert note['harmonic_target']['interval'] == 12 and note['sus'] == 2
    assert note['harmonic_target'] == checked['parts'][0]['notes'][0]['note']['harmonic_target']


@pytest.mark.parametrize('fret,touch', [(12, None), (19, None), (24, None), (15, 15), (3, 2.7)])
def test_historical_contract124_archives_keep_existing_exact_harmonic_rules(tmp_path, fret, touch):
    path = tmp_path/'source.json'
    path.write_text(json.dumps(document(fret, harmonicFret=touch)), encoding='utf-8')
    performance = load_performance(path)
    assert 'naturalHarmonicPlaybackPolicy' not in performance['source']
    report = performance['compatibilityReport']
    report['version'] = 124
    _, audio, _, job = inputs(tmp_path)
    alignment = {'status': 'validated', 'offset': 1, 'scale': 1}
    built = build_feedpak(performance, audio, alignment, job, output_dir=tmp_path/'out',
        source_path=path, compatibility=report, recipe={'preservationContract': 124})
    checked = verify_import(path, built['stagingPath'], alignment)
    assert checked['status'] == 'passed', checked

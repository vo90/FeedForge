from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
from zipfile import ZipFile

import pytest
import yaml

from feedback_converter.song_import import load_performance
from feedback_converter.song_import.compatibility import inspect_songsterr
from feedback_converter.song_import.songsterr import parse
from feedback_converter.song_import.verify_source import songsterr
from feedback_converter.song_import.verify_timeline import expected
from feedback_converter.song_import.verification import verify_import
from feedback_converter.song_import.builder import build_feedpak
from test_song_import_score import raw_score, measure, beat

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.songsterr_compatibility.audit import evaluate, compare_preparation, compare_authored_events

FIXTURE = json.loads(Path(__file__).with_name('fixtures').joinpath('songsterr_inactive_bend_vibrato_reference.json').read_text())


def example(value=0):
    return raw_score([measure(beat(5, bend={'points': [
        {'position': 0, 'tone': 0, 'vibrato': value},
        {'position': 60, 'tone': 100, 'vibrato': value}]}))])


@pytest.mark.parametrize('case', FIXTURE['cases'], ids=lambda c: c['id'])
def test_native_reference_and_independent_music_are_unchanged(case):
    assert FIXTURE['referenceSha256'] == '4219f9dbf0952af83be36c8b590f10fea7a0f36d72bd1f92297a3d768f93f5ab'
    source = deepcopy(case['source']); original = deepcopy(source)
    absent = deepcopy(source)
    for point in absent['parts'][0]['measures'][0]['voices'][0]['beats'][0]['notes'][0]['bend']['points']:
        del point['vibrato']
    actual, baseline = evaluate(source), evaluate(absent)
    assert actual['converter']['status'] == actual['independent']['status'] == 'rendered', actual
    assert compare_preparation(actual, case['reference']) == []
    assert compare_authored_events(actual, case['reference'], source) == []
    for stage in ('converter', 'independent'):
        assert actual[stage]['events'] == baseline[stage]['events']
    assert expected(songsterr(source), {'offset': 0, 'scale': 1}) == expected(songsterr(absent), {'offset': 0, 'scale': 1})
    entries = [e for e in parse(source).feature_inventory if e['scope'] == 'Songsterr bend point' and e['field'] == 'vibrato']
    assert len(entries) == 1 and entries[0]['handling'] == 'source'
    assert entries[0]['count'] == 3 and entries[0]['representations'] == ['source']
    assert not any(f['feature'] == 'bend_point.vibrato' for f in inspect_songsterr(source)['findings'])
    assert source == original


@pytest.mark.parametrize('value', [0.0, -0.0, None, False, 0])
def test_inactive_spelling_is_accepted(value):
    source = example(value)
    assert inspect_songsterr(source)['status'] == 'compatible'
    parse(source); songsterr(source)


@pytest.mark.parametrize('value', [True, 1, -1, .1, '0', '', 'slight', [], {}, [0], float('nan'), float('inf'), -float('inf')])
def test_active_or_malformed_point_vibrato_is_not_silently_lost(value):
    source = example(value)
    report = inspect_songsterr(source)
    assert report['status'] == 'blocked'
    assert any(f['feature'] == 'bend_point.vibrato' and f['impact'] == 'blocking' for f in report['findings'])
    json.dumps(report, allow_nan=False)
    with pytest.raises(ValueError, match='bend-point vibrato'): parse(source)
    with pytest.raises(ValueError, match='Bend-point vibrato'): songsterr(source)


def test_zero_exception_does_not_leak_to_other_fields():
    source = example()
    source['parts'][0]['measures'][0]['voices'][0]['beats'][0]['notes'][0]['bend']['points'][0]['unknownTechnique'] = 0
    assert inspect_songsterr(source)['status'] == 'blocked'
    with pytest.raises(ValueError): parse(source)
    with pytest.raises(ValueError): songsterr(source)


@pytest.mark.parametrize('hybrid', [False, True])
@pytest.mark.parametrize('corruption', [None, 'bend', 'missing-vibrato', 'extra-vibrato', 'timing', 'notation'])
def test_feedpak_preserves_source_and_checks_music(tmp_path, corruption, hybrid):
    from test_song_import_builder import inputs
    _, audio, alignment, job = inputs(tmp_path)
    source = example()
    source['parts'][0]['measures'][0]['voices'][0]['beats'][0]['notes'][0]['leftHandVibrato'] = 'wide'
    path = tmp_path / 'source.json'; path.write_text(json.dumps(source))
    performance = load_performance(path, composition_context=hybrid)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    recipe = {'preservationContract': 86, 'source': 'songsterr', 'scoreHash': digest, 'audioHash': audio['hash']}
    options = None
    if hybrid:
        from feedback_converter.song_import.hybrid_lead import choose_main, normalize_options
        options = normalize_options({'enabled': True})
        options.update(mainTrackId=choose_main(performance, options, digest), sourceSha256=digest)
        recipe['hybridLead'] = options
    built = build_feedpak(performance, audio, alignment, job, output_dir=tmp_path/'out', source_path=path,
                          compatibility=performance['compatibilityReport'], recipe=recipe,
                          hybrid_lead={'enabled': True, 'mainTrackId': options['mainTrackId'], 'options': options} if hybrid else None)
    archive = Path(built['stagingPath'])
    with ZipFile(archive) as z: files = {n: z.read(n) for n in z.namelist()}
    manifest = yaml.safe_load(files['manifest.yaml']); arrangement = manifest['arrangements'][0]
    assert files[manifest['song_import']['sourceFile']] == path.read_bytes()
    if hybrid:
        assert len(manifest['arrangements']) == 2
    if corruption:
        name = arrangement['notation'] if corruption == 'notation' else arrangement['file']
        value = json.loads(files[name])
        if corruption == 'notation': value['measures'][0]['staves']['staff']['voices'][0]['beats'][0]['notes'][0]['fret'] = 7
        elif corruption == 'bend': value['notes'][0]['bnv'][-1]['v'] = 3
        elif corruption == 'missing-vibrato': value['notes'][0].pop('vibrato_marks')
        elif corruption == 'extra-vibrato': value['notes'][0]['vibrato_marks'].append({'start': 0, 'end': .3, 'kind': 'slight'})
        else: value['notes'][0]['t'] += .1
        files[name] = json.dumps(value).encode(); archive = tmp_path/'corrupt.feedpak'
        with ZipFile(archive, 'w') as z:
            for name, data in files.items(): z.writestr(name, data)
    report = verify_import(path, archive, alignment, hybrid_options=options)
    assert report['status'] == ('failed' if corruption else 'passed'), report

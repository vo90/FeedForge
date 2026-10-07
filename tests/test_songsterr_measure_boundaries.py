"""Measure-bounded playback, including independent tamper detection."""
from copy import deepcopy
from fractions import Fraction as F
import json
from pathlib import Path
from zipfile import ZipFile
import pytest
import yaml
from test_song_import_score import beat, measure, raw_score
from feedback_converter.song_import.songsterr import parse
from feedback_converter.song_import.timeline import render
from feedback_converter.song_import.verify_source import songsterr
from feedback_converter.song_import.verify_timeline import expected


def cases():
    return {
        'clip_tie': raw_score([measure(beat(duration=(15, 16)), beat(fret=8, duration=(1, 8))),
                              measure(beat(fret=8, duration=(1, 2), tie=True), beat(fret=9, duration=(1, 2)))]),
        'outside': raw_score([measure(beat(), beat(fret=8, duration=(1, 4)), beat(fret=9, duration=(1, 4))), measure(beat(fret=7))]),
        'outside_tie': raw_score([measure(beat(), beat(fret=3, duration=(1, 4), tie=True)), measure(beat(fret=3, tie=True))]),
        'individual_cap': raw_score([measure(beat(duration=(2, 1)), beat(fret=8, duration=(1, 4))), measure(beat(fret=7))]),
        'before_grace': raw_score([measure(beat(duration=(15, 16)), beat(fret=8, duration=(1, 8))),
                                  measure({**beat(fret=9, duration=(1, 32)), 'graceNote': 'beforeBeat'}, beat(fret=10))]),
        'on_grace': raw_score([measure(beat(duration=(7, 8)), {**beat(fret=9, duration=(1, 32)), 'graceNote': 'onBeat'}, beat(fret=10, duration=(1, 4))), measure(beat())]),
        'strum': raw_score([measure(beat(duration=(7, 8)), {'duration': [1, 4], 'brushStroke': {'direction': 'down', 'duration': 30, 'shift': 100},
                             'notes': [{'string': 0, 'fret': 7}, {'string': 1, 'fret': 9}]}), measure(beat())]),
        'tuplet': raw_score([measure(*[beat(fret=3+i, duration=(1, 6)) for i in range(7)]), measure(beat())]),
        'ordinary': raw_score([measure(beat(duration=(1, 2)), beat(fret=8, duration=(1, 2))), measure(beat())]),
    }


@pytest.mark.parametrize('name', list(cases()))
def test_general_clocks_match_independent_reader_and_keep_source(name):
    doc = cases()[name]; original = deepcopy(doc)
    score = parse(doc); independent = songsterr(doc)
    assert score.source.get('measureBoundaryAdjustments', []) == independent.boundary_adjustments
    p = render(score); v = expected(independent, {'offset': 0, 'scale': 1})
    assert len(p['tracks'][0]['notes']) == len(v['parts'][0]['notes'])
    assert doc == original
    assert p['sourceScore']['document'] == original
    assert p['duration'] == 4
    if name == 'clip_tie':
        note = next(n for n in p['tracks'][0]['notes'] if n['f'] == 8)
        assert note['t'] == 1.875
        assert note['sus'] == 1.125
    if name == 'outside':
        assert [n['f'] for n in p['tracks'][0]['notes']] == [3, 7]
        assert p['tracks'][0]['notes'][1]['t'] == 2
    if name == 'ordinary':
        assert not independent.boundary_adjustments


@pytest.mark.parametrize('delta,omitted', [(F(-1, 10000), False), (F(0), True), (F(1, 10000), True)])
def test_attack_boundary_is_exact(delta, omitted):
    duration = (F(4) + delta) / 4
    # Split the prefix so native per-beat duration cap does not mask delta.
    doc = raw_score([measure(beat(duration=(1, 2)), beat(duration=((duration-F(1,2)).numerator, (duration-F(1,2)).denominator)), beat(fret=9, duration=(1, 4)))])
    parsed = parse(doc)
    assert any(r['action'] == 'omit' for r in parsed.source.get('measureBoundaryAdjustments', [])) is omitted


def test_repeats_do_not_lengthen_bar_or_move_following_attacks():
    doc = cases()['outside']; doc['parts'][0]['measures'][0].update(repeatStart=True, repeat=2)
    p = render(parse(doc))
    assert p['duration'] == 6
    assert [n['t'] for n in p['tracks'][0]['notes']] == [0, 2, 4]


@pytest.mark.parametrize('case', ['clip_tie', 'outside', 'individual_cap'])
@pytest.mark.parametrize('fault', [None, 'receipt_missing', 'receipt_extra', 'wrong_duration', 'report_missing', 'contract', 'missing_note'])
def test_package_and_tamper_detection(tmp_path, fault, case):
    from test_song_import_builder import inputs
    from feedback_converter.song_import import load_performance
    from feedback_converter.song_import.builder import build_feedpak
    from feedback_converter.song_import.verification import verify_import
    _, audio, _, job = inputs(tmp_path)
    path = tmp_path/'source.json'; path.write_text(json.dumps(cases()[case]))
    p = load_performance(path); alignment = {'status': 'validated', 'offset': 0, 'scale': 1}
    result = build_feedpak(p, audio, alignment, job, output_dir=tmp_path/'out', source_path=path,
                          compatibility=p['compatibilityReport'], recipe={'preservationContract': 99})
    archive = Path(result['stagingPath'])
    if fault:
        with ZipFile(archive) as z: files = {n:z.read(n) for n in z.namelist()}
        manifest = yaml.safe_load(files['manifest.yaml'])
        receipt = json.loads(files['import/measure-boundaries.json'])
        if fault == 'receipt_missing': receipt['adjustments'] = []
        if fault == 'receipt_extra': receipt['adjustments'] *= 2
        if fault == 'wrong_duration': receipt['adjustments'][0]['usedDuration'] = '1/2'
        files['import/measure-boundaries.json'] = json.dumps(receipt).encode()
        if fault == 'report_missing':
            report = json.loads(files['import/compatibility.json']); report.update(findings=[], findingCount=0, status='compatible')
            files['import/compatibility.json'] = json.dumps(report).encode()
        if fault == 'contract':
            manifest['song_import']['preservationContract'] = 98
            files['manifest.yaml'] = yaml.safe_dump(manifest).encode()
        if fault == 'missing_note':
            name = manifest['arrangements'][0]['file']; chart = json.loads(files[name]); chart['notes'].pop()
            files[name] = json.dumps(chart).encode()
        archive = tmp_path/'changed.feedpak'
        with ZipFile(archive, 'w') as z:
            for name, data in files.items(): z.writestr(name, data)
    checked = verify_import(path, archive, alignment)
    assert checked['status'] == ('failed' if fault else 'passed'), checked


def test_old_contract_does_not_silently_gain_new_interpretation():
    with pytest.raises(ValueError, match='overfull'):
        songsterr(cases()['outside'], bounded_measures=False)


@pytest.mark.parametrize('name', list(cases()))
def test_reviewed_native_reference_clocks_and_emitted_attacks(name):
    fixture = json.loads((Path(__file__).parent/'fixtures/songsterr_measure_boundaries_reference.json').read_text())
    assert fixture['referenceSha256'] == '4219f9dbf0952af83be36c8b590f10fea7a0f36d72bd1f92297a3d768f93f5ab'
    native = next(c for c in fixture['cases'] if c['id'] == name)['parts'][0]
    score = parse(cases()[name]); p = render(score)
    beats = {b['id']: b for b in native['beats']}
    for bi, voices in enumerate(score.tracks[0].written_bars):
        for voice in voices:
            for beat in voice.beats:
                row = beats[':'.join(beat.source_id.split(':')[2:])]
                assert float(beat.position) == pytest.approx(row['quarter'], abs=1e-12)
                assert float(beat.duration) == pytest.approx(row['duration'], abs=1e-12)
    # Native event end ticks are inclusive; the importer uses exclusive ends.
    native_events = {e['id']: e for e in native['events'] if not e['hidden']}
    actual_notes = p['tracks'][0]['notes'] + [n for c in p['tracks'][0].get('chords', []) for n in c['notes']]
    assert len(actual_notes) == len(native_events)
    for note in actual_notes:
        source_id = ':'.join(note['source_ids'][0].split(':')[2:])
        event = native_events[source_id]
        assert note['t'] == pytest.approx(event['attackTick']/native['tpqn']/2, abs=1e-6)
        assert note['t']+note['sus'] == pytest.approx((event['endTick']+1)/native['tpqn']/2, abs=1e-6)

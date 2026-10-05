"""The first raw tempo measure is the origin; unrelated coordinates never move."""
from copy import deepcopy
from fractions import Fraction as F
import json

import pytest

from feedback_converter.song_import import load_performance
from feedback_converter.song_import.compatibility import inspect_songsterr
from feedback_converter.song_import.songsterr import parse
from feedback_converter.song_import.timeline import render
from feedback_converter.song_import.verify_source import songsterr
from feedback_converter.song_import.verify_timeline import Clock, expected, visits
from test_song_import_score import beat, measure, raw_score
from test_songsterr_combined_tempo import clocks
from test_song_import_verification import example
from test_songsterr_legacy_metadata import current_package, verify_files


def document(origin=1, count=6, bpm=65):
    doc = raw_score([measure(beat(fret=3 + i)) for i in range(count)])
    doc['parts'][0]['automations']['tempo'] = [{'measure': origin, 'position': 0, 'type': 4, 'bpm': bpm}]
    return doc


def boundaries(doc):
    performance = render(parse(doc))
    source = songsterr(doc)
    clock = Clock(source, visits(source))
    actual = [row['start'] for row in performance['scoreTimeline']['measures']] + [performance['duration']]
    independent = [clock.at(F(4 * i)) for i in range(len(source.bars) + 1)]
    return actual, independent


@pytest.mark.parametrize('origin', [0, 1, 3])
def test_opening_origin_matches_pinned_native_65_bpm_boundary_control(origin):
    # Captured native synth and browser clocks both give 4*60/65 from bar zero.
    doc = document(origin)
    original = deepcopy(doc)
    wanted = [i * 240 / 65 for i in range(7)]
    actual, independent = boundaries(doc)
    assert actual == pytest.approx(wanted)
    assert independent == pytest.approx(wanted)
    assert clocks(doc) == ({(0, F(0)): 65}, {(0, F(0)): 65})
    assert doc == original
    findings = [r for r in inspect_songsterr(doc)['findings'] if r['feature'] == 'tempo.origin']
    assert len(findings) == (1 if origin else 0)
    if findings:
        assert findings[0]['value'] == {'authoredOrigin': origin, 'effectiveOrigin': 0}
        assert findings[0]['location'] == 'parts/0/automations/tempo/0/measure'


@pytest.mark.parametrize('origin', [1, 3])
def test_later_entries_keep_their_relative_bar_and_within_bar_position(origin):
    doc = document(origin, 3)
    doc['parts'][0]['automations']['tempo'].append({'measure': origin + 1, 'position': 960, 'bpm': 90})
    want = {(0, F(0)): 65, (1, F(1)): 90}
    assert clocks(doc) == (want, want)
    actual, independent = boundaries(doc)
    wanted = [0, 240 / 65, 300 / 65 + 180 / 90, 300 / 65 + 420 / 90]
    assert actual == pytest.approx(wanted)
    assert independent == pytest.approx(wanted)


def test_origin_is_chosen_from_raw_list_before_sorting_or_replacement():
    doc = document(3)
    doc['parts'][0]['automations']['tempo'].append({'measure': 1, 'position': 0, 'bpm': 90})
    for reader in (parse, songsterr):
        with pytest.raises(ValueError, match='negative|origin|preced'):
            reader(doc)
    # Sorting first would silently admit a different clock, which is forbidden.
    sorted_copy = deepcopy(doc)
    sorted_copy['parts'][0]['automations']['tempo'].reverse()
    assert clocks(sorted_copy)[0] == {(0, F(0)): 90, (2, F(0)): 65}


def test_duplicate_replacement_uses_effective_coordinate_and_last_complete_entry():
    doc = document()
    auto = doc['parts'][0]['automations']['tempo']
    auto += [{'measure': 1, 'position': 0, 'bpm': 90},
             {'measure': 3, 'position': 0, 'bpm': 100},
             {'measure': 3, 'position': 0, 'bpm': 110}]
    want = {(0, F(0)): 90, (2, F(0)): 110}
    assert clocks(doc) == (want, want)
    findings = [r for r in inspect_songsterr(doc)['findings'] if r['feature'] == 'tempo.superseded']
    assert [r['value']['selectedIndex'] for r in findings] == [1, 3]
    assert findings[0]['value']['authored'] == auto[0]
    assert findings[1]['value']['authored'] == auto[2]


@pytest.mark.parametrize('change', [
    {'measure': True}, {'measure': -1}, {'measure': 1.5}, {'position': -1}, {'position': False},
    {'bpm': 0}, {'bpm': 'NaN'}, {'bpm': '65'}, {'bpm': True}, {'type': 0}, {'type': '4'},
    {'linear': 1}, {'unknown': 0}, {'measure': 10000001}, {'position': 10000001},
    {'measure': [1, 1]}, {'measure': '1/1'}, {'measure': '١'}, {'position': '٠'},
])
def test_invalid_first_or_superseded_mark_is_validated_before_normalization(change):
    doc = document()
    auto = doc['parts'][0]['automations']['tempo']
    auto.append(deepcopy(auto[0]))
    auto[0].update(change)
    for reader in (parse, songsterr):
        with pytest.raises(ValueError):
            reader(doc)


@pytest.mark.parametrize('position', [None, False, [0, 1], '0/1', 480, []])
def test_nonzero_origin_requires_demonstrated_explicit_scalar_opening_position(position):
    doc = document()
    auto = doc['parts'][0]['automations']['tempo']
    if position is None:
        auto[0].pop('position')
    else:
        auto[0]['position'] = position
    for reader in (parse, songsterr):
        with pytest.raises(ValueError):
            reader(doc)


@pytest.mark.parametrize('position', [0, '0', 0.0])
def test_demonstrated_scalar_zero_positions_are_accepted(position):
    doc = document()
    doc['parts'][0]['automations']['tempo'][0]['position'] = position
    assert boundaries(doc)[0][1] == pytest.approx(240 / 65)


@pytest.mark.parametrize('position', [1, 480, [960, 1], '1/1', '٩٦٠'])
def test_shifted_later_marks_require_qualified_quarter_aligned_scalar_ticks(position):
    doc = document()
    doc['parts'][0]['automations']['tempo'].append({'measure': 2, 'position': position, 'bpm': 90})
    for reader in (parse, songsterr):
        with pytest.raises(ValueError):
            reader(doc)


@pytest.mark.parametrize('change', [{'bpm': 65.5}, {'type': 16}, {'bpm': 65, 'dotted': True}])
def test_shifted_scope_does_not_admit_unqualified_browser_rate_rounding(change):
    doc = document()
    doc['parts'][0]['automations']['tempo'][0].update(change)
    for reader in (parse, songsterr):
        with pytest.raises(ValueError):
            reader(doc)


@pytest.mark.parametrize('unit,bpm,rate', [(2, 65, 130), (4, 65, 65), (8, 130, 65)])
def test_shifted_native_equivalent_rate_units(unit, bpm, rate):
    doc = document()
    doc['parts'][0]['automations']['tempo'][0].update(type=unit, bpm=bpm)
    assert clocks(doc) == ({(0, F(0)): rate}, {(0, F(0)): rate})


@pytest.mark.parametrize('position,valid', [(1920, True), (2880, False), (3360, False)])
def test_effective_destination_meter_supplies_position_bounds(position, valid):
    doc = document(1, 3)
    doc['parts'][0]['measures'][1] = measure(beat(duration=(3, 4)), signature=[3, 4])
    doc['parts'][0]['automations']['tempo'].append({'measure': 2, 'position': position, 'bpm': 90})
    for reader in (parse, songsterr):
        if valid:
            reader(doc)
        else:
            with pytest.raises(ValueError):
                reader(doc)


def test_ramp_and_fermata_use_effective_tempos_but_fermata_bar_is_not_shifted():
    doc = document(3, 4, bpm=60)
    auto = doc['parts'][0]['automations']
    auto.update(gradualTempo=True, tempo=[{'measure': 3, 'position': 0, 'bpm': 60},
        {'measure': 5, 'position': 0, 'bpm': 120, 'linear': True}],
        fermata=[{'measure': 2, 'position': 1920, 'type': 'medium', 'length': .6}])
    control = deepcopy(doc)
    for row in control['parts'][0]['automations']['tempo']:
        row['measure'] -= 3
    assert clocks(doc) == clocks(control)
    assert (2, F(2)) in clocks(doc)[0]
    assert boundaries(doc) == boundaries(control)
    assert auto['fermata'][0]['measure'] == 2


def test_parts_with_different_raw_origins_must_agree_on_the_effective_clock():
    doc = document(1)
    doc['tracks'].append({**deepcopy(doc['tracks'][0]), 'id': 1})
    doc['parts'].append(deepcopy(doc['parts'][0]))
    doc['parts'][1]['automations']['tempo'][0]['measure'] = 3
    assert clocks(doc) == ({(0, F(0)): 65}, {(0, F(0)): 65})
    doc['parts'][1]['automations']['tempo'][0]['bpm'] = 66
    for reader in (parse, songsterr):
        with pytest.raises(ValueError, match='disagree|different'):
            reader(doc)


@pytest.mark.parametrize('invalid', ['missing_position', 'array_position', 'empty', 'fractional_rate'])
def test_one_shifted_part_requires_every_shared_part_clock_to_be_qualified(invalid):
    doc = document(3)
    doc['tracks'].append({**deepcopy(doc['tracks'][0]), 'id': 1})
    doc['parts'].append(deepcopy(doc['parts'][0]))
    auto = doc['parts'][1]['automations']['tempo']
    auto[0]['measure'] = 0
    if invalid == 'missing_position': auto[0].pop('position')
    elif invalid == 'array_position': auto[0]['position'] = [0, 1]
    elif invalid == 'empty': auto.clear()
    else: auto[0]['bpm'] = 65.5
    for reader in (parse, songsterr):
        with pytest.raises(ValueError):
            reader(doc)


def test_outside_accounting_uses_effective_measure_but_retains_raw_entry():
    doc = document(3, 2)
    auto = doc['parts'][0]['automations']['tempo']
    auto += [{'measure': 4, 'position': 0, 'bpm': 90}, {'measure': 5, 'position': 0, 'bpm': 110}]
    want = {(0, F(0)): 65, (1, F(0)): 90}
    assert clocks(doc) == (want, want)
    findings = [r for r in inspect_songsterr(doc)['findings'] if r['feature'] == 'tempo.outside_score']
    assert len(findings) == 1
    assert findings[0]['location'] == 'parts/0/automations/tempo/2'
    assert findings[0]['value'] == auto[2]


@pytest.mark.parametrize('fault', [None, 'missing', 'wrong_origin', 'float_origin', 'wrong_location', 'downgrade', 'wrong_time'])
def test_package_checker_independently_requires_current_origin_and_music(tmp_path, fault):
    source, _ = example()
    source['parts'][0]['automations']['tempo'][0]['measure'] = 3
    files, manifest, alignment, path = current_package(tmp_path, source)
    report = json.loads(files['import/compatibility.json'])
    target = next(r for r in report['findings'] if r['feature'] == 'tempo.origin')
    if fault == 'missing': report['findings'].remove(target); report['findingCount'] -= 1
    elif fault == 'wrong_origin': target['value']['authoredOrigin'] = 1
    elif fault == 'float_origin': target['value']['authoredOrigin'] = 3.0
    elif fault == 'wrong_location': target['location'] = 'parts/0/automations/tempo/1/measure'
    elif fault == 'downgrade': report['version'] = 87
    elif fault == 'wrong_time':
        chart_path = manifest['arrangements'][0]['file']
        chart = json.loads(files[chart_path]); chart['notes'][1]['t'] += .1
        files[chart_path] = json.dumps(chart).encode()
    files['import/compatibility.json'] = json.dumps(report).encode()
    result = verify_files(tmp_path, files, path, alignment)
    assert result['status'] == ('failed' if fault else 'passed'), result
    if fault == 'wrong_time':
        assert 'note_time' in {row['code'] for row in result['errors']}
    elif fault:
        assert any(row['code'].startswith('compatibility') for row in result['errors']), result


def test_recording_map_and_inverse_use_the_normalized_within_bar_clock(tmp_path):
    from feedback_converter.song_import.synchronization import align_from_songsterr, map_source_time
    from feedback_converter.song_import.hybrid_context import Clock as HybridClock
    from test_song_import_synchronization import METADATA, audio_fixture, synchronization
    doc = document(3, 2, bpm=120)
    doc.update(songId='12', revisionId='34')
    doc['parts'][0]['measures'][0] = measure(beat(fret=3, duration=(1, 2)),
        beat(fret=5, duration=(1, 4)), beat(fret=7, duration=(1, 4)))
    doc['parts'][0]['automations']['tempo'].append({'measure': 3, 'position': 1920, 'bpm': 60})
    source_path = tmp_path/'source.json'
    source_path.write_text(json.dumps(doc), encoding='utf-8')
    performance = load_performance(source_path, metadata=METADATA)
    alignment = align_from_songsterr(performance, audio_fixture(11), synchronization((.5, 6.5, 10.5)), METADATA)
    assert performance['duration'] == 7
    assert map_source_time(alignment, 2) == pytest.approx(4.5)
    assert alignment['tempos'] == [{'time': .5, 'bpm': 60}, {'time': 2.5, 'bpm': 30}, {'time': 6.5, 'bpm': 60}]
    # The source quarter at 2 score seconds is 3, rather than a uniform 8/3.
    assert HybridClock(performance['scoreTimeline']).quarter(2) == pytest.approx(3)
    independent = expected(songsterr(doc), alignment)
    assert independent['parts'][0]['notes'][2]['note']['t'] == pytest.approx(4.5)

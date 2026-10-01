"""Calculated source timestamps have precision; acoustic decisions stay exact."""
from copy import deepcopy

import pytest

from feedback_converter.song_import.local_sync import compare_assessment
from feedback_converter.song_import.verification import Check
from test_song_import_phrase_clock import phrase_package


def receipt():
    return {
        'version': 'recording-clock-v4', 'status': 'inconclusive',
        'audioSha256': 'audio', 'mapHash': 'map', 'audioDuration': 300.,
        'windowCount': 4, 'outroSupported': False,
        'windows': [{'start': 270., 'end': 300., 'bestOffset': .04}],
        'endingEvidence': {'groups': [{'time': 283.671563, 'status': 'supported',
            'offset': .04, 'parts': [{'trackId': 'lead', 'bestOffset': .04}]}],
            'supportedGroups': 1},
        'phraseEvidence': {'status': 'inconclusive', 'phrases': [{
            'trackId': 'lead', 'start': 270.123456, 'end': 283.671563,
            'times': [270.123456, 283.671563], 'offset': .04,
            'halves': [{'start': 270.123456, 'end': 283.671563, 'bestOffset': .04}]}],
            'coverage': {'status': 'inconclusive', 'intervals': [[270.123456, 283.671563]],
                'uncoveredAttackTimes': [282.009188], 'offsetSpread': .02}},
    }


TIME_PATHS = [
    ('endingEvidence', 'groups', 0, 'time'),
    *[('phraseEvidence', 'phrases', 0, key) for key in ('start', 'end')],
    ('phraseEvidence', 'phrases', 0, 'times', 1),
    *[('phraseEvidence', 'phrases', 0, 'halves', 0, key) for key in ('start', 'end')],
    ('phraseEvidence', 'coverage', 'uncoveredAttackTimes', 0),
    *[('phraseEvidence', 'coverage', 'intervals', 0, i) for i in (0, 1)],
]


def parent(value, path):
    for key in path[:-1]:
        value = value[key]
    return value, path[-1]


@pytest.mark.parametrize('path', TIME_PATHS)
@pytest.mark.parametrize('delta', [-.000002, -.000001, .000001, .000002])
def test_only_one_microsecond_source_time_rounding_is_accepted(path, delta):
    fresh = receipt(); stored = deepcopy(fresh)
    row, key = parent(stored, path); row[key] = round(row[key] + delta, 6)
    check = Check(); compare_assessment(fresh, stored, check, 'import/ending-padding-sync')
    assert bool(check.errors) == (abs(delta) > .0000011)
    assert fresh == receipt(), 'Comparison must not modify the source calculation.'


@pytest.mark.parametrize('bad', [None, True, '283.671563', float('nan'), float('inf'), -float('inf')])
def test_invalid_time_values_are_not_rounding(bad):
    fresh = receipt(); stored = deepcopy(fresh)
    stored['endingEvidence']['groups'][0]['time'] = bad
    check = Check(); compare_assessment(fresh, stored, check)
    assert check.errors


@pytest.mark.parametrize('path,new', [
    (('status',), 'supported'), (('outroSupported',), True),
    (('audioSha256',), 'other-audio'), (('mapHash',), 'other-map'),
    (('version',), 'other-version'), (('windowCount',), 4.000001),
    (('audioDuration',), 300.000001), (('windows', 0, 'start'), 270.000001),
    (('endingEvidence', 'supportedGroups'), 1.000001),
    (('endingEvidence', 'groups', 0, 'status'), 'unassessed'),
    (('endingEvidence', 'groups', 0, 'offset'), .040001),
    (('endingEvidence', 'groups', 0, 'parts', 0, 'bestOffset'), .040001),
    (('endingEvidence', 'groups', 0, 'parts', 0, 'trackId'), 'other-track'),
    (('phraseEvidence', 'coverage', 'status'), 'supported'),
    (('phraseEvidence', 'coverage', 'offsetSpread'), .020001),
    (('phraseEvidence', 'phrases', 0, 'halves', 0, 'bestOffset'), .040001),
])
def test_identity_decisions_counts_and_measured_offsets_stay_exact(path, new):
    fresh = receipt(); stored = deepcopy(fresh)
    row, key = parent(stored, path); row[key] = new
    check = Check(); compare_assessment(fresh, stored, check)
    assert check.errors


@pytest.mark.parametrize('fault', ['missing_time', 'extra_time', 'reorder', 'missing_field'])
def test_time_array_structure_and_order_stay_strict(fault):
    fresh = receipt(); stored = deepcopy(fresh)
    phrase = stored['phraseEvidence']['phrases'][0]
    if fault == 'missing_time': phrase['times'].pop()
    if fault == 'extra_time': phrase['times'].append(290.)
    if fault == 'reorder': phrase['times'].reverse()
    if fault == 'missing_field': phrase.pop('times')
    check = Check(); compare_assessment(fresh, stored, check)
    assert check.errors


def test_standalone_phrase_comparison_uses_same_precision():
    fresh = receipt()['phraseEvidence']; stored = deepcopy(fresh)
    stored['phrases'][0]['end'] -= .000001
    check = Check()
    compare_assessment(fresh, stored, check, 'import/ending-padding-sync/phraseEvidence')
    assert not check.errors


def test_unrecognized_time_field_does_not_gain_tolerance():
    fresh = {'otherEvidence': {'time': 10., 'start': 5., 'times': [1.]}}
    stored = {'otherEvidence': {'time': 10.000001, 'start': 5.000001, 'times': [1.000001]}}
    check = Check(); compare_assessment(fresh, stored, check)
    assert check.total_errors == 3


@pytest.mark.parametrize('delta', [-.000002, -.000001, .000001, .000002])
def test_full_archive_remeasures_audio_and_checks_receipt_precision(phrase_package, tmp_path, delta):
    import json
    from zipfile import ZipFile
    import yaml
    from feedback_converter.song_import import recording_sync as rs
    from feedback_converter.song_import.verification import verify_import

    root, source, result, meta = phrase_package
    assert result['ok'], result
    record = json.loads((root/'evidence/records'/f"{result['evidence']['id']}.json").read_text())
    alignment = json.loads((root/'evidence/objects'/record['objects']['appliedAlignment']).read_text())
    with ZipFile(result['stagingPath']) as z:
        files = {n: z.read(n) for n in z.namelist()}
    manifest = yaml.safe_load(files['manifest.yaml']); recipe = manifest['song_import']
    times = alignment['endingPaddingSync']['phraseEvidence']['phrases'][0]['times']
    times[-1] = round(times[-1] + delta, 6)
    # A self-consistent receipt must still be checked against a fresh analysis
    # of the original audio and independently reconstructed source positions.
    alignment['endingPadding']['syncEvidenceHash'] = rs.digest(alignment['endingPaddingSync'])
    files[recipe['endingPaddingSyncFile']] = json.dumps(alignment['endingPaddingSync']).encode()
    files[recipe['endingPaddingFile']] = json.dumps(alignment['endingPadding']).encode()
    recipe['alignment']['endingPadding'] = deepcopy(alignment['endingPadding'])
    files['manifest.yaml'] = yaml.safe_dump(manifest).encode()
    target = tmp_path/'changed.feedpak'
    with ZipFile(target, 'w') as z:
        for name, data in files.items(): z.writestr(name, data)
    report = verify_import(source, target, alignment, meta)
    assert report['status'] == ('passed' if abs(delta) <= .0000011 else 'failed'), report['errors']
    if abs(delta) > .0000011:
        assert any(e['code'] == 'timing_assessment_time' for e in report['errors'])

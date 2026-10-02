"""Hybrid coverage must invert the musical clock before serialization rounding."""
from copy import deepcopy
import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from test_song_import_builder import inputs
from test_songsterr_hybrid_lead import prepared, song
from feedback_converter.song_import.builder import build_feedpak
from feedback_converter.song_import.verification import verify_import


@pytest.fixture(params=['linear', 'opening', 'internal'])
def mapped_package(tmp_path, request):
    doc = song()
    # Four quarters occupy a short, but strictly increasing, recording interval.
    # An inverse search through rounded seconds introduces a systematic bias.
    if request.param == 'linear':
        alignment = {'status': 'validated', 'offset': 2, 'scale': .06}
    else:
        audio_points = [2, 2.12, 5, 8] if request.param == 'opening' else [0, 3, 3.12, 8]
        alignment = {'status': 'validated', 'mapping': 'piecewise-linear',
                     'anchors': [{'score': s, 'audio': a} for s, a in zip([0, 2, 4, 8], audio_points)]}
        alignment['tempos'] = [{'time': a, 'bpm': 120 * (s1-s) / (a1-a)}
                               for s, s1, a, a1 in zip([0, 2, 4], [2, 4, 8], audio_points, audio_points[1:])]
    source, performance, options = prepared(tmp_path, doc)
    _, audio, _, directory = inputs(tmp_path)
    recipe = {'preservationContract': 42, 'source': 'songsterr',
              'scoreHash': options['sourceSha256'], 'audioHash': audio['hash'], 'hybridLead': options}
    built = build_feedpak(performance, audio, alignment, directory, output_dir=tmp_path/'out',
                         source_path=source, recipe=recipe, compatibility=performance['compatibilityReport'],
                         hybrid_lead={'enabled': True, 'mainTrackId': options['mainTrackId'], 'options': options})
    return source, Path(built['stagingPath']), alignment, options


def test_valid_coverage_survives_short_recording_intervals(mapped_package):
    source, archive, alignment, options = mapped_package
    report = verify_import(source, archive, alignment, hybrid_options=options)
    assert report['status'] == 'passed', report
    with ZipFile(archive) as z:
        coverage = json.loads(z.read('import/hybrid-lead.json'))['coverage']['events']
    # These positions follow directly from the authored quarter-note fixture.
    assert any(row['start'] == 4 and row['end'] == 6 for row in coverage)


@pytest.mark.parametrize('field', ['start', 'end', 'recordingStart', 'recordingEnd'])
def test_changed_coverage_is_still_rejected(mapped_package, tmp_path, field):
    source, archive, alignment, options = mapped_package
    with ZipFile(archive) as z:
        files = {name: z.read(name) for name in z.namelist()}
    receipt = json.loads(files['import/hybrid-lead.json'])
    row = next(row for row in receipt['coverage']['events'] if row['start'] == 4)
    row[field] += .01
    files['import/hybrid-lead.json'] = json.dumps(receipt).encode()
    changed = tmp_path/'changed.feedpak'
    with ZipFile(changed, 'w') as z:
        for name, data in files.items():
            z.writestr(name, data)
    report = verify_import(source, changed, alignment, hybrid_options=deepcopy(options))
    assert report['status'] == 'failed', report
    assert any(e['code'] == 'hybrid_coverage' for e in report['errors']), report

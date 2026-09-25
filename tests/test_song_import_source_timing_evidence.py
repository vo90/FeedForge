from copy import deepcopy
import json
from pathlib import Path
from zipfile import ZipFile

import pytest
import yaml

from test_song_import_builder import inputs
from test_song_import_verification import example
from test_song_import_synchronization import METADATA, VIDEO, synchronization
from feedback_converter.song_import.builder import build_feedpak
from feedback_converter.song_import.compatibility import inspect_songsterr
from feedback_converter.song_import.songsterr import parse
from feedback_converter.song_import.timeline import render
from feedback_converter.song_import.synchronization import align_from_songsterr
from feedback_converter.song_import.verification import verify_import
from feedback_converter.song_import.worker import _alignment_summary


def package(tmp_path, points):
    document, _ = example()
    document['parts'][0]['measures'][0]['voices'][0]['beats'][3]['notes'][0]['fret'] = 12
    source = tmp_path/'score.json'
    source.write_text(json.dumps(document), encoding='utf-8')
    _, audio, _, job = inputs(tmp_path)
    audio['source'] = {'kind': 'youtube', 'videoId': VIDEO}
    performance = render(parse(document))
    alignment = align_from_songsterr(performance, audio, synchronization(points), METADATA)
    recipe = {'preservationContract': 20, 'audioSource': audio['source'],
              'alignment': {'provenance': deepcopy(alignment['provenance'])}}
    result = build_feedpak(performance, audio, alignment, job, output_dir=tmp_path/'out', recipe=recipe,
                           source_path=source, compatibility=inspect_songsterr(document))
    return source, Path(result['stagingPath']), alignment


@pytest.mark.parametrize('points', [(0, 2), (0, 2, 4), (0, 2, 4, 6)])
def test_retained_complete_map_is_independently_checked(tmp_path, points):
    source, archive, alignment = package(tmp_path, points)
    result = verify_import(source, archive, alignment, METADATA)
    assert result['status'] == 'passed', result
    assert 'retained_source_timing_boundaries' in result['scope']
    with ZipFile(archive) as z:
        assert json.loads(z.read('import/source-timing.json'))['points'] == list(points)
    assert 'sourceTiming' not in _alignment_summary(alignment)


@pytest.mark.parametrize('fault', ['unused_point', 'interior_point', 'missing', 'policy', 'anchor', 'recording', 'hash'])
def test_map_or_boundary_tampering_fails_verification(tmp_path, fault):
    source, archive, alignment = package(tmp_path, (0, 2, 4))
    with ZipFile(archive) as z:
        entries = {name:z.read(name) for name in z.namelist()}
    timing = json.loads(entries['import/source-timing.json'])
    manifest = yaml.safe_load(entries['manifest.yaml'])
    if fault == 'unused_point': timing['points'][-1] += .1
    elif fault == 'interior_point': timing['points'][1] += .1
    elif fault == 'missing': manifest['song_import'].pop('sourceTimingFile')
    elif fault == 'policy': alignment['provenance']['boundaryPolicy']['unusedTrailing'] = 0
    elif fault == 'anchor': alignment['anchors'][-1]['audio'] += .1
    elif fault == 'recording': manifest['song_import']['audioSource']['videoId'] = 'zzzzzzzzzzz'
    elif fault == 'hash': alignment['provenance']['mapHash'] = '0'*64
    entries['import/source-timing.json'] = json.dumps(timing).encode()
    entries['manifest.yaml'] = yaml.safe_dump(manifest).encode()
    bad = tmp_path/'bad.feedpak'
    with ZipFile(bad, 'w') as z:
        for name, data in entries.items(): z.writestr(name, data)
    result = verify_import(source, bad, alignment, METADATA)
    assert result['status'] == 'failed', result

"""Empty source references must not shift independently replayed timing rows."""
from copy import deepcopy
import json
from types import SimpleNamespace
from zipfile import ZipFile

import numpy as np
import pytest
import yaml

from feedback_converter.song_import import audio, local_sync as ls, recording_sync as rs, worker
from feedback_converter.song_import.verification import verify_import
from test_song_import_low_bass_sync import fixture as low_bass_fixture, META, VIDEO


def add_empty_references(source):
    raw = json.loads(source.read_text(encoding='utf8'))
    original_track, original_part = raw['tracks'][0], raw['parts'][0]
    tracks, parts = [], []
    for ident, empty in [('empty-before', True), ('live-a', False), ('empty-between', True),
                         ('live-b', False), ('empty-after', True)]:
        track, part = deepcopy(original_track), deepcopy(original_part)
        track.update(id=ident, name=ident, isEmpty=empty)
        # Provider flags cannot choose the acoustic roster: actual events do.
        if ident == 'live-b':
            track['isEmpty'] = True
        elif ident == 'empty-before':
            track['isEmpty'] = False
        if empty:
            for measure in part['measures']:
                measure['voices'] = [{'beats': [{'duration': [1, 1], 'rest': True, 'notes': []}]}]
        if ident == 'empty-between':
            track.update(instrumentId=25, tuning=[64, 59, 55, 50, 45, 40])
            part['strings'] = 6
        tracks.append(track)
        parts.append(part)
    raw.update(tracks=tracks, parts=parts)
    source.write_text(json.dumps(raw), encoding='utf8')


@pytest.fixture(scope='module', params=['cutoff', 'padding'])
def empty_reference_package(request, tmp_path_factory):
    """Real encoded audio and worker: only remote download is replaced."""
    root = tmp_path_factory.mktemp('empty-acoustic-' + request.param)
    duration = 48 if request.param == 'cutoff' else 49.75
    source, recording, _ = low_bass_fixture(root, duration=duration)
    add_empty_references(source)
    original = source.read_bytes()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(audio, '_public_url', lambda url: url)
        patch.setattr(audio, '_download_youtube', lambda *args, **kwargs: (
            recording, {'kind': 'youtube', 'videoId': VIDEO}))
        result = worker.run_import({
            'scorePath': str(source), 'metadata': META, 'artworkLookup': False,
            'audio': {'kind': 'url', 'url': f'https://youtu.be/{VIDEO}'},
            'workDir': str(root / 'work'), 'outputDir': str(root / 'out'),
            'synchronization': {'version': 1, 'source': 'songsterr-video-points', **META,
                'videoId': VIDEO, 'status': 'done', 'feature': None, 'points': list(range(0, 52, 2))}})
    assert result['ok'], result
    assert result['verification']['status'] == 'passed'
    assert source.read_bytes() == original
    evidence = root / 'song-import-evidence'
    record = json.loads((evidence / 'records' / (result['evidence']['id'] + '.json')).read_text())
    alignment = json.loads((evidence / 'objects' / record['objects']['appliedAlignment']).read_text())
    with ZipFile(result['stagingPath']) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    manifest = yaml.safe_load(files['manifest.yaml'])
    key = 'recordingSync' if request.param == 'cutoff' else 'endingPaddingSync'
    return source, result, alignment, files, manifest, key


def test_empty_source_tracks_are_retained_but_not_acoustic_rows(empty_reference_package):
    source, result, alignment, files, manifest, key = empty_reference_package
    recipe = manifest['song_import']
    assert files[recipe['sourceFile']] == source.read_bytes()
    assert [arrangement['id'] for arrangement in manifest['arrangements']] == ['live-a', 'live-b']
    stored = alignment[key]
    assert stored['version'] == (rs.CUTOFF_VERSION if key == 'recordingSync' else rs.PADDING_VERSION)
    assert stored['status'] == 'supported' and stored['lowBassEvidence']['windows']
    assert all([row['trackId'] for row in window['tracks']] == ['live-a', 'live-b']
               for window in stored['windows'])
    assert result['verification']['counts']['sourceTracks'] == 5


@pytest.mark.parametrize('fault', ['missing_real_track', 'renamed_real_track', 'invented_empty_row', 'changed_note'])
def test_empty_filter_does_not_accept_corrupted_receipts_or_notes(empty_reference_package, tmp_path, fault):
    source, _, original_alignment, original_files, original_manifest, key = empty_reference_package
    alignment, files, manifest = deepcopy(original_alignment), dict(original_files), deepcopy(original_manifest)
    recipe = manifest['song_import']
    if fault == 'changed_note':
        name = manifest['arrangements'][0]['file']
        chart = json.loads(files[name])
        chart['notes'][8]['f'] += 1
        files[name] = json.dumps(chart).encode()
    else:
        stored = alignment[key]
        rows = stored['windows'][0]['tracks']
        if fault == 'missing_real_track':
            rows.pop()
        elif fault == 'renamed_real_track':
            rows[1]['trackId'] = 'empty-between'
        else:
            rows.insert(1, {'trackId': 'empty-between', 'pitchedAttacks': 0, 'attacks': 0,
                            'status': 'inconclusive', 'reason': 'too_little_distinct_material'})
        policy_key = 'recordingEnd' if key == 'recordingSync' else 'endingPadding'
        policy = alignment[policy_key]
        policy['syncEvidenceHash'] = rs.digest(stored)
        recipe['alignment'][policy_key] = deepcopy(policy)
        files[recipe[key + 'File']] = json.dumps(stored).encode()
        if key == 'recordingSync':
            recipe['recordingEnd'] = deepcopy(policy)
            name = recipe['endingOmissionsFile']
            ledger = json.loads(files[name])
            ledger['syncEvidenceHash'] = policy['syncEvidenceHash']
            files[name] = json.dumps(ledger).encode()
        else:
            files[recipe['endingPaddingFile']] = json.dumps(policy).encode()
    files['manifest.yaml'] = yaml.safe_dump(manifest).encode()
    target = tmp_path / 'tampered.feedpak'
    with ZipFile(target, 'w') as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    report = verify_import(source, target, alignment, META)
    assert report['status'] == 'failed', report
    if fault == 'changed_note':
        assert any(error['code'] == 'note_f' for error in report['errors']), report
    else:
        assert any(error['code'].startswith('timing_assessment_') for error in report['errors']), report


def test_acoustic_filter_keeps_silent_windows_muted_events_and_omitted_high_frets():
    source = SimpleNamespace(id='high', instrument='guitar', tuning=[40], capo=0)
    # This part is empty only in the playable chart; its omitted source event
    # still participates in synchronization through sync_notes.
    wanted = {'parts': [{'source': source, 'notes': [], 'sync_notes': [
        {'note': {'t': 20, 'sus': .4, 's': 0, 'f': 30}}]}]}
    high = ls.tracks_from_expected(wanted)[0]
    tracks = [
        {'id': 'empty', 'instrument': 'guitar', 'events': []},
        {'id': 'later', 'instrument': 'guitar', 'events': [
            {'t': 24, 'end': 24.4, 'midi': 45, 'effects': {}}]},
        {'id': 'muted', 'instrument': 'guitar', 'events': [
            {'t': .5, 'end': .6, 'midi': None, 'effects': {'mt': True}}]}, high]
    before = deepcopy(tracks)
    selected = ls.nonempty_acoustic_tracks(tracks)
    assert [track['id'] for track in selected] == ['later', 'muted', 'high']
    assert selected[2]['events'][0]['midi'] == 70
    assert tracks == before
    # An active mute makes this first window real, although every track lacks
    # enough evidence. Both the later and high-fret part must still have rows.
    length = int(32 / rs.DT) + 1
    report = rs.assess_features(selected, np.zeros((length, 69)), np.zeros((length, 4)), 32)
    first = report['windows'][0]
    assert [row['trackId'] for row in first['tracks']] == ['later', 'muted', 'high']
    assert first['tracks'][0]['pitchedAttacks'] == 0
    assert first['status'] == 'inconclusive'


def test_missing_event_list_is_not_treated_as_an_empty_track():
    with pytest.raises(KeyError):
        ls.nonempty_acoustic_tracks([{'id': 'malformed', 'instrument': 'guitar'}])

"""Artwork and acoustic assessment share no mutable inputs or worker lifetime."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
import threading

import pytest

from feedback_converter.song_import import artwork, worker
from feedback_converter.song_import.audio import ImportFailure
from test_song_import_artwork import Network, recording, release, uid


@pytest.fixture
def import_job(tmp_path, monkeypatch):
    score = tmp_path / 'score.json'
    score.write_text('{}')
    request = {
        'scorePath': str(score), 'workDir': str(tmp_path / 'work'),
        'outputDir': str(tmp_path / 'output'), 'artworkCacheDir': str(tmp_path / 'cache'),
        'metadata': {'artist': 'Example Artist', 'title': 'Example Song', 'nested': {'value': 'original'}},
        'hybridLead': {'enabled': False},
    }
    performance = {'artist': 'Copy', 'title': 'Copy', 'tracks': [], 'warnings': ['Source warning.']}
    audio = {'path': 'prepared.ogg', 'duration': 123, 'hash': 'audio-hash',
             'source': {'kind': 'youtube', 'title': 'Example Artist - Example Song (Official Audio)'}}
    alignment = {'method': 'songsterr-video-points-v1', 'mapping': 'piecewise-linear',
                 'provenance': {'mapHash': 'map-hash'}, 'preparation': {'seconds': 1}}
    report = {'version': 1, 'status': 'supported', 'everyNoteVerified': False, 'windowCount': 1,
              'supportedWindows': 1, 'suspectedMismatchWindows': 0, 'suspectedMismatchRanges': [],
              'unverifiedWindows': []}
    built = []
    monkeypatch.setattr('feedback_converter.song_import.score.load_performance', lambda *a, **k: performance)
    monkeypatch.setattr('feedback_converter.song_import.runtime.resolve_tools', lambda *a: {})
    monkeypatch.setattr(worker, 'prepare_audio', lambda *a, **k: audio)
    monkeypatch.setattr(worker, '_choose_alignment', lambda *a, **k: alignment)
    monkeypatch.setattr('feedback_converter.song_import.preparation.finalize', lambda *a: (audio, alignment))
    monkeypatch.setattr('feedback_converter.song_import.ending_cutoff.mapped_tracks', lambda *a: [])
    monkeypatch.setattr('feedback_converter.song_import.local_sync.assess', lambda *a: deepcopy(report))
    monkeypatch.setattr('feedback_converter.song_import.verification.verify_import',
                        lambda *a, **k: {'version': 1, 'status': 'passed', 'counts': {}})
    monkeypatch.setattr(worker, 'save_evidence', lambda *a, **k: {'outputHash': 'output-hash'})

    def build(performance, audio, alignment, job, **kwargs):
        cover = kwargs['artwork']
        built.append({'performance': deepcopy(performance), 'audio': deepcopy(audio),
                      'alignment': deepcopy(alignment), 'recipe': deepcopy(kwargs['recipe']),
                      'artwork': {key: value for key, value in cover.items() if key != 'path'},
                      'cover': Path(cover['path']).read_bytes() if 'path' in cover else None})
        return {'stagingPath': 'staged.feedpak', 'warnings': ['Builder warning.']}

    monkeypatch.setattr(worker, 'build_feedpak', build)
    return request, performance, audio, alignment, report, built


def test_lookup_overlaps_assessment_after_finalization_retry_with_detached_inputs(import_job, monkeypatch):
    request, performance, audio, alignment, report, built = import_job
    lookup_started, assessment_entered, lookup_done = (threading.Event() for _ in range(3))
    finalized = []
    captured = []
    main_thread = threading.get_ident()

    def finalize(*args):
        assert not lookup_started.is_set()
        finalized.append(True)
        if len(finalized) == 1:
            raise ImportFailure('ending_padding_unconfirmed', 'Try without padding.')
        audio['duration'] = 125
        return audio, alignment

    def lookup(metadata, directory, **kwargs):
        assert threading.get_ident() != main_thread
        assert len(finalized) == 2
        lookup_started.set()
        assert assessment_entered.wait(5), 'lookup did not overlap assessment'
        captured.append(deepcopy(metadata))
        lookup_done.set()
        return {'status': 'unavailable', 'message': 'Same warning.'}

    def assess(*args):
        assert lookup_started.wait(5), 'artwork did not start before assessment completed'
        # Even nested request data is detached; only the main thread touches
        # live conversion state while the resolver consumes its snapshot.
        request['metadata']['nested']['value'] = 'changed'
        performance['title'] = 'Changed after capture'
        audio['source']['title'] = 'Changed after capture'
        assessment_entered.set()
        assert lookup_done.wait(5)
        return deepcopy(report)

    monkeypatch.setattr('feedback_converter.song_import.preparation.finalize', finalize)
    monkeypatch.setattr(artwork, 'resolve_album_art', lookup)
    monkeypatch.setattr('feedback_converter.song_import.local_sync.assess', assess)
    stages = []
    result = worker.run_import(request, stages.append)
    assert result['ok'], result
    assert captured == [{**request['metadata'], 'nested': {'value': 'original'},
                         'title': 'Example Song', 'album': None, 'duration': 125,
                         'audioKind': 'youtube', 'audioTitle': 'Example Artist - Example Song (Official Audio)'}]
    assert built[0]['artwork'] == result['artwork']
    assert [event['message'] for event in stages][-4:] == [
        'Checking timing across the recording.', 'Finding the album cover.',
        'Building and validating the FeedPak.', 'Comparing the completed FeedPak with the original tab.']


@pytest.mark.parametrize('failure', [ImportFailure('cancelled', 'Cancelled.'),
                                    ValueError('Timing failed.'), KeyboardInterrupt()])
def test_failure_and_cancellation_join_artwork_before_return(import_job, monkeypatch, failure):
    request, _, _, _, _, built = import_job
    started, release_lookup, joined = (threading.Event() for _ in range(3))
    finished = threading.Event()
    results = []
    writes = []

    class ObservedExecutor(ThreadPoolExecutor):
        def shutdown(self, **kwargs):
            joined.set()
            return super().shutdown(**kwargs)

    def lookup(metadata, directory, **kwargs):
        started.set()
        assert release_lookup.wait(5)
        directory.mkdir()
        target = directory / 'cover.jpg'
        target.write_bytes(b'completed-before-return')
        writes.append(target)
        return {'status': 'matched', 'path': str(target)}

    def assess(*args):
        assert started.wait(5)
        raise failure

    def run():
        try:
            results.append(worker.run_import(request))
        except BaseException as exc:
            results.append(exc)
        finally:
            finished.set()

    monkeypatch.setattr(worker, 'ThreadPoolExecutor', ObservedExecutor)
    monkeypatch.setattr(artwork, 'resolve_album_art', lookup)
    monkeypatch.setattr('feedback_converter.song_import.local_sync.assess', assess)
    runner = threading.Thread(target=run)
    runner.start()
    try:
        assert joined.wait(5), 'failure did not close its artwork executor'
        assert not finished.is_set(), 'returned while artwork could still write'
        assert not built
    finally:
        release_lookup.set()
        runner.join(5)
    assert not runner.is_alive()
    assert writes and writes[0].read_bytes() == b'completed-before-return'
    if isinstance(failure, KeyboardInterrupt):
        assert results == [failure]
    else:
        assert not results[0]['ok']
        assert results[0]['code'] == ('cancelled' if isinstance(failure, ImportFailure) else 'unsupported_score')
    assert not any(t.name.startswith('song-import-artwork') for t in threading.enumerate())


@pytest.mark.parametrize('failure', [ValueError('Lookup failed.'), RuntimeError('Unexpected worker failure.')])
def test_worker_exception_is_observed_without_building_or_leaking_thread(import_job, monkeypatch, failure):
    request, _, _, _, _, built = import_job

    def lookup(*args, **kwargs):
        raise failure

    monkeypatch.setattr(artwork, 'resolve_album_art', lookup)
    if isinstance(failure, ValueError):
        result = worker.run_import(request)
        assert result['code'] == 'unsupported_score' and result['error'] == str(failure)
    else:
        with pytest.raises(RuntimeError, match='Unexpected worker failure'):
            worker.run_import(request)
    assert not built
    assert not any(t.name.startswith('song-import-artwork') for t in threading.enumerate())


def test_disabled_lookup_starts_no_executor(import_job, monkeypatch):
    request, _, _, _, _, built = import_job
    request['artworkLookup'] = False
    monkeypatch.setattr(worker, 'ThreadPoolExecutor', lambda **k: pytest.fail('artwork disabled'))
    result = worker.run_import(request)
    assert result['ok']
    assert built[0]['artwork'] == {'status': 'unavailable', 'message': 'Album artwork was not looked up.'}


@pytest.mark.parametrize('stage', ['construct', 'submit'])
@pytest.mark.parametrize('error', [RuntimeError("can't start new thread"), OSError('No thread resources')])
def test_scheduling_failure_uses_original_synchronous_lookup(import_job, monkeypatch, stage, error):
    request, _, _, _, report, built = import_job
    events = []
    main_thread = threading.get_ident()

    class UnavailableExecutor:
        def __init__(self, **kwargs):
            if stage == 'construct':
                raise error

        def submit(self, *args, **kwargs):
            events.append('queued')
            raise error

        def shutdown(self, *, wait, cancel_futures):
            assert wait and cancel_futures
            events.append('drained')

    def assess(*args):
        events.append('assessed')
        return deepcopy(report)

    def lookup(*args, **kwargs):
        assert threading.get_ident() == main_thread
        events.append('looked-up')
        return {'status': 'unavailable', 'message': 'Same warning.'}

    monkeypatch.setattr(worker, 'ThreadPoolExecutor', UnavailableExecutor)
    monkeypatch.setattr('feedback_converter.song_import.local_sync.assess', assess)
    monkeypatch.setattr(artwork, 'resolve_album_art', lookup)
    result = worker.run_import(request)
    assert result['ok'] and built
    assert events == (['queued', 'drained'] if stage == 'submit' else []) + ['assessed', 'looked-up']


class DeferredExecutor:
    """Sequential control: run the resolver at its original, post-assess join."""
    def __init__(self, **kwargs):
        assert kwargs['max_workers'] == 1

    def submit(self, function, *args, **kwargs):
        class DeferredResult:
            def result(self):
                return function(*args, **kwargs)
        return DeferredResult()

    def shutdown(self, **kwargs):
        pass


@pytest.mark.parametrize('kind,method,expected', [
    ('youtube', 'songsterr-video-points-v1', 'newvideo123'),
    ('local', 'songsterr-video-points-v1', None),
    ('youtube', 'affine-chroma-v2', None),
])
def test_artwork_video_identity_comes_from_final_selected_source(
        import_job, monkeypatch, kind, method, expected):
    request, _, audio, alignment, _, _ = import_job
    request['metadata']['audioVideoId'] = 'staleinput1'
    audio['source']['videoId'] = 'oldvideo123'
    captured = []
    def finalize(*args):
        audio['source'].update(kind=kind, videoId='newvideo123')
        alignment['method'] = method
        return audio, alignment
    def lookup(metadata, *args, **kwargs):
        captured.append(metadata)
        return {'status': 'unavailable', 'message': 'No artwork.'}
    monkeypatch.setattr('feedback_converter.song_import.preparation.finalize', finalize)
    monkeypatch.setattr(artwork, 'resolve_album_art', lookup)
    monkeypatch.setattr(worker, 'ThreadPoolExecutor', DeferredExecutor)
    assert worker.run_import(request)['ok']
    assert captured[0].get('audioVideoId') == expected


@pytest.mark.parametrize('mode', ['miss', 'hit', 'ambiguous', 'timeout', 'transport-error'])
@pytest.mark.parametrize('source_timing', [True, False])
def test_real_resolver_matches_sequential_bytes_metadata_warnings_and_candidate_order(
        import_job, monkeypatch, tmp_path, mode, source_timing):
    request, _, _, alignment, _, built = import_job
    if not source_timing:
        alignment.update(method='affine-chroma-v2', mapping='affine')
    resolve = artwork.resolve_album_art
    snapshots, results, calls = [], [], []
    for name, executor in [('sequential', DeferredExecutor), ('parallel', ThreadPoolExecutor)]:
        network = Network()
        if mode == 'ambiguous':
            network.records.append(recording(20))
            network.releases[uid(20)] = [release(22, 23, 'Different Album')]
        elif mode in ('timeout', 'transport-error'):
            def fail(url):
                raise TimeoutError() if mode == 'timeout' else ValueError('transport failed')
            network.override = fail
        cache = tmp_path / name / 'cache'
        request['artworkCacheDir'] = str(cache)

        def lookup(metadata, directory, **kwargs):
            snapshots.append(deepcopy(metadata))
            return resolve(metadata, directory, **kwargs, transport=network,
                           clock=network.clock, sleep=network.clock.sleep, now=network.clock)

        if mode == 'hit':
            resolve({**request['metadata'], 'album': None,
                     'audioTitle': 'Example Artist - Example Song (Official Audio)' if source_timing else ''},
                    tmp_path / name / 'seed', cache, transport=network,
                    clock=network.clock, sleep=network.clock.sleep, now=network.clock)
            network.calls.clear()
        monkeypatch.setattr(artwork, 'resolve_album_art', lookup)
        monkeypatch.setattr(worker, 'ThreadPoolExecutor', executor)
        results.append(worker.run_import(request))
        calls.append(network.calls)
    assert all(result['ok'] for result in results), results
    assert snapshots[0] == snapshots[1]
    assert built[0] == built[1]
    assert results[0] == results[1]
    assert calls[0] == calls[1]
    if mode in ('hit', 'miss'):
        assert built[0]['cover'] is not None
    if mode == 'hit':
        assert calls == [[], []]


def test_thread_rate_lock_wait_uses_remaining_lookup_budget(monkeypatch, tmp_path):
    network = Network()
    acquired = []

    class BusyLock:
        def acquire(self, *, timeout):
            acquired.append(timeout)
            return False

        def release(self):
            pytest.fail('must not release another worker\'s lock')

    monkeypatch.setattr(artwork, '_RATE_LOCK', BusyLock())
    result = network.resolve(tmp_path)
    assert result['status'] == 'ambiguous'
    assert result['reason'] == 'lookup_budget_exceeded'
    assert acquired and all(0 < wait <= artwork.MAX_SECONDS for wait in acquired)
    assert not network.calls

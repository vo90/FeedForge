import http.client
import socket
import subprocess
import sys
import types
import urllib.error

import pytest

from feedback_converter.song_import import audio
from feedback_converter.song_import.transport import classify, retry_after


@pytest.mark.parametrize('error,reason', [
    (TimeoutError(), 'timeout'), (ConnectionResetError(), 'connection_reset'),
    (socket.gaierror(socket.EAI_AGAIN, 'temporary'), 'temporary_dns'),
    (http.client.IncompleteRead(b'', 10), 'interrupted_transfer'),
    ('ERROR: unable to download video data: HTTP Error 403: Forbidden', 'media_url_expired'),
    ('ERROR: HTTP Error 503: Service Unavailable', 'http'),
    ('ERROR: [download] Read timed out', 'timeout')])
def test_transient_download_facts(error, reason):
    detail = classify(error, service='youtube', downloader=True)
    assert detail['reason'] == reason
    assert detail['version'] == 1
    assert set(detail) <= {'version', 'phase', 'operation', 'service', 'reason', 'status', 'retryAfterAt'}


@pytest.mark.parametrize('error', [
    'HTTP Error 403: Forbidden', 'Video unavailable', 'This video is private', 'Sign in to confirm your age',
    'Sign in to confirm you are not a bot: HTTP Error 429', 'Permission denied: HTTP Error 503',
    'Postprocessing: timed out', 'unrecognized error', FileNotFoundError(), PermissionError(13, 'denied'),
    OSError(28, 'No space left'), socket.gaierror(socket.EAI_NONAME, 'no name')])
def test_non_transient_or_unknown_does_not_loop(error):
    assert classify(error, service='youtube', downloader=True) is None


def test_http_retry_after_and_no_private_host_retry():
    value = classify(urllib.error.HTTPError('https://host/secret', 429, 'limited', {'Retry-After': '30'}, None))
    assert value['retryAfterAt'] > 0
    assert 'secret' not in str(value)
    assert classify(urllib.error.HTTPError('https://host/', 403, 'denied', {}, None)) is None
    assert retry_after('bad') is None


def test_module_ytdlp_failure_and_nested_retry_limits(tmp_path, monkeypatch):
    options = {}
    class Downloader:
        def __init__(self, settings): options.update(settings)
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def extract_info(self, url, download):
            raise Exception('ERROR: unable to download video data: HTTP Error 403: Forbidden')
    monkeypatch.setattr(audio.importlib.util, 'find_spec', lambda name: True)
    monkeypatch.setitem(sys.modules, 'yt_dlp', types.SimpleNamespace(YoutubeDL=Downloader))
    monkeypatch.setattr(audio, '_tool', lambda *args: 'ffmpeg')
    with pytest.raises(audio.ImportFailure) as failure:
        audio._download_youtube('https://youtube.com/watch?v=abcdefghijk', tmp_path, {}, managed_retries=True)
    assert failure.value.transport['reason'] == 'media_url_expired'
    assert not failure.value.diagnostics
    for key in ['retries', 'fragment_retries', 'extractor_retries', 'file_access_retries']:
        assert options[key] == 0
    assert options['skip_unavailable_fragments'] is False


def test_executable_ytdlp_same_contract_and_no_nested_retries(tmp_path, monkeypatch):
    commands = []
    monkeypatch.setattr(audio, '_tool', lambda *args: 'ffmpeg')
    def fail(command, **kwargs):
        commands.append(command)
        raise subprocess.CalledProcessError(1, command, stderr=b'ERROR: unable to download video data: HTTP Error 403: Forbidden')
    monkeypatch.setattr(audio.subprocess, 'run', fail)
    with pytest.raises(audio.ImportFailure) as failure:
        audio._download_youtube('https://youtube.com/watch?v=abcdefghijk', tmp_path, {'ytDlp': 'tool'}, managed_retries=True)
    assert failure.value.transport['reason'] == 'media_url_expired'
    for flag in ['--retries', '--fragment-retries', '--extractor-retries', '--file-access-retries']:
        assert commands[0][commands[0].index(flag) + 1] == '0'


def test_global_subprocess_timeout_is_not_network_evidence(monkeypatch):
    monkeypatch.setattr(audio.subprocess, 'run', lambda *args, **kwargs: (_ for _ in ()).throw(subprocess.TimeoutExpired('tool', 240)))
    with pytest.raises(audio.ImportFailure) as failure:
        audio._run(['tool'], retrieval=True)
    assert failure.value.transport is None


def test_worker_keeps_transport_out_of_alignment_and_compatibility(tmp_path, monkeypatch):
    from feedback_converter.song_import import worker, score, runtime
    source = tmp_path / 'score.json'
    source.write_text('{}')
    monkeypatch.setattr(score, 'load_performance', lambda *args, **kwargs: {'tracks': [], 'title': 'Test'})
    monkeypatch.setattr(runtime, 'resolve_tools', lambda value: {})
    fact = classify('unable to download video data: HTTP Error 403: Forbidden', service='youtube', downloader=True)
    def fail(*args, **kwargs):
        assert kwargs['managed_retries'] is True
        raise audio.ImportFailure('needs_audio', 'Temporary download failure', transport=fact)
    monkeypatch.setattr(worker, 'prepare_audio', fail)
    result = worker.run_import({'scorePath': str(source), 'workDir': str(tmp_path / 'work'), 'outputDir': str(tmp_path / 'output'), 'managedRetries': True})
    assert result['transport'] == fact
    assert 'alignment' not in result
    assert result['compatibility']['findingCount'] == 0


def test_direct_transfer_truncation_and_local_write_errors_are_distinct(tmp_path, monkeypatch):
    import io
    monkeypatch.setattr(audio, '_public_url', lambda url: url)
    class Response(io.BytesIO):
        headers = {'Content-Length': '30', 'Content-Type': 'audio/ogg'}
    monkeypatch.setattr(audio.urllib.request, 'build_opener', lambda *a: types.SimpleNamespace(open=lambda *a, **k: Response(b'partial')))
    with pytest.raises(audio.ImportFailure) as failure:
        audio._download_direct('https://example.test/audio', tmp_path / 'audio')
    assert failure.value.transport['reason'] == 'interrupted_transfer'
    with pytest.raises(audio.ImportFailure) as failure:
        audio._download_direct('https://example.test/audio', tmp_path / 'missing' / 'audio')
    assert failure.value.transport is None

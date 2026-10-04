"""Worker routing tests: source timing and estimated timing are separate gates."""
import json
from pathlib import Path
import zipfile

import numpy as np
import pytest
import soundfile as sf
import yaml

from feedback_converter.feedpak_validator import validate_feedpak
from feedback_converter.song_import import worker
from feedback_converter.song_import import audio as audio_module
from feedback_converter.song_import.audio import ImportFailure


def test_source_timing_does_not_run_pitch_matching(monkeypatch):
    expected = {"status": "validated", "method": "songsterr-video-points-v1"}
    calls = []

    def source_map(performance, audio, synchronization, metadata, **kwargs):
        calls.append((performance, audio, synchronization, metadata))
        return expected

    monkeypatch.setattr(worker, "align_from_songsterr", source_map)
    monkeypatch.setattr(worker, "align_audio", lambda *args, **kwargs: pytest.fail("source map must not enter heuristic gate"))
    progress = []
    request = {"synchronization": {"videoId": "abcdefghijk"}, "metadata": {"revisionId": "123"}}
    assert worker._choose_alignment({"title": "Test"}, {"path": "audio.ogg"}, request, progress.append) is expected
    assert calls[0][2:] == (request["synchronization"], request["metadata"])
    assert progress and progress[0]["stage"] == "aligning"


def test_unusable_source_map_uses_existing_matcher_and_retains_reason(monkeypatch):
    def unavailable(*args, **kwargs):
        raise ImportFailure("source_sync_unavailable", "The recording changed.", {"sourceSyncReason": "video_mismatch"})

    monkeypatch.setattr(worker, "align_from_songsterr", unavailable)
    calls = []

    def estimate(performance, audio, progress=None):
        calls.append(audio)
        return {"method": "affine-chroma-v2", "status": "validated", "offset": 1.0, "scale": 1.0}

    monkeypatch.setattr(worker, "align_audio", estimate)
    result = worker._choose_alignment({}, {"path": "audio.ogg"}, {})
    assert calls == [Path("audio.ogg")]
    assert result["sourceSynchronization"]["sourceSyncReason"] == "video_mismatch"


def test_failed_fallback_preserves_both_diagnostics(monkeypatch):
    def unavailable(*args, **kwargs):
        raise ImportFailure("source_sync_unavailable", "Timing is incomplete.", {"sourceSyncReason": "incomplete_points"})

    def failed(*args, **kwargs):
        raise ImportFailure("alignment_failed", "Could not match audio.", {"validationScore": 0.6})

    monkeypatch.setattr(worker, "align_from_songsterr", unavailable)
    monkeypatch.setattr(worker, "align_audio", failed)
    with pytest.raises(ImportFailure) as caught:
        worker._choose_alignment({}, {"path": "audio.ogg"}, {})
    assert caught.value.code == "alignment_failed"
    assert caught.value.diagnostics["validationScore"] == 0.6
    assert caught.value.diagnostics["sourceSynchronization"]["sourceSyncReason"] == "incomplete_points"


def test_unexpected_source_failure_is_not_silently_downgraded(monkeypatch):
    def cancelled(*args, **kwargs):
        raise ImportFailure("cancelled", "Cancelled.")

    monkeypatch.setattr(worker, "align_from_songsterr", cancelled)
    monkeypatch.setattr(worker, "align_audio", lambda *args, **kwargs: pytest.fail("must stop"))
    with pytest.raises(ImportFailure) as caught:
        worker._choose_alignment({}, {"path": "audio.ogg"}, {})
    assert caught.value.code == "cancelled"


def test_job_history_keeps_map_identity_without_unbounded_anchor_lists():
    alignment = {"method": "songsterr-video-points-v1", "status": "validated",
                 "anchors": [{"score": i, "audio": i + 1} for i in range(1000)],
                 "tempos": [{"time": i, "bpm": 100 + i % 7} for i in range(1000)],
                 "provenance": {"mapHash": "a" * 64, "revisionId": "123", "videoId": "abcdefghijk"}}
    summary = worker._alignment_summary(alignment)
    assert "anchors" not in summary
    assert "tempos" not in summary
    assert summary["provenance"] == alignment["provenance"]
    assert len(alignment["anchors"]) == 1000


@pytest.mark.parametrize('precise_harmonic', [False, True])
@pytest.mark.parametrize('hybrid', [False, True])
def test_source_sync_builds_a_real_feedpak_with_matching_timeline_and_settings(tmp_path, monkeypatch, precise_harmonic, hybrid):
    """Only inbound media is faked; parser, timing, audio encoding and builder run."""
    video_id = "abcdefghijk"
    measures = [{"signature": [4, 4], "voices": [{"beats": [
        {"duration": [1, 4], "notes": [{"string": 0, "fret": fret}]} for fret in (0, 3, 5, 7)
    ]}]} for _ in range(2)]
    raw = {"format": "songsterr", "songId": 12, "revisionId": 34, "title": "Copy", "artist": "Copy artist",
           "tracks": [{"id": 0, "name": "Lead Guitar", "instrumentId": 29, "tuning": [64, 59, 55, 50, 45, 40]}],
           "parts": [{"measures": measures, "automations": {"tempo": [{"measure": 0, "position": [0, 1], "bpm": 120, "type": 4}]}}]}
    measures[0]["voices"][0]["beats"][0]["notes"][0]["slide"] = "belowdownwards"
    if precise_harmonic:
        measures[0]["voices"][0]["beats"][1]["notes"][0].update(harmonic='natural', harmonicFret=3.2)
    score = tmp_path / "score.json"
    score.write_text(json.dumps(raw), encoding="utf-8")
    recording = tmp_path / "selected-recording.wav"
    rate = 22050
    samples = np.zeros(6 * rate)
    expected_starts = [1 + index * 0.525 for index in range(4)] + [3.1 + index * 0.675 for index in range(4)]
    for start in expected_starts:
        wave = 0.2 * np.sin(2 * np.pi * 440 * np.arange(rate // 5) / rate)
        begin = round(start * rate)
        samples[begin:begin + len(wave)] += wave
    sf.write(recording, samples, rate)
    monkeypatch.setattr(audio_module, "_public_url", lambda url: url)
    monkeypatch.setattr(audio_module, "_download_youtube", lambda url, directory, tools, **kwargs:
                        (recording, {"kind": "youtube", "videoId": video_id, "url": url, "title": "Test artist - Test song (Official Audio)"}))
    artwork_metadata = []
    def album_lookup(metadata, *args, **kwargs):
        artwork_metadata.append(metadata)
        return {'status': 'unavailable', 'albumStatus': 'matched', 'album': 'Confirmed album', 'year': 2001}
    monkeypatch.setattr('feedback_converter.song_import.artwork.resolve_album_art', album_lookup)
    monkeypatch.setattr(worker, "align_audio", lambda *args, **kwargs: pytest.fail("must use supplied source timing"))
    synchronization = {"version": 1, "source": "songsterr-video-points", "songId": "12", "revisionId": "34",
                       "videoId": video_id, "status": "done", "feature": None, "points": [1, 3.1, 5.8]}
    request = {"scorePath": str(score), "audio": {"kind": "url", "url": f"https://www.youtube.com/watch?v={video_id}"}, "artworkLookup": True,
               "metadata": {"songId": "12", "revisionId": "34", "approval": "approved", "title": "Test song", "artist": "Test artist"},
               "synchronization": synchronization, "workDir": str(tmp_path / "work"), "outputDir": str(tmp_path / "library"),
               "outputSettings": {"nameTemplate": "{artist} - {title}", "outputLayout": "artist", "generateDifficulty": precise_harmonic}}
    request['hybridLead'] = {'enabled': hybrid}
    result = worker.run_import(request)
    assert result["ok"], result
    assert artwork_metadata[0]['audioTitle'] == 'Test artist - Test song (Official Audio)'
    assert result['artwork']['albumStatus'] == result['recipe']['artwork']['albumStatus'] == 'matched'
    if hybrid:
        assert result['verification']['hybridLead']['policy'] == 'hybrid-lead-v3'
        assert result['verification']['hybridLead']['primaryCoverage'] == 'checked'
        assert result['verification']['hybridLead']['status'] == 'no_additions'
    assert result["recipe"]["preservationContract"] == result["verification"]["version"] == result["evidence"]["version"] == 73
    extensions = ["slide_in_marks", "slide_out", "slide_out_marks"]
    if precise_harmonic:
        extensions = ['hn', 'hps', *extensions]
    assert result["recipe"]["compatibility"] == {"version": 6, "extensions": extensions, "status": "requires_consumer_support"}
    assert result["alignment"]["method"] == "songsterr-video-points-v1"
    assert len(result["recipe"]["alignment"]["provenance"]["mapHash"]) == 64
    assert "anchors" not in result["alignment"] and "tempos" not in result["alignment"]
    assert result["relativePath"] == "Test artist/Test artist - Test song.feedpak"
    assert not (tmp_path / "library").exists(), "publication belongs to Electron"
    assert validate_feedpak(Path(result["stagingPath"])).ok
    with zipfile.ZipFile(result["stagingPath"]) as archive:
        manifest = yaml.safe_load(archive.read("manifest.yaml"))
        assert archive.read(manifest['song_import']['sourceFile']) == score.read_bytes()
        assert json.loads(archive.read(manifest['song_import']['compatibilityFile']))['status'] == 'compatible'
        chart = json.loads(archive.read(manifest["arrangements"][0]["file"]))
        if precise_harmonic:
            assert {k: chart['notes'][1][k] for k in ('f', 'hn', 'hps')} == {'f':3, 'hn':3.2, 'hps':31}
            retained = [n for phrase in chart['phrases'] for level in phrase['levels'] for n in level['notes'] if n.get('hn')]
            assert retained and all((n['f'], n['hn'], n['hps']) == (3, 3.2, 31) for n in retained)
            assert manifest['song_import']['compatibility'] == result['recipe']['compatibility']
        assert [note["t"] for note in chart["notes"]] == pytest.approx([t+1 for t in expected_starts])
        assert [note["sus"] for note in chart["notes"]] == pytest.approx([0.525] * 4 + [0.675] * 4)
        assert chart["notes"][0]["slide_out_marks"] == [{"direction": "down", "start": 0, "end": .525}]
        assert chart["notes"][0]["slide_in_marks"] == [{"direction": "up", "time": 0}]
        assert [beat["time"] for beat in chart["beats"]] == pytest.approx([t+1 for t in expected_starts])
        assert len(chart["tempos"]) == 2
        assert manifest["preview"] in archive.namelist()
        assert manifest["duration"] == pytest.approx(7)
        assert manifest['song_import']['preparation']['seconds'] == 1
    changed = {**request, "synchronization": {**synchronization, "points": [1, 3.2, 5.8]}}
    retried = worker.run_import(changed)
    assert retried["ok"], retried
    assert retried["recipe"]["alignment"]["provenance"]["mapHash"] != result["recipe"]["alignment"]["provenance"]["mapHash"]


@pytest.mark.parametrize('supplied_points', [[0, 2], [0, 2, 4, 6, 8, 10]])
def test_completed_package_verifies_several_silent_trailing_bars(tmp_path, monkeypatch, supplied_points):
    video_id = 'abcdefghijk'
    raw = {'format': 'songsterr', 'songId': 12, 'revisionId': 34, 'title': 'Tail', 'artist': 'Artist',
        'tracks': [{'id': 0, 'name': 'Lead', 'instrumentId': 29, 'tuning': [64, 59, 55, 50, 45, 40]}],
        'parts': [{'automations': {'tempo': [{'measure': 0, 'position': 0, 'bpm': 120}]}, 'measures': [
            {'signature': [4, 4], 'voices': [{'beats': [{'type': 1, 'duration': [1, 1], 'notes': [{'string': 5, 'fret': 3}]}]}]},
            *[{'signature': [4, 4], 'voices': [{'beats': [{'type': 1, 'duration': [1, 1], 'rest': True, 'notes': [{'rest': True}]}]}]} for _ in range(4)]
        ]}]}
    score = tmp_path / 'score.json'
    score.write_text(json.dumps(raw), encoding='utf-8')
    recording = tmp_path / 'recording.wav'
    samples = np.zeros(22050 * 3)
    samples[:22050 * 2] = .2 * np.sin(2 * np.pi * 440 * np.arange(22050 * 2) / 22050)
    sf.write(recording, samples, 22050)
    monkeypatch.setattr(audio_module, '_public_url', lambda url: url)
    monkeypatch.setattr(audio_module, '_download_youtube', lambda url, directory, tools, **kwargs:
        (recording, {'kind': 'youtube', 'videoId': video_id, 'url': url}))
    monkeypatch.setattr(worker, 'align_audio', lambda *args, **kwargs: pytest.fail('must use the source map'))
    result = worker.run_import({'scorePath': str(score), 'audio': {'kind': 'url', 'url': f'https://www.youtube.com/watch?v={video_id}'},
        'metadata': {'songId': '12', 'revisionId': '34', 'approval': 'approved'}, 'artworkLookup': False,
        'synchronization': {'version': 1, 'source': 'songsterr-video-points', 'songId': '12', 'revisionId': '34',
            'videoId': video_id, 'status': 'done', 'feature': None, 'points': supplied_points},
        'workDir': str(tmp_path / 'work'), 'outputDir': str(tmp_path / 'library')})
    assert result['ok'], result
    assert result['verification']['status'] == 'passed'
    with zipfile.ZipFile(result['stagingPath']) as archive:
        manifest = yaml.safe_load(archive.read('manifest.yaml'))
        chart = json.loads(archive.read(manifest['arrangements'][0]['file']))
        notation = json.loads(archive.read(manifest['arrangements'][0]['notation']))
        assert manifest['duration'] == pytest.approx(5)
        assert len(chart['notes']) == 1
        assert chart['notes'][0]['t'] == 2 and chart['notes'][0]['sus'] == 2
        assert len(notation['measures']) == 5
        assert notation['measures'][-1]['t'] == 10 and notation['measures'][-1]['duration_seconds'] == 2
        assert all(b['time'] <= 5 for b in chart['beats'])

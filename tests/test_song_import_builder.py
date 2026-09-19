import json
from pathlib import Path
import sys
import subprocess
import types
import zipfile

import numpy as np
import pytest
import soundfile as sf
import yaml

from feedback_converter.feedpak_validator import validate_feedpak
from feedback_converter.song_import.audio import ImportFailure, prepare_audio
from feedback_converter.song_import.builder import build_feedpak
from feedback_converter.song_import.worker import run_import


def inputs(tmp_path):
    audio_path = tmp_path / "input.wav"
    rate = 22050
    samples = 0.2 * np.sin(2 * np.pi * 440 * np.arange(rate * 8) / rate)
    sf.write(audio_path, samples, rate)
    job = tmp_path / "job"
    job.mkdir()
    audio = prepare_audio({"kind": "file", "path": str(audio_path)}, job)
    performance = {"title": "Woodland Rites", "artist": "Green Lung", "duration": 4,
                   "tracks": [{"id": "guitar", "name": "Lead", "instrument": "guitar", "role": "lead",
                               "tuning": [38, 43, 48, 53, 57, 62], "capo": 1,
                               "notes": [{"t": 1, "s": 0, "f": 3, "sus": 1,
                                          "bnv": [{"t": 0.5, "v": 1}]}]}],
                   "beats": [{"time": 1, "measure": 1}], "tempos": [{"time": 0, "bpm": 120}],
                   "sections": [{"time": 0, "name": "Verse"}]}
    alignment = {"status": "validated", "offset": 0.75, "scale": 1.1}
    return performance, audio, alignment, job


def test_builder_preserves_settings_metadata_pitch_and_timed_techniques(tmp_path):
    performance, audio, alignment, job = inputs(tmp_path)
    output = tmp_path / "library"
    result = build_feedpak(performance, audio, alignment, job, output_dir=output,
                           output_settings={"nameTemplate": "{artist} - {year} - {title}", "outputLayout": "artist"})
    assert result["relativePath"] == "Green Lung/Green Lung - Woodland Rites.feedpak"
    assert not output.exists(), "worker must not publish into the real song library"
    assert validate_feedpak(Path(result["stagingPath"])).ok
    with zipfile.ZipFile(result["stagingPath"]) as archive:
        manifest = yaml.safe_load(archive.read("manifest.yaml"))
        chart = json.loads(archive.read(manifest["arrangements"][0]["file"]))
        assert manifest["artist"] == "Green Lung"
        assert "year" not in manifest and "album" not in manifest
        assert manifest["preview"] in archive.namelist()
        assert "cover" not in manifest, "a placeholder must not block downstream artwork lookup"
        assert manifest["stems"][0]["id"] == "full"
        assert chart["tuning"] == [-2] * 6
        assert chart["capo"] == 1
        assert chart["notes"][0]["t"] == pytest.approx(1.85)
        assert chart["notes"][0]["sus"] == pytest.approx(1.1)
        assert chart["notes"][0]["bnv"][0]["t"] == pytest.approx(0.55)
        assert chart["beats"][0]["time"] == pytest.approx(1.85)
        assert chart["beats"][0]["measure"] == 1
        assert chart["tempos"][0]["bpm"] == pytest.approx(120 / 1.1)


def test_unvalidated_sync_cannot_build(tmp_path):
    performance, audio, _, job = inputs(tmp_path)
    with pytest.raises(ImportFailure, match="synchronization"):
        build_feedpak(performance, audio, {}, job, output_dir=tmp_path / "out")


def test_builder_difficulty_is_optional_and_keeps_full_chart_and_notation(tmp_path):
    performance, audio, alignment, job = inputs(tmp_path)
    second = tmp_path / "generated-job"
    second.mkdir()
    faithful = build_feedpak(performance, audio, alignment, job, output_dir=tmp_path / "out")
    generated = build_feedpak(performance, audio, alignment, second, output_dir=tmp_path / "out",
                              output_settings={"generateDifficulty": True})
    with zipfile.ZipFile(faithful["stagingPath"]) as a, zipfile.ZipFile(generated["stagingPath"]) as b:
        original = json.loads(a.read("arrangements/guitar.json"))
        practice = json.loads(b.read("arrangements/guitar.json"))
        assert "phrases" not in original
        assert practice["notes"] == original["notes"]
        assert practice["beats"] == original["beats"]
        assert practice["ext"]["generatedDifficulty"]["sourceAuthored"] is False
        assert a.read("song_timeline.json") == b.read("song_timeline.json")


def test_builder_rejects_invalid_zero_based_downbeats(tmp_path):
    from feedback_converter.feedpak_validator import FeedpakValidationError

    performance, audio, alignment, job = inputs(tmp_path)
    performance["beats"][0]["measure"] = 0
    with pytest.raises(FeedpakValidationError, match="1-based downbeat"):
        build_feedpak(performance, audio, alignment, job, output_dir=tmp_path / "out")
    assert not (job / "result.feedpak").exists()


@pytest.mark.parametrize("extension", ["png", "jpg"])
def test_builder_embeds_album_cover_without_overwriting_source_album(tmp_path, extension):
    from PIL import Image
    performance, audio, alignment, job = inputs(tmp_path)
    cover = tmp_path / f"album.{extension}"
    Image.new("RGB", (300, 200), "red").save(cover)
    performance["album"] = "Source album"
    artwork = {"status": "matched", "path": str(cover), "album": "Source album", "year": 2019}
    result = build_feedpak(performance, audio, alignment, job, output_dir=tmp_path / "out", artwork=artwork)
    with zipfile.ZipFile(result["stagingPath"]) as archive:
        manifest = yaml.safe_load(archive.read("manifest.yaml"))
        assert manifest["album"] == "Source album" and manifest["year"] == 2019
        assert manifest["cover"] == f"cover.{extension}"
        assert archive.read(manifest["cover"]) == cover.read_bytes()


def test_notation_uses_recording_time_but_keeps_written_values_and_lineage(tmp_path):
    performance, audio, alignment, job = inputs(tmp_path)
    performance["tracks"][0]["notes"][0]["source_ids"] = ["authored-note"]
    performance["tracks"][0]["notation"] = {"version": 1, "staves": [{"id": "s", "clef": "G2"}],
        "measures": [{"idx": 1, "t": 0, "end_time": 2, "tempo": 120, "ts": [4, 4], "source_measure": 1,
                      "staves": {"s": {"voices": [{"v": 0, "beats": [{"t": 1, "end_time": 1.5, "dur": 8, "rest": True, "source_id": "beat:1"}]}]}}}]}
    result = build_feedpak(performance, audio, alignment, job, output_dir=tmp_path / "out")
    with zipfile.ZipFile(result["stagingPath"]) as archive:
        manifest = yaml.safe_load(archive.read("manifest.yaml"))
        entry = manifest["arrangements"][0]
        assert "source_ids" not in json.loads(archive.read(entry["file"]))["notes"][0]
        measure = json.loads(archive.read(entry["notation"]))["measures"][0]
        assert measure["written_tempo"] == 120
        assert measure["tempo"] == pytest.approx(120 / 1.1)
        beat = measure["staves"]["s"]["voices"][0]["beats"][0]
        assert beat["dur"] == 8 and beat["rest"] and beat["source_id"] == "beat:1"
        assert beat["t"] == 1.85 and beat["duration_seconds"] == 0.55


def test_archive_metadata_is_deterministic_across_staging_directories(tmp_path):
    performance, audio, alignment, job = inputs(tmp_path)
    second = tmp_path / "different-job"
    second.mkdir()
    a = build_feedpak(performance, audio, alignment, job, output_dir=tmp_path / "out")
    b = build_feedpak(performance, audio, alignment, second, output_dir=tmp_path / "out")
    assert Path(a["stagingPath"]).read_bytes() == Path(b["stagingPath"]).read_bytes()


def test_notes_outside_recording_cannot_build(tmp_path):
    performance, audio, alignment, job = inputs(tmp_path)
    performance["tracks"][0]["notes"][0]["sus"] = 100
    with pytest.raises(ImportFailure, match="outside"):
        build_feedpak(performance, audio, alignment, job, output_dir=tmp_path / "out")


def test_worker_returns_actionable_missing_audio_without_publishing(tmp_path, monkeypatch):
    score = tmp_path / "score.gp"
    score.write_bytes(b"fixture")
    parser = types.ModuleType("feedback_converter.song_import.score")
    parser.load_performance = lambda path, metadata=None: {"title": "Copy", "artist": "Misc Covers"}
    monkeypatch.setitem(sys.modules, parser.__name__, parser)
    progress = []
    result = run_import({"scorePath": str(score), "workDir": str(tmp_path / "work"),
                         "outputDir": str(tmp_path / "out"), "metadata": {"artist": "Green Lung"}}, progress.append)
    assert result["ok"] is False and result["code"] == "needs_audio"
    assert not (tmp_path / "out").exists()
    assert all(isinstance(item, dict) and "stage" in item for item in progress)


def test_real_score_parser_worker_cli_and_feedpak_validator_end_to_end(tmp_path):
    """Exercise real score parsing and matching; no mocked success/remote downloads."""
    frets = np.random.default_rng(512).integers(0, 12, 48)
    measures = [{"signature": [4, 4], "voices": [{"beats": [
        {"duration": [1, 4], "notes": [{"string": 0, "fret": int(fret)}]} for fret in frets[start:start + 4]
    ]}]} for start in range(0, 48, 4)]
    raw = {"format": "songsterr", "songId": 12, "revisionId": 34, "title": "Editor copy", "artist": "Misc Covers",
           "tracks": [{"id": 0, "name": "Lead Guitar", "instrumentId": 29, "tuning": [64, 59, 55, 50, 45, 40]}],
           "parts": [{"measures": measures, "automations": {"tempo": [{"measure": 0, "position": [0, 1], "bpm": 120, "type": 4}]}}]}
    score = tmp_path / "score.json"
    score.write_text(json.dumps(raw), encoding="utf-8")
    from feedback_converter.song_import.score import load_performance
    performed = load_performance(score)
    rate, offset, scale = 22050, 1.25, 1.04
    samples = np.zeros(round((performed["duration"] * scale + offset + 0.4) * rate))
    for track in performed["tracks"]:
        for note in track["notes"]:
            duration = note["sus"] * scale * 0.8
            t = np.arange(round(duration * rate)) / rate
            midi = track["tuning"][note["s"]] + note["f"]
            wave = 0.5 * np.sin(2 * np.pi * 440 * 2 ** ((midi - 69) / 12) * t)
            fade = round(rate * 0.008)
            wave[:fade] *= np.linspace(0, 1, fade)
            wave[-fade:] *= np.linspace(1, 0, fade)
            start = round((note["t"] * scale + offset) * rate)
            samples[start:start + len(wave)] += wave
    audio = tmp_path / "recording.wav"
    sf.write(audio, samples, rate)
    request = {"scorePath": str(score), "audio": {"kind": "file", "path": str(audio)}, "artworkLookup": False,
               "metadata": {"songId": 12, "revisionId": 34, "title": "Real song", "artist": "Original band"},
               "workDir": str(tmp_path / "jobs"), "outputDir": str(tmp_path / "library"),
               "outputSettings": {"nameTemplate": "{artist} - {title}", "outputLayout": "artist"}}
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    result = subprocess.run([sys.executable, "-m", "feedback_converter.cli", "--song-import-file", str(request_path)],
                            capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
    imported = json.loads(result.stdout)
    assert imported["ok"] and imported["coverage"]["notes"] == 48
    assert imported["alignment"]["offset"] == pytest.approx(offset, abs=0.06)
    assert imported["alignment"]["scale"] == pytest.approx(scale, abs=0.005)
    assert imported["relativePath"] == "Original band/Original band - Real song.feedpak"
    assert not (tmp_path / "library").exists()
    validation = subprocess.run([sys.executable, "-m", "feedback_converter.cli", "--validate-feedpak", imported["stagingPath"]],
                                capture_output=True, text=True, timeout=30)
    assert validation.returncode == 0, validation.stdout + validation.stderr


@pytest.mark.parametrize("outcome", ["success", "alignment_failed", "exception"])
def test_song_import_cli_keeps_noisy_worker_output_off_json_protocol(monkeypatch, capsys, outcome):
    from feedback_converter import cli
    from feedback_converter.song_import import worker

    expected = {"ok": True, "stagingPath": "synthetic.feedpak"} if outcome == "success" else {
        "ok": False, "code": "alignment_failed", "error": "The synthetic recording did not match."}

    def noisy_worker(path, progress):
        print("[download] 100% offline fixture")
        progress({"stage": "aligning", "message": "Checking the fixture."})
        if outcome == "exception":
            raise RuntimeError("Synthetic worker exception")
        return expected

    monkeypatch.setattr(worker, "run_request_file", noisy_worker)
    exit_code = cli.main(["--song-import-file", "synthetic-request.json"])
    output = capsys.readouterr()
    result = json.loads(output.out)
    assert len(output.out.splitlines()) == 1
    assert exit_code == (0 if outcome == "success" else 1)
    assert "[download] 100% offline fixture" in output.err
    assert "FEEDFORGE_PROGRESS " in output.err
    if outcome == "exception":
        assert result == {"ok": False, "code": "import_failed", "error": "Synthetic worker exception"}
    else:
        assert result == expected


def test_song_import_health_keeps_library_diagnostics_off_json_protocol(monkeypatch, capsys):
    from feedback_converter import cli
    from feedback_converter.song_import import runtime

    def noisy_health():
        print("Offline dependency diagnostic")
        return {"ok": True, "portable": False}

    monkeypatch.setattr(runtime, "runtime_health", noisy_health)
    assert cli.main(["--song-import-health"]) == 0
    output = capsys.readouterr()
    assert json.loads(output.out) == {"ok": True, "portable": False}
    assert output.err == "Offline dependency diagnostic\n"

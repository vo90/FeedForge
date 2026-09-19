"""Offline portable-worker smoke test using wholly synthetic score/audio.

Run this harness with a development Python containing NumPy, soundfile and
PyYAML. The CHILD converter gets only System32 on PATH, no Python search path,
no virtual environment, and no network request input. This verifies the frozen
application independently of the host's Python/Node installations.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback
import zipfile

import numpy as np
import soundfile as sf
import yaml


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def synthetic_inputs(root: Path) -> dict:
    """Two pitched tracks (including chords) plus one explicitly excluded drum track."""
    frets = np.random.default_rng(512).integers(0, 12, 48)
    guitar_measures, bass_measures, drum_measures = [], [], []
    for begin in range(0, 48, 4):
        guitar_beats, bass_beats, drum_beats = [], [], []
        for value in frets[begin:begin + 4]:
            fret = int(value)
            guitar_beats.append({"duration": [1, 4], "notes": [
                {"string": 0, "fret": fret}, {"string": 1, "fret": (64 + fret + 7 - 59) % 12}]})
            bass_beats.append({"duration": [1, 4], "notes": [{"string": 3, "fret": (64 + fret - 28) % 12}]})
            drum_beats.append({"duration": [1, 4], "notes": [{"string": 0, "fret": 36}]})
        for measures, beats in ((guitar_measures, guitar_beats), (bass_measures, bass_beats), (drum_measures, drum_beats)):
            measures.append({"signature": [4, 4], "voices": [{"beats": beats}]})
    tempo = {"tempo": [{"measure": 0, "position": [0, 1], "bpm": 120, "type": 4}]}
    document = {"format": "songsterr", "songId": 12, "revisionId": 34, "approved": True,
                "title": "Synthetic editor copy", "artist": "Misc Covers",
                "tracks": [
                    {"id": 0, "name": "Lead Guitar", "instrumentId": 29, "tuning": [64, 59, 55, 50, 45, 40]},
                    {"id": 1, "name": "Bass", "instrumentId": 33, "tuning": [43, 38, 33, 28]},
                    {"id": 2, "name": "Drums", "instrumentId": 128, "tuning": []}],
                "parts": [{"measures": rows, "automations": tempo} for rows in (guitar_measures, bass_measures, drum_measures)]}
    score = root / "synthetic-score.json"
    score.write_text(json.dumps(document), encoding="utf-8")
    rate, offset, scale, duration = 22050, 1.25, 1.04, 24.0
    samples = np.zeros(round((offset + duration * scale + 0.4) * rate), dtype=np.float64)
    for index, value in enumerate(frets):
        fret = int(value)
        length = round(0.4 * scale * rate)
        t = np.arange(length) / rate
        for midi, amplitude in ((64 + fret, 0.25), (59 + (64 + fret + 7 - 59) % 12, 0.25),
                                (28 + (64 + fret - 28) % 12, 0.125)):
            wave = amplitude * np.sin(2 * np.pi * 440 * 2 ** ((midi - 69) / 12) * t)
            fade = round(rate * 0.008)
            wave[:fade] *= np.linspace(0, 1, fade)
            wave[-fade:] *= np.linspace(1, 0, fade)
            begin = round((offset + index * 0.5 * scale) * rate)
            samples[begin:begin + length] += wave
    audio = root / "synthetic-recording.wav"
    sf.write(audio, samples, rate, subtype="PCM_16")
    request = {"scorePath": str(score), "audio": {"kind": "file", "path": str(audio)}, "artworkLookup": False,
               "metadata": {"songId": 12, "revisionId": 34, "approved": True,
                            "title": "Portable fixture", "artist": "Original band"},
               "workDir": str(root / "jobs"), "outputDir": str(root / "unpublished-library"),
               "outputSettings": {"nameTemplate": "{artist} - {year} - {title}", "outputLayout": "artist"}}
    request_path = root / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    return {"score": score, "audio": audio, "request": request_path, "offset": offset, "scale": scale,
            "duration": len(samples) / rate}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--converter", required=True, type=Path)
    parser.add_argument("--root", required=True, type=Path, help="A NEW directory for owned verification output")
    args = parser.parse_args()
    converter = args.converter.resolve(strict=True)
    if not converter.is_file() or converter.suffix.lower() != ".exe":
        parser.error("--converter must identify the frozen Windows executable")
    root = args.root.absolute()
    if root.exists() or root.is_symlink():
        parser.error("--root must not already exist; this harness never deletes or repairs output")
    root.mkdir(parents=True, exist_ok=False)
    root = root.resolve(strict=True)
    converter_hash = digest(converter)
    receipt = {"owner": "Test-SongsterrConverter.py", "version": 1, "converter": str(converter),
               "converterHash": converter_hash, "offlineInputs": True}
    (root / "ownership.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    environment = dict(os.environ)
    for key in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "NODE_PATH", "NODE_OPTIONS",
                "IMAGEIO_FFMPEG_EXE", "SONGSTERR_NODE_PATH", "FEEDFORGE_TOOLS_DIR"):
        environment.pop(key, None)
    environment["PATH"] = str(Path(environment.get("SystemRoot", r"C:\Windows")) / "System32")
    environment["PYTHONNOUSERSITE"] = "1"
    report = {**receipt, "path": environment["PATH"], "ok": False, "checks": []}

    def run(label: str, arguments: list[str], timeout: int = 120, *, executable: Path | None = None,
            json_result: bool = True):
        started = time.monotonic()
        result = subprocess.run([str(executable or converter), *arguments], cwd=root, env=environment,
                                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        (root / f"{label}.stdout.txt").write_text(result.stdout, encoding="utf-8")
        (root / f"{label}.stderr.txt").write_text(result.stderr, encoding="utf-8")
        report["checks"].append({"name": label, "exitCode": result.returncode, "seconds": round(time.monotonic() - started, 3)})
        if result.returncode:
            raise AssertionError(f"{label} failed with exit {result.returncode}; see logs in {root}")
        return json.loads(result.stdout.strip().splitlines()[-1]) if json_result else result.stdout.strip()

    try:
        health = run("health", ["--song-import-health"])
        assert health.get("ok") and health.get("portable"), "health must establish a complete frozen runtime"
        assert all(health["modules"][name]["available"] for name in ("numpy", "soundfile", "yt_dlp", "yt_dlp_ejs"))
        assert all(health["tools"][name] for name in ("ffmpeg", "ffprobe", "jsRuntime"))
        report["health"] = health
        editor_request = root / "editor-request.json"
        editor_request.write_text(json.dumps({"action": "health"}), encoding="utf-8")
        editor = run("editor-health", ["songsterr", str(editor_request)])
        assert editor.get("ok") and editor["result"]["ok"], editor
        assert editor["result"]["engine"] == "songsterr-editor"
        assert all(item["available"] for item in editor["result"]["dependencies"].values())
        assert editor["result"]["browserTokenProvider"]["registered"], "Bundled audio fallback must register with yt-dlp."
        editor_ffmpeg = Path(editor["result"]["ffmpeg"]["path"]).resolve(strict=True)
        assert editor_ffmpeg.is_relative_to(converter.parent), "Editor FFmpeg must be bundled."
        assert editor["result"]["ffmpeg"]["usable"]
        report["editorHealth"] = editor["result"]
        tool_root = converter.parent / "_internal" / "feedback_converter" / "tools"
        report["toolVersions"] = {}
        for name, option in (("ffmpeg", "-version"), ("ffprobe", "-version"), ("node", "--version")):
            tool = tool_root / f"{name}.exe"
            assert tool.is_file(), f"Bundled {name} executable is missing"
            version = run(name, [option], executable=tool, json_result=False)
            report["toolVersions"][name] = version.splitlines()[0]
        ejs_root = converter.parent / "_internal" / "yt_dlp_ejs" / "yt" / "solver"
        report["ejsData"] = {}
        for name in ("core.min.js", "lib.min.js"):
            file = ejs_root / name
            assert file.is_file() and file.stat().st_size > 0, f"Bundled EJS data is missing: {name}"
            report["ejsData"][name] = {"bytes": file.stat().st_size, "sha256": digest(file)}
        inputs = synthetic_inputs(root)
        wav_imported = run("import-wav", ["--song-import-file", str(inputs["request"])])
        assert wav_imported.get("ok"), wav_imported
        # AAC/M4A exercises the worker's bundled FFmpeg decode path as well as
        # lossless WAV input. It contains only the generated test tones.
        encoded_audio = root / "synthetic-recording.m4a"
        run("encode-m4a", ["-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(inputs["audio"]),
                           "-c:a", "aac", "-b:a", "192k", str(encoded_audio)],
            executable=tool_root / "ffmpeg.exe", json_result=False)
        inputs["audio"] = encoded_audio
        request = json.loads(inputs["request"].read_text(encoding="utf-8"))
        request["audio"]["path"] = str(encoded_audio)
        inputs["request"].write_text(json.dumps(request), encoding="utf-8")
        source_hashes = {name: digest(inputs[name]) for name in ("score", "audio", "request")}
        imported = run("import", ["--song-import-file", str(inputs["request"])])
        assert imported.get("ok"), imported
        assert imported["title"] == "Portable fixture" and imported["artist"] == "Original band"
        assert imported["relativePath"] == "Original band/Original band - Portable fixture.feedpak"
        assert imported["coverage"]["arrangements"] == 2 and imported["coverage"]["notes"] == 144
        assert imported["coverage"]["trackCount"] == 3 and imported["coverage"]["playableTrackCount"] == 2
        assert len(imported["coverage"]["excludedTracks"]) == 1
        assert imported["alignment"]["status"] == "validated"
        timing_failures = []
        report["timing"] = {}
        for label, converted in (("wav", wav_imported), ("m4a", imported)):
            offset_error = abs(converted["alignment"]["offset"] - inputs["offset"])
            scale_error = abs(converted["alignment"]["scale"] - inputs["scale"])
            end_error = abs((converted["alignment"]["offset"] - inputs["offset"]) +
                            24.0 * (converted["alignment"]["scale"] - inputs["scale"]))
            maximum_error = max(offset_error, end_error)
            report["timing"][label] = {"expectedOffset": inputs["offset"], "expectedScale": inputs["scale"],
                                       "offsetErrorSeconds": offset_error, "scaleError": scale_error,
                                       "maximumTimelineErrorSeconds": maximum_error,
                                       "alignment": converted["alignment"]}
            if maximum_error >= 0.06 or scale_error >= 0.005:
                timing_failures.append(f"{label} timing exceeded the smoke target: offset error {offset_error:.6f} s "
                                       f"and maximum timeline error {maximum_error:.6f} s (limit 0.060 s), "
                                       f"scale error {scale_error:.6f} (limit 0.005)")
        assert abs(imported["duration"] - inputs["duration"]) < 0.05, "AAC decoded duration changed unexpectedly"
        assert imported["scoreHash"] == source_hashes["score"] and imported["audioHash"] == source_hashes["audio"]
        archive = Path(imported["stagingPath"]).resolve(strict=True)
        assert archive.is_relative_to(root / "jobs"), "staged output escaped the owned job"
        validation = run("validate", ["--validate-feedpak", str(archive)])
        assert validation.get("ok"), validation
        with zipfile.ZipFile(archive) as pack:
            manifest = yaml.safe_load(pack.read("manifest.yaml"))
            assert manifest["title"] == "Portable fixture" and manifest["artist"] == "Original band"
            assert "year" not in manifest and "album" not in manifest
            assert {entry["type"] for entry in manifest["arrangements"]} == {"lead", "bass"}
            assert len(manifest["stems"]) == 1 and manifest["stems"][0]["id"] == "full"
            assert "cover" not in manifest and manifest["preview"] in pack.namelist()
            assert imported["verification"]["status"] == "passed"
            for entry in manifest["arrangements"]:
                chart = json.loads(pack.read(entry["file"]))
                events = chart["notes"] + chart["chords"]
                assert events and all(note["t"] >= 0 for note in events)
                assert entry["notation"] in pack.namelist()
            preview_path = root / "preview-check.ogg"
            preview_path.write_bytes(pack.read(manifest["preview"]))
            assert 2 < sf.info(preview_path).duration <= 30.01
            assert manifest["song_import"]["sourceMetadata"]["revisionId"] == 34
        assert not (root / "unpublished-library").exists(), "frozen worker must only stage output"
        assert all(digest(inputs[name]) == value for name, value in source_hashes.items()), "source inputs changed"
        assert digest(converter) == converter_hash, "converter changed during smoke test"
        report.update({"import": imported, "validation": validation, "sourceHashes": source_hashes,
                       "scope": "Offline synthetic conversion and frozen dependencies; no live website or audio download tested."})
        assert not timing_failures, "; ".join(timing_failures)
        report["ok"] = True
    except Exception as exc:
        report["error"] = str(exc) or type(exc).__name__
        report["traceback"] = traceback.format_exc()
    (root / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"ok": report["ok"], "report": str(root / "report.json"), **({"error": report["error"]} if "error" in report else {})}))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

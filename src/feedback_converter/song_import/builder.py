"""Build validated staged FeedPaks from an already performed, aligned score."""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from copy import deepcopy
import json
import math
from pathlib import Path
import shutil
import zipfile

import yaml
from PIL import Image

from ..feedpak_validator import require_valid_feedpak
from ..output_naming import output_path, safe_path_segment
from .alignment import map_time
from .audio import ImportFailure
from .synchronization import source_time_scale


def _tuning_offsets(track: dict) -> list[int]:
    tuning = track.get("tuning", [])
    if track.get("instrument") == "bass":
        standards = {4: [28, 33, 38, 43], 5: [23, 28, 33, 38, 43], 6: [23, 28, 33, 38, 43, 48]}
    else:
        standards = {6: [40, 45, 50, 55, 59, 64], 7: [35, 40, 45, 50, 55, 59, 64],
                     8: [30, 35, 40, 45, 50, 55, 59, 64]}
    if len(tuning) not in standards:
        raise ImportFailure("unsupported_score", f"The {len(tuning)}-string {track.get('instrument', 'guitar')} tuning is not supported yet.")
    return [int(value) - base for value, base in zip(tuning, standards[len(tuning)])]


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")), encoding="utf-8")


def _retime_note(note: dict, alignment: dict, duration: float, *, chord_time: float | None = None) -> dict:
    result = deepcopy(note)
    result.pop("source_ids", None)
    original = float(note.get("t", chord_time if chord_time is not None else 0))
    start = map_time(alignment, original)
    if chord_time is None or "t" in note:
        result["t"] = round(start, 6)
    nonlinear = alignment.get("mapping") == "piecewise-linear"
    original_sustain = float(note.get("sus", 0))
    if nonlinear and (not math.isfinite(original_sustain) or original_sustain < 0):
        raise ImportFailure("alignment_failed", "The note has an invalid sustain in the recording timing map.")
    original_sustain = max(0.0, original_sustain)
    sustain = (map_time(alignment, original + original_sustain) - start if nonlinear
               else original_sustain * float(alignment["scale"]))
    if start > duration + 0.05 or start + sustain > duration + 0.05:
        raise ImportFailure("alignment_failed", "The matched tab contains notes outside the recording.")
    if "sus" in note:
        result["sus"] = round(sustain, 6)
    if note.get("bnv"):
        # FeedPak bend-curve t values are relative to their note's onset.
        if nonlinear:
            # A curve segment can cross a map boundary even when it has no
            # authored bend point there. Insert that breakpoint to preserve
            # the original piecewise-linear pitch curve after retiming.
            curve = []
            for point in note["bnv"]:
                if curve and float(point["t"]) > float(curve[-1]["t"]):
                    left = curve[-1]
                    anchors = alignment["anchors"]
                    first = bisect_right(anchors, original + float(left["t"]), key=lambda anchor: anchor["score"])
                    last = bisect_left(anchors, original + float(point["t"]), key=lambda anchor: anchor["score"])
                    for index in range(first, last):
                        anchor = anchors[index]
                        relative = float(anchor["score"]) - original
                        if float(left["t"]) < relative < float(point["t"]):
                            fraction = (relative - float(left["t"])) / (float(point["t"]) - float(left["t"]))
                            curve.append({"t": relative, "v": float(left["v"]) + fraction * (float(point["v"]) - float(left["v"]))})
                curve.append(point)
            result["bnv"] = [{**point, "t": round(map_time(alignment, original + float(point["t"])) - start, 6)} for point in curve]
        else:
            result["bnv"] = [{**point, "t": round(float(point["t"]) * float(alignment["scale"]), 6)} for point in note["bnv"]]
    return result


def _retime_notation(value, alignment: dict):
    """Retain written values; only absolute score-second coordinates are mapped."""
    if isinstance(value, list):
        return [_retime_notation(item, alignment) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: _retime_notation(item, alignment) for key, item in value.items()
              if key not in {"source_ids", "end_time"}}
    if "t" in value:
        result["t"] = round(map_time(alignment, float(value["t"]), allow_negative=True), 6)
        if "tempo" in value:
            scale = source_time_scale(alignment, float(value["t"])) if alignment.get("mapping") == "piecewise-linear" else float(alignment["scale"])
            result["written_tempo"] = value["tempo"]
            result["tempo"] = float(value["tempo"]) / scale
    if "end_time" in value and "t" in value:
        result["duration_seconds"] = round(map_time(alignment, float(value["end_time"]), allow_negative=True) -
                                           map_time(alignment, float(value["t"]), allow_negative=True), 6)
    return result


def _timeline_items(items: list, alignment: dict, duration: float) -> list:
    result = []
    for item in items:
        time = map_time(alignment, item["time"], allow_negative=alignment.get("mapping") == "piecewise-linear")
        if 0 <= time <= duration:
            entry = {**item, "time": time}
            if "bpm" in entry:
                scale = source_time_scale(alignment, item["time"]) if alignment.get("mapping") == "piecewise-linear" else float(alignment["scale"])
                entry["bpm"] = float(entry["bpm"]) / scale
            result.append(entry)
    return result


def build_feedpak(performance: dict, audio: dict, alignment: dict, directory: Path,
                  *, output_dir: Path, output_settings: dict | None = None, recipe: dict | None = None,
                  artwork: dict | None = None) -> dict:
    """Only write inside directory. Publishing/collision handling belongs to the app."""
    if alignment.get("status") != "validated":
        raise ImportFailure("alignment_failed", "The recording has not passed synchronization checks.")
    settings = output_settings or {}
    package = directory / "package"
    package.mkdir(exist_ok=False)
    duration = float(audio["duration"])
    title, artist = str(performance.get("title") or "").strip(), str(performance.get("artist") or "").strip()
    if not title or not artist:
        raise ImportFailure("unsupported_score", "The song title and original artist are required.")
    timeline = {"version": 1}
    for key in ("beats", "sections", "tempos", "time_signatures"):
        timeline[key] = _timeline_items(performance.get(key, []), alignment, duration)
    if alignment.get("mapping") == "piecewise-linear" and alignment.get("tempos"):
        timeline["tempos"] = [deepcopy(item) for item in alignment["tempos"] if 0 <= item["time"] <= duration]
    arrangements = []
    used = set()
    for index, track in enumerate(performance.get("tracks", [])):
        if track.get("instrument") not in {"guitar", "bass"}:
            raise ImportFailure("unsupported_score", "An unsupported instrument was included in the playable arrangements.")
        if not track.get("notes") and not track.get("chords"):
            continue
        name = str(track.get("name") or f"Track {index + 1}")
        ident = safe_path_segment(str(track.get("id") or f"track-{index + 1}"), max_length=60)
        if ident.lower() in used:
            ident = f"{ident}-{index + 1}"
        used.add(ident.lower())
        tuning = _tuning_offsets(track)
        chart = {"name": name, "tuning": tuning, "capo": max(0, int(track.get("capo", 0))),
                 "notes": [_retime_note(note, alignment, duration) for note in track.get("notes", [])],
                 "chords": [], "templates": deepcopy(track.get("templates", [])),
                 "anchors": [], "handshapes": [], "beats": timeline.get("beats", []),
                 "sections": timeline.get("sections", [])}
        for chord in track.get("chords", []):
            entry = {**deepcopy(chord), "t": round(map_time(alignment, chord["t"]), 6)}
            entry.pop("source_ids", None)
            if "notes" in chord:
                entry["notes"] = [_retime_note(note, alignment, duration, chord_time=float(chord["t"])) for note in chord["notes"]]
            chart["chords"].append(entry)
        if timeline.get("tempos"):
            chart["tempos"] = timeline["tempos"]
        if timeline.get("time_signatures"):
            chart["time_signatures"] = timeline["time_signatures"]
        relative_file = f"arrangements/{ident}.json"
        _write_json(package / relative_file, chart)
        kind = "bass" if track["instrument"] == "bass" else str(track.get("role") or "guitar")
        arrangements.append({"id": ident, "name": name, "file": relative_file, "type": kind,
                             "tuning": tuning, "capo": chart["capo"],
                             "event_count": len(chart["notes"]) + len(chart["chords"]),
                             "note_count": len(chart["notes"]) + sum(len(chord.get("notes", [])) for chord in chart["chords"])})
        if track.get("notation"):
            notation_file = f"notation/{ident}.json"
            _write_json(package / notation_file, _retime_notation(track["notation"], alignment))
            arrangements[-1]["notation"] = notation_file
    if not arrangements:
        raise ImportFailure("unsupported_score", "The tab has no supported playable guitar or bass arrangements.")
    source = performance.get("source") or {}
    coverage = {"arrangements": len(arrangements), "notes": sum(x["note_count"] for x in arrangements),
                "excludedTrackWarnings": [str(message) for message in performance.get("warnings", [])
                                           if str(message).startswith("Excluded non-guitar/bass track:")]}
    for key in ("trackCount", "playableTrackCount", "excludedTracks"):
        if key in source:
            coverage[key] = deepcopy(source[key])
    (package / "audio").mkdir()
    shutil.copyfile(audio["path"], package / "audio/full.ogg")
    shutil.copyfile(audio["previewPath"], package / "audio/preview.ogg")
    _write_json(package / "song_timeline.json", timeline)
    manifest = {"feedpak_version": "1.16.0", "title": title, "artist": artist, "duration": duration,
                "arrangements": arrangements, "stems": [{"id": "full", "file": "audio/full.ogg", "codec": "ogg", "default": True}],
                "preview": "audio/preview.ogg", "song_timeline": "song_timeline.json"}
    if performance.get("album"):
        manifest["album"] = str(performance["album"])
    if performance.get("year"):
        try:
            manifest["year"] = int(performance["year"])
        except (TypeError, ValueError):
            pass
    if recipe:
        manifest["song_import"] = {**recipe, "coverage": coverage}
    if artwork and artwork.get("status") == "matched" and artwork.get("path"):
        with Image.open(artwork["path"]) as cover:
            extension = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}[cover.format]
        cover_name = f"cover.{extension}"
        shutil.copyfile(artwork["path"], package / cover_name)
        manifest["cover"] = cover_name
        for key in ("album", "year"):
            if key not in manifest and artwork.get(key):
                manifest[key] = artwork[key]
    (package / "manifest.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True), encoding="utf-8")
    validation = require_valid_feedpak(package)
    archive = directory / "result.feedpak"
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED) as output:
        for item in sorted(package.rglob("*")):
            if item.is_file():
                entry = zipfile.ZipInfo(item.relative_to(package).as_posix(), date_time=(1980, 1, 1, 0, 0, 0))
                entry.compress_type = zipfile.ZIP_DEFLATED
                entry.create_system = 3
                entry.external_attr = 0o100644 << 16
                output.writestr(entry, item.read_bytes(), compresslevel=6)
    require_valid_feedpak(archive)
    naming = {**manifest, "arrangement_names": {entry["id"]: entry["type"] for entry in arrangements}}
    # Network downloads have no meaningful source folder or copied-tab filename.
    source_path = Path(safe_path_segment(f"{artist} - {title}") + ".gp")
    destination = output_path(source_path, output_dir, naming,
                              output_layout=str(settings.get("outputLayout") or "flat"), source_root=None,
                              name_template=str(settings.get("nameTemplate") or "{artist} - {title}"),
                              fallback_title=title, suffix=".feedpak")
    return {"stagingPath": str(archive), "relativePath": destination.relative_to(output_dir).as_posix(),
            "title": title, "artist": artist, "duration": duration,
            "coverage": coverage,
            "warnings": list(validation.warnings)}

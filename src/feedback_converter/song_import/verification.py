"""Independent raw-source-to-archive fidelity verification.

``passed`` concerns the explicitly listed conversion checks, not musical quality
or proof of audio synchronization. No production score parser, renderer or time
mapper is used to construct expected values. The caller retains raw sources and
the complete applied alignment alongside this bounded, JSON-serializable report.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path, PurePosixPath
from zipfile import BadZipFile, ZipFile

import yaml

from .verify_source import UnverifiedFeature, inactive, read_source
from .verify_timeline import expected

VERSION = 4
TIME_TOLERANCE = 0.0000011
TECHNIQUES = {"pm", "mt", "vb", "ghost", "ac", "tp", "lr", "tr", "slp", "plk", "hm", "hp", "ho", "po", "ln", "sl", "slu", "slide_out", "slide_out_marks", "slide_in_marks", "bn", "pkd"}
TUNINGS = {"guitar": {6: [40, 45, 50, 55, 59, 64], 7: [35, 40, 45, 50, 55, 59, 64], 8: [30, 35, 40, 45, 50, 55, 59, 64]},
           "bass": {4: [28, 33, 38, 43], 5: [23, 28, 33, 38, 43], 6: [23, 28, 33, 38, 43, 48]}}


class Check:
    def __init__(self):
        self.errors = []
        self.total_errors = 0

    def fail(self, code, location, message, wanted=None, actual=None):
        self.total_errors += 1
        if len(self.errors) < 100:
            row = {"code": code, "location": location, "message": message}
            if wanted is not None:
                row["expected"] = wanted
            if actual is not None:
                row["actual"] = actual
            self.errors.append(row)

    def equal(self, code, location, wanted, actual):
        if wanted != actual or (isinstance(wanted, bool) or isinstance(actual, bool)) and type(wanted) is not type(actual):
            self.fail(code, location, "The archived value differs from the source-derived value.", wanted, actual)

    def near(self, code, location, wanted, actual, tolerance=TIME_TOLERANCE):
        if isinstance(actual, bool) or not isinstance(actual, (int, float)) or not math.isfinite(actual) or abs(wanted - actual) > tolerance:
            self.fail(code, location, "The archived number differs beyond the documented rounding tolerance.", wanted, actual)


def _finite(value, location, check):
    if isinstance(value, float) and not math.isfinite(value):
        check.fail("nonfinite_number", location, "FeedPak contains a non-finite number.")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _finite(item, location + f"/{index}", check)
    elif isinstance(value, dict):
        for key, item in value.items():
            _finite(item, location + "/" + str(key), check)


def _json(archive, name, check):
    info = archive.getinfo(name)
    if info.file_size > 80 * 1024 * 1024:
        raise ValueError(name + ": verification JSON size limit exceeded")
    result = json.loads(archive.read(info))
    _finite(result, name, check)
    return result


def _flatten(chart):
    output = [(dict(n), f"notes/{i}") for i, n in enumerate(chart.get("notes", []))]
    templates = chart.get("templates", [])
    for ci, chord in enumerate(chart.get("chords", [])):
        children = chord.get("notes")
        if children is None:
            # A generated template-only chord cannot preserve individual
            # note durations/effects; report its actual playable information.
            template = templates[int(chord.get("id", chord.get("cid", chord.get("chordId", -1))))]
            children = [{"s": s, "f": f, "sus": chord.get("sus", 0)}
                        for s, f in enumerate(template["frets"]) if f >= 0]
        for ni, raw in enumerate(children):
            n = {"t": chord["t"], **{k: v for k, v in chord.items() if k in TECHNIQUES}, **raw}
            output.append((n, f"chords/{ci}/notes/{ni}"))
    return output


def _timeline(wanted, actual, check, name):
    if not isinstance(actual, list):
        check.fail("missing_timeline", name, "A source-derived timeline array is missing.")
        return
    check.equal("timeline_count", name, len(wanted), len(actual))
    for index, (a, b) in enumerate(zip(wanted, actual)):
        loc = name + f"/{index}"
        check.near("timeline_time", loc + "/time", a["time"], b.get("time"))
        for key in a:
            if key == "bpm":
                check.near("tempo_value", loc + "/bpm", a[key], b.get(key), max(1e-7, abs(a[key]) * 1e-9))
            elif key != "time":
                check.equal("timeline_value", loc + "/" + key, a[key], b.get(key))


def _notes(wanted, actual, check, part, duration):
    check.equal("note_count", "tracks/" + part.id, len(wanted), len(actual))
    # Stable physical-string/time order is independent of exporter chord
    # grouping; source duplicates remain distinct multiset entries.
    expected_order = sorted(wanted, key=lambda x: (x["note"]["s"], x["note"]["t"], x["note"]["f"]))
    actual_order = sorted(actual, key=lambda x: (x[0].get("s", -1), x[0].get("t", -1), x[0].get("f", -1)))
    for item, (b, destination) in zip(expected_order, actual_order):
        a = item["note"]
        loc = item["locations"][0] + f"@visit{item['occurrence']} -> tracks/{part.id}/{destination}"
        for key in set(b) - ({"t", "s", "f", "sus", "bnv", "source_id", "source_ids"} | TECHNIQUES):
            if not inactive(b[key]):
                check.fail("unexpected_note_field", loc + "/" + key, "An unaccounted playable-note field was introduced.")
        for key in ("s", "f"):
            check.equal("note_" + key, loc + "/" + key, a[key], b.get(key))
        for key in ("t", "sus"):
            check.near("note_time" if key == "t" else "note_sustain", loc + "/" + key, a[key], b.get(key, 0))
        for key in TECHNIQUES:
            if key in {"slide_out_marks", "slide_in_marks"}:
                continue
            wanted_value, actual_value = a.get(key), b.get(key)
            if wanted_value is None and (actual_value is None or actual_value is False):
                continue
            if key == "bn" and isinstance(wanted_value, (int, float)):
                check.near("note_technique", loc + "/bn", wanted_value, actual_value, 1e-8)
            else:
                check.equal("note_technique", loc + "/" + key, wanted_value, actual_value)
        _slide_marks(a.get("slide_out_marks", []), b, check, loc)
        _incoming_marks(a.get("slide_in_marks", []), b, check, loc)
        curves = (a.get("bnv", []), b.get("bnv", []))
        check.equal("bend_point_count", loc + "/bnv", len(curves[0]), len(curves[1]))
        for pi, (ap, bp) in enumerate(zip(*curves)):
            check.near("bend_time", loc + f"/bnv/{pi}/t", ap["t"], bp.get("t"))
            check.near("bend_value", loc + f"/bnv/{pi}/v", ap["v"], bp.get("v"), 1e-8)
        if not isinstance(b.get("t"), (int, float)) or not isinstance(b.get("sus", 0), (int, float)):
            continue
        if b["t"] < 0 or b.get("sus", 0) < 0 or b["t"] + b.get("sus", 0) > duration + TIME_TOLERANCE:
            check.fail("note_audio_bounds", loc, "A playable note is outside the encoded audio timeline.")


def _slide_marks(wanted, note, check, location):
    """Compare source intervals individually, including absence and validity."""
    actual = note.get("slide_out_marks", [])
    loc = location + "/slide_out_marks"
    if not isinstance(actual, list):
        check.fail("slide_mark_shape", loc, "Slide-out marks must be an array.")
        return
    check.equal("slide_mark_count", loc, len(wanted), len(actual))
    previous_end = 0.0
    for index, mark in enumerate(actual):
        at = loc + f"/{index}"
        if not isinstance(mark, dict) or set(mark) != {"direction", "start", "end"}:
            check.fail("slide_mark_shape", at, "A slide-out interval has an invalid shape.")
            continue
        left, right = mark["start"], mark["end"]
        if (not isinstance(mark["direction"], str) or mark["direction"] not in {"up", "down"}
                or any(isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n) for n in (left, right))):
            check.fail("slide_mark_shape", at, "A slide-out interval has an invalid direction or number.")
            continue
        sustain = note.get("sus", 0)
        if (left < 0 or right <= left or left < previous_end - TIME_TOLERANCE
                or not isinstance(sustain, (int, float)) or right > sustain + TIME_TOLERANCE):
            check.fail("slide_mark_bounds", at, "A slide-out interval is outside the sustain or out of order.")
        previous_end = right
        if index < len(wanted):
            expected = wanted[index]
            check.equal("slide_mark_direction", at + "/direction", expected["direction"], mark["direction"])
            check.near("slide_mark_time", at + "/start", expected["start"], left)
            check.near("slide_mark_time", at + "/end", expected["end"], right)


def _incoming_marks(wanted, note, check, location):
    """Check authored destination onsets, never an inferred approach trajectory."""
    actual = note.get("slide_in_marks", [])
    loc = location + "/slide_in_marks"
    if not isinstance(actual, list):
        check.fail("slide_in_shape", loc, "Slide-in marks must be an array.")
        return
    check.equal("slide_in_count", loc, len(wanted), len(actual))
    previous = -1.0
    for index, mark in enumerate(actual):
        at = loc + f"/{index}"
        if not isinstance(mark, dict) or set(mark) != {"direction", "time"}:
            check.fail("slide_in_shape", at, "An incoming-slide cue has an invalid shape.")
            continue
        when = mark["time"]
        if (not isinstance(mark["direction"], str) or mark["direction"] not in {"up", "down"}
                or isinstance(when, bool) or not isinstance(when, (int, float))
                or isinstance(when, float) and not math.isfinite(when)):
            check.fail("slide_in_shape", at, "An incoming-slide cue has an invalid direction or time.")
            continue
        sustain = note.get("sus", 0)
        if (when < 0 or when <= previous or isinstance(sustain, bool) or not isinstance(sustain, (int, float))
                or isinstance(sustain, float) and not math.isfinite(sustain) or when > sustain + TIME_TOLERANCE):
            check.fail("slide_in_bounds", at, "An incoming-slide cue is outside the sustain or out of order.")
            previous = when
            continue
        previous = when
        if index < len(wanted):
            check.equal("slide_in_direction", at + "/direction", wanted[index]["direction"], mark["direction"])
            check.near("slide_in_time", at + "/time", wanted[index]["time"], when)


def _chords(wanted, chart, check, part):
    groups = {}
    labels = {beat["location"]: beat.get("chord_label", "") for bar in part.beats for beat in bar}
    for item in wanted:
        groups.setdefault((item["occurrence"], item["beat"]), []).append(item["note"])
    expected_shapes = {}
    for (_, beat), notes in groups.items():
        if len(notes) > 1 and len({n["t"] for n in notes}) == 1:
            shape = (tuple(sorted((n["s"], n["f"]) for n in notes)), labels.get(beat, ""))
            expected_shapes.setdefault(shape, []).append(notes[0]["t"])
    actual_shapes = {}
    templates = chart.get("templates", [])
    for ci, chord in enumerate(chart.get("chords", [])):
        loc = f"tracks/{part.id}/chords/{ci}"
        children = chord.get("notes", [])
        shape = tuple(sorted((n["s"], n["f"]) for n in children))
        template_id = chord.get("id")
        if isinstance(template_id, bool) or not isinstance(template_id, int) or not 0 <= template_id < len(templates):
            check.fail("chord_template", loc, "Chord has no valid template.")
            continue
        actual_shapes.setdefault((shape, templates[template_id].get("name") or ""), []).append(chord["t"])
        names = {label for (expected_shape, label), times in expected_shapes.items() if expected_shape == shape
                 and any(abs(time - chord["t"]) <= TIME_TOLERANCE for time in times)}
        if (templates[template_id].get("name") or "") not in names:
            check.fail("invented_chord_name", loc + "/name", "The chord label differs from the authored label at this beat.")
        frets = [-1] * len(part.tuning)
        for s, f in shape:
            if not 0 <= s < len(frets):
                check.fail("chord_string", loc, "Chord uses an invalid string.")
                continue
            frets[s] = f
        check.equal("chord_template", loc + "/frets", frets, templates[template_id].get("frets"))
        check.equal("invented_chord_fingers", loc + "/fingers", [-1] * len(frets), templates[template_id].get("fingers", [-1] * len(frets)))
    if {k: len(v) for k, v in expected_shapes.items()} != {k: len(v) for k, v in actual_shapes.items()}:
        check.fail("authored_chord_groups", "tracks/" + part.id, "Archived chords differ from source-authored simultaneous-note groups.",
                   sum(map(len, expected_shapes.values())), sum(map(len, actual_shapes.values())))
    for shape in expected_shapes.keys() & actual_shapes.keys():
        for wanted_time, actual_time in zip(sorted(expected_shapes[shape]), sorted(actual_shapes[shape])):
            check.near("chord_time", "tracks/" + part.id + "/chords", wanted_time, actual_time)


def _notation(archive, arrangement, wanted, check):
    """Verify available beat time/rest facts without inferring engraving style."""
    name = arrangement.get("notation")
    if not name:
        raise UnverifiedFeature("tracks/" + wanted["source"].id, "Written source notation is missing; its preservation cannot be independently verified.")
    data = _json(archive, name, check)
    facts = wanted["notation_beats"]
    actual = []
    check.equal("notation_instrument", name, wanted["source"].instrument, data.get("instrument"))
    measures = data.get("measures", [])
    check.equal("notation_measure_count", name, len(wanted["notation_measures"]), len(measures))
    for mi, (a, b) in enumerate(zip(wanted["notation_measures"], measures)):
        loc = name + f"/measures/{mi}"
        for key, value in a.items():
            if key in {"t", "duration_seconds", "tempo", "written_tempo"}:
                check.near("notation_measure", loc + "/" + key, value, b.get(key), TIME_TOLERANCE if key in {"t", "duration_seconds"} else 1e-6)
            else:
                check.equal("notation_measure", loc + "/" + key, value, b.get(key))
        for staff in b.get("staves", {}).values():
            for voice in staff.get("voices", []):
                for beat in voice.get("beats", []):
                    actual.append({**beat, "_measure": mi + 1, "_voice": str(voice.get("v"))})
    check.equal("notation_beat_count", name, len(facts), len(actual))
    # Raw ID spelling is exporter-specific; use stable time/voice-independent
    # multisets so simultaneous voices still retain their multiplicity.
    sorted_facts = sorted(facts, key=lambda f: (f["measure"], f["voice"], f["time"]))
    sorted_actual = sorted(actual, key=lambda b: (b["_measure"], b["_voice"], b["t"]))
    for index, (a, b) in enumerate(zip(sorted_facts, sorted_actual)):
        loc = a["location"] + " -> " + name + f"/beat/{index}"
        check.near("notation_time", loc, a["time"], b.get("t"))
        check.near("notation_duration", loc, a["end"] - a["time"], b.get("duration_seconds"))
        check.equal("notation_rest", loc, a["rest"], bool(b.get("rest")))
        check.equal("notation_voice", loc, a["voice"], b["_voice"])
        check.equal("notation_position", loc + "/beat_pos", [a["quarter"].numerator, a["quarter"].denominator], b.get("beat_pos"))
        for key, value in a["notation"].items():
            check.equal("notation_written_duration", loc + "/" + key, value, b.get(key, 0 if key == "dot" else None))
        for key in set(b) - ({"t", "duration_seconds", "beat_pos", "source_id", "notes", "rest", "_voice", "_measure"} | set(a["notation"])):
            if not inactive(b[key]):
                check.fail("invented_notation_annotation", loc + "/" + key, "A notation annotation absent from the source was introduced.")
        desired_notes = sorted(a["notes"], key=lambda n: (n["str"], n["fret"]))
        actual_notes = sorted(b.get("notes", []), key=lambda n: (n.get("str", -1), n.get("fret", -1)))
        check.equal("notation_note_count", loc, len(desired_notes), len(actual_notes))
        for ni, (an, bn) in enumerate(zip(desired_notes, actual_notes)):
            for key, value in an.items():
                check.equal("notation_note", loc + f"/notes/{ni}/" + key, value, bn.get(key, False if key == "tied" else None))
            for key in set(bn) - (set(an) | {"source_id"}):
                if not inactive(bn[key]):
                    check.fail("invented_notation_note", loc + f"/notes/{ni}/" + key, "A notation note property absent from the source was introduced.")


def verify_import(score_path: Path, archive: Path, alignment: dict, metadata: dict | None = None) -> dict:
    """Compare an immutable raw score with a completed staged FeedPak.

    Does not mutate sources, archive or alignment; performs no network I/O.
    ``unsupported`` means the independent checker lacks a source interpretation,
    never that an unusual but preserved musical choice was judged incorrect.
    """
    check = Check()
    report = {"version": VERSION, "status": "failed", "errors": check.errors, "warnings": [], "counts": {},
              "scope": ["source_identity", "track_coverage", "physical_pitch", "note_timing", "techniques", "bend_curves",
                        "slide_out_segment_timing", "slide_in_destination_timing", "authored_chord_groups", "beats", "meter", "sections", "tempo", "notation_beat_timing",
                        "notation_written_rhythm", "notation_pitch_and_ties", "container_references"],
              "rounding": {"secondsDecimalPlaces": 6, "comparisonToleranceSeconds": TIME_TOLERANCE},
              "musicalQualityAssessed": False}
    try:
        score_path, archive = Path(score_path), Path(archive)
        source = read_source(score_path)
        report["sourceSha256"] = hashlib.sha256(score_path.read_bytes()).hexdigest()
        report["format"] = source.format
        metadata = metadata or {}
        for key, value in source.identity.items():
            if metadata.get(key) is not None:
                check.equal("source_identity", "metadata/" + key, value, str(metadata[key]))
            provenance = alignment.get("provenance", {})
            if provenance.get(key) is not None:
                check.equal("timing_identity", "alignment/" + key, value, str(provenance[key]))
        wanted = expected(source, alignment)
        report["counts"].update(sourceTracks=source.track_count, selectedTracks=len(wanted["parts"]),
                                excludedTracks=len(source.excluded), writtenMeasures=len(source.bars),
                                performedMeasures=len(wanted["order"]), sourceNotes=wanted["raw_notes"],
                                tieSegments=wanted["tie_segments"], expectedNotes=sum(len(p["notes"]) for p in wanted["parts"]))
        report["excludedTracks"] = source.excluded
        report["uncomparedSourceAnnotations"] = sorted(source.ignored)
        report["timing"] = {"method": str(alignment.get("method", "unspecified")),
                            "independentAudioMatchAssessed": False,
                            "scoreDuration": wanted["score_duration"], "mappedEnd": wanted["mapped_end"]}
        with ZipFile(archive) as z:
            names = z.namelist()
            if len(set(names)) != len(names):
                check.fail("duplicate_archive_entry", "archive", "Archive contains duplicate entry names.")
            if len(names) > 100_000 or sum(i.file_size for i in z.infolist()) > 2 * 1024 * 1024 * 1024:
                raise ValueError("archive: expanded verification size limit exceeded")
            for name in names:
                p = PurePosixPath(name)
                if p.is_absolute() or ".." in p.parts or "\\" in name or ":" in name:
                    check.fail("unsafe_archive_path", "archive", "Archive contains an unsafe entry path.")
            if z.getinfo("manifest.yaml").file_size > 1024 * 1024:
                raise ValueError("manifest: verification size limit exceeded")
            manifest = yaml.safe_load(z.read("manifest.yaml"))
            _finite(manifest, "manifest", check)
            recipe = manifest.get("song_import", {})
            if recipe.get("preservationContract", 0) >= 4:
                original = recipe.get("sourceFile")
                if original not in names or z.getinfo(original).file_size > 80 * 1024 * 1024:
                    check.fail("retained_source", "import", "The original source is missing or too large.")
                else:
                    check.equal("retained_source", original, report["sourceSha256"], hashlib.sha256(z.read(original)).hexdigest())
                compatibility = _json(z, recipe.get("compatibilityFile", ""), check)
                if compatibility.get("status") not in {"compatible", "limitations"}:
                    check.fail("compatibility", "import/compatibility", "A blocked or invalid compatibility report cannot be published.")
            if isinstance(recipe, dict):
                if recipe.get("scoreHash"):
                    check.equal("source_hash", "manifest/song_import/scoreHash", report["sourceSha256"], recipe["scoreHash"])
                for key, value in source.identity.items():
                    if recipe.get(key) is not None:
                        check.equal("source_identity", "manifest/song_import/" + key, value, str(recipe[key]))
            for key in ("title", "artist"):
                check.equal("metadata", "manifest/" + key, str(metadata.get(key) or getattr(source, key)).strip(), manifest.get(key))
            duration = manifest.get("duration")
            if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration <= 0:
                raise ValueError("manifest/duration: invalid audio duration")
            refs = [manifest.get("preview"), manifest.get("song_timeline")]
            if manifest.get("cover"):
                refs.append(manifest["cover"])
            refs.extend(s.get("file") for s in manifest.get("stems", []))
            arrangements = manifest.get("arrangements", [])
            refs.extend(a.get("file") for a in arrangements)
            refs.extend(a.get("notation") for a in arrangements if a.get("notation"))
            for reference in refs:
                if not isinstance(reference, str) or reference not in names:
                    check.fail("missing_asset", "manifest", "A required package asset reference is missing.", actual=reference)
            timeline = _json(z, manifest["song_timeline"], check)
            for key in ("beats", "sections", "time_signatures", "tempos"):
                _timeline(wanted[key], timeline.get(key), check, "song_timeline/" + key)
            selected = [p for p in wanted["parts"] if p["notes"]]
            check.equal("arrangement_count", "manifest/arrangements", len(selected), len(arrangements))
            unmatched = list(arrangements)
            actual_note_count, actual_chord_count, notation_beat_count = 0, 0, 0
            for part in selected:
                src = part["source"]
                candidates = [a for a in unmatched if str(a.get("id")) == src.id]
                if not candidates:
                    candidates = [a for a in unmatched if a.get("name") == src.name]
                if len(candidates) != 1:
                    check.fail("track_identity", "tracks/" + src.id, "Source track does not identify exactly one archived arrangement.")
                    continue
                arr = candidates[0]
                unmatched.remove(arr)
                chart = _json(z, arr["file"], check)
                check.equal("track_name", "tracks/" + src.id, src.name, arr.get("name"))
                check.equal("track_name", arr["file"] + "/name", src.name, chart.get("name"))
                standard = TUNINGS.get(src.instrument, {}).get(len(src.tuning))
                if standard is None:
                    raise UnverifiedFeature("tracks/" + src.id, "This physical string layout is not independently verified.")
                offsets = [pitch - base for pitch, base in zip(src.tuning, standard)]
                for container, loc in ((arr, "manifest/arrangements/" + src.id), (chart, arr["file"])):
                    check.equal("tuning", loc + "/tuning", offsets, container.get("tuning"))
                    check.equal("capo", loc + "/capo", src.capo, container.get("capo", 0))
                if src.instrument == "bass":
                    check.equal("instrument", "tracks/" + src.id, "bass", arr.get("type"))
                elif arr.get("type") not in {"guitar", "lead", "rhythm"}:
                    check.fail("instrument", "tracks/" + src.id, "Guitar source was exported as another instrument.")
                flattened = _flatten(chart)
                actual_note_count += len(flattened)
                actual_chord_count += len(chart.get("chords", []))
                notation_beat_count += len(part["notation_beats"])
                _notes(part["notes"], flattened, check, src, duration)
                _chords(part["notes"], chart, check, src)
                for key in ("beats", "sections", "tempos"):
                    _timeline(wanted[key], chart.get(key), check, arr["file"] + "/" + key)
                _notation(z, arr, part, check)
            report["counts"]["archivedNotes"] = actual_note_count
            report["counts"]["archivedChords"] = actual_chord_count
            report["counts"]["notationBeats"] = notation_beat_count
        report["status"] = "failed" if check.total_errors else "passed"
    except UnverifiedFeature as exc:
        report["status"] = "failed" if check.total_errors else "unsupported"
        report["unsupported"] = [{"location": exc.location, "message": str(exc)}]
    except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError, BadZipFile, ZeroDivisionError, RecursionError, yaml.YAMLError) as exc:
        check.fail("verification_input", "source_or_archive", str(exc))
        report["status"] = "failed"
    report["errorCount"] = check.total_errors
    return report

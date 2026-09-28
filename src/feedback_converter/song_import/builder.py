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
from ..difficulty import ensure_difficulty
from ..chart_guidance import POLICY as GUIDANCE_POLICY, finalize as finalize_guidance
from ..verify_chart_guidance import validate_arrangement as check_guidance
from ..output_naming import output_path, safe_path_segment
from .alignment import map_time
from .audio import ImportFailure
from .synchronization import source_time_scale
from .terminal_sustains import allowed as terminal_sustains_allowed, trim_held_note, slides_allowed
from .ending_cutoff import allowed as ending_cutoff_allowed, omitted_note


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
    # Match the completed archive's microsecond timing precision. Check the
    # actual serialized onset/sustain as well as the unrounded source map;
    # Only the explicit final-sustain policy may shorten a held tail; the old
    # 50 ms allowance must never hide missing attacks or technique events.
    archived_end = round(start, 6) + round(sustain, 6)
    if max(start + sustain, archived_end) > duration + 0.0000011 and not terminal_sustains_allowed(alignment, duration):
        raise ImportFailure("alignment_failed", "The matched tab contains notes outside the recording.",
                            {"mappedNoteEnd": start + sustain, "archivedNoteEnd": archived_end,
                             "audioDuration": duration})
    if "sus" in note:
        result["sus"] = round(sustain, 6)
    if 'harmonic_changes' in note:
        from ..harmonic_changes import retime_changes
        result['harmonic_changes'] = retime_changes({**note, 't':original}, lambda t: map_time(alignment,t))
    if "whammy" in note:
        from ..whammy import retime_whammy
        try:
            result["whammy"] = retime_whammy(note["whammy"], original, original_sustain, start,
                                             lambda t: map_time(alignment, t),
                                             [p['score'] for p in alignment.get('anchors', [])])
        except ValueError as exc:
            raise ImportFailure("unsupported_score", str(exc)) from exc
    if "pick_scrape_marks" in note:
        from .pick_scrape import retime
        result["pick_scrape_marks"] = retime(note, alignment, original, start, sustain)
    if "slide_out_marks" in note:
        marks = note["slide_out_marks"]
        if not isinstance(marks, list):
            raise ImportFailure("unsupported_score", "Slide-out marks must be an array of source intervals.")
        mapped, previous_end = [], 0.0
        for mark in marks:
            if (not isinstance(mark, dict) or set(mark) != {"direction", "start", "end"}
                    or not isinstance(mark.get("direction"), str) or mark["direction"] not in {"up", "down"}
                    or any(isinstance(mark.get(key), bool) or not isinstance(mark.get(key), (int, float))
                           or not math.isfinite(mark[key]) for key in ("start", "end"))):
                raise ImportFailure("unsupported_score", "A slide-out mark has invalid direction or interval values.")
            left, right = mark["start"], mark["end"]
            if left < 0 or right <= left or right > original_sustain or left < previous_end:
                raise ImportFailure("unsupported_score", "A slide-out mark is outside its source segment or out of order.")
            # Both boundaries can cross different synchronization segments.
            # Scaling the interval by the attack's local scale is incorrect.
            mapped_left = round(map_time(alignment, original + left) - start, 6)
            mapped_right = round(map_time(alignment, original + right) - start, 6)
            # The established affine sustain and independently rounded absolute
            # endpoints can differ by one microsecond. Preserve both values.
            if mapped_left < 0 or mapped_right <= mapped_left or mapped_right > round(sustain, 6) + 0.0000011:
                raise ImportFailure("alignment_failed", "A slide-out interval cannot be represented at FeedPak timing precision.")
            mapped.append({"direction": mark["direction"], "start": mapped_left, "end": mapped_right})
            previous_end = right
        result["slide_out_marks"] = mapped
    if "slide_in_marks" in note:
        marks = note["slide_in_marks"]
        if not isinstance(marks, list):
            raise ImportFailure("unsupported_score", "Slide-in marks must be an array of destination onsets.")
        mapped, previous = [], -1.0
        for mark in marks:
            if (not isinstance(mark, dict) or set(mark) != {"direction", "time"}
                    or not isinstance(mark.get("direction"), str) or mark["direction"] not in {"up", "down"}
                    or isinstance(mark.get("time"), bool) or not isinstance(mark.get("time"), (int, float))
                    or isinstance(mark["time"], float) and not math.isfinite(mark["time"])):
                raise ImportFailure("unsupported_score", "A slide-in mark has an invalid direction or time.")
            when = mark["time"]
            if when < 0 or when > original_sustain or when <= previous:
                raise ImportFailure("unsupported_score", "A slide-in mark is outside its source note or out of order.")
            mapped_time = round(map_time(alignment, original + when) - start, 6)
            if (mapped_time < 0 or mapped_time > round(sustain, 6) + 0.0000011
                    or mapped and mapped_time <= mapped[-1]["time"]):
                raise ImportFailure("alignment_failed", "Slide-in onsets cannot be represented at FeedPak timing precision.")
            mapped.append({"direction": mark["direction"], "time": mapped_time})
            previous = when
        result["slide_in_marks"] = mapped
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
    if terminal_sustains_allowed(alignment, duration):
        # Chord children inherit their attack from the chord. Supply it for the
        # policy check without changing the archived representation.
        from .terminal_sustains import cutoff_for
        cutoff = cutoff_for(alignment, duration, start, start+sustain)
        if cutoff is not None:
            adjusted, _ = trim_held_note({**result, "t": round(start, 6)}, cutoff,
                                         allow_directional_slides=slides_allowed(alignment, duration))
            if "t" not in result:
                adjusted.pop("t")
            result = adjusted
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


def _timeline_items(items: list, alignment: dict, duration: float, *, kind: str | None = None) -> list:
    result = []
    active_meter = None
    for item in items:
        time = map_time(alignment, item["time"], allow_negative=alignment.get("mapping") == "piecewise-linear")
        if kind == "time_signatures" and time < 0:
            active_meter = {**item, "time": 0.0}
        if 0 <= time <= duration:
            entry = {**item, "time": time}
            if "bpm" in entry:
                scale = source_time_scale(alignment, item["time"]) if alignment.get("mapping") == "piecewise-linear" else float(alignment["scale"])
                entry["bpm"] = float(entry["bpm"]) / scale
            result.append(entry)
    if active_meter is not None and (not result or result[0]["time"] > 0):
        result.insert(0, active_meter)
    if kind == "beats":
        # FeedPak uses an ordinal for exported downbeats, not source bar IDs.
        # Cropping silent pre-roll can remove one or more earlier downbeats.
        # Keep pickup beats and all timestamps; original bar references remain
        # in retained source and written notation, whose numbering is unchanged.
        ordinal = 0
        for entry in result:
            if entry.get("measure", -1) > 0:
                ordinal += 1
                entry["measure"] = ordinal
    if kind == 'tempos':
        effective = []
        for entry in result:
            if effective and effective[-1]['time'] == entry['time']:
                effective[-1] = entry
            elif not effective or not math.isclose(effective[-1]['bpm'], entry['bpm'], rel_tol=1e-12):
                effective.append(entry)
        result = effective
    return result


def build_feedpak(performance: dict, audio: dict, alignment: dict, directory: Path,
                  *, output_dir: Path, output_settings: dict | None = None, recipe: dict | None = None,
                  artwork: dict | None = None, source_path: Path | None = None,
                  compatibility: dict | None = None, hybrid_lead: dict | None = None) -> dict:
    """Only write inside directory. Publishing/collision handling belongs to the app."""
    if alignment.get("status") != "validated":
        raise ImportFailure("alignment_failed", "The recording has not passed synchronization checks.")
    if alignment.get('endingPadding') and (source_path is None or (recipe or {}).get('preservationContract',0)<36
            or 'recordingSamplesSha256' not in alignment['endingPadding']):
        raise ImportFailure('alignment_failed','Ending silence requires confirmed recording evidence and the original tab.')
    if alignment.get('endingPadding',{}).get('version') == 2 and (recipe or {}).get('preservationContract',0)<37:
        raise ImportFailure('alignment_failed','Combined ending handling requires current preservation evidence.')
    if alignment.get('endingPaddingSync',{}).get('version') == 'recording-clock-v4' and (recipe or {}).get('preservationContract',0)<38:
        raise ImportFailure('alignment_failed','Phrase timing evidence requires the current preservation contract.')
    if alignment.get('endingPadding',{}).get('timingWarning') and (recipe or {}).get('preservationContract',0)<39:
        raise ImportFailure('alignment_failed','Ending timing warnings require the current preservation contract.')
    if alignment.get("terminalSlides") is not None and (not slides_allowed(alignment, audio["duration"])
            or source_path is None or (recipe or {}).get("preservationContract", 0) < 33):
        raise ImportFailure("alignment_failed", "A final slide-out cutoff requires verified recording timing, the original tab and contract 33.")
    settings = output_settings or {}
    original_tracks = performance.get("tracks", [])
    from .high_frets import project, archive_receipt, summary as omission_summary
    omissions = None
    if (performance.get("source") or {}).get("format") == "songsterr":
        performance, omissions = project(performance)
        if omissions["notes"] and (source_path is None or (recipe or {}).get("preservationContract", 0) < 24):
            raise ImportFailure("unsupported_score", "High-fret omissions require the original tab and preservation contract 24.")
    package = directory / "package"
    package.mkdir(exist_ok=False)
    duration = float(audio["duration"])
    title, artist = str(performance.get("title") or "").strip(), str(performance.get("artist") or "").strip()
    if not title or not artist:
        raise ImportFailure("unsupported_score", "The song title and original artist are required.")
    timeline = {"version": 1}
    for key in ("beats", "sections", "tempos", "time_signatures"):
        timeline[key] = _timeline_items(performance.get(key, []), alignment, duration, kind=key)
    if alignment.get("mapping") == "piecewise-linear" and alignment.get("tempos"):
        timeline["tempos"] = [deepcopy(item) for item in alignment["tempos"] if 0 <= item["time"] <= duration]
    arrangements, sustain_adjustments, ending_omissions = [], [], []
    cut_ending = ending_cutoff_allowed(alignment, duration)
    if cut_ending:
        for original_track in original_tracks:
            originals = [(n, n["t"]) for n in original_track.get("notes", [])]
            originals += [(n, n.get("t", c["t"])) for c in original_track.get("chords", []) for n in c.get("notes", [])]
            ending_omissions.extend(omitted_note(original_track["id"], n, alignment, start)
                                    for n, start in originals if map_time(alignment, start) >= duration)
    used = set()
    original_charts = {}
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
        def keep(note, start):
            if cut_ending and map_time(alignment, start) >= duration:
                return False
            return True
        chart = {"name": name, "tuning": tuning, "capo": max(0, int(track.get("capo", 0))),
                 "notes": [_retime_note(note, alignment, duration) for note in track.get("notes", []) if keep(note, note["t"])],
                 "chords": [], "templates": deepcopy(track.get("templates", [])),
                 "anchors": [], "handshapes": [], "beats": timeline.get("beats", []),
                 "sections": timeline.get("sections", [])}
        for chord in track.get("chords", []):
            children = [n for n in chord.get("notes", []) if keep(n, n.get("t", chord["t"]))]
            if not children:
                continue
            if len(children) != len(chord.get("notes", [])):
                raise ImportFailure("alignment_failed", "The recording ends inside a staggered chord; it cannot yet be cut faithfully.")
            entry = {**deepcopy(chord), "t": round(map_time(alignment, chord["t"]), 6)}
            entry.pop("source_ids", None)
            if "notes" in chord:
                entry["notes"] = [_retime_note(note, alignment, duration, chord_time=float(chord["t"])) for note in chord["notes"]]
            chart["chords"].append(entry)
        if not chart["notes"] and not chart["chords"]:
            raise ImportFailure("alignment_failed", "Cutting at the recording ending would remove an entire arrangement.")
        if timeline.get("tempos"):
            chart["tempos"] = timeline["tempos"]
        if timeline.get("time_signatures"):
            chart["time_signatures"] = timeline["time_signatures"]
        if terminal_sustains_allowed(alignment, duration):
            # The evidence describes individual strings, including chord members
            # and tied notes, independently of chord/template grouping.
            originals = [(n, n["t"]) for n in track.get("notes", [])]
            originals += [(n, n.get("t", c["t"])) for c in track.get("chords", []) for n in c.get("notes", [])]
            for note, start in originals:
                left = map_time(alignment, start)
                sustain = round(map_time(alignment, start + note.get("sus", 0)) - left, 6)
                from .terminal_sustains import cutoff_for
                cutoff = cutoff_for(alignment, duration, left, left+sustain)
                if cutoff is not None and left < cutoff and left + sustain > cutoff + 0.0000011:
                    exported = round(math.floor(cutoff * 1_000_000) / 1_000_000 - left, 6)
                    detail = {"trackId": track["id"], "string": note["s"], "fret": note["f"],
                                                "audioStart": round(left, 6), "originalDuration": sustain,
                                                "exportedDuration": exported, "trimmedSeconds": round(sustain - exported, 6)}
                    marks = [{"index": i, "direction": p["direction"],
                              "start": round(map_time(alignment, start + p["start"]) - left, 6),
                              "originalEnd": round(map_time(alignment, start + p["end"]) - left, 6),
                              "exportedEnd": exported} for i, p in enumerate(note.get("slide_out_marks", []))
                             if map_time(alignment, start + p["end"]) - left > exported + 0.0000011]
                    if marks:
                        detail["slideOuts"] = marks
                    sustain_adjustments.append(detail)
        finalize_guidance(chart)
        if settings.get("generateDifficulty") is True:
            ensure_difficulty(chart, duration=duration)
        guidance_errors = check_guidance(chart, duration=duration)
        if guidance_errors:
            raise ImportFailure("guidance_failed", "Arrangement guidance failed validation: " + guidance_errors[0])
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
        if hybrid_lead and hybrid_lead.get("mainTrackId"):
            indices = {}
            for kind in ("notes", "chords"):
                j = 0
                for i, event in enumerate(track[kind]):
                    if keep(event, event["t"]):
                        indices[(kind, i)] = j
                        j += 1
            identity = performance["compositionContext"]["tracks"][track["id"]]
            original_charts[track["id"]] = {"chart": chart, "manifest": arrangements[-1], "eventIndices": indices,
                "identity": {k: identity[k] for k in ("sourceTrackId", "voices")},
                "notation": _retime_notation(track["notation"], alignment) if track.get("notation") else None}
    if not arrangements:
        raise ImportFailure("unsupported_score", "The tab has no supported playable guitar or bass arrangements.")
    hybrid_summary = None
    if hybrid_lead and hybrid_lead.get("enabled"):
        if not hybrid_lead.get("mainTrackId"):
            hybrid_summary = {"status": "not_applicable", "reason": "No usable guitar arrangement."}
        else:
            from .hybrid_lead import plan
            from .hybrid_materialize import materialize
            from .hybrid_reporting import SELECTION_REVISION, activity_summary
            planning = deepcopy(performance)
            # Preserve raw evidence before high-fret projection so a local
            # unsupported gesture cannot erase an otherwise supported solo.
            main_id = hybrid_lead["mainTrackId"]
            options = hybrid_lead["options"]
            affected = {n["trackId"] for n in (omissions or {}).get("notes", [])}
            planning["hybridOmittedTracks"] = sorted(affected)
            planning["hybridRawTracks"] = original_tracks
            planning["hybridSourceOmissions"] = [*(omissions or {}).get("notes", []), *(omissions or {}).get("links", [])]
            composition = plan(planning, options, main_id, alignment, duration, original_charts)
            composition['selectionRevision'] = SELECTION_REVISION
            chart, notation, derived, receipt = materialize(composition, original_charts, options,
                recipe["scoreHash"], recipe["audioHash"], duration, settings.get("generateDifficulty") is True)
            receipt['tabActivity'] = activity_summary(original_charts, performance['tracks'], main_id, chart)
            if derived["id"].lower() in used:
                raise ImportFailure("hybrid_failed", "The derived arrangement ID collides with a source track.")
            _write_json(package / derived["file"], chart)
            if notation:
                _write_json(package / derived["notation"], notation)
            _write_json(package / "import/hybrid-lead.json", receipt)
            arrangements.append(derived)
            hybrid_summary = {"status": receipt["status"], "mainTrackId": main_id,
                "mainName": original_charts[main_id]["manifest"]["name"], "passageCount": len(receipt["passages"]),
                "contributors": [{"id": ident, "name": original_charts[ident]["manifest"]["name"]} for ident in dict.fromkeys(p["trackId"] for p in receipt["passages"])],
                "addedSeconds": receipt['addedSeconds'], "selectedSeconds": receipt['selectedSeconds'],
                "primaryPassages": sum(p.get('priority') in {'solo','lead'} for p in receipt['passages']),
                "replacedMainEvents": len(receipt.get('removedMain', [])), "coverageStatus": receipt['coverage']['status'],
                "coverageScope": "identified_lead_requirements", "selectionRevision": SELECTION_REVISION,
                "tabActivity": receipt['tabActivity'],
                "baseTuning": receipt['baseSelection']['setup']['tuning'], "baseCapo": receipt['baseSelection']['setup']['capo'],
                "limitations": [{**row, "name": next((t['name'] for t in performance['tracks'] if t['id'] == row['trackId']), row['trackId'])} for row in receipt.get('limitations', [])],
                "planningLimited": bool(receipt.get('primaryBudgetLimited') or receipt.get('selection', {}).get('budgetLimited')),
                "leadPassages": [{"name": original_charts[p['trackId']]['manifest']['name'], "start": p['recordingStart'], "end": p['recordingEnd'],
                                  "section": p.get('sectionName', ''), "evidence": p.get('evidence'), "confidence": p.get('confidence')}
                                 for p in receipt['passages'] if p.get('priority') in {'solo', 'lead'}],
                "excluded": [{**row, "name": next((t["name"] for t in performance["tracks"] if t["id"] == row["trackId"]), row["trackId"])} for row in receipt["excluded"]],
                "notationStatus": receipt["notationStatus"]}
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
        manifest["song_import"] = {**deepcopy(recipe), "coverage": coverage}
    manifest.setdefault("song_import", {})["chartGuidancePolicy"] = GUIDANCE_POLICY
    if hybrid_summary:
        manifest.setdefault("song_import", {})["hybridLeadResult"] = hybrid_summary
        if hybrid_summary["status"] != "not_applicable":
            manifest["song_import"]["hybridLeadFile"] = "import/hybrid-lead.json"
    if 'sectionLabels' in source:
        manifest.setdefault('song_import', {}).setdefault('sourceMetadata', {})['sectionLabels'] = deepcopy(source['sectionLabels'])
    if source_path is not None:
        original = "import/source" + source_path.suffix.lower()
        (package / "import").mkdir(exist_ok=True)
        shutil.copyfile(source_path, package / original)
        _write_json(package / "import/compatibility.json", compatibility)
        manifest.setdefault("song_import", {}).update(sourceFile=original, compatibilityFile="import/compatibility.json")
    if omissions and omissions["notes"]:
        _write_json(package / "import/high-fret-omissions.json", archive_receipt(omissions, source_path))
        coverage["omissions"] = omission_summary(omissions)
        manifest.setdefault("song_import", {}).update(highFretOmissionsFile="import/high-fret-omissions.json",
                                                       omissions=coverage["omissions"])
    if alignment.get("sourceTiming") is not None:
        _write_json(package / "import/source-timing.json", alignment["sourceTiming"])
        manifest.setdefault("song_import", {})["sourceTimingFile"] = "import/source-timing.json"
    for key, filename in [('openingRepair','opening-repair'),('timingAssessment','timing-assessment'),
                          ('endingPadding','ending-padding'),('endingPaddingSync','ending-padding-sync'),
                          ('endingPaddingDeclined','ending-padding-declined')]:
        if alignment.get(key) is not None:
            _write_json(package / f'import/{filename}.json', alignment[key])
            manifest.setdefault('song_import', {})[key+'File'] = f'import/{filename}.json'
    if source_path is not None:
        from .pickup_archive import archive as pickup_archive
        pickups = pickup_archive(performance, alignment, source_path)
        if pickups:
            _write_json(package / 'import/pickup-timeline.json', pickups)
            manifest.setdefault('song_import', {})['pickupTimelineFile'] = 'import/pickup-timeline.json'
    if performance.get('mutedSlideEvidence'):
        if source_path is None or (recipe or {}).get('preservationContract', 0) < 28:
            raise ImportFailure('unsupported_score', 'Muted slides require the retained original tab and preservation contract 28.')
        from .muted_slides import archive_evidence as muted_slide_archive
        _write_json(package / 'import/muted-slides.json', muted_slide_archive(performance, source_path))
        manifest.setdefault('song_import', {})['mutedSlidesFile'] = 'import/muted-slides.json'
    if performance.get('staccatoBendEvidence'):
        if source_path is None or (recipe or {}).get('preservationContract', 0) < 31:
            raise ImportFailure('unsupported_score', 'Tied staccato bends require the retained original tab and preservation contract 31.')
        from .staccato_bends import archive_evidence as staccato_bend_archive
        _write_json(package / 'import/staccato-bends.json', staccato_bend_archive(performance, source_path))
        manifest.setdefault('song_import', {})['staccatoBendsFile'] = 'import/staccato-bends.json'
    if performance.get('mutedTieIdentityEvidence'):
        if source_path is None or (recipe or {}).get('preservationContract', 0) < 30:
            raise ImportFailure('unsupported_score', 'Muted tie identity requires the retained original tab and preservation contract 30.')
        from .muted_ties import archive_evidence as muted_tie_archive
        _write_json(package / 'import/muted-tie-identity.json', muted_tie_archive(performance, source_path))
        manifest.setdefault('song_import', {})['mutedTieIdentityFile'] = 'import/muted-tie-identity.json'
    if performance.get('tiedMuteEvidence'):
        if source_path is None or (recipe or {}).get('preservationContract', 0) < 25:
            raise ImportFailure('unsupported_score', 'Tied mute interpretation requires the retained original tab and preservation contract 25.')
        from .tied_mutes import archive_evidence as mute_archive
        _write_json(package / 'import/tied-mutes.json', mute_archive(performance, source_path))
        manifest.setdefault('song_import', {})['tiedMutesFile'] = 'import/tied-mutes.json'
    if performance.get('voiceProjection'):
        if source_path is None:
            raise ImportFailure('unsupported_score', 'Voice arrangements require the retained original tab.')
        from .voices import archive_receipt as voice_receipt
        _write_json(package / 'import/voices.json', voice_receipt(performance, source_path))
        manifest.setdefault('song_import', {})['voicesFile'] = 'import/voices.json'
    if source_path is not None and performance.get('strumEvidence'):
        import hashlib
        groups = deepcopy(performance['strumEvidence'])
        for group in groups:
            group['time'] = round(map_time(alignment,group['time']),6)
            for note in group['notes']:
                note['t'] = round(map_time(alignment,note['t']),6)
        _write_json(package / 'import/strums.json', {'version':1,'timeDomain':'recording_seconds',
                    'sourceSha256':hashlib.sha256(source_path.read_bytes()).hexdigest(),'groups':groups})
        manifest.setdefault('song_import', {})['strumsFile'] = 'import/strums.json'
    if performance.get('harmonicTieEvidence'):
        if source_path is None:
            raise ImportFailure('unsupported_score', 'Tied harmonic interpretation requires the retained original tab.')
        from .tied_harmonics import archive_evidence
        _write_json(package / 'import/tied-harmonics.json', archive_evidence(performance, source_path))
        manifest.setdefault('song_import', {})['tiedHarmonicsFile'] = 'import/tied-harmonics.json'
    if performance.get('trillEvidence'):
        if source_path is None:
            raise ImportFailure('unsupported_score', 'Trill expansion requires the retained original tab.')
        from .songsterr_trills import archive_evidence as trill_archive
        _write_json(package / 'import/trills.json', trill_archive(performance, source_path))
        manifest.setdefault('song_import', {})['trillsFile'] = 'import/trills.json'
    if sustain_adjustments:
        if source_path is None:
            raise ImportFailure("unsupported_score", "Sustain adjustments require the retained original tab.")
        detail = {**alignment["terminalSustains"], "notes": sustain_adjustments}
        if alignment.get("terminalSlides") is not None:
            detail["directionalSlides"] = alignment["terminalSlides"]
            manifest.setdefault("song_import", {})["terminalSlides"] = alignment["terminalSlides"]
        _write_json(package / "import/sustain-adjustments.json", detail)
        manifest.setdefault("song_import", {}).update(terminalSustains=alignment["terminalSustains"],
                                                       adjustmentsFile="import/sustain-adjustments.json")
    if cut_ending:
        if source_path is None:
            raise ImportFailure("unsupported_score", "Ending omissions require the retained original tab.")
        _write_json(package / "import/ending-omissions.json", {**alignment["recordingEnd"], "notes": ending_omissions})
        _write_json(package / "import/recording-sync.json", alignment["recordingSync"])
        manifest.setdefault("song_import", {}).update(recordingEnd=alignment["recordingEnd"],
                    endingOmissionsFile="import/ending-omissions.json", recordingSyncFile="import/recording-sync.json")
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
            **({"hybridLead": hybrid_summary} if hybrid_summary else {}),
            "warnings": list(validation.warnings) + ([f"Completed with omitted notes: {len(omissions['notes'])} unsupported high-fret or connected slide events are not displayed or scored. Original tab retained. Staff notation for affected arrangements is retained in the source only."]
                                                       if omissions and omissions["notes"] else [])}

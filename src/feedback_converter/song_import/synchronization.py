"""Validated recording timing supplied for one exact Songsterr revision/video.

This module consumes a captured response only; it never makes requests. Source
timing is separate from the independent audio matcher and its confidence gates.
"""
from __future__ import annotations

from bisect import bisect_right
import hashlib
import json
import math
import re

from .audio import ImportFailure

VERSION = "songsterr-video-points-v1"
MAX_MEASURES = 20_000
_EPSILON = 1e-8


def _unavailable(reason: str, **details):
    raise ImportFailure("source_sync_unavailable", "Songsterr recording timing could not be verified; automatic matching is required.",
                        {"sourceSyncReason": reason, **details})


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _identifier(value):
    text = str(value) if not isinstance(value, bool) else ""
    return text if re.fullmatch(r"[1-9]\d{0,11}", text) else None


def _segment(alignment: dict, time: float):
    anchors = alignment.get("anchors")
    if not isinstance(anchors, list) or len(anchors) < 2 or not _number(time):
        raise ImportFailure("alignment_failed", "The recording timing map is invalid.")
    try:
        first, last = float(anchors[0]["score"]), float(anchors[-1]["score"])
        if time < first - _EPSILON or time > last + _EPSILON:
            raise ImportFailure("alignment_failed", "The recording timing map does not cover this score position.")
        position = min(last, max(first, time))
        index = min(len(anchors) - 2, max(0, bisect_right(anchors, position, key=lambda point: point["score"]) - 1))
        left, right = anchors[index], anchors[index + 1]
        s0, s1, a0, a1 = (float(left["score"]), float(right["score"]), float(left["audio"]), float(right["audio"]))
        if not all(math.isfinite(value) for value in (s0, s1, a0, a1)) or s1 <= s0 or a1 <= a0:
            raise ValueError()
        return position, s0, s1, a0, a1
    except ImportFailure:
        raise
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise ImportFailure("alignment_failed", "The recording timing map is invalid.") from exc


def _mapped_value(alignment: dict, time: float) -> float:
    position, s0, s1, a0, a1 = _segment(alignment, time)
    return a0 + (position - s0) * (a1 - a0) / (s1 - s0)


def map_source_time(alignment: dict, time: float, *, allow_negative: bool = False) -> float:
    value = _mapped_value(alignment, time)
    if not math.isfinite(value) or (value < 0 and not allow_negative):
        raise ImportFailure("alignment_failed", "The recording timing would omit the beginning of a playable note.")
    return round(value, 6)


def source_time_scale(alignment: dict, time: float) -> float:
    _, s0, s1, a0, a1 = _segment(alignment, time)
    return (a1 - a0) / (s1 - s0)


def align_from_songsterr(performance: dict, audio: dict, synchronization: dict | None,
                        metadata: dict) -> dict:
    """Return verified source timing or a typed reason for matcher fallback."""
    if not all(isinstance(value, dict) for value in (performance, audio, metadata)):
        _unavailable("invalid_input")
    if not isinstance(synchronization, dict) or synchronization.get("status") != "done":
        provider_reason = synchronization.get("reasonCode") if isinstance(synchronization, dict) else None
        allowed_reasons = {"unavailable", "restricted", "rate_limited", "timeout", "network_error", "identity_mismatch",
                           "invalid_response", "invalid_points", "ambiguous", "too_large", "unsupported_audio"}
        _unavailable("missing", **({"sourceSyncProviderReason": provider_reason} if provider_reason in allowed_reasons else {}))
    if synchronization.get("version") != 1 or synchronization.get("source") != "songsterr-video-points":
        _unavailable("unsupported_format")
    if synchronization.get("feature") not in (None, "alternative"):
        _unavailable("not_full_mix")
    song_id, revision_id = _identifier(metadata.get("songId")), _identifier(metadata.get("revisionId"))
    if not song_id or not revision_id or metadata.get("approval") != "approved":
        _unavailable("unapproved_revision")
    source = performance.get("source") or {}
    if (_identifier(synchronization.get("songId")) != song_id
            or _identifier(synchronization.get("revisionId")) != revision_id
            or _identifier(source.get("songId")) != song_id
            or _identifier(source.get("revisionId")) != revision_id):
        _unavailable("revision_mismatch")
    recording = audio.get("source") or {}
    video_id = synchronization.get("videoId")
    if (not isinstance(video_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{11}", video_id)
            or recording.get("kind") != "youtube" or recording.get("videoId") != video_id):
        _unavailable("recording_mismatch")
    duration = audio.get("duration")
    if not _number(duration) or duration <= 0:
        _unavailable("invalid_audio_duration")
    timeline = performance.get("scoreTimeline") or {}
    measures = timeline.get("measures")
    if timeline.get("version") != 1 or not isinstance(measures, list) or not 1 <= len(measures) <= MAX_MEASURES:
        _unavailable("missing_score_coordinates")
    if timeline.get("maxRepeatDepth", 0) > 1:
        _unavailable("unverified_nested_repeats")
    if timeline.get("hasRepeats") and timeline.get("hasWithinBarTempoChanges"):
        _unavailable("unverified_repeat_tempo_inheritance")
    if timeline.get("hasAlternateEndings") and not timeline.get("hasRepeats"):
        _unavailable("unverified_alternate_endings")
    if timeline.get("hasMultiBarAlternateEndings"):
        _unavailable("unverified_multibar_endings")
    points = synchronization.get("points")
    if not isinstance(points, list) or not all(_number(value) for value in points):
        _unavailable("invalid_points")
    supplied_points = list(points)
    inferred_terminal = len(points) == len(measures) and len(points) >= 2
    if inferred_terminal:
        # Songsterr's public player repeats the last interval when the final
        # progression boundary is absent. Admit that documented rule for one
        # missing terminal boundary only, with explicit provenance and bounds.
        points = [*points, points[-1] + points[-1] - points[-2]]
    if len(points) != len(measures) + 1:
        _unavailable("point_count_mismatch", sourceSyncPointCount=len(points), sourceSyncMeasureCount=len(measures))
    if any(right <= left for left, right in zip(points, points[1:])):
        _unavailable("non_increasing_points")
    if points[-1] > duration + 0.05:
        _unavailable("terminal_boundary_outside_recording")
    anchors = []
    previous_end, previous_quarter = 0.0, 0.0
    tempo_points = timeline.get("tempoPoints") or []
    if (not isinstance(tempo_points, list) or not tempo_points
            or any(not isinstance(point, dict) or not _number(point.get("time")) or not _number(point.get("bpm"))
                   or point["bpm"] <= 0 for point in tempo_points)
            or tempo_points[0]["time"] != 0
            or any(b["time"] <= a["time"] for a, b in zip(tempo_points, tempo_points[1:]))):
        _unavailable("invalid_score_tempos")
    for index, measure in enumerate(measures):
        start, end = measure.get("start"), measure.get("end")
        quarter, quarters = measure.get("quarter"), measure.get("quarters")
        if (not all(_number(value) for value in (start, end, quarter, quarters))
                or measure.get("index") != index
                or not isinstance(measure.get("writtenIndex"), int)
                or not 0 <= measure["writtenIndex"] < timeline.get("writtenMeasureCount", 0)
                or abs(start - previous_end) > _EPSILON or abs(quarter - previous_quarter) > _EPSILON
                or end <= start or quarters <= 0):
            _unavailable("invalid_score_coordinates")
        anchors.append({"score": start, "audio": float(points[index]), "quarter": quarter})
        previous_end, previous_quarter = end, quarter + quarters
    if not _number(performance.get("duration")) or abs(performance["duration"] - previous_end) > _EPSILON:
        _unavailable("incomplete_score_coordinates")
    anchors.append({"score": previous_end, "audio": float(points[-1]), "quarter": previous_quarter})
    result = {"status": "validated", "method": VERSION, "mapping": "piecewise-linear", "experimental": True,
              "anchors": anchors}
    # The official player interpolates in tempo-integrated score seconds.
    # Preserve within-bar tempo events and introduce effective tempo changes
    # at recording-map boundaries, rather than replacing each bar by one BPM.
    if any(point["time"] >= previous_end for point in tempo_points):
        _unavailable("invalid_score_tempos")
    tempo_times = [point["time"] for point in tempo_points]
    change_times = sorted({point["score"] for point in anchors[:-1]} | set(tempo_times))
    tempos = []
    for time in change_times:
        bpm = tempo_points[bisect_right(tempo_times, time) - 1]["bpm"] / source_time_scale(result, time)
        tempos.append({"time": map_source_time(result, time, allow_negative=True), "bpm": bpm})
    # Keep a tempo at the recording origin when source anchors describe silent
    # pre-roll. No playable note or sustain is ever clipped into the recording.
    mapped_tempos = []
    for tempo in tempos:
        item = {**tempo, "time": max(0.0, tempo["time"])}
        if mapped_tempos and mapped_tempos[-1]["time"] == item["time"]:
            mapped_tempos[-1] = item
        elif not mapped_tempos or not math.isclose(mapped_tempos[-1]["bpm"], item["bpm"], rel_tol=1e-12):
            mapped_tempos.append(item)
    canonical = {key: synchronization.get(key) for key in ("version", "source", "songId", "revisionId", "videoId", "status", "feature", "points")}
    canonical.update(songId=song_id, revisionId=revision_id, points=[float(value) for value in supplied_points])
    digest = hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    result.update({"tempos": mapped_tempos,
              "provenance": {"source": "songsterr-video-points", "version": 1, "mapHash": digest,
                             "songId": song_id, "revisionId": revision_id, "videoId": video_id,
                             "terminalBoundary": "songsterr-last-interval" if inferred_terminal else "explicit"},
              "diagnostics": {"sourceSyncPointCount": len(supplied_points), "sourceSyncMeasureCount": len(measures),
                              "sourceSyncNegativePreroll": points[0] < 0,
                              "sourceSyncInferredTerminalBoundary": inferred_terminal}})
    checked = 0
    for track in performance.get("tracks", []):
        notes = [(note, note.get("t")) for note in track.get("notes", [])]
        notes += [(note, note.get("t", chord.get("t"))) for chord in track.get("chords", []) for note in chord.get("notes", [])]
        for note, start in notes:
            sustain = note.get("sus", 0)
            if not _number(start) or not _number(sustain) or sustain < 0:
                _unavailable("invalid_note_timing")
            try:
                mapped_start = _mapped_value(result, start)
                mapped_end = _mapped_value(result, start + sustain)
            except ImportFailure:
                _unavailable("note_outside_map")
            if mapped_start < 0:
                _unavailable("negative_note_time")
            if mapped_end > duration + 0.05:
                _unavailable("note_outside_recording")
            for point in note.get("bnv", []):
                if (not isinstance(point, dict) or not _number(point.get("t")) or not _number(point.get("v"))
                        or point["t"] < 0 or point["t"] > sustain + _EPSILON):
                    _unavailable("invalid_bend_timing")
            checked += 1
    if not checked:
        _unavailable("no_playable_notes")
    return result

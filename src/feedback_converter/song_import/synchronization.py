"""Validated recording timing supplied for one exact Songsterr revision/video.

This module consumes a captured response only; it never makes requests. Source
timing is separate from the independent audio matcher and its confidence gates.
"""
from __future__ import annotations

from bisect import bisect_right
from collections import Counter
import hashlib
import json
import math
import re

from .audio import ImportFailure
from .revision_policy import valid_revision_selection
from .terminal_sustains import policy_for, trim_held_note

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


def _opening_strum_policy(performance):
    """Bound the public first-interval extension by actual authored members.

    The archive verifier independently derives these members from raw source.
    A general negative-time allowance would also accept unsupported pickups.
    """
    actual = Counter()
    for track in performance.get('tracks', []):
        notes = [(n, n.get('t')) for n in track.get('notes', [])]
        notes += [(n, n.get('t', c.get('t'))) for c in track.get('chords', []) for n in c.get('notes', [])]
        for note, time in notes:
            if _number(time) and time < 0:
                actual[(track['id'], time, note['s'], note['f'])] += 1
    if not actual:
        return None
    if performance.get('source', {}).get('format') != 'songsterr':
        _unavailable('unverified_opening_strum')
    members, groups = Counter(), []
    for group in performance.get('strumEvidence', []):
        early = [n for n in group.get('notes', []) if _number(n.get('t')) and n['t'] < 0]
        if not early:
            continue
        if (group.get('occurrence') != 1 or not _number(group.get('time')) or group['time'] < 0
                or group.get('direction') not in ('up', 'down') or group.get('kind') not in ('brush', 'arpeggio')
                or not re.fullmatch(r'songsterr:\d+:0:\d+:\d+', group.get('sourceId', ''))):
            _unavailable('unverified_opening_strum')
        groups.append({'trackId': group['trackId'], 'sourceId': group['sourceId']})
        for note in early:
            members[(group['trackId'], note['t'], note['s'], note['f'])] += 1
    if set(actual) != set(members):
        _unavailable('unverified_opening_strum')
    return {'version': 1, 'rule': 'authored-opening-strum-first-interval',
            'scoreStart': min(key[1] for key in actual), 'noteCount': len(actual),
            'groups': sorted(groups, key=lambda g: (g['trackId'], g['sourceId']))}


def _segment(alignment: dict, time: float):
    anchors = alignment.get("anchors")
    if not isinstance(anchors, list) or len(anchors) < 2 or not _number(time):
        raise ImportFailure("alignment_failed", "The recording timing map is invalid.")
    try:
        first, last = float(anchors[0]["score"]), float(anchors[-1]["score"])
        opening = alignment.get('provenance', {}).get('openingStrum')
        lower = first
        if opening is not None:
            if (not isinstance(opening, dict) or alignment.get('method') != VERSION or first != 0 or opening.get('version') != 1
                    or opening.get('rule') != 'authored-opening-strum-first-interval'
                    or not _number(opening.get('scoreStart')) or opening['scoreStart'] >= 0):
                raise ValueError()
            lower = opening['scoreStart']
        if time < lower - _EPSILON or time > last + _EPSILON:
            raise ImportFailure("alignment_failed", "The recording timing map does not cover this score position.")
        position = min(last, max(lower, time))
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


def _verify_source_clock(performance, measures, *, external_tempos=False):
    """Re-read retained source; counts or a producer capability flag are insufficient."""
    from .verify_source import songsterr
    from .verify_timeline import visits, Clock
    try:
        envelope = performance['sourceScore']
        if envelope.get('format') != 'songsterr':
            raise ValueError('missing source navigation')
        source = songsterr(envelope['document'])
        for key in ('songId', 'revisionId'):
            if source.identity.get(key) != str(performance['source'].get(key)):
                raise ValueError('source identity mismatch')
        order = visits(source)
        if external_tempos:
            # Qualification is deliberately narrower than all repeat/tempo
            # combinations: disjoint constant-tempo repeat regions only.
            stack, intervals = [], []
            for index, bar in enumerate(source.bars):
                if bar.endings:
                    raise ValueError('alternate ending tempo inheritance')
                if bar.repeat_start:
                    stack.append(index)
                if bar.repeat_count:
                    start = stack.pop() if stack else 0
                    if any(source.bars[i].tempos for i in range(start, index + 1)):
                        raise ValueError('tempo event inside repeat')
                    if any(start <= hi and lo <= index for lo, hi in intervals):
                        raise ValueError('nested repeat tempo inheritance')
                    intervals.append((start, index))
            if stack or not intervals:
                raise ValueError('missing repeat structure')
        if len(order) != len(measures):
            raise ValueError('order length mismatch')
        clock, counts = Clock(source, order), {}
        for index, (written, actual) in enumerate(zip(order, measures)):
            counts[written] = counts.get(written, 0) + 1
            quarter = clock.measure_starts[index]
            length = source.bars[written].length
            if actual.get('writtenIndex') != written or actual.get('visit') != counts[written]:
                raise ValueError('written occurrence mismatch')
            expected = {'quarter': float(quarter), 'quarters': float(length),
                        'start': float(clock.at(quarter)), 'end': float(clock.at(quarter + length))}
            if any(not _number(actual.get(k)) or abs(actual[k] - v) > _EPSILON for k, v in expected.items()):
                raise ValueError('source coordinates mismatch')
        if external_tempos:
            points = performance['scoreTimeline']['tempoPoints']
            if len(points) != len(clock.positions):
                raise ValueError('source tempo count mismatch')
            for point, quarter, bpm in zip(points, clock.positions, clock.bpms):
                expected = {'quarter': float(quarter), 'time': float(clock.at(quarter)), 'bpm': float(bpm)}
                if any(not _number(point.get(k)) or abs(point[k] - v) > _EPSILON for k, v in expected.items()):
                    raise ValueError('source tempo mismatch')
    except (ValueError, KeyError, TypeError, AttributeError, IndexError):
        _unavailable('unverified_repeat_tempo_inheritance' if external_tempos else 'unverified_multibar_endings')


def align_from_songsterr(performance: dict, audio: dict, synchronization: dict | None,
                        metadata: dict, *, allow_ending_candidate: bool = False, allow_padding_candidate: bool = False,
                        _opening_probe: bool = False) -> dict:
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
    if not song_id or not revision_id or not valid_revision_selection(metadata):
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
    repeat_tempo_policy = None
    if timeline.get("hasRepeats") and timeline.get("hasWithinBarTempoChanges"):
        _verify_source_clock(performance, measures, external_tempos=True)
        repeat_tempo_policy = 'constant-repeat-with-external-tempos-v1'
    if timeline.get("hasAlternateEndings") and not timeline.get("hasRepeats"):
        _unavailable("unverified_alternate_endings")
    if timeline.get("hasMultiBarAlternateEndings"):
        _verify_source_clock(performance, measures)
    points = synchronization.get("points")
    if not isinstance(points, list) or not all(_number(value) for value in points):
        _unavailable("invalid_points")
    supplied_points = list(points)
    if any(right <= left for left, right in zip(points, points[1:])):
        _unavailable("non_increasing_points")
    # The public player's score/video interpolation uses the shorter boundary
    # array (gk), in both directions. Only an unused suffix can be omitted;
    # never choose an interior point or stretch the score to the extra endpoint.
    unused_count = max(0, len(points) - len(measures) - 1)
    points = points[:len(measures) + 1]
    inferred_count = max(0, len(measures) + 1 - len(points)) if len(points) >= 2 else 0
    inferred_terminal = inferred_count > 0
    if inferred_terminal:
        # The public player repeats the last interval until every progression
        # boundary exists (video/putPointsIntoPlayer). These are trailing
        # boundaries only, never replacements for absent interior entries.
        # Attacks and pitch gestures must still fit the actual recording;
        # held tails use the separately recorded final-sustain policy below.
        interval = points[-1] - points[-2]
        points = [*points, *(points[-1] + interval * i for i in range(1, inferred_count + 1))]
    if len(points) != len(measures) + 1:
        _unavailable("point_count_mismatch", sourceSyncPointCount=len(points), sourceSyncMeasureCount=len(measures))
    if any(right <= left for left, right in zip(points, points[1:])):
        _unavailable("non_increasing_points")
    silent_terminal = points[-1] > duration + 0.05
    # Supplied or source-rule trailing boundaries can include written silence
    # past the recording. Every playable attack, sustain and bend is checked
    # below; shortening a held tail requires an explicit adjustment record.
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
    opening_strum = _opening_strum_policy(performance)
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
    # pre-roll. A playable attack before the recording is never shifted into it.
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
              "sourceTiming": canonical,
              "provenance": {"source": "songsterr-video-points", "version": 1, "mapHash": digest,
                             "songId": song_id, "revisionId": revision_id, "videoId": video_id,
                             "boundaryPolicy": {"version": 1, "rule": "songsterr-shared-boundary-prefix",
                                                "supplied": len(supplied_points), "used": len(points),
                                                "unusedTrailing": unused_count, "inferredTrailing": inferred_count},
                             "terminalBoundary": "songsterr-last-interval" if inferred_terminal else "explicit",
                             **({'repeatTempoPolicy': repeat_tempo_policy} if repeat_tempo_policy else {}),
                             **({"openingStrum": opening_strum} if opening_strum else {}),
                             **({"terminalBeyondAudio": "silent_notation_only"} if silent_terminal else {})},
              "diagnostics": {"sourceSyncPointCount": len(supplied_points), "sourceSyncMeasureCount": len(measures),
                              "sourceSyncNegativePreroll": points[0] < 0,
                              "sourceSyncInferredTerminalBoundary": inferred_terminal,
                              "sourceSyncInferredBoundaryCount": inferred_count,
                              "sourceSyncUnusedTrailingPointCount": unused_count,
                              "sourceSyncSilentTerminalExtension": silent_terminal}})
    checked, trimmed, late, slide_trims = 0, 0, 0, 0
    if _opening_probe:
        # Structural evidence only. It can never be handed to the builder as
        # an accepted map; repair must rerun ALL playable-event checks.
        result['status']='needs_opening_check'
        return result
    from .ending_padding import candidate
    padding = candidate(performance, result, audio) if allow_padding_candidate else None
    from .ending_cutoff import boundary_policy
    ending_boundary = boundary_policy(result, duration) if allow_ending_candidate else None
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
            if mapped_start >= duration:
                # A candidate cannot omit anything until acoustic evidence
                # supports the timing across the available recording.
                if ending_boundary is None:
                    _unavailable("note_outside_recording", mappedNoteEnd=mapped_end, audioDuration=duration)
                late += 1
                checked += 1
                continue
            for point in note.get("bnv", []):
                if (not isinstance(point, dict) or not _number(point.get("t")) or not _number(point.get("v"))
                        or point["t"] < 0 or point["t"] > sustain + _EPSILON):
                    _unavailable("invalid_bend_timing")
            if mapped_end > duration + 0.0000011 and padding is None:
                mapped = {**note, "t": round(mapped_start, 6), "sus": round(mapped_end - mapped_start, 6)}
                if 'slide_interval' in note:
                    mapped['slide_interval'] = {k: round(_mapped_value(result, start + v) - mapped_start, 6)
                                                for k, v in note['slide_interval'].items()}
                if 'whammy' in note:
                    from ..whammy import retime_whammy
                    try:
                        mapped['whammy'] = retime_whammy(note['whammy'], start, sustain, mapped_start,
                                                        lambda t: _mapped_value(result, t),
                                                        [p['score'] for p in result.get('anchors', [])])
                    except ValueError:
                        _unavailable('invalid_whammy_timing')
                for key, coordinates in (("bnv", ("t",)), ("slide_out_marks", ("start", "end")), ("vibrato_marks", ("start", "end")), ("slide_in_marks", ("time",))):
                    if key in mapped:
                        mapped[key] = [{**p, **{k: round(_mapped_value(result, start + p[k]) - mapped_start, 6) for k in coordinates}}
                                       for p in mapped[key]]
                try:
                    # This is only a candidate. Neither the builder nor verifier
                    # permits a slide cutoff until the acoustic check authorizes it.
                    slide_candidate = ending_boundary is not None
                    _, adjustment = trim_held_note(mapped, duration, allow_directional_slides=slide_candidate)
                except ImportFailure as exc:
                    _unavailable("terminal_technique_outside_recording", mappedNoteEnd=mapped_end, audioDuration=duration, message=str(exc))
                trimmed += adjustment is not None
                slide_trims += len((adjustment or {}).get("slideOuts", []))
            checked += 1
    if not checked:
        _unavailable("no_playable_notes")
    if padding is not None:
        result.update(status='needs_padding_check', paddingCandidate=padding)
        return result
    if trimmed:
        result["terminalSustains"] = policy_for(duration)
        result["diagnostics"]["shortenedFinalSustains"] = trimmed
        if silent_terminal:
            result["provenance"]["terminalBeyondAudio"] = "recorded_sustain_adjustments"
    if late or slide_trims:
        result["status"] = "needs_ending_check"
        result["endingCandidate"] = {"lateNotes": late, "audioDuration": duration}
        if slide_trims:
            result["endingCandidate"]["directionalSlides"] = slide_trims
    return result

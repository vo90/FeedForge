"""Prove gaps in recording-clock windows contain no playable source activity.

Coordinates are original recording seconds, excluding preparation silence.
No acoustic verdict is inferred from absence of charted material.
"""
import math
from bisect import bisect_right

POLICY = 'source-activity-coverage-v1'
KEY = 'sourceActivityCoverage'
EPSILON = 0.0000011  # The chart/archive stores times to six decimal places.


def gaps(report):
    duration, origin = report.get('audioDuration'), report.get('analysisOriginSeconds', 0)
    if (any(type(v) not in (int, float) or not math.isfinite(v) for v in (duration, origin))
            or not 0 <= origin <= 2 or duration <= origin):
        return None
    duration -= origin
    windows = report.get('windows')
    if not isinstance(windows, list) or not windows:
        return None
    result, end, previous_start = [], 0.0, -1.0
    for row in windows:
        if not isinstance(row, dict):
            return None
        left, right = row.get('start'), row.get('end')
        if (any(type(v) not in (int, float) or not math.isfinite(v) for v in (left, right))
                or not 0 <= left < right <= duration + EPSILON
                or left <= previous_start or right <= end):
            return None
        if left > end + EPSILON:
            result.append({'start': end, 'end': left})
        end, previous_start = min(duration, right), left
    if end < duration - EPSILON:
        result.append({'start': end, 'end': duration})
    return result


def receipt(report, tracks):
    """Use all source events, including mutes, ghost notes and held sustains.

    Only portions inside the actual recording matter here; ending omissions remain
    subject to their separate policy and source verification.
    """
    missing = gaps(report)
    if missing is None or not isinstance(tracks, list) or not tracks:
        return None
    origin = report.get('analysisOriginSeconds', 0)
    duration = report['audioDuration'] - origin
    gap_ends = [gap['end'] for gap in missing]
    active = False
    for track in tracks:
        if not isinstance(track, dict) or not isinstance(track.get('events'), list):
            return None
        for note in track['events']:
            if not isinstance(note, dict):
                return None
            start, end = note.get('t'), note.get('end')
            if (any(type(v) not in (int, float) or not math.isfinite(v) for v in (start, end))
                    or end < start or start < origin - EPSILON):
                return None
            start, end = max(0, start - origin), end - origin
            if start >= duration:
                continue
            active = True
            index = bisect_right(gap_ends, start + EPSILON)
            if index < len(missing):
                gap = missing[index]
                # Half-open intervals: an attack at the first covered instant
                # is covered, but even a zero-length attack inside a gap is not.
                if (gap['start'] - EPSILON <= start < gap['end'] - EPSILON
                        or start < gap['end'] - EPSILON and end > gap['start'] + EPSILON):
                    return None
    if not active:
        return None
    return {'version': 1, 'policy': POLICY, 'timeDomain': 'original_recording_seconds',
            'unchartedRanges': missing}


def declared(report):
    """Validate shape only. Producer and independent verifier must prove it."""
    missing = gaps(report)
    return (missing is not None and bool(missing) and report.get(KEY) == {
        'version': 1, 'policy': POLICY, 'timeDomain': 'original_recording_seconds',
        'unchartedRanges': missing})


def attach(report, tracks):
    proof = receipt(report, tracks)
    return {**report, KEY: proof} if proof and proof['unchartedRanges'] else report

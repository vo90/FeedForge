"""Recording-end projection with verified or explicitly warned source timing."""
from __future__ import annotations

from bisect import bisect_right
import math

from .alignment import map_time
from .audio import ImportFailure
from .recording_sync import assess, digest, PREVIOUS_VERSION, LEGACY_VERSION

POLICY = "cut-at-recording-end-v1"
SUFFIX_POLICY = "cut-at-recording-end-v2"


def boundary_policy(alignment, duration):
    """Describe a suffix cutoff, never a timing correction or audio verdict.

    Preserve the established receipt for its original final-bar cases. Longer
    endings identify the actual cutoff measure and still require acoustic
    evidence across the available recording before any notes can be omitted.
    """
    anchors = alignment.get('anchors', [])
    if (type(duration) not in (int, float) or not math.isfinite(duration)
            or not isinstance(anchors, list) or len(anchors) < 2
            or any(not isinstance(a, dict) or any(type(a.get(k)) not in (int, float)
                or not math.isfinite(a[k]) for k in ('score', 'audio')) for a in anchors)
            or any(b['audio'] <= a['audio'] or b['score'] <= a['score'] for a,b in zip(anchors, anchors[1:]))
            or not anchors[0]['audio'] < duration < anchors[-1]['audio']):
        return None
    if (anchors[-2]['audio'] < duration and anchors[-1]['audio']-duration <= 3.0
            and anchors[-1]['audio']-anchors[-2]['audio'] <= 8.0):
        return {'version': 1, 'policy': POLICY, 'audioDuration': duration,
                'finalMeasureStart': anchors[-2]['audio']}
    index = bisect_right([a['audio'] for a in anchors], duration)-1
    return {'version': 2, 'policy': SUFFIX_POLICY, 'audioDuration': duration,
            'cutoffMeasureIndex': index, 'cutoffMeasureStart': anchors[index]['audio'],
            'mappedScoreEnd': anchors[-1]['audio']}


def mapped_tracks(performance, alignment):
    tracks = []
    for track in performance["tracks"]:
        notes = list(track.get("notes", [])) + [{**n, "t": n.get("t", c["t"])}
                  for c in track.get("chords", []) for n in c.get("notes", [])]
        events = []
        for n in notes:
            events.append({"t": map_time(alignment, n["t"]), "end": map_time(alignment, n["t"] + n.get("sus", 0)),
                           "midi": track["tuning"][n["s"]] + track.get("capo", 0) + n["f"] if n["f"] != 127 else None,
                           "effects": {k: v for k, v in n.items() if k not in {"t", "sus", "s", "f", "source_ids"}}})
        tracks.append({"id": track["id"], "instrument": track["instrument"], "events": events})
    return tracks


def authorize(performance, audio, alignment):
    policy = boundary_policy(alignment, audio['duration'])
    if policy is None:
        raise ImportFailure('alignment_failed', 'The recording ending is outside a valid source timeline.')
    offset=alignment.get('preparation',{}).get('seconds',0)
    report = assess(mapped_tracks(performance, alignment), audio["path"], audio["duration"], alignment["provenance"]["mapHash"],
                    **({'analysis_origin':offset} if offset else {}), clock_version=PREVIOUS_VERSION)
    alignment["recordingSync"] = report
    warning = None
    if report["status"] != "supported":
        from .cutoff_warning import acceptance, matches_recording
        warning = acceptance(report, alignment) if matches_recording(alignment, audio.get('source', {})) else None
        if warning is None:
            raise ImportFailure("alignment_failed", "The earlier tab could not be matched reliably to this recording, so its ending was not shortened.",
                                {"sourceSyncReason": "ending_sync_inconclusive", "recordingSync": report})
    alignment["recordingEnd"] = {**policy, "syncEvidenceHash": digest(report),
                                **({'timingWarning': warning} if warning else {})}
    if (alignment.get("endingCandidate") or {}).get("directionalSlides"):
        from .terminal_sustains import slides_policy_for
        alignment["terminalSlides"] = slides_policy_for(audio["duration"])
    alignment.pop("endingCandidate", None)
    alignment["status"] = "validated"
    alignment["provenance"]["terminalBeyondAudio"] = "recorded_ending_cutoff"
    return alignment


def allowed(alignment, duration):
    policy, report = alignment.get("recordingEnd"), alignment.get("recordingSync")
    boundary = boundary_policy(alignment, duration)
    if not isinstance(policy, dict) or not isinstance(report, dict):
        return False
    from .cutoff_warning import acceptance
    warning = acceptance(report, alignment) if report.get('status') != 'supported' else None
    expected = {**(boundary or {}), 'syncEvidenceHash': digest(report),
                **({'timingWarning': warning} if warning else {})}
    return (alignment.get("status") == "validated" and alignment.get("method") == "songsterr-video-points-v1"
            and alignment.get("mapping") == "piecewise-linear"
            and boundary is not None and policy == expected
            and report.get("version") in (PREVIOUS_VERSION, LEGACY_VERSION)
            and (report.get("status") == "supported" or warning is not None)
            and report.get("audioDuration") == duration and report.get("mapHash") == alignment.get("provenance", {}).get("mapHash"))


def omitted_note(track_id, note, alignment, start):
    left = map_time(alignment, start)
    return {"trackId": track_id, "string": note["s"], "fret": note["f"], "audioStart": left,
            "originalDuration": round(map_time(alignment, start + note.get("sus", 0)) - left, 6)}

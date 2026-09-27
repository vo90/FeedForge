"""Recording-end omission requires both source identity and acoustic evidence."""
from __future__ import annotations

from .alignment import map_time
from .audio import ImportFailure
from .recording_sync import assess, digest, VERSION

POLICY = "cut-at-recording-end-v1"


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
    offset=alignment.get('preparation',{}).get('seconds',0)
    report = assess(mapped_tracks(performance, alignment), audio["path"], audio["duration"], alignment["provenance"]["mapHash"],
                    **({'analysis_origin':offset} if offset else {}))
    alignment["recordingSync"] = report
    if report["status"] != "supported":
        raise ImportFailure("alignment_failed", "The earlier tab could not be matched reliably to this recording, so its ending was not shortened.",
                            {"sourceSyncReason": "ending_sync_inconclusive", "recordingSync": report})
    alignment["recordingEnd"] = {"version": 1, "policy": POLICY, "audioDuration": audio["duration"],
                                 "finalMeasureStart": alignment["anchors"][-2]["audio"], "syncEvidenceHash": digest(report)}
    if (alignment.get("endingCandidate") or {}).get("directionalSlides"):
        from .terminal_sustains import slides_policy_for
        alignment["terminalSlides"] = slides_policy_for(audio["duration"])
    alignment.pop("endingCandidate", None)
    alignment["status"] = "validated"
    alignment["provenance"]["terminalBeyondAudio"] = "recorded_ending_cutoff"
    return alignment


def allowed(alignment, duration):
    policy, report = alignment.get("recordingEnd"), alignment.get("recordingSync")
    anchors = alignment.get("anchors", [])
    if len(anchors) < 2 or not all(isinstance(a, dict) and isinstance(a.get("audio"), (int, float)) for a in anchors):
        return False
    return (isinstance(policy, dict) and isinstance(report, dict)
            and alignment.get("status") == "validated" and alignment.get("method") == "songsterr-video-points-v1"
            and alignment.get("mapping") == "piecewise-linear"
            and anchors[-2]["audio"] < duration < anchors[-1]["audio"]
            and anchors[-1]["audio"] - duration <= 3.0
            and anchors[-1]["audio"] - anchors[-2]["audio"] <= 8.0
            and policy == {"version": 1, "policy": POLICY, "audioDuration": duration,
                           "finalMeasureStart": alignment["anchors"][-2]["audio"], "syncEvidenceHash": digest(report)}
            and report.get("version") == VERSION and report.get("status") == "supported"
            and report.get("audioDuration") == duration and report.get("mapHash") == alignment.get("provenance", {}).get("mapHash"))


def omitted_note(track_id, note, alignment, start):
    left = map_time(alignment, start)
    return {"trackId": track_id, "string": note["s"], "fret": note["f"], "audioStart": left,
            "originalDuration": round(map_time(alignment, start + note.get("sus", 0)) - left, 6)}

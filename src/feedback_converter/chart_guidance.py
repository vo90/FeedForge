"""Conservative, display-only completion of final fretted arrangements.

No musical event is rewritten. Positions describe visibility/hand-position
suggestions, not a claim that a wide chord has one ergonomic fingering.
"""
from bisect import bisect_right
from copy import deepcopy
import hashlib
import json
from .generated_hand_positions import POSITION_POLICY, PREVIOUS_POSITION_POLICIES, _members, generate_positions

POLICY = "feedforge-chart-guidance-v2"
FIELDS = ("anchors", "handshapes")
MUSIC = ("tuning", "capo", "centOffset", "notes", "chords", "templates")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False, separators=(",", ":")).encode()).hexdigest()


def music_digest(chart):
    return digest({key: chart[key] for key in MUSIC if key in chart})


def _handshapes(chart, attacks):
    shapes = []
    eligible = 0
    # Motion, harmonic contacts, linked attacks and unpitched strikes require
    # richer authored intent. Leave these spans to the note/technique renderer.
    complex_keys = {"sl", "slu", "bn", "bnv", "hm", "hp", "hn", "harmonic_target", "harmonic_changes",
                    "slide_out", "slide_out_marks", "slide_in_marks", "pick_scrape_marks", "ln", "ho", "po", "mt", "fhm"}
    for chord in chart.get("chords", []):
        notes = chord.get("notes", [])
        tid, time = chord.get("id"), chord["t"]
        if type(tid) is not int or not 0 <= tid < len(chart.get("templates", [])):
            raise ValueError("Guidance requires valid chord templates.")
        if len(notes) < 2 or any(n.get("t", time) != time or n["f"] < 0 or
                                 any(k in n and n[k] is not False and n[k] is not None for k in complex_keys) for n in notes):
            continue
        template = chart["templates"][tid]
        name, display = str(template.get("name", "")).lower(), str(template.get("displayName", "")).lower()
        if template.get("arp") or template.get("arpeggio") or "-arp" in display or name.endswith("(arp)") or " arpeggio" in name:
            continue
        frets = [-1] * len(chart["tuning"])
        for note in notes:
            frets[note["s"]] = note["f"]
        if frets != template.get("frets") or len({n["s"] for n in notes}) != len(notes):
            continue
        end = min(time + n.get("sus", 0) for n in notes)
        for note in notes:
            times = attacks[note["s"]]
            i = bisect_right(times, time)
            if i < len(times):
                end = min(end, times[i])
        if end <= time:
            continue
        eligible += 1
        if shapes and shapes[-1]["chord_id"] == tid and shapes[-1]["end_time"] == time:
            shapes[-1]["end_time"] = end
        else:
            shapes.append({"chord_id": tid, "start_time": time, "end_time": end, "arp": False})
    return shapes, eligible


def finalize(chart, *, regenerate=False, window=None):
    """Fill absent guidance, or explicitly rebuild previously owned fields.

    Authored fields and manually edited generated fields are never overwritten.
    Call after composition/retiming, before difficulty and package hashes.
    """
    previous = chart.get("ext", {}).get("chartGuidance")
    input_hash = music_digest(chart)
    if previous is not None:
        if (previous.get("policy") != POLICY or previous.get("sourceAuthored") is not False
                or previous.get("positionPolicy") not in (*PREVIOUS_POSITION_POLICIES, POSITION_POLICY)):
            raise ValueError("Unknown chart guidance provenance; preserve it for review.")
        fields = previous.get("fields")
        if not isinstance(fields, list) or not fields or any(k not in FIELDS for k in fields) or len(fields) != len(set(fields)):
            raise ValueError("Invalid chart guidance ownership.")
        if previous.get("guidanceSha256") != digest({k: chart.get(k) for k in fields}):
            raise ValueError("Generated chart guidance was edited; preserve it for review.")
        if (previous.get("musicSha256") == input_hash and previous.get("window") == (list(window) if window else None)
                and (not regenerate or previous.get("positionPolicy") == POSITION_POLICY)):
            return deepcopy(previous)
        if not regenerate:
            raise ValueError("The arrangement changed; regenerate its guidance explicitly.")
    else:
        fields = [k for k in FIELDS if not chart.get(k)]
    if not fields:
        return None
    _, attacks = _members(chart)
    additions = {}
    if "anchors" in fields:
        additions["anchors"] = generate_positions(chart)
    if "handshapes" in fields:
        additions["handshapes"], eligible = _handshapes(chart, attacks)
    if window is not None:
        left, right = window
        if not 0 <= left < right:
            raise ValueError("Invalid guidance phrase window.")
        if "anchors" in additions:
            anchors = additions["anchors"]
            initial = next(a for a in reversed(anchors) if a["time"] <= left)
            additions["anchors"] = [{**initial, "time": left}, *[a for a in anchors if left < a["time"] < right]]
        if "handshapes" in additions:
            additions["handshapes"] = [{**h, "end_time": min(right, h["end_time"])}
                                      for h in additions["handshapes"] if left <= h["start_time"] < right]
    receipt = {"policy": POLICY, "positionPolicy": POSITION_POLICY, "sourceAuthored": False, "fields": fields,
               "musicSha256": input_hash, "guidanceSha256": digest(additions),
               "slidePolicy": "timed-known-slides", "legatoPolicy": "compact-explicit-hopo", "fingeringAssessed": False}
    if "anchors" in fields:
        receipt["wideAnchorCount"] = sum(a["width"] > 4 for a in additions["anchors"])
    if "handshapes" in fields:
        receipt["handshapeEligibleChords"] = eligible
        receipt["handshapeSkippedChords"] = len(chart.get("chords", [])) - eligible
    if window is not None:
        receipt["window"] = list(window)
    # Prepare everything before mutating the caller; musical keys are untouched.
    chart.update(additions)
    chart.setdefault("ext", {})["chartGuidance"] = receipt
    return deepcopy(receipt)

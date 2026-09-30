"""Conservative, display-only completion of final fretted arrangements.

No musical event is rewritten. Positions describe visibility/hand-position
suggestions, not a claim that a wide chord has one ergonomic fingering.
"""
from bisect import bisect_right
from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
import math

POLICY = "feedforge-chart-guidance-v2"
FIELDS = ("anchors", "handshapes")
MUSIC = ("tuning", "capo", "centOffset", "notes", "chords", "templates")
MAX_FRET = 24


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False, separators=(",", ":")).encode()).hexdigest()


def music_digest(chart):
    return digest({key: chart[key] for key in MUSIC if key in chart})


def _members(chart):
    members = [(n["t"], n) for n in chart.get("notes", [])]
    members += [(n.get("t", c["t"]), n) for c in chart.get("chords", []) for n in c.get("notes", [])]
    members.sort(key=lambda row: (row[0], row[1]["s"]))
    attacks = defaultdict(set)
    for time, note in members:
        attacks[note["s"]].add(time)
    attacks = {s: sorted(times) for s, times in attacks.items()}
    rows = []
    for time, note in members:
        times = attacks[note["s"]]
        index = bisect_right(times, time)
        end = time + max(0, note.get("sus", 0))
        if index < len(times):
            # Guidance ends at a new pick on this string. The serialized
            # sustain itself is never shortened or otherwise repaired.
            end = min(end, times[index])
        rows.append((time, end, note))
    return rows, attacks


def _frets(note):
    fret = note["f"]
    if fret < 0 or note.get("mt") and (fret == 127 or note.get("pick_scrape_marks") or _plain_dead(note)):
        return ()  # Unpitched strike; do not invent a location for it.
    values = [fret]
    if note.get("hm") and type(note.get("hn")) in (int, float):
        values.append(note["hn"])  # Natural harmonic contact, not its pitch interval.
    for key in ("sl", "slu"):
        target = note.get(key)
        if type(target) in (int, float) and target >= 0:
            values.append(target)
    # Reserve the complete known slide corridor for its duration. This is
    # deliberately conservative across consumers with different slide easing.
    result = set()
    for value in values:
        if not math.isfinite(value) or value < 0 or value > MAX_FRET:
            raise ValueError("Guidance requires the supported 0–24 fret projection first.")
        if value > 0:
            result.update((max(1, math.floor(value)), math.ceil(value)))
    return tuple(sorted(result))


def _plain_dead(note):
    """A dead strike's stored editor fret is not a hand-position target."""
    return note.get("mt") is True and (type(note.get("f")) is int and (0 <= note["f"] <= MAX_FRET or note["f"] == 127)) and not any(note.get(k) for k in (
        "slide_out", "slide_out_marks", "slide_in_marks", "pick_scrape_marks", "bn", "bnv",
        "vb", "tr", "whammy", "hm", "hp", "harmonic_target", "harmonic_changes", "ho", "po", "ln"
    )) and not any(type(note.get(k)) in (int, float) and note[k] >= 0 for k in ("sl", "slu", "su"))


def _positions(rows):
    # Build instant releases explicitly so zero-duration notes cannot linger.
    changes = defaultdict(lambda: {"add": [], "remove": [], "instant": [], "attack": False})
    for start, end, note in rows:
        frets = _frets(note)
        changes[start]["attack"] = True
        changes[start]["add"].append(frets)
        if end > start:
            changes[end]["remove"].append(frets)
        else:
            changes[start]["instant"].append(frets)
    active, demands = Counter(), []
    for time, change in sorted(changes.items()):
        for frets in change["remove"]:
            active.subtract(frets)
        for frets in change["add"]:
            active.update(frets)
        occupied = [f for f, count in active.items() if count > 0]
        demands.append((time, min(occupied, default=0), max(occupied, default=0), change["attack"]))
        for frets in change["instant"]:
            active.subtract(frets)

    def choose(i, width, prior=None):
        time, low, high, _ = demands[i]
        candidates = range(max(1, high - width + 1), min(low, MAX_FRET - width + 1) + 1)
        # A bounded half-second preview breaks ties; it never excludes the
        # current sounding material, nor widens the lane for future notes.
        preview = []
        for row in demands[i + 1:i + 17]:
            if row[0] > time + .5:
                break
            if row[1] > 0 and row[3]:
                preview.append(row)
        def cost(fret):
            misses = sum(not (fret <= lo and hi < fret + width) for _, lo, hi, _ in preview)
            return misses, abs(fret - (prior if prior is not None else low)), -fret
        return {"fret": min(candidates, key=cost), "width": width}

    first = next((i for i, row in enumerate(demands) if row[1] > 0), None)
    current = choose(first, max(4, demands[first][2] - demands[first][1] + 1)) if first is not None else {"fret": 1, "width": 4}
    result = [{"time": 0.0, **current}]
    narrow = None
    for i, (time, low, high, attack) in enumerate(demands):
        if not low:
            continue
        width = max(4, high - low + 1)
        covers = current["fret"] <= low and high < current["fret"] + current["width"]
        shrink = False
        if covers and current["width"] > 4 and width == 4 and attack:
            if narrow is None or time - narrow["last"] > 1 or max(high, narrow["high"]) - min(low, narrow["low"]) >= 4:
                narrow = {"start": time, "last": time, "low": low, "high": high, "count": 1}
            else:
                narrow.update(last=time, low=min(low, narrow["low"]), high=max(high, narrow["high"]), count=narrow["count"] + 1)
            shrink = narrow["count"] >= 3 and time - narrow["start"] >= 1
        elif width > 4:
            narrow = None
        if covers and not shrink:
            continue
        current = choose(i, width, current["fret"])
        narrow = None
        row = {"time": time, **current}
        if result[-1]["time"] == time:
            result[-1] = row
        else:
            result.append(row)
    return result


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
        if previous.get("policy") != POLICY or previous.get("sourceAuthored") is not False:
            raise ValueError("Unknown chart guidance provenance; preserve it for review.")
        fields = previous.get("fields")
        if not isinstance(fields, list) or not fields or any(k not in FIELDS for k in fields) or len(fields) != len(set(fields)):
            raise ValueError("Invalid chart guidance ownership.")
        if previous.get("guidanceSha256") != digest({k: chart.get(k) for k in fields}):
            raise ValueError("Generated chart guidance was edited; preserve it for review.")
        if previous.get("musicSha256") == input_hash and previous.get("window") == (list(window) if window else None):
            return deepcopy(previous)
        if not regenerate:
            raise ValueError("The arrangement changed; regenerate its guidance explicitly.")
    else:
        fields = [k for k in FIELDS if not chart.get(k)]
    if not fields:
        return None
    rows, attacks = _members(chart)
    additions = {}
    if "anchors" in fields:
        additions["anchors"] = _positions(rows)
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
    receipt = {"policy": POLICY, "sourceAuthored": False, "fields": fields,
               "musicSha256": input_hash, "guidanceSha256": digest(additions),
               "slidePolicy": "known-corridor", "fingeringAssessed": False}
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

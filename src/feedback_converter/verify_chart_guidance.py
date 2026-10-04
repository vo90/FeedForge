"""Independent constraints for generated chart guidance, not generator replay.

Raw musical events remain the responsibility of source/Hybrid verification.
This checker does not import the finalizer or its event/range/shape helpers.
"""
from bisect import bisect_right
from collections import defaultdict
import hashlib
import heapq
import json
import math

POLICY = "feedforge-chart-guidance-v2"


def _slide_fits(note, start, time, anchor):
    """Check continuous slide geometry directly, independently of anchor cuts."""
    key = next(k for k in ("sl", "slu") if type(note.get(k)) in (int, float) and note[k] >= 0)
    def wire(f, logarithmic):
        raw = 330 * (1 - 2 ** (-f / 12))
        return (raw if f <= 12 else 165 + (raw - 165) * 1.1) if logarithmic else 255.75 * f / 24
    for logarithmic in (False, True):
        def centre(f):
            return -2 if f <= 0 else (wire(f - 1, logarithmic) + wire(f, logarithmic)) / 2
        origin, destination = centre(int(note["f"])), centre(int(note[key]))
        positions = []
        # Anchors serialize to microseconds. Permit boundary rounding only;
        # never borrow a whole future fret cell or the original slide corridor.
        for offset in (-.000001, .000001):
            p = min(1., max(0., (time + offset - start) / note["sus"]))
            weight = math.sin(p * math.pi / 2) ** 3 if key == "sl" else 1 - math.cos(p * math.pi / 2)
            positions.append(origin + (destination - origin) * weight)
        low = -2 if anchor["fret"] == 1 else wire(anchor["fret"] - 1, logarithmic)
        high = wire(anchor["fret"] + anchor["width"] - 1, logarithmic)
        if max(positions) < low - 1e-8 or min(positions) > high + 1e-8:
            return False
    return True


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    allow_nan=False, separators=(",", ":")).encode()).hexdigest()


def validate(chart, *, window=None, duration=None):
    """Return bounded diagnostics; [] means these presentation checks passed."""
    errors = []
    def fail(message):
        if len(errors) < 40:
            errors.append(message)
    def number(value):
        return type(value) in (int, float) and math.isfinite(value)
    try:
        proof = chart.get("ext", {}).get("chartGuidance")
        if not isinstance(proof, dict) or proof.get("policy") != POLICY or proof.get("sourceAuthored") is not False:
            return ["Missing or unsupported generated guidance provenance."]
        timed_slides = proof.get("positionPolicy") == "slide-follow-v1"
        if proof.get("positionPolicy") not in (None, "chord-local-v1", "open-preparation-v1", "open-preparation-v2", "slide-follow-v1"):
            return ["Unsupported generated position policy."]
        fields = proof.get("fields")
        if not isinstance(fields, list) or not fields or len(set(fields)) != len(fields) or any(k not in {"anchors", "handshapes"} for k in fields):
            return ["Invalid generated guidance ownership."]
        musical = {k: chart[k] for k in ("tuning", "capo", "centOffset", "notes", "chords", "templates") if k in chart}
        if proof.get("musicSha256") != _hash(musical):
            fail("Guidance describes different musical events.")
        if proof.get("guidanceSha256") != _hash({k: chart.get(k) for k in fields}):
            fail("Generated guidance content changed.")
        if (proof.get("slidePolicy") != ("timed-known-slides" if timed_slides else "known-corridor") or proof.get("fingeringAssessed") is not False
                or proof.get("legatoPolicy") != "compact-explicit-hopo"):
            fail("Unsupported guidance interpretation.")
        if proof.get("window") != (list(window) if window is not None else None):
            fail("Guidance phrase window changed.")
        left, right = window if window is not None else (0, duration if duration is not None else math.inf)
        members = [(n["t"], n) for n in chart.get("notes", [])]
        members.extend((n.get("t", c["t"]), n) for c in chart.get("chords", []) for n in c.get("notes", []))
        times = defaultdict(set)
        for start, note in members:
            if not number(start) or start < left or start >= right or type(note.get("s")) is not int or not 0 <= note["s"] < len(chart["tuning"]):
                fail("Invalid musical event coordinates for guidance.")
                return errors
            times[note["s"]].add(start)
        times = {s: sorted(values) for s, values in times.items()}
        # Independent end-time heap; release old strings before checking the
        # next attack, then include all simultaneous and zero-length attacks.
        events, motions = defaultdict(list), {}
        for index, (start, note) in enumerate(members):
            sustain = note.get("sus", 0)
            moving = any(note.get(k) for k in ("slide_out", "slide_out_marks", "slide_in_marks", "pick_scrape_marks",
                "bn", "bnv", "vb", "tr", "whammy", "hm", "hp", "harmonic_target", "harmonic_changes", "ho", "po", "ln"))
            moving |= any(number(note.get(k)) and note[k] >= 0 for k in ("sl", "slu", "su"))
            unpitched = note.get("mt") is True and (note["f"] == 127 or bool(note.get("pick_scrape_marks")) or (not moving and type(note["f"]) is int and 0 <= note["f"] <= 24))
            if not number(sustain) or sustain < 0 or not number(note["f"]) or not (-1 <= note["f"] <= 24 or unpitched):
                return errors + ["Unsupported musical fret/duration for guidance."]
            end = min(right, start + sustain)
            subsequent = times[note["s"]]
            pos = bisect_right(subsequent, start)
            if pos < len(subsequent):
                end = min(end, subsequent[pos])
            needed = []
            if note["f"] >= 0 and not unpitched:
                values = [note["f"]]
                if note.get("hm") and number(note.get("hn")):
                    values.append(note["hn"])
                values.extend(note[k] for k in ("sl", "slu") if number(note.get(k)) and note[k] >= 0)
                for value in values:
                    if value > 24:
                        return errors + ["Slide or harmonic contact outside the supported neck."]
                    if value > 0:
                        needed += [max(1, math.floor(value)), math.ceil(value)]
                target = next((note[k] for k in ("sl", "slu") if number(note.get(k)) and note[k] >= 0), None)
                if (timed_slides and target is not None and note["f"] > 0 and note["f"] == int(note["f"])
                        and sustain > 0 and end > start
                        and int(target) != note["f"] and not (note.get("hm") and number(note.get("hn")))):
                    motions[index] = (note, start)
                    needed = []
            # Compare occupancy on the archive's microsecond clock. Keep this
            # independent of the generator and leave the musical data intact.
            events[round(start, 6)].append((round(end, 6), index, needed))
            events[round(end, 6)]  # A release also ends an interval to verify.
        anchors = chart.get("anchors", [])
        if "anchors" in fields:
            if not anchors or anchors[0].get("time") != left:
                return errors + ["Generated anchors must cover the start of the chart/phrase."]
            previous = None
            for a in anchors:
                if set(a) != {"time", "fret", "width"} or not number(a["time"]) or not left <= a["time"] < right:
                    return errors + ["Invalid generated anchor time/fields."]
                if type(a["fret"]) is not int or type(a["width"]) is not int or a["fret"] < 1 or a["width"] < 4 or a["fret"] + a["width"] - 1 > 24:
                    return errors + ["Generated anchor has invalid inclusive fret bounds."]
                if previous and (a["time"] <= previous["time"] or (a["fret"], a["width"]) == (previous["fret"], previous["width"])):
                    fail("Generated anchors are not ordered and coalesced.")
                previous = a
            anchor_times = [a["time"] for a in anchors]
            if type(proof.get("wideAnchorCount")) is not int or proof["wideAnchorCount"] != sum(a["width"] > 4 for a in anchors):
                fail("Wide-position diagnostic differs from generated anchors.")
            checkpoints = sorted(set(events) | {round(t, 6) for t in anchor_times})
            sounding, expiry = {}, []
            for ci, t in enumerate(checkpoints):
                while expiry and expiry[0][0] <= t:
                    _, index = heapq.heappop(expiry)
                    sounding.pop(index, None)
                instant, instant_ids = [], []
                for end, index, frets in events.get(t, []):
                    instant.extend(frets)
                    instant_ids.append(index)
                    if end > t:
                        sounding[index] = frets
                        heapq.heappush(expiry, (end, index))
                a = anchors[bisect_right(anchor_times, t) - 1]
                needed = instant + [f for values in sounding.values() for f in values]
                if any(f < a["fret"] or f >= a["fret"] + a["width"] for f in needed):
                    fail(f"Generated lane excludes an active fret at {t}.")
                through = checkpoints[ci + 1] if ci + 1 < len(checkpoints) else t
                for index in sounding.keys() | set(instant_ids):
                    if index not in motions:
                        continue
                    note, start = motions[index]
                    # Each path is monotone between onset, release and anchor
                    # boundaries, so its two endpoints cover the whole interval.
                    if not all(_slide_fits(note, start, sample, a) for sample in (t, through)):
                        fail(f"Generated lane excludes an active slide at {t}.")
        if "handshapes" in fields:
            permitted = defaultdict(list)
            for chord in chart.get("chords", []):
                start, tid = chord["t"], chord.get("id")
                notes = chord.get("notes", [])
                if type(tid) is not int or not 0 <= tid < len(chart.get("templates", [])):
                    return errors + ["Invalid handshape source template."]
                template = chart["templates"][tid]
                name, display = str(template.get("name", "")).lower(), str(template.get("displayName", "")).lower()
                prohibited = ("sl", "slu", "bn", "bnv", "hm", "hp", "hn", "harmonic_target", "harmonic_changes",
                              "slide_out", "slide_out_marks", "slide_in_marks", "pick_scrape_marks", "ln", "ho", "po", "mt", "fhm")
                if (len(notes) < 2 or template.get("arp") or template.get("arpeggio") or "-arp" in display or name.endswith("(arp)") or " arpeggio" in name or
                    any(n.get("t", start) != start or n["f"] < 0 or any(k in n and n[k] is not False and n[k] is not None for k in prohibited) for n in notes)):
                    continue
                frets = [-1] * len(chart["tuning"])
                for n in notes:
                    frets[n["s"]] = n["f"]
                if template.get("frets") != frets or len({n["s"] for n in notes}) != len(notes):
                    continue
                end = min(right, *(start + n.get("sus", 0) for n in notes))
                for n in notes:
                    i = bisect_right(times[n["s"]], start)
                    if i < len(times[n["s"]]):
                        end = min(end, times[n["s"]][i])
                if end > start:
                    permitted[tid].append((start, end))
            actual = defaultdict(list)
            eligible = sum(len(spans) for spans in permitted.values())
            if (type(proof.get("handshapeEligibleChords")) is not int or type(proof.get("handshapeSkippedChords")) is not int or
                proof["handshapeEligibleChords"] != eligible or proof["handshapeSkippedChords"] != len(chart.get("chords", [])) - eligible):
                fail("Handshape coverage diagnostic differs from supported chord holds.")
            permitted_starts = {tid: {a for a, _ in spans} for tid, spans in permitted.items()}
            last_start = -1
            for h in chart.get("handshapes", []):
                if (set(h) != {"chord_id", "start_time", "end_time", "arp"} or h["arp"] is not False or
                    type(h["chord_id"]) is not int or not number(h["start_time"]) or not number(h["end_time"]) or
                    not left <= h["start_time"] < h["end_time"] <= right or h["start_time"] < last_start):
                    return errors + ["Invalid generated handshape fields/span."]
                actual[h["chord_id"]].append((h["start_time"], h["end_time"]))
                if h["start_time"] not in permitted_starts.get(h["chord_id"], set()):
                    fail("A handshape starts without its real chord attack.")
                last_start = h["start_time"]
            def unions(groups):
                result = {}
                for tid, spans in groups.items():
                    merged = []
                    for a, b in sorted(spans):
                        if merged and a < merged[-1][1]:
                            fail("Overlapping generated or source handshape spans.")
                        if merged and a == merged[-1][1]:
                            merged[-1] = (merged[-1][0], b)
                        else:
                            merged.append((a, b))
                    result[tid] = merged
                return result
            if unions(permitted) != unions(actual):
                fail("Handshapes do not cover exactly the supported chord holds.")
    except (ValueError, TypeError, KeyError, IndexError, AttributeError, OverflowError) as exc:
        fail("Malformed generated guidance: " + str(exc))
    return errors


def validate_arrangement(chart, *, duration=None):
    errors = validate(chart, duration=duration)
    for pi, phrase in enumerate(chart.get("phrases", [])):
        for li, level in enumerate(phrase.get("levels", [])):
            if not level.get("ext", {}).get("chartGuidance"):
                errors.append(f"Missing generated difficulty guidance at phrase {pi}, level {li}.")
                continue
            context = {k: chart[k] for k in ("tuning", "capo", "centOffset", "templates") if k in chart}
            context.update(level)
            errors.extend(f"phrase {pi}, level {li}: {e}" for e in validate(context, window=(phrase["start_time"], phrase["end_time"])))
    return errors[:100]

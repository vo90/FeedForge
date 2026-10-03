"""Pure generated hand-position policy shared by FeedForge and FeedBack.

Canonical source: FeedForge src/feedback_converter/generated_hand_positions.py.
Keep the game compatibility copy byte-identical; no application dependencies.
Derived from FeedForge chart_guidance.py, MIT License, Copyright (c) 2026 Balki97.
See generated_hand_positions.LICENSE for the full license.
"""
from bisect import bisect_right
from collections import Counter, defaultdict
import math

MAX_FRET = 24
POSITION_POLICY = "chord-local-v1"


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


def _connected_frets(rows):
    """Reserve a compact, explicit HO/PO phrase at its initiating attack.

    Ambiguous voices and rests cannot imply a connection. The bounded preview
    is presentation only; it neither links attacks nor changes their timing.
    """
    by_string = defaultdict(lambda: defaultdict(list))
    for row in rows:
        by_string[row[2]["s"]][row[0]].append(row)
    reservations = defaultdict(set)
    def plain_pitch(note):
        return (type(note.get("f")) is int and 0 <= note["f"] <= MAX_FRET
                and not any(note.get(k) for k in ("mt", "bn", "bnv", "hm", "hp", "harmonic_target",
                    "harmonic_changes", "whammy", "slide_out", "slide_out_marks", "slide_in_marks", "pick_scrape_marks"))
                and not any(type(note.get(k)) in (int, float) and note[k] >= 0 for k in ("sl", "slu", "su")))
    for groups in by_string.values():
        sequence = [group[0] if len(group) == 1 else None for _, group in sorted(groups.items())]
        for i, source in enumerate(sequence[:-1]):
            if source is None or not plain_pitch(source[2]):
                continue
            start, _, note = source
            frets = set(_frets(note))
            prior = source
            for target in sequence[i + 1:i + 17]:
                if target is None or target[0] - start > .5 or not plain_pitch(target[2]):
                    break
                time, _, n = target
                hammer, pull = n.get("ho") is True, n.get("po") is True
                if (hammer == pull or (hammer and n["f"] <= prior[2]["f"])
                        or (pull and n["f"] >= prior[2]["f"])
                        or abs(prior[1] - time) > .001000001):
                    break
                extended = frets | set(_frets(n))
                if extended and max(extended) - min(extended) >= 4:
                    break
                frets = extended
                reservations[start].update(frets)
                prior = target
    return reservations


def _positions(rows, chords=()):
    # Guidance compares serialized microsecond boundaries, not binary float
    # addition artefacts (38.1525 + .1625 can exceed 38.315). This is a local
    # occupancy clock only: source attacks, sustains and scoring stay intact.
    rows = [(round(start, 6), round(end, 6), note) for start, end, note in rows]
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
    # A chord establishes a position even when an older, wider anchor covers it.
    # Keep its preference through releases of overlapping notes; all-open and
    # unpitched-only shapes have no fret preference.
    chord_positions = {}
    for chord in chords:
        frets = [n["f"] for n in chord.get("notes", []) if n["f"] > 0 and _frets(n)]
        if frets:
            chord_positions[round(chord["t"], 6)] = min(frets)
    connected = _connected_frets(rows)
    active, demands = Counter(), []
    for time, change in sorted(changes.items()):
        for frets in change["remove"]:
            active.subtract(frets)
        for frets in change["add"]:
            active.update(frets)
        occupied = [f for f, count in active.items() if count > 0]
        prepared = occupied + list(connected.get(time, ()))
        # Never widen the lane or exclude sounding material for anticipation.
        if prepared and max(prepared) - min(prepared) < 4:
            occupied = prepared
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
    preferred = None
    for i, (time, low, high, attack) in enumerate(demands):
        if time in chord_positions:
            preferred = chord_positions[time]
        elif attack:
            preferred = None  # A single-note phrase resumes stable positioning.
        if not low:
            if not attack or current["width"] <= 4:
                continue
            # An open-only passage needs context, not a previous slide's span.
            next_position = {"fret": min(current["fret"], MAX_FRET - 3), "width": 4}
        else:
            width = max(4, high - low + 1)
            covers = current["fret"] <= low and high < current["fret"] + current["width"]
            if preferred is not None:
                # Clamp the chord preference only to cover simultaneous material,
                # compact explicit legato, and the physical end of the neck.
                fret = max(1, high - width + 1,
                           min(preferred, low, MAX_FRET - width + 1))
                next_position = {"fret": fret, "width": width}
            elif covers and current["width"] == width:
                continue
            else:
                # Excess width belongs to the sounding demand, not a future run of
                # three attacks. Release events can narrow an overlapping hold too.
                next_position = choose(i, width, current["fret"])
        if next_position == current:
            continue
        current = next_position
        row = {"time": time, **current}
        if result[-1]["time"] == time:
            result[-1] = row
        elif len(result) == 1 and first is not None and i == first:
            result[0] = {"time": 0.0, **current}
        else:
            result.append(row)
    return result


def generate_positions(chart):
    """Return fresh display anchors without changing the input chart."""
    return _positions(_members(chart)[0], chart.get("chords", ()))

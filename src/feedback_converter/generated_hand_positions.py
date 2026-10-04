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
POSITION_POLICY = "positionless-preparation-v1"
PREVIOUS_POSITION_POLICIES = (None, "chord-local-v1", "open-preparation-v1", "open-preparation-v2", "slide-follow-v1")


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
        "slide_out", "slideOut", "slide_out_marks", "slide_in_marks", "pick_scrape_marks", "bn", "bnv", "bt",
        "vb", "vibrato", "vibrato_marks", "whammy", "hm", "hp", "hn", "harmonic_target",
        "harmonic_changes", "harmonic_alias", "ho", "po", "ln"
    )) and not any(type(note.get(k)) in (int, float) and note[k] >= 0 for k in ("sl", "slu", "su"))


def _position_spans(start, end, note):
    """Occupied cells along a known slide, bounded by neck size, not duration.

    The highway eases between fret centres in world space. Cover both uniform
    and logarithmic spacing, so changing the display preference cannot strand
    the gem outside its generated lane. Other strings still reserve their own
    cells. Unknown slide directions never become invented fret destinations.
    """
    frets = _frets(note)  # Validate even fields hidden by slide precedence.
    key = next((k for k in ("sl", "slu")
                if type(note.get(k)) in (int, float) and note[k] >= 0), None)
    if (not frets or key is None or note["f"] <= 0 or note["f"] != int(note["f"]) or end <= start
            or note.get("sus", 0) <= 0
            or note.get("hm") and type(note.get("hn")) in (int, float)):
        return [(start, end, frets)]
    target = int(note[key])  # Same precedence/integer target as slideTrailEnd.
    if target == note["f"]:
        return [(start, end, frets)]

    motion_start, motion_duration = start, note['sus']
    interval = note.get('slide_interval')
    if key == 'sl' and interval is not None:
        if (not isinstance(interval, dict) or set(interval) != {'start', 'end'}
                or any(type(interval[k]) not in (int, float) or not math.isfinite(interval[k]) for k in interval)
                or not 0 <= interval['start'] < interval['end'] <= note['sus'] + .0000011):
            raise ValueError('Guidance requires a valid targeted slide interval.')
        motion_start += interval['start']
        motion_duration = interval['end'] - interval['start']

    # Units of renderer K: FRET_SCALE/K = 330; open centre = -2*K.
    logarithmic = [330 * (1 - 2 ** (-f / 12)) for f in range(MAX_FRET + 1)]
    logarithmic = [x if f <= 12 else logarithmic[12] + (x - logarithmic[12]) * 1.1
                   for f, x in enumerate(logarithmic)]
    uniform = [logarithmic[-1] * f / MAX_FRET for f in range(MAX_FRET + 1)]
    paths, cuts = [], {start, end}
    for wires in (uniform, logarithmic):
        def centre(f):
            return -2 if f <= 0 else (wires[f - 1] + wires[f]) / 2
        origin, destination = centre(int(note["f"])), centre(target)
        paths.append((wires, origin, destination))
        for wire in wires:
            weight = (wire - origin) / (destination - origin)
            if not 0 < weight < 1:
                continue
            progress = (math.asin(weight ** (1 / 3)) * 2 / math.pi if key == "sl"
                        else 1 - math.asin(1 - weight) * 2 / math.pi)
            time = round(motion_start + motion_duration * progress, 6)
            if start < time < end:
                cuts.add(time)
    cuts = sorted(cuts)
    spans = []
    for left, right in zip(cuts, cuts[1:]):
        p = min(1., max(0., ((left + right) / 2 - motion_start) / motion_duration))
        weight = math.sin(p * math.pi / 2) ** 3 if key == "sl" else 1 - math.cos(p * math.pi / 2)
        cells = tuple(sorted({max(1, min(MAX_FRET, bisect_right(wires, origin + (destination - origin) * weight)))
                              for wires, origin, destination in paths}))
        if spans and spans[-1][2] == cells:
            spans[-1] = (spans[-1][0], right, cells)
        else:
            spans.append((left, right, cells))
    return spans


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
        changes[start]["attack"] = True
        for left, right, frets in _position_spans(start, end, note):
            changes[left]["add"].append(frets)
            if right > left:
                changes[right]["remove"].append(frets)
            else:
                changes[left]["instant"].append(frets)
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


def _positionless_pickup(note):
    """An attack may prepare a lane only when no contact or gesture pins it.

    Dynamics, picking style, tremolo and muting an open string need no fret.
    Open whammy motion is also positionless; camera focus is decided separately.
    Dead strikes may carry an editor placeholder instead of a played fret.
    Fret-hand mute alone never turns a positive fret into a dead strike.
    """
    context = ("bn", "bnv", "bt", "vb", "vibrato", "vibrato_marks",
               "hm", "hp", "hn", "harmonic_target", "harmonic_changes", "harmonic_alias",
               "ho", "po", "ln", "slide_in_marks", "slide_out", "slideOut",
               "slide_out_marks", "pick_scrape_marks")
    if any(note.get(k) for k in context) or any(
            type(note.get(k)) in (int, float) and note[k] >= 0 for k in ("sl", "slu", "su")):
        return False
    return type(note.get("f")) is int and (note["f"] == 0 or _plain_dead(note))


def _linked_pickup_context(rows):
    """Protect both ends when only one note declares the connected gesture."""
    by_string = defaultdict(lambda: defaultdict(list))
    for row in rows:
        by_string[row[2]["s"]][round(row[0], 6)].append(row)
    linked = set()
    for groups in by_string.values():
        sequence = sorted(groups.items())
        for (_, source), (time, targets) in zip(sequence, sequence[1:]):
            if any(n.get("ln") and abs(end - time) <= .001000001 for _, end, n in source):
                linked.update(id(n) for _, _, n in targets)
            if any(n.get("ho") or n.get("po") for _, _, n in targets):
                linked.update(id(n) for _, end, n in source if abs(end - time) <= .001000001)
    return linked


def _prepare_opens(rows, positions, beats=()):
    """Walk backward from each fretted destination through positionless play.

    Resolve the destination first, so its fingering context agrees with the
    open bar. Never borrow a remote position or hide a sounding fretted note.
    The run has no total-duration limit. At most one local beat of silence
    connects neighbouring attacks (0.5 s without beats, capped at 1 s). This
    admits detached picks without borrowing a lane across a substantial rest.
    """
    groups = defaultdict(list)
    for start, end, note in rows:
        groups[round(start, 6)].append((round(end, 6), note))
    linked = _linked_pickup_context(rows)

    def eligible_pickup(note):
        return id(note) not in linked and _positionless_pickup(note)

    beat_times = sorted({b["time"] for b in beats if isinstance(b, dict)
                         and type(b.get("time")) in (int, float) and math.isfinite(b["time"])})

    def allowed_gap(time):
        if len(beat_times) < 2:
            return .5
        index = max(0, min(len(beat_times) - 2, bisect_right(beat_times, time) - 1))
        return max(.1, min(1., beat_times[index + 1] - beat_times[index]))

    attacks, blocked_until, open_until = [], -math.inf, -math.inf
    for time, members in sorted(groups.items()):
        plain = all(eligible_pickup(n) for _, n in members)
        open_until = max(open_until, max((end for end, n in members if eligible_pickup(n)), default=-math.inf))
        attacks.append((time, max(open_until, max(end for end, _ in members)),
                        plain and blocked_until <= time,
                        any(_frets(n) for _, n in members)))
        blocked_until = max(blocked_until, max((end for end, n in members if not eligible_pickup(n)), default=-math.inf))

    times = [a["time"] for a in positions]
    spans = []
    for index, (time, _, _, fretted) in enumerate(attacks):
        if not fretted:
            continue
        start, following = None, time
        for prior in range(index - 1, -1, -1):
            onset, end, eligible, _ = attacks[prior]
            if not eligible or following - end > allowed_gap(onset) + .001:
                break
            start = following = onset
        if start is not None:
            target = positions[max(0, bisect_right(times, time) - 1)]
            spans.append((start, time, target))

    if not spans:
        return positions
    shifted, index = [], 0
    for anchor in positions:
        while index < len(spans) and spans[index][1] <= anchor["time"]:
            index += 1
        if index < len(spans) and spans[index][0] <= anchor["time"] < spans[index][1]:
            continue
        shifted.append(anchor)
    shifted.extend({**target, "time": start} for start, _, target in spans)
    result = []
    for anchor in sorted(shifted, key=lambda a: a["time"]):
        if result and all(result[-1][k] == anchor[k] for k in ("fret", "width")):
            continue
        result.append(anchor)
    return result


def generate_positions(chart):
    """Return fresh display anchors without changing the input chart."""
    rows = _members(chart)[0]
    return _prepare_opens(rows, _positions(rows, chart.get("chords", ())), chart.get("beats", ()))

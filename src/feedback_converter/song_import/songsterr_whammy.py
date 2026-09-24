"""Authored bar expression, independent of fretting-hand bends.

The pinned Songsterr performer uses 50 tone units per semitone and either
sixtieths or precise percentages across the sounding segment. Its two-point
form is a preset/hold: the second value is also installed at position zero.
"""
from copy import deepcopy

from .model import ScoreImportError, rational


def source_whammy(beat):
    bar = beat.get("tremoloBar")
    vibrato = beat.get("vibratoWithTremoloBar")
    if vibrato not in (None, "slight", "wide"):
        raise ScoreImportError("Unknown bar-vibrato intensity.")
    if bar is None and vibrato is None:
        return None
    result = {"curve": [], "vibrato": vibrato}
    if bar is not None:
        if not isinstance(bar, dict) or set(bar) - {"points", "tone", "extend"}:
            raise ScoreImportError("Unknown tremolo-bar structure.")
        # The captured performer reads points only; the editor's `extend`
        # flag remains in the archived source and must not extend note timing.
        if "extend" in bar and not isinstance(bar["extend"], bool):
            raise ScoreImportError("Invalid tremolo-bar editor extension flag.")
        points = bar.get("points")
        if not isinstance(points, list) or not 1 <= len(points) <= 1024:
            raise ScoreImportError("Tremolo bar needs a bounded, nonempty curve.")
        if any(not isinstance(p, dict) or set(p) - {"position", "precisePosition", "tone"} for p in points):
            raise ScoreImportError("Unknown tremolo-bar point.")
        precise = ["precisePosition" in p for p in points]
        if any(precise) and not all(precise):
            raise ScoreImportError("Incomplete precise tremolo-bar coordinates.")
        previous = previous_coarse = -1
        for point in points:
            coarse = rational(point.get("position"), "bar position")
            position = rational(point["precisePosition"], "precise bar position") / 100 if all(precise) else coarse / 60
            tone = rational(point.get("tone"), "bar pitch") / 50
            if not 0 <= coarse <= 60 or coarse < previous_coarse or not 0 <= position <= 1 or position < previous or not -16 <= tone <= 8:
                raise ScoreImportError("Invalid or unordered tremolo-bar coordinates.")
            previous, previous_coarse = position, coarse
        # `tone` is an editor summary, not authoritative curve data. Validate
        # its shape but do not replace a curve endpoint with that summary.
        if "tone" in bar and not -16 <= rational(bar["tone"], "bar summary") / 50 <= 8:
            raise ScoreImportError("Invalid tremolo-bar summary.")
        normalized = deepcopy(points)
        if len(points) == 2 and points[0]["position"] == 0:
            normalized.insert(1, {**points[1], "position": 0, **({"precisePosition": 0} if all(precise) else {})})
        normalized.append({**points[-1], "position": 60, **({"precisePosition": 100} if all(precise) else {})})
        values = {}
        for point in normalized:
            position = rational(point["precisePosition"], "bar time") / 100 if all(precise) else rational(point["position"], "bar time") / 60
            values[position] = float(rational(point["tone"], "bar pitch") / 50)
        result["curve"] = list(values.items())
    return result


def append_segment(output, note, start, end, at, occurrence):
    """Retain each authored beat interval before tied notes are flattened."""
    onset = start + (note.attack_offset if not note.tie else 0)
    segments = output.get('whammy', {}).get('segments', [])
    previous = segments[-1] if note.tie and segments else None
    # The pinned performer's Ps resets to the normalized FIRST tone before a
    # tied continuation. A hidden tied note does not emit the ordinary attack
    # pitch reset. Make that held state explicit, rather than treating an
    # unmarked continuation as a fresh neutral attack.
    held = previous['curve'][0]['v'] if previous and previous['curve'] else 0
    if note.whammy is None and not held:
        return
    if end <= onset:
        raise ScoreImportError("Bar expression has no sounding interval.")
    origin = output["t"]
    left, right = at(onset) - origin, at(end) - origin
    if held and left > previous['end'] + 1e-9:
        segments.append({'start': previous['end'], 'end': left,
                         'source_id': previous['source_id'], 'group': previous['group'],
                         'curve': [{'t': previous['end'], 'v': held}, {'t': left, 'v': held}]})
    if note.whammy is None:
        segments.append({'start': left, 'end': right,
                         'source_id': previous['source_id'], 'group': previous['group'],
                         'curve': [{'t': left, 'v': held}, {'t': right, 'v': held}]})
        return
    segment = {"start": at(onset) - origin, "end": at(end) - origin,
               "source_id": note.beat_id, "group": f"{note.beat_id}@{occurrence}",
               "curve": [{"t": at(onset + (end - onset) * p) - origin, "v": v}
                         for p, v in note.whammy["curve"]]}
    if not segment['curve'] and held:
        segment['curve'] = [{'t': left, 'v': held}, {'t': right, 'v': held}]
    if note.whammy["vibrato"]:
        segment["vibrato"] = note.whammy["vibrato"]
    output.setdefault("whammy", {"version": 1, "policy": "optional", "segments": []})["segments"].append(segment)

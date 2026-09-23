"""Explicit tempo automation interpreted without inventing a performance."""
from bisect import bisect_right
from fractions import Fraction as F
from math import floor

from .model import ScoreImportError, integer, rational


def performed_tempos(automations, measures, events):
    """Expand established automation into the existing step-tempo clock."""
    if any(not 0 <= q < measures[bar].length or bpm <= 0 for (bar, q), bpm in events.items()):
        raise ScoreImportError("Invalid source tempo position or rate.")
    gradual = automations.get("gradualTempo", False)
    if not isinstance(gradual, bool):
        raise ScoreImportError("Invalid gradual-tempo enablement.")
    fermatas = automations.get("fermata") or []
    if not isinstance(fermatas, list):
        raise ScoreImportError("Invalid fermata automation.")
    if fermatas and any(t.get("type", 4) != 4 or t.get("dotted") for t in automations.get("tempo", [])):
        raise ScoreImportError("Fermata with non-quarter tempo units needs additional interpretation.")
    starts = [F(0)]
    for m in measures:
        starts.append(starts[-1] + m.length)
    by_time = {starts[bar] + position: bpm for (bar, position), bpm in events.items()}
    ramps = [t for t in automations.get("tempo", []) if t.get("linear")]
    if gradual and ramps:
        if fermatas or any(t.get("position") not in (None, 0, [0, 1]) for t in automations["tempo"]):
            raise ScoreImportError("Combined fermata/ramp or nonzero legacy tempo positions need additional interpretation.")
        linear = {starts[integer(t["measure"], "ramp measure")] for t in ramps}
        times = sorted(by_time)
        for left, right in zip(times, times[1:]):
            if right not in linear:
                continue
            steps = min(64, max(1, floor(right - left + F(1, 2))))
            first, last = floor(by_time[left]), floor(by_time[right])
            for i in range(1, steps):
                at = left + (right - left) * F(i, steps)
                by_time.setdefault(at, floor(first + (last - first) * F(i, steps) + F(1, 2)))
    occupied = []
    for f in fermatas:
        if not isinstance(f, dict) or set(f) - {"measure", "position", "type", "length"}:
            raise ScoreImportError("Unrecognized fermata automation.")
        bar = integer(f.get("measure"), "fermata measure")
        position = rational(f.get("position"), "fermata position") / 960
        length = rational(f.get("length"), "fermata length")
        if not 0 <= bar < len(measures) or not 0 <= position < measures[bar].length or not 0 <= length <= 1:
            raise ScoreImportError("Invalid explicit fermata coordinate or length.")
        if f.get("type") not in ("short", "medium", "long"):
            raise ScoreImportError("Unknown fermata type.")
        # The public player applies the bar's final explicit tempo; accept the
        # unambiguous start-only case rather than reinterpret conflicting marks.
        if any(b == bar and q != 0 for b, q in events):
            raise ScoreImportError("A fermata sharing a measure with tempo changes needs additional interpretation.")
        at = starts[bar] + position
        original = {starts[b] + q: bpm for (b, q), bpm in events.items()}
        prior = [t for t in original if t <= at]
        if not prior:
            raise ScoreImportError("Fermata has no explicit preceding tempo.")
        bpm = original[max(prior)]
        unit = F(4, measures[bar].denominator)
        fractional = position / unit % 1
        divisor = next((2 ** power for power in range(8) if (fractional * 2 ** power).denominator == 1), 128)
        end = at + unit / divisor
        if any(at < stop and end > begin for begin, stop in occupied):
            raise ScoreImportError("Overlapping fermata automation needs additional interpretation.")
        occupied.append((at, end))
        slowed = floor(F(str(bpm)) * (F(4, 5) - F(7, 15) * length) + F(1, 2))
        if slowed <= 0:
            raise ScoreImportError("Fermata produces a nonpositive tempo.")
        by_time[at] = slowed
        if end < starts[-1]:
            by_time.setdefault(end, bpm)
    result = {}
    for at, bpm in by_time.items():
        bar = bisect_right(starts, at) - 1
        if not 0 <= bar < len(measures):
            raise ScoreImportError("Tempo automation is outside the score.")
        result[bar, at - starts[bar]] = bpm
    return result

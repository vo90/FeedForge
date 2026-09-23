"""Independent source automation clock; never imports conversion helpers."""
from fractions import Fraction as F
from math import floor

from .verify_source import fraction, integer, unsupported


def expand(auto, bars, events, loc):
    enabled = auto.get("gradualTempo", False)
    if not isinstance(enabled, bool):
        raise ValueError(loc + ": invalid gradual-tempo flag")
    holds = auto.get("fermata") or []
    if not isinstance(holds, list):
        raise ValueError(loc + ": invalid fermata list")
    if holds and any(t.get("type", 4) != 4 or t.get("dotted") for t in auto.get("tempo", [])):
        unsupported(loc, "Fermata tempo-unit combination is not independently verified.")
    boundaries, position = [], F(0)
    for bar in bars:
        boundaries.append(position)
        position += bar.length
    original = {boundaries[b] + q: bpm for (b, q), bpm in events.items()}
    result = dict(original)
    if enabled and any(t.get("linear") for t in auto.get("tempo", [])):
        if holds or any(t.get("position") not in (None, 0, [0, 1]) for t in auto["tempo"]):
            unsupported(loc, "Combined tempo automation is not independently verified.")
        destinations = {boundaries[integer(t["measure"], loc)] for t in auto["tempo"] if t.get("linear")}
        points = sorted(original.items())
        for (start, a), (end, b) in zip(points, points[1:]):
            if end in destinations:
                n = max(1, min(64, int(end - start + F(1, 2))))
                for j in range(1, n):
                    result.setdefault(start + F(j, n) * (end - start),
                                      F(int(F(n - j, n) * floor(a) + F(j, n) * floor(b) + F(1, 2))))
    ranges = []
    for hold in holds:
        if not isinstance(hold, dict) or set(hold) - {"measure", "position", "length", "type"}:
            unsupported(loc, "Unknown fermata fields.")
        bar = integer(hold["measure"], loc)
        if not 0 <= bar < len(bars):
            raise ValueError(loc + ": invalid fermata measure")
        q, amount = fraction(hold["position"], loc) / 960, fraction(hold["length"], loc)
        if not 0 <= q < bars[bar].length or not 0 <= amount <= 1 or hold.get("type") not in ("short", "medium", "long"):
            raise ValueError(loc + ": invalid fermata data")
        if any(b == bar and offset != 0 for b, offset in events):
            unsupported(loc, "Fermata with a midbar tempo change is not independently verified.")
        start = boundaries[bar] + q
        choices = [t for t in original if t <= start]
        if not choices:
            raise ValueError(loc + ": missing fermata base tempo")
        rate = original[max(choices)]
        unit = F(4, bars[bar].signature[1])
        subdivision = 1
        while subdivision < 128 and (q / unit * subdivision).denominator != 1:
            subdivision *= 2
        end = start + unit / subdivision
        if any(start < b and end > a for a, b in ranges):
            unsupported(loc, "Overlapping fermatas are not independently verified.")
        ranges.append((start, end))
        slow = F(int(rate * (F(12) - 7 * amount) / 15 + F(1, 2)))
        if slow <= 0:
            raise ValueError(loc + ": nonpositive fermata tempo")
        result[start] = slow
        if end < position:
            result.setdefault(end, rate)
    mapped = {}
    for q, rate in result.items():
        index = max(i for i, start in enumerate(boundaries) if start <= q)
        if q >= position:
            raise ValueError(loc + ": automation outside score")
        mapped[index, q - boundaries[index]] = rate
    return mapped

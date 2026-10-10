"""Inverse of the verifier's independently reconstructed performance clock."""
from bisect import bisect_right
from fractions import Fraction
from functools import lru_cache


def inverse_clock(clock, recording, first_quarter):
    """Build a bounded, assessment-local inverse; never use producer timing.

    RecordingMap's outgoing-segment epsilon can make tiny discontinuities at
    joins. Preserve the former continuous-clock search there. Ordinary points
    invert the two linear segments directly, with the same domain clamping.
    """
    last_quarter = float(clock.quarters)
    audio = tuple(a for _, a in recording.anchors) if recording.piecewise else ()
    # Retain the forward clock's domain validation even for direct queries.
    recording.raw(clock.at(Fraction(str(first_quarter))))
    recording.raw(clock.at(clock.quarters))
    joins = []
    if recording.piecewise:
        epsilon = Fraction(2, 10**9)
        for i in range(1, len(audio) - 1):
            (s0, a0), (s1, a1), (s2, a2) = recording.anchors[i - 1:i + 2]
            radius = epsilon * max((a1 - a0) / (s1 - s0), (a2 - a1) / (s2 - s1))
            joins.append((a1 - radius, a1 + radius))
    joins.sort()
    lower, upper = [], []
    for left, right in joins:
        lower.append(left)
        upper.append(max(right, upper[-1]) if upper else right)

    def search(seconds):
        low, high = first_quarter, last_quarter
        for _ in range(45):
            mid = (low + high) / 2
            if float(recording.raw(clock.at(Fraction(str(mid))))) < seconds:
                low = mid
            else:
                high = mid
        return (low + high) / 2

    @lru_cache(maxsize=100000)
    def quarter_at(seconds):
        value = Fraction(str(seconds))
        if recording.piecewise:
            join = bisect_right(lower, value) - 1
            if join >= 0 and value <= upper[join]:
                return search(seconds)
            index = min(len(audio) - 2, max(recording.collapsed_count,
                                          bisect_right(audio, value) - 1))
            (s0, a0), (s1, a1) = recording.anchors[index:index + 2]
            score = s0 + (value - a0) * (s1 - s0) / (a1 - a0)
        else:
            score = (value - recording.offset) / recording.scale
        index = max(0, bisect_right(clock.seconds, score) - 1)
        quarter = clock.positions[index] + (score - clock.seconds[index]) * clock.bpms[index] / 60
        return max(first_quarter, min(last_quarter, float(quarter)))

    return quarter_at

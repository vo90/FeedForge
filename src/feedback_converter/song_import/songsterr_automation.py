"""Explicit tempo automation interpreted without inventing a performance."""
from bisect import bisect_right
from copy import deepcopy
from fractions import Fraction as F
from math import floor
import re

from .model import ScoreImportError, integer, rational


def effective_tempo_entries(automations, *, qualify_clock=False):
    """Return the player's relative tempo coordinates, leaving source intact.

    The first raw entry establishes the origin before duplicate selection.
    For newly admitted shifted lists, restrict positions and effective rates
    to clocks demonstrated equivalent in synth and browser playback. Other
    default/rounding/quantization contexts require separate qualification.
    """
    raw = automations.get("tempo", [])
    if not isinstance(raw, list) or any(not isinstance(t, dict) for t in raw):
        raise ScoreImportError("Invalid Songsterr tempo list.")
    if not raw:
        if qualify_clock:
            raise ScoreImportError("Shifted-origin tracks require matching explicit opening tempo clocks.")
        return [], 0
    bars = [integer(t.get("measure"), "tempo measure") for t in raw]
    if any(bar < 0 for bar in bars):
        raise ScoreImportError("Tempo references a negative measure.")
    origin = bars[0]
    if origin or qualify_clock:
        first = raw[0].get("position")
        if isinstance(first, bool) or first not in (0, "0"):
            raise ScoreImportError("A shifted tempo origin requires an explicit scalar zero opening position.")
        for tempo, bar in zip(raw, bars):
            measure = tempo.get("measure")
            scalar_measure = (type(measure) in (int, float)
                              or isinstance(measure, str) and re.fullmatch(
                                  r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", measure))
            if not scalar_measure:
                raise ScoreImportError("Shifted tempo measures require scalar numeric source coordinates.")
            if bar < origin:
                raise ScoreImportError("Tempo references a negative effective measure.")
            position = tempo.get("position")
            scalar = (type(position) in (int, float)
                      or isinstance(position, str) and re.fullmatch(
                          r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", position))
            if not scalar or (rational(position, "tempo position") / 960).denominator != 1:
                raise ScoreImportError("Shifted tempo positions require qualified scalar whole-quarter ticks.")
            if (type(tempo.get("bpm")) not in (int, float)
                    or type(tempo.get("type", 4)) not in (int, float)):
                raise ScoreImportError("Shifted tempo rates and note values require numeric source values.")
            unit = integer(tempo.get("type", 4), "tempo note value")
            rate = rational(tempo.get("bpm"), "tempo") * F(4, unit) if unit in (2, 4, 8) else None
            if rate is None or rate <= 0:
                raise ScoreImportError("The shifted tempo note value or rate needs additional clock qualification.")
            if tempo.get("dotted") is True:
                rate *= F(3, 2)
            if rate.denominator != 1:
                raise ScoreImportError("The shifted tempo rate needs additional browser-rounding qualification.")
    entries = deepcopy(raw)
    for entry, bar in zip(entries, bars):
        entry["measure"] = bar - origin
    return entries, origin


def inactive_tempo_context(automations):
    """Qualify unused outside-bar marks, never a missing initial/ramp clock.

    The player attaches step instructions only to existing written measures.
    Call this only after initial-measure normalization. Ramp coordinate
    fallback can activate outside marks, so active ramps remain excluded.
    Individual entries must still pass ordinary source validation.
    """
    raw = automations.get("tempo", [])
    if not isinstance(raw, list) or not raw or not all(isinstance(t, dict) for t in raw):
        return False
    try:
        return (integer(raw[0].get("measure"), "tempo measure") == 0
                and rational(raw[0].get("position", 0), "tempo position") == 0
                and not (automations.get("gradualTempo") is True and any(t.get("linear") for t in raw)))
    except ScoreImportError:
        return False


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
    starts = [F(0)]
    for m in measures:
        starts.append(starts[-1] + m.length)
    by_time = {starts[bar] + position: bpm for (bar, position), bpm in events.items()}
    ramps = [t for t in automations.get("tempo", []) if t.get("linear")]
    linear = {starts[integer(t["measure"], "ramp measure")] + rational(t.get('position', 0)) / 960 for t in ramps}
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
        # Qr/ei round the authored BPM before converting its note unit. The
        # generated hold/restoration events copy type but not the dotted flag.
        raw_tempos = {starts[integer(t['measure'], 'tempo measure')] + rational(t.get('position', 0)) / 960: t
                      for t in automations.get('tempo', [])}
        raw = raw_tempos[max(t for t in raw_tempos if t <= at)]
        authored_bpm = rational(raw['bpm'], 'tempo')
        unit_scale = F(4, integer(raw.get('type', 4), 'tempo note value'))
        bpm = authored_bpm * unit_scale
        unit = F(4, measures[bar].denominator)
        fractional = position / unit % 1
        divisor = next((2 ** power for power in range(8) if (fractional * 2 ** power).denominator == 1), 128)
        end = at + unit / divisor
        if any(at < stop and end > begin for begin, stop in occupied):
            raise ScoreImportError("Overlapping fermata automation needs additional interpretation.")
        occupied.append((at, end))
        slowed = floor(authored_bpm * (F(4, 5) - F(7, 15) * length) + F(1, 2)) * unit_scale
        if slowed <= 0:
            raise ScoreImportError("Fermata produces a nonpositive tempo.")
        by_time[at] = slowed
        # Songsterr replaces this event, including its linear flag. A hold at
        # a ramp destination therefore removes that ramp, not just its BPM.
        linear.discard(at)
        if end < starts[-1]:
            by_time.setdefault(end, bpm)
    # Qr/Zr expands holds before Kr/Vr chooses adjacent ramp endpoints. Using
    # the authored tempo list here would ramp through a hold or start the
    # following ramp too early. Snapshot before adding interpolation points.
    if gradual and linear:
        times = sorted(by_time)
        for left, right in zip(times, times[1:]):
            if right not in linear:
                continue
            steps = min(64, max(1, floor(right - left + F(1, 2))))
            first, last = floor(by_time[left]), floor(by_time[right])
            for i in range(1, steps):
                at = left + (right - left) * F(i, steps)
                by_time.setdefault(at, floor(first + (last - first) * F(i, steps) + F(1, 2)))
    result = {}
    for at, bpm in by_time.items():
        bar = bisect_right(starts, at) - 1
        if not 0 <= bar < len(measures):
            raise ScoreImportError("Tempo automation is outside the score.")
        result[bar, at - starts[bar]] = bpm
    return result

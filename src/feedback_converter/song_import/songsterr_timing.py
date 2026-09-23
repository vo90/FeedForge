"""Authored performance timing, separate from written rhythm and synth effects.

See docs/songsterr-timing.md for the public-source semantics and boundaries.
All offsets remain rational until the audio clock is applied.
"""
from fractions import Fraction as F

from .model import ScoreImportError, integer, rational
from .songsterr_fields import whole_measure_rest


FEELS = {f"{prefix}{unit}": (F(4, unit), first, second)
         for prefix, first, second in (("", F(4, 3), F(2, 3)),
                                       ("dotted", F(3, 2), F(1, 2)),
                                       ("scottish", F(1, 2), F(3, 2)))
         for unit in (8, 16)}
FEELS = {key + "th": value for key, value in FEELS.items()}


def measure_timing(measure, bar_length, feel, previous=None, initial=False):
    """Swing exemptions are shared by all voices of this source part."""
    if any(not isinstance(v, dict) or not isinstance(v.get("beats"), list) for v in measure["voices"]):
        raise ScoreImportError("Missing Songsterr beat data.")
    if feel in (None, "off"):
        return [voice_timing(v["beats"], bar_length, previous=(previous or {}).get(vi), initial=initial)
                for vi, v in enumerate(measure["voices"])]
    if feel not in FEELS:
        raise ScoreImportError("Unknown authored swing feel.")
    step, first, second = FEELS[feel]
    excluded, voices = set(), []
    for voice in measure["voices"]:
        cursor, rows = F(0), []
        for b in voice["beats"]:
            length = rational(b.get("duration"), "beat duration") * 4
            rows.append((cursor, length))
            if not b.get("graceNote"):
                if (length / step).denominator != 1 or feel in ("8th", "16th") and b.get("tuplet") == 2:
                    excluded.add(cursor // (2 * step))
                cursor += length
        voices.append((rows, cursor))
    clocks = []
    for vi, (voice, (rows, total)) in enumerate(zip(measure["voices"], voices)):
        def swung(q):
            pair = q // (2 * step)
            if pair in excluded or (pair + 1) * 2 * step > total:
                return q
            start = pair * 2 * step
            local = q - start
            return start + (local * first if local <= step else step * first + (local - step) * second)
        lengths = [length if b.get("graceNote") else swung(q + length) - swung(q)
                   for b, (q, length) in zip(voice["beats"], rows)]
        clocks.append(voice_timing(voice["beats"], bar_length, lengths, previous=(previous or {}).get(vi), initial=initial))
    return clocks


def part_timing(measures, lengths):
    """Resolve written-neighbour grace borrowing before constructing notes."""
    clocks, previous, feel = [], {}, "off"
    for bi, (measure, length) in enumerate(zip(measures, lengths)):
        if not isinstance(measure, dict) or not isinstance(measure.get('voices'), list):
            raise ScoreImportError("Missing Songsterr voices.")
        feel = measure.get("tripletFeel") or feel
        try:
            values = measure_timing(measure, length, feel, previous, initial=bi == 0)
        except ScoreImportError as exc:
            exc.source_location = {'measure': bi + 1}
            raise
        clocks.append(values)
        previous = {vi: (v["beats"], values[vi], length) for vi, v in enumerate(measure["voices"])
                    if v["beats"]}
    return clocks


def voice_timing(beats, bar_length, performed_lengths=None, *, previous=None, initial=False):
    written_lengths = [rational(b.get("duration"), "beat duration") * 4 for b in beats]
    lengths = performed_lengths if performed_lengths is not None else written_lengths
    if any(d <= 0 for d in lengths):
        raise ScoreImportError("Invalid beat duration.")
    if whole_measure_rest(beats):
        return [(F(0), bar_length, F(0))]
    if beats and beats[0].get("graceNote") == "beforeBeat":
        if initial:
            # The public clock treats a before-beat group with no preceding
            # beat at score start as on-beat. Keep the written mark unchanged.
            count = next((i for i, b in enumerate(beats) if not b.get("graceNote")), len(beats))
            if any(b.get("graceNote") != "beforeBeat" for b in beats[:count]):
                raise ScoreImportError("Mixed initial grace group needs additional support.")
            normalized = [{**b, "graceNote": "onBeat"} if i < count else b for i, b in enumerate(beats)]
            return voice_timing(normalized, bar_length, lengths)
        if previous:
            old_beats, old_clock, old_length = previous
            if old_beats[-1].get("graceNote"):
                raise ScoreImportError("Adjacent cross-bar grace groups need additional support.")
            start, duration, written = old_clock[-1]
            available = old_length - start
            if available <= 0:
                raise ScoreImportError("Cross-bar grace has no preceding time budget.")
            prefix = {"duration": [available.numerator, available.denominator * 4], "notes": []}
            combined = voice_timing([prefix, *beats], available + bar_length, [available, *lengths])
            old_clock[-1] = (start, min(duration, combined[0][1]), written)
            return [(p - available, d, w - available) for p, d, w in combined[1:]]
    output, position, written_position, index = [], F(0), F(0), 0
    while index < len(beats):
        principal = index
        while principal < len(beats) and beats[principal].get("graceNote"):
            if beats[principal]["graceNote"] not in ("onBeat", "beforeBeat"):
                raise ScoreImportError("This grace-note type needs additional timing support.")
            principal += 1
        before = principal > index and beats[index]["graceNote"] == "beforeBeat"
        if principal > index and any(b.get("graceNote") != beats[index]["graceNote"] for b in beats[index:principal]):
            raise ScoreImportError("Mixed grace groups require additional interpretation.")
        if principal == len(beats) and not before:
            raise ScoreImportError("An on-beat grace note has no following note.")
        if before and (not output or beats[index - 1].get("graceNote")):
            raise ScoreImportError("Before-beat grace crossing a measure or recording boundary needs additional support.")
        end = position + lengths[principal] if principal < len(beats) else position
        if end > bar_length:
            raise ScoreImportError("Voice exceeds its authored measure; it was not shortened.")
        offset = F(0)
        if principal > index:
            group = beats[index:principal]
            # The source clock allocates grace time to beats, including rests.
            # A grace rest can deliberately delay the following attack.
            dots = max(integer(b.get("dots", 0), "grace dots") for b in group)
            if not 0 <= dots <= 4:
                raise ScoreImportError("Invalid grace dot count.")
            reserved = F(1, 8 if dots >= 2 else 4 if dots == 1 else 2)
            # All source group budgets are calculated before grace durations
            # are subtracted. A previous on-beat group must not shrink the
            # budget a later before-beat group receives from that principal.
            available = lengths[index - 1] if before else bar_length - position if principal == len(beats) - 1 else lengths[principal]
            total = sum(lengths[index:principal], F(0))
            budget = min(available * (1 - reserved), total / len(group))
            used = budget if total > budget else total
            if before:
                previous = output[-1]
                if previous[1] <= used:
                    raise ScoreImportError("Grace groups consume their shared principal; no repair was applied.")
                output[-1] = (previous[0], previous[1] - used, previous[2])
            for gi in range(index, principal):
                length = budget / len(group) if total > budget else lengths[gi]
                output.append((position + offset - (used if before else 0), length, written_position))
                offset += length
        if principal == len(beats):
            break
        if before:
            offset = F(0)
        if lengths[principal] <= offset:
            raise ScoreImportError("The grace group leaves no positive principal duration; no repair was applied.")
        output.append((position + offset, lengths[principal] - offset, written_position))
        written_position += written_lengths[principal]
        position, index = end, principal + 1
    return output


def strum_offsets(beat):
    """Return source-note-index offsets and explicit direction, never a guessed strum."""
    sources = [key for key in ("brushStroke", "arpeggio") if beat.get(key) is not None]
    legacy = [key for key in ("upStroke", "downStroke") if beat.get(key)]
    old_arps = [key for key in ("upArpeggio", "downArpeggio") if beat.get(key)]
    old_step = None
    if old_arps and not sources:
        if len(old_arps) != 1 or legacy:
            raise ScoreImportError("Conflicting legacy arpeggio and brush data.")
        value = integer(beat[old_arps[0]], "legacy arpeggio")
        if not 1 <= value <= 8:
            raise ScoreImportError("Legacy arpeggio duration needs additional interpretation.")
        old_step = F(4, 13 * 2 ** (8 - value))
        direction = "up" if old_arps[0] == "upArpeggio" else "down"
    if len(sources) > 1 or len(legacy) > 1:
        raise ScoreImportError("Conflicting authored strum directions.")
    if not sources and not legacy and old_step is None:
        return {}, None
    if sources:
        stroke = beat[sources[0]]
        if not isinstance(stroke, dict) or set(stroke) != {"direction", "duration", "shift"}:
            raise ScoreImportError("Unrecognized strum timing data.")
        direction = stroke["direction"]
        duration, shift = rational(stroke["duration"], "strum duration"), rational(stroke["shift"], "strum shift")
    elif old_step is None:
        if integer(beat[legacy[0]], "legacy strum") != 1:
            raise ScoreImportError("Legacy strum duration needs additional interpretation.")
        direction, duration, shift = ("down" if legacy[0] == "upStroke" else "up"), F(30), 100
    else:
        duration, shift = F(0), 100
    if direction not in ("up", "down") or not 0 <= duration <= 960 or not 0 <= shift <= 100:
        raise ScoreImportError("Invalid authored strum timing.")
    if legacy and sources:
        expected = "down" if legacy[0] == "upStroke" else "up"
        if expected != direction or integer(beat[legacy[0]], "legacy strum") != 1:
            raise ScoreImportError("Conflicting legacy and current strum data.")
    ordered = sorted(((integer(n.get("string"), "strum string"), i) for i, n in enumerate(beat["notes"]) if not n.get("rest")))
    if len({s for s, _ in ordered}) != len(ordered):
        raise ScoreImportError("A strum has multiple notes on the same string.")
    # Linked pitch gestures disable spreading in Songsterr's performer. Leave
    # their explicit slide/bend/hammer interpretation to the ordinary parser.
    if any(any(n.get(k) for k in ("bend", "hp", "slide", "leftSlide", "rightSlide")) for n in beat["notes"]):
        return {}, direction
    count = len(ordered)
    if count < 2:
        return {}, direction
    # Explicit spreading follows grace allocation. The source cap uses the
    # written beat duration; callers still reject nonpositive sounding notes.
    cap = min(960, int(rational(beat["duration"], "strum beat duration") * 1920))
    step = old_step if old_step is not None else F(min(duration, cap)) / (480 * count)
    span = step * (count - 1)
    advance = span * F(100 - shift, 100)
    offsets = {i: (step * (rank if direction == "up" else count - 1 - rank)) - advance
               for rank, (_, i) in enumerate(ordered)}
    return offsets, direction

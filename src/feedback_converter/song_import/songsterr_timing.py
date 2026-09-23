"""Authored performance timing, separate from written rhythm and synth effects.

See docs/songsterr-timing.md for the public-source semantics and boundaries.
All offsets remain rational until the audio clock is applied.
"""
from fractions import Fraction as F

from .model import ScoreImportError, integer, rational


def voice_timing(beats, bar_length):
    lengths = [rational(b.get("duration"), "beat duration") * 4 for b in beats]
    if any(d <= 0 for d in lengths):
        raise ScoreImportError("Invalid beat duration.")
    output, position, index = [], F(0), 0
    while index < len(beats):
        principal = index
        while principal < len(beats) and beats[principal].get("graceNote"):
            if beats[principal]["graceNote"] != "onBeat":
                raise ScoreImportError("This grace-note type needs additional timing support.")
            principal += 1
        if principal == len(beats):
            raise ScoreImportError("An on-beat grace note has no following note.")
        end = position + lengths[principal]
        if end > bar_length:
            raise ScoreImportError("Voice exceeds its authored measure; it was not shortened.")
        offset = F(0)
        if principal > index:
            group = beats[index:principal]
            if any(b.get("rest") or not any(not n.get("rest") for n in b.get("notes", []))
                   for b in beats[index:principal + 1]):
                raise ScoreImportError("An on-beat grace note needs a following pitched note.")
            dots = max(integer(b.get("dots", 0), "grace dots") for b in group)
            if not 0 <= dots <= 4:
                raise ScoreImportError("Invalid grace dot count.")
            reserved = F(1, 8 if dots >= 2 else 4 if dots == 1 else 2)
            available = bar_length - position if principal == len(beats) - 1 else lengths[principal]
            total = sum(lengths[index:principal], F(0))
            budget = min(available * (1 - reserved), total / len(group))
            for gi in range(index, principal):
                length = budget / len(group) if total > budget else lengths[gi]
                output.append((position + offset, length, position))
                offset += length
        if lengths[principal] <= offset:
            raise ScoreImportError("The grace group leaves no positive principal duration; no repair was applied.")
        output.append((position + offset, lengths[principal] - offset, position))
        position, index = end, principal + 1
    return output


def strum_offsets(beat):
    """Return source-note-index offsets and explicit direction, never a guessed strum."""
    sources = [key for key in ("brushStroke", "arpeggio") if beat.get(key) is not None]
    legacy = [key for key in ("upStroke", "downStroke") if beat.get(key)]
    if len(sources) > 1 or len(legacy) > 1:
        raise ScoreImportError("Conflicting authored strum directions.")
    if not sources and not legacy:
        return {}, None
    if sources:
        stroke = beat[sources[0]]
        if not isinstance(stroke, dict) or set(stroke) != {"direction", "duration", "shift"}:
            raise ScoreImportError("Unrecognized strum timing data.")
        direction = stroke["direction"]
        duration, shift = rational(stroke["duration"], "strum duration"), integer(stroke["shift"], "strum shift")
    else:
        if integer(beat[legacy[0]], "legacy strum") != 1:
            raise ScoreImportError("Legacy strum duration needs additional interpretation.")
        direction, duration, shift = ("down" if legacy[0] == "upStroke" else "up"), F(30), 100
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
    if any(n.get("tie") for n in beat["notes"]):
        raise ScoreImportError("A strum containing tied notes needs additional timing support.")
    if beat.get("graceNote"):
        raise ScoreImportError("Combined grace and strum timing needs additional support.")
    cap = min(960, int(rational(beat["duration"], "strum beat duration") * 1920))
    step = F(min(duration, cap)) / (480 * count)
    span = step * (count - 1)
    advance = span * F(100 - shift, 100)
    offsets = {i: (step * (rank if direction == "up" else count - 1 - rank)) - advance
               for rank, (_, i) in enumerate(ordered)}
    return offsets, direction

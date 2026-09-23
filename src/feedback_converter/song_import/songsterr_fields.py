"""Explicit Songsterr fields, without altering the retained source document."""
from fractions import Fraction as F

from .model import ScoreImportError, rational


def whole_measure_rest(beats):
    """The single, undotted whole-rest glyph denotes the complete measure."""
    if len(beats) != 1:
        return False
    b = beats[0]
    return (b.get("rest") is True and b.get("type") == 1
            and not b.get("graceNote") and not b.get("dots") and not b.get("tuplet")
            and rational(b.get("duration"), "whole rest") == 1
            and isinstance(b.get("notes"), list)
            and all(n.get("rest") is True for n in b["notes"]))


def bend_points(points):
    """Precise coordinates use percent; old coordinates use sixtieths.

    Source order is meaningful. At an equal coordinate the last authored value
    wins, as in the public performer. Missing terminal points hold their last
    value; consumers already hold the last curve point through the sustain.
    """
    if not isinstance(points, list) or not points:
        raise ScoreImportError("Bend has no curve data.")
    if any(not isinstance(p, dict) for p in points):
        raise ScoreImportError("Invalid bend point.")
    precise = ["precisePosition" in p for p in points]
    if any(precise) and not all(precise):
        raise ScoreImportError("Mixed precise and legacy bend coordinates.")
    use_precise = all(precise)
    out = []
    previous_coarse = F(-1)
    for p in points:
        coarse = rational(p.get("position"), "bend position")
        if not previous_coarse <= coarse <= 60 or coarse < 0:
            raise ScoreImportError("Bend positions are outside the note or out of order.")
        previous_coarse = coarse
        position = (rational(p["precisePosition"], "precise bend position") / 100
                    if use_precise else coarse / 60)
        tone = rational(p.get("tone"), "bend value") / 50
        if not 0 <= position <= 1 or out and position < out[-1][0]:
            raise ScoreImportError("Precise bend positions are outside the note or out of order.")
        # Existing fretting-hand bend support; negative gestures are not silently
        # reclassified as ordinary bends or tremolo-bar effects.
        if not 0 <= tone <= 8:
            raise ScoreImportError("This finger-bend pitch range needs additional support.")
        point = (position, float(tone))
        if out and position == out[-1][0]:
            out[-1] = point
        else:
            out.append(point)
    return out

"""Explicit Songsterr fields, without altering the retained source document."""
from fractions import Fraction as F
import math

from .model import ScoreImportError, integer, rational


def validate_bend_point_vibrato(value):
    """Legacy point-local zero is inactive, not note-level timed vibrato.

    Keep this exception local: zero can be meaningful in other source fields.
    Active values have no verified mapping and must not silently disappear.
    """
    if value is None or value is False:
        return
    if type(value) is int or type(value) is float and math.isfinite(value):
        if value == 0:
            return
        raise ScoreImportError("Unsupported active bend-point vibrato.")
    if value is True:
        raise ScoreImportError("Unsupported active bend-point vibrato.")
    raise ScoreImportError("Invalid bend-point vibrato; expected null, false or numeric zero.")


def effective_dots(beat):
    """Read the modern count or legacy flag without reapplying written duration.

    The source player prefers a positive dots count; zero/null falls back to
    dotted. Validate both fields even when the modern count takes precedence.
    """
    legacy = beat.get("dotted")
    if legacy is not None and type(legacy) is not bool:
        raise ScoreImportError("Invalid dotted flag; expected a boolean.")
    value = beat.get("dots")
    if value is None:
        dots = 0
    else:
        if type(value) not in (int, float):
            raise ScoreImportError("Invalid dots count; expected a number.")
        dots = integer(value, "dots count")
        if not 0 <= dots <= 4:
            raise ScoreImportError("Unsupported dots count.")
    return dots if dots > 0 else int(legacy is True)


def validate_sustain_pedal(value):
    """A retained synth-controller flag, never a note-duration instruction."""
    if value is not None and type(value) is not bool:
        raise ScoreImportError("Invalid sustainPedal flag; expected a boolean.")


def whole_measure_rest(beats):
    """The single, undotted whole-rest glyph denotes the complete measure."""
    if len(beats) != 1:
        return False
    b = beats[0]
    return (b.get("rest") is True and b.get("type") == 1
            and not b.get("graceNote") and not effective_dots(b) and not b.get("tuplet")
            and rational(b.get("duration"), "whole rest") == 1
            and isinstance(b.get("notes"), list)
            and all(n.get("rest") is True for n in b["notes"]))


def bounded_dotted_whole_rest(beats, bar_length):
    """A lone dotted whole-rest may end at the bar boundary, never a note.

    Keep the source glyph/dots/duration intact. Only its silent performed span
    is bounded, matching the source scheduler. Ordinary rests, tuplets, grace
    and malformed dot/duration pairs do not qualify.
    """
    if len(beats) != 1:
        return False
    b = beats[0]
    dots = effective_dots(b)
    if (b.get("rest") is not True or b.get("type") != 1
            or type(dots) is not int or not 1 <= dots <= 4
            or b.get("graceNote") or b.get("tuplet")
            or not isinstance(b.get("notes"), list) or not b["notes"]
            or any(n.get("rest") is not True for n in b["notes"])):
        return False
    length = rational(b.get("duration"), "dotted whole rest")
    return length == F(2) - F(1, 2**dots) and length * 4 > bar_length


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
    # Songsterr selects percent mode for the whole curve when any point has
    # precisePosition. Its Dn/En helpers round missing percent coordinates
    # with Math.round(position * 100 / 60), not a per-point /60 fallback.
    use_precise = any("precisePosition" in p for p in points)
    out = []
    previous_coarse = F(-1)
    for p in points:
        coarse = rational(p.get("position"), "bend position")
        if not previous_coarse <= coarse <= 60 or coarse < 0:
            raise ScoreImportError("Bend positions are outside the note or out of order.")
        previous_coarse = coarse
        if use_precise:
            percent = (rational(p["precisePosition"], "precise bend position")
                       if "precisePosition" in p else (coarse * 100 / 60 + F(1, 2)) // 1)
            position = F(percent) / 100
        else:
            position = coarse / 60
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

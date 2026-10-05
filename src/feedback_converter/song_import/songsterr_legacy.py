"""Validate obsolete Songsterr metadata without deriving musical instructions.

The captured preparation replaces source measure indices and beat tempos, and
current playback/notation reads beat.graceNote rather than the old note flag.
These exceptions cover the demonstrated JSON shapes, never arbitrary payloads
under the same field names. Keep every original value in the source document.
"""
import math

from .model import ScoreImportError


LEGACY_METADATA = {
    ("measure", "index"): "The source measure index is retained. Prepared measure identity follows the source array order.",
    ("beat", "tempo"): "The legacy beat tempo is retained. The validated tempo automation determines the musical clock.",
    ("note", "grace"): "The legacy note flag is retained. Explicit beat graceNote instructions determine grace timing and notation.",
}


def validate_legacy_field(scope, field, value):
    """Return whether this is a known metadata field; validate even null/rests."""
    key = (scope.removeprefix("Songsterr "), field)
    if key not in LEGACY_METADATA:
        return False
    if value is None:
        return True
    if key == ("measure", "index"):
        if (type(value) not in (int, float) or value < 0
                or type(value) is float and (not math.isfinite(value) or not value.is_integer())):
            raise ScoreImportError("Invalid legacy measure index; expected null or a nonnegative integer.")
    elif key == ("note", "grace"):
        if type(value) is not bool:
            raise ScoreImportError("Invalid legacy note grace flag; expected null or a boolean.")
    else:
        if not isinstance(value, dict) or set(value) != {"type", "bpm"}:
            raise ScoreImportError("Invalid legacy beat tempo; expected null or exactly numeric type and bpm fields.")
        if type(value["type"]) is not int or value["type"] not in {1, 2, 4, 8, 16, 32, 64}:
            raise ScoreImportError("Invalid legacy beat tempo note value.")
        bpm = value["bpm"]
        if (type(bpm) not in (int, float) or bpm <= 0
                or type(bpm) is float and not math.isfinite(bpm)):
            raise ScoreImportError("Invalid legacy beat tempo rate; expected a finite positive number.")
    return True


def validate_legacy_metadata(obj, scope):
    """Validate present legacy metadata before field inventory or rest skips."""
    if not isinstance(obj, dict):
        raise ScoreImportError(f"Malformed Songsterr {scope} object.")
    for field, value in obj.items():
        validate_legacy_field(scope, field, value)

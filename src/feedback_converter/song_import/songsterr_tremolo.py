"""Map authored tremolo picking to the existing FeedBack instruction.

The source subdivision stays in source evidence; ``tr`` has no rate field.
This is not whammy-bar tremolo and does not invent separately scored attacks.
"""
from .model import ScoreImportError


def tremolo_mark(value):
    if value is None or value is False:
        return False
    if value is True:  # legacy instruction without an explicit subdivision
        return True
    if (isinstance(value, list) and len(value) == 2
            and all(type(v) is int and v > 0 for v in value)):
        return True
    raise ScoreImportError("Invalid tremolo-picking subdivision; expected a positive rational pair or boolean.")

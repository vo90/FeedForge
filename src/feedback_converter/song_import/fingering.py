"""Authored hand-specific teaching marks; never pitch or scoring instructions."""
from .model import ScoreImportError


def validate_right_finger(value):
    # This is a picking-hand annotation, not a fret-hand fg number. Its exact
    # spelling remains in source evidence and the located compatibility report.
    if value is not None and (type(value) is not str or value not in ("P", "I", "M", "A", "C")):
        raise ScoreImportError("Invalid Songsterr rightFingering; expected P, I, M, A or C.")


def left_finger(value):
    # Songsterr's public note schema uses strings: 0, 1..4, T. Its zero is
    # open/no finger, whereas FeedBack's fg=0 means THUMB. Never coerce them.
    if value is None or value == "0":
        return None
    if type(value) is not str or value not in ("1", "2", "3", "4", "T"):
        raise ScoreImportError("Invalid Songsterr leftFingering; expected 0, 1–4 or T.")
    return 0 if value == "T" else int(value)


def template_fingers(notes, width):
    fingers = [-1] * width
    for note in notes:
        if "fg" in note:
            fingers[note["s"]] = note["fg"]
    return fingers

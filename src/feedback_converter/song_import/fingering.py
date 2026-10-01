"""Authored fret-hand teaching marks; never pitch or scoring instructions."""
from .model import ScoreImportError


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

"""Verified natural touch positions and source playback pitches.

The integer fret is the author's tablature shorthand, NOT the sounding pitch.
Exact source positions take precedence over the displayed integer fret.
Verified aliases apply to every matching source note, independently of song,
artist, revision or arrangement. Unknown positions are never rounded to a
nearby known node: that could change the intended partial.
"""
import math
from ..harmonic_target import target_for, ALIAS


# Semitones above the open string, agreeing with source playback and its
# harmonic-node lookup. Fractional nodes need a separate precise playing cue.
NATURAL_PITCH = {2.4: 36, 2.7: 34, 3.2: 31, 4: 28, 5: 24, 5.8: 34,
                 7: 19, 8.2: 36, 9: 28, 9.6: 34, 12: 12, 14.7: 34,
                 16: 28, 17: 36, 19: 19, 21.7: 34, 24: 24}


def natural_target(note):
    fret, touch = note.get('fret'), note.get('harmonicFret')
    if (note.get('harmonic') != 'natural' or note.get('harmonicData') is not None
            or type(fret) not in (int, float) or not math.isfinite(fret)):
        return None
    if touch is None:
        touch = fret
    if type(touch) not in (int, float) or not math.isfinite(touch):
        return None
    if fret == 15 and touch == 15 and note.get("harmonicFret") is not None:
        return 14.7, 34
    for node, semitones in NATURAL_PITCH.items():
        if abs(touch - node) <= 1e-9 and fret == round(node):
            return node, semitones
    return None


def exact_natural(note):
    return natural_target(note) is not None


def source_target(note):
    if note.get("harmonicData") is not None:
        return None
    return target_for(note.get("harmonic"), note.get("harmonicFret"))


def natural_alias(note):
    return ALIAS if natural_target(note) == (14.7, 34) and note.get("harmonicFret") == 15 else None

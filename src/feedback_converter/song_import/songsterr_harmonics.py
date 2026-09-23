"""Natural nodes exactly expressible by the existing hm + authored fret cue."""
import math


# Semitones above the open string, agreeing with source playback and its
# harmonic-node lookup. Fractional nodes need a separate precise playing cue.
NATURAL_PITCH = {4: 28, 5: 24, 7: 19, 9: 28, 12: 12, 16: 28, 19: 19}


def exact_natural(note):
    fret, touch = note.get('fret'), note.get('harmonicFret')
    return (note.get('harmonic') == 'natural' and note.get('harmonicData') is None
            and isinstance(fret, (int, float)) and not isinstance(fret, bool)
            and math.isfinite(fret) and fret in NATURAL_PITCH
            and (touch is None or isinstance(touch, (int, float)) and not isinstance(touch, bool)
                 and math.isfinite(touch) and touch == fret))

"""Verified natural touch positions and source playback pitches.

The integer fret is the author's tablature shorthand, NOT the sounding pitch.
Exact source positions take precedence over the displayed integer fret.
Verified aliases apply to every matching source note, independently of song,
artist, revision or arrangement. Unknown positions are never rounded to a
nearby known node: that could change the intended partial.
"""
import math
from ..harmonic_target import target_for, ALIAS, PLAYBACK_FALLBACK


# Semitones above the open string, agreeing with source playback and its
# harmonic-node lookup. Fractional nodes need a separate precise playing cue.
NATURAL_PITCH = {2.4: 36, 2.7: 34, 3.2: 31, 4: 28, 5: 24, 5.8: 34,
                 7: 19, 8.2: 36, 9: 28, 9.6: 34, 12: 12, 14.7: 34,
                 16: 28, 17: 36, 19: 19, 21.7: 34, 24: 24}

# Explicitly qualified integer aliases, not a nearest-node approximation.
NATURAL_SHORTHAND = {2: (2.4, 36), 3: (3.2, 31), 6: (5.8, 34),
                     8: (8.2, 36), 10: (9.6, 34), 15: (14.7, 34), 22: (21.7, 34)}
# The native natural-harmonic lookup has no transform for these frets.
# 19/24 are real contact nodes; 0 has a special +24 transform. Neither belongs
# here. Higher authored frets remain subject to the existing range policy.
NATURAL_PLAIN_FRETS = frozenset({1, 11, 13, 14, 18, 20, 21, 23})
NATURAL_PLAYBACK_POLICY = 'songsterr-natural-playback-v1'
NATURAL_FALLBACK_MESSAGE = (
    'This natural-harmonic marking has no defined harmonic playback target. '
    'The note uses the ordinary fretted pitch, matching Songsterr playback; '
    'bends, ties and timing are preserved. The unresolved harmonic instruction '
    'remains in the source and is not shown or scored as a harmonic.'
)
NATURAL_SHORTHAND_MESSAGE = (
    'The integer natural-harmonic shorthand is represented by its verified '
    'precise touch position and playback pitch; the authored value is retained.'
)


def _integer_natural(note):
    fret, touch = note.get('fret'), note.get('harmonicFret')
    return (note.get('harmonic') == 'natural' and note.get('harmonicData') is None
            and not any(note.get(k) for k in ('dead', 'rest', 'pickScrape'))
            and type(fret) in (int, float) and math.isfinite(fret) and fret == int(fret)
            and (touch is None or type(touch) in (int, float)
                 and math.isfinite(touch) and touch == fret))


def natural_shorthand(note):
    """New contract-125 aliases; explicit 15/15 already had a qualified rule."""
    return (_integer_natural(note) and note['fret'] in NATURAL_SHORTHAND
            and not (note['fret'] == 15 and note.get('harmonicFret') == 15))


def natural_playback_fallback(note):
    return _integer_natural(note) and note['fret'] in NATURAL_PLAIN_FRETS


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
    if natural_shorthand(note):
        return NATURAL_SHORTHAND[fret]
    for node, semitones in NATURAL_PITCH.items():
        if abs(touch - node) <= 1e-9 and fret == round(node):
            return node, semitones
    return None


def exact_natural(note):
    return natural_target(note) is not None


def source_target(note):
    if note.get("harmonicData") is not None:
        return None
    target = target_for(note.get("harmonic"), note.get("harmonicFret"))
    if target is not None:
        return target
    if artificial_playback_fallback(note):
        return dict(PLAYBACK_FALLBACK)
    return None


def artificial_playback_fallback(note):
    """Verified synth pitch, explicitly without a known physical contact node.

    Songsterr uses +24 when its lookup finds no node. Its tolerance is wider
    than our exact contact mapping: near-node values must not take this path.
    Missing/null/string nodes and opaque harmonicData remain unqualified.
    """
    touch = note.get('harmonicFret')
    return (note.get('harmonic') == 'artificial' and note.get('harmonicData') is None
            and type(touch) in (int, float) and math.isfinite(touch) and touch >= 0
            and all(abs(touch - node) > .001 for node in NATURAL_PITCH))


def natural_alias(note):
    return ALIAS if natural_target(note) == (14.7, 34) and note.get("harmonicFret") == 15 else None

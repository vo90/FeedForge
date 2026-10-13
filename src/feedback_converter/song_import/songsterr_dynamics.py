"""Authored note-local loudness, retained without changing playable events."""
from .model import ScoreImportError


DYNAMICS = frozenset({'ppp', 'pp', 'p', 'mp', 'mf', 'f', 'ff', 'fff'})
DISPLAY_LIMITATION = (
    'Per-note volume markings are retained in the source and written-note data. '
    'The game does not display or apply individual note loudness; backing audio, '
    'note timing and scoring are unchanged.'
)
RETAINED_MESSAGE = (
    'This note-volume field has no selected sounding note instruction; '
    'its original value and location are retained in the source.'
)


def note_dynamic(value):
    if value is None:
        return None
    if not isinstance(value, str) or value not in DYNAMICS:
        raise ScoreImportError('Invalid Songsterr note dynamic; expected null or ppp, pp, p, mp, mf, f, ff, fff.')
    return value


def note_dynamics(parts):
    """Find explicit fields even on rests/excluded parts, without assuming shape.

    Structural validation belongs to the parser. This traversal never uses an
    unfamiliar container as a list or suppresses malformed dynamic values.
    """
    for pi, part in enumerate(parts):
        bars = part.get('measures') if isinstance(part, dict) else None
        for mi, bar in enumerate(bars if isinstance(bars, list) else []):
            voices = bar.get('voices') if isinstance(bar, dict) else None
            for vi, voice in enumerate(voices if isinstance(voices, list) else []):
                beats = voice.get('beats') if isinstance(voice, dict) else None
                for bi, beat in enumerate(beats if isinstance(beats, list) else []):
                    notes = beat.get('notes') if isinstance(beat, dict) else None
                    for ni, note in enumerate(notes if isinstance(notes, list) else []):
                        if isinstance(note, dict) and 'velocity' in note:
                            yield pi, mi, vi, bi, ni, note

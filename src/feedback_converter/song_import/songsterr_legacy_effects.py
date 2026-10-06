"""Retain qualified legacy beat flags without deriving musical effects."""
from .model import ScoreImportError
from .songsterr_harmonics import exact_natural

POLICY = 'songsterr-legacy-beat-effects-v1'
FIELDS = frozenset({'harmonic', 'fadeIn'})


def validate_legacy_beat_effect(scope, field, value, *, beat, qualify_harmonics=True):
    """Recognize and validate one scoped flag before inactive/rest handling."""
    if scope.removeprefix('Songsterr ') != 'beat' or field not in FIELDS:
        return False
    if value is not None and type(value) is not bool:
        raise ScoreImportError(f'Invalid legacy beat {field}; expected null or a boolean.')
    if field == 'harmonic' and value is True and qualify_harmonics:
        notes = beat.get('notes')
        if beat.get('rest') or not isinstance(notes, list) or any(not isinstance(n, dict) for n in notes):
            raise ScoreImportError('Legacy beat harmonic needs explicit valid natural harmonics on sounding notes.')
        sounding = [n for n in notes if not n.get('rest')]
        if not sounding or any(n.get('dead') or n.get('pickScrape') or not exact_natural(n) for n in sounding):
            raise ScoreImportError('Legacy beat harmonic needs explicit valid natural harmonics on every sounding note.')
    return True


def validate_legacy_beat_effects(beat, *, qualify_harmonics=True):
    present = FIELDS.intersection(beat)
    for field in present:
        validate_legacy_beat_effect('beat', field, beat[field], beat=beat,
                                  qualify_harmonics=qualify_harmonics)
    return present


def beat_effects(parts):
    """Locate only these scoped flags without inspecting excluded music."""
    for pi, part in enumerate(parts):
        measures = part.get('measures', []) if isinstance(part, dict) else []
        for mi, measure in enumerate(measures if isinstance(measures, list) else []):
            voices = measure.get('voices', []) if isinstance(measure, dict) else []
            for vi, voice in enumerate(voices if isinstance(voices, list) else []):
                beats = voice.get('beats', []) if isinstance(voice, dict) else []
                for bi, beat in enumerate(beats if isinstance(beats, list) else []):
                    if isinstance(beat, dict) and FIELDS.intersection(beat):
                        yield pi, mi, vi, bi, beat


def retention(field, value, *, selected=True):
    """Classification for an already validated flag; preserve the raw value."""
    if not selected:
        return ('source_metadata', 'source_retained',
                f'The legacy beat {field} flag is retained with the excluded arrangement in the '
                'original source; no playable effect is inferred for that arrangement.')
    if field == 'fadeIn' and value is True:
        return ('game_limitation', 'display_or_expression',
                'The authored fade-in volume-swell marking is retained in the original source. '
                'Notes, ties, timing and scoring are unchanged; the game does not display or score '
                'a dedicated volume-swell envelope.')
    message = ('The legacy beat harmonic flag is retained. Explicit note harmonic instructions '
               'determine the playable targets; no harmonic is inferred from the beat flag.'
               if field == 'harmonic' else
               'The inactive legacy beat fade-in flag is retained. Notes, timing and scoring are unchanged.')
    return 'source_metadata', 'source_retained', message

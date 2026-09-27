"""Explicit opening-bar duration, separate from its written time signature."""
from fractions import Fraction

from .model import ScoreImportError, rational
from .songsterr_fields import whole_measure_rest


def opening_length(parts, nominal):
    flags = [part.get('anacrusis', False) for part in parts]
    if any(type(flag) is not bool for flag in flags):
        raise ScoreImportError('Invalid Songsterr pickup flag.')
    if len(set(flags)) != 1:
        raise ScoreImportError('Tracks disagree about the Songsterr pickup flag.')
    if not flags[0]:
        return nominal
    lengths = []
    for part in parts:
        voices = part['measures'][0].get('voices')
        if not isinstance(voices, list) or not voices:
            raise ScoreImportError('A pickup has no explicit voice duration.')
        totals = []
        for voice in voices:
            if not isinstance(voice, dict) or not isinstance(voice.get('beats'), list):
                raise ScoreImportError('A pickup has invalid beat data.')
            if whole_measure_rest(voice['beats']):
                totals.append(nominal)
                continue
            total = Fraction(0)
            for beat in voice['beats']:
                if not isinstance(beat, dict):
                    raise ScoreImportError('A pickup has invalid beat data.')
                length = rational(beat.get('duration'), 'pickup beat duration') * 4
                if length <= 0:
                    raise ScoreImportError('A pickup has nonpositive beat duration.')
                grace = beat.get('graceNote')
                if grace not in (None, 'onBeat', 'beforeBeat'):
                    raise ScoreImportError('Unknown pickup grace-note timing.')
                # Grace notes borrow their principal's time; swing redistributes
                # complete pairs without extending the written voice span.
                if not grace:
                    total += length
            totals.append(total)
        length = max(totals)
        if not 0 < length <= nominal:
            raise ScoreImportError('The explicit pickup is empty or exceeds its time signature.')
        lengths.append(length)
    if len(set(lengths)) != 1:
        raise ScoreImportError('Tracks disagree about the explicit pickup duration.')
    return lengths[0]

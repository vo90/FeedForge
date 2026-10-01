"""Choose shared section annotations from the complete source envelope."""
import re

from .model import ScoreImportError


_NUMBERED_SECTION = re.compile(
    r'(intro|verse|pre-chorus|chorus|post-chorus|bridge|interlude|solo|outro|'
    r'ending|break|breakdown|riff|hook)(?: ([1-9][0-9]*))?\Z')


def _equivalent_label(candidates):
    """Resolve only formatting or an unambiguous optional section number.

    Arbitrary suffixes (especially player names) are not disposable metadata.
    The chosen value is always an authored string, independent of track order.
    """
    normalized = {text: ' '.join(text.split()).casefold() for text in candidates}
    choices = candidates
    if len(set(normalized.values())) != 1:
        parsed = {text: _NUMBERED_SECTION.fullmatch(value) for text, value in normalized.items()}
        if not all(parsed.values()):
            return None
        bases = {match[1] for match in parsed.values()}
        numbers = {match[2] for match in parsed.values() if match[2] is not None}
        if len(bases) != 1 or len(numbers) > 1:
            return None
        choices = {text for text, match in parsed.items() if match[2] is not None} or candidates
    return min(choices, key=lambda text: (len(text) - len(' '.join(text.split())), text.casefold(), text))


def section_label(samples, metadata, eligible, measure):
    labels = []
    for index, (bar, meta) in enumerate(zip(samples, metadata)):
        value = bar.get('marker')
        if value is None or value is False or value == '' or value == {}:
            continue
        if isinstance(value, dict):
            if set(value) - {'text', 'width'}:
                raise ScoreImportError(f'Unsupported section marker in measure {measure + 1}, track {index + 1}.')
            value = value.get('text', '')
        if not isinstance(value, str):
            raise ScoreImportError(f'Invalid section marker in measure {measure + 1}, track {index + 1}.')
        if value:
            labels.append({'trackIndex': index, 'trackId': str(meta.get('id', index)),
                           'eligible': index in eligible, 'text': value,
                           'location': f'parts/{index}/measures/{measure}/marker'})
    preferred = {label['text'] for label in labels if label['eligible']}
    candidates = preferred or {label['text'] for label in labels}
    text = _equivalent_label(candidates) if len(candidates) > 1 else next(iter(candidates), '')
    if text is None:
        scope = 'Guitar/bass tracks' if preferred else 'Fallback tracks'
        error = ScoreImportError(f'{scope} have different section labels in measure {measure + 1}; review is required.')
        error.source_feature = 'arrangement.section_labels'
        error.source_location = {'measure': measure + 1, 'location': f'measures/{measure}/marker'}
        error.source_value = labels
        raise error
    basis = 'guitar_bass' if preferred else 'other_tracks'
    basis += '_equivalent_labels' if len(candidates) > 1 else '_consensus'
    detail = {'measure': measure + 1, 'label': text,
              'basis': basis, 'labels': labels}
    return text, detail if labels else None

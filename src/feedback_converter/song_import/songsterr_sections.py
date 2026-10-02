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
    preferred = [label for label in labels if label['eligible']]
    voters = preferred or labels
    candidates = {label['text'] for label in voters}
    text = _equivalent_label(candidates) if len(candidates) > 1 else next(iter(candidates), '')
    selection = '_equivalent_labels' if len(candidates) > 1 else '_consensus'
    if text is None:
        # One annotation per original source track, before voice projection or
        # Hybrid creation. Formatting variants must not split the same vote.
        groups = {}
        for label in voters:
            key = ' '.join(label['text'].split()).casefold()
            groups.setdefault(key, []).append(label['text'])
        most = max(map(len, groups.values()))
        winners = [values for values in groups.values() if len(values) == most]
        # Dict insertion order is source track order, including only labels
        # tied for the highest count. Always choose an actual authored string.
        text = _equivalent_label(set(winners[0]))
        selection = '_track_order_tiebreak' if len(winners) > 1 else '_majority_label'
    basis = 'guitar_bass' if preferred else 'other_tracks'
    basis += selection
    detail = {'measure': measure + 1, 'label': text,
              'basis': basis, 'labels': labels}
    return text, detail if labels else None

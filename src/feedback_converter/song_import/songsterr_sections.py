"""Choose shared section annotations from the complete source envelope."""
from .model import ScoreImportError


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
    if len(candidates) > 1:
        scope = 'Guitar/bass tracks' if preferred else 'Fallback tracks'
        error = ScoreImportError(f'{scope} have different section labels in measure {measure + 1}; review is required.')
        error.source_feature = 'arrangement.section_labels'
        error.source_location = {'measure': measure + 1, 'location': f'measures/{measure}/marker'}
        error.source_value = labels
        raise error
    text = next(iter(candidates), '')
    detail = {'measure': measure + 1, 'label': text,
              'basis': 'guitar_bass_consensus' if preferred else 'other_tracks_consensus', 'labels': labels}
    return text, detail if labels else None

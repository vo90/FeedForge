"""Located failures for existing link guards; never resolves a musical link."""
from .model import ScoreImportError


def coordinates(source_id):
    fields = source_id.split(':')
    if len(fields) != 6 or fields[0] != 'songsterr' or not all(p.isdigit() for p in fields[1:]):
        return {}
    part, bar, voice, beat, note = map(int, fields[1:])
    return {'measure': bar + 1, 'voice': voice + 1, 'beat': beat + 1, 'note': note + 1,
            'location': f'parts/{part}/measures/{bar}/voices/{voice}/beats/{beat}/notes/{note}'}


def point(note, occurrence, time):
    return {'sourceId': note.source_id, 'occurrence': occurrence + 1,
            'string': note.string + 1, 'fret': None if note.fret == 127 else note.fret,
            'muted': bool(note.effects.get('mt')), 'time': time}


class LinkDiagnostics:
    """Keep only active link origins, separate from renderer decision state."""
    def __init__(self):
        self.pending = {}

    def remember(self, family, key, note, occurrence, time):
        self.pending[family, key] = (note, occurrence, time)

    def resolve(self, family, key):
        self.pending.pop((family, key), None)

    def error(self, message, reason, *, family=None, key=None, destination=None, boundary=None):
        origins = [(kind, item) for (kind, link), item in self.pending.items()
                   if (family is None or kind == family) and (key is None or link == key)]
        if origins:
            loc = coordinates(origins[0][1][0].source_id)
            if loc:
                message += f" Link starts at measure {loc['measure']}, beat {loc['beat']} (occurrence {origins[0][1][1] + 1})."
        if destination:
            loc = coordinates(destination[0].source_id)
            if loc:
                message += f" Destination is measure {loc['measure']}, beat {loc['beat']} (occurrence {destination[1] + 1})."
        error = ScoreImportError(message)
        error.source_feature = 'note.slide_destination' if family in {'slide', 'muted_shift'} else 'note.linked_destination'
        primary = destination or (origins[0][1] if origins else None)
        error.source_location = {'ruleId': 'technique.linked_targets', 'stage': 'timeline'}
        if primary:
            error.source_location.update(coordinates(primary[0].source_id))
            error.source_location['occurrence'] = primary[1] + 1
        # Failure-only, bounded details. Full note identities remain in the source.
        error.source_value = {
            'reason': reason, 'timeBasis': 'score_seconds_before_recording_alignment',
            'stringOrder': 'low_to_high_one_based', 'linkCount': len(origins),
            'links': [{'kind': kind, 'technique': item[0].slide if kind != 'hopo' else 'hopo',
                       **point(*item)} for kind, item in origins[:4]],
            'linksTruncated': len(origins) > 4,
            'destination': point(*destination) if destination else None,
            'boundary': boundary,
        }
        return error

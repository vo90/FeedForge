"""Keep a pending source slide across plain, hidden tied placeholders."""
from .plain_ties import record as plain_record

RULE = 'plain-tie-keeps-pending-slide-attack-target'


def _raw(note, document):
    fields = note.source_id.split(':')
    if len(fields) != 6 or fields[0] != 'songsterr' or not all(v.isdigit() for v in fields[1:]):
        return None
    try:
        p, m, v, b, n = map(int, fields[1:])
        return document['parts'][p]['measures'][m]['voices'][v]['beats'][b]['notes'][n]
    except (KeyError, IndexError, TypeError):
        return None


def _plain(note, raw, *, initial=False):
    allowed = {'pkd', 'fg', 'ac', 'ghost'}
    if initial and raw.get('vibrato') is True:
        allowed |= {'vb', '__finger_vibrato'}
    return not (note.bends or note.hopo or note.slide and not initial or note.slide_in
                or note.pick_scrape or note.whammy or note.trill or note.staccato or note.attack_offset
                or note.effects.get('__finger_vibrato') not in (None, 'slight')
                or any(value and key not in allowed for key, value in note.effects.items()))


def admitted(output, note, articulation, document, *, pending, competing=False,
             unique=True, uninterrupted=True, continuous=False):
    """Only the original literal marker may survive this qualified held chain.

    Unlike outgoing HP, a slide is NOT consumed by the hidden tied entry. Its
    real destination and missing-destination checks remain in the link resolver.
    """
    if (pending is None or pending[0] is not output or pending[1] not in ('shift', 'legato')
            or competing or not unique or not uninterrupted or not continuous
            or not articulation.get('source_unique') or not articulation.get('pending_hopo_history_unique')
            or any(articulation.get(k) for k in ('linked', 'muted_identity', 'bent_identity'))
            or not note.tie or not 0 <= note.fret <= 48 or not 0 <= output['f'] <= 48):
        return False
    raw = _raw(note, document)
    if not isinstance(raw, dict) or raw.get('tie') is not True or not _plain(note, raw):
        return False
    allowed_output = {'t', 's', 'f', 'sus', 'source_ids', 'pkd', 'fg', 'ac', 'ghost', 'vb'}
    if any(value and key not in allowed_output for key, value in output.items()):
        return False
    history = articulation.get('bend_segments', ())
    if not history or history[0][0].tie:
        return False
    first, first_start, _end, first_visit = history[0]
    marker = pending[2]
    if (first.slide != pending[1] or marker['sourceId'] != first.source_id
            or marker['occurrence'] != first_visit + 1
            or first.source_id != output['source_ids'][0]
            or first_start != articulation['start']):
        return False
    for i, (segment, start, end, _visit) in enumerate(history):
        source = _raw(segment, document)
        if (not isinstance(source, dict) or not 0 <= segment.fret <= 48
                or (segment.voice_id, segment.string) != (note.voice_id, note.string)
                or not _plain(segment, source, initial=i == 0)
                or not i and source.get('slide') != pending[1]
                or i and (not segment.tie or source.get('tie') is not True or start != history[i - 1][2])):
            return False
    return True


def record(output, note, track, occurrence, position, end, at, articulation):
    result = plain_record(output, note, track, occurrence, position, end, at)
    marker, start, finish, visit = articulation['bend_segments'][0]
    _, p, m, v, b, n = marker.source_id.split(':')
    result.update(rule=RULE, markerSourceId=marker.source_id,
                  markerLocation=f'parts/{p}/measures/{m}/voices/{v}/beats/{b}/notes/{n}',
                  markerOccurrence=visit + 1, markerStart=at(start), markerEnd=at(finish),
                  authored={'fret': note.fret, 'tie': True, 'slide': marker.slide},
                  used={'fret': output['f'], 'pendingSlide': marker.slide})
    return result

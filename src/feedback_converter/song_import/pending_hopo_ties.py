"""Consume a source HP instruction at its immediate plain held continuation.

Songsterr's tied entry does not create a playable attack. A hidden synthesis
articulation is retained in the receipt, rather than becoming a later HO/PO.
"""
from copy import deepcopy
import hashlib

POLICY = 'songsterr-pending-hopo-immediate-plain-tie-v1'
RULE = 'pending-hopo-consumed-by-immediate-plain-tie'


def _raw(note, document):
    fields = note.source_id.split(':')
    if len(fields) != 6 or fields[0] != 'songsterr' or not all(v.isdigit() for v in fields[1:]):
        return None
    try:
        part, measure, voice, beat, index = map(int, fields[1:])
        return document['parts'][part]['measures'][measure]['voices'][voice]['beats'][beat]['notes'][index]
    except (KeyError, IndexError, TypeError):
        return None


def _plain_segment(note, *, initial=False, marker=False):
    allowed = {'pkd', 'fg', 'ac', 'ghost'}
    if initial:
        allowed |= {'vb', '__finger_vibrato', '__wide_vibrato'}
    if marker:
        allowed.add('__hopo_origin')
    return not (note.bends or note.slide or note.slide_in or note.pick_scrape
                or note.whammy or note.trill or note.staccato or note.attack_offset
                or note.hopo and not initial
                or any(value and key not in allowed for key, value in note.effects.items()))


def admitted(output, note, articulation, document, *, pending, competing=False,
             unique=True, uninterrupted=True, continuous=False):
    """Require the exact marker-to-tie boundary, never proximity or a gap."""
    if (pending is not output or competing or not unique or not uninterrupted or not continuous
            or articulation.get('source_unique') is False
            or articulation.get('pending_hopo_history_unique') is False
            or articulation.get('linked') or articulation.get('muted_identity')
            or articulation.get('bent_identity') or not note.tie
            or not 0 <= output['f'] <= 48 or not 0 <= note.fret <= 48):
        return False
    raw = _raw(note, document)
    if not isinstance(raw, dict) or raw.get('tie') is not True or not _plain_segment(note):
        return False
    allowed_output = {'t', 's', 'f', 'sus', 'source_ids', 'pkd', 'fg', 'ac', 'ghost', 'vb', 'ho', 'po', 'ln'}
    if any(value and key not in allowed_output for key, value in output.items()):
        return False
    segments = articulation.get('bend_segments', ())
    if not segments or segments[0][0].tie:
        return False
    marker = segments[-1][0]
    raw_marker = _raw(marker, document)
    if (not isinstance(raw_marker, dict) or raw_marker.get('hp') is not True
            or not marker.effects.get('__hopo_origin')
            or (marker.voice_id, marker.string) != (note.voice_id, note.string)):
        return False
    for i, (segment, start, end, _visit) in enumerate(segments):
        source = _raw(segment, document)
        if (not isinstance(source, dict)
                or (segment.voice_id, segment.string) != (note.voice_id, note.string)
                or not 0 <= segment.fret <= 48
                or not _plain_segment(segment, initial=i == 0, marker=i == len(segments) - 1)
                or i and (source.get('tie') is not True or start != segments[i - 1][2])):
            return False
        if not i and segment.hopo and not (output.get('ho') or output.get('po')):
            return False
    return True


def record(output, note, track, occurrence, position, end, at, articulation):
    marker, marker_start, marker_end, marker_visit = articulation['bend_segments'][-1]
    _, pi, mi, vi, bi, ni = note.source_id.split(':')
    _, mpi, mmi, mvi, mbi, mni = marker.source_id.split(':')
    return {'trackId': track.id, 'sourceId': note.source_id,
            'originSourceId': output['source_ids'][0], 'markerSourceId': marker.source_id,
            'location': f'parts/{pi}/measures/{mi}/voices/{vi}/beats/{bi}/notes/{ni}',
            'markerLocation': f'parts/{mpi}/measures/{mmi}/voices/{mvi}/beats/{mbi}/notes/{mni}',
            'occurrence': occurrence + 1, 'markerOccurrence': marker_visit + 1,
            'voice': int(note.voice_id), 'string': note.string, 'attack': output['t'],
            'markerStart': at(marker_start), 'markerEnd': at(marker_end),
            'start': at(position), 'end': at(end),
            'authored': {'hp': True, 'tie': True, 'fret': note.fret},
            'used': {'fret': output['f'], 'outgoingHopo': False}, 'rule': RULE}


def archive_evidence(performance, source_path):
    return {'version': 1, 'policy': POLICY, 'timeDomain': 'score_seconds',
            'sourceSha256': hashlib.sha256(source_path.read_bytes()).hexdigest(),
            'consumptions': deepcopy(performance['pendingHopoTieEvidence'])}


def report_findings(performance, report):
    from .compatibility import add_finding
    for row in performance.get('pendingHopoTieEvidence', []):
        add_finding(report, feature='note.pending_hopo_tie', category='game_limitation',
                    impact='display_or_expression',
                    message='The outgoing HO/PO instruction is consumed by its immediate tied continuation. '
                            'The held note, timing and genuine incoming technique are preserved. '
                            'The hidden synthesis articulation is retained in the source and report; '
                            'no extra attack or later HO/PO is added.',
                    location=row['location'] + f"@visit{row['occurrence']}",
                    value={k: row[k] for k in ('authored', 'used', 'rule', 'markerSourceId', 'originSourceId')},
                    trackId=row['trackId'])

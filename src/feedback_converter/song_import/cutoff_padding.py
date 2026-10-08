"""Two ending boundaries: original audio for attacks, bounded padding for tails."""
import math

POLICY = 'cutoff-and-preserve-started-tail-v1'


def selected(alignment):
    receipt = alignment.get('endingPadding', {})
    return receipt.get('version') == 3 and receipt.get('policy') == POLICY


def retained(tracks, duration):
    """Validate all source events, then partition at the ORIGINAL recording end."""
    if type(duration) not in (int, float) or not math.isfinite(duration) or duration <= 0:
        return None
    result, omitted = [], 0
    for track in tracks:
        events = track['events']
        if any(any(type(n.get(k)) not in (int, float) or not math.isfinite(n[k]) for k in ('t', 'end'))
               or n['t'] < 0 or n['end'] < n['t'] for n in events):
            return None
        keep = [n for n in events if n['t'] < duration]
        if events and not keep:
            return None  # Do not silently erase an entire arrangement.
        omitted += len(events) - len(keep)
        result.append({**track, 'events': keep})
    return (result, omitted) if omitted else None


def plan(tracks, duration):
    from .ending_padding import plan_bounds
    partition = retained(tracks, duration)
    if partition is None:
        return None
    keep, count = partition
    spec = plan_bounds(keep, duration)
    return {**spec, 'omitLateAttacks': True, 'omittedNoteCount': count} if spec else None


def cutoff(alignment):
    return alignment['endingPadding']['originalDuration'] + alignment.get('preparation', {}).get('seconds', 0)


def allowed(alignment, duration):
    from .recording_sync import digest, VERSION, PADDING_VERSION
    receipt, report = alignment.get('endingPadding', {}), alignment.get('endingPaddingSync', {})
    return (selected(alignment) and receipt.get('omitLateAttacks') is True
            and type(receipt.get('omittedNoteCount')) is int and receipt['omittedNoteCount'] > 0
            and alignment.get('status') == 'validated' and alignment.get('mapping') == 'piecewise-linear'
            and alignment.get('method') == 'songsterr-video-points-v1'
            and report.get('version') in (VERSION, PADDING_VERSION) and report.get('status') == 'supported'
            and report.get('outroSupported') is True and bool(report.get('windows'))
            and all(w.get('status') == 'supported' for w in report['windows'])
            and report.get('audioDuration') == receipt.get('originalDuration')
            and report.get('mapHash') == alignment.get('provenance', {}).get('mapHash')
            and receipt.get('syncEvidenceHash') == digest(report)
            and 0 < receipt.get('seconds', 0) <= 2
            and abs(cutoff(alignment) + receipt['seconds'] - duration) < 1e-8)


def omission_header(alignment):
    return {'version': 1, 'policy': POLICY, 'audioDuration': cutoff(alignment),
            'syncEvidenceHash': alignment['endingPadding']['syncEvidenceHash']}


def verify_omissions(wanted, alignment, recipe, archive, duration, check):
    """Reconstruct omitted identities from independent source events, never charts."""
    import json
    if (recipe.get('preservationContract', 0) < 102 or not allowed(alignment, duration)
            or any(recipe.get(k) for k in ('recordingEnd', 'recordingSyncFile', 'terminalSlides'))):
        raise ValueError('Combined cutoff and padding require current independent timing evidence.')
    boundary = cutoff(alignment)
    omissions = []
    for part in wanted['parts']:
        # Later acoustic and padding checks need the complete independent source.
        part.setdefault('sync_notes', part['notes'])
        for item in part.get('sync_notes', part['notes']):
            n = item['note']
            if n['t'] >= boundary:
                omissions.append({'trackId': part['source'].id, 'string': n['s'], 'fret': n['f'],
                                  'audioStart': round(n['t'], 6), 'originalDuration': round(n.get('sus', 0), 6)})
        keep = [i for i in part['notes'] if i['note']['t'] < boundary]
        if part['notes'] and not keep:
            raise ValueError('Combined ending would remove an entire arrangement.')
        part['notes'] = keep
    ledger = json.loads(archive.read(recipe['endingOmissionsFile']))
    check.equal('ending_header', 'import/ending', omission_header(alignment),
                {k: v for k, v in ledger.items() if k != 'notes'})
    recorded = ledger.get('notes')
    if not isinstance(recorded, list) or any(not isinstance(n, dict) for n in recorded):
        raise ValueError('Invalid combined ending omissions.')
    check.equal('ending_count', 'import/ending', len(omissions), len(recorded))
    check.equal('ending_count', 'ending-padding/omittedNoteCount', len(omissions), alignment['endingPadding']['omittedNoteCount'])
    if not omissions:
        raise ValueError('Combined ending has no omitted attacks.')
    key = lambda n: (str(n.get('trackId')), n.get('audioStart', -1), n.get('string', -1), n.get('fret', -1))
    for i, (a, b) in enumerate(zip(sorted(omissions, key=key), sorted(recorded, key=key))):
        check.equal('ending_fields', f'import/ending/{i}', sorted(a), sorted(b))
        for field, value in a.items():
            if field in ('audioStart', 'originalDuration'):
                check.near('ending_value', f'import/ending/{i}/{field}', value, b.get(field))
            else:
                check.equal('ending_identity', f'import/ending/{i}/{field}', value, b.get(field))
    return {'omittedEndingNotes': len(omissions)}

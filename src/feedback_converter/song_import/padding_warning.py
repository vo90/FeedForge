"""Source-derived uncertainty for bounded ending silence, never acoustic proof."""
import math

from .activity_coverage import EPSILON, gaps


def merged(ranges):
    result = []
    for row in sorted(ranges, key=lambda r: (r['start'], r['end'])):
        if result and row['start'] <= result[-1]['end']:
            result[-1]['end'] = max(result[-1]['end'], row['end'])
        else:
            result.append(dict(row))
    return result


def coverage(report, tracks):
    missing = gaps(report)
    if missing is None or not isinstance(tracks, list) or not tracks:
        return None
    duration = report['audioDuration']
    if report.get('analysisOriginSeconds', 0) != 0:
        return None  # Callers supply the original recording and original coordinates.
    active, found = [], False
    for track in tracks:
        if not isinstance(track, dict) or not isinstance(track.get('events'), list):
            return None
        for note in track['events']:
            if not isinstance(note, dict):
                return None
            start, end = note.get('t'), note.get('end')
            if (any(type(v) not in (int, float) or not math.isfinite(v) for v in (start, end))
                    or not 0 <= start < duration or end < start):
                return None
            found = True
            for gap in missing:
                if start < gap['end'] - EPSILON and end >= gap['start']:
                    left, right = max(start, gap['start']), min(end, gap['end'])
                    if right > left + EPSILON or gap['start'] <= start < gap['end'] - EPSILON:
                        active.append({'start': round(left, 6), 'end': round(right, 6)})
    if not found:
        return None
    return {'version': 1, 'policy': 'source-padding-coverage-v1',
            'unanalysedRanges': missing, 'unverifiedSourceRanges': merged(active)}


def acceptance(report, alignment, source, tracks):
    from .recording_sync import VERSION, PADDING_VERSION
    from .cutoff_warning import matches_recording
    timing, provenance = alignment.get('sourceTiming', {}), alignment.get('provenance', {})
    if (report.get('version') not in (VERSION, PADDING_VERSION) or report.get('status') != 'inconclusive'
            or type(report.get('outroSupported')) is not bool
            or report.get('suspectedMismatchWindows') != 0
            or alignment.get('method') != 'songsterr-video-points-v1'
            or alignment.get('mapping') != 'piecewise-linear'
            or timing.get('source') != 'songsterr-video-points' or timing.get('status') != 'done'
            or timing.get('feature') is not None or not matches_recording(alignment, source)
            or any(not timing.get(k) or timing[k] != provenance.get(k) for k in ('songId','revisionId','videoId'))
            or not report.get('mapHash') or report['mapHash'] != provenance.get('mapHash')):
        return None
    proof = coverage(report, tracks)
    if proof is None:
        return None
    independent, uncertain = [], list(proof['unverifiedSourceRanges'])
    for window in report['windows']:
        if (window.get('status') not in ('supported', 'inconclusive')
                or window.get('jointEvidence', {}).get('status') == 'suspected_mismatch'):
            return None
        if window['status'] == 'supported':
            if not independent or window['start'] >= independent[-1]['end']:
                independent.append(window)
        else:
            uncertain.append({'start': window['start'], 'end': window['end']})
    if len(independent) < 3 or not uncertain:
        return None
    return {'version': 2, 'policy': 'source-map-padding-warning-v2', 'status': 'accepted_with_warning',
            'message': 'Imported with timing warnings. Songsterr timing retained; short ending silence added.',
            'timeDomain': 'original_recording_seconds', 'uncertainRanges': merged(uncertain),
            'supportedPassages': len(independent), 'everyNoteVerified': False, 'sourceCoverage': proof}

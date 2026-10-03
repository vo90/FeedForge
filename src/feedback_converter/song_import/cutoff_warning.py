"""Keep an imperfect source clock without certifying its uncertain passages.

A warning permits the existing recording-end projection, not retiming. The
selected recording must still be corroborated by independent source passages.
There are no song, arrangement or ending-length exceptions in this policy.
"""
import math

POLICY = 'source-timing-cutoff-warning-v1'


def matches_recording(alignment, audio_source):
    timing = alignment.get('sourceTiming', {})
    return (audio_source.get('kind') == 'youtube' and bool(timing.get('videoId'))
            and audio_source.get('videoId') == timing['videoId'])


def acceptance(report, alignment):
    from .recording_sync import PREVIOUS_VERSION
    timing = alignment.get('sourceTiming', {})
    provenance = alignment.get('provenance', {})
    if (report.get('version') != PREVIOUS_VERSION
            or report.get('status') not in ('inconclusive', 'suspected_mismatch')
            or alignment.get('method') != 'songsterr-video-points-v1'
            or alignment.get('mapping') != 'piecewise-linear'
            or timing.get('source') != 'songsterr-video-points'
            or timing.get('status') != 'done' or timing.get('feature') is not None
            or any(not timing.get(k) or timing[k] != provenance.get(k)
                   for k in ('songId', 'revisionId', 'videoId'))
            or not report.get('mapHash') or report['mapHash'] != provenance.get('mapHash')):
        return None
    origin = report.get('analysisOriginSeconds', 0)
    duration = report.get('audioDuration')
    if (any(type(v) not in (int, float) or not math.isfinite(v) for v in (duration, origin))
            or not 0 <= origin <= 2 or duration <= origin):
        return None
    # Evidence window coordinates refer to the original recording, before the
    # optional preparation silence. Keep warning locations in that domain.
    duration -= origin
    windows = report.get('windows', [])
    if not isinstance(windows, list) or not windows:
        return None
    independent, uncertain = [], []
    for i, window in enumerate(windows):
        if not isinstance(window, dict):
            return None
        start, end = window.get('start'), window.get('end')
        status = window.get('status')
        if (any(type(v) not in (int, float) or not math.isfinite(v) for v in (start, end))
                or not 0 <= start < end <= duration + .001
                or status not in ('supported', 'inconclusive', 'suspected_mismatch')
                or (i and (start <= windows[i-1]['start'] or end <= windows[i-1]['end']
                           or start > windows[i-1]['end']))):
            return None
        if status == 'supported':
            if window.get('jointEvidence', {}).get('status') == 'suspected_mismatch':
                return None
            if not independent or start >= independent[-1]['end']:
                independent.append(window)
        elif uncertain and start <= uncertain[-1]['end']:
            uncertain[-1]['end'] = end
        else:
            uncertain.append({'start': start, 'end': end})
    # A wholly unconfirmed recording must still use the matching-audio fallback.
    # Overlapping windows must not count as independent corroboration.
    if (len(independent) < 3 or not uncertain or windows[0]['start'] > .001
            or abs(windows[-1]['end']-duration) > .001):
        return None
    return {'version': 1, 'policy': POLICY, 'status': 'accepted_with_warning',
            'message': 'Imported with timing warnings. Songsterr timing was retained; some notes may not match the recording.',
            'timeDomain': 'original_recording_seconds', 'uncertainRanges': uncertain,
            'supportedPassages': len(independent), 'everyNoteVerified': False}

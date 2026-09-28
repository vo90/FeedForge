"""Bounded acceptance of source timing without claiming an acoustic ending match.

This policy never authorizes missing attacks, changes the recording map, or
changes the acoustic assessment. Structural/source checks remain mandatory.
"""
import math

POLICY = 'source-map-ending-warning-v1'
MESSAGE = 'Ending sync not independently confirmed. Songsterr timing retained; short ending silence added.'


def acceptance(report, alignment, audio_source):
    """Return a reproducible warning receipt only for a supported song body."""
    from .recording_sync import VERSION
    timing = alignment.get('sourceTiming', {})
    provenance = alignment.get('provenance', {})
    if (report.get('version') != VERSION or report.get('status') != 'inconclusive'
            or report.get('outroSupported') is not False
            or report.get('suspectedMismatchWindows') != 0
            or alignment.get('method') != 'songsterr-video-points-v1'
            or alignment.get('mapping') != 'piecewise-linear'
            or timing.get('source') != 'songsterr-video-points'
            or timing.get('status') != 'done' or timing.get('feature') is not None
            or audio_source.get('kind') != 'youtube' or not timing.get('videoId')
            or audio_source.get('videoId') != timing['videoId']
            or any(not timing.get(k) or timing[k] != provenance.get(k)
                   for k in ('songId', 'revisionId', 'videoId'))
            or not report.get('mapHash') or report['mapHash'] != provenance.get('mapHash')):
        return None
    windows = report.get('windows', [])
    duration = report.get('audioDuration')
    if (type(duration) not in (int, float) or not math.isfinite(duration) or duration <= 0
            or not windows):
        return None
    for i, window in enumerate(windows):
        start, end = window.get('start'), window.get('end')
        if (any(type(v) not in (int, float) or not math.isfinite(v) for v in (start, end))
                or not 0 <= start < end <= duration + .001
                or window.get('status') not in ('supported', 'inconclusive')
                or window.get('jointEvidence', {}).get('status') == 'suspected_mismatch'
                or (i and (start <= windows[i-1]['start'] or start > windows[i-1]['end']
                           or end <= windows[i-1]['end']))):
            return None
    failed = [i for i, window in enumerate(windows) if window['status'] != 'supported']
    if (not failed or failed != list(range(failed[0], len(windows)))
            or windows[0]['start'] > .001 or abs(windows[-1]['end']-duration) > .001):
        return None
    left = windows[failed[0]]['start']
    # Uncertainty must be a short final region, never an unknown song body.
    if not 0 < duration-left <= min(32.0, duration*.2):
        return None
    independent_body = []
    for window in windows[:failed[0]]:
        if (window['end'] <= left and
                (not independent_body or window['start'] >= independent_body[-1]['end'])):
            independent_body.append(window)
    if len(independent_body) < 3:
        return None
    return {'version': 1, 'policy': POLICY, 'status': 'accepted_with_warning',
            'message': MESSAGE, 'uncertainStart': left, 'originalDuration': duration,
            'supportedBodyWindows': len(independent_body),
            'uncertainWindows': len(failed), 'everyNoteVerified': False}

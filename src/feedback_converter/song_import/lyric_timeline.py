"""Existing FeedPak lyric wire format, on the final recording clock."""
from copy import deepcopy
import hashlib
import math

from .alignment import map_time
from .songsterr_lyrics import POLICY
from .tone_timeline import clock_parameters, digest


def phrases(events):
    """Bound phrases for the unchanged renderer, at complete word boundaries."""
    output, words, width, start = [], 0, 0, None
    for i, item in enumerate(events):
        text = ' '.join(item['w'].split())
        if not text:
            continue
        if start is None:
            start = item['t']
        output.append({**item, 'w': text})
        width += len(text.rstrip('-')) + (0 if text.endswith('-') else 1)
        words += 0 if text.endswith('-') else max(1, len(text.split()))
        following = events[i + 1] if i + 1 < len(events) else None
        gap = following['t'] - (item['t'] + item['d']) if following else math.inf
        hard_end = following is None or gap >= 1.25
        line_end = hard_end or not text.endswith('-') and (
            text.endswith(('.', '!', '?', ';', ':')) or '\n' in item['w'] or '\r' in item['w']
            or words >= 8 or width >= 56 or following['t'] - start >= 8)
        if line_end:
            output[-1]['w'] = text.rstrip('-') + '+'
            words, width, start = 0, 0, None
    return output


def export(timeline, alignment, duration):
    report = {k: deepcopy(v) for k, v in timeline.items() if k != 'events'}
    report.update(timeDomain='recording_seconds', acousticAccuracyAssessed=False,
                  phrasePolicy='source-punctuation-gaps-bounded-phrases-v1',
                  events=[], exportedEvents=0, clippedEvents=0, omittedEvents=0)
    events = []
    if timeline['status'] != 'ready':
        return events, report
    try:
        for row in timeline['events']:
            lo = map_time(alignment, row['time'], allow_negative=True)
            hi = map_time(alignment, row['end'], allow_negative=True)
            from .collapsed_opening import omitted, score_end
            if omitted(alignment, row['time']) and row['end'] <= score_end(alignment) + 1e-8:
                report['omittedEvents'] += 1
                report['events'].append({**row, 'audioStart': lo, 'audioEnd': hi, 'disposition': 'collapsed_opening'})
                continue
            if not all(math.isfinite(v) for v in (lo, hi)) or hi <= lo:
                raise ValueError('Invalid lyric timing interval.')
            start, end = max(0., lo), min(duration, hi)
            evidence = {**row, 'audioStart': lo, 'audioEnd': hi}
            if end <= start:
                report['omittedEvents'] += 1
                evidence['disposition'] = 'outside_recording'
            else:
                if start != lo or end != hi:
                    report['clippedEvents'] += 1
                evidence['disposition'] = 'clipped' if start != lo or end != hi else 'exported'
                event = {'t': round(start, 6), 'd': round(end - start, 6), 'w': row['text']}
                if event['d'] <= 0 or events and event['t'] < events[-1]['t'] + events[-1]['d'] - 0.0000011:
                    raise ValueError('Lyrics overlap or collapse at export precision.')
                events.append(event)
            report['events'].append(evidence)
        events = phrases(events)
        report.update(status='imported' if events else 'outside_recording', exportedEvents=len(events),
                      lyricsSha256=digest(events))
    except (ValueError, TypeError, KeyError) as exc:
        report.update(status='unsupported', reason=str(exc), exportedEvents=0)
        events = []
    return events, report


def receipt(report, source_path, audio_hash, alignment):
    return {**report, 'version': 1, 'policy': POLICY,
            'sourceSha256': hashlib.sha256(source_path.read_bytes()).hexdigest(),
            'audioSha256': audio_hash, 'alignmentSha256': digest(clock_parameters(alignment))}

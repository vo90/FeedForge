"""Descriptive chart activity, separate from inferred musical ownership.

These are retained tab-note durations, not measurements of audible guitar or
musical quality. Zero-duration attacks and ghosts do not imply sustained time.
"""
from collections import defaultdict
from bisect import bisect_left, bisect_right

SELECTION_REVISION = 4
EPS = 1.1e-6


def _spans(chart):
    atoms = [(n, n['t']) for n in chart.get('notes', [])]
    atoms.extend((n, n.get('t', c['t'])) for c in chart.get('chords', []) for n in c.get('notes', []))
    return [(float(t), float(t) + float(n.get('sus', 0))) for n, t in atoms
            if not n.get('ghost') and float(n.get('sus', 0)) > EPS]


def _attacks(chart, *, pitched):
    result = []
    for key in ('notes', 'chords'):
        for event in chart.get(key, []):
            notes = [event] if key == 'notes' else event.get('notes', [])
            live = [n for n in notes if not n.get('ghost') and (not pitched or not n.get('mt'))]
            if live:
                result.append(min(float(n.get('t', event['t'])) for n in live))
    return sorted(set(result))


def _rest_windows(originals, names, compatible, hybrid, regions):
    """Group source activity by the player's actual rests, including donor rests.

    Zero-duration played attacks still interrupt a rest. Source attack counts
    are per source, so doubled guitars cannot inflate the maximum count.
    This does not establish that a source gesture can safely be inserted.
    """
    attacks = {tid: _attacks(originals[tid]['chart'], pitched=True) for tid in names}
    played = _attacks(hybrid, pitched=False)
    ends = [r['end'] for r in regions] + played + [t for values in attacks.values() for t in values]
    if not ends:
        return []
    stop = max(ends)
    spans = sorted([*_spans(hybrid), *((t, t) for t in played)])
    windows, cursor = [], 0.0
    for start, end in [*spans, (stop, stop)]:
        if start > cursor + EPS:
            windows.append((cursor, min(start, stop)))
        cursor = max(cursor, end)
        if cursor >= stop:
            break
    starts = [r['start'] for r in regions]
    region_ends = [r['end'] for r in regions]
    result = []
    for lo, hi in windows:
        nearby = regions[bisect_right(region_ends, lo + EPS):bisect_left(starts, hi - EPS)]
        durations = defaultdict(float)
        for r in nearby:
            durations[r['reason']] += max(0, min(hi, r['end']) - max(lo, r['start']))
        source_attacks = []
        point = bisect_left(played, lo - EPS)
        attack_lo = lo + EPS if point < len(played) and played[point] <= lo + EPS else lo - EPS
        terminal = bisect_left(played, hi - EPS)
        source_only_end = abs(hi - stop) <= EPS and (terminal == len(played) or played[terminal] > hi + EPS)
        attack_hi = hi + EPS if source_only_end else hi - EPS
        for tid, values in attacks.items():
            count = bisect_left(values, attack_hi) - bisect_left(values, attack_lo)
            if count:
                source_attacks.append({'id': tid, 'name': names[tid], 'count': count, 'compatible': tid in compatible})
        if not nearby and not source_attacks:
            continue
        max_attacks = max((r['count'] for r in source_attacks if r['compatible']), default=0)
        result.append({'start': round(lo, 6), 'end': round(hi, 6),
                       'compatibleActiveSeconds': round(durations['compatible_source_not_selected'], 6),
                       'incompatibleOnlySeconds': round(durations['only_incompatible_setup'], 6),
                       'maxCompatibleSourceAttacks': max_attacks,
                       'kind': 'new_attacks' if max_attacks else 'no_pitched_attacks' if durations['compatible_source_not_selected'] else 'incompatible_setup',
                       'sourceAttacks': source_attacks})
    return result


def activity_summary(originals, tracks, main_id, hybrid, *, include_rest_windows=True):
    """Union source durations and expose gaps without rewarding duplicates.

    Sweep exact recording-time boundaries. Regions are split when a compatible
    source becomes available, so an incompatible-only explanation never hides
    a compatible option later in that same gap. Other omitted notes, such as
    high frets removed during import, remain in their existing omission report.
    """
    main = next(t for t in tracks if t['id'] == main_id)
    compatible = set()
    edges = defaultdict(list)
    names = {}
    for track in tracks:
        tid = track['id']
        if track.get('instrument') != 'guitar' or tid not in originals:
            continue
        names[tid] = track.get('name', tid)
        if track['tuning'] == main['tuning'] and track.get('capo', 0) == main.get('capo', 0):
            compatible.add(tid)
        for start, end in _spans(originals[tid]['chart']):
            edges[start].append((tid, 1))
            edges[end].append((tid, -1))
    hybrid_key = object()
    for start, end in _spans(hybrid):
        edges[start].append((hybrid_key, 1))
        edges[end].append((hybrid_key, -1))
    counts = defaultdict(int)
    boundaries = sorted(edges)
    source_seconds = selected_seconds = 0.0
    regions = []
    for index, start in enumerate(boundaries[:-1]):
        for tid, delta in edges[start]:
            counts[tid] += delta
        end = boundaries[index + 1]
        active = {tid for tid, count in counts.items() if tid is not hybrid_key and count > 0}
        sounding = counts[hybrid_key] > 0
        if active:
            source_seconds += end - start
        if sounding:
            selected_seconds += end - start
        if not active or sounding or end - start <= EPS:
            continue
        reason = 'compatible_source_not_selected' if active & compatible else 'only_incompatible_setup'
        if regions and abs(regions[-1]['end'] - start) <= EPS and regions[-1]['reason'] == reason:
            regions[-1]['end'] = end
            regions[-1]['trackIds'].update(active)
        else:
            regions.append({'start': start, 'end': end, 'reason': reason, 'trackIds': set(active)})
    totals = {reason: sum(r['end'] - r['start'] for r in regions if r['reason'] == reason)
              for reason in ('only_incompatible_setup', 'compatible_source_not_selected')}
    result = {'scope': 'retained_non_ghost_guitar_note_durations',
            'sourceActiveSeconds': round(source_seconds, 6),
            'hybridActiveSeconds': round(selected_seconds, 6),
            'unfilledSeconds': round(sum(totals.values()), 6),
            'incompatibleOnlySeconds': round(totals['only_incompatible_setup'], 6),
            'compatibleUnfilledSeconds': round(totals['compatible_source_not_selected'], 6),
            'regions': [{**r, 'start': round(r['start'], 6), 'end': round(r['end'], 6),
                         'trackIds': sorted(r['trackIds']),
                         'sources': [{'id': tid, 'name': names[tid]} for tid in sorted(r['trackIds'])]}
                        for r in regions]}
    if include_rest_windows:
        result['restWindowScope'] = 'continuous_hybrid_rests_with_retained_source_activity'
        result['restWindows'] = _rest_windows(originals, names, compatible, hybrid, regions)
    return result

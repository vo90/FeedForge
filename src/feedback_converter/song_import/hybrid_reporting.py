"""Descriptive chart activity, separate from inferred musical ownership.

These are retained tab-note durations, not measurements of audible guitar or
musical quality. Zero-duration attacks and ghosts do not imply sustained time.
"""
from collections import defaultdict

SELECTION_REVISION = 2
EPS = 1.1e-6


def _spans(chart):
    atoms = [(n, n['t']) for n in chart.get('notes', [])]
    atoms.extend((n, n.get('t', c['t'])) for c in chart.get('chords', []) for n in c.get('notes', []))
    return [(float(t), float(t) + float(n.get('sus', 0))) for n, t in atoms
            if not n.get('ghost') and float(n.get('sus', 0)) > EPS]


def activity_summary(originals, tracks, main_id, hybrid):
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
    return {'scope': 'retained_non_ghost_guitar_note_durations',
            'sourceActiveSeconds': round(source_seconds, 6),
            'hybridActiveSeconds': round(selected_seconds, 6),
            'unfilledSeconds': round(sum(totals.values()), 6),
            'incompatibleOnlySeconds': round(totals['only_incompatible_setup'], 6),
            'compatibleUnfilledSeconds': round(totals['compatible_source_not_selected'], 6),
            'regions': [{**r, 'start': round(r['start'], 6), 'end': round(r['end'], 6),
                         'trackIds': sorted(r['trackIds']),
                         'sources': [{'id': tid, 'name': names[tid]} for tid in sorted(r['trackIds'])]}
                        for r in regions]}

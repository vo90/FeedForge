"""Pitch-reference hypotheses for diagnostics only, never musical correction.

The audio-only estimator is circular modulo 100 cents. Resolve its neighbouring
branches against fixed source times, using disjoint selection/held-out thirds.
This evidence must not authorize opening repair or recording-end edits.
"""
import math

import numpy as np

from . import recording_sync as rs

VERSION = 'local-recording-clock-v2'
POLICY = 'whole-recording-pitch-reference-v1'


def _support(report):
    windows = report['windows']
    partitions = []
    for indices in np.array_split(np.arange(len(windows)), 3):
        rows = [windows[int(i)] for i in indices]
        supported = sum(w['status'] == 'supported' for w in rows)
        partitions.append({'windows': len(rows), 'supported': supported,
                           'accepted': len(rows) >= 2 and supported >= math.ceil(.75 * len(rows))
                           and not any(w['status'] == 'suspected_mismatch' for w in rows)})
    return partitions


def _strength(report, indices):
    # One vote per passage; duplicated/unison arrangements cannot add votes.
    return float(np.mean([max((p['jointAtMap'] for p in report['windows'][int(i)]['parts']), default=0)
                          for i in indices])) if len(indices) else 0.0


def select(candidates, baseline):
    """Choose on the first third; require independent middle/end corroboration."""
    count = len(candidates[0]['report']['windows'])
    partitions = np.array_split(np.arange(count), 3)
    evidence = []
    for c in candidates:
        report = c['report']
        support = _support(report)
        evidence.append({'cents': c['cents'], 'referenceHz': c['referenceHz'],
                         'partitions': support,
                         'strengths': [round(_strength(report, ids), 6) for ids in partitions]})
    first = sorted(range(len(candidates)), key=lambda i: evidence[i]['strengths'][0], reverse=True)
    selected = first[0]
    winner = evidence[selected]
    # A shared non-default reference needs a clear advantage at the unchanged
    # clock in every partition. Never search each passage for its own tuning.
    corroborated = all(p['accepted'] for p in winner['partitions']) and all(
        winner['strengths'][j] >= max(.05, 1.5 * max(
            (e['strengths'][j] for i, e in enumerate(evidence) if i != selected), default=0))
        for j in range(3))
    if not corroborated:
        return baseline, {'policy': POLICY, 'status': 'inconclusive', 'candidates': evidence}
    chosen = candidates[selected]
    cents = chosen['cents']
    return chosen['report'], {'policy': POLICY, 'status': 'corroborated',
                             'referenceHz': chosen['referenceHz'], 'cents': cents,
                             'selectionPartition': 'first-third', 'heldOutPartitions': ['middle-third', 'last-third'],
                             'scope': 'shared-timing-diagnostic-only', 'candidates': evidence,
                             'pitchDifference': abs(cents) >= 20}


def assess(tracks, path, duration, calibration, baseline, *, feature_session=None):
    from .local_sync import assess_features
    # Too little independent material: retain the existing diagnostic.
    if (baseline['status'] == 'supported' and abs(calibration['cents']) < 20) or len(baseline['windows']) < 6:
        return baseline, {'policy': POLICY, 'status': 'not_required' if baseline['status'] == 'supported'
                          else 'insufficient_coverage'}
    cents = calibration['cents']
    hypotheses = [0.0] + [round(cents + shift, 3) for shift in (-100, 0, 100)
                         if abs(cents + shift) <= 100 and abs(cents + shift) >= 5]
    if feature_session is not None:
        feature_session.prepare([(round(440 * 2 ** (value / 1200), 6), 28)
                                 for value in hypotheses if value != cents])
    candidates = []
    for value in hypotheses:
        reference = round(440 * 2 ** (value / 1200), 6)
        if value == cents:
            report = baseline
            reference = calibration['referenceHz']
        else:
            if feature_session is None:
                if hasattr(path, 'seek'):
                    path.seek(0)
                pitch, flux, _ = rs.features(path, reference_hz=reference)
            else:
                pitch, flux, _ = feature_session.features(reference_hz=reference)
            report = assess_features(tracks, pitch, flux, duration)
        candidates.append({'cents': value, 'referenceHz': reference, 'report': report})
    return select(candidates, baseline)


def warning(report):
    reference = report.get('pitchReference', {})
    if reference.get('status') != 'corroborated' or not reference.get('pitchDifference'):
        return None
    direction = 'higher' if reference['cents'] > 0 else 'lower'
    timing = 'Timing is supported' if report['status'] == 'supported' else 'Timing remains uncertain'
    return (f"{timing}, but the recording's pitch appears about {abs(reference['cents']):.0f} cents "
            f"{direction} than the tab's tuning (100 cents is one semitone). Playing along may sound out of tune. "
            "No pitch correction was applied to the source tab or recording.")

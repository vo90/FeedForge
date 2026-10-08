"""Corroborate suspected timing errors; never authorize edits to the music."""
import numpy as np

VERSION = 'local-recording-clock-v3'
POLICY = 'independent-attacks-and-disjoint-passages-v1'


def corroborate(pitch, attacks, lags, candidate):
    """Require pitch and attacks at one unique offset in two disjoint halves.

    Scores are engineering gates, not probabilities. Each half must contain
    four distinct source attacks. A weak/ambiguous observation stays unknown.
    These checks only downgrade suspected mismatches; they never create passes.
    """
    near = abs(lags - candidate) <= .08001
    at_map = abs(lags) <= .08001
    other = abs(lags - candidate) >= .20
    rows = []
    reasons = []
    for indices in np.array_split(np.arange(pitch.shape[1]), 2):
        if len(indices) < 4:
            rows.append({'groups': len(indices), 'accepted': False, 'reasons': ['insufficient_attacks']})
            reasons.append('insufficient_attacks')
            continue
        curves = {'pitch': pitch[:, indices].mean(axis=1),
                  'attack': attacks[:, indices].mean(axis=1),
                  'joint': (pitch[:, indices] * attacks[:, indices]).mean(axis=1)}
        metrics = {}
        failed = []
        for name, curve in curves.items():
            strength = float(curve[near].max())
            best = float(curve.max())
            distant = float(curve[other].max())
            contrast = strength / max(float(np.median(curve)), 1e-8)
            metrics[name] = {'atCandidate': round(strength, 6), 'best': round(best, 6),
                             'atMap': round(float(curve[at_map].max()), 6),
                             'contrast': round(contrast, 6), 'distantBest': round(distant, 6),
                             'bestOffset': round(float(lags[int(curve.argmax())]), 3)}
            if strength < .90 * best or strength <= 0:
                failed.append(name + '_offset_disagrees')
            # A sustain has a broad pitch plateau; repeated picking has
            # several attack peaks. Only a competing paired pitch+attack
            # match constitutes an alternative clock. Each individual signal
            # must still support this candidate near its own strongest peak.
            if name == 'joint' and distant >= .90 * strength:
                failed.append(name + '_ambiguous_offset')
        attack = metrics['attack']
        if attack['contrast'] < 1.5 or attack['atMap'] >= .80 * attack['atCandidate']:
            failed.append('weak_attack_evidence')
        if metrics['pitch']['atCandidate'] < .12 or metrics['joint']['atCandidate'] < .15:
            failed.append('weak_pitch_or_joint_evidence')
        rows.append({'groups': len(indices), 'accepted': not failed, 'reasons': failed, 'metrics': metrics})
        reasons.extend(failed)
    return {'policy': POLICY, 'status': 'corroborated' if not reasons else 'inconclusive',
            'candidateOffset': round(float(candidate), 3), 'halves': rows,
            'reasons': sorted(set(reasons))}


def summary(report):
    return {'suspectedMismatchRanges': [{'start': w['start'], 'end': w['end']}
                                      for w in report['windows'] if w['status'] == 'suspected_mismatch'],
            'unverifiedWindows': report['windowCount'] - report['supportedWindows']}


def warning(report, *, opening_repaired=False):
    if report.get('status') != 'inconclusive':
        return None
    ending = ('The recorded opening adjustment was retained; later Songsterr timing was preserved.'
              if opening_repaired else "Songsterr's timing was preserved.")
    return (f"Timing not fully verified. Automatic checks supported timing in {report['supportedWindows']} "
            f"of {report['windowCount']} passages. The remaining passages could not be reliably verified; "
            f"this does not necessarily mean they are wrong. {ending}")

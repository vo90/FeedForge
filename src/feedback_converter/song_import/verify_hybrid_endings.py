"""Independent proof of an optional phrase's accepted recording endpoint.

Uses the verifier's raw performed source facts and independently verified
original chart, never the producer's projection or terminal adjustment ledger.
"""
import math

TOL = 0.0000011


def audit(passage, part, donor, recipe, alignment, duration, at, check):
    ending = passage.get('acceptedEnding')
    if ending is None:
        return None
    location = 'hybrid/passages/acceptedEnding'
    valid = (isinstance(ending, dict)
             and set(ending) == {'policy', 'sourceEnd', 'sourceRecordingEnd'}
             and ending.get('policy') == 'accepted-terminal-sustain-v1'
             and all(type(ending.get(k)) in (int, float) and math.isfinite(ending[k])
                     for k in ('sourceEnd', 'sourceRecordingEnd')))
    if not valid:
        check.fail('hybrid_ending', location, 'Invalid accepted ending provenance.')
        return None
    a, b, raw_end = at(passage['start']), at(passage['end']), at(ending['sourceEnd'])
    policy = recipe.get('terminalSustains', {})
    # The original-chart verifier already independently proved this policy and
    # every adjusted technique. Require that authority here too.
    authorized = (policy == alignment.get('terminalSustains')
                  and policy.get('policy') in {'trim-final-sustain-v1', 'trim-long-held-tail-with-padding-v1'}
                  and alignment.get('method') == 'songsterr-video-points-v1'
                  and alignment.get('mapping') == 'piecewise-linear')
    if (recipe.get('preservationContract', 0) < 58 or not authorized
            or passage.get('priority') in {'solo', 'lead'}
            or not a < b or raw_end <= duration + TOL):
        check.fail('hybrid_ending', location, 'The accepted ending has no terminal-sustain authority.')
    check.near('hybrid_ending', location + '/sourceRecordingEnd', raw_end, ending['sourceRecordingEnd'])
    check.near('hybrid_ending', location + '/end', duration, b)
    raw = [r['note'] for r in part['notes'] if a - TOL <= r['note']['t'] < raw_end - TOL]
    actual = [*donor['notes'], *[{'t': c['t'], **n} for c in donor['chords'] for n in c['notes']]]
    actual = [n for n in actual if a - TOL <= n['t'] < raw_end - TOL]
    key = lambda n: (n['s'], n['f'], n['t'])
    raw, actual = sorted(raw, key=key), sorted(actual, key=key)
    check.equal('hybrid_ending_attacks', location, len(raw), len(actual))
    trimmed = False
    for source, final in zip(raw, actual):
        check.equal('hybrid_ending_attacks', location, (source['s'], source['f']), (final['s'], final['f']))
        check.near('hybrid_ending_attacks', location, source['t'], final['t'])
        end = final['t'] + final.get('sus', 0)
        if source['t'] >= duration or final['t'] >= duration or end > duration + TOL:
            check.fail('hybrid_ending_attacks', location, 'A source attack was outside the accepted recording.')
        original_end = source['t'] + source.get('sus', 0)
        trimmed |= original_end > duration + TOL and end < original_end - TOL
    if not trimmed:
        check.fail('hybrid_ending', location, 'No accepted source sustain establishes this ending.')
    return ending

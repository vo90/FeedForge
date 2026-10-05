"""Independently check source-player boundary selection from retained evidence.

Does not call the producer's parser, timeline renderer, or synchronization code.
"""
import hashlib
import json
import math

from .verify_timeline import Clock, visits


def verify_source_timing(source, alignment, recipe, timing, check, *, strums=None):
    def fail(message):
        check.fail('source_timing', 'import/source-timing', message)

    keys = {'version', 'source', 'songId', 'revisionId', 'videoId', 'status', 'feature', 'points'}
    if (source.format != 'songsterr' or not isinstance(timing, dict) or set(timing) != keys
            or timing.get('version') != 1 or timing.get('source') != 'songsterr-video-points'
            or timing.get('status') != 'done' or timing.get('feature') not in (None, 'alternative')):
        fail('Invalid retained recording timing format.'); return
    points = timing.get('points')
    if (not isinstance(points, list) or len(points) < 2 or len(points) > 100_000
            or any(type(v) not in (float, int) or not math.isfinite(v) for v in points)
            or any(a > b for a, b in zip(points, points[1:]))):
        fail('Retained recording points must be finite and strictly increasing.'); return
    collapsed_count = 0
    while collapsed_count+1 < len(points) and points[collapsed_count+1] == points[0]:
        collapsed_count += 1
    if (collapsed_count == len(points)-1 or any(a >= b for a,b in zip(points[collapsed_count:], points[collapsed_count+1:]))
            or collapsed_count and recipe.get('preservationContract',0) < 87):
        fail('Only an exact collapsed opening prefix is supported.'); return
    provenance = alignment.get('provenance', {})
    for key, value in source.identity.items():
        check.equal('source_timing_identity', 'import/source-timing/' + key, value, timing.get(key))
    check.equal('source_timing_recording', 'import/source-timing/videoId',
                recipe.get('audioSource', {}).get('videoId'), timing['videoId'])
    check.equal('source_timing_recording', 'manifest/song_import/audioSource/kind',
                'youtube', recipe.get('audioSource', {}).get('kind'))
    check.equal('source_timing_evidence', 'import/source-timing', alignment.get('sourceTiming'), timing)
    digest = hashlib.sha256(json.dumps(timing, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    check.equal('source_timing_hash', 'alignment/provenance/mapHash', digest, provenance.get('mapHash'))
    check.equal('source_timing_recipe', 'manifest/song_import/alignment/provenance',
                provenance, recipe.get('alignment', {}).get('provenance'))
    order = visits(source)
    # Independently qualify the newly admitted repeat/tempo combination from
    # raw bars, never from the producer's timeline capability flags.
    repeat_tempos = any(b.repeat_count for b in source.bars) and any(q > 0 for b in source.bars for q in b.tempos)
    expected_repeat_policy = None
    if repeat_tempos:
        starts, spans = [], []
        for i, bar in enumerate(source.bars):
            if bar.endings:
                fail('Repeat/tempo ending inheritance is not qualified.'); return
            if bar.repeat_start:
                starts.append(i)
            if bar.repeat_count:
                first = starts.pop() if starts else 0
                spans.append((first, i))
        covered = set()
        for first, last in spans:
            region = set(range(first, last + 1))
            if covered & region or any(source.bars[i].tempos for i in region):
                fail('Tempo changes inside repeats or nested repeat clocks are not qualified.'); return
            covered.update(region)
        if starts or not spans:
            fail('Unverified repeat structure.'); return
        expected_repeat_policy = 'constant-repeat-with-external-tempos-v1'
    check.equal('source_timing_repeat_tempos', 'alignment/provenance/repeatTempoPolicy',
                expected_repeat_policy, provenance.get('repeatTempoPolicy'))
    clock = Clock(source, order)
    boundaries = [*clock.measure_starts, clock.quarters]
    if collapsed_count >= len(order):
        fail('Skipped opening covers the complete score.'); return
    skipped = alignment.get('collapsedOpening')
    if collapsed_count:
        if (not isinstance(skipped,dict) or set(skipped) != {'version','policy','measureCount','scoreEnd','recordingTime','noteCount'}
                or alignment.get('openingRepair') or provenance.get('openingStrum')):
            fail('Invalid skipped-opening policy.'); return
        for key,value in {'version':1,'policy':'songsterr-collapsed-opening-v1','measureCount':collapsed_count,'recordingTime':points[0]}.items():
            check.equal('collapsed_opening_policy','alignment/collapsedOpening/'+key,value,skipped.get(key))
        check.near('collapsed_opening_policy','alignment/collapsedOpening/scoreEnd',
                   float(clock.at(boundaries[collapsed_count])),skipped.get('scoreEnd'),1e-8)
    elif skipped is not None:
        fail('A strictly increasing map cannot skip opening bars.')
    required = len(boundaries)
    retained = points[:required]
    while len(retained) < required:
        retained.append(retained[-1] + (points[-1] - points[-2]))
    policy = {'version': 1, 'rule': 'songsterr-shared-boundary-prefix',
              'supplied': len(points), 'used': required,
              'unusedTrailing': max(0, len(points) - required),
              'inferredTrailing': max(0, required - len(points))}
    check.equal('source_timing_policy', 'alignment/provenance/boundaryPolicy', policy, provenance.get('boundaryPolicy'))
    anchors = alignment.get('anchors', [])
    repair = alignment.get('openingRepair')
    if repair:
        if repair.get('version') != 'bounded-opening-map-v1' or repair.get('status') != 'supported':
            fail('Unverified opening repair.'); return
        if repair.get('originalBoundaries') != retained[:2] or repair.get('lockedBoundary') != retained[2]:
            fail('Opening repair does not belong to this original map.'); return
        replacement=repair.get('replacementBoundaries')
        if (not isinstance(replacement,list) or len(replacement)!=2
                or any(type(v) not in (int,float) or not math.isfinite(v) for v in replacement)
                or not 0<=replacement[0]<replacement[1]<retained[2]):
            fail('Invalid repaired opening boundaries.'); return
        retained[:2]=replacement
    preparation=alignment.get('preparation') or {}
    offset=preparation.get('seconds',0)
    if type(offset) not in (int,float) or not math.isfinite(offset) or not 0<=offset<=2:
        fail('Invalid preparation offset.'); return
    retained=[value+offset for value in retained]
    check.equal('source_timing_boundaries', 'alignment/anchors', required, len(anchors))
    for i, (quarter, audio, anchor) in enumerate(zip(boundaries, retained, anchors)):
        where = f'alignment/anchors/{i}'
        check.near('source_timing_score', where, float(clock.at(quarter)), anchor.get('score'), 1e-8)
        check.near('source_timing_quarter', where, float(quarter), anchor.get('quarter'), 1e-8)
        check.near('source_timing_audio', where, audio, anchor.get('audio'), 1e-8)
    # Derive the opening boundary from independently parsed source atoms.
    # Never accept the producer's negative timestamps or receipt as evidence.
    if strums is None:
        from .verify_timeline import expected as evaluate
        strums = evaluate(source, {'offset': 0, 'scale': 1})['strums']
    early, groups = set(), set()
    for group in strums:
        for note in group['notes']:
            if note['t'] >= 0:
                continue
            if group['occurrence'] != 1 or group['time'] < 0:
                fail('Opening event is not an authored strum attack.'); return
            early.add((group['trackId'], note['t'], note['s'], note['f']))
            groups.add((group['trackId'], group['sourceId']))
    expected = None
    if early:
        expected = {'version': 1, 'rule': 'authored-opening-strum-first-interval',
                    'scoreStart': min(n[1] for n in early), 'noteCount': len(early),
                    'groups': [{'trackId': t, 'sourceId': s} for t, s in sorted(groups)]}
        first = retained[0] - offset
        ratio = (retained[1] - retained[0]) / float(clock.at(boundaries[1]))
        if first + expected['scoreStart'] * ratio < 0:
            fail('Opening strum begins before the original recording; padding cannot supply missing audio.')
    actual = provenance.get('openingStrum')
    if expected is not None and isinstance(actual, dict):
        # Initial tempo division differs by harmless floating-point roundoff.
        check.near('opening_strum_boundary', 'alignment/provenance/openingStrum/scoreStart',
                   expected['scoreStart'], actual.get('scoreStart'), 1e-8)
        actual = {**actual, 'scoreStart': expected['scoreStart']}
    check.equal('opening_strum_evidence', 'alignment/provenance/openingStrum', expected, actual)

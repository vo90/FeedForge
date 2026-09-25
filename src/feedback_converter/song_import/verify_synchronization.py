"""Independently check source-player boundary selection from retained evidence.

Does not call the producer's parser, timeline renderer, or synchronization code.
"""
import hashlib
import json
import math

from .verify_timeline import Clock, visits


def verify_source_timing(source, alignment, recipe, timing, check):
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
            or any(a >= b for a, b in zip(points, points[1:]))):
        fail('Retained recording points must be finite and strictly increasing.'); return
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
    clock = Clock(source, order)
    boundaries = [*clock.measure_starts, clock.quarters]
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
    check.equal('source_timing_boundaries', 'alignment/anchors', required, len(anchors))
    for i, (quarter, audio, anchor) in enumerate(zip(boundaries, retained, anchors)):
        where = f'alignment/anchors/{i}'
        check.near('source_timing_score', where, float(clock.at(quarter)), anchor.get('score'), 1e-8)
        check.near('source_timing_quarter', where, float(quarter), anchor.get('quarter'), 1e-8)
        check.near('source_timing_audio', where, audio, anchor.get('audio'), 1e-8)

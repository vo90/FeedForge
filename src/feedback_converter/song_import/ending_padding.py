"""Preserve a short existing note tail without inventing recording evidence."""
from copy import deepcopy
import io
import json
import math

import soundfile as sf

from .audio import ImportFailure
from . import recording_sync as rs

POLICY = 'preserve-existing-tail-v1'
MAX_SECONDS = 2.0
EPSILON = .0000011
MIXED_POLICY = 'preserve-gesture-after-held-tail-trim-v1'
PLAIN_FLAGS = {'mt', 'vb', 'ac', 'pm', 'lr', 'ghost', 'hm', 'hp', 'hn', 'hps', 'st', 'tr'}


def bounds(tracks, duration):
    """Every arrangement must start its attacks inside the original recording."""
    events = [n for t in tracks for n in t['events']]
    if (not events or not math.isfinite(duration) or duration <= 0
            or any(not all(math.isfinite(n[k]) for k in ('t', 'end'))
                   or not 0 <= n['t'] < duration or n['end'] < n['t'] for n in events)):
        return None
    last = max(n['end'] for n in events)
    if not EPSILON < last - duration <= MAX_SECONDS:
        return None
    return {'originalDuration': duration, 'lastNoteEnd': last}


def candidate(performance, alignment, audio):
    from .ending_cutoff import mapped_tracks
    try:
        return plan_bounds(mapped_tracks(performance, alignment), audio['duration'])
    except (ImportFailure, ValueError, KeyError, TypeError):
        return None  # The ordinary structural/playable checks supply the error.


def plan_bounds(tracks, duration):
    ordinary = bounds(tracks, duration)
    if ordinary:
        return ordinary
    events = [n for t in tracks for n in t['events']]
    if (not events or not math.isfinite(duration) or duration <= 0
            or any(not all(math.isfinite(n[k]) for k in ('t','end'))
                   or not 0 <= n['t'] < duration or n['end'] < n['t'] for n in events)):
        return None
    long = [n for n in events if n['end'] > duration + MAX_SECONDS]
    if not long or any(set(n.get('effects', {})) - PLAIN_FLAGS for n in long):
        return None
    retained = max([duration] + [n['end'] for n in events if n['end'] <= duration + MAX_SECONDS])
    if retained <= duration + EPSILON:
        return None  # The established held-tail trimming route already covers this.
    return {'originalDuration':duration,'lastNoteEnd':retained,'trimLongHeldTails':True,
            'sourceLastNoteEnd':max(n['end'] for n in events)}


def original_tracks(tracks, seconds):
    tracks = deepcopy(tracks)
    for track in tracks:
        for note in track['events']:
            note['t'] = round(note['t'] - seconds, 6)
            note['end'] = round(note['end'] - seconds, 6)
    return tracks


def assess(tracks, path, duration, map_hash, *, legacy=False):
    from .local_sync import _hash
    report = rs.assess(tracks, path, duration, map_hash, **({'legacy':True} if legacy else {}))
    # WAV containers may contain timestamped PEAK headers. Bind decoded samples.
    report.update(audioSha256=_hash(path), audioHashKind='decoded-float32')
    # Require support in the actual ending region, not just earlier verses.
    outro = any(w['end'] >= duration - .001 and w['status'] == 'supported'
                for w in report['windows'])
    report['outroSupported'] = outro
    if not outro:
        report['status'] = 'inconclusive'
    return report


def authorize(performance, audio, alignment):
    from .ending_cutoff import mapped_tracks
    from .local_sync import _hash
    spec = candidate(performance, alignment, audio)
    if spec is None or alignment.get('mapping') != 'piecewise-linear':
        raise ImportFailure('source_sync_unavailable', 'This ending cannot use short silent padding.')
    info = sf.info(audio['path'])
    if abs(info.frames / info.samplerate - audio['duration']) > 1e-8:
        raise ImportFailure('source_sync_unavailable', 'The original recording length could not be verified.')
    frames = math.ceil((spec['lastNoteEnd'] - audio['duration']) * info.samplerate - 1e-8)
    report = assess(mapped_tracks(performance, alignment), audio['path'], audio['duration'], alignment['provenance']['mapHash'])
    if report['status'] != 'supported':
        raise ImportFailure('source_sync_unavailable', 'The recording timing, including its ending, did not support adding silence.',
                            {'sourceSyncReason': 'ending_padding_sync_inconclusive', 'endingPaddingSync': report})
    result = deepcopy(alignment)
    result['endingPadding'] = {'version': 1, 'policy': POLICY, **spec,
        'originalFrames': info.frames, 'sampleRate': info.samplerate, 'frames': frames,
        'seconds': frames / info.samplerate, 'sourceSamplesSha256': _hash(audio['path'])}
    if spec.get('trimLongHeldTails'):
        from .terminal_sustains import mixed_policy_for
        result['endingPadding'].update(version=2, policy=MIXED_POLICY)
        result['terminalSustains'] = mixed_policy_for(audio['duration'])
    result['endingPaddingSync'] = report
    result.pop('paddingCandidate', None)
    result['status'] = 'validated'
    result['provenance']['terminalBeyondAudio'] = 'preserved_with_short_silence'
    return result


def confirm_encoded(performance, audio, alignment):
    from .ending_cutoff import mapped_tracks
    from .preparation import recording_view
    from .local_sync import _hash
    receipt = alignment['endingPadding']
    recording = recording_view(audio['path'], alignment['preparation'])
    tracks = original_tracks(mapped_tracks(performance, alignment), alignment['preparation']['seconds'])
    report = assess(tracks, recording, receipt['originalDuration'], alignment['provenance']['mapHash'])
    if report['status'] != 'supported':
        raise ImportFailure('ending_padding_unconfirmed', 'The encoded recording did not confirm short ending padding.',
                            {'endingPaddingSync': report})
    receipt['recordingSamplesSha256'] = _hash(recording)
    receipt['syncEvidenceHash'] = rs.digest(report)
    alignment['endingPaddingSync'] = report


def verify(wanted, alignment, recipe, archive, manifest, check):
    """Reconstruct eligibility from raw-source notes and remeasure original audio."""
    receipt = alignment.get('endingPadding')
    if receipt is None:
        if any(recipe.get(k) for k in ('endingPaddingFile', 'endingPaddingSyncFile')) or alignment.get('endingPaddingSync'):
            check.fail('ending_padding_unexpected', 'import', 'Undeclared ending padding evidence.')
        return
    from .local_sync import tracks_from_expected, _hash
    from .preparation import recording_view
    check.equal('ending_padding_receipt', 'import/ending-padding', receipt,
                json.loads(archive.read(recipe['endingPaddingFile'])))
    check.equal('ending_padding_recipe', 'manifest/song_import/alignment/endingPadding', receipt,
                recipe.get('alignment', {}).get('endingPadding'))
    stored = json.loads(archive.read(recipe['endingPaddingSyncFile']))
    check.equal('ending_padding_sync_receipt', 'import/ending-padding-sync', alignment.get('endingPaddingSync'), stored)
    mixed = receipt.get('version') == 2 and receipt.get('policy') == MIXED_POLICY and receipt.get('trimLongHeldTails') is True
    if (recipe.get('preservationContract', 0) < (37 if mixed else 36)
            or not (mixed or receipt.get('version') == 1 and receipt.get('policy') == POLICY)
            or alignment.get('method') != 'songsterr-video-points-v1' or alignment.get('mapping') != 'piecewise-linear'
            or any(alignment.get(k) for k in ('recordingEnd', 'terminalSlides', 'paddingCandidate'))
            or bool(alignment.get('terminalSustains')) != mixed):
        raise ValueError('Invalid ending padding policy.')
    prep = alignment['preparation']
    rate, frames, original_frames = receipt['sampleRate'], receipt['frames'], receipt['originalFrames']
    if (any(type(n) != int for n in (rate, frames, original_frames)) or not 8000 <= rate <= 384000
            or not 0 < frames <= MAX_SECONDS * rate or original_frames <= 0):
        raise ValueError('Invalid ending padding sample count.')
    duration = original_frames / rate
    tracks = original_tracks(tracks_from_expected(wanted), prep['seconds'])
    spec = bounds(tracks, duration)
    if mixed:
        # Reconstruct the mixed policy from independent raw-source events.
        # The receipt cannot select which notes are shortened.
        events = [n for t in tracks for n in t['events']]
        long = [n for n in events if n['end'] > duration + 2.0]
        if (not long or any(not 0 <= n['t'] < duration or n['end'] < n['t'] for n in events)
                or any(set(n['effects']) - {'mt','vb','ac','pm','lr','ghost','hm','hp','hn','hps','st','tr'} for n in long)):
            raise ValueError('Mixed ending would shorten an active gesture or omit an attack.')
        remaining = max([duration]+[n['end'] for n in events if n['end'] <= duration+2.0])
        if not duration+EPSILON < remaining <= duration+2.0:
            raise ValueError('Mixed ending does not require bounded padding.')
        spec={'originalDuration':duration,'lastNoteEnd':remaining}
        check.near('ending_padding_source_last','ending-padding/sourceLastNoteEnd',max(n['end'] for n in events),receipt.get('sourceLastNoteEnd'))
    if spec is None:
        raise ValueError('The raw source has new attacks outside the recording or no eligible short tail.')
    check.near('ending_padding_original', 'ending-padding/originalDuration', duration, receipt['originalDuration'], 1e-10)
    check.near('ending_padding_original', 'preparation/originalDuration', duration, prep['originalDuration'], 1e-10)
    check.equal('ending_padding_frames', 'preparation/endingFrames', frames, prep.get('endingFrames'))
    check.equal('ending_padding_frames', 'preparation/originalFrames', original_frames, prep.get('originalFrames'))
    check.equal('ending_padding_rate', 'preparation/sampleRate', rate, prep['sampleRate'])
    check.near('ending_padding_last', 'ending-padding/lastNoteEnd', spec['lastNoteEnd'], receipt['lastNoteEnd'])
    # Independent microsecond rounding can straddle one decoded sample boundary.
    needed = math.ceil((spec['lastNoteEnd'] - duration) * rate - 1e-8)
    if abs(frames - needed) > 1:
        check.fail('ending_padding_minimum', 'ending-padding', 'Padding exceeds the actual final note tail.')
    check.near('ending_padding_seconds', 'ending-padding/seconds', frames / rate, receipt['seconds'], 1e-10)
    check.near('ending_padding_duration', 'manifest/duration', duration + prep['seconds'] + frames / rate, manifest['duration'], 1e-10)
    check.equal('ending_padding_sync_hash', 'ending-padding/syncEvidenceHash', rs.digest(stored), receipt.get('syncEvidenceHash'))
    full = [s for s in manifest['stems'] if s['id'] == 'full']
    if len(full) != 1:
        raise ValueError('Ending padding requires one full recording.')
    recording = recording_view(io.BytesIO(archive.read(full[0]['file'])), prep)
    check.equal('ending_padding_recording', 'ending-padding/recordingSamplesSha256', _hash(recording), receipt.get('recordingSamplesSha256'))
    if not check.total_errors:
        fresh = assess(tracks, recording, duration, alignment['provenance']['mapHash'], legacy=stored.get('version') == rs.LEGACY_VERSION)
        check.equal('ending_padding_sync', 'import/ending-padding-sync', 'supported', fresh['status'])
        for key in ('audioSha256', 'audioDuration', 'mapHash', 'version', 'outroSupported'):
            check.equal('ending_padding_sync_identity', 'import/ending-padding-sync/' + key, fresh[key], stored.get(key))

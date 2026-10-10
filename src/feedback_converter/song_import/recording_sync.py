"""Conservative recording-map evidence for an explicit recording-end cutoff.

Pitch ranks and onset ratios are engineering gates, not probabilities. This
module never changes timing or selects audio. Inconclusive evidence cannot
permit omission of source notes.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import numpy as np
import soundfile as sf

LEGACY_VERSION = "mapped-pitch-onsets-v2"
PREVIOUS_VERSION = "recording-clock-v3"
VERSION = "recording-clock-v4"
CUTOFF_VERSION = "recording-clock-v3-low-bass-v1"
PADDING_VERSION = "recording-clock-v4-low-bass-v1"
RATE = 11025
HOP = 110
DT = HOP / RATE
MIDIS = np.arange(28, 97)
MIN_GROUPS = 6
CONTEXT_EXTENSION = 8.0
CONTEXT_GAP = 2.0
PITCH_EXCLUSIONS = ('mt', 'bn', 'bnv', 'sl', 'slu', 'whammy')
ATTACK_EXCLUSIONS = ('ho', 'po', 'ln')
SPARSE_EXCLUSIONS = PITCH_EXCLUSIONS + ATTACK_EXCLUSIONS + (
    'hm', 'hp', 'hn', 'hps', 'harmonic_target', 'harmonic_changes',
    'harmonic_alias', 'pick_scrape_marks', 'slide_out', 'slide_out_marks', 'slide_in_marks', 'slide_interval', 'tr',
)

def _decode(path):
    with sf.SoundFile(path) as reader:
        if not 2 <= len(reader) / reader.samplerate <= 1202 or not 1 <= reader.channels <= 2:
            raise ValueError("The timing check needs a bounded mono or stereo recording.")
        rate = reader.samplerate
        mono = reader.read(dtype='float32', always_2d=True).mean(axis=1)
    if not np.isfinite(mono).all():
        raise ValueError("The timing check recording contains invalid samples.")
    signal = np.interp(np.arange(round(len(mono) * RATE / rate)) * rate / RATE,
                       np.arange(len(mono)), mono).astype('float32')
    count = 1 + len(signal) // HOP
    return signal, count


def _pitch_bank(reference_hz, midi_min):
    if midi_min not in (21, 28):
        raise ValueError('Unsupported recording pitch profile.')
    midis = MIDIS if midi_min == 28 else np.arange(midi_min, 97)
    # Harmonic pitch evidence, independent of the score. +/- 35 cent bands
    # accommodate modest detuning; neighbours supply local spectral contrast.
    n = 4096
    freq = np.fft.rfftfreq(n, 1 / RATE)
    bank = np.zeros((len(midis), len(freq)), dtype='float32')
    for i, midi in enumerate(midis):
        base = reference_hz * 2 ** ((midi - 69) / 12)
        for harmonic in range(1, 6):
            f = base * harmonic
            if f > 4900:
                break
            width = max(RATE / n * .8, f * .014)
            centre = np.exp(-.5 * ((freq - f) / width) ** 2)
            flank = (np.exp(-.5 * ((freq - f * 2 ** (-.7 / 12)) / width) ** 2)
                     + np.exp(-.5 * ((freq - f * 2 ** (.7 / 12)) / width) ** 2)) * .5
            bank[i] += (centre / centre.sum() - flank / flank.sum()) / (harmonic ** .7)
    bank = bank.T
    return bank


def _pitches(signal, count, keys):
    # Only a bounded block of spectra is live, irrespective of song length.
    banks = {key: _pitch_bank(*key) for key in keys}
    outputs = {key: np.zeros((count, bank.shape[1]), dtype='float32')
               for key, bank in banks.items()}
    n = 4096
    pad = np.pad(signal, (n // 2, n // 2))
    window = np.hanning(n).astype('float32')
    for begin in range(0, count, 128):
        frames = pad[(np.arange(begin, min(begin + 128, count)) * HOP)[:, None] + np.arange(n)]
        mag = abs(np.fft.rfft(frames * window, axis=1)).astype('float32')
        transformed = np.log1p(mag * 20)
        for key, bank in banks.items():
            outputs[key][begin:begin + len(frames)] = np.maximum(transformed @ bank, 0)
    for pitches in outputs.values():
        pitches /= np.maximum(np.linalg.norm(pitches, axis=1, keepdims=True), 1e-7)
    return outputs


def _onsets(signal, count):
    # Short FFT spectral changes measure transient attacks with ~10 ms hops.
    n = 1024
    freq = np.fft.rfftfreq(n, 1 / RATE)
    pad = np.pad(signal, (n // 2, n // 2))
    window = np.hanning(n).astype('float32')
    masks = [(freq >= low) & (freq < high) for low, high in [(35, 300), (250, 1400), (1200, 5000), (35, 5000)]]
    flux = np.zeros((count, 4), dtype='float32')
    previous = np.zeros(len(freq))
    for begin in range(0, count, 256):
        frames = pad[(np.arange(begin, min(begin + 256, count)) * HOP)[:, None] + np.arange(n)]
        spec = np.log1p(abs(np.fft.rfft(frames * window, axis=1)) * 20)
        difference = np.maximum(np.diff(spec, axis=0, prepend=previous[None, :]), 0)
        previous = spec[-1]
        for i, mask in enumerate(masks):
            flux[begin:begin + len(frames), i] = difference[:, mask].mean(axis=1)
    smooth = np.exp(-.5 * (np.arange(-4, 5) / 1.7) ** 2)
    smooth /= smooth.sum()
    for i in range(4):
        flux[:, i] = np.convolve(flux[:, i], smooth, mode='same')
        # Local normalization prevents loud passages dominating a region.
        avg = np.convolve(flux[:, i], np.ones(201) / 201, mode='same')
        flux[:, i] /= np.maximum(avg, np.quantile(avg, .3) * .4 + 1e-7)
        flux[:, i] = np.minimum(flux[:, i], 8)
    return flux


def features(path, *, reference_hz=440.0, midi_min=28):
    if midi_min not in (21, 28):
        raise ValueError('Unsupported recording pitch profile.')
    signal, count = _decode(path)
    pitches = _pitches(signal, count, [(reference_hz, midi_min)])[reference_hz, midi_min]
    return pitches, _onsets(signal, count), signal


class FeatureSession:
    """One independent assessment of one immutable recording.

    Cache at most 64 MiB of compact pitch arrays, never whole-song spectra.
    The bounded recording signal/onsets and FFT scratch are separate from that
    budget. Long recordings fall back to sequential pitch computation and LRU
    eviction. Producer and verifier create separate sessions; nothing is global.
    """
    def __init__(self, path, *, cache_bytes=64 * 1024 * 1024):
        self.path = path
        self.cache_bytes = max(0, cache_bytes)
        self.cache = {}
        self.signal = self.flux = None
        self.count = 0
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        self.cache.clear()
        self.signal = self.flux = self.path = None
        self.closed = True

    def _load(self):
        if self.closed:
            raise RuntimeError('Audio feature session is closed.')
        if self.signal is None:
            if hasattr(self.path, 'seek'):
                self.path.seek(0)
            self.signal, self.count = _decode(self.path)
            self.flux = _onsets(self.signal, self.count)

    @property
    def cached_bytes(self):
        return sum(p.nbytes for p in self.cache.values())

    def prepare(self, keys):
        """Share an FFT pass only when every requested output fits the budget."""
        keys = list(dict.fromkeys(keys))
        if any(midi not in (21, 28) for _, midi in keys):
            raise ValueError('Unsupported recording pitch profile.')
        self._load()
        keys = [key for key in keys if key not in self.cache]
        required = sum(self.count * (97 - midi) * 4 for _, midi in keys)
        if keys and self.cached_bytes + required <= self.cache_bytes:
            self.cache.update(_pitches(self.signal, self.count, keys))

    def features(self, *, reference_hz=440.0, midi_min=28):
        if midi_min not in (21, 28):
            raise ValueError('Unsupported recording pitch profile.')
        self._load()
        key = reference_hz, midi_min
        if key in self.cache:
            pitches = self.cache.pop(key)
            self.cache[key] = pitches
        else:
            required = self.count * (97 - midi_min) * 4
            while self.cache and self.cached_bytes + required > self.cache_bytes:
                self.cache.pop(next(iter(self.cache)))
            pitches = _pitches(self.signal, self.count, [key])[key]
            if required <= self.cache_bytes:
                self.cache[key] = pitches
        return pitches, self.flux, self.signal


def samples(events, left, right):
    notes = [n for n in events if left <= n['t'] < right and n['midi'] is not None and 28 <= n['midi'] <= 96
             and not any(n['effects'].get(k) for k in PITCH_EXCLUSIONS)]
    groups = {}
    for note in notes:
        groups.setdefault(round(note['t'], 3), []).append(note)
    times, vectors = [], []
    for time, notes in sorted(groups.items()):
        delay = min(.16, max(.05, min(n['end'] - n['t'] for n in notes) * .35))
        vector = np.zeros(len(MIDIS))
        # Limit expected simultaneous pitches to the selected track's notes.
        for note in notes:
            vector[int(note['midi']) - 28] = 1
        vector /= np.linalg.norm(vector)
        times.append(time + delay)
        vectors.append(vector)
    return np.asarray(times), np.asarray(vectors)

def pitch_scores(pitches, times, vectors, lags):
    indices = np.rint((times[None, :] + lags[:, None]) / DT).astype(int)
    valid = (indices >= 0) & (indices < len(pitches))
    values = pitches[np.clip(indices, 0, len(pitches) - 1)]
    return ((values * vectors[None, :, :]).sum(axis=2) * valid).mean(axis=1)

def attack_scores(flux, times, lags, channel):
    indices = np.rint((times[None, :] + lags[:, None]) / DT).astype(int)
    valid = (indices >= 0) & (indices < len(flux))
    return (flux[np.clip(indices, 0, len(flux) - 1), channel] * valid).mean(axis=1)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def assess(tracks, audio_path, duration, map_hash, *, analysis_origin=0.0, legacy=False, clock_version=None):
    """Assess already mapped track events without adjusting their positions.

    All non-silent body windows containing attacks must have supporting pitch
    AND attack evidence in at least one part. Other parts remain explicitly
    unassessed/inconclusive; this checks the shared recording clock, not tab quality.
    """
    version = LEGACY_VERSION if legacy else clock_version or VERSION
    base_version = {CUTOFF_VERSION: PREVIOUS_VERSION, PADDING_VERSION: VERSION}.get(version, version)
    if base_version not in (LEGACY_VERSION, PREVIOUS_VERSION, VERSION):
        raise ValueError('Unknown recording-clock evidence version.')
    if hasattr(audio_path, "read"):
        audio_path.seek(0)
        audio_hash = hashlib.sha256(audio_path.read()).hexdigest()
        audio_path.seek(0)
    else:
        audio_hash = hashlib.sha256(Path(audio_path).read_bytes()).hexdigest()
    original_duration=duration
    if analysis_origin:
        import math
        from copy import deepcopy
        from .preparation import recording_view
        if not math.isfinite(analysis_origin) or not 0<analysis_origin<=2:
            raise ValueError('Invalid preparation origin for the recording check.')
        if hasattr(audio_path,'seek'):audio_path.seek(0)
        rate=sf.info(audio_path).samplerate
        audio_path=recording_view(audio_path,{'frames':round(analysis_origin*rate)})
        tracks=deepcopy(tracks)
        for track in tracks:
            for event in track['events']:
                event['t']=round(event['t']-analysis_origin,6)
                event['end']=round(event['end']-analysis_origin,6)
        duration-=analysis_origin
    pitch, flux, signal = features(audio_path)
    actual_duration = len(signal) / RATE
    if abs(actual_duration - duration) > .001:
        raise ValueError("The timing check audio duration does not match the recording.")
    report = assess_features(tracks, pitch, flux, duration)
    if base_version == LEGACY_VERSION:
        report['version'] = LEGACY_VERSION
    elif report['status'] != 'supported':
        from .clock_evidence import assess as assess_clock
        report = assess_clock(tracks, audio_path, duration, pitch, flux, report,
                              phrases=base_version == VERSION)
    else:
        report.update(method='established-pitch-onset-check', referenceEvidence={'status':'default', 'referenceHz':440.0})
    if version in (CUTOFF_VERSION, PADDING_VERSION):
        from .low_bass_sync import supplement
        report = supplement(tracks, audio_path, duration, report)
        # Reports which never used the supplement retain their exact legacy
        # version and schema, including historical receipt replay.
        if 'lowBassEvidence' not in report:
            version = base_version
    return {**report, 'version':version,
            "audioSha256": audio_hash, "audioDuration": original_duration, "mapHash": map_hash,
            **({'analysisOriginSeconds':analysis_origin} if analysis_origin else {})}


def _attacks(events, left, right):
    return np.asarray(sorted({round(n['t'], 3) for n in events
        if left <= n['t'] < right and not any(n['effects'].get(k) for k in ATTACK_EXCLUSIONS)}))


def _measure(track, pitch, flux, left, right, minimum=MIN_GROUPS):
    times, vectors = samples(track['events'], left, right)
    attacks = _attacks(track['events'], left, right)
    row = {'trackId': track['id'], 'pitchedAttacks': len(times), 'attacks': len(attacks)}
    if len(times) < minimum or len(attacks) < minimum:
        return {**row, 'status': 'inconclusive', 'reason': 'too_little_distinct_material'}
    lags = np.arange(-.30, .3001, .01)
    wide = np.arange(-3, 3.001, .05)
    near = abs(lags) <= .12001
    zero = int(np.argmin(abs(lags)))
    ps = pitch_scores(pitch, times, vectors, lags)
    pw = pitch_scores(pitch, times, vectors, wide)
    ac = attack_scores(flux, attacks, lags, 0 if track['instrument'] == 'bass' else 1)
    # Preserve the normal pitch/onset gates. Sparse local checks below use
    # these same gates, with a separately required six-group context check.
    pitch_near = float(ps[near].max())
    pitch_rank = float((pw < pitch_near).mean())
    local_lag = float(lags[ac.argmax()])
    near_lag = float(lags[near][ac[near].argmax()])
    supported = (pitch_near >= .10 and pitch_rank >= .90
                 and pitch_near >= .95 * ps.max() and ps.max() - np.median(pw) >= .015
                 and abs(near_lag) <= .08001
                 and ac[near].max() >= 1.15 * np.median(ac)
                 and ac[near].max() >= .90 * ac.max())
    return {**row, 'status': 'supported' if supported else 'inconclusive',
            'pitchAtMap': round(float(ps[zero]), 6), 'pitchShiftRank': round(pitch_rank, 6),
            'pitchNearBest': round(pitch_near, 6), 'pitchBest': round(float(ps.max()), 6),
            'pitchContrast': round(float(ps.max() - np.median(pw)), 6),
            'attackAtMap': round(float(ac[zero]), 6), 'attackBest': round(float(ac.max()), 6),
            'attackNearBest': round(float(ac[near].max()), 6),
            'attackContrast': round(float(ac[near].max() / max(np.median(ac), 1e-9)), 6),
            'attackNearLag': round(near_lag, 3), 'attackLocalLag': round(local_lag, 3)}


def _paired(group, *, midi_min=28):
    # The low-sample path cannot infer an onset or a fixed pitch from a mute,
    # legato transition or moving-pitch gesture, including mixed chords.
    return all(n['midi'] is not None and midi_min <= n['midi'] <= 96 and int(n['midi']) == n['midi']
               and not any(n['effects'].get(k) for k in SPARSE_EXCLUSIONS)
               for n in group)


def _group_evidence(groups):
    return [{'time': round(group[0]['t'], 3), 'midis': sorted({n['midi'] for n in group})}
            for group in groups]


def _group_pitch_checks(groups, pitch, onset_lag, *, local=False):
    """Require audible expected pitches at the onset-consistent position.

    An aggregate can pass with only half its expected pitches present. Sparse
    evidence cannot afford that dilution. Use a 30 ms sampling neighbourhood
    around the measured attack offset, not a free 120 ms pitch-only shift that
    might land in the previous note. Context still uses the full ordinary rank
    gate in aggregate; each sparse local group also needs that rank on its own.
    """
    rows = []
    for group in groups:
        # A chord's correct member must not conceal an absent/wrong member.
        for midi in sorted({n['midi'] for n in group}):
            times, vectors = samples([n for n in group if n['midi'] == midi], -float('inf'), float('inf'))
            aligned = pitch_scores(pitch, times, vectors, onset_lag + np.arange(-.03, .0301, .01))
            wide = pitch_scores(pitch, times, vectors, np.arange(-3, 3.001, .05))
            score = float(aligned.max())
            rank = float((wide < score).mean())
            contrast = float(score - np.median(wide))
            supported = score >= .10 and contrast >= .015 and (not local or rank >= .90)
            rows.append({'time': round(group[0]['t'], 3), 'midi': midi,
                         'status': 'supported' if supported else 'inconclusive',
                         'pitchAtAttack': round(score, 6), 'pitchShiftRank': round(rank, 6),
                         'pitchContrast': round(contrast, 6)})
    return rows


def _sparse_context(track, pitch, flux, left, right, duration):
    """Deterministic neighbouring context plus undiluted local pairs.

    This supplements a sample-count rejection only. The original local sample
    interval is retained, while context includes the window's end-margin events.
    Two local attack groups are required; isolated attacks remain inconclusive.
    """
    base = {'trackId': track['id'], 'status': 'inconclusive', 'method': 'sparse-context',
            'sampleInterval': [round(float(left), 6), round(float(right - .2), 6)]}
    by_time = {}
    for n in sorted(track['events'], key=lambda n: n['t']):
        by_time.setdefault(round(n['t'], 3), []).append(n)
    groups = list(by_time.values())
    within = [i for i, g in enumerate(groups) if left <= g[0]['t'] < right]
    local = [groups[i] for i in within if groups[i][0]['t'] < right - .2]
    if not 2 <= len(local) < MIN_GROUPS:
        return {**base, 'reason': 'insufficient_local_pairs'}
    # Never silently exclude a difficult event when borrowing context.
    if not all(_paired(groups[i]) for i in within):
        return {**base, 'reason': 'unassessable_sparse_events'}
    lo, hi = within[0], within[-1]
    if any(groups[i + 1][0]['t'] - groups[i][0]['t'] > CONTEXT_GAP for i in range(lo, hi)):
        return {**base, 'reason': 'sparse_context_gap'}
    while hi - lo + 1 < MIN_GROUPS:
        candidates = []
        for index, gap in ((lo - 1, groups[lo][0]['t'] - groups[lo - 1][0]['t'] if lo else float('inf')),
                           (hi + 1, groups[hi + 1][0]['t'] - groups[hi][0]['t'] if hi + 1 < len(groups) else float('inf'))):
            if not 0 <= index < len(groups):
                continue
            group = groups[index]
            t = group[0]['t']
            if (gap <= CONTEXT_GAP and max(0, left - CONTEXT_EXTENSION) <= t < min(duration - .2, right + CONTEXT_EXTENSION)
                    and _paired(group)):
                candidates.append((gap, index))
        if not candidates:
            return {**base, 'reason': 'insufficient_contiguous_context'}
        # Selection depends only on source time, never on acoustic success.
        _, index = min(candidates)
        lo, hi = min(lo, index), max(hi, index)
    context = groups[lo:hi + 1]
    if context[-1][0]['t'] >= duration - .2 or context[-1][0]['t'] - context[0][0]['t'] > 16:
        return {**base, 'reason': 'sparse_context_outside_bounds'}
    local_checks = []
    # Disjoint adjacent pairs, with an overlapping final pair for odd counts.
    # Later good notes cannot dilute an incorrect first pair in one average.
    for start in sorted(set(list(range(0, len(local) - 1, 2)) + [len(local) - 2])):
        pair = local[start:start + 2]
        selected = {**track, 'events': [n for group in pair for n in group]}
        measured = _measure(selected, pitch, flux, 0, duration, minimum=2)
        checks = _group_pitch_checks(pair, pitch, measured['attackNearLag'], local=True)
        local_checks.append({'groups': _group_evidence(pair), **measured, 'aggregateStatus': measured['status'],
                             'status': 'supported' if measured['status'] == 'supported' and all(c['status'] == 'supported' for c in checks) else 'inconclusive',
                             'pitchChecks': checks})
    selected = {**track, 'events': [n for group in context for n in group]}
    context_check = {**_measure(selected, pitch, flux, 0, duration), 'groups': _group_evidence(context)}
    context_check['pitchChecks'] = _group_pitch_checks(context, pitch, context_check['attackNearLag'])
    context_check['aggregateStatus'] = context_check['status']
    if any(c['status'] != 'supported' for c in context_check['pitchChecks']):
        context_check['status'] = 'inconclusive'
    margin = [groups[i] for i in within if groups[i][0]['t'] >= right - .2]
    margin_checks = _group_pitch_checks(margin, pitch, context_check['attackNearLag'], local=True)
    checks = context_check['pitchChecks'] + margin_checks + [p for c in local_checks for p in c['pitchChecks']]
    supported = (context_check['status'] == 'supported' and all(c['status'] == 'supported' for c in local_checks)
                 and all(c['status'] == 'supported' for c in checks))
    return {**base, 'status': 'supported' if supported else 'inconclusive',
            'reason': 'sparse_context_supported' if supported else 'sparse_evidence_inconclusive',
            'localChecks': local_checks, 'context': context_check, 'endMarginPitchChecks': margin_checks}


def assess_features(tracks, pitch, flux, duration):
    """Pure evidence decision, also exercised with known negative controls."""
    windows = []
    for left in np.arange(0, max(1, duration - 8), 8):
        right = min(duration, left + 16)
        active = any(left <= n["t"] < right for t in tracks for n in t["events"])
        if not active:
            continue
        rows = [_measure(track, pitch, flux, left, right - .2) for track in tracks]
        # Do not override a measured mismatch with another, more convenient
        # interval. This path is only for a window lacking enough samples in
        # every part, not one that already supplied inconclusive audio evidence.
        if rows and all(r.get('reason') == 'too_little_distinct_material' for r in rows):
            for track, row in zip(tracks, rows):
                supplement = _sparse_context(track, pitch, flux, left, right, duration)
                row['sparseEvidence'] = supplement
                if supplement['status'] == 'supported':
                    row.update(status='supported', reason='sparse_context_supported')
        windows.append({"start": round(float(left), 6), "end": round(float(right), 6),
                        "status": "supported" if any(r["status"] == "supported" for r in rows) else "inconclusive", "tracks": rows})
    supported = len(windows) >= 3 and all(w["status"] == "supported" for w in windows)
    return {"version": VERSION, "status": "supported" if supported else "inconclusive",
            "windows": windows, "windowCount": len(windows),
            "supportedWindows": sum(w["status"] == "supported" for w in windows),
            "sparseWindows": sum(any(r.get('reason') == 'sparse_context_supported' for r in w['tracks']) for w in windows),
            "scope": "shared_recording_timing", "everyNoteVerified": False,
            "calibratedProbability": False}

"""Conservative recording-map evidence for an explicit final-bar cutoff.

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

VERSION = "mapped-pitch-onsets-v1"
RATE = 11025
HOP = 110
DT = HOP / RATE
MIDIS = np.arange(28, 97)

def features(path):
    with sf.SoundFile(path) as reader:
        if not 2 <= len(reader) / reader.samplerate <= 1200 or not 1 <= reader.channels <= 2:
            raise ValueError("The timing check needs a bounded mono or stereo recording.")
        rate = reader.samplerate
        mono = reader.read(dtype='float32', always_2d=True).mean(axis=1)
    if not np.isfinite(mono).all():
        raise ValueError("The timing check recording contains invalid samples.")
    signal = np.interp(np.arange(round(len(mono) * RATE / rate)) * rate / RATE,
                       np.arange(len(mono)), mono).astype('float32')
    count = 1 + len(signal) // HOP
    # Harmonic pitch evidence, independent of the score. +/- 35 cent bands
    # accommodate modest detuning; neighbours supply local spectral contrast.
    n = 4096
    freq = np.fft.rfftfreq(n, 1 / RATE)
    bank = np.zeros((len(MIDIS), len(freq)), dtype='float32')
    for i, midi in enumerate(MIDIS):
        base = 440 * 2 ** ((midi - 69) / 12)
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
    pad = np.pad(signal, (n // 2, n // 2))
    window = np.hanning(n).astype('float32')
    pitches = np.zeros((count, len(MIDIS)), dtype='float32')
    for begin in range(0, count, 128):
        frames = pad[(np.arange(begin, min(begin + 128, count)) * HOP)[:, None] + np.arange(n)]
        mag = abs(np.fft.rfft(frames * window, axis=1)).astype('float32')
        pitches[begin:begin + len(frames)] = np.maximum(np.log1p(mag * 20) @ bank, 0)
    pitches /= np.maximum(np.linalg.norm(pitches, axis=1, keepdims=True), 1e-7)
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
    return pitches, flux, signal

def samples(events, left, right):
    notes = [n for n in events if left <= n['t'] < right and n['midi'] is not None and 28 <= n['midi'] <= 96
             and not any(n['effects'].get(k) for k in ['mt', 'bn', 'bnv', 'sl', 'slu'])]
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


def assess(tracks, audio_path, duration, map_hash):
    """Assess already mapped track events without adjusting their positions.

    All non-silent body windows containing attacks must have supporting pitch
    AND attack evidence in at least one part. Other parts remain explicitly
    unassessed/inconclusive; this checks the shared recording clock, not tab quality.
    """
    if hasattr(audio_path, "read"):
        audio_path.seek(0)
        audio_hash = hashlib.sha256(audio_path.read()).hexdigest()
        audio_path.seek(0)
    else:
        audio_hash = hashlib.sha256(Path(audio_path).read_bytes()).hexdigest()
    pitch, flux, signal = features(audio_path)
    actual_duration = len(signal) / RATE
    if abs(actual_duration - duration) > .001:
        raise ValueError("The timing check audio duration does not match the recording.")
    return {**assess_features(tracks, pitch, flux, duration),
            "audioSha256": audio_hash, "audioDuration": duration, "mapHash": map_hash}


def assess_features(tracks, pitch, flux, duration):
    """Pure evidence decision, also exercised with known negative controls."""
    lags = np.arange(-.30, .3001, .01)
    wide = np.arange(-3, 3.001, .05)
    near = abs(lags) <= .12001
    zero = int(np.argmin(abs(lags)))
    windows = []
    for left in np.arange(0, max(1, duration - 8), 8):
        right = min(duration, left + 16)
        active = any(left <= n["t"] < right for t in tracks for n in t["events"])
        if not active:
            continue
        rows = []
        for track in tracks:
            times, vectors = samples(track["events"], left, right - .2)
            attacks = np.asarray(sorted({round(n["t"], 3) for n in track["events"]
                if left <= n["t"] < right - .2 and not any(n["effects"].get(k) for k in ("ho", "po", "ln"))}))
            if len(times) < 6 or len(attacks) < 6:
                rows.append({"trackId": track["id"], "status": "inconclusive", "reason": "too_little_distinct_material"})
                continue
            ps = pitch_scores(pitch, times, vectors, lags)
            pw = pitch_scores(pitch, times, vectors, wide)
            ac = attack_scores(flux, attacks, lags, 0 if track["instrument"] == "bass" else 1)
            # Harmonic windows integrate ringing audio and need not peak exactly
            # at the sampled position inside a note. Attack timing is checked
            # independently, so allow this bounded pitch-analysis neighbourhood.
            pitch_near = float(ps[near].max())
            pitch_rank = float((pw < pitch_near).mean())
            local_lag = float(lags[ac.argmax()])
            near_lag = float(lags[near][ac[near].argmax()])
            # A nearby competing beat can be marginally stronger. Require the
            # mapped neighbourhood to retain almost all attack evidence, and
            # forbid a materially stronger out-of-neighbourhood peak.
            supported = (pitch_near >= .10 and pitch_rank >= .90
                         and pitch_near >= .95 * ps.max() and ps.max() - np.median(pw) >= .015
                         and abs(near_lag) <= .08001
                         and ac[near].max() >= 1.15 * np.median(ac)
                         and ac[near].max() >= .90 * ac.max())
            rows.append({"trackId": track["id"], "status": "supported" if supported else "inconclusive",
                         "pitchedAttacks": len(times), "attacks": len(attacks),
                         "pitchAtMap": round(float(ps[zero]), 6), "pitchShiftRank": round(pitch_rank, 6),
                         "pitchNearBest": round(pitch_near, 6),
                         "pitchBest": round(float(ps.max()), 6), "pitchContrast": round(float(ps.max() - np.median(pw)), 6),
                         "attackAtMap": round(float(ac[zero]), 6), "attackBest": round(float(ac.max()), 6),
                         "attackNearBest": round(float(ac[near].max()), 6),
                         "attackContrast": round(float(ac[near].max() / max(np.median(ac), 1e-9)), 6),
                         "attackNearLag": round(near_lag, 3), "attackLocalLag": round(local_lag, 3)})
        windows.append({"start": round(float(left), 6), "end": round(float(right), 6),
                        "status": "supported" if any(r["status"] == "supported" for r in rows) else "inconclusive", "tracks": rows})
    supported = len(windows) >= 3 and all(w["status"] == "supported" for w in windows)
    return {"version": VERSION, "status": "supported" if supported else "inconclusive",
            "windows": windows, "windowCount": len(windows),
            "supportedWindows": sum(w["status"] == "supported" for w in windows),
            "scope": "shared_recording_timing", "everyNoteVerified": False,
            "calibratedProbability": False}

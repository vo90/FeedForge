"""Independent conservative score/audio matching; no copied game implementation.

This first matcher accepts a constant tempo ratio and offset only. Its gates are
engineering safeguards, not a calibrated probability of musical correctness.
Nonlinear drift and ambiguous recordings are deliberately not auto-published.
"""
from __future__ import annotations

import math
from pathlib import Path

from .audio import ImportFailure

VERSION = "affine-chroma-v2"
ANALYSIS_RATE = 11025
HOP = 441


def _audio_features(path: Path):
    import numpy as np
    import soundfile as sf

    # Decode in bounded blocks and downsample only the analysis copy. The full
    # recording used by the game keeps its original timeline and sample rate.
    chunks = []
    with sf.SoundFile(path) as reader:
        rate = reader.samplerate
        for block in reader.blocks(blocksize=rate * 10, dtype="float32", always_2d=True):
            mono = block.mean(axis=1)
            count = round(len(mono) * ANALYSIS_RATE / rate)
            chunks.append(np.interp(np.arange(count) * rate / ANALYSIS_RATE, np.arange(len(mono)), mono))
    signal = np.concatenate(chunks)
    nfft = 4096
    signal = np.pad(signal, (nfft // 2, nfft // 2))
    frequencies = np.fft.rfftfreq(nfft, 1 / ANALYSIS_RATE)
    usable = (frequencies >= 35) & (frequencies <= 2200)
    pitch = np.rint(69 + 12 * np.log2(np.maximum(frequencies, 1) / 440)).astype(int) % 12
    frames = 1 + (len(signal) - nfft) // HOP
    chroma = np.zeros((frames, 12), dtype=np.float32)
    energy = np.zeros(frames)
    window = np.hanning(nfft)
    for begin in range(0, frames, 256):
        indices = np.arange(begin, min(begin + 256, frames))[:, None] * HOP + np.arange(nfft)
        block = signal[indices]
        spectrum = abs(np.fft.rfft(block * window, axis=1)) ** 2
        for pc in range(12):
            chroma[begin:begin + len(block), pc] = spectrum[:, usable & (pitch == pc)].sum(axis=1)
        energy[begin:begin + len(block)] = (block * block).mean(axis=1)
    chroma = np.sqrt(chroma)
    chroma /= np.maximum(np.linalg.norm(chroma, axis=1, keepdims=True), 1e-10)
    # Short-window energy flux supplies an independent attack-time check.
    raw = signal[nfft // 2:-nfft // 2]
    hop = 110
    padded = np.pad(raw, (hop, hop))
    rms = np.sqrt(np.convolve(padded * padded, np.ones(hop) / hop, mode="valid"))[::hop]
    flux = np.maximum(np.diff(rms, prepend=rms[0]), 0)
    peaks = np.where((flux[1:-1] > flux[:-2]) & (flux[1:-1] >= flux[2:]) &
                     (flux[1:-1] > max(float(flux.max()) * 0.07, 1e-6)))[0] + 1
    return chroma, peaks * hop / ANALYSIS_RATE, flux[peaks], len(raw) / ANALYSIS_RATE


def _score_samples(performance: dict):
    import numpy as np

    events = []
    for track in performance.get("tracks", []):
        tuning = track.get("tuning", [])
        capo = int(track.get("capo", 0))
        for note in track.get("notes", []):
            string, fret = int(note.get("s", -1)), int(note.get("f", -1))
            if not 0 <= string < len(tuning) or fret < 0 or note.get("mute"):
                continue
            t, duration = float(note["t"]), float(note.get("sus", 0.2))
            if math.isfinite(t) and math.isfinite(duration) and t >= 0:
                events.append((t, max(duration, 0.12), (int(tuning[string]) + capo + fret) % 12))
    groups: dict[float, list] = {}
    for event in events:
        groups.setdefault(round(event[0], 3), []).append(event)
    if len(groups) < 24 or len({event[2] for event in events}) < 4:
        raise ImportFailure("alignment_failed", "This tab has too little distinctive pitched material for reliable automatic matching.")
    rows = sorted(groups.items())
    selected = np.unique(np.linspace(0, len(rows) - 1, min(192, len(rows))).round().astype(int))
    times, attacks, reference = [], [], []
    for index in selected:
        attack, notes = rows[index]
        delay = min(0.16, max(0.06, min(n[1] for n in notes) * 0.4))
        time = attack + delay
        active = [pc for start, duration, pc in events if start <= time <= start + duration]
        if not active:
            active = [n[2] for n in notes]
        vector = np.zeros(12)
        for pc in active:
            vector[pc] = 1
        vector /= max(float(np.linalg.norm(vector)), 1e-9)
        times.append(time)
        attacks.append(attack)
        reference.append(vector)
    return np.asarray(times), np.asarray(attacks), np.asarray(reference)


def align_audio(performance: dict, audio_path: Path, progress=None) -> dict:
    """Fit and validate an affine timeline, or raise alignment_failed."""
    import numpy as np

    if progress:
        progress({"stage": "aligning", "message": "Matching the recording to the performed tab."})
    times, attacks, reference = _score_samples(performance)
    # A nearly periodic pitch sequence has no reliable section identity. A
    # plausible offset can simply pick a neighbouring occurrence of the riff.
    # Reject that ambiguity until a structural/section-aware matcher exists.
    for lag in range(1, min(25, len(reference) // 3)):
        recurrence = float((reference[lag:] * reference[:-lag]).sum(axis=1).mean())
        if recurrence > 0.94:
            raise ImportFailure("alignment_failed", "The repeated passages in this tab are too ambiguous for automatic synchronization.",
                                {"repeatedPatternSimilarity": round(recurrence, 4), "sampleLag": lag})
    chroma, onsets, onset_strength, duration = _audio_features(audio_path)
    score_duration = float(performance.get("duration") or 0)
    if not math.isfinite(score_duration) or score_duration <= 0:
        raise ImportFailure("unsupported_score", "The tab has no valid performed duration.")
    if not 0.65 <= duration / score_duration <= 1.6:
        raise ImportFailure("alignment_failed", "The recording length does not match this tab. Choose the same version of the song.")
    frame_rate = ANALYSIS_RATE / HOP
    train = np.arange(len(times)) % 3 != 1
    validate = ~train

    def scores(scale, offsets, mask):
        positions = (times[mask][None, :] * scale + np.asarray(offsets)[:, None]) * frame_rate
        indices = np.rint(positions).astype(int)
        valid = (indices >= 0) & (indices < len(chroma))
        values = chroma[np.clip(indices, 0, len(chroma) - 1)]
        similarity = (values * reference[mask][None, :, :]).sum(axis=2) * valid
        return similarity.mean(axis=1)

    offsets = np.arange(-min(float(attacks[0]), 10), min(30, max(5, duration - score_duration * 0.75)) + 0.025, 0.05)
    candidates = []
    for scale in np.arange(0.75, 1.2501, 0.005):
        values = scores(scale, offsets, train)
        for index in np.argpartition(values, -min(3, len(values)))[-3:]:
            candidates.append((float(values[index]), float(scale), float(offsets[index])))
    candidates.sort(reverse=True)
    best_score, scale, offset = candidates[0]
    # Refine nearby candidates; precision of this grid is not an accuracy claim.
    fine_offsets = np.arange(offset - 0.06, offset + 0.0601, 0.005)
    for fine_scale in np.arange(scale - 0.005, scale + 0.0051, 0.0005):
        values = scores(fine_scale, fine_offsets, train)
        index = int(values.argmax())
        if values[index] > best_score:
            best_score, scale, offset = float(values[index]), float(fine_scale), float(fine_offsets[index])
    alternatives = [value for value, s, o in candidates
                    if max(abs(o - offset), abs((s - scale) * score_duration + o - offset)) > 0.4]
    margin = best_score - max(alternatives, default=0)
    validation_score = float(scores(scale, [offset], validate)[0])
    regional = []
    for region in np.array_split(np.arange(len(times)), 4):
        mask = np.zeros(len(times), dtype=bool)
        mask[region] = True
        regional.append(float(scores(scale, [offset], mask)[0]))
    diagnostics = {"trainingSimilarity": round(best_score, 4), "validationSimilarity": round(validation_score, 4),
                   "regionalSimilarity": [round(x, 4) for x in regional], "alternativeMargin": round(margin, 4),
                   "sampleCount": len(times), "audioDuration": duration, "scoreDuration": score_duration}
    unmatched_end = duration - (offset + score_duration * scale)
    diagnostics["unmatchedEndSeconds"] = round(unmatched_end, 4)
    if abs(unmatched_end) > max(2.0, duration * 0.05):
        raise ImportFailure("alignment_failed", "The recording and tab have different endings or lengths. Choose the matching version.", diagnostics)
    # These conservative initial gates must be calibrated on a held-out real
    # recording corpus before this experimental path is described as general.
    if best_score < 0.78 or validation_score < 0.75 or min(regional) < 0.67 or margin < 0.025:
        raise ImportFailure("alignment_failed", "The recording could not be matched confidently to this tab. Choose the same recording or another audio source.", diagnostics)
    predicted = attacks * scale + offset
    if len(onsets) < 16:
        raise ImportFailure("alignment_failed", "The recording has too few clear attacks to validate synchronization.", diagnostics)
    # Sustained chord interference also creates small energy-flux peaks. The
    # closest peak can therefore be ringing rather than the note attack. Use
    # the strongest attack inside the bounded chroma-matching neighbourhood.
    distance = np.abs(predicted[:, None] - onsets[None, :])
    neighbours = distance <= 0.12
    chosen = np.argmax(np.where(neighbours, onset_strength[None, :], -1), axis=1)
    matched = onsets[chosen]
    residuals = matched - predicted
    supported = neighbours.any(axis=1)
    support = float(supported.mean())
    regional_bias = [float(np.median(residuals[group])) for group in np.array_split(np.arange(len(residuals)), 4)]
    diagnostics.update({"onsetSupport": round(support, 4), "onsetMedianError": round(float(np.median(abs(residuals))), 4),
                        "onsetP90Error": round(float(np.percentile(abs(residuals), 90)), 4),
                        "regionalOnsetBias": [round(x, 4) for x in regional_bias]})
    if support < 0.75 or max(regional_bias) - min(regional_bias) > 0.12:
        raise ImportFailure("alignment_failed", "This recording needs a changing tempo alignment that is not supported reliably yet. Choose another recording.", diagnostics)
    # Refit both clock scale and offset on training attacks. Held-out attacks
    # and pitch samples remain independent checks. Trim isolated mismatches;
    # never stretch the score to arbitrary local transients.
    inliers = train & supported
    if int(inliers.sum()) < 12:
        raise ImportFailure("alignment_failed", "Too few distinct attacks support automatic synchronization.", diagnostics)
    design = np.column_stack((attacks, np.ones(len(attacks))))
    for _ in range(3):
        refined_scale, refined_offset = np.linalg.lstsq(design[inliers], matched[inliers], rcond=None)[0]
        errors = matched - (attacks * refined_scale + refined_offset)
        centre = float(np.median(errors[inliers]))
        spread = float(np.median(abs(errors[inliers] - centre)))
        inliers = train & supported & (abs(errors - centre) <= max(0.025, 3 * 1.4826 * spread))
        if int(inliers.sum()) < 12:
            raise ImportFailure("alignment_failed", "The recording attacks disagree about a common timeline.", diagnostics)
    predicted_refined = attacks * refined_scale + refined_offset
    errors = matched - predicted_refined
    held_out = validate & supported
    diagnostics.update({"refinedAttackMedianError": round(float(np.median(abs(errors[held_out]))), 4),
                        "refinedAttackP90Error": round(float(np.percentile(abs(errors[held_out]), 90)), 4)})
    if (float(supported[validate].mean()) < 0.75 or
            float(np.percentile(abs(errors[held_out]), 90)) > 0.06 or
            float(np.max(abs(predicted_refined - predicted))) > 0.12 or
            not 0.75 <= refined_scale <= 1.25):
        raise ImportFailure("alignment_failed", "The recording attacks do not support a reliable constant-tempo alignment.", diagnostics)
    # Timing refinement must preserve the musical identity checks, including
    # regions and competing alignments; onset density alone cannot prove it.
    refined_training = float(scores(refined_scale, [refined_offset], train)[0])
    refined_validation = float(scores(refined_scale, [refined_offset], validate)[0])
    refined_regions = []
    for region in np.array_split(np.arange(len(times)), 4):
        mask = np.zeros(len(times), dtype=bool)
        mask[region] = True
        refined_regions.append(float(scores(refined_scale, [refined_offset], mask)[0]))
    alternatives = [value for value, s, o in candidates
                    if max(abs(o - refined_offset), abs((s - refined_scale) * score_duration + o - refined_offset)) > 0.4]
    refined_margin = refined_training - max(alternatives, default=0)
    diagnostics.update({"refinedTrainingSimilarity": round(refined_training, 4),
                        "refinedValidationSimilarity": round(refined_validation, 4),
                        "refinedRegionalSimilarity": [round(x, 4) for x in refined_regions],
                        "refinedAlternativeMargin": round(refined_margin, 4)})
    if (refined_training < 0.78 or refined_validation < 0.75 or min(refined_regions) < 0.67 or
            refined_margin < 0.025):
        raise ImportFailure("alignment_failed", "The refined timing does not preserve a reliable musical match.", diagnostics)
    scale, offset = float(refined_scale), float(refined_offset)
    unmatched_end = duration - (offset + score_duration * scale)
    diagnostics["unmatchedEndSeconds"] = round(unmatched_end, 4)
    if abs(unmatched_end) > max(2.0, duration * 0.05):
        raise ImportFailure("alignment_failed", "The refined tab and recording endings do not agree.", diagnostics)
    if offset < -0.01 or offset + times[-1] * scale > duration + 0.05:
        raise ImportFailure("alignment_failed", "The matching would place part of the tab outside the recording.", diagnostics)
    return {"method": VERSION, "status": "validated", "experimental": True,
            "offset": max(0, offset), "scale": scale, "diagnostics": diagnostics,
            "anchors": [{"score": 0.0, "audio": max(0, offset)},
                        {"score": score_duration, "audio": offset + score_duration * scale}]}


def map_time(alignment: dict, time: float) -> float:
    value = float(alignment["offset"]) + float(time) * float(alignment["scale"])
    if not math.isfinite(value) or value < -0.00001:
        raise ImportFailure("alignment_failed", "Invalid time in the matched arrangement.")
    return round(max(0, value), 6)

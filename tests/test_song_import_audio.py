from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from feedback_converter.song_import.alignment import align_audio
from feedback_converter.song_import.audio import ImportFailure, _public_url, prepare_audio


def performance_and_audio(tmp_path: Path, *, offset=1.2, scale=1.03, wrong=False, drift=False, repetitive=False,
                          spacing=0.45, sustain=0.29):
    rng = np.random.default_rng(512)
    frets = np.tile([0, 3, 5, 7], 12) if repetitive else rng.integers(0, 12, 48)
    starts = np.arange(48) * spacing
    notes = [{"t": float(t), "s": 5, "f": int(fret), "sus": sustain} for t, fret in zip(starts, frets)]
    performance = {"title": "Test Song", "artist": "Original Artist", "duration": float(starts[-1] + 0.4),
                   "tracks": [{"id": "lead", "name": "Guitar", "instrument": "guitar", "role": "lead",
                               "tuning": [40, 45, 50, 55, 59, 64], "capo": 0, "notes": notes}],
                   "beats": [{"time": float(t), "measure": i // 4} for i, t in enumerate(starts)],
                   "sections": [{"time": 0, "name": "Song"}], "tempos": [{"time": 0, "bpm": 60 / 0.45}]}
    warp = (0.16 * np.sin(2 * np.pi * starts / starts[-1]) if drift == "oscillating" else
            0.014 * starts ** 2 if drift else 0)
    actual_starts = starts * scale + offset + warp
    rate = 22050
    samples = np.zeros(round((actual_starts[-1] + 1) * rate))
    sounding = rng.integers(0, 12, 48) if wrong else frets
    for start, fret in zip(actual_starts, sounding):
        length = round(sustain * scale * rate)
        time = np.arange(length) / rate
        frequency = 440 * 2 ** ((64 + int(fret) - 69) / 12)
        wave = 0.6 * np.sin(2 * np.pi * frequency * time)
        fade = round(rate * 0.008)
        wave[:fade] *= np.linspace(0, 1, fade)
        wave[-fade:] *= np.linspace(1, 0, fade)
        index = round(start * rate)
        samples[index:index + length] += wave
    path = tmp_path / "recording.wav"
    sf.write(path, samples, rate)
    return performance, path


def test_local_audio_has_playable_full_mix_preview_and_identity(tmp_path):
    _, source = performance_and_audio(tmp_path)
    job = tmp_path / "job"
    job.mkdir()
    audio = prepare_audio({"kind": "file", "path": str(source)}, job)
    assert sf.info(audio["path"]).duration == pytest.approx(sf.info(source).duration, abs=0.002)
    assert sf.info(audio["previewPath"]).duration > 2
    assert len(audio["hash"]) == 64
    assert len(audio["source"]["sha256"]) == 64
    assert source.exists()


def test_alignment_recovers_offset_and_tempo_from_distinct_recording(tmp_path):
    score, path = performance_and_audio(tmp_path)
    result = align_audio(score, path)
    assert result["status"] == "validated"
    assert result["experimental"] is True
    assert result["offset"] == pytest.approx(1.2, abs=0.055)
    assert result["scale"] == pytest.approx(1.03, abs=0.004)
    assert result["diagnostics"]["onsetSupport"] > 0.9


@pytest.mark.parametrize("offset,scale,spacing,sustain", [(1.2, 1.03, 0.45, 0.29), (1.25, 1.04, 0.5, 0.4)])
def test_two_instrument_chord_mix_matches_one_shared_timeline(tmp_path, offset, scale, spacing, sustain):
    score, path = performance_and_audio(tmp_path, offset=offset, scale=scale, spacing=spacing, sustain=sustain)
    samples, rate = sf.read(path)
    guitar = score["tracks"][0]
    roots = list(guitar["notes"])
    bass = {"id": "bass", "name": "Bass", "instrument": "bass", "role": "bass", "capo": 0,
            "tuning": [28, 33, 38, 43], "notes": []}
    for root in roots:
        pitch = 64 + root["f"]
        chord = {**root, "s": 4, "f": (pitch + 7 - 59) % 12}
        low = {**root, "s": 0, "f": (pitch - 28) % 12}
        guitar["notes"].append(chord)
        bass["notes"].append(low)
        for midi, amplitude in [(59 + chord["f"], 0.6), (28 + low["f"], 0.3)]:
            length = round(root["sus"] * scale * rate)
            t = np.arange(length) / rate
            wave = amplitude * np.sin(2 * np.pi * 440 * 2 ** ((midi - 69) / 12) * t)
            fade = round(rate * 0.008)
            wave[:fade] *= np.linspace(0, 1, fade)
            wave[-fade:] *= np.linspace(1, 0, fade)
            start = round((root["t"] * scale + offset) * rate)
            samples[start:start + length] += wave
    score["tracks"].append(bass)
    sf.write(path, samples * 0.55, rate)
    result = align_audio(score, path)
    assert result["offset"] == pytest.approx(offset, abs=0.055)
    assert result["scale"] == pytest.approx(scale, abs=0.004)
    # Attacks in the longer, 120 bpm polyphonic fixture expose false matches
    # to weak beating peaks inside sustained notes. Check the whole timeline.
    for note in roots:
        assert result["offset"] + note["t"] * result["scale"] == pytest.approx(offset + note["t"] * scale, abs=0.055)


def test_matching_excerpt_of_longer_recording_is_not_a_complete_song(tmp_path):
    score, path = performance_and_audio(tmp_path)
    samples, rate = sf.read(path)
    extra = 0.2 * np.sin(2 * np.pi * 220 * np.arange(rate * 7) / rate)
    sf.write(path, np.concatenate([samples, extra]), rate)
    with pytest.raises(ImportFailure, match="endings|lengths"):
        align_audio(score, path)


@pytest.mark.parametrize("options", [{"wrong": True}, {"drift": True}, {"drift": "oscillating"}, {"repetitive": True}])
def test_wrong_drifting_or_ambiguous_recording_never_becomes_ready(tmp_path, options):
    score, path = performance_and_audio(tmp_path, **options)
    with pytest.raises(ImportFailure) as error:
        align_audio(score, path)
    assert error.value.code == "alignment_failed"


def test_missing_and_silent_audio_need_replacement(tmp_path):
    with pytest.raises(ImportFailure, match="Choose an audio"):
        prepare_audio(None, tmp_path)
    path = tmp_path / "silent.wav"
    sf.write(path, np.zeros(22050 * 3), 22050)
    with pytest.raises(ImportFailure, match="silent"):
        prepare_audio({"kind": "file", "path": str(path)}, tmp_path)


@pytest.mark.parametrize("url", ["file:///secret.wav", "http://user:password@example.com/x", "http://127.0.0.1/a.wav", "http://[::1]/a.wav"])
def test_remote_audio_rejects_private_or_credential_urls(url):
    with pytest.raises(ImportFailure):
        _public_url(url)

import io

import numpy as np
import pytest
import soundfile as sf

from feedback_converter.song_import import recording_sync as rs


def recording():
    t = np.arange(22050 * 3) / 22050
    wave = .2 * np.sin(2 * np.pi * 82.41 * t) + .1 * np.sin(2 * np.pi * 246.94 * t)
    stream = io.BytesIO()
    sf.write(stream, np.column_stack([wave, wave * .7]), 22050, format='WAV', subtype='FLOAT')
    stream.seek(0)
    return stream


@pytest.mark.parametrize('budget', [0, 100000, 64 * 1024 * 1024])
def test_reuse_and_eviction_are_numerically_identical(budget, monkeypatch):
    path = recording()
    keys = [(440., 28), (450., 28), (430., 21)]
    expected = {}
    for key in keys:
        path.seek(0)
        expected[key] = rs.features(path, reference_hz=key[0], midi_min=key[1])
    calls = []
    decode = rs._decode
    monkeypatch.setattr(rs, '_decode', lambda p: (calls.append(p), decode(p))[1])
    with rs.FeatureSession(path, cache_bytes=budget) as session:
        session.prepare(keys)
        for hz, midi in keys + keys[::-1]:
            actual = session.features(reference_hz=hz, midi_min=midi)
            for a, e in zip(actual, expected[hz, midi]):
                np.testing.assert_array_equal(a, e)
            assert session.cached_bytes <= budget
    assert len(calls) == 1
    assert session.signal is session.flux is session.path is None
    assert not session.cache
    with pytest.raises(RuntimeError, match='closed'):
        session.features()


def test_alternatives_share_blocks_and_cache_hits(monkeypatch):
    calls = []
    pitches = rs._pitches
    monkeypatch.setattr(rs, '_pitches', lambda s, c, keys: (calls.append(keys), pitches(s, c, keys))[1])
    with rs.FeatureSession(recording()) as session:
        session.features()
        session.prepare([(450., 28), (430., 28), (450., 28)])
        session.features(reference_hz=450.)
        session.features(reference_hz=430.)
    assert calls == [[(440., 28)], [(450., 28), (430., 28)]]


def test_session_releases_on_error_and_does_not_cross_recordings():
    first, second = rs.FeatureSession(recording()), rs.FeatureSession(recording())
    with pytest.raises(ValueError, match='pitch profile'):
        with first:
            first.features()
            first.prepare([(440., 22)])
    assert first.closed and first.cached_bytes == 0 and first.signal is None
    with second:
        second.features()
        assert second.cached_bytes > 0


@pytest.mark.parametrize('seconds,channels,nonfinite', [(1,1,False),(3,3,False),(3,1,True)])
def test_invalid_audio_is_still_rejected(seconds, channels, nonfinite):
    stream = io.BytesIO()
    samples = np.zeros((11025 * seconds, channels))
    if nonfinite:
        samples[100] = np.nan
    sf.write(stream, samples, 11025, format='WAV', subtype='FLOAT')
    with pytest.raises(ValueError):
        with rs.FeatureSession(stream) as session:
            session.features()
    assert session.closed and session.signal is None

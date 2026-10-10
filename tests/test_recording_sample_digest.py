"""Decoded identity reuse stays local to the exact acoustic snapshot."""
import io
import weakref

import numpy as np
import pytest
import soundfile as sf

from feedback_converter.song_import import local_sync as ls, recording_sync as rs
from feedback_converter.song_import.audio import ImportFailure
from feedback_converter.song_import.preparation import finalize, recording_view


def recording(*, rate=22050, channels=2, frames=None, format='WAV', subtype='FLOAT'):
    frames = frames if frames is not None else 3 * rate
    t = np.arange(frames) / rate
    samples = np.column_stack([.2 * np.sin(2 * np.pi * (110 + 55 * c) * t)
                               for c in range(channels)]).astype('float32')
    stream = io.BytesIO()
    sf.write(stream, samples, rate, format=format, subtype=subtype)
    stream.seek(0)
    return stream


def changed_samples(data):
    samples, rate = sf.read(io.BytesIO(data), dtype='float32', always_2d=True)
    samples[1000:1100] *= .5
    stream = io.BytesIO()
    sf.write(stream, samples, rate, format='WAV', subtype='FLOAT')
    return stream.getvalue()


@pytest.mark.parametrize('rate', [8000, 44100, 48000, 96000])
@pytest.mark.parametrize('channels', [1, 2])
@pytest.mark.parametrize('format,subtype', [('WAV', 'FLOAT'), ('WAV', 'PCM_16'),
                                          ('FLAC', 'PCM_24'), ('OGG', 'VORBIS')])
def test_full_native_read_matches_independent_block_digest(rate, channels, format, subtype):
    data = recording(rate=rate, channels=channels, frames=2 * rate + 7,
                     format=format, subtype=subtype).getvalue()
    expected, count = rs._decode(io.BytesIO(data))
    actual, actual_count, digest = rs._decode(io.BytesIO(data), hash_samples=True)
    np.testing.assert_array_equal(actual, expected)
    assert actual_count == count
    assert digest == ls._hash(io.BytesIO(data))


def test_standalone_and_default_session_do_not_compute_sample_hash(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail('Default features must not add a decoded-sample hash.')

    monkeypatch.setattr(rs.hashlib, 'sha256', unexpected)
    expected = rs.features(recording())
    with rs.FeatureSession(recording()) as session:
        actual = session.features()
        assert session.sample_sha256 is None
        for a, e in zip(actual, expected):
            np.testing.assert_array_equal(a, e)


def test_opt_in_session_hashes_once_and_releases_native_before_onsets(monkeypatch):
    stream = recording(rate=8000, frames=32768 + 7)
    expected_hash = ls._hash(stream)
    original_read, original_onsets = sf.SoundFile.read, rs._onsets
    original_sha256 = rs.hashlib.sha256
    reads, native_refs, update_sizes = [], [], []

    def read(reader, *args, **kwargs):
        native = original_read(reader, *args, **kwargs)
        reads.append(reader)
        native_refs.append(weakref.ref(native))
        return native

    def onsets(*args):
        assert all(ref() is None for ref in native_refs)
        return original_onsets(*args)

    class Digest:
        def __init__(self, *args):
            self.digest = original_sha256(*args)

        def update(self, data):
            update_sizes.append(len(data))
            self.digest.update(data)

        def hexdigest(self):
            return self.digest.hexdigest()

    monkeypatch.setattr(sf.SoundFile, 'read', read)
    monkeypatch.setattr(rs, '_onsets', onsets)
    monkeypatch.setattr(rs.hashlib, 'sha256', Digest)
    with rs.FeatureSession(stream, hash_samples=True, cache_bytes=0) as session:
        for hz in [440., 450., 430., 440.]:
            session.features(reference_hz=hz)
            assert session.sample_sha256 == expected_hash
        assert session.cached_bytes == 0
    assert len(reads) == 1 and reads[0].closed
    assert update_sizes[1:] == [32768 * 2 * 4, 7 * 2 * 4]
    assert session.sample_sha256 is session.signal is session.flux is session.path is None
    with pytest.raises(RuntimeError, match='closed'):
        session.features()


@pytest.mark.parametrize('frames,channels', [(15999, 1), (1202 * 8000 + 1, 1), (16000, 3)])
def test_invalid_bounds_never_read_or_publish_a_digest(monkeypatch, frames, channels):
    class Reader:
        samplerate = 8000
        closed = False

        def __init__(self, _):
            self.frames, self.channels = frames, channels

        def __len__(self):
            return self.frames

        def __enter__(self):
            return self

        def __exit__(self, *_):
            self.closed = True

        def read(self, **kwargs):
            pytest.fail('Invalid bounds must fail before decoding.')

    reader = Reader(None)
    monkeypatch.setattr(sf, 'SoundFile', lambda _: reader)
    with pytest.raises(ValueError, match='bounded mono or stereo'):
        with rs.FeatureSession(io.BytesIO(), hash_samples=True) as session:
            session.features()
    assert reader.closed and session.closed
    assert session.sample_sha256 is session.signal is session.flux is session.path is None


@pytest.mark.parametrize('invalid', [np.nan, np.inf, -np.inf, 'truncated'])
def test_bad_recording_cannot_leave_digest_or_open_reader(monkeypatch, invalid):
    stream = recording()
    if invalid == 'truncated':
        stream = io.BytesIO(stream.getvalue()[:20])
    else:
        native, rate = sf.read(stream, dtype='float32', always_2d=True)
        native[100, 0] = invalid
        stream = io.BytesIO()
        sf.write(stream, native, rate, format='WAV', subtype='FLOAT')
    readers, original = [], sf.SoundFile

    def opened(*args, **kwargs):
        reader = original(*args, **kwargs)
        readers.append(reader)
        return reader

    monkeypatch.setattr(sf, 'SoundFile', opened)
    with pytest.raises((ValueError, RuntimeError)):
        with rs.FeatureSession(stream, hash_samples=True) as session:
            session.features()
    assert all(reader.closed for reader in readers)
    assert not stream.closed and session.closed and not session.cache
    assert session.sample_sha256 is session.signal is session.flux is session.path is None


@pytest.mark.parametrize('stage', ['read', 'onsets', 'pitches', 'assessment', 'alternate', 'duration'])
def test_assessment_failure_closes_and_clears_session(monkeypatch, stage):
    sessions, readers = [], []
    original_session, original_file = rs.FeatureSession, sf.SoundFile

    class Session(original_session):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            sessions.append(self)

    def opened(*args, **kwargs):
        reader = original_file(*args, **kwargs)
        readers.append(reader)
        return reader

    def fail(*args, **kwargs):
        if stage == 'onsets':
            assert sessions[0].signal is sessions[0].sample_sha256 is None
        raise ValueError('injected failure')

    stream = recording()
    monkeypatch.setattr(rs, 'FeatureSession', Session)
    monkeypatch.setattr(sf, 'SoundFile', opened)
    monkeypatch.setattr(ls, 'tuning', lambda _: {'referenceHz': 440., 'cents': 0.})
    if stage == 'read':
        monkeypatch.setattr(original_file, 'read', fail)
    elif stage in ('onsets', 'pitches'):
        monkeypatch.setattr(rs, '_' + stage, fail)
    elif stage == 'assessment':
        monkeypatch.setattr(ls, 'assess_features', fail)
    elif stage == 'alternate':
        from feedback_converter.song_import import pitch_clock
        monkeypatch.setattr(pitch_clock, 'assess', fail)
    with pytest.raises(ValueError, match='injected failure|duration'):
        ls.assess([], stream, 4. if stage == 'duration' else 3., 'map')
    assert len(sessions) == 1 and all(reader.closed for reader in readers)
    session = sessions[0]
    assert session.closed and not session.cache and not stream.closed
    assert session.sample_sha256 is session.signal is session.flux is session.path is None


def test_failed_onsets_can_retry_only_with_a_fresh_decode(monkeypatch):
    calls, original_decode = [], rs._decode

    def decode(*args, **kwargs):
        calls.append(True)
        return original_decode(*args, **kwargs)

    def fail(*args):
        raise ValueError('onsets failed')

    monkeypatch.setattr(rs, '_decode', decode)
    stream = recording()
    expected = ls._hash(stream)
    with rs.FeatureSession(stream, hash_samples=True) as session:
        with monkeypatch.context() as patch:
            patch.setattr(rs, '_onsets', fail)
            with pytest.raises(ValueError, match='onsets failed'):
                session.features()
        assert session.signal is session.flux is session.sample_sha256 is None
        assert not session.cache and session.count == 0
        session.features()
        assert session.sample_sha256 == expected
        assert len(calls) == 2
    assert session.closed and session.sample_sha256 is None


@pytest.mark.parametrize('when', ['load', 'after_assessment'])
def test_seek_failure_still_clears_the_owned_session(monkeypatch, when):
    class Stream(io.BytesIO):
        fail = False

        def seek(self, *args):
            if self.fail:
                raise OSError('seek failed')
            return super().seek(*args)

    stream, sessions = Stream(recording().getvalue()), []
    original_session, assess_features = rs.FeatureSession, ls.assess_features

    class Session(original_session):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            sessions.append(self)

    def finish(*args, **kwargs):
        report = assess_features(*args, **kwargs)
        stream.fail = True
        return report

    monkeypatch.setattr(rs, 'FeatureSession', Session)
    monkeypatch.setattr(ls, 'tuning', lambda _: {'referenceHz': 440., 'cents': 0.})
    if when == 'load':
        stream.fail = True
    else:
        monkeypatch.setattr(ls, 'assess_features', finish)
    with pytest.raises(OSError, match='seek failed'):
        ls.assess([], stream, 3., 'map', version=ls.VERSION)
    assert len(sessions) == 1 and sessions[0].closed and not stream.closed
    assert sessions[0].sample_sha256 is sessions[0].signal is sessions[0].flux is None


@pytest.mark.parametrize('version', [ls.VERSION, 'local-recording-clock-v2', 'local-recording-clock-v3'])
@pytest.mark.parametrize('kind', ['path', 'stream', 'recording_view'])
def test_complete_report_matches_separate_decode_hash(tmp_path, monkeypatch, version, kind):
    data = recording().getvalue()
    tracks = [{'id': 'guitar', 'instrument': 'guitar', 'events': [
        {'t': .2 + i * .3, 'end': .3 + i * .3, 'midi': 45, 'effects': {}}
        for i in range(8)]}]
    if kind == 'path':
        path = tmp_path / 'audio.wav'
        path.write_bytes(data)
    elif kind == 'recording_view':
        padded = recording(frames=22050 * 6)
        path = recording_view(padded, {'frames': 22050 * 2, 'endingFrames': 22050,
                                       'originalFrames': 22050 * 3})
        assert ls._hash(path) != ls._hash(padded)
    else:
        path = io.BytesIO(data)
        path.seek(17)
    original_decode = rs._decode

    def separate_decode_hash(path, *, hash_samples=False):
        signal, count = original_decode(path)
        return (signal, count, ls._hash(path)) if hash_samples else (signal, count)

    with monkeypatch.context() as patch:
        patch.setattr(rs, '_decode', separate_decode_hash)
        expected = ls.assess(tracks, path, 3., 'same-map', version=version)
    assert expected['windows'][0]['parts'][0]['groups'] == 8
    if hasattr(path, 'tell'):
        assert path.tell() == 0
    calls = []

    def decode(*args, **kwargs):
        calls.append(kwargs)
        return original_decode(*args, **kwargs)

    monkeypatch.setattr(rs, '_decode', decode)
    monkeypatch.setattr(ls, '_hash', lambda _: pytest.fail('Assessment must not decode again to hash.'))
    actual = ls.assess(tracks, path, 3., 'same-map', version=version)
    assert actual == expected
    assert calls == [{'hash_samples': True}]
    if hasattr(path, 'tell'):
        assert path.tell() == 0 and not path.closed


@pytest.mark.parametrize('kind', ['path', 'stream'])
def test_digest_binds_analyzed_snapshot_and_never_crosses_assessments(tmp_path, monkeypatch, kind):
    original = recording().getvalue()
    replacement = changed_samples(original)
    assert sf.info(io.BytesIO(original)).frames == sf.info(io.BytesIO(replacement)).frames
    initial_digest = ls._hash(io.BytesIO(original))
    changed_digest = ls._hash(io.BytesIO(replacement))
    if kind == 'path':
        path = tmp_path / 'audio.wav'
        path.write_bytes(original)
    else:
        path = io.BytesIO(original)
    assess_features = ls.assess_features

    def replace_after_decode(*args, **kwargs):
        if kind == 'path':
            path.write_bytes(replacement)
        else:
            path.seek(0)
            path.truncate()
            path.write(replacement)
        return assess_features(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(ls, 'assess_features', replace_after_decode)
        report = ls.assess([], path, 3., 'map', version=ls.VERSION)
    assert report['audioSha256'] == initial_digest != changed_digest
    assert ls._hash(path) == changed_digest
    assert ls.assess([], path, 3., 'map', version=ls.VERSION)['audioSha256'] == changed_digest


def test_replaced_ending_source_is_reread_before_preparation(tmp_path, monkeypatch):
    path = tmp_path / 'audio.wav'
    original = recording().getvalue()
    path.write_bytes(original)
    with rs.FeatureSession(path, hash_samples=True) as session:
        session.features()
        original_digest = session.sample_sha256
    path.write_bytes(changed_samples(original))
    alignment = {'offset': 0., 'scale': 1., 'endingPadding': {
        'sourceSamplesSha256': original_digest, 'frames': 100,
        'sampleRate': 22050, 'originalFrames': 22050 * 3}}
    calls, original_hash = [], ls._hash

    def fresh_hash(source):
        calls.append(source)
        return original_hash(source)

    monkeypatch.setattr(ls, '_hash', fresh_hash)
    with pytest.raises(ImportFailure, match='recording changed') as caught:
        finalize({'tracks': [{'notes': [{'t': 0, 'sus': 1}]}]},
                 {'path': str(path), 'duration': 3.}, alignment, tmp_path)
    assert caught.value.code == 'needs_audio' and calls == [path]
    assert not (tmp_path / 'prepared-full.ogg').exists()

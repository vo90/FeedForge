"""Long recordings retain samples, timing and finite acquisition bounds."""
import io
import json
import subprocess
import sys
import types

import numpy as np
import pytest
import soundfile as sf

from feedback_converter.song_import import audio, recording_sync as rs, local_sync as ls
from feedback_converter.song_import.audio_limits import MAX_DURATION, MAX_ANALYSIS_DURATION
from feedback_converter.song_import.preparation import recording_view


@pytest.mark.parametrize('external', [False, True])
@pytest.mark.parametrize('duration', [None, 2, 1200, 1239, 3600, 3600.01, 1, True, '3600', float('inf'), float('nan')])
def test_downloaders_share_bounds_and_preserve_rejection_reason(tmp_path, monkeypatch, external, duration):
    valid=duration is None or type(duration) in (int,float) and np.isfinite(duration) and 2 <= duration <= MAX_DURATION
    info={'id':'abcdefghijk','duration':duration}
    transfers=[]
    monkeypatch.setattr(audio, '_tool', lambda *args: 'ffmpeg')
    if external:
        def run(command, *args, **kwargs):
            if '--skip-download' in command:
                assert '--dump-single-json' in command
                return subprocess.CompletedProcess(command,0,json.dumps(info).encode(),b'')
            transfers.append(command)
            (tmp_path/'download.ogg').write_bytes(b'fixture')
            (tmp_path/'download.info.json').write_text(json.dumps(info))
        monkeypatch.setattr(audio,'_run',run)
    else:
        class Downloader:
            def __init__(self,options):self.options=options
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def extract_info(self,*args,**kwargs):
                self.options['match_filter'](info)
                transfers.append(True)
                (tmp_path/'download.ogg').write_bytes(b'fixture')
                return info
        monkeypatch.setitem(sys.modules,'yt_dlp',types.SimpleNamespace(YoutubeDL=Downloader))
        monkeypatch.setattr(audio.importlib.util,'find_spec',lambda _:True)
    if valid:
        path,_=audio._download_youtube('https://youtube.com/watch?v=abcdefghijk',tmp_path,{'ytDlp':'tool'} if external else {})
        assert path.name=='download.ogg' and len(transfers)==1
    else:
        with pytest.raises(audio.ImportFailure,match='60 minutes') as failure:
            audio._download_youtube('https://youtube.com/watch?v=abcdefghijk',tmp_path,{'ytDlp':'tool'} if external else {})
        assert failure.value.transport is None and failure.value.code=='needs_audio'
        assert failure.value.diagnostics['audioDurationLimit']['maximumSeconds']==3600
        assert not transfers


@pytest.mark.parametrize('rate',[4000,8000,11025,22050,44100,48000,96000,44117])
@pytest.mark.parametrize('channels',[1,2])
@pytest.mark.parametrize('extra',[0,1,7])
def test_streaming_resampler_is_bit_identical_to_whole_recording(rate,channels,extra):
    frames=max(2*rate,32768*3)+extra
    samples=np.random.default_rng(42).uniform(-.8,.8,(frames,channels)).astype('float32')
    stream=io.BytesIO();sf.write(stream,samples,rate,format='WAV',subtype='FLOAT')
    mono=samples.mean(axis=1)
    expected=np.interp(np.arange(round(len(mono)*rs.RATE/rate))*rate/rs.RATE,
                       np.arange(len(mono)),mono).astype('float32')
    stream.seek(0)
    actual,count,digest=rs._decode(stream,hash_samples=True)
    np.testing.assert_array_equal(actual,expected)
    assert count==1+len(expected)//rs.HOP
    stream.seek(0);assert digest==ls._hash(stream)


@pytest.mark.parametrize('duration',[1239,3600,3604])
def test_long_analysis_reads_bounded_blocks(tmp_path,monkeypatch,duration):
    path=tmp_path/'long.wav';rate=8000
    # Sparse synthetic fixture: exact duration with negligible creation cost.
    with sf.SoundFile(path,'w',samplerate=rate,channels=1,subtype='PCM_16') as writer:
        writer.seek(int(duration*rate)-1);writer.write([.25])
    read=sf.SoundFile.read;requests=[]
    def bounded(reader,frames=-1,*args,**kwargs):
        assert 0 < frames <= 32768
        requests.append(frames)
        return read(reader,frames,*args,**kwargs)
    monkeypatch.setattr(sf.SoundFile,'read',bounded)
    signal,count=rs._decode(path)
    assert len(signal)==round(duration*rs.RATE) and len(requests)>1
    assert np.isfinite(signal).all()


def test_trimmed_view_preserves_bytes_and_spools_large_recording(tmp_path):
    path=tmp_path/'padded.wav';rate=8000;frames=rate*140
    samples=np.random.default_rng(7).uniform(-.25,.25,(32768,2)).astype('float32')
    with sf.SoundFile(path,'w',samplerate=rate,channels=2,subtype='FLOAT') as writer:
        for start in range(0,frames,32768):writer.write(samples[:min(32768,frames-start)])
    prep={'frames':16000,'originalFrames':frames-24000,'endingFrames':8000}
    with sf.SoundFile(path) as reader:
        reader.seek(prep['frames']);data=reader.read(prep['originalFrames'],dtype='float32',always_2d=True)
    old=io.BytesIO();sf.write(old,data,rate,format='WAV',subtype='FLOAT')
    with recording_view(path,prep) as view:
        assert view._rolled
        assert view.read()==old.getvalue()


@pytest.mark.parametrize('kind',['file','url'])
@pytest.mark.parametrize('duration',[1239,3600,3601])
def test_decoded_duration_is_authoritative_for_local_and_direct_audio(tmp_path,monkeypatch,kind,duration):
    path=tmp_path/'source.wav'
    with sf.SoundFile(path,'w',samplerate=8000,channels=1,subtype='PCM_16') as writer:
        writer.seek(duration*8000-1);writer.write([.5])
    # Stop after validation to avoid encoding hours of fixture silence.
    class ReachedEncoding(Exception):pass
    monkeypatch.setattr(audio,'sha256_file',lambda _: 'fixture-hash')
    real=sf.SoundFile
    def opened(file,mode='r',*args,**kwargs):
        if mode=='w':raise ReachedEncoding()
        return real(file,mode,*args,**kwargs)
    monkeypatch.setattr(sf,'SoundFile',opened)
    monkeypatch.setattr(audio,'_public_url',lambda x:x)
    monkeypatch.setattr(audio,'_download_direct',lambda url,target: target.write_bytes(path.read_bytes()))
    request={'kind':'file','path':str(path)} if kind=='file' else {'kind':'url','url':'https://example.org/audio.wav'}
    if duration<=MAX_DURATION:
        with pytest.raises(ReachedEncoding):audio.prepare_audio(request,tmp_path)
    else:
        with pytest.raises(audio.ImportFailure,match='60 minutes'):audio.prepare_audio(request,tmp_path)

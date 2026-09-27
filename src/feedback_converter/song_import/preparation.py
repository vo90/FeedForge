"""One audio encode for preparation time and an independently approved short tail."""
from copy import deepcopy
import io
import math
from pathlib import Path

import numpy as np
import soundfile as sf

from .alignment import map_time
from .audio import ImportFailure, sha256_file

POLICY = 'minimum-two-second-preparation-v1'


def recording_view(path, preparation):
    """Decode only the original recording, excluding both declared silent ends."""
    if not preparation or not (preparation.get('frames') or preparation.get('endingFrames')):
        return path
    if hasattr(path,'seek'):path.seek(0)
    with sf.SoundFile(path) as reader:
        reader.seek(preparation['frames'])
        data=reader.read(frames=preparation.get('originalFrames', -1),dtype='float32',always_2d=True)
        rate=reader.samplerate
    output=io.BytesIO()
    sf.write(output,data,rate,format='WAV',subtype='FLOAT')
    output.seek(0)
    return output


def finalize(performance,audio,alignment,directory):
    """Encode once from the decoder source, then shift the complete export clock."""
    from .high_frets import project
    playable=project(performance)[0] if performance.get('source',{}).get('format')=='songsterr' else performance
    starts=[]
    for track in playable['tracks']:
        starts += [map_time(alignment,n['t']) for n in track.get('notes',[])]
        starts += [map_time(alignment,n.get('t',c['t'])) for c in track.get('chords',[]) for n in c.get('notes',[])]
    if not starts or min(starts)<0:
        raise ImportFailure('alignment_failed','Preparation time requires a synchronized playable chart.')
    first=min(starts)
    source=Path(audio.get('encodingSourcePath') or audio['path'])
    ending=alignment.get('endingPadding')
    if ending:
        from .local_sync import _hash
        if _hash(source)!=ending['sourceSamplesSha256']:
            raise ImportFailure('needs_audio','The recording changed after its ending check. Select the recording again.')
    with sf.SoundFile(source) as reader:
        rate=reader.samplerate
        original_frames=reader.frames
        ending_frames=ending['frames'] if ending else 0
        if ending and (ending['sampleRate']!=rate or ending['originalFrames']!=original_frames
                       or abs(original_frames/rate-audio['duration'])>1e-8):
            raise ImportFailure('needs_audio','The recording changed after its ending check. Select the recording again.')
        frames=max(0,math.ceil((2-first)*rate-1e-8))
        offset=frames/rate
        target=Path(directory)/'prepared-full.ogg'
        with sf.SoundFile(target,'w',samplerate=rate,channels=reader.channels,format='OGG',subtype='VORBIS') as writer:
            for i in range(0,frames,32768):
                writer.write(np.zeros((min(32768,frames-i),reader.channels),dtype='float32'))
            for block in reader.blocks(blocksize=32768,dtype='float32',always_2d=True):
                writer.write(block)
            for i in range(0,ending_frames,32768):
                writer.write(np.zeros((min(32768,ending_frames-i),reader.channels),dtype='float32'))
    prepared=deepcopy(audio)
    prepared.update(path=str(target),encodedHash=sha256_file(target),duration=audio['duration']+offset+ending_frames/rate)
    prepared.pop('encodingSourcePath',None)
    result=deepcopy(alignment)
    receipt={'version':1,'policy':POLICY,'frames':frames,'sampleRate':rate,'seconds':offset,
             'originalDuration':audio['duration'],'originalFirstNote':first,'audioSha256':prepared['encodedHash']}
    if ending:
        receipt.update(originalFrames=original_frames,endingFrames=ending_frames)
    if result.get('mapping')=='piecewise-linear':
        for anchor in result['anchors']:anchor['audio']+=offset
        # Recompute effective tempos from the unshifted score coordinates;
        # previously clamped silent pre-roll tempo points must not move twice.
        from .synchronization import source_time_scale, map_source_time
        points=performance['scoreTimeline']['tempoPoints']
        from bisect import bisect_right
        times=[p['time'] for p in points]
        tempos=[]
        for t in sorted({a['score'] for a in result['anchors'][:-1]}|set(times)):
            bpm=points[bisect_right(times,t)-1]['bpm']/source_time_scale(result,t)
            row={'time':max(0,map_source_time(result,t,allow_negative=True)),'bpm':bpm}
            if tempos and tempos[-1]['time']==row['time']:tempos[-1]=row
            elif not tempos or not math.isclose(tempos[-1]['bpm'],bpm,rel_tol=1e-12):tempos.append(row)
        result['tempos']=tempos
    else:
        result['offset']+=offset
    for key in ('terminalSustains','terminalSlides'):
        if key in result:
            if result[key].get('version') == 2:
                result[key]['audioDuration'] += offset
            else:
                result[key]['audioDuration']=prepared['duration']
    result['preparation']=receipt
    # Check the actual encoded recording, not a pre-encoding approximation.
    if result.get('openingRepair'):
        from . import local_sync as ls, recording_sync as rs
        original=recording_view(target,receipt)
        calibration=ls.tuning(original)
        if hasattr(original,'seek'):original.seek(0)
        pitch,flux,_=rs.features(original,reference_hz=calibration['referenceHz'])
        proposal=ls.propose(ls.score_tracks(performance),[a['score'] for a in result['anchors']],
                            result['sourceTiming']['points'],pitch,flux)
        proposal.update(tuning=calibration,audioSha256=ls._hash(original))
        if proposal['status']!='supported' or proposal['replacementBoundaries']!=result['openingRepair']['replacementBoundaries']:
            raise ImportFailure('alignment_failed','The encoded recording did not confirm the opening timing repair.',{'openingRepair':proposal})
        result['openingRepair']=proposal
    if result.get('recordingEnd'):
        from .ending_cutoff import authorize
        result=authorize(performance,prepared,result)
    if ending:
        from .ending_padding import confirm_encoded
        confirm_encoded(performance,prepared,result)
    return prepared,result


def verify(wanted,alignment,recipe,archive,manifest,check):
    receipt=alignment.get('preparation')
    if receipt is None:
        if recipe.get('preparation'):
            check.fail('preparation','import','Unexpected preparation time.')
        return
    check.equal('preparation_receipt','manifest/song_import/preparation',receipt,recipe.get('preparation'))
    if receipt.get('version')!=1 or receipt.get('policy')!=POLICY:
        raise ValueError('Unknown preparation policy.')
    frames,rate=receipt.get('frames'),receipt.get('sampleRate')
    if type(frames)!=int or type(rate)!=int or not 8000<=rate<=384000 or not 0<=frames<=2*rate:
        raise ValueError('Invalid preparation sample count.')
    offset=frames/rate
    check.near('preparation_offset','preparation/seconds',offset,receipt.get('seconds'),1e-10)
    starts=[n['note']['t'] for p in wanted['parts'] for n in p['notes']]
    first=min(starts)-offset
    check.near('preparation_first','preparation/originalFirstNote',first,receipt.get('originalFirstNote'))
    desired=max(0,math.ceil((2-first)*rate-1e-8))
    # Exported source times have microsecond rounding, up to one audio frame.
    if abs(desired-frames)>1 or min(starts)<2-0.0000011:
        check.fail('preparation_length','preparation','The preparation is not the required minimum two seconds.')
    ending_frames=receipt.get('endingFrames',0)
    if type(ending_frames)!=int or not 0<=ending_frames<=2*rate or bool(ending_frames)!=bool(alignment.get('endingPadding')):
        raise ValueError('Undeclared or invalid ending silence.')
    check.near('preparation_duration','manifest/duration',receipt['originalDuration']+offset+ending_frames/rate,manifest['duration'])
    full=[s for s in manifest['stems'] if s['id']=='full']
    if len(full)!=1:raise ValueError('Preparation requires one complete recording.')
    data=archive.read(full[0]['file'])
    import hashlib
    check.equal('preparation_audio','preparation/audioSha256',hashlib.sha256(data).hexdigest(),receipt.get('audioSha256'))
    with sf.SoundFile(io.BytesIO(data)) as reader:
        check.equal('preparation_rate','audio/sampleRate',rate,reader.samplerate)
        check.near('preparation_audio_duration','audio/duration',reader.frames/rate,manifest['duration'],1/rate)
        # Vorbis may ring immediately around the transition into the recording.
        count=max(0,frames-math.ceil(.05*rate))
        prefix=reader.read(count,dtype='float32',always_2d=True)
        if len(prefix) and np.max(abs(prefix))>1e-5:
            check.fail('preparation_silence','audio','The declared preparation contains audible audio.')
        if ending_frames:
            # Allow codec ringing for at most 50 ms at the recording boundary.
            reader.seek(frames+receipt['originalFrames']+min(ending_frames,math.ceil(.05*rate)))
            suffix=reader.read(dtype='float32',always_2d=True)
            if len(suffix) and np.max(abs(suffix))>1e-5:
                check.fail('ending_padding_silence','audio','The declared ending padding contains audible audio.')

"""Bounded recording-clock diagnostics and opening repair, not tab correction.

Scores are engineering evidence, never probabilities or proof of every note.
The old recording-end authority is deliberately independent of this module.
"""
from copy import deepcopy
import math
from pathlib import Path

import numpy as np
import soundfile as sf

from . import recording_sync as rs

VERSION = 'local-recording-clock-v1'
REPAIR = 'bounded-opening-map-v1'


def tuning(path):
    """Estimate sub-semitone reference from audio only; never retune output."""
    residuals, weights = [], []
    if hasattr(path, 'seek'):
        path.seek(0)
    with sf.SoundFile(path) as reader:
        rate = reader.samplerate
        n = 8192
        source_frames=math.ceil(n*rate/rs.RATE)
        # Bounded excerpts spread through the recording, independent of score.
        for start in np.linspace(0, max(0, len(reader) - source_frames), 96).astype(int):
            reader.seek(int(start))
            raw = reader.read(source_frames, dtype='float32', always_2d=True).mean(axis=1)
            if len(raw) != source_frames:
                continue
            raw=np.interp(np.arange(n)*rate/rs.RATE,np.arange(len(raw)),raw)
            mag = abs(np.fft.rfft(raw * np.hanning(n)))
            ids = np.flatnonzero((mag[1:-1] > mag[:-2]) & (mag[1:-1] >= mag[2:])
                                & (mag[1:-1] > max(float(mag.max()) * .08, 1e-5))) + 1
            logs = np.log(np.maximum(mag, 1e-12))
            denominator = logs[ids-1] - 2*logs[ids] + logs[ids+1]
            shift = .5 * (logs[ids-1] - logs[ids+1]) / np.minimum(denominator, -1e-12)
            hz = (ids + np.clip(shift, -.5, .5)) * rs.RATE / n
            keep = (hz >= 65) & (hz <= 1800)
            weight = mag[ids[keep]]
            if not len(weight):
                continue
            weight /= weight.sum()
            residuals.extend((1200*np.log2(hz[keep]/440)+50) % 100-50)
            weights.extend(weight)
    if not weights:
        return {'referenceHz': 440.0, 'cents': 0.0, 'concentration': 0.0, 'status': 'inconclusive'}
    vector = np.average(np.exp(2j*np.pi*np.asarray(residuals)/100), weights=weights)
    cents = float(np.angle(vector)*100/(2*np.pi))
    supported = abs(vector) >= .35
    # Even an uncertain estimate is a bounded analysis hypothesis. Falling
    # back to 440 would assert a tuning the recording did not establish.
    # Repair still requires the independent pitch/attack/held-out gates.
    return {'referenceHz': round(440*2**(cents/1200), 6),
            'cents': round(cents, 3), 'concentration': round(float(abs(vector)), 4),
            'status': 'supported' if supported else 'inconclusive'}


def score_tracks(performance):
    tracks = []
    for part in performance['tracks']:
        notes = list(part.get('notes', [])) + [{**n, 't': n.get('t', c['t'])}
                    for c in part.get('chords', []) for n in c.get('notes', [])]
        tracks.append({'id': part['id'], 'instrument': part['instrument'], 'events': [
            {'t': n['t'], 'end': n['t'] + n.get('sus', 0),
             'midi': part['tuning'][n['s']] + part.get('capo', 0) + n['f'] if n['f'] != 127 else None,
             'effects': {k:v for k,v in n.items() if k not in {'s','f','t','sus','source_ids'}}} for n in notes]})
    return tracks


def groups(track, left, right):
    by_time = {}
    for event in track['events']:
        if left <= event['t'] < right:
            by_time.setdefault(round(event['t'], 6), []).append(event)
    output = []
    for time, events in sorted(by_time.items()):
        # A dead string inside a pitched chord has no expected fixed pitch.
        # Keep its time in the group, but assess only the pitched strings.
        pitched=[e for e in events if not e['effects'].get('mt') and e['midi'] is not None]
        if not pitched or not rs._paired(pitched):
            continue
        vector = np.zeros(len(rs.MIDIS))
        vector[[int(e['midi'])-28 for e in pitched]] = 1
        vector /= np.linalg.norm(vector)
        output.append((time, vector))
    return output


def joint(pitch, flux, times, vectors, channel):
    """Sample pitch shortly after the SAME onset, never at an independent lag."""
    times = np.atleast_2d(times)
    pi = np.rint((times + .085) / rs.DT).astype(int)
    ai = np.rint(times / rs.DT).astype(int)
    valid = (pi >= 0) & (pi < len(pitch)) & (ai >= 0) & (ai < len(flux))
    ps = (pitch[np.clip(pi, 0, len(pitch)-1)] * vectors[None, :, :]).sum(axis=2)
    ac = flux[np.clip(ai, 0, len(flux)-1), channel]
    return ps * valid, ac * valid


def assess_features(tracks, pitch, flux, duration):
    rows = []
    # Non-overlapping windows provide coverage without inflating evidence counts.
    for left in np.arange(0, duration, 16):
        right = min(float(left+16), duration)
        parts = []
        for track in tracks:
            selected = groups(track, float(left), right-.2)
            if len(selected) < 6:
                continue
            selected = selected[::max(1, math.ceil(len(selected)/96))]
            times = np.array([g[0] for g in selected])
            vectors = np.array([g[1] for g in selected])
            lags = np.arange(-3, 3.001, .02)
            ps, ac = joint(pitch, flux, times[None,:]+lags[:,None], vectors,
                           0 if track['instrument']=='bass' else 1)
            scores = (ps*ac).mean(axis=1)
            at = int(scores.argmax())
            lag = float(lags[at])
            near = abs(lags) <= .08001
            strength = float(scores[at])
            contrast = strength / max(float(np.median(scores)), 1e-8)
            # An inconclusive acoustic observation does not prove source error.
            confident = float(ps[at].mean()) >= .12 and strength >= .15 and contrast >= 1.8
            supported = confident and scores[near].max() >= .90*strength
            separated = scores[near].max() < .65*strength and abs(lag) >= .16
            parts.append({'trackId': track['id'], 'groups': len(selected),
                          'status': 'supported' if supported else 'suspected_mismatch' if confident and separated else 'inconclusive',
                          'bestOffset': round(lag, 3), 'jointContrast': round(contrast, 4),
                          'jointAtMap': round(float(scores[near].max()), 6), 'jointBest': round(strength, 6)})
        active = any(any(left <= n['t'] < right for n in t['events']) for t in tracks)
        if active:
            status = 'supported' if any(p['status']=='supported' for p in parts) else 'inconclusive'
            if parts and all(p['status']=='suspected_mismatch' for p in parts):
                offsets = [p['bestOffset'] for p in parts]
                if max(offsets)-min(offsets) <= .10:
                    status = 'suspected_mismatch'
            rows.append({'start': round(float(left),6), 'end':round(right,6), 'status':status, 'parts':parts})
    return {'version': VERSION, 'status':'supported' if len(rows)>=3 and all(r['status']=='supported' for r in rows) else 'inconclusive',
            'everyNoteVerified': False, 'windows': rows, 'windowCount':len(rows),
            'supportedWindows':sum(r['status']=='supported' for r in rows),
            'suspectedMismatchWindows':sum(r['status']=='suspected_mismatch' for r in rows)}


def assess(tracks, path, duration, map_hash):
    calibration = tuning(path)
    if hasattr(path, 'seek'):
        path.seek(0)
    pitch, flux, signal = rs.features(path, reference_hz=calibration['referenceHz'])
    if abs(len(signal)/rs.RATE-duration) > .001:
        raise ValueError('The recording duration does not match its timing evidence.')
    return {**assess_features(tracks, pitch, flux, duration), 'tuning':calibration,
            'audioSha256': _hash(path), 'mapHash': map_hash, 'audioDuration':duration}


def _hash(path):
    import hashlib
    # WAV floating-point containers can include a timestamped PEAK chunk.
    # Bind acoustic evidence to decoded samples, not that incidental header.
    if hasattr(path,'seek'):path.seek(0)
    result=hashlib.sha256(b'decoded-float32-v1\0')
    with sf.SoundFile(path) as reader:
        result.update(f'{reader.samplerate}:{reader.channels}:{reader.frames}\0'.encode('ascii'))
        for block in reader.blocks(blocksize=32768,dtype='float32',always_2d=True):
            result.update(block.astype('<f4',copy=False).tobytes())
    if hasattr(path,'seek'):path.seek(0)
    return result.hexdigest()


def propose(tracks, score_boundaries, audio_boundaries, pitch, flux):
    """Fit a faulty opening only, using later attacks exclusively as validation."""
    no = lambda reason: {'version':REPAIR, 'status':'inconclusive', 'reason':reason}
    if len(score_boundaries)<4 or len(audio_boundaries)<4:
        return no('insufficient_boundaries')
    s0,s1,s2 = score_boundaries[:3]
    a0,a1,a2 = audio_boundaries[:3]
    if not (s0==0 and 0<s1<s2 and -.75<=a0<0<a1<a2<=8):
        return no('outside_opening_repair_bounds')
    eligible=[]
    for track in tracks:
        first, held = groups(track,s0,s1), groups(track,s1,s2)
        # Do not fit an incomplete subset of a mixed/unsupported opening.
        total={round(n['t'],6) for n in track['events'] if s0<=n['t']<s2}
        if len(first)>=3 and len(held)>=2 and len(first)+len(held)==len(total):
            eligible.append((track,first,held))
    if not eligible:
        return no('insufficient_pitched_opening_and_context')
    # Source-only choice prevents searching arrangements until one passes.
    track,first,held = sorted(eligible,key=lambda x:(-len(x[1]),str(x[0]['id'])))[0]
    if len(first)>32 or len(held)>64:
        return no('opening_too_dense')
    fractions=np.array([(g[0]-s0)/(s1-s0) for g in first])
    vectors=np.array([g[1] for g in first])
    channel=0 if track['instrument']=='bass' else 1
    max_start=min(.75,a1*.65)
    changes=min(.30,(a2-a1)*.15)
    grid=np.array([(a,b) for a in np.arange(0,max_start+.0001,.01)
                   for b in np.arange(max(.1,a1-changes),a1+changes+.0001,.01)
                   if b>a and .6 <= (b-a)/(a1-a0) <= 1.4])
    if not len(grid):
        return no('empty_candidate_grid')
    times=grid[:,0,None]+(grid[:,1]-grid[:,0])[:,None]*fractions
    ps,ac=joint(pitch,flux,times,vectors,channel)
    scores=np.exp(np.log(np.maximum(ps*ac,1e-12)).mean(axis=1))
    best=int(scores.argmax()); chosen=times[best]
    separate=np.max(abs(times-chosen),axis=1)>.10
    alternative=float(scores[separate].max()) if separate.any() else 0
    margin=float(scores[best]/max(alternative,1e-12))
    b0,b1=grid[best]
    checks=np.array([b1+(a2-b1)*(g[0]-s1)/(s2-s1) for g in held])
    hp,ha=joint(pitch,flux,checks,np.array([g[1] for g in held]),channel)
    # All fitted attacks and held-out attacks require independent pitch AND
    # transient support. Broad/competing peaks must not produce a guessed repair.
    peak_errors=[]
    for t in [*chosen,*checks]:
        lo=max(0,int(round((t-.10)/rs.DT)));hi=min(len(flux),int(round((t+.10)/rs.DT))+1)
        at=lo+int(flux[lo:hi,channel].argmax())
        peak_errors.append(abs(at*rs.DT-t))
    supported=(margin>=1.15 and min(ps[best])>=.12 and min(ac[best])>=1.3
               and min(hp[0])>=.12 and min(ha[0])>=1.3 and max(peak_errors)<=.055)
    return {'version':REPAIR, 'status':'supported' if supported else 'inconclusive',
            'reason':'matched_opening_and_held_out_context' if supported else 'ambiguous_or_unsupported_opening',
            'trackId':track['id'], 'originalBoundaries':[a0,a1],
            'replacementBoundaries':[round(float(b0),6),round(float(b1),6)], 'lockedBoundary':a2,
            'fitTimes':[round(float(t),6) for t in chosen], 'heldOutTimes':[round(float(t),6) for t in checks],
            'alternativeRatio':round(margin,6), 'fitPitch':[round(float(v),6) for v in ps[best]],
            'heldOutPitch':[round(float(v),6) for v in hp[0]], 'maxOnsetError':round(max(peak_errors),6)}


def repair(performance, audio, synchronization, metadata):
    from .synchronization import align_from_songsterr
    from .audio import ImportFailure
    original=deepcopy(synchronization)
    points=original.get('points',[]) if isinstance(original,dict) else []
    if len(points)<4 or not -.75<=points[0]<0:
        raise ImportFailure('source_sync_unavailable','The opening cannot be repaired within safe bounds.')
    # Re-run every structural/identity/end check with a positive candidate.
    trial=deepcopy(original); trial['points'][0]=0
    checked=align_from_songsterr(performance,audio,trial,metadata,allow_ending_candidate=True,_opening_probe=True)
    boundaries=[p['score'] for p in checked['anchors']]
    calibration=tuning(audio['path'])
    pitch,flux,_=rs.features(audio['path'],reference_hz=calibration['referenceHz'])
    proposal=propose(score_tracks(performance),boundaries,points,pitch,flux)
    proposal.update(tuning=calibration,audioSha256=_hash(audio['path']))
    if proposal['status']!='supported':
        raise ImportFailure('source_sync_unavailable','The opening timing could not be corrected confidently.',{'openingRepair':proposal})
    applied=deepcopy(original); applied['points'][:2]=proposal['replacementBoundaries']
    try:
        result=align_from_songsterr(performance,audio,applied,metadata,allow_ending_candidate=True)
    except ImportFailure as exc:
        exc.diagnostics['openingRepair']=proposal
        raise
    canonical={**result['sourceTiming'],'points':[float(p) for p in points]}
    result['sourceTiming']=canonical
    result['provenance']['mapHash']=rs.digest(canonical)
    result['openingRepair']=proposal
    return result


def independent_tracks(source):
    from .verify_timeline import expected, Clock, visits
    wanted=expected(source,{'offset':0,'scale':1})
    tracks=[]
    for part in wanted['parts']:
        src=part['source']
        events=[]
        for item in part.get('sync_notes',part['notes']):
            n=item['note']
            events.append({'t':n['t'],'end':n['t']+n.get('sus',0),
                'midi':src.tuning[n['s']]+src.capo+n['f'] if n['f']!=127 else None,
                'effects':{k:v for k,v in n.items() if k not in {'t','sus','s','f'}}})
        tracks.append({'id':src.id,'instrument':src.instrument,'events':events})
    clock=Clock(source,visits(source))
    return tracks,[float(clock.at(q)) for q in [*clock.measure_starts,clock.quarters]]


def verify_repair(source, alignment, stored, path, check):
    """Re-evaluate acoustic gates from independent raw-source note reconstruction."""
    check.equal('opening_repair_receipt','import/opening-repair',alignment.get('openingRepair'),stored)
    if not isinstance(stored,dict) or stored.get('version')!=REPAIR or stored.get('status')!='supported':
        check.fail('opening_repair','import','A repair requires current supported acoustic evidence.');return
    tracks,boundaries=independent_tracks(source)
    calibration=tuning(path)
    if hasattr(path,'seek'):path.seek(0)
    pitch,flux,_=rs.features(path,reference_hz=calibration['referenceHz'])
    fresh=propose(tracks,boundaries,alignment['sourceTiming']['points'],pitch,flux)
    fresh.update(tuning=calibration,audioSha256=_hash(path))
    check.equal('opening_repair_audio','import/opening-repair',fresh,stored)
    check.equal('opening_repair_supported','import/opening-repair/status','supported',fresh['status'])

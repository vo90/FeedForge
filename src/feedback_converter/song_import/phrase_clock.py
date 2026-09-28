"""Bounded, source-selected phrase evidence for an uncertain recording ending.

This measures an existing clock. It never fits or publishes a replacement map.
Individual ambiguous attacks may support a sequence together, but a good final
chord cannot conceal a contradictory earlier half of that sequence.
"""
import numpy as np

from . import recording_sync as rs

VERSION = 'ending-phrases-v1'
MAX_ENDING = 32.0
CONTEXT = 8.0
SPAN = 16.0
LAGS = np.arange(-1.5, 1.5001, .01)
NEAR = abs(LAGS) <= .08001
DISTANT = abs(LAGS) >= .20


def groups(track, left, right):
    """Select by source alone; tremolo pitch is context, not one picked attack."""
    by_time = {}
    for n in track['events']:
        if left <= n['t'] < right:
            by_time.setdefault(round(n['t'], 6), []).append(n)
    output = []
    excluded = set(rs.SPARSE_EXCLUSIONS) - {'tr'}
    for time, notes in sorted(by_time.items()):
        pitched = [n for n in notes if n['midi'] is not None and 28 <= n['midi'] <= 96
                   and not n['effects'].get('mt')]
        if not pitched or any(any(n['effects'].get(k) for k in excluded) for n in pitched):
            continue
        vector = np.zeros(len(rs.MIDIS))
        vector[[int(n['midi'])-28 for n in pitched]] = 1
        vector /= np.linalg.norm(vector)
        length = max(0, min(n['end']-n['t'] for n in pitched))
        output.append({'time':time, 'vector':vector, 'length':length,
                       'attack':not any(n['effects'].get('tr') for n in pitched)})
    return output


def _curves(selected, pitch, flux, channel):
    times = np.array([g['time'] for g in selected])
    vectors = np.array([g['vector'] for g in selected])
    # Short notes are sampled inside their authored duration, rather than always
    # 85 ms after attack (which can already be the next note in a fast run).
    delays = np.array([min(.085, g['length']*.35) if g['length']>0 else .025 for g in selected])
    pi = np.rint((times[None,:]+LAGS[:,None]+delays[None,:])/rs.DT).astype(int)
    ai = np.rint((times[None,:]+LAGS[:,None])/rs.DT).astype(int)
    valid = (pi>=0)&(pi<len(pitch))&(ai>=0)&(ai<len(flux))
    ps = (pitch[np.clip(pi,0,len(pitch)-1)]*vectors[None,:,:]).sum(axis=2)*valid
    channels=np.array([g.get('channel',channel) for g in selected])
    ac = flux[np.clip(ai,0,len(flux)-1),channels[None,:]]*valid
    attack = np.array([g['attack'] for g in selected])
    joint = ps[:,attack]*ac[:,attack]
    # Equal bounded cue weights prevent one loud final chord or a duplicated
    # arrangement from dominating the decision. Sustains cannot authorize alone.
    normalized = joint/np.maximum(joint.max(axis=0,keepdims=True),1e-8)
    # A dense run contributes by temporal coverage, not by offering dozens of
    # attacks while a later held passage offers only a handful.
    at_times=times[attack]
    bins=np.floor((at_times-at_times[0])/2).astype(int)
    weights=1/np.bincount(bins)[bins]
    sequence = np.exp(np.average(np.log(np.maximum(normalized,.02)),axis=1,weights=weights))
    context=[]
    for g in selected:
        if g['length'] < .4:
            continue
        offsets=np.linspace(.15,min(g['length']-.1,1.0),4)
        ids=np.rint((g['time']+LAGS[:,None]+offsets[None,:])/rs.DT).astype(int)
        good=(ids>=0)&(ids<len(pitch))
        context.append(((pitch[np.clip(ids,0,len(pitch)-1)]*g['vector']).sum(axis=2)*good).mean(axis=1))
    if context:
        held=np.array(context).mean(axis=0)
        # Supporting pitch context has limited weight and no onset authority.
        sequence *= np.maximum(held/max(float(held.max()),1e-8),.02)**.25
    return sequence, joint, ps[:,attack], attack, weights


def _decision(curve):
    best=int(curve.argmax()); near_ids=np.flatnonzero(NEAR)
    aligned=int(near_ids[curve[NEAR].argmax()])
    strength=float(curve[best]); at=float(curve[aligned])
    contrast=strength/max(float(np.median(curve)),1e-8)
    distant=float(curve[DISTANT].max())
    return {'offset':round(float(LAGS[aligned]),3), 'bestOffset':round(float(LAGS[best]),3),
            'atMap':round(at,6), 'best':round(strength,6), 'distantBest':round(distant,6),
            'contrast':round(contrast,4)}, aligned, (
                contrast>=1.8 and at>=.9*strength and distant<.9*at)


def measure(selected, pitch, flux, instrument):
    curve,joint,ps,attack,weights=_curves(selected,pitch,flux,0 if instrument=='bass' else 1)
    evidence,aligned,distinct=_decision(curve)
    at_times=np.array([g['time'] for g in selected])[attack]
    normalized=joint/np.maximum(joint.max(axis=0,keepdims=True),1e-8)
    at_map=float(np.exp(np.average(np.log(np.maximum(normalized[aligned],.02)),weights=weights)))
    fraction=(at_times-at_times[0])/(at_times[-1]-at_times[0])
    drift={'atMap':round(at_map,6),'best':0.0,'startOffset':0.0,'endOffset':0.0}
    # Compare bounded linear drift hypotheses diagnostically. They cannot repair
    # or replace source timing, and a competitive drifting clock stays uncertain.
    for first in (-.8,-.4,0,.4,.8):
        for last in (-.8,-.4,0,.4,.8):
            if first==last:
                continue
            offsets=first+(last-first)*fraction
            values=[np.interp(offset,LAGS,normalized[:,i]) for i,offset in enumerate(offsets)]
            score=float(np.exp(np.average(np.log(np.maximum(values,.02)),weights=weights)))
            if score>drift['best']:
                drift.update(best=score,startOffset=first,endOffset=last)
    drift['best']=round(drift['best'],6)
    drift['distinct']=bool(drift['best']<.9*at_map)
    # Assess chronological halves separately. Only the full phrase must have a
    # unique peak; each half must agree with it and contain audible pitch/attacks.
    halves=[]
    split=int(np.searchsorted(at_times,(at_times[0]+at_times[-1])/2))
    # Overlap the boundary cues when necessary, retaining at least two attacks
    # per half. Split by elapsed time so a rapid run cannot absorb both halves.
    for ids in (np.arange(min(len(at_times),max(2,split+1))),
                np.arange(max(0,min(len(at_times)-2,split-1)),len(at_times))):
        normal=joint[:,ids]/np.maximum(joint[:,ids].max(axis=0,keepdims=True),1e-8)
        part=np.exp(np.average(np.log(np.maximum(normal,.02)),axis=1,weights=weights[ids]))
        peak=float(part.max()); at=float(part[aligned])
        audible=np.average((joint[aligned,ids]>=.15)&(ps[aligned,ids]>=.12),weights=weights[ids])>=.6
        agrees=at>=.9*peak
        halves.append({'start':float(at_times[ids[0]]),'end':float(at_times[ids[-1]]),
                       'atMap':round(at,6),'best':round(peak,6),
                       'bestOffset':round(float(LAGS[part.argmax()]),3),
                       'audible':bool(audible),'agrees':bool(agrees)})
    return {'status':'supported' if distinct and drift['distinct'] and all(h['audible'] and h['agrees'] for h in halves) else 'inconclusive',
            **evidence, 'halves':halves, 'driftAlternatives':drift, 'attackCount':int(attack.sum()),
            'contextCount':sum(g['length']>=.4 for g in selected)}


def coverage(phrases, tracks, left, duration):
    """Cover actual event positions, not a deadline before any eligible cue.

    An overlapping earlier window is not evidence for its entire span. Only
    measured phrase intervals contribute; genuine gaps remain explicit.
    """
    good=[p for p in phrases if p['status']=='supported']
    intervals=[]
    for p in sorted(good,key=lambda p:(p['start'],p['end'])):
        if intervals and p['start']<=intervals[-1][1]+1e-6:
            intervals[-1][1]=max(intervals[-1][1],p['end'])
        else:
            intervals.append([p['start'],p['end']])
    attacks=sorted({round(n['t'],6) for t in tracks for n in t['events'] if left<=n['t']<duration-2})
    uncovered=[t for t in attacks if not any(a-1e-6<=t<=b+1e-6 for a,b in intervals)]
    offsets=[p['offset'] for p in good]
    supported=bool(good and attacks and not uncovered and max(b for _,b in intervals)>=duration-2
                   and max(offsets)-min(offsets)<=.08001)
    return {'status':'supported' if supported else 'inconclusive','intervals':intervals,
            'uncoveredAttackTimes':uncovered,'offsetSpread':round(max(offsets)-min(offsets),6) if offsets else None}


def assess(tracks, pitch, flux, duration, left):
    report={'version':VERSION,'status':'inconclusive','start':left,'end':duration,
            'scope':'shared_recording_timing','everyNoteVerified':False,'phrases':[]}
    if not 0<duration-left<=MAX_ENDING:
        return {**report,'reason':'outside_bounded_ending'}
    sources=[]; ensemble={}
    for track in tracks:
        selected=groups(track,max(0,left-CONTEXT),duration-.12)
        sources.append((track['id'],track['instrument'],selected))
        for g in selected:
            ensemble.setdefault(g['time'],[]).append({**g,'channel':0 if track['instrument']=='bass' else 1})
    combined=[]
    for time,parts in sorted(ensemble.items()):
        # A duplicated arrangement supplies no extra weight. Simultaneous notes
        # form one cue, and later bass/guitar changes can form one shared phrase.
        vector=np.maximum.reduce([p['vector']>0 for p in parts]).astype(float)
        vector/=np.linalg.norm(vector)
        channels={p['channel'] for p in parts if p['attack']}
        combined.append({'time':time,'vector':vector,'length':min(p['length'] for p in parts),
                         'attack':any(p['attack'] for p in parts),
                         'channel':next(iter(channels)) if len(channels)==1 else 3})
    if len(tracks)>1:
        sources.append(('ensemble','guitar',combined))
    for track_id,instrument,selected in sources:
        for start in np.arange(max(0,left-CONTEXT),duration,CONTEXT):
            part=[g for g in selected if start<=g['time']<min(start+SPAN,duration-.12)]
            if len(part)<3 or part[-1]['time']-part[0]['time']<4:
                continue
            # Fixed sampling from source positions; never select successful cues.
            if len(part)>96:
                part=[part[i] for i in np.linspace(0,len(part)-1,96).astype(int)]
            attack_times=[g['time'] for g in part if g['attack']]
            if len(attack_times)<3 or attack_times[-1]-attack_times[0]<4:
                continue
            measured=measure(part,pitch,flux,instrument)
            report['phrases'].append({'trackId':track_id,'start':attack_times[0],
                'end':attack_times[-1],'times':attack_times,**measured})
    report['coverage']=coverage(report['phrases'],tracks,left,duration)
    report['status']=report['coverage']['status']
    report['reason']='connected_phrase_evidence' if report['status']=='supported' else 'insufficient_phrase_evidence'
    return report

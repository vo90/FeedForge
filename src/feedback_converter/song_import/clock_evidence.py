"""Recording-clock support, distinct from correctness of each transcribed note.

No source timing or pitch is changed. Uncertain tuning estimates must earn
independent passage support before authorizing any ending adjustment.
"""
import math
import numpy as np

from . import local_sync as ls, recording_sync as rs

MAX_ENDING = 32.0


def _joint(tracks, pitch, flux, duration, legacy):
    return ls.assess_features(tracks, pitch, flux, duration,
        windows=[(w['start'], w['end']) for w in legacy['windows']])


def _reference_support(joint, duration):
    # Selection and holdout partitions depend only on source clock positions.
    # Exclude the ending whose adjustment is being authorized.
    body = []
    for window in joint['windows']:
        if window['end'] <= duration - MAX_ENDING and (not body or window['start'] >= body[-1]['end']):
            body.append(window)
    partitions = [body[::2], body[1::2]]
    counts = [{'windows':len(rows), 'supported':sum(w['status']=='supported' for w in rows)} for rows in partitions]
    supported = all(c['windows'] >= 3 and c['supported']/c['windows'] >= .8 for c in counts)
    return {'status':'supported' if supported else 'inconclusive', 'partitions':counts,
            'scope':'body-selection-and-held-out-passages'}


def sparse_ending(tracks, pitch, flux, duration, left):
    """Use source-selected sparse parts; retain every unassessed group explicitly.

    Dense solos cannot manufacture a match by offering hundreds of candidates.
    Source eligibility is selected before examining acoustic results. Multiple
    independent attack times, coverage, and consistent offsets are required.
    """
    base = {'status':'inconclusive', 'start':left, 'end':duration, 'groups':[],
            'reason':'insufficient_distinct_ending_evidence'}
    if not 0 < duration-left <= MAX_ENDING:
        return base
    candidates = {}
    for track in tracks:
        groups = ls.groups(track, left, duration-.12)
        # A sparse part has at most one assessable group per two seconds. Require
        # spacing so a rapid run is not accidentally treated as sparse anchors.
        if len(groups) > max(4, math.ceil((duration-left)/2)):
            continue
        for at,(time,vector) in enumerate(groups):
            if ((at and time-groups[at-1][0] < .35)
                    or (at+1<len(groups) and groups[at+1][0]-time < .35)):
                continue
            lags=np.arange(-1.5,1.5001,.01)
            ps,ac=ls.joint(pitch,flux,np.array([[time]])+lags[:,None],vector[None,:],
                           0 if track['instrument']=='bass' else 1)
            scores=(ps*ac).mean(axis=1); near=abs(lags)<=.08001
            best=int(scores.argmax()); near_ids=np.flatnonzero(near)
            aligned=int(near_ids[scores[near].argmax()])
            strength=float(scores[best]); contrast=strength/max(float(np.median(scores)),1e-8)
            distant=float(scores[abs(lags)>=.20].max())
            supported=(float(ps[aligned].mean())>=.12 and strength>=.15 and contrast>=1.8
                       and scores[aligned]>=.90*strength and distant < .90*scores[aligned])
            candidates.setdefault(round(time,6),[]).append({
                'trackId':track['id'], 'status':'supported' if supported else 'unassessed',
                'offset':round(float(lags[aligned]),3), 'bestOffset':round(float(lags[best]),3),
                'jointAtMap':round(float(scores[aligned]),6), 'jointBest':round(strength,6),
                'distantBest':round(distant,6),
                'contrast':round(contrast,4)})
    rows=[]
    for time,parts in sorted(candidates.items()):
        supported=[p for p in parts if p['status']=='supported']
        chosen=max(supported,key=lambda p:p['jointAtMap']) if supported else None
        rows.append({'time':time,'status':'supported' if chosen else 'unassessed',
                     'offset':chosen['offset'] if chosen else None,'parts':parts})
    good=[r for r in rows if r['status']=='supported']
    coverage=(len(good)>=3 and len(good)>=.6*len(rows)
              and good[0]['time']<=left+4 and good[-1]['time']>=duration-2
              and good[-1]['time']-good[0]['time']>=min(8,(duration-left)*.6)
              and max(r['offset'] for r in good)-min(r['offset'] for r in good)<=.08001)
    return {**base,'groups':rows,'supportedGroups':len(good),'unassessedGroups':len(rows)-len(good),
            'status':'supported' if coverage else 'inconclusive',
            'reason':'multiple_ending_clock_anchors' if coverage else base['reason'],
            'everyNoteVerified':False}


def assess(tracks, path, duration, pitch, flux, original):
    baseline=_joint(tracks,pitch,flux,duration,original)
    calibration=ls.tuning(path)
    reference={'status':'default','referenceHz':440.0,'hypothesis':calibration}
    joint=baseline
    # The estimate is bounded to less than half a semitone. A weak estimate is
    # never trusted alone; selection AND held-out body passages must support it.
    if math.isfinite(calibration['referenceHz']) and abs(calibration['cents'])<=50 and abs(calibration['cents'])>=5:
        if hasattr(path,'seek'):path.seek(0)
        trial_pitch,trial_flux,_=rs.features(path,reference_hz=calibration['referenceHz'])
        trial=_joint(tracks,trial_pitch,trial_flux,duration,original)
        support=_reference_support(trial,duration)
        old_support=_reference_support(baseline,duration)
        improved=sum(p['supported'] for p in support['partitions'])>sum(p['supported'] for p in old_support['partitions'])
        reference.update(corroboration=support)
        if support['status']=='supported' and improved:
            pitch,flux,joint=trial_pitch,trial_flux,trial
            reference.update(status='corroborated',referenceHz=calibration['referenceHz'])
    # Retain established, conservative evidence at the selected reference, and
    # supplement it with joint evidence at the SAME clock and window positions.
    measured = original if reference['status']=='default' else rs.assess_features(tracks,pitch,flux,duration)
    windows=[]
    for old,new in zip(measured['windows'],joint['windows']):
        status = new['status'] if new['status']=='suspected_mismatch' else (
            'supported' if old['status']=='supported' or new['status']=='supported' else new['status'])
        windows.append({**old,'status':status,'jointEvidence':new,
                        'method':'established-pitch-onsets' if old['status']=='supported' else 'joint-pitch-attacks'})
    failed=[i for i,w in enumerate(windows) if w['status']!='supported']
    ending=None
    if (failed and failed[0]>=3 and failed==list(range(failed[0],len(windows)))
            and not any(w['status']=='suspected_mismatch' for w in windows)):
        ending=sparse_ending(tracks,pitch,flux,duration,windows[failed[0]]['start'])
        if ending['status']=='supported':
            for i in failed:
                windows[i].update(status='supported',method='sparse-ending-clock',individualNotesUnassessed=True)
    mismatches=sum(w['status']=='suspected_mismatch' for w in windows)
    status='supported' if len(windows)>=3 and all(w['status']=='supported' for w in windows) else 'suspected_mismatch' if mismatches else 'inconclusive'
    return {**measured,'status':status,'windows':windows,'method':'joint-recording-clock',
            'supportedWindows':sum(w['status']=='supported' for w in windows),
            'suspectedMismatchWindows':mismatches,
            'referenceEvidence':reference,'endingEvidence':ending,
            'everyNoteVerified':False,'scope':'shared_recording_timing'}

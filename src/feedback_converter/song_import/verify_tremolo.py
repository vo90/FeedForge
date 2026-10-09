"""Independent expectation from source atoms and the verification clocks."""
from bisect import bisect_right
from collections import defaultdict
from fractions import Fraction
from .verify_timeline import Clock, RecordingMap, expected


def attach(source, result, alignment, duration, *, raw=None):
    if source.format != 'songsterr': return
    if not any(item['note'].get('tr') for part in result['parts'] for item in part['notes']):return
    clock = Clock(source,result['order'])
    mapping = RecordingMap(alignment)
    if raw is None:raw = expected(source, {'scale':1,'offset':0})
    candidates = defaultdict(list)
    for part in source.parts:
        for oi,bar in enumerate(result['order']):
            for beat in part.beats[bar]:
                for atom in beat['notes']:
                    value = atom.tremolo_rate
                    if value is None: continue
                    q = clock.measure_starts[oi]+atom.q+atom.attack_offset
                    candidates[atom.location].append((float(clock.at(q)),float(q),
                        Fraction(*value) if isinstance(value,list) else None))
    def q_at(t):
        i = max(0,bisect_right(clock.seconds,t)-1)
        return float(clock.positions[i])+(t-float(clock.seconds[i]))*float(clock.bpms[i])/60
    for pi,part in enumerate(result['parts']):
        originals = {(tuple(r['locations']),r['occurrence']):r for r in raw['parts'][pi]['notes']}
        for item in part['notes']:
            note=item['note']
            if not note.get('tr'):continue
            base=originals[(tuple(item['locations']),item['occurrence'])]['note']
            start,end=base['t'],base['t']+base['sus']
            owners=[]
            for loc in item['locations']:
                owners.extend(r for r in candidates.get(loc,[]) if start-1.1e-6<=r[0]<end-1.1e-6)
            boundaries={start,end,*[start if abs(start-o[0])<=1.1e-6 else o[0] for o in owners]}
            boundaries.update(float(t) for t in clock.seconds if start+1.1e-6<float(t)<end-1.1e-6)
            if alignment.get('mapping')=='piecewise-linear':
                boundaries.update(a['score'] for a in alignment['anchors'] if start<a['score']<end)
            boundaries=sorted(boundaries)
            marks=[]
            for left,right in zip(boundaries,boundaries[1:]):
                active=[o for o in owners if o[0]<=left+1.1e-6]
                if not active:continue
                rates={o[2] for o in active}
                mode='unspecified' if rates=={None} else 'conflicting'
                rate=next(iter(rates)) if len(rates)==1 else None
                phase=0
                if rate is not None:
                    phases=[0 if abs(left-o[0])<=1.1e-6 else ((q_at(left)-o[1])/(4*float(rate)))%1 for o in active]
                    if all(min(abs(p-phases[0]),1-abs(p-phases[0]))<=1e-5 for p in phases):
                        mode,phase='specified',round(phases[0],9)%1
                    else:rate=None
                a,b=float(mapping.at(left)),float(mapping.at(right))
                lo=round(max(0,a-note['t']),6)
                hi=round(min(note['sus'],duration-note['t'],b-note['t']),6)
                if lo>=hi:continue
                tempo=float(clock.bpms[max(0,bisect_right(clock.seconds,left+1e-8)-1)])
                period=4*float(rate)*60/tempo*(b-a)/(right-left) if rate else None
                marks.append({'start':lo,'end':hi,'rate':[rate.numerator,rate.denominator] if rate else None,
                    'period':period,'phase':phase if rate else 0,'mode':mode})
            note['tremolo_marks']=marks


def compare(wanted, actual, sustain, check, location):
    if wanted is None and actual is None:return
    from ..tremolo import validate
    try:validate(actual,sustain)
    except (ValueError,TypeError):
        check.fail('tremolo_marks',location,'Invalid or missing tremolo detail.')
        return
    if wanted is None or len(wanted)!=len(actual):
        check.fail('tremolo_marks',location,'Tremolo interval count differs.')
        return
    for i,(a,b) in enumerate(zip(wanted,actual)):
        for k in a:
            if k in ('start','end','period','phase') and a[k] is not None and b[k] is not None:
                check.near('tremolo_marks',f'{location}/{i}/{k}',a[k],b[k],1.1e-6)
            else:check.equal('tremolo_marks',f'{location}/{i}/{k}',a[k],b[k])

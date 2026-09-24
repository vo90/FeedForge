"""Independent source facts; never call the production normalization helper."""
from .verify_source import fraction, unsupported


def read_bar(beat, location):
    active = beat.get("tremoloBar")
    vibe = beat.get("vibratoWithTremoloBar")
    if active is None and vibe is None:
        return None
    if vibe not in (None, 'slight', 'wide'):
        unsupported(location, 'Unknown bar vibrato')
    pairs = []
    if active is not None:
        if not isinstance(active, dict) or set(active) - {'points','tone','extend'}:
            unsupported(location, 'Unknown bar structure')
        if 'extend' in active and type(active['extend']) is not bool:
            unsupported(location, 'Invalid editor extension')
        if 'tone' in active and not -800 <= fraction(active['tone'], location) <= 400:
            unsupported(location, 'Invalid bar summary')
        points = active.get('points')
        if not isinstance(points, list) or not 1 <= len(points) <= 1024:
            unsupported(location, 'No bounded bar curve')
        if any(not isinstance(p,dict) or set(p)-{'position','tone','precisePosition'} for p in points):
            unsupported(location, 'Unknown bar point')
        exact = sum('precisePosition' in p for p in points)
        if exact not in (0,len(points)):
            unsupported(location, 'Mixed bar coordinate systems')
        coarse = [fraction(p.get('position'),location) for p in points]
        coords = [fraction(p.get('precisePosition'),location)/100 if exact else x/60 for p,x in zip(points,coarse)]
        tones = [fraction(p.get('tone'),location)/50 for p in points]
        if (coarse != sorted(coarse) or coords != sorted(coords) or any(not 0 <= x <= 60 for x in coarse)
                or any(not 0 <= x <= 1 for x in coords) or any(not -16 <= x <= 8 for x in tones)):
            unsupported(location, 'Invalid bar curve')
        pairs = list(zip(coords,tones))
        # Pinned performer installs the second value immediately in this form.
        if len(pairs)==2 and coarse[0]==0:
            pairs.insert(1,(fraction(0),tones[1]))
        pairs.append((fraction(1),tones[-1]))
        pairs = list(dict(pairs).items())
    return {'curve':pairs,'vibrato':vibe}


def check_bar(wanted, actual, check, location):
    if wanted is None or actual is None:
        check.equal('whammy_presence',location,wanted,actual)
        return
    if not isinstance(actual,dict):
        check.fail('whammy_structure',location,'Expected expression object'); return
    check.equal('whammy_fields',location,sorted(wanted),sorted(actual))
    for key in ('version','policy'):
        check.equal('whammy_'+key,location,wanted[key],actual.get(key))
    segments=actual.get('segments')
    if not isinstance(segments,list):
        check.fail('whammy_segments',location,'Missing segments'); return
    check.equal('whammy_segment_count',location,len(wanted['segments']),len(segments))
    for i,(left,right) in enumerate(zip(wanted['segments'],segments)):
        loc=location+f'/segments/{i}'
        if not isinstance(right,dict):
            check.fail('whammy_segment',loc,'Expected segment'); continue
        check.equal('whammy_segment_fields',loc,sorted(left),sorted(right))
        for key in ('source_id','group','vibrato'):
            check.equal('whammy_'+key,loc,left.get(key),right.get(key))
        for key in ('start','end'):
            check.near('whammy_time',loc+'/'+key,left[key],right.get(key))
        curve=right.get('curve')
        if not isinstance(curve,list):
            check.fail('whammy_curve',loc,'Expected curve'); continue
        check.equal('whammy_point_count',loc,len(left['curve']),len(curve))
        for p,q in zip(left['curve'],curve):
            if not isinstance(q,dict):
                check.fail('whammy_point',loc,'Expected point'); continue
            check.equal('whammy_point_fields',loc,['t','v'],sorted(q))
            check.near('whammy_time',loc,p['t'],q.get('t'))
            check.near('whammy_pitch',loc,p['v'],q.get('v'),1e-8)

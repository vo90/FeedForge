"""Bounded opening evidence for bass attacks below E1, with legacy checks first."""
from . import local_sync as ls, recording_sync as rs

VERSION = 'bounded-opening-low-bass-v1'
MIDI_MIN = 21
LEGACY_MIN = 28


def _measure(tracks, boundaries, points, path, reference_hz, *, confirmation=False, candidate=None):
    no = lambda reason: {'version': VERSION, 'status': 'inconclusive', 'reason': reason}
    if len(boundaries)<4 or len(points)<4:
        return no('insufficient_boundaries')
    s0,s1,s2=boundaries[:3]
    a0,a1,a2=points[:3]
    if not (s0==0 and 0<s1<s2 and -.75<=a0<0<a1<a2<=8):
        return no('outside_opening_repair_bounds')
    # Source-only eligibility is replayed independently. An acoustic failure
    # of an eligible legacy arrangement cannot be rescued by fishing for bass.
    if ls.opening_groups(tracks,s0,s1,s2):
        return no('legacy_opening_available')
    eligible=[]
    for track,first,held in ls.opening_groups(
            [t for t in tracks if t['instrument']=='bass'],s0,s1,s2,midi_min=MIDI_MIN):
        fit_low=sum(bool(v[:LEGACY_MIN-MIDI_MIN].any()) for _,v in first)
        held_low=sum(bool(v[:LEGACY_MIN-MIDI_MIN].any()) for _,v in held)
        if fit_low>=3 and held_low>=2:
            eligible.append((track,first,held,fit_low,held_low))
    if not eligible:
        return no('insufficient_low_bass_opening_and_context')
    track,first,held,fit_low,held_low=sorted(eligible,key=lambda x:(-len(x[1]),str(x[0]['id'])))[0]
    if len(first)>32 or len(held)>64:
        return no('opening_too_dense')
    if hasattr(path,'seek'):
        path.seek(0)
    pitch,flux,_=rs.features(path,reference_hz=reference_hz,midi_min=MIDI_MIN)
    result=ls._opening([track],boundaries,points,pitch,flux,midi_min=MIDI_MIN,
        version=ls.CONFIRMED_REPAIR if confirmation else ls.REPAIR,candidate=candidate)
    result['version']=VERSION
    result['analysisProfile']={'midiMin':MIDI_MIN,'midiMax':96,'instrument':'bass',
        'fitGroups':len(first),'fitLowGroups':fit_low,'heldOutGroups':len(held),
        'heldOutLowGroups':held_low}
    return result


def propose(tracks, boundaries, points, path, reference_hz):
    return _measure(tracks,boundaries,points,path,reference_hz)


def confirm(tracks, boundaries, points, path, reference_hz, candidate):
    """Confirm only the applied candidate, retaining the 95%-of-best gate."""
    return _measure(tracks,boundaries,points,path,reference_hz,confirmation=True,candidate=candidate)

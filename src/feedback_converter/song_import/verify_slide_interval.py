"""Reconstruct targeted slide bounds from independently parsed atoms."""


def resolve(event, destination, onset):
    atoms = event['bend_atoms']
    first = atoms[0][0]
    tail, left, right, _ = atoms[-1]
    if (tail.slide not in ('shift', 'legato') or not 0 < first.fret <= 24
            or not 0 <= destination.fret <= 24 or destination.effects.get('mt')
            or onset != right or event['end'] != right or event['start'] != atoms[0][1]
            or (destination.string, destination.voice) != (first.string, first.voice)):
        return None
    previous = event['start']
    for i,(a,start,end,_) in enumerate(atoms):
        if ((a.fret,a.string,a.voice) != (first.fret,first.string,first.voice)
                or start != previous or (i and not a.tie)
                or a.staccato or a.attack_offset or a.effects.get('mt')
                or (a.slide and i < len(atoms)-1)):
            return None
        previous = end
    return left,right


def compare(wanted, actual, note, check, location):
    if wanted is None and actual is None:
        return
    if not isinstance(wanted, dict) or not isinstance(actual, dict):
        check.fail('slide_interval', location, 'Missing or unexpected targeted slide interval.')
        return
    check.equal('slide_interval', location+'/keys', ['end','start'], sorted(actual))
    for key in ('start','end'):
        value = actual.get(key)
        if type(value) not in (int,float):
            check.fail('slide_interval', location+'/'+key, 'Slide interval must be numeric.')
        else:
            check.near('slide_interval', location+'/'+key, wanted[key], value)
    if (type(actual.get('start')) in (int,float) and type(actual.get('end')) in (int,float)
            and not 0 <= actual['start'] < actual['end'] <= note.get('sus',0)+.0000011):
        check.fail('slide_interval', location, 'Slide interval exceeds its sustain.')

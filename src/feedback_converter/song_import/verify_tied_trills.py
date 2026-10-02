"""Independent raw-source accounting for marked tied trill continuations."""
from collections import defaultdict
from fractions import Fraction


def expected_sequence(event):
    from .verify_source import unsupported
    def reject(message):
        unsupported(event['locations'][0] + '/trill', message)
    atoms = event['bend_atoms']
    if any(a.staccato for a, *_ in atoms):
        reject('Tied staccato trill is not qualified.')
    beginning, finish, pitch = event['start'], event['end'], event['f']
    commands = [(beginning, pitch, 1), (finish, pitch, -1)]
    descriptions = []
    for index, (atom, offset, ending, occurrence) in enumerate(atoms):
        shape = atom.trill
        if shape is None:
            continue
        if index == 0:
            offset, ending = beginning, finish
        clock, interval = shape['tpqn'], shape['ticks']
        span = (ending-offset)*clock
        cycles = max(2, span//interval) if 2*(span-1) >= 3*interval else 1
        if len(commands) + 2*cycles > 1000000:
            reject('Tied trill event count exceeds limit.')
        pitches = (pitch, shape['auxiliaryFret'])
        for ordinal in range(1, cycles):
            position = offset + Fraction(ordinal*interval, clock)
            commands.append((position, pitches[(ordinal-1)%2], -1))
            commands.append((position, pitches[ordinal%2], 1))
        if cycles > 1 and cycles%2 == 0:
            commands.append((ending, pitches[1], -1))
        path = atom.location.split('/')
        descriptions.append({'sourceId': 'songsterr:' + ':'.join(path[i] for i in (1,3,5,7,9)),
            'occurrence': occurrence+1, 'startQuarter': str(offset), 'endQuarter': str(ending),
            'tied': atom.tie, **shape})
    at_time = defaultdict(list)
    for when, fret, direction in commands:
        at_time[when].append((fret, direction))
    sounding, attacks = {}, []
    for when in sorted(at_time):
        starts = []
        for fret, direction in at_time[when]:
            if direction < 0:
                sounding.pop(fret, None)
            else:
                if fret in sounding:
                    reject('Repeated pitch in tied trill requires a reattack policy.')
                sounding[fret] = when
                starts.append(fret)
        if len(sounding)>1 or len(starts)>1:
            reject('Tied trill has simultaneous pitches or attacks.')
        if when < finish and not sounding:
            reject('Tied trill contains silence; no note extension is assumed.')
        if starts:
            if attacks and attacks[-1][1] == starts[0]:
                reject('Tied trill same-fret transition has no defined HO/PO.')
            attacks.append((when, starts[0]))
    if sounding or not attacks or attacks[0] != (beginning,pitch):
        reject('Incomplete tied trill sequence.')
    intervals = [(start, attacks[i+1][0] if i+1<len(attacks) else finish, fret)
                 for i,(start,fret) in enumerate(attacks)]
    return intervals, descriptions

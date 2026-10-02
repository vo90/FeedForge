"""Independent occurrence evidence for slides whose ending is not performed."""
from collections import defaultdict


def source_id(location):
    fields = location.split('/')
    return 'songsterr:' + ':'.join(fields[i] for i in (1, 3, 5, 7, 9))


def omissions(source, part, order, clock):
    if source.format != 'songsterr' or not part.beats:
        return {}
    if all(after <= before + 1 or not source.bars[before + 1].endings
           for before, after in zip(order, order[1:])):
        return {}
    # Reverse each voice's event stream. A rest only qualifies while it is
    # closer than the next event on this string. This does not use the producer
    # model, endpoint index, traversal, clock or omission detector.
    streams = defaultdict(list)
    candidates = {}
    for visit, bar in enumerate(order):
        origin = clock.measure_starts[visit]
        for beat in part.beats[bar]:
            if beat['rest'] and beat['length'] > 0:
                streams[beat['voice']].append((origin + beat['q'], 0, visit, beat['location'], beat))
        for atom in part.bars[bar]:
            streams[atom.voice].append((origin + atom.q, 1, visit, atom.location, atom))
            if (visit + 1 >= len(order) or order[visit + 1] <= bar + 1
                    or not source.bars[bar + 1].endings or atom.slide not in ('shift', 'legato')
                    or atom.fret == 127 or atom.effects.get('mt')
                    or atom.q + atom.length != source.bars[bar].length):
                continue
            targets = [a for a in part.bars[bar + 1]
                       if a.voice == atom.voice and a.string == atom.string and a.q == 0]
            if len(targets) == 1 and not targets[0].tie and targets[0].fret != 127 and not targets[0].effects.get('mt'):
                candidates[visit, atom.location] = (bar, targets[0])
    result = {}
    for stream in streams.values():
        next_note, next_rest = {}, None
        for q, kind, visit, location, item in sorted(stream, key=lambda r: r[:4], reverse=True):
            if kind == 0:
                next_rest = (q, visit, location)
                continue
            candidate = candidates.get((visit, location))
            if candidate and next_rest:
                rest_q, rest_visit, rest_location = next_rest
                if rest_q >= q + item.length and next_note.get(item.string, rest_q + 1) > rest_q:
                    bar, target = candidate
                    result[visit, location] = {
                        'target': {'sourceId': source_id(target.location), 'measure': bar + 2,
                                   'fret': target.fret, 'performed': False},
                        'transition': {'fromMeasure': bar + 1, 'toMeasure': order[visit + 1] + 1,
                                       'occurrence': visit + 2},
                        'interruption': {'location': rest_location, 'occurrence': rest_visit + 1,
                                         'time': float(clock.at(rest_q)), 'kind': 'rest'},
                        'used': {'rule': 'omit-slide-skipped-ending-rest'}}
            next_note[item.string] = q
    return result


def receipt(part, atom, visit, event, start, clock, facts):
    return {'trackId': part.id, 'sourceId': source_id(atom.location), 'location': atom.location,
            'occurrence': visit + 1, 'attack': float(clock.at(event['start'])),
            'start': float(clock.at(start)), 'string': atom.string, 'fret': atom.fret,
            'muted': atom.effects.get('mt') is True, 'authored': {'slide': atom.slide}, **facts}

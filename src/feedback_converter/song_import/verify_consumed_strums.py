"""Independent reconstruction of source playback's non-sounding strum notes."""


def reconstruct(source, part, events, links, clock):
    if source.format != 'songsterr':
        return []
    written = {b['location']: b for bar in part.beats for b in bar}
    neighbors = set()
    for voice in {b['voice'] for b in written.values()}:
        sequence = [b for bar in part.beats for b in bar if b['voice'] == voice]
        for i, b in enumerate(sequence):
            if any(x['notation'].get('grace') for x in sequence[max(0, i-1):i+2]):
                neighbors.add(b['location'])
    connected = {id(e) for pair in links for e in pair}
    rows, removed = [], set()
    for event in events:
        if event['end'] > event['start'] or len(event['locations']) != 1:
            continue
        atom, left, right, visit = event['bend_atoms'][0]
        beat = written[atom.beat]
        if (not atom.strum_direction or atom.beat not in neighbors or atom.length <= 0
                or atom.length >= beat['written_length'] or atom.attack_offset < atom.length
                or id(event) in connected or event.get('linked')
                or atom.tie or atom.staccato or atom.bends or atom.slide or atom.slide_in
                or atom.whammy or atom.trill or atom.pick_scrape
                or atom.hopo_origin or atom.hopo_destination):
            continue
        p = atom.location.split('/')
        rows.append({'trackId': part.id, 'sourceId': 'songsterr:' + ':'.join(p[i] for i in (1,3,5,7,9)),
                     'location': atom.location, 'occurrence': visit + 1,
                     'start': float(clock.at(left)), 'attack': float(clock.at(event['start'])),
                     'end': float(clock.at(right)), 'string': atom.string, 'fret': atom.fret,
                     'authored': {'writtenQuarters': str(beat['written_length']),
                                  'soundingQuarters': str(atom.length),
                                  'attackOffsetQuarters': str(atom.attack_offset)},
                     'used': {'rule': 'consumed-strum-grace-no-attack'}})
        removed.add(id(event))
    events[:] = [e for e in events if id(e) not in removed]
    return rows

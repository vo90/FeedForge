"""Independent raw-source trill oracle; never imports the production expander."""
from copy import deepcopy
from fractions import Fraction as F
from math import lcm


def read(note, beat, measures, location):
    from .verify_source import unsupported, fraction, integer
    value = note.get('trill')
    if value is None:
        return None
    where = location + '/trill'
    if not isinstance(value, dict) or sorted(value) != ['auxiliaryFret', 'speed']:
        unsupported(where, 'Unverified trill description.')
    auxiliary = integer(value['auxiliaryFret'], where)
    speed = fraction(value['speed'], where)
    if not 0 <= auxiliary <= 24 or not 0 <= note.get('fret', -1) <= 24 or auxiliary == note.get('fret') or not 0 < speed <= 960:
        unsupported(where, 'Invalid trill pitch or rate.')
    divisors = []
    flags = set()
    for bar in measures:
        if bar.get('tripletFeel') not in (None, 'off'):
            flags.add('swing')
        for voice in bar.get('voices', []):
            if voice.get('rest'): continue
            for b in voice.get('beats', []):
                if isinstance(b.get('tuplet'), int) and b['tuplet'] > 0 and b['tuplet'] not in divisors:
                    divisors.append(b['tuplet'])
                d = b.get('duration')
                if not isinstance(d, list) or len(d) != 2: continue
                denominator = integer(d[1], where)
                if denominator not in [2**i for i in range(16)]: continue
                if denominator not in divisors: divisors.append(denominator)
                for n in b.get('notes', []):
                    if n.get('dead'): flags.add('dead')
                    if n.get('slide') or n.get('bend'): flags.add('pitch')
                    if n.get('tremolo'): flags.add('tremolo')
    for flag, extra in [('swing', [24,48]), ('dead', [128]), ('pitch', [60]), ('tremolo', [8,16,32,64])]:
        if flag in flags: divisors.extend(extra)
    clock = max(32, 4 * max(divisors, default=0))
    for d in divisors:
        merged = lcm(clock, d)
        if merged < 32768: clock = merged
    while clock < 10000: clock *= 2
    clock = min(32767, clock)
    written_ticks = 1920 * fraction(beat.get('duration', [1,4]), where)
    cap = min(960, written_ticks.numerator // written_ticks.denominator)
    interval_ticks = (F(clock, 480) * min(speed, cap)) // 1
    if interval_ticks <= 0: unsupported(where, 'Trill rate exceeds the source clock.')
    return {'auxiliaryFret': auxiliary, 'speed': str(speed), 'tpqn': clock, 'ticks': interval_ticks}


def segment(atom, event):
    from .verify_source import unsupported
    if atom.trill or event.get('trill'):
        if atom.tie and atom.trill:
            unsupported(atom.location + '/trill', 'A tied continuation carries another trill.')
        if (set(atom.effects) - {'pm','ghost','ac','vb','pkd'} or atom.bends or atom.slide
                or atom.slide_in or atom.whammy or atom.pick_scrape or atom.attack_offset):
            unsupported(atom.location + '/trill', 'Combined trill gesture is not independently verified.')


def expand(events, links, part, notation, retained):
    from .verify_source import unsupported
    expanded, last_pitch, groups = [], {}, []
    for event in events:
        data = event.get('trill')
        if not data:
            expanded.append(event)
            continue
        left, right = event['start'], event['end']
        if event['staccato']:
            right = left + max((right-left)/2, F(1,32))
            if right > event['end']:
                unsupported(event['locations'][0], 'Staccato exceeds the written duration.')
        step = F(data['ticks'], data['tpqn'])
        intervals = ((right-left)*data['tpqn']+1) // data['ticks']
        length = max(2, intervals) if 2*(right-left) >= 3*step else 1
        if len(expanded) + length > 500000:
            unsupported(event['locations'][0], 'Expanded source note limit exceeded.')
        generated = []
        for index in range(length):
            item = deepcopy(event)
            item.update(start=left+index*step, end=right if index+1 == length else left+(index+1)*step, staccato=False)
            item['f'] = data['auxiliaryFret'] if index % 2 else event['f']
            if index:
                item['effects'] = {k:v for k,v in event['effects'].items() if k in {'pm','ghost','vb'}}
                item['effects']['ho' if item['f'] > generated[-1]['f'] else 'po'] = True
            item['effects'].pop('ln', None)
            if index+1 < length or event['effects'].get('ln'): item['effects']['ln'] = True
            if item['end'] <= item['start']:
                unsupported(event['locations'][0], 'Empty trill event.')
            generated.append(item)
        last_pitch[id(event)] = generated[-1]['f']
        expanded.extend(generated)
        groups.append((event, generated, left, right, data))
    firsts = {id(event): rows[0] for event, rows, *_ in groups}
    for previous, following in links:
        if id(previous) not in last_pitch: continue
        note = firsts.get(id(following), following)
        if note['f'] == last_pitch[id(previous)]:
            unsupported(note['locations'][0], 'Trill exit has an ambiguous same-pitch legato.')
        for fx in ('ho','po'): note['effects'].pop(fx, None)
        note['effects']['ho' if note['f'] > last_pitch[id(previous)] else 'po'] = True
        written = notation[(note['occurrence']-1, note['locations'][0])]
        for fx in ('ho','po'): written.pop(fx, None)
        written.update({fx:True for fx in ('ho','po') if note['effects'].get(fx)})
    for original, rows, left, right, data in groups:
        ids = []
        for path in original['locations']:
            bits = path.split('/')
            ids.append(f'songsterr:{part.id}:{bits[3]}:{bits[5]}:{bits[7]}:{bits[9]}')
        identity = f"{ids[0]}@{original['occurrence']}:trill"
        retained.append({'id': identity, 'trackId': part.id, 'occurrence': original['occurrence'],
            'sourceIds': ids, 'string': original['s'], 'mainFret': original['f'], **data,
            'startQuarter': str(left), 'endQuarter': str(right),
            'events': [{'id':f'{identity}:{i}', 'ordinal':i, 'fret':r['f'],
                        'startQuarter':str(r['start']), 'endQuarter':str(r['end']),
                        'articulation':'ho' if r['effects'].get('ho') else 'po' if r['effects'].get('po') else 'initial',
                        'linked':bool(r['effects'].get('ln'))} for i,r in enumerate(rows)]})
    return expanded

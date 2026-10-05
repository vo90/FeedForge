"""Independent rational reconstruction; no producer imports or curve reuse."""
from bisect import bisect_left, bisect_right
from fractions import Fraction as F


def check_evidence(wanted, actual, check, path='import/finger-bend-timing', field=None):
    """Exact structure/identities; bounded numeric error, never rounded equality.

    Subtracting two large float timestamps can straddle a rounding half-unit
    even when the underlying rational and floating clocks agree.
    """
    code = 'finger_bend_timing'
    if isinstance(wanted, dict):
        if not isinstance(actual, dict):
            check.fail(code, path, 'Expected a bend evidence object.')
            return
        check.equal(code, path + '/keys', sorted(wanted), sorted(actual))
        for key, value in wanted.items():
            check_evidence(value, actual.get(key), check, path + '/' + key, key)
    elif isinstance(wanted, list):
        if not isinstance(actual, list):
            check.fail(code, path, 'Expected a bend evidence array.')
            return
        check.equal(code, path + '/count', len(wanted), len(actual))
        for i, (a, b) in enumerate(zip(wanted, actual)):
            check_evidence(a, b, check, path + '/' + str(i))
    elif field in {'start', 'end', 'gestureEnd', 't'}:
        check.near(code, path, wanted, actual)
    elif field in {'v', 'value'}:
        check.near(code, path, wanted, actual, 1e-9)
    else:
        if type(wanted) is not type(actual):
            check.fail(code, path, 'Bend evidence identity type differs.')
        else:
            check.equal(code, path, wanted, actual)


def terminal_contexts(events):
    """Independent source-event neighbours, before generated trill expansion."""
    grouped, by_string, result = {}, {}, {}
    for e in events:
        a = e['bend_atoms'][0][0]
        grouped.setdefault((a.voice, e['start']), []).append(e)
        by_string.setdefault(e['s'], []).append(e)
    for peers in grouped.values():
        if len(peers) > 1:
            for e in peers:
                result.setdefault(id(e), set()).add('simultaneous-attack')
    for strand in by_string.values():
        ordered = sorted(strand, key=lambda e:e['start'])
        for current, following in zip(ordered, ordered[1:]):
            if following['bend_atoms'][0][0].slide_in:
                result.setdefault(id(current), set()).add('following-slide-in')
    return result


def reconstruct(event, part, clock, sound_end, terminal_context=(), *, following_hopo=None):
    entries = event['bend_atoms']
    if not any(a.bends for a, *_ in entries):
        return None
    if len(entries) > 1 and any(a.staccato for a, *_ in entries):
        return None
    origin = event['start']
    boundaries = clock.positions[bisect_right(clock.positions, origin):bisect_left(clock.positions, sound_end)]
    if len(entries) == 1 and not boundaries:
        return None

    def identity(atom):
        fields = atom.location.split('/')
        return 'songsterr:' + ':'.join(fields[i] for i in (1, 3, 5, 7, 9))

    rows, gestures = [], []
    mixed = any(a.slide or a.slide_in or a.whammy or a.attack_offset for a, *_ in entries)
    overlap = False
    crossings = []
    last_end = None
    for i, (atom, start, end, visit) in enumerate(entries):
        row = {'sourceId': identity(atom), 'occurrence': visit + 1,
               'start': float(clock.at(start)), 'end': float(clock.at(end)),
               'bend': [{'position': str(p), 'value': float(v)} for p, v in atom.bends]}
        rows.append(row)
        if not atom.bends:
            continue
        stop = sound_end if i == 0 else end
        if i + 1 < len(entries) and entries[i + 1][0].bends:
            stop = entries[i + 1][1]
        row['gestureEnd'] = float(clock.at(stop))
        if last_end is not None and start < last_end:
            overlap = True
            crossings.append((len(gestures)-1, start))
        last_end = min(stop, sound_end)
        gestures.append((atom, start, stop))

    first = entries[0][0]
    evidence = {'trackId': part.id, 'sourceId': identity(first), 'location': first.location,
                'occurrence': entries[0][3] + 1, 'start': float(clock.at(origin)), 'end': float(clock.at(sound_end)),
                'string': event['s'], 'fret': event['f'], 'segments': rows}
    targeted = event.get('targeted_slide')
    ordinary_target = bool(targeted) and not overlap
    for index,(a,*_) in enumerate(entries):
        if (a.whammy or a.attack_offset or a.staccato or a.hopo_origin or a.hopo_destination
                or a.trill or a.pick_scrape or a.beat_vibrato
                or (index and a.slide_in) or (a.slide and index < len(entries)-1)
                or any(a.effects.get(k) for k in ('hm','hp','hn','harmonic_target','mt','lr','pm','tr'))
                or (a.effects.get('vb') and a.finger_vibrato not in ('slight','wide'))):
            ordinary_target = False
    if ordinary_target:
        mixed = False
        a,start,end,visit = entries[-1]
        evidence['targetedSlide'] = {'sourceId': identity(a), 'occurrence': visit+1,
            'start': float(clock.at(start)), 'end': float(clock.at(end)),
            'fret': event['effects']['sl'], 'kind': a.slide, 'policy':'independent-authored-interval'}
    # Reconstruct from independently parsed atoms and retained bar intervals.
    # An empty qualitative control cannot establish an explicit or held pitch.
    bar_atoms = [(a, start, end, visit) for a, start, end, visit in entries if a.whammy]
    separate_bar = bool(bar_atoms) and not overlap and bool(event.get('whammy'))
    for atom, *_ in entries:
        if (atom.slide or atom.slide_in or atom.attack_offset
                or (atom.whammy and (atom.whammy['curve']
                    or atom.whammy['vibrato'] not in ('slight', 'wide')))):
            separate_bar = False
    if any(region['points'] for region in event.get('whammy', [])):
        separate_bar = False
    if separate_bar:
        mixed = False
        evidence['barVibrato'] = {
            'policy': 'independent-qualitative-control',
            'segments': [{'sourceId': identity(a), 'occurrence': visit + 1,
                          'start': float(clock.at(start)), 'end': float(clock.at(end)),
                          'vibrato': a.whammy['vibrato']} for a, start, end, visit in bar_atoms]}
    # Rebuild explicit control independence from source atoms, not producer
    # evidence. Bar pitch remains on its own retained timeline, not summed.
    explicit_bar = (not overlap and 0 < first.fret < 127
                    and entries[0][1] == origin and entries[-1][2] == sound_end
                    and any(a.whammy['curve'] for a, *_ in bar_atoms)
                    and any(r['points'] for r in event.get('whammy', [])))
    tail_atom, tail_begin, tail_finish, _ = entries[-1]
    bar_slide = (len(entries) > 1 and tail_atom.tie and tail_atom.slide in ('up', 'down')
                 and origin < tail_begin < sound_end and tail_finish == sound_end)
    prior_end = None
    for index, (a, begin, end, _) in enumerate(entries):
        if ((a.fret, a.string, a.voice) != (first.fret, first.string, first.voice)
                or (index and (not a.tie or begin != prior_end))
                or (a.slide and not (bar_slide and index == len(entries)-1))
                or a.slide_in or a.attack_offset or a.staccato
                or a.hopo_origin or a.hopo_destination or a.trill or a.pick_scrape or a.beat_vibrato
                or any(a.effects.get(k) for k in ('hm', 'hp', 'hn', 'harmonic_target', 'mt', 'lr', 'pm', 'tr'))
                or (a.effects.get('vb') and a.finger_vibrato not in ('slight', 'wide'))):
            explicit_bar = False
        prior_end = end
    bar_slide = bar_slide and explicit_bar
    explicit_control = None
    if explicit_bar:
        explicit_control = {
            'policy': 'independent-explicit-control',
            'segments': [{'sourceId': identity(a), 'occurrence': visit + 1,
                          'start': float(clock.at(start)), 'end': float(clock.at(end)),
                          'curve': [{'position': str(p), 'value': float(v)} for p, v in a.whammy['curve']],
                          'vibrato': a.whammy['vibrato']} for a, start, end, visit in bar_atoms]}
        if not bar_slide:
            mixed = False
            evidence['barCurve'] = explicit_control
    tail, tail_start, tail_end, _ = entries[-1]
    # Independently establish the complete composition from rational source
    # atoms before either cue may relax the other's expression guard.
    boundary_cues = (len(entries) > 1 and not overlap and 0 < first.fret < 127
                     and first.slide_in in ('up', 'down') and tail.tie
                     and tail.slide in ('up', 'down') and tail_end == sound_end
                     and origin < tail_start < sound_end
                     and all(c in ('simultaneous-attack', 'following-slide-in') for c in terminal_context))
    for index, (a, *_) in enumerate(entries):
        if (a.fret != first.fret or a.string != first.string
                or (a.slide and index != len(entries)-1) or (a.slide_in and index != 0)
                or a.whammy or a.attack_offset or a.staccato or a.hopo_origin or a.hopo_destination
                or a.trill or a.pick_scrape or a.beat_vibrato
                or (a.effects.get('vb') and a.finger_vibrato not in ('slight', 'wide'))
                or any(a.effects.get(k) for k in ('hm', 'hp', 'hn', 'harmonic_target', 'mt', 'lr', 'pm', 'tr'))):
            boundary_cues = False
    incoming = first.slide_in in ('up', 'down') and 0 < first.fret < 127 and not overlap
    harmonic_targets = [a.effects.get('harmonic_target') for a, *_ in entries]
    fixed_artificial = (bool(harmonic_targets[0]) and harmonic_targets[0].get('kind') == 'artificial'
                        and harmonic_targets[0].get('policy') == 'harmonic'
                        and all(t == harmonic_targets[0] for t in harmonic_targets))
    for index, (atom, *_) in enumerate(entries):
        if ((atom.slide and not (boundary_cues and index == len(entries)-1))
                or (index != 0 and atom.slide_in) or atom.whammy or atom.attack_offset
                or atom.hopo_origin or atom.hopo_destination or atom.trill or atom.pick_scrape
                or atom.beat_vibrato
                or (atom.effects.get('harmonic_target') and not fixed_artificial)
                or any(atom.effects.get(k) for k in ('hm', 'hp', 'hn', 'mt', 'lr', 'pm', 'tr'))
                or (atom.effects.get('vb') and atom.finger_vibrato not in ('slight', 'wide'))):
            incoming = False
    if incoming:
        mixed = False
        evidence['initialSlideIn'] = {'sourceId': identity(first), 'direction': first.slide_in,
                                     'start': float(clock.at(origin)), 'attackTiming': 'authored-note',
                                     'bendTiming': 'authored-tie'}
    terminal_candidate = None
    # Source-only qualification of one held pinch target. Changed or omitted
    # pinch markings on ties retain their separate harmonic interpretation.
    initial_target = harmonic_targets[0]
    terminal_pinch = (bool(initial_target) and initial_target.get('kind') == 'pinch'
                      and initial_target.get('policy') == 'harmonic' and not overlap
                      and event['effects'].get('harmonic_target') == initial_target
                      and event['effects'].get('hp') is True and not event.get('contact')
                      and 0 < first.fret < 127 and entries[0][1] == origin
                      and tail_end == sound_end)
    previous_stop = None
    for i, (atom, start, stop, _) in enumerate(entries):
        target = harmonic_targets[i]
        if ((atom.fret, atom.string, atom.voice) != (first.fret, first.string, first.voice)
                or (i and (not atom.tie or start != previous_stop))
                or (target and (target.get('kind') != 'pinch' or target.get('policy') != 'harmonic'
                                or atom.effects.get('hp') is not True))
                or (not target and atom.effects.get('hp'))):
            terminal_pinch = False
        previous_stop = stop
    # Independently qualify the written beat instruction from source atoms.
    # It stays a separate playback limitation, not a new pitch controller.
    beat_entries = []
    independent_beat = (not overlap and len(entries) > 1 and 0 < first.fret < 127
                        and entries[0][1] == origin and tail_end == sound_end
                        and tail.slide in ('up', 'down'))
    previous_stop = None
    for i, (a, begin, finish, visit) in enumerate(entries):
        if ((a.fret, a.string, a.voice) != (first.fret, first.string, first.voice)
                or (i and (not a.tie or begin != previous_stop))
                or a.slide_in or (a.slide and i != len(entries)-1)
                or a.whammy or a.attack_offset or a.staccato or a.hopo_origin or a.hopo_destination
                or a.trill or a.pick_scrape
                or any(a.effects.get(k) for k in ('hm', 'hp', 'hn', 'harmonic_target', 'mt', 'lr', 'pm', 'tr'))
                or ((a.effects.get('vb') or a.beat_vibrato) and a.finger_vibrato not in ('slight', 'wide'))):
            independent_beat = False
        if a.beat_vibrato:
            beat_entries.append({'sourceId': identity(a), 'occurrence': visit + 1,
                                 'start': float(clock.at(begin)), 'end': float(clock.at(finish)),
                                 'intensity': a.finger_vibrato})
        previous_stop = finish
    beat_control = {'policy': 'independent-written-instruction', 'segments': beat_entries} if independent_beat and beat_entries else None
    if (len(entries) > 1 and tail.tie
            and tail.slide in {'up', 'down'} and tail_end == sound_end
            and origin < tail_start < sound_end and 0 < tail.fret < 127):
        extras = any((a.slide_in and not (boundary_cues and i == 0))
                     or (a.whammy and not bar_slide) or a.attack_offset or a.hopo_origin or a.hopo_destination
                     or a.trill or a.pick_scrape or (a.beat_vibrato and not beat_control) or (i < len(entries)-1 and a.slide)
                     or ((tail.bends or terminal_pinch) and a.effects.get('vb') and a.finger_vibrato not in ('slight', 'wide'))
                     or (not terminal_pinch and (a.effects.get('hp') or a.effects.get('harmonic_target')))
                     or any(a.effects.get(k) for k in ('hm', 'hn', 'mt', 'lr', 'pm', 'tr'))
                     for i,(a,*_) in enumerate(entries))
        bend, begin, finish = gestures[-1]
        controls = [(begin+(finish-begin)*p,v) for p,v in bend.bends]
        settled = controls[0][0] <= tail_start and all(
            a[1] == b[1] for a,b in zip(controls,controls[1:]) if b[0] > tail_start)
        # Simultaneous source notes remain a chord. Native auto-strumming is
        # synthesis, not an authored offset. A later slide-in's synth lead also
        # must not shrink this source note or its bend interval.
        unsupported_context = any(c not in ('simultaneous-attack', 'following-slide-in') for c in terminal_context)
        if not extras and (settled or not unsupported_context):
            terminal_candidate = {'sourceId': identity(tail), 'direction': tail.slide,
                        'start': float(clock.at(tail_start)), 'end': float(clock.at(sound_end)),
                        'value': float(controls[-1][1]),
                        **({'beatVibrato': beat_control} if beat_control else {}),
                        **({'continuedPinchHarmonic': {'initialTarget': dict(initial_target),
                                                     'policy': 'initial-target-continued'}} if terminal_pinch else {}),
                        **({'bendTiming': 'authored-segment'} if tail.bends else {}),
                        **({'bendPhase': 'changing', 'pitchPolicy': 'independent-source-bend'} if not settled else {}),
                        **({'attackTiming': 'authored-chord'} if not settled and 'simultaneous-attack' in terminal_context else {}),
                        **({'endTiming': 'authored-tie'} if not settled and 'following-slide-in' in terminal_context else {})}
    terminal_handoff = False
    overlap_reason = None
    if overlap:
        # Reconstruct initial-target continuation from source atoms, not from
        # producer evidence. Later pinch targets may be absent or different;
        # other harmonic kinds and timed contact are outside this rule.
        initial_pinch = harmonic_targets[0]
        pinch_continuation = (bool(initial_pinch) and initial_pinch.get('kind') == 'pinch'
                              and initial_pinch.get('policy') == 'harmonic'
                              and event['effects'].get('harmonic_target') == initial_pinch
                              and event['effects'].get('hp') is True and not event.get('contact'))
        for i, (a, *_) in enumerate(entries):
            target = harmonic_targets[i]
            if ((i and not a.tie)
                    or (target and (target.get('kind') != 'pinch' or target.get('policy') != 'harmonic'
                                    or a.effects.get('hp') is not True))
                    or (not target and a.effects.get('hp'))):
                pinch_continuation = False
        # Derive terminal outgoing legato from independently resolved source
        # links. Neither producer output nor retained evidence establishes it.
        outgoing = None
        if following_hopo is not None and len(entries) > 1:
            tail, _, tail_end, tail_visit = entries[-1]
            dest, dest_start, _, dest_visit = following_hopo['bend_atoms'][0]
            technique = 'ho' if dest.fret > first.fret else 'po'
            valid = (tail.hopo_origin and tail.tie and event['effects'].get('ln') is True
                     and not event['effects'].get('ho') and not event['effects'].get('po')
                     and tail_end == sound_end == dest_start == following_hopo['start']
                     and 0 < first.fret < 127 and 0 <= dest.fret < 127 and dest.fret != first.fret
                     and (dest.string, dest.voice) == (first.string, first.voice)
                     and following_hopo['effects'].get(technique) is True)
            previous_end = None
            for i, (atom, begin, end, _) in enumerate(entries):
                if ((atom.fret, atom.string, atom.voice) != (first.fret, first.string, first.voice)
                        or (i and (not atom.tie or begin != previous_end))
                        or (atom.hopo_origin and i != len(entries)-1) or atom.hopo_destination
                        or atom.slide or atom.slide_in or atom.whammy or atom.attack_offset
                        or atom.staccato or atom.trill or atom.pick_scrape or atom.beat_vibrato
                        or any(atom.effects.get(k) for k in ('hm', 'hp', 'hn', 'harmonic_target',
                                                             'mt', 'lr', 'pm', 'tr', 'vb'))):
                    valid = False
                previous_end = end
            if (dest.tie or dest.bends or dest.hopo_origin or dest.slide or dest.slide_in or dest.whammy
                    or dest.attack_offset or dest.staccato or dest.trill or dest.pick_scrape or dest.beat_vibrato
                    or any(dest.effects.get(k) for k in ('hm', 'hp', 'hn', 'harmonic_target',
                                                        'mt', 'lr', 'pm', 'tr', 'vb'))):
                valid = False
            if valid:
                outgoing = {'sourceId': identity(tail), 'occurrence': tail_visit + 1,
                            'destinationSourceId': identity(dest), 'destinationOccurrence': dest_visit + 1,
                            'start': float(clock.at(dest_start)), 'fret': dest.fret, 'technique': technique,
                            'policy': 'independent-outgoing-link'}
        compound = any((a.hopo_origin and outgoing is None) or a.hopo_destination or a.trill or a.pick_scrape or
                       (a.effects.get('hp') and not pinch_continuation) or
                       any(a.effects.get(k) for k in ('hm', 'hn',
                                                      'mt', 'lr', 'pm', 'tr')) for a, *_ in entries)
        handoffs = []
        for index, q in crossings:
            atom, begin, finish = gestures[index]
            points = [(begin+(finish-begin)*p, v) for p,v in atom.bends]
            # The suffix must already be flat, including the segment crossing
            # the handoff. The remaining terminal update must be at note-off.
            flat = points[0][0] <= q and all(
                b[1] == a[1] for a,b in zip(points,points[1:]) if b[0] > q)
            safe = flat and finish == sound_end
            handoffs.append({'sourceId': identity(atom), 'nextSourceId': identity(gestures[index+1][0]),
                             'start': float(clock.at(q)), 'end': float(clock.at(finish)),
                             'classification': 'settled-tail' if safe else 'changing-tail'})
        flat_handoffs = all(h['classification']=='settled-tail' for h in handoffs)
        compound = compound or (outgoing is not None and not flat_handoffs)
        vibrato_atoms = [a for a, *_ in entries if a.effects.get('vb')]
        # Reconstruct eligibility from independently parsed source atoms. A
        # constant or continued target is separate from the settled handoff.
        harmonic_handoff = ((fixed_artificial or pinch_continuation) and flat_handoffs and not mixed and not compound
                            and 0 < first.fret < 127
                            and all(a.fret == first.fret and a.string == first.string
                                    and not a.beat_vibrato for a, *_ in entries)
                            and all(a.finger_vibrato in ('slight', 'wide') for a in vibrato_atoms))
        compound = compound or (any(harmonic_targets) and not harmonic_handoff)
        terminal_handoff = (terminal_candidate is not None and flat_handoffs and not compound
                            and not any(a.beat_vibrato for a, *_ in entries)
                            and all(a.finger_vibrato in ('slight', 'wide') for a in vibrato_atoms))
        if terminal_handoff:
            mixed = False
        separate_vibrato = (bool(vibrato_atoms) and flat_handoffs and not mixed and not compound
                            and not any(a.beat_vibrato for a, *_ in entries)
                            and all(a.finger_vibrato in ('slight', 'wide') for a in vibrato_atoms))
        compound = compound or (bool(vibrato_atoms) and not separate_vibrato)
        label = ('other-expression' if mixed or compound else
                 'clear-handoff' if flat_handoffs
                 else 'conflicting-controls')
        evidence['overlap'] = {'classification': label, 'handoffs': handoffs}
        if outgoing is not None and label == 'clear-handoff':
            evidence['overlap']['outgoingLegato'] = outgoing
        if harmonic_handoff:
            if pinch_continuation:
                evidence['overlap']['continuedPinchHarmonic'] = {
                    'initialTarget': dict(initial_pinch), 'policy': 'initial-target-continued'}
            else:
                evidence['overlap']['fixedHarmonic'] = dict(harmonic_targets[0])
        if separate_vibrato:
            evidence['overlap']['vibratoTiming'] = 'independent-note-controls'
        if terminal_handoff:
            evidence['overlap']['slideOutTiming'] = 'independent-terminal-cue'
        if label != 'clear-handoff':
            overlap_reason = 'overlap-with-other-expression' if compound else 'overlapping-bend-controls'
    terminal = terminal_candidate if not overlap or terminal_handoff else None
    if terminal:
        evidence['terminalSlideOut'] = terminal
        mixed = False
        if bar_slide:
            evidence['barCurve'] = explicit_control
    if mixed or overlap_reason:
        return {**evidence, 'status': 'deferred', 'rule': 'retained-segment-timing',
                'reason': 'mixed-pitch-or-displaced-attack' if mixed else overlap_reason,
                'curve': [{'t': float(q-clock.at(origin)), 'v': float(v)} for q,v in event['curve']]}

    knots = []
    for i, (atom, begin, finish) in enumerate(gestures):
        if begin >= sound_end:
            continue
        lo, hi = max(origin, begin), min(sound_end, finish)
        if i + 1 < len(gestures):
            hi = min(hi, gestures[i+1][1])
        controls = [(begin + (finish-begin)*p, v) for p,v in atom.bends]
        if lo > origin and not knots:
            knots.append((clock.at(origin), F(0)))
        if knots and knots[-1][0] < clock.at(lo):
            knots.append((clock.at(lo), knots[-1][1]))
        samples = [(q,v) for q,v in controls if lo <= q <= hi]
        extra = {lo, hi, *[q for q in boundaries if lo < q < hi]} - {q for q,v in samples}
        for q in extra:
            earlier = [pair for pair in controls if pair[0] <= q]
            later = [pair for pair in controls if pair[0] > q]
            if not earlier:
                v = controls[0][1]
            elif not later:
                v = controls[-1][1]
            else:
                a,b = earlier[-1], later[0]
                v = a[1] + (b[1]-a[1])*(q-a[0])/(b[0]-a[0])
            samples.append((q,v))
        for q,v in sorted(samples, key=lambda pair: pair[0]):
            point = (clock.at(q), v)
            if not knots or knots[-1] != point:
                knots.append(point)
    event['curve'] = knots
    return {**evidence, 'status': 'resolved',
            'rule': ('bend-with-slide-out' if terminal.get('bendPhase') == 'changing' else 'bend-hold-slide-out')
                    if terminal else 'settled-bend-handoff' if overlap else 'tie-resolved-finger-bend',
            'curve': [{'t': float(q-clock.at(origin)), 'v': float(v)} for q,v in knots]}

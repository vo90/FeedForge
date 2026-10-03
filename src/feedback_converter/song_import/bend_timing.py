"""Resolve ordinary Songsterr finger bends on the completed tie timeline.

Mixed pitch gestures keep their existing interpretation, with an explicit
limitation. The established tied-staccato resolver retains its stricter guards.
"""
from bisect import bisect_right, bisect_left
from copy import deepcopy
import hashlib


def terminal_contexts(articulations):
    """Index surrounding attacks once, outside the curve sampling loop.

    Chord attacks retain their authored clock, without automatic synth strums.
    A following slide-in can shorten a synthesizer note, not its authored clock.
    """
    onsets, strings, result = {}, {}, {}
    for key, a in articulations.items():
        n = a['bend_segments'][0][0]
        onsets.setdefault((n.voice_id, a['start']), []).append(key)
        strings.setdefault(n.string, []).append((a['start'], key, n))
    for members in onsets.values():
        if len(members) > 1:
            for key in members:
                result.setdefault(key, set()).add('simultaneous-attack')
    for events in strings.values():
        events.sort(key=lambda e: e[0])
        for (_, key, _), (_, _, following) in zip(events, events[1:]):
            if following.slide_in:
                result.setdefault(key, set()).add('following-slide-in')
    return result


def _terminal_slide_out(segments, intervals, attack, stop, at, context):
    """Independent bend clock with one terminal tied, direction-only flourish.

    Keep the source bend clock. Never shorten a changing bend to make it fit,
    or infer a release, target fret or playable pitch for the slide-out. A bend
    authored on that last segment uses its own source interval; native synth
    truncation to make room for slide sounds is not a musical note boundary.
    """
    last, start, end, _ = segments[-1]
    if (len(segments) < 2 or not last.tie
            or last.slide not in {'out_down', 'out_up'} or end != stop
            or not attack < start < stop or not 0 < last.fret < 127):
        return None
    for i, (n, *_rest) in enumerate(segments):
        if (n.slide and i != len(segments)-1 or n.slide_in or n.whammy or n.attack_offset
                or n.hopo or n.trill or n.pick_scrape or n.effects.get('__beat_vibrato')
                or (last.bends and n.effects.get('vb')
                    and n.effects.get('__finger_vibrato') not in {'slight', 'wide'})
                or any(n.effects.get(k) for k in ( 'hm', 'hp', 'hn', 'harmonic_target',
                                                  'mt', 'lr', 'pm', 'tr', '__hopo_origin'))):
            return None
    bend, left, right = intervals[-1]
    controls = [(left + (right-left)*p, v) for p,v in bend.bends]
    last_change = controls[0][0]
    for a,b in zip(controls, controls[1:]):
        if a[1] != b[1]:
            last_change = b[0]
    changing = last_change > start
    if changing and set(context) - {'simultaneous-attack', 'following-slide-in'}:
        return None
    return {'sourceId': last.source_id, 'direction': 'down' if last.slide == 'out_down' else 'up',
            'start': at(start), 'end': at(stop), 'value': controls[-1][1],
            **({'bendTiming': 'authored-segment'} if last.bends else {}),
            **({'bendPhase': 'changing', 'pitchPolicy': 'independent-source-bend'} if changing else {}),
            **({'attackTiming': 'authored-chord'} if changing and 'simultaneous-attack' in context else {}),
            **({'endTiming': 'authored-tie'} if changing and 'following-slide-in' in context else {})}


def finish(output, articulation, track_id, at, tempo_positions, terminal_context=()):
    segments = articulation['bend_segments']
    if not any(n.bends for n, *_ in segments):
        return None
    if len(segments) > 1 and any(n.staccato for n, *_ in segments):
        return None  # Independently covered by the tied-staccato policy.
    attack, stop = articulation['start'], articulation['end']
    boundaries = tempo_positions[bisect_right(tempo_positions, attack):bisect_left(tempo_positions, stop)]
    if len(segments) == 1 and not boundaries:
        return None
    source_id = segments[0][0].source_id
    _, part, bar, voice, beat, ni = source_id.split(':')
    evidence = {'trackId': track_id, 'sourceId': source_id,
                'location': f'parts/{part}/measures/{bar}/voices/{voice}/beats/{beat}/notes/{ni}',
                'occurrence': segments[0][3] + 1, 'start': at(attack), 'end': at(stop),
                'string': output['s'], 'fret': output['f'], 'segments': []}
    intervals = []
    previous_end = None
    reason = None
    overlaps = []
    for i, (note, start, end, visit) in enumerate(segments):
        row = {'sourceId': note.source_id, 'occurrence': visit + 1,
               'start': at(start), 'end': at(end),
               'bend': [{'position': str(p), 'value': v} for p, v in note.bends]}
        evidence['segments'].append(row)
        if note.slide or note.slide_in or note.whammy or note.attack_offset:
            reason = 'mixed-pitch-or-displaced-attack'
        if not note.bends:
            continue
        following = segments[i + 1] if i + 1 < len(segments) else None
        right = following[1] if following and following[0].bends else stop if i == 0 else end
        row['gestureEnd'] = at(right)
        if previous_end is not None and start < previous_end:
            overlaps.append((len(intervals) - 1, start))
        previous_end = min(right, stop)
        intervals.append((note, start, right))
    first = segments[0][0]
    harmonic = first.effects.get('harmonic_target')
    steady_harmonic = (isinstance(harmonic, dict) and harmonic.get('kind') == 'artificial'
                       and harmonic.get('policy') == 'harmonic'
                       and all(n.effects.get('harmonic_target') == harmonic for n, *_ in segments))
    # An approach cue has no authored target interval inside this note. The
    # native synth's pre-attack flourish does not set the finger-bend clock.
    # Qualify only an initial cue with independently supported note controls;
    # later cues, outgoing/targeted slides and overlapping controllers remain
    # separate compositions requiring their own playback qualification.
    initial_slide = (first.slide_in in {'up', 'down'} and 0 < first.fret < 127
                     and not overlaps and all(
                         not (n.slide or (i and n.slide_in) or n.whammy or n.attack_offset
                              or n.hopo or n.trill or n.pick_scrape
                              or n.effects.get('__beat_vibrato')
                              or (n.effects.get('harmonic_target') and not steady_harmonic)
                              or any(n.effects.get(k) for k in ('hm', 'hp', 'hn',
                                                               'mt', 'lr', 'pm', 'tr', '__hopo_origin')))
                         and (not n.effects.get('vb') or n.effects.get('__finger_vibrato') in {'slight', 'wide'})
                         for i, (n, *_) in enumerate(segments)))
    if initial_slide:
        reason = None
        evidence['initialSlideIn'] = {'sourceId': first.source_id, 'direction': first.slide_in,
                                     'start': at(attack), 'attackTiming': 'authored-note',
                                     'bendTiming': 'authored-tie'}
    if overlaps:
        # The native worker can keep the initial controller alive across ties.
        # Only a settled tail is safe to hand off: it emits no more pitch
        # changes before its terminal reset at note-off. Do not copy competing
        # synth updates or silently rescale either written gesture.
        other_expression = any(
            n.hopo or n.trill or n.pick_scrape
            or any(n.effects.get(k) for k in ('hm', 'hp', 'hn', 'harmonic_target',
                                             'mt', 'lr', 'pm', 'tr', '__hopo_origin'))
            for n, *_ in segments)
        handoffs = []
        for index, handoff in overlaps:
            old, left, right = intervals[index]
            controls = [(left + (right-left)*p, v) for p, v in old.bends]
            last_change = controls[0][0]
            for before, after in zip(controls, controls[1:]):
                if before[1] != after[1]:
                    last_change = after[0]
            settled = last_change <= handoff and right == stop
            handoffs.append({'sourceId': old.source_id,
                             'nextSourceId': intervals[index + 1][0].source_id,
                             'start': at(handoff), 'end': at(right),
                             'classification': 'settled-tail' if settled else 'changing-tail'})
        settled_handoffs = all(h['classification'] == 'settled-tail' for h in handoffs)
        has_vibrato = any(n.effects.get('vb') for n, *_ in segments)
        # Explicit note vibrato has its own verified control timeline. It does
        # not displace the source bend clock or create another finger bend.
        qualified_vibrato = (has_vibrato and settled_handoffs and not reason and not other_expression
                            and all(not n.effects.get('__beat_vibrato')
                                    and (not n.effects.get('vb') or n.effects.get('__finger_vibrato') in {'slight', 'wide'})
                                    for n, *_ in segments))
        other_expression = other_expression or (has_vibrato and not qualified_vibrato)
        evidence['overlap'] = {'classification': 'other-expression' if reason or other_expression
                              else 'clear-handoff' if settled_handoffs
                              else 'conflicting-controls', 'handoffs': handoffs}
        if qualified_vibrato:
            evidence['overlap']['vibratoTiming'] = 'independent-note-controls'
        if evidence['overlap']['classification'] != 'clear-handoff':
            reason = reason or ('overlap-with-other-expression' if other_expression else 'overlapping-bend-controls')
    terminal = None if overlaps else _terminal_slide_out(segments, intervals, attack, stop, at, terminal_context)
    if terminal is not None:
        reason = None
        evidence['terminalSlideOut'] = terminal
    if reason:
        return {**evidence, 'status': 'deferred', 'reason': reason,
                'rule': 'retained-segment-timing', 'curve': deepcopy(output.get('bnv', []))}

    curve = []
    for i, (note, start, right) in enumerate(intervals):
        if start >= stop:
            continue
        left, right_edge = max(start, attack), min(right, stop)
        if i + 1 < len(intervals):
            right_edge = min(right_edge, intervals[i + 1][1])
        controls = [(start + (right - start) * p, v) for p, v in note.bends]

        def value_at(q):
            before = controls[0]
            for after in controls[1:]:
                if after[0] > q:
                    return before[1] if q <= before[0] else before[1] + (after[1] - before[1]) * float((q-before[0])/(after[0]-before[0]))
                before = after
            return before[1]

        if not curve and left > attack:
            curve.append({'t': 0., 'v': 0.})
        if curve and curve[-1]['t'] < at(left) - output['t']:
            curve.append({'t': at(left) - output['t'], 'v': curve[-1]['v']})
        # Preserve both sides of an authored discontinuity; extra tempo knots
        # interpolate in quarter-note time before converting to seconds.
        knots = [(q, v) for q, v in controls if left <= q <= right_edge]
        positions = {left, right_edge, *[q for q in boundaries if left < q < right_edge]}
        existing = {q for q, _ in knots}
        knots += [(q, value_at(q)) for q in positions - existing]
        for q, value in sorted(knots, key=lambda pair: pair[0]):
            point = {'t': at(q) - output['t'], 'v': value}
            if not curve or point != curve[-1]:
                curve.append(point)
    output['bnv'] = curve
    output['bn'] = max((p['v'] for p in curve), key=abs)
    return {**evidence, 'status': 'resolved',
            'rule': ('bend-with-slide-out' if terminal.get('bendPhase') == 'changing' else 'bend-hold-slide-out')
                    if terminal else 'settled-bend-handoff' if overlaps else 'tie-resolved-finger-bend',
            'curve': deepcopy(curve)}


def archive_evidence(performance, source_path):
    gestures = performance['fingerBendTimingEvidence']
    version = (9 if any(e.get('terminalSlideOut', {}).get('bendTiming') == 'authored-segment' for e in gestures)
               else 8 if any(e.get('initialSlideIn') for e in gestures)
               else 7 if any(e.get('overlap', {}).get('vibratoTiming') == 'independent-note-controls' for e in gestures)
               else 6 if any(e.get('terminalSlideOut', {}).get('endTiming') == 'authored-tie' for e in gestures)
               else 5 if any(e.get('terminalSlideOut', {}).get('attackTiming') == 'authored-chord' for e in gestures)
               else 4 if any(e.get('rule') == 'bend-with-slide-out' for e in gestures) else 3)
    return {'version': version, 'policy': f'songsterr-finger-bend-timing-v{version}',
            'timeDomain': 'score_seconds',
            'sourceSha256': hashlib.sha256(source_path.read_bytes()).hexdigest(),
            'gestures': deepcopy(performance['fingerBendTimingEvidence'])}


def report_findings(performance, report):
    from .compatibility import add_finding
    for row in performance.get('fingerBendTimingEvidence', []):
        if row['status'] == 'deferred':
            message = ('Overlapping bend instructions have conflicting playback timing. '
                       'The written bend segments are retained instead of reproducing competing pitch updates. '
                       'Exact Songsterr playback matching is not claimed; the complete source is preserved.'
                       if row['reason'] == 'overlapping-bend-controls' else
                       'This bend combines another expression or displaced attack with its timing. '
                       'Its written segment timing is retained, but matching Songsterr playback has not yet been verified. '
                       'The complete source is preserved.')
            add_finding(report, feature='note.bend_timing', category='conversion_check',
                        impact='display_or_expression', location=row['location'] + f"@visit{row['occurrence']}",
                        trackId=row['trackId'], value={'reason': row['reason']},
                        message=message)

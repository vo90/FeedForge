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


def _boundary_slides(segments, overlaps, attack, stop, context):
    """Qualify independent approach/departure cues on one held fret.

    This composition has no overlapping bend controllers. Other expressions
    keep their own qualification boundaries; a slide cue cannot clear them.
    """
    first = segments[0][0]
    last, start, end, _ = segments[-1]
    return (len(segments) > 1 and not overlaps and 0 < first.fret < 127
            and first.slide_in in {'up', 'down'} and last.tie
            and last.slide in {'out_up', 'out_down'} and end == stop
            and attack < start < stop
            and not set(context) - {'simultaneous-attack', 'following-slide-in'}
            and all(n.fret == first.fret and n.string == first.string
                    and not (n.slide and i != len(segments)-1 or n.slide_in and i != 0
                             or n.whammy or n.attack_offset or n.staccato or n.hopo
                             or n.trill or n.pick_scrape or n.effects.get('__beat_vibrato')
                             or (n.effects.get('vb') and n.effects.get('__finger_vibrato') not in {'slight', 'wide'})
                             or any(n.effects.get(k) for k in ('hm', 'hp', 'hn', 'harmonic_target',
                                                              'mt', 'lr', 'pm', 'tr', '__hopo_origin')))
                    for i, (n, *_) in enumerate(segments)))


def _terminal_artificial(segments, intervals, overlaps, attack, stop, output):
    """Prove a fixed artificial target plus a final direction cue as a whole.

    An omitted target on a tie continues the initial harmonic. A different
    target, delayed contact or still-changing controller cannot qualify here.
    """
    first = segments[0][0]
    target = first.effects.get('harmonic_target')
    if (not isinstance(target, dict) or target.get('kind') != 'artificial'
            or target.get('policy') != 'harmonic' or output.get('harmonic_target') != target
            or output.get('harmonic_changes') or len(segments) < 2
            or not 0 < first.fret < 127 or segments[0][1] != attack
            or segments[-1][2] != stop or segments[-1][0].slide not in {'out_up', 'out_down'}):
        return None
    for i, (n, begin, _, _) in enumerate(segments):
        if ((n.fret, n.string, n.voice_id) != (first.fret, first.string, first.voice_id)
                or (i and (not n.tie or begin != segments[i-1][2]))
                or n.effects.get('harmonic_target') not in (None, target)
                or n.slide_in or (n.slide and i != len(segments)-1)
                or n.whammy or n.attack_offset or n.staccato or n.hopo or n.trill or n.pick_scrape
                or any(n.effects.get(k) for k in ('hm', 'hp', 'hn', 'mt', 'lr', 'pm', 'tr', '__hopo_origin'))
                or ((n.effects.get('vb') or n.effects.get('__beat_vibrato'))
                    and n.effects.get('__finger_vibrato') not in {'slight', 'wide'})):
            return None
    for index, handoff in overlaps:
        old, left, right = intervals[index]
        controls = [(left + (right-left)*p, v) for p, v in old.bends]
        last_change = controls[0][0]
        for a, b in zip(controls, controls[1:]):
            if a[1] != b[1]:
                last_change = b[0]
        if last_change > handoff or right != stop:
            return None
    return target


def _terminal_beat_vibrato(segments, overlaps, attack, stop, at, *, artificial=None):
    """Written beat vibrato does not set this independent finger-bend clock.

    Qualify a continuous ordinary note or the separately proved artificial
    harmonic composition. Preserve vibrato marks and their separate finding.
    """
    first = segments[0][0]
    if ((overlaps and not artificial) or len(segments) < 2 or not 0 < first.fret < 127
            or segments[0][1] != attack or segments[-1][2] != stop
            or segments[-1][0].slide not in {'out_up', 'out_down'}):
        return None
    written = []
    for i, (n, start, end, visit) in enumerate(segments):
        if ((n.fret, n.string, n.voice_id) != (first.fret, first.string, first.voice_id)
                or (i and (not n.tie or start != segments[i-1][2]))
                or n.slide_in or (n.slide and i != len(segments)-1)
                or n.whammy or n.attack_offset or n.staccato or n.hopo or n.trill or n.pick_scrape
                or (n.effects.get('harmonic_target') and not artificial)
                or any(n.effects.get(k) for k in ('hm', 'hp', 'hn',
                                                 'mt', 'lr', 'pm', 'tr', '__hopo_origin'))
                or ((n.effects.get('vb') or n.effects.get('__beat_vibrato'))
                    and n.effects.get('__finger_vibrato') not in {'slight', 'wide'})):
            return None
        if n.effects.get('__beat_vibrato'):
            written.append({'sourceId': n.source_id, 'occurrence': visit + 1,
                            'start': at(start), 'end': at(end),
                            'intensity': n.effects['__finger_vibrato']})
    return {'policy': 'independent-written-instruction', 'segments': written} if written else None


def _independent_bar_curve(segments, overlaps, attack, stop, output, at, *, terminal_slide=False):
    """An explicit bar controller does not set the finger-bend clock.

    Preserve the bar's own curve/held resets. Qualify only one continuous
    ordinary held fret with noncompeting finger controls, never a summed pitch.
    """
    first = segments[0][0]
    if (overlaps or not 0 < first.fret < 127 or segments[0][1] != attack
            or segments[-1][2] != stop
            or not any(n.whammy and n.whammy['curve'] for n, *_ in segments)
            or not any(s['curve'] for s in output.get('whammy', {}).get('segments', []))):
        return None
    last, tail_start, _, _ = segments[-1]
    if terminal_slide and not (len(segments) > 1 and last.tie
            and last.slide in {'out_up', 'out_down'} and attack < tail_start < stop):
        return None
    controls = []
    for i, (n, start, end, visit) in enumerate(segments):
        if ((n.fret, n.string, n.voice_id) != (first.fret, first.string, first.voice_id)
                or (i and (not n.tie or start != segments[i-1][2]))
                or (n.slide and not (terminal_slide and i == len(segments)-1))
                or n.slide_in or n.attack_offset or n.staccato or n.hopo
                or n.trill or n.pick_scrape or n.effects.get('__beat_vibrato')
                or any(n.effects.get(k) for k in ('hm', 'hp', 'hn', 'harmonic_target',
                                                 'mt', 'lr', 'pm', 'tr', '__hopo_origin'))
                or (n.effects.get('vb') and n.effects.get('__finger_vibrato') not in {'slight', 'wide'})):
            return None
        if n.whammy:
            controls.append({'sourceId': n.source_id, 'occurrence': visit + 1,
                             'start': at(start), 'end': at(end),
                             'curve': [{'position': str(p), 'value': v} for p, v in n.whammy['curve']],
                             'vibrato': n.whammy['vibrato']})
    return {'policy': 'independent-explicit-control', 'segments': controls}


def _terminal_slide_out(segments, intervals, attack, stop, at, context, *, boundary_slides=False, continued_pinch=None, beat_vibrato=None, bar_curve=None, artificial=None):
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
        if (n.slide and i != len(segments)-1 or (n.slide_in and not (boundary_slides and i == 0))
                or (n.whammy and not bar_curve) or n.attack_offset
                or n.hopo or n.trill or n.pick_scrape or (n.effects.get('__beat_vibrato') and not beat_vibrato)
                or ((last.bends or continued_pinch) and n.effects.get('vb')
                    and n.effects.get('__finger_vibrato') not in {'slight', 'wide'})
                or (not (continued_pinch or artificial) and (n.effects.get('hp') or n.effects.get('harmonic_target')))
                or any(n.effects.get(k) for k in ('hm', 'hn', 'mt', 'lr', 'pm', 'tr', '__hopo_origin'))):
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
            **({'beatVibrato': beat_vibrato} if beat_vibrato else {}),
            **({'continuedPinchHarmonic': {'initialTarget': deepcopy(continued_pinch),
                                         'policy': 'initial-target-continued'}} if continued_pinch else {}),
            **({'continuedArtificialHarmonic': {'initialTarget': deepcopy(artificial),
                                              'policy': 'fixed-or-omitted-tied-target'}} if artificial else {}),
            **({'bendTiming': 'authored-segment'} if last.bends else {}),
            **({'bendPhase': 'changing', 'pitchPolicy': 'independent-source-bend'} if changing else {}),
            **({'attackTiming': 'authored-chord'} if changing and 'simultaneous-attack' in context else {}),
            **({'endTiming': 'authored-tie'} if changing and 'following-slide-in' in context else {})}


def _outgoing_legato(output, articulation, following, at):
    """Prove one terminal legato link without changing the preceding bend.

    `following` comes from resolved source links, not fret/time proximity. Only
    plain, continuous fretted ties and a plain adjacent destination are covered.
    Incoming/intermediate legato and other pitch gestures remain unqualified.
    """
    if following is None:
        return None
    destination, target = following
    segments = articulation['bend_segments']
    first = segments[0][0]
    last, _, end, occurrence = segments[-1]
    note, start, _, visit = target['bend_segments'][0]
    if (len(segments) < 2 or not last.tie or not last.effects.get('__hopo_origin')
            or not output.get('ln') or output.get('ho') or output.get('po')
            or end != articulation['end'] or start != end
            or target['start'] != start or not 0 < first.fret < 127
            or not 0 <= note.fret < 127 or note.fret == first.fret
            or (note.string, note.voice_id) != (first.string, first.voice_id)):
        return None
    for i, (n, begin, _, _) in enumerate(segments):
        if (n.fret != first.fret or n.string != first.string or n.voice_id != first.voice_id
                or (i and (not n.tie or begin != segments[i-1][2]))
                or (i != len(segments)-1 and n.effects.get('__hopo_origin'))
                or n.hopo or n.slide or n.slide_in or n.whammy or n.attack_offset
                or n.staccato or n.trill or n.pick_scrape or n.effects.get('__beat_vibrato')
                or any(n.effects.get(k) for k in ('hm', 'hp', 'hn', 'harmonic_target',
                                                 'mt', 'lr', 'pm', 'tr', 'vb'))):
            return None
    if (note.tie or note.bends or note.slide or note.slide_in or note.whammy or note.attack_offset
            or note.staccato or note.trill or note.pick_scrape or note.effects.get('__beat_vibrato')
            or any(note.effects.get(k) for k in ('hm', 'hp', 'hn', 'harmonic_target',
                                                'mt', 'lr', 'pm', 'tr', 'vb', '__hopo_origin'))):
        return None
    technique = 'ho' if note.fret > first.fret else 'po'
    if destination.get(technique) is not True:
        return None
    return {'sourceId': last.source_id, 'occurrence': occurrence + 1,
            'destinationSourceId': note.source_id, 'destinationOccurrence': visit + 1,
            'start': at(start), 'fret': note.fret, 'technique': technique,
            'policy': 'independent-outgoing-link'}


def finish(output, articulation, track_id, at, tempo_positions, terminal_context=(), *, following_hopo=None):
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
    # Qualitative bar vibrato is a separate modulation control, not an
    # authored pitch curve. Qualify only non-overlapping finger bends with
    # no displaced attack or slide. Explicit curves qualify separately below.
    bar_vibrato = (not overlaps and any(n.whammy for n, *_ in segments)
                   and all(not (n.slide or n.slide_in or n.attack_offset)
                           and (not n.whammy or (not n.whammy['curve']
                                and n.whammy['vibrato'] in {'slight', 'wide'}))
                           for n, *_ in segments)
                   and bool(output.get('whammy', {}).get('segments'))
                   and all(not s['curve'] for s in output['whammy']['segments']))
    if bar_vibrato:
        reason = None
        evidence['barVibrato'] = {
            'policy': 'independent-qualitative-control',
            'segments': [{'sourceId': n.source_id, 'occurrence': visit + 1,
                          'start': at(start), 'end': at(end), 'vibrato': n.whammy['vibrato']}
                         for n, start, end, visit in segments if n.whammy]}
    bar_curve = _independent_bar_curve(segments, overlaps, attack, stop, output, at)
    if bar_curve:
        reason = None
        evidence['barCurve'] = bar_curve
    # A separately retained terminal slide interval does not set the bend
    # clock. Never clear the guard for competing controllers or other gestures.
    targeted = output.get('slide_interval')
    if (targeted and not overlaps and segments[-1][0].slide in {'shift', 'legato'}
            and all(not (n.whammy or n.attack_offset or n.staccato or n.hopo or n.trill
                         or n.pick_scrape or n.effects.get('__beat_vibrato')
                         or (i and n.slide_in) or (n.slide and i != len(segments)-1)
                         or any(n.effects.get(k) for k in ('hm','hp','hn','harmonic_target','mt','lr','pm','tr','__hopo_origin'))
                         or (n.effects.get('vb') and n.effects.get('__finger_vibrato') not in {'slight','wide'}))
                    for i,(n,*_) in enumerate(segments))):
        reason = None
        last, left, right, visit = segments[-1]
        evidence['targetedSlide'] = {'sourceId': last.source_id, 'occurrence': visit+1,
            'start': at(left), 'end': at(right), 'fret': output['sl'], 'kind': last.slide,
            'policy': 'independent-authored-interval'}
    harmonic = first.effects.get('harmonic_target')
    steady_harmonic = (isinstance(harmonic, dict) and harmonic.get('kind') == 'artificial'
                       and harmonic.get('policy') == 'harmonic'
                       and all(n.effects.get('harmonic_target') == harmonic for n, *_ in segments))
    # Tied pinch markings use the already established initial-target policy.
    # Missing/later pinch targets do not repick the note or replace its target.
    # Their authored differences remain in the separate harmonic-tie evidence.
    continued_pinch = (isinstance(harmonic, dict) and harmonic.get('kind') == 'pinch'
                       and harmonic.get('policy') == 'harmonic'
                       and output.get('harmonic_target') == harmonic and output.get('hp') is True
                       and not output.get('harmonic_changes')
                       and all((not i or n.tie) and (
                           (not n.effects.get('harmonic_target') and not n.effects.get('hp'))
                           or (n.effects.get('harmonic_target', {}).get('kind') == 'pinch'
                               and n.effects['harmonic_target'].get('policy') == 'harmonic'
                               and n.effects.get('hp') is True))
                           for i, (n, *_) in enumerate(segments)))
    boundary_slides = _boundary_slides(segments, overlaps, attack, stop, terminal_context)
    # An approach cue has no authored target interval inside this note. The
    # native synth's pre-attack flourish does not set the finger-bend clock.
    # Qualify only an initial cue with independently supported note controls;
    # The separately qualified boundary composition permits only the terminal
    # direction cue. Later cues, targeted slides and overlap stay guarded.
    initial_slide = (first.slide_in in {'up', 'down'} and 0 < first.fret < 127
                     and not overlaps and all(
                         not ((n.slide and not (boundary_slides and i == len(segments)-1))
                              or (i and n.slide_in) or n.whammy or n.attack_offset
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
    # Establish slide eligibility independently. A valid flourish alone must
    # never clear a conflicting bend or another unqualified expression.
    # A continued pinch target is independent of this bend clock. Qualify the
    # entire held note before allowing its terminal cue through; do not extend
    # this rule to overlapping controls or timed harmonic contact.
    pinch_slide = (continued_pinch and not overlaps and 0 < first.fret < 127
                   and segments[0][1] == attack and segments[-1][2] == stop
                   and all((n.fret, n.string, n.voice_id) == (first.fret, first.string, first.voice_id)
                           and (not i or (n.tie and start == segments[i-1][2]))
                           for i, (n, start, _, _) in enumerate(segments)))
    terminal_bar = _independent_bar_curve(segments, overlaps, attack, stop, output, at, terminal_slide=True)
    artificial_slide = _terminal_artificial(segments, intervals, overlaps, attack, stop, output)
    terminal_candidate = _terminal_slide_out(segments, intervals, attack, stop, at, terminal_context,
                                             boundary_slides=boundary_slides,
                                             continued_pinch=harmonic if pinch_slide else None,
                                             beat_vibrato=_terminal_beat_vibrato(segments, overlaps, attack, stop, at, artificial=artificial_slide),
                                             bar_curve=terminal_bar, artificial=artificial_slide)
    if artificial_slide and terminal_candidate:
        reason = None  # The complete composition, including every handoff, qualified.
    terminal_handoff = False
    if overlaps:
        # The native worker can keep the initial controller alive across ties.
        # Only a settled tail is safe to hand off: it emits no more pitch
        # changes before its terminal reset at note-off. Do not copy competing
        # synth updates or silently rescale either written gesture.
        outgoing = _outgoing_legato(output, articulation, following_hopo, at)
        other_expression = any(
            n.hopo or n.trill or n.pick_scrape
            or (n.effects.get('hp') and not continued_pinch)
            or (n.effects.get('__hopo_origin') and outgoing is None)
            or any(n.effects.get(k) for k in ('hm', 'hn',
                                             'mt', 'lr', 'pm', 'tr'))
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
        # Legato eligibility alone must not reclassify an unresolved overlap.
        other_expression = other_expression or (outgoing is not None and not settled_handoffs)
        # A fixed artificial target or established pinch continuation does
        # not change the source bend clock. Qualify the whole tie; conflicting
        # controllers and other harmonic interpretations stay guarded.
        qualified_harmonic = ((steady_harmonic or continued_pinch or artificial_slide) and settled_handoffs and not reason
                             and not other_expression and 0 < first.fret < 127
                             and all(n.fret == first.fret and n.string == first.string
                                     and (not n.effects.get('__beat_vibrato') or artificial_slide)
                                     and (not n.effects.get('vb') or n.effects.get('__finger_vibrato') in {'slight', 'wide'})
                                     for n, *_ in segments))
        other_expression = other_expression or (any(n.effects.get('harmonic_target') for n, *_ in segments)
                                                and not qualified_harmonic)
        has_vibrato = any(n.effects.get('vb') for n, *_ in segments)
        terminal_handoff = (terminal_candidate is not None and settled_handoffs and not other_expression
                            and all((not n.effects.get('__beat_vibrato') or artificial_slide)
                                    and (not n.effects.get('vb') or n.effects.get('__finger_vibrato') in {'slight', 'wide'})
                                    for n, *_ in segments))
        if terminal_handoff:
            reason = None  # Only this independently qualified slide was mixed.
        # Explicit note vibrato has its own verified control timeline. It does
        # not displace the source bend clock or create another finger bend.
        qualified_vibrato = (has_vibrato and settled_handoffs and not reason and not other_expression
                            and all((not n.effects.get('__beat_vibrato') or artificial_slide)
                                    and (not n.effects.get('vb') or n.effects.get('__finger_vibrato') in {'slight', 'wide'})
                                    for n, *_ in segments))
        other_expression = other_expression or (has_vibrato and not qualified_vibrato)
        evidence['overlap'] = {'classification': 'other-expression' if reason or other_expression
                              else 'clear-handoff' if settled_handoffs
                              else 'conflicting-controls', 'handoffs': handoffs}
        if outgoing is not None and evidence['overlap']['classification'] == 'clear-handoff':
            evidence['overlap']['outgoingLegato'] = outgoing
        if qualified_harmonic:
            if artificial_slide:
                evidence['overlap']['continuedArtificialHarmonic'] = {
                    'initialTarget': deepcopy(harmonic), 'policy': 'fixed-or-omitted-tied-target'}
            elif continued_pinch:
                evidence['overlap']['continuedPinchHarmonic'] = {
                    'initialTarget': deepcopy(harmonic), 'policy': 'initial-target-continued'}
            else:
                evidence['overlap']['fixedHarmonic'] = deepcopy(harmonic)
        if qualified_vibrato:
            evidence['overlap']['vibratoTiming'] = 'independent-note-controls'
        if terminal_handoff:
            evidence['overlap']['slideOutTiming'] = 'independent-terminal-cue'
        if evidence['overlap']['classification'] != 'clear-handoff':
            reason = reason or ('overlap-with-other-expression' if other_expression else 'overlapping-bend-controls')
    terminal = terminal_candidate if not overlaps or terminal_handoff else None
    if terminal is not None:
        reason = None
        evidence['terminalSlideOut'] = terminal
        # Both constituents must qualify before either guard can be relaxed.
        # Preserve the bar's authored interval, independently of the synth's
        # shortened slide sample and the finger-bend scoring pitch.
        if terminal_bar:
            evidence['barCurve'] = terminal_bar
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
    version = (21 if any(e.get('terminalSlideOut', {}).get('continuedArtificialHarmonic') for e in gestures)
               else 20 if any(e.get('barCurve') and e.get('terminalSlideOut') for e in gestures)
               else 19 if any(e.get('targetedSlide') for e in gestures)
               else 18 if any(e.get('barCurve') for e in gestures)
               else 17 if any(e.get('terminalSlideOut', {}).get('beatVibrato') for e in gestures)
               else 16 if any(e.get('terminalSlideOut', {}).get('continuedPinchHarmonic') for e in gestures)
               else 15 if any(e.get('barVibrato') for e in gestures)
               else 14 if any(e.get('overlap', {}).get('outgoingLegato') for e in gestures)
               else 13 if any(e.get('overlap', {}).get('continuedPinchHarmonic') for e in gestures)
               else 12 if any(e.get('overlap', {}).get('fixedHarmonic') for e in gestures)
               else 11 if any(e.get('initialSlideIn') and e.get('terminalSlideOut') for e in gestures)
               else 10 if any(e.get('overlap', {}).get('slideOutTiming') == 'independent-terminal-cue' for e in gestures)
               else 9 if any(e.get('terminalSlideOut', {}).get('bendTiming') == 'authored-segment' for e in gestures)
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

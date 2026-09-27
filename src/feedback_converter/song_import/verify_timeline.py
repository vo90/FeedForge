"""Independent rational performance evaluator used only by verification."""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from fractions import Fraction as F
import math

from .verify_source import Source, fraction, unsupported

MAX_EVENTS = 500_000


def visits(source: Source):
    """Expand repeat intervals recursively, independently of the import walker.

    Partition each repeat into a common prefix and pass-owned ending spans.
    This checker does not use the producer's masks or iterative stack walker.
    """
    openings, intervals = [], {}
    for i, bar in enumerate(source.bars):
        if bar.repeat_start:
            openings.append(i)
        if bar.repeat_count:
            if not 2 <= bar.repeat_count <= 32:
                raise ValueError(f"measures/{i}: invalid repeat count")
            start = openings.pop() if openings else 0
            if start in intervals:
                unsupported(f"measures/{i}", "Overlapping repeat intervals are not independently verified.")
            intervals[start] = (i, bar.repeat_count)
    if openings:
        unsupported(f"measures/{openings[-1]}", "Unclosed source repeat has no definite performance order.")
    if not intervals and any(bar.endings for bar in source.bars):
        unsupported("measures", "Alternate endings without a repeat have no independently verified performance order.")
    endings, claimed = {}, set()
    for lo, (hi, count) in intervals.items():
        starts = [j for j in range(lo, hi + 1) if source.bars[j].endings]
        tail = hi + 1
        if tail < len(source.bars) and source.bars[tail].endings and not source.bars[tail].repeat_start:
            starts.append(tail)
        if not starts:
            continue
        if starts[0] == lo or (not source.bars[lo].repeat_start and starts[0] < hi):
            unsupported(f'measures/{lo}', 'The common repeat prefix is not independently established.')
        if any(a != lo and a <= hi and b >= lo for a, (b, _) in intervals.items()):
            unsupported(f'measures/{lo}', 'Nested alternate-ending ownership is not independently verified.')
        spans, seen = [], set()
        for j, at in enumerate(starts):
            turns = source.bars[at].endings
            if any(type(t) is not int or t < 1 or t > count for t in turns):
                raise ValueError(f'measures/{at}: invalid ending pass')
            if at == tail and turns != {count}:
                unsupported(f'measures/{at}', 'Only the final ending can follow the close.')
            if seen & turns:
                unsupported(f'measures/{at}', 'Disjoint regions claim the same repeat pass.')
            seen.update(turns)
            spans.append((at, min(hi + 1, starts[j + 1] if j + 1 < len(starts) else hi + 1), turns))
            claimed.add(at)
        if seen != set(range(1, count + 1)):
            unsupported(f'measures/{lo}', 'Alternate endings omit a repeat pass.')
        endings[lo] = spans
    if any(b.endings and i not in claimed for i, b in enumerate(source.bars)):
        unsupported('measures', 'An alternate ending has no definite owning repeat.')
    result = []

    def segment(lo, hi, turn=1, owner=None):
        i = lo
        while i <= hi:
            if i in intervals and i != owner:
                end, count = intervals[i]
                if end > hi:
                    unsupported(f"measures/{i}", "Crossing source repeats are not independently verified.")
                for repeat_turn in range(1, count + 1):
                    segment(i, end, repeat_turn, i)
                i = end + 1
                continue
            bar = source.bars[i]
            owned = next((turns for a, b, turns in endings.get(owner, []) if a <= i < b), None)
            if owned is None or turn in owned:
                result.append(i)
                if len(result) > 20_000:
                    raise ValueError("source: performed measure limit exceeded")
            i += 1

    segment(0, len(source.bars) - 1)
    return result


class Clock:
    def __init__(self, source, order):
        self.measure_starts = []
        self.quarters = F(0)
        tempo_changes = {F(0): F(120)}
        # Written tempo inheritance is needed when execution jumps backwards.
        inherited, tempo = [], F(120)
        for bar in source.bars:
            inherited.append(tempo)
            for _, tempo in sorted(bar.tempos.items()):
                pass
        previous = -1
        for index in order:
            bar = source.bars[index]
            self.measure_starts.append(self.quarters)
            if index != previous + 1:
                tempo_changes[self.quarters] = inherited[index]
            for q, bpm in bar.tempos.items():
                if bpm <= 0 or q < 0 or q >= bar.length:
                    raise ValueError(f"measures/{index}: invalid tempo")
                tempo_changes[self.quarters + q] = bpm
            self.quarters += bar.length
            previous = index
        self.positions = sorted(tempo_changes)
        self.bpms = [tempo_changes[q] for q in self.positions]
        self.seconds = [F(0)]
        for i in range(1, len(self.positions)):
            self.seconds.append(self.seconds[-1] + (self.positions[i] - self.positions[i - 1]) * 60 / self.bpms[i - 1])

    def at(self, q):
        index = max(0, bisect_right(self.positions, q) - 1)
        return self.seconds[index] + (q - self.positions[index]) * 60 / self.bpms[index]


class RecordingMap:
    """Separate interpolation implementation, using decimal-rational inputs.

    Output seconds are rounded once to six decimal places, Python round's
    half-even convention. Comparison admits one microsecond for existing
    converter floating-point arithmetic at exact half-microsecond boundaries.
    """
    def __init__(self, alignment):
        self.piecewise = alignment.get("mapping") == "piecewise-linear"
        self.anchors = []
        if self.piecewise:
            self.anchors = [(fraction(a["score"]), fraction(a["audio"])) for a in alignment["anchors"]]
            if len(self.anchors) < 2 or any(s1 <= s0 or a1 <= a0 for (s0, a0), (s1, a1) in zip(self.anchors, self.anchors[1:])):
                raise ValueError("alignment: anchors must be strictly increasing")
            self.scores = [a[0] for a in self.anchors]
        else:
            self.offset = fraction(alignment["offset"])
            self.scale = fraction(alignment["scale"])
            if self.scale <= 0:
                raise ValueError("alignment: nonpositive clock scale")

    def segment(self, seconds):
        if seconds < self.scores[0] - F(1, 10_000_000) or seconds > self.scores[-1] + F(1, 10_000_000):
            raise ValueError("alignment: source event is outside the supplied timing map")
        # Exact rational bar positions and their captured floating-point
        # spelling denote the same boundary; select the outgoing segment.
        return min(len(self.anchors) - 2, max(0, bisect_right(self.scores, seconds + F(1, 1_000_000_000)) - 1))

    def raw(self, seconds):
        if not self.piecewise:
            return self.offset + seconds * self.scale
        index = self.segment(seconds)
        (s0, a0), (s1, a1) = self.anchors[index:index + 2]
        return a0 + (seconds - s0) * (a1 - a0) / (s1 - s0)

    def at(self, seconds):
        return round(float(self.raw(seconds)), 6)

    def ratio(self, seconds):
        if not self.piecewise:
            return self.scale
        index = self.segment(seconds)
        (s0, a0), (s1, a1) = self.anchors[index:index + 2]
        return (a1 - a0) / (s1 - s0)


def expected(source, alignment):
    from .verify_voices import project
    result = _expected(source, alignment)
    return project(source, alignment, result, _expected) if source.format == 'songsterr' else result


def _expected(source, alignment):
    order = visits(source)
    clock, recording = Clock(source, order), RecordingMap(alignment)
    result = {"parts": [], "beats": [], "sections": [], "time_signatures": [], "tempos": [], "order": order,
              'harmonic_ties': [], 'tied_mutes': [], 'muted_tie_identities': [], 'staccato_bends': [], 'muted_slides': [], 'trills': [], 'strums': [],
              "raw_notes": sum(len(bar) for p in source.parts for bar in p.bars), "tie_segments": 0}
    measure_facts = []
    inherited_tempos, inherited = [], F(120)
    for bar in source.bars:
        inherited_tempos.append(bar.tempos.get(F(0), inherited))
        for _, inherited in sorted(bar.tempos.items()):
            pass
    previous_signature = None
    visible_downbeats = 0
    for occurrence, index in enumerate(order):
        bar, origin = source.bars[index], clock.measure_starts[occurrence]
        start_time = recording.at(clock.at(origin))
        measure_facts.append({"idx": occurrence + 1, "source_measure": index + 1, "t": start_time,
                              "ts": list(bar.signature), "written_tempo": float(inherited_tempos[index]),
                              "tempo": float(inherited_tempos[index] / recording.ratio(clock.at(origin))),
                              "duration_seconds": round(recording.at(clock.at(origin + bar.length)) - start_time, 6)})
        if bar.pickup:
            measure_facts[-1]['pickup'] = True
        q = bar.length % F(4, bar.signature[1]) if bar.pickup else F(0)
        while q < bar.length:
            time = recording.at(clock.at(origin + q))
            if time >= 0:
                if q == 0 and not bar.pickup:
                    visible_downbeats += 1
                result["beats"].append({"time": time, "measure": visible_downbeats if q == 0 and not bar.pickup else -1})
            q += F(4, bar.signature[1])
        if bar.section:
            time = recording.at(clock.at(origin))
            if time >= 0:
                result["sections"].append({"time": time, "name": bar.section})
        if bar.signature != previous_signature:
            meter = {"time": max(0., recording.at(clock.at(origin))), "ts": list(bar.signature)}
            # If several silent bars precede audio, only the last meter at its
            # origin is active. Derive this from source bars independently of
            # the producer's exported-timeline filtering.
            if result["time_signatures"] and result["time_signatures"][-1]["time"] == meter["time"]:
                result["time_signatures"][-1] = meter
            else:
                result["time_signatures"].append(meter)
            previous_signature = bar.signature
    tempo_seconds = set(clock.seconds)
    if recording.piecewise:
        tempo_seconds.update(recording.scores[:-1])
    for time in sorted(tempo_seconds):
        idx = max(0, bisect_right(clock.seconds, time + F(1, 10_000_000)) - 1)
        bpm = float(clock.bpms[idx] / recording.ratio(time))
        entry = {"time": max(0., recording.at(time)), "bpm": bpm}
        if result["tempos"] and result["tempos"][-1]["time"] == entry["time"]:
            result["tempos"][-1] = entry
        elif not result["tempos"] or not math.isclose(result["tempos"][-1]["bpm"], bpm, rel_tol=1e-12):
            result["tempos"].append(entry)
    for part in source.parts:
        borrowing_bars = {i for i, beats in enumerate(part.beats) if any(b['q'] < 0 for b in beats)}
        for visit, index in enumerate(order):
            if ((index in borrowing_bars and (visit == 0 or order[visit - 1] != index - 1))
                    or (index + 1 in borrowing_bars and (visit + 1 == len(order) or order[visit + 1] != index + 1))):
                unsupported(f'tracks/{part.id}/measures/{index}', 'Cross-bar grace borrows across a repeat jump; traversal is not verified.')
        rests = {}
        for visit, bi in enumerate(order):
            origin = clock.measure_starts[visit]
            for beat in part.beats[bi]:
                if beat['rest']:
                    rests.setdefault(beat['voice'], []).append((origin + beat['q'], origin + beat['q'] + beat['length']))
        rest_indices = {}
        for voice, spans in rests.items():
            spans.sort()
            starts = [p for p, _ in spans]
            ends, last = [], F(-1)
            for _, q in spans:
                last = max(last, q); ends.append(last)
            rest_indices[voice] = (starts, ends)
        notes, state, pending_slides, pending_hopos = [], {}, {}, {}
        muted_pending = {}
        notation_notes = {}
        strum_groups = {}
        hopo_links = []
        last_bar = -1
        for occurrence, index in enumerate(order):
            origin = clock.measure_starts[occurrence]
            if index <= last_bar:
                if pending_slides or pending_hopos or muted_pending:
                    unsupported(f"tracks/{part.id}/measures/{index}", "A linked technique crosses a repeat jump.")
                state.clear()
            last_bar = index
            for atom in sorted(part.bars[index], key=lambda n: (n.q, n.voice, n.string)):
                key = (atom.voice, atom.string)
                start, end = origin + atom.q, origin + atom.q + atom.length
                if atom.length <= 0 or atom.string < 0 or atom.string >= len(part.tuning):
                    raise ValueError(atom.location + ": invalid duration/string")
                previous = state.get(key)
                if not atom.tie and atom.strum_direction:
                    path = atom.beat.split('/')
                    group_key = (occurrence, atom.beat)
                    group = strum_groups.setdefault(group_key, {
                        'trackId':part.id, 'sourceId':'songsterr:' + ':'.join(path[i] for i in (1,3,5,7)),
                        'occurrence':occurrence+1, 'time':float(clock.at(start)),
                        'direction':atom.strum_direction, 'notes':[]})
                    group['notes'].append({'t':float(clock.at(start+atom.attack_offset)), 's':atom.string,'f':atom.fret})
                if atom.tie:
                    gap_allowed = source.format == 'songsterr'
                    if gap_allowed and previous is not None and previous['end'] < start:
                        rest_starts, rest_ends = rest_indices.get(atom.voice, ([], []))
                        i = bisect_left(rest_starts, start) - 1
                        gap_allowed = i < 0 or rest_ends[i] <= previous['end']
                    if (previous is None or previous["end"] > start
                            or previous["end"] != start and not gap_allowed):
                        unsupported(atom.location, "Source tie does not identify a continuous prior note; it has not been repaired.")
                    event = previous
                    # Reconstruct independently of the producer's normalization.
                    expressive = (atom.bends or atom.slide or atom.slide_in or atom.whammy
                                  or atom.trill or atom.pick_scrape or atom.hopo_origin or atom.hopo_destination
                                  or any(atom.effects.get(k) for k in ('hm','hp','hn','harmonic_target','vb')))
                    if event.get('muted_identity') and expressive:
                        unsupported(atom.location, 'A normalized muted continuation has a pitch gesture.')
                    if event['f'] != atom.fret:
                        rest_starts, rest_ends = rest_indices.get(atom.voice, ([], []))
                        ri = bisect_left(rest_starts, start) - 1
                        interrupted = ri >= 0 and rest_ends[ri] > event['start']
                        if (source.format != 'songsterr' or event['end'] != start or interrupted
                                or event['effects'].get('mt') is not True or atom.effects.get('mt') is not True
                                or sorted((event['f'], atom.fret)) != [0, 127]
                                or expressive or event['pitch_gesture'] or event['trill'] or event['scrapes']
                                or any(event['effects'].get(k) for k in ('hm','hp','hn','harmonic_target','vb','ho','po','ln'))):
                            unsupported(atom.location, 'Source tie does not identify a continuous prior note; it has not been repaired.')
                        p = atom.location.split('/')
                        first = event['locations'][0].split('/')
                        result['muted_tie_identities'].append({
                            'trackId': part.id, 'sourceId': 'songsterr:' + ':'.join(p[i] for i in (1,3,5,7,9)),
                            'originSourceId': 'songsterr:' + ':'.join(first[i] for i in (1,3,5,7,9)),
                            'location': atom.location, 'occurrence': occurrence + 1,
                            'attack': float(clock.at(event['start'])), 'start': float(clock.at(start)), 'end': float(clock.at(end)),
                            'string': atom.string,
                            'authored': {'dead': True, 'fret': None if atom.fret == 127 else atom.fret},
                            'used': {'dead': True, 'fret': None if event['f'] == 127 else event['f']},
                            'rule': 'muted-tie-keeps-attack-target'})
                        event['muted_identity'] = True
                    late_mute = (source.format == 'songsterr' and atom.effects.get('mt') is True
                                 and not event['effects'].get('mt') and atom.fret != 127
                                 and not atom.pick_scrape and not event['scrapes'])
                    if late_mute:
                        path = atom.location.split('/')
                        result['tied_mutes'].append({
                            'trackId': part.id, 'sourceId': 'songsterr:' + ':'.join(path[i] for i in (1,3,5,7,9)),
                            'location': atom.location, 'occurrence': occurrence + 1,
                            'attack': float(clock.at(event['start'])), 'start': float(clock.at(start)), 'end': float(clock.at(end)),
                            'string': atom.string, 'fret': atom.fret,
                            'authored': {'dead': True}, 'used': {'dead': False},
                            'rule': 'initial-pitched-target-continued'})
                    harmonic_fields = {'hm', 'hp', 'hn', 'hps', 'harmonic_target', 'harmonic_alias'}
                    if source.format == 'songsterr':
                        stated = {k: atom.effects[k] for k in harmonic_fields if k in atom.effects}
                        kept = {k: event['effects'][k] for k in harmonic_fields if k in event['effects']}
                        if event.get('contact'):
                            kept = {'harmonic_target': dict(event['contact']['target'])}
                        if stated and stated != kept:
                            def harmonic_pitch(fx):
                                if 'hps' in fx: return fx['hps']
                                h = fx.get('harmonic_target')
                                if h: return atom.fret + h['interval']
                                return None if fx.get('hp') or fx.get('hm') else atom.fret
                            feedback = any(fx.get('harmonic_target', {}).get('kind') == 'feedback' for fx in (stated, kept))
                            same_pitch = harmonic_pitch(stated) is not None and harmonic_pitch(stated) == harmonic_pitch(kept)
                            parts = atom.location.split('/')
                            source_id = 'songsterr:' + ':'.join(parts[i] for i in (1,3,5,7,9))
                            can_touch = (not kept and not event.get('harmonic_conflict') and not event['effects'].get('mt')
                                         and stated.get('harmonic_target', {}).get('kind') == 'artificial'
                                         and start > event['start'] and not atom.slide and not atom.slide_in)
                            if can_touch:
                                event['contact'] = {'at':start, 'target':dict(stated['harmonic_target']), 'source_id':source_id}
                                kept = dict(stated)
                            else:
                                event['harmonic_conflict'] = True
                            result['harmonic_ties'].append({
                                'trackId': part.id, 'sourceId': source_id,
                                'location': atom.location, 'occurrence': occurrence + 1,
                                'attack': float(clock.at(event['start'])), 'start': float(clock.at(start)), 'end': float(clock.at(end)),
                                'string': atom.string, 'fret': atom.fret, 'authored': stated, 'used': kept,
                                'rule': 'timed-artificial-contact' if can_touch else 'feedback-optional' if feedback else 'same-pitch' if same_pitch else 'initial-target-continued'})
                    elif "hn" in atom.effects and any(event["effects"].get(k) != atom.effects[k] for k in ("hn", "hps")):
                        unsupported(atom.location, "A changing harmonic target inside a tie is not independently representable.")
                    if source.format != 'songsterr' and "harmonic_target" in atom.effects and event["effects"].get("harmonic_target") != atom.effects["harmonic_target"]:
                        unsupported(atom.location, "A changing fretted harmonic inside a tie is not independently representable.")
                    event["end"] = end
                    event["effects"].update({k: v for k, v in atom.effects.items() if k != "pkd" and not (late_mute and k == 'mt') and (source.format != 'songsterr' or k not in harmonic_fields)})
                    event["locations"].append(atom.location)
                    result["tie_segments"] += 1
                else:
                    event = {"start": start + atom.attack_offset, "end": end, "s": atom.string, "f": atom.fret, "effects": dict(atom.effects),
                             "curve": [], "slide_marks": [], "incoming_marks": [], "scrapes": [], "scrape_direction": None, "locations": [atom.location], "occurrence": occurrence + 1, "beat": atom.beat,
                             "staccato": atom.staccato, "any_staccato": False, "pitch_gesture": False, "trill": atom.trill,
                             "other_pitch_gesture": False, "bend_atoms": []}
                    if atom.hopo_destination or key in pending_hopos:
                        if previous is None:
                            unsupported(atom.location, "Source hammer-on/pull-off has no prior note.")
                        if atom.fret == 127 or previous["f"] == 127:
                            unsupported(atom.location, "A hammer/pull link has an unpitched endpoint.")
                        event["effects"]["ho" if atom.fret > previous["f"] else "po"] = True
                        pending_hopos.pop(key, None)
                        hopo_links.append((previous, event))
                    if key in muted_pending:
                        if atom.fret == 127 or atom.effects.get('mt'):
                            unsupported(atom.location, 'A muted shift lacks a pitched destination.')
                        path = atom.location.split('/')
                        for record in muted_pending.pop(key):
                            record['target'] = {'sourceId': 'songsterr:' + ':'.join(path[i] for i in (1,3,5,7,9)),
                                                'time': float(clock.at(event['start'])), 'fret': atom.fret}
                    if key in pending_slides:
                        if atom.fret == 127:
                            unsupported(atom.location, "A pitched slide has an unpitched destination.")
                        target, slide = pending_slides.pop(key)
                        target["effects"]["sl"] = atom.fret
                        if slide == "legato":
                            target["effects"]["ln"] = True
                    notes.append(event)
                    if len(notes) > MAX_EVENTS:
                        raise ValueError("source: performed note limit exceeded")
                from .verify_trills import segment as verify_trill_segment
                verify_trill_segment(atom, event)
                event["any_staccato"] |= atom.staccato
                direction = atom.pick_scrape or (event["scrape_direction"] if atom.tie else None)
                event["scrape_direction"] = direction
                if direction:
                    intervals = event["scrapes"]
                    if intervals and intervals[-1][0] == direction and intervals[-1][2] == start:
                        intervals[-1] = (direction, intervals[-1][1], end)
                    else:
                        intervals.append((direction, max(start, event["start"]), end))
                event["pitch_gesture"] |= bool(atom.bends or atom.slide or atom.slide_in or atom.whammy)
                event['other_pitch_gesture'] |= bool(atom.slide or atom.slide_in or atom.whammy)
                event['bend_atoms'].append((atom, start, end, occurrence))
                if (len(event["locations"]) > 1 and event["any_staccato"] and event["pitch_gesture"]
                        and (source.format != 'songsterr' or event['other_pitch_gesture'])):
                    unsupported(atom.location, "Staccato ties with pitch gestures are not independently verified.")
                gesture_length = max(atom.length / 2, F(1, 32)) if atom.staccato else atom.length
                retained = event.get('whammy', [])
                old = retained[-1] if atom.tie and retained else None
                held_bar = old['points'][0][1] if old and old['points'] else 0
                if held_bar:
                    left_time, right_time = clock.at(start), clock.at(start + gesture_length)
                    def held_interval(a, b):
                        return {'left': a, 'right': b, 'source_id': old['source_id'],
                                'group': old['group'], 'vibrato': None,
                                'points': [(a, held_bar), (b, held_bar)]}
                    if old['right'] < left_time:
                        retained.append(held_interval(old['right'], left_time))
                    if atom.whammy is None:
                        retained.append(held_interval(left_time, right_time))
                if atom.whammy is not None:
                    left = start + (atom.attack_offset if not atom.tie else 0)
                    right = start + gesture_length
                    if left >= right:
                        unsupported(atom.location, 'Bar expression has no sounding interval')
                    event.setdefault('whammy', []).append({
                        'left': clock.at(left), 'right': clock.at(right),
                        'source_id': atom.whammy['source_id'],
                        'group': f"{atom.whammy['source_id']}@{occurrence}",
                        'vibrato': atom.whammy['vibrato'],
                        'points': [(clock.at(left+(right-left)*p),v) for p,v in atom.whammy['curve']]
                                  or ([(clock.at(left),held_bar),(clock.at(right),held_bar)] if held_bar else [])})
                for fraction_, value in atom.bends:
                    if not 0 <= fraction_ <= 1:
                        raise ValueError(atom.location + ": bend outside note")
                    event["curve"].append((clock.at(start + gesture_length * fraction_), value))
                if atom.fret == 127 and atom.slide:
                    path = atom.location.split('/')
                    record = {'trackId':part.id, 'sourceId':'songsterr:' + ':'.join(path[i] for i in (1,3,5,7,9)),
                              'location':atom.location, 'occurrence':occurrence+1,
                              'attack':float(clock.at(event['start'])), 'start':float(clock.at(start)),
                              'end':float(clock.at(start+gesture_length)), 'string':atom.string,
                              'authored':{'slide':{'shift':'shift','up':'upwards','down':'downwards'}[atom.slide]},
                              'used':{'rule':'retained-shift-no-pitch-path' if atom.slide=='shift' else 'unscored-directional-slide'},
                              'target':None}
                    result['muted_slides'].append(record)
                    if atom.slide == 'shift':muted_pending.setdefault(key,[]).append(record)
                if atom.slide in {"shift", "legato"} and atom.fret != 127:
                    pending_slides[key] = (event, atom.slide)
                elif atom.slide in {'up','down'}:
                    event["slide_marks"].append((atom.slide, start, start + gesture_length))
                if atom.slide_in:
                    event["incoming_marks"].append((atom.slide_in, start))
                if atom.hopo_origin:
                    pending_hopos[key] = event
                    event["effects"]["ln"] = True
                state[key] = event
                written_note = {"str": atom.string, "fret": atom.fret, "tied": atom.tie}
                if atom.fret != 127:
                    written_note["midi"] = part.tuning[atom.string] + part.capo + atom.fret + atom.pitch_offset
                for raw_key, out_key in {"mt": "dead", "ghost": "ghost", "vb": "vib", "ac": "ac", "tp": "tp"}.items():
                    if atom.effects.get(raw_key):
                        written_note[out_key] = atom.effects[raw_key]
                if atom.wide_vibrato:
                    written_note["vibw"] = True
                if not atom.tie:
                    for fx in ("ho", "po"):
                        if event["effects"].get(fx):
                            written_note[fx] = True
                notation_notes[(occurrence, atom.location)] = written_note
        if pending_slides or pending_hopos or muted_pending:
            unsupported(f"tracks/{part.id}", "Source linked technique has no destination.")
        from .verify_trills import expand as trill_expectations
        for group in strum_groups.values():
            if len(group['notes']) >= 2 and len({n['s'] for n in group['notes']}) == len(group['notes']):
                group['notes'].sort(key=lambda n:n['s'])
                result['strums'].append(group)
        notes = trill_expectations(notes, hopo_links, part, notation_notes, result["trills"])
        rendered = []
        for n in notes:
            sound_end = n["end"]
            if n["staccato"]:
                sound_end = n["start"] + max((n["end"] - n["start"]) / 2, F(1, 32))
                if sound_end > n["end"]:
                    unsupported(n["locations"][0], "Staccato minimum exceeds authored duration.")
            start, end = clock.at(n["start"]), clock.at(sound_end)
            if source.format == 'songsterr':
                from .verify_staccato_bends import reconstruct
                evidence = reconstruct(n, part, clock, sound_end)
                if evidence:
                    result['staccato_bends'].append(evidence)
            if n["start"] < 0 or sound_end <= n["start"]:
                unsupported(n["locations"][0], "Strum exceeds its sounding interval; no attack or endpoint was repaired.")
            mapped_start, mapped_end = recording.at(start), recording.at(end)
            row = {"t": mapped_start, "sus": round(mapped_end - mapped_start, 6), "s": n["s"], "f": n["f"], **n["effects"]}
            if n.get('contact'):
                contact = n['contact']
                if (contact['at'] >= sound_end or n['slide_marks'] or n['incoming_marks']
                        or any(k in n['effects'] for k in ('sl','slu','slide_out'))):
                    for evidence in result['harmonic_ties']:
                        if (evidence['trackId'] == part.id and evidence['attack'] == float(start)
                                and evidence['string'] == n['s'] and evidence['fret'] == n['f']):
                            evidence['used'] = {k: n['effects'][k] for k in harmonic_fields if k in n['effects']}
                            evidence['rule'] = 'initial-target-continued'
                else:
                    row['harmonic_changes'] = {'version':1, 'events':[{
                        'start':round(recording.at(clock.at(contact['at']))-mapped_start,6),
                        'end':row['sus'], 'target':contact['target'], 'source_id':contact['source_id']}]}
            if n.get('whammy'):
                segments = []
                for expression in n['whammy']:
                    points = []
                    for point in expression['points']:
                        if points and recording.piecewise:
                            previous = points[-1]
                            for boundary in recording.scores:
                                if previous[0]+F(1,10**9) < boundary < point[0]-F(1,10**9):
                                    points.append((boundary, previous[1]+(point[1]-previous[1])*(boundary-previous[0])/(point[0]-previous[0])))
                        points.append(point)
                    segments.append({'start': round(recording.at(expression['left'])-mapped_start,6),
                                     'end': round(recording.at(expression['right'])-mapped_start,6),
                                     'source_id': expression['source_id'], 'group': expression['group'],
                                     'curve': [{'t':round(recording.at(p)-mapped_start,6),'v':float(v)} for p,v in points],
                                     **({'vibrato':expression['vibrato']} if expression['vibrato'] else {})})
                row['whammy'] = {'version':1,'policy':'optional','segments':segments}
            if n["scrapes"]:
                row["pick_scrape_marks"] = [{"direction": direction,
                    "start": round(recording.at(clock.at(left)) - mapped_start, 6),
                    "end": round(recording.at(clock.at(min(right, sound_end))) - mapped_start, 6)}
                    for direction, left, right in n["scrapes"] if left < sound_end]
                if not row["pick_scrape_marks"]:
                    row.pop("pick_scrape_marks")
            if n["slide_marks"]:
                row["slide_out_marks"] = [{"direction": direction,
                                           "start": round(recording.at(clock.at(left)) - mapped_start, 6),
                                           "end": round(recording.at(clock.at(right)) - mapped_start, 6)}
                                          for direction, left, right in n["slide_marks"]]
                directions = {direction for direction, _, _ in n["slide_marks"]}
                if len(directions) == 1:
                    row["slide_out"] = next(iter(directions))
            if n["incoming_marks"]:
                row["slide_in_marks"] = [{"direction": direction,
                                          "time": round(recording.at(clock.at(destination)) - mapped_start, 6)}
                                         for direction, destination in n["incoming_marks"]]
            if n["curve"]:
                curve = []
                for p in n["curve"]:
                    if curve and p[0] > curve[-1][0] and recording.piecewise:
                        left = curve[-1]
                        for boundary in recording.scores:
                            # Alignment anchors originate as double-precision
                            # score seconds. Do not invent a second curve knot
                            # when that float is merely a sub-nanosecond spelling
                            # of an exact rational note/curve boundary.
                            if left[0] + F(1, 1_000_000_000) < boundary < p[0] - F(1, 1_000_000_000):
                                value = left[1] + (p[1] - left[1]) * (boundary - left[0]) / (p[0] - left[0])
                                curve.append((boundary, value))
                    curve.append(p)
                row["bnv"] = [{"t": round(recording.at(p) - mapped_start, 6), "v": float(v)} for p, v in curve]
                row["bn"] = float(max((v for _, v in curve), key=abs))
            rendered.append({"note": row, "locations": n["locations"], "occurrence": n["occurrence"], "beat": n["beat"],
                             **({'trill': True} if n.get('trill') else {})})
        notation_beats = []
        for occurrence, index in enumerate(order):
            origin = clock.measure_starts[occurrence]
            for beat in part.beats[index]:
                notation_beats.append({"measure": occurrence + 1, "voice": beat["voice"], "location": beat["location"],
                                       "time": recording.at(clock.at(origin + beat["q"])),
                                       "end": recording.at(clock.at(origin + beat["q"] + beat["length"])),
                                       "quarter": beat["written_q"], "length": beat["length"], "rest": beat["rest"],
                                       "notation": beat["notation"], "notes": [notation_notes[(occurrence, n.location)] for n in beat["notes"]]})
        rendered.sort(key=lambda item: (item["note"]["t"], item["note"]["s"]))
        result["parts"].append({"source": part, "notes": rendered, "notation_beats": notation_beats, "notation_measures": measure_facts})
    result["score_duration"] = float(clock.at(clock.quarters))
    result['harmonic_ties'].sort(key=lambda r: (r['trackId'], r['occurrence'], r['start'], r['string'], r['location']))
    result['tied_mutes'].sort(key=lambda r: (r['trackId'], r['occurrence'], r['start'], r['string'], r['location']))
    result['muted_tie_identities'].sort(key=lambda r: (r['trackId'], r['occurrence'], r['start'], r['string'], r['location']))
    result['staccato_bends'].sort(key=lambda r: (r['trackId'], r['occurrence'], r['start'], r['string'], r['location']))
    result['muted_slides'].sort(key=lambda r: (r['trackId'], r['occurrence'], r['start'], r['string'], r['location']))
    result['strums'].sort(key=lambda r:(r['trackId'],r['occurrence'],r['time'],r['sourceId']))
    result["mapped_end"] = recording.at(clock.at(clock.quarters))
    return result

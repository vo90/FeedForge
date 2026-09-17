"""Independent rational performance evaluator used only by verification."""
from __future__ import annotations

from bisect import bisect_right
from fractions import Fraction as F
import math

from .verify_source import Source, fraction, unsupported

MAX_EVENTS = 500_000


def visits(source: Source):
    """Expand repeat intervals recursively, independently of the import walker.

    Ending regions beginning before a repeat close remain explicitly unverified;
    accepting individually filtered bars there can silently change navigation.
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
            if any(b.endings for b in source.bars[start:i]):
                unsupported(f"measures/{i}", "Multi-bar ending regions are not independently verified.")
            intervals[start] = (i, bar.repeat_count)
    if openings:
        unsupported(f"measures/{openings[-1]}", "Unclosed source repeat has no definite performance order.")
    if not intervals and any(bar.endings for bar in source.bars):
        unsupported("measures", "Alternate endings without a repeat have no independently verified performance order.")
    result = []

    def segment(lo, hi, turn=1, owner=None):
        i, ending_turn = lo, turn
        while i <= hi:
            if i in intervals and i != owner:
                end, count = intervals[i]
                if end > hi:
                    unsupported(f"measures/{i}", "Crossing source repeats are not independently verified.")
                for repeat_turn in range(1, count + 1):
                    segment(i, end, repeat_turn, i)
                ending_turn = count if owner is None else turn
                i = end + 1
                continue
            bar = source.bars[i]
            if not bar.endings or ending_turn in bar.endings:
                result.append(i)
                if len(result) > 20_000:
                    raise ValueError("source: performed measure limit exceeded")
            if not bar.endings:
                ending_turn = turn
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
    order = visits(source)
    clock, recording = Clock(source, order), RecordingMap(alignment)
    result = {"parts": [], "beats": [], "sections": [], "time_signatures": [], "tempos": [], "order": order,
              "raw_notes": sum(len(bar) for p in source.parts for bar in p.bars), "tie_segments": 0}
    measure_facts = []
    inherited_tempos, inherited = [], F(120)
    for bar in source.bars:
        inherited_tempos.append(bar.tempos.get(F(0), inherited))
        for _, inherited in sorted(bar.tempos.items()):
            pass
    previous_signature = None
    for occurrence, index in enumerate(order):
        bar, origin = source.bars[index], clock.measure_starts[occurrence]
        start_time = recording.at(clock.at(origin))
        measure_facts.append({"idx": occurrence + 1, "source_measure": index + 1, "t": start_time,
                              "ts": list(bar.signature), "written_tempo": float(inherited_tempos[index]),
                              "tempo": float(inherited_tempos[index] / recording.ratio(clock.at(origin))),
                              "duration_seconds": round(recording.at(clock.at(origin + bar.length)) - start_time, 6)})
        q = F(0)
        while q < bar.length:
            time = recording.at(clock.at(origin + q))
            if time >= 0:
                result["beats"].append({"time": time, "measure": occurrence + 1 if q == 0 else -1})
            q += F(4, bar.signature[1])
        if bar.section:
            time = recording.at(clock.at(origin))
            if time >= 0:
                result["sections"].append({"time": time, "name": bar.section})
        if bar.signature != previous_signature:
            result["time_signatures"].append({"time": max(0., recording.at(clock.at(origin))), "ts": list(bar.signature)})
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
        notes, state, pending_slides, pending_hopos = [], {}, {}, {}
        notation_notes = {}
        last_bar = -1
        for occurrence, index in enumerate(order):
            origin = clock.measure_starts[occurrence]
            if index <= last_bar:
                if pending_slides or pending_hopos:
                    unsupported(f"tracks/{part.id}/measures/{index}", "A linked technique crosses a repeat jump.")
                state.clear()
            last_bar = index
            for atom in sorted(part.bars[index], key=lambda n: (n.q, n.voice, n.string)):
                key = (atom.voice, atom.string)
                start, end = origin + atom.q, origin + atom.q + atom.length
                if atom.length <= 0 or atom.string < 0 or atom.string >= len(part.tuning):
                    raise ValueError(atom.location + ": invalid duration/string")
                previous = state.get(key)
                if atom.tie:
                    if previous is None or previous["f"] != atom.fret or previous["end"] != start:
                        unsupported(atom.location, "Source tie does not identify a continuous prior note; it has not been repaired.")
                    event = previous
                    event["end"] = end
                    event["effects"].update(atom.effects)
                    event["locations"].append(atom.location)
                    result["tie_segments"] += 1
                else:
                    event = {"start": start, "end": end, "s": atom.string, "f": atom.fret, "effects": dict(atom.effects),
                             "curve": [], "locations": [atom.location], "occurrence": occurrence + 1, "beat": atom.beat}
                    if atom.hopo_destination or key in pending_hopos:
                        if previous is None:
                            unsupported(atom.location, "Source hammer-on/pull-off has no prior note.")
                        event["effects"]["ho" if atom.fret > previous["f"] else "po"] = True
                        pending_hopos.pop(key, None)
                    if key in pending_slides:
                        target, slide = pending_slides.pop(key)
                        target["effects"]["sl"] = atom.fret
                        if slide == "legato":
                            target["effects"]["ln"] = True
                    notes.append(event)
                    if len(notes) > MAX_EVENTS:
                        raise ValueError("source: performed note limit exceeded")
                for fraction_, value in atom.bends:
                    if not 0 <= fraction_ <= 1:
                        raise ValueError(atom.location + ": bend outside note")
                    event["curve"].append((clock.at(start + atom.length * fraction_), value))
                if atom.slide in {"shift", "legato"}:
                    pending_slides[key] = (event, atom.slide)
                elif atom.slide:
                    event["effects"]["slide_out"] = atom.slide
                if atom.hopo_origin:
                    pending_hopos[key] = event
                    event["effects"]["ln"] = True
                state[key] = event
                written_note = {"midi": part.tuning[atom.string] + part.capo + atom.fret,
                                "str": atom.string, "fret": atom.fret, "tied": atom.tie}
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
        if pending_slides or pending_hopos:
            unsupported(f"tracks/{part.id}", "Source linked technique has no destination.")
        rendered = []
        for n in notes:
            start, end = clock.at(n["start"]), clock.at(n["end"])
            mapped_start, mapped_end = recording.at(start), recording.at(end)
            row = {"t": mapped_start, "sus": round(mapped_end - mapped_start, 6), "s": n["s"], "f": n["f"], **n["effects"]}
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
            rendered.append({"note": row, "locations": n["locations"], "occurrence": n["occurrence"], "beat": n["beat"]})
        notation_beats = []
        for occurrence, index in enumerate(order):
            origin = clock.measure_starts[occurrence]
            for beat in part.beats[index]:
                notation_beats.append({"measure": occurrence + 1, "voice": beat["voice"], "location": beat["location"],
                                       "time": recording.at(clock.at(origin + beat["q"])),
                                       "end": recording.at(clock.at(origin + beat["q"] + beat["length"])),
                                       "quarter": beat["written_q"], "length": beat["length"], "rest": beat["rest"],
                                       "notation": beat["notation"], "notes": [notation_notes[(occurrence, n.location)] for n in beat["notes"]]})
        result["parts"].append({"source": part, "notes": rendered, "notation_beats": notation_beats, "notation_measures": measure_facts})
    result["score_duration"] = float(clock.at(clock.quarters))
    result["mapped_end"] = recording.at(clock.at(clock.quarters))
    return result

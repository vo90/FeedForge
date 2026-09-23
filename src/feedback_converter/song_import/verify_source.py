"""Independent, read-only source facts for the import fidelity checker.

This module deliberately does not import the production score parsers or model.
Its small vocabulary is a verification capability boundary, not a repair policy.
Unknown active musical fields are reported as unverified instead of being ignored.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction as F
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET
from zipfile import ZipFile

MAX_SOURCE = 80 * 1024 * 1024


class UnverifiedFeature(ValueError):
    def __init__(self, location: str, message: str):
        self.location = location
        super().__init__(message)


def unsupported(location, message):
    raise UnverifiedFeature(location, message)


def fraction(value, location="source"):
    try:
        if isinstance(value, bool):
            raise ValueError()
        result = F(str(value)) if not isinstance(value, (tuple, list)) else F(str(value[0])) / F(str(value[1]))
        if abs(result) > 1_000_000 or result.denominator.bit_length() > 256:
            raise ValueError()
        return result
    except (ValueError, TypeError, ZeroDivisionError, IndexError, OverflowError) as exc:
        raise ValueError(f"{location}: invalid rational value") from exc


def integer(value, location="source"):
    n = fraction(value, location)
    if n.denominator != 1:
        raise ValueError(f"{location}: expected integer")
    return int(n)


@dataclass
class Atom:
    q: F
    length: F
    string: int
    fret: int
    location: str
    voice: str
    beat: str
    tie: bool = False
    effects: dict = field(default_factory=dict)
    bends: list = field(default_factory=list)
    slide: str | None = None
    hopo_origin: bool = False
    hopo_destination: bool = False
    wide_vibrato: bool = False
    slide_in: str | None = None


@dataclass
class Bar:
    signature: tuple[int, int]
    repeat_start: bool = False
    repeat_count: int = 0
    endings: frozenset = frozenset()
    section: str = ""
    tempos: dict = field(default_factory=dict)

    @property
    def length(self):
        return F(4 * self.signature[0], self.signature[1])


@dataclass
class Part:
    id: str
    name: str
    instrument: str
    tuning: list
    capo: int
    bars: list
    # Independent notation facts: every written voice/beat, including rests.
    beats: list = field(default_factory=list)
    notation_unavailable: list = field(default_factory=list)
    clefs: list = field(default_factory=list)


@dataclass
class Source:
    format: str
    title: str
    artist: str
    bars: list
    parts: list
    excluded: list
    track_count: int
    identity: dict = field(default_factory=dict)
    ignored: set = field(default_factory=set)


def inactive(value):
    return value is None or value is False or isinstance(value, (str, list, dict)) and len(value) == 0


def _active_unknown(obj, known, ignored, location, inventory):
    for key, value in obj.items():
        if key in ignored:
            inventory.add(key)
        elif key not in known and not inactive(value):
            unsupported(location + "/" + key, f"No independent interpretation for source field {key}.")


def _program_instrument(meta):
    if meta.get("isVocalTrack") is True:
        return ""
    program = meta.get("instrumentId", meta.get("midiProgram"))
    if isinstance(program, int) and not isinstance(program, bool):
        if 24 <= program <= 31:
            return "guitar"
        if 32 <= program <= 39:
            return "bass"
        if program >= 0:
            return ""
    label = str(meta.get("instrument", meta.get("type", ""))).lower()
    if "bass" in label:
        return "bass"
    return "guitar" if "guitar" in label else ""


def _endings(raw, loc):
    if not raw:
        return frozenset()
    if isinstance(raw, list):
        return frozenset(integer(v, loc) for v in raw)
    mask = integer(raw, loc)
    if mask < 0 or mask > 65535:
        raise ValueError(f"{loc}: invalid ending mask")
    return frozenset(i + 1 for i in range(16) if mask & (1 << i))


def _songsterr_swing_lengths(bar, feel, location):
    original = [[fraction(b["duration"], location) * 4 for b in v["beats"]] for v in bar["voices"]]
    if feel in (None, "off"):
        return original
    patterns = {"8th": (F(1, 2), F(2, 3)), "16th": (F(1, 4), F(2, 3)),
                "dotted8th": (F(1, 2), F(3, 4)), "dotted16th": (F(1, 4), F(3, 4)),
                "scottish8th": (F(1, 2), F(1, 4)), "scottish16th": (F(1, 4), F(1, 4))}
    if feel not in patterns:
        unsupported(location, "Unknown swing feel.")
    half, ratio = patterns[feel]
    disabled, rows = set(), []
    for voice, lengths in zip(bar["voices"], original):
        starts, cursor = [], F(0)
        for beat, length in zip(voice["beats"], lengths):
            starts.append(cursor)
            if not beat.get("graceNote"):
                if length % half != 0 or feel in ("8th", "16th") and beat.get("tuplet") == 2:
                    disabled.add(cursor // (half * 2))
                cursor += length
        rows.append((starts, cursor))
    output = []
    for voice, durations, (starts, end) in zip(bar["voices"], original, rows):
        def mapped(q):
            pair, remainder = divmod(q, half * 2)
            if pair in disabled or (pair + 1) * half * 2 > end:
                return q
            left = pair * half * 2
            if remainder <= half:
                return left + remainder * 2 * ratio
            return left + half * 2 * ratio + (remainder - half) * 2 * (1 - ratio)
        output.append([d if b.get("graceNote") else mapped(q + d) - mapped(q)
                       for b, q, d in zip(voice["beats"], starts, durations)])
    return output


def _songsterr_beat_clock(beats, measure_length, location, performed_lengths=None):
    written = [4 * fraction(b["duration"], location) for b in beats]
    lengths = performed_lengths if performed_lengths is not None else written
    if (len(beats) == 1 and beats[0].get("rest") is True and beats[0].get("type") == 1
            and lengths == [F(4)] and not any(beats[0].get(k) for k in ("dots", "tuplet", "graceNote"))
            and isinstance(beats[0].get("notes"), list)
            and all(n.get("rest") is True for n in beats[0]["notes"])):
        return [[F(0), measure_length, F(0)]]
    starts, written_starts, position, written_position = [], [], F(0), F(0)
    for beat, length in zip(beats, lengths):
        if length <= 0:
            raise ValueError(location + ": invalid written duration")
        starts.append(position)
        written_starts.append(written_position)
        if not beat.get("graceNote"):
            position += length
            written_position += written[len(starts) - 1]
    if position > measure_length:
        raise ValueError(location + ": overfull written voice")
    times = [[start, length, w] for start, length, w in zip(starts, lengths, written_starts)]
    pending = []
    for index, beat in enumerate([*beats, {}]):
        if beat.get("graceNote"):
            if beat["graceNote"] not in ("onBeat", "beforeBeat"):
                unsupported(location, "This grace-note timing is not independently verified.")
            pending.append(index)
            continue
        if not pending:
            continue
        kind = beats[pending[0]]["graceNote"]
        if any(beats[i]["graceNote"] != kind for i in pending):
            unsupported(location, "Mixed grace group is not independently verified.")
        before = kind == "beforeBeat"
        if before:
            principal = pending[0] - 1
            if principal < 0 or beats[principal].get("graceNote"):
                unsupported(location, "Before-beat grace crosses an unresolved boundary.")
            if principal > 0 and beats[principal - 1].get("graceNote"):
                unsupported(location, "Shared-principal grace timing is not independently verified.")
        else:
            principal = index
            if principal >= len(beats):
                unsupported(location, "A grace note has no independently resolvable principal note.")
        tested = pending if before else [*pending, principal]
        if any(beats[i].get("rest") or not any(not n.get("rest") for n in beats[i].get("notes", [])) for i in tested):
            raise ValueError(location + ": grace group has no pitched principal")
        dots = max(integer(beats[i].get("dots", 0), location) for i in pending)
        principal_available = (times[principal][1] if before else
                               measure_length - starts[principal] if principal + 1 == len(beats) else lengths[principal])
        fraction_available = F(7, 8) if dots >= 2 else F(3, 4) if dots == 1 else F(1, 2)
        total = sum((lengths[i] for i in pending), F(0))
        maximum = min(total / len(pending), principal_available * fraction_available)
        used = F(0)
        budget = min(total, maximum)
        anchor = starts[pending[0]] if before else starts[principal]
        for i in pending:
            length = maximum / len(pending) if total > maximum else lengths[i]
            times[i] = [anchor + used - (budget if before else 0), length, written_starts[pending[0]]]
            used += length
        times[principal][1] -= used
        if not before:
            times[principal][0] += used
        if times[principal][1] <= 0:
            raise ValueError(location + ": grace group consumes principal")
        pending.clear()
    if pending:
        unsupported(location, "A grace note has no independently resolvable principal note.")
    return times


def _songsterr_strum(beat, location):
    options = [beat[k] for k in ("brushStroke", "arpeggio") if beat.get(k) is not None]
    legacy = [k for k in ("upStroke", "downStroke") if beat.get(k)]
    old = [k for k in ("upArpeggio", "downArpeggio") if beat.get(k)]
    legacy_interval = None
    if old and not options:
        if len(old) != 1 or legacy:
            unsupported(location, "Conflicting old arpeggio markings.")
        value = integer(beat[old[0]], location)
        if value < 1 or value > 8:
            unsupported(location, "Unverified old arpeggio duration.")
        legacy_interval = F(2) ** (value - 6) / 13
        direction = {"upArpeggio": "up", "downArpeggio": "down"}[old[0]]
    if len(options) > 1 or len(legacy) > 1:
        unsupported(location, "Conflicting strum markings.")
    if not options and not legacy and legacy_interval is None:
        return {}, None
    if options:
        stroke = options[0]
        if not isinstance(stroke, dict) or set(stroke) != {"direction", "duration", "shift"}:
            unsupported(location, "Unknown strum fields.")
        direction = stroke["direction"]
        amount, shift = fraction(stroke["duration"], location), integer(stroke["shift"], location)
    elif legacy_interval is None:
        direction, amount, shift = ("down" if legacy[0] == "upStroke" else "up"), F(30), 100
    else:
        amount, shift = F(0), 100
    if legacy and (integer(beat[legacy[0]], location) != 1 or direction != ("down" if legacy[0] == "upStroke" else "up")):
        unsupported(location, "Unverified legacy strum value.")
    if direction not in ("up", "down") or amount < 0 or amount > 960 or not 0 <= shift <= 100:
        raise ValueError(location + ": invalid strum parameters")
    notes = [(i, n) for i, n in enumerate(beat["notes"]) if not n.get("rest")]
    strings = [integer(n["string"], location) for _, n in notes]
    if len(set(strings)) != len(strings):
        raise ValueError(location + ": duplicate string in strum")
    if any(any(n.get(k) for k in ("bend", "hp", "slide", "leftSlide", "rightSlide")) for _, n in notes) or len(notes) < 2:
        return {}, direction
    if beat.get("graceNote") or any(n.get("tie") for _, n in notes):
        unsupported(location, "Combined grace/tie and strum requires additional verification.")
    capped = min(amount, 960, (fraction(beat["duration"], location) * 1920).__floor__())
    ordered = sorted(notes, key=lambda item: item[1]["string"], reverse=direction == "down")
    interval = legacy_interval if legacy_interval is not None else F(capped) / (len(notes) * 480)
    first = -interval * (len(notes) - 1) * F(100 - shift, 100)
    return {index: first + rank * interval for rank, (index, _) in enumerate(ordered)}, direction


def songsterr(document):
    if not isinstance(document, dict) or document.get("format") != "songsterr":
        raise ValueError("source: not a Songsterr envelope")
    metadata, raw_parts = document["tracks"], document["parts"]
    if not isinstance(metadata, list) or not isinstance(raw_parts, list) or not metadata or len(metadata) != len(raw_parts):
        raise ValueError("source: incomplete track envelope")
    count = len(raw_parts[0]["measures"])
    if not 0 < count <= 20_000 or any(len(p["measures"]) != count for p in raw_parts):
        raise ValueError("source: inconsistent measure counts")
    ignored, bars, signature = set(), [], (4, 4)
    for bi in range(count):
        loc = f"measures/{bi}"
        samples = [part["measures"][bi] for part in raw_parts]
        signatures = {tuple(integer(x, loc) for x in b["signature"]) for b in samples if b.get("signature")}
        repeats = {integer(b["repeat"], loc) for b in samples if "repeat" in b}
        endings = {_endings(b["alternateEnding"], loc) for b in samples if b.get("alternateEnding")}
        if any(len(values) > 1 for values in (signatures, repeats, endings)):
            unsupported(loc, "Source tracks disagree about the performed meter or navigation.")
        if signatures:
            signature = next(iter(signatures))
        if len(signature) != 2 or min(signature) < 1:
            raise ValueError(f"{loc}: invalid meter")
        for b in samples:
            _active_unknown(b, {"voices", "signature", "rest", "marker", "repeat", "repeatStart", "alternateEnding", "tripletFeel", "clef"},
                            {"width", "id", "index", "doubleBarline", "keySignature"}, loc, ignored)
        markers = [b["marker"] for b in samples if b.get("marker")]
        names = {str(m.get("text", "")) if isinstance(m, dict) else str(m) for m in markers}
        if len(names) > 1:
            unsupported(loc + "/marker", "Source tracks have different section markers.")
        bars.append(Bar(signature, any(b.get("repeatStart") for b in samples), next(iter(repeats), 0),
                        next(iter(endings), frozenset()), next(iter(names), "")))
    all_clocks = []
    for pi, raw in enumerate(raw_parts):
        automations = raw.get("automations", {})
        _active_unknown(automations, {"tempo", "gradualTempo", "fermata"}, {"volume", "balance"}, f"parts/{pi}/automations", ignored)
        part_events = {}
        for tempo in automations.get("tempo", []):
            loc = f"parts/{pi}/automations/tempo"
            _active_unknown(tempo, {"measure", "position", "bpm", "type", "linear", "dotted"}, {"text"}, loc, ignored)
            if tempo.get("linear") is not None and type(tempo["linear"]) is not bool:
                raise ValueError(loc + ": invalid linear flag")
            bi = integer(tempo["measure"], loc)
            q = fraction(tempo.get("position", 0), loc) * 4
            bpm = fraction(tempo["bpm"], loc) * F(4, integer(tempo.get("type", 4), loc))
            if tempo.get("dotted") is not None and type(tempo["dotted"]) is not bool:
                raise ValueError(loc + ": invalid dotted tempo")
            if tempo.get("dotted"):
                bpm *= F(3, 2)
            if not 0 <= bi < count or not 0 <= q < bars[bi].length or bpm <= 0:
                raise ValueError(f"{loc}: invalid tempo coordinate")
            if (bi, q) in part_events and part_events[bi, q] != bpm:
                raise ValueError(loc + ": conflicting source tempo events")
            part_events[bi, q] = bpm
        from .verify_automation import expand
        expanded = expand(automations, bars, part_events, f"parts/{pi}/automations")
        all_clocks.append(expanded)
        for (bi, q), bpm in expanded.items():
            if q in bars[bi].tempos and bars[bi].tempos[q] != bpm:
                unsupported(loc, "Source tracks disagree about tempo.")
            bars[bi].tempos[q] = bpm
    if any(p.get("automations", {}).get("gradualTempo") or p.get("automations", {}).get("fermata") for p in raw_parts):
        points = sorted(set().union(*(c.keys() for c in all_clocks)))
        for point in points:
            rates = []
            for clock in all_clocks:
                before = [q for q in clock if q <= point]
                if before:
                    rates.append(clock[max(before)])
            if len(set(rates)) > 1:
                unsupported("automations", "Tracks have different performed tempo automation.")
    parts, excluded = [], []
    note_keys = {"string", "fret", "tie", "rest", "dead", "vibrato", "wideVibrato", "ghost", "accentuated",
                 "tap", "tapping", "hp", "harmonic", "harmonicFret", "slide", "bend", "leftHandVibrato", "staccato"}
    beat_keys = {"duration", "notes", "rest", "type", "dots", "tuplet", "tupletStart", "tupletStop", "graceNote",
                 "palmMute", "letRing", "tremolo", "tap", "tapping", "slap", "pop", "slapping", "popping", "vibrato", "wideVibrato", "text", "velocity", "gradualVelocity", "chord", "pickStroke", "wahwah", "brushStroke", "arpeggio", "upStroke", "downStroke", "upArpeggio", "downArpeggio"}
    for pi, (meta, raw) in enumerate(zip(metadata, raw_parts)):
        tid = str(meta.get("id", pi))
        name = str(meta.get("name") or meta.get("title") or meta.get("instrument") or f"Track {pi + 1}")
        kind = _program_instrument(meta)
        if not kind:
            excluded.append({"id": tid, "name": name, "reason": "non_playable_instrument"})
            continue
        tuning = [integer(v) for v in reversed(raw.get("tuning") or meta["tuning"])]
        track = Part(tid, name, kind, tuning, integer(raw.get("capo", meta.get("capo", 0))), [], [])
        feel = "off"
        for bi, bar in enumerate(raw["measures"]):
            feel = bar.get("tripletFeel") or feel
            lengths_by_voice = _songsterr_swing_lengths(bar, feel, f"parts/{pi}/measures/{bi}")
            clef = bar.get("clef")
            if clef not in (None, "G2", "F4", "C3", "C4", "neutral"):
                unsupported(f"parts/{pi}/measures/{bi}/clef", "Unknown source clef.")
            track.clefs.append(clef or (track.clefs[-1] if track.clefs else None))
            atoms, beat_facts = [], []
            for vi, voice in enumerate(bar["voices"]):
                _active_unknown(voice, {"beats", "rest"}, {"id"}, f"parts/{pi}/measures/{bi}/voices/{vi}", ignored)
                times = _songsterr_beat_clock(voice["beats"], bars[bi].length, f"parts/{pi}/measures/{bi}/voices/{vi}", lengths_by_voice[vi])
                for bti, beat in enumerate(voice["beats"]):
                    loc = f"parts/{pi}/measures/{bi}/voices/{vi}/beats/{bti}"
                    _active_unknown(beat, beat_keys, {"beamStart", "beamStop", "id"}, loc, ignored)
                    for key in ("slapping", "popping"):
                        if key in beat and not isinstance(beat[key], bool):
                            raise ValueError(loc + ": invalid " + key + " flag")
                    label = beat.get("chord", {})
                    if not isinstance(label, dict) or set(label) - {"text", "width"} or not isinstance(label.get("text", ""), str):
                        unsupported(loc + "/chord", "Unverified authored chord label.")
                    if beat.get("wahwah") not in (None, "open", "closed"):
                        unsupported(loc + "/wahwah", "Unverified wah pedal marking.")
                    written_duration = fraction(beat["duration"], loc) * 4
                    q, duration, written_q = times[bti]
                    offsets, direction = _songsterr_strum(beat, loc)
                    dots = integer(beat.get("dots", 0), loc)
                    denominator = beat.get("type")
                    if denominator is None:
                        denominator = next((d for d in (1, 2, 4, 8, 16, 32, 64, 128, 256)
                                            if F(4, d) * (2 - F(1, 2 ** dots)) == written_duration), None)
                    denominator = integer(denominator, loc) if denominator is not None else None
                    if denominator not in (1, 2, 4, 8, 16, 32) or not 0 <= dots <= 2:
                        track.notation_unavailable.append(loc)
                    ratio = F(4, denominator) * (2 - F(1, 2 ** dots)) / written_duration if denominator and denominator > 0 else F(1)
                    written = {"dur": denominator, "dot": dots, "tu": [ratio.numerator, ratio.denominator] if ratio != 1 else None}
                    for field, output in {"velocity": "dyn", "vibrato": "vib", "wideVibrato": "vibw", "palmMute": "pm",
                                          "letRing": "lr", "tap": "tap", "tapping": "tap", "slap": "slap", "pop": "pop", "slapping": "slap", "popping": "pop"}.items():
                        if beat.get(field):
                            written[output] = beat[field]
                    if beat.get("text"):
                        written["txt"] = str(beat["text"].get("text", "")) if isinstance(beat["text"], dict) else str(beat["text"])
                    if beat.get("gradualVelocity"):
                        if beat["gradualVelocity"] not in {"crescendo", "decrescendo"}:
                            unsupported(loc, "This gradual dynamic is not independently verified.")
                        written["cre" if beat["gradualVelocity"] == "crescendo" else "dec"] = True
                    grace = beat.get("graceNote")
                    if grace not in (None, "onBeat", "beforeBeat"):
                        unsupported(loc, "This grace-note timing is not independently verified.")
                    if duration <= 0:
                        raise ValueError(f"{loc}: nonpositive performed beat duration")
                    fact = {"q": q, "length": duration, "voice": str(vi), "location": loc, "written_q": written_q,
                            "rest": not any(not n.get("rest") for n in beat["notes"]), "notation": written, "notes": [], "chord_label": label.get("text", "")}
                    if grace:
                        fact["notation"]["grace"] = "a" if grace == "beforeBeat" else "p"
                    beat_facts.append(fact)
                    for ni, note in enumerate(beat["notes"]):
                        nloc = loc + f"/notes/{ni}"
                        if note.get("rest"):
                            continue
                        _active_unknown(note, note_keys, {"id", "velocity", "finger", "leftFinger", "rightFinger"}, nloc, ignored)
                        fx = {}
                        if note.get("leftHandVibrato") not in (None, "slight", "wide"):
                            unsupported(nloc + "/leftHandVibrato", "Unverified vibrato width.")
                        if note.get("leftHandVibrato"):
                            fx["vb"] = True
                        picking = beat.get("pickStroke")
                        if picking is None:
                            picking = direction
                        if picking not in (None, "up", "down"):
                            unsupported(loc + "/pickStroke", "Unverified pick direction.")
                        if picking is not None:
                            fx["pkd"] = {"down": 0, "up": 1}[picking]
                        for key, out in {"dead": "mt", "vibrato": "vb", "wideVibrato": "vb", "ghost": "ghost",
                                         "accentuated": "ac", "tap": "tp", "tapping": "tp"}.items():
                            if note.get(key):
                                fx[out] = True
                        for key, out in {"palmMute": "pm", "letRing": "lr", "tremolo": "tr", "tap": "tp", "tapping": "tp",
                                         "slap": "slp", "pop": "plk", "slapping": "slp", "popping": "plk", "vibrato": "vb", "wideVibrato": "vb"}.items():
                            if beat.get(key):
                                fx[out] = True
                        harmonic = note.get("harmonic")
                        if note.get("harmonicFret"):
                            unsupported(nloc, "Harmonic-fret pitch interpretation is not independently verified.")
                        if harmonic:
                            if harmonic not in {"natural", "pinch"}:
                                unsupported(nloc, "This harmonic type is not independently verified.")
                            fx[{"natural": "hm", "pinch": "hp"}[harmonic]] = True
                        slide = {None: None, "above": None, "below": None,
                                 "shift": "shift", "aboveshift": "shift", "belowshift": "shift",
                                 "legato": "legato", "abovelegato": "legato", "belowlegato": "legato",
                                 "upwards": "up", "aboveupwards": "up", "belowupwards": "up",
                                 "downwards": "down", "abovedownwards": "down", "belowdownwards": "down"}
                        raw_slide = note.get("slide")
                        if raw_slide is not None and not isinstance(raw_slide, str) or raw_slide not in slide:
                            unsupported(nloc, "This slide type is not independently verified.")
                        bends = []
                        if note.get("bend"):
                            _active_unknown(note["bend"], {"points", "tone"}, set(), nloc + "/bend", ignored)
                            for point in note["bend"]["points"]:
                                _active_unknown(point, {"position", "precisePosition", "tone"}, set(), nloc + "/bend/points", ignored)
                            points = note["bend"]["points"]
                            precision_count = sum("precisePosition" in p for p in points)
                            if precision_count not in (0, len(points)):
                                raise ValueError(nloc + ": incomplete precise bend coordinates")
                            coords = [fraction(p["precisePosition"], nloc) / 100 if precision_count
                                      else fraction(p["position"], nloc) / 60 for p in points]
                            coarse = [fraction(p["position"], nloc) for p in points]
                            values = [fraction(p["tone"], nloc) / 50 for p in points]
                            if (coords != sorted(coords) or coarse != sorted(coarse)
                                    or any(not 0 <= p <= 1 for p in coords)
                                    or any(not 0 <= p <= 60 for p in coarse)
                                    or any(not 0 <= v <= 8 for v in values)):
                                raise ValueError(nloc + ": invalid bend coordinates")
                            # Independent last-value selection at duplicate coordinates.
                            bends = list(dict(zip(coords, values)).items())
                            if not bends:
                                raise ValueError(f"{nloc}: missing bend points")
                        attack = q + offsets.get(ni, F(0))
                        length = q + duration - attack
                        if attack < 0 or length <= 0:
                            unsupported(nloc, "Strum crosses an unresolved timing boundary.")
                        if note.get("staccato"):
                            if note["staccato"] is not True or any(note.get(k) for k in ("tie", "hp", "slide", "bend")):
                                unsupported(nloc, "Linked staccato is not independently verified.")
                            reduced = max(length / 2, F(1, 32))
                            if reduced > length:
                                unsupported(nloc, "Staccato minimum exceeds authored duration.")
                            length = reduced
                        atoms.append(Atom(attack, length, len(tuning) - 1 - integer(note["string"], nloc), integer(note["fret"], nloc),
                                          nloc, str(vi), loc, bool(note.get("tie")), fx, sorted(bends), slide[note.get("slide")],
                                          bool(note.get("hp"))))
                        fact["notes"].append(atoms[-1])
                        atoms[-1].wide_vibrato = bool(note.get("wideVibrato")) or note.get("leftHandVibrato") == "wide"
                        if raw_slide in {"above", "aboveshift", "abovelegato", "aboveupwards", "abovedownwards"}:
                            atoms[-1].slide_in = "down"
                        elif raw_slide in {"below", "belowshift", "belowlegato", "belowupwards", "belowdownwards"}:
                            atoms[-1].slide_in = "up"
            track.bars.append(atoms)
            track.beats.append(beat_facts)
        parts.append(track)
    return Source("songsterr", str(document.get("title", "")), str(document.get("artist", "")), bars, parts,
                  excluded, len(metadata), {k: str(document[k]) for k in ("songId", "revisionId") if k in document}, ignored)


def _txt(node, path, default=""):
    return str(node.findtext(path, default) or default).strip()


def gpif(data):
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise ValueError("source: XML declarations/entities are not accepted")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise ValueError("source: invalid GPIF XML") from exc
    if root.tag != "GPIF" or not _txt(root, "GPVersion").startswith(("7.", "8.")):
        unsupported("source", "Only GPIF 7/8 is independently verified.")
    tables = {}
    for name in ("Tracks", "Bars", "Voices", "Beats", "Notes", "Rhythms"):
        rows = root.findall(name + "/*")
        tables[name] = {row.attrib["id"]: row for row in rows}
        if len(rows) != len(tables[name]):
            raise ValueError(f"{name}: duplicate source ID")
    masters, bars, signature = root.findall("MasterBars/MasterBar"), [], (4, 4)
    for bi, mb in enumerate(masters):
        loc = f"MasterBars/{bi}"
        unknown = {child.tag for child in mb} - {"Time", "Bars", "Repeat", "AlternateEndings", "Section", "TripletFeel",
                                                   "Directions", "Fermatas", "FreeTime", "Anacrusis", "Key", "DoubleBar", "XProperties"}
        if unknown:
            unsupported(loc, "Unverified GPIF master-bar fields: " + ", ".join(sorted(unknown)))
        if _txt(mb, "Time"):
            signature = tuple(int(v) for v in _txt(mb, "Time").split("/"))
        if len(signature) != 2 or min(signature) < 1:
            raise ValueError(f"{loc}: invalid meter")
        for key in ("Directions", "Fermatas", "FreeTime", "Anacrusis"):
            if mb.find(key) is not None:
                unsupported(loc + "/" + key, "GPIF navigation/free-time feature is not independently verified.")
        if _txt(mb, "TripletFeel") not in ("", "NoTripletFeel", "None", "0"):
            unsupported(loc, "GPIF swing is not independently verified.")
        rep = mb.find("Repeat")
        bars.append(Bar(signature, rep is not None and rep.get("start") == "true",
                        int(rep.get("count", "2")) if rep is not None and rep.get("end") == "true" else 0,
                        frozenset(int(x) for x in re.split(r"[\s,]+", _txt(mb, "AlternateEndings")) if x),
                        _txt(mb, "Section/Text") or _txt(mb, "Section/Letter")))
    for auto in root.findall("MasterTrack/Automations/Automation"):
        kind = _txt(auto, "Type")
        if kind in {"Volume", "Balance", "Pan"}:
            continue
        loc = "MasterTrack/Automations"
        if kind != "Tempo":
            unsupported(loc, "This GPIF automation type is not independently verified.")
        unknown = {child.tag for child in auto} - {"Type", "Linear", "Bar", "Position", "Value", "Visible"}
        if unknown:
            unsupported(loc, "Unverified GPIF tempo fields: " + ", ".join(sorted(unknown)))
        if _txt(auto, "Linear") == "true":
            unsupported(loc, "GPIF tempo ramps are not independently verified.")
        bi = int(_txt(auto, "Bar", "0"))
        value = _txt(auto, "Value").split()
        if len(value) > 1 and value[1] != "2":
            unsupported(loc, "This GPIF tempo unit is not independently verified.")
        bars[bi].tempos[fraction(_txt(auto, "Position", "0")) * bars[bi].length] = fraction(value[0])
    ids = _txt(root, "MasterTrack/Tracks").split() or list(tables["Tracks"])
    if len(set(ids)) != len(ids) or set(ids) != set(tables["Tracks"]):
        raise ValueError("MasterTrack/Tracks: inconsistent track list")
    parts, excluded, column = [], [], 0
    rhythm_values = {"Long": F(16), "DoubleWhole": F(8), "Whole": F(4), "Half": F(2), "Quarter": F(1),
                     "Eighth": F(1, 2), **{f"{n}th": F(4, n) for n in (16, 64, 128, 256)}, "32nd": F(1, 8)}
    for tid in ids:
        raw = tables["Tracks"][tid]
        name = _txt(raw, "Name", f"Track {tid}")
        staves = raw.findall("Staves/Staff") or [raw]
        kind = _program_instrument({"type": _txt(raw, "InstrumentSet/Type"),
                                    "midiProgram": int(_txt(raw, "Sounds/Sound/MIDI/Program", "-1"))})
        col = column
        column += len(staves)
        if not kind:
            excluded.append({"id": tid, "name": name, "reason": "non_playable_instrument"})
            continue
        if len(staves) != 1:
            unsupported(f"Tracks/{tid}", "Multiple GPIF staves are not independently verified.")
        props = {p.get("name"): p for p in staves[0].findall("Properties/Property")}
        if "PartialCapoFret" in props and int("".join(props["PartialCapoFret"].itertext()).strip() or 0):
            unsupported(f"Tracks/{tid}", "Partial capo is not independently verified.")
        tuning = [int(x) for x in _txt(props["Tuning"], "Pitches").split()]
        capo = int("".join(props["CapoFret"].itertext()).strip()) if "CapoFret" in props else 0
        track = Part(tid, name, kind, tuning, capo, [], [])
        for bi, mb in enumerate(masters):
            rawbar = tables["Bars"][_txt(mb, "Bars").split()[col]]
            atoms, beat_facts = [], []
            for voice_slot, vid in enumerate(_txt(rawbar, "Voices").split()):
                if vid == "-1":
                    continue
                q = F(0)
                for bid in _txt(tables["Voices"][vid], "Beats").split():
                    beat = tables["Beats"][bid]
                    loc = f"Tracks/{tid}/measures/{bi}/voices/{voice_slot}/Beats/{bid}"
                    known_beat_tags = {"Rhythm", "Notes", "Tremolo", "Slapped", "Popped", "Dynamic", "FreeText", "Properties", "XProperties"}
                    unknown = {child.tag for child in beat} - known_beat_tags
                    if unknown:
                        unsupported(loc, "Unverified GPIF beat fields: " + ", ".join(sorted(unknown)))
                    if any(p.get("name") not in {"StemDirection", "BeamingMode"} for p in beat.findall("Properties/Property")):
                        unsupported(loc, "Unverified GPIF beat property.")
                    for key in ("GraceNotes", "Whammy", "Arpeggio", "Brush"):
                        if beat.find(key) is not None:
                            unsupported(loc + "/" + key, "This GPIF beat timing is not independently verified.")
                    rhythm = tables["Rhythms"][beat.find("Rhythm").attrib["ref"]]
                    unknown = {child.tag for child in rhythm} - {"NoteValue", "AugmentationDot", "PrimaryTuplet", "SecondaryTuplet"}
                    if unknown:
                        unsupported(loc, "Unverified GPIF rhythm fields: " + ", ".join(sorted(unknown)))
                    duration = rhythm_values[_txt(rhythm, "NoteValue")]
                    denominator = 4 / duration
                    dot = rhythm.find("AugmentationDot")
                    dots = int(dot.get("count", "1")) if dot is not None else 0
                    if not 0 <= dots <= 4:
                        unsupported(loc, "This dot count is not independently verified.")
                    duration *= 2 - F(1, 2 ** dots)
                    tuplet = rhythm.find("PrimaryTuplet")
                    if tuplet is not None:
                        duration *= F(int(tuplet.attrib["den"]), int(tuplet.attrib["num"]))
                    if rhythm.find("SecondaryTuplet") is not None:
                        unsupported(loc, "Nested GPIF tuplets are not independently verified.")
                    if denominator.denominator != 1 or int(denominator) not in (1, 2, 4, 8, 16, 32) or dots > 2:
                        unsupported(loc, "Written duration is outside FeedPak notation v1; raw source must remain available.")
                    note_ids = _txt(beat, "Notes").split()
                    fact = {"q": q, "length": duration, "voice": str(voice_slot), "location": loc, "rest": not note_ids,
                            "written_q": q, "notes": [], "notation": {"dur": int(denominator), "dot": dots,
                                "tu": [int(tuplet.attrib["num"]), int(tuplet.attrib["den"])] if tuplet is not None else None}}
                    if _txt(beat, "Dynamic"):
                        fact["notation"]["dyn"] = _txt(beat, "Dynamic").lower()
                    if _txt(beat, "FreeText"):
                        fact["notation"]["txt"] = _txt(beat, "FreeText")
                    beat_facts.append(fact)
                    for nid in note_ids:
                        node = tables["Notes"][nid]
                        p = {n.get("name"): n for n in node.findall("Properties/Property")}
                        val = lambda key: (list(p[key])[0].text or "").strip()
                        enabled = lambda key: key in p and p[key].find("Enable") is not None
                        nloc = loc + "/Notes/" + nid
                        unknown = {child.tag for child in node} - {"Properties", "Tie", "Vibrato", "Accent", "AntiAccent", "XProperties"}
                        if unknown:
                            unsupported(nloc, "Unverified GPIF note fields: " + ", ".join(sorted(unknown)))
                        known = {"String", "Fret", "Midi", "ConcertPitch", "TransposedPitch", "Octave", "Tone", "Variation",
                                 "Muted", "PalmMuted", "Tapped", "LeftHandTapped", "RightHandTapped", "LetRing", "Vibrato",
                                 "HopoOrigin", "HopoDestination", "Harmonic", "HarmonicType", "HarmonicFret", "Slide", "Bended",
                                 "BendOriginOffset", "BendOriginValue", "BendMiddleOffset1", "BendMiddleOffset2", "BendMiddleValue",
                                 "BendDestinationOffset", "BendDestinationValue"}
                        if set(p) - known:
                            unsupported(nloc, "Unknown GPIF note property: " + ", ".join(sorted(set(p) - known)))
                        if "HarmonicFret" in p and fraction(val("HarmonicFret")):
                            unsupported(nloc, "GPIF harmonic-fret pitch interpretation is not independently verified.")
                        for key in ("Trill", "Ornament"):
                            if node.find(key) is not None:
                                unsupported(nloc + "/" + key, "This GPIF technique is not independently verified.")
                        if _txt(node, "Vibrato").lower() not in {"", "slight", "wide"}:
                            unsupported(nloc + "/Vibrato", "This GPIF vibrato width is not independently verified.")
                        fx = {out: True for key, out in {"Muted": "mt", "PalmMuted": "pm", "Tapped": "tp", "LeftHandTapped": "tp",
                                                        "RightHandTapped": "tp", "LetRing": "lr", "Vibrato": "vb"}.items() if enabled(key)}
                        for key, out in {"Vibrato": "vb", "Accent": "ac", "AntiAccent": "ghost"}.items():
                            if node.find(key) is not None:
                                fx[out] = True
                        for key, out in {"Tremolo": "tr", "Slapped": "slp", "Popped": "plk"}.items():
                            if beat.find(key) is not None:
                                fx[out] = True
                        if enabled("Harmonic"):
                            harmonic = val("HarmonicType")
                            if harmonic not in {"natural", "pinch"}:
                                unsupported(nloc, "This GPIF harmonic is not independently verified.")
                            fx[{"natural": "hm", "pinch": "hp"}[harmonic]] = True
                        slides = {out + incoming: (kind, direction)
                                  for out, kind in ((0, None), (1, "shift"), (2, "legato"), (4, "down"), (8, "up"))
                                  for incoming, direction in ((0, None), (16, "up"), (32, "down"))}
                        flag = int(val("Slide")) if "Slide" in p else 0
                        if flag not in slides:
                            unsupported(nloc, "This GPIF slide combination is not independently verified.")
                        bends = []
                        if enabled("Bended"):
                            for prefix, value_key in (("Origin", "Origin"), ("MiddleOffset1", "Middle"), ("MiddleOffset2", "Middle"), ("Destination", "Destination")):
                                off = "Bend" + (prefix if prefix.startswith("Middle") else prefix + "Offset")
                                value_key = "Bend" + value_key + "Value"
                                if off in p and value_key in p:
                                    bends.append((fraction(val(off)) / 100, fraction(val(value_key)) / 50))
                            bends = sorted(set(bends))
                            if not bends:
                                raise ValueError(f"{nloc}: bend has no points")
                        tie = node.find("Tie")
                        atoms.append(Atom(q, duration, int(val("String")), int(val("Fret")), nloc, str(voice_slot), loc,
                                          tie is not None and tie.get("destination") == "true", fx, bends, slides[flag][0], enabled("HopoOrigin"),
                                          enabled("HopoDestination")))
                        fact["notes"].append(atoms[-1])
                        atoms[-1].wide_vibrato = _txt(node, "Vibrato").lower() == "wide"
                        atoms[-1].slide_in = slides[flag][1]
                    q += duration
            track.bars.append(atoms)
            track.beats.append(beat_facts)
        parts.append(track)
    if any(len(_txt(mb, "Bars").split()) != column for mb in masters):
        raise ValueError("MasterBars: incomplete staff columns")
    return Source("gpif", _txt(root, "Score/Title"), _txt(root, "Score/Artist"), bars, parts, excluded, len(ids))


def read_source(path: Path):
    if path.stat().st_size > MAX_SOURCE:
        raise ValueError("source: verification size limit exceeded")
    if path.suffix.lower() == ".json":
        return songsterr(json.loads(path.read_text(encoding="utf-8-sig")))
    if path.suffix.lower() in {".gpif", ".xml"}:
        return gpif(path.read_bytes())
    with ZipFile(path) as archive:
        info = archive.getinfo("Content/score.gpif")
        if info.file_size > MAX_SOURCE:
            raise ValueError("source: expanded verification size limit exceeded")
        return gpif(archive.read(info))

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
            _active_unknown(b, {"voices", "signature", "rest", "marker", "repeat", "repeatStart", "alternateEnding", "tripletFeel"},
                            {"width", "id", "index"}, loc, ignored)
            if b.get("tripletFeel") not in (None, "off"):
                unsupported(loc + "/tripletFeel", "Swing timing is not independently verified.")
        markers = [b["marker"] for b in samples if b.get("marker")]
        names = {str(m.get("text", "")) if isinstance(m, dict) else str(m) for m in markers}
        if len(names) > 1:
            unsupported(loc + "/marker", "Source tracks have different section markers.")
        bars.append(Bar(signature, any(b.get("repeatStart") for b in samples), next(iter(repeats), 0),
                        next(iter(endings), frozenset()), next(iter(names), "")))
    for pi, raw in enumerate(raw_parts):
        automations = raw.get("automations", {})
        _active_unknown(automations, {"tempo"}, {"volume", "balance"}, f"parts/{pi}/automations", ignored)
        for tempo in automations.get("tempo", []):
            loc = f"parts/{pi}/automations/tempo"
            _active_unknown(tempo, {"measure", "position", "bpm", "type", "linear"}, set(), loc, ignored)
            if tempo.get("linear"):
                unsupported(loc, "Linear tempo ramps are not independently verified.")
            bi = integer(tempo["measure"], loc)
            q = fraction(tempo.get("position", 0), loc) * 4
            bpm = fraction(tempo["bpm"], loc) * F(4, integer(tempo.get("type", 4), loc))
            if not 0 <= bi < count or not 0 <= q < bars[bi].length or bpm <= 0:
                raise ValueError(f"{loc}: invalid tempo coordinate")
            if q in bars[bi].tempos and bars[bi].tempos[q] != bpm:
                unsupported(loc, "Source tracks disagree about tempo.")
            bars[bi].tempos[q] = bpm
    parts, excluded = [], []
    note_keys = {"string", "fret", "tie", "rest", "dead", "vibrato", "wideVibrato", "ghost", "accentuated",
                 "tap", "tapping", "hp", "harmonic", "harmonicFret", "slide", "bend"}
    beat_keys = {"duration", "notes", "rest", "type", "dots", "tuplet", "tupletStart", "tupletStop", "graceNote",
                 "palmMute", "letRing", "tremolo", "tap", "tapping", "slap", "pop", "vibrato", "wideVibrato", "text", "velocity", "gradualVelocity"}
    for pi, (meta, raw) in enumerate(zip(metadata, raw_parts)):
        tid = str(meta.get("id", pi))
        name = str(meta.get("name") or meta.get("title") or meta.get("instrument") or f"Track {pi + 1}")
        kind = _program_instrument(meta)
        if not kind:
            excluded.append({"id": tid, "name": name, "reason": "non_playable_instrument"})
            continue
        tuning = [integer(v) for v in reversed(raw.get("tuning") or meta["tuning"])]
        track = Part(tid, name, kind, tuning, integer(raw.get("capo", meta.get("capo", 0))), [], [])
        for bi, bar in enumerate(raw["measures"]):
            atoms, beat_facts = [], []
            for vi, voice in enumerate(bar["voices"]):
                _active_unknown(voice, {"beats", "rest"}, {"id"}, f"parts/{pi}/measures/{bi}/voices/{vi}", ignored)
                q, borrowed = F(0), F(0)
                for bti, beat in enumerate(voice["beats"]):
                    loc = f"parts/{pi}/measures/{bi}/voices/{vi}/beats/{bti}"
                    _active_unknown(beat, beat_keys, {"beamStart", "beamStop", "id"}, loc, ignored)
                    duration = fraction(beat["duration"], loc) * 4
                    written_duration, written_q = duration, q - borrowed
                    dots = integer(beat.get("dots", 0), loc)
                    denominator = beat.get("type")
                    if denominator is None:
                        denominator = next((d for d in (1, 2, 4, 8, 16, 32, 64, 128, 256)
                                            if F(4, d) * (2 - F(1, 2 ** dots)) == duration), None)
                    if denominator is None:
                        unsupported(loc, "Written duration cannot be independently represented in notation.")
                    denominator = integer(denominator, loc)
                    if denominator not in (1, 2, 4, 8, 16, 32) or not 0 <= dots <= 2:
                        unsupported(loc, "Written duration is outside FeedPak notation v1; raw source must remain available.")
                    ratio = F(4, denominator) * (2 - F(1, 2 ** dots)) / written_duration
                    written = {"dur": denominator, "dot": dots, "tu": [ratio.numerator, ratio.denominator] if ratio != 1 else None}
                    for field, output in {"velocity": "dyn", "vibrato": "vib", "wideVibrato": "vibw", "palmMute": "pm",
                                          "letRing": "lr", "tap": "tap", "tapping": "tap", "slap": "slap", "pop": "pop"}.items():
                        if beat.get(field):
                            written[output] = beat[field]
                    if beat.get("text"):
                        written["txt"] = str(beat["text"].get("text", "")) if isinstance(beat["text"], dict) else str(beat["text"])
                    if beat.get("gradualVelocity"):
                        if beat["gradualVelocity"] not in {"crescendo", "decrescendo"}:
                            unsupported(loc, "This gradual dynamic is not independently verified.")
                        written["cre" if beat["gradualVelocity"] == "crescendo" else "dec"] = True
                    grace = beat.get("graceNote")
                    if grace not in (None, "onBeat"):
                        unsupported(loc, "This grace-note timing is not independently verified.")
                    if grace:
                        borrowed += duration
                    elif borrowed:
                        duration -= borrowed
                        borrowed = F(0)
                    if duration <= 0:
                        raise ValueError(f"{loc}: nonpositive performed beat duration")
                    fact = {"q": q, "length": duration, "voice": str(vi), "location": loc, "written_q": written_q,
                            "rest": not any(not n.get("rest") for n in beat["notes"]), "notation": written, "notes": []}
                    if grace:
                        fact["notation"]["grace"] = "p"
                    beat_facts.append(fact)
                    for ni, note in enumerate(beat["notes"]):
                        nloc = loc + f"/notes/{ni}"
                        if note.get("rest"):
                            continue
                        _active_unknown(note, note_keys, {"id", "velocity", "finger", "leftFinger", "rightFinger"}, nloc, ignored)
                        fx = {}
                        for key, out in {"dead": "mt", "vibrato": "vb", "wideVibrato": "vb", "ghost": "ghost",
                                         "accentuated": "ac", "tap": "tp", "tapping": "tp"}.items():
                            if note.get(key):
                                fx[out] = True
                        for key, out in {"palmMute": "pm", "letRing": "lr", "tremolo": "tr", "tap": "tp", "tapping": "tp",
                                         "slap": "slp", "pop": "plk", "vibrato": "vb", "wideVibrato": "vb"}.items():
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
                                _active_unknown(point, {"position", "tone"}, set(), nloc + "/bend/points", ignored)
                            bends = [(fraction(p["position"], nloc) / 60, fraction(p["tone"], nloc) / 50)
                                     for p in note["bend"]["points"]]
                            if not bends:
                                raise ValueError(f"{nloc}: missing bend points")
                        atoms.append(Atom(q, duration, len(tuning) - 1 - integer(note["string"], nloc), integer(note["fret"], nloc),
                                          nloc, str(vi), loc, bool(note.get("tie")), fx, sorted(bends), slide[note.get("slide")],
                                          bool(note.get("hp"))))
                        fact["notes"].append(atoms[-1])
                        atoms[-1].wide_vibrato = bool(note.get("wideVibrato"))
                        if raw_slide in {"above", "aboveshift", "abovelegato", "aboveupwards", "abovedownwards"}:
                            atoms[-1].slide_in = "down"
                        elif raw_slide in {"below", "belowshift", "belowlegato", "belowupwards", "belowdownwards"}:
                            atoms[-1].slide_in = "up"
                    q += duration
                if borrowed:
                    unsupported(loc, "A grace note has no independently resolvable principal note.")
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

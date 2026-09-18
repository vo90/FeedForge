"""Read GP7/8's ZIP/GPIF score format using only Python's standard library.

This is FeedForge code, not a copy of the game's AGPL Guitar Pro converter.
Positions, notes, string order and techniques are read from the score itself.
Unsupported timing/navigation is reported rather than silently flattened.
"""

import base64
from fractions import Fraction
from pathlib import Path
import re
import xml.etree.ElementTree as ET
from zipfile import BadZipFile, ZipFile

from .inventory import FeatureInventory
from .model import Measure, Note, Score, ScoreImportError, Track, WrittenBeat, WrittenVoice, integer, rational

MAX_XML = 64 * 1024 * 1024
VALUES = {"Long": 16, "DoubleWhole": 8, "Whole": 4, "Half": 2, "Quarter": 1,
          "Eighth": Fraction(1, 2), "16th": Fraction(1, 4), "32nd": Fraction(1, 8),
          "64th": Fraction(1, 16), "128th": Fraction(1, 32), "256th": Fraction(1, 64)}


def text(element, path, default=""):
    return (element.findtext(path) or default).strip()


def props(element) -> dict:
    return {p.get("name"): p for p in element.findall("Properties/Property")}


def property_value(properties, name, default=""):
    node = properties.get(name)
    if node is None or len(node) == 0:
        return default
    return (node[0].text or default).strip()


def enabled(properties, name):
    item = properties.get(name)
    return item is not None and item.find("Enable") is not None


def required_table(root, name):
    parent = root.find(name)
    if parent is None:
        raise ScoreImportError(f"Missing GPIF {name} table.")
    result = {}
    for item in parent:
        key = item.get("id")
        if key is None or key in result:
            raise ScoreImportError(f"Missing/duplicate identifier in GPIF {name}.")
        result[key] = item
    return result


def resolve(table, key, label):
    if key not in table:
        raise ScoreImportError(f"Missing GPIF {label} reference: {key}.")
    return table[key]


def _duration(beat, rhythms):
    ref = beat.find("Rhythm")
    if ref is None:
        raise ScoreImportError("A beat is missing its rhythm.")
    rhythm = resolve(rhythms, ref.get("ref"), "rhythm")
    value = text(rhythm, "NoteValue")
    if value not in VALUES:
        raise ScoreImportError(f"Unsupported note duration: {value}.")
    duration = Fraction(VALUES[value])
    dot = rhythm.find("AugmentationDot")
    dots = integer(dot.get("count", "1"), "dot count") if dot is not None else 0
    if not 0 <= dots <= 4:
        raise ScoreImportError("Unsupported augmentation dot count.")
    duration *= sum((Fraction(1, 2 ** i) for i in range(dots + 1)), Fraction(0))
    tuplet = rhythm.find("PrimaryTuplet")
    if tuplet is not None:
        n, d = integer(tuplet.get("num"), "tuplet count"), integer(tuplet.get("den"), "tuplet count")
        if n <= 0 or d <= 0:
            raise ScoreImportError("Invalid tuplet ratio.")
        duration *= Fraction(d, n)
    if rhythm.find("SecondaryTuplet") is not None:
        raise ScoreImportError("Nested GPIF tuplets are not supported yet.")
    return duration


def _written_rhythm(beat, rhythms):
    rhythm = resolve(rhythms, beat.find("Rhythm").get("ref"), "rhythm")
    value = Fraction(4) / Fraction(VALUES[text(rhythm, "NoteValue")])
    dot = rhythm.find("AugmentationDot")
    dots = integer(dot.get("count", "1"), "dot count") if dot is not None else 0
    tuplet = rhythm.find("PrimaryTuplet")
    ratio = (integer(tuplet.get("num"), "tuplet count"), integer(tuplet.get("den"), "tuplet count")) if tuplet is not None else None
    return int(value) if value.denominator == 1 else None, dots, ratio


def _note(node, position, duration, beat):
    p = props(node)
    known = {"ConcertPitch", "TransposedPitch", "Fret", "String", "Midi", "Octave",
             "Tone", "Variation", "Bended", "Slide", "Harmonic", "HarmonicType",
             "HarmonicFret", "HopoOrigin", "HopoDestination", "Muted", "PalmMuted",
             "Tapped", "LeftHandTapped", "RightHandTapped", "LetRing", "Vibrato",
             "BendDestinationOffset", "BendDestinationValue", "BendOriginOffset",
             "BendOriginValue", "BendMiddleOffset1", "BendMiddleOffset2", "BendMiddleValue"}
    unfamiliar = set(p) - known
    if unfamiliar:
        raise ScoreImportError(f"Unsupported GPIF note property: {', '.join(sorted(unfamiliar))}.")
    if any(node.find(tag) is not None for tag in ("Trill", "Ornament")):
        raise ScoreImportError("Trills/ornaments require additional conversion support.")
    if "HarmonicFret" in p and rational(property_value(p, "HarmonicFret", "0"), "harmonic fret"):
        raise ScoreImportError("GPIF harmonic-fret interpretation requires additional conversion support.")
    string = integer(property_value(p, "String", None), "string index")
    fret = integer(property_value(p, "Fret", None), "fret")
    effects = {}
    for key, target in {"Muted": "mt", "PalmMuted": "pm", "Tapped": "tp",
                        "LeftHandTapped": "tp", "RightHandTapped": "tp", "LetRing": "lr"}.items():
        if enabled(p, key):
            effects[target] = True
    if enabled(p, "HopoOrigin"):
        effects["__hopo_origin"] = True
    vibrato = text(node, "Vibrato")
    if vibrato not in {"", "Slight", "Wide"}:
        raise ScoreImportError(f"Unsupported GPIF vibrato: {vibrato}.")
    if node.find("Vibrato") is not None or enabled(p, "Vibrato"):
        effects["vb"] = True
    if vibrato == "Wide":
        effects["__wide_vibrato"] = True
    if node.find("Accent") is not None:
        effects["ac"] = True
    if node.find("AntiAccent") is not None:
        effects["ghost"] = True
    harmonic = property_value(p, "HarmonicType")
    if enabled(p, "Harmonic"):
        if harmonic == "natural":
            effects["hm"] = True
        elif harmonic == "pinch":
            effects["hp"] = True
        else:
            raise ScoreImportError(f"Unsupported harmonic type: {harmonic or 'unspecified'}.")
    if beat.find("Tremolo") is not None:
        effects["tr"] = True
    if beat.find("Slapped") is not None:
        effects["slp"] = True
    if beat.find("Popped") is not None:
        effects["plk"] = True
    tie = node.find("Tie")
    tied = tie is not None and tie.get("destination", "false").lower() == "true"
    slide, slide_in = None, None
    if "Slide" in p:
        flags = integer(property_value(p, "Slide"), "slide flags")
        choices = {0: None, 1: "shift", 2: "legato", 4: "out_down", 8: "out_up"}
        outgoing, incoming = flags & 15, flags & 48
        if flags < 0 or flags & ~63 or outgoing not in choices or incoming not in {0, 16, 32}:
            raise ScoreImportError(f"Unsupported slide flags: {flags}.")
        slide = choices[outgoing]
        slide_in = {0: None, 16: "up", 32: "down"}[incoming]
    bends = []
    if enabled(p, "Bended"):
        for offset, value in (("BendOriginOffset", "BendOriginValue"),
                              ("BendMiddleOffset1", "BendMiddleValue"),
                              ("BendMiddleOffset2", "BendMiddleValue"),
                              ("BendDestinationOffset", "BendDestinationValue")):
            if offset in p and value in p:
                bends.append((rational(property_value(p, offset), "bend position") / 100,
                              float(rational(property_value(p, value), "bend value") / 50)))
        if not bends:
            raise ScoreImportError("Bend has no curve values.")
        bends = sorted(set(bends))
    return Note(position, duration, string, fret, tied, effects, bends,
                enabled(p, "HopoDestination"), slide, slide_in=slide_in)


def parse(path: Path) -> Score:
    try:
        if path.suffix.lower() in {".gpif", ".xml"}:
            if path.stat().st_size > MAX_XML:
                raise ScoreImportError("GPIF score is too large.")
            data = path.read_bytes()
        else:
            with ZipFile(path) as archive:
                info = archive.getinfo("Content/score.gpif")
                if info.file_size > MAX_XML:
                    raise ScoreImportError("GPIF score is too large.")
                data = archive.read(info)
    except (BadZipFile, KeyError) as exc:
        raise ScoreImportError("Expected a GP7/GP8 ZIP containing Content/score.gpif.") from exc
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise ScoreImportError("XML entities are not supported in imported scores.")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise ScoreImportError("Invalid GPIF XML.") from exc
    version = text(root, "GPVersion")
    inventory = FeatureInventory()
    if root.tag != "GPIF" or not version.startswith(("7.", "8.")):
        raise ScoreImportError("This importer currently supports GP7/GP8 scores only.")
    masterbars = root.findall("MasterBars/MasterBar")
    measures = []
    numerator, denominator = 4, 4
    for mb in masterbars:
        sig = text(mb, "Time")
        if sig:
            try:
                numerator, denominator = (int(n) for n in sig.split("/"))
            except (ValueError, TypeError) as exc:
                raise ScoreImportError("Invalid GPIF time signature.") from exc
        if numerator < 1 or denominator < 1:
            raise ScoreImportError("Invalid GPIF time signature.")
        if any(mb.find(tag) is not None for tag in ("Directions", "Fermatas", "FreeTime", "Anacrusis")):
            raise ScoreImportError("Navigation, fermatas, free time or pickup bars require additional support.")
        swing = text(mb, "TripletFeel")
        if swing and swing not in {"NoTripletFeel", "None", "0"}:
            raise ScoreImportError("Swing/triplet feel is not supported yet.")
        repeat = mb.find("Repeat")
        end = repeat is not None and repeat.get("end", "false") == "true"
        count = integer(repeat.get("count", "2"), "repeat count") if end else 0
        ending_text = text(mb, "AlternateEndings")
        endings = frozenset(integer(x, "alternate ending") for x in re.split(r"[\s,]+", ending_text) if x)
        section = text(mb, "Section/Text") or text(mb, "Section/Letter")
        measures.append(Measure(numerator, denominator, Fraction(4 * numerator, denominator),
                                repeat is not None and repeat.get("start", "false") == "true",
                                count, endings, section))
    if not measures:
        raise ScoreImportError("GPIF contains no measures.")
    for auto_index, auto in enumerate(root.findall("MasterTrack/Automations/Automation")):
        automation_type = text(auto, "Type")
        if automation_type in {"Volume", "Balance", "Pan"}:
            inventory.record("GPIF automation", automation_type, "source", f"MasterTrack/Automations/{auto_index}")
            continue
        if automation_type != "Tempo":
            raise ScoreImportError(f"Unsupported GPIF automation: {automation_type or 'unspecified'}.")
        inventory.inspect({child.tag: True for child in auto}, "GPIF tempo", f"MasterTrack/Automations/{auto_index}",
                          playable={"Type", "Linear", "Bar", "Position", "Value"}, retained={"Visible"}, strict=True)
        if text(auto, "Linear").lower() == "true":
            raise ScoreImportError("Linear tempo ramps require additional conversion support.")
        bi = integer(text(auto, "Bar", "0"), "tempo measure")
        if not 0 <= bi < len(measures):
            raise ScoreImportError("Tempo references a missing measure.")
        ratio = rational(text(auto, "Position", "0"), "tempo position")
        raw = text(auto, "Value").split()
        if not raw:
            raise ScoreImportError("Tempo value is missing.")
        if len(raw) > 1 and raw[1] != "2":
            raise ScoreImportError("Non-quarter-note GPIF tempo units require additional support.")
        # GPIF Position is a ratio within the master bar, not a quarter-note count.
        measures[bi].tempos.append((ratio * measures[bi].length, float(rational(raw[0], "tempo"))))
    bars = required_table(root, "Bars")
    voices = required_table(root, "Voices")
    beats = required_table(root, "Beats")
    notes = required_table(root, "Notes")
    rhythms = required_table(root, "Rhythms")
    tracks_table = required_table(root, "Tracks")
    track_ids = text(root, "MasterTrack/Tracks").split() or list(tracks_table)
    if len(track_ids) != len(set(track_ids)) or set(track_ids) != set(tracks_table):
        raise ScoreImportError("GPIF track list is incomplete or duplicated.")
    column = 0
    tracks = []
    warnings = []
    excluded = []
    for tid in track_ids:
        track = tracks_table[tid]
        name = text(track, "Name", f"Track {tid}")
        staves = track.findall("Staves/Staff")
        width = len(staves) or 1
        staff = staves[0] if staves else track
        properties = props(staff)
        tuning_node = properties.get("Tuning")
        tuning = [integer(x, "tuning pitch") for x in text(tuning_node, "Pitches").split()] if tuning_node is not None else []
        instrument_type = text(track, "InstrumentSet/Type").lower()
        program = integer(text(track, "Sounds/Sound/MIDI/Program", "-1"), "MIDI program")
        instrument = "bass" if "bass" in instrument_type or 32 <= program <= 39 else (
            "guitar" if "guitar" in instrument_type or 24 <= program <= 31 else "")
        if not instrument:
            warnings.append(f"Excluded non-guitar/bass track: {name}.")
            excluded.append({"id": tid, "name": name, "instrument": instrument_type or "unknown"})
            column += width
            continue
        if width != 1:
            raise ScoreImportError(f"Multiple staves in {name} require explicit mapping.")
        if not tuning:
            raise ScoreImportError(f"Missing string tuning in {name}.")
        if integer(property_value(properties, "PartialCapoFret", "0"), "partial capo"):
            raise ScoreImportError("Partial capo is not supported yet.")
        capo = integer(property_value(properties, "CapoFret", "0"), "capo")
        arrangement_bars = []
        written_bars = []
        for bi, mb in enumerate(masterbars):
            refs = text(mb, "Bars").split()
            if column >= len(refs):
                raise ScoreImportError(f"Truncated bar list in measure {bi + 1}.")
            bar = resolve(bars, refs[column], "bar")
            bar_notes = []
            written_voices = []
            for vi, vid in enumerate(text(bar, "Voices").split()):
                if vid == "-1":
                    continue
                voice = resolve(voices, vid, "voice")
                position = Fraction(0)
                written_voice = WrittenVoice(f"gpif:{tid}:{bi}:{vid}", source_index=vi)
                for bid in text(voice, "Beats").split():
                    beat = resolve(beats, bid, "beat")
                    beat_id = f"gpif:{tid}:{bi}:{vid}:{bid}"
                    inventory.inspect({child.tag: True for child in beat}, "GPIF beat", beat_id,
                                      playable={"Rhythm", "Notes", "Tremolo", "Slapped", "Popped", "GraceNotes", "Whammy", "Arpeggio", "Brush"},
                                      notation={"Rhythm", "Notes", "Dynamic", "FreeText"}, retained={"Properties", "XProperties"}, strict=True)
                    for property_name in props(beat):
                        if property_name not in {"StemDirection", "BeamingMode"}:
                            raise ScoreImportError(f"Unsupported GPIF beat property at {beat_id}: {property_name}.")
                        inventory.record("GPIF beat property", property_name, "layout", beat_id)
                    if any(beat.find(tag) is not None for tag in ("GraceNotes", "Whammy", "Arpeggio", "Brush")):
                        raise ScoreImportError("Grace notes, whammy, arpeggio or brush timing needs additional support.")
                    duration = _duration(beat, rhythms)
                    rhythm = resolve(rhythms, beat.find("Rhythm").get("ref"), "rhythm")
                    inventory.inspect({child.tag: True for child in rhythm}, "GPIF rhythm", beat_id,
                                      playable={"NoteValue", "AugmentationDot", "PrimaryTuplet", "SecondaryTuplet"},
                                      notation={"NoteValue", "AugmentationDot", "PrimaryTuplet"}, strict=True)
                    denominator, dots, tuplet = _written_rhythm(beat, rhythms)
                    annotations = {}
                    if text(beat, "Dynamic"):
                        annotations["dyn"] = text(beat, "Dynamic").lower()
                        if annotations["dyn"] not in {"ppp", "pp", "p", "mp", "mf", "f", "ff", "fff"}:
                            raise ScoreImportError(f"Unsupported GPIF dynamic marking at {beat_id}.")
                    if text(beat, "FreeText"):
                        annotations["txt"] = text(beat, "FreeText")
                    written_beat = WrittenBeat(beat_id, position, duration, denominator=denominator,
                                               dots=dots, tuplet=tuplet, annotations=annotations,
                                               written_duration=duration, written_position=position)
                    for nid in text(beat, "Notes").split():
                        node = resolve(notes, nid, "note")
                        source_id = f"{beat_id}:{nid}"
                        inventory.inspect({child.tag: True for child in node}, "GPIF note", source_id,
                                          playable={"Properties", "Tie", "Vibrato", "Accent", "AntiAccent", "Trill", "Ornament"},
                                          notation={"Tie", "Vibrato", "Accent", "AntiAccent"},
                                          retained={"XProperties"}, strict=True)
                        for property_name in props(node):
                            handling = "playable" if property_name not in {"ConcertPitch", "TransposedPitch", "HarmonicFret", "Midi", "Octave", "Tone", "Variation"} else "source"
                            destinations = ["source", handling]
                            if property_name in {"String", "Fret", "HopoOrigin", "HopoDestination", "Muted", "Tapped", "LeftHandTapped", "RightHandTapped", "Vibrato"}:
                                destinations.append("notation")
                            inventory.record("GPIF note property", property_name, handling, source_id, destinations)
                        parsed = _note(node, position, duration, beat)
                        parsed.source_id, parsed.beat_id, parsed.voice_id = source_id, beat_id, str(vi)
                        bar_notes.append(parsed)
                        written_beat.notes.append(parsed)
                    written_beat.rest = not written_beat.notes
                    written_voice.beats.append(written_beat)
                    position += duration
                if position > measures[bi].length:
                    raise ScoreImportError(f"Voice exceeds measure {bi + 1} in {name}.")
                written_voices.append(written_voice)
            arrangement_bars.append(bar_notes)
            written_bars.append(written_voices)
        role = "bass" if instrument == "bass" else (
            "rhythm" if "rhythm" in name.lower() else "lead" if any(s in name.lower() for s in ("lead", "solo")) else "guitar")
        tracks.append(Track(tid, name, instrument, tuning, arrangement_bars, capo, role, written_bars))
        column += width
    for mb in masterbars:
        if len(text(mb, "Bars").split()) != column:
            raise ScoreImportError("GPIF master-bar columns do not match the track/staff list.")
    warnings.extend(inventory.warnings())
    return Score(text(root, "Score/Title"), text(root, "Score/Artist"), measures, tracks,
                 text(root, "Score/Album"), "", {"format": "gpif", "version": version,
                    "trackCount": len(track_ids), "excludedTracks": excluded}, warnings,
                 {"version": 1, "format": "gpif", "encoding": "base64", "data": base64.b64encode(data).decode("ascii")}, inventory.entries())

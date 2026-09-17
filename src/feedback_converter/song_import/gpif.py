"""Read GP7/8's ZIP/GPIF score format using only Python's standard library.

This is FeedForge code, not a copy of the game's AGPL Guitar Pro converter.
Positions, notes, string order and techniques are read from the score itself.
Unsupported timing/navigation is reported rather than silently flattened.
"""

from fractions import Fraction
from pathlib import Path
import re
import xml.etree.ElementTree as ET
from zipfile import BadZipFile, ZipFile

from .model import Measure, Note, Score, ScoreImportError, Track, integer, rational

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
    string = integer(property_value(p, "String", None), "string index")
    fret = integer(property_value(p, "Fret", None), "fret")
    effects = {}
    for key, target in {"Muted": "mt", "PalmMuted": "pm", "Tapped": "tp",
                        "LeftHandTapped": "tp", "RightHandTapped": "tp", "LetRing": "lr"}.items():
        if enabled(p, key):
            effects[target] = True
    if enabled(p, "HopoOrigin"):
        effects["ln"] = True
    if node.find("Vibrato") is not None or enabled(p, "Vibrato"):
        effects["vb"] = True
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
    slide = None
    if "Slide" in p:
        flags = integer(property_value(p, "Slide"), "slide flags")
        choices = {0: None, 1: "shift", 2: "legato", 4: "out_down", 8: "out_up"}
        if flags not in choices:
            raise ScoreImportError(f"Unsupported slide flags: {flags}.")
        slide = choices[flags]
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
                enabled(p, "HopoDestination"), slide)


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
    for auto in root.findall("MasterTrack/Automations/Automation"):
        if text(auto, "Type") != "Tempo":
            continue
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
        for bi, mb in enumerate(masterbars):
            refs = text(mb, "Bars").split()
            if column >= len(refs):
                raise ScoreImportError(f"Truncated bar list in measure {bi + 1}.")
            bar = resolve(bars, refs[column], "bar")
            bar_notes = []
            for vid in text(bar, "Voices").split():
                if vid == "-1":
                    continue
                voice = resolve(voices, vid, "voice")
                position = Fraction(0)
                for bid in text(voice, "Beats").split():
                    beat = resolve(beats, bid, "beat")
                    if any(beat.find(tag) is not None for tag in ("GraceNotes", "Whammy", "Arpeggio", "Brush")):
                        raise ScoreImportError("Grace notes, whammy, arpeggio or brush timing needs additional support.")
                    duration = _duration(beat, rhythms)
                    for nid in text(beat, "Notes").split():
                        bar_notes.append(_note(resolve(notes, nid, "note"), position, duration, beat))
                    position += duration
                if position > measures[bi].length:
                    raise ScoreImportError(f"Voice exceeds measure {bi + 1} in {name}.")
            arrangement_bars.append(bar_notes)
        role = "bass" if instrument == "bass" else (
            "rhythm" if "rhythm" in name.lower() else "lead" if any(s in name.lower() for s in ("lead", "solo")) else "guitar")
        tracks.append(Track(tid, name, instrument, tuning, arrangement_bars, capo, role))
        column += width
    for mb in masterbars:
        if len(text(mb, "Bars").split()) != column:
            raise ScoreImportError("GPIF master-bar columns do not match the track/staff list.")
    return Score(text(root, "Score/Title"), text(root, "Score/Artist"), measures, tracks,
                 text(root, "Score/Album"), "", {"format": "gpif", "version": version,
                    "trackCount": len(track_ids), "excludedTracks": excluded}, warnings)

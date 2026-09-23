"""Parse a captured Songsterr score document. This module never downloads data.

Input is a complete envelope with metadata plus one note-data part per track.
The parser intentionally rejects incomplete data and unsupported musical effects.
"""

from copy import deepcopy
from fractions import Fraction

from .inventory import FeatureInventory
from .model import Measure, Note, Score, ScoreImportError, Track, WrittenBeat, WrittenVoice, integer, rational
from .songsterr_timing import measure_timing, strum_offsets, FEELS
from .songsterr_fields import bend_points
from .songsterr_automation import performed_tempos


def _instrument(meta):
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
    # The authored part name is descriptive text, not an instrument assignment.
    # A "Bass Clarinet" or "Guitar cues" label cannot override its MIDI program.
    instrument = str(meta.get("instrument", meta.get("type", ""))).lower()
    if "bass" in instrument:
        return "bass"
    if "guitar" in instrument:
        return "guitar"
    return ""


def _endings(value):
    if not value:
        return frozenset()
    if isinstance(value, list):
        return frozenset(integer(x, "alternate ending") for x in value)
    mask = integer(value, "alternate ending mask")
    if mask < 0 or mask > 65535:
        raise ScoreImportError("Unsupported alternate ending mask.")
    return frozenset(i + 1 for i in range(16) if mask & (1 << i))


def _note(raw, beat, position, duration, strings):
    string = strings - 1 - integer(raw.get("string"), "string index")
    fret = integer(raw.get("fret"), "fret")
    unsupported = ("trill", "grace", "graceNote", "tremoloBar", "whammy")
    for key in unsupported:
        if raw.get(key):
            raise ScoreImportError(f"Songsterr {key} needs additional conversion support.")
    if raw.get("harmonicFret") is not None:
        value = raw["harmonicFret"]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or rational(value, "harmonic fret") < 0:
            raise ScoreImportError("Invalid harmonic fret.")
        if value and raw.get("harmonic") is not None:
            raise ScoreImportError("Songsterr harmonicFret needs additional conversion support.")
    effects = {}
    vibrato = raw.get("leftHandVibrato")
    if vibrato not in (None, "slight", "wide"):
        raise ScoreImportError(f"Unsupported left-hand vibrato: {vibrato!r}.")
    if vibrato:
        effects["vb"] = True
        if vibrato == "wide":
            effects["__wide_vibrato"] = True
    picking = beat.get("pickStroke")
    if picking not in (None, "up", "down"):
        raise ScoreImportError("Unsupported picking direction.")
    if picking:
        effects["pkd"] = 1 if picking == "up" else 0
    for source, target in {"dead": "mt", "vibrato": "vb", "wideVibrato": "vb",
                           "ghost": "ghost", "accentuated": "ac", "tap": "tp",
                           "tapping": "tp", "hp": "__hopo_origin"}.items():
        if raw.get(source):
            effects[target] = True
    if raw.get("wideVibrato") or beat.get("wideVibrato"):
        effects["__wide_vibrato"] = True
    for source, target in {"palmMute": "pm", "letRing": "lr", "tremolo": "tr",
                           "tap": "tp", "tapping": "tp", "slap": "slp", "pop": "plk", "slapping": "slp", "popping": "plk",
                           "vibrato": "vb", "wideVibrato": "vb"}.items():
        if beat.get(source):
            effects[target] = True
    if raw.get("staccato"):
        if raw["staccato"] is not True or any(raw.get(k) for k in ("tie", "hp", "slide", "bend")):
            raise ScoreImportError("Linked or malformed staccato needs additional timing support.")
        shortened = max(duration / 2, Fraction(1, 32))
        if shortened > duration:
            raise ScoreImportError("Staccato's minimum would extend the authored note; no repair was applied.")
        duration = shortened
    harmonic = raw.get("harmonic")
    if harmonic:
        if harmonic == "natural":
            effects["hm"] = True
        elif harmonic == "pinch":
            effects["hp"] = True
        else:
            raise ScoreImportError(f"Unsupported Songsterr harmonic: {harmonic}.")
    slide_raw = raw.get("slide")
    outgoing = {"": None, "shift": "shift", "legato": "legato",
                "downwards": "out_down", "upwards": "out_up"}
    # Songsterr's schema includes above/below alone and each prefix combined
    # with an outgoing type. Above/below describe the unspecified starting fret.
    slides = {None: (None, None), **{key: (value, None) for key, value in outgoing.items() if key}}
    slides.update({prefix + suffix: (out, direction)
                   for prefix, direction in (("below", "up"), ("above", "down"))
                   for suffix, out in outgoing.items()})
    if slide_raw is not None and not isinstance(slide_raw, str) or slide_raw not in slides:
        raise ScoreImportError(f"Unsupported Songsterr slide: {slide_raw}.")
    slide_out, slide_in = slides[slide_raw]
    bends = []
    if raw.get("bend"):
        bend = raw["bend"]
        if not isinstance(bend, dict) or not bend.get("points"):
            raise ScoreImportError("Bend has no curve data.")
        bends = bend_points(bend["points"])
    return Note(position, duration, string, fret, bool(raw.get("tie")), effects,
                sorted(bends), False, slide_out, slide_in=slide_in)


def _written_rhythm(beat, duration):
    """Keep supplied notation; derive a ratio only from two explicit durations."""
    denominator = integer(beat["type"], "written duration") if beat.get("type") is not None else None
    dots = integer(beat.get("dots", 0), "dot count")
    if not 0 <= dots <= 4:
        raise ScoreImportError("Unsupported written dot count.")
    if denominator is None:
        for candidate in (1, 2, 4, 8, 16, 32, 64, 128, 256):
            if Fraction(4, candidate) * (2 - Fraction(1, 2 ** dots)) == duration:
                denominator = candidate
                break
    if denominator is None or denominator <= 0:
        return denominator, dots, None
    ratio = Fraction(4, denominator) * (2 - Fraction(1, 2 ** dots)) / duration
    return denominator, dots, (ratio.numerator, ratio.denominator) if ratio != 1 else None


def _annotations(beat):
    result = {}
    for key in ("slapping", "popping"):
        if key in beat and not isinstance(beat[key], bool):
            raise ScoreImportError(f"Invalid {key} flag.")
    if beat.get("velocity") and beat["velocity"] not in {"ppp", "pp", "p", "mp", "mf", "f", "ff", "fff"}:
        raise ScoreImportError("Unsupported Songsterr dynamic marking.")
    if beat.get("gradualVelocity") and beat["gradualVelocity"] not in {"crescendo", "decrescendo"}:
        raise ScoreImportError("Unsupported Songsterr gradual dynamic marking.")
    for source, target in {"velocity": "dyn", "vibrato": "vib", "wideVibrato": "vibw",
                           "palmMute": "pm", "letRing": "lr", "tap": "tap", "tapping": "tap",
                           "slap": "slap", "pop": "pop", "slapping": "slap", "popping": "pop"}.items():
        if beat.get(source):
            result[target] = deepcopy(beat[source])
    if beat.get("text"):
        value = beat["text"]
        result["txt"] = str(value.get("text", "")) if isinstance(value, dict) else str(value)
    if beat.get("gradualVelocity") in {"crescendo", "decrescendo"}:
        result["cre" if beat["gradualVelocity"] == "crescendo" else "dec"] = True
    return result


def _chord_label(beat):
    chord = beat.get("chord")
    if chord is None:
        return ""
    if not isinstance(chord, dict) or set(chord) - {"text", "width"} or not isinstance(chord.get("text"), str):
        raise ScoreImportError("Unsupported authored chord label data.")
    return chord["text"]


def parse(document: dict, *, track_indices=None) -> Score:
    if not isinstance(document, dict) or document.get("format") != "songsterr":
        raise ScoreImportError("Expected a complete Songsterr score envelope.")
    metadata, parts = document.get("tracks"), document.get("parts")
    inventory = FeatureInventory()
    inventory.inspect(document, "Songsterr document", "$", playable={"format", "songId", "revisionId", "title", "artist", "tracks", "parts"},
                      retained={"album", "year", "approved", "url", "version"})
    if not isinstance(metadata, list) or not isinstance(parts, list) or not metadata or len(metadata) != len(parts):
        raise ScoreImportError("Songsterr track metadata and note parts are incomplete.")
    for part in parts:
        if not isinstance(part, dict) or not isinstance(part.get("measures"), list) or not part["measures"]:
            raise ScoreImportError("A Songsterr track has missing note data.")
    count = len(parts[0]["measures"])
    if any(len(part["measures"]) != count for part in parts):
        raise ScoreImportError("A Songsterr track is truncated.")
    measures = []
    signature = (4, 4)
    for bi in range(count):
        candidates = [p["measures"][bi] for p in parts]
        if not all(isinstance(m, dict) for m in candidates):
            raise ScoreImportError("Malformed Songsterr measure.")
        signatures = {tuple(m["signature"]) for m in candidates if m.get("signature")}
        if len(signatures) > 1:
            raise ScoreImportError("Tracks disagree about the time signature.")
        if signatures:
            signature = next(iter(signatures))
        if len(signature) != 2:
            raise ScoreImportError("Invalid time signature.")
        n, d = (integer(v, "time signature") for v in signature)
        if n <= 0 or d <= 0:
            raise ScoreImportError("Invalid time signature.")
        for m in candidates:
            if any(m.get(key) for key in ("direction", "directions", "fromDirection", "fermata", "freeTime")):
                raise ScoreImportError("Songsterr navigation/free time requires additional support.")
            if m.get("tripletFeel") not in (None, "off", *FEELS):
                raise ScoreImportError("Unknown authored swing feel.")
        repeat_values = {integer(m["repeat"], "repeat count") for m in candidates if "repeat" in m}
        if len(repeat_values) > 1:
            raise ScoreImportError("Tracks disagree about the repeat count.")
        marker = next((m.get("marker") for m in candidates if m.get("marker")), "")
        section = str(marker.get("text", "")) if isinstance(marker, dict) else str(marker)
        ending_values = {_endings(m.get("alternateEnding")) for m in candidates if m.get("alternateEnding")}
        if len(ending_values) > 1:
            raise ScoreImportError("Tracks disagree about alternate endings.")
        measures.append(Measure(n, d, Fraction(4 * n, d), any(m.get("repeatStart") for m in candidates),
                                next(iter(repeat_values), 0), next(iter(ending_values), frozenset()), section))
    tempo_events = {}
    part_clocks = []
    for part_index, part in enumerate(parts):
        automations = part.get("automations", {})
        if not isinstance(automations, dict):
            raise ScoreImportError("Invalid Songsterr automation data.")
        inventory.inspect(automations, "Songsterr automations", f"$.parts[{part_index}].automations",
                          playable={"tempo", "fermata", "gradualTempo"}, retained={"volume", "balance"}, strict=True)
        part_events = {}
        for tempo_index, tempo in enumerate(automations.get("tempo", [])):
            inventory.inspect(tempo, "Songsterr tempo", f"$.parts[{part_index}].automations.tempo[{tempo_index}]",
                              playable={"measure", "position", "bpm", "type", "linear", "dotted"}, retained={"text", "visible"}, strict=True)
            if "visible" in tempo and not isinstance(tempo["visible"], bool):
                raise ScoreImportError("Invalid tempo visibility flag.")
            if "text" in tempo and not isinstance(tempo["text"], str):
                raise ScoreImportError("Invalid descriptive tempo text.")
            bar = integer(tempo.get("measure"), "tempo measure")
            if not 0 <= bar < count:
                raise ScoreImportError("Tempo references a missing measure.")
            if tempo.get("linear") is not None and type(tempo["linear"]) is not bool:
                raise ScoreImportError("Invalid linear tempo flag.")
            # Songsterr's exact duration and position fractions are whole-note units.
            position = rational(tempo.get("position", 0), "tempo position") * 4
            bpm = float(rational(tempo.get("bpm"), "tempo"))
            unit = integer(tempo.get("type", 4), "tempo note value")
            if unit <= 0:
                raise ScoreImportError("Invalid tempo note value.")
            bpm *= 4 / unit
            if tempo.get("dotted") is not None and type(tempo["dotted"]) is not bool:
                raise ScoreImportError("Invalid dotted tempo flag.")
            if tempo.get("dotted") is True:
                bpm *= 1.5
            key = (bar, position)
            if key in part_events and part_events[key] != bpm:
                raise ScoreImportError("Conflicting tempo events at the same position.")
            part_events[key] = bpm
        part_events = performed_tempos(automations, measures, part_events)
        part_clocks.append(part_events)
        for key, bpm in part_events.items():
            if key in tempo_events and tempo_events[key] != bpm:
                raise ScoreImportError("Tracks disagree about tempo.")
            tempo_events[key] = bpm
    if any(p.get("automations", {}).get("gradualTempo") or p.get("automations", {}).get("fermata") for p in parts):
        clocks = []
        for events in part_clocks:
            if not events:
                continue
            changes, prior = [], None
            for point, bpm in sorted(events.items()):
                if bpm != prior:
                    changes.append((point, bpm))
                prior = bpm
            clocks.append(changes)
        if clocks and any(clock != clocks[0] for clock in clocks[1:]):
            raise ScoreImportError("Tracks disagree about the performed tempo automation.")
    for (bar, position), bpm in tempo_events.items():
        measures[bar].tempos.append((position, bpm))
    tracks = []
    warnings = []
    excluded = []
    for index, (meta, part) in enumerate(zip(metadata, parts)):
        if not isinstance(meta, dict):
            raise ScoreImportError("Invalid track metadata.")
        instrument = _instrument(meta)
        inventory.inspect(meta, "Songsterr track", f"$.tracks[{index}]", playable={"id", "instrumentId", "midiProgram", "name", "instrument", "title", "type", "tuning", "capo", "isVocalTrack"},
                          retained={"views", "difficulty", "hash", "isEmpty"})
        inventory.inspect(part, "Songsterr part", f"$.parts[{index}]", playable={"measures", "tuning", "automations", "capo"},
                          retained={"name", "balance", "volume", "frets", "strings", "instrumentId", "instrument", "newLyrics", "withLyrics", "tuningFlat", "partId", "version", "songId", "revisionId"})
        if not instrument or track_indices is not None and index not in track_indices:
            warnings.append(f"Excluded non-guitar/bass track: {meta.get('name') or meta.get('instrument') or index}.")
            excluded.append({"id": str(meta.get("id", index)),
                             "name": str(meta.get("name") or meta.get("title") or meta.get("instrument") or f"Track {index + 1}"),
                             "instrument": str(meta.get("instrument") or meta.get("type") or "unknown")})
            continue
        tuning_raw = part.get("tuning") or meta.get("tuning")
        if not isinstance(tuning_raw, list) or not tuning_raw:
            raise ScoreImportError("A playable track has no explicit tuning.")
        tuning = list(reversed([integer(v, "tuning pitch") for v in tuning_raw]))
        name = str(meta.get("name") or meta.get("title") or meta.get("instrument") or f"Track {index + 1}")
        track_bars = []
        written_bars = []
        feel = "off"
        for bi, measure in enumerate(part["measures"]):
            inventory.inspect(measure, "Songsterr measure", f"$.parts[{index}].measures[{bi}]",
                              playable={"signature", "voices", "rest", "repeat", "repeatStart", "alternateEnding", "marker", "direction", "directions", "fromDirection", "fermata", "freeTime", "tripletFeel"},
                              notation={"signature", "voices", "rest", "clef"}, retained={"keySignature", "doubleBarline"}, strict=True)
            if measure.get("clef") not in (None, "G2", "F4", "C3", "C4", "neutral"):
                raise ScoreImportError("This authored clef needs additional notation support.")
            if "doubleBarline" in measure and not isinstance(measure["doubleBarline"], bool):
                raise ScoreImportError("Invalid double barline annotation.")
            if measure.get("keySignature"):
                warnings.append(f"Key signature in {name}, measure {bi + 1}, is retained in source evidence only.")
            if "voices" not in measure or not isinstance(measure["voices"], list):
                raise ScoreImportError(f"Missing voices in {name}, measure {bi + 1}.")
            bar_notes = []
            written_voices = []
            feel = measure.get("tripletFeel") or feel
            try:
                measure_clocks = measure_timing(measure, measures[bi].length, feel)
            except ScoreImportError as exc:
                exc.source_location = {"measure": bi + 1, "location": f"parts/{index}/measures/{bi}"}
                raise
            for vi, voice in enumerate(measure["voices"]):
                if not isinstance(voice, dict) or not isinstance(voice.get("beats"), list):
                    raise ScoreImportError("Missing Songsterr beat data.")
                timings = measure_clocks[vi]
                voice_id = f"songsterr:{index}:{bi}:{vi}"
                inventory.inspect(voice, "Songsterr voice", voice_id, playable={"beats", "rest"}, strict=True)
                written_voice = WrittenVoice(voice_id, source_index=vi)
                for beat_index, beat in enumerate(voice["beats"]):
                    beat_id = f"{voice_id}:{beat_index}"
                    inventory.inspect(beat, "Songsterr beat", beat_id,
                                      playable={"duration", "notes", "rest", "palmMute", "tremolo", "tap", "tapping", "slap", "pop", "slapping", "popping", "vibrato", "wideVibrato", "letRing", "graceNote", "grace", "graceNotes", "tremoloBar", "stroke", "whammy", "pickStroke", "brushStroke", "arpeggio", "upStroke", "downStroke", "upArpeggio", "downArpeggio"},
                                      notation={"duration", "notes", "rest", "type", "dots", "tuplet", "text", "velocity", "gradualVelocity", "letRing", "palmMute", "tap", "tapping", "slap", "pop", "vibrato", "wideVibrato", "graceNote"},
                                      retained={"chord", "wahwah"},
                                      # Attack offsets are kept in the playable chart.
                                      layout={"beamStart", "beamStop", "tupletStart", "tupletStop"},
                                      strict=True)
                    if beat.get("wahwah") not in (None, "open", "closed"):
                        raise ScoreImportError("Unsupported wah pedal marking.")
                    if any(beat.get(k) for k in ("grace", "graceNotes", "tremoloBar", "stroke", "whammy")):
                        raise ScoreImportError("Unsupported Songsterr beat technique.")
                    grace = beat.get("graceNote")
                    if grace not in (None, "onBeat", "beforeBeat"):
                        raise ScoreImportError(f"Songsterr grace note type {grace!r} is unsupported in {name}, measure {bi + 1}.")
                    if "duration" not in beat:
                        raise ScoreImportError("Missing exact Songsterr beat duration.")
                    written_duration = rational(beat["duration"], "beat duration") * 4
                    position, duration, written_position = timings[beat_index]
                    if not isinstance(beat.get("notes"), list):
                        raise ScoreImportError("Missing Songsterr notes (rests must have an explicit empty list).")
                    offsets, strum_direction = strum_offsets(beat)
                    denominator, dots, tuplet = _written_rhythm(beat, written_duration)
                    written_beat = WrittenBeat(beat_id, position, duration, rest=bool(beat.get("rest")),
                                               denominator=denominator, dots=dots, tuplet=tuplet,
                                               grace=("a" if grace == "beforeBeat" else "p") if grace else "", annotations=_annotations(beat),
                                               written_duration=written_duration, written_position=written_position)
                    written_beat.chord_label = _chord_label(beat)
                    for note_index, note in enumerate(beat["notes"]):
                        source_id = f"{beat_id}:{note_index}"
                        inventory.inspect(note, "Songsterr note", source_id,
                                          playable={"string", "fret", "rest", "tie", "dead", "vibrato", "wideVibrato", "ghost", "accentuated", "tap", "tapping", "hp", "harmonic", "slide", "bend", "trill", "grace", "graceNote", "tremoloBar", "whammy", "staccato", "leftHandVibrato"},
                                          notation={"string", "fret", "rest", "tie", "dead", "vibrato", "wideVibrato", "ghost", "accentuated", "tap", "tapping", "hp", "leftHandVibrato"},
                                          retained={"harmonicFret"}, strict=True)
                        if isinstance(note.get("bend"), dict):
                            inventory.inspect(note["bend"], "Songsterr bend", source_id + ".bend", playable={"points", "tone"}, strict=True)
                            for point_index, point in enumerate(note["bend"].get("points", [])):
                                inventory.inspect(point, "Songsterr bend point", f"{source_id}.bend.points[{point_index}]", playable={"position", "precisePosition", "tone"}, strict=True)
                        if not note.get("rest"):
                            offset = offsets.get(note_index, Fraction(0))
                            if position + offset < 0 or duration <= offset:
                                raise ScoreImportError("The authored strum crosses a measure boundary or consumes a note; additional timing support is required.")
                            try:
                                parsed = _note(note, beat, position + offset, duration - offset, len(tuning))
                            except ScoreImportError as exc:
                                exc.source_location = {"measure": bi + 1, "voice": vi + 1, "beat": beat_index + 1,
                                    "note": note_index + 1, "location": f"parts/{index}/measures/{bi}/voices/{vi}/beats/{beat_index}/notes/{note_index}"}
                                raise
                            if strum_direction and "pkd" not in parsed.effects:
                                parsed.effects["pkd"] = 1 if strum_direction == "up" else 0
                            parsed.source_id, parsed.beat_id, parsed.voice_id = source_id, beat_id, str(vi)
                            bar_notes.append(parsed)
                            written_beat.notes.append(parsed)
                    written_beat.rest = not written_beat.notes
                    written_voice.beats.append(written_beat)
                written_voices.append(written_voice)
            track_bars.append(bar_notes)
            written_bars.append(written_voices)
        role = "bass" if instrument == "bass" else (
            "rhythm" if "rhythm" in name.lower() else "lead" if any(w in name.lower() for w in ("lead", "solo")) else "guitar")
        tracks.append(Track(str(meta.get("id", index)), name, instrument, tuning, track_bars,
                            integer(part.get("capo", meta.get("capo", 0)), "capo"), role, written_bars))
        clef = None
        for bar in part["measures"]:
            clef = bar.get("clef") or clef
            tracks[-1].clefs.append(clef)
    source = {key: document[key] for key in ("songId", "revisionId", "approved", "url") if key in document}
    source["format"] = "songsterr"
    source.update(trackCount=len(metadata), excludedTracks=excluded)
    warnings.extend(inventory.warnings())
    return Score(str(document.get("title", "")), str(document.get("artist", "")), measures, tracks,
                 str(document.get("album", "")), document.get("year", ""), source, warnings,
                 {"version": 1, "format": "songsterr", "document": deepcopy(document)}, inventory.entries())

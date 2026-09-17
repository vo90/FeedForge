"""Parse a captured Songsterr score document. This module never downloads data.

Input is a complete envelope with metadata plus one note-data part per track.
The parser intentionally rejects incomplete data and unsupported musical effects.
"""

from fractions import Fraction

from .model import Measure, Note, Score, ScoreImportError, Track, integer, rational


def _instrument(meta):
    name = " ".join(str(meta.get(k, "")) for k in ("name", "instrument", "title", "type")).lower()
    program = meta.get("instrumentId", meta.get("midiProgram"))
    if "drum" in name or "percussion" in name or program == 1024:
        return ""
    if "bass" in name or isinstance(program, int) and 32 <= program <= 39:
        return "bass"
    if "guitar" in name or isinstance(program, int) and 24 <= program <= 31:
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
    unsupported = ("trill", "grace", "graceNote", "tremoloBar", "whammy", "harmonicFret")
    for key in unsupported:
        if raw.get(key) and key != "harmonicFret":
            raise ScoreImportError(f"Songsterr {key} needs additional conversion support.")
    effects = {}
    for source, target in {"dead": "mt", "vibrato": "vb", "wideVibrato": "vb",
                           "ghost": "ghost", "accentuated": "ac", "tap": "tp",
                           "tapping": "tp", "hp": "__hopo_origin"}.items():
        if raw.get(source):
            effects[target] = True
    for source, target in {"palmMute": "pm", "letRing": "lr", "tremolo": "tr",
                           "tap": "tp", "slap": "slp", "pop": "plk"}.items():
        if beat.get(source):
            effects[target] = True
    if raw.get("staccato"):
        raise ScoreImportError("Staccato sustain interpretation is not implemented yet.")
    harmonic = raw.get("harmonic")
    if harmonic:
        if harmonic == "natural":
            effects["hm"] = True
        elif harmonic == "pinch":
            effects["hp"] = True
        else:
            raise ScoreImportError(f"Unsupported Songsterr harmonic: {harmonic}.")
    slide_raw = raw.get("slide")
    slides = {None: None, "shift": "shift", "legato": "legato",
              "downwards": "out_down", "upwards": "out_up"}
    if slide_raw not in slides:
        raise ScoreImportError(f"Unsupported Songsterr slide: {slide_raw}.")
    bends = []
    if raw.get("bend"):
        bend = raw["bend"]
        if not isinstance(bend, dict) or not bend.get("points"):
            raise ScoreImportError("Bend has no curve data.")
        for p in bend["points"]:
            bends.append((rational(p.get("position"), "bend position") / 60,
                          float(rational(p.get("tone"), "bend value") / 50)))
    return Note(position, duration, string, fret, bool(raw.get("tie")), effects,
                sorted(bends), False, slides[slide_raw])


def parse(document: dict) -> Score:
    if not isinstance(document, dict) or document.get("format") != "songsterr":
        raise ScoreImportError("Expected a complete Songsterr score envelope.")
    metadata, parts = document.get("tracks"), document.get("parts")
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
            if m.get("tripletFeel") not in (None, "off"):
                raise ScoreImportError("Swing/triplet feel is not supported yet.")
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
    for part in parts:
        automations = part.get("automations", {})
        if not isinstance(automations, dict):
            raise ScoreImportError("Invalid Songsterr automation data.")
        for tempo in automations.get("tempo", []):
            bar = integer(tempo.get("measure"), "tempo measure")
            if not 0 <= bar < count:
                raise ScoreImportError("Tempo references a missing measure.")
            if tempo.get("linear"):
                raise ScoreImportError("Linear tempo ramps require additional support.")
            # Songsterr's exact duration and position fractions are whole-note units.
            position = rational(tempo.get("position", 0), "tempo position") * 4
            bpm = float(rational(tempo.get("bpm"), "tempo"))
            unit = integer(tempo.get("type", 4), "tempo note value")
            if unit <= 0:
                raise ScoreImportError("Invalid tempo note value.")
            bpm *= 4 / unit
            key = (bar, position)
            if key in tempo_events and tempo_events[key] != bpm:
                raise ScoreImportError("Tracks disagree about tempo.")
            tempo_events[key] = bpm
    for (bar, position), bpm in tempo_events.items():
        measures[bar].tempos.append((position, bpm))
    tracks = []
    warnings = []
    excluded = []
    for index, (meta, part) in enumerate(zip(metadata, parts)):
        if not isinstance(meta, dict):
            raise ScoreImportError("Invalid track metadata.")
        instrument = _instrument(meta)
        if not instrument:
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
        for bi, measure in enumerate(part["measures"]):
            if "voices" not in measure or not isinstance(measure["voices"], list):
                raise ScoreImportError(f"Missing voices in {name}, measure {bi + 1}.")
            bar_notes = []
            for voice in measure["voices"]:
                if not isinstance(voice, dict) or not isinstance(voice.get("beats"), list):
                    raise ScoreImportError("Missing Songsterr beat data.")
                position = Fraction(0)
                for beat in voice["beats"]:
                    if any(beat.get(k) for k in ("grace", "graceNotes", "tremoloBar", "stroke", "whammy")):
                        raise ScoreImportError("Unsupported Songsterr beat technique.")
                    if "duration" not in beat:
                        raise ScoreImportError("Missing exact Songsterr beat duration.")
                    duration = rational(beat["duration"], "beat duration") * 4
                    if duration <= 0:
                        raise ScoreImportError("Invalid beat duration.")
                    if not isinstance(beat.get("notes"), list):
                        raise ScoreImportError("Missing Songsterr notes (rests must have an explicit empty list).")
                    for note in beat["notes"]:
                        if not note.get("rest"):
                            bar_notes.append(_note(note, beat, position, duration, len(tuning)))
                    position += duration
                if position > measures[bi].length:
                    raise ScoreImportError(f"Voice exceeds measure {bi + 1} in {name}.")
            track_bars.append(bar_notes)
        role = "bass" if instrument == "bass" else (
            "rhythm" if "rhythm" in name.lower() else "lead" if any(w in name.lower() for w in ("lead", "solo")) else "guitar")
        tracks.append(Track(str(meta.get("id", index)), name, instrument, tuning, track_bars,
                            integer(part.get("capo", meta.get("capo", 0)), "capo"), role))
    source = {key: document[key] for key in ("songId", "revisionId", "approved", "url") if key in document}
    source["format"] = "songsterr"
    source.update(trackCount=len(metadata), excludedTracks=excluded)
    return Score(str(document.get("title", "")), str(document.get("artist", "")), measures, tracks,
                 str(document.get("album", "")), document.get("year", ""), source, warnings)

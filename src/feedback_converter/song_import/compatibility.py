"""Collect source capability gaps before conversion, without repairing music.

This is a capability inventory, not a proof that the music is correct. The
parser and independent verifier still validate every supported representation.
"""
from copy import deepcopy
import json
import math
from .songsterr_harmonics import exact_natural, natural_alias, source_target
from .fingering import left_finger, validate_right_finger
from .songsterr_tremolo import tremolo_mark
from .songsterr_fields import validate_sustain_pedal
from .model import ScoreImportError, integer, rational

VERSION = 75
TARGET = {"feedpak": "1.16.0", "notation": 1,
          "gameVersion": "not_detected", "assessment": "converter_capabilities"}
KNOWN = {
    "measure": set("signature voices rest repeat repeatStart alternateEnding marker direction directions fromDirection fermata freeTime tripletFeel keySignature".split()),
    "voice": {"beats", "rest"},
    "beat": set("duration notes rest palmMute tremolo tap tapping slap pop vibrato wideVibrato letRing graceNote grace graceNotes tremoloBar stroke whammy type dots tuplet text velocity gradualVelocity beamStart beamStop tupletStart tupletStop".split()),
    "note": set("string fret rest tie dead vibrato wideVibrato ghost accentuated tap tapping hp harmonic slide bend trill grace graceNote tremoloBar whammy staccato harmonicFret".split()),
    "automations": {"tempo", "volume", "balance", "fermata", "gradualTempo"},
    "tempo": {"measure", "position", "bpm", "type", "linear"},
    "bend": {"points", "tone"}, "bend_point": {"position", "tone"},
}
KNOWN["measure"].add("doubleBarline")
KNOWN["measure"].add("clef")
KNOWN["beat"].update({"slapping", "popping", "upArpeggio", "downArpeggio"})
KNOWN["tempo"].add("dotted")
KNOWN["bend_point"].add("precisePosition")
KNOWN["tempo"].add("text")
KNOWN["tempo"].add("visible")
KNOWN["beat"].update({"chord", "upStroke", "downStroke", "pickStroke", "wahwah", "brushStroke", "arpeggio", "vibratoWithTremoloBar"})
KNOWN["note"].update({"leftHandVibrato", "pickScrape", "vibratoWithTremoloBar"})
KNOWN["note"].add("leftFingering")
KNOWN["note"].add("rightFingering")
KNOWN["note"].add("tremolo")
KNOWN["beat"].add("hasRasgueado")
KNOWN["beat"].add("sustainPedal")
LIMITATIONS = {
    ("beat", "vibrato"): "Beat-level vibrato is retained as a written instruction; its playback timing is not confirmed by the source player.",
    ("beat", "wideVibrato"): "Beat-level wide vibrato is retained as a written instruction; its playback timing is not confirmed by the source player.",
    ("beat", "sustainPedal"): "The sustain-pedal marking is retained in the original source. Songsterr uses it for synthesizer pedal control. Written notes, ties and durations are preserved; the game does not display or score the pedal effect, or extend note trails for it.",
    ("note", "rightFingering"): "The authored picking-hand finger is retained in the original source. The game does not display picking-hand fingering; fretting-hand hints, notes, timing and scoring are unchanged.",
    ("note", "tremolo"): "Tremolo picking uses the game's existing tremolo instruction. Exact subdivision and within-tie timing remain in the source. A tied sustain has one marker for the whole sustain; individual repeated picks are not expanded or scored separately, and the written subdivision is not engraved.",
    ("beat", "tremolo"): "Tremolo picking uses the game's existing tremolo instruction. Exact subdivision and within-tie timing remain in the source. A tied sustain has one marker for the whole sustain; individual repeated picks are not expanded or scored separately, and the written subdivision is not engraved.",
    ("beat", "hasRasgueado"): "The rasgueado strumming instruction is retained in the original source. Written notes, ties, durations and explicit strums are preserved and scored normally; no rasgueado display, additional strokes or special scoring are added.",
    ("beat", "tremoloBar"): "Whammy-bar pitch curves are preserved. Bar expression is optional; an updated game is required for the display and scoring policy.",
    ("beat", "vibratoWithTremoloBar"): "Slight/wide bar vibrato is preserved. These notes are visual only in the updated game because the source does not specify an exact pitch curve; they do not reduce accuracy or streaks.",
    ("note", "pickScrape"): "Pick scrapes: visual only, not scored. Direction, strings and timing are preserved; an updated game is required to display them.",
    ("beat", "letRing"): "The let-ring marking is retained in the original source. Written and tied note durations are preserved; the game does not display this marking or extend ringing beyond those durations.",
    ("measure", "doubleBarline"): "The double barline is retained in the source; the game uses its ordinary measure display. Notes and timing are unchanged.",
    ("measure", "keySignature"): "The written key signature is retained in the source. Explicit pitches are converted unchanged.",
    ("tempo", "text"): "The descriptive tempo text is retained in the source. The explicit numeric tempo is converted unchanged.",
    ("beat", "chord"): "Authored labels name simultaneous chord templates. Labels on rests or single notes, and label engraving, remain in the source.",
    ("beat", "wahwah"): "The wah pedal marking is retained in the source. Notes and timing are converted; pedal expression is not represented in the game chart.",
    ("note", "staccato"): "Performed duration follows the source's staccato rule. The written staccato marking is retained in the original source; the game chart has no dedicated staccato marker.",
}
# These names are understood but do not yet have a faithful conversion mapping.
UNIMPLEMENTED = {
    "measure": {"direction", "directions", "fromDirection", "fermata", "freeTime"},
    "beat": {"grace", "graceNotes", "stroke", "whammy"},
    "note": {"grace", "graceNote", "tremoloBar", "whammy", "harmonicFret", "vibratoWithTremoloBar"},
    "tempo": set(),
}


DECISIONS = {
    "pickScrape": "D1", "tremoloBar": "D2", "whammy": "D2", "vibratoWithTremoloBar": "D2",
    "harmonicFret": "D3", "trill": "D4", "rasgueado": "D5", "unpitched_mute": "D6",
    "fret_range": "D7", "simultaneous_voices": "D8",
}


def inactive(value):
    return value is None or value is False or isinstance(value, (str, list, dict)) and not value


def new_report(identity=None):
    return {"version": VERSION, "target": dict(TARGET), "status": "compatible",
            "musicalQualityAssessed": False, "source": dict(identity or {}),
            "findings": [], "findingCount": 0, "truncated": False}


def add_finding(report, *, feature, category, impact, message, location="source", value=None, **coordinates):
    report["findingCount"] += 1
    if len(report["findings"]) < 20000:
        # Full original values remain in the immutable source. The report is a
        # bounded index into it, not a second copy of arbitrarily large objects.
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
        decision = DECISIONS.get(feature.split(".")[-1]) if impact == "blocking" else None
        report["findings"].append({"feature": feature, "category": category, "impact": impact,
            "workStatus": "decision_required" if decision else "gameplay_omission" if impact == "gameplay_omission" else "display_limitation" if impact == "display_or_expression" else "technical_work",
            **({"decisionId": decision} if decision else {}),
            "message": message, "location": location, **coordinates,
            "value": deepcopy(value) if len(encoded) <= 2048 else encoded[:2048],
            "valueTruncated": len(encoded) > 2048, "retained": "original_source"})
    else:
        report["truncated"] = True
    if impact == "blocking" or report["truncated"]:
        report["status"] = "blocked"
    elif report["status"] != "blocked":
        report["status"] = "limitations"


def inspect_songsterr(document, *, track_indices=None):
    if not isinstance(document, dict):
        report = new_report()
        add_finding(report, feature="document", category="source_structure", impact="blocking", message="Expected a Songsterr score object.")
        return report
    identity = {k: document[k] for k in ("songId", "revisionId", "title", "artist") if k in document}
    report = new_report(identity)

    def inspect(obj, scope, path, coordinates):
        if not isinstance(obj, dict):
            add_finding(report, feature=scope, category="source_structure", impact="blocking",
                        message="Expected a source object.", location=path, value=obj, **coordinates)
            return
        for key, value in obj.items():
            if scope == "beat" and key == "sustainPedal":
                try:
                    validate_sustain_pedal(value)
                except ScoreImportError as exc:
                    add_finding(report, feature="beat.sustainPedal", category="source_structure", impact="blocking",
                                message=str(exc), location=path + "/" + key, value=value, **coordinates)
                    continue
            if scope == "note" and key == "rightFingering":
                try:
                    validate_right_finger(value)
                except ScoreImportError as exc:
                    add_finding(report, feature="note.rightFingering", category="source_structure", impact="blocking",
                                message=str(exc), location=path + "/" + key, value=value, **coordinates)
                    continue
            if scope in {"beat", "note"} and key == "tremolo":
                try:
                    tremolo_mark(value)
                except ScoreImportError as exc:
                    add_finding(report, feature=f"{scope}.tremolo", category="source_structure", impact="blocking",
                                message=str(exc), location=path + "/" + key, value=value, **coordinates)
                    continue
            if scope == "note" and key == "leftFingering":
                try:
                    left_finger(value)
                except ScoreImportError as exc:
                    add_finding(report, feature="note.leftFingering", category="source_structure", impact="blocking",
                                message=str(exc), location=path + "/" + key, value=value, **coordinates)
                    continue
                if value is not None and (obj.get("tie") or value == "0" and obj.get("fret") != 0):
                    add_finding(report, feature="note.leftFingering", category="game_limitation", impact="display_or_expression",
                                message="This continuation-only or open/no-finger instruction is retained in the source. The attack keeps its authored finger hint; no finger change or fret correction is invented.",
                                location=path + "/" + key, value=value, **coordinates)
                continue
            # Only the boolean expression flag is approved. Named patterns in
            # the separate rasgueado field still need rhythmic interpretation.
            if scope == "beat" and key == "hasRasgueado" and value is not None and not isinstance(value, bool):
                add_finding(report, feature="beat.hasRasgueado", category="source_structure", impact="blocking",
                            message="Invalid rasgueado flag; expected a boolean.",
                            location=path + "/" + key, value=value, **coordinates)
                continue
            if scope == "note" and key == "harmonicFret" and (exact_natural(obj) or source_target(obj)):
                interpretation = ("Natural harmonic position 15 is displayed as 14.7, matching the source playback harmonic; the authored value is retained."
                                  if natural_alias(obj) else
                                  "Semi-harmonics use the pinch-harmonic display and permit the fretted sound alongside the selected harmonic."
                                  if obj.get("harmonic") == "semi" else
                                  "Feedback keeps the initial note and sustain. The initial attack accepts the fretted or harmonic pitch; amplifier feedback is not required."
                                  if obj.get("harmonic") == "feedback" else None)
                if interpretation:
                    add_finding(report, feature="note.harmonicFret", category="game_limitation", impact="display_or_expression",
                                message=interpretation, location=path + "/" + key, value=value, **coordinates)
                continue
            if scope == "tempo" and key == "visible" and isinstance(value, bool):
                add_finding(report, feature="tempo.visible", category="game_limitation", impact="display_or_expression",
                            message="Tempo-label visibility is retained in the source. The numeric tempo and playback timing are unchanged.",
                            location=path + "/" + key, value=value, **coordinates)
                continue
            if (scope == "note" and key == "harmonicFret" and obj.get("harmonic") is None
                    and obj.get("harmonicData") is None and isinstance(value, (int, float))
                    and not isinstance(value, bool) and math.isfinite(value) and value >= 0):
                # The source performer activates harmonics via harmonic/type,
                # never from the retained touch-position field by itself.
                continue
            if inactive(value):
                continue
            category = message = None
            if scope == "tempo" and key == "visible" and not isinstance(value, bool):
                category, message = "source_structure", "Invalid tempo visibility flag."
            elif scope == "note" and key == "pickScrape" and (value not in ("up", "down") or obj.get("dead") is not True):
                category, message = "source_structure", "Pick scrapes require a direction and an unpitched dead note."
            elif key not in KNOWN[scope]:
                category, message = "unknown_semantics", "This feature needs interpretation before reliable conversion."
            elif key in UNIMPLEMENTED.get(scope, set()) and value:
                category, message = "converter_gap", "The source technique is retained, but its conversion is not implemented."
            elif scope == "measure" and key == "tripletFeel" and value not in ("off", "8th", "16th", "dotted8th", "dotted16th", "scottish8th", "scottish16th"):
                category, message = "unknown_semantics", "This swing feel needs additional interpretation."
            elif (scope, key) in LIMITATIONS:
                add_finding(report, feature=f"{scope}.{key}", category="game_limitation", impact="display_or_expression",
                            message=LIMITATIONS[scope, key], location=path + "/" + key, value=value, **coordinates)
            if category:
                add_finding(report, feature=f"{scope}.{key}", category=category, impact="blocking",
                            message=message, location=path + "/" + key, value=value, **coordinates)

    metadata, parts = document.get("tracks"), document.get("parts")
    if not isinstance(metadata, list) or not isinstance(parts, list) or len(metadata) != len(parts):
        add_finding(report, feature="track_inventory", category="source_structure", impact="blocking",
                    message="Track metadata and note parts are incomplete.")
        return report
    for pi, (meta, part) in enumerate(zip(metadata, parts)):
        if not isinstance(meta, dict) or not isinstance(part, dict):
            add_finding(report, feature="track", category="source_structure", impact="blocking",
                        message="Invalid track data.", location=f"parts/{pi}")
            continue
        program = meta.get("instrumentId", meta.get("midiProgram"))
        label = str(meta.get("instrument", meta.get("type", ""))).lower()
        playable = (24 <= program <= 39 if isinstance(program, int) and not isinstance(program, bool) and program >= 0
                    else "guitar" in label or "bass" in label)
        playable = playable and meta.get("isVocalTrack") is not True and (track_indices is None or pi in track_indices)
        coordinates = {"arrangement": str(meta.get("name") or meta.get("title") or f"Track {pi + 1}"),
                       "trackIndex": pi, "trackId": str(meta.get("id", pi))}
        auto = part.get("automations", {})
        inspect(auto, "automations", f"parts/{pi}/automations", coordinates)
        if isinstance(auto, dict):
            tempos = auto.get("tempo", []) if isinstance(auto.get("tempo", []), list) else []
            from .songsterr_automation import inactive_tempo_context
            unused_context = inactive_tempo_context(auto)
            positions = {}
            for ti, tempo in enumerate(tempos):
                inspect(tempo, "tempo", f"parts/{pi}/automations/tempo/{ti}", coordinates)
                if isinstance(tempo, dict):
                    try:
                        key = integer(tempo.get('measure'), 'tempo measure'), rational(tempo.get('position', 0), 'tempo position')
                    except ScoreImportError:
                        continue  # The parser reports malformed coordinates.
                    positions.setdefault(key, []).append(ti)
                    if key[0] >= len(part.get('measures', [])):
                        if unused_context:
                            add_finding(report, feature='tempo.outside_score', category='source_interpretation', impact='display_or_expression',
                                        message='This tempo instruction belongs to a bar beyond the written score. It has no playback effect without an active ramp or initial-clock shift; the complete instruction is retained in the original source.',
                                        location=f'parts/{pi}/automations/tempo/{ti}', value=tempo, **coordinates)
            for indices in positions.values():
                winner = indices[-1]
                for ti in indices[:-1]:
                    add_finding(report, feature='tempo.superseded', category='source_interpretation', impact='display_or_expression',
                                message='Songsterr uses the last tempo instruction at this exact position. The earlier entry is retained in the original source; its rate and ramp flags do not control playback.',
                                location=f'parts/{pi}/automations/tempo/{ti}',
                                value={'authored': tempos[ti], 'used': tempos[winner], 'selectedIndex': winner}, **coordinates)
        for bi, bar in enumerate(part.get("measures", []) if isinstance(part.get("measures"), list) else []):
            path = f"parts/{pi}/measures/{bi}"
            coord = {**coordinates, "measure": bi + 1}
            # Navigation/meter applies across tracks, even excluded instruments.
            inspected = bar if playable or not isinstance(bar, dict) else {k: v for k, v in bar.items() if k != "voices"}
            inspect(inspected, "measure", path, coord)
            if not playable or not isinstance(bar, dict):
                continue
            for vi, voice in enumerate(bar.get("voices", []) if isinstance(bar.get("voices"), list) else []):
                vpath = path + f"/voices/{vi}"
                inspect(voice, "voice", vpath, coord)
                if not isinstance(voice, dict):
                    continue
                for bti, beat in enumerate(voice.get("beats", []) if isinstance(voice.get("beats"), list) else []):
                    bpath = vpath + f"/beats/{bti}"
                    bc = {**coord, "voice": vi + 1, "beat": bti + 1}
                    inspect(beat, "beat", bpath, bc)
                    if not isinstance(beat, dict):
                        continue
                    for ni, note in enumerate(beat.get("notes", []) if isinstance(beat.get("notes"), list) else []):
                        npath = bpath + f"/notes/{ni}"
                        nc = {**bc, "note": ni + 1}
                        inspect(note, "note", npath, nc)
                        if not isinstance(note, dict):
                            continue
                        visual_scrape = note.get('dead') is True and note.get('pickScrape') in ('up', 'down')
                        if (not visual_scrape and not note.get('rest') and type(note.get('fret')) is int and 24 < note['fret'] <= 48):
                            add_finding(report, feature='note.fret_range', category='game_limitation', impact='gameplay_omission',
                                        message='Notes above fret 24 and slide events leading above fret 24 are omitted from gameplay and scoring. Original pitches and positions remain in the retained source; no substitution was made.',
                                        location=npath + '/fret', value=note['fret'], **nc)
                        if (note.get("dead") is True and note.get("fret") is None and not note.get("rest")
                                and (any(note.get(key) for key in ("hp", "bend", "harmonic", "vibrato", "wideVibrato", "leftHandVibrato"))
                                     or note.get("slide") not in (None, "shift", "upwards", "downwards")
                                     and not (visual_scrape and note.get('slide') in ('above', 'below')))):
                            add_finding(report, feature="note.unpitched_mute", category="game_representation", impact="blocking",
                                        message="This unpitched mute also has a pitch gesture. That combination needs representation support; no fret or pitch was invented.",
                                        location=npath, value=note, **nc)
                        if isinstance(note.get("bend"), dict):
                            inspect(note["bend"], "bend", npath + "/bend", nc)
                            for point_index, point in enumerate(note["bend"].get("points", []) if isinstance(note["bend"].get("points", []), list) else []):
                                inspect(point, "bend_point", npath + f"/bend/points/{point_index}", nc)
    return report


def summary(report):
    if not report:
        return None
    return {key: deepcopy(report[key]) for key in ("version", "target", "status", "findingCount", "truncated", "omissions") if key in report}

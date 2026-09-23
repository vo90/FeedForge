"""Collect source capability gaps before conversion, without repairing music.

This is a capability inventory, not a proof that the music is correct. The
parser and independent verifier still validate every supported representation.
"""
from copy import deepcopy
import json
import math

VERSION = 6
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
LIMITATIONS = {
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
    "beat": {"grace", "graceNotes", "tremoloBar", "stroke", "whammy", "vibratoWithTremoloBar"},
    "note": {"trill", "grace", "graceNote", "tremoloBar", "whammy", "harmonicFret", "pickScrape", "vibratoWithTremoloBar"},
    "tempo": set(),
}


DECISIONS = {
    "pickScrape": "D1", "tremoloBar": "D2", "whammy": "D2", "vibratoWithTremoloBar": "D2",
    "harmonicFret": "D3", "trill": "D4", "rasgueado": "D5", "hasRasgueado": "D5", "unpitched_mute": "D6",
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
        decision = DECISIONS.get(feature.split(".")[-1])
        report["findings"].append({"feature": feature, "category": category, "impact": impact,
            "workStatus": "decision_required" if decision else "display_limitation" if impact == "display_or_expression" else "technical_work",
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
            for ti, tempo in enumerate(auto.get("tempo", []) if isinstance(auto.get("tempo", []), list) else []):
                inspect(tempo, "tempo", f"parts/{pi}/automations/tempo/{ti}", coordinates)
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
                        if note.get("dead") and note.get("fret") is None and not note.get("rest"):
                            add_finding(report, feature="note.unpitched_mute", category="game_representation", impact="blocking",
                                        message="This muted note has no authored fret. A faithful unpitched representation is required; no fret was invented.",
                                        location=npath, value=note, **nc)
                        if isinstance(note.get("bend"), dict):
                            inspect(note["bend"], "bend", npath + "/bend", nc)
                            for point_index, point in enumerate(note["bend"].get("points", []) if isinstance(note["bend"].get("points", []), list) else []):
                                inspect(point, "bend_point", npath + f"/bend/points/{point_index}", nc)
    return report


def summary(report):
    if not report:
        return None
    return {key: deepcopy(report[key]) for key in ("version", "target", "status", "findingCount", "truncated")}

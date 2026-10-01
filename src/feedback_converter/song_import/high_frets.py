"""Approved partial-chart policy; never modify the source performance in place."""
from copy import deepcopy
import hashlib
from .fingering import template_fingers

POLICY = "omit-unsupported-frets-v1"


def reason(note):
    if note.get("pick_scrape_marks"):
        return None
    if type(note.get("f")) is int and 24 < note["f"] <= 48:
        return "fret_above_24"
    if any(type(note.get(k)) is int and 24 < note[k] <= 48 for k in ("sl", "slu")):
        return "slide_target_above_24"
    return None


def position(track_id, note):
    return {"trackId": track_id, "scoreStart": round(note["t"], 6),
            "scoreDuration": round(note.get("sus", 0), 6),
            "string": note["s"], "fret": note["f"]}


def project(performance):
    """Return playable projection and a source-time receipt, before audio cuts."""
    receipt = {"version": 1, "policy": POLICY, "maxFret": 24,
               "notes": [], "links": [], "excludedTracks": [], "notationWithheld": []}
    if not any(reason(n) for track in performance["tracks"]
               for n in [*track.get("notes", []), *(n for c in track.get("chords", []) for n in c["notes"])]):
        return performance, receipt
    result = {**performance, "tracks": deepcopy(performance["tracks"])}
    for track in result["tracks"]:
        flat = list(track.get("notes", []))
        for chord in track.get("chords", []):
            for note in chord["notes"]:
                # Temporary absolute time is removed again from chord children.
                note.setdefault("t", chord["t"])
                flat.append(note)
        removed = {id(n) for n in flat if reason(n)}
        if not removed:
            for chord in track.get("chords", []):
                for note in chord["notes"]:
                    note.pop("t", None)
            continue
        for note in flat:
            if id(note) in removed:
                receipt["notes"].append({**position(track["id"], note), "reason": reason(note),
                    **{k: note[k] for k in ("sl", "slu") if k in note}})
        strings = {}
        for note in sorted(flat, key=lambda n: (n["t"], n["s"])):
            strings.setdefault(note["s"], []).append(note)
        for notes in strings.values():
            for note, following in zip(notes, notes[1:]):
                if id(note) not in removed and id(following) in removed and note.get("ln"):
                    receipt["links"].append(position(track["id"], note))
                    note.pop("ln")
        track["notes"] = [n for n in track.get("notes", []) if id(n) not in removed]
        chords, templates, shapes = [], [], {}
        for chord in track.get("chords", []):
            children = [n for n in chord["notes"] if id(n) not in removed]
            if len(children) == 1:
                track["notes"].append(children[0])
            elif children:
                template = deepcopy(track["templates"][chord["id"]])
                template["frets"] = [-1] * len(track["tuning"])
                template["fingers"] = template_fingers(children, len(track["tuning"]))
                for note in children:
                    template["frets"][note["s"]] = note["f"]
                    note.pop("t", None)
                shape = (tuple(template["frets"]), template.get("name", ""), tuple(template["fingers"]))
                if shape not in shapes:
                    shapes[shape] = len(templates)
                    templates.append(template)
                chords.append({**chord, "id": shapes[shape], "notes": children})
        track.update(chords=chords, templates=templates)
        track["notes"].sort(key=lambda n: (n["t"], n["s"]))
        if track.pop("notation", None) is not None:
            receipt["notationWithheld"].append(track["id"])
        if not track["notes"] and not track["chords"]:
            receipt["excludedTracks"].append(track["id"])
    for key in ("notes", "links"):
        receipt[key].sort(key=lambda n: (n["trackId"], n["scoreStart"], n["string"], n["fret"]))
    for key in ("excludedTracks", "notationWithheld"):
        receipt[key].sort()
    return result, receipt


def archive_receipt(receipt, source_path):
    return {**receipt, "sourceSha256": hashlib.sha256(source_path.read_bytes()).hexdigest()}


def summary(receipt):
    return {"policy": POLICY, "omittedNotes": len(receipt["notes"]),
            "omittedSlideEvents": sum(n["reason"] == "slide_target_above_24" for n in receipt["notes"]),
            "clearedLinks": len(receipt["links"]), "excludedTracks": receipt["excludedTracks"],
            "notationWithheld": receipt["notationWithheld"]}

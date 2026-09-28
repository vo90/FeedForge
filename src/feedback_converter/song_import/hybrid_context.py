"""Performed written slots for composition, independent of optional notation.

Coordinates are quarter-note beats. Repeated source IDs are disambiguated by
performed occurrence. This context is private to the worker, never a chart.
"""
from bisect import bisect_right
from copy import deepcopy
from dataclasses import asdict
from hashlib import sha256
import json


def _authored_bar_signature(source, bar_index, selected):
    """Retain raw musical distinctions which playable projection can erase."""
    atoms = []
    for note in source.bars[bar_index]:
        if selected is not None and int(note.voice_id) not in selected:
            continue
        atom = asdict(note)
        for key in ('source_id', 'beat_id', 'voice_id'):
            atom.pop(key, None)
        atoms.append(atom)
    atoms.sort(key=lambda value: json.dumps(value, sort_keys=True, default=str))
    beats = []
    for vi, voice in enumerate(source.written_bars[bar_index]):
        index = voice.source_index if voice.source_index is not None else vi
        if selected is not None and index not in selected:
            continue
        beats.extend((index, b.position, b.duration, b.rest, b.denominator, b.dots,
                      b.tuplet, b.grace, b.written_position, b.written_duration) for b in voice.beats)
    body = json.dumps([atoms, beats], sort_keys=True, default=str, separators=(',', ':'))
    return sha256(body.encode('utf-8')).hexdigest()


class Clock:
    def __init__(self, timeline):
        self.points = timeline["tempoPoints"]

    def seconds(self, quarter):
        i = max(0, bisect_right(self.points, quarter, key=lambda p: p["quarter"]) - 1)
        p = self.points[i]
        return p["time"] + (quarter - p["quarter"]) * 60 / p["bpm"]

    def quarter(self, seconds):
        i = max(0, bisect_right(self.points, seconds, key=lambda p: p["time"]) - 1)
        p = self.points[i]
        return p["quarter"] + (seconds - p["time"]) * p["bpm"] / 60


def capture(score, performance):
    timeline = performance["scoreTimeline"]
    tracks = {}
    for output in performance["tracks"]:
        original_id, selected = output["id"], None
        for row in performance.get("voiceProjection", {}).get("tracks", []):
            match = next((a for a in row["arrangements"] if a["id"] == output["id"]), None)
            if match:
                original_id, selected = row["sourceTrackId"], match["voices"]
        source = next(t for t in score.tracks if t.id == original_id)
        beats = []
        for visit in timeline["measures"]:
            for vi, voice in enumerate(source.written_bars[visit["writtenIndex"]]):
                index = voice.source_index if voice.source_index is not None else vi
                if selected is not None and index not in selected:
                    continue
                for beat in voice.beats:
                    start = visit["quarter"] + float(beat.position)
                    beats.append({"sourceId": beat.source_id, "occurrence": visit["index"] + 1,
                                  "voice": index, "start": start, "end": start + float(beat.duration),
                                  "rest": beat.rest, "grace": beat.grace,
                                  "noteIds": [n.source_id for n in beat.notes],
                                  "pitchOffsets": sorted({n.effects.get("__harmonic_pitch_offset", 0) for n in beat.notes})})
        tracks[output["id"]] = {"sourceTrackId": original_id, "voices": selected, "beats": beats,
                                 "authoredBarSignatures": [_authored_bar_signature(source, i, selected)
                                                           for i in range(len(source.bars))]}
    return {"version": 1, "timeline": deepcopy(timeline), "tracks": tracks}

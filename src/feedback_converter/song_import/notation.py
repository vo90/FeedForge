"""Write source notation alongside, never reconstructing it from flat notes.

This adapter is independently implemented from the public FeedPak vocabulary.
The playable chart remains authoritative for performed durations. ``end_time``
retains the separately mapped endpoint without changing written note lengths.
"""
from copy import deepcopy
from fractions import Fraction


def _fraction(value):
    value = Fraction(value)
    return [value.numerator, value.denominator]


def _note(note, track, performed):
    result = {"midi": track.tuning[note.string] + track.capo + note.fret,
              "str": note.string, "fret": note.fret, "source_id": note.source_id}
    if note.tie:
        result["tied"] = True
    effects = {**note.effects, **{key: value for key, value in performed.items() if not note.tie and key in {"ho", "po"}}}
    for source, target in {"mt": "dead", "ghost": "ghost", "vb": "vib", "__wide_vibrato": "vibw", "ac": "ac",
                           "tp": "tp", "ho": "ho", "po": "po", "fg": "fng"}.items():
        if effects.get(source):
            result[target] = effects[source]
    # Source-only detail keeps its original representation in sourceScore.
    # Do not assign a new meaning to an underspecified notation effect object.
    return result


def render_notation(score, track, visits, at, performed_notes=()):
    if not track.written_bars:
        return None, []
    for voices in track.written_bars:
        for voice in voices:
            for beat in voice.beats:
                if beat.denominator not in {1, 2, 4, 8, 16, 32} or not 0 <= beat.dots <= 2:
                    return None, [f"Notation for {track.name} is retained in source evidence only: "
                                  f"written duration at {beat.source_id} is outside FeedPak notation v1."]
    staves = [{"id": "staff", "clef": "F4" if track.instrument == "bass" else "G2", "label": track.name}]
    performed_by_source = {(source_id, round(note["t"], 8)): note
                           for note in performed_notes for source_id in note.get("source_ids", [])}
    measures = []
    written_tempos = []
    tempo = 120.0
    for measure in score.measures:
        current = next((bpm for position, bpm in sorted(measure.tempos) if position == 0), tempo)
        written_tempos.append(current)
        for _, tempo in sorted(measure.tempos):
            pass
    for occurrence, (index, start) in enumerate(visits):
        source_measure = score.measures[index]
        measure = {"idx": occurrence + 1, "t": at(start), "end_time": at(start + source_measure.length),
                   "ts": [source_measure.numerator, source_measure.denominator],
                   "tempo": written_tempos[index], "source_measure": index + 1,
                   "staves": {"staff": {"voices": []}}}
        for vi, voice in enumerate(track.written_bars[index]):
            beats = []
            for beat in voice.beats:
                item = {"t": at(start + beat.position), "end_time": at(start + beat.position + beat.duration),
                        "dur": beat.denominator, "source_id": beat.source_id,
                        "beat_pos": _fraction(beat.written_position if beat.written_position is not None else beat.position),
                        **deepcopy(beat.annotations)}
                if beat.dots:
                    item["dot"] = beat.dots
                if beat.tuplet:
                    item["tu"] = list(beat.tuplet)
                if beat.grace:
                    item["grace"] = beat.grace
                if beat.rest:
                    item["rest"] = True
                else:
                    item["notes"] = [_note(note, track, performed_by_source.get(
                        (note.source_id, round(at(start + note.position), 8)), {})) for note in beat.notes]
                beats.append(item)
            voice_index = voice.source_index if voice.source_index is not None else vi
            measure["staves"]["staff"]["voices"].append({"v": voice_index, "source_id": voice.source_id, "beats": beats})
        measures.append(measure)
    return {"version": 1, "instrument": track.instrument, "staves": staves, "measures": measures}, []

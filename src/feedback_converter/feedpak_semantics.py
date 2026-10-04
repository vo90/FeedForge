from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any


ErrorSink = Callable[[str], None]
DEFAULT_STRING_COUNT = 6
MAX_FRET = 24
LEVEL_EVENT_KEYS = ("notes", "chords", "anchors", "handshapes")
BOOLEAN_NOTE_FIELDS = (
    "ho",
    "po",
    "hm",
    "hp",
    "pm",
    "mt",
    "vb",
    "tr",
    "ac",
    "tp",
    "ln",
    "fhm",
    "plk",
    "slp",
    "ig",
)


def validate_manifest_semantics(
    manifest: dict[str, Any], label: str, error: ErrorSink
) -> None:
    _nonnegative_number(manifest.get("duration"), f"{label}: duration", error)


def validate_arrangement_semantics(
    data: dict[str, Any],
    manifest_entry: dict[str, Any],
    label: str,
    error: ErrorSink,
) -> None:
    string_count = _effective_string_count(data, manifest_entry)
    template_count = len(data.get("templates", []))

    _validate_templates(data.get("templates", []), label, string_count, error,
                        unpitched=_unpitched_template_slots(data))
    _validate_payload(data, label, string_count, template_count, error)
    _validate_phrases(data.get("phrases", []), label, string_count, template_count, error)
    _validate_timed_collection(data.get("tempos", []), "time", f"{label}: tempos", error)
    _validate_timed_collection(data.get("sections", []), "time", f"{label}: sections", error)
    _validate_tempos(data.get("tempos", []), f"{label}: tempos", error)
    _validate_beats(data.get("beats", []), f"{label}: beats", error)


def validate_timeline_semantics(data: dict[str, Any], label: str, error: ErrorSink) -> None:
    for key in ("tempos", "time_signatures", "sections"):
        _validate_timed_collection(data.get(key, []), "time", f"{label}: {key}", error)
    _validate_tempos(data.get("tempos", []), f"{label}: tempos", error)
    _validate_beats(data.get("beats", []), f"{label}: beats", error)


def _effective_string_count(data: dict[str, Any], manifest_entry: dict[str, Any]) -> int:
    manifest_tuning = manifest_entry.get("tuning")
    if isinstance(manifest_tuning, list) and manifest_tuning:
        return len(manifest_tuning)
    arrangement_tuning = data.get("tuning")
    if isinstance(arrangement_tuning, list) and arrangement_tuning:
        return len(arrangement_tuning)
    return DEFAULT_STRING_COUNT


def _validate_payload(
    payload: dict[str, Any],
    label: str,
    string_count: int,
    template_count: int,
    error: ErrorSink,
    *,
    window: tuple[float, float] | None = None,
) -> None:
    notes = payload.get("notes", [])
    chords = payload.get("chords", [])
    anchors = payload.get("anchors", [])
    handshapes = payload.get("handshapes", [])

    _validate_timed_collection(notes, "t", f"{label}: notes", error)
    _validate_timed_collection(chords, "t", f"{label}: chords", error)
    _validate_timed_collection(anchors, "time", f"{label}: anchors", error)
    _validate_timed_collection(handshapes, "start_time", f"{label}: handshapes", error)

    for index, note in enumerate(notes):
        path = f"{label}: notes/{index}"
        _validate_note(note, path, string_count, error)
        _validate_event_in_window(note.get("t"), path, window, error)

    for index, chord in enumerate(chords):
        path = f"{label}: chords/{index}"
        _nonnegative_number(chord.get("t"), f"{path}/t", error)
        _template_reference(chord.get("id", 0), f"{path}/id", template_count, error)
        for note_index, note in enumerate(chord.get("notes", [])):
            _validate_note(note, f"{path}/notes/{note_index}", string_count, error, timed=False)
        _validate_event_in_window(chord.get("t"), path, window, error)

    for index, anchor in enumerate(anchors):
        path = f"{label}: anchors/{index}"
        _nonnegative_number(anchor.get("time"), f"{path}/time", error)
        fret = anchor.get("fret")
        if isinstance(fret, int) and not isinstance(fret, bool) and fret < 0:
            error(f"{path}/fret: must be >= 0")
        width = anchor.get("width", 4)
        if isinstance(width, int) and not isinstance(width, bool) and width <= 0:
            error(f"{path}/width: must be > 0")
        _validate_event_in_window(anchor.get("time"), path, window, error)

    for index, handshape in enumerate(handshapes):
        path = f"{label}: handshapes/{index}"
        start = _nonnegative_number(handshape.get("start_time", 0.0), f"{path}/start_time", error)
        end = _nonnegative_number(handshape.get("end_time", 0.0), f"{path}/end_time", error)
        if start is not None and end is not None and end < start:
            error(f"{path}: end_time must be >= start_time")
        _template_reference(handshape.get("chord_id", 0), f"{path}/chord_id", template_count, error)
        _validate_span_in_window(start, end, path, window, error)


def _valid_pick_scrape(note: dict[str, Any]) -> bool:
    marks, sustain = note.get("pick_scrape_marks"), note.get("sus")
    if (note.get("mt") is not True or not isinstance(marks, list) or not marks
            or type(sustain) not in (int, float) or not math.isfinite(sustain) or sustain <= 0):
        return False
    previous = 0.0
    for mark in marks:
        if (not isinstance(mark, dict) or set(mark) != {"direction", "start", "end"}
                or mark.get("direction") not in ("up", "down")
                or any(type(mark.get(k)) not in (int, float) or not math.isfinite(mark[k]) for k in ("start", "end"))):
            return False
        if not previous <= mark["start"] < mark["end"] <= sustain + 0.0000011:
            return False
        previous = mark["end"]
    return True


def _validate_note(
    note: dict[str, Any],
    path: str,
    string_count: int,
    error: ErrorSink,
    *,
    timed: bool = True,
) -> None:
    if timed:
        _nonnegative_number(note.get("t"), f"{path}/t", error)
    string = note.get("s")
    if (
        isinstance(string, int)
        and not isinstance(string, bool)
        and not 0 <= string < string_count
    ):
        error(f"{path}/s: string {string} is outside the effective 0..{string_count - 1} range")
    # Existing game wire sentinel: a dead strike with no authored fret. Do
    # not widen the physical fret range or accept palm mute alone as evidence.
    scrape = _valid_pick_scrape(note)
    if 'slide_interval' in note:
        from .slide_interval import validate
        try:
            validate(note)
        except ValueError as exc:
            error(f'{path}/slide_interval: {exc}')
    if "harmonic_target" in note:
        from .harmonic_target import valid_note_target
        if not valid_note_target(note):
            error(f"{path}/harmonic_target: invalid fretted-harmonic target or conflicting note flags")
    if "whammy" in note:
        from .whammy import valid_whammy
        if not valid_whammy(note['whammy'], note.get('sus', 0)):
            error(f"{path}/whammy: invalid bar expression or interval")
    if "harmonic_alias" in note and not (note["harmonic_alias"] == "songsterr-natural-15"
            and note.get("hm") is True and type(note.get("f")) is int and note["f"] == 15
            and not any(note.get(k) for k in ("hp", "mt", "fhm"))
            and type(note.get("hn")) in (int, float) and note["hn"] == 14.7
            and type(note.get("hps")) is int and note["hps"] == 34):
        error(f"{path}/harmonic_alias: inconsistent natural harmonic interpretation")
    if "pick_scrape_marks" in note and not scrape:
        error(f"{path}/pick_scrape_marks: requires a dead note with ordered, positive intervals within its sustain")
    unpitched = scrape or type(note.get("f")) is int and note["f"] == 127 and note.get("mt") is True
    if scrape:
        # This is retained source metadata, never a playable fret or pitch.
        _bounded_integer(note.get("f"), f"{path}/f", 0, 127, error)
    elif not unpitched:
        _bounded_integer(note.get("f"), f"{path}/f", 0, MAX_FRET, error)
    if unpitched and (any(note.get(key) for key in ("ho", "po", "hm", "hp", "bn", "bnv", "vb", "slide_in_marks")) or any(
            isinstance(note.get(key), (int, float)) and note[key] >= 0 for key in ("sl", "slu"))):
        error(f"{path}: an unpitched mute cannot carry a pitched gesture")
    if unpitched and (note.get("slide_out") or note.get("slide_out_marks")) and (scrape or not note.get("slide_out_marks")):
        error(f"{path}: an unpitched slide-out requires explicit intervals and cannot also be a pick scrape")
    if "sus" in note:
        _nonnegative_number(note["sus"], f"{path}/sus", error)
    for field in ("sl", "slu"):
        if field in note:
            _bounded_integer(note[field], f"{path}/{field}", -1, MAX_FRET, error)
    if "bn" in note:
        _known_nonnegative_number(note["bn"], f"{path}/bn", error)
    if "bt" in note:
        _bounded_integer(note["bt"], f"{path}/bt", 0, 4, error)
    if "bnv" in note:
        _validate_bend_curve(note["bnv"], f"{path}/bnv", error)
    if "rh" in note:
        _bounded_integer(note["rh"], f"{path}/rh", -1, None, error)
    if "pkd" in note:
        _integer_choice(note["pkd"], f"{path}/pkd", {-1, 0, 1}, error)
    if "fg" in note:
        _bounded_integer(note["fg"], f"{path}/fg", -1, 4, error)
    if "ch" in note:
        _bounded_integer(note["ch"], f"{path}/ch", -1, None, error)
    if "sd" in note:
        _bounded_integer(note["sd"], f"{path}/sd", -1, 11, error)
    for field in BOOLEAN_NOTE_FIELDS:
        if field in note and not isinstance(note[field], bool):
            error(f"{path}/{field}: must be a boolean")


def _validate_bend_curve(points: Any, label: str, error: ErrorSink) -> None:
    if not isinstance(points, list):
        error(f"{label}: must be an array")
        return
    previous: float | None = None
    for index, point in enumerate(points):
        if not isinstance(point, dict):
            error(f"{label}/{index}: must be an object")
            continue
        value = _known_nonnegative_number(point.get("t"), f"{label}/{index}/t", error)
        bend = point.get("v")
        if not _is_number(bend):
            error(f"{label}/{index}/v: must be a number")
        elif not math.isfinite(float(bend)):
            error(f"{label}/{index}/v: must be finite")
        if value is not None and previous is not None and value < previous:
            error(f"{label}: point times must be in non-descending order")
            break
        if value is not None:
            previous = value


def _validate_phrases(
    phrases: list[dict[str, Any]],
    label: str,
    string_count: int,
    template_count: int,
    error: ErrorSink,
) -> None:
    _validate_timed_collection(phrases, "start_time", f"{label}: phrases", error)
    for phrase_index, phrase in enumerate(phrases):
        path = f"{label}: phrases/{phrase_index}"
        start = _nonnegative_number(phrase.get("start_time"), f"{path}/start_time", error)
        end = _nonnegative_number(phrase.get("end_time"), f"{path}/end_time", error)
        if start is not None and end is not None and end <= start:
            error(f"{path}: end_time must be > start_time")

        maximum = phrase.get("max_difficulty", 0)
        if not isinstance(maximum, int) or isinstance(maximum, bool) or maximum < 0:
            error(f"{path}/max_difficulty: must be an integer >= 0")
            maximum = None

        levels = phrase.get("levels", [])

        previous_difficulty: int | None = None
        for level_index, level in enumerate(levels):
            level_path = f"{path}/levels/{level_index}"
            if not isinstance(level, dict):
                error(f"{level_path}: must be an object")
                continue
            malformed = [
                key for key in LEVEL_EVENT_KEYS
                if key in level and not isinstance(level[key], list)
            ]
            if malformed:
                error(f"{level_path}: fields must be arrays: {', '.join(malformed)}")
                continue

            difficulty = level.get("difficulty", 0)
            if not isinstance(difficulty, int) or isinstance(difficulty, bool) or difficulty < 0:
                error(f"{level_path}/difficulty: must be an integer >= 0")
            else:
                if previous_difficulty is not None and difficulty <= previous_difficulty:
                    error(f"{path}/levels: difficulties must be in strictly increasing order")
                if maximum is not None and difficulty > maximum:
                    error(f"{level_path}/difficulty: exceeds max_difficulty {maximum}")
                previous_difficulty = difficulty

            window = (start, end) if start is not None and end is not None and end > start else None
            _validate_payload(level, level_path, string_count, template_count, error, window=window)


def _unpitched_template_slots(data: dict[str, Any]) -> set[tuple[int, int]]:
    """A sentinel template slot needs an explicit dead child on every use.

    Include practice levels: accepting a template-only use would fabricate a
    pitched note when the loader expands it. Unreferenced slots are not proven.
    """
    candidates = {ti: [si for si, fret in enumerate(template.get("frets", [])) if type(fret) is int and fret > MAX_FRET]
                  for ti, template in enumerate(data.get("templates", []))}
    seen, invalid = set(), set()
    payloads = [data] + [level for phrase in data.get("phrases", []) for level in phrase.get("levels", [])]
    for payload in payloads:
        for chord in payload.get("chords", []):
            for string in candidates.get(chord.get("id", 0), []):
                slot = (chord.get("id", 0), string)
                seen.add(slot)
                children = [n for n in chord.get("notes", []) if n.get("s") == slot[1]]
                expected = data["templates"][slot[0]]["frets"][string]
                if (len(children) != 1 or children[0].get("f") != expected or children[0].get("mt") is not True
                        or expected != 127 and not _valid_pick_scrape(children[0])):
                    invalid.add(slot)
    return seen - invalid


def _validate_templates(
    templates: list[dict[str, Any]],
    label: str,
    string_count: int,
    error: ErrorSink,
    *, unpitched: set[tuple[int, int]] = frozenset(),
) -> None:
    for template_index, template in enumerate(templates):
        path = f"{label}: templates/{template_index}"
        for field, minimum, maximum in (
            ("frets", -1, MAX_FRET),
            ("fingers", -1, 4),
        ):
            if field not in template:
                continue
            values = template[field]
            if len(values) != string_count:
                error(f"{path}/{field}: length must match effective string count {string_count}")
            for value_index, value in enumerate(values):
                if field == "frets" and (template_index, value_index) in unpitched:
                    continue
                _bounded_integer(
                    value,
                    f"{path}/{field}/{value_index}",
                    minimum,
                    maximum,
                    error,
                )


def _validate_tempos(tempos: list[dict[str, Any]], label: str, error: ErrorSink) -> None:
    for index, tempo in enumerate(tempos):
        bpm = tempo.get("bpm")
        if _is_number(bpm) and (not math.isfinite(float(bpm)) or float(bpm) <= 0):
            error(f"{label}/{index}/bpm: must be finite and > 0")


def _validate_beats(beats: list[dict[str, Any]], label: str, error: ErrorSink) -> None:
    _validate_timed_collection(beats, "time", label, error, strict=True)
    previous_measure: int | None = None
    for index, beat in enumerate(beats):
        measure = beat.get("measure")
        if not isinstance(measure, int) or isinstance(measure, bool):
            continue
        if measure == -1:
            continue
        if measure < 1:
            error(f"{label}/{index}/measure: must be -1 or a 1-based downbeat number")
            continue
        expected = 1 if previous_measure is None else previous_measure + 1
        if measure != expected:
            error(f"{label}/{index}/measure: expected downbeat {expected}, got {measure}")
        previous_measure = measure


def _validate_timed_collection(
    items: list[dict[str, Any]],
    key: str,
    label: str,
    error: ErrorSink,
    *,
    strict: bool = False,
) -> None:
    previous: float | None = None
    for index, item in enumerate(items):
        value = _nonnegative_number(item.get(key), f"{label}/{index}/{key}", error)
        if value is not None and previous is not None:
            out_of_order = value <= previous if strict else value < previous
            if out_of_order:
                order = "strictly increasing" if strict else "non-descending"
                error(f"{label}: {key} values must be in {order} order")
                return
        if value is not None:
            previous = value


def _validate_event_in_window(
    value: Any,
    label: str,
    window: tuple[float, float] | None,
    error: ErrorSink,
) -> None:
    if window is None or not _is_finite_number(value):
        return
    start, end = window
    if not start <= float(value) <= end:
        error(f"{label}: event time must be inside phrase window [{start}, {end}]")


def _validate_span_in_window(
    start: float | None,
    end: float | None,
    label: str,
    window: tuple[float, float] | None,
    error: ErrorSink,
) -> None:
    if window is None or start is None or end is None:
        return
    window_start, window_end = window
    if start < window_start or start > window_end or end > window_end:
        error(f"{label}: span must be inside phrase window [{window_start}, {window_end}]")


def _template_reference(value: Any, label: str, count: int, error: ErrorSink) -> None:
    if isinstance(value, int) and not isinstance(value, bool) and not 0 <= value < count:
        error(f"{label}: template index {value} is outside 0..{count - 1}")


def _known_nonnegative_number(value: Any, label: str, error: ErrorSink) -> float | None:
    if not _is_number(value):
        error(f"{label}: must be a number")
        return None
    return _nonnegative_number(value, label, error)


def _bounded_integer(
    value: Any,
    label: str,
    minimum: int,
    maximum: int | None,
    error: ErrorSink,
) -> int | None:
    if not isinstance(value, int) or isinstance(value, bool):
        error(f"{label}: must be an integer")
        return None
    if value < minimum or maximum is not None and value > maximum:
        expected = f">= {minimum}" if maximum is None else f"between {minimum} and {maximum}"
        error(f"{label}: must be {expected}")
        return None
    return value


def _integer_choice(value: Any, label: str, choices: set[int], error: ErrorSink) -> int | None:
    if not isinstance(value, int) or isinstance(value, bool):
        error(f"{label}: must be an integer")
        return None
    if value not in choices:
        expected = ", ".join(str(choice) for choice in sorted(choices))
        error(f"{label}: must be one of {expected}")
        return None
    return value


def _nonnegative_number(value: Any, label: str, error: ErrorSink) -> float | None:
    if not _is_number(value):
        return None
    number = float(value)
    if not math.isfinite(number) or number < 0:
        error(f"{label}: must be finite and >= 0")
        return None
    return number


def _is_finite_number(value: Any) -> bool:
    return _is_number(value) and math.isfinite(float(value))


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)

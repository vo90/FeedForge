"""Safe Rocksmith arrangement XML parser and SNG-compatible chart adapter.

Rocksmith packages normally contain both an authored arrangement XML and a
compiled SNG.  The XML can be used as an authoritative chart-data fallback when
the SNG archive entry is missing, provided its package identity is checked
first.  Compiler-generated chord templates that lack manifest metadata are
reported explicitly as conservative approximations.

The public parser returns a lightweight object with the attributes consumed by
``converter._song_to_arrangement`` and ``converter._song_to_timeline``.  It is
deliberately independent of ``converter.py`` so the fallback policy remains at
the call site.
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Iterable


UINT32_NONE = 0xFFFFFFFF
CHORD_MASK_ARPEGGIO = 0x1

NOTE_MASK_FRETHANDMUTE = 0x08
NOTE_MASK_TREMOLO = 0x10
NOTE_MASK_HARMONIC = 0x20
NOTE_MASK_PALMMUTE = 0x40
NOTE_MASK_SLAP = 0x80
NOTE_MASK_PLUCK = 0x0100
NOTE_MASK_HAMMERON = 0x0200
NOTE_MASK_PULLOFF = 0x0400
NOTE_MASK_SUSTAIN = 0x2000
NOTE_MASK_TAP = 0x4000
NOTE_MASK_PINCHHARMONIC = 0x8000
NOTE_MASK_VIBRATO = 0x010000
NOTE_MASK_MUTE = 0x020000
NOTE_MASK_IGNORE = 0x040000
NOTE_MASK_HIGHDENSITY = 0x200000
NOTE_MASK_ACCENT = 0x04000000
NOTE_MASK_PARENT = 0x08000000

MAX_XML_BYTES = 16 * 1024 * 1024
MAX_XML_NODES = 250_000
MAX_XML_DEPTH = 128
XML_PARSE_CHUNK_CHARS = 64 * 1024
SUPPORTED_XML_VERSIONS = frozenset({"7"})
_DECLARATION_RE = re.compile(r"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)


class RocksmithXmlError(ValueError):
    """Raised when an arrangement XML is unsafe, malformed, or mismatched."""


@dataclass(frozen=True)
class XmlBend:
    time: float
    step: float
    UNK: int = 0


@dataclass(frozen=True)
class XmlBendSet:
    bendValues: tuple[XmlBend, ...] = ()

    @property
    def count(self) -> int:
        return len(self.bendValues)


@dataclass(frozen=True)
class XmlBeat:
    time: float
    measure: int
    beat: int
    phraseIteration: int = 0
    mask: int = 0


@dataclass(frozen=True)
class XmlPhrase:
    solo: int
    disparity: int
    ignore: int
    maxDifficulty: int
    name: str
    phraseIterationLinks: int = 0


@dataclass(frozen=True)
class XmlPhraseIteration:
    phraseId: int
    time: float
    endTime: float
    difficulty: tuple[int, int, int] = (0, 0, 0)


@dataclass(frozen=True)
class XmlChordTemplate:
    mask: int
    frets: tuple[int, int, int, int, int, int]
    fingers: tuple[int, int, int, int, int, int]
    name: str
    notes: tuple[int, int, int, int, int, int] = (-1, -1, -1, -1, -1, -1)


@dataclass(frozen=True)
class RocksmithChordTemplateHint:
    """Authoritative compiled template metadata selected from one song manifest.

    ``mask=None`` means the manifest omitted the field.  In that case the
    parser inherits a uniquely matching XML master's mask when both its frets
    and normalized name match; otherwise the conservative mask is zero.
    Explicit integer masks, including zero, always take precedence.
    """

    frets: tuple[int, int, int, int, int, int]
    fingers: tuple[int, int, int, int, int, int]
    name: str = ""
    mask: int | None = None


@dataclass(frozen=True)
class XmlChordNote:
    mask: tuple[int, int, int, int, int, int]
    bends: tuple[XmlBendSet, XmlBendSet, XmlBendSet, XmlBendSet, XmlBendSet, XmlBendSet]
    slideTo: tuple[int, int, int, int, int, int]
    slideUnpitchTo: tuple[int, int, int, int, int, int]
    vibrato: tuple[int, int, int, int, int, int]


@dataclass(frozen=True)
class XmlNote:
    mask: int
    time: float
    string: int
    fret: int
    chordId: int = UINT32_NONE
    chordNoteId: int = UINT32_NONE
    sustain: float = 0.0
    slideTo: int = -1
    slideUnpitchTo: int = -1
    leftHand: int = -1
    tap: int = -1
    pickDirection: int = 0
    slap: int = -1
    pluck: int = -1
    vibrato: int = 0
    bend_time: float = 0.0
    bends: tuple[XmlBend, ...] = ()
    flags: int = 0
    hash: int = 0
    anchorFret: int = -1
    anchorWidth: int = -1
    phraseId: int = 0
    phraseIterationId: int = 0
    fingerPrintId: tuple[int, int] = (0, 0)
    nextIterNote: int = 0
    prevIterNote: int = 0
    parentPrevNote: int = 0


@dataclass(frozen=True)
class XmlAnchor:
    time: float
    endTime: float
    fret: int
    width: int
    UNK_time: float = 0.0
    UNK_time2: float = 0.0
    phraseIterationId: int = 0


@dataclass(frozen=True)
class XmlFingerPrint:
    chordId: int
    startTime: float
    endTime: float
    UNK_startTime: float = 0.0
    UNK_endTime: float = 0.0


@dataclass(frozen=True)
class XmlLevel:
    difficulty: int
    anchors: tuple[XmlAnchor, ...]
    fingerprints: tuple[tuple[XmlFingerPrint, ...], tuple[XmlFingerPrint, ...]]
    notes: tuple[XmlNote, ...]
    anchor_extensions: tuple[object, ...] = ()
    averageNotesPerIter: tuple[float, ...] = ()
    notesInIterCountNoIgnored: tuple[int, ...] = ()
    notesInIterCount: tuple[int, ...] = ()


@dataclass(frozen=True)
class XmlTone:
    time: float
    id: int
    name: str = ""


@dataclass(frozen=True)
class XmlSection:
    name: str
    number: int
    startTime: float
    endTime: float
    startPhraseIterationId: int = 0
    endPhraseIterationId: int = 0
    stringMask: tuple[int, ...] = (0,) * 36


@dataclass(frozen=True)
class XmlEvent:
    time: float
    name: str


@dataclass(frozen=True)
class XmlMetadata:
    songLength: float
    tuning: tuple[int, int, int, int, int, int]
    capo: int
    part: int
    maxDifficulty: int
    startTime: float = 0.0
    firstBeatLength: float = 0.0
    firstNoteTime: float = 0.0
    firstNoteTime2: float = 0.0
    maxScores: float = 0.0
    maxNotes: float = 0.0
    maxNotesNoIgnored: float = 0.0
    pointsPerNote: float = 0.0
    lastConversionDateTime: str = ""


@dataclass(frozen=True)
class RocksmithXmlSong:
    """Normalized XML arrangement with the shape of a parsed SNG ``Song``."""

    source_path: str
    song_key: str
    arrangement: str
    title: str
    artist: str
    album: str
    album_year: str
    cent_offset: float
    offset: float
    tone_base: str
    tone_slots: tuple[str, str, str, str]
    arrangement_properties: dict[str, str]
    beats: tuple[XmlBeat, ...]
    phrases: tuple[XmlPhrase, ...]
    chordTemplates: tuple[XmlChordTemplate, ...]
    approximate_chord_template_ids: tuple[int, ...]
    chordNotes: tuple[XmlChordNote, ...]
    phraseIterations: tuple[XmlPhraseIteration, ...]
    events: tuple[XmlEvent, ...]
    tones: tuple[XmlTone, ...]
    sections: tuple[XmlSection, ...]
    levels: tuple[XmlLevel, ...]
    metadata: XmlMetadata
    vocals: tuple[object, ...] = ()
    symbols: None = None
    phraseExtraInfos: tuple[object, ...] = ()
    newLinkedDiffs: tuple[object, ...] = ()
    actions: tuple[object, ...] = ()
    dna: tuple[object, ...] = ()


@dataclass(frozen=True)
class _RawChord:
    element: ET.Element = field(compare=False, repr=False)
    raw_chord_id: int


def parse_rocksmith_arrangement_xml(
    data: bytes | str,
    *,
    source_path: str,
    expected_song_key: str,
    expected_arrangement: str,
    allowed_xml_arrangements: Iterable[str] | None = None,
    compiled_chord_templates: Mapping[int, RocksmithChordTemplateHint] | None = None,
) -> RocksmithXmlSong:
    """Parse one Rocksmith v7 arrangement XML into a converter-compatible song.

    ``source_path`` must be the archive-relative XML path.  Rocksmith XML does
    not carry a SongKey field, so the filename is the independent package
    identity used to verify ``expected_song_key``.  The filename suffix must
    match ``expected_arrangement``.  By default the XML ``<arrangement>`` must
    also match it; callers handling known legacy labels may provide a separate
    manifest-derived ``allowed_xml_arrangements`` set.
    """

    text = _safe_xml_text(data)
    root = _bounded_xml_root(text)
    if root.tag != "song":
        raise RocksmithXmlError(f"Expected <song> root, found <{root.tag}>.")
    version = str(root.attrib.get("version") or "").strip()
    if version not in SUPPORTED_XML_VERSIONS:
        raise RocksmithXmlError(
            f"Unsupported Rocksmith arrangement XML version {version!r}; expected version 7."
        )

    arrangement = _single_text(root, "arrangement", required=True)
    _validate_identity(
        source_path=source_path,
        expected_song_key=expected_song_key,
        expected_arrangement=expected_arrangement,
        xml_arrangement=arrangement,
        allowed_xml_arrangements=allowed_xml_arrangements,
    )

    song_length = _single_float(root, "songLength", minimum=0.001)
    tuning = _parse_tuning(_single_element(root, "tuning", required=True))
    capo = _single_int(root, "capo", default=0)
    part = _single_int(root, "part", default=0)
    cent_offset = _single_float(root, "centOffset", default=0.0)
    offset = _single_float(root, "offset", default=0.0)

    phrases = _parse_phrases(root)
    phrase_iterations = _parse_phrase_iterations(root, phrases, song_length)
    beats = _parse_beats(root, phrase_iterations)
    master_templates = list(_parse_chord_templates(root))
    raw_levels = _raw_levels(root)
    chord_templates, template_map, approximate_chord_template_ids = _complete_chord_templates(
        master_templates,
        raw_levels,
        compiled_chord_templates,
    )
    chord_notes: list[XmlChordNote] = []
    levels = _parse_levels(raw_levels, chord_templates, template_map, chord_notes, song_length)
    sections = _parse_sections(root, phrase_iterations, song_length)
    tone_base = _single_text(root, "tonebase", required=False)
    tone_slots = tuple(
        _single_text(root, tag, required=False)
        for tag in ("tonea", "toneb", "tonec", "toned")
    )
    tones = _parse_tones(root, tone_slots)
    events = tuple(
        XmlEvent(
            time=_float_attr(element, "time"),
            name=str(element.attrib.get("code") or element.attrib.get("name") or ""),
        )
        for element in _container_children(root, "events", "event", required=False)
    )

    if not levels:
        raise RocksmithXmlError("Rocksmith arrangement XML has no difficulty levels.")
    if not any(level.notes for level in levels):
        raise RocksmithXmlError("Rocksmith arrangement XML has no playable notes or chords.")

    first_note_time = min(
        (note.time for level in levels for note in level.notes),
        default=0.0,
    )
    max_difficulty = max(level.difficulty for level in levels)
    first_beat_length = beats[1].time - beats[0].time if len(beats) > 1 else 0.0
    metadata = XmlMetadata(
        songLength=song_length,
        tuning=tuning,
        capo=capo,
        part=part,
        maxDifficulty=max_difficulty,
        startTime=beats[0].time if beats else 0.0,
        firstBeatLength=max(0.0, first_beat_length),
        firstNoteTime=first_note_time,
        firstNoteTime2=first_note_time,
        lastConversionDateTime=_single_text(root, "lastConversionDateTime", required=False),
    )

    properties_element = _single_element(root, "arrangementProperties", required=False)
    properties = dict(properties_element.attrib) if properties_element is not None else {}
    return RocksmithXmlSong(
        source_path=source_path,
        song_key=expected_song_key,
        arrangement=arrangement,
        title=_single_text(root, "title", required=True),
        artist=_single_text(root, "artistName", required=False),
        album=_single_text(root, "albumName", required=False),
        album_year=_single_text(root, "albumYear", required=False),
        cent_offset=cent_offset,
        offset=offset,
        tone_base=tone_base,
        tone_slots=tone_slots,
        arrangement_properties=properties,
        beats=beats,
        phrases=phrases,
        chordTemplates=tuple(chord_templates),
        approximate_chord_template_ids=approximate_chord_template_ids,
        chordNotes=tuple(chord_notes),
        phraseIterations=phrase_iterations,
        events=events,
        tones=tones,
        sections=sections,
        levels=levels,
        metadata=metadata,
    )


def _safe_xml_text(data: bytes | str) -> str:
    if isinstance(data, bytes):
        size = len(data)
        if b"\x00" in data:
            raise RocksmithXmlError("Rocksmith arrangement XML must be UTF-8, not a NUL-containing encoding.")
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise RocksmithXmlError("Rocksmith arrangement XML is not valid UTF-8.") from exc
    elif isinstance(data, str):
        text = data.lstrip("\ufeff")
        size = len(text.encode("utf-8"))
    else:
        raise TypeError("Rocksmith arrangement XML must be bytes or str.")
    if size > MAX_XML_BYTES:
        raise RocksmithXmlError(
            f"Rocksmith arrangement XML is too large ({size} bytes > {MAX_XML_BYTES})."
        )
    if _DECLARATION_RE.search(text):
        raise RocksmithXmlError("Rocksmith arrangement XML must not contain DTD or entity declarations.")
    return text


def _bounded_xml_root(text: str) -> ET.Element:
    """Parse XML while enforcing structural limits before the full tree exists."""

    parser = ET.XMLPullParser(events=("start", "end"))
    root: ET.Element | None = None
    node_count = 0
    depth = 0

    def consume_events() -> None:
        nonlocal root, node_count, depth
        for event, element in parser.read_events():
            if event == "start":
                node_count += 1
                if node_count > MAX_XML_NODES:
                    raise RocksmithXmlError(
                        "Rocksmith arrangement XML has too many nodes "
                        f"({node_count} > {MAX_XML_NODES})."
                    )
                depth += 1
                if depth > MAX_XML_DEPTH:
                    raise RocksmithXmlError(
                        "Rocksmith arrangement XML is too deeply nested "
                        f"({depth} > {MAX_XML_DEPTH})."
                    )
                if root is None:
                    root = element
            else:
                depth -= 1

    try:
        for offset in range(0, len(text), XML_PARSE_CHUNK_CHARS):
            parser.feed(text[offset : offset + XML_PARSE_CHUNK_CHARS])
            consume_events()
        parser.close()
        consume_events()
    except ET.ParseError as exc:
        raise RocksmithXmlError(f"Malformed Rocksmith arrangement XML: {exc}") from exc

    if root is None:
        raise RocksmithXmlError("Malformed Rocksmith arrangement XML: no root element.")
    return root


def _validate_identity(
    *,
    source_path: str,
    expected_song_key: str,
    expected_arrangement: str,
    xml_arrangement: str,
    allowed_xml_arrangements: Iterable[str] | None,
) -> None:
    key_id = _identifier(expected_song_key)
    arrangement_id = _identifier(expected_arrangement)
    if not key_id:
        raise RocksmithXmlError("Expected SongKey is empty.")
    if not arrangement_id:
        raise RocksmithXmlError("Expected arrangement is empty.")
    allowed_xml_ids = {
        _identifier(value)
        for value in (
            allowed_xml_arrangements
            if allowed_xml_arrangements is not None
            else (expected_arrangement,)
        )
        if _identifier(value)
    }
    if _identifier(xml_arrangement) not in allowed_xml_ids:
        expected_xml = ", ".join(sorted(repr(value) for value in allowed_xml_ids))
        raise RocksmithXmlError(
            "Arrangement mismatch: XML arrangement must match one of the "
            f"manifest-authorized kinds ({expected_xml}); XML contains "
            f"{xml_arrangement!r}."
        )

    normalized_path = str(source_path or "").replace("\\", "/")
    stem_id = _identifier(PurePosixPath(normalized_path).stem)
    if not stem_id.startswith(key_id):
        raise RocksmithXmlError(
            f"SongKey mismatch: XML path {source_path!r} does not belong to {expected_song_key!r}."
        )
    suffix = stem_id[len(key_id) :]
    suffix_number = suffix[len(arrangement_id) :] if suffix.startswith(arrangement_id) else ""
    if suffix != arrangement_id and not (suffix.startswith(arrangement_id) and suffix_number.isdigit()):
        raise RocksmithXmlError(
            f"Arrangement/path mismatch: XML path {source_path!r} does not identify {expected_arrangement!r}."
        )


def _identifier(value: str) -> str:
    return "".join(character for character in str(value or "").casefold() if character.isalnum())


def _single_element(root: ET.Element, tag: str, *, required: bool) -> ET.Element | None:
    matches = root.findall(tag)
    if len(matches) > 1:
        raise RocksmithXmlError(f"Rocksmith arrangement XML contains duplicate <{tag}> elements.")
    if not matches:
        if required:
            raise RocksmithXmlError(f"Rocksmith arrangement XML is missing <{tag}>.")
        return None
    return matches[0]


def _single_text(root: ET.Element, tag: str, *, required: bool) -> str:
    element = _single_element(root, tag, required=required)
    if element is None:
        return ""
    value = str(element.text or "").strip()
    if required and not value:
        raise RocksmithXmlError(f"Rocksmith arrangement XML has an empty <{tag}> value.")
    return value


def _single_float(
    root: ET.Element,
    tag: str,
    *,
    default: float | None = None,
    minimum: float | None = None,
) -> float:
    element = _single_element(root, tag, required=default is None)
    if element is None:
        assert default is not None
        return default
    return _finite_float(element.text, f"<{tag}>", minimum=minimum)


def _single_int(root: ET.Element, tag: str, *, default: int | None = None) -> int:
    element = _single_element(root, tag, required=default is None)
    if element is None:
        assert default is not None
        return default
    return _integer(element.text, f"<{tag}>")


def _container_children(
    root: ET.Element,
    container_tag: str,
    child_tag: str,
    *,
    required: bool,
) -> tuple[ET.Element, ...]:
    container = _single_element(root, container_tag, required=required)
    if container is None:
        return ()
    unexpected = [child.tag for child in container if child.tag != child_tag]
    if unexpected:
        raise RocksmithXmlError(
            f"Unexpected <{unexpected[0]}> inside <{container_tag}>; expected only <{child_tag}>."
        )
    return tuple(container)


def _finite_float(value: object, label: str, *, minimum: float | None = None) -> float:
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise RocksmithXmlError(f"Invalid number for {label}: {value!r}.") from exc
    if not math.isfinite(parsed):
        raise RocksmithXmlError(f"Non-finite number for {label}: {value!r}.")
    if minimum is not None and parsed < minimum:
        raise RocksmithXmlError(f"Value for {label} must be at least {minimum}, found {parsed}.")
    return parsed


def _integer(value: object, label: str) -> int:
    text = str(value).strip()
    if not re.fullmatch(r"[+-]?\d+", text):
        raise RocksmithXmlError(f"Invalid integer for {label}: {value!r}.")
    return int(text, 10)


def _float_attr(
    element: ET.Element,
    name: str,
    *,
    default: float | None = None,
    minimum: float | None = None,
) -> float:
    value = element.attrib.get(name)
    if value is None:
        if default is None:
            raise RocksmithXmlError(f"<{element.tag}> is missing required {name!r} attribute.")
        return default
    return _finite_float(value, f"<{element.tag}>.{name}", minimum=minimum)


def _int_attr(element: ET.Element, name: str, *, default: int | None = None) -> int:
    value = element.attrib.get(name)
    if value is None:
        if default is None:
            raise RocksmithXmlError(f"<{element.tag}> is missing required {name!r} attribute.")
        return default
    return _integer(value, f"<{element.tag}>.{name}")


def _flag_attr(element: ET.Element, name: str) -> bool:
    value = str(element.attrib.get(name, "0")).strip().casefold()
    if value in {"", "0", "false", "no"}:
        return False
    if value in {"1", "true", "yes"}:
        return True
    try:
        return _finite_float(value, f"<{element.tag}>.{name}") != 0.0
    except RocksmithXmlError as exc:
        raise RocksmithXmlError(f"Invalid flag for <{element.tag}>.{name}: {value!r}.") from exc


def _parse_tuning(element: ET.Element) -> tuple[int, int, int, int, int, int]:
    tuning = tuple(_int_attr(element, f"string{index}") for index in range(6))
    return tuning  # type: ignore[return-value]


def _parse_beats(
    root: ET.Element, phrase_iterations: tuple[XmlPhraseIteration, ...]
) -> tuple[XmlBeat, ...]:
    elements = _container_children(root, "ebeats", "ebeat", required=True)
    beats: list[XmlBeat] = []
    current_measure = 0
    beat_index = -1
    phrase_index = 0
    last_time = -math.inf
    for element in elements:
        time = _float_attr(element, "time")
        if time < last_time:
            raise RocksmithXmlError("Rocksmith ebeats are not ordered by time.")
        last_time = time
        authored_measure = _int_attr(element, "measure", default=-1)
        if authored_measure >= 0:
            current_measure = authored_measure
            beat_index = 0
        else:
            beat_index += 1
        while (
            phrase_index + 1 < len(phrase_iterations)
            and phrase_iterations[phrase_index + 1].time <= time
        ):
            phrase_index += 1
        beats.append(
            XmlBeat(
                time=time,
                measure=current_measure,
                beat=max(0, beat_index),
                phraseIteration=phrase_index,
            )
        )
    return tuple(beats)


def _parse_phrases(root: ET.Element) -> tuple[XmlPhrase, ...]:
    phrases = []
    for element in _container_children(root, "phrases", "phrase", required=True):
        phrases.append(
            XmlPhrase(
                solo=_int_attr(element, "solo", default=0),
                disparity=_int_attr(element, "disparity", default=0),
                ignore=_int_attr(element, "ignore", default=0),
                maxDifficulty=_int_attr(element, "maxDifficulty", default=0),
                name=str(element.attrib.get("name") or ""),
            )
        )
    return tuple(phrases)


def _parse_phrase_iterations(
    root: ET.Element,
    phrases: tuple[XmlPhrase, ...],
    song_length: float,
) -> tuple[XmlPhraseIteration, ...]:
    elements = _container_children(root, "phraseIterations", "phraseIteration", required=True)
    parsed: list[tuple[int, float, tuple[int, int, int]]] = []
    last_time = -math.inf
    for element in elements:
        phrase_id = _int_attr(element, "phraseId")
        if phrase_id < 0 or phrase_id >= len(phrases):
            raise RocksmithXmlError(
                f"Phrase iteration references invalid phraseId {phrase_id}; only {len(phrases)} phrases exist."
            )
        time = _float_attr(element, "time")
        if time < last_time:
            raise RocksmithXmlError("Rocksmith phrase iterations are not ordered by time.")
        last_time = time
        hero = element.find("heroLevels")
        difficulties = [0, 0, 0]
        if hero is not None:
            unexpected = [child.tag for child in hero if child.tag != "heroLevel"]
            if unexpected:
                raise RocksmithXmlError(f"Unexpected <{unexpected[0]}> inside <heroLevels>.")
            for child in hero:
                hero_index = _int_attr(child, "hero", default=0)
                if 1 <= hero_index <= 3:
                    difficulties[hero_index - 1] = _int_attr(child, "difficulty", default=0)
        parsed.append((phrase_id, time, tuple(difficulties)))

    iterations = []
    for index, (phrase_id, time, difficulties) in enumerate(parsed):
        end_time = parsed[index + 1][1] if index + 1 < len(parsed) else song_length
        if end_time < time:
            raise RocksmithXmlError("Rocksmith phrase iteration has an end before its start.")
        iterations.append(
            XmlPhraseIteration(
                phraseId=phrase_id,
                time=time,
                endTime=end_time,
                difficulty=difficulties,
            )
        )
    return tuple(iterations)


def _parse_chord_templates(root: ET.Element) -> tuple[XmlChordTemplate, ...]:
    templates = []
    for element in _container_children(root, "chordTemplates", "chordTemplate", required=True):
        frets = tuple(_int_attr(element, f"fret{index}") for index in range(6))
        fingers = tuple(_int_attr(element, f"finger{index}", default=-1) for index in range(6))
        arpeggio = _flag_attr(element, "arpeggio") or _flag_attr(element, "arp")
        templates.append(
            XmlChordTemplate(
                mask=CHORD_MASK_ARPEGGIO if arpeggio else 0,
                frets=frets,  # type: ignore[arg-type]
                fingers=fingers,  # type: ignore[arg-type]
                name=str(element.attrib.get("displayName") or element.attrib.get("chordName") or ""),
            )
        )
    return tuple(templates)


def _raw_levels(root: ET.Element) -> tuple[ET.Element, ...]:
    elements = _container_children(root, "levels", "level", required=True)
    difficulties = [_int_attr(element, "difficulty") for element in elements]
    if any(difficulty < 0 for difficulty in difficulties):
        raise RocksmithXmlError("Rocksmith level difficulty cannot be negative.")
    if len(difficulties) != len(set(difficulties)):
        raise RocksmithXmlError("Rocksmith arrangement XML contains duplicate level difficulties.")
    return tuple(sorted(elements, key=lambda element: _int_attr(element, "difficulty")))


def _raw_chords(levels: Iterable[ET.Element]) -> Iterable[_RawChord]:
    for level in levels:
        for element in _level_children(level, "chords", "chord"):
            chord_id = _int_attr(element, "chordId")
            if chord_id < 0:
                raise RocksmithXmlError(f"Chord has invalid negative chordId {chord_id}.")
            yield _RawChord(element=element, raw_chord_id=chord_id)


def _complete_chord_templates(
    master_templates: list[XmlChordTemplate],
    levels: tuple[ET.Element, ...],
    compiled_hints: Mapping[int, RocksmithChordTemplateHint] | None,
) -> tuple[list[XmlChordTemplate], dict[int, int], tuple[int, ...]]:
    """Resolve decompiler-generated chord IDs and report approximated IDs.

    Normal authoring XML indexes ``chordTemplates`` directly.  Some RS1
    compatibility XML instead contains compiled chord IDs (the presence of an
    ID beyond the master-template list identifies that form).  In that form no
    referenced ID, including 0, may be naively treated as a master index.  Each
    ID is rebuilt from its authored child-note samples and then matched to a
    unique master template where possible for its name/fingering metadata.
    Unhinted partial shapes are lower-bound approximations and are reported to
    the caller instead of being represented as exact compiled templates.
    """

    candidates: dict[int, list[tuple[tuple[int, ...], tuple[int, ...]]]] = {}
    referenced: set[int] = set()
    for raw in _raw_chords(levels):
        referenced.add(raw.raw_chord_id)
        shape = _chord_child_shape(raw.element)
        if shape is not None:
            candidates.setdefault(raw.raw_chord_id, []).append(shape)
    for level in levels:
        for element in _level_children(level, "handShapes", "handShape"):
            raw_id = _int_attr(element, "chordId")
            if raw_id < 0:
                raise RocksmithXmlError(f"Handshape has invalid negative chordId {raw_id}.")
            referenced.add(raw_id)

    if not referenced:
        if compiled_hints:
            raise RocksmithXmlError("Compiled chord-template hints were provided for a chart with no chord IDs.")
        return master_templates, {index: index for index in range(len(master_templates))}, ()

    hints = _validated_chord_hints(compiled_hints, referenced)
    compiled_ids = bool(hints) or any(raw_id >= len(master_templates) for raw_id in referenced)
    if not compiled_ids:
        for raw_id, shapes in candidates.items():
            indexed_master = master_templates[raw_id]
            for sample_frets, _sample_fingers in shapes:
                if not _shape_is_subset(sample_frets, indexed_master.frets):
                    raise RocksmithXmlError(
                        f"Chord ID {raw_id} child-note shape conflicts with its "
                        "indexed XML master template."
                    )
        return master_templates, {raw_id: raw_id for raw_id in referenced}, ()

    resolved_templates: list[XmlChordTemplate] = []
    template_map: dict[int, int] = {}
    approximate_ids: list[int] = []
    for raw_id in sorted(referenced):
        shapes = candidates.get(raw_id, [])
        hint = hints.get(raw_id)
        if hint is not None:
            for sample_frets, _sample_fingers in shapes:
                if not _shape_is_subset(sample_frets, hint.frets):
                    raise RocksmithXmlError(
                        f"Compiled chord hint {raw_id} conflicts with an authored child-note sample."
                    )
            template_map[raw_id] = len(resolved_templates)
            hint_mask = _resolved_hint_mask(hint, master_templates)
            resolved_templates.append(
                XmlChordTemplate(
                    mask=hint_mask,
                    frets=hint.frets,
                    fingers=hint.fingers,
                    name=hint.name,
                )
            )
            continue
        if not shapes:
            raise RocksmithXmlError(
                f"Compiled chord ID {raw_id} has no child-note shape; refusing to fabricate it."
            )
        max_strings = max(sum(fret >= 0 for fret in frets) for frets, _fingers in shapes)
        fullest = {
            shape
            for shape in shapes
            if sum(fret >= 0 for fret in shape[0]) == max_strings
        }
        fret_shapes = {shape[0] for shape in fullest}
        if len(fret_shapes) != 1:
            raise RocksmithXmlError(
                f"Chord ID {raw_id} has conflicting equally complete child-note shapes."
            )
        frets = next(iter(fret_shapes))
        for sample_frets, _sample_fingers in shapes:
            if not _shape_is_subset(sample_frets, frets):
                raise RocksmithXmlError(
                    f"Chord ID {raw_id} has conflicting child-note samples."
                )

        finger_shapes = {shape[1] for shape in fullest if shape[0] == frets}
        if len(finger_shapes) != 1:
            raise RocksmithXmlError(
                f"Chord ID {raw_id} has conflicting fingering for its fullest shape."
            )
        fingers = next(iter(finger_shapes))
        master = _unique_master_for_shape(raw_id, frets, master_templates)
        if master is None:
            approximate_ids.append(raw_id)
        if master is not None:
            fingers = tuple(
                master.fingers[index]
                if fret >= 0 and fingers[index] < 0 and master.fingers[index] >= 0
                else fingers[index]
                for index, fret in enumerate(frets)
            )
        template_map[raw_id] = len(resolved_templates)
        resolved_templates.append(
            XmlChordTemplate(
                mask=master.mask if master is not None else 0,
                frets=frets,  # type: ignore[arg-type]
                fingers=fingers,  # type: ignore[arg-type]
                name=master.name if master is not None else "",
            )
        )
    return resolved_templates, template_map, tuple(approximate_ids)


def _shape_is_subset(candidate: tuple[int, ...], fullest: tuple[int, ...]) -> bool:
    return all(fret < 0 or fret == fullest[index] for index, fret in enumerate(candidate))


def _validated_chord_hints(
    hints: Mapping[int, RocksmithChordTemplateHint] | None,
    referenced: set[int],
) -> dict[int, RocksmithChordTemplateHint]:
    if hints is None:
        return {}
    normalized: dict[int, RocksmithChordTemplateHint] = {}
    for raw_id, hint in hints.items():
        if isinstance(raw_id, bool) or not isinstance(raw_id, int) or raw_id < 0:
            raise RocksmithXmlError(f"Compiled chord hint has invalid ChordId {raw_id!r}.")
        if raw_id not in referenced:
            raise RocksmithXmlError(
                f"Compiled chord hint {raw_id} is not referenced by this XML arrangement."
            )
        if not isinstance(hint, RocksmithChordTemplateHint):
            raise RocksmithXmlError(
                f"Compiled chord hint {raw_id} must be a RocksmithChordTemplateHint."
            )
        if len(hint.frets) != 6 or len(hint.fingers) != 6:
            raise RocksmithXmlError(f"Compiled chord hint {raw_id} must contain six frets and fingers.")
        if any(isinstance(value, bool) or not isinstance(value, int) for value in hint.frets):
            raise RocksmithXmlError(f"Compiled chord hint {raw_id} contains a non-integer fret.")
        if any(isinstance(value, bool) or not isinstance(value, int) for value in hint.fingers):
            raise RocksmithXmlError(f"Compiled chord hint {raw_id} contains a non-integer finger.")
        if hint.mask is not None and (
            isinstance(hint.mask, bool) or not isinstance(hint.mask, int) or hint.mask < 0
        ):
            raise RocksmithXmlError(f"Compiled chord hint {raw_id} has an invalid mask.")
        normalized[raw_id] = hint
    return normalized


def _resolved_hint_mask(
    hint: RocksmithChordTemplateHint,
    masters: list[XmlChordTemplate],
) -> int:
    if hint.mask is not None:
        return hint.mask
    normalized_name = _identifier(hint.name)
    if not normalized_name:
        return 0
    matches = [
        master
        for master in masters
        if master.frets == hint.frets and _identifier(master.name) == normalized_name
    ]
    return matches[0].mask if len(matches) == 1 else 0


def _unique_master_for_shape(
    raw_id: int,
    frets: tuple[int, ...],
    masters: list[XmlChordTemplate],
) -> XmlChordTemplate | None:
    # A partial child-note shape cannot tell us whether the compiler intended a
    # full master chord or a reduced dynamic-difficulty template.  Only exact
    # XML-only matches inherit master metadata; manifest hints resolve the rest.
    matches = [master for master in masters if frets == master.frets]
    if len(matches) > 1:
        names = ", ".join(repr(master.name) for master in matches)
        raise RocksmithXmlError(
            f"Compiled chord ID {raw_id} ambiguously matches multiple master templates: {names}."
        )
    return matches[0] if matches else None


def _chord_child_shape(
    element: ET.Element,
) -> tuple[tuple[int, ...], tuple[int, ...]] | None:
    if not len(element):
        return None
    frets = [-1] * 6
    fingers = [-1] * 6
    seen: set[int] = set()
    for child in element:
        if child.tag != "chordNote":
            raise RocksmithXmlError(f"Unexpected <{child.tag}> inside <chord>.")
        string = _int_attr(child, "string")
        if not 0 <= string < 6:
            raise RocksmithXmlError(f"Chord note string must be between 0 and 5, found {string}.")
        if string in seen:
            raise RocksmithXmlError(f"Chord contains duplicate child note for string {string}.")
        seen.add(string)
        fret = _int_attr(child, "fret")
        if fret < 0:
            raise RocksmithXmlError(
                f"Chord note fret cannot be negative, found {fret} on string {string}."
            )
        frets[string] = fret
        fingers[string] = _int_attr(child, "leftHand", default=-1)
    return tuple(frets), tuple(fingers)


def _parse_levels(
    raw_levels: tuple[ET.Element, ...],
    templates: list[XmlChordTemplate],
    template_map: dict[int, int],
    chord_notes: list[XmlChordNote],
    song_length: float,
) -> tuple[XmlLevel, ...]:
    levels = []
    for level in raw_levels:
        notes = [_parse_note(element) for element in _level_children(level, "notes", "note")]
        for raw in (
            _RawChord(element=element, raw_chord_id=_int_attr(element, "chordId"))
            for element in _level_children(level, "chords", "chord")
        ):
            mapped_id = template_map.get(raw.raw_chord_id)
            if mapped_id is None or not 0 <= mapped_id < len(templates):
                raise RocksmithXmlError(f"Chord references unresolved chordId {raw.raw_chord_id}.")
            notes.append(_parse_chord(raw.element, mapped_id, chord_notes))
        notes.sort(key=lambda note: note.time)

        anchors = _parse_anchors(level, song_length)
        normal_shapes: list[XmlFingerPrint] = []
        arpeggio_shapes: list[XmlFingerPrint] = []
        for element in _level_children(level, "handShapes", "handShape"):
            raw_id = _int_attr(element, "chordId")
            mapped_id = template_map.get(raw_id)
            if mapped_id is None:
                raise RocksmithXmlError(f"Handshape references unresolved chordId {raw_id}.")
            shape = XmlFingerPrint(
                chordId=mapped_id,
                startTime=_float_attr(element, "startTime"),
                endTime=_float_attr(element, "endTime"),
            )
            if shape.endTime < shape.startTime:
                raise RocksmithXmlError("Handshape endTime precedes startTime.")
            if templates[mapped_id].mask & CHORD_MASK_ARPEGGIO:
                arpeggio_shapes.append(shape)
            else:
                normal_shapes.append(shape)

        levels.append(
            XmlLevel(
                difficulty=_int_attr(level, "difficulty"),
                anchors=anchors,
                fingerprints=(tuple(normal_shapes), tuple(arpeggio_shapes)),
                notes=tuple(notes),
            )
        )
    return tuple(levels)


def _level_children(level: ET.Element, container_tag: str, child_tag: str) -> tuple[ET.Element, ...]:
    containers = level.findall(container_tag)
    if len(containers) != 1:
        raise RocksmithXmlError(
            f"<level> must contain exactly one <{container_tag}> element, found {len(containers)}."
        )
    unexpected = [child.tag for child in containers[0] if child.tag != child_tag]
    if unexpected:
        raise RocksmithXmlError(
            f"Unexpected <{unexpected[0]}> inside <{container_tag}>; expected only <{child_tag}>."
        )
    return tuple(containers[0])


def _parse_note(element: ET.Element) -> XmlNote:
    string = _int_attr(element, "string")
    if not 0 <= string < 6:
        raise RocksmithXmlError(f"Note string must be between 0 and 5, found {string}.")
    bends = _parse_bends(element)
    return XmlNote(
        mask=_note_mask(element),
        time=_float_attr(element, "time"),
        string=string,
        fret=_int_attr(element, "fret"),
        sustain=_float_attr(element, "sustain", default=0.0, minimum=0.0),
        slideTo=_int_attr(element, "slideTo", default=-1),
        slideUnpitchTo=_int_attr(element, "slideUnpitchTo", default=-1),
        leftHand=_int_attr(element, "leftHand", default=-1),
        tap=_int_attr(element, "tap", default=-1),
        pickDirection=_int_attr(element, "pickDirection", default=0),
        slap=_int_attr(element, "slap", default=-1),
        pluck=_int_attr(element, "pluck", default=-1),
        vibrato=_int_attr(element, "vibrato", default=0),
        bend_time=_float_attr(element, "bend", default=0.0, minimum=0.0),
        bends=bends,
    )


def _parse_chord(
    element: ET.Element,
    mapped_chord_id: int,
    chord_notes: list[XmlChordNote],
) -> XmlNote:
    children = tuple(element)
    chord_note_id = UINT32_NONE
    sustain = 0.0
    if children:
        chord_note_id = len(chord_notes)
        chord_note, sustain = _parse_chord_note(children)
        chord_notes.append(chord_note)
    return XmlNote(
        mask=_note_mask(element, chord=True),
        time=_float_attr(element, "time"),
        string=-1,
        fret=-1,
        chordId=mapped_chord_id,
        chordNoteId=chord_note_id,
        sustain=sustain,
    )


def _parse_chord_note(children: tuple[ET.Element, ...]) -> tuple[XmlChordNote, float]:
    masks = [0] * 6
    bend_sets = [XmlBendSet() for _index in range(6)]
    slide_to = [-1] * 6
    slide_unpitched_to = [-1] * 6
    vibrato = [0] * 6
    seen: set[int] = set()
    sustain = 0.0
    for element in children:
        if element.tag != "chordNote":
            raise RocksmithXmlError(f"Unexpected <{element.tag}> inside <chord>.")
        string = _int_attr(element, "string")
        if not 0 <= string < 6:
            raise RocksmithXmlError(f"Chord note string must be between 0 and 5, found {string}.")
        if string in seen:
            raise RocksmithXmlError(f"Chord contains duplicate child note for string {string}.")
        seen.add(string)
        masks[string] = _note_mask(element)
        bend_sets[string] = XmlBendSet(_parse_bends(element))
        slide_to[string] = _int_attr(element, "slideTo", default=-1)
        slide_unpitched_to[string] = _int_attr(element, "slideUnpitchTo", default=-1)
        vibrato[string] = _int_attr(element, "vibrato", default=0)
        sustain = max(sustain, _float_attr(element, "sustain", default=0.0, minimum=0.0))
    return (
        XmlChordNote(
            mask=tuple(masks),  # type: ignore[arg-type]
            bends=tuple(bend_sets),  # type: ignore[arg-type]
            slideTo=tuple(slide_to),  # type: ignore[arg-type]
            slideUnpitchTo=tuple(slide_unpitched_to),  # type: ignore[arg-type]
            vibrato=tuple(vibrato),  # type: ignore[arg-type]
        ),
        sustain,
    )


def _parse_bends(element: ET.Element) -> tuple[XmlBend, ...]:
    containers = element.findall("bendValues")
    if len(containers) > 1:
        raise RocksmithXmlError(f"<{element.tag}> contains duplicate <bendValues> elements.")
    if not containers:
        unexpected = [child.tag for child in element]
        if unexpected:
            raise RocksmithXmlError(f"Unexpected <{unexpected[0]}> inside <{element.tag}>.")
        return ()
    unexpected = [child.tag for child in element if child.tag != "bendValues"]
    if unexpected:
        raise RocksmithXmlError(f"Unexpected <{unexpected[0]}> inside <{element.tag}>.")
    container = containers[0]
    unexpected = [child.tag for child in container if child.tag != "bendValue"]
    if unexpected:
        raise RocksmithXmlError(f"Unexpected <{unexpected[0]}> inside <bendValues>.")
    bends = tuple(
        XmlBend(
            time=_float_attr(child, "time"),
            step=_float_attr(child, "step"),
            UNK=_int_attr(child, "unk5", default=0),
        )
        for child in container
    )
    return tuple(sorted(bends, key=lambda bend: bend.time))


def _note_mask(element: ET.Element, *, chord: bool = False) -> int:
    mask = 0
    flags = (
        ("fretHandMute", NOTE_MASK_FRETHANDMUTE),
        ("tremolo", NOTE_MASK_TREMOLO),
        ("harmonic", NOTE_MASK_HARMONIC),
        ("palmMute", NOTE_MASK_PALMMUTE),
        ("hammerOn", NOTE_MASK_HAMMERON),
        ("pullOff", NOTE_MASK_PULLOFF),
        ("tap", NOTE_MASK_TAP),
        ("harmonicPinch", NOTE_MASK_PINCHHARMONIC),
        ("vibrato", NOTE_MASK_VIBRATO),
        ("mute", NOTE_MASK_MUTE),
        ("ignore", NOTE_MASK_IGNORE),
        ("accent", NOTE_MASK_ACCENT),
        ("linkNext", NOTE_MASK_PARENT),
    )
    for name, bit in flags:
        if _flag_attr(element, name):
            mask |= bit
    if _float_attr(element, "sustain", default=0.0, minimum=0.0) > 0:
        mask |= NOTE_MASK_SUSTAIN
    if _int_attr(element, "slap", default=-1) >= 0:
        mask |= NOTE_MASK_SLAP
    if _int_attr(element, "pluck", default=-1) >= 0:
        mask |= NOTE_MASK_PLUCK
    if chord and _flag_attr(element, "highDensity"):
        mask |= NOTE_MASK_HIGHDENSITY
    return mask


def _parse_anchors(level: ET.Element, song_length: float) -> tuple[XmlAnchor, ...]:
    elements = _level_children(level, "anchors", "anchor")
    raw = [
        (
            _float_attr(element, "time"),
            _int_attr(element, "fret"),
            _int_attr(element, "width", default=4),
        )
        for element in elements
    ]
    anchors = []
    for index, (time, fret, width) in enumerate(raw):
        end_time = raw[index + 1][0] if index + 1 < len(raw) else song_length
        anchors.append(XmlAnchor(time=time, endTime=end_time, fret=fret, width=width))
    return tuple(anchors)


def _parse_sections(
    root: ET.Element,
    iterations: tuple[XmlPhraseIteration, ...],
    song_length: float,
) -> tuple[XmlSection, ...]:
    elements = _container_children(root, "sections", "section", required=False)
    raw = [
        (
            str(element.attrib.get("name") or "section"),
            _int_attr(element, "number", default=0),
            _float_attr(element, "startTime"),
        )
        for element in elements
    ]
    sections = []
    for index, (name, number, start) in enumerate(raw):
        end = raw[index + 1][2] if index + 1 < len(raw) else song_length
        if end < start:
            raise RocksmithXmlError("Rocksmith sections are not ordered by startTime.")
        start_phrase = _iteration_at(iterations, start)
        end_phrase = _iteration_at(iterations, end)
        sections.append(
            XmlSection(
                name=name,
                number=number,
                startTime=start,
                endTime=end,
                startPhraseIterationId=start_phrase,
                endPhraseIterationId=end_phrase,
            )
        )
    return tuple(sections)


def _iteration_at(iterations: tuple[XmlPhraseIteration, ...], time: float) -> int:
    index = 0
    for candidate, iteration in enumerate(iterations):
        if iteration.time > time:
            break
        index = candidate
    return index


def _parse_tones(
    root: ET.Element, tone_slots: tuple[str, str, str, str]
) -> tuple[XmlTone, ...]:
    elements = _container_children(root, "tones", "tone", required=False)
    slot_ids: dict[str, list[int]] = {}
    for tone_id, name in enumerate(tone_slots):
        normalized = _identifier(name)
        if normalized:
            slot_ids.setdefault(normalized, []).append(tone_id)

    explicit_ids = {
        _int_attr(element, "id")
        for element in elements
        if "id" in element.attrib
    }
    if any(tone_id < 0 for tone_id in explicit_ids):
        raise RocksmithXmlError("Tone event IDs cannot be negative.")
    used_ids = set(explicit_ids)
    used_ids.update(tone_id for matches in slot_ids.values() for tone_id in matches)
    assigned_names: dict[str, int] = {}
    tones = []
    for element in elements:
        name = str(element.attrib.get("name") or "").strip()
        normalized_name = _identifier(name)
        if "id" in element.attrib:
            tone_id = _int_attr(element, "id")
            matching_slots = slot_ids.get(normalized_name, []) if normalized_name else []
            if matching_slots and tone_id not in matching_slots:
                raise RocksmithXmlError(
                    f"Tone event {name!r} uses id {tone_id}, but its declared tone slot is {matching_slots[0]}."
                )
        else:
            if not normalized_name:
                raise RocksmithXmlError("Tone event must contain either an id or a non-empty name.")
            matching_slots = slot_ids.get(normalized_name, [])
            if len(matching_slots) > 1:
                raise RocksmithXmlError(
                    f"Tone event {name!r} ambiguously matches multiple declared tone slots."
                )
            if matching_slots:
                tone_id = matching_slots[0]
            elif normalized_name in assigned_names:
                tone_id = assigned_names[normalized_name]
            else:
                tone_id = 0
                while tone_id in used_ids:
                    tone_id += 1
                assigned_names[normalized_name] = tone_id
                used_ids.add(tone_id)
        tones.append(
            XmlTone(
                time=_float_attr(element, "time"),
                id=tone_id,
                name=name,
            )
        )
    return tuple(sorted(tones, key=lambda tone: tone.time))

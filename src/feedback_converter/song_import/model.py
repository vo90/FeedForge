"""Small source-neutral score model. Musical positions are quarter-note fractions."""

from dataclasses import dataclass, field
from fractions import Fraction
import math
from typing import Any


class ScoreImportError(ValueError):
    """Input cannot be imported without losing musical information."""


def rational(value: Any, label: str = "musical value") -> Fraction:
    try:
        if isinstance(value, bool):
            raise ValueError()
        if isinstance(value, (tuple, list)) and len(value) == 2:
            result = Fraction(str(value[0])) / Fraction(str(value[1]))
        else:
            result = Fraction(str(value))
        if abs(result) > 1_000_000 or not math.isfinite(float(result)):
            raise ValueError()
        return result
    except (ValueError, TypeError, ZeroDivisionError, OverflowError) as exc:
        raise ScoreImportError(f"Invalid {label}: {value!r}.") from exc


def integer(value: Any, label: str) -> int:
    result = rational(value, label)
    if result.denominator != 1:
        raise ScoreImportError(f"{label} must be a whole number.")
    return int(result)


@dataclass
class Note:
    position: Fraction
    duration: Fraction
    string: int
    fret: int
    tie: bool = False
    effects: dict = field(default_factory=dict)
    bends: list[tuple[Fraction, float]] = field(default_factory=list)
    hopo: bool = False
    slide: str | None = None
    source_id: str = ""
    beat_id: str = ""
    voice_id: str = ""
    slide_in: str | None = None  # Pitch motion into this authored segment.


@dataclass
class WrittenBeat:
    """A source beat before ties/repeats are folded for the playable chart."""
    source_id: str
    position: Fraction
    duration: Fraction
    notes: list[Note] = field(default_factory=list)
    rest: bool = False
    denominator: int | None = None
    dots: int = 0
    tuplet: tuple[int, int] | None = None
    grace: str = ""
    annotations: dict = field(default_factory=dict)
    written_duration: Fraction | None = None
    written_position: Fraction | None = None
    chord_label: str = ""


@dataclass
class WrittenVoice:
    source_id: str
    beats: list[WrittenBeat] = field(default_factory=list)
    source_index: int | None = None


@dataclass
class Measure:
    numerator: int = 4
    denominator: int = 4
    length: Fraction = Fraction(4)
    repeat_start: bool = False
    repeat_count: int = 0  # A repeat CLOSE; total number of passes, not extra passes.
    endings: frozenset[int] = frozenset()
    section: str = ""
    tempos: list[tuple[Fraction, float]] = field(default_factory=list)


@dataclass
class Track:
    id: str
    name: str
    instrument: str
    tuning: list[int]  # Physical low-to-high string order, NEVER sorted by pitch.
    bars: list[list[Note]]
    capo: int = 0
    role: str = ""
    written_bars: list[list[WrittenVoice]] = field(default_factory=list)
    clefs: list[str | None] = field(default_factory=list)


@dataclass
class Score:
    title: str
    artist: str
    measures: list[Measure]
    tracks: list[Track]
    album: str = ""
    year: str | int = ""
    source: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    source_document: dict = field(default_factory=dict)
    feature_inventory: list[dict] = field(default_factory=list)


def validate_score(score: Score) -> None:
    if not score.measures or len(score.measures) > 20_000:
        raise ScoreImportError("Score has no measures or exceeds the import limit.")
    if not score.tracks:
        raise ScoreImportError("The tab contains no playable guitar or bass arrangements.")
    ids: set[str] = set()
    count = 0
    for bar in score.measures:
        if not 0 < bar.length <= 128 or bar.numerator < 1 or bar.denominator < 1:
            raise ScoreImportError("Invalid measure length or time signature.")
        if bar.repeat_count and not 2 <= bar.repeat_count <= 32:
            raise ScoreImportError("Repeat count must be between 2 and 32 total passes.")
        if any(not 1 <= ending <= 32 for ending in bar.endings):
            raise ScoreImportError("Invalid alternate ending number.")
        if any(p < 0 or p >= bar.length or not math.isfinite(bpm) or bpm <= 0 or bpm > 1000
               for p, bpm in bar.tempos):
            raise ScoreImportError("Invalid tempo or tempo position.")
    for track in score.tracks:
        if track.id in ids:
            raise ScoreImportError("Duplicate track identifier.")
        ids.add(track.id)
        if not 1 <= len(track.tuning) <= 12 or any(not 0 <= p <= 127 for p in track.tuning):
            raise ScoreImportError(f"Invalid tuning in {track.name}.")
        if not 0 <= track.capo <= 24:
            raise ScoreImportError(f"Invalid capo in {track.name}.")
        if len(track.bars) != len(score.measures):
            raise ScoreImportError(f"Incomplete measures in {track.name}.")
        for bi, notes in enumerate(track.bars):
            for note in notes:
                count += 1
                leading_grace = (note.effects.get("__leading_grace") is True and bi > 0
                                 and -score.measures[bi - 1].length <= note.position < 0
                                 and note.position + note.duration <= 0)
                if (note.position < 0 and not leading_grace or note.duration <= 0
                        or note.position + note.duration > score.measures[bi].length
                        or not 0 <= note.string < len(track.tuning) or not 0 <= note.fret <= 48):
                    raise ScoreImportError(f"Invalid note in {track.name}, measure {bi + 1}.")
                if any(p < 0 or p > 1 or not math.isfinite(v) for p, v in note.bends):
                    raise ScoreImportError("Invalid bend curve.")
    if count > 500_000:
        raise ScoreImportError("Score exceeds the note import limit.")

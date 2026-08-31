from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from feedback_converter.feedpak_validator import validate_feedpak


def _arrangement() -> dict[str, Any]:
    return {
        "name": "Lead",
        "tuning": [0] * 6,
        "notes": [
            {
                "t": 0.25,
                "s": 3,
                "f": 3,
                "sus": 0.5,
                "bnv": [{"t": 0.0, "v": 0.0}, {"t": 0.25, "v": 1.0}],
                "future_note_key": True,
            },
            {"t": 1.0, "s": 0, "f": 5},
        ],
        "chords": [
            {"t": 1.25, "id": 0, "notes": [{"s": 3, "f": 2, "sus": 0.25}]},
        ],
        "anchors": [{"time": 0.0, "fret": 1, "width": 4}],
        "handshapes": [{"chord_id": 0, "start_time": 1.0, "end_time": 1.75}],
        "templates": [{"name": "D", "frets": [-1, -1, 0, 2], "future_template_key": 1}],
        "phrases": [
            {
                "start_time": 0.0,
                "end_time": 2.0,
                "max_difficulty": 1,
                "levels": [
                    {
                        "difficulty": 0,
                        "notes": [{"t": 0.5, "s": 0, "f": 3}],
                        "chords": [],
                        "anchors": [],
                        "handshapes": [],
                    },
                    {
                        "difficulty": 1,
                        "notes": [{"t": 0.75, "s": 1, "f": 5}],
                        "chords": [],
                        "anchors": [],
                        "handshapes": [
                            {"chord_id": 0, "start_time": 1.0, "end_time": 2.0},
                        ],
                        "future_level_key": {"kept": True},
                    },
                ],
                "future_phrase_key": "kept",
            },
        ],
        "tempos": [{"time": 0.0, "bpm": 120.0}],
        "beats": [
            {"time": 0.0, "measure": 1},
            {"time": 0.5, "measure": -1},
            {"time": 1.0, "measure": 2},
        ],
        "sections": [{"name": "intro", "time": 0.0}],
        "future_arrangement_key": [1, 2, 3],
    }


def _write_pack(
    tmp_path: Path,
    arrangement: dict[str, Any] | None = None,
    *,
    duration: float = 4.0,
    timeline: dict[str, Any] | None = None,
) -> Path:
    root = tmp_path / "semantic.feedpak"
    (root / "arrangements").mkdir(parents=True)
    (root / "stems").mkdir()
    (root / "arrangements" / "lead.json").write_text(
        json.dumps(arrangement or _arrangement()), encoding="utf-8"
    )
    (root / "stems" / "full.ogg").write_bytes(b"")
    manifest: dict[str, Any] = {
        "feedpak_version": "1.19.0",
        "title": "Semantic fixture",
        "artist": "FeedForge",
        "duration": duration,
        "arrangements": [
            {
                "id": "lead",
                "file": "arrangements/lead.json",
                # Manifest tuning is authoritative over the six-string fallback
                # embedded in the arrangement.
                "tuning": [0, 0, 0, 0],
            },
        ],
        "stems": [{"id": "full", "file": "stems/full.ogg"}],
        "future_manifest_key": {"kept": True},
    }
    if timeline is not None:
        (root / "song_timeline.json").write_text(json.dumps(timeline), encoding="utf-8")
        manifest["song_timeline"] = "song_timeline.json"
    (root / "manifest.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    return root


def _set(data: dict[str, Any], path: tuple[str | int, ...], value: Any) -> None:
    target: Any = data
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value


def test_semantic_validator_accepts_valid_data_defaults_and_extensions(tmp_path: Path):
    arrangement = _arrangement()
    arrangement["phrases"][0].pop("max_difficulty")
    arrangement["phrases"][0]["levels"] = [
        {"future_level_key": "kept", "phrases": []},
    ]

    result = validate_feedpak(_write_pack(tmp_path, arrangement))

    assert result.ok, result.errors


@pytest.mark.parametrize(
    ("path", "value", "expected"),
    [
        (("notes", 0, "t"), float("nan"), "notes/0/t: must be finite and >= 0"),
        (("notes", 0, "sus"), -0.25, "notes/0/sus: must be finite and >= 0"),
        (("notes", 0, "s"), 4, "outside the effective 0..3 range"),
        (("chords", 0, "id"), 1, "template index 1 is outside 0..0"),
        (("chords", 0, "notes", 0, "s"), 4, "outside the effective 0..3 range"),
        (("handshapes", 0, "chord_id"), -1, "template index -1 is outside 0..0"),
        (("anchors", 0, "fret"), -1, "anchors/0/fret: must be >= 0"),
        (("anchors", 0, "width"), 0, "anchors/0/width: must be > 0"),
        (
            ("notes", 0, "bnv"),
            [{"t": 0.25, "v": 1.0}, {"t": 0.0, "v": 0.0}],
            "point times must be in non-descending order",
        ),
    ],
)
def test_semantic_validator_rejects_invalid_note_chord_and_anchor_data(
    tmp_path: Path,
    path: tuple[str | int, ...],
    value: Any,
    expected: str,
):
    arrangement = _arrangement()
    _set(arrangement, path, value)

    result = validate_feedpak(_write_pack(tmp_path, arrangement))

    assert not result.ok
    assert any(expected in message for message in result.errors)


def test_semantic_validator_rejects_out_of_order_arrangement_events(tmp_path: Path):
    arrangement = _arrangement()
    arrangement["notes"][0]["t"] = 1.5

    result = validate_feedpak(_write_pack(tmp_path, arrangement))

    assert not result.ok
    assert any("notes: t values must be in non-descending order" in message for message in result.errors)


def test_semantic_validator_checks_phrase_windows_levels_and_containment(tmp_path: Path):
    arrangement = _arrangement()
    phrase = arrangement["phrases"][0]
    phrase["end_time"] = phrase["start_time"]
    phrase["levels"][1]["difficulty"] = 0
    phrase["levels"][0]["notes"][0]["t"] = 3.0

    result = validate_feedpak(_write_pack(tmp_path, arrangement))

    assert not result.ok
    assert any("end_time must be > start_time" in message for message in result.errors)
    assert any("difficulties must be in strictly increasing order" in message for message in result.errors)


def test_semantic_validator_checks_phrase_level_event_shapes(tmp_path: Path):
    arrangement = _arrangement()
    arrangement["phrases"][0]["levels"][0]["notes"] = [{"s": 0, "f": 3}]

    result = validate_feedpak(_write_pack(tmp_path, arrangement))

    assert not result.ok
    assert any("phrases/0/levels/0" in message and "'t' is a required property" in message for message in result.errors)


def test_semantic_validator_rejects_phrase_events_outside_the_window(tmp_path: Path):
    arrangement = _arrangement()
    arrangement["phrases"][0]["levels"][0]["notes"][0]["t"] = 3.0

    result = validate_feedpak(_write_pack(tmp_path, arrangement))

    assert not result.ok
    assert any("event time must be inside phrase window" in message for message in result.errors)


def test_semantic_validator_checks_beat_chronology_and_downbeat_progression(tmp_path: Path):
    arrangement = _arrangement()
    arrangement["beats"][1]["time"] = 0.0
    arrangement["beats"][2]["measure"] = 3

    result = validate_feedpak(_write_pack(tmp_path, arrangement))

    assert not result.ok
    assert any("time values must be in strictly increasing order" in message for message in result.errors)
    assert any("expected downbeat 2, got 3" in message for message in result.errors)


def test_semantic_validator_checks_song_timeline_beats(tmp_path: Path):
    timeline = {
        "version": 1,
        "beats": [{"time": 0.5, "measure": 2}],
        "future_timeline_key": True,
    }

    result = validate_feedpak(_write_pack(tmp_path, timeline=timeline))

    assert not result.ok
    assert any("song_timeline.json: beats/0/measure: expected downbeat 1, got 2" in message for message in result.errors)


def test_semantic_validator_rejects_non_finite_manifest_duration(tmp_path: Path):
    result = validate_feedpak(_write_pack(tmp_path, duration=float("inf")))

    assert not result.ok
    assert any("manifest.yaml: duration: must be finite and >= 0" in message for message in result.errors)

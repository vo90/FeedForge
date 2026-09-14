from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from feedback_converter.feedpak_validator import (
    FeedpakValidationResult,
    require_publishable_feedpak,
    validate_feedpak,
)


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


@pytest.mark.parametrize(
    ("path", "value", "expected"),
    [
        (("notes", 0, "f"), -1, "notes/0/f: must be between 0 and 24"),
        (("chords", 0, "notes", 0, "f"), 25, "notes/0/f: must be between 0 and 24"),
        (("notes", 0, "sl"), -2, "notes/0/sl: must be between -1 and 24"),
        (("chords", 0, "notes", 0, "slu"), 25, "notes/0/slu: must be between -1 and 24"),
        (("notes", 0, "bn"), -0.5, "notes/0/bn: must be finite and >= 0"),
        (("notes", 0, "bn"), float("inf"), "notes/0/bn: must be finite and >= 0"),
        (("chords", 0, "notes", 0, "bn"), "1", "notes/0/bn: must be a number"),
        (("notes", 0, "ho"), 1, "notes/0/ho: must be a boolean"),
        (("chords", 0, "notes", 0, "pm"), "true", "notes/0/pm: must be a boolean"),
        (("notes", 0, "rh"), -2, "notes/0/rh: must be >= -1"),
        (("chords", 0, "notes", 0, "rh"), "0", "notes/0/rh: must be an integer"),
        (("notes", 0, "pkd"), 2, "notes/0/pkd: must be one of -1, 0, 1"),
    ],
)
def test_semantic_validator_checks_normative_note_fields(
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


@pytest.mark.parametrize(
    "field",
    [
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
    ],
)
def test_semantic_validator_requires_boolean_technique_fields(tmp_path: Path, field: str):
    arrangement = _arrangement()
    arrangement["notes"][0][field] = 1

    result = validate_feedpak(_write_pack(tmp_path, arrangement))

    assert not result.ok
    assert any(f"notes/0/{field}: must be a boolean" in message for message in result.errors)


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("frets", [-1, 0, 2], "frets: length must match effective string count 4"),
        ("fingers", [-1, 0, 2], "fingers: length must match effective string count 4"),
        ("frets", [-2, 0, 2, 3], "frets/0: must be between -1 and 24"),
        ("frets", [-1, 0, 2, 25], "frets/3: must be between -1 and 24"),
        ("fingers", [-2, 0, 2, 3], "fingers/0: must be between -1 and 4"),
        ("fingers", [-1, 0, 2, 5], "fingers/3: must be between -1 and 4"),
    ],
)
def test_semantic_validator_checks_template_string_shape(
    tmp_path: Path,
    field: str,
    value: list[int],
    expected: str,
):
    arrangement = _arrangement()
    arrangement["templates"][0][field] = value

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
    assert not result.publishable


@pytest.mark.parametrize(
    ("mutate", "expected_code"),
    [
        (
            lambda arrangement: arrangement["notes"][0].update(f=127),
            "chart.fret.out-of-range",
        ),
        (
            lambda arrangement: arrangement["templates"][0]["frets"].__setitem__(3, 26),
            "chart.template-fret.out-of-range",
        ),
        (
            lambda arrangement: arrangement["notes"][0].update(slu=28),
            "chart.slide-destination.out-of-range",
        ),
        (
            lambda arrangement: arrangement["chords"][0]["notes"][0].update(f=127),
            "chart.fret.out-of-range",
        ),
        (
            lambda arrangement: arrangement["chords"][0]["notes"][0].update(slu=28),
            "chart.slide-destination.out-of-range",
        ),
        (
            lambda arrangement: arrangement["handshapes"][0].update(
                start_time=1.75,
                end_time=1.0,
            ),
            "chart.handshape.reversed-span",
        ),
    ],
)
def test_reviewed_chart_findings_are_strict_errors_but_publishable(
    tmp_path: Path,
    mutate: Any,
    expected_code: str,
):
    arrangement = _arrangement()
    mutate(arrangement)

    package = _write_pack(tmp_path, arrangement)
    result = validate_feedpak(package)

    assert not result.ok
    assert result.publishable
    assert result.blocking_errors == []
    assert result.nonblocking_errors == result.errors
    assert any(
        issue.code == expected_code
        and issue.category == "chart-semantic"
        and issue.severity == "error"
        and not issue.blocking
        for issue in result.issues
    )
    assert require_publishable_feedpak(package).publishable


@pytest.mark.parametrize(
    ("collection", "time_field", "expected_code"),
    [
        ("phrases", "start_time", "chart.timeline.phrase-order"),
        ("sections", "time", "chart.timeline.section-order"),
    ],
)
def test_reviewed_phrase_and_section_order_findings_are_publishable(
    tmp_path: Path,
    collection: str,
    time_field: str,
    expected_code: str,
):
    arrangement = _arrangement()
    if collection == "phrases":
        first = {"start_time": 1.0, "end_time": 2.0, "levels": []}
        second = {"start_time": 0.0, "end_time": 1.0, "levels": []}
    else:
        first = {"name": "verse", time_field: 1.0}
        second = {"name": "intro", time_field: 0.0}
    arrangement[collection] = [first, second]

    result = validate_feedpak(_write_pack(tmp_path, arrangement))

    assert not result.ok
    assert result.publishable
    assert {issue.code for issue in result.issues if not issue.blocking} == {expected_code}


def test_reviewed_beat_findings_are_publishable_with_stable_codes(tmp_path: Path):
    arrangement = _arrangement()
    arrangement["beats"][1]["time"] = 0.0
    arrangement["beats"][2]["measure"] = 3

    result = validate_feedpak(_write_pack(tmp_path, arrangement))

    assert not result.ok
    assert result.publishable
    assert {issue.code for issue in result.issues if not issue.blocking} == {
        "chart.timeline.beat-order",
        "chart.timeline.downbeat-progression",
    }


def test_shared_timeline_order_and_downbeat_findings_are_publishable(tmp_path: Path):
    timeline = {
        "version": 1,
        "beats": [
            {"time": 1.0, "measure": 2},
            {"time": 0.5, "measure": 1},
        ],
        "sections": [
            {"name": "verse", "time": 2.0},
            {"name": "intro", "time": 0.0},
        ],
    }

    result = validate_feedpak(_write_pack(tmp_path, timeline=timeline))

    assert not result.ok
    assert result.publishable
    assert {issue.code for issue in result.issues if not issue.blocking} == {
        "chart.timeline.beat-order",
        "chart.timeline.downbeat-progression",
        "chart.timeline.section-order",
    }


def test_unreviewed_chart_semantic_finding_fails_closed(tmp_path: Path):
    arrangement = _arrangement()
    arrangement["notes"][0]["s"] = 4

    result = validate_feedpak(_write_pack(tmp_path, arrangement))

    assert not result.ok
    assert not result.publishable
    assert result.blocking_errors == result.errors
    assert any(
        issue.code == "chart.semantic.unclassified"
        and issue.category == "chart-semantic"
        and issue.blocking
        for issue in result.issues
    )


def test_blocking_finding_cannot_be_hidden_by_publishable_chart_finding(tmp_path: Path):
    arrangement = _arrangement()
    arrangement["notes"][0].update(f=127, s=4)

    result = validate_feedpak(_write_pack(tmp_path, arrangement))

    assert not result.publishable
    assert len(result.nonblocking_errors) == 1
    assert any("/f: must be between 0 and 24" in item for item in result.nonblocking_errors)
    assert len(result.blocking_errors) == 1
    assert "outside the effective 0..3 range" in result.blocking_errors[0]


def test_schema_finding_remains_blocking(tmp_path: Path):
    arrangement = _arrangement()
    del arrangement["notes"][0]["t"]

    result = validate_feedpak(_write_pack(tmp_path, arrangement))

    assert not result.ok
    assert not result.publishable
    assert result.blocking_errors == result.errors
    assert all(issue.blocking for issue in result.issues if issue.severity == "error")


def test_validation_result_round_trip_preserves_classification(tmp_path: Path):
    arrangement = _arrangement()
    arrangement["notes"][0]["f"] = 127
    result = validate_feedpak(_write_pack(tmp_path, arrangement))

    reconstructed = FeedpakValidationResult.from_dict(result.to_dict())

    assert reconstructed == result
    assert reconstructed.publishable


def test_legacy_unclassified_result_fails_closed():
    result = FeedpakValidationResult(ok=False, errors=["legacy validation failure"])

    assert not result.publishable
    assert result.blocking_errors == ["legacy validation failure"]
    assert result.nonblocking_errors == []


def test_contradictory_failed_result_without_details_fails_closed():
    result = FeedpakValidationResult(ok=False)

    assert not result.publishable

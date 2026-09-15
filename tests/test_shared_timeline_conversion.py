import json
from types import SimpleNamespace

import pytest
import yaml

from feedback_converter import converter


def _ns(**kwargs):
    return SimpleNamespace(**kwargs)


def _song(
    *,
    second_downbeat: float = 2.0,
    section_time: float = 0.0,
    include_second_downbeat: bool = True,
):
    level = _ns(difficulty=0, notes=[], anchors=[], fingerprints=[[], []])
    beats = [
        _ns(time=0.0, measure=1, beat=0),
        _ns(time=1.0, measure=1, beat=1),
    ]
    if include_second_downbeat:
        beats.append(_ns(time=second_downbeat, measure=2, beat=0))
    return _ns(
        metadata=_ns(
            tuning=[0, 0, 0, 0, 0, 0],
            capo=0,
            songLength=5.0,
        ),
        chordTemplates=[],
        chordNotes=[],
        levels=[level],
        phraseIterations=[],
        phrases=[],
        beats=beats,
        sections=[_ns(name="intro", number=1, startTime=section_time)],
        tones=[],
        vocals=[],
    )


def _convert(tmp_path, monkeypatch, songs):
    class FakeSong:
        @staticmethod
        def parse(data):
            return songs[data.decode()]

    monkeypatch.setattr(converter, "Song", FakeSong)
    input_path = tmp_path / "song.psarc"
    input_path.write_bytes(b"fixture")
    output = tmp_path / "song.feedpak.work"
    content = {
        "songs/bin/generic/song_lead.sng": b"lead",
        "songs/bin/generic/song_rhythm.sng": b"rhythm",
        "audio/windows/song.ogg": b"OggS-test-audio",
    }
    result = converter.convert_psarc(
        input_path,
        output,
        archive=False,
        _content=content,
    )
    manifest = yaml.safe_load((output / "manifest.yaml").read_text(encoding="utf-8"))
    arrangements = [
        json.loads((output / entry["file"]).read_text(encoding="utf-8"))
        for entry in manifest["arrangements"]
    ]
    return output, result, manifest, arrangements


def test_matching_arrangement_timelines_are_hoisted(tmp_path, monkeypatch):
    output, result, manifest, arrangements = _convert(
        tmp_path,
        monkeypatch,
        {"lead": _song(), "rhythm": _song()},
    )

    assert manifest["song_timeline"] == "song_timeline.json"
    shared = json.loads(
        (output / manifest["song_timeline"]).read_text(encoding="utf-8")
    )
    assert all(arrangement["beats"] == shared["beats"] for arrangement in arrangements)
    assert all(
        arrangement["sections"] == shared["sections"]
        for arrangement in arrangements
    )
    assert not any("Arrangement beat maps differ" in warning.message for warning in result.warnings)


def test_section_only_differences_remain_embedded_without_a_warning(
    tmp_path, monkeypatch
):
    output, result, manifest, arrangements = _convert(
        tmp_path,
        monkeypatch,
        {"lead": _song(), "rhythm": _song(section_time=0.25)},
    )

    assert "song_timeline" not in manifest
    assert not (output / "song_timeline.json").exists()
    assert arrangements[0]["beats"] == arrangements[1]["beats"]
    assert arrangements[0]["sections"] != arrangements[1]["sections"]
    assert not any("Arrangement beat maps differ" in warning.message for warning in result.warnings)


@pytest.mark.parametrize(
    ("rhythm", "expected_detail"),
    [
        (_song(second_downbeat=2.25), "largest aligned timestamp difference 0.25s"),
        (_song(include_second_downbeat=False), "beat counts range from 2 to 3"),
    ],
    ids=["timestamp", "count"],
)
def test_beat_map_differences_are_diagnostic_details_and_remain_embedded(
    tmp_path, monkeypatch, rhythm, expected_detail
):
    output, result, manifest, arrangements = _convert(
        tmp_path,
        monkeypatch,
        {"lead": _song(), "rhythm": rhythm},
    )

    assert "song_timeline" not in manifest
    assert not (output / "song_timeline.json").exists()
    assert arrangements[0]["beats"] != arrangements[1]["beats"]
    detail = next(
        detail
        for detail in result.conversion_details
        if "Arrangement beat maps differ" in detail.message
    )
    assert detail.category == "source-compatibility"
    assert expected_detail in detail.message
    assert "All arrangement timing was preserved" in detail.message
    assert "FeedBack will use Lead as the global beat grid" in detail.message
    assert "song_lead.sng" not in detail.message
    assert "song_rhythm.sng" not in detail.message
    assert not any(
        "Arrangement beat maps differ" in warning.message
        for warning in result.warnings
    )

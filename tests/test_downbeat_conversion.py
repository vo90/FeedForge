from types import SimpleNamespace

import pytest

from feedback_converter.converter import (
    _beat_to_feedpak,
    _karaoke_arrangement,
    _song_to_arrangement,
    _song_to_timeline,
)


def _beat(*, time: float, measure: int, beat: int) -> SimpleNamespace:
    return SimpleNamespace(time=time, measure=measure, beat=beat)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (_beat(time=0.0, measure=0, beat=0), {"time": 0.0, "measure": -1}),
        (_beat(time=0.5, measure=1, beat=0), {"time": 0.5, "measure": 1}),
        (_beat(time=1.0, measure=1, beat=1), {"time": 1.0, "measure": -1}),
        (_beat(time=1.5, measure=1, beat=2), {"time": 1.5, "measure": -1}),
        (_beat(time=2.0, measure=1, beat=3), {"time": 2.0, "measure": -1}),
        (_beat(time=2.5, measure=2, beat=0), {"time": 2.5, "measure": 2}),
    ],
)
def test_beat_to_feedpak_marks_only_positive_measure_downbeats(source, expected):
    assert _beat_to_feedpak(source) == expected


def test_every_song_output_path_uses_downbeat_semantics():
    beats = [
        _beat(time=index * 0.5, measure=(index // 4) + 1, beat=index % 4)
        for index in range(8)
    ]
    song = SimpleNamespace(
        beats=beats,
        chordTemplates=[],
        levels=[],
        metadata=SimpleNamespace(tuning=[0, 0, 0, 0, 0, 0], capo=0),
        sections=[],
    )
    expected = [
        {"time": index * 0.5, "measure": measure}
        for index, measure in enumerate([1, -1, -1, -1, 2, -1, -1, -1])
    ]

    arrangement = _song_to_arrangement(
        song,
        "songs/bin/generic/example_lead.sng",
        {},
        include_tones=False,
    )
    timeline = _song_to_timeline(song)
    karaoke = _karaoke_arrangement(song)

    assert arrangement["beats"] == expected
    assert timeline["beats"] == expected
    assert karaoke["beats"] == expected

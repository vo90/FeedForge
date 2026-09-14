from types import SimpleNamespace

import pytest

from feedback_converter import converter


def _ns(**values):
    return SimpleNamespace(**values)


def _note(**overrides):
    values = {
        "time": 4.0,
        "string": 0,
        "fret": 3,
        "sustain": 0.0,
        "slideTo": -1,
        "slideUnpitchTo": -1,
        "bend_time": 0.0,
        "bends": [],
        "leftHand": -1,
        "mask": 0,
    }
    values.update(overrides)
    return _ns(**values)


@pytest.mark.parametrize("fret", [26, 127])
def test_out_of_highway_fret_is_preserved_by_chart_mapping(fret):
    converted = converter._note_to_feedpak(_note(fret=fret))

    assert converted["f"] == fret


def test_out_of_highway_slide_destination_is_preserved_by_chart_mapping():
    converted = converter._note_to_feedpak(_note(slideUnpitchTo=28))

    assert converted["slu"] == 28


def test_reversed_handshape_span_is_preserved_by_chart_mapping():
    level = _ns(
        fingerprints=[
            [_ns(chordId=0, startTime=8.0, endTime=7.0)],
            [],
        ]
    )

    assert converter._handshapes_to_feedpak(level) == [
        {"chord_id": 0, "start_time": 8.0, "end_time": 7.0}
    ]


def test_repeated_downbeat_numbers_are_preserved_by_chart_mapping():
    beats = [
        _ns(time=0.0, measure=1, beat=0),
        _ns(time=0.5, measure=1, beat=1),
        _ns(time=1.0, measure=1, beat=0),
    ]

    assert [converter._beat_to_feedpak(beat) for beat in beats] == [
        {"time": 0.0, "measure": 1},
        {"time": 0.5, "measure": -1},
        {"time": 1.0, "measure": 1},
    ]


def test_nonmonotonic_section_order_is_preserved_by_chart_mapping():
    song = _ns(
        sections=[
            _ns(name="verse", number=1, startTime=8.0),
            _ns(name="intro", number=1, startTime=0.0),
        ]
    )

    assert converter._sections_to_feedpak(song) == [
        {"name": "verse", "time": 8.0, "number": 1},
        {"name": "intro", "time": 0.0, "number": 1},
    ]

from types import SimpleNamespace

import pytest

from feedback_converter import converter


def _ns(**kwargs):
    return SimpleNamespace(**kwargs)


def _template(*, mask=None, name="C"):
    values = {
        "name": name,
        "frets": [3, 5, -1, -1, -1, -1],
        "fingers": [1, 3, -1, -1, -1, -1],
    }
    if mask is not None:
        values["mask"] = mask
    return _ns(**values)


def _fingerprint(chord_id, start, end):
    return _ns(chordId=chord_id, startTime=start, endTime=end)


@pytest.mark.parametrize(
    ("template", "expected_arp"),
    [
        (_template(mask=converter.CHORD_MASK_ARPEGGIO), True),
        (_template(mask=0), None),
        (_template(), None),
        (_template(mask=0, name="Arpeggio C"), None),
    ],
)
def test_template_arpeggio_comes_only_from_the_authored_mask(template, expected_arp):
    converted = converter._template_to_feedpak(template)

    assert converted.get("arp") is expected_arp


def test_handshape_fingerprint_groups_preserve_order_and_arpeggio_identity():
    level = _ns(
        fingerprints=[
            [
                _fingerprint(2, 1.0, 2.0),
                _fingerprint(converter.UINT32_NONE, 2.0, 3.0),
            ],
            [
                _fingerprint(converter.UINT32_NONE, 2.5, 3.0),
                _fingerprint(3, 3.0, 4.0),
            ],
        ]
    )

    shapes = converter._handshapes_to_feedpak(level)

    assert shapes == [
        {"chord_id": 2, "start_time": 1.0, "end_time": 2.0},
        {"chord_id": 3, "start_time": 3.0, "end_time": 4.0, "arp": True},
    ]


def test_high_density_does_not_infer_arpeggio():
    source_template = _template(mask=0, name="Arpeggio C")
    chord = _ns(
        time=1.0,
        string=-1,
        fret=-1,
        sustain=0.0,
        mask=converter.NOTE_MASK_HIGHDENSITY,
        chordId=0,
        chordNoteId=converter.UINT32_NONE,
    )
    level = _ns(
        difficulty=0,
        notes=[chord],
        anchors=[],
        fingerprints=[[_fingerprint(0, 1.0, 2.0)], []],
    )
    song = _ns(
        chordTemplates=[source_template],
        chordNotes=[],
        levels=[level],
        phraseIterations=[],
        phrases=[],
    )

    chart = converter._song_chart_data(song, [converter._template_to_feedpak(source_template)])

    assert chart["chords"][0]["hd"] is True
    assert "arp" not in chart["chords"][0]
    assert "arp" not in chart["handshapes"][0]


def test_song_chart_data_preserves_arpeggio_in_flat_and_phrase_handshapes():
    source_template = _template(mask=converter.CHORD_MASK_ARPEGGIO)
    level = _ns(
        difficulty=0,
        notes=[],
        anchors=[],
        fingerprints=[[], [_fingerprint(0, 1.0, 2.0)]],
    )
    song = _ns(
        chordTemplates=[source_template],
        chordNotes=[],
        levels=[level],
        phraseIterations=[_ns(phraseId=0, time=0.0, endTime=3.0)],
        phrases=[_ns(maxDifficulty=0)],
    )

    chart = converter._song_chart_data(song, [converter._template_to_feedpak(source_template)])

    expected = {"chord_id": 0, "start_time": 1.0, "end_time": 2.0, "arp": True}
    assert chart["handshapes"] == [expected]
    assert chart["phrases"][0]["levels"][0]["handshapes"] == [expected]

from types import SimpleNamespace

import pytest

from feedback_converter import converter
from feedback_converter.feedpak_semantics import validate_arrangement_semantics


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


def _note(time):
    return _ns(
        time=time,
        string=0,
        fret=3,
        sustain=0.0,
        slideTo=-1,
        slideUnpitchTo=-1,
        bends=[],
        bend_time=0.0,
        leftHand=-1,
        mask=0,
        chordId=converter.UINT32_NONE,
    )


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


def test_handshape_fingerprint_groups_merge_in_time_order_and_preserve_arpeggio_identity():
    level = _ns(
        fingerprints=[
            [
                _fingerprint(2, 3.0, 4.0),
                _fingerprint(4, 3.0, 3.5),
                _fingerprint(converter.UINT32_NONE, 2.0, 3.0),
            ],
            [
                _fingerprint(converter.UINT32_NONE, 2.5, 3.0),
                _fingerprint(3, 1.0, 2.0),
                _fingerprint(5, 3.0, 3.5),
            ],
        ]
    )

    shapes = converter._handshapes_to_feedpak(level)

    assert shapes == [
        {"chord_id": 3, "start_time": 1.0, "end_time": 2.0, "arp": True},
        {"chord_id": 2, "start_time": 3.0, "end_time": 4.0},
        {"chord_id": 4, "start_time": 3.0, "end_time": 3.5},
        {"chord_id": 5, "start_time": 3.0, "end_time": 3.5, "arp": True},
    ]


def test_phrase_span_slicing_clips_copies_and_preserves_extensions():
    items = [
        {"chord_id": 0, "start_time": 0.0, "end_time": 1.0},
        {"chord_id": 1, "start_time": 3.0, "end_time": 4.0},
        {"chord_id": 2, "start_time": 0.5, "end_time": 2.0, "future": "kept"},
        {"chord_id": 3, "start_time": 2.0, "end_time": 4.0, "arp": True},
        {"chord_id": 4, "start_time": 0.5, "end_time": 4.0},
        {"chord_id": 5, "start_time": 1.5, "end_time": 2.5},
        {"chord_id": 6, "start_time": 2.0, "end_time": 2.0},
    ]
    original = [dict(item) for item in items]

    sliced = converter._slice_spans_by_time(
        items, "start_time", "end_time", 1.0, 3.0
    )

    assert sliced == [
        {"chord_id": 2, "start_time": 1.0, "end_time": 2.0, "future": "kept"},
        {"chord_id": 4, "start_time": 1.0, "end_time": 3.0},
        {"chord_id": 5, "start_time": 1.5, "end_time": 2.5},
        {"chord_id": 3, "start_time": 2.0, "end_time": 3.0, "arp": True},
        {"chord_id": 6, "start_time": 2.0, "end_time": 2.0},
    ]
    assert items == original
    assert all(result is not source for result in sliced for source in items)


def test_phrase_span_slicing_keeps_reversed_spans_visible_to_validation():
    malformed = {"chord_id": 0, "start_time": 1.5, "end_time": 1.0}

    sliced = converter._slice_spans_by_time(
        [malformed], "start_time", "end_time", 1.0, 2.0
    )

    assert sliced == [malformed]
    assert sliced[0] is not malformed


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
    levels = [
        _ns(
            difficulty=difficulty,
            notes=[],
            anchors=[],
            fingerprints=[[], [_fingerprint(0, 1.0, 2.0)]],
        )
        for difficulty in (0, 1)
    ]
    song = _ns(
        chordTemplates=[source_template],
        chordNotes=[],
        levels=levels,
        phraseIterations=[_ns(phraseId=0, time=0.0, endTime=3.0)],
        phrases=[_ns(maxDifficulty=1)],
    )

    chart = converter._song_chart_data(song, [converter._template_to_feedpak(source_template)])

    expected = {"chord_id": 0, "start_time": 1.0, "end_time": 2.0, "arp": True}
    assert chart["handshapes"] == [expected]
    assert chart["phrases"][0]["levels"][1]["handshapes"] == [expected]


def test_cross_phrase_handshape_is_clipped_locally_but_preserved_at_top_level():
    source_template = _template(mask=0)
    levels = [
        _ns(
            difficulty=difficulty,
            notes=[_note(1.0), _note(3.0)],
            anchors=[],
            fingerprints=[[_fingerprint(0, 0.5, 3.5)], []],
        )
        for difficulty in (0, 1)
    ]
    song = _ns(
        metadata=_ns(tuning=[0, 0, 0, 0, 0, 0], capo=0),
        chordTemplates=[source_template],
        chordNotes=[],
        levels=levels,
        phraseIterations=[
            _ns(phraseId=0, time=0.0, endTime=2.0),
            _ns(phraseId=1, time=2.0, endTime=4.0),
        ],
        phrases=[
            _ns(name="verse", maxDifficulty=1),
            _ns(name="chorus", maxDifficulty=1),
        ],
        beats=[],
        sections=[],
    )

    arrangement = converter._song_to_arrangement(
        song,
        "songs/bin/generic/example_lead.sng",
        {},
        include_tones=False,
    )

    assert arrangement["handshapes"] == [
        {"chord_id": 0, "start_time": 0.5, "end_time": 3.5}
    ]
    assert arrangement["phrases"][0]["levels"][1]["handshapes"] == [
        {"chord_id": 0, "start_time": 0.5, "end_time": 2.0}
    ]
    assert arrangement["phrases"][1]["levels"][1]["handshapes"] == [
        {"chord_id": 0, "start_time": 2.0, "end_time": 3.5}
    ]

    errors = []
    validate_arrangement_semantics(
        arrangement,
        {"tuning": [0, 0, 0, 0, 0, 0]},
        "arrangements/lead.json",
        errors.append,
    )
    assert errors == []


def test_flat_handshapes_follow_dynamic_difficulty_across_phrase_boundary():
    source_template = _template(mask=0)
    levels = [
        _ns(
            difficulty=0,
            notes=[_note(1.0), _note(3.0)],
            anchors=[],
            fingerprints=[[_fingerprint(0, 0.5, 2.5)], []],
        ),
        _ns(
            difficulty=1,
            notes=[_note(1.0), _note(3.0)],
            anchors=[],
            fingerprints=[[_fingerprint(0, 1.5, 3.5)], []],
        ),
    ]
    song = _ns(
        metadata=_ns(tuning=[0, 0, 0, 0, 0, 0], capo=0),
        chordTemplates=[source_template],
        chordNotes=[],
        levels=levels,
        phraseIterations=[
            _ns(phraseId=0, time=0.0, endTime=2.0),
            _ns(phraseId=1, time=2.0, endTime=4.0),
        ],
        phrases=[
            _ns(name="verse", maxDifficulty=0),
            _ns(name="chorus", maxDifficulty=1),
        ],
        beats=[],
        sections=[],
    )

    arrangement = converter._song_to_arrangement(
        song,
        "songs/bin/generic/example_lead.sng",
        {},
        include_tones=False,
    )

    assert arrangement["handshapes"] == [
        {"chord_id": 0, "start_time": 0.5, "end_time": 2.5},
        {"chord_id": 0, "start_time": 1.5, "end_time": 3.5},
    ]
    assert arrangement["phrases"][0]["levels"][0]["handshapes"] == [
        {"chord_id": 0, "start_time": 0.5, "end_time": 2.0}
    ]
    assert arrangement["phrases"][1]["levels"][1]["handshapes"] == [
        {"chord_id": 0, "start_time": 2.0, "end_time": 3.5}
    ]

    errors = []
    validate_arrangement_semantics(
        arrangement,
        {"tuning": [0, 0, 0, 0, 0, 0]},
        "arrangements/lead.json",
        errors.append,
    )
    assert errors == []

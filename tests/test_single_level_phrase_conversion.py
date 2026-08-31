from types import SimpleNamespace

from feedback_converter import converter


def _ns(**kwargs):
    return SimpleNamespace(**kwargs)


def _note(*, fret, time=1.0):
    return _ns(
        time=time,
        string=0,
        fret=fret,
        sustain=0.0,
        slideTo=-1,
        slideUnpitchTo=-1,
        bends=[],
        bend_time=0.0,
        leftHand=-1,
        mask=0,
        chordId=converter.UINT32_NONE,
    )


def _level(*, difficulty, fret, handshape=False):
    return _ns(
        difficulty=difficulty,
        notes=[_note(fret=fret)],
        anchors=[_ns(time=0.5, fret=fret, width=4)],
        fingerprints=[
            [_ns(chordId=0, startTime=0.25, endTime=1.75)] if handshape else [],
            [],
        ],
    )


def _song(*levels, max_difficulty=None):
    if max_difficulty is None:
        max_difficulty = max(int(level.difficulty) for level in levels)
    return _ns(
        metadata=_ns(tuning=[0, 0, 0, 0, 0, 0], capo=0),
        chordTemplates=[],
        chordNotes=[],
        levels=list(levels),
        phraseIterations=[_ns(phraseId=0, time=0.0, endTime=2.0)],
        phrases=[_ns(name="verse", maxDifficulty=max_difficulty)],
        beats=[],
        sections=[],
    )


def test_single_level_chart_omits_meaningless_phrase_ladder():
    song = _song(_level(difficulty=0, fret=3, handshape=True))

    chart = converter._song_chart_data(song, [])

    assert chart["phrases"] == []
    assert chart["notes"] == [{"t": 1.0, "s": 0, "f": 3}]
    assert chart["anchors"] == [{"time": 0.5, "fret": 3, "width": 4}]
    assert chart["handshapes"] == [
        {"chord_id": 0, "start_time": 0.25, "end_time": 1.75}
    ]


def test_single_level_arrangement_does_not_serialize_phrases():
    song = _song(_level(difficulty=4, fret=7), max_difficulty=4)

    arrangement = converter._song_to_arrangement(
        song,
        "songs/bin/generic/example_lead.sng",
        {},
        include_tones=False,
    )

    assert "phrases" not in arrangement
    assert arrangement["notes"] == [{"t": 1.0, "s": 0, "f": 7}]


def test_multi_level_chart_keeps_authored_phrase_ladder_and_flat_highest_level():
    song = _song(
        _level(difficulty=0, fret=3),
        _level(difficulty=2, fret=8),
        max_difficulty=2,
    )

    chart = converter._song_chart_data(song, [])

    assert chart["notes"] == [{"t": 1.0, "s": 0, "f": 8}]
    assert len(chart["phrases"]) == 1
    assert [level["difficulty"] for level in chart["phrases"][0]["levels"]] == [0, 2]
    assert chart["phrases"][0]["levels"][0]["notes"] == [
        {"t": 1.0, "s": 0, "f": 3}
    ]
    assert chart["phrases"][0]["levels"][1]["notes"] == [
        {"t": 1.0, "s": 0, "f": 8}
    ]


def test_duplicate_source_levels_with_one_difficulty_still_omit_phrases():
    song = _song(
        _level(difficulty=0, fret=3),
        _level(difficulty=0, fret=5),
        max_difficulty=0,
    )

    chart = converter._song_chart_data(song, [])

    assert chart["phrases"] == []
    assert chart["notes"] == [{"t": 1.0, "s": 0, "f": 5}]

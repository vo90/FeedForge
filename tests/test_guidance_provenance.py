from copy import deepcopy

from feedback_converter.chart_guidance import finalize
from feedback_converter.difficulty import ensure_difficulty
from feedback_converter.guidance_provenance import edited, origins, resolve, stamp
from test_chart_guidance import chart, chord, template, note


def test_supplied_lanes_survive_generated_handshapes_and_practice_levels():
    data = chart(chords=[chord(t, (3, 5)) for t in range(1, 5)], templates=[template()])
    data["anchors"] = [{"time": 0, "fret": 2, "width": 4}]
    stamp(data, "anchors", "source", producer="fixture")
    original = deepcopy(data["anchors"])
    finalize(data)
    assert data["anchors"] == original
    assert origins(data, "anchors") == ["source"]
    assert resolve(data, "handshapes")["origin"] == "generated"
    ensure_difficulty(data, duration=5)
    for phrase in data["phrases"]:
        for level in phrase["levels"]:
            assert all(a["fret"] == 2 for a in level["anchors"])
            assert set(origins(level, "anchors")) <= {"source"}
            assert resolve({**data, **level}, "handshapes")["origin"] == "generated"


def test_empty_authoritative_guidance_is_not_missing():
    data = chart([note(1, 3)])
    stamp(data, "anchors", "source", producer="fixture", intentional_empty=True)
    stamp(data, "handshapes", "user", producer="fixture", intentional_empty=True)
    before = deepcopy(data)
    finalize(data, regenerate=True)
    assert data == before


def test_generated_regeneration_cannot_overwrite_user_handshape_edits():
    data = chart(chords=[chord(1)], templates=[template()])
    finalize(data)
    before = deepcopy(data)
    data["handshapes"][0]["end_time"] = 1.5
    edited(before, data)
    expected = deepcopy(data["handshapes"])
    data["notes"].append(note(4, 15))
    finalize(data, regenerate=True)
    assert data["handshapes"] == expected
    assert origins(data, "handshapes") == ["user"]
    assert data["ext"]["chartGuidance"]["fields"] == ["anchors"]
    assert resolve(data, "anchors")["applicability"] == "current"


def test_unknown_future_contract_preserves_empty_fields():
    data = chart([note(1, 3)])
    data["ext"] = {"guidanceProvenance": {"version": 99, "fields": {}}}
    before = deepcopy(data)
    finalize(data, regenerate=True)
    assert data == before


def test_archive_conversion_uses_supplied_lanes_and_completes_only_missing_fields():
    from test_converter import fake_song
    from feedback_converter.converter import _song_to_arrangement
    source = fake_song()
    authored = _song_to_arrangement(source, "lead.sng", {}, include_tones=False)
    assert authored["anchors"] == [{"time": 0, "fret": 1, "width": 4}]
    assert origins(authored, "anchors") == ["source"]
    assert resolve(authored, "handshapes")["origin"] == "generated"
    source.levels[0].anchors = []
    completed = _song_to_arrangement(source, "lead.sng", {}, include_tones=False)
    assert completed["anchors"]
    assert set(origins(completed, "anchors")) == {"generated"}
    for field in ("notes", "chords", "templates"):
        assert completed[field] == authored[field]

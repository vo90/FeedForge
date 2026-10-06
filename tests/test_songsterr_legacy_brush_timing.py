"""Producer musical answers for qualified old brushes, without verifier helpers."""
from copy import deepcopy
from fractions import Fraction as F

import pytest

from test_song_import_score import measure, raw_score
from feedback_converter.song_import.model import ScoreImportError
from feedback_converter.song_import.songsterr import parse
from feedback_converter.song_import.songsterr_timing import strum_offsets, LEGACY_BRUSH_POLICY
from feedback_converter.song_import.timeline import render
from feedback_converter.song_import.strum_groups import attach


INTERVALS = [F(1, 416), F(1, 208), F(1, 104), F(1, 52),
             F(1, 26), F(1, 13), F(2, 13), F(4, 13)]


def brush(field="upStroke", value=6, strings=(5, 0, 2), duration=(1, 1)):
    return {"duration": list(duration), field: value,
            "notes": [{"string": s, "fret": s + 1} for s in strings]}


def produced(document):
    original = deepcopy(document)
    score = parse(document)
    performance = render(score)
    assert document == original
    assert score.source_document["document"] == original
    return score, performance, performance["tracks"][0]


def flat(track):
    return sorted([*track["notes"], *[{**n, "t": c["t"]}
                  for c in track["chords"] for n in c["notes"]]], key=lambda n: (n["t"], n["s"]))


@pytest.mark.parametrize("field,direction", [("upStroke", "down"), ("downStroke", "up")])
@pytest.mark.parametrize("value", range(1, 9))
@pytest.mark.parametrize("strings", [(5, 0), (4, 1, 5), (5, 0, 3, 2, 1, 4)])
def test_old_subdivision_has_count_independent_attacks_and_written_end(field, direction, value, strings):
    source = raw_score([measure(brush(field, value, strings))])
    score, performance, track = produced(source)
    step = INTERVALS[value - 1]
    ordered = sorted(strings, reverse=direction == "down")
    attacks = flat(track)
    assert not track["chords"]
    assert [n["s"] for n in attacks] == [5 - s for s in ordered]
    assert [n["t"] for n in attacks] == pytest.approx([float(step * i / 2) for i in range(len(strings))])
    assert [n["t"] + n["sus"] for n in attacks] == pytest.approx([2] * len(strings))
    assert [n["pkd"] for n in attacks] == [int(direction == "up")] * len(strings)
    offsets = {n.string: n.attack_offset for n in score.tracks[0].bars[0]}
    assert offsets == {5 - s: i * step for i, s in enumerate(ordered)}
    assert performance["source"]["legacyBrushTimingPolicy"] == LEGACY_BRUSH_POLICY
    written = track["notation"]["measures"][0]["staves"]["staff"]["voices"][0]["beats"][0]
    assert written["t"] == 0 and written["end_time"] == 2
    assert len(written["notes"]) == len(strings)


@pytest.mark.parametrize("version", [None, 5, 8])
@pytest.mark.parametrize("value", [1, 3.0, 6, 8])
def test_part_version_does_not_migrate_the_numeric_subdivision(version, value):
    source = raw_score([measure(brush(value=value))])
    if version is not None:
        source["parts"][0]["version"] = version
    _, _, track = produced(source)
    assert [n["t"] for n in track["notes"]] == pytest.approx(
        [float(INTERVALS[int(value) - 1] * i / 2) for i in range(3)])


@pytest.mark.parametrize("duration", [(1, 1), (1, 4), (1, 8)])
def test_old_spacing_has_no_modern_written_beat_cap(duration):
    _, _, track = produced(raw_score([measure(brush(duration=duration, strings=(5, 4, 3, 2, 1, 0)))]))
    assert [n["t"] for n in track["notes"]] == pytest.approx([i / 26 for i in range(6)])
    assert [n["t"] + n["sus"] for n in track["notes"]] == pytest.approx([2 * duration[0] / duration[1]] * 6)


def test_short_brush_is_not_capped_or_hidden_to_force_positive_notes():
    source = raw_score([measure(brush(duration=(1, 128), strings=(5, 4, 3, 2, 1, 0)))])
    offsets, direction = strum_offsets(source["parts"][0]["measures"][0]["voices"][0]["beats"][0])
    assert direction == "down" and max(offsets.values()) == F(5, 13)
    with pytest.raises(ScoreImportError, match="consumes a sounding note"):
        render(parse(source))


@pytest.mark.parametrize("field", ["upStroke", "downStroke"])
@pytest.mark.parametrize("rest_index", [0, 1, 2])
def test_silent_note_slot_retains_its_rank(field, rest_index):
    b = brush(field, strings=(0, 2, 5))
    b["notes"][rest_index]["rest"] = True
    _, performance, track = produced(raw_score([measure(b)]))
    expected = {5 - s: float((2 - rank if field == "upStroke" else rank) * F(1, 26))
                for rank, s in enumerate((0, 2, 5)) if rank != rest_index}
    assert {n["s"]: n["t"] for n in flat(track)} == pytest.approx(expected)
    assert len(flat(track)) == 2
    assert len(performance["strumEvidence"][0]["notes"]) == 2


@pytest.mark.parametrize("slot", [None, -1, 6, 1.5, True])
def test_unqualified_rest_slot_is_a_located_source_error(slot):
    b = brush()
    rest = {"rest": True}
    if slot is not None:
        rest["string"] = slot
    b["notes"].append(rest)
    with pytest.raises(ScoreImportError) as caught:
        parse(raw_score([measure(b)]))
    assert caught.value.source_location["location"] == "parts/0/measures/0/voices/0/beats/0"


def test_duplicate_rest_slot_is_not_collapsed():
    b = brush()
    b["notes"].append({"rest": True, "string": 2})
    with pytest.raises(ScoreImportError, match="same string"):
        parse(raw_score([measure(b)]))


def test_shifted_tied_continuations_keep_origin_picks_and_common_end():
    head = brush(value=6, duration=(1, 2))
    tail = brush("downStroke", 8, duration=(1, 2))
    for n in tail["notes"]:
        n["tie"] = True
    _, _, track = produced(raw_score([measure(head, tail)]))
    attacks = flat(track)
    assert [n["t"] for n in attacks] == pytest.approx([0, 1 / 26, 1 / 13])
    assert [n["t"] + n["sus"] for n in attacks] == pytest.approx([2] * 3)
    assert [n["pkd"] for n in attacks] == [0] * 3
    assert all(len(n["source_ids"]) == 2 for n in attacks)


def test_partial_tie_keeps_its_slot_without_creating_a_new_attack():
    head = brush(duration=(1, 2))
    head.pop("upStroke")
    tail = brush(duration=(1, 2))
    tail["notes"][2]["tie"] = True  # source string2 is the middle sorted slot.
    _, _, track = produced(raw_score([measure(head, tail)]))
    attacks = flat(track)
    new = [n for n in attacks if n["t"] >= 1]
    assert [(n["s"], n["t"]) for n in new] == [(0, 1), (5, 1 + 1 / 13)]
    tied = next(n for n in attacks if n["s"] == 3 and n["t"] == 0)
    assert tied["sus"] == 2 and len(tied["source_ids"]) == 2


@pytest.mark.parametrize("value", [True, "1", "3", [1, 1], {}, [], "", 2.5, -1, 9, float("inf"), float("nan")])
def test_native_coercion_shapes_are_not_authorized_subdivisions(value):
    with pytest.raises(ScoreImportError, match="strum"):
        parse(raw_score([measure(brush(value=value))]))


@pytest.mark.parametrize("value", [None, False, 0, 0.0])
def test_inactive_values_leave_the_authored_simultaneous_chord(value):
    _, performance, track = produced(raw_score([measure(brush(value=value))]))
    assert len(track["chords"]) == 1 and not track["notes"]
    assert "legacyBrushTimingPolicy" not in performance["source"]
    assert not performance["strumEvidence"]


@pytest.mark.parametrize("kind", ["brushStroke", "arpeggio"])
def test_single_modern_representation_wins_over_retained_old_values(kind):
    b = brush(value=6)
    b[kind] = {"direction": "up", "duration": 120, "shift": 100}
    b["upArpeggio"] = 3
    _, performance, track = produced(raw_score([measure(b)]))
    assert [n["s"] for n in track["notes"]] == [5, 3, 0]
    assert [n["t"] for n in track["notes"]] == pytest.approx([0, 1 / 24, 1 / 12])
    assert "legacyBrushTimingPolicy" not in performance["source"]
    expected_kind = "brush" if kind == "brushStroke" else "arpeggio"
    assert performance["strumEvidence"][0]["kind"] == expected_kind
    attach(track, track["id"], performance["strumEvidence"], {"offset": 0, "scale": 1})
    assert all(("ch" in n) == (expected_kind == "brush") for n in track["notes"])


@pytest.mark.parametrize("extra", [{"downStroke": 3}, {"upArpeggio": 3},
                                   {"brushStroke": {"direction": "up", "duration": 30, "shift": 100},
                                    "arpeggio": {"direction": "down", "duration": 30, "shift": 100}}])
def test_conflicting_representations_keep_their_guards(extra):
    b = brush()
    b.update(extra)
    with pytest.raises(ScoreImportError, match="Conflicting"):
        parse(raw_score([measure(b)]))


def test_linked_brush_stays_simultaneous_and_explicit_picking_and_fingers_win():
    b = brush(value=8)
    b["pickStroke"] = "up"
    b["notes"][0]["bend"] = {"points": [{"position": 0, "tone": 0}, {"position": 60, "tone": 100}]}
    b["notes"][1]["leftFingering"] = "1"
    _, performance, track = produced(raw_score([measure(b)]))
    assert len(track["chords"]) == 1
    assert all(n["pkd"] == 1 for n in flat(track))
    assert next(n for n in flat(track) if n["s"] == 5)["fg"] == 1
    assert performance["source"]["legacyBrushTimingPolicy"] == LEGACY_BRUSH_POLICY


def test_singleton_brush_records_its_policy_and_direction():
    _, performance, track = produced(raw_score([measure(brush("downStroke", 3, (2,)))]))
    assert [(n["t"], n["pkd"]) for n in flat(track)] == [(0, 1)]
    assert performance["source"]["legacyBrushTimingPolicy"] == LEGACY_BRUSH_POLICY
    assert performance["strumEvidence"] == []


def test_policy_marker_only_counts_selected_supported_parts():
    source = raw_score([measure(brush(value=0))])
    source["tracks"].append({**deepcopy(source["tracks"][0]), "id": 1})
    source["parts"].append({"measures": [measure(brush(value=3, strings=(2,)))]})
    original = deepcopy(source)
    assert "legacyBrushTimingPolicy" not in render(parse(source, track_indices=[0]))["source"]
    assert render(parse(source, track_indices=[1]))["source"]["legacyBrushTimingPolicy"] == LEGACY_BRUSH_POLICY
    source["tracks"][1]["instrumentId"] = 0  # Unsupported acoustic piano part.
    assert "legacyBrushTimingPolicy" not in render(parse(source))["source"]
    original["tracks"][1]["instrumentId"] = 0
    assert source == original


def test_each_projected_voice_preserves_its_own_brush_origin_and_direction():
    first = brush(value=6)
    second = brush("downStroke", 6)
    for n in second["notes"]:
        n["fret"] += 12
    bar = measure(first)
    bar["voices"].append({"beats": [second]})
    _, performance, _ = produced(raw_score([bar]))
    tracks = performance["tracks"]
    assert len(tracks) == 2
    assert [[n["s"] for n in flat(t)] for t in tracks] == [[0, 3, 5], [5, 3, 0]]
    for t in tracks:
        assert [n["t"] for n in flat(t)] == pytest.approx([0, 1 / 26, 1 / 13])
        assert [n["t"] + n["sus"] for n in flat(t)] == pytest.approx([2] * 3)
    groups = performance["strumEvidence"]
    assert {g["trackId"] for g in groups} == {t["id"] for t in tracks}
    assert [g["direction"] for g in groups] == ["down", "up"]
    assert all(g["kind"] == "brush" and len(g["notes"]) == 3 for g in groups)


def test_repeated_shape_keeps_distinct_display_groups_and_source_occurrences():
    _, performance, track = produced(raw_score([measure(brush(), repeatStart=True, repeat=2)]))
    groups = performance["strumEvidence"]
    assert [g["occurrence"] for g in groups] == [1, 2]
    assert all(g["kind"] == "brush" and len(g["notes"]) == 3 for g in groups)
    original = deepcopy(track["notes"])
    attach(track, track["id"], groups, {"offset": 0, "scale": 1})
    assert {n["ch"] for n in track["notes"]} == {0, 1}
    assert [{k: v for k, v in n.items() if k != "ch"} for n in track["notes"]] == original

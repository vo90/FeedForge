"""Only qualified notation on excluded parts becomes a routine diagnostic."""
from copy import deepcopy

import pytest

from feedback_converter.song_import.inventory import FeatureInventory
from feedback_converter.song_import.model import ScoreImportError
from feedback_converter.song_import.songsterr import parse
from feedback_converter.song_import.timeline import render
from feedback_converter.song_import.verify_source import songsterr as read_source
from feedback_converter.song_import.verify_timeline import expected
from test_song_import_score import beat, measure, raw_score


MAX_SAFE_INTEGER = 9_007_199_254_740_991
VALID = [("clef", value) for value in ["G2", "F4", "C3", "C4", "neutral"]] + [
    ("grandStaffGroup", value) for value in [0, 1, 2, 0.0, -0.0, 3.0, MAX_SAFE_INTEGER]]


def warning(field):
    return f"Unclassified Songsterr part field {field} is retained in source evidence only."


def diagnostic(field):
    return f"Excluded arrangement notation: Songsterr part field {field} is retained in source evidence only."


def score(instruments=(29, 0)):
    document = raw_score([measure(beat(3))])
    document["tracks"], document["parts"] = [], []
    for index, instrument in enumerate(instruments):
        fragment = raw_score([measure(beat(index + 3))])
        fragment["tracks"][0].update(id=70 - index * 10, instrumentId=instrument,
                                     name=f"{'Keyboard' if instrument == 0 else 'Guitar'} {index}")
        document["tracks"].append(fragment["tracks"][0])
        document["parts"].append(fragment["parts"][0])
    return document


def musical_evidence(document, indices=None):
    rendered = render(parse(document, track_indices=indices))
    reference = expected(read_source(document, track_indices=indices), {"offset": 0, "scale": 1})
    return {key: value for key, value in rendered.items()
            if key not in {"warnings", "featureInventory", "sourceScore"}}, reference


def entries(rendered, field):
    return [row for row in rendered["featureInventory"]
            if row["scope"] == "Songsterr part" and row["field"] == field]


@pytest.mark.parametrize("field,value", VALID)
@pytest.mark.parametrize("part_index", [0, 1])
def test_part_selection_qualifies_diagnostics_without_changing_music(field, value, part_index):
    document = score()
    baseline = musical_evidence(document)
    document["parts"][part_index][field] = value
    original = deepcopy(document)
    actual = render(parse(document))
    assert musical_evidence(document) == baseline
    assert actual["sourceScore"]["document"] == original == document
    row, = entries(actual, field)
    assert row["handling"] == "unclassified"
    assert row["representations"] == ["source"]
    assert row["examples"] == [f"$.parts[{part_index}].{field}"]
    assert row["count"] == 1
    if part_index == 1:
        assert row["relevance"] == "excluded_arrangement"
        assert diagnostic(field) in actual["warnings"]
        assert warning(field) not in actual["warnings"]
    else:
        assert "relevance" not in row
        assert warning(field) in actual["warnings"]
        assert diagnostic(field) not in actual["warnings"]


@pytest.mark.parametrize("field,value", [("clef", "F4"), ("grandStaffGroup", 1)])
def test_mixed_selected_and_excluded_occurrences_survive_example_limit(field, value):
    document = score((0, 0, 0, 0, 0, 29))
    for part in document["parts"]:
        part[field] = value
    actual = render(parse(document))
    selected, excluded = entries(actual, field)
    assert selected["count"] == 1
    assert "relevance" not in selected
    assert selected["examples"] == [f"$.parts[5].{field}"]
    assert excluded["count"] == 5
    assert excluded["relevance"] == "excluded_arrangement"
    assert excluded["examples"] == [f"$.parts[{index}].{field}" for index in range(3)]
    assert actual["warnings"].count(warning(field)) == 1
    assert actual["warnings"].count(diagnostic(field)) == 1


@pytest.mark.parametrize("indices", [{0}, {1}, {0, 1}])
def test_user_selection_uses_array_positions_not_metadata_ids(indices):
    document = score((29, 29))
    baseline = musical_evidence(document, indices)
    for part in document["parts"]:
        part.update(clef="G2", grandStaffGroup=1)
    actual = render(parse(document, track_indices=indices))
    assert musical_evidence(document, indices) == baseline
    assert [track["id"] for track in actual["tracks"]] == [str(70 - index * 10) for index in sorted(indices)]
    for field in ("clef", "grandStaffGroup"):
        selected = next(row for row in entries(actual, field) if "relevance" not in row)
        assert selected["count"] == len(indices)
        assert selected["examples"] == [f"$.parts[{index}].{field}" for index in sorted(indices)]
        excluded = [row for row in entries(actual, field) if row.get("relevance") == "excluded_arrangement"]
        assert bool(excluded) == (len(indices) < 2)
        if excluded:
            assert excluded[0]["examples"] == [f"$.parts[{index}].{field}" for index in range(2) if index not in indices]
        assert warning(field) in actual["warnings"]


INVALID = [(field, value) for field in ("clef", "grandStaffGroup")
           for value in [False, True, "", [], {}, [1], {"value": 1}]] + [
    ("clef", value) for value in ["unknown", "F5", 0, 1]] + [
    ("grandStaffGroup", value) for value in [-1, -0.5, 1.5, "1", MAX_SAFE_INTEGER + 1, 10 ** 400]]


@pytest.mark.parametrize("field,value", INVALID)
@pytest.mark.parametrize("excluded", [False, True])
def test_malformed_notation_remains_a_warning(field, value, excluded):
    document = score()
    baseline = musical_evidence(document)
    document["parts"][int(excluded)][field] = value
    actual = render(parse(document))
    assert musical_evidence(document) == baseline
    assert actual["sourceScore"]["document"] == document
    row, = entries(actual, field)
    assert row["handling"] == "unclassified"
    assert "relevance" not in row
    assert warning(field) in actual["warnings"]
    assert diagnostic(field) not in actual["warnings"]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_group_remains_visible(value):
    inventory = FeatureInventory()
    inventory.inspect({"grandStaffGroup": value}, "Songsterr part", "$.parts[0]", excluded_part=True)
    assert inventory.warnings() == [warning("grandStaffGroup")]


@pytest.mark.parametrize("field", ["clef", "grandStaffGroup"])
@pytest.mark.parametrize("excluded", [False, True])
def test_absent_or_null_optional_notation_adds_no_warning(field, excluded):
    inventory = FeatureInventory()
    inventory.inspect({}, "Songsterr part", "$.parts[0]", excluded_part=excluded)
    assert inventory.entries() == []
    inventory.inspect({field: None}, "Songsterr part", "$.parts[0]", excluded_part=excluded)
    assert inventory.warnings() == []
    assert inventory.entries()[0]["handling"] == "inactive_unknown"
    assert "relevance" not in inventory.entries()[0]


@pytest.mark.parametrize("field,value", [("clef", "F4"), ("grandStaffGroup", 1)])
@pytest.mark.parametrize("scope", ["Songsterr document", "Songsterr track", "Songsterr measure", "Songsterr beat"])
def test_other_scopes_are_not_qualified_by_excluded_part_context(field, value, scope):
    inventory = FeatureInventory()
    inventory.inspect({field: value}, scope, "$.example", excluded_part=True)
    assert inventory.warnings() == [f"Unclassified {scope} field {field} is retained in source evidence only."]
    assert "relevance" not in inventory.entries()[0]


@pytest.mark.parametrize("excluded", [False, True])
@pytest.mark.parametrize("field,value", [("clef", "F4"), ("grandStaffGroup", 1), ("clef", False)])
def test_qualified_diagnostic_does_not_weaken_strict_inventory_mode(excluded, field, value):
    inventory = FeatureInventory()
    with pytest.raises(ScoreImportError, match="Unsupported Songsterr part field"):
        inventory.inspect({field: value}, "Songsterr part", "$.parts[0]", excluded_part=excluded, strict=True)


def test_other_unknown_fields_and_bad_titles_on_excluded_parts_stay_visible():
    document = score()
    document["parts"][1].update(clef="F4", grandStaffGroup=1, futureMusic=True, title=False)
    actual = render(parse(document))
    for field in ("futureMusic", "title"):
        assert warning(field) in actual["warnings"]
        assert "relevance" not in entries(actual, field)[0]


@pytest.mark.parametrize("global_field", ["signature", "tempo", "unknown_automation"])
def test_excluded_notation_does_not_bypass_global_music_guards(global_field):
    document = score()
    excluded = document["parts"][1]
    excluded.update(clef="F4", grandStaffGroup=1)
    if global_field == "signature":
        excluded["measures"][0]["signature"] = [3, 4]
        message = "Tracks disagree about the time signature"
    elif global_field == "tempo":
        excluded["automations"]["tempo"][0]["bpm"] = 130
        message = "Tracks disagree about tempo"
    else:
        excluded["automations"]["futureAutomation"] = True
        message = "Unsupported Songsterr automations field"
    with pytest.raises(ScoreImportError, match=message):
        parse(document)


def test_selected_measure_clef_validation_is_unchanged():
    document = score()
    document["parts"][0]["measures"][0]["clef"] = "unknown"
    with pytest.raises(ScoreImportError, match="authored clef needs additional notation support"):
        parse(document)

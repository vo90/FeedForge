"""Only consistent voice-count metadata is quiet; all musical voices survive."""
from copy import deepcopy

import pytest

from feedback_converter.song_import.inventory import FeatureInventory
from feedback_converter.song_import.model import ScoreImportError
from test_song_import_score import beat, import_json, measure, raw_score


WARNING = "Unclassified Songsterr part field voices is retained in source evidence only."


def score(counts):
    bars = []
    for count in counts:
        bar = measure()
        bar["voices"] = [{"beats": [beat(3 + i, string=i)]} for i in range(count)]
        bars.append(bar)
    return raw_score(bars)


@pytest.mark.parametrize("counts", [[1], [2], [4], [1, 2, 1], [3, 1, 2]])
@pytest.mark.parametrize("excluded", [False, True])
def test_consistent_count_preserves_all_music_and_source(tmp_path, counts, excluded):
    document = score(counts)
    index = 0
    if excluded:
        document["tracks"].append({"id": 1, "name": "Vocals", "instrumentId": 53})
        document["parts"].append(deepcopy(document["parts"][0]))
        index = 1
    baseline = import_json(tmp_path, document)
    document["parts"][index]["voices"] = max(counts)
    actual = import_json(tmp_path, document)
    assert actual["sourceScore"]["document"] == document
    for field in baseline.keys() - {"sourceScore", "featureInventory"}:
        assert actual[field] == baseline[field], field
    entry = next(row for row in actual["featureInventory"]
                 if row["scope"] == "Songsterr part" and row["field"] == "voices")
    assert entry["handling"] == "source"
    assert entry["representations"] == ["source"]


@pytest.mark.parametrize("value", [None, False, True, 0, -1, 1.5, 2.0, "2", "", [], {}, [2], 1, 3])
def test_bad_or_contradictory_count_warns_without_losing_voices(tmp_path, value):
    document = score([1, 2])
    baseline = import_json(tmp_path, document)
    document["parts"][0]["voices"] = value
    actual = import_json(tmp_path, document)
    assert WARNING in actual["warnings"]
    assert actual["sourceScore"]["document"] == document
    for field in baseline.keys() - {"sourceScore", "featureInventory", "warnings"}:
        assert actual[field] == baseline[field], field


@pytest.mark.parametrize("measures", [None, {}, [], [None], [{}], [{"voices": None}],
    [{"voices": {}}], [{"voices": []}], [{"voices": [None]}], [{"voices": [[]]}],
    [{"voices": [{}]}, {"voices": "bad"}]])
def test_malformed_collections_never_qualify_as_metadata(measures):
    inventory = FeatureInventory()
    inventory.inspect({"voices": 1, "measures": measures}, "Songsterr part", "$.parts[0]", playable={"measures"})
    assert WARNING in inventory.warnings()


def test_contradictory_count_obeys_strict_inventory_mode():
    inventory = FeatureInventory()
    with pytest.raises(ScoreImportError, match="Unsupported Songsterr part field"):
        inventory.inspect({"voices": 2, "measures": [{"voices": [{}]}]},
                          "Songsterr part", "$.parts[0]", strict=True)


@pytest.mark.parametrize("scope", ["Songsterr track", "Songsterr beat", "Songsterr note"])
def test_other_scopes_do_not_gain_a_metadata_exemption(scope):
    inventory = FeatureInventory()
    inventory.inspect({"voices": 1}, scope, "$.example")
    assert inventory.warnings() == [f"Unclassified {scope} field voices is retained in source evidence only."]


def test_other_unknown_part_fields_still_warn():
    part = score([1])["parts"][0]
    part.update(voices=1, futureFeature=True)
    inventory = FeatureInventory()
    inventory.inspect(part, "Songsterr part", "$.parts[0]", playable={"measures", "automations"})
    assert inventory.warnings() == ["Unclassified Songsterr part field futureFeature is retained in source evidence only."]

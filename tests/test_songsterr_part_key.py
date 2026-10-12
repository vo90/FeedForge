"""Opaque part keys must not become player limitations or musical changes."""
from copy import deepcopy

import pytest

from feedback_converter.song_import.inventory import FeatureInventory
from test_song_import_score import beat, import_json, measure, raw_score


@pytest.mark.parametrize("value", ["opaque-source-id", "", "E minor", "識別子"])
@pytest.mark.parametrize("excluded", [False, True])
def test_string_part_key_retains_source_without_changing_music(tmp_path, value, excluded):
    document = raw_score([measure(beat(3))])
    index = 0
    if excluded:
        document["tracks"].append({"id": 1, "name": "Vocals", "instrumentId": 53})
        document["parts"].append(deepcopy(document["parts"][0]))
        index = 1
    baseline = import_json(tmp_path, document)
    document["parts"][index]["key"] = value
    actual = import_json(tmp_path, document)
    assert actual["sourceScore"]["document"] == document
    assert actual["warnings"] == baseline["warnings"]
    for field in baseline.keys() - {"sourceScore", "featureInventory"}:
        assert actual[field] == baseline[field], field
    entry = next(row for row in actual["featureInventory"]
                 if row["scope"] == "Songsterr part" and row["field"] == "key")
    assert entry["handling"] == "source"
    assert entry["representations"] == ["source"]


@pytest.mark.parametrize("value", [None, False, True, 0, 12, [], {}, ["id"], {"pitch": 4}])
def test_non_string_part_key_stays_visible(tmp_path, value):
    document = raw_score([measure(beat())])
    document["parts"][0]["key"] = value
    actual = import_json(tmp_path, document)
    assert "Unclassified Songsterr part field key is retained in source evidence only." in actual["warnings"]
    assert actual["sourceScore"]["document"] == document


@pytest.mark.parametrize("scope", ["Songsterr track", "Songsterr measure", "Songsterr beat", "Songsterr note"])
def test_key_at_other_scopes_is_not_silenced(scope):
    inventory = FeatureInventory()
    inventory.inspect({"key": "opaque-source-id"}, scope, "$.example")
    assert inventory.warnings() == [f"Unclassified {scope} field key is retained in source evidence only."]


def test_other_part_fields_are_not_silenced():
    inventory = FeatureInventory()
    inventory.inspect({"key": "opaque-source-id", "futureFeature": True}, "Songsterr part", "$.parts[0]")
    assert inventory.warnings() == ["Unclassified Songsterr part field futureFeature is retained in source evidence only."]

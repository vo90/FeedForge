"""Raw part titles are metadata, not arrangement names or player limitations."""
from copy import deepcopy

import pytest

from feedback_converter.song_import.compatibility import inspect_songsterr
from feedback_converter.song_import.inventory import FeatureInventory
from feedback_converter.song_import.model import ScoreImportError
from feedback_converter.song_import.songsterr import parse
from feedback_converter.song_import.timeline import render
from feedback_converter.song_import.verify_source import songsterr as read_source
from feedback_converter.song_import.verify_timeline import expected
from test_song_import_score import beat, import_json, measure, raw_score


WARNING = "Unclassified Songsterr part field title is retained in source evidence only."


def music(document):
    rendered = render(parse(document))
    reference = expected(read_source(document), {"offset": 0, "scale": 1})
    return rendered["tracks"], rendered["duration"], reference


def document_with_excluded_part(excluded):
    document = raw_score([measure(beat(3))])
    if excluded:
        document["tracks"].append({"id": 15, "name": "Right Hand", "instrumentId": 0})
        document["parts"].append(deepcopy(document["parts"][0]))
    return document


@pytest.mark.parametrize("value", ["Lead Guitar", "Left Hand Copy", "Vocals", "", "  ", "識別子 ♯\nGuitar"])
@pytest.mark.parametrize("excluded", [False, True])
def test_string_title_retains_source_without_changing_music_or_notices(tmp_path, value, excluded):
    document = document_with_excluded_part(excluded)
    baseline = import_json(tmp_path, document)
    baseline_music = music(document)
    baseline_compatibility = inspect_songsterr(document)
    document["parts"][int(excluded)]["title"] = value
    original = deepcopy(document)
    actual = import_json(tmp_path, document)
    assert actual["sourceScore"]["document"] == original == document
    assert music(document) == baseline_music
    assert inspect_songsterr(document) == baseline_compatibility
    for field in baseline.keys() - {"sourceScore", "featureInventory"}:
        assert actual[field] == baseline[field], field
    entries = [row for row in actual["featureInventory"]
               if row["scope"] == "Songsterr part" and row["field"] == "title"]
    assert len(entries) == 1
    assert entries[0]["handling"] == "source"
    assert entries[0]["representations"] == ["source"]
    assert entries[0]["examples"] == [f"$.parts[{int(excluded)}].title"]


def test_absent_title_adds_no_retention_claim(tmp_path):
    document = raw_score([measure(beat())])
    actual = import_json(tmp_path, document)
    assert WARNING not in actual["warnings"]
    assert not any(row["scope"] == "Songsterr part" and row["field"] == "title"
                   for row in actual["featureInventory"])
    assert actual["sourceScore"]["document"] == document


@pytest.mark.parametrize("value", [None, False, True, 0, 12, 0.5, [], {}, ["Lead"], {"name": "Lead"}])
@pytest.mark.parametrize("excluded", [False, True])
def test_non_string_title_warns_without_changing_import(tmp_path, value, excluded):
    document = document_with_excluded_part(excluded)
    baseline = import_json(tmp_path, document)
    baseline_music = music(document)
    document["parts"][int(excluded)]["title"] = value
    original = deepcopy(document)
    actual = import_json(tmp_path, document)
    assert actual["warnings"] == [*baseline["warnings"], WARNING]
    assert actual["sourceScore"]["document"] == original == document
    assert music(document) == baseline_music
    for field in baseline.keys() - {"sourceScore", "featureInventory", "warnings"}:
        assert actual[field] == baseline[field], field


def test_bad_title_obeys_strict_inventory_mode():
    inventory = FeatureInventory()
    with pytest.raises(ScoreImportError, match="Unsupported Songsterr part field"):
        inventory.inspect({"title": False}, "Songsterr part", "$.parts[0]", strict=True)


@pytest.mark.parametrize("scope", ["Songsterr track", "Songsterr measure", "Songsterr voice", "Songsterr beat", "Songsterr note"])
def test_title_at_other_scopes_is_not_silenced(scope):
    inventory = FeatureInventory()
    inventory.inspect({"title": "Raw label"}, scope, "$.example")
    assert inventory.warnings() == [f"Unclassified {scope} field title is retained in source evidence only."]


@pytest.mark.parametrize("metadata_name", ["Current Guitar", ""])
def test_track_title_fallback_and_song_title_keep_their_existing_meaning(tmp_path, metadata_name):
    document = raw_score([measure(beat(3))])
    document["title"] = "Song title"
    document["tracks"][0].update(name=metadata_name, title="Metadata track title")
    document["parts"][0].update(name="Stale part name", title="Stale part title")
    original = deepcopy(document)
    actual = import_json(tmp_path, document)
    name = metadata_name or "Metadata track title"
    assert actual["title"] == "Song title"
    assert actual["tracks"][0]["name"] == name
    assert parse(document).tracks[0].name == name
    assert read_source(document).parts[0].name == name
    assert WARNING not in actual["warnings"]
    assert actual["sourceScore"]["document"] == original == document


def test_retention_does_not_hide_bad_titles_or_other_unknown_part_fields(tmp_path):
    document = document_with_excluded_part(True)
    document["parts"][0]["title"] = "Guitar"
    document["parts"][1].update(title=False, futureFeature=True)
    actual = import_json(tmp_path, document)
    assert WARNING in actual["warnings"]
    assert "Unclassified Songsterr part field futureFeature is retained in source evidence only." in actual["warnings"]
    entries = [row for row in actual["featureInventory"]
               if row["scope"] == "Songsterr part" and row["field"] == "title"]
    assert {row["handling"] for row in entries} == {"source", "unclassified"}

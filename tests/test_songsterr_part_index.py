"""Source part numbering is metadata, never a playback identity or limitation."""
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


WARNING = "Unclassified Songsterr part field index is retained in source evidence only."
MAX_SAFE_INTEGER = 9_007_199_254_740_991


def music(document):
    rendered = render(parse(document))
    reference = expected(read_source(document), {"offset": 0, "scale": 1})
    return rendered["tracks"], rendered["duration"], reference


def document_with_excluded_part(excluded):
    document = raw_score([measure(beat(3))])
    if excluded:
        document["tracks"].append({"id": 15, "name": "Keyboard", "instrumentId": 0})
        document["parts"].append(deepcopy(document["parts"][0]))
    return document


@pytest.mark.parametrize("value", [None, 0, 1, 500, 0.0, -0.0, 3.0,
                                   MAX_SAFE_INTEGER, float(MAX_SAFE_INTEGER)])
@pytest.mark.parametrize("excluded", [False, True])
def test_optional_number_retains_source_without_changing_music(tmp_path, value, excluded):
    document = document_with_excluded_part(excluded)
    baseline = import_json(tmp_path, document)
    baseline_music = music(document)
    baseline_compatibility = inspect_songsterr(document)
    document["parts"][int(excluded)]["index"] = value
    original = deepcopy(document)
    actual = import_json(tmp_path, document)
    assert actual["sourceScore"]["document"] == original == document
    assert music(document) == baseline_music
    assert inspect_songsterr(document) == baseline_compatibility
    for field in baseline.keys() - {"sourceScore", "featureInventory"}:
        assert actual[field] == baseline[field], field
    entries = [row for row in actual["featureInventory"]
               if row["scope"] == "Songsterr part" and row["field"] == "index"]
    assert len(entries) == 1
    assert entries[0]["handling"] == "source"
    assert entries[0]["representations"] == ["source"]
    assert entries[0]["examples"] == [f"$.parts[{int(excluded)}].index"]


def test_absence_adds_no_metadata_claim(tmp_path):
    document = raw_score([measure(beat())])
    actual = import_json(tmp_path, document)
    assert WARNING not in actual["warnings"]
    assert not any(row["scope"] == "Songsterr part" and row["field"] == "index"
                   for row in actual["featureInventory"])
    assert actual["sourceScore"]["document"] == document


@pytest.mark.parametrize("indices", [[0, 1, 2], [2, 1, 0], [0, 0, 0],
                                     [90, 80, 70], [None, 500, 500]])
@pytest.mark.parametrize("explicit_ids", [False, True])
def test_numbering_never_reorders_tracks_or_changes_identity(tmp_path, indices, explicit_ids):
    document = raw_score([measure(beat(3))])
    document["tracks"] = []
    document["parts"] = []
    for identity, fret in zip([50, 10, 30], [3, 7, 12]):
        fragment = raw_score([measure(beat(fret))])
        metadata = fragment["tracks"][0]
        metadata["name"] = f"Guitar at fret {fret}"
        if explicit_ids:
            metadata["id"] = identity
        else:
            del metadata["id"]
        document["tracks"].append(metadata)
        document["parts"].append(fragment["parts"][0])
    baseline = music(document)
    for part, index in zip(document["parts"], indices):
        part["index"] = index
    original = deepcopy(document)
    assert music(document) == baseline
    ids = ["50", "10", "30"] if explicit_ids else ["0", "1", "2"]
    assert [track.id for track in parse(document).tracks] == ids
    assert [part.id for part in read_source(document).parts] == ids
    assert [[note.fret for bar in track.bars for note in bar]
            for track in parse(document).tracks] == [[3], [7], [12]]
    actual = import_json(tmp_path, document)
    assert WARNING not in actual["warnings"]
    assert actual["sourceScore"]["document"] == original == document
    entry = next(row for row in actual["featureInventory"]
                 if row["scope"] == "Songsterr part" and row["field"] == "index")
    assert entry["count"] == 3
    assert entry["handling"] == "source"


@pytest.mark.parametrize("value", [False, True, -1, -0.5, 1.5, "0", "", [], {},
                                   [0], {"index": 0}, MAX_SAFE_INTEGER + 1,
                                   float(MAX_SAFE_INTEGER + 1), 10 ** 400])
@pytest.mark.parametrize("excluded", [False, True])
def test_unexpected_index_warns_without_changing_import(tmp_path, value, excluded):
    document = document_with_excluded_part(excluded)
    baseline = import_json(tmp_path, document)
    baseline_music = music(document)
    baseline_compatibility = inspect_songsterr(document)
    document["parts"][int(excluded)]["index"] = value
    original = deepcopy(document)
    actual = import_json(tmp_path, document)
    assert actual["warnings"] == [*baseline["warnings"], WARNING]
    assert actual["sourceScore"]["document"] == original == document
    assert music(document) == baseline_music
    assert inspect_songsterr(document) == baseline_compatibility
    for field in baseline.keys() - {"sourceScore", "featureInventory", "warnings"}:
        assert actual[field] == baseline[field], field


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_index_remains_unclassified(value):
    inventory = FeatureInventory()
    inventory.inspect({"index": value}, "Songsterr part", "$.parts[0]")
    assert inventory.warnings() == [WARNING]
    assert inventory.entries()[0]["handling"] == "unclassified"


def test_bad_index_obeys_strict_inventory_mode():
    inventory = FeatureInventory()
    with pytest.raises(ScoreImportError, match="Unsupported Songsterr part field"):
        inventory.inspect({"index": False}, "Songsterr part", "$.parts[0]", strict=True)


@pytest.mark.parametrize("scope", ["Songsterr track", "Songsterr voice", "Songsterr beat", "Songsterr note"])
def test_index_at_other_scopes_is_not_silenced(scope):
    inventory = FeatureInventory()
    inventory.inspect({"index": 0}, scope, "$.example")
    assert inventory.warnings() == [f"Unclassified {scope} field index is retained in source evidence only."]


def test_existing_measure_index_classification_is_unchanged():
    inventory = FeatureInventory()
    inventory.inspect({"index": 0}, "Songsterr measure", "$.measure")
    assert inventory.warnings() == []
    assert inventory.entries()[0]["handling"] == "source"
    with pytest.raises(ScoreImportError, match="index"):
        inventory.inspect({"index": False}, "Songsterr measure", "$.measure")


def test_unrelated_unknown_part_field_still_warns():
    inventory = FeatureInventory()
    inventory.inspect({"index": 0, "futureFeature": True}, "Songsterr part", "$.parts[0]")
    assert inventory.warnings() == ["Unclassified Songsterr part field futureFeature is retained in source evidence only."]

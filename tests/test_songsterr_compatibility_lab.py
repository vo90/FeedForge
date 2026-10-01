"""Fixtures come from a pinned external reference, never from converter snapshots."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.songsterr_compatibility.cases import cases
from tools.songsterr_compatibility.audit import (evaluate, compare_preparation,
    canonical_hash, load_manifest, minimize, compare_authored_events, comparison_status,
    classify_preparation_differences)
from tools.songsterr_compatibility.catalog import inventory

FIXTURE = Path(__file__).parent / "fixtures/songsterr_compatibility_reference.json"
EXPECTED = json.loads(FIXTURE.read_text())
CASES = list(cases())


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_preparation_against_pinned_external_reference(case):
    source = deepcopy(case["source"]); before = deepcopy(source)
    ref = EXPECTED["cases"][case["id"]]
    assert ref["sourceSha256"] == canonical_hash(source)
    result = evaluate(source)
    assert result["converter"]["status"] == ref["converterDisposition"]
    assert result["independent"]["status"] == ref["converterDisposition"]
    assert compare_preparation(result, ref["reference"]) == []
    assert compare_authored_events(result, ref["reference"], source) == []
    assert source == before


def test_known_omission_decision_is_visible_not_counted_as_supported():
    blocked = [r for r in EXPECTED["cases"].values() if r["converterDisposition"] == "blocked"]
    assert len(blocked) == 6
    assert all(r["decision"] == "strum_grace_consumed_attack" for r in blocked)


@pytest.mark.parametrize("mutation", ["timing", "missing", "duplicate", "traversal", "unavailable"])
def test_reference_comparison_detects_corruption(mutation):
    case = next(c for c in CASES if c['id'] == 'basic/attack')
    result = evaluate(case["source"])
    reference = deepcopy(EXPECTED["cases"][case["id"]]["reference"])
    part = reference["parts"][0]
    if mutation == "timing": part["beats"][0]["quarter"] += .01
    elif mutation == "missing": part["beats"].pop()
    elif mutation == "duplicate": part["beats"].append(deepcopy(part["beats"][0]))
    elif mutation == "traversal": part["traversal"].append(0)
    else: part["status"] = "reference_error"
    assert compare_preparation(result, reference)


@pytest.mark.parametrize('field', ['attackTick', 'endTick', 'string', 'fret', 'tie', 'id', 'occurrence', 'missing', 'extra'])
def test_authored_event_comparison_detects_note_corruption(field):
    case = next(c for c in CASES if c['id'] == 'basic/attack')
    result = evaluate(case['source'])
    reference = deepcopy(EXPECTED['cases'][case['id']]['reference'])
    events = reference['parts'][0]['authoredEvents']
    if field == 'missing': events.pop()
    elif field == 'extra': events.append(deepcopy(events[0]))
    elif field == 'id': events[0]['id'] = '0:0:0:99'
    elif field == 'tie': events[0]['tie'] = not events[0]['tie']
    else: events[0][field] += 1
    assert compare_authored_events(result, reference, case['source'])


def test_missing_reference_is_untested_not_a_match_or_musical_difference():
    differences = compare_preparation({}, {'parts': []})
    assert comparison_status(differences) == 'not_tested'
    assert comparison_status([{'code': 'converter_preparation_not_run'}]) == 'not_tested'
    assert comparison_status([{'code': 'duration'}]) == 'different'


def test_existing_whole_rest_policy_cannot_mask_note_timing_or_wrong_meter():
    from tools.songsterr_compatibility.cases import envelope, bar
    rest = {'type': 1, 'duration': [1, 1], 'rest': True, 'notes': [{'rest': True}]}
    source = envelope([bar(rest)]); source['parts'][0]['measures'][0]['signature'] = [6, 4]
    diff = {'code': 'duration', 'part': 0, 'beat': '0:0:0', 'converter': '6', 'reference': 4}
    assert classify_preparation_differences(source, [diff])[1] == []
    assert classify_preparation_differences(source, [{**diff, 'converter': '5'}])[0] == []
    source['parts'][0]['measures'][0]['voices'][0]['beats'][0]['notes'] = [{'fret': 5, 'string': 0}]
    assert classify_preparation_differences(source, [diff])[0] == []


def test_duplicate_converter_ids_are_never_silently_collapsed():
    case = next(c for c in CASES if c['id'] == 'basic/attack')
    result = evaluate(case['source']); reference = EXPECTED['cases'][case['id']]['reference']
    result['sourceTrace'][0]['beats'].append(deepcopy(result['sourceTrace'][0]['beats'][0]))
    assert any(d['code'] == 'duplicate_converter_beat' for d in compare_preparation(result, reference))


def test_corpus_rejects_changes_and_duplicate_identities(tmp_path):
    source = CASES[0]["source"]
    path = tmp_path / "source.json"; data = json.dumps(source).encode(); path.write_bytes(data)
    row = {"id":"a","source":str(path),"sha256":hashlib.sha256(data).hexdigest(),"songId":"1","revisionId":"1"}
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"rows":[row]}))
    assert list(load_manifest(manifest))[0][1] == source
    path.write_bytes(data + b" ")
    with pytest.raises(ValueError, match="changed"): list(load_manifest(manifest))
    path.write_bytes(data); manifest.write_text(json.dumps({"rows":[row,row]}))
    with pytest.raises(ValueError, match="Duplicate"): list(load_manifest(manifest))


def test_reducer_keeps_navigation_and_does_not_mutate_source():
    case = next(c for c in CASES if c["id"] == "repeats/two")
    before = deepcopy(case["source"])
    assert minimize(before, lambda _: True) == case["source"]
    assert before == case["source"]


def test_guard_inventory_never_hides_unclassified_guards():
    report = inventory()
    assert report["guards"]
    assert report["unclassifiedGuards"] == sum(r["classification"]=="unclassified" for r in report["guards"])
    assert report["deferred"]["source_player_repairs"]

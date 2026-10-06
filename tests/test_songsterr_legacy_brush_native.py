"""Native evidence for the explicitly normalized old-brush comparison scope."""
from copy import deepcopy
from fractions import Fraction as F
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.songsterr_compatibility.audit import (
    evaluate, compare_preparation, compare_authored_events, _strum_tick_adjustment,
    LEGACY_BRUSH_PROFILE, LEGACY_BRUSH_POLICY)
from tools.songsterr_compatibility.cases import cases, legacy_brush_cases
from tools.songsterr_compatibility.catalog import inventory
from tools.songsterr_compatibility.reports import compact_reference

FIXTURE = json.loads(Path(__file__).with_name("fixtures").joinpath("songsterr_legacy_brush_reference.json").read_text())
ROWS = FIXTURE["cases"]


@pytest.mark.parametrize("row", ROWS, ids=lambda r: r["id"])
def test_normalized_native_legacy_brush_schedules(row):
    source = deepcopy(row["source"]); before = deepcopy(source)
    actual = evaluate(source)
    disposition = row.get("expectedDisposition", "rendered")
    assert actual["converter"]["status"] == actual["independent"]["status"] == disposition, actual
    normalized = row["reference"][LEGACY_BRUSH_PROFILE]
    part = normalized["parts"][0]
    assert part["profile"] == LEGACY_BRUSH_PROFILE
    assert part["normalization"]["policy"] == LEGACY_BRUSH_POLICY
    assert row["reference"]["player-defaults"]["parts"][0]["authoredEvents"] == part["authoredEvents"]
    if disposition == "rendered":
        assert compare_preparation(actual, normalized) == []
        assert compare_authored_events(actual, normalized, source) == []
    else:
        # Native flooring can keep an exactly consumed rational member audible.
        # That does not authorize stretching or dropping the authored attack.
        beat = source["parts"][0]["measures"][0]["voices"][0]["beats"][0]
        assert F(*beat["duration"]) * 4 <= F(4, 13)
        assert "strum" in actual["converter"]["error"].lower()
    assert source == before


def test_profile_options_are_separate_and_deterministic():
    assert FIXTURE["profileOptions"]["authored"] == {
        "synth": "fluidsynth", "useRSE": False, "autoFixJson": False, "humanize": False}
    assert FIXTURE["profileOptions"][LEGACY_BRUSH_PROFILE] == {
        "synth": "fluidsynth", "useRSE": False, "autoFixJson": True, "humanize": False}
    assert FIXTURE["normalization"]["nativeStages"] == ["ro", "no"]
    assert len(list(cases())) == 184
    assert [r["id"] for r in legacy_brush_cases()] == [r["id"] for r in ROWS]


@pytest.mark.parametrize("field", ["upStroke", "downStroke"])
@pytest.mark.parametrize("value", range(1, 9))
def test_spacing_is_count_independent_and_value_one_is_corrected(field, value):
    step = F(4, 13 * 2**(8-value))
    for count in (2, 3, 6):
        row = next(r for r in ROWS if r["id"] == f"legacy-brush/{field}/{value}/{count}")
        actual = evaluate(row["source"])
        offsets = sorted(F(n["offsetQuarter"]) for n in actual["sourceTrace"][0]["notes"])
        assert offsets == [step * i for i in range(count)]
        # Raw authored direction stays distinct; the named profile is required.
        assert compare_authored_events(actual, row["reference"]["authored"], row["source"])


@pytest.mark.parametrize("field", ["upStroke", "downStroke"])
def test_rest_and_tie_members_occupy_native_slots(field):
    row = next(r for r in ROWS if r["id"] == f"legacy-brush/rest-slots/{field}")
    offsets = sorted(F(n["offsetQuarter"]) for n in evaluate(row["source"])["sourceTrace"][0]["notes"])
    assert offsets == [F(0), F(2, 13)]
    tie = next(r for r in ROWS if r["id"] == f"legacy-brush/tie-slots/{field}")
    final = tie["reference"][LEGACY_BRUSH_PROFILE]["parts"][0]["events"]
    assert sum(n["hidden"] for n in final) == 1
    assert len(tie["reference"][LEGACY_BRUSH_PROFILE]["parts"][0]["generatedEventTrace"]["scheduled"]) > 0


@pytest.mark.parametrize("change", ["half_tick", "whole_tick", "string", "missing"])
def test_independent_floor_adjustment_cannot_hide_event_corruption(change):
    row = next(r for r in ROWS if r["id"] == "legacy-brush/upStroke/3/6")
    reference = deepcopy(row["reference"][LEGACY_BRUSH_PROFILE])
    events = reference["parts"][0]["authoredEvents"]
    if change == "missing": events.pop()
    elif change == "string": events[0]["string"] = 5
    else: events[0]["attackTick"] += .5 if change == "half_tick" else 1
    assert compare_authored_events(evaluate(row["source"]), reference, row["source"])


def test_old_brush_floor_is_calculated_by_subdivision_and_raw_slot_rank():
    beat = {"upStroke": 3, "duration": [1, 4], "notes": [
        {"string": 5, "fret": 5}, {"string": 2, "rest": True}, {"string": 0, "fret": 5}]}
    # Q960 gives ideal step120/13 but native step9. Normalized upStroke starts
    # on the low string, and the rest keeps the high string at ordinal2.
    assert _strum_tick_adjustment(beat, 0, 960, LEGACY_BRUSH_PROFILE) == 0
    assert _strum_tick_adjustment(beat, 2, 960, LEGACY_BRUSH_PROFILE) == F(-6, 13)
    beat["notes"][1]["hp"] = True
    assert _strum_tick_adjustment(beat, 2, 960, LEGACY_BRUSH_PROFILE) == 0


def test_catalog_and_compact_evidence_retain_normalized_profile_policy():
    fields = {r["field"]: r for r in inventory()["fields"]}
    assert fields["beat.upStroke"]["rules"] == fields["beat.downStroke"]["rules"] == ["timing.strum_grace"]
    row = ROWS[0]
    evidence = compact_reference({"id": row["id"], **row["reference"][LEGACY_BRUSH_PROFILE]})
    assert evidence["parts"][0]["normalization"]["policy"] == LEGACY_BRUSH_POLICY


@pytest.mark.parametrize("change", ["profile", "policy", "missing_policy"])
def test_normalization_identity_cannot_be_misreported_as_raw_authored(change):
    row = ROWS[0]
    reference = deepcopy(row["reference"][LEGACY_BRUSH_PROFILE])
    actual = evaluate(row["source"])
    actual["referenceProfile"] = LEGACY_BRUSH_PROFILE
    if change == "profile": reference["parts"][0]["profile"] = "authored"
    elif change == "policy": reference["parts"][0]["normalization"]["policy"] = "other"
    else: reference["parts"][0].pop("normalization")
    assert any(d["code"] in {"reference_profile_mismatch", "reference_normalization_mismatch"}
               for d in compare_authored_events(actual, reference, row["source"]))

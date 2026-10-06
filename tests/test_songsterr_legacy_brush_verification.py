"""Independent legacy brush timing and source-bound archive checks."""

from copy import deepcopy
from fractions import Fraction as F
import json
from pathlib import Path
from zipfile import ZipFile

import pytest
import yaml

from test_song_import_score import beat, measure, raw_score
from feedback_converter.song_import import load_performance
from feedback_converter.song_import.builder import build_feedpak
from feedback_converter.song_import.evidence import CONTRACT_VERSION
from feedback_converter.song_import.verification import verify_import
from feedback_converter.song_import.verify_source import _songsterr_strum, songsterr
from feedback_converter.song_import.verify_timeline import expected


POLICY = "native-legacy-brush-v1"


def document(field="upStroke", value=1):
    chord = beat(fret=3, string=0)
    chord["notes"] += [{"string": 5, "fret": 5}, {"string": 2, "fret": 7}]
    chord[field] = value
    return raw_score([measure(chord)])


def compare(tmp_path, raw):
    original = deepcopy(raw)
    source = songsterr(raw)
    path = tmp_path / "source.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    performance = load_performance(path)
    checked = expected(source, {"offset": 0, "scale": 1})
    assert performance["strumEvidence"] == checked["strums"]
    track = performance["tracks"][0]
    actual = sorted(track["notes"] + [{**n, "t": c["t"]} for c in track["chords"] for n in c["notes"]],
                    key=lambda n: (n["t"], n["s"]))
    wanted = [n["note"] for n in checked["parts"][0]["notes"]]
    assert len(actual) == len(wanted)
    for got, want in zip(actual, wanted):
        assert got["t"] == pytest.approx(want["t"], abs=1e-6)
        assert got.get("sus", 0) == pytest.approx(want.get("sus", 0), abs=1e-6)
        assert (got["s"], got["f"]) == (want["s"], want["f"])
    assert raw == original
    return source, performance, checked


@pytest.mark.parametrize("field,direction", [("upStroke", "down"), ("downStroke", "up")])
@pytest.mark.parametrize("value", range(1, 9))
@pytest.mark.parametrize("version", [None, 5, 8])
def test_exact_subdivision_direction_and_unmodified_source(tmp_path, field, direction, value, version):
    raw = document(field, value)
    if version is not None:
        raw["parts"][0]["version"] = version
    source, performance, checked = compare(tmp_path, raw)
    step = F(4, 13 * 2 ** (8 - value))
    atoms = source.parts[0].bars[0]
    ranked = sorted(atoms, key=lambda atom: atom.string, reverse=direction == "up")
    assert [atom.attack_offset for atom in ranked] == [0, step, 2 * step]
    assert all(atom.strum_direction == direction and atom.strum_kind == "brush" for atom in atoms)
    assert source.legacy_brush_timing
    assert performance["source"]["legacyBrushTimingPolicy"] == POLICY
    group = checked["strums"][0]
    assert group["kind"] == "brush" and group["direction"] == direction
    assert sorted(n["t"] for n in group["notes"]) == [float(i * step / 2) for i in range(3)]


@pytest.mark.parametrize("field", ["upStroke", "downStroke"])
def test_rest_slot_consumes_its_sorted_rank(tmp_path, field):
    raw = document(field, 3)
    chord = raw["parts"][0]["measures"][0]["voices"][0]["beats"][0]
    chord["notes"][2] = {"string": 2, "rest": True}
    source, _, checked = compare(tmp_path, raw)
    step = F(1, 104)
    ranked = sorted(source.parts[0].bars[0], key=lambda atom: atom.string, reverse=field == "downStroke")
    assert [atom.attack_offset for atom in ranked] == [0, 2 * step]
    assert len(checked["strums"][0]["notes"]) == 2


def test_tie_slot_consumes_rank_without_new_attack(tmp_path):
    first = beat(fret=7, string=2, duration=(1, 4))
    second = beat(fret=3, string=0, duration=(1, 4))
    second["notes"] += [{"string": 2, "fret": 7, "tie": True}, {"string": 5, "fret": 5}]
    second["upStroke"] = 3
    raw = raw_score([measure(first, second)])
    source, performance, checked = compare(tmp_path, raw)
    atoms = source.parts[0].bars[0][1:]
    assert {a.string: a.attack_offset for a in atoms} == {5: F(1, 52), 3: F(1, 104), 0: 0}
    group = checked["strums"][0]
    assert [n["s"] for n in group["notes"]] == [0, 5]
    assert len(performance["tracks"][0]["notes"]) == 3


@pytest.mark.parametrize("modern,kind", [("brushStroke", "brush"), ("arpeggio", "arpeggio")])
@pytest.mark.parametrize("value", [1, 3, 8, 3.0])
def test_primary_modern_fields_supply_timing_direction_and_kind(tmp_path, modern, kind, value):
    raw = document("upStroke", value)
    chord = raw["parts"][0]["measures"][0]["voices"][0]["beats"][0]
    chord[modern] = {"direction": "up", "duration": 30, "shift": 50}
    chord["downArpeggio"] = 3
    source, performance, checked = compare(tmp_path, raw)
    assert not source.legacy_brush_timing
    assert "legacyBrushTimingPolicy" not in performance["source"]
    assert {a.attack_offset for a in source.parts[0].bars[0]} == {F(-1, 48), 0, F(1, 48)}
    assert all(a.strum_kind == kind and a.strum_direction == "up" for a in source.parts[0].bars[0])
    assert checked["strums"][0]["kind"] == kind


@pytest.mark.parametrize("value", [None, False, 0, 0.0])
def test_inactive_brush_values_do_not_claim_the_policy(tmp_path, value):
    raw = document(value=value)
    source = songsterr(raw)
    assert not source.legacy_brush_timing
    assert all(a.attack_offset == 0 and a.strum_direction is None for a in source.parts[0].bars[0])


def test_integral_float_is_a_scalar_subdivision(tmp_path):
    source, _, _ = compare(tmp_path, document(value=3.0))
    assert sorted(a.attack_offset for a in source.parts[0].bars[0]) == [0, F(1, 104), F(1, 52)]


@pytest.mark.parametrize("modern", [False, True])
@pytest.mark.parametrize("value", [True, "3", [], {}, [3, 1], 3.5, -1, 9, float("nan"), float("inf")])
def test_malformed_brush_values_are_rejected_independently(tmp_path, value, modern):
    raw = document(value=value)
    if modern:
        raw["parts"][0]["measures"][0]["voices"][0]["beats"][0]["brushStroke"] = {
            "direction": "down", "duration": 30, "shift": 100}
    with pytest.raises(ValueError):
        songsterr(raw)
    path = tmp_path / "source.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError):
        load_performance(path)


@pytest.mark.parametrize("fault", ["two_brushes", "old_arpeggio", "two_modern", "duplicate", "rest_duplicate",
                                   "rest_missing", "rest_negative", "rest_high", "rest_fractional"])
def test_conflicts_and_unresolved_raw_slots_stay_guarded(tmp_path, fault):
    raw = document(value=3)
    chord = raw["parts"][0]["measures"][0]["voices"][0]["beats"][0]
    if fault == "two_brushes":
        chord["downStroke"] = 3
    elif fault == "old_arpeggio":
        chord["upArpeggio"] = 3
    elif fault == "two_modern":
        chord.update({key: {"direction": "down", "duration": 30, "shift": 100}
                      for key in ("brushStroke", "arpeggio")})
    elif fault == "duplicate":
        chord["notes"][2]["string"] = 5
    else:
        rest = {"rest": True}
        slots = {"rest_duplicate": 5, "rest_negative": -1, "rest_high": 6, "rest_fractional": 2.5}
        if fault in slots:
            rest["string"] = slots[fault]
        chord["notes"][2] = rest
    with pytest.raises(ValueError):
        songsterr(raw)
    path = tmp_path / "source.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError):
        load_performance(path)


@pytest.mark.parametrize("effect", ["bend", "hp", "slide", "leftSlide", "rightSlide"])
def test_pitch_links_suppress_spreading_without_hiding_policy(effect):
    chord = document(value=6)["parts"][0]["measures"][0]["voices"][0]["beats"][0]
    chord["notes"][1][effect] = True
    assert _songsterr_strum(chord, "synthetic", string_count=6) == ({}, "down")


def test_nonpositive_sounding_interval_is_not_clipped(tmp_path):
    raw = document(value=8)
    raw["parts"][0]["measures"][0]["voices"][0]["beats"][0]["duration"] = [1, 64]
    source = songsterr(raw)
    with pytest.raises(ValueError):
        expected(source, {"offset": 0, "scale": 1})
    path = tmp_path / "source.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError):
        load_performance(path)


@pytest.mark.parametrize("fault", [None, "receipt_time", "receipt_direction", "note_time", "modern30",
                                   "missing_policy", "wrong_policy", "contract"])
def test_package_reconstructs_brush_and_rejects_tampering_or_downgrade(tmp_path, fault):
    from test_song_import_builder import inputs
    from feedback_converter.chart_guidance import finalize

    _, audio, _, job = inputs(tmp_path)
    raw = document(value=1)
    path = tmp_path / "source.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    performance = load_performance(path)
    alignment = {"status": "validated", "offset": 0, "scale": 1}
    built = build_feedpak(performance, audio, alignment, job, output_dir=tmp_path / "out", source_path=path,
                         compatibility=performance["compatibilityReport"],
                         recipe={"preservationContract": CONTRACT_VERSION})
    archive = Path(built["stagingPath"])
    with ZipFile(archive) as stream:
        files = {name: stream.read(name) for name in stream.namelist()}
    manifest = yaml.safe_load(files["manifest.yaml"])
    assert manifest["song_import"]["legacyBrushTimingPolicy"] == POLICY
    assert verify_import(path, archive, alignment)["status"] == "passed"
    if fault is None:
        return
    receipt = json.loads(files["import/strums.json"])
    chart_file = manifest["arrangements"][0]["file"]
    chart = json.loads(files[chart_file])
    if fault == "receipt_time":
        receipt["groups"][0]["notes"][1]["t"] += .01
    if fault == "receipt_direction":
        receipt["groups"][0]["direction"] = "up"
    if fault == "note_time":
        chart["notes"][1]["t"] += .01
    if fault == "modern30":
        # Forge mutually consistent chart/receipt timings for the former 30 ms
        # interpretation. Only independent raw-source reconstruction catches it.
        for rank, note in enumerate(sorted(chart["notes"], key=lambda n: n["s"])):
            note["t"] = round(float(F(rank * 30, 3 * 480 * 2)), 6)
            note["sus"] = round(2 - note["t"], 6)
        for rank, note in enumerate(receipt["groups"][0]["notes"]):
            note["t"] = round(float(F(rank * 30, 3 * 480 * 2)), 6)
    if fault in {"note_time", "modern30"}:
        finalize(chart, regenerate=True)
    if fault == "missing_policy":
        manifest["song_import"].pop("legacyBrushTimingPolicy")
    if fault == "wrong_policy":
        manifest["song_import"]["legacyBrushTimingPolicy"] = "modern-30ms"
    if fault == "contract":
        # Legacy brush interpretation starts at 89 independently of newer
        # preservation contracts for other source features.
        manifest["song_import"]["preservationContract"] = 88
    files["manifest.yaml"] = yaml.safe_dump(manifest).encode()
    files[chart_file] = json.dumps(chart).encode()
    files["import/strums.json"] = json.dumps(receipt).encode()
    changed = tmp_path / "changed.feedpak"
    with ZipFile(changed, "w") as stream:
        for name, data in files.items():
            stream.writestr(name, data)
    checked = verify_import(path, changed, alignment)
    assert checked["status"] == "failed", checked
    codes = {error["code"] for error in checked["errors"]}
    if fault == "contract":
        assert "legacy_brush_contract" in codes
    elif fault in {"missing_policy", "wrong_policy"}:
        assert "legacy_brush_policy" in codes
    elif fault == "receipt_direction":
        assert "strum_evidence" in codes
    elif fault in {"receipt_time", "modern30"}:
        assert "strum_time" in codes
        if fault == "modern30":
            assert "note_time" in codes


def test_bare_brush_policy_only_tracks_the_selected_playable_scope():
    raw = document(value=3)
    plain = deepcopy(raw["parts"][0])
    plain["measures"][0]["voices"][0]["beats"][0].pop("upStroke")
    raw["parts"].append(plain)
    raw["tracks"].append(dict(raw["tracks"][0], id=1))
    assert songsterr(raw).legacy_brush_timing
    assert not songsterr(raw, track_indices={1}).legacy_brush_timing


@pytest.mark.parametrize("shape", ["singleton", "link_suppressed"])
def test_bare_brush_policy_covers_suppressed_or_singleton_beats(shape):
    raw = document(value=3)
    chord = raw["parts"][0]["measures"][0]["voices"][0]["beats"][0]
    if shape == "singleton":
        chord["notes"] = chord["notes"][:1]
    else:
        chord["notes"][1]["hp"] = True
    source = songsterr(raw)
    assert source.legacy_brush_timing
    assert all(a.attack_offset == 0 for a in source.parts[0].bars[0])


@pytest.mark.parametrize("mapped", [False, True])
@pytest.mark.parametrize("tamper", [False, True])
def test_hybrid_borrowed_legacy_brush_keeps_exact_members_and_rejects_changed_attack(tmp_path, mapped, tamper):
    from test_song_import_builder import inputs
    from test_songsterr_hybrid_lead import prepared, song
    from feedback_converter.chart_guidance import finalize

    raw = song()
    chord = raw["parts"][1]["measures"][1]["voices"][0]["beats"][0]
    chord["notes"] += [{"string": 1, "fret": 7}, {"string": 2, "fret": 0}]
    chord["upStroke"] = 3
    path, performance, options = prepared(tmp_path, raw)
    _, audio, _, job = inputs(tmp_path)
    alignment = {"status": "validated", "offset": 0, "scale": 1}
    if mapped:
        alignment = {"status": "validated", "mapping": "piecewise-linear", "anchors": [
            {"score": 0, "audio": 0}, {"score": 2, "audio": 1.5}, {"score": 8, "audio": 8}],
            "tempos": [{"time": 0, "bpm": 160}, {"time": 1.5, "bpm": 120 / (6.5 / 6)}]}
    built = build_feedpak(performance, audio, alignment, job, output_dir=tmp_path / "out", source_path=path,
                         compatibility=performance["compatibilityReport"],
                         recipe={"preservationContract": CONTRACT_VERSION, "source": "songsterr",
                                 "scoreHash": options["sourceSha256"], "audioHash": audio["hash"], "hybridLead": options},
                         hybrid_lead={"enabled": True, "mainTrackId": options["mainTrackId"], "options": options})
    archive = Path(built["stagingPath"])
    assert verify_import(path, archive, alignment, hybrid_options=options)["status"] == "passed"
    with ZipFile(archive) as stream:
        files = {name: stream.read(name) for name in stream.namelist()}
    manifest = yaml.safe_load(files["manifest.yaml"])
    assert manifest["song_import"]["legacyBrushTimingPolicy"] == POLICY
    original = json.loads(files[manifest["arrangements"][1]["file"]])
    derived_file = manifest["arrangements"][-1]["file"]
    derived = json.loads(files[derived_file])
    members = [n for n in original["notes"] if "ch" in n]
    assert len(members) == 3 and len({n["t"] for n in members}) == 3
    assert all(n in derived["notes"] for n in members)
    receipt = json.loads(files["import/hybrid-lead.json"])
    assert any(p["trackId"] == "1" for p in receipt["passages"])
    if tamper:
        changed_note = next(n for n in derived["notes"] if n == members[1])
        changed_note["t"] += .01
        finalize(derived, regenerate=True)
        files[derived_file] = json.dumps(derived).encode()
        changed = tmp_path / "changed.feedpak"
        with ZipFile(changed, "w") as stream:
            for name, data in files.items():
                stream.writestr(name, data)
        checked = verify_import(path, changed, alignment, hybrid_options=options)
        assert checked["status"] == "failed", checked
        assert "hybrid_chart" in {error["code"] for error in checked["errors"]}

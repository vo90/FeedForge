"""Authored names/expressions stay distinct; unknown meanings still stop conversion."""
from copy import deepcopy

import pytest

from feedback_converter.song_import import ScoreImportError
from test_song_import_score import beat, import_json, measure, raw_score
from test_song_import_verification import example, verify


def test_same_shape_keeps_distinct_authored_labels_and_retained_annotations(tmp_path):
    chord = {"duration": [1, 4], "notes": [{"string": 0, "fret": 3}, {"string": 1, "fret": 5}],
             "chord": {"text": "C/G", "width": 40}}
    second = deepcopy(chord)
    second["chord"]["text"] = "Authored name"
    source = raw_score([measure(chord, second, doubleBarline=True)])
    source["parts"][0]["automations"]["tempo"][0]["text"] = "Moderate"
    result = import_json(tmp_path, source)
    track = result["tracks"][0]
    assert [t["name"] for t in track["templates"]] == ["C/G", "Authored name"]
    assert [c["id"] for c in track["chords"]] == [0, 1]
    assert result["sourceScore"]["document"] == source
    report = result["compatibilityReport"]
    assert report["status"] == "limitations"
    assert {f["feature"] for f in report["findings"]} == {"beat.chord", "measure.doubleBarline", "tempo.text"}


@pytest.mark.parametrize("direction,value", [("down", 0), ("up", 1)])
@pytest.mark.parametrize("vibrato,wide", [("slight", False), ("wide", True)])
def test_picking_and_left_hand_vibrato_have_known_values(tmp_path, direction, value, vibrato, wide):
    source = raw_score([measure({**beat(leftHandVibrato=vibrato), "pickStroke": direction})])
    result = import_json(tmp_path, source)
    note = result["tracks"][0]["notes"][0]
    assert note["vb"] and note["pkd"] == value
    written = result["tracks"][0]["notation"]["measures"][0]["staves"]["staff"]["voices"][0]["beats"][0]
    assert written["notes"][0]["vib"] and bool(written["notes"][0].get("vibw")) == wide


@pytest.mark.parametrize("field,value", [("pickStroke", "sideways"), ("chord", {"text": "C", "future": True}), ("wahwah", "future")])
def test_new_uninterpreted_values_are_not_silently_accepted(tmp_path, field, value):
    with pytest.raises(ScoreImportError):
        import_json(tmp_path, raw_score([measure({**beat(), field: value})]))


def test_legacy_brush_is_not_mistaken_for_pick_direction(tmp_path):
    result = import_json(tmp_path, raw_score([measure({**beat(), "upStroke": 1})]))
    assert result["tracks"][0]["notes"][0]["pkd"] == 0


def test_independent_verifier_detects_lost_pick_direction_and_vibrato(tmp_path):
    import json
    from pathlib import Path
    from zipfile import ZipFile
    import yaml
    from test_song_import_builder import inputs
    from feedback_converter.song_import.builder import build_feedpak
    source, package = example()
    source["parts"][0]["measures"][0]["voices"][0]["beats"][0]["pickStroke"] = "down"
    note = source["parts"][0]["measures"][0]["voices"][0]["beats"][0]["notes"][0]
    note["leftHandVibrato"] = "wide"
    package["chart.json"]["notes"][0].update(pkd=0, vb=True)
    package["chart.json"]["notes"][0]['vibrato_marks'] = [{'start':0, 'end':.5, 'intensity':'wide'}]
    package["notation.json"]["measures"][0]["staves"]["staff"]["voices"][0]["beats"][0]["notes"][0].update(vib=True, vibw=True)
    # Keep the current provenance envelope; compare the produced music to the
    # hand-calculated values before testing independent corruption detection.
    _,audio,_,job=inputs(tmp_path)
    p=import_json(tmp_path,source)
    result=build_feedpak(p,audio,{'status':'validated','offset':1,'scale':1},job,
        output_dir=tmp_path/'out',source_path=tmp_path/'score.json',compatibility=p['compatibilityReport'],
        recipe={'preservationContract':62})
    with ZipFile(Path(result['stagingPath'])) as z:
        current={name:(yaml.safe_load(z.read(name)) if name.endswith('.yaml') else json.loads(z.read(name)))
                 for name in z.namelist() if name.endswith(('.json','.yaml'))}
    cp=current['manifest.yaml']['arrangements'][0]['file']
    for actual, wanted in zip(current[cp]['notes'], package['chart.json']['notes']):
        for key, value in wanted.items():
            assert actual.get(key) == value, key
    report = verify(tmp_path, source, current)
    assert report["status"] == "passed", report
    del current[cp]["notes"][0]["pkd"]
    assert "note_technique" in {e["code"] for e in verify(tmp_path, source, current)["errors"]}


@pytest.mark.parametrize('fret,technique', [(7, 'ho'), (3, 'po')])
@pytest.mark.parametrize('shift', [0, 100])
def test_strummed_hopo_destination_keeps_written_technique_and_independent_verification(tmp_path, fret, technique, shift):
    from pathlib import Path
    from test_song_import_builder import inputs
    from feedback_converter.song_import.builder import build_feedpak
    from feedback_converter.song_import.verification import verify_import
    origin = {'duration': [1, 4], 'notes': [{'string': s, 'fret': 5, 'hp': True} for s in (5, 4, 3)]}
    destination = {'duration': [1, 4], 'brushStroke': {'direction': 'down', 'duration': 120, 'shift': shift},
                   'notes': [{'string': s, 'fret': fret} for s in (5, 4, 3)]}
    source = raw_score([measure(origin, destination)])
    performance = import_json(tmp_path, source)
    for m in performance['tracks'][0]['notation']['measures']:
        written = m['staves']['staff']['voices'][0]['beats'][1]
        assert all(n.get(technique) is True for n in written['notes'])
    _, audio, _, job = inputs(tmp_path)
    source_path = tmp_path / 'score.json'
    alignment = {'status': 'validated', 'offset': 0, 'scale': 1}
    built = build_feedpak(performance, audio, alignment, job, output_dir=tmp_path / 'out',
                         source_path=source_path, compatibility=performance['compatibilityReport'], recipe={'preservationContract': 17})
    checked = verify_import(source_path, Path(built['stagingPath']), alignment)
    assert checked['status'] == 'passed', checked['errors']

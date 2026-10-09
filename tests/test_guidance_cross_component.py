"""Optional integration against the paired Core and Editor checkouts.

Set GUIDANCE_CORE_ROOT and GUIDANCE_EDITOR_ROOT to run this with the actual
Songsterr score parser, FeedPak writer, Editor serializer and Core reader.
"""
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import subprocess
from zipfile import ZipFile

import pytest
import yaml

from feedback_converter.guidance_provenance import resolve
from test_songsterr_hybrid_lead import build


@pytest.mark.skipif(not os.getenv("GUIDANCE_CORE_ROOT") or not os.getenv("GUIDANCE_EDITOR_ROOT"),
                    reason="paired checkouts required")
def test_songsterr_package_editor_save_reload_preserves_guidance(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(os.environ["GUIDANCE_CORE_ROOT"])
    editor_root = Path(os.environ["GUIDANCE_EDITOR_ROOT"])
    monkeypatch.syspath_prepend(str(editor_root))
    from lib.song import arrangement_from_wire, arrangement_to_wire, phrase_to_wire
    shared = Path(os.environ["GUIDANCE_CORE_ROOT"]) / "lib/guidance_provenance.py"
    assert shared.read_bytes() == (editor_root / "guidance_provenance.py").read_bytes()
    assert shared.read_bytes() == (Path(__file__).parents[1] / "src/feedback_converter/guidance_provenance.py").read_bytes()
    *_, archive, report = build(tmp_path, difficulty=True)
    assert report["status"] == "passed"
    with ZipFile(archive) as pack:
        manifest = yaml.safe_load(pack.read("manifest.yaml"))
        charts = [json.loads(pack.read(a["file"])) for a in manifest["arrangements"] if a.get("file")]
    for chart in charts:
        before = deepcopy(chart)
        original_file = tmp_path / "editor-input.json"
        original_file.write_text(json.dumps(chart), encoding="utf-8")
        script = '''import json, os, sys
sys.path[:0] = [os.environ["GUIDANCE_CORE_ROOT"], os.environ["GUIDANCE_EDITOR_ROOT"]]
import routes as editor
from lib.song import arrangement_from_wire
with open(sys.argv[1], encoding="utf-8") as f: chart=json.load(f)
view=editor._arr_to_data(arrangement_from_wire(chart), chart["name"])
saved=editor._arr_dict_to_wire(view["name"], view["tuning"], view["capo"],
    view["notes"], view["chords"], view["chord_templates"], anchors_user=view["anchors_user"],
    handshapes=view["handshapes"], original=chart)
editor._preserve_phrase_guidance(chart, saved, chart["phrases"])
print(json.dumps(saved))
'''
        child = subprocess.run([os.getenv("GUIDANCE_EDITOR_PYTHON", sys.executable), "-c", script, str(original_file)],
            text=True, capture_output=True, timeout=30, check=True)
        saved = json.loads(child.stdout)
        for field in ("notes", "chords", "templates", "anchors", "handshapes", "ext", "phrases"):
            assert saved[field] == before[field], field
        loaded = arrangement_from_wire(json.loads(json.dumps(saved)))
        state = resolve(arrangement_to_wire(loaded), "anchors")
        assert state["integrity"] == "valid"
        assert state["applicability"] == "current"
        assert all(a.guidance_origin == "generated" for a in loaded.anchors)
        for phrase in loaded.phrases:
            for level in phrase_to_wire(phrase, playback=True)["levels"]:
                assert all(a["guidanceOrigin"] == "generated" for a in level["anchors"])
        assert chart == before

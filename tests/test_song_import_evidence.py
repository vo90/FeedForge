import hashlib
import json
import shutil
import zipfile

import pytest

from feedback_converter.song_import.evidence import save_evidence


def test_evidence_is_durable_content_addressed_and_exportable(tmp_path):
    cache = tmp_path / "job-cache"
    cache.mkdir()
    score = cache / "score.json"
    score.write_bytes(b'{"original": "score bytes"}')
    archive = cache / "result.feedpak"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("manifest.yaml", "title: Original")
    args = dict(score=score, metadata={"title": "Original", "songId": 12, "cookie": "secret", "path": "private"},
                performance={"tracks": [{"notes": [{"source_ids": ["source:1"], "f": 0}]}]},
                synchronization={"points": [0, 2], "revisionId": 34},
                alignment={"anchors": [{"score": 0, "audio": 1}, {"score": 2, "audio": 3}]},
                verification={"version": 3, "status": "passed"}, archive=archive)
    root = tmp_path / "evidence"
    first = save_evidence(root, **args)
    assert first == save_evidence(root, **args)
    record_file = root / "records" / (first["id"] + ".json")
    assert hashlib.sha256(record_file.read_bytes()).hexdigest() == first["id"]
    record = json.loads(record_file.read_text())
    assert record["version"] == first["version"] == 37
    assert record["sourceMetadata"] == {"title": "Original", "songId": 12}
    assert record["members"]["manifest.yaml"] == hashlib.sha256(b"title: Original").hexdigest()
    shutil.rmtree(cache)
    with zipfile.ZipFile(root / "records" / (first["id"] + ".zip")) as bundle:
        assert json.loads(bundle.read("report.json")) == record
        for digest in record["objects"].values():
            assert hashlib.sha256(bundle.read("objects/" + digest)).hexdigest() == digest
    assert not list(root.rglob("*.tmp"))


def test_changed_evidence_is_not_overwritten(tmp_path):
    score = tmp_path / "score.json"
    score.write_bytes(b"source")
    root = tmp_path / "evidence"
    args = dict(score=score, metadata={}, performance=None, synchronization=None, alignment=None,
                verification={"version": 3, "status": "incomplete"})
    result = save_evidence(root, **args)
    original = root / "objects" / result["sourceHash"]
    original.write_bytes(b"changed")
    with pytest.raises(ValueError, match="evidence has changed"):
        save_evidence(root, **args)
    assert original.read_bytes() == b"changed"

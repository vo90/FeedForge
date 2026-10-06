"""Immutable, content-addressed import evidence outside disposable job caches.

No account state or audio recordings are stored here. Bundles contain the source
score, applied timing, source correspondence and checks for the original output.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import uuid
import zipfile

CONTRACT_VERSION = 93
MAX_OBJECT_BYTES = 128 * 1024 * 1024


def _json(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")


def _immutable(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.is_symlink() or path.read_bytes() != data:
                raise ValueError("Saved conversion evidence has changed.")
    finally:
        temporary.unlink(missing_ok=True)


def save_evidence(root: Path, *, score: Path, metadata: dict, performance: dict | None,
                  synchronization: dict | None, alignment: dict | None,
                  verification: dict, archive: Path | None = None, artwork: dict | None = None,
                  compatibility: dict | None = None) -> dict:
    root = Path(root)
    objects: dict[str, bytes] = {}

    def add(data: bytes) -> str:
        if len(data) > MAX_OBJECT_BYTES:
            raise ValueError("Conversion evidence exceeds the supported size.")
        digest = hashlib.sha256(data).hexdigest()
        _immutable(root / "objects" / digest, data)
        objects[digest] = data
        return digest

    refs = {"source": add(score.read_bytes()), "verification": add(_json(verification))}
    if compatibility:
        refs["compatibility"] = add(_json(compatibility))
    if performance:
        # Original bytes are already retained verbatim; avoid a second copy of
        # the source envelope while keeping event lineage and coverage.
        refs["performance"] = add(_json({k: v for k, v in performance.items() if k != "sourceScore"}))
    if synchronization:
        refs["sourceSynchronization"] = add(_json(synchronization))
    if alignment:
        refs["appliedAlignment"] = add(_json(alignment))
    members, output_hash = {}, None
    if archive:
        output_hash = hashlib.sha256(archive.read_bytes()).hexdigest()
        with zipfile.ZipFile(archive) as package:
            members = {name: hashlib.sha256(package.read(name)).hexdigest()
                       for name in sorted(package.namelist()) if not name.endswith("/")}
    record = {"version": CONTRACT_VERSION, "sourceFormat": score.suffix.lower().lstrip("."),
              "sourceMetadata": {k: metadata[k] for k in ("songId", "revisionId", "approval", "revisionEvidence", "title", "artist", "album", "year") if k in metadata},
              "objects": refs, "outputHash": output_hash, "members": members,
              "artwork": {k: v for k, v in (artwork or {}).items() if k != "path"}}
    encoded = _json(record)
    digest = hashlib.sha256(encoded).hexdigest()
    _immutable(root / "records" / (digest + ".json"), encoded)
    # The portable report is generated before publication, and survives pruning
    # of both the temporary conversion and its source acquisition directory.
    import io
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name, data in [("report.json", encoded), *[("objects/" + key, data) for key, data in sorted(objects.items())]]:
            entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o100600 << 16
            bundle.writestr(entry, data)
    bundle_data = buffer.getvalue()
    _immutable(root / "records" / (digest + ".zip"), bundle_data)
    return {"version": CONTRACT_VERSION, "id": digest, "bundleHash": hashlib.sha256(bundle_data).hexdigest(),
            "outputHash": output_hash, "sourceHash": refs["source"], "verificationHash": refs["verification"]}

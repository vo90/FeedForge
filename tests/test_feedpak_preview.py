from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import yaml

from feedback_converter import feedpak


def test_feedpak_metadata_edit_preserves_browser_preview(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source-package"
    source.mkdir()
    preview_bytes = b"OggS" + b"preview" * 200
    (source / "preview.ogg").write_bytes(preview_bytes)
    (source / "manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "feedpak_version": "1.14.0",
                "title": "Original title",
                "artist": "Artist",
                "duration": 120.0,
                "arrangements": [],
                "stems": [],
                "preview": "preview.ogg",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        feedpak,
        "require_valid_feedpak",
        lambda _path: SimpleNamespace(warnings=[], ok=True),
    )
    target = tmp_path / "edited-package"

    result = feedpak.update_feedpak(
        source,
        target,
        metadata={"title": "Edited title"},
    )

    manifest = yaml.safe_load((target / "manifest.yaml").read_text(encoding="utf-8"))
    assert result.output_path == target
    assert manifest["title"] == "Edited title"
    assert manifest["preview"] == "preview.ogg"
    assert (target / "preview.ogg").read_bytes() == preview_bytes

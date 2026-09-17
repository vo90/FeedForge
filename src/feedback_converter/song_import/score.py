"""Stable import boundary used by FeedForge's conversion worker."""

import json
from pathlib import Path

from .model import ScoreImportError
from .timeline import render


def load_performance(path: Path | str, metadata: dict | None = None) -> dict:
    """Read a GP7/8 score or Songsterr JSON and render its performed timeline.

    ``metadata`` comes from the original approved Songsterr revision and takes
    precedence over editor-copy metadata (which can rename artist/title).
    No network requests or game runtime imports are made.
    """
    path = Path(path)
    if path.stat().st_size > 80 * 1024 * 1024:
        raise ScoreImportError("Score input exceeds the import size limit.")
    if path.suffix.lower() == ".json":
        from .songsterr import parse
        try:
            document = json.loads(path.read_text(encoding="utf-8-sig"))
        except (ValueError, UnicodeError) as exc:
            raise ScoreImportError("Invalid Songsterr score JSON.") from exc
        score = parse(document)
    elif path.suffix.lower() in {".gp", ".gpif", ".xml"}:
        from .gpif import parse
        score = parse(path)
    else:
        raise ScoreImportError("Expected a GP7/GP8 .gp file or Songsterr .json score.")
    if metadata:
        for field in ("title", "artist", "album", "year"):
            if field in metadata and metadata[field] is not None:
                setattr(score, field, str(metadata[field]).strip())
        score.source.update({key: metadata[key] for key in
                             ("songId", "revisionId", "url", "approved", "approval", "provider") if key in metadata})
    return render(score)

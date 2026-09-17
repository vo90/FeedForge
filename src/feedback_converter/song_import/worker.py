"""File-based, staged-only Songsterr import worker shared by desktop and CLI."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile

from .alignment import VERSION, align_audio
from .audio import ImportFailure, prepare_audio, sha256_file
from .builder import build_feedpak


def run_import(request: dict, progress=None) -> dict:
    job = None
    try:
        if not isinstance(request, dict):
            raise ImportFailure("unsupported_score", "The import request must be an object.")
        score = Path(str(request.get("scorePath") or ""))
        if not score.is_file():
            raise ImportFailure("unsupported_score", "The exported Guitar Pro file does not exist.")
        if not request.get("workDir") or not request.get("outputDir"):
            raise ImportFailure("unsupported_score", "The import workspace and FeedForge output folder are required.")
        work = Path(str(request["workDir"])).resolve()
        work.mkdir(parents=True, exist_ok=True)
        job = Path(tempfile.mkdtemp(prefix="song-import-", dir=work))
        if progress:
            progress({"stage": "converting", "message": "Reading the exported tab and expanding its performed timeline."})
        from .score import load_performance
        performance = load_performance(score, metadata=request.get("metadata") or {})
        metadata = request.get("metadata") or {}
        # The original selected artist/title override metadata of an editor copy.
        for key in ("title", "artist", "album", "year"):
            if metadata.get(key):
                performance[key] = metadata[key]
        score_hash = sha256_file(score)
        if progress:
            progress({"stage": "audio", "message": "Preparing the full recording and song preview."})
        from .runtime import resolve_tools
        audio = prepare_audio(request.get("audio"), job, tools=resolve_tools(request.get("tools") or {}))
        alignment = align_audio(performance, Path(audio["path"]), progress=progress)
        recipe = {"version": 1, "source": "songsterr", "songId": metadata.get("songId"),
                  "revisionId": metadata.get("revisionId"), "scoreHash": score_hash, "audioHash": audio["hash"],
                  "sourceMetadata": dict(performance.get("source") or {}),
                  "audioSource": {key: audio["source"][key] for key in ("kind", "videoId", "title", "sha256") if key in audio["source"]},
                  "alignment": {"method": VERSION, "offset": alignment["offset"], "scale": alignment["scale"]}}
        if progress:
            progress({"stage": "validating", "message": "Building and validating the FeedPak."})
        result = build_feedpak(performance, audio, alignment, job, output_dir=Path(request["outputDir"]),
                               output_settings=request.get("outputSettings"), recipe=recipe)
        return {"ok": True, **result, "scoreHash": score_hash, "audioHash": audio["hash"],
                "recipe": recipe, "alignment": alignment,
                "warnings": list(performance.get("warnings", [])) + result["warnings"] +
                            ["Songsterr audio matching is experimental; this recording passed the current automatic checks."]}
    except ImportFailure as exc:
        return {"ok": False, "code": exc.code, "error": str(exc), **({"alignment": exc.diagnostics} if exc.diagnostics else {})}
    except ImportError as exc:
        return {"ok": False, "code": "dependency_missing", "error": f"Song import needs an unavailable component: {exc.name or 'unknown'}."}
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return {"ok": False, "code": "unsupported_score", "error": str(exc)}


def run_request_file(path: Path, progress=None) -> dict:
    try:
        if Path(path).stat().st_size > 1024 * 1024:
            raise ValueError("Import request is too large.")
        request = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"ok": False, "code": "unsupported_score", "error": str(exc)}
    return run_import(request, progress=progress)

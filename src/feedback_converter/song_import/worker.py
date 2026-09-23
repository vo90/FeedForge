"""File-based, staged-only Songsterr import worker shared by desktop and CLI."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile

from .alignment import VERSION, align_audio
from .audio import ImportFailure, prepare_audio, sha256_file
from .builder import build_feedpak
from .evidence import CONTRACT_VERSION, save_evidence
from .model import ScoreImportError
from .compatibility import summary as compatibility_summary, new_report, add_finding
from .synchronization import align_from_songsterr


def _choose_alignment(performance: dict, audio: dict, request: dict, progress=None) -> dict:
    """Use recording-specific source timing first; estimate only when unavailable."""
    if progress:
        progress({"stage": "aligning", "message": "Checking Songsterr's recording timing."})
    try:
        return align_from_songsterr(performance, audio, request.get("synchronization"),
                                   request.get("metadata") or {})
    except ImportFailure as exc:
        if exc.code != "source_sync_unavailable":
            raise
        source_diagnostic = {**exc.diagnostics, "message": str(exc)}
    if progress:
        progress({"stage": "aligning", "message": "Songsterr timing is unavailable for this recording. Matching audio automatically."})
    try:
        alignment = align_audio(performance, Path(audio["path"]), progress=progress)
    except ImportFailure as exc:
        # Keep the failed source-map check alongside the independent matcher's
        # diagnostics. A fallback failure does not prove the site's sync is bad.
        exc.diagnostics = {**exc.diagnostics, "sourceSynchronization": source_diagnostic}
        raise
    alignment["sourceSynchronization"] = source_diagnostic
    return alignment


def _alignment_summary(alignment: dict) -> dict:
    # Full timing survives in the evidence store, not the bounded history ledger.
    return {key: value for key, value in alignment.items() if key not in {"anchors", "tempos"}}


def run_import(request: dict, progress=None) -> dict:
    job = None
    performance = alignment = None
    artwork = {"status": "unavailable", "message": "Album artwork was not looked up."}
    audit_root = None
    compatibility = None
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
        audit_root = Path(request.get("auditDir") or work.parent / "song-import-evidence").resolve()
        job = Path(tempfile.mkdtemp(prefix="song-import-", dir=work))
        if progress:
            progress({"stage": "converting", "message": "Reading the exported tab and expanding its performed timeline."})
        from .score import load_performance
        performance = load_performance(score, metadata=request.get("metadata") or {})
        compatibility = performance.get("compatibilityReport") or new_report(request.get("metadata"))
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
        alignment = _choose_alignment(performance, audio, request, progress)
        alignment_recipe = {key: alignment[key] for key in
                            ("method", "offset", "scale", "mapping", "provenance") if key in alignment}
        alignment_recipe.setdefault("method", VERSION)
        recipe = {"version": 4, "preservationContract": CONTRACT_VERSION, "source": "songsterr", "songId": metadata.get("songId"),
                  "revisionId": metadata.get("revisionId"), "scoreHash": score_hash, "audioHash": audio["hash"],
                  "sourceMetadata": dict(performance.get("source") or {}),
                  "audioSource": {key: audio["source"][key] for key in ("kind", "videoId", "title", "sha256") if key in audio["source"]},
                  "alignment": alignment_recipe}
        features = set()
        for track in performance.get("tracks", []):
            notes = list(track.get("notes", [])) + [note for chord in track.get("chords", []) for note in chord.get("notes", [])]
            features.update(key for key in ("ghost", "slide_out", "slide_out_marks", "slide_in_marks") if any(note.get(key) for note in notes))
        recipe["compatibility"] = {"version": 3, "extensions": sorted(features),
                                   "status": "requires_consumer_support" if features else "standard_fields"}
        if request.get("artworkLookup", True):
            if progress:
                progress({"stage": "converting", "message": "Finding the album cover."})
            from .artwork import resolve_album_art
            artwork = resolve_album_art({**metadata, "title": performance["title"], "artist": performance["artist"],
                                         "album": performance.get("album"), "duration": audio["duration"],
                                         "audioKind": audio["source"].get("kind")}, job / "artwork",
                                        cache_dir=Path(request.get("artworkCacheDir") or audit_root.parent / "artwork-cache"))
        recipe["artwork"] = {key: artwork[key] for key in ("status", "album", "year", "provenance", "reason") if key in artwork}
        if progress:
            progress({"stage": "validating", "message": "Building and validating the FeedPak."})
        result = build_feedpak(performance, audio, alignment, job, output_dir=Path(request["outputDir"]),
                               output_settings=request.get("outputSettings"), recipe=recipe, artwork=artwork,
                               source_path=score, compatibility=compatibility)
        if progress:
            progress({"stage": "validating", "message": "Comparing the completed FeedPak with the original tab."})
        from .verification import verify_import
        verification = verify_import(score, Path(result["stagingPath"]), alignment, metadata=metadata)
        verification["consumerCompatibility"] = recipe["compatibility"]
        if verification.get("status") != "passed":
            for finding in verification.get("unsupported", []) + verification.get("errors", []):
                add_finding(compatibility, feature="verification." + finding.get("code", "unsupported"),
                            category="converter_gap" if verification.get("status") == "unsupported" else "conversion_check",
                            impact="blocking", message=finding["message"], location=finding.get("location", "source"))
        evidence = save_evidence(audit_root, score=score, metadata=metadata, performance=performance,
                                 synchronization=request.get("synchronization"), alignment=alignment,
                                 verification=verification, archive=Path(result["stagingPath"]), artwork=artwork, compatibility=compatibility)
        summary = {key: verification[key] for key in ("version", "status", "counts") if key in verification}
        summary["outputHash"] = evidence["outputHash"]
        summary["timing"] = "source_map" if alignment.get("method") == "songsterr-video-points-v1" else "estimated"
        summary["compatibility"] = recipe["compatibility"]
        if (request.get("outputSettings") or {}).get("generateDifficulty") is True:
            summary["generatedDifficulty"] = {"requested": True, "sourceAuthored": False,
                                               "verificationScope": "full-source-chart"}
        if verification.get("status") != "passed":
            unsupported = verification.get("status") == "unsupported"
            return {"ok": False, "code": "needs_attention" if unsupported else "verification_failed",
                    "error": "This tab contains information that cannot yet be checked faithfully." if unsupported else
                             "The converted FeedPak differs from the source tab. It has not been saved to your song folder.",
                    "verification": summary, "evidence": evidence, "compatibility": compatibility_summary(compatibility),
                    "warnings": (verification.get("errors", []) + verification.get("unsupported", []))[:10]}
        return {"ok": True, **result, "scoreHash": score_hash, "audioHash": audio["hash"],
                "recipe": recipe, "alignment": _alignment_summary(alignment),
                "verification": summary, "evidence": evidence, "compatibility": compatibility_summary(compatibility),
                "artwork": {key: artwork[key] for key in ("status", "album", "year", "message", "reason") if key in artwork},
                "warnings": list(performance.get("warnings", [])) + result["warnings"] +
                            (["Practice difficulty was requested. Source verification covers the full chart; generated levels are not source-authored."]
                             if (request.get("outputSettings") or {}).get("generateDifficulty") is True else []) +
                            (["Timing was imported from Songsterr for this tab revision and recording."]
                             if alignment.get("method") == "songsterr-video-points-v1" else
                             ["Songsterr audio matching is experimental; this recording passed the current automatic checks."])}
    except ImportFailure as exc:
        failed = {"ok": False, "code": exc.code, "error": str(exc), **({"alignment": exc.diagnostics} if exc.diagnostics else {})}
    except ImportError as exc:
        failed = {"ok": False, "code": "dependency_missing", "error": f"Song import needs an unavailable component: {exc.name or 'unknown'}."}
    except ScoreImportError as exc:
        failed = {"ok": False, "code": "needs_attention", "error": str(exc)}
        compatibility = getattr(exc, "compatibility", None) or compatibility
    except (OSError, ValueError, TypeError, KeyError) as exc:
        failed = {"ok": False, "code": "unsupported_score", "error": str(exc)}
    if audit_root and score.is_file():
        try:
            if compatibility is None:
                compatibility = new_report(request.get("metadata"))
                add_finding(compatibility, feature="import_check", category="conversion_check", impact="blocking", message=failed["error"])
            failed["compatibility"] = compatibility_summary(compatibility)
            failed["evidence"] = save_evidence(audit_root, score=score, metadata=request.get("metadata") or {},
                                               performance=performance, synchronization=request.get("synchronization"), alignment=alignment,
                                               verification={"version": CONTRACT_VERSION, "status": "incomplete", "code": failed["code"]},
                                               compatibility=compatibility)
        except (OSError, ValueError, TypeError):
            pass  # The original failure remains actionable; no output is published.
    return failed


def run_request_file(path: Path, progress=None) -> dict:
    try:
        if Path(path).stat().st_size > 1024 * 1024:
            raise ValueError("Import request is too large.")
        request = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"ok": False, "code": "unsupported_score", "error": str(exc)}
    return run_import(request, progress=progress)

"""Per-arrangement diagnosis without changing source metadata or publishing parts."""
import re

from .compatibility import add_finding, inspect_songsterr
from .model import ScoreImportError
from .songsterr import _instrument, parse
from .timeline import render


def diagnose_arrangements(document, report):
    """A score-ready part is not yet an aligned or verified FeedPak."""
    report["publicationPolicy"] = "all_requested_arrangements"
    report["arrangements"] = []
    metadata = document.get("tracks", []) if isinstance(document, dict) else []
    if not isinstance(metadata, list):
        return
    for index, meta in enumerate(metadata):
        if not isinstance(meta, dict) or not _instrument(meta):
            continue
        row = {"trackIndex": index, "trackId": str(meta.get("id", index)),
               "name": str(meta.get("name") or meta.get("title") or f"Track {index + 1}"),
               "status": "blocked", "stage": "compatibility"}
        report["arrangements"].append(row)
        preflight = inspect_songsterr(document, track_indices={index})
        row["blockingFeatures"] = sorted({f["feature"] for f in preflight["findings"] if f["impact"] == "blocking"})
        if preflight["status"] == "blocked":
            continue
        try:
            row["stage"] = "parse"
            score = parse(document, track_indices={index})
            row["stage"] = "timeline"
            performance = render(score)
            row["status"] = "score_ready"
            row["noteCount"] = sum(len(t["notes"]) + sum(len(c["notes"]) for c in t["chords"]) for t in performance["tracks"])
        except (ScoreImportError, ValueError, TypeError, KeyError, IndexError) as exc:
            row["message"] = str(exc)[:1000]
            feature = getattr(exc, 'source_feature', "arrangement." + row["stage"])
            row["blockingFeatures"] = [feature]
            measure = re.search(r"measure (\d+)", str(exc), re.I)
            coordinates = {"trackIndex": index, "trackId": row["trackId"], "arrangement": row["name"]}
            if measure:
                coordinates["measure"] = int(measure[1])
            coordinates.update(getattr(exc, "source_location", {}))
            location = coordinates.pop("location", f"parts/{index}")
            add_finding(report, feature=feature, category="game_representation" if feature == 'arrangement.simultaneous_voices' else "source_interpretation", impact="blocking",
                        message=row["message"], location=location, value=getattr(exc, 'source_value', None), **coordinates)
    report["arrangementSummary"] = {"requested": len(report["arrangements"]),
        "scoreReady": sum(r["status"] == "score_ready" for r in report["arrangements"]),
        "packageVerified": False}

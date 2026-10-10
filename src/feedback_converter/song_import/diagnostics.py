"""Per-arrangement diagnosis without changing source metadata or publishing parts."""
import re
from dataclasses import replace

from .compatibility import add_finding, inspect_songsterr
from .model import ScoreImportError
from .songsterr import _instrument, parse
from .timeline import render


def diagnose_arrangements(document, report, *, parsed_score=None):
    """A score-ready part is not yet an aligned or verified FeedPak.

    ``parsed_score`` is the successful complete Songsterr parse of this exact
    document. Each diagnostic still renders its own selected tracks; the
    shared parsed source is read-only and is never used by the verifier.
    """
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
            if parsed_score is None:
                score = parse(document, track_indices={index})
            else:
                # Keep the complete source's meter, tempos and repeat context.
                # Source indices remain unambiguous even with duplicate IDs,
                # which the complete render still checks before publication.
                selected = [track for track in parsed_score.tracks
                            if track.tone_source['sourcePartIndex'] == index]
                score = replace(parsed_score, tracks=selected)
            row["stage"] = "timeline"
            performance = render(score)
            row["status"] = "score_ready"
            row["noteCount"] = sum(len(t["notes"]) + sum(len(c["notes"]) for c in t["chords"]) for t in performance["tracks"])
            if performance.get('voiceProjection'):
                row['generatedArrangements']=[{'id':t['id'],'name':t['name']} for t in performance['tracks']]
                row['message']=' '.join(w for w in performance['warnings'] if w.startswith('Source arrangement '))
            from .high_frets import project, summary
            _, omissions = project(performance)
            if omissions["notes"]:
                row["omissions"] = summary(omissions)
                row["message"] = f"{len(omissions['notes'])} high-fret or connected slide events will be omitted from gameplay and scoring."
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

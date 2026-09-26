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
        from .compatibility import inspect_songsterr, add_finding
        report = inspect_songsterr(document)
        capability_blocked = report["status"] == "blocked"
        from .diagnostics import diagnose_arrangements
        diagnose_arrangements(document, report)
        try:
            if capability_blocked:
                examples = ", ".join(dict.fromkeys(row["feature"] for row in report["findings"] if row["impact"] == "blocking"))
                raise ScoreImportError("Conversion needs support for " + examples[:600] + ". See compatibility details.")
            score = parse(document)
        except ScoreImportError as exc:
            if report["status"] != "blocked":
                add_finding(report, feature="interpretation", category="conversion_check", impact="blocking", message=str(exc))
            exc.compatibility = report
            raise
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
    try:
        performance = render(score)
    except ScoreImportError as exc:
        if path.suffix.lower() == ".json":
            add_finding(report, feature="timeline", category="conversion_check", impact="blocking", message=str(exc))
            exc.compatibility = report
        raise
    if path.suffix.lower() == ".json":
        from .tied_harmonics import report_findings
        report_findings(performance, report)
        from .tied_mutes import report_findings as report_tied_mutes
        report_tied_mutes(performance, report)
        from .high_frets import project, summary as omission_summary
        _, omissions = project(performance)
        if omissions["notes"]:
            report["omissions"] = omission_summary(omissions)
        for track in performance["tracks"]:
            if "notation" not in track:
                source_track = next(t for t in score.tracks if t.id == track["id"])
                reasons = []
                if any(n.pick_scrape or n.fret == 127 and n.effects.get("mt") is True for bar in source_track.bars for n in bar):
                    reasons.append(("unpitched_mute", "Unpitched mutes have no MIDI pitch for standard notation."))
                if any(b.denominator not in {1, 2, 4, 8, 16, 32} or not 0 <= b.dots <= 2
                       for voices in source_track.written_bars for voice in voices for b in voice.beats):
                    reasons.append(("written_rhythm", "This arrangement's written rhythm is outside the game's notation vocabulary."))
                for reason, message in reasons:
                    add_finding(report, feature="notation." + reason, category="game_limitation", impact="display_or_expression",
                                message=message + " Its playable tab is converted; complete notation remains in the original source.",
                                location="tracks/" + track["id"], value=None, arrangement=track["name"])
        performance["compatibilityReport"] = report
    return performance

"""Wire intervals for unpitched, visual-only pick scrapes."""
import math
from .alignment import map_time
from .audio import ImportFailure


def retime(note, alignment, origin, mapped_start, sustain):
    marks = note["pick_scrape_marks"]
    if note.get("mt") is not True or not isinstance(marks, list) or not marks:
        raise ImportFailure("unsupported_score", "Pick scrapes require a muted note and nonempty intervals.")
    result, previous = [], 0.
    for mark in marks:
        if (not isinstance(mark, dict) or set(mark) != {"direction", "start", "end"}
                or mark["direction"] not in ("up", "down")
                or any(type(mark[k]) not in (int, float) or not math.isfinite(mark[k]) for k in ("start", "end"))):
            raise ImportFailure("unsupported_score", "Invalid pick-scrape interval.")
        left, right = mark["start"], mark["end"]
        if left < previous - 1e-9 or right <= left or right > note.get("sus", 0) + 1e-9:
            raise ImportFailure("unsupported_score", "Pick-scrape interval is outside its sustain.")
        a = round(map_time(alignment, origin + left) - mapped_start, 6)
        b = round(map_time(alignment, origin + right) - mapped_start, 6)
        if a < 0 or b <= a or b > round(sustain, 6) + .0000011:
            raise ImportFailure("alignment_failed", "Pick-scrape interval cannot retain timing precision.")
        result.append({"direction": mark["direction"], "start": a, "end": b})
        previous = right
    return result

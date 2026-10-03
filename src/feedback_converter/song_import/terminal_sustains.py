"""Explicit audio-end policy for already verified Songsterr recording maps."""
from __future__ import annotations

import math

from .audio import ImportFailure

POLICY = "trim-final-sustain-v1"
EPSILON = 0.0000011


def policy_for(duration: float) -> dict:
    return {"version": 1, "policy": POLICY, "audioDuration": duration}


def allowed(alignment: dict, duration: float) -> bool:
    ending = alignment.get('endingPadding', {})
    if ending.get('version') == 2:
        from .ending_padding import MIXED_POLICY
        cutoff = ending['originalDuration'] + alignment.get('preparation', {}).get('seconds', 0)
        return (alignment.get('status') == 'validated' and ending.get('trimLongHeldTails') is True
                and ending.get('policy') == MIXED_POLICY and 0 < ending['seconds'] <= 2
                and alignment.get('method') == 'songsterr-video-points-v1' and alignment.get('mapping') == 'piecewise-linear'
                and alignment.get('terminalSustains') == mixed_policy_for(cutoff)
                and abs(duration-cutoff-ending['seconds']) <= EPSILON)
    return (alignment.get("method") == "songsterr-video-points-v1"
            and alignment.get("mapping") == "piecewise-linear"
            and alignment.get("terminalSustains") == policy_for(duration))


def mixed_policy_for(cutoff):
    return {'version':2,'policy':'trim-long-held-tail-with-padding-v1','audioDuration':cutoff,'onlyBeyondSeconds':2.0}


def cutoff_for(alignment, duration, start, end):
    policy = alignment.get('terminalSustains', {})
    if policy.get('version') == 2:
        cutoff=policy['audioDuration']
        return cutoff if end > cutoff+2.0 else None
    return duration


def slides_policy_for(duration: float) -> dict:
    return {"version": 1, "policy": "trim-final-directional-slide-v1", "audioDuration": duration}


def slides_allowed(alignment: dict, duration: float) -> bool:
    from .ending_cutoff import allowed as ending_allowed
    return (alignment.get("terminalSlides") == slides_policy_for(duration)
            and ending_allowed(alignment, duration))


def trim_held_note(note: dict, duration: float, *, allow_directional_slides: bool = False) -> tuple[dict, dict | None]:
    """Trim duration only. Never omit an attack or alter a timed pitch gesture.

    Input is in recording seconds. Continuous flags such as vibrato/mute/accent
    remain unchanged. A finished bend/slide may retain a shortened held tail;
    a timed pitch gesture crossing the cut still requires another recording.
    Direction-only segments may be capped only with separate acoustic authority.
    """
    start, sustain = float(note["t"]), float(note.get("sus", 0))
    if not all(math.isfinite(v) for v in (start, sustain, duration)) or sustain < 0 or start < 0 or start >= duration:
        raise ImportFailure("alignment_failed", "A note starts outside the recording; only final sustains can be shortened.")
    if start + sustain <= duration + EPSILON:
        return note, None
    end = math.floor(duration * 1_000_000) / 1_000_000
    shortened = round(end - start, 6)
    if shortened <= 0:
        raise ImportFailure("alignment_failed", "The recording does not contain the final note's attack.")
    curve = note.get("bnv", [])
    if any(p["t"] > shortened + EPSILON for p in curve):
        before = [p for p in curve if p["t"] <= shortened]
        after = [p for p in curve if p["t"] > shortened]
        # Exported curves often end with a duplicate held-value endpoint. That
        # constant tail can end with the sustain; never truncate a pitch change.
        if not before or any(not math.isclose(p["v"], before[-1]["v"], abs_tol=1e-10, rel_tol=0) for p in after):
            raise ImportFailure("alignment_failed", "The audio ends during a bend; it cannot be shortened as a held sustain.")
        curve = before + ([{**before[-1], "t": shortened}] if before[-1]["t"] < shortened else [])
    # Target-fret slides use the full sustain to describe their travel. Shortening
    # that interval would speed up the gesture, not merely shorten a held tail.
    if (any(note.get(key) is not None and note[key] != -1 for key in ("sl", "slu"))
            or note.get("slide_out") and not note.get("slide_out_marks")
            or note.get("bn") and not note.get("bnv")
            or not allow_directional_slides and any(p["end"] > shortened + EPSILON for p in note.get("slide_out_marks", []))
            or any(p["time"] > shortened + EPSILON for p in note.get("slide_in_marks", []))):
        raise ImportFailure("alignment_failed", "The audio ends during a bend or slide; it cannot be shortened as a held sustain.")
    adjusted = {**note, "sus": shortened}
    slide_changes = []
    if allow_directional_slides and note.get("slide_out_marks"):
        marks, previous = [], 0.0
        for index, mark in enumerate(note["slide_out_marks"]):
            if (not isinstance(mark, dict) or set(mark) != {"direction", "start", "end"}
                    or mark.get("direction") not in ("up", "down")
                    or any(isinstance(mark.get(k), bool) or not isinstance(mark.get(k), (int, float))
                           or not math.isfinite(mark[k]) for k in ("start", "end"))
                    or not previous <= mark["start"] < mark["end"] <= sustain + EPSILON):
                raise ImportFailure("alignment_failed", "The final slide-out has an invalid source interval.")
            previous = mark["end"]
            if mark["end"] > shortened + EPSILON:
                if mark["start"] >= shortened:
                    raise ImportFailure("alignment_failed", "The audio ends before the slide-out segment begins.")
                slide_changes.append({"index": index, "direction": mark["direction"], "start": mark["start"],
                                      "originalEnd": mark["end"], "exportedEnd": shortened})
                mark = {**mark, "end": shortened}
            marks.append(mark)
        adjusted["slide_out_marks"] = marks
    if 'harmonic_changes' in note:
        from copy import deepcopy
        changes = deepcopy(note['harmonic_changes'])
        if any(e['start'] >= shortened - EPSILON for e in changes['events']):
            raise ImportFailure('alignment_failed', 'The audio ends before a harmonic contact; it cannot be shortened as a held sustain.')
        for event in changes['events']: event['end'] = shortened
        adjusted['harmonic_changes'] = changes
    if 'whammy' in note:
        from ..whammy import trim_whammy
        try:
            adjusted['whammy'] = trim_whammy(note['whammy'], shortened)
        except ValueError as exc:
            raise ImportFailure('alignment_failed', str(exc)) from exc
    if 'vibrato_marks' in note:
        from ..vibrato import validate_marks, slice_marks
        try:
            adjusted['vibrato_marks'] = slice_marks(validate_marks(note['vibrato_marks'], sustain), 0, shortened)
        except ValueError as exc:
            raise ImportFailure('alignment_failed', str(exc)) from exc
    if "pick_scrape_marks" in note:
        adjusted["pick_scrape_marks"] = [{**mark, "end": min(mark["end"], shortened)}
            for mark in note["pick_scrape_marks"] if mark["start"] < shortened]
        if not adjusted["pick_scrape_marks"]:
            adjusted.pop("pick_scrape_marks")
    if "bnv" in note:
        adjusted["bnv"] = curve
    detail = {"string": note["s"], "fret": note["f"], "audioStart": round(start, 6),
              "originalDuration": round(sustain, 6), "exportedDuration": shortened,
              "trimmedSeconds": round(sustain - shortened, 6)}
    if slide_changes:
        detail["slideOuts"] = slide_changes
    return adjusted, detail

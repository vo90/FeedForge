"""Expand musical repeats and render a score onto one performed time axis."""

from bisect import bisect_right
from fractions import Fraction

from .model import Measure, Score, ScoreImportError, validate_score


def playback_order(measures: list[Measure]) -> list[int]:
    """Walk nested brackets; retain final pass for endings after the close bar."""
    opens: list[int] = []
    closes: dict[int, int] = {}
    for i, bar in enumerate(measures):
        if bar.repeat_start:
            opens.append(i)
        if bar.repeat_count:
            start = opens.pop() if opens else 0
            if start in closes:
                raise ScoreImportError("Multiple repeat closes sharing one start need explicit navigation support.")
            closes[start] = i
    if opens:
        raise ScoreImportError("An open repeat has no closing repeat.")
    active: list[list[int]] = []  # [start, close, pass]
    order: list[int] = []
    i = 0
    final_pass = 1
    steps = 0
    while i < len(measures):
        steps += 1
        if steps > 100_000 or len(order) >= 20_000:
            raise ScoreImportError("Repeat expansion exceeds the import limit.")
        if i in closes and (not active or active[-1][0] != i):
            active.append([i, closes[i], 1])
        bar = measures[i]
        turn = active[-1][2] if active else final_pass
        if not bar.endings or turn in bar.endings:
            order.append(i)
        if active and active[-1][1] == i:
            frame = active[-1]
            if frame[2] < bar.repeat_count:
                frame[2] += 1
                i = frame[0]
                continue
            final_pass = frame[2]
            active.pop()
        elif not active and not bar.endings:
            final_pass = 1
        i += 1
    return order


def render(score: Score) -> dict:
    validate_score(score)
    order = playback_order(score.measures)
    # Written tempos determine the inherited tempo when playback jumps backward.
    written_tempos: list[float] = []
    bpm = 120.0
    for bar in score.measures:
        written_tempos.append(bpm)
        for _, bpm in sorted(bar.tempos):
            pass
    visits: list[tuple[int, Fraction]] = []
    events: list[tuple[Fraction, float]] = []
    beat_positions: list[tuple[Fraction, int]] = []
    section_positions = []
    cursor = Fraction(0)
    previous = -1
    current_bpm = 120.0
    for occurrence, index in enumerate(order):
        bar = score.measures[index]
        if index != previous + 1:
            current_bpm = written_tempos[index]
            events.append((cursor, current_bpm))
        if not events:
            events.append((cursor, current_bpm))
        visits.append((index, cursor))
        for pos, bpm in sorted(bar.tempos):
            current_bpm = bpm
            events.append((cursor + pos, bpm))
        pos = Fraction(0)
        while pos < bar.length:
            beat_positions.append((cursor + pos, occurrence))
            pos += Fraction(4, bar.denominator)
        if bar.section:
            section_positions.append((cursor, bar.section))
        cursor += bar.length
        previous = index
    by_position = dict(events)
    points = sorted(by_position)
    bpms = [by_position[p] for p in points]
    seconds = [0.0]
    for i in range(1, len(points)):
        seconds.append(seconds[-1] + float(points[i] - points[i - 1]) * 60 / bpms[i - 1])

    def at(position: Fraction) -> float:
        k = bisect_right(points, position) - 1
        return seconds[k] + float(position - points[k]) * 60 / bpms[k]

    outputs = []
    warnings = list(score.warnings)
    source = {**score.source, "excludedTracks": list(score.source.get("excludedTracks", []))}
    performed_notes = 0
    for track in score.tracks:
        rendered: list[dict] = []
        previous_note: dict[int, tuple[dict, Fraction]] = {}
        pending_slide: dict[int, tuple[dict, str]] = {}
        pending_hopo: dict[int, dict] = {}
        last_written = -1
        for index, start in visits:
            if index <= last_written:
                if pending_slide or pending_hopo:
                    raise ScoreImportError(f"An unresolved linked technique crosses a repeat jump in {track.name}.")
                previous_note.clear()
            last_written = index
            seen = {}
            for note in sorted(track.bars[index], key=lambda n: (n.position, n.string)):
                position = start + note.position
                end = position + note.duration
                prior = previous_note.get(note.string)
                effects = {key: value for key, value in note.effects.items() if not key.startswith("__")}
                key = (note.position, note.string)
                if key in seen:
                    # Polyphonic notation sometimes duplicates a note in two voices;
                    # differing values on one physical string cannot both be played.
                    old = seen[key]
                    if old == note:
                        continue
                    raise ScoreImportError(f"Conflicting voices on one string in {track.name}, measure {index + 1}.")
                seen[key] = note
                if note.tie:
                    if prior is None or prior[0]["f"] != note.fret or prior[1] != position:
                        raise ScoreImportError(f"Unresolved tie in {track.name}, measure {index + 1}.")
                    output = prior[0]
                    output["sus"] = at(end) - output["t"]
                    output.update(effects)
                else:
                    output = {"t": at(position), "s": note.string, "f": note.fret,
                              "sus": at(end) - at(position), **effects}
                    if note.hopo or note.string in pending_hopo:
                        if prior is None:
                            raise ScoreImportError(f"Unresolved hammer-on/pull-off in {track.name}.")
                        output["ho" if note.fret > prior[0]["f"] else "po"] = True
                        pending_hopo.pop(note.string, None)
                    rendered.append(output)
                    performed_notes += 1
                    if performed_notes > 500_000:
                        raise ScoreImportError("Performed score exceeds the note import limit.")
                if note.bends:
                    curve = [{"t": at(position + note.duration * p) - output["t"], "v": v} for p, v in note.bends]
                    output.setdefault("bnv", []).extend(curve)
                    output["bn"] = max((p["v"] for p in output["bnv"]), key=abs)
                if note.string in pending_slide and not note.tie:
                    sliding, kind = pending_slide.pop(note.string)
                    sliding["sl"] = note.fret
                    if kind == "legato":
                        sliding["ln"] = True
                if note.slide in {"shift", "legato"}:
                    pending_slide[note.string] = (output, note.slide)
                elif note.slide in {"out_down", "out_up"}:
                    output["slu"] = max(0, note.fret - 5) if note.slide == "out_down" else note.fret + 5
                    message = "Unpitched slide display uses a five-fret span; the source specifies direction only."
                    if message not in warnings:
                        warnings.append(message)
                if note.effects.get("__hopo_origin"):
                    pending_hopo[note.string] = output
                    output["ln"] = True
                previous_note[note.string] = (output, end)
        if pending_slide or pending_hopo:
            raise ScoreImportError(f"A linked technique has no destination in {track.name}.")
        if not rendered:
            warnings.append(f"Skipped empty arrangement: {track.name}.")
            source["excludedTracks"].append({"id": track.id, "name": track.name,
                                             "instrument": track.instrument, "reason": "empty"})
            continue
        outputs.append({"id": track.id, "name": track.name, "instrument": track.instrument,
                        "role": track.role or track.instrument, "tuning": track.tuning,
                        "capo": track.capo, "notes": rendered})
    if not outputs:
        raise ScoreImportError("No playable notes were found.")
    source["playableTrackCount"] = len(outputs)
    return {"title": score.title, "artist": score.artist, "album": score.album, "year": score.year,
            "duration": at(cursor), "tracks": outputs,
            "beats": [{"time": at(p), "measure": n} for p, n in beat_positions],
            "sections": [{"time": at(p), "name": name} for p, name in section_positions],
            "tempos": [{"time": at(p), "bpm": by_position[p]} for p in points],
            "warnings": warnings, "source": source}

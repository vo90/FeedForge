"""Expand musical repeats and render a score onto one performed time axis."""

from bisect import bisect_left, bisect_right
from fractions import Fraction

from .model import Measure, Score, ScoreImportError, validate_score
from .notation import render_notation
from .songsterr_whammy import append_segment
from .repeat_regions import repeat_regions
from .fingering import template_fingers
from .link_diagnostics import LinkDiagnostics


def playback_order(measures: list[Measure]) -> list[int]:
    """Walk nested brackets; retain final pass for endings after the close bar."""
    opens: list[int] = []
    closes: dict[int, int] = {}
    for i, bar in enumerate(measures):
        if bar.repeat_start:
            opens.append(i)
        if bar.repeat_count:
            if not 2 <= bar.repeat_count <= 32:
                raise ScoreImportError('Invalid repeat count.')
            start = opens.pop() if opens else 0
            if start in closes:
                raise ScoreImportError("Multiple repeat closes sharing one start need explicit navigation support.")
            closes[start] = i
    if opens:
        raise ScoreImportError("An open repeat has no closing repeat.")
    ending_masks = repeat_regions(measures, closes)
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
        if not ending_masks[i] or turn in ending_masks[i]:
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
    from .voices import project
    result = _render(score)
    return project(score, result, _render) if score.source.get('format') == 'songsterr' else result


def _render(score: Score) -> dict:
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
        # Pickup beat ticks count into the following downbeat. A fractional
        # opening is not itself an accented downbeat or a fabricated extra beat.
        pos = bar.length % Fraction(4, bar.denominator) if bar.pickup else Fraction(0)
        while pos < bar.length:
            beat_positions.append((cursor + pos, occurrence + 1 if pos == 0 and not bar.pickup else -1))
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
        # An authored opening strum can begin before the first written beat.
        # Its clock continues the initial tempo, never the final tempo event.
        k = max(0, bisect_right(points, position) - 1)
        return seconds[k] + float(position - points[k]) * 60 / bpms[k]

    outputs = []
    harmonic_ties = []
    tied_mutes = []
    muted_tie_identities = []
    staccato_bends = []
    muted_slides = []
    undefined_slides = []
    consumed_strums = []
    scrape_entries = []
    strums = []
    trill_evidence = []
    warnings = list(score.warnings)
    source = {**score.source, "excludedTracks": list(score.source.get("excludedTracks", []))}
    performed_notes = 0
    for track in score.tracks:
        borrowed = {i for i, voices in enumerate(track.written_bars)
                    if any(b.position < 0 for v in voices for b in v.beats)}
        for visit, index in enumerate(order):
            if ((index in borrowed and (visit == 0 or order[visit - 1] != index - 1))
                    or (index + 1 in borrowed and (visit + 1 == len(order) or order[visit + 1] != index + 1))):
                raise ScoreImportError(f"Cross-bar grace crosses a repeat jump in {track.name}, measure {index + 1}; its borrowing needs traversal support.")
        rest_spans = {}
        for bi, start in visits:
            for voice in track.written_bars[bi] if track.written_bars else []:
                for beat in voice.beats:
                    if beat.rest:
                        rest_spans.setdefault(str(voice.source_index), []).append((start + beat.position, start + beat.position + beat.duration))
        rest_limits = {}
        for voice, spans in rest_spans.items():
            starts, ends, stop = [], [], Fraction(-1)
            for left, right in sorted(spans):
                starts.append(left); stop = max(stop, right); ends.append(stop)
            rest_limits[voice] = (starts, ends)
        from .skipped_slides import omissions as skipped_slide_omissions
        skipped_slides = skipped_slide_omissions(score, track, visits, at)
        rendered: list[dict] = []
        previous_note = {}
        pending_slide = {}
        pending_muted_shifts = {}
        pending_hopo = {}
        link_diagnostics = LinkDiagnostics()
        authored_groups = {}
        strum_origins = {}
        articulations = {}
        hopo_links = []
        last_written = -1
        for occurrence, (index, start) in enumerate(visits):
            if index <= last_written:
                if pending_slide or pending_hopo or pending_muted_shifts:
                    raise link_diagnostics.error(
                        f"An unresolved linked technique crosses a repeat jump in {track.name}.", 'repeat_jump',
                        boundary={'fromMeasure': last_written + 1, 'toMeasure': index + 1,
                                  'occurrence': occurrence + 1, 'time': at(start)})
                # A written tie at the repeat entrance may continue the note
                # identified by this performed boundary. Unfilled bar space
                # follows the ordinary tie rule below, including its rest
                # check. Carry no pitch repair or unrelated linked technique.
                entrances = {(n.voice_id, n.string): n.fret for n in track.bars[index]
                             if n.tie and n.position == 0}
                previous_note = {
                    key: prior for key, prior in previous_note.items()
                    if score.source.get('format') == 'songsterr' and prior[1] <= start
                    and entrances.get(key) == prior[0]['f']
                }
            last_written = index
            seen = {}
            for note in sorted(track.bars[index], key=lambda n: (n.position, n.string)):
                position = start + note.position
                end = position + note.duration
                link_key = (note.voice_id, note.string)
                prior = previous_note.get(link_key)
                effects = {key: value for key, value in note.effects.items() if not key.startswith("__")}
                key = (note.position, note.string)
                if not note.tie and key in seen and (score.source.get('format') != 'songsterr'
                                                     or seen[key].voice_id == note.voice_id):
                    # Keep authored voices in source evidence. A playable event
                    # cannot silently collapse their separate same-string attacks.
                    error = ScoreImportError(f"Simultaneous authored voices share one string in {track.name}, measure {index + 1}; choosing or combining their playing instructions needs review.")
                    error.source_feature = 'arrangement.simultaneous_voices'
                    error.source_value = [{'sourceId': n.source_id, 'voice': n.voice_id, 'string': n.string, 'fret': n.fret}
                                          for n in (seen[key], note)]
                    raise error
                if not note.tie:
                    seen[key] = note
                if note.tie:
                    gap_allowed = score.source.get('format') == 'songsterr'
                    if gap_allowed and prior is not None and prior[1] < position:
                        starts, ends = rest_limits.get(note.voice_id, ([], []))
                        rest_index = bisect_left(starts, position) - 1
                        gap_allowed = rest_index < 0 or ends[rest_index] <= prior[1]
                    if (prior is None or prior[1] > position
                            or prior[1] != position and not gap_allowed):
                        raise ScoreImportError(f"Unresolved tie in {track.name}, measure {index + 1}.")
                    output = prior[0]
                    songsterr_tie = score.source.get('format') == 'songsterr'
                    from .muted_ties import equivalent, plain_segment, record
                    prior_articulation = articulations[id(output)]
                    if prior_articulation.get('muted_identity') and not plain_segment(note):
                        raise ScoreImportError('A normalized muted tie cannot introduce a pitch gesture.')
                    if output['f'] != note.fret:
                        starts, ends = rest_limits.get(note.voice_id, ([], []))
                        rest_index = bisect_left(starts, position) - 1
                        uninterrupted = rest_index < 0 or ends[rest_index] <= prior_articulation['start']
                        if (not songsterr_tie or prior[1] != position or not uninterrupted
                                or not equivalent(output, note, prior_articulation)):
                            raise ScoreImportError(f"Unresolved tie in {track.name}, measure {index + 1}.")
                        muted_tie_identities.append(record(output, note, track, occurrence, position, end, at))
                        prior_articulation['muted_identity'] = True
                    late_mute = False
                    if songsterr_tie:
                        from .tied_mutes import retain as retain_mute
                        late_mute = retain_mute(output, effects, note, track, occurrence, position, end, at, tied_mutes)
                        from .tied_harmonics import FIELDS, retain
                        retain(output, effects, note, track, occurrence, position, end, at, harmonic_ties, articulations[id(output)])
                    elif "hn" in effects and any(output.get(k) != effects[k] for k in ("hn", "hps")):
                        raise ScoreImportError("A harmonic touch or pitch change inside a tie needs a separate gesture representation.")
                    if not songsterr_tie and "harmonic_target" in effects and output.get("harmonic_target") != effects["harmonic_target"]:
                        raise ScoreImportError("A harmonic type or pitch change inside a tie needs a separate gesture representation.")
                    articulation = articulations[id(output)]
                    articulation["segments"] += 1
                    # A continuation is not an attack. Its finger instruction
                    # must not overwrite the finger displayed at the onset.
                    output.update({k: v for k, v in effects.items() if k not in {"pkd", "fg"} and not (late_mute and k == 'mt') and (not songsterr_tie or k not in FIELDS)})
                    if note.source_id:
                        output.setdefault("source_ids", []).append(note.source_id)
                else:
                    attack = position + note.attack_offset
                    if attack < 0 and not (score.source.get('format') == 'songsterr'
                                          and occurrence == 0 and position >= 0
                                          and note.attack_offset < 0 and note.beat_id):
                        raise ScoreImportError("An opening attack has no authored strum provenance.")
                    output = {"t": at(attack), "s": note.string, "f": note.fret,
                              "sus": at(end) - at(attack), **effects}
                    if note.source_id:
                        output["source_ids"] = [note.source_id]
                    if note.hopo or link_key in pending_hopo:
                        if prior is None:
                            raise link_diagnostics.error(f"Unresolved hammer-on/pull-off in {track.name}.",
                                'missing_hopo_origin', family='hopo', key=link_key,
                                destination=(note, occurrence, output['t']))
                        if note.fret == 127 or prior[0]["f"] == 127:
                            raise link_diagnostics.error(
                                "A linked pitch technique reaches an unpitched mute; no fret was invented.",
                                'unpitched_hopo', family='hopo', key=link_key,
                                destination=(note, occurrence, output['t']))
                        output["ho" if note.fret > prior[0]["f"] else "po"] = True
                        pending_hopo.pop(link_key, None)
                        link_diagnostics.resolve('hopo', link_key)
                        hopo_links.append((prior[0], output))
                    rendered.append(output)
                    articulation = {"start": attack, "origin_staccato": note.staccato,
                                    "staccato": False, "pitch_gesture": False, "segments": 1,
                                    "trill": note.trill, "occurrence": occurrence + 1,
                                    "other_pitch_gesture": False, "bend_segments": []}
                    articulations[id(output)] = articulation
                    if note.beat_id:
                        authored_groups.setdefault((occurrence, note.beat_id), []).append(output)
                        if score.source.get('format') == 'songsterr':
                            _, pi, bi, vi, ei = note.beat_id.split(':')
                            raw = score.source_document['document']['parts'][int(pi)]['measures'][int(bi)]['voices'][int(vi)]['beats'][int(ei)]
                            from .songsterr_timing import strum_offsets
                            _, direction = strum_offsets(raw)
                            if direction:
                                kind = 'arpeggio' if any(raw.get(k) is not None for k in ('arpeggio', 'upArpeggio', 'downArpeggio')) else 'brush'
                                strum_origins[(occurrence, note.beat_id)] = (at(position), direction, kind)
                    performed_notes += 1
                    if performed_notes > 500_000:
                        raise ScoreImportError("Performed score exceeds the note import limit.")
                from .songsterr_trills import check_segment
                try:
                    check_segment(note, articulation)
                except ScoreImportError as exc:
                    exc.source_location = {"location": note.source_id + '/trill', "measure": index + 1}
                    raise
                articulation["staccato"] |= note.staccato
                retained_scrape_entry = (score.source.get('format') == 'songsterr' and output.get('mt')
                                        and note.slide_in and (note.pick_scrape or note.tie and articulation.get('scrape')))
                incoming_pitch = note.slide_in and not retained_scrape_entry
                articulation["pitch_gesture"] |= bool(note.bends or note.slide or incoming_pitch or note.whammy)
                articulation["other_pitch_gesture"] |= bool(note.slide or incoming_pitch or note.whammy)
                articulation['bend_segments'].append((note, position, end, occurrence))
                if (articulation["segments"] > 1 and articulation["staccato"] and articulation["pitch_gesture"]
                        and (score.source.get('format') != 'songsterr' or articulation['other_pitch_gesture'])):
                    raise ScoreImportError("Staccato ties with pitch gestures need additional timing verification.")
                sound_end = end
                if articulation["origin_staccato"]:
                    sound_end = articulation["start"] + max((end - articulation["start"]) / 2, Fraction(1, 32))
                    if sound_end > end:
                        raise ScoreImportError("Staccato's minimum would extend the authored note; no repair was applied.")
                articulation["end"] = sound_end
                output["sus"] = at(sound_end) - output["t"]
                # Scrapes inherit across tied continuations, but retain every
                # authored change of direction. These are time intervals, not
                # a trajectory between the (possibly hidden) source frets.
                scrape = note.pick_scrape or (articulation.get("scrape") if note.tie else None)
                articulation["scrape"] = scrape
                if scrape:
                    if not output.get("mt"):
                        raise ScoreImportError("A pick scrape cannot target a pitched note.")
                    marks = output.setdefault("pick_scrape_marks", [])
                    left, right = max(0., at(position) - output["t"]), at(end) - output["t"]
                    if marks and marks[-1]["direction"] == scrape and abs(marks[-1]["end"] - left) < 1e-9:
                        marks[-1]["end"] = right
                    else:
                        marks.append({"direction": scrape, "start": left, "end": right})
                gesture_duration = max(note.duration / 2, Fraction(1, 32)) if note.staccato else note.duration
                append_segment(output, note, position, position + gesture_duration, at, occurrence)
                if note.bends:
                    curve = [{"t": at(position + gesture_duration * p) - output["t"], "v": v} for p, v in note.bends]
                    output.setdefault("bnv", []).extend(curve)
                    output["bn"] = max((p["v"] for p in output["bnv"]), key=abs)
                if link_key in pending_muted_shifts and not note.tie:
                    articulation['linked'] = True
                    omit = score.source.get('format') == 'songsterr' and note.fret == 127 and effects.get('mt') is True
                    if (note.fret == 127 or effects.get('mt')) and not omit:
                        raise link_diagnostics.error(
                            'A muted shift needs an explicit pitched destination; no fret was invented.',
                            'muted_destination', family='muted_shift', key=link_key,
                            destination=(note, occurrence, output['t']))
                    for row, source_origin in pending_muted_shifts.pop(link_key):
                        row['target'] = {'sourceId': note.source_id, 'time': output['t'], 'fret': None if omit else note.fret}
                        if omit:
                            from .slide_omissions import retain
                            undefined_slides.append(retain(source_origin, note, occurrence, output['t']))
                            row['used']['rule'] = 'omitted-shift-to-unpitched-mute'
                    link_diagnostics.resolve('muted_shift', link_key)
                if note.fret == 127 and note.slide:
                    from .muted_slides import record
                    row = record(note, track, occurrence, output['t'], at(position), at(position + gesture_duration))
                    muted_slides.append(row)
                    if note.slide == 'shift':
                        from .slide_omissions import origin
                        pending_muted_shifts.setdefault(link_key, []).append((row, origin(note, track, occurrence, output['t'], at(position))))
                        link_diagnostics.remember('muted_shift', link_key, note, occurrence, at(position))
                if link_key in pending_slide and not note.tie:
                    omit = score.source.get('format') == 'songsterr' and note.fret == 127 and effects.get('mt') is True
                    if note.fret == 127 and not omit:
                        raise link_diagnostics.error(
                            "A slide reaches an unpitched mute; no destination fret was invented.",
                            'unpitched_destination', family='slide', key=link_key,
                            destination=(note, occurrence, output['t']))
                    sliding, kind, source_origin = pending_slide.pop(link_key)
                    link_diagnostics.resolve('slide', link_key)
                    articulation['linked'] = True
                    articulations[id(sliding)]['linked'] = True
                    if omit:
                        from .slide_omissions import retain
                        undefined_slides.append(retain(source_origin, note, occurrence, output['t']))
                    else:
                        sliding['sl'] = note.fret
                        if kind == 'legato':
                            sliding['ln'] = True
                if note.slide in {"shift", "legato"} and note.fret != 127:
                    from .slide_omissions import origin
                    source_origin = origin(note, track, occurrence, output['t'], at(position)) if score.source.get('format') == 'songsterr' else None
                    skipped = skipped_slides.get((occurrence, note.source_id))
                    if skipped:
                        undefined_slides.append({**source_origin, **skipped})
                    else:
                        pending_slide[link_key] = (output, note.slide, source_origin)
                        link_diagnostics.remember('slide', link_key, note, occurrence, at(position + note.attack_offset))
                elif note.slide in {"out_down", "out_up"}:
                    # A tied continuation may carry the marking only on its
                    # final written segment. Keep each interval before merging
                    # loses that boundary; it does not describe slide speed.
                    marks = output.setdefault("slide_out_marks", [])
                    marks.append({"direction": "down" if note.slide == "out_down" else "up",
                                  "start": at(position) - output["t"], "end": at(position + gesture_duration) - output["t"]})
                    directions = {mark["direction"] for mark in marks}
                    if len(directions) == 1:
                        output["slide_out"] = marks[0]["direction"]
                    else:
                        output.pop("slide_out", None)
                if retained_scrape_entry:
                    from .scrape_entries import record as record_scrape_entry
                    scrape_entries.append(record_scrape_entry(note, track, occurrence, output['t'],
                                          at(position), at(position + gesture_duration), scrape))
                elif note.slide_in:
                    # Only the destination segment onset is authored. Keep it
                    # before ties merge; never infer a starting fret or length.
                    output.setdefault("slide_in_marks", []).append({
                        "direction": note.slide_in, "time": at(position) - output["t"]})
                if note.effects.get("__hopo_origin"):
                    pending_hopo[link_key] = output
                    link_diagnostics.remember('hopo', link_key, note, occurrence, at(position + note.attack_offset))
                    output["ln"] = True
                previous_note[link_key] = (output, end)
        if pending_slide or pending_hopo or pending_muted_shifts:
            raise link_diagnostics.error(f"A linked technique has no destination in {track.name}.", 'end_of_score',
                                         boundary={'time': at(cursor)})
        from .consumed_strums import omit_consumed
        omissions = omit_consumed(score, track, rendered, articulations, authored_groups,
                                  strum_origins, hopo_links, at)
        consumed_strums.extend(omissions)
        if score.source.get('format') == 'songsterr':
            from .staccato_bends import finish as finish_bend
            for output in rendered:
                evidence = finish_bend(output, articulations[id(output)], track.id, at, points)
                if evidence:
                    staccato_bends.append(evidence)
        from .songsterr_trills import expand
        performed_notes += expand(rendered, articulations, hopo_links, track.id, at, trill_evidence, authored_groups)
        if performed_notes > 500_000:
            raise ScoreImportError("Performed score exceeds the note import limit.")
        # Apply the final sounding duration once ties have extended it. Clipping
        # each tied segment early loses later intervals on staccato attacks.
        for output in rendered:
            from .tied_harmonics import finish
            finish(output, harmonic_ties, track.id)
            if "pick_scrape_marks" in output:
                output["pick_scrape_marks"] = [{**m, "end": min(m["end"], output["sus"])}
                    for m in output["pick_scrape_marks"] if m["start"] < output["sus"]]
                if not output["pick_scrape_marks"]:
                    output.pop("pick_scrape_marks")
        if any(n["sus"] <= 0 for n in rendered):
            raise ScoreImportError(f"An authored strum consumes a sounding note in {track.name}; no duration was repaired.")
        # A written leading grace may sound in the preceding measure. Voices
        # remain independent while resolving links; transport is chronological.
        rendered.sort(key=lambda note: (note["t"], note["s"]))
        if not rendered:
            warnings.append(f"Skipped empty arrangement: {track.name}.")
            source["excludedTracks"].append({"id": track.id, "name": track.name,
                                             "instrument": track.instrument, "reason": "empty"})
            continue
        chords, templates, grouped = [], [], set()
        template_ids = {}
        labels = {beat.source_id: beat.chord_label for voices in track.written_bars for voice in voices for beat in voice.beats}
        for key, (time, direction, kind) in strum_origins.items():
            group = authored_groups[key]
            if len(group) >= 2 and len({n['s'] for n in group}) == len(group):
                strums.append({'trackId':track.id, 'sourceId':key[1], 'occurrence':key[0]+1,
                               'time':time, 'direction':direction, 'kind':kind,
                               'notes':[{'t':n['t'],'s':n['s'],'f':n['f']} for n in sorted(group,key=lambda n:n['s'])]})
        for (_, beat_id), group in authored_groups.items():
            if len(group) < 2 or len({note["t"] for note in group}) != 1:
                continue
            frets = [-1] * len(track.tuning)
            for note in group:
                frets[note["s"]] = note["f"]
                grouped.add(id(note))
            label = labels.get(beat_id, "")
            fingers = template_fingers(group, len(frets))
            shape = (tuple(frets), label, tuple(fingers))
            if shape not in template_ids:
                template_ids[shape] = len(templates)
                templates.append({"name": label, "fingers": fingers, "frets": frets})
            chords.append({"t": group[0]["t"], "id": template_ids[shape], "source_ids": [beat_id],
                           "notes": [{key: value for key, value in note.items() if key != "t"} for note in group]})
        notation, notation_warnings = (None, []) if omissions else render_notation(score, track, visits, at, rendered)
        warnings.extend(notation_warnings)
        outputs.append({"id": track.id, "name": track.name, "instrument": track.instrument,
                        "role": track.role or track.instrument, "tuning": track.tuning,
                        "capo": track.capo, "notes": [note for note in rendered if id(note) not in grouped],
                        "chords": chords, "templates": templates, **({"notation": notation} if notation else {})})
    if not outputs:
        raise ScoreImportError("No playable notes were found.")
    source["playableTrackCount"] = len(outputs)
    # Retain the written-to-performed relationship for independently supplied
    # recording synchronization. Seconds alone lose repeat occurrences and
    # musical positions when written tempos vary.
    occurrence_counts = {}
    performed_measures = []
    for occurrence, (index, start) in enumerate(visits):
        occurrence_counts[index] = occurrence_counts.get(index, 0) + 1
        bar = score.measures[index]
        performed_measures.append({"index": occurrence, "writtenIndex": index,
                                   "visit": occurrence_counts[index],
                                   "quarter": float(start), "quarters": float(bar.length),
                                   "start": at(start), "end": at(start + bar.length),
                                     "numerator": bar.numerator, "denominator": bar.denominator,
                                     **({'pickup': True} if bar.pickup else {})})
    repeat_starts = []
    repeat_intervals = []
    multibar_endings = False
    for index, bar in enumerate(score.measures):
        if bar.repeat_start:
            repeat_starts.append(index)
        if bar.repeat_count:
            start = repeat_starts.pop() if repeat_starts else 0
            repeat_intervals.append((start, index))
            # The complete region is skipped, including unmarked continuation
            # bars. Synchronization independently checks its performed order.
            multibar_endings = multibar_endings or any(item.endings for item in score.measures[start:index])
    # An outer bracket can start implicitly at bar zero. Count actual closed
    # intervals, not only explicit start markers, to detect such nesting.
    depth_changes = [0] * (len(score.measures) + 1)
    for start, end in repeat_intervals:
        depth_changes[start] += 1
        depth_changes[end + 1] -= 1
    repeat_depth = max_repeat_depth = 0
    for change in depth_changes:
        repeat_depth += change
        max_repeat_depth = max(max_repeat_depth, repeat_depth)
    score_timeline = {"version": 1, "writtenMeasureCount": len(score.measures),
                      "hasRepeats": any(bar.repeat_start or bar.repeat_count for bar in score.measures),
                      "hasAlternateEndings": any(bar.endings for bar in score.measures),
                      "maxRepeatDepth": max_repeat_depth,
                      "hasMultiBarAlternateEndings": multibar_endings,
                      "hasWithinBarTempoChanges": any(pos > 0 for bar in score.measures for pos, _ in bar.tempos),
                      "measures": performed_measures,
                      "tempoPoints": [{"quarter": float(p), "time": at(p), "bpm": by_position[p]} for p in points]}
    time_signatures = []
    previous_signature = None
    for index, start in visits:
        bar = score.measures[index]
        signature = [bar.numerator, bar.denominator]
        if signature != previous_signature:
            time_signatures.append({"time": at(start), "ts": signature})
            previous_signature = signature
    return {"title": score.title, "artist": score.artist, "album": score.album, "year": score.year,
            "duration": at(cursor), "tracks": outputs,
            "beats": [{"time": at(p), "measure": n} for p, n in beat_positions],
            "sections": [{"time": at(p), "name": name} for p, name in section_positions],
            "tempos": [{"time": at(p), "bpm": by_position[p]} for p in points],
            "time_signatures": time_signatures,
            "scoreTimeline": score_timeline,
            "warnings": warnings, "source": source,
            "sourceScore": score.source_document,
            **({'harmonicTieEvidence': sorted(harmonic_ties, key=lambda r: (r['trackId'], r['occurrence'], r['start'], r['string'], r['location']))} if harmonic_ties else {}),
            **({'tiedMuteEvidence': sorted(tied_mutes, key=lambda r: (r['trackId'], r['occurrence'], r['start'], r['string'], r['location']))} if tied_mutes else {}),
            **({'mutedTieIdentityEvidence': sorted(muted_tie_identities, key=lambda r: (r['trackId'], r['occurrence'], r['start'], r['string'], r['location']))} if muted_tie_identities else {}),
            **({'staccatoBendEvidence': sorted(staccato_bends, key=lambda r: (r['trackId'], r['occurrence'], r['start'], r['string'], r['location']))} if staccato_bends else {}),
            'strumEvidence': sorted(strums,key=lambda r:(r['trackId'],r['occurrence'],r['time'],r['sourceId'])),
            **({"trillEvidence": trill_evidence} if trill_evidence else {}),
            **({'undefinedSlideEvidence': sorted(undefined_slides, key=lambda r: (r['trackId'], r['occurrence'], r['start'], r['string'], r['location']))} if undefined_slides else {}),
            **({'mutedSlideEvidence': sorted(muted_slides, key=lambda r: (r['trackId'], r['occurrence'], r['start'], r['string'], r['location']))} if muted_slides else {}),
            **({'consumedStrumEvidence': sorted(consumed_strums, key=lambda r: (r['trackId'], r['occurrence'], r['start'], r['string'], r['location']))} if consumed_strums else {}),
            **({'scrapeEntryEvidence': sorted(scrape_entries, key=lambda r: (r['trackId'], r['occurrence'], r['start'], r['string'], r['location']))} if scrape_entries else {}),
            "featureInventory": score.feature_inventory}

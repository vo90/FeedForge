"""Independent raw-source-to-archive fidelity verification.

``passed`` concerns the explicitly listed conversion checks, not musical quality
or proof of audio synchronization. Instrument expectations use no production
score parser, renderer or time mapper. Lyrics share the separately qualified
lexical interpretation/phrase policy and use independent performance clocks.
The caller retains raw sources and
the complete applied alignment alongside this bounded, JSON-serializable report.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
import hashlib
import io
import json
import math
from pathlib import Path, PurePosixPath
from zipfile import BadZipFile, ZipFile

import yaml

from .verify_source import UnverifiedFeature, inactive, read_source
from .verify_timeline import expected

VERSION = 82
TIME_TOLERANCE = 0.0000011
TECHNIQUES = {"pm", "mt", "vb", "ghost", "ac", "tp", "lr", "tr", "slp", "plk", "hm", "hp", "hn", "hps", "ho", "po", "ln", "sl", "slu", "slide_out", "slide_out_marks", "slide_in_marks", "pick_scrape_marks", "vibrato_marks", "bn", "pkd"}
TECHNIQUES.update({"harmonic_target", "harmonic_alias", "whammy", "harmonic_changes"})
TECHNIQUES.add("fg")
TECHNIQUES.add("slide_interval")
TUNINGS = {"guitar": {6: [40, 45, 50, 55, 59, 64], 7: [35, 40, 45, 50, 55, 59, 64], 8: [30, 35, 40, 45, 50, 55, 59, 64]},
           "bass": {4: [28, 33, 38, 43], 5: [23, 28, 33, 38, 43], 6: [23, 28, 33, 38, 43, 48]}}


class Check:
    def __init__(self):
        self.errors = []
        self.total_errors = 0

    def fail(self, code, location, message, wanted=None, actual=None):
        self.total_errors += 1
        if len(self.errors) < 100:
            row = {"code": code, "location": location, "message": message}
            if wanted is not None:
                row["expected"] = wanted
            if actual is not None:
                row["actual"] = actual
            self.errors.append(row)

    def equal(self, code, location, wanted, actual):
        if wanted != actual or (isinstance(wanted, bool) or isinstance(actual, bool)) and type(wanted) is not type(actual):
            self.fail(code, location, "The archived value differs from the source-derived value.", wanted, actual)

    def near(self, code, location, wanted, actual, tolerance=TIME_TOLERANCE):
        if isinstance(actual, bool) or not isinstance(actual, (int, float)) or not math.isfinite(actual) or abs(wanted - actual) > tolerance:
            self.fail(code, location, "The archived number differs beyond the documented rounding tolerance.", wanted, actual)


def _finite(value, location, check):
    if isinstance(value, float) and not math.isfinite(value):
        check.fail("nonfinite_number", location, "FeedPak contains a non-finite number.")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _finite(item, location + f"/{index}", check)
    elif isinstance(value, dict):
        for key, item in value.items():
            _finite(item, location + "/" + str(key), check)


def _json(archive, name, check):
    info = archive.getinfo(name)
    if info.file_size > 80 * 1024 * 1024:
        raise ValueError(name + ": verification JSON size limit exceeded")
    result = json.loads(archive.read(info))
    _finite(result, name, check)
    return result


def _flatten(chart):
    output = [(dict(n), f"notes/{i}") for i, n in enumerate(chart.get("notes", []))]
    templates = chart.get("templates", [])
    for ci, chord in enumerate(chart.get("chords", [])):
        children = chord.get("notes")
        if children is None:
            # A generated template-only chord cannot preserve individual
            # note durations/effects; report its actual playable information.
            template = templates[int(chord.get("id", chord.get("cid", chord.get("chordId", -1))))]
            children = [{"s": s, "f": f, "sus": chord.get("sus", 0)}
                        for s, f in enumerate(template["frets"]) if f >= 0]
        for ni, raw in enumerate(children):
            n = {"t": chord["t"], **{k: v for k, v in chord.items() if k in TECHNIQUES}, **raw}
            output.append((n, f"chords/{ci}/notes/{ni}"))
    return output


def _timeline(wanted, actual, check, name):
    if not isinstance(actual, list):
        check.fail("missing_timeline", name, "A source-derived timeline array is missing.")
        return
    check.equal("timeline_count", name, len(wanted), len(actual))
    for index, (a, b) in enumerate(zip(wanted, actual)):
        loc = name + f"/{index}"
        check.near("timeline_time", loc + "/time", a["time"], b.get("time"))
        for key in a:
            if key == "bpm":
                check.near("tempo_value", loc + "/bpm", a[key], b.get(key), max(1e-7, abs(a[key]) * 1e-9))
            elif key != "time":
                check.equal("timeline_value", loc + "/" + key, a[key], b.get(key))


def _strum_groups(wanted, chart, check, track_id, alignment, policy):
    """Independently bind display IDs to complete, source-authored brushes."""
    from .verify_timeline import RecordingMap
    recording = RecordingMap(alignment)
    expected = {}
    members = {}
    for index, n in enumerate(chart.get('notes', [])):
        members.setdefault((n['s'], n['f']), []).append((n['t'], index))
    members = {key: sorted(values) for key, values in members.items()}
    times = {key: [t for t, _ in values] for key, values in members.items()}

    def match(t, string, fret):
        # Independent rational and producer floating-point clocks can round to
        # opposite sides of a half-microsecond boundary. Use the same precision
        # as the note-time check, never nearest-note or exact-match preference.
        # Multiple candidates (including an exact match) remain ambiguous.
        key = (string, fret)
        values = times.get(key, [])
        left = bisect_left(values, t - TIME_TOLERANCE)
        right = bisect_right(values, t + TIME_TOLERANCE)
        return members[key][left][1] if right - left == 1 else None

    if policy is not None:
        check.equal('strum_group_policy', 'manifest/song_import', 'authored-brush-groups-v1', policy)
        for ident, group in enumerate(wanted):
            if group['trackId'] != track_id or group.get('kind') != 'brush':
                continue
            keys = [(round(recording.at(n['t']), 6), n['s'], n['f']) for n in group['notes']]
            if (len(keys) < 2 or len({k[0] for k in keys}) < 2
                    or len({k[1] for k in keys}) != len(keys)):
                continue
            indices = [match(*key) for key in keys]
            if any(index is None for index in indices):
                continue
            locations = [f'notes/{index}' for index in indices]
            if len(set(indices)) != len(indices) or any(loc in expected for loc in locations):
                check.fail('strum_group', 'tracks/' + track_id, 'Source brushes reuse an archived note.')
                continue
            expected.update((loc, ident) for loc in locations)
    for note, loc in _flatten(chart):
        actual = note.get('ch')
        if actual is not None and (type(actual) is not int or actual < 0):
            check.fail('strum_group', loc, 'Invalid display group identity.')
        check.equal('strum_group', 'tracks/' + track_id + '/' + loc, expected.get(loc), actual)


def _notes(wanted, actual, check, part, duration):
    check.equal("note_count", "tracks/" + part.id, len(wanted), len(actual))
    # Stable physical-string/time order is independent of exporter chord
    # grouping; source duplicates remain distinct multiset entries.
    expected_order = sorted(wanted, key=lambda x: (x["note"]["s"], x["note"]["t"], x["note"]["f"]))
    actual_order = sorted(actual, key=lambda x: (x[0].get("s", -1), x[0].get("t", -1), x[0].get("f", -1)))
    for item, (b, destination) in zip(expected_order, actual_order):
        a = item["note"]
        loc = item["locations"][0] + f"@visit{item['occurrence']} -> tracks/{part.id}/{destination}"
        for key in set(b) - ({"t", "s", "f", "sus", "bnv", "source_id", "source_ids", "ch"} | TECHNIQUES):
            if not inactive(b[key]):
                check.fail("unexpected_note_field", loc + "/" + key, "An unaccounted playable-note field was introduced.")
        for key in ("s", "f"):
            check.equal("note_" + key, loc + "/" + key, a[key], b.get(key))
        for key in ("t", "sus"):
            check.near("note_time" if key == "t" else "note_sustain", loc + "/" + key, a[key], b.get(key, 0))
        for key in TECHNIQUES:
            if key in {"slide_interval", "slide_out_marks", "slide_in_marks", "pick_scrape_marks", "whammy", "harmonic_changes", "vibrato_marks"}:
                continue
            wanted_value, actual_value = a.get(key), b.get(key)
            if key == "fg" and key in b and (type(actual_value) is not int or not 0 <= actual_value <= 4):
                check.fail("note_fingering", loc + "/fg", "Finger hints must be integers from 0 (thumb) to 4.")
            if wanted_value is None and (actual_value is None or actual_value is False):
                continue
            if key == "bn" and isinstance(wanted_value, (int, float)):
                check.near("note_technique", loc + "/bn", wanted_value, actual_value, 1e-8)
            else:
                check.equal("note_technique", loc + "/" + key, wanted_value, actual_value)
        from .verify_vibrato import compare as compare_vibrato
        compare_vibrato(a.get('vibrato_marks'), b.get('vibrato_marks'), b.get('sus', 0), check, loc+'/vibrato_marks')
        _slide_marks(a.get("slide_out_marks", []), b, check, loc)
        _incoming_marks(a.get("slide_in_marks", []), b, check, loc)
        _scrape_marks(a.get("pick_scrape_marks", []), b, check, loc)
        from .verify_slide_interval import compare as compare_slide_interval
        if 'slide_interval' in b and b['slide_interval'] is None:
            check.fail('slide_interval', loc, 'A present slide interval cannot be null.')
        compare_slide_interval(a.get('slide_interval'), b.get('slide_interval'), b, check, loc+'/slide_interval')
        from .verify_whammy import check_bar
        check_bar(a.get('whammy'), b.get('whammy'), check, loc+'/whammy')
        from .verify_harmonic_changes import check_changes
        check_changes(a.get('harmonic_changes'), b.get('harmonic_changes'), check, loc+'/harmonic_changes')
        curves = (a.get("bnv", []), b.get("bnv", []))
        check.equal("bend_point_count", loc + "/bnv", len(curves[0]), len(curves[1]))
        for pi, (ap, bp) in enumerate(zip(*curves)):
            check.near("bend_time", loc + f"/bnv/{pi}/t", ap["t"], bp.get("t"))
            check.near("bend_value", loc + f"/bnv/{pi}/v", ap["v"], bp.get("v"), 1e-8)
        if not isinstance(b.get("t"), (int, float)) or not isinstance(b.get("sus", 0), (int, float)):
            continue
        if b["t"] < 0 or b.get("sus", 0) < 0 or b["t"] + b.get("sus", 0) > duration + TIME_TOLERANCE:
            check.fail("note_audio_bounds", loc, "A playable note is outside the encoded audio timeline.")


def _slide_marks(wanted, note, check, location):
    """Compare source intervals individually, including absence and validity."""
    actual = note.get("slide_out_marks", [])
    loc = location + "/slide_out_marks"
    if not isinstance(actual, list):
        check.fail("slide_mark_shape", loc, "Slide-out marks must be an array.")
        return
    check.equal("slide_mark_count", loc, len(wanted), len(actual))
    previous_end = 0.0
    for index, mark in enumerate(actual):
        at = loc + f"/{index}"
        if not isinstance(mark, dict) or set(mark) != {"direction", "start", "end"}:
            check.fail("slide_mark_shape", at, "A slide-out interval has an invalid shape.")
            continue
        left, right = mark["start"], mark["end"]
        if (not isinstance(mark["direction"], str) or mark["direction"] not in {"up", "down"}
                or any(isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n) for n in (left, right))):
            check.fail("slide_mark_shape", at, "A slide-out interval has an invalid direction or number.")
            continue
        sustain = note.get("sus", 0)
        if (left < 0 or right <= left or left < previous_end - TIME_TOLERANCE
                or not isinstance(sustain, (int, float)) or right > sustain + TIME_TOLERANCE):
            check.fail("slide_mark_bounds", at, "A slide-out interval is outside the sustain or out of order.")
        previous_end = right
        if index < len(wanted):
            expected = wanted[index]
            check.equal("slide_mark_direction", at + "/direction", expected["direction"], mark["direction"])
            check.near("slide_mark_time", at + "/start", expected["start"], left)
            check.near("slide_mark_time", at + "/end", expected["end"], right)


def _scrape_marks(wanted, note, check, location):
    # Reuse interval-shape checking, but retain scrape-specific error locations.
    if not wanted and "pick_scrape_marks" not in note:
        return
    if "pick_scrape_marks" in note and note.get("mt") is not True:
        check.fail("scrape_pitched", location, "Pick scrapes must be unpitched and unscored.")
    if "pick_scrape_marks" in note and not note["pick_scrape_marks"]:
        check.fail("scrape_empty", location, "A present scrape extension needs an interval.")
    class ScrapeCheck:
        def __getattr__(self, name):
            def call(code, loc, *args):
                return getattr(check, name)(code.replace("slide_mark", "scrape_mark"),
                    loc.replace("slide_out_marks", "pick_scrape_marks"), *args)
            return call
    proxy = dict(note)
    proxy["slide_out_marks"] = note.get("pick_scrape_marks", [])
    _slide_marks(wanted, proxy, ScrapeCheck(), location)


def _incoming_marks(wanted, note, check, location):
    """Check authored destination onsets, never an inferred approach trajectory."""
    actual = note.get("slide_in_marks", [])
    loc = location + "/slide_in_marks"
    if not isinstance(actual, list):
        check.fail("slide_in_shape", loc, "Slide-in marks must be an array.")
        return
    check.equal("slide_in_count", loc, len(wanted), len(actual))
    previous = -1.0
    for index, mark in enumerate(actual):
        at = loc + f"/{index}"
        if not isinstance(mark, dict) or set(mark) != {"direction", "time"}:
            check.fail("slide_in_shape", at, "An incoming-slide cue has an invalid shape.")
            continue
        when = mark["time"]
        if (not isinstance(mark["direction"], str) or mark["direction"] not in {"up", "down"}
                or isinstance(when, bool) or not isinstance(when, (int, float))
                or isinstance(when, float) and not math.isfinite(when)):
            check.fail("slide_in_shape", at, "An incoming-slide cue has an invalid direction or time.")
            continue
        sustain = note.get("sus", 0)
        if (when < 0 or when <= previous or isinstance(sustain, bool) or not isinstance(sustain, (int, float))
                or isinstance(sustain, float) and not math.isfinite(sustain) or when > sustain + TIME_TOLERANCE):
            check.fail("slide_in_bounds", at, "An incoming-slide cue is outside the sustain or out of order.")
            previous = when
            continue
        previous = when
        if index < len(wanted):
            check.equal("slide_in_direction", at + "/direction", wanted[index]["direction"], mark["direction"])
            check.near("slide_in_time", at + "/time", wanted[index]["time"], when)


def _chords(wanted, chart, check, part):
    groups = {}
    labels = {beat["location"]: beat.get("chord_label", "") for bar in part.beats for beat in bar}
    trill_beats = {(item['occurrence'], item['beat']) for item in wanted if item.get('trill')}
    for item in wanted:
        key = (item['occurrence'], item['beat'])
        groups.setdefault((*key, item['note']['t'] if key in trill_beats else None), []).append(item["note"])
    expected_shapes = {}
    for (_, beat, _), notes in groups.items():
        if len(notes) > 1 and len({n["t"] for n in notes}) == 1:
            shape = (tuple(sorted((n["s"], n["f"]) for n in notes)), labels.get(beat, ""))
            expected_shapes.setdefault(shape, []).append(notes[0]["t"])
    actual_shapes = {}
    templates = chart.get("templates", [])
    for ci, chord in enumerate(chart.get("chords", [])):
        loc = f"tracks/{part.id}/chords/{ci}"
        children = chord.get("notes", [])
        shape = tuple(sorted((n["s"], n["f"]) for n in children))
        template_id = chord.get("id")
        if isinstance(template_id, bool) or not isinstance(template_id, int) or not 0 <= template_id < len(templates):
            check.fail("chord_template", loc, "Chord has no valid template.")
            continue
        actual_shapes.setdefault((shape, templates[template_id].get("name") or ""), []).append(chord["t"])
        names = {label for (expected_shape, label), times in expected_shapes.items() if expected_shape == shape
                 and any(abs(time - chord["t"]) <= TIME_TOLERANCE for time in times)}
        if (templates[template_id].get("name") or "") not in names:
            check.fail("invented_chord_name", loc + "/name", "The chord label differs from the authored label at this beat.")
        frets = [-1] * len(part.tuning)
        for s, f in shape:
            if not 0 <= s < len(frets):
                check.fail("chord_string", loc, "Chord uses an invalid string.")
                continue
            frets[s] = f
        check.equal("chord_template", loc + "/frets", frets, templates[template_id].get("frets"))
        # Child hints are independently compared with raw source in _notes.
        # The chord diagram must carry those same hints on the same strings.
        fingers = [-1] * len(frets)
        for note in children:
            if type(note.get("s")) is int and 0 <= note["s"] < len(fingers):
                fingers[note["s"]] = note.get("fg", -1)
        check.equal("invented_chord_fingers", loc + "/fingers", fingers, templates[template_id].get("fingers", [-1] * len(frets)))
    if {k: len(v) for k, v in expected_shapes.items()} != {k: len(v) for k, v in actual_shapes.items()}:
        check.fail("authored_chord_groups", "tracks/" + part.id, "Archived chords differ from source-authored simultaneous-note groups.",
                   sum(map(len, expected_shapes.values())), sum(map(len, actual_shapes.values())))
    for shape in expected_shapes.keys() & actual_shapes.keys():
        for wanted_time, actual_time in zip(sorted(expected_shapes[shape]), sorted(actual_shapes[shape])):
            check.near("chord_time", "tracks/" + part.id + "/chords", wanted_time, actual_time)


def _compatibility_report(report, score_path, source, check, harmonic_ties=(), tied_mutes=(), projected_parts=None, muted_slides=(), preservation_contract=0, consumed_strums=(), scrape_entries=(), undefined_slides=(), finger_bends=()):
    """Verify retained limitations from source facts, not the producer's inventory."""
    from .verify_source import _program_instrument, fraction, inactive, integer
    rows = report.get("findings")
    if not isinstance(rows, list) or report.get("truncated") is not False:
        check.fail("compatibility", "import/compatibility", "The compatibility report is incomplete.")
        return
    check.equal("compatibility_count", "import/compatibility", len(rows), report.get("findingCount"))
    # Historical Hybrid and ending contracts share the same inventory schema.
    # Preserve independent checks when extending the preservation contract.
    if type(report.get('version')) is not int or report['version'] not in (32, 33, 34, 35, 36, 37, 38, 39, 40, 42, 43, 44, 45, 46, 47, 48, 49, 50, 51, 52, 53, 54, 55, 62, 63, 64, 65, 66, 67, 68, 69, 70, 71, 72, 73, 74, 75, 76, 77, 78, 79, VERSION):
        check.fail('compatibility_version', 'import/compatibility', 'Unsupported compatibility inventory version.')
    reports_precedence = type(report.get('version')) is int and report['version'] >= 46
    if preservation_contract >= 46 and not reports_precedence:
        check.fail('compatibility_version', 'import/compatibility', 'This preservation contract requires tempo precedence accounting.')
    reports_outside = type(report.get('version')) is int and report['version'] >= 48
    if preservation_contract >= 48 and not reports_outside:
        check.fail('compatibility_version', 'import/compatibility', 'This preservation contract requires outside-score tempo accounting.')
    check.equal("compatibility_status", "import/compatibility", "limitations" if rows else "compatible", report.get("status"))
    target = report.get("target", {})
    check.equal("compatibility_target", "import/compatibility", "1.16.0", target.get("feedpak"))
    check.equal("compatibility_target", "import/compatibility/notation", 1, target.get("notation"))
    expected = {}
    for row in finger_bends:
        if row['status'] == 'deferred':
            expected[('note.bend_timing', row['location'] + f"@visit{row['occurrence']}")] = {'reason': row['reason']}
    for row in undefined_slides:
        if row['used']['rule'] == 'omit-slide-skipped-ending-rest':
            expected[('note.slide_skipped_ending', row['location'] + f"@visit{row['occurrence']}")] = {
                k: row[k] for k in ('authored', 'used', 'target', 'transition')}
            continue
        expected[('note.undefined_slide_to_mute', row['location'] + f"@visit{row['occurrence']}")] = {
            **{k: row[k] for k in ('authored', 'used')},
            'target': {k: v for k, v in row['target'].items() if k != 'time'}}
    for row in scrape_entries:
        expected[('note.scrape_entry', row['location'] + f"@visit{row['occurrence']}")] = {k:row[k] for k in ('authored','used')}
    for row in consumed_strums:
        expected[('note.consumed_strum_grace', row['location'] + f"@visit{row['occurrence']}")] = {k:row[k] for k in ('authored','used')}
    for row in muted_slides:
        expected[('note.muted_slide', row['location'] + f"@visit{row['occurrence']}")] = {k:row[k] for k in ('authored','used')}
    for row in tied_mutes:
        expected[('note.tied_mute', row['location'] + f"@visit{row['occurrence']}")] = {k: row[k] for k in ('authored', 'used', 'rule')}
    for row in harmonic_ties:
        expected[('note.tied_harmonic', row['location'] + f"@visit{row['occurrence']}")] = {k: row[k] for k in ('authored', 'used', 'rule')}
    if source.format == "songsterr":
        document = json.loads(score_path.read_text(encoding="utf-8-sig"))

        def remember(obj, fields, scope, path):
            for key in fields:
                if key in obj and not inactive(obj[key]):
                    expected[(scope + "." + key, path + "/" + key)] = obj[key]

        for pi, (meta, part) in enumerate(zip(document["tracks"], document["parts"])):
            raw_tempos = part.get('automations', {}).get('tempo', [])
            final_positions = {}
            # Older packages allowed identical duplicate entries without this
            # diagnostic. Their musical clock is still independently checked.
            for ti in (range(len(raw_tempos) - 1, -1, -1) if reports_precedence else ()):
                tempo = raw_tempos[ti]
                key = integer(tempo['measure']), fraction(tempo.get('position', 0))
                if key in final_positions:
                    winner = final_positions[key]
                    expected[('tempo.superseded', f'parts/{pi}/automations/tempo/{ti}')] = {
                        'authored': tempo, 'used': raw_tempos[winner], 'selectedIndex': winner}
                else:
                    final_positions[key] = ti
            for ti, tempo in enumerate(part.get("automations", {}).get("tempo", [])):
                if integer(tempo['measure']) >= len(part['measures']):
                    if not reports_outside:
                        check.fail('compatibility_version', 'import/compatibility', 'Outside-score tempo needs explicit retention accounting.')
                    expected[('tempo.outside_score', f'parts/{pi}/automations/tempo/{ti}')] = tempo
                remember(tempo, ("text",), "tempo", f"parts/{pi}/automations/tempo/{ti}")
                # Both true and false are authored display preferences. Unlike
                # inactive technique flags, false must remain in the report.
                if isinstance(tempo.get("visible"), bool):
                    expected[("tempo.visible", f"parts/{pi}/automations/tempo/{ti}/visible")] = tempo["visible"]
            for bi, bar in enumerate(part["measures"]):
                path = f"parts/{pi}/measures/{bi}"
                remember(bar, ("doubleBarline", "keySignature"), "measure", path)
                if not _program_instrument(meta):
                    continue
                for vi, voice in enumerate(bar["voices"]):
                    for bti, beat in enumerate(voice["beats"]):
                        where = path + f"/voices/{vi}/beats/{bti}"
                        # Account for retained legacy beat vibrato limitations
                        # even when their playback timing is unconfirmed.
                        remember(beat, ("chord", "wahwah", "letRing", "tremolo", "tremoloBar", "vibratoWithTremoloBar", "vibrato", "wideVibrato", "hasRasgueado", "sustainPedal"), "beat", where)
                        for ni, note in enumerate(beat["notes"]):
                            if (not note.get("rest") and type(note.get("fret")) is int and 24 < note["fret"] <= 48
                                    and not (note.get("dead") is True and note.get("pickScrape") in ("up", "down"))):
                                expected[("note.fret_range", where + f"/notes/{ni}/fret")] = note["fret"]
                            remember(note, ("staccato", "pickScrape", "tremolo", "rightFingering"), "note", where + f"/notes/{ni}")
                            if note.get("tie") or note.get("leftFingering") == "0" and note.get("fret") != 0:
                                remember(note, ("leftFingering",), "note", where + f"/notes/{ni}")
                            if (note.get('harmonic') in ('semi', 'feedback') and note.get('harmonicFret') is not None
                                    or note.get('harmonic') == 'natural' and note.get('fret') == 15
                                    and note.get('harmonicFret') == 15 and note.get('harmonicData') is None):
                                remember(note, ('harmonicFret',), 'note', where + f'/notes/{ni}')
        for part in (projected_parts if projected_parts is not None else source.parts):
            if part.notation_unavailable and any(part.bars):
                expected[("notation.written_rhythm", "tracks/" + part.id)] = None
            if part.unpitched_mutes and any(part.bars):
                expected[("notation.unpitched_mute", "tracks/" + part.id)] = None
        for key, value in source.identity.items():
            check.equal("compatibility_identity", "import/compatibility/source/" + key, value, str(report.get("source", {}).get(key)))
    actual = {(row.get("feature"), row.get("location")): row for row in rows}
    check.equal("compatibility_coverage", "import/compatibility", sorted(expected), sorted(actual))
    check.equal("compatibility_duplicates", "import/compatibility", len(rows), len(actual))
    for key, value in expected.items():
        if key not in actual:
            continue
        row = actual[key]
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
        check.equal("compatibility_value", key[1], value if len(encoded) <= 2048 else encoded[:2048], row.get("value"))
        if key[0] == "beat.hasRasgueado":
            check.equal("compatibility_value", key[1] + "/boolean", True, row.get("value") is True)
        check.equal("compatibility_value", key[1] + "/truncated", len(encoded) > 2048, row.get("valueTruncated"))
        check.equal("compatibility_retention", key[1], "original_source", row.get("retained"))
        check.equal("compatibility_impact", key[1], "gameplay_omission" if key[0] in ("note.fret_range", "note.consumed_strum_grace") else "display_or_expression", row.get("impact"))
        category = ("conversion_check" if key[0] == "note.bend_timing" else
                    "source_interpretation" if key[0] in ("tempo.superseded", "tempo.outside_score", "note.consumed_strum_grace") else "game_limitation")
        check.equal("compatibility_category", key[1], category, row.get("category"))


def _notation(archive, arrangement, wanted, check):
    """Verify available beat time/rest facts without inferring engraving style."""
    name = arrangement.get("notation")
    if wanted.get("consumed_strum_omissions") or wanted.get("high_fret_omissions") or wanted["source"].notation_unavailable or wanted["source"].unpitched_mutes:
        if name:
            check.fail("unsupported_notation", name, "Unsupported source notation must not be replaced with invented pitches or rhythm.")
        return
    if not name:
        raise UnverifiedFeature("tracks/" + wanted["source"].id, "Written source notation is missing; its preservation cannot be independently verified.")
    data = _json(archive, name, check)
    facts = wanted["notation_beats"]
    actual = []
    check.equal("notation_instrument", name, wanted["source"].instrument, data.get("instrument"))
    measures = data.get("measures", [])
    check.equal("notation_measure_count", name, len(wanted["notation_measures"]), len(measures))
    for mi, (a, b) in enumerate(zip(wanted["notation_measures"], measures)):
        loc = name + f"/measures/{mi}"
        check.equal('notation_pickup', loc, a.get('pickup', False), b.get('pickup', False))
        for key, value in a.items():
            if key in {"t", "duration_seconds", "tempo", "written_tempo"}:
                check.near("notation_measure", loc + "/" + key, value, b.get(key), TIME_TOLERANCE if key in {"t", "duration_seconds"} else 1e-6)
            else:
                check.equal("notation_measure", loc + "/" + key, value, b.get(key))
        for staff in b.get("staves", {}).values():
            clefs = wanted["source"].clefs
            expected_clef = clefs[a["source_measure"] - 1] if clefs else None
            check.equal("notation_clef", loc, expected_clef, staff.get("clef"))
            for voice in staff.get("voices", []):
                for beat in voice.get("beats", []):
                    actual.append({**beat, "_measure": mi + 1, "_voice": str(voice.get("v"))})
    check.equal("notation_beat_count", name, len(facts), len(actual))
    # Raw ID spelling is exporter-specific; use stable time/voice-independent
    # multisets so simultaneous voices still retain their multiplicity.
    sorted_facts = sorted(facts, key=lambda f: (f["measure"], f["voice"], f["time"]))
    sorted_actual = sorted(actual, key=lambda b: (b["_measure"], b["_voice"], b["t"]))
    for index, (a, b) in enumerate(zip(sorted_facts, sorted_actual)):
        loc = a["location"] + " -> " + name + f"/beat/{index}"
        check.near("notation_time", loc, a["time"], b.get("t"))
        check.near("notation_duration", loc, a["end"] - a["time"], b.get("duration_seconds"))
        check.equal("notation_rest", loc, a["rest"], bool(b.get("rest")))
        check.equal("notation_voice", loc, a["voice"], b["_voice"])
        check.equal("notation_position", loc + "/beat_pos", [a["quarter"].numerator, a["quarter"].denominator], b.get("beat_pos"))
        for key, value in a["notation"].items():
            check.equal("notation_written_duration", loc + "/" + key, value, b.get(key, 0 if key == "dot" else None))
        for key in set(b) - ({"t", "duration_seconds", "beat_pos", "source_id", "notes", "rest", "_voice", "_measure"} | set(a["notation"])):
            if not inactive(b[key]):
                check.fail("invented_notation_annotation", loc + "/" + key, "A notation annotation absent from the source was introduced.")
        desired_notes = sorted(a["notes"], key=lambda n: (n["str"], n["fret"]))
        actual_notes = sorted(b.get("notes", []), key=lambda n: (n.get("str", -1), n.get("fret", -1)))
        check.equal("notation_note_count", loc, len(desired_notes), len(actual_notes))
        for ni, (an, bn) in enumerate(zip(desired_notes, actual_notes)):
            for key, value in an.items():
                check.equal("notation_note", loc + f"/notes/{ni}/" + key, value, bn.get(key, False if key == "tied" else None))
            for key in set(bn) - (set(an) | {"source_id"}):
                if not inactive(bn[key]):
                    check.fail("invented_notation_note", loc + f"/notes/{ni}/" + key, "A notation note property absent from the source was introduced.")


def _terminal_adjustments(wanted, alignment, recipe, archive, duration, source, check):
    """Recompute permitted changes from independent source facts, never from the ledger.

    Kept separate from the producer's trim helper so a wrong sustain or missing
    gesture cannot validate itself just by being listed in its own report.
    """
    policy = alignment.get("terminalSustains")
    slide_policy = alignment.get("terminalSlides")
    mixed = isinstance(policy,dict) and policy.get('version') == 2
    from .terminal_sustains import slides_policy_for
    slide_authorized = (slide_policy == slides_policy_for(duration)
                        and recipe.get("terminalSlides") == slide_policy
                        and recipe.get("preservationContract", 0) >= 33
                        and alignment.get("recordingEnd") is not None)
    if (slide_policy is not None or recipe.get("terminalSlides") is not None) and not slide_authorized:
        check.fail("slide_cutoff_policy", "import", "The slide-out cutoff has no current recording-end authority.")
    if not policy:
        if recipe.get("terminalSustains") or recipe.get("adjustmentsFile") or slide_policy is not None:
            check.fail("unexpected_adjustments", "import", "The package declares adjustments absent from its verified recording map.")
        return None
    required = {"version": 1, "policy": "trim-final-sustain-v1", "audioDuration": duration}
    if mixed:
        ending=alignment.get('endingPadding',{})
        duration=ending.get('originalDuration',0)+alignment.get('preparation',{}).get('seconds',0)
        required={'version':2,'policy':'trim-long-held-tail-with-padding-v1','audioDuration':duration,'onlyBeyondSeconds':2.0}
        if (ending.get('version')!=2 or ending.get('trimLongHeldTails') is not True
                or recipe.get('preservationContract',0)<37 or slide_policy is not None):
            check.fail('mixed_ending_policy','import','Held-tail shortening requires independent combined-ending authority.')
            return None
    if (policy != required or recipe.get("terminalSustains") != required
            or recipe.get("preservationContract", 0) < 10 or source.format != "songsterr"
            or alignment.get("method") != "songsterr-video-points-v1" or alignment.get("mapping") != "piecewise-linear"):
        check.fail("adjustment_policy", "import", "The final-sustain policy or recording identity is invalid.")
        return None
    cutoff = math.floor(duration * 1_000_000) / 1_000_000
    changes, slide_count = [], 0
    for part in wanted["parts"]:
        for item in part["notes"]:
            note = item["note"]
            start, sustain = note["t"], note.get("sus", 0)
            if mixed and start+sustain <= duration+2.0:
                continue
            if mixed and set(note)-{'s','f','t','sus','mt','vb','ac','pm','lr','ghost','hm','hp','hn','hps','st','tr'}:
                check.fail('mixed_ending_technique',item['locations'][0],'Combined ending cannot shorten a timed gesture.')
                continue
            if start < 0 or start >= duration:
                check.fail("adjustment_attack", item["locations"][0], "A new attack lies outside the recording; it cannot be trimmed.")
                continue
            if start + sustain <= duration + TIME_TOLERANCE:
                continue
            shortened = round(cutoff - start, 6)
            if note.get('harmonic_changes'):
                events = note['harmonic_changes']['events']
                if any(e['start'] >= shortened - TIME_TOLERANCE for e in events):
                    check.fail('adjustment_technique',item['locations'][0],'The audio boundary omits a harmonic contact.')
                    continue
                for event in events: event['end'] = shortened
            if note.get('whammy'):
                invalid = False
                for segment in note['whammy']['segments']:
                    if segment['end'] <= shortened + TIME_TOLERANCE:
                        continue
                    if segment['start'] >= shortened:
                        invalid = True; break
                    earlier = [p for p in segment['curve'] if p['t'] <= shortened]
                    later = [p for p in segment['curve'] if p['t'] > shortened]
                    if later and (not earlier or any(abs(p['v']-earlier[-1]['v']) > 1e-10 for p in later)):
                        invalid = True; break
                    if later:
                        segment['curve'] = earlier + ([{'t':shortened,'v':earlier[-1]['v']}] if earlier[-1]['t'] < shortened else [])
                    segment['end'] = shortened
                if invalid:
                    check.fail('adjustment_technique',item['locations'][0],'The audio boundary crosses an authored bar gesture.')
                    continue
            curve = note.get("bnv", [])
            if any(p["t"] > shortened + TIME_TOLERANCE for p in curve):
                earlier = [p for p in curve if p["t"] <= shortened]
                later = [p for p in curve if p["t"] > shortened]
                if not earlier or any(abs(p["v"] - earlier[-1]["v"]) > 1e-10 for p in later):
                    check.fail("adjustment_technique", item["locations"][0], "The audio boundary crosses a changing bend curve.")
                    continue
                curve = earlier + ([{"t": shortened, "v": earlier[-1]["v"]}] if earlier[-1]["t"] < shortened else [])
            if (shortened <= 0
                    or any(k in note and note[k] is not None and note[k] != -1 for k in ("sl", "slu"))
                    or note.get("slide_out") and not note.get("slide_out_marks")
                    or note.get("bn") and not note.get("bnv")
                    or not slide_authorized and any(p["end"] > shortened + TIME_TOLERANCE for p in note.get("slide_out_marks", []))
                    or any(p["time"] > shortened + TIME_TOLERANCE for p in note.get("slide_in_marks", []))):
                check.fail("adjustment_technique", item["locations"][0], "Shortening this note would change an authored bend or slide.")
                continue
            slide_changes = []
            for index, mark in enumerate(note.get("slide_out_marks", [])):
                if mark["end"] > shortened + TIME_TOLERANCE:
                    if mark["start"] >= shortened:
                        check.fail("slide_cutoff_start", item["locations"][0], "The cutoff would omit a slide segment's start.")
                        continue
                    slide_changes.append({"index": index, "direction": mark["direction"], "start": mark["start"],
                                          "originalEnd": mark["end"], "exportedEnd": shortened})
                    mark["end"] = shortened
            slide_count += len(slide_changes)
            changes.append({"trackId": part["source"].id, "string": note["s"], "fret": note["f"],
                            "audioStart": round(start, 6), "originalDuration": round(sustain, 6),
                            "exportedDuration": shortened, "trimmedSeconds": round(sustain - shortened, 6),
                            **({"slideOuts": slide_changes} if slide_changes else {})})
            note["sus"] = shortened
            if 'vibrato_marks' in note:
                note['vibrato_marks'] = [{**m, 'end': min(m['end'], shortened)}
                    for m in note['vibrato_marks'] if m['start'] < shortened]
            if "pick_scrape_marks" in note:
                note["pick_scrape_marks"] = [{**m, "end": min(m["end"], shortened)}
                    for m in note["pick_scrape_marks"] if m["start"] < shortened]
                if not note["pick_scrape_marks"]:
                    note.pop("pick_scrape_marks")
            if "bnv" in note:
                note["bnv"] = curve
    if not changes:
        check.fail("empty_adjustments", "import", "The package declares sustain adjustments without affected source notes.")
    if slide_policy is not None and not slide_count:
        check.fail("empty_slide_adjustments", "import", "The slide-out cutoff has no affected source interval.")
    detail = _json(archive, recipe.get("adjustmentsFile", ""), check)
    if slide_policy is not None:
        required = {**required, "directionalSlides": slide_policy}
    check.equal("adjustment_header", "import/adjustments", required, {k:v for k,v in detail.items() if k != "notes"})
    recorded = detail.get("notes")
    if not isinstance(recorded, list) or any(not isinstance(n, dict) for n in recorded):
        check.fail("adjustment_entries", "import/adjustments", "The adjustment ledger has invalid entries.")
        return None
    key = lambda n:(str(n.get("trackId")), n.get("string", -1), n.get("audioStart", -1), n.get("fret", -1))
    check.equal("adjustment_count", "import/adjustments", len(changes), len(recorded))
    for index, (a,b) in enumerate(zip(sorted(changes,key=key),sorted(recorded,key=key))):
        where = f"import/adjustments/notes/{index}"
        check.equal("adjustment_fields", where, sorted(a), sorted(b))
        for field, value in a.items():
            if field in {"trackId", "string", "fret", "slideOuts"}:
                check.equal("adjustment_identity", where + "/" + field, value, b.get(field))
            else:
                check.near("adjustment_value", where + "/" + field, value, b.get(field))
    return {"terminalSustains": len(changes), "maxShorteningSeconds": max((n["trimmedSeconds"] for n in changes), default=0),
            **({"terminalSlideOuts": slide_count} if slide_count else {})}


def _ending_adjustments(wanted, alignment, recipe, archive, duration, source, check, manifest, timing):
    """Independently derive omissions, then recheck audio from the raw-source map."""
    policy = alignment.get("recordingEnd")
    if not policy:
        if any(recipe.get(k) for k in ("recordingEnd", "endingOmissionsFile", "recordingSyncFile", "terminalSlides")) or alignment.get("terminalSlides"):
            check.fail("unexpected_ending", "import", "Unapproved ending omissions were declared.")
        return None
    anchors = alignment.get("anchors", [])
    if (source.format != "songsterr" or recipe.get("preservationContract", 0) < 11
            or alignment.get("method") != "songsterr-video-points-v1" or alignment.get("mapping") != "piecewise-linear"
            or len(anchors) != len(wanted["order"]) + 1 or len(anchors) < 2
            or any(not isinstance(a, dict) or any(type(a.get(k)) not in (int, float)
                or not math.isfinite(a[k]) for k in ('score', 'audio')) for a in anchors)
            or any(b['audio'] <= a['audio'] or b['score'] <= a['score'] for a,b in zip(anchors, anchors[1:]))
            or not anchors[0]['audio'] < duration < anchors[-1]['audio']):
        check.fail("ending_boundary", "alignment", "Omissions require a valid source map crossing the recording end.")
        return None
    # The acoustic check is recomputed from independently reconstructed notes
    # and the actual packaged recording. A forged saved status cannot authorize it.
    from .recording_sync import assess, digest, PREVIOUS_VERSION as SYNC_VERSION, LEGACY_VERSION
    stored = _json(archive, recipe.get("recordingSyncFile", ""), check)
    # Derive the policy independently; never trust the producer's cutoff index.
    if (anchors[-2]['audio'] < duration and anchors[-1]['audio']-duration <= 3.0
            and anchors[-1]['audio']-anchors[-2]['audio'] <= 8.0):
        required = {"version": 1, "policy": "cut-at-recording-end-v1", "audioDuration": duration,
                    "finalMeasureStart": anchors[-2]["audio"], "syncEvidenceHash": digest(stored)}
    else:
        if recipe.get('preservationContract', 0) < 56:
            check.fail('ending_boundary', 'alignment', 'A multi-bar cutoff requires contract 56.'); return None
        index = max(i for i,a in enumerate(anchors) if a['audio'] <= duration)
        required = {'version': 2, 'policy': 'cut-at-recording-end-v2', 'audioDuration': duration,
                    'cutoffMeasureIndex': index, 'cutoffMeasureStart': anchors[index]['audio'],
                    'mappedScoreEnd': anchors[-1]['audio'], 'syncEvidenceHash': digest(stored)}
    warning = policy.get('timingWarning')
    if warning:
        from .cutoff_warning import acceptance, matches_recording
        expected_warning = acceptance(stored, alignment)
        if (recipe.get('preservationContract', 0) < 57 or expected_warning is None
                or not matches_recording(alignment, recipe.get('audioSource', {}))
                or warning != expected_warning):
            check.fail('ending_warning', 'import', 'The timing warning has no matching source recording evidence.')
            return None
        required['timingWarning'] = expected_warning
    if (policy != required or recipe.get("recordingEnd") != required
            or alignment.get("recordingSync") != stored or stored.get("version") not in (SYNC_VERSION, LEGACY_VERSION)
            or (stored.get("status") != "supported" and not warning)
            or stored.get("mapHash") != alignment.get("provenance", {}).get("mapHash")):
        check.fail("ending_policy", "import", "The ending cutoff has no matching current timing evidence.")
        return None
    tracks, omissions = [], []
    for part in wanted["parts"]:
        src = part["source"]
        events = []
        for item in part.get("sync_notes", part["notes"]):
            n = item["note"]
            events.append({"t": n["t"], "end": n["t"] + n.get("sus", 0),
                           "midi": src.tuning[n["s"]] + src.capo + n["f"] if n["f"] != 127 else None,
                           "effects": {k: v for k, v in n.items() if k not in {"t", "sus", "s", "f"}}})
        tracks.append({"id": src.id, "instrument": src.instrument, "events": events})
        kept = [item for item in part["notes"] if item["note"]["t"] < duration]
        for item in part.get("sync_notes", part["notes"]):
            n = item["note"]
            if n["t"] >= duration:
                omissions.append({"trackId": src.id, "string": n["s"], "fret": n["f"],
                                  "audioStart": round(n["t"], 6), "originalDuration": round(n.get("sus", 0), 6)})
        if part["notes"] and not kept:
            check.fail("ending_track", "tracks/" + src.id, "The cutoff would remove an entire source arrangement.")
        part["notes"] = kept
    # A current directional-tail cutoff still needs full independent acoustic
    # verification when there are no late attacks. Derive that fact from source.
    has_slide_tail = any(n["note"]["t"] < duration < n["note"]["t"] + m["end"]
                         and n["note"]["t"] + m["start"] < duration
                         for p in wanted["parts"] for n in p["notes"]
                         for m in n["note"].get("slide_out_marks", []))
    if not omissions and not (has_slide_tail and alignment.get("terminalSlides") and recipe.get("preservationContract", 0) >= 33):
        check.fail("empty_ending", "import", "The declared cutoff has no omitted source notes.")
    ledger = _json(archive, recipe.get("endingOmissionsFile", ""), check)
    check.equal("ending_header", "import/ending", required, {k: v for k, v in ledger.items() if k != "notes"})
    recorded = ledger.get("notes")
    if not isinstance(recorded, list) or any(not isinstance(n, dict) for n in recorded):
        check.fail("ending_entries", "import/ending", "Ending omissions have invalid entries.")
        return None
    key = lambda n: (str(n.get("trackId")), n.get("string", -1), n.get("audioStart", -1), n.get("fret", -1))
    check.equal("ending_count", "import/ending", len(omissions), len(recorded))
    for i, (a, b) in enumerate(zip(sorted(omissions, key=key), sorted(recorded, key=key))):
        check.equal("ending_fields", f"import/ending/{i}", sorted(a), sorted(b))
        for field, value in a.items():
            if field in {"audioStart", "originalDuration"}:
                check.near("ending_value", f"import/ending/{i}/{field}", value, b.get(field))
            else:
                check.equal("ending_identity", f"import/ending/{i}/{field}", value, b.get(field))
    full = [s for s in manifest.get("stems", []) if s.get("id") == "full"]
    if len(full) != 1:
        check.fail("ending_audio", "manifest/stems", "A cutoff requires one full recording.")
    else:
        audio_bytes = archive.read(full[0]["file"])
        check.equal("ending_audio_identity", "import/recording-sync", stored.get("audioSha256"), hashlib.sha256(audio_bytes).hexdigest())
        if not check.total_errors:
            offset=alignment.get('preparation',{}).get('seconds',0)
            fresh = assess(tracks, io.BytesIO(audio_bytes), duration, alignment["provenance"]["mapHash"],
                           **({'analysis_origin':offset} if offset else {}), clock_version=stored['version'])
            timing["independentAudioMatchAssessed"] = True
            if warning:
                check.equal('ending_audio_warning', 'import/recording-sync/timingWarning', warning, acceptance(fresh, alignment))
                timing['timingWarning'] = warning
                timing['recordingSyncStatus'] = fresh['status']
            else:
                check.equal("ending_audio_sync", "import/recording-sync", "supported", fresh["status"])
    return {"omittedEndingNotes": len(omissions)}


def verify_import(score_path: Path, archive: Path, alignment: dict, metadata: dict | None = None, *, hybrid_options: dict | None = None, guidance_policy: str | None = None) -> dict:
    """Compare an immutable raw score with a completed staged FeedPak.

    Does not mutate sources, archive or alignment; performs no network I/O.
    ``unsupported`` means the independent checker lacks a source interpretation,
    never that an unusual but preserved musical choice was judged incorrect.
    """
    check = Check()
    report = {"version": VERSION, "status": "failed", "errors": check.errors, "warnings": [], "counts": {},
              "scope": ["source_identity", "track_coverage", "physical_pitch", "note_timing", "techniques", "bend_curves",
                        "slide_out_segment_timing", "slide_in_destination_timing", "pick_scrape_intervals", "authored_chord_groups", "beats", "meter", "sections", "tempo", "notation_beat_timing",
                        "notation_written_rhythm", "notation_pitch_and_ties", "container_references"],
              "rounding": {"secondsDecimalPlaces": 6, "comparisonToleranceSeconds": TIME_TOLERANCE},
              "musicalQualityAssessed": False}
    try:
        score_path, archive = Path(score_path), Path(archive)
        source = read_source(score_path)
        report["sourceSha256"] = hashlib.sha256(score_path.read_bytes()).hexdigest()
        report["format"] = source.format
        metadata = metadata or {}
        for key, value in source.identity.items():
            if metadata.get(key) is not None:
                check.equal("source_identity", "metadata/" + key, value, str(metadata[key]))
            provenance = alignment.get("provenance", {})
            if provenance.get(key) is not None:
                check.equal("timing_identity", "alignment/" + key, value, str(provenance[key]))
        wanted = expected(source, alignment)
        report["counts"].update(sourceTracks=source.track_count, selectedTracks=len(wanted["parts"]),
                                excludedTracks=len(source.excluded), writtenMeasures=len(source.bars),
                                performedMeasures=len(wanted["order"]), sourceNotes=wanted["raw_notes"],
                                tieSegments=wanted["tie_segments"], expectedNotes=sum(len(p["notes"]) for p in wanted["parts"]))
        report["excludedTracks"] = source.excluded
        report["uncomparedSourceAnnotations"] = sorted(source.ignored)
        report["timing"] = {"method": str(alignment.get("method", "unspecified")),
                            "independentAudioMatchAssessed": False,
                            "scoreDuration": wanted["score_duration"], "mappedEnd": wanted["mapped_end"]}
        with ZipFile(archive) as z:
            names = z.namelist()
            if len(set(names)) != len(names):
                check.fail("duplicate_archive_entry", "archive", "Archive contains duplicate entry names.")
            if len(names) > 100_000 or sum(i.file_size for i in z.infolist()) > 2 * 1024 * 1024 * 1024:
                raise ValueError("archive: expanded verification size limit exceeded")
            for name in names:
                p = PurePosixPath(name)
                if p.is_absolute() or ".." in p.parts or "\\" in name or ":" in name:
                    check.fail("unsafe_archive_path", "archive", "Archive contains an unsafe entry path.")
            if z.getinfo("manifest.yaml").file_size > 1024 * 1024:
                raise ValueError("manifest: verification size limit exceeded")
            manifest = yaml.safe_load(z.read("manifest.yaml"))
            _finite(manifest, "manifest", check)
            recipe = manifest.get("song_import", {})
            if any(n['note'].get('slide_interval') for p in wanted['parts'] for n in p['notes']):
                if recipe.get('preservationContract', 0) < 80:
                    check.fail('contract', 'manifest', 'Targeted slide intervals require preservation contract 80.')
            if alignment.get('timingAssessment') or recipe.get('timingAssessmentFile'):
                from .local_sync import assess, tracks_from_expected, compare_assessment
                # The independent evaluator maps exact rational source times.
                # Re-mapping its rounded unaligned output could put a terminal
                # sustain just outside the score domain or shift an FFT frame.
                tracks=tracks_from_expected(wanted)
                full=[s for s in manifest.get('stems',[]) if s.get('id')=='full']
                if len(full)!=1:raise ValueError('Timing assessment requires one full recording.')
                stored=_json(z,recipe.get('timingAssessmentFile',''),check)
                check.equal('timing_assessment_receipt','import/timing-assessment',alignment.get('timingAssessment'),stored)
                path,duration=io.BytesIO(z.read(full[0]['file'])),manifest['duration']
                if alignment.get('endingPadding'):
                    from .ending_padding import original_tracks
                    from .preparation import recording_view
                    tracks=original_tracks(tracks,alignment['preparation']['seconds'])
                    path=recording_view(path,alignment['preparation'])
                    duration=alignment['endingPadding']['originalDuration']
                fresh=assess(tracks,path,duration,alignment['provenance']['mapHash'])
                compare_assessment(fresh,stored,check)
                report['timing']['acousticAssessment']={k:fresh[k] for k in
                    ('version','status','everyNoteVerified','windowCount','supportedWindows','suspectedMismatchWindows')}
            if alignment.get('openingRepair') or recipe.get('openingRepairFile'):
                from .local_sync import verify_repair
                from .preparation import recording_view
                full=[s for s in manifest.get('stems',[]) if s.get('id')=='full']
                if len(full)!=1:raise ValueError('Opening repair requires one full recording.')
                recording=recording_view(io.BytesIO(z.read(full[0]['file'])),alignment.get('preparation'))
                verify_repair(source,alignment,_json(z,recipe.get('openingRepairFile',''),check),recording,check)
                report['scope'].append('verified_local_opening_repair')
            if recipe.get('pickupTimelineFile') or recipe.get('preservationContract', 0) >= 34 and any(b.pickup for b in source.bars):
                from .verify_pickup import verify as verify_pickup
                check.equal('pickup_clock', 'manifest/song_import/pickupTimelineFile',
                            'import/pickup-timeline.json', recipe.get('pickupTimelineFile'))
                verify_pickup(source, alignment, _json(z, recipe.get('pickupTimelineFile', ''), check),
                              report['sourceSha256'], check)
                report['scope'].append('pickup_display_clock')
            from copy import deepcopy
            hybrid_facts = deepcopy(wanted) if recipe.get("hybridLead", {}).get("enabled") else None
            if "hasRasgueado" in source.ignored and recipe.get("preservationContract", 0) < 5:
                check.fail("retained_rasgueado", "import", "The rasgueado instruction requires retained source and a verified compatibility report.")
            if recipe.get('preservationContract',0) < 27 and not recipe.get('voicesFile'):
                # Verification of historical archives retains their old voice
                # contract. New imports always declare the projection receipt.
                from .verify_timeline import _expected
                wanted = _expected(source, alignment)
                report['counts'].update(selectedTracks=len(wanted['parts']),expectedNotes=sum(len(p['notes']) for p in wanted['parts']))
            if wanted.get('voice_projection') or recipe.get('voicesFile'):
                from .verify_voices import verify as verify_voices
                check.equal('voice_projection','manifest/song_import/voicesFile','import/voices.json',recipe.get('voicesFile'))
                retained = _json(z,recipe.get('voicesFile',''),check)
                verify_voices(wanted.get('voice_projection',{}),retained,report['sourceSha256'],check)
                report['scope'].append('authored_voice_arrangements')
            if recipe.get('strumsFile') or recipe.get('preservationContract',0) >= 26 and wanted['strums']:
                from .verify_timeline import RecordingMap
                recording = RecordingMap(alignment)
                retained = _json(z, recipe.get('strumsFile',''), check)
                check.equal('strum_evidence','import/strums/version',1,retained.get('version'))
                check.equal('strum_evidence','import/strums/timeDomain','recording_seconds',retained.get('timeDomain'))
                check.equal('strum_evidence','import/strums/sourceSha256',report['sourceSha256'],retained.get('sourceSha256'))
                groups = retained.get('groups')
                if not isinstance(groups,list): raise ValueError('Missing strum groups')
                check.equal('strum_evidence','import/strums/count',len(wanted['strums']),len(groups))
                for i,(a,b) in enumerate(zip(wanted['strums'],groups)):
                    where=f'import/strums/{i}'
                    check.equal('strum_evidence',where+'/keys',sorted(a),sorted(b))
                    for key in ('trackId','sourceId','occurrence','direction','kind'):
                        check.equal('strum_evidence',where+'/'+key,a[key],b.get(key))
                    check.near('strum_time',where,recording.at(a['time']),b.get('time'))
                    check.equal('strum_evidence',where+'/count',len(a['notes']),len(b['notes']))
                    for n,m in zip(a['notes'],b['notes']):
                        check.equal('strum_evidence',where+'/note-keys',sorted(n),sorted(m))
                        for key in ('s','f'): check.equal('strum_evidence',where+'/'+key,n[key],m.get(key))
                        check.near('strum_time',where+'/note',recording.at(n['t']),m.get('t'))
                report['scope'].append('authored_strum_display_groups')
            if (recipe.get('preservationContract', 0) >= 20
                    and alignment.get('method') == 'songsterr-video-points-v1'):
                from .verify_synchronization import verify_source_timing
                timing = _json(z, recipe.get('sourceTimingFile', ''), check)
                verify_source_timing(source, alignment, recipe, timing, check, strums=wanted['strums'])
                report['scope'].append('retained_source_timing_boundaries')
            if recipe.get('preservationContract', 0) >= 18 and source.format == 'songsterr':
                check.equal('section_provenance', 'manifest/song_import/sourceMetadata/sectionLabels',
                            source.section_labels, recipe.get('sourceMetadata', {}).get('sectionLabels'))
            if recipe.get("preservationContract", 0) >= 4:
                original = recipe.get("sourceFile")
                if original not in names or z.getinfo(original).file_size > 80 * 1024 * 1024:
                    check.fail("retained_source", "import", "The original source is missing or too large.")
                else:
                    check.equal("retained_source", original, report["sourceSha256"], hashlib.sha256(z.read(original)).hexdigest())
                compatibility = _json(z, recipe.get("compatibilityFile", ""), check)
                if compatibility.get("status") not in {"compatible", "limitations"}:
                    check.fail("compatibility", "import/compatibility", "A blocked or invalid compatibility report cannot be published.")
                if recipe.get("preservationContract", 0) >= 5:
                    _compatibility_report(compatibility, score_path, source, check, wanted['harmonic_ties'], wanted['tied_mutes'],
                                          [p['source'] for p in wanted['parts']], wanted['muted_slides'], recipe.get('preservationContract', 0), wanted['consumed_strums'], wanted['scrape_entries'], wanted['undefined_slides'], wanted['finger_bends'])
            elif any(p.notation_unavailable or p.unpitched_mutes for p in source.parts):
                check.fail("retained_notation", "import", "A notation limitation requires embedded original source and a compatibility report.")
            if wanted['undefined_slides'] or recipe.get('undefinedSlidesFile'):
                if recipe.get('preservationContract', 0) < 54:
                    check.fail('undefined_slides', 'manifest/song_import', 'Undefined slide omissions require preservation contract 54.')
                retained = _json(z, recipe.get('undefinedSlidesFile', ''), check)
                skipped = any(r['used']['rule'] == 'omit-slide-skipped-ending-rest' for r in wanted['undefined_slides'])
                if skipped and recipe.get('preservationContract', 0) < 55:
                    check.fail('undefined_slides', 'manifest/song_import', 'Skipped-ending slide omissions require contract 55.')
                evidence = {'version': 2 if skipped else 1, 'policy': 'undefined-slides-v2' if skipped else 'undefined-slide-to-mute-v1', 'timeDomain': 'score_seconds',
                            'sourceSha256': report['sourceSha256'], 'gestures': wanted['undefined_slides']}
                from .verify_policy_receipt import compare
                compare(evidence, retained, check, 'undefined_slides')
                report['scope'].append('undefined_slide_omission_accounting')
            if wanted['scrape_entries'] or recipe.get('scrapeEntriesFile'):
                if recipe.get('preservationContract', 0) < 53:
                    check.fail('scrape_entries', 'manifest/song_import', 'Scrape entry retention requires preservation contract 53.')
                retained = _json(z, recipe.get('scrapeEntriesFile', ''), check)
                evidence = {'version': 1, 'policy': 'unpitched-scrape-entry-v1', 'timeDomain': 'score_seconds',
                            'sourceSha256': report['sourceSha256'], 'gestures': wanted['scrape_entries']}
                from .verify_policy_receipt import compare
                compare(evidence, retained, check, 'scrape_entries')
                report['scope'].append('unpitched_scrape_entry_retention')
            if wanted['consumed_strums'] or recipe.get('consumedStrumsFile'):
                if recipe.get('preservationContract', 0) < 52:
                    check.fail('consumed_strums', 'manifest/song_import', 'Consumed notes require preservation contract 52.')
                retained = _json(z, recipe.get('consumedStrumsFile', ''), check)
                evidence = {'version': 1, 'policy': 'consumed-strum-grace-v1', 'timeDomain': 'score_seconds',
                            'sourceSha256': report['sourceSha256'], 'omissions': wanted['consumed_strums']}
                from .verify_policy_receipt import compare
                compare(evidence, retained, check, 'consumed_strums')
                report['scope'].append('consumed_strum_grace_accounting')
            if wanted['trills'] or recipe.get('trillsFile'):
                retained = _json(z, recipe.get('trillsFile', ''), check)
                tied_trills = any(t.get('mode') == 'native-tied-segments-v1' for t in wanted['trills'])
                if tied_trills and recipe.get('preservationContract', 0) < 49:
                    check.fail('trill_evidence', 'import/trills', 'Tied trill expansion requires preservation contract 49.')
                evidence = {
                    'version': 2 if tied_trills else 1,
                    'policy': 'songsterr-trill-hopo-v2' if tied_trills else 'songsterr-trill-hopo-v1', 'timeDomain': 'quarter_notes',
                    'sourceSha256': report['sourceSha256'], 'trills': wanted['trills']}
                # JSON identity distinguishes nested true/1 and 1/1.0. A plain
                # Python dict comparison does not. Keep a corrupt large receipt
                # out of the error report rather than echoing both whole trees.
                if json.dumps(evidence, sort_keys=True) != json.dumps(retained, sort_keys=True):
                    check.fail('trill_evidence', 'import/trills',
                               'The retained trill expansion differs from the independently reconstructed source evidence.')
                report['scope'].append('source_trill_expansion')
            if wanted['muted_slides'] or recipe.get('mutedSlidesFile'):
                if recipe.get('preservationContract', 0) < 28:
                    check.fail('muted_slide_evidence', 'manifest/song_import', 'Muted slides require preservation contract 28.')
                retained = _json(z, recipe.get('mutedSlidesFile', ''), check)
                evidence = {'version':1, 'policy':'songsterr-muted-slides-v1', 'timeDomain':'score_seconds',
                            'sourceSha256':report['sourceSha256'], 'gestures':wanted['muted_slides']}
                def stable(value):
                    if type(value) is float:return round(value,6)
                    if isinstance(value,list):return [stable(v) for v in value]
                    if isinstance(value,dict):return {k:stable(v) for k,v in value.items()}
                    return value
                if json.dumps(stable(evidence),sort_keys=True) != json.dumps(stable(retained),sort_keys=True):
                    check.fail('muted_slide_evidence', 'import/muted-slides', 'Muted slide evidence differs from the independently reconstructed source.')
                report['scope'].append('muted_slide_interpretation')
            if any('vibrato_marks' in n['note'] for p in wanted['parts'] for n in p['notes']):
                if recipe.get('preservationContract', 0) < 62:
                    check.fail('vibrato_contract', 'manifest/song_import', 'Timed vibrato requires preservation contract 62.')
                report['scope'].append('timed_finger_vibrato')
            if wanted['finger_bends'] or recipe.get('fingerBendTimingFile'):
                if recipe.get('preservationContract', 0) < 61:
                    check.fail('finger_bend_timing', 'manifest/song_import', 'Finger bend timing requires preservation contract 61.')
                retained = _json(z, recipe.get('fingerBendTimingFile', ''), check)
                changing_slide = any(e.get('rule') == 'bend-with-slide-out' for e in wanted['finger_bends'])
                chord_slide = any(e.get('terminalSlideOut', {}).get('attackTiming') == 'authored-chord' for e in wanted['finger_bends'])
                following_slide = any(e.get('terminalSlideOut', {}).get('endTiming') == 'authored-tie' for e in wanted['finger_bends'])
                if following_slide and recipe.get('preservationContract', 0) < 65:
                    check.fail('finger_bend_timing', 'manifest/song_import', 'Bends followed by a slide-in require preservation contract 65.')
                if chord_slide and recipe.get('preservationContract', 0) < 64:
                    check.fail('finger_bend_timing', 'manifest/song_import', 'Chord bends with a terminal slide require preservation contract 64.')
                if changing_slide and recipe.get('preservationContract', 0) < 63:
                    check.fail('finger_bend_timing', 'manifest/song_import', 'Changing bends with a terminal slide require preservation contract 63.')
                vibrato_handoff = any(e.get('overlap', {}).get('vibratoTiming') == 'independent-note-controls' for e in wanted['finger_bends'])
                if vibrato_handoff and recipe.get('preservationContract', 0) < 66:
                    check.fail('finger_bend_timing', 'manifest/song_import', 'Bend handoffs with timed vibrato require preservation contract 66.')
                initial_slide = any(e.get('initialSlideIn') for e in wanted['finger_bends'])
                if initial_slide and recipe.get('preservationContract', 0) < 67:
                    check.fail('finger_bend_timing', 'manifest/song_import', 'Bends with an initial slide-in require preservation contract 67.')
                terminal_bend = any(e.get('terminalSlideOut', {}).get('bendTiming') == 'authored-segment' for e in wanted['finger_bends'])
                if terminal_bend and recipe.get('preservationContract', 0) < 68:
                    check.fail('finger_bend_timing', 'manifest/song_import', 'Terminal slide-out segments with bend controls require preservation contract 68.')
                overlap_slide = any(e.get('overlap', {}).get('slideOutTiming') == 'independent-terminal-cue' for e in wanted['finger_bends'])
                if overlap_slide and recipe.get('preservationContract', 0) < 69:
                    check.fail('finger_bend_timing', 'manifest/song_import', 'Bend handoffs with a terminal slide-out require preservation contract 69.')
                boundary_slides = any(e.get('initialSlideIn') and e.get('terminalSlideOut') for e in wanted['finger_bends'])
                if boundary_slides and recipe.get('preservationContract', 0) < 70:
                    check.fail('finger_bend_timing', 'manifest/song_import', 'Bends with incoming and terminal slide cues require preservation contract 70.')
                fixed_harmonic = any(e.get('overlap', {}).get('fixedHarmonic') for e in wanted['finger_bends'])
                if fixed_harmonic and recipe.get('preservationContract', 0) < 71:
                    check.fail('finger_bend_timing', 'manifest/song_import', 'Fixed harmonics with overlapping bends require preservation contract 71.')
                continued_pinch = any(e.get('overlap', {}).get('continuedPinchHarmonic') for e in wanted['finger_bends'])
                if continued_pinch and recipe.get('preservationContract', 0) < 72:
                    check.fail('finger_bend_timing', 'manifest/song_import', 'Continued pinch harmonics with overlapping bends require preservation contract 72.')
                outgoing_legato = any(e.get('overlap', {}).get('outgoingLegato') for e in wanted['finger_bends'])
                if outgoing_legato and recipe.get('preservationContract', 0) < 74:
                    check.fail('finger_bend_timing', 'manifest/song_import', 'Settled bends with outgoing legato require preservation contract 74.')
                bar_vibrato = any(e.get('barVibrato') for e in wanted['finger_bends'])
                if bar_vibrato and recipe.get('preservationContract', 0) < 75:
                    check.fail('finger_bend_timing', 'manifest/song_import', 'Finger bends with qualitative bar vibrato require preservation contract 75.')
                pinch_slide = any(e.get('terminalSlideOut', {}).get('continuedPinchHarmonic') for e in wanted['finger_bends'])
                if pinch_slide and recipe.get('preservationContract', 0) < 76:
                    check.fail('finger_bend_timing', 'manifest/song_import', 'Pinch-harmonic bends with terminal slide-outs require preservation contract 76.')
                beat_slide = any(e.get('terminalSlideOut', {}).get('beatVibrato') for e in wanted['finger_bends'])
                if beat_slide and recipe.get('preservationContract', 0) < 77:
                    check.fail('finger_bend_timing', 'manifest/song_import', 'Bends with written beat vibrato and terminal slides require preservation contract 77.')
                bar_curve = any(e.get('barCurve') for e in wanted['finger_bends'])
                if bar_curve and recipe.get('preservationContract', 0) < 78:
                    check.fail('finger_bend_timing', 'manifest/song_import', 'Independent finger bends with explicit bar curves require preservation contract 78.')
                targeted_slide = any(e.get('targetedSlide') for e in wanted['finger_bends'])
                if targeted_slide and recipe.get('preservationContract', 0) < 80:
                    check.fail('finger_bend_timing', 'manifest/song_import', 'Targeted slide bend timing requires preservation contract 80.')
                bar_slide = any(e.get('barCurve') and e.get('terminalSlideOut') for e in wanted['finger_bends'])
                if bar_slide and recipe.get('preservationContract', 0) < 81:
                    check.fail('finger_bend_timing', 'manifest/song_import', 'Bends with bar curves and terminal slides require preservation contract 81.')
                artificial_slide = any(e.get('terminalSlideOut', {}).get('continuedArtificialHarmonic') for e in wanted['finger_bends'])
                if artificial_slide and recipe.get('preservationContract', 0) < 82:
                    check.fail('finger_bend_timing', 'manifest/song_import', 'Artificial harmonic bends with terminal slides require preservation contract 82.')
                bend_version = 21 if artificial_slide else 20 if bar_slide else 19 if targeted_slide else 18 if bar_curve else 17 if beat_slide else 16 if pinch_slide else 15 if bar_vibrato else 14 if outgoing_legato else 13 if continued_pinch else 12 if fixed_harmonic else 11 if boundary_slides else 10 if overlap_slide else 9 if terminal_bend else 8 if initial_slide else 7 if vibrato_handoff else 6 if following_slide else 5 if chord_slide else 4 if changing_slide else 3
                evidence = {'version': bend_version, 'policy': f'songsterr-finger-bend-timing-v{bend_version}',
                            'timeDomain': 'score_seconds', 'sourceSha256': report['sourceSha256'],
                            'gestures': wanted['finger_bends']}
                from .verify_bend_timing import check_evidence
                check_evidence(evidence, retained, check)
                report['scope'].append('finger_bend_timing')
            if wanted['staccato_bends'] or recipe.get('staccatoBendsFile'):
                if recipe.get('preservationContract', 0) < 31:
                    check.fail('staccato_bends', 'manifest/song_import', 'Tied staccato bends require preservation contract 31.')
                retained = _json(z, recipe.get('staccatoBendsFile', ''), check)
                evidence = {'version': 1, 'policy': 'songsterr-staccato-bends-v1',
                            'timeDomain': 'score_seconds', 'sourceSha256': report['sourceSha256'],
                            'chains': wanted['staccato_bends']}
                def stable_staccato(value):
                    if type(value) is float: return round(value, 6)
                    if isinstance(value, list): return [stable_staccato(v) for v in value]
                    if isinstance(value, dict): return {k: stable_staccato(v) for k, v in value.items()}
                    return value
                if json.dumps(stable_staccato(evidence), sort_keys=True) != json.dumps(stable_staccato(retained), sort_keys=True):
                    check.fail('staccato_bends', 'import/staccato-bends', 'Tied staccato bend evidence differs from the independently reconstructed source.')
                report['scope'].append('staccato_bend_timing')
            if wanted['muted_tie_identities'] or recipe.get('mutedTieIdentityFile'):
                if recipe.get('preservationContract', 0) < 30:
                    check.fail('muted_tie_identity', 'manifest/song_import', 'Muted tie identity requires preservation contract 30.')
                retained = _json(z, recipe.get('mutedTieIdentityFile', ''), check)
                evidence = {'version': 1, 'policy': 'songsterr-muted-tie-identity-v1',
                            'timeDomain': 'score_seconds', 'sourceSha256': report['sourceSha256'],
                            'continuations': wanted['muted_tie_identities']}
                def stable_muted_tie(value):
                    if type(value) is float: return round(value, 6)
                    if isinstance(value, list): return [stable_muted_tie(v) for v in value]
                    if isinstance(value, dict): return {k: stable_muted_tie(v) for k, v in value.items()}
                    return value
                if json.dumps(stable_muted_tie(evidence), sort_keys=True) != json.dumps(stable_muted_tie(retained), sort_keys=True):
                    check.fail('muted_tie_identity', 'import/muted-tie-identity', 'Muted tie evidence differs from the independently reconstructed source.')
                report['scope'].append('muted_tie_identity')
            if wanted['tied_mutes'] or recipe.get('tiedMutesFile'):
                if recipe.get('preservationContract', 0) < 25:
                    check.fail('mute_evidence', 'manifest/song_import', 'Tied mute interpretation requires preservation contract 25.')
                retained = _json(z, recipe.get('tiedMutesFile', ''), check)
                identity = {'version': 1, 'policy': 'songsterr-tied-mutes-v1',
                            'timeDomain': 'score_seconds', 'sourceSha256': report['sourceSha256']}
                check.equal('mute_evidence', 'import/tied-mutes/keys', sorted([*identity, 'continuations']), sorted(retained))
                for key, value in identity.items():
                    check.equal('mute_evidence', 'import/tied-mutes/' + key, value, retained.get(key))
                rows = retained.get('continuations')
                if not isinstance(rows, list):
                    raise ValueError('import/tied-mutes: missing continuations')
                check.equal('mute_evidence', 'import/tied-mutes/count', len(wanted['tied_mutes']), len(rows))
                for i, (a, b) in enumerate(zip(wanted['tied_mutes'], rows)):
                    where = f'import/tied-mutes/{i}'
                    if not isinstance(b, dict):
                        check.fail('mute_evidence', where, 'A continuation evidence row must be an object.')
                        continue
                    check.equal('mute_evidence', where, sorted(a), sorted(b))
                    for key in a:
                        if key in {'attack', 'start', 'end'}:
                            check.near('mute_evidence_time', where + '/' + key, a[key], b.get(key))
                        else:
                            check.equal('mute_evidence', where + '/' + key,
                                        json.dumps(a[key], sort_keys=True), json.dumps(b.get(key), sort_keys=True))
                report['scope'].append('tied_mute_interpretation')
            if wanted['harmonic_ties'] or recipe.get('tiedHarmonicsFile'):
                retained = _json(z, recipe.get('tiedHarmonicsFile', ''), check)
                check.equal('harmonic_evidence', 'import/tied-harmonics/version', 2, retained.get('version'))
                check.equal('harmonic_evidence', 'import/tied-harmonics/policy', 'songsterr-tied-harmonics-v2', retained.get('policy'))
                check.equal('harmonic_evidence', 'import/tied-harmonics/timeDomain', 'score_seconds', retained.get('timeDomain'))
                check.equal('harmonic_evidence', 'import/tied-harmonics/sourceSha256', report['sourceSha256'], retained.get('sourceSha256'))
                rows = retained.get('continuations')
                if not isinstance(rows, list):
                    raise ValueError('import/tied-harmonics: missing continuations')
                check.equal('harmonic_evidence', 'import/tied-harmonics/count', len(wanted['harmonic_ties']), len(rows))
                for i, (a, b) in enumerate(zip(wanted['harmonic_ties'], rows)):
                    where = f'import/tied-harmonics/{i}'
                    if not isinstance(b, dict):
                        check.fail('harmonic_evidence', where, 'A continuation evidence row must be an object.')
                        continue
                    check.equal('harmonic_evidence', where, sorted(a), sorted(b))
                    for key in a:
                        if key in {'attack', 'start', 'end'}:
                            check.near('harmonic_evidence_time', where + '/' + key, a[key], b.get(key))
                        else:
                            check.equal('harmonic_evidence', where + '/' + key, a[key], b.get(key))
                report['scope'].append('tied_harmonic_interpretation')
            if isinstance(recipe, dict):
                if recipe.get("scoreHash"):
                    check.equal("source_hash", "manifest/song_import/scoreHash", report["sourceSha256"], recipe["scoreHash"])
                for key, value in source.identity.items():
                    if recipe.get(key) is not None:
                        check.equal("source_identity", "manifest/song_import/" + key, value, str(recipe[key]))
            for key in ("title", "artist"):
                check.equal("metadata", "manifest/" + key, str(metadata.get(key) or getattr(source, key)).strip(), manifest.get(key))
            duration = manifest.get("duration")
            if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration <= 0:
                raise ValueError("manifest/duration: invalid audio duration")
            from .verify_high_frets import verify as verify_omissions
            omissions = verify_omissions(source, wanted, score_path, recipe, z, check, _json)
            if omissions:
                report["omissions"] = omissions
                report["scope"].append("declared_high_fret_gameplay_omissions")
                check.equal("omission_compatibility", "import/compatibility", omissions, compatibility.get("omissions"))
                report["counts"]["expectedPlayableNotes"] = sum(len(p["notes"]) for p in wanted["parts"])
            ending_adjustments = _ending_adjustments(wanted, alignment, recipe, z, duration, source, check, manifest, report["timing"])
            from .preparation import verify as verify_preparation
            verify_preparation(wanted,alignment,recipe,z,manifest,check)
            from .ending_padding import verify as verify_ending_padding
            verify_ending_padding(wanted,alignment,recipe,z,manifest,check)
            if alignment.get('endingPadding'):
                report['scope'].append('verified_short_ending_silence')
                warning = alignment['endingPadding'].get('timingWarning')
                if warning and not check.total_errors:
                    report['warnings'].append({'code':'ending_sync_unconfirmed', 'location':'audio/ending',
                                               'message':warning['message']})
                    report['scope'].append('ending_timing_accepted_with_warning')
            adjustments = _terminal_adjustments(wanted, alignment, recipe, z, duration, source, check)
            if ending_adjustments:
                adjustments = {**(adjustments or {}), **ending_adjustments}
                report["counts"]["expectedPlayableNotes"] = sum(len(p["notes"]) for p in wanted["parts"])
            if adjustments:
                report["adjustments"] = adjustments
                # An adjustment must use the real packaged audio boundary, not
                # an invented manifest duration that happens to excuse a cut.
                import soundfile as sf
                full = [stem for stem in manifest.get("stems", []) if stem.get("id") == "full"]
                if len(full) != 1:
                    check.fail("adjustment_audio", "manifest/stems", "A final sustain needs one full recording.")
                else:
                    try:
                        info = sf.info(io.BytesIO(z.read(full[0]["file"])))
                        check.near("adjustment_audio_duration", "manifest/duration", info.frames / info.samplerate,
                                   duration, 1 / info.samplerate + TIME_TOLERANCE)
                    except (RuntimeError, ValueError) as exc:
                        check.fail("adjustment_audio", "audio", str(exc))
            if alignment.get("provenance", {}).get("terminalBeyondAudio") in {"silent_notation_only", "recorded_sustain_adjustments"} or adjustments:
                anchors = alignment.get("anchors", [])
                if (alignment.get("mapping") != "piecewise-linear"
                        or alignment.get("provenance", {}).get("terminalBoundary") not in {"explicit", "songsterr-last-interval"}
                        or len(anchors) < 2 or anchors[0]["audio"] >= duration
                        or anchors[-1]["audio"] <= duration):
                    check.fail("terminal_boundary", "alignment", "Invalid silent terminal extension.")
                # One or several trailing written bars may be silent. Their
                # boundaries may be supplied or extended by the source player.
                # The independent event comparison and audio bounds below,
                # not the penultimate grid boundary, prove that no playable
                # event was lost or extends beyond the recording.
            if alignment.get('endingPadding') or alignment.get("provenance", {}).get("terminalBeyondAudio") in {"silent_notation_only", "recorded_sustain_adjustments"} or adjustments:
                for key in ("beats", "sections", "time_signatures", "tempos"):
                    wanted[key] = [item for item in wanted[key] if item["time"] <= duration]
            refs = [manifest.get("preview"), manifest.get("song_timeline")]
            if manifest.get("cover"):
                refs.append(manifest["cover"])
            refs.extend(s.get("file") for s in manifest.get("stems", []))
            arrangements = manifest.get("arrangements", [])
            refs.extend(a.get("file") for a in arrangements)
            refs.extend(a.get("notation") for a in arrangements if a.get("notation"))
            for reference in refs:
                if not isinstance(reference, str) or reference not in names:
                    check.fail("missing_asset", "manifest", "A required package asset reference is missing.", actual=reference)
            from .verify_hybrid import partition, verify as verify_hybrid
            arrangements, derived = partition(manifest, check, hybrid_options)
            timeline = _json(z, manifest["song_timeline"], check)
            for key in ("beats", "sections", "time_signatures", "tempos"):
                _timeline(wanted[key], timeline.get(key), check, "song_timeline/" + key)
            selected = [p for p in wanted["parts"] if p["notes"]]
            check.equal("arrangement_count", "manifest/arrangements", len(selected), len(arrangements))
            unmatched = list(arrangements)
            tone_selected = []
            actual_note_count, actual_chord_count, notation_beat_count = 0, 0, 0
            for part in selected:
                src = part["source"]
                candidates = [a for a in unmatched if str(a.get("id")) == src.id]
                if not candidates:
                    candidates = [a for a in unmatched if a.get("name") == src.name]
                if len(candidates) != 1:
                    check.fail("track_identity", "tracks/" + src.id, "Source track does not identify exactly one archived arrangement.")
                    continue
                arr = candidates[0]
                unmatched.remove(arr)
                chart = _json(z, arr["file"], check)
                check.equal("track_name", "tracks/" + src.id, src.name, arr.get("name"))
                check.equal("track_name", arr["file"] + "/name", src.name, chart.get("name"))
                standard = TUNINGS.get(src.instrument, {}).get(len(src.tuning))
                if standard is None:
                    raise UnverifiedFeature("tracks/" + src.id, "This physical string layout is not independently verified.")
                offsets = [pitch - base for pitch, base in zip(src.tuning, standard)]
                for container, loc in ((arr, "manifest/arrangements/" + src.id), (chart, arr["file"])):
                    check.equal("tuning", loc + "/tuning", offsets, container.get("tuning"))
                    check.equal("capo", loc + "/capo", src.capo, container.get("capo", 0))
                if src.instrument == "bass":
                    check.equal("instrument", "tracks/" + src.id, "bass", arr.get("type"))
                elif arr.get("type") not in {"guitar", "lead", "rhythm"}:
                    check.fail("instrument", "tracks/" + src.id, "Guitar source was exported as another instrument.")
                tone_selected.append((src, arr, chart))
                flattened = _flatten(chart)
                actual_note_count += len(flattened)
                actual_chord_count += len(chart.get("chords", []))
                notation_beat_count += len(part["notation_beats"])
                _notes(part["notes"], flattened, check, src, duration)
                _strum_groups(wanted['strums'], chart, check, src.id, alignment,
                              recipe.get('strumGroupingPolicy'))
                _chords(part["notes"], chart, check, src)
                for key in ("beats", "sections", "tempos"):
                    _timeline(wanted[key], chart.get(key), check, arr["file"] + "/" + key)
                _notation(z, arr, part, check)
            if (source.format == 'songsterr' and recipe.get('preservationContract', 0) >= 73
                    or recipe.get('toneTimelineFile') or recipe.get('toneTimelinePolicy')
                    or any('tones' in chart for _, _, chart in tone_selected)):
                from .verify_tones import verify as verify_tones
                verify_tones(z, recipe, source, tone_selected, alignment, duration,
                             report['sourceSha256'], manifest, check)
                report['scope'].append('authored_tone_timelines')
            report["counts"]["archivedNotes"] = actual_note_count
            if source.format == 'songsterr' and (recipe.get('preservationContract', 0) >= 79
                    or recipe.get('lyricsPolicy') or recipe.get('lyricsFile') or manifest.get('lyrics')):
                from .verify_lyrics import verify as verify_lyrics
                verify_lyrics(z, recipe, manifest, score_path, source, alignment, duration, check, _json)
                report['scope'].append('authored_lyrics_export')
            report["counts"]["archivedChords"] = actual_chord_count
            report["counts"]["notationBeats"] = notation_beat_count
            if hybrid_facts is not None:
                report["hybridLead"] = verify_hybrid(z, manifest, arrangements, derived, hybrid_facts, source,
                                                       alignment, report["sourceSha256"], check)
                report["scope"].append("hybrid_main_preservation_and_source_passages")
            from ..verify_chart_guidance import POLICY as guidance_version, validate_arrangement
            declared = recipe.get("chartGuidancePolicy")
            if guidance_policy is not None:
                check.equal("guidance_policy", "manifest/song_import/chartGuidancePolicy", guidance_policy, declared)
            guidance_count = 0
            before_guidance = check.total_errors
            for entry in manifest.get("arrangements", []):
                chart = _json(z, entry["file"], check)
                proof = chart.get("ext", {}).get("chartGuidance")
                if declared is None and proof is None:
                    continue  # Historical package; not certified as completed.
                check.equal("guidance_policy", "manifest/song_import/chartGuidancePolicy", guidance_version, declared)
                check.equal("guidance_ownership", entry["file"], ["anchors", "handshapes"], (proof or {}).get("fields"))
                for error in validate_arrangement(chart, duration=duration):
                    check.fail("chart_guidance", entry["file"], error)
                guidance_count += 1
            if declared is not None:
                report["chartGuidance"] = {"policy": guidance_version, "status": "passed" if check.total_errors == before_guidance else "failed",
                                           "arrangements": guidance_count, "sourceAuthored": False}
                report["scope"].append("generated_chart_guidance")
        report["status"] = "failed" if check.total_errors else "passed"
    except UnverifiedFeature as exc:
        report["status"] = "failed" if check.total_errors else "unsupported"
        report["unsupported"] = [{"location": exc.location, "message": str(exc)}]
    except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError, BadZipFile, ZeroDivisionError, RecursionError, yaml.YAMLError) as exc:
        check.fail("verification_input", "source_or_archive", str(exc))
        report["status"] = "failed"
    report["errorCount"] = check.total_errors
    return report

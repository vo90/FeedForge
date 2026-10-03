from fractions import Fraction
import json
from pathlib import Path
import zipfile

import numpy as np
import pytest
import soundfile as sf
import yaml

from feedback_converter.song_import.alignment import map_time
from feedback_converter.song_import.audio import ImportFailure, prepare_audio
from feedback_converter.song_import.builder import _retime_note, _timeline_items, build_feedpak
from feedback_converter.song_import.model import Measure, Note, Score, Track
from feedback_converter.song_import.synchronization import align_from_songsterr
from feedback_converter.song_import.timeline import render


METADATA = {"songId": "12", "revisionId": "34", "approval": "approved"}
VIDEO = "abcdefghijk"


@pytest.mark.parametrize('basis,mode,pending', [('unreviewed', 'no', False), ('awaiting_moderation', 'pre', True), ('awaiting_review', 'post', True)])
def test_pending_selection_uses_exact_revision_map_with_existing_timing_checks(basis, mode, pending):
    fixture = json.loads((Path(__file__).parent / 'fixtures/songsterr-revision-selection.json').read_text())
    metadata = fixture['base']
    metadata.update(songId='12', revisionId='34')
    metadata['revisionEvidence'].update(songId='12', revisionId='34', defaultRevisionId='34', basis=basis, moderationType=mode, isOnModeration=pending)
    performance = render(score_fixture())
    alignment = align_from_songsterr(performance, audio_fixture(), synchronization(), metadata)
    assert alignment['status'] == 'validated'
    assert alignment['provenance']['revisionId'] == '34'
    unavailable('revision_mismatch', lambda: align_from_songsterr(performance, audio_fixture(), synchronization(revisionId='35'), metadata))
    unavailable('invalid_points', lambda: align_from_songsterr(performance, audio_fixture(), synchronization(points=(0, None, 4)), metadata))
    metadata['approval'] = 'approved'
    unavailable('unapproved_revision', lambda: align_from_songsterr(performance, audio_fixture(), synchronization(), metadata))


def score_fixture(measures=None):
    bars = measures or [Measure(tempos=[(Fraction(0), 120)]), Measure()]
    track_bars = [[] for _ in bars]
    # One tied note spans the bar boundary. Its bend begins in the first bar
    # and ends in the second, exposing duration-only and curve-only shortcuts.
    track_bars[0] = [Note(Fraction(2), Fraction(2), 0, 3, bends=[(Fraction(0), 0)])]
    track_bars[1] = [Note(Fraction(0), Fraction(2), 0, 3, tie=True, bends=[(Fraction(1), 2)])]
    return Score("Timing fixture", "Fixture artist", bars,
                 [Track("lead", "Lead", "guitar", [40, 45, 50, 55, 59, 64], track_bars)],
                 source={"songId": "12", "revisionId": "34"})


def audio_fixture(duration=8):
    return {"duration": duration, "source": {"kind": "youtube", "videoId": VIDEO}}


def synchronization(points=(0.25, 2.25, 6.25), **changes):
    return {"version": 1, "source": "songsterr-video-points", **METADATA,
            "videoId": VIDEO, "status": "done", "feature": None, "points": list(points), **changes}


def align(performance=None, points=(0.25, 2.25, 6.25), audio=None, **changes):
    return align_from_songsterr(performance or render(score_fixture()), audio or audio_fixture(), synchronization(points, **changes), METADATA)


def unavailable(reason, callback):
    with pytest.raises(ImportFailure) as caught:
        callback()
    assert caught.value.code == "source_sync_unavailable"
    assert caught.value.diagnostics["sourceSyncReason"] == reason
    return caught.value


def test_map_matches_performed_score_seconds_and_keeps_exact_identity_provenance():
    performance = render(score_fixture())
    result = align(performance)
    assert result["method"] == "songsterr-video-points-v1"
    assert result["mapping"] == "piecewise-linear"
    assert result["status"] == "validated"
    assert [(point["score"], point["audio"]) for point in result["anchors"]] == [(0, .25), (2, 2.25), (4, 6.25)]
    assert map_time(result, 1) == pytest.approx(1.25)
    assert map_time(result, 3) == pytest.approx(4.25)
    assert result["provenance"]["songId"] == "12"
    assert result["provenance"]["revisionId"] == "34"
    assert result["provenance"]["videoId"] == VIDEO
    assert result["provenance"]["terminalBoundary"] == "explicit"
    assert len(result["provenance"]["mapHash"]) == 64
    assert align(performance)["provenance"] == result["provenance"]
    assert align(performance, points=(.5, 2.5, 6.5))["provenance"]["mapHash"] != result["provenance"]["mapHash"]


def test_public_last_interval_rule_extends_only_the_trailing_array_and_keeps_audio_bounds():
    score = score_fixture([Measure(tempos=[(Fraction(0), 120)]), Measure(), Measure(), Measure()])
    performance = render(score)
    result = align(performance, points=(.25, 2.25), audio=audio_fixture(4))
    assert [p['audio'] for p in result['anchors']] == [.25, 2.25, 4.25, 6.25, 8.25]
    assert result['diagnostics']['sourceSyncInferredBoundaryCount'] == 3
    assert result['provenance']['terminalBeyondAudio'] == 'silent_notation_only'
    # The existing sounding tie ends at 3.25 s. Silent trailing measures may
    # outlast the audio, but a real attack in one of them cannot be discarded.
    score.tracks[0].bars[2] = [Note(Fraction(0), Fraction(1), 0, 5)]
    unavailable('note_outside_recording', lambda: align(render(score), points=(.25,2.25), audio=audio_fixture(4)))
    unavailable('invalid_points', lambda: align(performance, points=(.25,None,4.25), audio=audio_fixture(4)))


def test_explicit_silent_trailing_bars_are_retained_without_clipping_playable_events():
    performance = render(score_fixture([Measure(tempos=[(Fraction(0),120)]),Measure(),Measure(),Measure()]))
    result = align(performance, points=(.25,2.25,4.25,6.25,8.25), audio=audio_fixture(4))
    assert result['diagnostics']['sourceSyncInferredBoundaryCount'] == 0
    assert result['anchors'][-1]['audio'] == 8.25
    assert _retime_note(performance['tracks'][0]['notes'][0],result,4)['sus'] == 2


@pytest.mark.parametrize('overrun', [.00001, .02, .049])
@pytest.mark.parametrize('chord', [False, True])
def test_short_final_sustain_overrun_is_recorded_but_unapproved_maps_still_fail(overrun, chord):
    performance = render(score_fixture())
    note = performance['tracks'][0]['notes'][0]
    note.pop('bnv', None)
    note.pop('bn', None)
    # The fixture's mapped tie ends at 4.25 seconds; the remaining bars are
    # silence. The approved source map can shorten its constant held tail;
    # an old/unmarked alignment cannot silently do the same.
    alignment = align(performance)
    if chord:
        start = note['t']
        note = {k: v for k, v in note.items() if k != 't'}
        performance['tracks'][0]['notes'] = []
        performance['tracks'][0]['chords'] = [{'t': start, 'notes': [note]}]
    adjusted = align(performance, audio=audio_fixture(4.25 - overrun))
    assert adjusted['terminalSustains']['policy'] == 'trim-final-sustain-v1'
    assert _retime_note(note, adjusted, 4.25 - overrun, chord_time=1 if chord else None)['sus'] == pytest.approx(3 - overrun)
    with pytest.raises(ImportFailure) as caught:
        _retime_note(note, alignment, 4.25 - overrun, chord_time=1 if chord else None)
    assert caught.value.code == 'alignment_failed'
    assert caught.value.diagnostics['audioDuration'] == pytest.approx(4.25 - overrun)
    # The affine fallback must enforce the same output boundary.
    with pytest.raises(ImportFailure) as caught:
        _retime_note({'t': 1.25, 'sus': 3, 's': 0, 'f': 3},
                     {'offset': 0, 'scale': 1}, 4.25 - overrun)
    assert caught.value.code == 'alignment_failed'


def test_note_at_exact_audio_end_retains_onset_sustain_and_bend_values():
    performance = render(score_fixture())
    alignment = align(performance, audio=audio_fixture(4.25))
    mapped = _retime_note(performance['tracks'][0]['notes'][0], alignment, 4.25)
    assert (mapped['t'], mapped['sus']) == (1.25, 3)
    assert mapped['bnv'] == [{'t': 0, 'v': 0}, {'t': 1, 'v': 1}, {'t': 3, 'v': 2}]


def test_sustain_bend_breakpoints_chord_notes_and_all_timeline_events_share_one_map():
    performance = render(score_fixture())
    result = align(performance)
    note = performance["tracks"][0]["notes"][0]
    mapped = _retime_note(note, result, 8)
    assert mapped["t"] == pytest.approx(1.25)
    assert mapped["sus"] == pytest.approx(3)
    assert mapped["bnv"] == [{"t": 0, "v": 0}, {"t": 1, "v": 1}, {"t": 3, "v": 2}]
    chord_note = {key: value for key, value in note.items() if key != "t"}
    mapped_chord_note = _retime_note(chord_note, result, 8, chord_time=note["t"])
    assert "t" not in mapped_chord_note
    assert mapped_chord_note["sus"] == mapped["sus"]
    assert mapped_chord_note["bnv"] == mapped["bnv"]
    for key in ("beats", "sections", "time_signatures"):
        assert _timeline_items([{"time": 3, "value": key}], result, 8) == [{"time": 4.25, "value": key}]
    assert result["tempos"] == [{"time": .25, "bpm": 120}, {"time": 2.25, "bpm": 60}]


def test_within_bar_tempo_uses_score_seconds_instead_of_a_uniform_beat_fraction():
    score = score_fixture([Measure(tempos=[(Fraction(0), 120), (Fraction(2), 60)]), Measure()])
    performance = render(score)
    assert performance["duration"] == 7
    result = align(performance, points=(.5, 6.5, 8.5), audio=audio_fixture(9))
    # At quarter 3, score time is 2s; the official interpolation maps 2/3
    # of this bar, rather than 3/4 of its written beat count.
    assert map_time(result, 2) == pytest.approx(4.5)
    assert result["tempos"] == [{"time": .5, "bpm": 60}, {"time": 2.5, "bpm": 30}, {"time": 6.5, "bpm": 120}]


def repeat_fixture(*, within_bar=False, nested=False):
    bars = [Measure(repeat_start=True, tempos=[(Fraction(0), 120)]),
            Measure(repeat_count=2, endings=frozenset({1})),
            Measure(endings=frozenset({2}))]
    if within_bar:
        bars[0].tempos.append((Fraction(2), 60))
    if nested:
        bars = [Measure(repeat_start=True, tempos=[(Fraction(0), 120)]),
                Measure(repeat_start=True, repeat_count=2), Measure(repeat_count=2)]
    notes = [[Note(Fraction(0), Fraction(1), 0, i)] for i in range(len(bars))]
    return render(Score("Repeated", "Fixture", bars, [Track("lead", "Lead", "guitar", [40], notes)], source=METADATA))


def test_ordinary_repeats_and_alternate_endings_bind_each_performed_occurrence():
    performance = repeat_fixture()
    assert [bar["writtenIndex"] for bar in performance["scoreTimeline"]["measures"]] == [0, 1, 0, 2]
    result = align(performance, points=(.1, 2.1, 4.1, 7.1, 9.1), audio=audio_fixture(10))
    assert [map_time(result, note["t"]) for note in performance["tracks"][0]["notes"]] == pytest.approx([.1, 2.1, 4.1, 7.1])


@pytest.mark.parametrize("options,reason", [({"nested": True}, "unverified_nested_repeats"),
                                          ({"within_bar": True}, "unverified_repeat_tempo_inheritance")])
def test_unverified_repeat_structures_use_explicit_fallback(options, reason):
    unavailable(reason, lambda: align(repeat_fixture(**options)))


def test_multibar_first_ending_regions_are_not_mistaken_for_individually_skipped_bars():
    bars = [Measure(repeat_start=True, tempos=[(Fraction(0), 120)]), Measure(),
            Measure(endings=frozenset({1})), Measure(), Measure(repeat_count=2), Measure(endings=frozenset({2}))]
    notes = [[Note(Fraction(0), Fraction(1), 0, index)] for index in range(len(bars))]
    performance = render(Score("Ending regions", "Fixture", bars, [Track("lead", "Lead", "guitar", [40], notes)], source=METADATA))
    assert performance["scoreTimeline"]["hasMultiBarAlternateEndings"] is True
    unavailable("unverified_multibar_endings", lambda: align(performance))


def test_implicit_outer_repeat_start_is_still_classified_as_nested():
    bars = [Measure(tempos=[(Fraction(0), 120)]), Measure(repeat_start=True),
            Measure(repeat_count=2), Measure(repeat_count=2)]
    notes = [[Note(Fraction(0), Fraction(1), 0, index)] for index in range(len(bars))]
    performance = render(Score("Implicit nested", "Fixture", bars, [Track("lead", "Lead", "guitar", [40], notes)], source=METADATA))
    assert performance["scoreTimeline"]["maxRepeatDepth"] == 2
    unavailable("unverified_nested_repeats", lambda: align(performance))


def test_official_single_terminal_extension_is_marked_and_audio_bounded():
    result = align(points=(.25, 2.25), audio=audio_fixture(5))
    assert result["anchors"][-1]["audio"] == 4.25
    assert result["provenance"]["terminalBoundary"] == "songsterr-last-interval"
    assert result["diagnostics"]["sourceSyncInferredTerminalBoundary"] is True
    silent = align(points=(.25, 3.25), audio=audio_fixture(5))
    assert silent["anchors"][-1]["audio"] == 6.25
    assert silent["provenance"]["terminalBeyondAudio"] == "silent_notation_only"
    assert _retime_note(render(score_fixture())["tracks"][0]["notes"][0], silent, 5)["sus"] == 3
    unavailable('terminal_technique_outside_recording', lambda: align(points=(.25,3.25), audio=audio_fixture(4.6)))
    explicit = align(points=(.25, 3.25, 6.25), audio=audio_fixture(5))
    assert explicit['anchors'] == silent['anchors']
    assert explicit['provenance']['terminalBoundary'] == 'explicit'
    assert explicit['provenance']['terminalBeyondAudio'] == 'silent_notation_only'
    unavailable('terminal_technique_outside_recording', lambda: align(points=(.25,3.25,6.25), audio=audio_fixture(4.6)))
    unavailable('terminal_technique_outside_recording', lambda: align(points=(.25, 5.25, 6.25), audio=audio_fixture(5)))
    unavailable("point_count_mismatch", lambda: align(points=(.25,)))
    with pytest.raises(ImportFailure, match="cover"):
        map_time(result, 4.1)


@pytest.mark.parametrize("points,reason", [((0, None, 4), "invalid_points"), ((0, True, 4), "invalid_points"),
                                         ((0, float("nan"), 4), "invalid_points"), ((0, float("inf"), 4), "invalid_points"),
                                         ((0, 2, 2), "non_increasing_points"), ((0, 3, 2), "non_increasing_points"),
                                         ((0, 1, 2, 1), "non_increasing_points"),
                                         ((0, 1, 2, None), "invalid_points")])
def test_invalid_and_mismatched_anchor_arrays_do_not_get_patched(points, reason):
    unavailable(reason, lambda: align(points=points))


@pytest.mark.parametrize('extra', [(8,), (8, 1000)])
def test_surplus_points_are_an_unused_suffix_not_a_longer_final_bar(extra):
    points = (.25, 2.25, 6.25, *extra)
    result = align(points=points)
    baseline = align()
    assert result['anchors'] == baseline['anchors']
    assert result['tempos'] == baseline['tempos']
    assert result['sourceTiming']['points'] == list(points)
    assert result['provenance']['mapHash'] != baseline['provenance']['mapHash']
    assert result['provenance']['boundaryPolicy'] == {
        'version': 1, 'rule': 'songsterr-shared-boundary-prefix', 'supplied': len(points),
        'used': 3, 'unusedTrailing': len(extra), 'inferredTrailing': 0}
    for t in (0, .1, 1, 2, 2.5, 3, 4):
        assert map_time(result, t) == map_time(baseline, t)


def test_unused_suffix_does_not_rescue_attacks_outside_audio():
    unavailable('note_outside_recording', lambda: align(points=(1, 3, 5, 7), audio=audio_fixture(1)))


def test_exact_revision_recording_and_full_mix_binding_are_mandatory():
    unavailable("revision_mismatch", lambda: align(revisionId="35"))
    unavailable("revision_mismatch", lambda: align(songId="13"))
    unavailable("recording_mismatch", lambda: align(videoId="zzzzzzzzzzz"))
    unavailable("recording_mismatch", lambda: align(audio={"duration": 8, "source": {"kind": "file", "videoId": VIDEO}}))
    unavailable("not_full_mix", lambda: align(feature="backing"))
    performance = render(score_fixture())
    performance["source"]["revisionId"] = "35"
    unavailable("revision_mismatch", lambda: align(performance))


def test_negative_preroll_keeps_playable_notes_and_omits_only_negative_timeline_metadata():
    performance = render(score_fixture())
    result = align(performance, points=(-.5, 1.5, 3.5))
    assert result["diagnostics"]["sourceSyncNegativePreroll"] is True
    assert map_time(result, performance["tracks"][0]["notes"][0]["t"]) == .5
    beats = _timeline_items(performance["beats"], result, 8)
    assert min(beat["time"] for beat in beats) == 0
    assert len(beats) == len(performance["beats"]) - 1
    performance["tracks"][0]["notes"][0]["t"] = 0
    unavailable("negative_note_time", lambda: align(performance, points=(-.5, 1.5, 3.5)))
    with pytest.raises(ImportFailure, match="beginning"):
        _retime_note({"t": 0, "sus": 1, "s": 0, "f": 0}, result, 8)


def test_notes_and_sustains_outside_complete_map_are_not_clipped_or_extrapolated():
    performance = render(score_fixture())
    performance["tracks"][0]["notes"][0]["sus"] = 4
    unavailable("note_outside_map", lambda: align(performance))
    result = align()
    with pytest.raises(ImportFailure, match="cover"):
        _retime_note({"t": 3.5, "sus": 1, "s": 0, "f": 0}, result, 8)
    with pytest.raises(ImportFailure, match="sustain"):
        _retime_note({"t": 1, "sus": -1, "s": 0, "f": 0}, result, 8)
    performance = render(score_fixture())
    performance["tracks"][0]["notes"][0]["bnv"][-1]["t"] = 3
    unavailable("invalid_bend_timing", lambda: align(performance))


def test_unavailable_provider_reason_is_preserved_without_arbitrary_text():
    performance = render(score_fixture())
    caught = unavailable("missing", lambda: align_from_songsterr(performance, audio_fixture(),
                        {"status": "unavailable", "reasonCode": "rate_limited"}, METADATA))
    assert caught.diagnostics["sourceSyncProviderReason"] == "rate_limited"
    caught = unavailable("missing", lambda: align_from_songsterr(performance, audio_fixture(),
                        {"status": "unavailable", "reasonCode": "https://private.example/raw"}, METADATA))
    assert "sourceSyncProviderReason" not in caught.diagnostics


def test_affine_note_sustain_and_bend_behavior_is_unchanged():
    old = {"status": "validated", "offset": .75, "scale": 1.1}
    note = {"t": 1, "sus": 2, "s": 0, "f": 3, "bnv": [{"t": .5, "v": 1}, {"t": 2, "v": 2}]}
    assert _retime_note(note, old, 8) == {**note, "t": 1.85, "sus": 2.2, "bnv": [{"t": .55, "v": 1}, {"t": 2.2, "v": 2}]}
    assert map_time(old, 3) == 4.05


def test_piecewise_feedpak_has_real_audio_preview_and_retimed_arrangement(tmp_path):
    source = tmp_path / "recording.wav"
    sf.write(source, .1 * np.sin(np.arange(22050 * 8) * 2 * np.pi * 440 / 22050), 22050)
    job = tmp_path / "job"
    job.mkdir()
    audio = prepare_audio({"kind": "file", "path": str(source)}, job)
    # This local generated test recording models a downloader-identified video;
    # source identity is never inferred for arbitrary files in production.
    audio["source"].update(kind="youtube", videoId=VIDEO)
    performance = render(score_fixture())
    result = align(performance, audio=audio)
    built = build_feedpak(performance, audio, result, job, output_dir=tmp_path / "library")
    with zipfile.ZipFile(Path(built["stagingPath"])) as archive:
        manifest = yaml.safe_load(archive.read("manifest.yaml"))
        chart = json.loads(archive.read(manifest["arrangements"][0]["file"]))
        assert chart["notes"][0]["sus"] == 3
        assert chart["notes"][0]["bnv"][1] == {"t": 1, "v": 1}
        assert chart["tempos"] == result["tempos"]
        assert manifest["preview"] in archive.namelist()
        assert manifest["stems"][0]["file"] in archive.namelist()
    assert not (tmp_path / "library").exists()

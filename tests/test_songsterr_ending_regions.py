"""Hand-calculated performed orders and independent archive rejection checks."""
from copy import deepcopy
import json
from zipfile import ZipFile

import pytest
from test_song_import_score import raw_score, measure, beat
from feedback_converter.song_import.songsterr import parse
from feedback_converter.song_import.timeline import playback_order, render
from feedback_converter.song_import.verify_source import songsterr
from feedback_converter.song_import.verify_timeline import visits, expected
from feedback_converter.song_import.builder import _timeline_items


CASES = [
    # Unholy Confessions' actual opening: the unmarked closing bar belongs to ending 1.
    ([{'repeatStart': True}, {}, {'alternateEnding': [1]}, {'repeat': 2}, {'alternateEnding': [2]}, {}],
     [0, 1, 2, 3, 0, 1, 4, 5]),
    # Multiple bars in both endings, including two unmarked first-ending bars.
    ([{'repeatStart': True}, {'alternateEnding': [1]}, {}, {'repeat': 2}, {'alternateEnding': [2]}, {}, {}],
     [0, 1, 2, 3, 0, 4, 5, 6]),
    # Three passes with separate first and second regions inside the repeat.
    ([{'repeatStart': True}, {'alternateEnding': [1]}, {}, {'alternateEnding': [2]}, {'repeat': 3}, {'alternateEnding': [3]}],
     [0, 1, 2, 0, 3, 4, 0, 5]),
    # A shared ending for passes 1 and 2.
    ([{'repeatStart': True}, {'alternateEnding': [1, 2]}, {'repeat': 3}, {'alternateEnding': [3]}, {}],
     [0, 1, 2, 0, 1, 2, 0, 3, 4]),
    # An implicit start is established by its marked close.
    ([{}, {'alternateEnding': [1], 'repeat': 2}, {'alternateEnding': [2]}], [0, 1, 0, 2]),
    # All endings inside the repeat; each pass skips other ending regions.
    ([{'repeatStart': True}, {'alternateEnding': [1]}, {}, {'alternateEnding': [2]}, {'repeat': 2}, {}],
     [0, 1, 2, 0, 3, 4, 5]),
    # The final ending can be immediately followed by a separate repeated section.
    ([{'repeatStart': True}, {'alternateEnding': [1]}, {'repeat': 2}, {'alternateEnding': [2]},
      {'repeatStart': True}, {'repeat': 3}], [0, 1, 2, 0, 3, 4, 5, 4, 5, 4, 5]),
]


def document(specs):
    return raw_score([measure(beat(i + 1), **spec) for i, spec in enumerate(specs)])


@pytest.mark.parametrize('specs,order', CASES)
def test_known_orders_notes_notation_and_measure_occurrences(specs, order):
    doc = document(specs)
    score = parse(doc)
    reference = songsterr(doc)
    assert playback_order(score.measures) == visits(reference) == order
    result = render(score)
    assert [n['f'] for n in result['tracks'][0]['notes']] == [i + 1 for i in order]
    assert [n['t'] for n in result['tracks'][0]['notes']] == [i * 2 for i in range(len(order))]
    assert result['duration'] == 2 * len(order)
    assert result['sourceScore']['document'] == doc
    counts = {}
    for row, i in zip(result['scoreTimeline']['measures'], order):
        counts[i] = counts.get(i, 0) + 1
        assert (row['writtenIndex'], row['visit']) == (i, counts[i])
    checked = expected(reference, {'offset': 0, 'scale': 1})
    assert result['beats'] == checked['beats']


@pytest.mark.parametrize('specs', [
    [{}, {'alternateEnding': [1]}],
    [{'repeatStart': True}, {'alternateEnding': [1]}, {'repeat': 3}, {'alternateEnding': [3]}],
    [{'repeatStart': True}, {'alternateEnding': [1, 3]}, {'repeat': 2}, {'alternateEnding': [2]}],
    [{'repeatStart': True}, {'alternateEnding': [1]}, {'repeat': 2}, {}, {'alternateEnding': [2]}],
    [{'repeatStart': True}, {'alternateEnding': [1]}, {'alternateEnding': [2]}, {'alternateEnding': [1], 'repeat': 2}],
    [{'repeatStart': True}, {'alternateEnding': [1]}, {'repeat': 2}, {'alternateEnding': [1, 2]}],
    [{'repeatStart': True}, {'repeatStart': True}, {'alternateEnding': [1], 'repeat': 2}, {'alternateEnding': [2]}, {'repeat': 2}],
    [{'repeatStart': True}, {}],
    [{}, {'alternateEnding': [1]}, {'repeat': 2}, {'alternateEnding': [2]}],
    [{'repeatStart': True, 'alternateEnding': [1]}, {'repeat': 2}, {'alternateEnding': [2]}],
    [{'repeatStart': True}, {'alternateEnding': [1]}, {'alternateEnding': [1], 'repeat': 2}, {'alternateEnding': [2]}],
])
def test_unresolved_ending_ownership_remains_rejected(specs):
    doc = document(specs)
    with pytest.raises(ValueError): playback_order(parse(doc).measures)
    with pytest.raises(ValueError): visits(songsterr(doc))


def test_nested_repeats_without_endings_keep_their_previous_order():
    doc = document([{'repeatStart': True}, {'repeatStart': True}, {'repeat': 2}, {'repeat': 3}])
    assert playback_order(parse(doc).measures) == visits(songsterr(doc)) == [0, 1, 2, 1, 2, 3] * 3


def test_tempo_compaction_keeps_restorations_and_within_bar_changes():
    doc = document(CASES[0][0])
    doc['parts'][0]['automations']['tempo'] += [
        {'measure': 1, 'position': [0, 1], 'bpm': 90, 'type': 4},
        {'measure': 2, 'position': [1, 2], 'bpm': 60, 'type': 4},
        {'measure': 4, 'position': [0, 1], 'bpm': 120, 'type': 4}]
    result = render(parse(doc)); alignment = {'offset': .5, 'scale': 1.1}
    wanted = expected(songsterr(doc), alignment)['tempos']
    actual = _timeline_items(result['tempos'], alignment, 100, kind='tempos')
    assert [t['bpm'] for t in actual] == [120/1.1, 90/1.1, 60/1.1, 120/1.1, 90/1.1, 120/1.1]
    for left, right in zip(actual, wanted):
        assert left == pytest.approx(right, abs=1e-6)


@pytest.mark.parametrize('fault', ['order', 'visit', 'time', 'quarters', 'source', 'identity'])
def test_source_sync_requires_independent_occurrence_proof(fault):
    from test_song_import_synchronization import align, audio_fixture, unavailable
    doc = document(CASES[0][0]); doc.update(songId=12, revisionId=34)
    performance = render(parse(doc))
    points = list(range(0, 18, 2))
    assert align(performance, points=points, audio=audio_fixture(20))['status'] == 'validated'
    broken = deepcopy(performance)
    row = broken['scoreTimeline']['measures'][5]
    if fault == 'order': row['writtenIndex'] = 3
    elif fault == 'visit': row['visit'] += 1
    elif fault == 'time': row['start'] += .25
    elif fault == 'quarters': row['quarters'] += 1
    elif fault == 'source': broken.pop('sourceScore')
    elif fault == 'identity': broken['sourceScore']['document']['revisionId'] += 1
    unavailable('unverified_multibar_endings', lambda: align(broken, points=points, audio=audio_fixture(20)))


def test_complete_archive_and_deliberately_mistimed_note(tmp_path):
    from test_song_import_builder import inputs
    from feedback_converter.song_import.builder import build_feedpak
    from feedback_converter.song_import.compatibility import inspect_songsterr
    from feedback_converter.song_import.verification import verify_import
    import yaml
    # Short measures fit the generated audio fixture; navigation is identical.
    doc = document(CASES[0][0])
    for bar in doc['parts'][0]['measures']:
        bar['signature'] = [1, 4]
        bar['voices'][0]['beats'][0]['duration'] = [1, 4]
    performance = render(parse(doc)); _, audio, _, job = inputs(tmp_path)
    source = tmp_path / 'source.json'; source.write_text(json.dumps(doc), encoding='utf-8')
    alignment = {'status': 'validated', 'offset': 0, 'scale': 1}
    built = build_feedpak(performance, audio, alignment, job, output_dir=tmp_path/'out', source_path=source,
                         compatibility=inspect_songsterr(doc), recipe={'preservationContract': 19})
    archive = built['stagingPath']
    report = verify_import(source, archive, alignment)
    assert report['status'] == 'passed', report
    with ZipFile(archive) as z: entries = {name: z.read(name) for name in z.namelist()}
    manifest = yaml.safe_load(entries['manifest.yaml']); chart_path = manifest['arrangements'][0]['file']
    chart = json.loads(entries[chart_path]); chart['notes'][5]['t'] += .5
    entries[chart_path] = json.dumps(chart).encode()
    corrupt = tmp_path/'corrupt.feedpak'
    with ZipFile(corrupt, 'w') as z:
        for name, data in entries.items(): z.writestr(name, data)
    assert verify_import(source, corrupt, alignment)['status'] == 'failed'

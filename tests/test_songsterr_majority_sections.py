"""Section names are annotations; voting cannot edit the musical score."""
from copy import deepcopy
from zipfile import ZipFile
import pytest
import yaml

from test_songsterr_section_labels import source
from feedback_converter.song_import.songsterr import parse
from feedback_converter.song_import.timeline import render
from feedback_converter.song_import.verify_source import songsterr


@pytest.mark.parametrize('labels,chosen,basis', [
    (['Interlude', 'Evil hillbilly lick', 'Evil hillbilly lick'], 'Evil hillbilly lick', 'majority_label'),
    (['First', 'Second', 'Second', 'Third', 'Third'], 'Second', 'track_order_tiebreak'),
    (['First', 'Second', 'First', 'Second'], 'First', 'track_order_tiebreak'),
    (['First', 'Second', 'Second', 'Second', 'First'], 'Second', 'majority_label'),
    (['Solo (Kurt)', 'Solo (Krist)', 'Solo (Krist)'], 'Solo (Krist)', 'majority_label'),
    (['Verse 1', 'Verse 2', 'Verse 2'], 'Verse 2', 'majority_label'),
    (['Other', ' Interlude ', 'interlude', 'Different'], 'interlude', 'majority_label'),
    (['', 'Other', 'Interlude', 'Interlude'], 'Interlude', 'majority_label'),
])
def test_votes_and_ties_preserve_authored_names_and_every_note(labels, chosen, basis):
    doc = source(labels, (30,) * len(labels)); original = deepcopy(doc)
    score, reference = parse(doc), songsterr(doc)
    assert score.measures[0].section == reference.bars[0].section == chosen
    assert score.source['sectionLabels'] == reference.section_labels
    decision, = reference.section_labels
    assert decision['basis'] == 'guitar_bass_' + basis
    assert [r['text'] for r in decision['labels']] == [label for label in labels if label]
    assert doc == original == score.source_document['document']
    uniform = deepcopy(doc)
    for part in uniform['parts']: part['measures'][0]['marker'] = chosen
    assert render(score)['tracks'] == render(parse(uniform))['tracks']
    assert reference.parts == songsterr(uniform).parts
    # Even requesting only the losing track cannot alter the shared decision.
    assert parse(doc, track_indices={0}).source['sectionLabels'] == reference.section_labels
    assert songsterr(doc, track_indices={0}).section_labels == reference.section_labels


def test_excluded_instruments_cannot_outvote_guitar_bass():
    doc = source(['Vocal'] * 4 + ['Interlude', 'Riff', 'Riff'], (68,) * 4 + (30, 30, 34))
    score, reference = parse(doc), songsterr(doc)
    assert score.measures[0].section == reference.bars[0].section == 'Riff'
    assert len(reference.section_labels[0]['labels']) == 7
    assert score.source['sectionLabels'] == reference.section_labels


def test_other_track_majority_is_only_a_fallback():
    doc = source(['Verse', 'Chorus', 'Chorus', ''], (68, 70, 68, 30))
    assert parse(doc).measures[0].section == songsterr(doc).bars[0].section == 'Chorus'
    assert songsterr(doc).section_labels[0]['basis'] == 'other_tracks_majority_label'
    doc['parts'][3]['measures'][0]['marker'] = 'Guitar entrance'
    assert parse(doc).measures[0].section == songsterr(doc).bars[0].section == 'Guitar entrance'


def test_tie_break_follows_source_order_but_majority_is_order_independent():
    doc = source(['First', 'Second', 'First', 'Second'], (30,) * 4)
    swapped = {**doc, 'parts': doc['parts'][1:] + doc['parts'][:1],
               'tracks': doc['tracks'][1:] + doc['tracks'][:1]}
    assert parse(doc).measures[0].section == songsterr(doc).bars[0].section == 'First'
    assert parse(swapped).measures[0].section == songsterr(swapped).bars[0].section == 'Second'
    for case in (doc, swapped):
        case = deepcopy(case)
        for part in case['parts']:
            if part['measures'][0]['marker'] == 'First':
                part['measures'][0]['marker'] = 'Second'; break
        assert parse(case).measures[0].section == songsterr(case).bars[0].section == 'Second'


def test_split_voices_do_not_gain_votes():
    from feedback_converter.song_import.verify_timeline import expected
    doc = source(['Minority', 'Majority', 'Majority'], (30,) * 3)
    voices = doc['parts'][0]['measures'][0]['voices']
    for fret in (5, 7, 9):
        voice = deepcopy(voices[0]); voice['beats'][0]['notes'][0]['fret'] = fret
        voices.append(voice)
    p, reference = render(parse(doc)), expected(songsterr(doc), {'offset': 0, 'scale': 1})
    assert len(p['tracks']) > len(doc['tracks'])
    assert len(reference['parts']) == len(p['tracks'])
    assert p['sections'][0]['name'] == 'Majority'
    assert len(p['source']['sectionLabels'][0]['labels']) == 3
    assert p['source']['sectionLabels'] == songsterr(doc).section_labels


def test_hybrid_generation_retains_three_source_votes_in_verified_package(tmp_path):
    from test_songsterr_hybrid_lead import song, build
    doc = song()
    for part, label in zip(doc['parts'], ('Minority', 'Majority', 'Majority')):
        part['measures'][0]['marker'] = label
    *_, archive, report = build(tmp_path, doc)
    assert report['status'] == 'passed', report
    with ZipFile(archive) as z:
        manifest = yaml.safe_load(z.read('manifest.yaml'))
    assert len(manifest['arrangements']) == 4
    decision, = manifest['song_import']['sourceMetadata']['sectionLabels']
    assert decision['label'] == 'Majority'
    assert decision['basis'] == 'guitar_bass_majority_label'
    assert len(decision['labels']) == 3

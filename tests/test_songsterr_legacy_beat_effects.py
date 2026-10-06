"""Scoped legacy beat flags retain source values without musical fan-out."""
from copy import deepcopy
import pytest

from feedback_converter.song_import.compatibility import inspect_songsterr
from feedback_converter.song_import.inventory import FeatureInventory
from feedback_converter.song_import.model import ScoreImportError
from feedback_converter.song_import.songsterr import parse
from feedback_converter.song_import.songsterr_legacy_effects import validate_legacy_beat_effect
from feedback_converter.song_import.timeline import render
from test_song_import_score import beat, measure, raw_score


def document(*, harmonic=None, fade=None, fret=12, node=None, program=29):
    first = beat(fret, harmonic='natural', **({'harmonicFret': node} if node is not None else {}))
    first.update(harmonic=harmonic, fadeIn=fade)
    doc = raw_score([measure(first)])
    if program == 33:
        doc['tracks'][0].update(instrumentId=33, tuning=[43, 38, 33, 28])
    return doc


def without_flags(doc):
    control = deepcopy(doc)
    for part in control['parts']:
        for bar in part['measures']:
            for voice in bar['voices']:
                for item in voice['beats']:
                    item.pop('harmonic', None)
                    item.pop('fadeIn', None)
    return control


def checked(doc, *, selected=None):
    original = deepcopy(doc)
    score = parse(doc, track_indices=selected)
    parsed = deepcopy(score)
    actual = render(score)
    control = render(parse(without_flags(doc), track_indices=selected))
    assert actual['tracks'] == control['tracks']
    assert actual['scoreTimeline'] == control['scoreTimeline']
    assert actual['duration'] == control['duration']
    assert score == parsed and doc == original
    return actual, score.feature_inventory, inspect_songsterr(doc, track_indices=selected)


def rows(report, field):
    return [r for r in report['findings'] if r['feature'] == 'beat.' + field]


@pytest.mark.parametrize('harmonic', [None, False, True])
@pytest.mark.parametrize('fade', [None, False, True])
@pytest.mark.parametrize('fret,node', [(12, None), (7, 7), (3, 3.2), (15, 15)])
@pytest.mark.parametrize('program', [29, 33])
def test_qualified_flags_keep_existing_natural_targets_timing_and_notation(harmonic, fade, fret, node, program):
    doc = document(harmonic=harmonic, fade=fade, fret=fret, node=node, program=program)
    p, inventory, report = checked(doc)
    note, = p['tracks'][0]['notes']
    assert note['f'] == fret and note['hm'] is True
    for field, value in [('harmonic', harmonic), ('fadeIn', fade)]:
        entry, = [r for r in inventory if r['scope'] == 'Songsterr beat' and r['field'] == field]
        assert entry['count'] == 1 and entry['handling'] == 'source' and entry['representations'] == ['source']
        row, = rows(report, field)
        assert row['value'] is value and row['retained'] == 'original_source'
        assert row['location'] == 'parts/0/measures/0/voices/0/beats/0/' + field
        assert (row['category'], row['impact']) == (('game_limitation', 'display_or_expression') if field == 'fadeIn' and value is True else ('source_metadata', 'source_retained'))
    assert report['status'] == ('limitations' if fade or node == 15 else 'compatible')


@pytest.mark.parametrize('field', ['harmonic', 'fadeIn'])
@pytest.mark.parametrize('value', [None, False])
@pytest.mark.parametrize('rest', ['ordinary', 'note', 'beat'])
def test_inactive_flags_are_typed_retained_even_on_rests(field, value, rest):
    doc = raw_score([measure(beat(4)), measure(beat(7))])
    first = doc['parts'][0]['measures'][0]['voices'][0]['beats'][0]
    first[field] = value
    if rest != 'ordinary':
        first['notes'] = [{'rest': True}]
    if rest == 'beat':
        first['rest'] = True
    p, inventory, report = checked(doc)
    row, = rows(report, field)
    assert row['value'] is value and row['category'] == 'source_metadata' and row['impact'] == 'source_retained'
    entry, = [r for r in inventory if r['scope'] == 'Songsterr beat' and r['field'] == field]
    assert entry['count'] == 1 and entry['handling'] == 'source'
    if rest == 'ordinary':
        assert not any(n.get('hm') or n.get('hp') for n in p['tracks'][0]['notes'])


@pytest.mark.parametrize('rest', ['ordinary', 'note', 'beat'])
def test_active_fade_in_is_retained_without_a_volume_or_pitch_envelope(rest):
    doc = raw_score([measure(beat(5)), measure(beat(7))])
    first = doc['parts'][0]['measures'][0]['voices'][0]['beats'][0]
    first['fadeIn'] = True
    if rest != 'ordinary':
        first['notes'] = [{'rest': True}]
    if rest == 'beat':
        first['rest'] = True
    _, _, report = checked(doc)
    row, = rows(report, 'fadeIn')
    assert (row['category'], row['impact'], row['value']) == ('game_limitation', 'display_or_expression', True)
    assert 'volume-swell' in row['message'] and 'scoring are unchanged' in row['message']


MALFORMED = [0, 1, -1, 0., 1., '', 'true', 'false', [], {}, [False], {'enabled': True},
             float('nan'), float('inf'), [float('nan')], {'nested': float('nan')}]


@pytest.mark.parametrize('field', ['harmonic', 'fadeIn'])
@pytest.mark.parametrize('value', MALFORMED)
@pytest.mark.parametrize('rest', ['ordinary', 'note', 'beat'])
def test_malformed_flags_fail_before_inactive_or_rest_handling(field, value, rest):
    doc = raw_score([measure(beat(12, harmonic='natural')), measure(beat(7))])
    first = doc['parts'][0]['measures'][0]['voices'][0]['beats'][0]
    first[field] = deepcopy(value)
    if rest != 'ordinary':
        first['notes'] = [{'rest': True}]
    if rest == 'beat':
        first['rest'] = True
    with pytest.raises(ScoreImportError, match='Invalid legacy beat ' + field):
        parse(doc)
    report = inspect_songsterr(doc)
    row, = rows(report, field)
    assert report['status'] == 'blocked' and row['category'] == 'source_structure' and row['impact'] == 'blocking'


@pytest.mark.parametrize('fault', ['missing', 'mixed', 'pinch', 'opaque', 'unknown_node', 'conflicting_node',
                                  'dead', 'scrape', 'empty', 'note_rest', 'beat_rest', 'malformed_notes'])
def test_active_harmonic_never_infers_missing_or_conflicting_note_targets(fault):
    doc = document(harmonic=True)
    first = doc['parts'][0]['measures'][0]['voices'][0]['beats'][0]
    note = first['notes'][0]
    if fault == 'missing': note.pop('harmonic')
    elif fault == 'mixed': first['notes'].append({'string': 1, 'fret': 12})
    elif fault == 'pinch': note['harmonic'] = 'pinch'
    elif fault == 'opaque': note['harmonicData'] = {'type': 'natural'}
    elif fault == 'unknown_node': note.update(fret=6, harmonicFret=6)
    elif fault == 'conflicting_node': note['harmonicFret'] = 7
    elif fault == 'dead': note['dead'] = True
    elif fault == 'scrape': note['pickScrape'] = 'up'
    elif fault == 'empty': first['notes'] = []
    elif fault == 'note_rest': first['notes'] = [{'rest': True}]
    elif fault == 'beat_rest': first['rest'] = True
    elif fault == 'malformed_notes': first['notes'] = [None]
    with pytest.raises(ScoreImportError, match='Legacy beat harmonic'):
        parse(doc)
    row, = rows(inspect_songsterr(doc), 'harmonic')
    assert row['impact'] == 'blocking' and row['category'] == 'unknown_semantics'


def test_mixed_rest_slots_do_not_create_harmonic_notes():
    doc = document(harmonic=True)
    doc['parts'][0]['measures'][0]['voices'][0]['beats'][0]['notes'].append({'rest': True})
    p, _, report = checked(doc)
    assert len(p['tracks'][0]['notes']) == 1
    assert rows(report, 'harmonic')[0]['impact'] == 'source_retained'


def test_chord_voices_repeats_and_ties_preserve_every_authored_flag():
    first = {'duration': [1, 1], 'harmonic': True, 'fadeIn': True,
             'brushStroke': {'direction': 'down', 'duration': 30, 'shift': 100},
             'notes': [{'string': s, 'fret': 12, 'harmonic': 'natural'} for s in (0, 1, 2)]}
    last = deepcopy(first)
    last.update(harmonic=False, fadeIn=None)
    for n in last['notes']:
        n['tie'] = True
    doc = raw_score([measure(first, repeatStart=True), measure(last, repeat=2)])
    for bar in doc['parts'][0]['measures']:
        bar['voices'].append({'beats': [beat(12, string=3, harmonic='natural')]})
        bar['voices'][1]['beats'][0].update(harmonic=None, fadeIn=False)
    p, inventory, report = checked(doc)
    for field in ('harmonic', 'fadeIn'):
        entry, = [r for r in inventory if r['scope'] == 'Songsterr beat' and r['field'] == field]
        assert entry['count'] == 4
        assert len(rows(report, field)) == 4
    assert sum(len(c['notes']) for c in p['tracks'][0]['chords']) + len(p['tracks'][0]['notes']) == 10


def with_unselected_part(*, program=0, harmonic=True, fade=True):
    doc = raw_score([measure(beat(7))])
    doc['tracks'].append({'id': 15, 'name': 'Excluded context', 'instrumentId': program, 'tuning': [64, 59, 55, 50, 45, 40]})
    doc['parts'].append({'measures': [measure(beat(-1, string=99))]})
    first = doc['parts'][1]['measures'][0]['voices'][0]['beats'][0]
    first.update(harmonic=harmonic, fadeIn=fade)
    return doc


@pytest.mark.parametrize('program,selected', [(0, None), (120, None), (29, {0})])
@pytest.mark.parametrize('harmonic,fade', [(None, False), (False, None), (True, True)])
def test_unselected_parts_keep_scoped_flags_without_new_pitch_guards(program, selected, harmonic, fade):
    p, inventory, report = checked(with_unselected_part(program=program, harmonic=harmonic, fade=fade), selected=selected)
    assert len(p['tracks']) == 1
    for field, value in [('harmonic', harmonic), ('fadeIn', fade)]:
        entry, = [r for r in inventory if r['scope'] == 'Songsterr beat' and r['field'] == field]
        assert entry['count'] == 1 and entry['handling'] == 'source'
        row, = rows(report, field)
        assert row['value'] is value and row['trackId'] == '15'
        assert (row['category'], row['impact']) == ('source_metadata', 'source_retained')
        assert 'excluded arrangement' in row['message'] and 'no playable effect' in row['message']


@pytest.mark.parametrize('field', ['harmonic', 'fadeIn'])
@pytest.mark.parametrize('value', [0, 1, '', [], {}])
@pytest.mark.parametrize('program,selected', [(0, None), (29, {0})])
def test_excluded_and_diagnostic_unselected_parts_still_validate_flag_types(field, value, program, selected):
    doc = with_unselected_part(program=program)
    doc['parts'][1]['measures'][0]['voices'][0]['beats'][0][field] = value
    with pytest.raises(ScoreImportError, match='Invalid legacy beat ' + field):
        parse(doc, track_indices=selected)
    row, = rows(inspect_songsterr(doc, track_indices=selected), field)
    assert row['category'] == 'source_structure' and row['impact'] == 'blocking'


def test_new_flags_do_not_relax_other_negative_dead_frets_or_bend_compound_plain_tie_guards():
    doc = raw_score([measure(beat(-2, dead=True))])
    doc['parts'][0]['measures'][0]['voices'][0]['beats'][0]['fadeIn'] = True
    with pytest.raises(ScoreImportError, match='Invalid authored fret'):
        parse(doc)
    doc = raw_score([measure(beat(5, duration=(1, 2), vibrato=True, bend={'points': [{'position': 0, 'tone': 0}, {'position': 60, 'tone': 100}]}),
                             beat(0, duration=(1, 2), tie=True))])
    doc['parts'][0]['measures'][0]['voices'][0]['beats'][0]['fadeIn'] = True
    with pytest.raises(ScoreImportError, match='Unresolved tie'):
        render(parse(doc))


def test_validator_is_scoped_to_songsterr_beat_fields():
    assert not validate_legacy_beat_effect('Songsterr note', 'harmonic', 'natural', beat={})
    assert not validate_legacy_beat_effect('GPIF beat', 'fadeIn', True, beat={})
    with pytest.raises(ScoreImportError, match='Unsupported Songsterr note field'):
        FeatureInventory().inspect({'fadeIn': True}, 'Songsterr note', 'source-note', strict=True)

"""Section markers need positive authored continuity before optional joining."""
from copy import deepcopy

import pytest

from feedback_converter.song_import.hybrid_variants import generate
from test_hybrid_gap_variants import QuarterClock, source


def repeating_source(bars=1):
    parent, rows, context, timeline = source([(q / 2, q / 2 + .5) for q in range(128)])
    for i, row in enumerate(rows):
        row['notes'][0]['f'] = 3 + (i // 8) % bars
        row['occurrences'] = [i // 8 + 1]
        context['beats'][i].update(noteIds=list(row['sourceIds']), occurrence=i // 8 + 1, voice=0, grace=False)
    timeline['measures'] = [{'index': i, 'writtenIndex': i, 'visit': 1, 'quarter': i * 4,
                             'quarters': 4, 'numerator': 4, 'denominator': 4} for i in range(16)]
    timeline['tempoPoints'] = [{'quarter': 0, 'time': 0, 'bpm': 60}]
    context['sectionQuarters'] = [0, 32, 64]
    context['authoredBarSignatures'] = [f'authored-pattern-{i % bars}' for i in range(16)]
    parents = [{**deepcopy(parent), 'start': q, 'end': q + 4,
                'boundaryQuarters': [q, q + 4],
                'boundaries': ['repeat', 'section'] if q == 28 else ['section', 'repeat'],
                'events': [ref for ref in parent['events'] if q <= rows[ref['index']]['start'] < q + 4]}
               for q in (28, 32)]
    return parents, rows, context, timeline


def joined(parts, gaps=((28.5, 35.5),), **budget):
    result, info = generate(*parts, QuarterClock(), list(gaps), **budget)
    return [p for p in result if p.get('variant', {}).get('sectionJoin')], info


@pytest.mark.parametrize('bars', [1, 2, 4])
def test_exact_repeated_music_crosses_one_marker_with_explicit_provenance(bars):
    parts = repeating_source(bars)
    before = deepcopy(parts)
    result, info = joined(parts)
    assert result and not info['budgetLimited']
    best = max(result, key=lambda p: p['activeQuarterBeats'])
    assert (best['start'], best['end'], best['activeQuarterBeats']) == (28.5, 35.5, 7)
    assert {e['index'] for e in best['events']} == set(range(57, 71))
    proof = best['variant']['sectionJoin']
    assert proof == {'quarter': 32, 'barsPerSide': bars, 'proofStart': 32 - 4 * bars,
                     'proofEnd': 32 + 4 * bars, 'meter': {'numerator': 4, 'denominator': 4}, 'bpm': 60,
                     'parents': [{k: p[k] for k in ('start', 'end', 'boundaryQuarters', 'boundaries')}
                                 for p in parts[0]]}
    assert best['variant']['parentPhraseCount'] == 2
    assert proof['parents'][0]['boundaries'][1] == proof['parents'][1]['boundaries'][0] == 'section'
    assert parts == before


@pytest.mark.parametrize('change', ['pitch', 'technique', 'voice', 'missing_raw_note', 'missing_occurrence',
                                  'tempo', 'meter', 'repeat_jump', 'performed_jump', 'missing_traversal',
                                  'second_section', 'physical_rest', 'different_source', 'authored_technique', 'missing_signature'])
def test_section_join_rejects_changed_music_or_incomplete_source_proof(change):
    parts = repeating_source()
    parents, rows, context, timeline = parts
    if change == 'pitch':
        rows[64]['notes'][0]['f'] += 1
    elif change == 'technique':
        rows[64]['notes'][0]['vb'] = True
    elif change == 'voice':
        context['beats'][64]['voice'] = 1
    elif change == 'missing_raw_note':
        context['beats'][64]['noteIds'].append('unprojected-note')
    elif change == 'missing_occurrence':
        rows[64]['occurrences'] = [99]
    elif change == 'tempo':
        timeline['tempoPoints'].append({'quarter': 32, 'time': 32, 'bpm': 90})
    elif change == 'meter':
        timeline['measures'][8]['numerator'] = 3
    elif change == 'repeat_jump':
        timeline['measures'][8]['writtenIndex'] = 0
    elif change == 'performed_jump':
        timeline['measures'][8]['index'] = 99
    elif change == 'missing_traversal':
        timeline['measures'][8].pop('writtenIndex')
    elif change == 'second_section':
        context['sectionQuarters'].append(34)
    elif change == 'physical_rest':
        parents[0]['end'] = 31.5
    elif change == 'authored_technique':
        context['authoredBarSignatures'][8] = 'different-authored-technique'
    elif change == 'missing_signature':
        context.pop('authoredBarSignatures')
    else:
        parents[1]['trackId'] = 'other'
    assert joined(parts)[0] == []


def test_repeated_proof_cannot_cross_an_extra_marker_outside_joined_parents():
    parts = repeating_source(4)
    parts[2]['sectionQuarters'].append(20)
    assert joined(parts)[0] == []


def test_join_never_bridges_two_protected_windows():
    assert joined(repeating_source(), gaps=((28.5, 31.75), (32.25, 35.5)))[0] == []


def test_two_adjacent_parents_never_become_a_three_parent_section_run():
    parts = repeating_source()
    parents, rows, context, _ = parts
    third = deepcopy(parents[1])
    third.update(start=36, end=40, boundaryQuarters=[36, 40])
    third['events'] = [{k: deepcopy(r[k]) for k in ('kind', 'index', 'sourceIds', 'occurrences')}
                       for r in rows if 36 <= r['start'] < 40]
    parents[1]['boundaries'][1] = 'section'
    parents.append(third)
    context['sectionQuarters'].append(36)
    result, _ = joined(parts, gaps=((28.5, 39.5),))
    assert result
    assert all(p['variant']['parentPhraseCount'] == 2 and len(p['variant']['sectionJoin']['parents']) == 2 for p in result)
    assert all(sum(p['start'] < q < p['end'] for q in (32, 36)) == 1 for p in result)


def test_continuity_work_obeys_shared_budget_and_cached_marker_proof():
    parts = repeating_source(4)
    # Two parent/window checks, then three bounded fingerprint sizes.
    result, info = joined(parts, max_window_checks=4)
    assert not result and info['budgetLimited'] and info['windowChecks'] == 4
    result, info = joined(parts, max_window_checks=5)
    assert result and not info['budgetLimited'] and info['windowChecks'] == 5
    assert (result, info) == joined(parts, max_window_checks=5)
    result, info = joined(parts, max_variants=0)
    assert not result and info['budgetLimited']


def test_unproven_legacy_section_inputs_remain_hard_barriers():
    parts = repeating_source()
    parts[3].pop('tempoPoints')
    assert joined(parts)[0] == []


def test_authored_signature_ignores_ids_and_chord_array_order_but_keeps_techniques():
    from fractions import Fraction
    from feedback_converter.song_import.hybrid_context import _authored_bar_signature
    from feedback_converter.song_import.model import Note, Track, WrittenBeat, WrittenVoice
    notes = [Note(Fraction(0), Fraction(1), string, 3 + string, source_id=f'n{string}',
                  beat_id='first', voice_id='0') for string in (0, 1)]
    beat = WrittenBeat('first', Fraction(0), Fraction(1), notes, denominator=4)
    track = Track('guitar', 'Guitar', 'guitar', [40, 45], [notes],
                  written_bars=[[WrittenVoice('voice', [beat], source_index=0)]])
    original = _authored_bar_signature(track, 0, None)
    renamed = deepcopy(track)
    renamed.bars[0].reverse()
    for note in renamed.bars[0]:
        note.source_id += '-another-bar'
        note.beat_id = 'different-id'
    assert _authored_bar_signature(renamed, 0, None) == original
    for field, value in [('tie', True), ('staccato', True), ('slide', 'shift'),
                         ('trill', {'fret': 5, 'speed': 16}), ('effects', {'vb': True})]:
        changed = deepcopy(track)
        setattr(changed.bars[0][0], field, value)
        assert _authored_bar_signature(changed, 0, None) != original


def test_authored_section_join_materializes_complete_events_and_passes_independent_verifier(tmp_path):
    import json
    from zipfile import ZipFile
    from test_song_import_score import beat, measure, raw_score
    from test_songsterr_hybrid_lead import rest, build
    doc = raw_score([measure(beat(12)), measure(rest()),
                     measure(rest((3, 4)), beat(12, duration=(1, 4)))])
    doc['tracks'].append({**deepcopy(doc['tracks'][0]), 'id': 1, 'name': 'Rhythm Guitar'})
    doc['parts'].append({'measures': [measure(*(beat(3 + i % 2, duration=(1, 8)) for i in range(8)))
                                          for _ in range(3)]})
    for part in doc['parts']:
        part['measures'][2]['marker'] = 'Different section label'
    *_, archive, report = build(tmp_path, doc, overrides={'mainTrackId': '0', 'roles': {'1': 'accompaniment'}})
    assert report['status'] == 'passed', report
    with ZipFile(archive) as z:
        receipt = json.loads(z.read('import/hybrid-lead.json'))
        joined = [p for p in receipt['passages'] if p.get('variant', {}).get('sectionJoin')]
        assert joined
        passage = joined[0]
        assert passage['variant']['sectionJoin']['quarter'] == 8
        assert passage['variant']['sectionJoin']['barsPerSide'] == 1
        assert (passage['start'], passage['end']) == (4.5, 10.5)
        original = json.loads(z.read('arrangements/1.json'))
        hybrid = json.loads(z.read(next(p for p in z.namelist() if p.startswith('arrangements/hybrid-'))))
        assert len(passage['events']) == 12
        assert all(original[e['kind']][e['index']] in hybrid[e['kind']] for e in passage['events'])

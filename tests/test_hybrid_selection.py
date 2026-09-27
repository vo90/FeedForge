"""Known musical choices independent of Hybrid Lead materialization."""
from copy import deepcopy

import pytest

from feedback_converter.song_import.hybrid_selection import (
    automatic_role, parse_track, regional_candidates, select_base, suggested_role,
)


STANDARD = [40, 45, 50, 55, 59, 64]
DROP = [38, 45, 50, 55, 59, 64]


def track(tid, name, start=0, count=16, tuning=None, capo=0, expressive=False, fret=3):
    return {'id': tid, 'name': name, 'instrument': 'guitar', 'role': 'lead',
            'tuning': list(tuning or STANDARD), 'capo': capo, 'templates': [], 'chords': [],
            'notes': [dict(t=start + i, sus=1, s=0, f=fret + i % 4, **({'vb': True} if expressive else {})) for i in range(count)]}


def performance(*tracks, sections=None, duration=32):
    return {'tracks': list(tracks), 'duration': duration, 'sections': sections or [],
            'scoreTimeline': {'tempoPoints': [{'quarter': 0, 'time': 0, 'bpm': 60}]}}


@pytest.mark.parametrize('name,expected', [
    ('Solo Chords', 'accompaniment'), ('Solo and Harmonies', 'lead'),
    ('Dave | Lead & Rhythm Guitar', 'lead'), ('Electric Guitar | Solo', 'solo'),
    ('Extra Lead Guitar', 'lead'), ('Additional Guitar', None), ('Guitar 2', None),
    ('Main Guitar', 'lead'), ('Extras', 'accompaniment'), ('Delay Guitar', 'accompaniment'),
])
def test_role_suggestions_scope_mixed_names_and_unknown_defaults(name, expected):
    source = track('a', name)
    assert suggested_role(source) == expected
    assert automatic_role(source) == (expected or 'accompaniment')


def test_instrument_model_is_not_a_harmony_role_and_role_field_can_be_first():
    assert not parse_track(track('a', 'Jimmy Page | Harmony Sovereign | Acoustic Guitar'))['harmony']
    facts = parse_track(track('b', 'Lead Guitar | Rudolf Schenker | Gibson Flying V'))
    assert facts['performerTokens'] == ['rudolf', 'schenker']
    assert facts['lead']


def test_brief_solo_never_moves_the_song_to_its_outlier_tuning():
    base = track('base', 'Main Guitar', count=24)
    solo = track('solo', 'Guitar Solo', start=10, count=2, tuning=DROP, expressive=True)
    result = select_base(performance(base, solo))
    assert result['mainTrackId'] == 'base'
    assert result['setup'] == {'tuning': STANDARD, 'capo': 0}
    assert result['excluded'] == [{'trackId': 'solo', 'reason': 'incompatible_setup'}]


def test_tuning_dominance_uses_activity_union_not_duplicate_votes():
    base = track('base', 'Main Guitar', count=24)
    brief = track('other', 'Guitar', count=6, tuning=DROP)
    doubled = [dict(deepcopy(brief), id=f'double-{i}') for i in range(10)]
    result = select_base(performance(base, brief, *doubled))
    assert result['mainTrackId'] == 'base'
    assert result['setupRanking'][1]['nonSoloActivity'] == 6


def test_capo_follows_strong_base_not_longest_capo_group():
    lead = track('lead', 'Lead Guitar', count=12)
    accompaniment = track('rhythm', '12-String Acoustic Rhythm Guitar', count=32, capo=7)
    result = select_base(performance(lead, accompaniment))
    assert result['mainTrackId'] == 'lead'
    assert result['setup'] == {'tuning': STANDARD, 'capo': 0}
    assert result['excluded'] == [{'trackId': 'rhythm', 'reason': 'incompatible_setup'}]


def test_a_solo_excerpt_can_use_the_solo_as_base():
    solo = track('solo', 'Guitar Solo', count=28)
    backing = track('rhythm', 'Rhythm Guitar', count=32)
    assert select_base(performance(solo, backing))['mainTrackId'] == 'solo'


def test_provided_main_is_honored_without_claiming_user_provenance():
    base, other = track('base', 'Main Guitar'), track('other', 'Guitar Solo', count=2, tuning=DROP)
    result = select_base(performance(base, other), {'mainTrackId': 'other'})
    assert result['mainTrackId'] == 'other'
    assert result['reason'] == 'provided_main'


def test_base_selection_is_source_order_invariant_and_uses_canonical_name_before_id():
    a, b = track('z', 'Adrian Smith | Lead Guitar'), track('a', 'Dave Murray | Lead Guitar')
    first = select_base(performance(a, b))
    assert first == select_base(performance(b, a))
    assert first['mainTrackId'] == 'z'


def test_generic_guitars_always_get_a_stable_automatic_base():
    a, b = track('a', 'Guitar 2', count=10), track('b', 'Guitar 3', count=20)
    assert select_base(performance(a, b))['mainTrackId'] == 'b'
    assert parse_track(a)['priorRole'] == 'unknown'


def test_excluded_or_unplayable_sources_cannot_be_automatic_base():
    a, b = track('a', 'Main Guitar'), track('b', 'Rhythm Guitar')
    assert select_base(performance(a, b), {'excludedTrackIds': ['a']})['mainTrackId'] == 'b'
    assert select_base(performance(a, b), {'excludedTrackIds': ['a', 'b']})['mainTrackId'] is None
    a['notes'][0]['f'] = 30
    a['notes'] = a['notes'][:1]
    assert select_base(performance(a, b))['mainTrackId'] == 'b'


@pytest.mark.parametrize('names,labels', [
    (('Adrian Smith', 'Dave Murray'), ('Solo (Adrian Smith)', 'Solo (Dave Murray)')),
    (('Marty Friedman', 'Dave Mustaine'), ('Solo A (Marty)', 'Solo C (Dave)')),
])
def test_named_sections_follow_each_guitarists_solo_even_when_base_keeps_playing(names, labels):
    a = track('a', f'{names[0]} | Lead & Rhythm Guitar', count=24)
    b = track('b', f'{names[1]} | Lead & Rhythm Guitar', count=24)
    sections = [{'name': labels[0], 'time': 0}, {'name': labels[1], 'time': 12}]
    candidates = regional_candidates(performance(a, b, sections=sections, duration=24), {}, 'b')
    named = [p for p in candidates if p['evidence'] == 'named_soloist']
    assert [(p['trackId'], p['start'], p['end']) for p in named] == [('a', 0, 12), ('b', 12, 24)]
    assert all(p['confidence'] == 'high' and p['eligible'] for p in named)


def test_named_performer_extra_and_harmony_do_not_beat_the_full_lead():
    main = track('main', 'Don Felder | Lead Guitar', count=12)
    extra = track('extra', 'Don Felder | Extra Lead Guitar', count=4, expressive=True, fret=14)
    harmony = track('harmony', 'Don Felder | Harmony Guitar', count=8, expressive=True, fret=17)
    p = performance(main, extra, harmony, sections=[{'name': 'Solo (Don Felder)', 'time': 0}], duration=12)
    result = regional_candidates(p, {}, 'main')
    assert next(c for c in result if c['evidence'] == 'named_soloist')['trackId'] == 'main'


@pytest.mark.parametrize('label', ['Pre-Solo', 'Post Solo', 'Tenor Saxophone Solo', 'Bass Solo'])
def test_non_guitar_or_pre_post_solo_labels_do_not_create_solo_obligations(label):
    p = performance(track('a', 'Adrian Smith | Lead Guitar'), track('b', 'Dave Murray | Lead Guitar'),
                    sections=[{'name': label, 'time': 0}])
    assert regional_candidates(p, {}, 'a') == []


def test_solo_chords_are_not_featured_over_main_solo_melody():
    lead = track('lead', 'John Frusciante | Lead Guitar', expressive=True)
    chords = track('chords', 'John Frusciante | Solo Chords')
    p = performance(lead, chords, sections=[{'name': 'Guitar Solo', 'time': 0}])
    candidates = regional_candidates(p, {}, 'lead')
    assert {c['trackId'] for c in candidates} == {'lead'}


def test_dedicated_pickup_is_retained_and_ghost_only_fade_is_not_primary():
    base = track('base', 'Main Guitar')
    solo = track('solo', 'Guitar Solo', start=7, count=6)
    solo['notes'][-1]['ghost'] = True
    p = performance(base, solo, sections=[{'name': 'Verse', 'time': 0}, {'name': 'Solo', 'time': 8}])
    result = regional_candidates(p, {}, 'base')
    island = next(c for c in result if c['evidence'] == 'dedicated_solo')
    assert (island['start'], island['end']) == (7, 12)


def test_internal_ghost_attack_bridges_dedicated_phrase_while_fade_is_trimmed():
    base = track('base', 'Main Guitar')
    solo = track('solo', 'Guitar Solo', start=4, count=6)
    solo['notes'][2]['ghost'] = True
    solo['notes'][-1]['ghost'] = True
    candidates = regional_candidates(performance(base, solo), {}, 'base')
    islands = [c for c in candidates if c['evidence'] == 'dedicated_solo']
    assert [(c['start'], c['end']) for c in islands] == [(4, 9)]


@pytest.mark.parametrize('change,reason', [('tuning', 'incompatible_setup'), ('capo', 'incompatible_setup'),
                                          ('exclude', 'excluded_by_user'), ('role', 'explicit_accompaniment')])
def test_primary_evidence_is_retained_with_specific_ineligibility(change, reason):
    base, solo = track('base', 'Main Guitar'), track('solo', 'Guitar Solo', start=4, count=4)
    options = {}
    if change == 'tuning': solo['tuning'] = DROP
    elif change == 'capo': solo['capo'] = 2
    elif change == 'exclude': options['excludedTrackIds'] = ['solo']
    else: options['roles'] = {'solo': 'accompaniment'}
    result = regional_candidates(performance(base, solo), options, 'base')
    candidate = next(c for c in result if c['trackId'] == 'solo')
    assert not candidate['eligible'] and candidate['reason'] == reason


def test_ordinary_lead_label_alone_cannot_replace_active_base():
    p = performance(track('base', 'Main Guitar'), track('other', 'Lead Guitar'))
    assert regional_candidates(p, {}, 'base') == []


def test_unlabelled_foreground_can_replace_backing_with_multiple_local_clues():
    base = track('base', 'Lead & Rhythm Guitar', fret=3)
    solo = track('solo', 'Lead & Rhythm Guitar', fret=15, expressive=True)
    candidates = regional_candidates(performance(base, solo), {}, 'base')
    assert {c['trackId'] for c in candidates} == {'solo'}
    assert all(c['evidence'] == 'regional_lead' and c['confidence'] == 'medium' for c in candidates)


def test_simultaneous_named_players_offer_a_stable_voice_and_alternatives():
    a, b = track('a', 'Don Felder | Lead Guitar'), track('b', 'Joe Walsh | Lead Guitar')
    p = performance(a, b, sections=[{'name': 'Solo (Don & Joe)', 'time': 0}])
    one = regional_candidates(p, {}, 'a')
    p['tracks'].reverse()
    assert regional_candidates(p, {}, 'a') == one
    assert len(one) == 1 and one[0]['alternatives'] == ['b']


def test_parallel_solo_takes_keep_one_voice_across_sections():
    a, b = track('a', 'Lead Guitar [Solo #1]'), track('b', 'Lead Guitar [Solo #2]')
    for note in b['notes'][8:10]:
        note['vb'] = True
    p = performance(a, b, sections=[{'name': 'Main Solo Part 1', 'time': 0},
                                    {'name': 'Main Solo Part 2', 'time': 8}], duration=16)
    assert [c['trackId'] for c in regional_candidates(p, {}, 'a')] == ['a', 'a']


def test_polyphonic_slide_foreground_beats_single_note_clean_backing():
    base = track('base', 'Main Guitar', start=16)
    clean = track('clean', 'Clean Guitar', count=8)
    slide = track('slide', 'Double Slide Guitar', count=0, tuning=DROP)
    slide['chords'] = [{'t': i, 'id': 0, 'notes': [{'s': s, 'f': 9 + i % 4, 'sus': 1, 'vb': True} for s in (2, 3)]} for i in range(8)]
    p = performance(base, clean, slide, sections=[{'name': 'Solo 1', 'time': 0}, {'name': 'Verse', 'time': 8}])
    selected = next(c for c in regional_candidates(p, {}, 'base') if c['sectionName'] == 'Solo 1')
    assert selected['trackId'] == 'slide'
    assert not selected['eligible'] and selected['reason'] == 'incompatible_setup'


def test_ambiguous_first_name_does_not_claim_confirmed_named_ownership():
    a, b = track('a', 'Dave Murray | Lead Guitar'), track('b', 'Dave Mustaine | Lead Guitar')
    p = performance(a, b, sections=[{'name': 'Solo (Dave)', 'time': 0}])
    result = regional_candidates(p, {}, 'a')
    assert all(c['evidence'] != 'named_soloist' for c in result)


def test_shared_ensemble_alias_is_not_a_different_person_with_the_same_first_name():
    base = track('base', 'Kirk Hammett | Lead Guitar', count=1)
    clean = track('clean', 'James Hetfield | Clean Guitar', count=12)
    extra = track('extra', 'Kirk & James | Extra Guitars', count=12)
    rhythm = track('rhythm', 'James Hetfield | Rhythm Guitar', count=12)
    p = performance(base, clean, extra, rhythm, sections=[{'name': 'Solo (James)', 'time': 0}], duration=12)
    selected = next(c for c in regional_candidates(p, {}, 'base') if c['sectionName'] == 'Solo (James)')
    assert selected['trackId'] == 'clean'
    assert selected['evidence'] == 'named_soloist'


def test_named_octave_bass_guitar_layer_cannot_determine_normal_guitar_tuning():
    bass = track('bass', 'Guitarist | Bass Overdrive', count=32, tuning=[28, 33, 38, 43, 47, 52])
    normal = track('normal', 'Guitarist | Lead Guitar', count=12)
    result = select_base(performance(bass, normal))
    assert result['mainTrackId'] == 'normal'
    assert result['setupRanking'][1]['votingExcluded'] == [{'trackId': 'bass', 'reason': 'bass_register_guitar_layer'}]


def test_sole_bass_register_guitar_source_remains_usable():
    source = track('only', 'Bass Overdrive', tuning=[28, 33, 38, 43, 47, 52])
    result = select_base(performance(source))
    assert result['mainTrackId'] == 'only'
    assert not result['setupRanking'][0]['votingExcluded']


@pytest.mark.parametrize('name,tuning', [('Baritone Guitar', [28, 33, 38, 43, 47, 52]),
                                       ('Guitar 2', [28, 33, 38, 43, 47, 52]),
                                       ('Bass Chords Guitar', STANDARD)])
def test_pitch_or_bass_word_alone_cannot_remove_a_tuning_vote(name, tuning):
    source = track('candidate', name, count=32, tuning=tuning)
    other = track('other', 'Lead Guitar', count=12, tuning=DROP)
    result = select_base(performance(source, other))
    assert result['mainTrackId'] == 'candidate'
    assert not result['setupRanking'][0]['votingExcluded']


@pytest.mark.parametrize('name,tuning', [
    ('High-Strung Acoustic Guitar', [64, 57, 62, 67, 59, 64]),
    ('Nashville Guitar', [52, 57, 62, 67, 59, 64]),
    ('High Strung Guitar', [50, 55, 60, 65, 57, 62]),
])
def test_named_octave_texture_cannot_outvote_substantial_regular_guitar(name, tuning):
    texture = track('texture', name, count=32, tuning=tuning)
    ordinary = track('ordinary', 'Lead Guitar', count=20)
    result = select_base(performance(texture, ordinary))
    assert result['mainTrackId'] == 'ordinary'
    assert result['setupRanking'][1]['votingExcluded'] == [
        {'trackId': 'texture', 'reason': 'octave_texture_guitar_layer'}]


def test_sole_octave_texture_source_keeps_its_setup():
    texture = track('texture', 'Nashville Guitar', tuning=[52, 57, 62, 67, 59, 64])
    result = select_base(performance(texture))
    assert result['mainTrackId'] == 'texture'
    assert not result['setupRanking'][0]['votingExcluded']


@pytest.mark.parametrize('name', ['Solo Guitar', 'Lead Guitar'])
def test_long_octave_texture_song_is_not_displaced_by_a_brief_other_tuning(name):
    texture = track('texture', 'High-Strung Acoustic Guitar', count=32, tuning=[52, 57, 62, 67, 59, 64])
    brief = track('brief', name, count=4, tuning=DROP)
    result = select_base(performance(texture, brief))
    assert result['mainTrackId'] == 'texture'
    assert not result['setupRanking'][0]['votingExcluded']


@pytest.mark.parametrize('name,tuning', [
    ('Acoustic Guitar', [52, 57, 62, 67, 59, 64]),
    ('Nashville Guitar', STANDARD),
    ('High-Strung Guitar', [52, 57, 62, 67, 71, 76]),
])
def test_octave_texture_requires_name_and_relative_string_displacement(name, tuning):
    source = track('candidate', name, count=32, tuning=tuning)
    other = track('other', 'Lead Guitar', count=20, tuning=DROP)
    result = select_base(performance(source, other))
    assert result['mainTrackId'] == 'candidate'
    assert not result['setupRanking'][0]['votingExcluded']


def test_named_body_tail_does_not_extend_attack_ownership_into_following_section():
    a, b = track('a', 'Adrian Smith | Lead Guitar', count=12), track('b', 'Dave Murray | Lead Guitar', count=12)
    a['notes'][7]['sus'] = 4
    p = performance(a, b, sections=[{'name': 'Solo (Adrian Smith)', 'time': 0},
                                    {'name': 'Verse', 'time': 8}], duration=12)
    selected = next(c for c in regional_candidates(p, {}, 'b') if c['evidence'] == 'named_soloist')
    assert selected['end'] == 11
    assert selected['ownedEnd'] == 8


def test_distinct_named_attack_windows_survive_a_shared_hard_footprint():
    source = track('solo', 'Adrian Smith | Lead Guitar', count=2)
    source['notes'][1]['t'] = 8
    rows = {'solo': [{'kind': 'notes', 'index': i, 'start': 0, 'end': 12, 'notes': [n]}
                     for i, n in enumerate(source['notes'])]}
    p = performance(source, sections=[{'name': 'Solo A (Adrian)', 'time': 0},
                                      {'name': 'Solo B (Adrian)', 'time': 8}], duration=16)
    candidates = regional_candidates(p, {}, 'solo', rows=rows)
    assert len(candidates) == 2
    assert {c['ownedEnd'] for c in candidates} == {8, 16}


@pytest.mark.parametrize('label,fallback_evidence', [('Solo (Adrian & Dave)', 'named_soloist'),
                                                   ('Solo (Adrian)', 'regional_lead'),
                                                   ('Guitar Solo', 'regional_lead')])
def test_incompatible_winner_cannot_hide_a_compatible_lead_under_rhythm_base(label, fallback_evidence):
    base = track('base', 'Rhythm Guitar')
    first = track('first', 'Adrian Smith | Lead & Rhythm Guitar', tuning=DROP, expressive=True, fret=15)
    second = track('second', 'Dave Murray | Lead & Rhythm Guitar', fret=10)
    p = performance(base, first, second, sections=[{'name': label, 'time': 0}])
    candidates = regional_candidates(p, {}, 'base')
    preferred = next(c for c in candidates if c['trackId'] == 'first')
    substitute = next(c for c in candidates if c['trackId'] == 'second')
    assert not preferred['eligible'] and preferred['reason'] == 'incompatible_setup'
    assert substitute['eligible'] and substitute['evidence'] == fallback_evidence
    assert substitute['fallbackForTrackId'] == 'first'


def test_incompatible_named_solo_does_not_mislabel_rhythm_as_named_replacement():
    base = track('base', 'Rhythm Guitar')
    solo = track('solo', 'Adrian Smith | Lead Guitar', tuning=DROP)
    p = performance(base, solo, sections=[{'name': 'Solo (Adrian)', 'time': 0}])
    candidates = regional_candidates(p, {}, 'base')
    assert len(candidates) == 1 and not candidates[0]['eligible']


def _local_owner_case(solo_name='Alex | Rhythm Guitar', solo_fret=2):
    base = track('base', 'Other Player | Lead Guitar', count=1, expressive=True, fret=20)
    backing = track('backing', 'Alex | Clean Guitar', count=16, fret=14)
    for note in backing['notes']:
        note['lr'] = True
    melody = track('melody', solo_name, count=16, fret=solo_fret)
    for i, note in enumerate(melody['notes']):
        note['t'] = i + (0.25 if i % 3 == 1 else 0)
        note['sus'] = .75
        note['f'] = solo_fret + [0, 2, 3, 5, 2, 0, 3, 1][i % 8]
        if i % 4 == 0:
            note['vb'] = True
    return performance(base, backing, melody, sections=[{'name': 'Solo (Alex)', 'time': 0}], duration=16)


@pytest.mark.parametrize('name', ['Alex | Rhythm Guitar', 'Alex | Clean Guitar', 'Alex | Acoustic Guitar', 'Alex | Guitar 2'])
@pytest.mark.parametrize('fret', [0, 14])
def test_named_local_melody_beats_repeating_arpeggios_regardless_of_storage_label_or_register(name, fret):
    p = _local_owner_case(name, fret)
    result = regional_candidates(p, {}, 'base')
    selected = next(c for c in result if c['evidence'] == 'named_soloist')
    assert selected['trackId'] == 'melody'
    assert selected['confidence'] == 'high'
    evidence = selected['selectionEvidence']
    assert evidence['reason'] == 'clear_local_foreground'
    assert evidence['confidenceMargin'] > .6
    assert {c['trackId'] for c in evidence['considered']} == {'melody', 'backing'}


def test_clear_content_choice_is_invariant_under_ordinary_role_labels_and_source_order():
    p = _local_owner_case()
    selected = regional_candidates(p, {}, 'base')[0]
    assert selected['trackId'] == 'melody'
    p['tracks'][1]['name'] = 'Alex | Lead Guitar'
    p['tracks'][2]['name'] = 'Alex | Rhythm Guitar'
    p['tracks'].reverse()
    changed = regional_candidates(p, {}, 'base')[0]
    assert changed['trackId'] == 'melody'
    assert changed['confidence'] == 'high'


def test_clean_double_stop_melody_can_beat_single_note_arpeggio_backing():
    p = _local_owner_case('Alex | Clean Guitar')
    melody = p['tracks'][2]
    melody['chords'] = [{'t': n['t'], 'id': 0,
                        'notes': [dict(n, s=s, vb=True) for s in (2, 3)]} for n in melody['notes']]
    melody['notes'] = []
    result = regional_candidates(p, {}, 'base')
    assert result[0]['trackId'] == 'melody'
    assert result[0]['confidence'] == 'high'


def test_named_sparse_phrase_beats_denser_palm_muted_backing_without_density_reward():
    p = _local_owner_case()
    backing = p['tracks'][1]
    backing['notes'] = [dict(t=i / 4, sus=.25, s=0, f=2 + i % 4, pm=True) for i in range(64)]
    p['tracks'][2]['notes'] = p['tracks'][2]['notes'][::2]
    assert regional_candidates(p, {}, 'base')[0]['trackId'] == 'melody'


def test_plain_clean_melody_does_not_require_expressive_technique_flags():
    p = _local_owner_case('Alex | Clean Guitar')
    for note in p['tracks'][2]['notes']:
        note.pop('vb', None)
    selected = regional_candidates(p, {}, 'base')[0]
    assert selected['trackId'] == 'melody'
    assert selected['confidence'] == 'high'


def test_one_expressive_sustain_is_not_enough_to_outvote_a_named_melodic_phrase():
    p = _local_owner_case()
    p['tracks'].append(track('sustain', 'Alex | Clean Guitar', count=1, expressive=True, fret=20))
    p['tracks'][-1]['notes'][0]['sus'] = 16
    assert regional_candidates(p, {}, 'base')[0]['trackId'] == 'melody'


def test_close_named_owner_choices_report_ambiguity_and_preserve_a_coherent_incumbent():
    a = track('a', 'Alex | Lead Guitar')
    b = track('b', 'Alex | Clean Guitar')
    b['notes'][0]['vb'] = True
    p = performance(a, b, sections=[{'name': 'Solo (Alex)', 'time': 0}], duration=16)
    selected = regional_candidates(p, {}, 'a')[0]
    assert selected['trackId'] == 'a'
    assert selected['confidence'] == 'low'
    assert selected['alternatives'] == ['b']
    assert selected['selectionEvidence']['reason'] in {'coherent_incumbent', 'ambiguous_local_default'}
    p['tracks'].reverse()
    assert regional_candidates(p, {}, 'a')[0] == selected


def test_clear_same_performer_handover_overrides_the_previous_clean_incumbent():
    p = _local_owner_case()
    selected = regional_candidates(p, {}, 'backing')[0]
    assert selected['trackId'] == 'melody'
    assert selected['confidence'] == 'high'


def test_rhythm_label_does_not_hide_strong_unnamed_low_register_foreground():
    p = _local_owner_case()
    p['sections'] = [{'name': 'Interlude', 'time': 0}]
    p['tracks'] = p['tracks'][1:]
    result = regional_candidates(p, {}, 'backing')
    assert {c['trackId'] for c in result} == {'melody'}
    assert all(c['evidence'] == 'regional_lead' and c['confidence'] == 'medium' for c in result)


def test_repeated_rhythm_with_a_few_techniques_is_not_automatically_foreground():
    base = track('base', 'Main Guitar')
    rhythm = track('rhythm', 'Rhythm Guitar', fret=14)
    for i, note in enumerate(rhythm['notes']):
        note['pm'] = True
        if i % 4 == 0:
            note['vb'] = True
    assert regional_candidates(performance(base, rhythm), {}, 'base') == []


def test_strong_content_does_not_override_fixed_tuning_or_explicit_source_exclusion():
    p = _local_owner_case()
    p['tracks'][2]['tuning'] = DROP
    selected = next(c for c in regional_candidates(p, {}, 'base') if c['trackId'] == 'melody')
    assert not selected['eligible'] and selected['reason'] == 'incompatible_setup'
    p['tracks'][2]['tuning'] = STANDARD
    selected = next(c for c in regional_candidates(p, {'excludedTrackIds': ['melody']}, 'base') if c['trackId'] == 'melody')
    assert not selected['eligible'] and selected['reason'] == 'excluded_by_user'


def test_named_harmony_extra_and_effect_layers_cannot_replace_an_ordinary_local_owner():
    p = _local_owner_case()
    p['tracks'].extend(track(key, f'Alex | {name}', expressive=True, fret=17)
                       for key, name in [('harmony', 'Harmony Guitar'), ('extra', 'Extra Guitar'), ('effect', 'Delay Guitar')])
    assert regional_candidates(p, {}, 'base')[0]['trackId'] == 'melody'


@pytest.mark.parametrize('label', ['Extra Lead Guitar', 'Harmony Guitar'])
def test_clear_featured_phrase_can_live_on_secondary_track_when_all_ordinary_parts_are_backing(label):
    p = _local_owner_case(f'Alex | {label}')
    selected = regional_candidates(p, {}, 'base')[0]
    assert selected['trackId'] == 'melody'
    assert selected['confidence'] == 'high'
    assert selected['selectionEvidence']['reason'] == 'foreground_over_secondary_label'
    row = next(item for item in selected['selectionEvidence']['considered'] if item['trackId'] == 'melody')
    assert row['layerPromotedByContent']


def test_ordinary_real_melody_is_not_superseded_by_a_busier_secondary_voice():
    p = _local_owner_case('Alex | Lead Guitar')
    extra = deepcopy(p['tracks'][2])
    extra.update(id='extra', name='Alex | Extra Lead Guitar')
    for note in extra['notes']:
        note['vb'] = True
    p['tracks'].append(extra)
    selected = regional_candidates(p, {}, 'base')[0]
    assert selected['trackId'] == 'melody'
    extra_row = next(item for item in selected['selectionEvidence']['considered'] if item['trackId'] == 'extra')
    assert not extra_row['layerPromotedByContent']
    assert selected['confidence'] == 'low'
    assert selected['selectionEvidence']['reason'] == 'secondary_layer_uncertain'
    assert selected['selectionEvidence']['confidenceMargin'] < 0


def test_similar_secondary_part_is_not_promoted_merely_because_primary_is_repetitive():
    p = _local_owner_case()
    p['tracks'][2] = deepcopy(p['tracks'][1])
    p['tracks'][2].update(id='extra', name='Alex | Extra Lead Guitar')
    selected = regional_candidates(p, {}, 'base')[0]
    assert selected['trackId'] == 'backing'
    assert not any(item['layerPromotedByContent'] for item in selected['selectionEvidence']['considered'])


def test_effect_layer_is_not_promoted_by_melodic_content():
    p = _local_owner_case('Alex | Delay Guitar')
    assert regional_candidates(p, {}, 'base')[0]['trackId'] == 'backing'


def test_clear_unnamed_secondary_foreground_can_replace_repeating_backing():
    p = _local_owner_case('Alex | Extra Lead Guitar')
    p['sections'] = [{'name': 'Interlude', 'time': 0}]
    p['tracks'] = p['tracks'][1:]
    result = regional_candidates(p, {}, 'backing')
    assert {item['trackId'] for item in result} == {'melody'}


@pytest.mark.parametrize('constraint', ['tuning', 'capo', 'excluded', 'role'])
def test_unavailable_primary_cannot_hide_the_best_compatible_secondary_melody(constraint):
    p = _local_owner_case('Alex | Extra Lead Guitar')
    unavailable = track('unavailable', 'Alex | Lead Guitar', expressive=True,
                        tuning=DROP if constraint == 'tuning' else STANDARD,
                        capo=2 if constraint == 'capo' else 0)
    p['tracks'].append(unavailable)
    options = ({'excludedTrackIds': ['unavailable']} if constraint == 'excluded' else
               {'roles': {'unavailable': 'accompaniment'}} if constraint == 'role' else {})
    candidates = regional_candidates(p, options, 'base')
    rejected = next(item for item in candidates if item['trackId'] == 'unavailable')
    assert not rejected['eligible']
    fallback = next(item for item in candidates if item.get('fallbackForTrackId') == 'unavailable')
    assert fallback['trackId'] == 'melody'
    assert fallback['eligible'] and fallback['confidence'] == 'high'


@pytest.mark.parametrize('name', ['Solo FX Guitar', 'Alex | Solo Delay Guitar'])
def test_effect_name_cannot_bypass_exclusion_by_containing_solo(name):
    p = performance(track('base', 'Main Guitar'), track('effect', name, expressive=True))
    assert regional_candidates(p, {}, 'base') == []
    # An explicit review choice is the user's judgment about that source.
    chosen = regional_candidates(p, {'roles': {'effect': 'solo'}}, 'base')
    assert len(chosen) == 1 and chosen[0]['trackId'] == 'effect'
    assert chosen[0]['eligible'] and chosen[0]['evidence'] == 'dedicated_solo'


@pytest.mark.parametrize('constraint', ['tuning', 'capo', 'excluded', 'role'])
def test_unavailable_nonwinner_does_not_veto_a_playable_secondary_foreground(constraint):
    p = _local_owner_case('Alex | Extra Lead Guitar')
    unavailable = track('unavailable', 'Alex | Clean Guitar', count=0,
                        tuning=DROP if constraint == 'tuning' else STANDARD,
                        capo=2 if constraint == 'capo' else 0)
    unavailable['chords'] = [{'t': i, 'id': 0, 'notes': [{'s': s, 'f': i, 'sus': 1} for s in (0, 1)]}
                             for i in range(8)]
    p['tracks'].append(unavailable)
    options = ({'excludedTrackIds': ['unavailable']} if constraint == 'excluded' else
               {'roles': {'unavailable': 'accompaniment'}} if constraint == 'role' else {})
    selected = regional_candidates(p, options, 'base')[0]
    assert selected['trackId'] == 'melody' and selected['eligible']


@pytest.mark.parametrize('storage_label', ['Rhythm Guitar', 'Clean Guitar', 'Guitar 2', 'Extra Lead Guitar'])
def test_incompatible_named_solo_uses_another_players_clear_foreground_regardless_of_storage_label(storage_label):
    p = _local_owner_case(f'Other Player | {storage_label}')
    p['tracks'] = p['tracks'][1:]
    p['tracks'][0]['name'] = 'Accompanist | Lead Guitar'
    unavailable = track('unavailable', 'Alex | Lead Guitar', expressive=True, tuning=DROP)
    p['tracks'].append(unavailable)
    candidates = regional_candidates(p, {}, 'backing')
    rejected = next(c for c in candidates if c['trackId'] == 'unavailable')
    assert not rejected['eligible'] and rejected['reason'] == 'incompatible_setup'
    fallback = next(c for c in candidates if c.get('fallbackForTrackId') == 'unavailable')
    assert fallback['trackId'] == 'melody' and fallback['eligible']
    assert fallback['evidence'] == 'regional_lead'


def test_same_named_players_compatible_backing_does_not_veto_another_players_clear_melody():
    p = _local_owner_case('Blake | Rhythm Guitar')
    p['tracks'].append(track('unavailable', 'Alex | Lead Guitar', expressive=True, tuning=DROP))
    candidates = regional_candidates(p, {}, 'base')
    fallback = next(c for c in candidates if c.get('fallbackForTrackId') == 'unavailable')
    assert fallback['trackId'] == 'melody'
    assert fallback['evidence'] == 'regional_lead' and fallback['confidence'] == 'medium'


def test_credible_same_player_fallback_retains_identity_preference_over_parallel_other_player():
    p = _local_owner_case('Alex | Rhythm Guitar')
    p['tracks'].append(track('unavailable', 'Alex | Lead Guitar', expressive=True, tuning=DROP))
    other = deepcopy(p['tracks'][2])
    other.update(id='other', name='Blake | Lead Guitar')
    for note in other['notes']:
        note['vb'] = True
    p['tracks'].append(other)
    candidates = regional_candidates(p, {}, 'base')
    fallback = next(c for c in candidates if c.get('fallbackForTrackId') == 'unavailable')
    assert fallback['trackId'] == 'melody'
    assert fallback['evidence'] == 'named_soloist'

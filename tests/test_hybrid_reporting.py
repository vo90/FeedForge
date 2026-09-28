from feedback_converter.song_import.hybrid_reporting import activity_summary


def track(tid, capo=0, instrument='guitar'):
    return {'id': tid, 'name': tid, 'instrument': instrument, 'tuning': [40, 45, 50, 55, 59, 64], 'capo': capo}


def chart(*intervals):
    return {'notes': [{'t': a, 'sus': b-a, 's': 0, 'f': 0} for a, b in intervals], 'chords': []}


def test_capoed_opening_is_reported_without_claiming_no_guitar_is_playing():
    tracks = [track('main'), track('acoustic', 7)]
    originals = {'main': {'chart': chart((26, 30))}, 'acoustic': {'chart': chart((0, 26))}}
    report = activity_summary(originals, tracks, 'main', chart((26, 30)))
    assert report['sourceActiveSeconds'] == 30
    assert report['hybridActiveSeconds'] == 4
    assert report['incompatibleOnlySeconds'] == 26
    assert report['regions'] == [{'start': 0, 'end': 26, 'reason': 'only_incompatible_setup',
                                  'trackIds': ['acoustic'], 'sources': [{'id': 'acoustic', 'name': 'acoustic'}]}]


def test_duplicate_sources_and_chord_members_do_not_multiply_activity():
    chord = {'notes': [], 'chords': [{'t': 0, 'notes': [{'s': 0, 'f': 1, 'sus': 4}, {'s': 1, 'f': 2, 'sus': 2}]}]}
    report = activity_summary({'a': {'chart': chord}, 'b': {'chart': chart((0, 4))}},
                              [track('a'), track('b')], 'a', chart((1, 3)))
    assert report['sourceActiveSeconds'] == 4
    assert report['hybridActiveSeconds'] == 2
    assert report['compatibleUnfilledSeconds'] == 2
    assert [(r['start'], r['end']) for r in report['regions']] == [(0, 1), (3, 4)]


def test_gap_is_split_when_compatible_material_becomes_available():
    report = activity_summary({'a': {'chart': chart((2, 4))}, 'b': {'chart': chart((0, 6))}},
                              [track('a'), track('b', 7)], 'a', chart())
    assert [(r['start'], r['end'], r['reason']) for r in report['regions']] == [
        (0, 2, 'only_incompatible_setup'), (2, 4, 'compatible_source_not_selected'), (4, 6, 'only_incompatible_setup')]


def test_ghosts_bass_and_zero_duration_attacks_do_not_inflate_sustained_activity():
    notes = chart((0, 0), (1, 4))
    notes['notes'][1]['ghost'] = True
    report = activity_summary({'a': {'chart': notes}, 'bass': {'chart': chart((0, 50))}},
                              [track('a'), track('bass', instrument='bass')], 'a', chart())
    assert report['sourceActiveSeconds'] == report['unfilledSeconds'] == 0
    assert report['regions'] == []


def test_continuous_hybrid_rest_groups_source_regions_across_donor_rests():
    base = chart((0, 1), (4, 5))
    donor = chart((1.5, 2), (2.5, 3))
    report = activity_summary({'a': {'chart': base}, 'b': {'chart': donor}, 'c': {'chart': donor}},
                              [track('a'), track('b'), track('c')], 'a', base)
    assert len(report['regions']) == 2
    rest, = report['restWindows']
    assert (rest['start'], rest['end'], rest['compatibleActiveSeconds']) == (1, 4, 1)
    assert rest['maxCompatibleSourceAttacks'] == 2  # Doubled sources do not count four attacks.
    assert rest['kind'] == 'new_attacks'


def test_held_tail_is_not_reported_as_a_new_attack_opportunity():
    base = chart((0, 1), (4, 5))
    report = activity_summary({'a': {'chart': base}, 'b': {'chart': chart((0, 3))}},
                              [track('a'), track('b')], 'a', base)
    rest, = report['restWindows']
    assert rest['kind'] == 'no_pitched_attacks'
    assert rest['compatibleActiveSeconds'] == 2
    assert rest['maxCompatibleSourceAttacks'] == 0


def test_zero_duration_played_attack_splits_rest_without_inventing_sustain():
    base = chart((0, 1), (2, 2), (4, 5))
    donor = chart((1.5, 1.75), (2, 2), (2.5, 3))
    report = activity_summary({'a': {'chart': base}, 'b': {'chart': donor}},
                              [track('a'), track('b')], 'a', base)
    assert report['hybridActiveSeconds'] == 2
    assert [(r['start'], r['end'], r['maxCompatibleSourceAttacks']) for r in report['restWindows']] == [
        (1, 2, 1), (2, 4, 1)]


def test_muted_and_ghost_source_attacks_do_not_claim_pitched_fill_opportunities():
    base = chart((0, 1), (4, 5))
    donor = chart((1.5, 2), (2.5, 3))
    donor['notes'][0]['mt'] = True
    donor['notes'][1]['ghost'] = True
    report = activity_summary({'a': {'chart': base}, 'b': {'chart': donor}},
                              [track('a'), track('b')], 'a', base)
    assert report['restWindows'][0]['maxCompatibleSourceAttacks'] == 0


def test_legacy_activity_comparison_can_omit_new_diagnostics():
    report = activity_summary({'a': {'chart': chart((0, 1))}}, [track('a')], 'a', chart(),
                              include_rest_windows=False)
    assert 'restWindows' not in report and 'restWindowScope' not in report


def test_simultaneous_split_notes_count_as_one_source_attack():
    base = chart((0, 1), (4, 5))
    donor = chart((2, 3), (2, 3))
    donor['notes'][1]['s'] = 1
    report = activity_summary({'a': {'chart': base}, 'b': {'chart': donor}},
                              [track('a'), track('b')], 'a', base)
    assert report['restWindows'][0]['maxCompatibleSourceAttacks'] == 1


def test_omitted_zero_duration_attacks_after_last_played_note_remain_visible():
    base = chart((0, 1))
    report = activity_summary({'a': {'chart': base}, 'b': {'chart': chart((2, 2), (3, 3))}},
                              [track('a'), track('b')], 'a', base)
    assert report['compatibleUnfilledSeconds'] == 0  # No invented sustained duration.
    rest, = report['restWindows']
    assert (rest['start'], rest['end'], rest['maxCompatibleSourceAttacks']) == (1, 3, 2)

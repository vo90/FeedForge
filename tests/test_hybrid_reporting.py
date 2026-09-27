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

"""Independent source-atom qualification of a slide across hidden ties.

No producer helper, normalized performance, or stored receipt is consulted.
"""


def admitted(atom, event, pending, start, *, unique, interrupted, competing):
    if (pending is None or event is None or pending[0] is not event
            or pending[1] not in ('shift', 'legato') or not atom.explicit_tie
            or not unique or interrupted or competing or event['end'] != start
            or not event.get('unique_origin') or not event.get('history_unique')
            or not 0 <= atom.fret <= 48 or not 0 <= event['f'] <= 48):
        return False
    if (any(event.get(k) for k in ('linked', 'curve', 'whammy', 'trill', 'scrapes',
                                  'slide_marks', 'incoming_marks', 'targeted_slide',
                                  'contact', 'harmonic_conflict', 'muted_identity', 'bent_identity'))
            or any(value and key not in {'pkd', 'fg', 'ac', 'ghost', 'vb'}
                   for key, value in event['effects'].items())):
        return False

    def plain(a, initial=False):
        return not (a.bends or a.hopo_origin or a.hopo_destination or a.slide and not initial
                    or a.slide_in or a.whammy or a.trill or a.pick_scrape or a.staccato
                    or a.attack_offset or a.pitch_offset or a.wide_vibrato or a.beat_vibrato
                    or a.finger_vibrato and (not initial or not a.explicit_note_vibrato or a.finger_vibrato != 'slight')
                    or any(value and key not in ({'pkd', 'fg', 'ac', 'ghost', 'vb'} if initial and a.explicit_note_vibrato
                                                else {'pkd', 'fg', 'ac', 'ghost'})
                           for key, value in a.effects.items()))

    history = event.get('bend_atoms', ())
    if not history or not plain(atom):
        return False
    first, left, right, visit = history[0]
    marker, marker_visit, attack, marker_start = pending[2]
    if (first.tie or first is not marker or first.explicit_slide != pending[1]
            or first.slide != pending[1] or visit != marker_visit or left != marker_start
            or left != event['start'] or attack != event['start']):
        return False
    for i, (entry, left, right, _visit) in enumerate(history):
        if (not plain(entry, initial=i == 0) or not 0 <= entry.fret <= 48
                or (entry.voice, entry.string) != (atom.voice, atom.string)
                or i and (not entry.explicit_tie or left != history[i - 1][2])):
            return False
    return True


def record(part, atom, event, occurrence, start, end, clock):
    def source_id(location):
        path = location.split('/')
        return 'songsterr:' + ':'.join(path[i] for i in (1, 3, 5, 7, 9))
    marker, left, right, visit = event['bend_atoms'][0]
    return {'trackId': part.id, 'sourceId': source_id(atom.location),
            'originSourceId': source_id(event['locations'][0]), 'location': atom.location,
            'occurrence': occurrence + 1, 'voice': int(atom.voice), 'string': atom.string,
            'attack': float(clock.at(event['start'])), 'start': float(clock.at(start)), 'end': float(clock.at(end)),
            'markerSourceId': source_id(marker.location), 'markerLocation': marker.location,
            'markerOccurrence': visit + 1, 'markerStart': float(clock.at(left)), 'markerEnd': float(clock.at(right)),
            'authored': {'fret': atom.fret, 'tie': True, 'slide': marker.slide},
            'used': {'fret': event['f'], 'pendingSlide': marker.slide},
            'rule': 'plain-tie-keeps-pending-slide-attack-target'}

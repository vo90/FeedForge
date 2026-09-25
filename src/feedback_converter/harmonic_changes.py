"""Version-one delayed AH contact: one attack, one later contact, one sustain."""
from copy import deepcopy
import math
from .harmonic_target import valid_note_target


def validate_changes(note):
    if 'harmonic_changes' not in note:
        return None
    value = note['harmonic_changes']
    finite = lambda x: type(x) in (int, float) and math.isfinite(x)
    if (not isinstance(value, dict) or set(value) != {'version','events'}
            or type(value['version']) is not int or value['version'] != 1
            or not isinstance(value['events'], list) or len(value['events']) != 1
            or any(note.get(k) for k in ('hm','hp','mt','fhm'))
            or any(k in note for k in ('harmonic_target','hn','hps','harmonic_alias'))
            or not finite(note.get('sus')) or note['sus'] <= 0):
        raise ValueError('Invalid delayed harmonic contact')
    event = value['events'][0]
    if (not isinstance(event, dict) or set(event) != {'start','end','target','source_id'}
            or not all(finite(event[k]) for k in ('start','end'))
            or not 0 < event['start'] < event['end']
            or abs(event['end'] - note['sus']) > .0000011
            or not isinstance(event['source_id'], str) or not 0 < len(event['source_id']) <= 512
            or not isinstance(event['target'], dict) or event['target'].get('kind') != 'artificial'
            or not valid_note_target({'f':note.get('f'), 'harmonic_target':event['target']})):
        raise ValueError('Invalid delayed harmonic interval or target')
    return deepcopy(value)


def retime_changes(note, map_time):
    value = validate_changes(note)
    if value is None: return None
    start = map_time(note['t'])
    for event in value['events']:
        for key in ('start','end'):
            event[key] = round(map_time(note['t'] + event[key]) - start, 6)
    validate_changes({**note, 'sus':round(map_time(note['t']+note['sus'])-start,6), 'harmonic_changes':value})
    return value

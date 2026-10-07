"""Playable instrument evidence, separate from the authored playback sound."""
import re

POLICY = 'songsterr-bass-layout-v1'


def program_kind(meta):
    if meta.get('isVocalTrack') is True:
        return ''
    program = meta.get('instrumentId', meta.get('midiProgram'))
    if type(program) is int and program >= 0:
        return 'guitar' if 24 <= program <= 31 else 'bass' if 32 <= program <= 39 else ''
    label = str(meta.get('instrument', meta.get('type', ''))).lower()
    return 'bass' if 'bass' in label else 'guitar' if 'guitar' in label else ''


def bass_layout(meta, part):
    """Qualify four/five strings in fourths, optionally dropping the lowest.

    B-standard through F#-standard four-string register (five-string extends
    down a fourth); uniform transposition -5..+2, lowest string drop 0..2.
    This deliberately does not infer arbitrary alternate or six-string layouts.
    """
    tuning = part.get('tuning') or meta.get('tuning')
    if not isinstance(tuning, list) or len(tuning) not in (4, 5) or any(type(v) is not int for v in tuning):
        return None
    for owner in (meta, part):
        if 'tuning' in owner and owner['tuning'] != tuning:
            return None
        if 'strings' in owner and (type(owner['strings']) is not int or owner['strings'] != len(tuning)):
            return None
    shift = tuning[0] - 43
    if not -5 <= shift <= 2 or tuning[:-1] != [43 + shift - 5*i for i in range(len(tuning)-1)]:
        return None
    drop = 43 + shift - 5*(len(tuning)-1) - tuning[-1]
    return list(tuning) if 0 <= drop <= 2 else None


def resolve(metadata, parts):
    kinds = [program_kind(meta) for meta in metadata]
    resolved, receipts = list(kinds), []
    for i, (meta, part) in enumerate(zip(metadata, parts)):
        program = meta.get('instrumentId', meta.get('midiProgram'))
        if kinds[i] != 'guitar' or type(program) is not int or not 24 <= program <= 31:
            continue
        if part.get('isVocalTrack') is True or ('instrumentId' in part and (type(part['instrumentId']) is not int or part['instrumentId'] != program)):
            continue
        tuning = bass_layout(meta, part)
        if tuning is None:
            continue
        label = str(meta.get('name') or meta.get('title') or part.get('name') or '')
        named = bool(re.search(r'\bbass\b', label, re.I)) and not re.search(r'\b(drums?|clarinet|sax(?:ophone)?|vocals?|cues?)\b', label, re.I)
        peers = [str(other.get('id', j)) for j, (other, body) in enumerate(zip(metadata, parts))
                 if j != i and kinds[j] == 'bass' and bass_layout(other, body) == tuning
                 and type(other.get('instrumentId', other.get('midiProgram'))) is int
                 and 32 <= other.get('instrumentId', other.get('midiProgram')) <= 39
                 and ('instrumentId' not in body or type(body['instrumentId']) is int and body['instrumentId'] == other.get('instrumentId', other.get('midiProgram')))]
        if not named and not peers:
            continue
        resolved[i] = 'bass'
        receipts.append({'trackId': str(meta.get('id', i)), 'trackIndex': i,
                         'instrument': 'bass', 'originalProgram': program,
                         'tuningHighToLow': tuning, 'bassLabel': bool(named), 'bassPeerIds': peers})
    return resolved, {'policy': POLICY, 'tracks': receipts}

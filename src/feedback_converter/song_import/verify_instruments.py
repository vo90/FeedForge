"""Independent source-only check of the bass-layout policy (no parser imports)."""
import re


def resolve(metadata, bodies, kinds):
    def layout(index):
        meta, body = metadata[index], bodies[index]
        pitches = body.get('tuning') or meta.get('tuning')
        if type(pitches) is not list or len(pitches) not in (4, 5):
            return None
        if any(type(p) is not int for p in pitches):
            return None
        for obj in (meta, body):
            if 'tuning' in obj and obj['tuning'] != pitches:
                return None
            count = obj.get('strings', len(pitches))
            if type(count) is not int or count != len(pitches):
                return None
        # Enumerate the permitted layouts instead of using parser arithmetic.
        signatures = [[43 + transpose - 5*s - (drop if s == count-1 else 0)
                       for s in range(count)] for count in (4, 5)
                      for transpose in range(-5, 3) for drop in (0, 1, 2)]
        return pitches if pitches in signatures else None

    def program(index):
        meta, body = metadata[index], bodies[index]
        number = meta.get('instrumentId', meta.get('midiProgram'))
        if type(number) is not int:
            return None
        if 'instrumentId' in body and (type(body['instrumentId']) is not int or body['instrumentId'] != number):
            return None
        return number

    resolved, rows = list(kinds), []
    for i, (meta, body) in enumerate(zip(metadata, bodies)):
        p = program(i)
        tuning = layout(i)
        if kinds[i] != 'guitar' or p not in range(24, 32) or tuning is None or body.get('isVocalTrack') is True:
            continue
        words = re.findall(r'\w+', str(meta.get('name') or meta.get('title') or body.get('name') or '').lower())
        named = 'bass' in words and not set(words).intersection({'drum', 'drums', 'clarinet', 'sax', 'saxophone', 'vocal', 'vocals', 'cue', 'cues'})
        peers = [str(metadata[j].get('id', j)) for j in range(len(metadata))
                 if j != i and kinds[j] == 'bass' and program(j) in range(32, 40) and layout(j) == tuning]
        if named or peers:
            resolved[i] = 'bass'
            rows.append({'trackId': str(meta.get('id', i)), 'trackIndex': i, 'instrument': 'bass',
                         'originalProgram': p, 'tuningHighToLow': list(tuning),
                         'bassLabel': named, 'bassPeerIds': peers})
    return resolved, {'policy': 'songsterr-bass-layout-v1', 'tracks': rows}

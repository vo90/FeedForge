"""Independent source-controller reconstruction and archive validation."""
import math


def intervals(event):
    entries = event['bend_atoms']
    updates = {}
    for index, (atom, begin, end, _) in enumerate(entries):
        if atom.finger_vibrato is None:
            continue
        stop = event['end'] if index == 0 else end
        updates.setdefault(begin, []).append((1, index, atom.finger_vibrato))
        updates.setdefault(stop, []).append((0, index, None))
    if not updates:
        return None
    points = sorted(updates)
    result, intensity = [], None
    for i, begin in enumerate(points):
        intensity = sorted(updates[begin])[-1][2]
        if intensity and i+1 < len(points):
            end = points[i+1]
            if result and result[-1][1] == begin and result[-1][2] == intensity:
                result[-1] = (result[-1][0], end, intensity)
            else:
                result.append((begin, end, intensity))
    return result


def compare(wanted, actual, sustain, check, location):
    code = 'vibrato_marks'
    if wanted is None and actual is None:
        return
    if not isinstance(wanted, list) or not isinstance(actual, list):
        check.fail(code, location, 'Timed vibrato presence differs from the source.')
        return
    if type(sustain) not in (int, float) or not math.isfinite(sustain) or sustain < 0:
        check.fail(code, location, 'Invalid vibrato owning duration.')
        return
    check.equal(code, location+'/count', len(wanted), len(actual))
    previous = 0
    for i, row in enumerate(actual):
        loc = location+'/'+str(i)
        if (not isinstance(row, dict) or set(row) != {'start', 'end', 'intensity'}
                or row['intensity'] not in ('slight', 'wide')
                or any(type(row[k]) not in (int, float) or not math.isfinite(row[k]) for k in ('start', 'end'))
                or not previous <= row['start'] < row['end'] <= sustain + 0.0000011):
            check.fail(code, loc, 'Invalid timed vibrato interval.')
            continue
        previous = row['end']
        if i < len(wanted):
            check.equal(code, loc+'/intensity', wanted[i]['intensity'], row['intensity'])
            for key in ('start', 'end'):
                check.near(code, loc+'/'+key, wanted[i][key], row[key])

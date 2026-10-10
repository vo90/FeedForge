"""Additional ending evidence for bass attacks below E1; never retime notes.

The legacy pitch bank and its decisions remain intact. A separate bank covers
A0 upwards, at the already selected reference, only for inconclusive windows.
No sparse-context or phrase inference is granted by this fallback.
"""
from copy import deepcopy

from . import local_sync as ls, recording_sync as rs

VERSION = 'low-bass-ending-evidence-v1'
MIDI_MIN = 21
LEGACY_MIN = 28


def supplement(tracks, path, duration, baseline, *, feature_session=None):
    if baseline['status'] == 'supported':
        return baseline
    bass = [t for t in tracks if t['instrument'] == 'bass' and any(
        n['midi'] is not None and MIDI_MIN <= n['midi'] < LEGACY_MIN
        for n in t['events'])]
    if not bass:
        return baseline
    candidates = []
    for i, window in enumerate(baseline['windows']):
        if (window['status'] != 'inconclusive'
                or window.get('jointEvidence', {}).get('status') == 'suspected_mismatch'):
            continue
        eligible = []
        for track in bass:
            groups = ls.groups(track, window['start'], window['end']-.2, midi_min=MIDI_MIN)
            low_times = {time for time, vector in groups if vector[:LEGACY_MIN-MIDI_MIN].any()}
            if len(low_times) >= rs.MIN_GROUPS:
                # Score the eligible low attacks themselves. Higher notes in
                # another passage cannot earn support for an inaudible bass.
                selected = {**track, 'events': [n for n in track['events']
                    if round(n['t'], 6) in low_times]}
                eligible.append((selected, len(low_times)))
        if eligible:
            candidates.append((i, eligible))
    if not candidates:
        return baseline
    reference = baseline.get('referenceEvidence', {}).get('referenceHz', 440.0)
    if hasattr(path, 'seek'):
        path.seek(0)
    pitch, flux, _ = (rs.features(path, reference_hz=reference, midi_min=MIDI_MIN)
        if feature_session is None else feature_session.features(reference_hz=reference, midi_min=MIDI_MIN))
    report = deepcopy(baseline)
    evidence = []
    for index, eligible in candidates:
        window = report['windows'][index]
        measured = ls.assess_features([t for t, _ in eligible], pitch, flux, duration,
            windows=[(window['start'], window['end'])], unique_clock=True,
            corroborate_mismatch=True, midi_min=MIDI_MIN)['windows'][0]
        counts = {t['id']: count for t, count in eligible}
        for part in measured['parts']:
            part['lowPitchGroups'] = counts[part['trackId']]
        evidence.append(measured)
        if measured['status'] == 'supported':
            window.update(status='supported', method='low-bass-joint-pitch-attacks')
    report['supportedWindows'] = sum(w['status'] == 'supported' for w in report['windows'])
    if len(report['windows']) >= 3 and report['supportedWindows'] == len(report['windows']):
        report['status'] = 'supported'
    report['lowBassEvidence'] = {'version': VERSION, 'midiMin': MIDI_MIN, 'midiMax': 96,
        'referenceHz': reference, 'windows': evidence,
        'everyNoteVerified': False, 'scope': 'shared_recording_timing'}
    return report

"""Account for attacks consumed by explicit Songsterr strum/grace timing."""
from copy import deepcopy
import hashlib


def omit_consumed(score, track, rendered, articulations, groups, origins, links, at):
    if score.source.get('format') != 'songsterr':
        return []
    written = {b.source_id: b for bar in track.written_bars for v in bar for b in v.beats}
    grace_neighbors = set()
    voices = {}
    for bar in track.written_bars:
        for voice in bar:
            voices.setdefault(voice.source_index, []).extend(voice.beats)
    for beats in voices.values():
        for i, beat in enumerate(beats):
            if beat.grace:
                grace_neighbors.update(b.source_id for b in beats[max(0, i-1):i+2])
    linked = {id(n) for pair in links for n in pair}
    omitted, rows = set(), []
    for output in rendered:
        a = articulations[id(output)]
        if output['sus'] > 0 or a['segments'] != 1:
            continue
        note, start, end, occurrence = a['bend_segments'][0]
        beat = written.get(note.beat_id)
        if (beat is None or note.beat_id not in grace_neighbors or beat.written_duration is None
                or not 0 < note.duration < beat.written_duration
                or note.attack_offset < note.duration
                or (occurrence, note.beat_id) not in origins
                or id(output) in linked or a.get('linked')
                or note.tie or note.staccato or note.bends or note.slide or note.slide_in
                or note.whammy or note.trill or note.pick_scrape or note.hopo
                or note.effects.get('__hopo_origin')):
            continue
        _, pi, mi, vi, bi, ni = note.source_id.split(':')
        rows.append({'trackId': track.id, 'sourceId': note.source_id,
                     'location': f'parts/{pi}/measures/{mi}/voices/{vi}/beats/{bi}/notes/{ni}',
                     'occurrence': occurrence + 1, 'start': at(start),
                     'attack': output['t'], 'end': at(end), 'string': note.string, 'fret': note.fret,
                     'authored': {'writtenQuarters': str(beat.written_duration),
                                  'soundingQuarters': str(note.duration),
                                  'attackOffsetQuarters': str(note.attack_offset)},
                     'used': {'rule': 'consumed-strum-grace-no-attack'}})
        omitted.add(id(output))
    rendered[:] = [n for n in rendered if id(n) not in omitted]
    for group in groups.values():
        group[:] = [n for n in group if id(n) not in omitted]
    return rows


def report_findings(performance, report):
    from .compatibility import add_finding
    for row in performance.get('consumedStrumEvidence', []):
        add_finding(report, feature='note.consumed_strum_grace', category='source_interpretation',
                    impact='gameplay_omission',
                    message='Explicit strum timing consumes this grace-shortened note. Like source playback, it has no playable attack. The complete source is retained; this arrangement uses playable tablature instead of written notation.',
                    location=row['location'] + f"@visit{row['occurrence']}",
                    value={k: row[k] for k in ('authored', 'used')}, trackId=row['trackId'])


def archive_evidence(performance, source_path):
    return {'version': 1, 'policy': 'consumed-strum-grace-v1',
            'sourceSha256': hashlib.sha256(source_path.read_bytes()).hexdigest(),
            'timeDomain': 'score_seconds', 'omissions': deepcopy(performance['consumedStrumEvidence'])}

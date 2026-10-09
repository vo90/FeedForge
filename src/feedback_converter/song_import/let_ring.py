"""Source let-ring spans, separate from attack/scored and written durations.

Runs close at the last nonempty beat (including an unmarked closing beat).
They cross bars, but physical string reattacks bound each resulting trail.
No synthesis envelope, extra attack or linked-note instruction is generated.
"""
from bisect import bisect_right
from collections import defaultdict

POLICY = 'songsterr-let-ring-v1'
FIELD = 'let_ring_sus'


def clip_event(event, end):
    for n in event.get('notes', [event]):
        if FIELD in n:
            value = round(min(n[FIELD], end-n.get('t', event['t'])), 6)
            if value > n.get('sus', 0)+0.0000011:
                n[FIELD] = value
            else:
                n.pop(FIELD)


def clip_chart(chart):
    notes = [(n, n['t']) for n in chart['notes']]
    notes += [(n, n.get('t', c['t'])) for c in chart['chords'] for n in c['notes']]
    attacks = defaultdict(set)
    for n, t in notes:
        attacks[n['s']].add(t)
    attacks = {s: sorted(times) for s, times in attacks.items()}
    for n, t in notes:
        if FIELD in n:
            times = attacks[n['s']]
            pos = bisect_right(times, t+1e-8)
            if pos < len(times):
                end = times[pos]
                value = round(min(n[FIELD], end-t), 6)
                if value > n.get('sus', 0)+0.0000011:
                    n[FIELD] = value
                else:
                    n.pop(FIELD)


def attach(score, performance):
    if score.source.get('format') != 'songsterr':
        return performance
    timeline = performance['scoreTimeline']
    tempos = timeline['tempoPoints']
    positions = [p['quarter'] for p in tempos]
    times = [p['time'] for p in tempos]

    def at(q):
        p = tempos[max(0, bisect_right(positions, float(q))-1)]
        return p['time'] + (float(q)-p['quarter'])*60/p['bpm']

    def quarter(seconds):
        p = tempos[max(0, bisect_right(times, seconds)-1)]
        return p['quarter'] + (seconds-p['time'])*p['bpm']/60

    spans = defaultdict(list)
    staccato = set()
    for track in score.tracks:
        waiting, last = defaultdict(list), {}

        def flush(voice):
            for sid, start in waiting.pop(voice, []):
                spans[sid].append((start, last[voice]))

        for visit in timeline['measures']:
            bar = visit['writtenIndex']
            present = set()
            for vi, voice in enumerate(track.written_bars[bar]):
                key = voice.source_index if voice.source_index is not None else vi
                present.add(key)
                if not voice.beats:
                    flush(key)
                for beat in voice.beats:
                    nonempty = not beat.rest and bool(beat.notes)
                    if nonempty:
                        last[key] = at(visit['quarter'] + beat.position + beat.duration)
                    if not nonempty or not beat.annotations.get('lr'):
                        flush(key)
                        continue
                    for note in beat.notes:
                        if note.staccato:
                            staccato.add(note.source_id)
                        # Native pitch gestures have their own release policy.
                        # Dead notes cannot acquire a pitched ringing tail.
                        if (note.bends or note.slide or note.slide_in or note.effects.get('__hopo_origin')
                                or note.effects.get('mt') or note.trill):
                            continue
                        waiting[key].append((note.source_id, at(visit['quarter'] + note.position + note.attack_offset)))
            for key in set(waiting)-present:
                flush(key)
        for key in list(waiting):
            flush(key)
    for rows in spans.values():
        rows.sort()
    for track in performance['tracks']:
        notes = [(n, n['t']) for n in track['notes']]
        notes += [(n, n.get('t', c['t'])) for c in track['chords'] for n in c['notes']]
        attacks = defaultdict(set)
        for note, start in notes:
            attacks[note['s']].add(start)
        attacks = {s: sorted(times) for s, times in attacks.items()}
        for note, start in notes:
            if note.get('mt') or note.get('tr'):
                continue
            end = start + note['sus']
            target = end
            for sid in note.get('source_ids', []):
                rows = spans.get(sid, ())
                lo = bisect_right(rows, (start-1e-8, float('inf')))
                for when, until in rows[lo:]:
                    if when > end+1e-8:
                        break
                    target = max(target, until)
            following = attacks[note['s']]
            nxt = bisect_right(following, start+1e-8)
            if nxt < len(following):
                target = min(target, following[nxt])
            if target > end+1e-8 and note.get('source_ids', [None])[0] in staccato:
                # Native staccato follows let-ring and same-string clipping.
                # Halve the musical span, before mapping into recording time.
                target = at((quarter(start)+quarter(target))/2)
            if target > end+1e-8:
                note[FIELD] = target-start
    return performance

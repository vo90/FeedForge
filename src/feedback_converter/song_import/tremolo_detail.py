"""Attach source-owned picking rates without changing notes or their durations."""
from bisect import bisect_right
from collections import defaultdict
from fractions import Fraction

QUALIFIED_MESSAGE = ('Tremolo picking rates and active spans are preserved for the updated highway and tab view. '
                     'Trail spacing indicates speed within readable limits; individual picks are not scored separately.')


def report_findings(performance, report):
    """Qualify existing source findings only where the complete display is known."""
    states = defaultdict(set)
    for track in performance['tracks']:
        notes = track['notes'] + [n for c in track['chords'] for n in c['notes']]
        for note in notes:
            if 'tremolo_marks' not in note:continue
            problems = set()
            for mark in note['tremolo_marks']:
                if mark['mode'] != 'specified':problems.add(mark['mode'])
                elif mark['rate'] not in ([1,8],[1,16],[1,32]):problems.add('notation')
            for sid in note.get('source_ids',[]):
                pieces = sid.split(':')
                if len(pieces)!=6 or pieces[0]!='songsterr':continue
                _,p,m,v,b,n=pieces
                base=f'parts/{p}/measures/{m}/voices/{v}/beats/{b}'
                for location in (base+'/tremolo',base+f'/notes/{n}/tremolo'):
                    states[location].update(problems or {'qualified'})
    for finding in report.get('findings',[]):
        if finding['feature'] not in ('beat.tremolo','note.tremolo'):continue
        problems=states.get(finding['location'])
        if not problems:continue
        if problems=={'qualified'}:finding['message']=QUALIFIED_MESSAGE
        elif 'conflicting' in problems:
            finding['message']='Overlapping tremolo instructions disagree. Those spans use a generic picking cue; exact source instructions are retained.'
        elif 'unspecified' in problems:
            finding['message']='The source marks tremolo without an explicit picking rate. Those spans use a generic picking cue.'
        else:
            finding['message']='The picking rate is preserved and controls highway trail spacing. This subdivision cannot be engraved by the current tab view, which shows a generic tremolo instruction.'


def attach(score, performance):
    if score.source.get('format') != 'songsterr': return performance
    timeline = performance['scoreTimeline']
    tempos = timeline['tempoPoints']
    positions = [p['quarter'] for p in tempos]
    seconds = [p['time'] for p in tempos]
    def at(q):
        p = tempos[max(0, bisect_right(positions, float(q))-1)]
        return p['time'] + (float(q)-p['quarter'])*60/p['bpm']
    def quarter(t):
        p = tempos[max(0, bisect_right(seconds, t)-1)]
        return p['quarter'] + (t-p['time'])*p['bpm']/60
    candidates = defaultdict(list)
    for track in score.tracks:
        for visit in timeline['measures']:
            for voice in track.written_bars[visit['writtenIndex']]:
                for beat in voice.beats:
                    for atom in beat.notes:
                        if atom.tremolo_rate is None: continue
                        q = float(visit['quarter'] + atom.position + atom.attack_offset)
                        rate = None if atom.tremolo_rate is True else Fraction(*atom.tremolo_rate)
                        candidates[atom.source_id].append((at(q), q, rate))
    for track in performance['tracks']:
        notes = [(n, n['t']) for n in track['notes']]
        notes += [(n, n.get('t', c['t'])) for c in track['chords'] for n in c['notes']]
        for note, start in notes:
            if not note.get('tr'): continue
            end = start + note['sus']
            owners = sorted(set(row for sid in note.get('source_ids', []) for row in candidates.get(sid, ())
                                if start-1.1e-6 <= row[0] < end-1.1e-6), key=lambda r:r[0])
            edges = sorted({start, end, *[start if abs(start-r[0])<=1.1e-6 else r[0] for r in owners],
                            *[t for t in seconds if start+1.1e-6<t<end-1.1e-6]})
            marks = []
            for a,b in zip(edges, edges[1:]):
                active = [r for r in owners if r[0] <= a+1.1e-6]
                if not active: continue
                rates = {r[2] for r in active}
                rate = next(iter(rates)) if len(rates)==1 else None
                mode = 'specified' if rate else 'unspecified' if rates=={None} else 'conflicting'
                if rate:
                    phases = [0 if abs(a-r[0])<=1.1e-6 else ((quarter(a)-r[1])/(4*float(rate))) % 1 for r in active]
                    if any(min(abs(p-phases[0]),1-abs(p-phases[0])) > 1e-5 for p in phases):
                        mode,rate = 'conflicting',None
                p = tempos[max(0,bisect_right(seconds,a+1e-8)-1)]
                marks.append({'start':round(a-start,6),'end':round(b-start,6),
                    'rate':[rate.numerator,rate.denominator] if rate else None,
                    'period':4*float(rate)*60/p['bpm'] if rate else None,
                    'phase':round(phases[0],9)%1 if rate else 0,'mode':mode})
            note['tremolo_marks'] = marks
    return performance


def retime(note, alignment, original, mapped_start, sustain):
    from .alignment import map_time
    from ..tremolo import validate
    output = []
    anchors = [a['score'] for a in alignment.get('anchors', [])] if alignment.get('mapping') == 'piecewise-linear' else []
    for m in validate(note['tremolo_marks'], note['sus']):
        a,b = original+m['start'], original+m['end']
        edges = sorted({a,b,*[t for t in anchors if a<t<b]})
        for left,right in zip(edges,edges[1:]):
            x,y = map_time(alignment,left),map_time(alignment,right)
            start,end = round(max(0,x-mapped_start),6),round(min(sustain,y-mapped_start),6)
            if start>=end:continue
            period = m['period']*(y-x)/(right-left) if m['period'] else None
            phase = (m['phase']+(left-a)/m['period'])%1 if m['period'] else 0
            output.append({**m,'start':start,'end':end,'period':period,'phase':round(phase,9)%1})
    return validate(output,sustain)

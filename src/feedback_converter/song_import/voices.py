"""Playable voice projection; original written voices remain source evidence."""
from collections import defaultdict
from copy import deepcopy
import hashlib
from .fingering import template_fingers

POLICY = 'source-voices-v1'
SIDECARS = ('undefinedSlideEvidence', 'harmonicTieEvidence', 'tiedMuteEvidence', 'mutedTieIdentityEvidence', 'staccatoBendEvidence', 'mutedSlideEvidence', 'consumedStrumEvidence', 'scrapeEntryEvidence', 'trillEvidence', 'strumEvidence')


def derived_id(track_id, voice):
    return 'voice-' + hashlib.sha256(track_id.encode()).hexdigest()[:20] + '-' + str(voice + 1)


def flat(track):
    return sorted([*deepcopy(track['notes']), *[{**deepcopy(n), 't': c['t']}
        for c in track['chords'] for n in c['notes']]], key=lambda n: (n['t'], n['s']))


def voice(note):
    return int(note['source_ids'][0].split(':')[3])


def semantic(note, ignored=()):
    def norm(v):
        if isinstance(v, float): return round(v, 6)
        if isinstance(v, dict): return {k:norm(x) for k,x in v.items()}
        if isinstance(v, list): return [norm(x) for x in v]
        return v
    return norm({k:v for k,v in note.items() if k not in {'source_ids', *ignored}})


def decide(notes):
    groups = defaultdict(list)
    for n in notes: groups[(n['t'],n['s'])].append(n)
    merges = []
    for (time,string), group in groups.items():
        if len(group) < 2: continue
        if len({voice(n) for n in group}) != len(group): return None
        first = min(group, key=voice)
        rule = 'equivalent'
        if any(semantic(n) != semantic(first) for n in group):
            longest = max(n['sus'] for n in group)
            # No curve, linked gesture, scrape, harmonic change or choke is
            # inferred across the newly shared sustain.
            allowed = {'t','s','f','sus','lr','source_ids'}
            if (any(set(n)-allowed for n in group)
                    or any(semantic(n,('sus','lr')) != semantic(first,('sus','lr')) for n in group)
                    or any(n['sus'] < longest-1e-6 and not n.get('lr') for n in group)
                    or any(n['s']==string and time < n['t'] < time+longest-1e-6 for n in notes)):
                return None
            rule = 'let-ring'
        merged = deepcopy(first)
        merged['source_ids'] = sorted({sid for n in group for sid in n['source_ids']})
        if rule == 'let-ring':
            merged['sus'] = max(n['sus'] for n in group)
            merged['lr'] = True
        merges.append((group,merged,rule))
    # Different-onset conflicting pitches on one physical string also cannot
    # be made playable by silently shortening the first voice's sustain.
    strings=defaultdict(list)
    for n in notes: strings[n['s']].append(n)
    for rows in strings.values():
        active=[]
        for n in sorted(rows,key=lambda n:n['t']):
            active=[p for p in active if p['t']+p['sus']>n['t']+1e-6]
            if any(voice(p)!=voice(n) and p['t']!=n['t'] and p['f']!=n['f'] for p in active):
                return None
            active.append(n)
    return merges


def combine(track, notes, merges):
    affected={m['t'] for _,m,_ in merges}
    replacements={(m['t'],m['s']):m for _,m,_ in merges}
    kept=[];seen=set()
    for n in notes:
        key=(n['t'],n['s'])
        if key in replacements:
            if key in seen: continue
            n=replacements[key];seen.add(key)
        kept.append(n)
    track['notes']=[n for n in track['notes'] if n['t'] not in affected]
    track['chords']=[c for c in track['chords'] if c['t'] not in affected]
    at=defaultdict(list)
    for n in kept:
        if n['t'] in affected: at[n['t']].append(n)
    for time,children in at.items():
        if len(children)==1:track['notes'].append(children[0]);continue
        frets=[-1]*len(track['tuning'])
        for n in children:frets[n['s']]=n['f']
        ident=len(track['templates'])
        track['templates'].append({'name':'','frets':frets,'fingers':template_fingers(children,len(frets))})
        track['chords'].append({'t':time,'id':ident,'notes':[{k:v for k,v in n.items() if k!='t'} for n in children]})
    track['notes'].sort(key=lambda n:(n['t'],n['s']))
    track['chords'].sort(key=lambda n:n['t'])


def project(score, performance, render_one):
    receipt={'version':1,'policy':POLICY,'timeDomain':'score_seconds','tracks':[]}
    outputs=[];source_by_id={t.id:t for t in score.tracks};reserved=set(source_by_id)
    for track in performance['tracks']:
        original=source_by_id[track['id']]
        voices=sorted({int(n.voice_id) for bar in original.bars for n in bar})
        if len(voices)<2:outputs.append(track);continue
        notes=flat(track);merges=decide(notes)
        if merges == []:outputs.append(track);continue
        entry={'sourceTrackId':track['id'],'mode':'split' if merges is None else 'combined',
               'arrangements':[],'combinedAttacks':[]}
        if merges is not None:
            combine(track,notes,merges)
            entry['arrangements']=[{'id':track['id'],'name':track['name'],'voices':voices}]
            for group,n,rule in merges:
                entry['combinedAttacks'].append({'sourceIds':n['source_ids'],'time':round(n['t'],6),
                    'duration':round(n['sus'],6),'string':n['s'],'fret':n['f'],'rule':rule})
            entry['combinedAttacks'].sort(key=lambda n:(n['time'],n['string']))
            outputs.append(track)
        else:
            for key in SIDECARS:
                if key in performance:
                    performance[key]=[r for r in performance[key] if r['trackId']!=track['id']]
            for vi in voices:
                selected=deepcopy(original)
                selected.id=derived_id(track['id'],vi)
                if selected.id in reserved:raise ValueError('Derived voice arrangement identity collides with a source track.')
                reserved.add(selected.id)
                selected.name=f"{original.name} — Voice {vi+1}"
                selected.bars=[[n for n in b if int(n.voice_id)==vi] for b in selected.bars]
                selected.written_bars=[[v for v in b if v.source_index==vi] for b in selected.written_bars]
                one=deepcopy(score);one.tracks=[selected]
                rendered=render_one(one)
                outputs.extend(rendered['tracks'])
                for key in SIDECARS:
                    if rendered.get(key):performance.setdefault(key,[]).extend(rendered[key])
                entry['arrangements'].append({'id':selected.id,'name':selected.name,'voices':[vi]})
        receipt['tracks'].append(entry)
    performance['tracks']=outputs
    performance['source']['playableTrackCount']=len(outputs)
    if receipt['tracks']:
        performance['voiceProjection']=receipt
        for row in receipt['tracks']:
            performance['warnings'].append(
                f"Source arrangement {row['sourceTrackId']}: separated into {len(row['arrangements'])} selectable voices."
                if row['mode']=='split' else
                f"Source arrangement {row['sourceTrackId']}: {len(row['combinedAttacks'])} equivalent or compatible let-ring attacks combined; original voices retained.")
        for key in SIDECARS:
            if key in performance:performance[key].sort(key=lambda r:(r['trackId'],r.get('occurrence',0),r.get('start',r.get('time',0)),r.get('sourceId','')))
    return performance


def archive_receipt(performance, source_path):
    return {**deepcopy(performance['voiceProjection']),
            'sourceSha256':hashlib.sha256(source_path.read_bytes()).hexdigest()}

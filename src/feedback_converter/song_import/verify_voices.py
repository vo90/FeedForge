"""Independent voice projection oracle over independently reconstructed notes.

Does not import the exporter, its score model or its reconciliation functions.
"""
from copy import deepcopy
import hashlib
from collections import defaultdict

EVIDENCE={'harmonic_ties','tied_mutes','trills','strums'}


def source_ids(item):
    result=[]
    for loc in item['locations']:
        p=loc.split('/')
        result.append('songsterr:'+':'.join(p[i] for i in (1,3,5,7,9)))
    return sorted(set(result))


def voice(item):
    return int(item['locations'][0].split('/')[5])


def same(a,b,omit=()):
    def value(x):
        if isinstance(x,float):return round(x,6)
        if isinstance(x,list):return [value(v) for v in x]
        if isinstance(x,dict):return {k:value(v) for k,v in x.items()}
        return x
    return value({k:v for k,v in a.items() if k not in omit})==value({k:v for k,v in b.items() if k not in omit})


def classify(items):
    positions=defaultdict(list)
    for i,item in enumerate(items):positions[(item['note']['t'],item['note']['s'])].append(i)
    combined=[]
    for (time,string),indices in positions.items():
        if len(indices)==1:continue
        if len({voice(items[i]) for i in indices})!=len(indices):return None
        base=min(indices,key=lambda i:voice(items[i]));notes=[items[i]['note'] for i in indices]
        rule='equivalent';duration=max(n['sus'] for n in notes)
        if not all(same(n,items[base]['note']) for n in notes):
            if (not all(set(n)<={'t','s','f','sus','lr'} for n in notes)
                    or not all(same(n,items[base]['note'],('sus','lr')) for n in notes)
                    or not all(n.get('lr') or n['sus']>=duration-1e-6 for n in notes)
                    or any(n['note']['s']==string and time<n['note']['t']<time+duration-1e-6 for n in items)):
                return None
            rule='let-ring'
        combined.append((indices,base,rule))
    active=defaultdict(list)
    for item in sorted(items,key=lambda n:n['note']['t']):
        note=item['note'];previous=active[note['s']]
        previous[:]=[p for p in previous if p['note']['t']+p['note']['sus']>note['t']+1e-6]
        for p in previous:
            if (voice(p)!=voice(item) and p['note']['t']!=note['t'] and p['note']['f']!=note['f']):
                return None
        previous.append(item)
    return combined


def project(source, alignment, mapped, reconstruct):
    if not any(len({int(n.voice) for bar in p.bars for n in bar}) > 1 for p in source.parts):
        return mapped
    # All decisions use the score clock, never quantized/retimed recording data.
    reference=reconstruct(source,{'offset':0,'scale':1})
    receipt={'version':1,'policy':'source-voices-v1','timeDomain':'score_seconds','tracks':[]}
    parts=[];reserved={p.id for p in source.parts}
    for ref,target in zip(reference['parts'],mapped['parts']):
        src=ref['source'];voices=sorted({int(n.voice) for bar in src.bars for n in bar})
        if len(voices)<2:parts.append(target);continue
        groups=classify(ref['notes'])
        if groups==[]:parts.append(target);continue
        entry={'sourceTrackId':src.id,'mode':'split' if groups is None else 'combined',
               'arrangements':[],'combinedAttacks':[]}
        if groups is None:
            for key in EVIDENCE:mapped[key]=[r for r in mapped[key] if r['trackId']!=src.id]
            for vi in voices:
                selected=deepcopy(src)
                selected.id='voice-'+hashlib.sha256(src.id.encode()).hexdigest()[:20]+'-'+str(vi+1)
                if selected.id in reserved:raise ValueError('Derived voice identity collision')
                reserved.add(selected.id)
                selected.name=f'{src.name} — Voice {vi+1}'
                selected.bars=[[n for n in b if int(n.voice)==vi] for b in selected.bars]
                selected.beats=[[b for b in bar if int(b['voice'])==vi] for bar in selected.beats]
                selected.notation_unavailable=[p for p in selected.notation_unavailable if int(p.split('/')[5])==vi]
                selected.unpitched_mutes=[p for p in selected.unpitched_mutes if int(p.split('/')[5])==vi]
                single=deepcopy(source);single.parts=[selected]
                converted=reconstruct(single,alignment)
                parts.extend(converted['parts'])
                for key in EVIDENCE:mapped[key].extend(converted[key])
                entry['arrangements'].append({'id':selected.id,'name':selected.name,'voices':[vi]})
        else:
            entry['arrangements']=[{'id':src.id,'name':src.name,'voices':voices}]
            discard=set();affected=set()
            for indices,base,rule in groups:
                a=ref['notes'][base]['note'];b=target['notes'][base]['note']
                locations=sorted({l for i in indices for l in ref['notes'][i]['locations']})
                ids=sorted({s for i in indices for s in source_ids(ref['notes'][i])})
                duration=max(ref['notes'][i]['note']['sus'] for i in indices)
                if rule=='let-ring':
                    b['sus']=max(target['notes'][i]['note']['sus'] for i in indices);b['lr']=True
                target['notes'][base]['locations']=locations
                discard.update(i for i in indices if i!=base);affected.update(target['notes'][i]['note']['t'] for i in indices)
                entry['combinedAttacks'].append({'sourceIds':ids,'time':round(a['t'],6),'duration':round(duration,6),
                    'string':a['s'],'fret':a['f'],'rule':rule})
            target['notes']=[n for i,n in enumerate(target['notes']) if i not in discard]
            for n in target['notes']:
                if n['note']['t'] in affected:
                    n['beat']='voice-combined:'+str(n['note']['t']);n.pop('trill',None)
            entry['combinedAttacks'].sort(key=lambda n:(n['time'],n['string']))
            parts.append(target)
        receipt['tracks'].append(entry)
    mapped['parts']=parts
    if receipt['tracks']:mapped['voice_projection']=receipt
    if receipt['tracks']:
        for key in EVIDENCE:
            mapped[key].sort(key=lambda r:(r['trackId'],r.get('occurrence',0),r.get('start',r.get('time',0)),r.get('sourceId','')))
    return mapped


def verify(receipt, actual, source_hash, check):
    wanted={**receipt,'sourceSha256':source_hash}
    def compare(a,b,path):
        if isinstance(a,dict):
            if not isinstance(b,dict) or set(a)!=set(b):
                check.fail('voice_projection',path,'Voice projection fields differ from independently derived source instructions.');return
            for k,v in a.items():compare(v,b[k],path+'/'+k)
        elif isinstance(a,list):
            if not isinstance(b,list) or len(a)!=len(b):
                check.fail('voice_projection',path,'Voice projection count differs from source.');return
            for i,(x,y) in enumerate(zip(a,b)):compare(x,y,path+'/'+str(i))
        elif path.endswith(('/time','/duration')):check.near('voice_projection',path,a,b)
        else:check.equal('voice_projection',path,a,b)
    compare(wanted,actual,'import/voices')

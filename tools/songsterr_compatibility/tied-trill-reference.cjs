'use strict';
// Explicit local-reference maintenance. No player code or real tabs are shipped.
const fs=require('node:fs');
const ref=require('./reference.cjs');
const [worker,output]=process.argv.slice(2);
if(!worker||!output||fs.existsSync(output))throw Error('Supply reviewed worker and NEW output');
const code=ref.prepare(worker),cases=[];
for(const initial of [null,38,60,120])for(const continuation of [31,38,60,120])
for(const first of [8,4])for(const second of [32,8])for(const auxiliary of [15,10])for(const tail of [false,true]) {
 const note=(speed,aux=15)=>({fret:12,string:0,...(speed===null?{}:{trill:{auxiliaryFret:aux,speed}})});
 const beats=[{duration:[1,first],type:first,notes:[note(initial)]},
   {duration:[1,second],type:second,notes:[{...note(continuation,auxiliary),tie:true}]}];
 if(tail)beats.push({duration:[1,8],type:8,notes:[{...note(null),tie:true}]});
 const part={instrumentId:25,strings:6,frets:24,tuning:[64,59,55,50,45,40],
   automations:{tempo:[{measure:0,position:0,bpm:120,type:4}]},
   measures:[{signature:[4,4],voices:[{beats}]}]};
 const source={format:'songsterr',songId:1,revisionId:1,title:'Tied trill fixture',artist:'Synthetic',
   tracks:[{id:'0',name:'Synthetic guitar',instrumentId:25,tuning:part.tuning}],parts:[part]};
 const native=ref.runPart(code,part,part.measures,{eventDetails:true});
 const scheduled=native.generatedEventTrace.scheduled.filter(s=>['NOTE_ON','NOTE_OFF'].includes(s.event.name))
   .sort((a,b)=>a.event.time-b.event.time||a.priority-b.priority||a.ordinal-b.ordinal);
 const active=new Set(),attacks=[],issues=[];
 for(let i=0;i<scheduled.length;) {
   const time=scheduled[i].event.time,group=[];
   while(i<scheduled.length&&scheduled[i].event.time===time)group.push(scheduled[i++].event);
   for(const e of group) {
     if(e.name==='NOTE_OFF')active.delete(e.pitch);
     else {if(active.has(e.pitch))issues.push('same-pitch reattack');active.add(e.pitch);attacks.push([e.time,e.pitch-64]);}
   }
   if(active.size>1)issues.push('overlapping pitches');
   if(!active.size&&i<scheduled.length&&scheduled[i].event.time>time+1)issues.push('gap inside tie');
 }
 if(active.size)issues.push('unterminated pitch');
 const end=(4/first+4/second+(tail?0.5:0))*native.tpqn;
 if(attacks.some((a,i)=>i&&a[1]===attacks[i-1][1]))issues.push('same-fret legato');
 if(attacks.some((a,i)=>i&&a[0]===attacks[i-1][0]))issues.push('simultaneous attacks');
 cases.push({id:`${initial}/${continuation}/${first}/${second}/${auxiliary}/${tail}`,source,
   expected:{tpqn:native.tpqn,endTick:end,attacks,issues:[...new Set(issues)]}});
}
const scope='Native NOTE_ON/OFF evidence. Ambiguous pitch overlap, reattack or silence is not translated into gameplay.';
fs.writeFileSync(output,'{\n  "referenceSha256": '+JSON.stringify(ref.manifest.sha256)+',\n  "scope": '+
 JSON.stringify(scope)+',\n  "cases": [\n'+cases.map(c=>'    '+JSON.stringify(c)).join(',\n')+'\n  ]\n}\n',{flag:'wx'});
console.log(JSON.stringify({cases:cases.length,unambiguous:cases.filter(c=>!c.expected.issues.length).length}));

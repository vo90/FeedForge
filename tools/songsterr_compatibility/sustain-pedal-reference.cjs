'use strict';
// Development only: invoke the complete pinned scheduler, observing CC64 emission.
const fs=require('node:fs'), vm=require('node:vm'), assert=require('node:assert/strict');
const ref=require('./reference.cjs');
const [worker,schema,output]=process.argv.slice(2);
if(!worker||!schema||!output||fs.existsSync(output))throw Error('Supply pinned worker, schema and a NEW output file');
const schemaBytes=fs.readFileSync(schema);
const schemaSha256='8b9267cd39f7f3b0511bade44de01cf3fe7c8025d5a7d534f8a6de448c940e17';
assert.equal(ref.digest(schemaBytes),schemaSha256);
assert.ok(schemaBytes.toString().includes('sustainPedal:L(wn())'));
const code=ref.prepare(worker),cases=[];
function run(part,profile){
 const ctx=vm.createContext({input:structuredClone(part),profile,console:{warn(){},error(){},log(){}}},
  {codeGeneration:{strings:false,wasm:false}});
 vm.runInContext(code,ctx,{timeout:5000});
 vm.runInContext(`globalThis.pedalEvents=[];globalThis.pedalSpans=[];
 {const original=Ds;Ds=function(model){pedalSpans=Es(model);const add=model.timeline.addEvent;
 model.timeline.addEvent=function(event,...args){pedalEvents.push(JSON.parse(JSON.stringify(event)));return add.call(this,event,...args)};
 try{return original(model)}finally{model.timeline.addEvent=add}}}
 globalThis.tpqn=_r(input.measures);
 globalThis.model=mc(input,xc(profile==='authored'?{synth:'fluidsynth',useRSE:false,autoFixJson:false,humanize:false,tpqn}:{tpqn}));`,ctx,{timeout:5000});
 const events=JSON.parse(JSON.stringify(ctx.pedalEvents));
 assert.ok(events.every(e=>e.name==='CONTROLLER'&&e.controller===64&&[0,127].includes(e.value)));
 return {noteHash:ref.digest(JSON.stringify(ref.snapshot(ctx.model))),notes:ref.snapshot(ctx.model).length,
  spans:JSON.parse(JSON.stringify(ctx.pedalSpans)).map(s=>[s.startTick/ctx.tpqn,s.endTick/ctx.tpqn]),
  controllers:[...new Set(events.map(e=>JSON.stringify([e.time/ctx.tpqn,e.value])))].map(x=>JSON.parse(x)).sort((a,b)=>a[0]-b[0])};
}
for(const program of [24,33])for(const profile of ['authored','player-defaults'])for(const shape of ['rest','tie','overlap']){
 const chord=[{fret:5,string:0},{fret:7,string:1}],rest={duration:[1,4],type:4,rest:true,notes:[{rest:true}]};
 const voices=[{beats:[{duration:[1,4],type:4,sustainPedal:true,notes:chord},
  shape==='tie'?{duration:[1,4],type:4,sustainPedal:true,notes:chord.map(n=>({...n,tie:true}))}:{...rest,sustainPedal:true},
  {duration:[1,4],type:4,sustainPedal:false,notes:[{fret:9,string:0}]},{...rest,sustainPedal:true}]}];
 if(shape==='overlap')voices.push({beats:[{...rest,duration:[1,2],type:2,sustainPedal:false},{...rest,duration:[1,2],type:2,sustainPedal:true}]});
 const part={instrumentId:program,tuning:[64,59,55,50,45,40],strings:6,frets:24,
  measures:[{signature:[4,4],repeatStart:true,repeat:2,voices}]};
 const inactive=structuredClone(part);
 for(const v of inactive.measures[0].voices)for(const b of v.beats)b.sustainPedal=false;
 const active=run(part,profile),control=run(inactive,profile);
 assert.equal(active.noteHash,control.noteHash);assert.equal(control.controllers.length,0);
 assert.deepEqual(active.spans,shape==='overlap'?[[0,8]]:[[0,2],[3,6],[7,8]]);
 cases.push({program,profile,shape,active,control});
}
fs.writeFileSync(output,JSON.stringify({schemaSha256,referenceSha256:ref.manifest.sha256,
 scope:'MIDI CC64 expression differs; sampled authored/final note scheduling does not. No claim of equivalent synthesized sounding duration.',cases},null,2)+'\n',{flag:'wx'});

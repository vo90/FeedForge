'use strict';
// Development only. Regenerate known answers with the exact reviewed worker.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const ref=require('./reference.cjs');
const [worker,output]=process.argv.slice(2);
if(!worker||!output||fs.existsSync(output))throw Error('Supply reviewed worker and NEW output path');
const code=ref.prepare(worker),cases=[];
function run(input,profile){
 const ctx=vm.createContext({input:structuredClone(input),profile,console:{warn(){},error(){},log(){}}},
  {codeGeneration:{strings:false,wasm:false}});
 vm.runInContext(code,ctx,{timeout:5000});
 vm.runInContext(`globalThis.tempos=[];{const original=uo;uo=function(model){const add=model.timeline.addEvent;
 model.timeline.addEvent=function(e,...args){if(e.name==='TEMPO')tempos.push(JSON.parse(JSON.stringify(e)));return add.call(this,e,...args)};
 try{return original(model)}finally{model.timeline.addEvent=add}}}
 globalThis.tpqn=_r(input.measures);globalThis.prepared=Pi(input,{useReprisesCheck:true,tpqn});
 globalThis.model=mc(input,xc(profile==='authored'?{synth:'fluidsynth',useRSE:false,autoFixJson:false,humanize:false,tpqn}:{tpqn}));`,ctx,{timeout:5000});
 return JSON.parse(JSON.stringify({tempos:ctx.prepared.automations.tempos,emitted:ctx.tempos,
  tpqn:ctx.tpqn,notes:ref.digest(JSON.stringify(ref.snapshot(ctx.model)))}));
}
for(const profile of ['authored','player-defaults'])for(const repeat of [false,true])for(const hold of [false,true])
for(const winner of [{bpm:57,type:2,dotted:true,linear:false},{bpm:84,type:8,linear:true},{bpm:60,type:4}]){
 const input={instrumentId:25,tuning:[64,59,55,50,45,40],strings:6,frets:24,
  automations:{gradualTempo:true,tempo:[{measure:0,position:0,bpm:100,type:4},
   {measure:1,position:0,bpm:83,type:4,linear:true},{measure:1,position:0,...winner},
   {measure:2,position:0,bpm:140,type:4,linear:true}],
   fermata:hold?[{measure:1,position:1920,type:'medium',length:0.6}]:[]},
  measures:Array.from({length:4},(_,i)=>({signature:[4,4],...(repeat&&i===0?{repeatStart:true}:{}),...(repeat&&i===2?{repeat:2}:{}),
   voices:[{beats:[{duration:[1,1],type:1,notes:[{fret:i+3,string:0}]}]}]}))};
 const control=structuredClone(input);control.automations.tempo.splice(1,1);
 const actual=run(input,profile);assert.deepEqual(actual,run(control,profile));assert.ok(actual.emitted.length);
 cases.push({profile,repeat,hold,input,expected:actual});
}
fs.writeFileSync(output,JSON.stringify({referenceSha256:ref.manifest.sha256,
 scope:'Native complete scheduling plus tempo emission. Duplicate instructions resolved before hold/ramp expansion. No audio-alignment claim.',cases},null,2)+'\n',{flag:'wx'});

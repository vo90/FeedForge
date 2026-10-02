'use strict';
// Development only; exact reviewed local source, bounded VM, no network.
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
 return JSON.parse(JSON.stringify({clocks:ctx.prepared.measures.map(m=>m.tempos),emitted:ctx.tempos,
  traversal:ctx.prepared.progression,tpqn:ctx.tpqn,notes:ref.digest(JSON.stringify(ref.snapshot(ctx.model)))}));
}
for(const profile of ['authored','player-defaults'])for(const repeat of [false,true])for(const hold of [false,true])
for(const enabled of [false,true])for(const distance of [0,17])for(const position of [0,960]){
 const input={instrumentId:25,tuning:[64,59,55,50,45,40],strings:6,frets:24,
  automations:{gradualTempo:enabled,tempo:[{measure:0,position:0,bpm:100,type:4},
   {measure:1,position:0,bpm:84,type:8},{measure:4+distance,position,bpm:177,type:2,dotted:true},
   {measure:9+distance,position:0,bpm:55,type:4,linear:!enabled}],
   fermata:hold?[{measure:2,position:1920,type:'medium',length:0.6}]:[]},
  measures:Array.from({length:4},(_,i)=>({signature:[4,4],...(repeat&&i===0?{repeatStart:true}:{}),...(repeat&&i===2?{repeat:2}:{}),
   voices:[{beats:[{duration:[1,1],type:1,notes:[{fret:i+3,string:0}]}]}]}))};
 const control=structuredClone(input);control.automations.tempo.splice(2);
 const actual=run(input,profile);assert.deepEqual(actual,run(control,profile));assert.ok(actual.emitted.length);
 cases.push({profile,repeat,hold,enabled,distance,position,input,expected:actual});
}
// A missing-bar event is NOT harmless in the presence of an active ramp:
// Ur maps its absent offset to zero, changing the starting rate of a ramp.
const counterexample=structuredClone(cases[0].input);
counterexample.automations={gradualTempo:true,tempo:[{measure:0,position:0,bpm:100},
 {measure:1,position:0,bpm:180,linear:true},{measure:8,position:0,bpm:40}]};
const control=structuredClone(counterexample);control.automations.tempo.pop();
const negative=[];
for(const profile of ['authored','player-defaults']){
 const actual=run(counterexample,profile),removed=run(control,profile);
 assert.notDeepEqual(actual.clocks,removed.clocks);
 negative.push({profile,input:counterexample,actual,removed});
}
fs.writeFileSync(output,JSON.stringify({referenceSha256:ref.manifest.sha256,
 scope:'Beyond-score tempo is inactive only with an explicit initial clock and no active gradual ramps. Native emitted tempo and scheduled notes compared; no acoustic claim.',cases,negative},null,2)+'\n',{flag:'wx'});
console.log(JSON.stringify({cases:cases.length,negative:negative.length}));

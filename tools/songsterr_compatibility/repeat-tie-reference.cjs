'use strict';
const fs=require('node:fs'),assert=require('node:assert/strict');
const ref=require('./reference.cjs');
const [worker,output]=process.argv.slice(2);
if(!worker||!output||fs.existsSync(output))throw Error('Supply reviewed worker and NEW output');
const code=ref.prepare(worker),cases=[];
for(const profile of ['authored','player-defaults'])for(const bass of [false,true])
for(const loopLength of [1,2])for(const repeats of [2,3])for(const chord of [false,true]){
 const fret=repeats===2?0:7,strings=bass?4:6;
 const bar=tie=>({signature:[4,4],voices:[{beats:[{duration:[1,1],type:1,
  notes:Array.from({length:chord?2:1},(_,s)=>({string:s,fret:fret+s,...(tie?{tie:true}:{})}))}]}]});
 const measures=[bar(false),...Array.from({length:loopLength},()=>bar(true)),bar(true)];
 measures[1].repeatStart=true;measures[loopLength].repeat=repeats;
 const input={instrumentId:bass?33:25,strings,frets:24,tuning:bass?[43,38,33,28]:[64,59,55,50,45,40],
  measures,automations:{tempo:[{measure:0,position:0,bpm:120,type:4}]}};
 const r=ref.runPart(code,input,measures,{trace:true,profile});
 const notes=r.stages.ks.filter(n=>!n.hidden);
 assert.equal(notes.length,chord?2:1);
 assert.equal(r.events.filter(n=>!n.hidden).length,notes.length);
 const quarters=4*(2+loopLength*repeats);
 assert.ok(notes.every(n=>n.attackTick===0&&n.endTick+1===quarters*r.tpqn));
 cases.push({profile,bass,loopLength,repeats,chord,input,tpqn:r.tpqn,expected:notes,traversal:r.traversal});
}
fs.writeFileSync(output,JSON.stringify({referenceSha256:ref.manifest.sha256,
 scope:'Native ks tied-note stage before synthesis strum/envelope adjustments; final visible attack count also checked.',cases},null,2)+'\n',{flag:'wx'});

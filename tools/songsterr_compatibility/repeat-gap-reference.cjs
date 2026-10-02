'use strict';
// Explicit maintenance only. Expected spans come from the pinned native tie
// stage, with both ordinary and unrolled repeat traversal checked first.
const fs=require('node:fs'),assert=require('node:assert/strict');
const ref=require('./reference.cjs');
function inputs(){
 const cases=[];
 for(const bass of [false,true])for(const duration of [[1,2],[3,4],[1,8]])
 for(const repeats of [2,4])for(const chord of [false,true])for(const freshAttack of [false,true]){
  const strings=bass?4:6, fret=repeats===2?0:7;
  const beat=(tie,dur)=>({duration:dur,type:dur[1],...(dur[0]===3?{type:2,dots:1}:{}),
   notes:Array.from({length:chord?2:1},(_,s)=>({string:s,fret:fret+s,...(tie?{tie:true}:{})}))});
  const bar=tie=>({signature:[4,4],voices:[{beats:[beat(tie,duration)]}]});
  const measures=[bar(false),bar(true)];
  if(freshAttack)measures[1].voices[0].beats=[beat(true,[1,8]),beat(false,[1,8])];
  measures[1].repeatStart=true;measures[1].repeat=repeats;
  cases.push({id:`${bass?'bass':'guitar'}-${duration.join('_')}-${repeats}-${chord?'chord':'single'}-${freshAttack?'reattack':'held'}`,
   input:{instrumentId:bass?33:25,strings,frets:24,tuning:bass?[43,38,33,28]:[64,59,55,50,45,40],
    measures,automations:{tempo:[{measure:0,position:0,bpm:120,type:4}]}},repeats});
 }
 return cases;
}
function unroll(input,repeats){
 const out=structuredClone(input),entrance=out.measures.pop();
 delete entrance.repeatStart;delete entrance.repeat;
 out.measures.push(...Array.from({length:repeats},()=>structuredClone(entrance)));
 return out;
}
function musical(notes){return notes.filter(n=>!n.hidden).map(n=>({string:n.string,fret:n.fret,attackTick:n.attackTick,endTick:n.endTick}));}
function main(){
 const [worker,output]=process.argv.slice(2);
 if(!worker||!output||fs.existsSync(output))throw Error('Supply reviewed worker and NEW output');
 const code=ref.prepare(worker),cases=[];
 for(const row of inputs())for(const profile of ['authored','player-defaults']){
  const before=JSON.stringify(row.input);
  const r=ref.runPart(code,row.input,row.input.measures,{profile,trace:true});
  const expanded=unroll(row.input,row.repeats);
  const u=ref.runPart(code,expanded,expanded.measures,{profile,trace:true});
  assert.equal(r.tpqn,u.tpqn);
  assert.deepEqual(musical(r.stages.ks),musical(u.stages.ks));
  assert.equal(JSON.stringify(row.input),before);
  cases.push({...row,profile,tpqn:r.tpqn,expected:musical(r.stages.ks),traversal:r.traversal});
 }
 fs.writeFileSync(output,JSON.stringify({referenceSha256:ref.manifest.sha256,
  scope:'Native tied spans through unfilled bar space, equivalent to written-out repetition. No explicit-rest or pitch repairs authorized.',cases},null,2)+'\n',{flag:'wx'});
 console.log(JSON.stringify({cases:cases.length,output}));
}
if(require.main===module)main();
module.exports={inputs,unroll};

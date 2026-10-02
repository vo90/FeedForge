'use strict';
// Explicit offline regeneration from hash-pinned public bundles; never runs in the app.
const fs=require('node:fs'),assert=require('node:assert/strict');
const [common,vendor,output]=process.argv.slice(2);
if(!common||!vendor||!output||fs.existsSync(output))throw Error('Supply common, vendor and a NEW output fixture path');
const ref=require('./video-clock-reference.cjs').prepare(common,vendor),cases=[];
for(const signature of [[4,4],[3,4],[6,8]])for(const side of ['before','after'])
for(const position of [480,960,1440])for(const repeat of [2,5])for(const bass of [false,true]){
 const tuning=bass?[43,38,33,28]:[64,59,55,50,45,40];
 const measures=Array.from({length:5},()=>({signature,voices:[{beats:[{duration:[1,4],type:4,notes:[{string:0,fret:3}]}]}]}));
 measures[1].repeatStart=true;measures[2].repeat=repeat;
 const input={instrumentId:bass?33:29,tuning,measures,automations:{tempo:[
  {measure:0,position:0,bpm:120,type:4},{measure:side==='before'?0:3,position,bpm:80,type:4}]}};
 const native=ref.replay(input),boundaries=native.boundaries.map(x=>x/1000);
 const recording=boundaries.map((x,i)=>.5+x*(1+i/200));
 const samples=[];
 for(let i=0;i<boundaries.length-1;i++)for(const fraction of [.25,.5,.75]){
  const score=boundaries[i]+fraction*(boundaries[i+1]-boundaries[i]);
  const audio=ref.interpolate(boundaries,recording,score);
  assert.ok(Math.abs(ref.interpolate(recording,boundaries,audio)-score)<1e-10);
  samples.push({score,audio});
 }
 cases.push({id:`${signature.join('/')}-${side}-${position}-${repeat}-${bass?'bass':'guitar'}`,input,
  order:native.order,boundaries,recording,samples});
}
fs.writeFileSync(output,JSON.stringify({version:1,reference:ref.manifest.map(({id,sha256})=>({id,sha256})),cases},null,2)+'\n');
console.log(JSON.stringify({cases:cases.length,output}));

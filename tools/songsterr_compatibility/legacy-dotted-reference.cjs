'use strict';
// Development only: expected clocks come from the hash-pinned public player.
const fs=require('node:fs'),assert=require('node:assert/strict');
const ref=require('./reference.cjs');
const [worker,output]=process.argv.slice(2);
if(!worker||!output||fs.existsSync(output))throw Error('Supply pinned worker and a NEW fixture path');
const code=ref.prepare(worker),cases=[];
const flags=[{dotted:true},{dots:1,dotted:true},{dots:0,dotted:true},{dots:null,dotted:true},
 {dots:2,dotted:true},{dots:2,dotted:false},{dotted:false},{dotted:null}];
for(const profile of ['authored','player-defaults'])for(const [fi,flag] of flags.entries())
for(const shape of ['note','rest','tuplet','grace','whole-rest']){
 const dots=flag.dots>0?flag.dots:flag.dotted===true?1:0,num=2**(dots+1)-1,den=2**dots;
 const type=shape==='whole-rest'?1:shape==='grace'?2:4;
 const duration=[num*(shape==='tuplet'?2:1),den*type*(shape==='tuplet'?3:1)];
 const b={type,duration,...flag,notes:[{fret:5,string:0}]};
 if(shape==='rest'||shape==='whole-rest')Object.assign(b,{rest:true,notes:[{rest:true}]});
 if(shape==='tuplet')b.tuplet=3;
 if(shape==='grace')b.graceNote='onBeat';
 const beats=shape==='whole-rest'?[b]:shape==='grace'?[b,
  {type:4,duration:[1,4],notes:[{fret:7,string:0}]},
  {type:2,dots:1,duration:[3,4],rest:true,notes:[{rest:true}]}]:[b,
  {duration:[duration[1]-duration[0],duration[1]],rest:true,notes:[{rest:true}]}];
 const part={instrumentId:30,tuning:[64,59,55,50,45,40],strings:6,frets:24,
  automations:{tempo:[{measure:0,position:0,bpm:120,type:4}]},measures:[
   {signature:[4,4],voices:[{beats}]},
   {signature:[4,4],voices:[{beats:[{type:1,duration:[1,1],notes:[{fret:9,string:0}]}]}]}]};
 const source={format:'songsterr',songId:1,revisionId:1,title:'Dot fixture',artist:'Synthetic',
  tracks:[{id:'g',name:'Lead',instrumentId:30,tuning:part.tuning}],parts:[part]};
 const before=JSON.stringify(source),reference=ref.runPart(code,part,part.measures,{profile});
 assert.equal(JSON.stringify(source),before);
 cases.push({id:`${profile}-${fi}-${shape}`,shape,dots,source,
  reference:{parts:[{index:0,...reference}]}});
}
fs.writeFileSync(output,JSON.stringify({referenceSha256:ref.manifest.sha256,cases})+'\n',{flag:'wx'});
console.log(`${cases.length} pinned-player cases captured`);

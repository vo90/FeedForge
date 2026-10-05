'use strict';
// Development only. Capture synthetic expectations from the reviewed player.
const fs=require('node:fs'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const ref=require('./reference.cjs');
const [worker,output]=process.argv.slice(2);
if(!worker||!output||fs.existsSync(output))throw Error('Supply pinned worker and a NEW fixture path');
const code=ref.prepare(worker),cases=[];
const hash=x=>crypto.createHash('sha256').update(JSON.stringify(x)).digest('hex');
for(const instrumentId of [27,30,34])for(const profile of ['authored','player-defaults'])
for(const shape of ['bend','note-vibrato','tied-bend']){
 const tuning=instrumentId===34?[43,38,33,28]:[64,59,55,50,45,40];
 const note={fret:5,string:0,bend:{points:[{position:0,tone:0},{position:30,tone:100},{position:60,tone:100}]}};
 if(shape==='note-vibrato')note.leftHandVibrato='wide';
 const part={instrumentId,tuning,strings:tuning.length,frets:24,
  automations:{tempo:[{measure:0,position:0,bpm:120,type:4}]},measures:[
   {signature:[4,4],voices:[{beats:[{type:1,duration:[1,1],notes:[note]}]}]},
   {signature:[4,4],voices:[{beats:[{type:1,duration:[1,1],notes:[shape==='tied-bend'?{fret:5,string:0,tie:true}:{fret:7,string:0}]}]}]}]};
 const trace=p=>ref.runPart(code,p,p.measures,{profile,trace:true,eventDetails:true});
 const baseline=trace(part);
 for(const value of [null,false,0]){
  const candidate=structuredClone(part);
  candidate.measures[0].voices[0].beats[0].notes[0].bend.points.forEach(p=>p.vibrato=value);
  const before=JSON.stringify(candidate),actual=trace(candidate);
  assert.equal(hash(actual),hash(baseline),'Inactive field changed native preparation or synthesis');
  assert.equal(JSON.stringify(candidate),before);
  const source={format:'songsterr',songId:1,revisionId:1,title:'Bend fixture',artist:'Synthetic',
   tracks:[{id:'g',name:instrumentId===34?'Bass':'Lead',instrumentId,tuning}],parts:[candidate]};
  cases.push({id:`${instrumentId}-${profile}-${shape}-${value}`,source,
   traceSha256:hash(actual),reference:{parts:[{index:0,...ref.runPart(code,candidate,candidate.measures,{profile})}]}});
  if(global.gc)global.gc();
 }
}
fs.writeFileSync(output,JSON.stringify({referenceSha256:ref.manifest.sha256,cases})+'\n',{flag:'wx'});
console.log(`${cases.length} pinned-player cases captured with identical native synthesis`);

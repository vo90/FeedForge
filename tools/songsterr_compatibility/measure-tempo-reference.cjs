'use strict';
// Offline qualification against the hash-pinned complete worker and extracted runner.
const fs=require('node:fs'),assert=require('node:assert/strict');
const ref=require('./reference.cjs'),native=require('./legacy-beat-effects-reference.cjs');
const [worker,output]=process.argv.slice(2);
if(!worker||!output||fs.existsSync(output))throw Error('Supply reviewed worker and NEW report path');
const scripts=native.scripts(worker),qualifications=[],checks=[];
const bar=fret=>({signature:[4,4],voices:[{beats:[{duration:[1,1],notes:[{string:0,fret}]}]}]});
const source={parts:[{instrumentId:29,tuning:[64,59,55,50,45,40],
  measures:[{...bar(3),repeatStart:true},{...bar(5),repeat:2}],
  automations:{tempo:[{measure:0,position:0,type:4,bpm:120},{measure:1,position:0,type:4,bpm:90}]}}]};
for(const profile of ['authored','player-defaults']){
 const baseline=native.nativeCase(scripts,source,profile,qualifications,'baseline');
 assert(baseline.parts.every(p=>p.status==='executed'));
 for(const value of [null,{type:4,bpm:90},{type:4,bpm:47},{type:8,bpm:246}]){
  const variant=structuredClone(source);
  for(const measure of variant.parts[0].measures)measure.tempo=value;
  assert.deepEqual(native.nativeCase(scripts,variant,profile,qualifications,'local-'+JSON.stringify(value)),baseline);
 }
 const positive=structuredClone(source);positive.parts[0].automations.tempo[1].bpm=100;
 const changed=native.nativeCase(scripts,positive,profile,qualifications,'changed-automation');
 assert.notDeepEqual(changed.parts[0].scheduled,baseline.parts[0].scheduled);
 checks.push({profile,localVariantsIdentical:4,automationChangesScheduledEvents:true});
}
assert(qualifications.every(q=>q.status==='matched'));
fs.writeFileSync(output,JSON.stringify({referenceSha256:ref.manifest.sha256,source,checks,qualifications,
 scope:'Pinned worker scheduling and preparation only; no live website or acoustic verification claim.'},null,2)+'\n',{flag:'wx'});
console.log(JSON.stringify({checks,comparisons:qualifications.length}));

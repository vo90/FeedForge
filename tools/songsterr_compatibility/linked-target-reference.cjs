'use strict';
// Explicit regeneration from the reviewed local worker, never converter output.
const fs=require('node:fs'),assert=require('node:assert/strict');
const ref=require('./reference.cjs');
const {cases}=require('./linked-target-cases.cjs');
const [worker,output]=process.argv.slice(2);
if(!worker||!output||fs.existsSync(output))throw Error('Supply reviewed worker and NEW output');
const code=ref.prepare(worker),rows=[];
for(const row of cases()) {
 const original=JSON.stringify(row.source),profiles=[];
 for(const profile of ['authored','player-defaults']) {
  const part=row.source.parts[0];
  const result=ref.runPart(code,part,part.measures,{profile,trace:true,eventDetails:true});
  const plain=ref.runPart(code,part,part.measures,{profile});
  for(const key of ['beats','traversal','authoredEvents','events'])
   assert.deepEqual(structuredClone(result[key]),structuredClone(plain[key]));
  const again=ref.runPart(code,part,part.measures,{profile,trace:true,eventDetails:true});
  assert.deepEqual(result.generatedEventTrace,again.generatedEventTrace);
  profiles.push({profile,tpqn:result.tpqn,authored:result.authoredEvents,final:result.events,
   generatedSha256:ref.digest(JSON.stringify(result.generatedEventTrace))});
 }
 assert.equal(JSON.stringify(row.source),original);
 rows.push({...row,sourceSha256:ref.digest(original),profiles});
}
fs.writeFileSync(output,JSON.stringify({version:1,referenceSha256:ref.manifest.sha256,
 scope:'Synthetic source/final fret and identity observations; not authorization to copy synthesis repairs into gameplay.',
 cases:rows},null,2)+'\n',{flag:'wx'});
console.log(JSON.stringify({cases:rows.length,profiles:rows.length*2,deterministic:true,observerNoninterference:true}));

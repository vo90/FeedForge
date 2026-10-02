'use strict';
// Explicit maintenance only. Expectations come from the pinned local reference,
// never from converter output. Qualify the full script separately before review.
const fs=require('node:fs'),assert=require('node:assert/strict');
const ref=require('./reference.cjs');
const {cases}=require('./generated-event-cases.cjs');
const [worker,output]=process.argv.slice(2);
if(!worker||!output||fs.existsSync(output))throw Error('Supply reviewed worker and NEW output path');
const code=ref.prepare(worker),rows=[];
for(const profile of ['authored','player-defaults'])for(const row of cases()) {
  const part=row.source.parts[0],allMeasures=part.measures;
  const original=JSON.stringify(row.source);
  const plain=ref.runPart(code,part,allMeasures,{profile});
  const observed=ref.runPart(code,part,allMeasures,{profile,eventDetails:true});
  for(const key of ['tpqn','beats','traversal','authoredEvents','events','warnings'])
    // Native arrays belong to different VM realms; compare cloned values,
    // preserving undefined and exact numbers rather than realm prototypes.
    assert.deepEqual(structuredClone(observed[key]),structuredClone(plain[key]),`Observer changed ${key}`);
  const again=ref.runPart(code,part,allMeasures,{profile,eventDetails:true});
  assert.deepEqual(observed.generatedEventTrace,again.generatedEventTrace,'Nondeterministic event trace');
  assert.equal(JSON.stringify(row.source),original,'Reference mutated source input');
  rows.push({id:row.id,profile,sourceSha256:ref.digest(original),tpqn:observed.tpqn,
    generatedEventTrace:observed.generatedEventTrace});
}
fs.writeFileSync(output,JSON.stringify({version:1,referenceSha256:ref.manifest.sha256,
  scope:'Native tied-trill event evidence only; not approved gameplay behavior.',cases:rows},null,2)+'\n',{flag:'wx'});
console.log(JSON.stringify({cases:rows.length,deterministic:true,sourceUnchanged:true,observerNoninterference:true}));

'use strict';
// Reviewed native fixture generation. No captured worker bytes are bundled.
const fs=require('node:fs'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const ref=require('./reference.cjs'),inputs=require('./plain-tie-cases.cjs');
const [worker,output]=process.argv.slice(2);
if(!worker||!output||fs.existsSync(output))throw Error('Supply reviewed worker and NEW fixture');
const code=ref.prepare(worker),profiles=['authored',ref.profiles.LEGACY_BRUSH_PROFILE,'player-defaults'],rows=[];
const compact=r=>({status:r.status,profile:r.profile,tpqn:r.tpqn,...(r.normalization?{normalization:r.normalization}:{}),
 beats:r.beats,traversal:r.traversal,authoredEvents:r.authoredEvents,heldEvents:r.stages.ks,events:r.events,
 generatedFinal:r.generatedEventTrace.final,scheduled:r.generatedEventTrace.scheduled});
for(const row of inputs.cases()){
 const before=JSON.stringify(row.source),allMeasures=row.source.parts.flatMap(p=>p.measures),reference={};
 for(const profile of profiles){
  const r=ref.runPart(code,row.source.parts[0],allMeasures,{profile,trace:true,eventDetails:true});
  reference[profile]=compact(r);
  if(profile!=='player-defaults')assert.equal(JSON.stringify(compact(ref.runPart(code,row.source.parts[0],allMeasures,{profile,trace:true,eventDetails:true}))),JSON.stringify(reference[profile]),'Nondeterministic authored native result');
 }
 assert.equal(JSON.stringify(row.source),before,'Source changed');
 rows.push({...row,reference});
}
const bindings=ref.manifest.bindings.filter(b=>['fc','ks','za','Y','ro','no'].includes(b.name));
fs.writeFileSync(output,JSON.stringify({version:1,policy:'songsterr-plain-tie-identity-v1',referenceSha256:ref.manifest.sha256,
 manifestSha256:crypto.createHash('sha256').update(fs.readFileSync(require.resolve('./reference-manifest.json'))).digest('hex'),
 node:process.version,bindings,profileOptions:Object.fromEntries(profiles.map(p=>[p,ref.profiles.options(p,undefined)])),
 scope:'Native fc effective tie target, authored Ts and merged ks source-clock events; source frets remain serialized. Later synthesis effects are separately captured. Guard-only inputs never imply import support.',cases:rows}),{flag:'wx'});
console.log(JSON.stringify({output,cases:rows.length,profiles:profiles.length,guards:rows.filter(r=>r.expectedDisposition==='blocked').length}));

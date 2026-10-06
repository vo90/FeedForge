'use strict';
// Explicit development fixture generation from the hash-pinned native worker.
const fs=require('node:fs'),assert=require('node:assert/strict');
const ref=require('./reference.cjs');
const [worker,inputPath,outputPath]=process.argv.slice(2);
if(!worker||!inputPath||!outputPath||fs.existsSync(outputPath))throw Error('Supply pinned worker, synthetic input and a NEW fixture');
const code=ref.prepare(worker),input=JSON.parse(fs.readFileSync(inputPath,'utf8'));
if(input.cases.length>160)throw Error('Legacy brush fixture limit');
const profiles=['authored',ref.profiles.LEGACY_BRUSH_PROFILE,'player-defaults'],cases=[];
for(const row of input.cases){
 const original=JSON.stringify(row.source),allMeasures=row.source.parts.flatMap(p=>p.measures),reference={};
 for(const profile of profiles){
  reference[profile]={parts:row.source.parts.map((p,index)=>({index,...ref.runPart(code,p,allMeasures,{profile,eventDetails:true})}))};
  if(profile===ref.profiles.LEGACY_BRUSH_PROFILE){
   const repeat=row.source.parts.map((p,index)=>({index,...ref.runPart(code,p,allMeasures,{profile,eventDetails:true})}));
   assert.equal(JSON.stringify(reference[profile].parts),JSON.stringify(repeat),'Normalized profile is not deterministic');
  }
 }
 assert.equal(JSON.stringify(row.source),original,'Native fixture source mutated');
 cases.push({...row,reference});
}
fs.writeFileSync(outputPath,JSON.stringify({version:1,referenceSha256:ref.manifest.sha256,
 normalization:ref.profiles.evidence(ref.profiles.LEGACY_BRUSH_PROFILE),
 profileOptions:Object.fromEntries(profiles.map(p=>[p,ref.profiles.options(p,undefined)])),
 scope:'Pinned native scheduling with distinct raw-authored, normalized old-brush and player-default profiles. No acoustic or game equivalence.',cases})+'\n',{flag:'wx'});
console.log(JSON.stringify({cases:cases.length,profiles:profiles.length,output:outputPath}));

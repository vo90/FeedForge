'use strict';
// Offline development evidence. The reviewed complete worker stays outside Git.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const ref=require('./reference.cjs'),trace=require('./event-trace.cjs'),inputs=require('./legacy-beat-effects-cases.cjs');
const profiles=['authored',ref.profiles.LEGACY_BRUSH_PROFILE,'player-defaults'];
const copy=x=>JSON.parse(JSON.stringify(x));
function sorted(x){return Array.isArray(x)?x.map(sorted):x&&typeof x==='object'?Object.fromEntries(Object.keys(x).sort().map(k=>[k,sorted(x[k])])):x;}
const sourceHash=x=>ref.digest(JSON.stringify(sorted(x)));
function annotate(part){
 const input=structuredClone(part);
 input.measures.forEach((m,mi)=>{m.__ffbar=mi;(m.voices||[]).forEach((v,vi)=>(v.beats||[]).forEach((b,bi)=>{
  b.__ffbeat=mi+':'+vi+':'+bi;(b.notes||[]).forEach((n,ni)=>{n.__ffnote=b.__ffbeat+':'+ni;});
 }));});
 return input;
}
function targets(model){
 const rows=[];
 for(const [occurrence,m] of model.progression.entries())for(const [voiceIndex,v] of m.voices.entries())
  for(const [beatIndex,b] of (v.beats||[]).entries())for(const [noteIndex,n] of (b.notes||[]).entries())
   if(!b.rest&&!n.rest)rows.push({id:n.source?.__ffnote??n.__ffnote,occurrence,bar:m.__ffbar,voiceIndex,beatIndex,noteIndex,
    string:n.string,fret:n.fret,sourceFret:n.source?.fret,pitch:n.pitch,originalPitch:n.originalPitch,
    hidden:!!n.isHidden,tie:!!n.tie,attackTick:n.onTick,endTick:n.offTick,
    ...(n.harmonic!==undefined?{harmonic:n.harmonic}:{}),...(n.harmonicFret!==undefined?{harmonicFret:n.harmonicFret}:{}),
    disallowOpenString:!!n.disallowOpenString});
 return copy(rows);
}
const wrappers=['Ts','ks','Qo'].map(name=>'{const original='+name+';'+name+'=function(m){original(m);captureModel("'+name+'",m);};}').join('');
function modelRun(script,input,allMeasures,profile){
 const captured={},scheduled=trace.collector(),options=ref.profiles.options(profile,undefined);
 const c=vm.createContext({URL,input:annotate(input),allMeasures,profileOptions:options,
  captureScheduled:scheduled.record,captureModel:(stage,m)=>{captured[stage]={events:copy(ref.snapshot(m)),targets:targets(m)};},
  location:{origin:'https://www.songsterr.com',href:'https://static3.songsterr.com/reference.js'},
  self:{location:{origin:'https://www.songsterr.com'},addEventListener(){},postMessage(){}},
  console:{log(){},warn(){},error(){}},fetch(){throw Error('Native qualification requested network');},
  setTimeout(){throw Error('Native qualification requested timer');}}, {codeGeneration:{strings:false,wasm:false}});
 vm.runInContext(script,c,{timeout:5000});
 vm.runInContext('globalThis.tpqn=referenceExports._r(allMeasures);globalThis.prepared=referenceExports.Pi(input,{useReprisesCheck:true,tpqn});globalThis.model=referenceExports.mc(input,referenceExports.xc({...profileOptions,tpqn}));',c,{timeout:5000});
 return copy({tpqn:c.tpqn,beats:c.prepared.measures.flatMap((m,mi)=>m.voices.flatMap((v,vi)=>(v.beats||[]).map((b,bi)=>
  ({id:mi+':'+vi+':'+bi,quarter:b.startTick/c.tpqn,duration:b.durationInTicks/c.tpqn})))),traversal:copy(c.prepared.progression),
  authoredEvents:captured.Ts.events,heldEvents:captured.ks.events,naturalTargets:captured.Qo.targets,
  events:copy(ref.snapshot(c.model)),generatedFinal:trace.generatedSnapshot(c.model),scheduled:scheduled.events});
}
function scripts(worker){
 const code=ref.prepare(worker),raw=fs.readFileSync(worker,'utf8'),at=ref.manifest.exposeOffset;
 assert.equal(raw[at],'}','Unreviewed export position');
 const expose='globalThis.referenceExports={mc,Pi,xc,_r};';
 return {code,extracted:code+wrappers+trace.observeEmitter+expose,
  complete:raw.slice(0,at)+';'+wrappers+trace.observeEmitter+expose+raw.slice(at)};
}
function nativeCase(scriptSet,source,profile,qualification,label){
 const allMeasures=source.parts.flatMap(p=>p.measures),parts=[];
 for(const [index,part] of source.parts.entries()){
  let a,b,ae,be;
  try{a=modelRun(scriptSet.extracted,part,allMeasures,profile);}catch(e){ae=String(e.message);}
  try{b=modelRun(scriptSet.complete,part,allMeasures,profile);}catch(e){be=String(e.message);}
  assert.equal(ae,be,'Complete/extracted error mismatch: '+label);
  assert.deepEqual(a,b,'Complete/extracted result mismatch: '+label);
  if(ae){
   qualification.push({id:label,index,profile,status:'matching_reference_error',error:ae});
   parts.push({index,status:'reference_error',profile,error:ae});continue;
  }
  // The added pitch observation must preserve the existing runner as well.
  const ordinary=ref.runPart(scriptSet.code,part,allMeasures,{profile,trace:true,eventDetails:true});
  for(const key of ['tpqn','beats','traversal','authoredEvents','events'])assert.deepEqual(a[key],copy(ordinary[key]),'Existing runner changed: '+key);
  assert.deepEqual(a.heldEvents,copy(ordinary.stages.ks));
  assert.deepEqual(a.generatedFinal,copy(ordinary.generatedEventTrace.final));
  assert.deepEqual(a.scheduled,copy(ordinary.generatedEventTrace.scheduled));
  qualification.push({id:label,index,profile,status:'matched',resultSha256:sourceHash(a)});
  parts.push({index,status:'executed',profile,...(ref.profiles.evidence(profile)?{normalization:ref.profiles.evidence(profile)}:{}),
   ...a,generatedFinalSha256:sourceHash(a.generatedFinal),scheduledSha256:sourceHash(a.scheduled)});
 }
 return {parts};
}
function musical(reference){return reference.parts.map(p=>{
 const {profile,normalization,...rest}=p;return rest;
});}
function main(){
 const [worker,output,qualificationOutput]=process.argv.slice(2);
 if(!worker||!output||!qualificationOutput||fs.existsSync(output)||fs.existsSync(qualificationOutput))throw Error('Supply reviewed worker, NEW fixture and NEW qualification report');
 const rows=inputs.cases();if(rows.length>160)throw Error('Legacy beat fixture case limit');
 const scriptSet=scripts(worker),qualified=[],cases=[];
 for(const row of rows){
  const before=JSON.stringify(row.source),reference={},metadataRemoval={},positiveControl={};
  for(const profile of profiles){
   const actual=nativeCase(scriptSet,row.source,profile,qualified,row.id);
   const absent=nativeCase(scriptSet,inputs.withoutBeatFlags(row.source),profile,qualified,row.id+'/remove-beat-flags');
   assert.deepEqual(musical(actual),musical(absent),'Beat metadata changes native output: '+row.id+'/'+profile);
   reference[profile]=actual;
   metadataRemoval[profile]={matched:true,resultSha256:sourceHash(musical(actual))};
   if(row.noteInstructionControl){
    const unmarked=inputs.withoutBeatFlags(row.source);
    for(const p of unmarked.parts)for(const m of p.measures)for(const v of m.voices)for(const b of v.beats||[])for(const n of b.notes||[]){delete n.harmonic;delete n.harmonicFret;}
    const control=nativeCase(scriptSet,unmarked,profile,qualified,row.id+'/remove-note-harmonics');
    positiveControl[profile]={naturalTargets:control.parts[0].naturalTargets,scheduledSha256:control.parts[0].scheduledSha256,
     differentScheduledEvents:actual.parts[0].scheduledSha256!==control.parts[0].scheduledSha256};
   }
  }
  assert.equal(JSON.stringify(row.source),before,'Serialized source mutated');
  cases.push({...row,sourceSha256:sourceHash(row.source),reference,metadataRemoval,...(row.noteInstructionControl?{positiveControl}:{})});
 }
 const evidence={version:1,policy:inputs.POLICY,node:process.version,referenceSha256:ref.manifest.sha256,
  manifestSha256:ref.digest(fs.readFileSync(require.resolve('./reference-manifest.json'))),
  profileOptions:Object.fromEntries(profiles.map(p=>[p,ref.profiles.options(p,undefined)])),
  bindings:ref.manifest.bindings.filter(b=>['Qo','Harmonics','HarmonicsForElectricity','Ui','fc','ks','ro','no'].includes(b.name)),
  scope:'Pinned complete-worker and extracted-runner scheduling/pitch equivalence per existing profile. Beat summaries are retained only with independently qualified explicit natural notes; fade is disclosed as an unrepresented marking. Guard inputs never grant support. No browser RPC, engraving, acoustic, full-song or FeedPak acceptance claim.'};
 const summary={...evidence,cases:cases.length,guards:cases.filter(r=>r.guardOnly).length,comparisons:qualified.length,
  matched:qualified.filter(r=>r.status==='matched').length,matchingReferenceErrors:qualified.filter(r=>r.status!=='matched').length,
  metadataEquivalences:cases.length*profiles.length,results:qualified};
 fs.writeFileSync(qualificationOutput,JSON.stringify(summary,null,2)+'\n',{flag:'wx'});
 fs.writeFileSync(output,JSON.stringify({...evidence,qualification:{comparisons:summary.comparisons,matched:summary.matched,
  matchingReferenceErrors:summary.matchingReferenceErrors,metadataEquivalences:summary.metadataEquivalences},cases})+'\n',{flag:'wx'});
 console.log(JSON.stringify({cases:summary.cases,guards:summary.guards,comparisons:summary.comparisons,matched:summary.matched,matchingReferenceErrors:summary.matchingReferenceErrors,output,qualificationOutput}));
}
module.exports={sourceHash,scripts,modelRun,nativeCase,targets};
if(require.main===module)try{main();}catch(e){console.error(e.stack);process.exitCode=2;}

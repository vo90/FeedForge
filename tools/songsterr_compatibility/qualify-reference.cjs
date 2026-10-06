'use strict';
// Compare dependency extraction with the entire captured script, not with our converter.
const fs=require('node:fs'),vm=require('node:vm');
const {prepare,runPart,snapshot,manifest}=require('./reference.cjs');
const eventTrace=require('./event-trace.cjs');
const profiles=require('./reference-profiles.cjs');
function main() {
const [worker,casesFile,output]=process.argv.slice(2);
if(!worker||!casesFile||!output||fs.existsSync(output))throw Error('Supply worker, cases and a NEW output');
const code=prepare(worker),source=fs.readFileSync(worker,'utf8');
const at=manifest.exposeOffset;
if(source[at]!=='}')throw Error('Unreviewed export location');
const cases=JSON.parse(fs.readFileSync(casesFile,'utf8').replace(/^\uFEFF/,'')),results=[];
const profile=cases.profile||'authored';
const profileOptions=profiles.options(profile,undefined);
const full=source.slice(0,at)+';const observedTs=Ts;Ts=function(m){observedTs(m);captureAuthored(m)};'+
  (cases.eventDetails?eventTrace.observeEmitter:'')+'globalThis.referenceExports={mc,Pi,xc,_r};'+source.slice(at);
for(const row of cases.cases){
 const allMeasures=row.source.parts.flatMap(p=>p.measures);
 for(const [index,input] of row.source.parts.entries()){
  let authored;const scheduled=eventTrace.collector();
  const c=vm.createContext({URL,input:structuredClone(input),allMeasures,profile,profileOptions,captureScheduled:scheduled.record,captureAuthored:m=>{authored=snapshot(m)},
   location:{origin:'https://www.songsterr.com',href:'https://static3.songsterr.com/reference.js'},
   self:{location:{origin:'https://www.songsterr.com'},addEventListener(){},postMessage(){}},
   console:{log(){},warn(){},error(){}},
   fetch(){throw Error('Qualification requested network')},setTimeout(){throw Error('Qualification requested timer')}
  },{codeGeneration:{strings:false,wasm:false}});
  vm.runInContext(full,c,{timeout:5000});
  vm.runInContext('globalThis.resolution=referenceExports._r(allMeasures);globalThis.prepared=referenceExports.Pi(input,{useReprisesCheck:true,tpqn:resolution});globalThis.model=referenceExports.mc(input,referenceExports.xc({...profileOptions,tpqn:resolution}));',c,{timeout:5000});
  const extracted=runPart(code,input,allMeasures,{annotateIds:false,profile,eventDetails:!!cases.eventDetails});
  const beats=c.prepared.measures.flatMap((m,mi)=>m.voices.flatMap((v,vi)=>(v.beats||[]).map((b,bi)=>({id:mi+':'+vi+':'+bi,quarter:b.startTick/c.resolution,duration:b.durationInTicks/c.resolution}))));
  const generatedEventMatched=!cases.eventDetails ||
    (JSON.stringify(scheduled.events)===JSON.stringify(extracted.generatedEventTrace.scheduled)&&
     JSON.stringify(eventTrace.generatedSnapshot(c.model))===JSON.stringify(extracted.generatedEventTrace.final));
  const matched=generatedEventMatched&&JSON.stringify(snapshot(c.model))===JSON.stringify(extracted.events)&&JSON.stringify(authored)===JSON.stringify(extracted.authoredEvents)&&JSON.stringify(beats)===JSON.stringify(extracted.beats)&&JSON.stringify(c.prepared.progression)===JSON.stringify(extracted.traversal);
  results.push({id:row.id,index,profile,matched,...(cases.eventDetails?{generatedEventMatched}:{})});
 }
}
const report={version:1,referenceSha256:manifest.sha256,profile,...(profiles.evidence(profile)?{normalization:profiles.evidence(profile)}:{}),scope:'Captured complete script versus extracted scheduling dependencies; no browser RPC or acoustic verification.',results};
fs.writeFileSync(output,JSON.stringify(report,null,2));console.log(JSON.stringify({cases:results.length,matched:results.filter(r=>r.matched).length}));
if(results.some(r=>!r.matched))process.exitCode=3;
}
try { main(); } catch(e) { console.error(e.message); process.exitCode=2; }

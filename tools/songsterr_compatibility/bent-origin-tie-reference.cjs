'use strict';
// Development only. Complete pinned worker supplied outside Git.
const fs=require('node:fs'),assert=require('node:assert/strict');
const ref=require('./reference.cjs'),native=require('./legacy-beat-effects-reference.cjs'),inputs=require('./bent-origin-tie-cases.cjs');
const profiles=['authored',ref.profiles.LEGACY_BRUSH_PROFILE,'player-defaults'];
function compact(part){assert.equal(part.status,'executed');return {profile:part.profile,
 ...(part.normalization?{normalization:part.normalization}:{}),tpqn:part.tpqn,beats:part.beats,traversal:part.traversal,
 authoredEvents:part.authoredEvents,heldEvents:part.heldEvents,events:part.events,
 bends:part.generatedFinal.map(n=>({id:n.id,occurrence:n.occurrence,hidden:n.hidden,
  points:n.events.filter(e=>e.name==='PITCH_BEND').map(e=>[e.time,e.value])})).filter(n=>n.points.length),
 noteEvents:part.scheduled.filter(e=>['NOTE_ON','NOTE_OFF'].includes(e.event.name)).map(e=>
  ({ordinal:e.ordinal,priority:e.priority,time:e.event.time,name:e.event.name,pitch:e.event.pitch,channel:e.event.channel})),
 scheduledSha256:part.scheduledSha256,generatedFinalSha256:part.generatedFinalSha256};}
function main(){const [worker,output,qualificationOutput]=process.argv.slice(2);
 if(!worker||!output||!qualificationOutput||fs.existsSync(output)||fs.existsSync(qualificationOutput))throw Error('Supply reviewed worker and NEW output paths');
 const scripts=native.scripts(worker),qualification=[],result=[],rows=inputs.cases();assert(rows.length<=64);
 for(const row of rows){const before=JSON.stringify(row.source),reference={},sameFret={};
  for(const profile of profiles){const part=native.nativeCase(scripts,row.source,profile,qualification,row.id).parts[0];
   reference[profile]=compact(part);
   if(row.sameFretControl){const control=native.nativeCase(scripts,inputs.sameFret(row.source),profile,qualification,row.id+'/same-fret').parts[0];
    assert.deepEqual(part.scheduled,control.scheduled,row.id+'/'+profile+' stored fret changes native emission');
    sameFret[profile]={emittedScheduleMatched:true,scheduledSha256:control.scheduledSha256};}}
  assert.equal(JSON.stringify(row.source),before);result.push({...row,sourceSha256:native.sourceHash(row.source),reference,
   ...(row.sameFretControl?{sameFret}: {})});}
 const metadata={version:1,family:'ordinary-first-bent-origin-plain-tie',node:process.version,referenceSha256:ref.manifest.sha256,
  manifestSha256:ref.digest(fs.readFileSync(require.resolve('./reference-manifest.json'))),
  profileOptions:Object.fromEntries(profiles.map(p=>[p,ref.profiles.options(p,undefined)])),
  bindings:ref.manifest.bindings.filter(b=>['fc','uc','ks','As','xo','uo'].includes(b.name)),
  scope:'Authored/held clocks and native integer bend emission; no acoustic equality to rational game curve. Complete/extracted/existing runner comparisons include guarded observations, which never grant admission. Fresh worker unexecuted.'};
 const counts={cases:rows.length,guardCases:rows.filter(r=>r.guardOnly).length,comparisons:qualification.length,
  matched:qualification.filter(r=>r.status==='matched').length,matchingReferenceErrors:qualification.filter(r=>r.status!=='matched').length};
 fs.writeFileSync(qualificationOutput,JSON.stringify({...metadata,...counts,results:qualification},null,2)+'\n',{flag:'wx'});
 fs.writeFileSync(output,JSON.stringify({...metadata,qualification:counts,cases:result})+'\n',{flag:'wx'});
 console.log(JSON.stringify({...counts,fixtureBytes:fs.statSync(output).size,output,qualificationOutput}));}
if(require.main===module)try{main();}catch(e){console.error(e.stack);process.exitCode=2;}
module.exports={compact};

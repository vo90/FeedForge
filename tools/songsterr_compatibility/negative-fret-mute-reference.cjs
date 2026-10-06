'use strict';
// Compact development fixture. The reviewed complete worker stays outside Git.
const fs=require('node:fs'),assert=require('node:assert/strict');
const ref=require('./reference.cjs'),native=require('./legacy-beat-effects-reference.cjs');
const POLICY='songsterr-negative-fret-mute-v1';
const profiles=['authored',ref.profiles.LEGACY_BRUSH_PROFILE,'player-defaults'];
const note=(fret,string=0,extra={})=>({string,...(fret!==undefined?{fret}:{}),...extra});
const beat=(notes,duration=[1,4],extra={})=>({type:duration[1],duration,notes,...extra});
const bar=(beats,extra={})=>({signature:[4,4],voices:[{beats}],...extra});
function envelope(measures,{program=24,version=5,capo=0}={}){
 const tuning=program===33?[43,38,33,28]:[64,59,55,50,45,40];
 const part={name:'Unpitched mute fixture',instrumentId:program,tuning,strings:tuning.length,frets:24,capo,measures,
  automations:{tempo:[{measure:0,position:0,bpm:72,type:4}]}};
 if(version!==null)part.version=version;
 return {format:'songsterr',songId:1,revisionId:1,title:'Negative fret mute native fixture',artist:'Synthetic',
  tracks:[{id:0,name:'Unpitched mute fixture',instrumentId:program,tuning}],parts:[part]};
}
function cases(){
 const rows=[],add=(id,beats,options={},extra={})=>rows.push({id:'negative-fret-mute/'+id,expectedDisposition:'rendered',
  source:envelope([bar(beats)],options),...extra});
 for(let string=0;string<6;string++)add('guitar/string'+string,[beat([note(-1,string,{dead:true})])],{capo:4},{nullContrast:true});
 for(let string=0;string<4;string++)add('bass/string'+string,[beat([note(-1,string,{dead:true})])],{program:33,version:8},{nullContrast:true});
 for(const version of [8,null])add('version/'+(version??'omitted'),[beat([note(-1,0,{dead:true})])],{version},{nullContrast:true});
 add('electric/omitted',[beat([note(-1,0,{dead:true})])],{program:29,version:null,capo:2},{nullContrast:true});
 for(const fret of [null,undefined,0,5])add('existing-form/'+(fret===undefined?'missing':fret),[beat([note(fret,0,{dead:true})])],{capo:4});
 const guard=(id,beats,extra={})=>add('guard/'+id,beats,{}, {expectedDisposition:'blocked',guardOnly:true,...extra});
 for(const [label,dead] of [['missing',undefined],['false',false],['null',null],['number',1],['string','true']])
  guard('dead/'+label,[beat([note(-1,0,dead===undefined?{}:{dead})])]);
 for(const [label,fret] of [['negative-two',-2],['fraction',-.5],['string','-1']])guard('fret/'+label,[beat([note(fret,0,{dead:true})])]);
 const bend={points:[{position:0,tone:0},{position:60,tone:100}]};
 for(const [field,value] of [['harmonic','natural'],['bend',bend],['slide','above'],['hp',true],['vibrato',true]])
  guard('note-gesture/'+field,[beat([note(-1,0,{dead:true,[field]:value})]),beat([note(5)])]);
 for(const [field,value] of [['vibrato',true],['wideVibrato',true],['tremoloBar',bend],['vibratoWithTremoloBar','slight']])
  guard('inherited-gesture/'+field,[beat([note(-1,0,{dead:true})],[1,4],{[field]:value})],
   {comparisonScope:'New alias remains guarded; native gesture acceptance does not authorize a pitched expression on an unpitched game instruction.'});
 for(const [label,origin,target] of [['sentinel-sentinel',-1,-1],['null-sentinel',null,-1],['sentinel-null',-1,null],
  ['zero-sentinel',0,-1],['sentinel-zero',-1,0]])
  add('tie/'+label,[beat([note(origin,0,{dead:true})]),beat([note(target,0,{dead:true,tie:true})])]);
 guard('tie/orphan',[beat([note(-1,0,{dead:true,tie:true})])]);
 guard('tie/rest',[beat([note(-1,0,{dead:true})]),beat([{rest:true}],[1,4],{rest:true}),beat([note(-1,0,{dead:true,tie:true})])]);
 guard('tie/string',[beat([note(-1,0,{dead:true})]),beat([note(-1,1,{dead:true,tie:true})])]);
 rows.push({id:'negative-fret-mute/guard/tie/voice',expectedDisposition:'blocked',guardOnly:true,
  source:envelope([{signature:[4,4],voices:[{beats:[beat([note(5)],[1,1])]},
   {beats:[beat([note(-1,0,{dead:true,tie:true})],[1,1])]}]}])});
 add('chord/mixed',[beat([note(-1,0,{dead:true}),note(5,1),note(-1,2,{dead:true})])],{}, {nullContrast:true});
 add('chord/only-mutes',[beat([note(-1,0,{dead:true}),note(-1,2,{dead:true}),note(-1,5,{dead:true})])],{}, {nullContrast:true});
 add('rest-slot',[beat([note(-1,0,{dead:true}),{string:1,rest:true},note(-1,2,{dead:true})])],{}, {nullContrast:true});
 rows.push({id:'negative-fret-mute/voices',expectedDisposition:'rendered',nullContrast:true,
  source:envelope([{signature:[4,4],voices:[{beats:[beat([note(-1,0,{dead:true})],[1,1])]},
   {beats:[beat([note(-1,2,{dead:true})],[1,1])]}]}])});
 rows.push({id:'negative-fret-mute/repeats',expectedDisposition:'rendered',nullContrast:true,
  source:envelope([bar([beat([note(-1,0,{dead:true})],[1,1])],{repeatStart:true}),
   bar([beat([note(-1,2,{dead:true})],[1,1])],{repeat:2})])});
 add('nonpitch-marks',[beat([note(-1,0,{dead:true,ghost:true,accentuated:true})])],{}, {nullContrast:true});
 return rows;
}
function compact(part){
 assert.equal(part.status,'executed');
 return {profile:part.profile,...(part.normalization?{normalization:part.normalization}:{}),tpqn:part.tpqn,
  beats:part.beats,traversal:part.traversal,authoredSlots:part.authoredEvents,heldSlots:part.heldEvents,finalSlots:part.events,
  pitchTargets:part.naturalTargets,noteEvents:part.scheduled.filter(e=>['NOTE_ON','NOTE_OFF'].includes(e.event.name)),
  scheduledSha256:part.scheduledSha256,generatedFinalSha256:part.generatedFinalSha256};
}
function main(){
 const [worker,output,qualificationOutput]=process.argv.slice(2);
 if(!worker||!output||!qualificationOutput||fs.existsSync(output)||fs.existsSync(qualificationOutput))throw Error('Supply reviewed worker, NEW compact fixture and NEW qualification report');
 const rows=cases();assert(rows.length<=64,'Compact fixture case limit');
 const scripts=native.scripts(worker),qualification=[],result=[];
 for(const row of rows){
  const before=JSON.stringify(row.source),reference={},nullContrast={};
  for(const profile of profiles){
   const part=native.nativeCase(scripts,row.source,profile,qualification,row.id).parts[0];
   reference[profile]=compact(part);
   if(row.nullContrast){
    const source=structuredClone(row.source);for(const p of source.parts)for(const m of p.measures)for(const v of m.voices)
     for(const b of v.beats||[])for(const n of b.notes||[])if(n.fret===-1)n.fret=null;
    const control=native.nativeCase(scripts,source,profile,qualification,row.id+'/null-control').parts[0];
    const clock=n=>({id:n.id,occurrence:n.occurrence,bar:n.bar,string:n.string,attackTick:n.attackTick,endTick:n.endTick,hidden:n.hidden,tie:n.tie});
    for(const key of ['authoredEvents','heldEvents'])assert.deepEqual(part[key].map(clock),control[key].map(clock),row.id+'/'+profile+'/'+key+': null control clock differs');
    const noteEvents=reference[profile].noteEvents,other=compact(control).noteEvents;
    const nonPitch=e=>{const {pitch,...rest}=e.event;return {...e,event:rest};};
    const targets=new Map(control.naturalTargets.map(n=>[n.occurrence+':'+n.id,n]));
    const differences=part.naturalTargets.flatMap(n=>{const c=targets.get(n.occurrence+':'+n.id);
     assert(c,'Null control missing source slot');return n.pitch!==c.pitch?[{id:n.id,occurrence:n.occurrence,
      rawPitch:n.pitch,nullPitch:c.pitch}]:[];});
    assert(differences.length>0);assert(differences.every(e=>e.nullPitch-e.rawPitch===1));
    nullContrast[profile]={sameAuthoredAndHeldClock:true,
     sameFinalClock:native.sourceHash(part.events.map(clock))===native.sourceHash(control.events.map(clock)),
     sameNonPitchNoteEvents:native.sourceHash(noteEvents.map(nonPitch))===native.sourceHash(other.map(nonPitch)),
     pitchDifferences:differences,controlFinalSlots:control.events,controlNoteEvents:other,
     controlScheduledSha256:control.scheduledSha256};
   }
  }
  assert.equal(JSON.stringify(row.source),before,'Source input changed');
  result.push({...row,sourceSha256:native.sourceHash(row.source),reference,...(row.nullContrast?{nullContrast}: {})});
 }
 const metadata={version:1,policy:POLICY,node:process.version,referenceSha256:ref.manifest.sha256,
  manifestSha256:ref.digest(fs.readFileSync(require.resolve('./reference-manifest.json'))),
  profileOptions:Object.fromEntries(profiles.map(p=>[p,ref.profiles.options(p,undefined)])),
  scope:'Literal integer-1/deadtrue game interpretation; source clocks and native nominal/emitted pitches are distinct. Complete-worker qualification observes guard cases without granting support. No acoustic, browser rendering or whole-chart acceptance.'};
 const counts={cases:result.length,guardCases:result.filter(r=>r.guardOnly).length,comparisons:qualification.length,
  matched:qualification.filter(r=>r.status==='matched').length,matchingReferenceErrors:qualification.filter(r=>r.status!=='matched').length};
 fs.writeFileSync(qualificationOutput,JSON.stringify({...metadata,...counts,results:qualification},null,2)+'\n',{flag:'wx'});
 fs.writeFileSync(output,JSON.stringify({...metadata,qualification:counts,cases:result})+'\n',{flag:'wx'});
 console.log(JSON.stringify({...counts,fixtureBytes:fs.statSync(output).size,output,qualificationOutput}));
}
module.exports={POLICY,note,beat,bar,envelope,cases};
if(require.main===module)try{main();}catch(e){console.error(e.stack);process.exitCode=2;}

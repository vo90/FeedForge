'use strict';
// Synthetic, serialized source inputs. Beat flags never supply note harmonics.
const tuning=[64,59,55,50,45,40], POLICY='songsterr-legacy-beat-effects-v1';
const note=(fret,string=0,extra={})=>({string,fret,...extra});
const natural=(fret,string=0,extra={})=>note(fret,string,{harmonic:'natural',...extra});
const beat=(notes,duration=[1,4],extra={})=>({type:duration[1],duration,notes,...extra});
const bar=(beats,extra={})=>({signature:[4,4],voices:[{beats}],...extra});
function envelope(measures,{version=5,capo=0,bass=false}={}){
 const t=bass?[43,38,33,28]:tuning,program=bass?33:24;
 const source={format:'songsterr',songId:1,revisionId:1,title:'Legacy beat effects native fixture',artist:'Synthetic',
  tracks:[{id:0,name:bass?'Bass':'Acoustic',instrumentId:program,tuning:t}],
  parts:[{name:bass?'Bass':'Acoustic',instrumentId:program,tuning:t,strings:t.length,frets:24,capo,measures,
   automations:{tempo:[{measure:0,position:0,bpm:72,type:4}]}}]};
 if(version!==null)source.parts[0].version=version;
 return source;
}
function withoutBeatFlags(source){
 const result=structuredClone(source);
 for(const p of result.parts)for(const m of p.measures)for(const v of m.voices)for(const b of v.beats||[]){delete b.harmonic;delete b.fadeIn;}
 return result;
}
function cases(){
 const rows=[];
 const add=(id,measures,options={},extra={})=>rows.push({id:'legacy-beat-effects/'+id,family:'source.legacy_beat_effects',
  expectedDisposition:'rendered',source:envelope(measures,options),...extra});
 for(const version of [5,8,null])for(const capo of [0,4])for(const fret of [4,5,7,9,12,16,19])
  add(`natural/${version??'omitted'}/capo${capo}/fret${fret}`,[bar([beat([natural(fret)],[1,4],{harmonic:true})])],{version,capo},
   {noteInstructionControl:true,nativeEffectScope:fret===19?'Same pitch; native harmonic lookup does not apply.':
    fret===12?'Same pitch; native harmonic instruction applies.':'Distinct harmonic pitch and scheduled events.'});
 for(const bass of [false,true])for(const sparse of [false,true]){
  const strings=bass?(sparse?[0,3]:[0,1,2]):(sparse?[0,2,5]:[0,1,2]);
  add(`chord/${bass?'bass':'guitar'}/${sparse?'sparse':'dense'}`,[bar([beat(strings.map(s=>natural(12,s)),[1,4],{harmonic:true})])],{bass,capo:bass?0:4});
 }
 add('natural/rest-slot',[bar([beat([natural(7),{string:1,rest:true},natural(5,2)],[1,4],{harmonic:true})])]);
 add('natural/voices',[{signature:[4,4],voices:[{beats:[beat([natural(7)],[1,1],{harmonic:true})]},
  {beats:[beat([natural(5,2)],[1,1],{harmonic:true})]}]}]);
 add('natural/repeat',[bar([beat([natural(7)],[1,1],{harmonic:true})],{repeatStart:true}),
  bar([beat([natural(5,2)],[1,1],{harmonic:true})],{repeat:2})]);
 add('natural/tie',[bar([beat([natural(7)],[1,2],{harmonic:true}),beat([natural(7,0,{tie:true})],[1,2],{harmonic:true})])]);
 add('natural/modern-brush',[bar([beat([natural(5,0),natural(7,2),natural(12,5)],[1,4],
  {harmonic:true,brushStroke:{direction:'down',duration:30,shift:100}})])]);
 for(const field of ['harmonic','fadeIn'])for(const value of [false,null])for(const rest of [false,true])
  add(`inactive/${field}/${value}/${rest?'rest':'note'}`,[bar([beat(rest?[{rest:true}]:[note(5)],[1,4],{[field]:value,...(rest?{rest:true}:{})}),
   ...(rest?[beat([note(5)])]:[])])]);
 for(const version of [5,8,null]){
  add(`fade/${version??'omitted'}/plain`,[bar([beat([note(5)],[1,4],{fadeIn:true})])],{version});
  add(`fade/${version??'omitted'}/rest`,[bar([beat([{rest:true}],[1,4],{rest:true,fadeIn:true}),beat([note(5)])])],{version});
 }
 add('fade/natural',[bar([beat([natural(7)],[1,4],{harmonic:true,fadeIn:true})])]);
 add('fade/tie',[bar([beat([note(5)],[1,2],{fadeIn:true}),beat([note(5,0,{tie:true})],[1,2])])]);
 const bend={tone:100,points:[{position:0,tone:0,vibrato:0},{position:15,tone:100,vibrato:0},
  {position:30,tone:100,vibrato:0},{position:45,tone:0,vibrato:0},{position:60,tone:0,vibrato:0}]};
 add('fade/bend',[bar([beat([note(5,1,{bend})],[1,16],{fadeIn:true})])],{capo:4},
  {comparisonScope:'Retained fade flag with one already-supported bend; no differing-fret expressive tie admission.'});
 const guard=(id,beats)=>add('guard/'+id,[bar(beats)],{}, {expectedDisposition:'blocked',guardOnly:true});
 for(const field of ['harmonic','fadeIn'])for(const [name,value] of [['number',1],['string','true'],['array',[]],['object',{}]])
  for(const rest of [false,true])guard(`${field}/${name}/${rest?'rest':'note'}`,
   [beat(rest?[{rest:true}]:[natural(7)],[1,4],{[field]:value,...(rest?{rest:true}:{})})]);
 for(const [id,notes,extra] of [
  ['unmarked',[note(7)],{}],['mixed',[natural(7),note(5,1)],{}],['empty',[],{}],
  ['rest', [{rest:true}],{rest:true}],['only-rest-slots',[{string:0,rest:true}],{}],
  ['dead',[natural(7,0,{dead:true})],{}],['unsupported-node',[natural(6)],{}],
  ['conflicting-data',[natural(7,0,{harmonicData:{type:'ah',shift:12}})],{}],
  ['nonmatching-touch',[natural(7,0,{harmonicFret:12})],{}],['unknown-note',[note(7,0,{harmonic:'unknown'})],{}],
  ['boolean-note-true',[note(7,0,{harmonic:true})],{}],['boolean-note-false',[note(7,0,{harmonic:false})],{}]])
  guard('harmonic/'+id,[beat(notes,[1,4],{harmonic:true,...extra})]);
 // Native synthesis can inherit these tied frets. The import keeps its expressive tie guard.
 guard('fade/bent-zero-tie',[beat([note(5,1,{bend})],[1,16],{fadeIn:true}),beat([note(0,1,{tie:true})],[1,16])]);
 return rows;
}
module.exports={POLICY,note,natural,beat,bar,envelope,withoutBeatFlags,cases};
if(require.main===module){
 const fs=require('node:fs'),output=process.argv[2];
 if(!output||fs.existsSync(output))throw Error('Supply NEW synthetic input output');
 fs.writeFileSync(output,JSON.stringify({version:1,policy:POLICY,cases:cases()})+'\n',{flag:'wx'});
}

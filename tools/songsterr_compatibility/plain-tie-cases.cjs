'use strict';
// Synthetic native inputs; original serialized frets are deliberately retained.
const tuning=[64,59,55,50,45,40];
const note=(fret,string=0,extra={})=>({string,fret,...extra});
const beat=(notes,duration=[1,4],extra={})=>({type:duration[1],duration,notes,...extra});
const bar=(beats,extra={})=>({signature:[4,4],voices:[{beats}],...extra});
const rest=()=>beat([{rest:true}],[1,1],{rest:true});
function envelope(measures,{bass=false,version=5}={}){
 const strings=bass?4:6,t=bass?[43,38,33,28]:tuning,program=bass?33:29;
 const source={format:'songsterr',songId:1,revisionId:1,title:'Plain tie identity native fixture',artist:'Synthetic',
  tracks:[{id:0,name:bass?'Bass':'Lead',instrumentId:program,tuning:t}],
  parts:[{name:bass?'Bass':'Lead',instrumentId:program,tuning:t,strings,frets:24,measures,
   automations:{tempo:[{measure:0,position:0,bpm:65,type:4}]}}]};
 if(version!==null)source.parts[0].version=version;
 return source;
}
function repeatCases(){
 const cases=[];
 for(const [origin,last,tied] of [[7,9,0],[7,9,8],[0,9,0],[7,0,9]]){
  const source=envelope([bar([beat([note(origin)],[1,1])]),bar([beat([note(tied,0,{tie:true})],[1,1])],{repeatStart:true}),
   bar([beat([note(last)],[1,1])],{repeat:2}),bar([beat([note(tied,0,{tie:true})],[1,1])])]);
  cases.push({id:`plain-tie/repeat/visits/${origin}/${last}/${tied}`,source,expectedDisposition:'rendered'});
 }
 const orphan=envelope([bar([beat([note(0,0,{tie:true})],[1,1])],{repeatStart:true}),
  bar([beat([note(9)],[1,1])],{repeat:2})]);
 cases.push({id:'plain-tie/repeat/orphan-first-visit',source:orphan,expectedDisposition:'blocked'});
 const restSource=envelope([bar([beat([note(7)],[1,1])]),bar([beat([note(0,0,{tie:true})],[1,1])],{repeatStart:true}),
  bar([rest()],{repeat:2})]);
 cases.push({id:'plain-tie/repeat/explicit-rest-recovery',source:restSource,expectedDisposition:'blocked'});
 const otherVoice=envelope([bar([beat([note(7)],[1,1])]),{signature:[4,4],repeatStart:true,
  voices:[{beats:[rest()]},{beats:[beat([note(0,0,{tie:true})],[1,1])]}]},
  bar([beat([note(9)],[1,1])],{repeat:2})]);
 cases.push({id:'plain-tie/repeat/cross-voice-orphan',source:otherVoice,expectedDisposition:'blocked'});
 const otherString=envelope([bar([beat([note(7,0)],[1,1])]),bar([beat([note(0,1,{tie:true})],[1,1])],{repeatStart:true}),
  bar([beat([note(9,0)],[1,1])],{repeat:2})]);
 cases.push({id:'plain-tie/repeat/cross-string-orphan',source:otherString,expectedDisposition:'blocked'});
 const ambiguous=envelope([bar([beat([note(7),note(9)],[1,1])]),bar([beat([note(0,0,{tie:true})],[1,1])],{repeatStart:true,repeat:2})]);
 cases.push({id:'plain-tie/repeat/duplicate-string-origin',source:ambiguous,expectedDisposition:'blocked'});
 return cases;
}
function cases(){
 const rows=[];
 function add(id,beats,{bass=false,version=5,disposition='rendered',...extra}={}){
  rows.push({id:'plain-tie/'+id,source:envelope([bar(beats)],{bass,version}),expectedDisposition:disposition,...extra});
 }
 for(const bass of [false,true])for(const version of [5,8,null])for(const origin of [0,7])for(const tied of [0,9])
  add(`basic/${bass?'bass':'guitar'}/${version??'omitted'}/${origin}/${tied}`,
   [beat([note(origin)]),beat([note(tied,0,{tie:true})])],{bass,version});
 for(const bass of [false,true])for(const sparse of [false,true])for(const tied of [0,9]){
  const strings=sparse?(bass?[0,3]:[0,2,5]):[0,1,2];
  add(`chord/${bass?'bass':'guitar'}/${sparse?'sparse':'dense'}/${tied}`,
   [beat(strings.map((s,i)=>note(5+i,s))),beat(strings.map(s=>note(tied,s,{tie:true})))],{bass});
 }
 for(const bass of [false,true]){
  const kind=bass?'bass':'guitar';
  add(`chain/${kind}`,[beat([note(7)]),...([0,9,0].map(f=>beat([note(f,0,{tie:true})])))],{bass});
  add(`triplet-endpoint/${kind}`,[beat([note(7)],[1,6],{type:4,tuplet:3}),beat([note(0,0,{tie:true})],[1,12],{type:8,tuplet:3}),beat([note(9,0,{tie:true})],[1,12],{type:8,tuplet:3})],{bass});
  rows.push({id:`plain-tie/cross-bar/${kind}`,source:envelope([bar([beat([note(7)],[1,1])]),bar([beat([note(0,0,{tie:true})],[1,1])])],{bass}),expectedDisposition:'rendered'});
  add(`unfilled-gap/${kind}`,[beat([note(7)]),beat([note(8,1)]),beat([note(0,0,{tie:true})])],{bass});
  for(const mark of ['note','beat'])add(`origin-vibrato/${kind}/${mark}`,
   [beat([note(7,0,mark==='note'?{vibrato:true}:{})],[1,4],mark==='beat'?{vibrato:true}:{}),beat([note(0,0,{tie:true})])],{bass});
  add(`resolved-incoming-slide/${kind}`,[beat([note(5,0,{slide:'shift'})]),beat([note(7)]),beat([note(0,0,{tie:true})])],{bass});
  add(`resolved-incoming-hp/${kind}`,[beat([note(5,0,{hp:true})]),beat([note(7)]),beat([note(0,0,{tie:true})])],{bass});
  add(`origin-staccato/${kind}`,[beat([note(7,0,{staccato:true})]),beat([note(0,0,{tie:true})])],
   {bass,nativeClockStage:'final',comparisonScope:'Singleton origin-staccato final release; no automatic chord strumming or other synthesis effects.'});
  add(`continuation-staccato/${kind}`,[beat([note(7)]),beat([note(0,0,{tie:true,staccato:true})])],
   {bass,nativeClockStage:'final',comparisonScope:'Singleton continuation-only staccato preserves the native full endpoint.'});
  const strings=bass?[0,1,3]:[1,2,5];
  for(const field of ['upStroke','downStroke'])add(`legacy-brush/${kind}/${field}`,
   [beat(strings.map((s,i)=>note(5+i,s)),[1,4],{[field]:6}),beat(strings.map(s=>note(0,s,{tie:true})),[1,4],{[field]:6})],
   {bass,referenceProfile:'legacy-brush-authored-v1'});
  add(`modern-brush/${kind}`,[beat(strings.map((s,i)=>note(5+i,s)),[1,4],{brushStroke:{direction:'down',duration:30,shift:100}}),
   beat(strings.map(s=>note(0,s,{tie:true})),[1,4],{brushStroke:{direction:'up',duration:30,shift:100}})],{bass});
 }
 rows.push(...repeatCases());
 for(const [id,beats] of [
  ['orphan',[beat([note(0,0,{tie:true})])]],
  ['different-string',[beat([note(7)]),beat([note(0,1,{tie:true})])]],
  ['explicit-rest',[beat([note(7)]),beat([{rest:true}],[1,4],{rest:true}),beat([note(0,0,{tie:true})])]],
  ['duplicate-origin',[beat([note(7),note(9)]),beat([note(0,0,{tie:true})])]],
  ['numeric-tie',[beat([note(7)]),beat([note(0,0,{tie:1})])]],
  ['string-tie',[beat([note(7)]),beat([note(0,0,{tie:'true'})])]]])add('guard/'+id,beats,{disposition:'blocked'});
 const bend={points:[{position:0,tone:0},{position:60,tone:100}]};
 for(const [effect,value] of [['bend',bend],['slide','shift'],['hp',true],['dead',true],['vibrato',true]])
  add('guard/continuation-'+effect,[beat([note(7)]),beat([note(0,0,{tie:true,[effect]:value})]),beat([note(9)])],{disposition:'blocked'});
 add('guard/ancestor-bend',[beat([note(7,0,{bend})]),beat([note(0,0,{tie:true})])],{disposition:'blocked'});
 add('guard/ancestor-dead',[beat([note(7,0,{dead:true})]),beat([note(0,0,{tie:true})])],{disposition:'blocked'});
 add('guard/ancestor-outgoing-slide',[beat([note(7,0,{slide:'shift'})]),beat([note(0,0,{tie:true})]),beat([note(9)])],{disposition:'blocked'});
 add('after-plain/harmonic',[beat([note(9)]),beat([note(0,0,{tie:true})]),beat([note(9,0,{tie:true,harmonic:'artificial',harmonicFret:7})])],
  {comparisonScope:'held_source_clock_only; harmonic projection is separately disclosed'});
 add('after-plain/slide',[beat([note(9)]),beat([note(0,0,{tie:true})]),beat([note(9,0,{tie:true,slide:'shift'})]),beat([note(12)])]);
 return rows;
}
module.exports={note,beat,bar,rest,envelope,repeatCases,cases};

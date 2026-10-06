'use strict';
// Ordinary first-origin bends only; guarded compounds remain observations.
const point=(position,tone)=>({position,tone,vibrato:0});
const curve=(...pairs)=>({points:pairs.map(([p,t])=>point(p,t))});
const curves={release:curve([0,100],[20,100],[40,0],[60,0]),
 raise:curve([0,0],[60,100]),hold:curve([0,100],[60,100]),flat:curve([0,0],[60,0])};
const note=(fret=2,extra={})=>({string:2,fret,...extra});
const beat=(notes,duration=[1,4],extra={})=>({type:duration[1],duration,notes,...extra});
const bar=(beats,extra={})=>({signature:[4,4],voices:[{beats}],...extra});
function envelope(measures,{program=24,version=5,capo=4,tempos=[{measure:0,position:0,bpm:72,type:4}]}={}){
 const tuning=program===33?[43,38,33,28]:[64,59,55,50,45,40];
 const part={name:'Bent tie fixture',instrumentId:program,tuning,strings:tuning.length,frets:24,capo,measures,automations:{tempo:tempos}};
 if(version!==null)part.version=version;
 return {format:'songsterr',songId:1,revisionId:1,title:'Bent origin tie fixture',artist:'Synthetic',
  tracks:[{id:0,name:'Bent tie fixture',instrumentId:program,tuning}],parts:[part]};}
function cases(){const rows=[],add=(id,beats,options={},extra={})=>rows.push({id:'bent-origin-tie/'+id,
 expectedDisposition:'rendered',source:envelope([bar(beats)],options),sameFretControl:true,...extra});
 for(const [name,bend]of Object.entries(curves))for(const fret of [0,2,5])
  add('basic/'+name+'/stored'+fret,[beat([note(2,{bend})]),beat([note(fret,{tie:true})])]);
 for(const [label,options]of [['version8',{version:8}],['omitted',{version:null}],['bass',{program:33}],
  ['electric',{program:29}],['capo0',{capo:0}]])
  add('metadata/'+label,[beat([note(2,{bend:curves.release})]),beat([note(0,{tie:true})])],options);
 add('unequal/long-origin',[beat([note(2,{bend:curves.release})],[3,16]),beat([note(0,{tie:true})],[1,16])]);
 add('unequal/long-tie',[beat([note(2,{bend:curves.raise})],[1,16]),beat([note(5,{tie:true})],[3,16])]);
 add('chain/plain',[beat([note(2,{bend:curves.release})],[1,8]),beat([note(0,{tie:true})],[1,16]),beat([note(5,{tie:true})],[1,16])]);
 add('metadata/marks',[beat([note(2,{bend:curves.release,ghost:true,accentuated:true,leftFingering:'2'})]),
  beat([note(0,{tie:true})])],{}, {allowedMarks:true});
 const tempos=[{measure:0,position:0,bpm:72,type:4},{measure:0,position:1920,bpm:96,type:4},
  {measure:1,position:1920,bpm:120,type:4}];
 rows.push({id:'bent-origin-tie/tempo/across-bars',expectedDisposition:'rendered',sameFretControl:true,
  source:envelope([bar([beat([note(2,{bend:curves.release})],[1,1])]),bar([beat([note(0,{tie:true})],[1,1])])],{tempos})});
 add('tempo/within-group',[beat([note(2,{bend:curves.hold})],[1,2]),beat([note(0,{tie:true})],[1,2])],
  {tempos:tempos.slice(0,2)});
 rows.push({id:'bent-origin-tie/repeat/per-visit-origin',expectedDisposition:'rendered',sameFretControl:true,
  source:envelope([bar([beat([note(2,{bend:curves.release})],[1,1])]),
   bar([beat([note(0,{tie:true})],[1,1])],{repeatStart:true}),
   bar([beat([note(5,{bend:curves.raise})],[1,1])],{repeat:2})])});
 add('chord/independent-bends',[beat([note(2,{bend:curves.release}),{string:1,fret:3,bend:curves.raise}]),
  beat([note(0,{tie:true}),{string:1,fret:0,tie:true}])],{}, {sameFretControl:false});
 rows.push({id:'bent-origin-tie/voices',expectedDisposition:'rendered',sameFretControl:false,
  source:envelope([{signature:[4,4],voices:[{beats:[beat([note(2,{bend:curves.release})],[1,2]),beat([note(0,{tie:true})],[1,2])]},
   {beats:[beat([{string:1,fret:3,bend:curves.raise}],[1,2]),beat([{string:1,fret:0,tie:true}],[1,2])]}]}])});
 const guard=(id,beats,extra={})=>add('guard/'+id,beats,{}, {expectedDisposition:'blocked',guardOnly:true,sameFretControl:false,...extra});
 guard('continuation-bend',[beat([note(2,{bend:curves.release})]),beat([note(0,{tie:true,bend:curves.raise})])]);
 guard('later-controller',[beat([note(2,{bend:curves.release})]),beat([note(0,{tie:true})]),beat([note(2,{tie:true,bend:curves.raise})])]);
 guard('later-origin-bend',[beat([note(2)]),beat([note(2,{tie:true,bend:curves.release})]),beat([note(0,{tie:true})])]);
 guard('negative-curve',[beat([note(2,{bend:curve([0,100],[60,-100])})]),beat([note(0,{tie:true})])]);
 for(const [field,value]of [['vibrato',true],['harmonic','natural'],['dead',true],['slide','upwards'],['hp',true],['staccato',true]])
  guard('continuation/'+field,[beat([note(2,{bend:curves.release})]),beat([note(0,{tie:true,[field]:value})])]);
 for(const field of ['palmMute','letRing','tremolo','tap','slap','pop','staccato','vibrato','hp'])
  guard('origin/'+field,[beat([note(2,{bend:curves.release,[field]:true})]),beat([note(0,{tie:true})])]);
 guard('rest',[beat([note(2,{bend:curves.release})]),beat([],[1,4],{rest:true}),beat([note(0,{tie:true})])]);
 guard('orphan',[beat([note(0,{tie:true})])]);
 guard('other-string',[beat([note(2,{bend:curves.release})]),beat([{string:1,fret:0,tie:true}])]);
 rows.push({id:'bent-origin-tie/guard/gap',expectedDisposition:'blocked',guardOnly:true,sameFretControl:false,
  source:envelope([bar([beat([note(2,{bend:curves.release})])]),bar([beat([note(0,{tie:true})])])])});
 rows.push({id:'bent-origin-tie/guard/cross-voice',expectedDisposition:'blocked',guardOnly:true,sameFretControl:false,
  source:envelope([{signature:[4,4],voices:[{beats:[beat([note(2,{bend:curves.release})],[1,1])]},
   {beats:[beat([note(0,{tie:true})],[1,1])]}]}])});
 return rows;}
function sameFret(source){const used=structuredClone(source);for(const p of used.parts)for(const m of p.measures)for(const v of m.voices)
 for(const b of v.beats||[])for(const n of b.notes||[])if(n.tie)n.fret=2;return used;}
module.exports={cases,sameFret,curves,note,beat,bar,envelope};

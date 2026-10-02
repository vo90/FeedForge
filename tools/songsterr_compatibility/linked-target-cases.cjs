'use strict';
// Small authored examples; no song IDs, names or bars select converter behavior.
function cases() {
 const rows=[];
 for(const slide of ['shift','legato'])for(const fret of [0,3,12])for(const target of ['pitched','unpitched']) {
  const note={string:0,fret,slide};
  const destination=target==='pitched'?{string:0,fret:fret+2}:{string:0,dead:true};
  for(const tied of [false,true]) {
   const beat=n=>({duration:[1,4],type:4,notes:[n]});
   const beats=tied?[beat({string:0,fret}),beat({...note,tie:true}),beat(destination)]:[beat(note),beat(destination)];
   const part={instrumentId:29,strings:6,frets:24,tuning:[64,59,55,50,45,40],
    automations:{tempo:[{measure:0,position:0,bpm:120,type:4}]},
    measures:[{signature:[4,4],voices:[{beats}]}]};
   rows.push({id:`${slide}-${fret}-${target}-${tied?'tied':'attack'}`,target,tied,
    source:{format:'songsterr',songId:1,revisionId:1,title:'Linked target fixture',artist:'Synthetic',
     tracks:[{id:0,name:'Guitar',instrumentId:29,tuning:part.tuning}],parts:[part]}});
  }
 }
 return rows;
}
module.exports={cases};

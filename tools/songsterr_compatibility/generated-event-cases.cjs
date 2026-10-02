'use strict';
// Synthetic sources only. No song identity or retained user tab is shipped.
function cases() {
  const values=[];
  for(const initial of [false,true])for(const continuation of [null,38,31,60]) {
    const trill=speed=>({auxiliaryFret:15,speed});
    const part={instrumentId:25,strings:6,frets:24,tuning:[64,59,55,50,45,40],
      automations:{tempo:[{measure:0,position:0,bpm:120,type:4}]},
      measures:[{signature:[4,4],voices:[{beats:[
        {type:8,duration:[1,8],notes:[{fret:12,string:0,...(initial?{trill:trill(38)}:{})}]},
        {type:32,duration:[1,32],notes:[{fret:12,string:0,tie:true,...(continuation===null?{}:{trill:trill(continuation)})}]},
        {type:4,duration:[1,4],rest:true,notes:[]}
      ]}]}]};
    values.push({id:`tied-generated/${initial}/${continuation}`,source:{tracks:[{id:'0',instrumentId:25}],parts:[part]}});
  }
  return values;
}
module.exports={cases};

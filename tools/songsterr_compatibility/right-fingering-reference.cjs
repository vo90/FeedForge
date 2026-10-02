'use strict';
// Local, pinned development reference. No downloaded/executed code in the app.
const fs = require('node:fs');
const assert = require('node:assert/strict');
const ref = require('./reference.cjs');
const [worker, schema, output] = process.argv.slice(2);
if (!worker || !schema || !output || fs.existsSync(output)) throw Error('Supply pinned worker, schema and a new output path');
const schemaSha256 = '8b9267cd39f7f3b0511bade44de01cf3fe7c8025d5a7d534f8a6de448c940e17';
const bytes = fs.readFileSync(schema);
assert.equal(ref.digest(bytes), schemaSha256);
assert.ok(bytes.toString('utf8').includes('Pre=G([q(`C`),q(`A`),q(`M`),q(`I`),q(`P`)])'));
assert.ok(bytes.toString('utf8').includes('rightFingering:L(Pre)'));
assert.ok(!fs.readFileSync(worker,'utf8').includes('rightFingering'));
const code = ref.prepare(worker), cases=[];
for(const value of [null,'P','I','M','A','C']) {
 const note={fret:5,string:0,leftFingering:'1',...(value===null?{}:{rightFingering:value})};
 const input={instrumentId:24,tuning:[64,59,55,50,45,40],strings:6,frets:24,
  measures:[{signature:[4,4],repeatStart:true,repeat:2,voices:[{beats:[
   {duration:[1,4],type:4,notes:[note,{fret:7,string:1,leftFingering:'3'}]},
   {duration:[1,4],type:4,notes:[{...note,tie:true},{fret:7,string:1,tie:true}]},
   {duration:[1,2],type:2,rest:true,notes:[{rest:true}]}
  ]}]}]};
 const result=ref.runPart(code,input,input.measures,{trace:true});
 const events={beats:result.beats,traversal:result.traversal,authored:result.authoredEvents,final:result.events};
 const performanceSha256=ref.digest(JSON.stringify(events));
 if(cases.length)assert.equal(performanceSha256,cases[0].performanceSha256,'Fingering changed scheduling');
 cases.push({value,performanceSha256,events:result.events.length});
}
fs.writeFileSync(output,JSON.stringify({schemaSha256,referenceSha256:ref.manifest.sha256,
 scope:'Recognized picking-hand annotations leave sampled chord/tie/repeat scheduling unchanged; no engraving or scoring claim.',cases},null,2)+'\n',{flag:'wx'});

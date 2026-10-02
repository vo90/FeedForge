'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {generatedSnapshot,collector}=require('../../tools/songsterr_compatibility/event-trace.cjs');
const event=()=>({name:'NOTE_ON',type:144,time:992,channel:0,pitch:79,velocity:105,shift:0});
function model(){return {progression:[{__ffbar:4,voices:[{beats:[{notes:[{__ffnote:'4:0:0:0',fret:12,string:0,onTick:0,offTick:1919,
  tie:true,isHidden:true,events:[event()],preEvents:[]}]}]}]}]};}
test('a hidden tied segment retains its generated attack and source identity',()=>{
 const rows=generatedSnapshot(model());assert.equal(rows.length,1);
 assert.equal(rows[0].id,'4:0:0:0');assert.equal(rows[0].hidden,true);
 assert.deepEqual(rows[0].events,[event()]);
});
test('later stages cannot mutate earlier event snapshots',()=>{
 const input=model(),before=generatedSnapshot(input);
 input.progression[0].voices[0].beats[0].notes[0].events[0].pitch=12;
 input.progression[0].voices[0].beats[0].notes[0].events.push(event());
 assert.equal(before[0].events[0].pitch,79);assert.equal(before[0].events.length,1);
});

test('captured VM arrays become portable snapshots without realm prototypes',()=>{
 const vm=require('node:vm');
 const native=vm.runInNewContext('('+JSON.stringify(model())+')');
 assert.deepEqual(generatedSnapshot(native),generatedSnapshot(model()));
});
test('scheduler evidence preserves duplicates, priority and insertion order',()=>{
 const c=collector(),one=event();c.record(one,2);c.record(one,1);one.pitch=55;
 assert.deepEqual(c.events.map(e=>[e.ordinal,e.priority,e.event.pitch]),[[0,2,79],[1,1,79]]);
});
test('unknown nested and nonfinite event data is rejected, never normalized away',()=>{
 const c=collector();assert.throws(()=>c.record({...event(),time:NaN},2),/Nonfinite/);
 assert.throws(()=>c.record({...event(),extension:{}},2),/Unqualified/);
 assert.throws(()=>c.record(event(),undefined),/priority/);
 assert.deepEqual(c.events,[]);
});

test('reviewed tied-trill evidence retains hidden emitted attacks in both profiles',()=>{
 const fixture=require('../fixtures/songsterr_generated_event_reference.json');
 const ref=require('../../tools/songsterr_compatibility/reference.cjs');
 const cases=require('../../tools/songsterr_compatibility/generated-event-cases.cjs').cases();
 assert.equal(fixture.referenceSha256,ref.manifest.sha256);
 assert.equal(fixture.cases.length,16);
 for(const profile of ['authored','player-defaults'])for(const source of cases) {
   const row=fixture.cases.find(r=>r.id===source.id&&r.profile===profile);
   assert.equal(row.sourceSha256,ref.digest(JSON.stringify(source.source)));
   const trace=row.generatedEventTrace,hidden=trace.final.find(n=>n.id==='0:0:1:0');
   assert.equal(trace.version,1);assert.equal(hidden.hidden,true);assert.equal(hidden.tie,true);
   assert.equal(trace.stages.Ts.find(n=>n.id===hidden.id).hidden,false);
   const continuation=source.source.parts[0].measures[0].voices[0].beats[1].notes[0].trill;
   const attacks=hidden.events.filter(e=>e.name==='NOTE_ON');
   assert.equal(attacks.length,[31,38].includes(continuation?.speed)?1:0);
   for(const event of attacks)assert.ok(trace.scheduled.some(s=>
     s.event.name===event.name&&s.event.pitch===event.pitch&&s.event.time===event.time));
   trace.scheduled.forEach((event,index)=>assert.equal(event.ordinal,index));
 }
});

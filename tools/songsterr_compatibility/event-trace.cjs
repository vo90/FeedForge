'use strict';
// Development evidence only. Hidden written notes can still emit custom MIDI
// events; neither visibility nor note count establishes the played sequence.
const LIMIT=500000;
function copyEvent(event) {
  if(!event || typeof event!=='object' || Array.isArray(event))throw Error('Invalid reference event');
  const result={};
  for(const [key,value] of Object.entries(event)) {
    if(value!==null && !['number','string','boolean'].includes(typeof value))throw Error('Unqualified reference event field: '+key);
    if(typeof value==='number' && !Number.isFinite(value))throw Error('Nonfinite reference event field: '+key);
    result[key]=value;
  }
  return result;
}
function generatedSnapshot(model) {
  const rows=[];let count=0;
  for(const [occurrence,bar] of model.progression.entries())
    for(const [voiceIndex,voice] of bar.voices.entries())
      for(const [beatIndex,beat] of (voice.beats||[]).entries())
        for(const [noteIndex,note] of (beat.notes||[]).entries()) {
          if(beat.rest || note.rest)continue;
          const events=note.events||[],preEvents=note.preEvents||[];
          count+=events.length+preEvents.length;
          if(count>LIMIT || rows.length>=LIMIT)throw Error('Reference event trace limit');
          rows.push({id:note.source?.__ffnote??note.__ffnote,occurrence,bar:bar.__ffbar,
            voiceIndex,beatIndex,noteIndex,string:note.string,fret:note.fret,
            hidden:!!note.isHidden,tie:!!note.tie,attackTick:note.onTick,endTick:note.offTick,
            events:Array.from(events,copyEvent),preEvents:Array.from(preEvents,copyEvent)});
        }
  return rows;
}
function collector() {
  const events=[];
  return {events,record(event,priority){
    if(events.length>=LIMIT)throw Error('Reference scheduled event limit');
    if(!Number.isInteger(priority))throw Error('Unqualified event priority');
    events.push({ordinal:events.length,priority,event:copyEvent(event)});
  }};
}
// Observe the pinned native emitter without changing event values, multiplicity,
// priority or insertion order. The original addEvent still receives every call.
const observeEmitter='{const original=uo;uo=function(m){const add=m.timeline.addEvent;'+
  'm.timeline.addEvent=function(event,priority){captureScheduled(event,priority);return add.call(this,event,priority);};'+
  'try{return original(m);}finally{m.timeline.addEvent=add;}};}';
module.exports={generatedSnapshot,collector,observeEmitter};

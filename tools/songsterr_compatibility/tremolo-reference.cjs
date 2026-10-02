'use strict';
// Development-only, bounded observations from the locally supplied pinned worker.
const fs = require('node:fs');
const vm = require('node:vm');
const ref = require('./reference.cjs');
const [worker, output] = process.argv.slice(2);
if (!worker || !output || fs.existsSync(output)) throw Error('Supply pinned worker and a new output file');
const code = ref.prepare(worker);
const cases = [];
for (const [id, beatValue, noteValue] of [
  ['none', null, null], ['note16', null, [1,16]], ['note32', null, [1,32]],
  ['noteTrue', null, true], ['noteFalse', null, false], ['beat8', [1,8], null],
  ['both', [1,8], [1,16]], ['beatFalse', false, [1,16]]
]) {
  const notes = [{fret:5, string:0}, {fret:7, string:1}];
  if (noteValue !== null) notes[0].tremolo = noteValue;
  const beat = {duration:[1,4], type:4, notes};
  if (beatValue !== null) beat.tremolo = beatValue;
  const input = {instrumentId:30, tuning:[64,59,55,50,45,40], strings:6, frets:24,
    measures:[{signature:[4,4], voices:[{beats:[beat, {duration:[3,4],rest:true,notes:[{rest:true}]}]}]}]};
  const ctx = vm.createContext({input, console:{warn(){},error(){},log(){}}},
    {codeGeneration:{strings:false,wasm:false}});
  vm.runInContext(code, ctx, {timeout:5000});
  vm.runInContext(`
    globalThis.tpqn=_r(input.measures);
    const native=js;
    js=function(model){
      native(model);
      globalThis.extraNoteOns=[];
      Y(model.progression,(m,v,b,n)=>extraNoteOns.push(n.events.filter(e=>e.name==='NOTE_ON').length));
    };
    mc(input,xc({synth:'fluidsynth',useRSE:false,autoFixJson:false,humanize:false,tpqn}));
  `, ctx, {timeout:5000});
  cases.push({id, beat:beatValue, note:noteValue, extraNoteOns:ctx.extraNoteOns});
}
fs.writeFileSync(output, JSON.stringify({referenceSha256:ref.manifest.sha256,
  scope:'Complete scheduler observation at the tremolo stage, not game/synthesis equivalence', cases}, null, 2)+'\n', {flag:'wx'});

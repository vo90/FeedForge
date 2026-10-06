'use strict';
// Reviewed source is supplied locally and is never downloaded by this tool.
// vm bounds execution time, not OS security. Only the exact reviewed digest is accepted.
const fs = require('node:fs');
const crypto = require('node:crypto');
const vm = require('node:vm');
const path = require('node:path');
const MANIFEST = require('./reference-manifest.json');
const eventTrace = require('./event-trace.cjs');
const profiles = require('./reference-profiles.cjs');
const digest = b => crypto.createHash('sha256').update(b).digest('hex');

function prepare(worker) {
  const bytes = fs.readFileSync(worker);
  if (digest(bytes) !== MANIFEST.sha256) throw Error('Unreviewed Songsterr reference digest');
  const source = bytes.toString('utf8');
  const code = MANIFEST.bindings.map(b => {
    const text = source.slice(b.start, b.end);
    if (digest(text) !== b.sha256) throw Error('Reference binding mismatch: ' + b.name);
    return (b.type === 'VariableDeclarator' ? 'let ' : '') + text + ';';
  }).join('\n');
  return code;
}
function annotate(part) {
  const input = structuredClone(part);
  input.measures.forEach((m, mi) => {
    m.__ffbar = mi;
    (m.voices || []).forEach((v, vi) => (v.beats || []).forEach((b, bi) => {
      b.__ffbeat = mi + ':' + vi + ':' + bi;
      (b.notes || []).forEach((n, ni) => { n.__ffnote = b.__ffbeat + ':' + ni; });
    }));
  });
  return input;
}
function snapshot(model) {
  const rows = [];
  for (const [occurrence, m] of model.progression.entries())
    for (const v of m.voices) for (const b of v.beats || []) for (const n of b.notes || [])
      if (!b.rest && !n.rest) rows.push({
        id: n.source?.__ffnote ?? n.__ffnote, occurrence, bar: m.__ffbar,
        string: n.string, fret: n.fret, sourceFret: n.source?.fret,
        attackTick: n.onTick, endTick: n.offTick, hidden: !!n.isHidden,
        tie: !!n.tie, customEvents: n.events?.length || 0,
        preEvents: n.preEvents?.length || 0
      });
  if (rows.length > 500000) throw Error('Reference event limit');
  return rows;
}
function runPart(code, part, allMeasures, {trace = false, profile = 'authored', annotateIds = true, eventDetails = false, soundDetails = false} = {}) {
  const profileOptions = profiles.options(profile, undefined);
  const input = annotateIds ? annotate(part) : structuredClone(part);
  const warnings = [], stages = {}, generatedStages = {}, scheduled = eventTrace.collector();
  const ctx = vm.createContext({input, allMeasures, stages, profile, profileOptions,
    console: {warn: (...x) => warnings.push(String(x)), error: (...x) => warnings.push(String(x)), log(){}},
    captureScheduled: scheduled.record,
    capture: (name, model) => {
      if (trace || name === 'Ts') stages[name] = snapshot(model);
      if (eventDetails) generatedStages[name] = eventTrace.generatedSnapshot(model);
    }
  }, {codeGeneration: {strings: false, wasm: false}});
  vm.runInContext(code, ctx, {timeout: 5000});
  if(eventDetails)vm.runInContext(eventTrace.observeEmitter,ctx,{timeout:1000});
  vm.runInContext('globalThis.tpqn=_r(allMeasures);globalThis.prepared=Pi(input,{useReprisesCheck:true,tpqn});', ctx, {timeout: 5000});
  // Wrap captured native stages for observation only; mc still determines the complete order.
  vm.runInContext([...new Set([...(trace ? MANIFEST.traceStages : ['Ts']),...(eventDetails?['Is','uo']:[])])].map(name =>
    '{const original=' + name + ';' + name + '=function(model){original(model);capture("' + name + '",model);};}').join('\n'), ctx, {timeout: 1000});
  vm.runInContext('globalThis.model=mc(input,xc({...profileOptions,tpqn}));', ctx, {timeout: 5000});
  const prepared = ctx.prepared;
  const beats = prepared.measures.flatMap((m, mi) => m.voices.flatMap((v, vi) => (v.beats || []).map((b, bi) =>
    ({id: mi + ':' + vi + ':' + bi, quarter: b.startTick / ctx.tpqn,
      duration: b.durationInTicks / ctx.tpqn}))));
  return {status: 'executed', referenceSha256: MANIFEST.sha256, profile,
    ...(profiles.evidence(profile) ? {normalization:profiles.evidence(profile)} : {}),
    tpqn: ctx.tpqn, beats, traversal: prepared.progression,
    authoredEvents: stages.Ts, events: snapshot(ctx.model), stages: trace ? stages : {}, warnings,
    ...(eventDetails ? {generatedEventTrace:{version:1,stages:generatedStages,
      final:eventTrace.generatedSnapshot(ctx.model),scheduled:scheduled.events,
      scope:'Native synthesis event emission, including hidden-note events; not gameplay instructions or acoustic alignment.'}} : {}),
    ...(soundDetails ? {
      soundAutomations: Array.from(ctx.model.soundAutomations || [], e => ({...e})),
      soundBeatDurations: JSON.parse(JSON.stringify(ctx.model.progression.map(m =>
        m.voices.map(v => (v.beats || []).map(b => ({duration:b.duration, startTick:b.startTick}))))))
    } : {}),
    scope: 'Source scheduling only; not acoustic alignment or FeedPak verification.'};
}
function main() {
  const [worker, inputFile, outputFile] = process.argv.slice(2);
  if (!worker || !inputFile || !outputFile) throw Error('Usage: reference.cjs worker.js input.json output.json');
  if (fs.existsSync(outputFile)) throw Error('Refusing to replace an existing reference output');
  if (fs.statSync(inputFile).size > 80*1024*1024) throw Error('Reference input limit');
  const input = JSON.parse(fs.readFileSync(inputFile, 'utf8').replace(/^\uFEFF/, ''));
  const code = prepare(worker), output = {version: 1, referenceSha256: MANIFEST.sha256, node:process.version, cases: []};
  for (const row of input.cases) {
    const source = row.source, allMeasures = source.parts.flatMap(p => p.measures), parts=[];
    for (let i=0;i<source.parts.length;i++) {
      const program=source.tracks[i]?.instrumentId ?? source.parts[i].instrumentId;
      if (!Number.isInteger(program) || program < 24 || program > 39 || source.tracks[i]?.isVocalTrack) continue;
      try { parts.push({index:i, ...runPart(code, source.parts[i], allMeasures, {trace:!!input.trace, profile:input.profile, eventDetails:!!input.eventDetails, soundDetails:!!input.soundDetails})}); }
      catch (e) { parts.push({index:i,status:'reference_error',error:String(e.message)}); }
    }
    output.cases.push({id:row.id, sourceSha256:row.sourceSha256, parts});
  }
  fs.writeFileSync(outputFile, JSON.stringify(output));
}
module.exports={prepare,runPart,snapshot,digest,manifest:MANIFEST,profiles};
if(require.main===module)try{main();}catch(e){console.error(e.message);process.exitCode=2;}

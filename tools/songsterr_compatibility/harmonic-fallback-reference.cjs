'use strict';
// Offline development oracle. No Songsterr source is checked into this repository.
const fs = require('node:fs'), vm = require('node:vm'), path = require('node:path'), assert = require('node:assert/strict');
const ref = require('./reference.cjs');
const whole = require('./legacy-beat-effects-reference.cjs');
const CURRENT_SHA = 'a7f191f19196af972bd61ed5f9c67a6b63151fc8b0b8a1f665061a0cb493c980';
const NODES = [2.4, 2.7, 3.2, 4, 5, 5.8, 7, 8.2, 9, 9.6, 12, 14.7, 16, 17, 19, 21.7, 24];
const POLICY = 'songsterr-harmonic-fallback-audit-v1';
const copy = x => JSON.parse(JSON.stringify(x));

function cases() {
  const rows = [];
  const add = (id, note, disposition, wholeScore = false) => rows.push({id,
    note: {fret: 10, string: 1, ...note}, disposition, wholeScore});
  for (const node of NODES) add('artificial/canonical/' + node,
    {harmonic: 'artificial', harmonicFret: node}, 'canonical-contact', [7, 9.6, 12].includes(node));
  for (const node of [0, 6, 10, 15, 25, 7.0011, 6.9989, 9.6011]) add('artificial/fallback/' + node,
    {harmonic: 'artificial', harmonicFret: node}, 'playback-fallback', [0, 6, 10, 15, 7.0011].includes(node));
  for (const node of [6.9991, 7.0009, 11.9991, 12.0009, 9.5991, 9.6009]) add('artificial/near-node/' + node,
    {harmonic: 'artificial', harmonicFret: node}, 'guarded', node === 7.0009);
  // Exact decimal boundary subtraction has binary floating-point behavior; record
  // the native result rather than assuming <= .001 from decimal spelling alone.
  for (const node of [7.001, 6.999]) add('artificial/exact-boundary/' + node,
    {harmonic: 'artificial', harmonicFret: node}, 'boundary-observation');
  for (const [name, value] of [['null', null], ['numeric-string', '12'], ['false', false],
    ['true', true], ['array', [12]], ['object', {}], ['negative', -1]])
    add('artificial/malformed/' + name, {harmonic: 'artificial', harmonicFret: value}, 'guarded');
  add('artificial/missing', {harmonic: 'artificial'}, 'guarded', true);
  for (const kind of ['pinch', 'semi', 'tapped', 'feedback']) {
    add(kind + '/canonical', {harmonic: kind, harmonicFret: 7}, 'canonical-contact');
    add(kind + '/unknown', {harmonic: kind, harmonicFret: 10}, 'guarded');
    add(kind + '/null', {harmonic: kind, harmonicFret: null}, 'guarded');
    add(kind + '/missing', {harmonic: kind}, kind === 'pinch' ? 'existing-special-case' : 'guarded', kind === 'semi');
  }
  for (const [name, value] of [['unrecognized', 'something-else'], ['boolean', true],
    ['empty', ''], ['false', false], ['null', null], ['tap-alias', 'tap']])
    add('kind/' + name, {harmonic: value, harmonicFret: 12}, 'guarded');
  for (const [name, value] of [['ah-pitch', {type: 'ah', note: 'C', shift: 12}],
    ['ah-shift', {type: 'ah', shift: 12}], ['ah-empty', {type: 'ah'}],
    ['unknown-type', {type: 'future'}], ['empty', {}]])
    add('legacy-data/' + name, {harmonic: 'artificial', harmonicFret: 10, harmonicData: value}, 'guarded');
  add('legacy-data/tapped', {harmonic: 'tapped', harmonicData: {type: 'th', fret: 14}}, 'guarded');
  for (const [fret, harmonicFret] of [[3, 2.7], [7, 7], [12, 12], [19, 19]])
    add('natural/' + fret, {harmonic: 'natural', fret, harmonicFret}, 'existing-special-case');
  return rows;
}

function context(worker, identity) {
  const raw = fs.readFileSync(worker, 'utf8');
  assert.equal(ref.digest(raw), identity === 'current' ? CURRENT_SHA : ref.manifest.sha256,
    'Unreviewed public worker; review source and update the development oracle explicitly');
  const current = identity === 'current';
  const marker = current ? 'function ls(e){let{progression:t,instrument:n}=e' : 'function Qo(e){let{progression:t,instrument:n}=e';
  assert.equal(raw.split(marker).length, 2, 'Unique reviewed harmonic-stage boundary required');
  const injected = raw.replace(marker,
    'globalThis.harmonicAudit={Harmonics,lookup:' + (current ? 'Qn' : 'Hn') + '};' + marker);
  const c = vm.createContext({URL,
    location: {origin: 'https://www.songsterr.com', href: 'https://static3.songsterr.com/reference.js'},
    self: {location: {origin: 'https://www.songsterr.com'}, addEventListener() {}, postMessage() {}},
    console: {log() {}, warn() {}, error() {}},
    fetch() {throw Error('Unexpected network access');}, setTimeout() {throw Error('Unexpected timer');}
  }, {codeGeneration: {strings: false, wasm: false}});
  vm.runInContext(injected, c, {timeout: 5000});
  return c;
}

function perform(c, input) {
  c.input = copy(input);
  return copy(vm.runInContext(`(() => {
    const note = {...input, pitch: 69, originalPitch: 69}, beat = {notes: [note]};
    const performer = new harmonicAudit.Harmonics({synth: 'fluidsynth'});
    let applied, error;
    try {applied = performer.apply(beat, note);} catch (e) {error = String(e.message);}
    const number = value => Number.isFinite(value) ? value : String(value);
    return {applied, ...(error ? {error} : {}),
      lookup: harmonicAudit.lookup.getHarmonicPitchShift(input.harmonicFret),
      tapping: !!beat.tapping,
      notes: beat.notes.map(n => ({fret: n.fret, pitch: number(n.pitch),
        interval: number(n.pitch - 69), disallowOpenString: !!n.disallowOpenString}))};
  })()`, c, {timeout: 1000}));
}

function envelope(note, beatFlag) {
  const tuning = [64, 59, 55, 50, 45, 40];
  return {format: 'songsterr', songId: 1, revisionId: 1, title: 'Harmonic fallback oracle', artist: 'Synthetic',
    tracks: [{id: 0, name: 'Guitar', instrumentId: 30, tuning}],
    parts: [{version: 8, name: 'Guitar', instrumentId: 30, tuning, strings: 6, frets: 24, capo: 0,
      automations: {tempo: [{measure: 0, position: 0, bpm: 120, type: 4}]},
      measures: [{signature: [4, 4], voices: [{beats: [{type: 1, duration: [1, 1], notes: [copy(note)],
        ...(beatFlag !== undefined ? {artificialHarmonic: beatFlag} : {})}]}]}]}]};
}

function main() {
  const [pinnedWorker, currentWorker, output, report] = process.argv.slice(2);
  if (!pinnedWorker || !currentWorker || !output || !report || fs.existsSync(output) || fs.existsSync(report))
    throw Error('Supply pinned worker, reviewed current worker, NEW fixture and NEW report paths');
  for (const destination of [output, report])
    if (!fs.statSync(path.dirname(path.resolve(destination))).isDirectory()) throw Error('Output parent must be an existing directory');
  const pinned = context(pinnedWorker, 'pinned'), current = context(currentWorker, 'current');
  const scripts = whole.scripts(pinnedWorker), qualifications = [], profiles = ['authored', ref.profiles.LEGACY_BRUSH_PROFILE, 'player-defaults'];
  const rows = cases();
  for (const row of rows) {
    row.pinned = perform(pinned, row.note);
    row.current = perform(current, row.note);
    assert.deepEqual(row.pinned, row.current, 'Playback branch changed: ' + row.id);
    if (!row.wholeScore) continue;
    const before = JSON.stringify(row.note), result = {};
    for (const profile of profiles) {
      const original = whole.nativeCase(scripts, envelope(row.note), profile, qualifications, row.id);
      const flagged = whole.nativeCase(scripts, envelope(row.note, true), profile, qualifications, row.id + '/summary');
      assert.deepEqual(original, flagged, 'Beat artificialHarmonic changes complete-worker playback: ' + row.id);
      result[profile] = original.parts.map(p => ({status: p.status, ...(p.error ? {error: p.error} : {}),
        targets: p.naturalTargets?.map(n => ({pitch: n.pitch, fret: n.fret, string: n.string,
          attackTick: n.attackTick, endTick: n.endTick, hidden: n.hidden, tie: n.tie})),
        scheduledSha256: p.scheduledSha256, generatedFinalSha256: p.generatedFinalSha256}));
    }
    assert.equal(JSON.stringify(row.note), before, 'Source note mutated');
    row.completeWorker = result;
  }
  const summary = {cases: rows.length, currentPinnedMatches: rows.length,
    wholeScoreCases: rows.filter(r => r.wholeScore).length, comparisons: qualifications.length,
    matched: qualifications.filter(q => q.status === 'matched').length,
    matchingReferenceErrors: qualifications.filter(q => q.status !== 'matched').length};
  const fixture = {version: 1, policy: POLICY, pinnedWorkerSha256: ref.manifest.sha256,
    currentWorkerSha256: CURRENT_SHA,
    currentWorkerUrl: 'https://static3.songsterr.com/production-main/static3/latest/FluidsynthAudioPlayerWorkerEntry-DYKF5XmaQXwQm0gJ.js',
    limits: 'Complete workers evaluated offline. Direct deterministic fluidsynth Harmonics stage for every row; selected full-score pinned complete/extracted and existing-runner equivalence in all three profiles. No current whole-score, browser, acoustic, real-song or FeedPak acceptance claim. Guarded rows observe source behavior without granting importer support. RSE intermediate MIDI is sample dependent.',
    summary, cases: rows};
  fs.writeFileSync(output, JSON.stringify(fixture, null, 2) + '\n', {flag: 'wx'});
  fs.writeFileSync(report, JSON.stringify({policy: POLICY, summary, qualifications}, null, 2) + '\n', {flag: 'wx'});
  console.log(JSON.stringify(summary));
}

module.exports = {POLICY, NODES, CURRENT_SHA, cases, context, perform, envelope};
if (require.main === module) try {main();} catch (e) {console.error(e.stack); process.exitCode = 2;}

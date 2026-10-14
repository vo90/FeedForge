'use strict';
// Offline qualification only. No vendor code or real-song chart is distributed.
const fs = require('node:fs'), assert = require('node:assert/strict');
const ref = require('./reference.cjs');
const whole = require('./legacy-beat-effects-reference.cjs');
const harmonic = require('./harmonic-fallback-reference.cjs');

function main() {
  const [pinnedPath, currentPath, output] = process.argv.slice(2);
  if (!pinnedPath || !currentPath || !output || fs.existsSync(output))
    throw Error('Supply pinned worker, reviewed current worker and NEW output path');
  const pinned = harmonic.context(pinnedPath, 'pinned'), current = harmonic.context(currentPath, 'current');
  const scripts = whole.scripts(pinnedPath), qualifications = [], rows = [];
  const aliases = {2: 2.4, 3: 3.2, 6: 5.8, 8: 8.2, 10: 9.6, 15: 14.7, 22: 21.7};
  const ordinary = new Set([1, 11, 13, 14, 18, 20, 21, 23]);
  const profiles = ['authored', ref.profiles.LEGACY_BRUSH_PROFILE, 'player-defaults'];
  for (let fret = 0; fret <= 24; fret++) {
    const note = {fret, string: 1, harmonic: 'natural'};
    const a = harmonic.perform(pinned, note), b = harmonic.perform(current, note);
    assert.deepEqual(a, b, 'Current/pinned natural branch ' + fret);
    const row = {fret, direct: a, disposition: ordinary.has(fret) ? 'ordinary-playback'
      : Object.hasOwn(aliases, fret) ? 'precise-shorthand' : 'existing-or-guarded'};
    if (ordinary.has(fret) || Object.hasOwn(aliases, fret)) {
      row.profiles = {};
      for (const profile of profiles) {
        const marked = harmonic.envelope(note), control = structuredClone(marked);
        if (ordinary.has(fret)) delete control.parts[0].measures[0].voices[0].beats[0].notes[0].harmonic;
        else control.parts[0].measures[0].voices[0].beats[0].notes[0].harmonicFret = aliases[fret];
        const first = whole.nativeCase(scripts, marked, profile, qualifications, 'natural/' + fret).parts[0];
        const second = whole.nativeCase(scripts, control, profile, qualifications, 'control/' + fret).parts[0];
        assert.equal(first.status, 'executed'); assert.equal(second.status, 'executed');
        assert.deepEqual(first.scheduled, second.scheduled);
        assert.deepEqual(first.generatedFinal, second.generatedFinal);
        row.profiles[profile] = {equivalent: true, targets: first.naturalTargets,
          scheduledSha256: first.scheduledSha256, generatedFinalSha256: first.generatedFinalSha256};
      }
    }
    rows.push(row);
  }
  assert(qualifications.every(q => q.status === 'matched'));
  const result = {version: 1, policy: 'songsterr-natural-playback-v1',
    pinnedWorkerSha256: ref.manifest.sha256, currentWorkerSha256: harmonic.CURRENT_SHA,
    limits: 'Synthetic natural frets 0..24, standard tuning and capo0. Current complete worker direct Harmonics stage; pinned complete/extracted/runner full-score equivalence for 15 admitted shorthand/ordinary cases across three profiles. No current full-score, acoustic, real-song or game-equivalence claim. Fret0 and unspecified transforms remain guarded; known19/24 remain precise harmonics.',
    summary: {direct: rows.length, fullScorePairs: 45, comparisons: qualifications.length,
      matched: qualifications.filter(q => q.status === 'matched').length}, rows};
  fs.writeFileSync(output, JSON.stringify(result, null, 2) + '\n', {flag: 'wx'});
  console.log(JSON.stringify(result.summary));
}
if (require.main === module) try { main(); } catch (e) { console.error(e.stack); process.exitCode = 2; }

'use strict';
// Offline regeneration using the reviewed, hash-pinned public playback clock.
const fs = require('node:fs');
const assert = require('node:assert/strict');
const [common, vendor, output] = process.argv.slice(2);
if (!common || !vendor || !output || fs.existsSync(output)) throw Error('Supply common, vendor and a NEW fixture path');
const ref = require('./video-clock-reference.cjs').prepare(common, vendor);
const cases = [];
for (const count of [1, 2]) for (const bpm of [90, 120, 157]) {
  const input = { instrumentId: 29, tuning: [64, 59, 55, 50, 45, 40],
    measures: Array.from({ length: 4 }, () => ({ signature: [4, 4],
      voices: [{ beats: [{ duration: [1, 1], notes: [{ string: 0, fret: 3 }] }] }] })),
    automations: { tempo: [{ measure: 0, position: 0, bpm, type: 4 }] } };
  const boundaries = ref.replay(input).boundaries.map(x => x / 1000);
  const points = boundaries.map((_, i) => i <= count ? .53 : .53 + (i - count) * 2.09);
  const samples = [];
  for (let i = 0; i < boundaries.length - 1; i++) for (const fraction of [0, .25, .5, .75]) {
    const score = boundaries[i] + fraction * (boundaries[i + 1] - boundaries[i]);
    samples.push({ score, audio: ref.interpolate(boundaries, points, score) });
  }
  const inverseAtOpening = ref.interpolate(points, boundaries, .53);
  assert.equal(inverseAtOpening, boundaries[count]);
  cases.push({ id: `${count}-bars-${bpm}-bpm`, count, input, boundaries, points, samples, inverseAtOpening });
}
fs.writeFileSync(output, JSON.stringify({ version: 1,
  reference: ref.manifest.map(({ id, sha256 }) => ({ id, sha256 })), cases }, null, 2) + '\n');
console.log(JSON.stringify({ cases: cases.length, output }));

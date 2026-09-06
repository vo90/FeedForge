'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const { selectFileCandidate, sanitizeFileCandidate, normalizeRequirements, validateRequirements } = require('../electron/song-browser/file-selection.cjs');

const rows = (...labels) => labels.map((label, index) => ({ id: `file-${index}`, label }));
const preview = (arrangements, extra = {}) => ({ source_platforms: ['pc'], arrangements, ...extra });
const bass = (extra = {}) => ({ id: 'bass', type: 'bass', tuning: [0, 0, 0, 0, 0, 0], ...extra });
const instrument = (extra = {}) => ({ part: 'bass', family: 'bass', stringCount: 5, ...extra });

test('rich candidate hints remain portable, sanitized and explicitly unknown where missing', () => {
  const input = { id: 'choice-1', label: 'Song_Live_No_Guitar_v2.1_p.psarc', sizeBytes: 4096,
    url: 'https://mega.nz/file/secret#key', providerId: 'private-node', evidence: { version: 'explicit' } };
  const result = sanitizeFileCandidate(input);
  assert.equal(result.platform, 'pc'); assert.equal(result.sizeBytes, 4096); assert.equal(result.versionHint, 'v2.1');
  assert.equal(result.editionHint, 'live'); assert.equal(result.backingHint, 'no-guitar');
  assert.deepEqual(result.evidence, { size: 'observed', version: 'filename_hint', edition: 'filename_hint', backing: 'filename_hint' });
  assert.doesNotMatch(JSON.stringify(result), /https|secret|private-node|providerId/);
  assert.equal(sanitizeFileCandidate(input, { includeId: false }).id, undefined);
  assert.equal(sanitizeFileCandidate({ ...input, label: '../Bad_p.psarc' }), null);
  assert.equal(sanitizeFileCandidate({ ...input, label: 'https:secret_p.psarc' }), null);
  const unknown = sanitizeFileCandidate({ id: 'one', label: 'Song_p.psarc', sizeBytes: Infinity, versionHint: 'https://secret/' });
  for (const key of ['sizeBytes', 'versionHint', 'editionHint', 'backingHint']) assert.equal(unknown[key], null);
  assert.equal(unknown.evidence.backing, 'unknown');
});

test('PC platform orders choices while retired audio preferences preserve provider order', () => {
  const input = rows('Song_Full_Mix_m.psarc', 'Song_No_Bass_v9_p.psarc', 'Song_Full_Mix_v1_p.psarc');
  const result = selectFileCandidate(input);
  assert.equal(result.status, 'choose_file'); assert.equal(result.candidates[0].label, 'Song_No_Bass_v9_p.psarc');
  assert.equal(result.candidates[2].platform, 'mac');
  for (const request of [{ backingTrack: 'full' }, { backingTrack: 'no-bass' }, { requirements: { backingTrack: 'full' } }]) {
    assert.deepEqual(selectFileCandidate(input, request).candidates.map((candidate) => candidate.id), ['file-1', 'file-2', 'file-0']);
  }
  assert.equal(selectFileCandidate(input, { choice: { id: 'file-1' }, backingTrack: 'full' }).candidate.label, 'Song_No_Bass_v9_p.psarc');
  assert.equal(selectFileCandidate(rows('Same_p.psarc', 'Same_p.psarc'), { choice: { label: 'Same_p.psarc', platform: 'pc' } }).status, 'choose_file');
  const standalone = vm.runInNewContext(`(${selectFileCandidate.toString()})(${JSON.stringify(input)}, {backingTrack:'full'})`);
  assert.equal(standalone.candidates[0].label, 'Song_No_Bass_v9_p.psarc');
});

test('audio preferences are retired without warnings or hidden verification requirements', () => {
  const content = preview([bass()]);
  const defaults = normalizeRequirements({}); assert.equal(defaults.backingTrack, 'any'); assert.equal(defaults.backingStrict, false);
  assert.deepEqual(defaults.instrumentRequirements, []);
  const result = validateRequirements(content); assert.equal(result.ok, true); assert.deepEqual(result.warnings, []);
  const strict = { backingTrack: 'no-bass', backingStrict: true };
  for (const evidence of [undefined, 'filename_hint', 'observed', 'user_selected']) {
    const result = validateRequirements({ ...content, backing_track: 'no-bass', backing_track_evidence: evidence }, strict);
    assert.equal(result.ok, true); assert.deepEqual(result.warnings, []);
  }
  assert.equal(validateRequirements({ ...content, backing_track: 'no-bass', backing_track_evidence: 'explicit' }, strict).ok, true);
  assert.equal(validateRequirements({ ...content, backing_track: 'full', backing_track_evidence: 'explicit' }, strict).ok, true);
  assert.equal(validateRequirements(content, { backingTrack: 'any', backingStrict: true }).warnings.length, 0);
});

test('retired instrument and string requirements never block otherwise valid arrangements', () => {
  const request = { instrumentRequirements: [instrument()] };
  for (const arr of [bass(), bass({ string_count: 5 }), bass({ minimum_used_strings: 5 }), bass({ string_count: 5, string_count_evidence: 'filename_hint' })]) {
    const result = validateRequirements(preview([arr]), request); assert.equal(result.ok, true); assert.deepEqual(result.warnings, []);
  }
  const explicit = bass({ instrument_family: 'bass', instrument_family_evidence: 'explicit', string_count: 5, string_count_evidence: 'explicit' });
  assert.equal(validateRequirements(preview([explicit]), request).ok, true);
  assert.equal(validateRequirements(preview([{ ...explicit, string_count: 4 }]), request).ok, true);
  assert.equal(validateRequirements(preview([{ ...explicit, instrument_family: 'guitar' }]), request).ok, true);
  const soft = validateRequirements(preview([bass()]), { instrumentRequirements: [instrument({ strict: false })] });
  assert.equal(soft.ok, true); assert.deepEqual(soft.warnings, []);
});

test('arrangement and tuning requirements remain active after retiring advanced requirements', () => {
  const explicit = { instrument_family: 'bass', instrument_family_evidence: 'explicit', string_count_evidence: 'explicit' };
  const content = preview([bass({ ...explicit, tuning: [0, 0, 0, 0], string_count: 4 }), bass({ ...explicit, tuning: [-2, -2, -2, -2, -2], string_count: 5 })]);
  assert.equal(validateRequirements(content, { parts: ['bass'], tuning: 'E Standard', instrumentRequirements: [instrument()] }).ok, true);
  assert.equal(validateRequirements(content, { parts: ['bass'], tuning: 'D Standard', instrumentRequirements: [instrument()] }).ok, true);
  const retired = { backingTrack: 'no-guitar', backingStrict: true, instrumentRequirements: [{ part: 'lead', family: 'bass', stringCount: null }] };
  assert.equal(validateRequirements(content, { ...retired, parts: ['lead'] }).ok, false);
  assert.equal(validateRequirements(content, { ...retired, parts: ['bass'], tuning: 'Eb Standard' }).ok, false);
  assert.equal(validateRequirements({ ...content, source_platforms: ['mac'] }, retired).ok, false);
});

test('requirement normalization clears legacy advanced values while preserving supported requirements', () => {
  const active = { parts: ['bass'], tuning: 'D Standard', platform: 'pc', strictPlatform: true };
  const value = normalizeRequirements({ ...active, backingTrack: 'no-guitar', backingStrict: true, instrumentRequirements: [instrument({ strict: false })] });
  assert.equal(value.backingTrack, 'any'); assert.equal(value.backingStrict, false); assert.deepEqual(value.instrumentRequirements, []);
  for (const [key, expected] of Object.entries(active)) assert.deepEqual(value[key], expected);
  assert.deepEqual(normalizeRequirements(JSON.parse(JSON.stringify(value))), value);
  for (const request of [{ backingStrict: 'true' }, { instrumentRequirements: 'bass' }, { instrumentRequirements: [instrument({ family: 'drums' })] },
    { instrumentRequirements: [instrument({ stringCount: 7 })] }, { instrumentRequirements: [instrument({ strict: 'false' })] }, { instrumentRequirements: [instrument(), instrument()] }]) {
    assert.deepEqual(normalizeRequirements({ ...active, ...request }), value);
  }
  for (const request of [{ parts: ['vocals'] }, { tuning: [0, '0', 0, 0] }, { platform: 'console' }]) assert.throws(() => normalizeRequirements(request));
});

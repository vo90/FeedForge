'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { sanitizeChoice, sanitizeFileEvidence, normalizeRecipe, recipesCompatible, reuseDecision } = require('../electron/song-browser/provenance.cjs');

const recipe = { version: '0.1.40', build: 'a'.repeat(64), options: { audio: 'vorbis', stems: false } };
const chart = { id: '12', artist: 'Artist', title: 'Song', version: '1', updated: '2026-09-01' };
const choice = { label: 'Song_p.psarc', platform: 'pc' };
const record = { chart, recipe, sourceHash: 'a'.repeat(64), resolvedFile: sanitizeFileEvidence({ ...choice, evidence: { filename: 'observed' } }),
  coverage: { source_platforms: ['pc'], arrangements: [{ id: 'lead', type: 'guitar', tuning: [0, 0, 0, 0, 0, 0],
    instrument_family: 'guitar', instrument_family_evidence: 'explicit', string_count: 6, string_count_evidence: 'explicit' }] } };
const request = { chart, recipe, requirements: { parts: ['lead'], tuning: 'E Standard', strictPlatform: true } };

test('file evidence keeps bounded descriptors and classifications without live identifiers', () => {
  const result = sanitizeFileEvidence({ ...choice, id: 'provider-handle', url: 'https://mega.nz/#key', token: 'secret',
    sizeBytes: 12345, versionHint: 'v2', editionHint: 'live', backingHint: 'no-guitar', backingTrack: 'full',
    evidence: { filename: 'observed', platform: 'filename_hint', backingHint: 'filename_hint' } });
  assert.equal(result.filename, choice.label);
  assert.equal(result.evidence.filename, 'observed');
  assert.equal(result.evidence.versionHint, 'unknown');
  assert.equal(result.backingHint, 'no-guitar');
  assert.doesNotMatch(JSON.stringify(result), /provider-handle|mega.nz|secret|backingTrack/);
  assert.equal(sanitizeChoice({ ...choice, platform: 'mac' }), null);
  assert.equal(sanitizeFileEvidence({ filename: 'https://example.test/Song_p.psarc' }), null);
  assert.equal(sanitizeFileEvidence({ filename: '../Song_p.psarc' }), null);
  const aliases = sanitizeFileEvidence({ ...choice, sizeBytes: 12345, versionHint: 'v2', editionHint: 'live', backingHint: 'no-bass',
    evidence: { label: 'observed', size: 'observed', version: 'filename_hint', edition: 'filename_hint', backing: 'filename_hint' } });
  assert.equal(aliases.evidence.filename, 'observed');
  assert.equal(aliases.evidence.sizeBytes, 'observed');
  assert.equal(aliases.evidence.versionHint, 'filename_hint');
  assert.equal(aliases.evidence.editionHint, 'filename_hint');
  assert.equal(aliases.evidence.backingHint, 'filename_hint');
});

test('recipes identify canonical semantic options and reject unknown/private provenance', () => {
  const canonical = normalizeRecipe(recipe);
  assert.match(canonical.identity, /^[a-f0-9]{64}$/);
  assert.equal(canonical.identity, normalizeRecipe({ ...recipe, identity: 'forged', options: { stems: false, audio: 'vorbis' } }).identity);
  assert.equal(recipesCompatible(recipe, { ...recipe, options: { audio: 'vorbis', stems: false } }), true);
  assert.equal(recipesCompatible(recipe, { ...recipe, options: { audio: 'opus', stems: false } }), false);
  assert.equal(recipesCompatible(recipe, { ...recipe, build: 'b'.repeat(64) }), false);
  assert.equal(recipesCompatible(null, null), false);
  for (const input of [{ version: '1' }, { ...recipe, options: { outputPath: 'C:\\private' } },
    { ...recipe, options: { value: 'https://example.test/key' } }, { ...recipe, options: { password: 'secret' } }]) {
    assert.equal(normalizeRecipe(input), null);
  }
  const tooDeep = { options: {} }; let parent = tooDeep.options;
  for (let i = 0; i < 10; i++) parent = parent.nested = {};
  assert.equal(normalizeRecipe({ ...recipe, ...tooDeep }), null);
});

test('pre-download reuse requires matching variant, revision, recipe and coverage', () => {
  assert.equal(reuseDecision(record, request).reusable, true);
  assert.equal(reuseDecision(record, { ...request, requestedChoice: choice }).reusable, true);
  assert.equal(reuseDecision(record, { ...request, requestedChoice: { label: 'Song_v2_p.psarc', platform: 'pc' } }).code, 'different_file');
  assert.equal(reuseDecision(record, { ...request, reviewAnother: true }).code, 'review_file');
  assert.equal(reuseDecision({ ...record, resolvedFile: null }, request).code, 'unknown_file');
  assert.equal(reuseDecision({ ...record, resolvedFile: sanitizeFileEvidence(choice) }, request).code, 'unknown_file');
  assert.equal(reuseDecision({ ...record, recipe: null }, request).code, 'unknown_recipe');
  assert.equal(reuseDecision(record, { ...request, chart: { ...chart, version: '2' } }).code, 'different_revision');
  assert.equal(reuseDecision(record, { ...request, chart: { ...chart, version: '', updated: '' } }).code, 'unknown_revision');
  assert.equal(reuseDecision(record, { ...request, requirements: { parts: ['bass'] } }).code, 'different_requirements');
});

test('exact source reuse can cross filename/chart revisions but never incompatible conversion or content requirements', () => {
  const after = { ...request, sourceHash: record.sourceHash, requestedChoice: { label: 'Renamed_p.psarc', platform: 'pc' },
    chart: { ...chart, id: '99', version: '2' }, reviewAnother: true };
  assert.equal(reuseDecision(record, after).reusable, true);
  assert.equal(reuseDecision(record, { ...after, sourceHash: 'b'.repeat(64) }).code, 'different_source');
  assert.equal(reuseDecision(record, { ...after, recipe: { ...recipe, version: '2' } }).code, 'different_recipe');
  assert.equal(reuseDecision(record, { ...after, requirements: { parts: ['bass'] } }).code, 'different_requirements');
  assert.equal(reuseDecision({ ...record, recipe: null }, after).code, 'unknown_recipe');
});

test('retired backing requirements cannot block reuse and arrangement checks remain active', () => {
  const hinted = { ...record, resolvedFile: sanitizeFileEvidence({ ...choice, backingHint: 'no-guitar', evidence: { filename: 'observed' } }) };
  assert.equal(reuseDecision(hinted, { ...request, requirements: { backingTrack: 'no-guitar', backingStrict: true } }).reusable, true);
  assert.equal(reuseDecision(hinted, { ...request, requirements: { backingTrack: 'no-guitar', backingStrict: false } }).reusable, true);
  assert.equal(reuseDecision(hinted, { ...request, requirements: { parts: ['bass'], backingTrack: 'no-guitar', backingStrict: true } }).code, 'different_requirements');
});

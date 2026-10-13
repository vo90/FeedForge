'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { playerNotices } = require('../electron/song-browser/songsterr-notices.cjs');

const completed = { state: 'completed', verification: { status: 'passed', timing: 'source_map' } };
const report = { version: 123, status: 'compatible', findings: [] };
const diagnostic = field => `Excluded arrangement notation: Songsterr part field ${field} is retained in source evidence only.`;
const unclassified = field => `Unclassified Songsterr part field ${field} is retained in source evidence only.`;

test('qualified excluded-part notation is diagnostic and raw evidence stays intact', () => {
  const job = { ...completed, warnings: [diagnostic('clef'), diagnostic('grandStaffGroup')] };
  const before = JSON.stringify({ job, report });
  const result = playerNotices(job, report);
  assert.deepEqual(result.warnings, []);
  assert.equal(result.diagnosticCount, 2);
  assert.equal(result.rawWarningCount, 2);
  assert.equal(JSON.stringify({ job, report }), before);
});

test('ordinary notation warnings and malformed title warnings are never blanket-hidden', () => {
  for (const field of ['clef', 'grandStaffGroup', 'title']) {
    const result = playerNotices({ ...completed, warnings: [unclassified(field)] }, report);
    assert.equal(result.warnings.length, 1);
    assert.equal(result.warnings[0].message, unclassified(field));
    assert.equal(result.diagnosticCount, 0);
  }
});

test('an excluded occurrence cannot quiet the same field on a selected arrangement', () => {
  for (const field of ['clef', 'grandStaffGroup']) {
    for (const warnings of [[diagnostic(field), unclassified(field)], [unclassified(field), diagnostic(field)]]) {
      const result = playerNotices({ ...completed, warnings }, report);
      assert.equal(result.warnings.length, 1);
      assert.equal(result.warnings[0].message, unclassified(field));
      assert.equal(result.diagnosticCount, 1);
      assert.equal(result.rawWarningCount, 2);
    }
  }
});

test('unqualified scopes, fields, changed wording and object warnings stay visible', () => {
  const original = diagnostic('clef');
  for (const warning of [diagnostic('title'), diagnostic('futureField'),
    original.replace('Songsterr part', 'Songsterr note'),
    original.replace('Excluded arrangement notation:', 'Arrangement notation:'),
    original + ' Some notes were omitted.', original.slice(0, -1),
    { message: original, location: 'selected arrangement' }]) {
    const result = playerNotices({ ...completed, warnings: [warning] }, report);
    assert.equal(result.warnings.length, 1);
    assert.equal(result.diagnosticCount, 0);
  }
});

test('real compatibility limitations remain visible beside excluded notation diagnostics', () => {
  const finding = { feature: 'notation.future', category: 'unknown_semantics', impact: 'blocking',
    message: 'Selected arrangement has an unsupported notation instruction.' };
  const job = { ...completed, warnings: [diagnostic('clef')] };
  const result = playerNotices(job, { ...report, status: 'blocked', findings: [finding] });
  assert.equal(result.warnings.length, 1);
  assert.equal(result.warnings[0].message, finding.message);
  assert.equal(result.diagnosticCount, 1);
});

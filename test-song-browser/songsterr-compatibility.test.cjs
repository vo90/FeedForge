'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');
const { compatibilityBacklog, compatibilityReport } = require('../electron/song-browser/songsterr-evidence.cjs');
const hash = data => crypto.createHash('sha256').update(data).digest('hex');

test('durable list deduplicates retries, records locations, and replaces resolved gaps', t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'feedforge-compatibility-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  fs.mkdirSync(path.join(root, 'objects')); fs.mkdirSync(path.join(root, 'records'));
  let clock = 100;
  function record(songId, findings, version = 1) {
    const object = value => { const bytes = JSON.stringify(value), id = hash(bytes); fs.writeFileSync(path.join(root, 'objects', id), bytes); return id; };
    const compatibility = object({ version, findings, findingCount: findings.length, status: findings.length ? 'blocked' : 'compatible' });
    const sourceHash = hash(songId), verificationHash = object({ status: 'incomplete' });
    const bytes = JSON.stringify({ version: 4, sourceMetadata: { songId, revisionId: '34', title: 'Original', artist: 'Artist' }, objects: { compatibility, source: sourceHash, verification: verificationHash } });
    const id = hash(bytes), file = path.join(root, 'records', id + '.json');
    fs.writeFileSync(file, bytes); fs.utimesSync(file, ++clock, clock);
    return { version: 4, id, sourceHash, verificationHash };
  }
  const finding = { feature: 'note.future', category: 'unknown_semantics', impact: 'blocking', measure: 5, value: 1 };
  const reference = record('12', [finding, { ...finding, measure: 7 }]);
  record('12', [finding, { ...finding, measure: 7 }]);
  record('13', [finding]);
  let result = compatibilityBacklog(root);
  assert.equal(result.groups[0].affectedSongs, 2); assert.equal(result.groups[0].occurrences, 3);
  assert.deepEqual(compatibilityReport(root, reference).findings.map(x => x.measure), [5, 7]);
  record('12', [], 2);
  record('12', [finding], 1); // An older converter cannot overwrite a newer assessment.
  result = compatibilityBacklog(root);
  assert.equal(result.groups[0].affectedSongs, 1); assert.equal(result.groups[0].occurrences, 1);
  fs.writeFileSync(path.join(root, 'records', reference.id + '.json'), '{}');
  assert.equal(compatibilityBacklog(root).unreadable, 1);
  assert.throws(() => compatibilityReport(root, reference), /changed/);
});

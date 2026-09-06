'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { createDiagnostics } = require('../electron/song-browser/diagnostics.cjs');

test('diagnostics retain only fixed event codes and bounded numeric timing', () => {
  const diagnostics = createDiagnostics({ appVersion: '0.1.40-song-browser.2' });
  assert.equal(diagnostics.record({ code: 'download_failed', stage: 'downloading', host: 'dropbox',
    outcome: 'interrupted', durationMs: 124.7, url: 'https://secret.test/file?signature=secret',
    cookie: 'private session', title: 'private song', error: 'private user error', path: 'C:\\private' }), true);
  assert.deepEqual(diagnostics.report(), { schemaVersion: 1, adapterVersion: 5, appVersion: '0.1.40-song-browser.2', omittedEvents: 0,
    events: [{ elapsedMs: diagnostics.report().events[0].elapsedMs, code: 'download_failed',
      stage: 'downloading', host: 'dropbox', outcome: 'interrupted', durationMs: 125 }] });
  assert.doesNotMatch(JSON.stringify(diagnostics.report()), /private|signature|secret|cookie/);
});

test('arbitrary strings cannot hide in apparently diagnostic fields', () => {
  const diagnostics = createDiagnostics({ appVersion: 'private version text' });
  assert.equal(diagnostics.record({ code: 'private error message' }), false);
  assert.equal(diagnostics.record(null), false);
  diagnostics.record({ code: 'job_failed', stage: 'private stage', host: 'https://private', outcome: 'private error', durationMs: Infinity });
  diagnostics.record({ code: 'output_failed', durationMs: -1 });
  assert.equal(diagnostics.report().appVersion, 'unknown');
  assert.deepEqual(Object.keys(diagnostics.report().events[0]).sort(), ['code', 'elapsedMs']);
  assert.doesNotMatch(JSON.stringify(diagnostics.report()), /private/);
});

test('diagnostics bounds memory, exposes omissions and returns isolated copies', () => {
  const diagnostics = createDiagnostics({ appVersion: '0.1.40', maxEvents: 2 });
  diagnostics.record({ code: 'search_started' });
  diagnostics.record({ code: 'search_finished' });
  diagnostics.record({ code: 'download_started', durationMs: Number.MAX_VALUE });
  const report = diagnostics.report();
  assert.equal(report.omittedEvents, 1); assert.equal(report.events.length, 2);
  assert.equal(report.events[1].durationMs, 86400000);
  report.events[0].code = 'private'; report.events.push({ private: true });
  assert.equal(diagnostics.report().events[0].code, 'search_finished');
  assert.equal(diagnostics.report().events.length, 2);
});

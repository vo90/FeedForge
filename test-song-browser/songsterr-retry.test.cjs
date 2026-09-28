'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { transport, retryAfter, retryPlan, networkTransport } = require('../electron/song-browser/songsterr-retry.cjs');
const FACT = { version: 1, phase: 'audio', operation: 'audio_download', service: 'youtube', reason: 'media_url_expired', status: 403 };
test('unavailable recording requires explicit scheduler eligibility and shares the two credits', () => {
  const fact = { version: 1, phase: 'audio', operation: 'audio_download', service: 'youtube', reason: 'recording_unavailable' };
  assert.deepEqual(transport(fact), fact);
  assert.equal(retryPlan(fact, 0, 1000), null);
  assert.equal(retryPlan(fact, 0, 1000, () => 0, { recordingRecovery: true }).at, 6000);
  assert.equal(retryPlan(fact, 2, 1000, () => 0, { recordingRecovery: true }), null);
  for (const patch of [{ service: 'songsterr' }, { operation: 'audio_probe' }, { status: 403 }, { phase: 'score' }]) assert.equal(transport({ ...fact, ...patch }), null);
});
test('allowlist rejects broad errors, arbitrary statuses and cross-provider 403', () => {
  assert.equal(transport({ code: 'needs_audio' }), null);
  assert.equal(transport({ ...FACT, service: 'songsterr' }), null);
  assert.equal(transport({ ...FACT, operation: 'audio_probe' }), null);
  assert.equal(transport({ ...FACT, reason: 'http' }), null);
  for (const status of [408, 429, 500, 502, 503, 504]) assert.equal(transport({ ...FACT, reason: 'http', status }).status, status);
  assert.deepEqual(transport({ ...FACT, cookies: 'secret', url: 'https://private/token' }), FACT);
  assert.equal(networkTransport(new Error('random timeout'), 'score', 'page_load'), null);
  assert.equal(networkTransport({ cause: { code: 'EAI_AGAIN' } }, 'score', 'score_part').reason, 'temporary_dns');
});
test('shared two-credit policy, positive-only jitter, Retry-After and long-wait parking', () => {
  assert.equal(retryPlan(FACT, 0, 1000, () => 0).at, 6000);
  assert.equal(retryPlan(FACT, 1, 1000, () => 1).at, 22000);
  assert.equal(retryPlan(FACT, 2, 1000), null);
  assert.equal(retryPlan({ ...FACT, retryAfterAt: 600000 }, 0, 1000).parked, true);
  assert.equal(retryPlan({ ...FACT, retryAfterAt: 600000 }, 0, 1000).at, 600000);
  assert.equal(retryAfter('30', 1000), 31000);
  assert.equal(retryAfter('Thu, 01 Jan 1970 00:01:00 GMT', 1000), 60000);
  for (const value of ['invalid', '-1', '', '1e100', null]) assert.equal(retryAfter(value, 1000), undefined);
});

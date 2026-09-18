'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { retrieveSynchronization, selectSynchronization, audioVideo, synchronizationSummary, MAX_SYNC_BYTES, MAX_ENTRIES } = require('../electron/song-browser/providers/songsterr/synchronization.cjs');

const ID = { songId: '123', revisionId: '456', videoId: 'abcdefghijk' };
const ENTRY = { ...ID, id: 'entry-one', revisionToVideoId: 'binding-one', status: 'done', feature: null,
  problematic: null, tracks: null, points: [1.25, 3.25, 5.5] };
const response = (value, status = 200, headers = {}) => new Response(typeof value === 'string' ? value : JSON.stringify(value), { status, headers });

test('timing request uses one anonymous fixed-origin read for the exact revision and video', async () => {
  const requests = [];
  const result = await retrieveSynchronization(ID, { fetch: async (url, options) => {
    requests.push({ url, options }); return response([ENTRY]);
  } });
  assert.equal(result.status, 'done');
  assert.equal(result.source, 'songsterr-video-points');
  assert.deepEqual(result.points, ENTRY.points);
  assert.equal(result.videoId, ID.videoId);
  assert.equal(result.revisionId, ID.revisionId);
  assert.match(result.mapHash, /^[a-f0-9]{64}$/);
  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, 'https://www.songsterr.com/api/video-points/123/456/list');
  assert.equal(requests[0].options.credentials, 'omit');
  assert.equal(requests[0].options.redirect, 'error');
  assert.deepEqual(requests[0].options.headers, { Accept: 'application/json' });
});

test('selection never borrows another song, revision, video, mix or problematic map', () => {
  for (const changed of [
    { songId: '124' }, { revisionId: '457' }, { videoId: 'zzzzzzzzzzz' },
    { feature: 'backing' }, { feature: 'solo' }, { feature: undefined }, { tracks: [0] }, { tracks: [] },
    { status: 'processing' }, { status: 'failed' }, { problematic: true }, { problematic: {} },
    { problematic: [] }, { problematic: 'false' },
  ]) assert.equal(selectSynchronization([{ ...ENTRY, ...changed }], ID).status, 'unavailable', JSON.stringify(changed));
  for (const problematic of [null, false, undefined]) {
    assert.equal(selectSynchronization([{ ...ENTRY, problematic, feature: 'alternative' }], ID).status, 'done');
  }
});

test('conflicting eligible maps are ambiguous; equivalent duplicates are order-independent', () => {
  assert.equal(selectSynchronization([ENTRY, { ...ENTRY, points: [1, 3, 5] }], ID).reasonCode, 'ambiguous');
  assert.equal(selectSynchronization([ENTRY, { ...ENTRY, points: [1.25, 3.25] }], ID).reasonCode, 'ambiguous');
  const alternative = { ...ENTRY, id: 'another', feature: 'alternative', points: [...ENTRY.points] };
  const a = selectSynchronization([ENTRY, alternative], ID), b = selectSynchronization([alternative, ENTRY], ID);
  assert.deepEqual(a, b);
  a.points[0] = 0;
  assert.equal(ENTRY.points[0], 1.25, 'validated maps do not retain a mutable upstream array');
});

test('malformed, missing, oversized and nonmonotonic points never reach the worker as usable', () => {
  for (const points of [null, [], [0], ['0', 2], [null, 2], [0, NaN], [0, Infinity], [0, 0], [2, 1],
    [-86401, 0], [0, 86401], Array.from({ length: 20002 }, (_, i) => i)]) {
    assert.equal(selectSynchronization([{ ...ENTRY, points }], ID).reasonCode, 'invalid_points');
  }
  for (const entries of [{ entries: [ENTRY] }, [null], ['bad'], Array(MAX_ENTRIES + 1).fill(ENTRY)]) {
    assert.equal(selectSynchronization(entries, ID).reasonCode, 'invalid_response');
  }
});

test('negative source pre-roll is preserved for performed-note validation, never clamped', () => {
  const result = selectSynchronization([{ ...ENTRY, points: [-0.38, 1.62, 3.62] }], ID);
  assert.equal(result.status, 'done'); assert.deepEqual(result.points, [-0.38, 1.62, 3.62]);
});

test('metadata/audio identity and local files cannot masquerade as the selected video', () => {
  assert.equal(audioVideo({ kind: 'file', path: 'local.wav', videoId: ID.videoId }), null);
  assert.equal(audioVideo({ kind: 'url', url: 'https://audio.example.test/song.ogg', videoId: ID.videoId }), null);
  assert.equal(audioVideo({ kind: 'url', url: 'https://www.youtube.com/watch?v=abcdefghijk', videoId: 'zzzzzzzzzzz' }), null);
  assert.equal(audioVideo({ kind: 'url', url: 'https://youtu.be/abcdefghijk' }).videoId, ID.videoId);
});

test('HTTP restrictions are local-match diagnostics, never login prompts or host rotation', async () => {
  for (const [status, reasonCode] of [[401, 'restricted'], [403, 'restricted'], [429, 'rate_limited'], [404, 'unavailable'], [503, 'network_error']]) {
    let requests = 0;
    const value = await retrieveSynchronization(ID, { fetch: async () => { requests++; return response({}, status); } });
    assert.equal(value.status, 'unavailable'); assert.equal(value.reasonCode, reasonCode); assert.equal(requests, 1);
    assert.equal(value.canUseAccount, undefined);
  }
  const unavailable = await retrieveSynchronization(ID, { fetch: async () => { throw new Error('Network credentials must not leak'); } });
  assert.equal(unavailable.reasonCode, 'network_error'); assert.doesNotMatch(unavailable.message, /credentials/);
});

test('changed destinations, non-JSON and declared/streaming oversize responses fail closed', async () => {
  const redirected = response([ENTRY]); Object.defineProperty(redirected, 'url', { value: 'https://other.example.test/map' });
  assert.equal((await retrieveSynchronization(ID, { fetch: async () => redirected })).reasonCode, 'restricted');
  assert.equal((await retrieveSynchronization(ID, { fetch: async () => response('<html>Sign in</html>', 200, { 'content-type': 'text/html' }) })).reasonCode, 'invalid_response');
  assert.equal((await retrieveSynchronization(ID, { fetch: async () => response('not json') })).reasonCode, 'invalid_response');
  assert.equal((await retrieveSynchronization(ID, { fetch: async () => response([], 200, { 'content-length': String(MAX_SYNC_BYTES + 1) }) })).reasonCode, 'too_large');
  assert.equal((await retrieveSynchronization(ID, { fetch: async () => response(' '.repeat(MAX_SYNC_BYTES + 1)) })).reasonCode, 'too_large');
});

test('cancellation before and during timing retrieval stops the job instead of falling back', async () => {
  const before = new AbortController(); before.abort();
  await assert.rejects(retrieveSynchronization(ID, { signal: before.signal, fetch: async () => { throw new Error('Must not fetch'); } }), { code: 'cancelled' });
  const during = new AbortController(); let transportSignal;
  const pending = retrieveSynchronization(ID, { signal: during.signal, fetch: async (_url, options) => {
    transportSignal = options.signal; during.abort(); return response([ENTRY]);
  } });
  await assert.rejects(pending, { code: 'cancelled' });
  assert.equal(transportSignal.aborted, true);
});

test('a stalled transport times out once and releases its abort signal', async () => {
  let signal, calls = 0;
  const result = await retrieveSynchronization(ID, { timeoutMs: 5, fetch: async (_url, options) => {
    calls++; signal = options.signal; return new Promise(() => {});
  } });
  assert.equal(result.reasonCode, 'timeout'); assert.equal(calls, 1); assert.equal(signal.aborted, true);
});

test('history summary keeps only bounded provenance, not point arrays', () => {
  const map = selectSynchronization([{ ...ENTRY, points: Array.from({ length: 20001 }, (_, i) => i) }], ID);
  const summary = synchronizationSummary(map);
  assert.equal(summary.pointCount, 20001);
  assert.equal(summary.points, undefined);
  assert.ok(JSON.stringify(summary).length < 256);
});

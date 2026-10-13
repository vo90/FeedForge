'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const { SongsterrProvider } = require('../electron/song-browser/providers/songsterr/browser.cjs');
const { sanitizeAudioDiscovery, sanitizeAudioDiscoveryEvent, createAudioDiscoveryTrace,
  audioDiscoveryErrorReason } = require('../electron/song-browser/songsterr-audio-diagnostics.cjs');

const result = { source: 'songsterr', id: '123', title: 'Song', artist: 'Artist', url: 'https://www.songsterr.com/a/wsa/artist-song-tab-s123' };
const identity = { songId: '123', revisionId: '456' }, pinnedUrl = result.url + '/r456';
const descriptor = { ...result, revisionId: '456', approval: 'approved', pinnedUrl };
const foundEvent = { code: 'audio_probe_found', outcome: 'success' };
const summary = () => ({ version: 1, outcome: 'found', reason: 'audio_probe_found', startedAt: 10, finishedAt: 11,
  events: [foundEvent], truncated: false });

test('discovery sanitizer copies only bounded enum data and strips private properties', () => {
  const dirty = { ...summary(), url: 'https://secret.invalid/account', message: 'PRIVATE', account: 'PRIVATE',
    events: [{ ...foundEvent, pageText: 'PRIVATE', message: 'PRIVATE' }] };
  const clean = sanitizeAudioDiscovery(dirty);
  assert.deepEqual(clean, summary());
  assert.notEqual(clean.events[0], dirty.events[0]);
  assert.equal(sanitizeAudioDiscoveryEvent({ code: 'PRIVATE', outcome: 'ready' }), null);
  assert.equal(sanitizeAudioDiscoveryEvent({ code: foundEvent.code, outcome: 'PRIVATE' }), null);
  const bounded = sanitizeAudioDiscovery({ ...dirty, events: Array(200).fill(dirty.events[0]) });
  assert.equal(bounded.events.length, 16); assert.equal(bounded.truncated, true);
  assert.ok(JSON.stringify(bounded).length < 1600); assert.doesNotMatch(JSON.stringify(bounded), /PRIVATE|secret/);
  const filtered = sanitizeAudioDiscovery({ ...summary(), events: [null, { code: 'PRIVATE', outcome: 'ready' }, foundEvent] });
  assert.deepEqual(filtered.events, [foundEvent]); assert.equal(filtered.truncated, true);
});

test('discovery sanitizer rejects unfamiliar schemas, reasons, outcomes and unsafe timestamps', () => {
  for (const fields of [{ version: 2 }, { version: '1' }, { outcome: 'PRIVATE' }, { reason: 'PRIVATE' }, { events: null },
    { truncated: 'false' }, { startedAt: -1 }, { startedAt: 1.1 }, { startedAt: '10' }, { startedAt: NaN },
    { finishedAt: Infinity }, { finishedAt: 8640000000000001 }, { finishedAt: 9 }]) {
    assert.equal(sanitizeAudioDiscovery({ ...summary(), ...fields }), null, JSON.stringify(fields));
  }
  for (const value of [null, undefined, 0, false, 'PRIVATE']) assert.equal(sanitizeAudioDiscovery(value), null);
});

test('collector is bounded and unknown exceptions are represented by enums, never messages', () => {
  const trace = createAudioDiscoveryTrace();
  for (let count = 0; count < 50; count++) trace.record(foundEvent.code, foundEvent.outcome);
  const value = trace.finish('found', 'audio_probe_found');
  assert.equal(value.events.length, 16); assert.equal(value.truncated, true); assert.ok(value.finishedAt >= value.startedAt);
  assert.equal(audioDiscoveryErrorReason({ code: 'needs_login', message: 'PRIVATE' }), 'needs_login');
  assert.equal(audioDiscoveryErrorReason({ code: 'PRIVATE', message: 'PRIVATE' }), 'failed');
  assert.equal(audioDiscoveryErrorReason({ name: 'AbortError', message: 'PRIVATE' }), 'cancelled');
});

// Keep the provider's resolution, acquisition and audio-selection code real;
// replace only browser/network transport with a deterministic pinned page.
function fixture(options = {}) {
  const actions = [], diagnostics = [], requests = [];
  const page = { status: 'ready', url: pinnedUrl, ...identity, tabReady: true, originalAvailable: true,
    originalSelected: true, sourceControlState: 'original', audioMix: 'main', mixControlState: 'main', mixControlKind: 'select',
    fullMixAvailable: true, canPlay: true, playEnabled: true, playing: false, audio: ['https://www.youtube.com/embed/abcdefghijk'],
    ...options.page };
  const win = { url: pinnedUrl, isDestroyed: () => false, webContents: { getURL: () => win.url } };
  const track = { name: 'Guitar', instrumentId: 29, tuning: [64, 59, 55, 50, 45, 40] };
  const meta = { songId: '123', revisionId: '456', title: 'Song', artist: 'Artist', isPublished: true, image: 'fixture', tracks: [track] };
  const history = [{ ...meta, createdAt: '2026-01-01T00:00:00Z', author: { personId: 10 }, isDeleted: false,
    isBlocked: false, isOnModeration: false, moderationType: 'pre', reviewed: { conclusion: 'approved' } }];
  const part = { tuning: track.tuning, measures: [{ signature: [4, 4], voices: [{ beats: [{ duration: [1, 1], notes: [{ string: 0, fret: 3 }] }] }] }] };
  const provider = Object.assign(Object.create(SongsterrProvider.prototype), {
    disposed: false, searching: false, resolving: false, acquiring: false, controllers: new Set(),
    results: new Map([['123', result]]), resolved: new Map(), onDiagnostic: value => diagnostics.push(value),
    anonymousSession: { fetch: async url => {
      requests.push(url);
      if (url.includes('/video-points/')) return new Response(JSON.stringify(options.sync ?? [{ ...identity,
        videoId: 'abcdefghijk', status: 'done', feature: null, points: [0, 2, 4] }]), { status: options.syncStatus || 200 });
      const data = url.endsWith('/revisions') ? history : url.includes('/api/meta/') ? meta : part;
      return new Response(JSON.stringify(data));
    } },
    _window: () => win,
    _navigate: async (_win, url) => { win.url = page.url = url; },
    _readyPinnedPage: async () => page,
    _revisionHistory: async () => ({ songId: '123', historyReady: true, revisionRows: [{ revisionId: '456', status: 'reviewed' }] }),
    _read: async () => { if (options.readError) throw options.readError; return page; },
    _act: async (_win, action, signal) => {
      actions.push(action);
      if (action === 'pause') { assert.equal(signal, undefined); page.playing = false; if (options.pauseError) throw options.pauseError; }
      if (action === 'play') { page.playing = true; options.onPlay?.(page); if (options.playError) throw options.playError; }
      return { ok: options.actionFailed !== action };
    },
  });
  return { provider, win, page, actions, diagnostics, requests,
    probe: (callback, signal, timeout = 0) => provider._discoverAudio(win, identity, page, signal, timeout, [], callback) };
}

async function directory(t) {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'feedforge-audio-summary-'));
  t.after(() => fs.rm(root, { recursive: true, force: true })); return root;
}

test('found callback is awaited exactly once after pause cleanup before returning the recording', async () => {
  const state = fixture({ page: { playing: true } }), summaries = [];
  let release, entered;
  const gate = new Promise(resolve => { release = resolve; }), callbackStarted = new Promise(resolve => { entered = resolve; });
  let settled = false;
  const running = state.probe(async value => { summaries.push(value); assert.deepEqual(state.actions, ['pause']); entered(); await gate; })
    .then(value => { settled = true; return value; });
  await callbackStarted; assert.equal(settled, false); release();
  assert.equal((await running).videoId, 'abcdefghijk');
  assert.equal(summaries.length, 1); assert.equal(summaries[0].outcome, 'found'); assert.equal(summaries[0].reason, 'audio_probe_found');
  assert.deepEqual(summaries[0].events, [{ code: 'audio_probe_started', outcome: 'started' }, foundEvent]);
});

test('a null result records the concrete absent control without fabricating a failure', async () => {
  const state = fixture({ page: { originalAvailable: false, sourceControlState: 'unknown' } }), summaries = [];
  assert.equal(await state.probe(value => summaries.push(value)), null);
  assert.equal(summaries.length, 1); assert.equal(summaries[0].outcome, 'not_found');
  assert.equal(summaries[0].reason, 'audio_probe_original_unavailable');
  assert.deepEqual(state.actions, []);
});

test('a specific mix failure survives generic fallback and a failed cleanup; detail stays bounded', async () => {
  const state = fixture({ page: { audioMix: 'solo', mixControlState: 'solo', fullMixAvailable: false }, pauseError: new Error('PRIVATE') });
  let captured;
  assert.equal(await state.probe(value => { captured = value; }), null);
  assert.equal(captured.outcome, 'not_found'); assert.equal(captured.reason, 'audio_probe_full_mix_unavailable');
  assert.equal(captured.events.length, 16); assert.equal(captured.truncated, true);
  assert.deepEqual(state.actions, ['pause']); assert.doesNotMatch(JSON.stringify(captured), /PRIVATE/);
});

test('timeout retains its original retryable error and records the exact stage after pause', async () => {
  const state = fixture({ page: { audio: [] } }), summaries = [];
  await assert.rejects(state.probe(value => { assert.deepEqual(state.actions, ['play', 'pause']); summaries.push(value); }),
    error => error.code === 'needs_audio' && error.transport.reason === 'player_timeout');
  assert.equal(summaries.length, 1); assert.equal(summaries[0].outcome, 'failed'); assert.equal(summaries[0].reason, 'audio_probe_iframe_timeout');
});

test('unexpected exceptions keep their identity while the summary strips error content', async () => {
  const original = Object.assign(new Error('PRIVATE https://secret.invalid/token'), { code: 'PRIVATE' });
  const state = fixture({ page: { audio: [] }, playError: original }), summaries = [];
  await assert.rejects(state.probe(value => summaries.push(value)), error => error === original);
  assert.deepEqual(state.actions, ['play', 'pause']); assert.equal(summaries.length, 1);
  assert.equal(summaries[0].outcome, 'failed'); assert.equal(summaries[0].reason, 'failed');
  assert.doesNotMatch(JSON.stringify(summaries), /PRIVATE|secret/);
});

test('cancellation after Play is saved after cleanup and still rejects as cancellation', async () => {
  const controller = new AbortController(), state = fixture({ page: { audio: [] }, onPlay: () => controller.abort() }), summaries = [];
  await assert.rejects(state.probe(value => summaries.push(value), controller.signal, 100), { code: 'cancelled' });
  assert.deepEqual(state.actions, ['play', 'pause']); assert.equal(summaries.length, 1);
  assert.equal(summaries[0].outcome, 'cancelled'); assert.equal(summaries[0].reason, 'cancelled');
});

for (const status of ['needs_attention', 'needs_login', 'revision_unavailable', 'unavailable']) {
  test(`guarded page failure ${status} is retained without page text`, async () => {
    const state = fixture({ page: status === 'revision_unavailable' ? { revisionId: '999' }
      : { status: status === 'unavailable' ? 'unknown' : status } }), summaries = [];
    await assert.rejects(state.probe(value => summaries.push(value)), { code: status });
    assert.equal(summaries.length, 1); assert.equal(summaries[0].outcome, 'failed'); assert.equal(summaries[0].reason, status);
  });
}

test('primary-recording fallback has its own verified and unverified terminal reasons', async () => {
  for (const syncStatus of [200, 503]) {
    const state = fixture({ page: { audioMix: null, mixControlState: 'absent', mixControlKind: 'absent' }, syncStatus }), summaries = [];
    const running = state.probe(value => summaries.push(value));
    if (syncStatus === 200) assert.equal((await running).videoId, 'abcdefghijk');
    else await assert.rejects(running, error => error.code === 'needs_audio' && error.transport.status === 503);
    assert.equal(summaries.length, 1); assert.deepEqual(state.actions, ['play', 'pause']);
    assert.equal(summaries[0].reason, syncStatus === 200 ? 'audio_probe_primary_verified' : 'audio_probe_primary_unverified');
    assert.equal(summaries[0].outcome, syncStatus === 200 ? 'found' : 'failed');
  }
});

test('persistence callback rejection propagates and is not mistaken for another audio probe', async () => {
  const state = fixture({ page: { playing: true } }), expected = new Error('Save failed');
  let count = 0;
  await assert.rejects(state.probe(() => { count++; throw expected; }), error => error === expected);
  assert.equal(count, 1); assert.deepEqual(state.actions, ['pause']);
});

test('initial resolve, first acquire, cached pending acquire and rediscovery carry only their own callback', async t => {
  for (const operation of ['resolve', 'acquire', 'pending_acquire', 'findAudio']) {
    const state = fixture(), captures = [], options = { onAudioDiscovery: value => captures.push(value) };
    if (operation === 'resolve') await state.provider.resolve(result, options);
    else if (operation === 'findAudio') await state.provider.findAudio(result, { ...options, revisionId: '456' });
    else await state.provider.acquire(result, { ...options, directory: await directory(t),
      ...(operation === 'pending_acquire' ? { pinnedDescriptor: { ...descriptor, audioProbePending: true } } : {}) });
    assert.equal(captures.length, 1, operation); assert.equal(captures[0].outcome, 'found', operation);
    assert.equal(state.provider.controllers.size, 0, operation);
  }
});

test('an already probed cached descriptor does not fabricate new discovery history', async t => {
  const state = fixture(), captures = [];
  await state.provider.acquire(result, { directory: await directory(t), pinnedDescriptor: { ...descriptor,
    audio: { kind: 'url', url: 'https://www.youtube.com/watch?v=abcdefghijk' } }, onAudioDiscovery: value => captures.push(value) });
  assert.deepEqual(captures, []); assert.deepEqual(state.actions, []);
});

test('operation callbacks remain separate when another probe completes while the first callback waits', async () => {
  const state = fixture(), first = [], second = [];
  let release, arrived;
  const gate = new Promise(resolve => { release = resolve; }), waiting = new Promise(resolve => { arrived = resolve; });
  const a = state.probe(async value => { first.push(value); arrived(); await gate; });
  await waiting;
  Object.assign(state.page, { originalAvailable: false, sourceControlState: 'unknown' });
  assert.equal(await state.probe(value => second.push(value)), null);
  release(); await a;
  assert.equal(first.length, 1); assert.equal(second.length, 1);
  assert.equal(first[0].reason, 'audio_probe_found'); assert.equal(second[0].reason, 'audio_probe_original_unavailable');
  assert.equal(first[0].events.some(event => event.code === 'audio_probe_original_unavailable'), false);
  assert.equal(second[0].events.some(event => event.code === 'audio_probe_found'), false);
});

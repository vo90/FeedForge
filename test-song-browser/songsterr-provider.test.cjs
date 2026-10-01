'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const syncFs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { EventEmitter } = require('node:events');
const { SongsterrProvider } = require('../electron/song-browser/providers/songsterr/index.cjs');
const { acquireAnonymous, partUrl, validateMeta, validatePart } = require('../electron/song-browser/providers/songsterr/acquire.cjs');
const { allowedNavigation, allowedDownload, songUrl, publicAudio, sourceFilename } = require('../electron/song-browser/providers/songsterr/policy.cjs');

const result = { id: '564073', source: 'songsterr', title: 'Woodland Rites', artist: 'Green Lung', url: 'https://www.songsterr.com/a/wsa/green-lung-woodland-rites-tab-s564073' };
const descriptor = { ...result, revisionId: '2585330', approval: 'approved', approvedUrl: result.url + '/r2585330' };
const track = { name: 'Eight string guitar', instrument: 'Overdriven Guitar', instrumentId: 29, tuning: [64, 59, 55, 50, 45, 40, 35, 30] };
const part = { tuning: track.tuning, automations: { tempo: [{ measure: 0, position: [0, 1], bpm: 120, type: 4 }] }, measures: [{ signature: [4, 4], voices: [{ beats: [{ duration: [1, 1], notes: [{ string: 7, fret: 3 }] }] }] }] };
const meta = { songId: result.id, revisionId: descriptor.revisionId, image: 'fixture-image', title: result.title, artist: result.artist, tracks: [track, { ...track, name: 'Second guitar' }] };
const response = (value, status = 200, headers = {}) => new Response(typeof value === 'string' ? value : JSON.stringify(value), { status, headers });
async function temporary(t) { const root = await fs.mkdtemp(path.join(os.tmpdir(), 'feedforge-songsterr-provider-')); t.after(() => fs.rm(root, { recursive: true, force: true })); return root; }

test('public URL policy admits only exact Songsterr pages and known export origins', () => {
  assert.equal(allowedNavigation(result.url), true);
  for (const url of ['http://www.songsterr.com/', 'https://songsterr.com.evil.test/', 'https://user@www.songsterr.com/', 'file:///C:/secret', 'https://accounts.google.com/']) assert.equal(allowedNavigation(url), false);
  assert.equal(songUrl(result.url + '/r2585330?open=editor').revisionId, '2585330');
  assert.equal(songUrl(result.url + '/r0'), null);
  assert.equal(allowedDownload('blob:https://www.songsterr.com/123'), true);
  assert.equal(allowedDownload('blob:https://evil.test/123'), false);
  assert.deepEqual(publicAudio('https://www.youtube-nocookie.com/embed/abcdefghijk?start=50'), { kind: 'url', url: 'https://www.youtube.com/watch?v=abcdefghijk', videoId: 'abcdefghijk' });
  assert.equal(publicAudio('https://www.youtube.com.evil.test/watch?v=abcdefghijk'), null);
  assert.equal(sourceFilename('AC/DC', 'Song?'), 'AC_DC - Song_.gp');
});

test('anonymous adapter preserves all tracks, lowest strings and exact approved revision using no credentials', async (t) => {
  const directory = await temporary(t), calls = [];
  const fetch = async (url, options) => { calls.push({ url, options }); return response(calls.length === 1 ? meta : part); };
  const out = await acquireAnonymous(descriptor, { fetch, directory });
  assert.equal(out.format, 'songsterr'); assert.equal(out.metadata.revisionId, '2585330');
  const score = JSON.parse(await fs.readFile(out.path, 'utf8'));
  assert.equal(score.tracks.length, 2); assert.equal(score.parts.length, 2);
  assert.equal(score.parts[0].measures[0].voices[0].beats[0].notes[0].string, 7);
  assert.equal(score.tracks[0].tuning.length, 8);
  assert.equal(calls.length, 3);
  for (const call of calls) { assert.equal(call.options.credentials, 'omit'); assert.equal(call.options.redirect, 'error'); assert.equal(call.options.headers.Cookie, undefined); assert.equal(call.options.headers.Authorization, undefined); }
  assert.equal(calls[0].url, 'https://www.songsterr.com/api/meta/564073/2585330');
  assert.equal(calls[1].url, partUrl('564073', '2585330', 'fixture-image', 0));
});

test('anonymous adapter fails the entire acquisition on missing tracks; it never rotates hosts', async (t) => {
  const directory = await temporary(t); const urls = [];
  await assert.rejects(acquireAnonymous(descriptor, { directory, fetch: async (url) => { urls.push(url); return response(urls.length === 1 ? meta : {}, urls.length < 3 ? 200 : 403); } }), { code: 'incomplete_score' });
  assert.equal(urls.length, 2); assert.deepEqual(await fs.readdir(directory), []);
  const directory2 = path.join(directory, 'again'); await fs.mkdir(directory2); urls.length = 0;
  await assert.rejects(acquireAnonymous(descriptor, { directory: directory2, fetch: async (url) => { urls.push(url); return response(urls.length === 1 ? meta : part, urls.length < 3 ? 200 : 403); } }), { code: 'access_denied' });
  assert.equal(urls.length, 3); assert.equal(new Set(urls.filter((url) => url.includes('cloudfront')).map((url) => new URL(url).hostname)).size, 1);
  assert.deepEqual(await fs.readdir(directory2), []);
});

test('anonymous metadata mismatch, HTTP restrictions and network failures remain distinct', async (t) => {
  assert.throws(() => validateMeta({ ...meta, revisionId: '999' }, descriptor), { code: 'revision_unavailable' });
  assert.throws(() => validateMeta({ ...meta, tracks: [] }, descriptor), { code: 'invalid_score' });
  assert.throws(() => validatePart(part, track, 2), { code: 'incomplete_score' });
  const directory = await temporary(t);
  for (const [status, code] of [[401, 'needs_login'], [403, 'access_denied'], [429, 'rate_limited'], [404, 'unavailable'], [503, 'network_error']]) {
    await assert.rejects(acquireAnonymous(descriptor, { directory, fetch: async () => response({}, status) }), { code });
  }
  await assert.rejects(acquireAnonymous(descriptor, { directory, fetch: async () => { throw new Error('offline'); } }), { code: 'network_error' });
  assert.deepEqual(await fs.readdir(directory), []);
});

test('anonymous retrieval carries retry-after and only known transport failures are eligible', async t => {
  const directory = await temporary(t);
  await assert.rejects(acquireAnonymous(descriptor, { directory, fetch: async () => response({}, 429, { 'Retry-After': '30' }) }), error =>
    error.transport.status === 429 && error.transport.retryAfterAt > Date.now() && error.transport.operation === 'score_metadata');
  await assert.rejects(acquireAnonymous(descriptor, { directory, fetch: async () => response({}, 403) }), error => !error.transport);
  await assert.rejects(acquireAnonymous(descriptor, { directory, fetch: async () => { throw Object.assign(new Error('network'), { cause: { code: 'ECONNRESET' } }); } }), error => error.transport.reason === 'connection_reset');
});

test('mismatched revision never downloads parts or offers account copying', async (t) => {
  const root = await temporary(t), state = runtime(root);
  const directory = path.join(root, 'score'); await fs.mkdir(directory);
  await state.provider.search({ query: 'Green Lung' });
  let calls = 0;
  state.provider.anonymousSession.fetch = async (url) => {
    calls++;
    assert.equal(url, `https://www.songsterr.com/api/meta/${descriptor.id}/${descriptor.revisionId}`);
    return response({ ...meta, revisionId: '999' });
  };
  await assert.rejects(state.provider.acquire(result, { directory }), (error) => {
    assert.equal(error.code, 'revision_unavailable');
    assert.equal(error.canUseAccount, false);
    assert.match(error.message, /999.*2585330/);
    return true;
  });
  assert.equal(calls, 1);
  assert.deepEqual(await fs.readdir(directory), []);
  state.provider.dispose();
});

test('anonymous adapter rejects large/HTML responses and honors cancellation', async (t) => {
  const directory = await temporary(t);
  await assert.rejects(acquireAnonymous(descriptor, { directory, fetch: async () => response({}, 200, { 'content-length': String(256 * 1024 * 1024) }) }), { code: 'score_too_large' });
  await assert.rejects(acquireAnonymous(descriptor, { directory, fetch: async () => response('<html>Please log in</html>') }), { code: 'invalid_score' });
  const controller = new AbortController(); controller.abort(); let calls = 0;
  await assert.rejects(acquireAnonymous(descriptor, { directory, signal: controller.signal, fetch: async () => { calls++; return response(meta); } }), { name: 'AbortError' });
  assert.equal(calls, 0);
});

function runtime(root, options = {}) {
  const sessions = [], windows = [], actions = [], requests = [], partitions = [], diagnostics = [];
  function makeSession() {
    const value = new EventEmitter();
    value.setPermissionRequestHandler = (handler) => { value.permission = handler; };
    value.setPermissionCheckHandler = (handler) => { value.checkPermission = handler; };
    value.fetch = async (url, settings) => { requests.push({ url, settings }); return response(requests.length % 3 === 1 ? meta : part); };
    sessions.push(value); return value;
  }
  const session = { fromPartition: (name, config) => { partitions.push({ name, config }); return makeSession(); }, fromPath: (profile) => { assert.equal(profile, path.join(root, 'profile')); return makeSession(); } };
  class Window extends EventEmitter {
    constructor(config) {
      super(); this.config = config; this.shown = false; this.destroyed = false;
      this.webContents = new EventEmitter(); const wc = this.webContents; wc.url = ''; wc.page = {}; wc.setAudioMuted = (value) => { wc.muted = value; }; wc.stop = () => {};
      wc.setWindowOpenHandler = (fn) => { wc.popup = fn; }; wc.getURL = () => wc.url;
      wc.loadURL = async (url) => {
        wc.url = url; wc.controlReads = 0; wc.historyReads = 0; wc.mixerReads = 0; wc.audioControlReads = 0; wc.playerReads = 0; wc.playerPending = false; wc.historyPending = false; const parsed = songUrl(url);
        if (url.includes('pattern=')) wc.page = options.challengeOnSearch ? { status: 'needs_attention' } : {
          status: 'ready', url, canSearch: true, searchQuery: new URL(url).searchParams.get('pattern'),
          searchInput: new URL(url).searchParams.get('pattern'), searchReady: true, results: [result], hasMore: false };
        else if (parsed?.id === '99999') wc.page = { status: 'ready', url, songId: '99999', unpublished: true, editor: true };
        else wc.page = { status: 'ready', url, songId: parsed?.id, revisionId: parsed?.revisionId, canOpenHistory: Boolean(parsed), tabReady: Boolean(parsed),
          signedOut: config.webPreferences.session === sessions[1] && options.signedOut === true,
          audio: options.lazyAudio ? [] : ['https://www.youtube.com/embed/' + (options.initialMix === 'backing' ? 'zzzzzzzzzzz' : 'abcdefghijk')],
          originalAvailable: !options.noOriginal, originalSelected: !options.initialSynth, audioMix: options.initialMix || 'main', fullMixAvailable: true,
          canPlay: true, playing: false };
      };
      wc.mainFrame = { executeJavaScript: async (script) => {
        if (script.includes('function readSongsterrPage')) {
          if (options.delayedHistory && wc.page.songId && !wc.page.historyVisible && wc.controlReads++ < 1) return { ...wc.page, canOpenHistory: false };
          if (wc.historyPending && wc.historyReads++ < 1) return { ...wc.page, historyReady: false, approvedRevisions: [] };
          if (options.delayedMixer && wc.page.revisionId && wc.mixerReads++ < 1) return { ...wc.page, tabReady: false };
          if (options.delayedAudioControls && wc.page.revisionId && wc.audioControlReads++ < 2) return { ...wc.page, originalAvailable: false, originalSelected: false, canPlay: false };
          if (options.delayedPlayControl && wc.page.revisionId && wc.audioControlReads++ < 2) return { ...wc.page, canPlay: false };
          if (wc.playerPending && !options.noAudio && wc.playerReads++ >= (options.delayedAudio ? 1 : 0)) {
            wc.page.audio = ['https://www.youtube.com/embed/' + (options.fallbackVideo || 'abcdefghijk')]; wc.playerPending = false;
          }
          return wc.page;
        }
        const request = JSON.parse(script.slice(script.lastIndexOf(')(') + 2, -1)); actions.push(request.action);
        if (['selectOriginal', 'selectFullMix', 'play', 'pause'].includes(request.action)) {
          if (request.songId !== wc.page.songId || request.revisionId !== wc.page.revisionId) return { ok: false, reason: 'revision_changed' };
          if (request.action === 'selectOriginal') wc.page.originalSelected = true;
          if (request.action === 'selectFullMix') wc.page.audioMix = 'main';
          if (request.action === 'pause') wc.page.playing = false;
          if (request.action === 'play') {
            assert.equal(wc.muted, true); assert.equal(wc.page.originalSelected, true); assert.equal(wc.page.audioMix, 'main');
            wc.page.playing = true; wc.playerPending = true;
            if (options.afterPlayStatus) wc.page.status = options.afterPlayStatus;
            if (options.changedRevision) { wc.page.revisionId = '999'; wc.url = result.url + '/r999'; wc.page.url = wc.url; }
            options.onPlay?.();
          }
        }
        if (request.action === 'history') {
          if (options.delayedHistory) { assert.ok(wc.controlReads >= 2, 'wait for rendered history controls'); wc.historyPending = true; }
          wc.page = { ...wc.page, historyVisible: true, historyReady: true, approvedRevisions: options.noApproved ? [] : [{ revisionId: '626617', approval: 'approved', date: '10/8/2023' }, { revisionId: '2585330', approval: 'approved', date: '7/31/2025' }] };
        }
        if (request.action === 'copy') wc.page = { ...wc.page, copyForm: true };
        if (request.action === 'create') {
          if (options.uncertainCreate) throw new Error('lost create response');
          wc.url = 'https://www.songsterr.com/a/wsa/unpublished-copy-tab-s99999?open=editor';
          wc.page = { status: 'ready', url: wc.url, songId: '99999', unpublished: true, editor: true };
        }
        if (request.action === 'exportMenu') wc.page.canExport = true;
        if (request.action === 'export') {
          if (options.exportMenu && !wc.page.canExport) return { ok: false, reason: 'control_unavailable' };
          const item = new EventEmitter(); item.getURL = () => 'blob:https://www.songsterr.com/fixture'; item.getFilename = () => 'Misc Covers-Woodland Rites.gp';
          item.getTotalBytes = () => 64; item.getReceivedBytes = () => 64; item.cancel = () => {};
          item.setSavePath = (file) => { syncFs.writeFileSync(file, Buffer.concat([Buffer.from([80, 75, 3, 4]), Buffer.alloc(60)])); };
          const event = { preventDefault() { event.prevented = true; } };
          sessions[1].emit('will-download', event, item, wc); assert.equal(event.prevented, undefined);
          queueMicrotask(() => item.emit('done', {}, 'completed'));
        }
        return { ok: true };
      } };
      windows.push(this);
    }
    isDestroyed() { return this.destroyed; } destroy() { this.destroyed = true; this.emit('closed'); } show() { this.shown = true; } hide() { this.shown = false; } focus() {}
  }
  return { provider: new SongsterrProvider({ BrowserWindow: Window, session, profilePath: path.join(root, 'profile'), onDiagnostic: (event) => diagnostics.push(event) }), sessions, windows, actions, requests, partitions, diagnostics };
}

test('provider search, approved resolution and anonymous acquisition use an isolated hidden browser', async (t) => {
  const root = await temporary(t), state = runtime(root); t.after(() => state.provider.dispose());
  assert.match(state.partitions[0].name, /^songsterr-anonymous-/); assert.doesNotMatch(state.partitions[0].name, /^persist:/);
  assert.equal(state.sessions[0].checkPermission(), false);
  const search = await state.provider.search({ query: 'green lung' }); assert.equal(search.results[0].id, result.id);
  const pinned = await state.provider.resolve(search.results[0]); assert.equal(pinned.revisionId, descriptor.revisionId);
  assert.equal(pinned.audio.videoId, 'abcdefghijk');
  const directory = path.join(root, 'job'); await fs.mkdir(directory);
  const out = await state.provider.acquire(pinned, { directory }); assert.equal(out.format, 'songsterr');
  assert.equal(state.windows.length, 1); assert.equal(state.windows[0].shown, false);
  assert.equal(state.windows[0].config.webPreferences.nodeIntegration, false); assert.equal(state.windows[0].config.webPreferences.sandbox, true);
  assert.equal(state.windows[0].config.webPreferences.contextIsolation, true); assert.equal(state.windows[0].config.webPreferences.preload, undefined);
  assert.equal(state.actions.includes('create'), false); assert.equal(state.requests.length, 3);
});

test('provider retrieves timing anonymously for a trusted cached revision without loading a page', async (t) => {
  const state = runtime(await temporary(t)); t.after(() => state.provider.dispose());
  state.provider.restoreTrustedResult(result);
  const requests = [];
  state.sessions[0].fetch = async (url, settings) => {
    requests.push({ url, settings });
    return response([{ songId: result.id, revisionId: descriptor.revisionId, videoId: 'abcdefghijk',
      feature: null, status: 'done', problematic: null, points: [0, 2, 4] }]);
  };
  state.sessions[1].fetch = async () => { throw new Error('Account session must not be used'); };
  const audio = { kind: 'url', url: 'https://www.youtube.com/watch?v=abcdefghijk' };
  const sync = await state.provider.findSynchronization(result, { revisionId: descriptor.revisionId, audio });
  assert.equal(sync.status, 'done'); assert.equal(sync.videoId, 'abcdefghijk');
  assert.equal(requests.length, 1); assert.equal(requests[0].settings.credentials, 'omit');
  assert.equal(state.windows.length, 0); assert.equal(state.actions.length, 0);
  assert.equal((await state.provider.findSynchronization(result, { revisionId: descriptor.revisionId,
    audio: { kind: 'file', path: 'replacement.wav' } })).reasonCode, 'unsupported_audio');
  assert.equal((await state.provider.findSynchronization({ ...result, id: '999' }, { revisionId: descriptor.revisionId, audio })).reasonCode, 'identity_mismatch');
  assert.equal(requests.length, 1, 'unbound recordings and songs do not trigger timing requests');
});

test('provider will not resolve a renderer-supplied song that was not searched', async (t) => {
  const root = await temporary(t), state = runtime(root); t.after(() => state.provider.dispose());
  await assert.rejects(state.provider.resolve(result), { code: 'invalid_result' }); assert.equal(state.windows.length, 0);
});

test('revision resolution waits for rendered controls and populated history rather than accepting the URL or empty list', async (t) => {
  const root = await temporary(t), state = runtime(root, { delayedHistory: true }); t.after(() => state.provider.dispose());
  await state.provider.search({ query: 'green lung' });
  const pinned = await state.provider.resolve(result);
  assert.equal(pinned.revisionId, descriptor.revisionId);
  assert.equal(state.actions.filter((action) => action === 'history').length, 1);
  assert.equal(state.requests.length, 0);
});

test('loaded history without an approved matching revision reports a distinct approval failure', async (t) => {
  const root = await temporary(t), state = runtime(root, { noApproved: true }); t.after(() => state.provider.dispose());
  await state.provider.search({ query: 'green lung' });
  await assert.rejects(state.provider.resolve(result), { code: 'unapproved_revision', message: 'Songsterr’s revision history loaded, but no approved revision with a matching tab link could be verified.' });
  assert.equal(state.requests.length, 0);
});

test('pinned revision resolution waits for its mixer instead of returning as soon as the URL matches', async (t) => {
  const root = await temporary(t), state = runtime(root, { delayedMixer: true }); t.after(() => state.provider.dispose());
  await state.provider.search({ query: 'green lung' });
  const pinned = await state.provider.resolve(result);
  assert.equal(pinned.revisionId, descriptor.revisionId); assert.ok(state.windows[0].webContents.mixerReads >= 2);
  assert.equal(state.requests.length, 0);
});

test('lazy Original audio is discovered through muted Play and is paused after its delayed iframe appears', async (t) => {
  const root = await temporary(t), state = runtime(root, { lazyAudio: true, delayedAudio: true }); t.after(() => state.provider.dispose());
  await state.provider.search({ query: 'green lung' });
  const pinned = await state.provider.resolve(result);
  assert.equal(pinned.audio.videoId, 'abcdefghijk'); assert.deepEqual(state.actions, ['history', 'play', 'pause']);
  assert.equal(state.windows[0].webContents.page.playing, false); assert.equal(state.windows[0].shown, false); assert.equal(state.requests.length, 0);
});

test('an already rendered Original full-mix player needs no playback action', async (t) => {
  const root = await temporary(t), state = runtime(root); t.after(() => state.provider.dispose());
  state.provider.restoreTrustedResult(result);
  assert.equal((await state.provider.findAudio(result, { revisionId: '626617' })).videoId, 'abcdefghijk');
  assert.deepEqual(state.actions, []); assert.equal(state.windows[0].webContents.getURL(), result.url + '/r626617'); assert.equal(state.requests.length, 0);
});

test('audio discovery waits for Original and Play controls that load after the tab mixer', async (t) => {
  for (const option of ['delayedAudioControls', 'delayedPlayControl']) {
    const root = await temporary(t), state = runtime(root, { lazyAudio: true, [option]: true }); t.after(() => state.provider.dispose());
    state.provider.restoreTrustedResult(result);
    assert.equal((await state.provider.findAudio(result, { revisionId: descriptor.revisionId })).videoId, 'abcdefghijk');
    assert.deepEqual(state.actions, ['play', 'pause']); assert.ok(state.windows[0].webContents.audioControlReads >= 3);
    assert.ok(state.diagnostics.some((event) => event.code === 'audio_probe_play_requested'));
    assert.ok(state.diagnostics.some((event) => event.code === 'audio_probe_found'));
  }
});

test('audio rediscovery uses the cached revision, selecting Original and Full mix without accepting the stale backing iframe', async (t) => {
  const root = await temporary(t), state = runtime(root, { initialSynth: true, initialMix: 'backing', delayedAudio: true }); t.after(() => state.provider.dispose());
  state.provider.restoreTrustedResult(result);
  const audio = await state.provider.findAudio(result, { revisionId: '626617' });
  assert.equal(audio.videoId, 'abcdefghijk'); assert.notEqual(audio.videoId, 'zzzzzzzzzzz');
  assert.equal(state.windows[0].webContents.getURL(), result.url + '/r626617');
  assert.deepEqual(state.actions, ['selectOriginal', 'selectFullMix', 'play', 'pause']); assert.equal(state.requests.length, 0);
});

test('missing Original stays absent while a stalled known player is retryable and always paused', async (t) => {
  const root = await temporary(t), state = runtime(root, { noOriginal: true }); t.after(() => state.provider.dispose());
  const noOriginalWin = state.provider._window(false); await state.provider._navigate(noOriginalWin, descriptor.approvedUrl);
  assert.equal(await state.provider._discoverAudio(noOriginalWin, { songId: result.id, revisionId: descriptor.revisionId }, noOriginalWin.webContents.page, undefined, 10), null); assert.deepEqual(state.actions, []);
  assert.ok(state.diagnostics.some((event) => event.code === 'audio_probe_original_unavailable' && event.outcome === 'unavailable'));
  const missing = runtime(root, { lazyAudio: true, noAudio: true }); t.after(() => missing.provider.dispose());
  const win = missing.provider._window(false); await missing.provider._navigate(win, descriptor.approvedUrl);
  await assert.rejects(missing.provider._discoverAudio(win, { songId: result.id, revisionId: descriptor.revisionId }, win.webContents.page, undefined, 10),
    error => error.code === 'needs_audio' && error.transport.reason === 'player_timeout');
  assert.deepEqual(missing.actions, ['play', 'pause']); assert.equal(win.webContents.page.playing, false);
  assert.ok(missing.diagnostics.some((event) => event.code === 'audio_probe_iframe_timeout'));
  assert.ok(missing.diagnostics.some((event) => event.code === 'audio_probe_play_control' && event.outcome === 'ready'));
});

test('recovery waits past a failed iframe for the full-mix replacement and pauses afterward', async t => {
  const root = await temporary(t), state = runtime(root, { fallbackVideo: 'lmnopqrstuv', delayedAudio: true });
  t.after(() => state.provider.dispose());
  await state.provider.search({ query: 'Green Lung' });
  const audio = await state.provider.findAudio(result, { revisionId: descriptor.revisionId, excludedVideoIds: ['abcdefghijk'] });
  assert.equal(audio.videoId, 'lmnopqrstuv');
  assert.deepEqual(state.actions, ['play', 'pause']);
});

test('recovery cannot reaccept the excluded iframe or malformed exclusions', async t => {
  const root = await temporary(t), state = runtime(root, { noAudio: true }); t.after(() => state.provider.dispose());
  const win = state.provider._window(false); await state.provider._navigate(win, descriptor.approvedUrl);
  const audio = await state.provider._discoverAudio(win, { songId: result.id, revisionId: descriptor.revisionId }, win.webContents.page, undefined, 10, ['abcdefghijk']);
  assert.equal(audio, null); assert.deepEqual(state.actions, ['play', 'pause']);
  await state.provider.search({ query: 'Green Lung' });
  await assert.rejects(state.provider.findAudio(result, { revisionId: descriptor.revisionId, excludedVideoIds: ['../bad'] }), { code: 'invalid_result' });
});

test('a stale backing player that never updates cannot be accepted as Full mix', async (t) => {
  const root = await temporary(t), state = runtime(root, { initialMix: 'backing', noAudio: true }); t.after(() => state.provider.dispose());
  const win = state.provider._window(false); await state.provider._navigate(win, descriptor.approvedUrl);
  const value = await state.provider._discoverAudio(win, { songId: result.id, revisionId: descriptor.revisionId }, win.webContents.page, undefined, 10);
  assert.equal(value, null); assert.deepEqual(state.actions, ['selectFullMix', 'play', 'pause']); assert.equal(win.webContents.page.playing, false);
});

test('cancellation after Play still pauses and rejects without losing the cancellation status', async (t) => {
  const controller = new AbortController(), root = await temporary(t), state = runtime(root, { lazyAudio: true, onPlay: () => controller.abort() }); t.after(() => state.provider.dispose());
  state.provider.restoreTrustedResult(result);
  await assert.rejects(state.provider.findAudio(result, { revisionId: descriptor.revisionId, signal: controller.signal }), { code: 'cancelled' });
  assert.deepEqual(state.actions, ['play', 'pause']); assert.equal(state.windows[0].webContents.page.playing, false); assert.equal(state.requests.length, 0);
});

test('player challenges and sign-in prompts retain their specific errors and trigger playback cleanup', async (t) => {
  for (const status of ['needs_attention', 'needs_login']) {
    const root = await temporary(t), state = runtime(root, { lazyAudio: true, afterPlayStatus: status }); t.after(() => state.provider.dispose());
    state.provider.restoreTrustedResult(result);
    await assert.rejects(state.provider.findAudio(result, { revisionId: descriptor.revisionId }), { code: status });
    assert.deepEqual(state.actions, ['play', 'pause']); assert.equal(state.windows[0].webContents.page.playing, false);
    if (status === 'needs_attention') assert.equal(state.provider.attentionWindow, state.windows[0]);
  }
});

test('rediscovery rejects unregistered songs, arbitrary URLs and invalid revisions before navigation', async (t) => {
  const root = await temporary(t), state = runtime(root); t.after(() => state.provider.dispose());
  await assert.rejects(state.provider.findAudio(result, { revisionId: descriptor.revisionId }), { code: 'invalid_result' });
  state.provider.restoreTrustedResult(result);
  await assert.rejects(state.provider.findAudio({ ...result, url: 'https://evil.test/' }, { revisionId: descriptor.revisionId }), { code: 'invalid_result' });
  await assert.rejects(state.provider.findAudio(result, { revisionId: '2585330/../../anything' }), { code: 'invalid_result' });
  assert.equal(state.windows.length, 0);
});

test('a revision change during playback cannot attach another revision audio', async (t) => {
  const root = await temporary(t), state = runtime(root, { lazyAudio: true, changedRevision: true }); t.after(() => state.provider.dispose());
  state.provider.restoreTrustedResult(result);
  await assert.rejects(state.provider.findAudio(result, { revisionId: descriptor.revisionId }), { code: 'revision_unavailable' });
  assert.deepEqual(state.actions, ['play', 'pause']); assert.equal(state.requests.length, 0);
});

test('trusted ledger restoration validates provider, numeric identity and canonical source URL before retry', async (t) => {
  const root = await temporary(t), state = runtime(root); t.after(() => state.provider.dispose());
  for (const chart of [
    { ...result, source: 'customsforge' }, { ...result, id: '999' },
    { ...result, url: 'https://evil.test/a/wsa/song-tab-s564073' },
    { ...result, url: 'file:///C:/secret' }, { ...result, url: 'https://user:password@www.songsterr.com/a/wsa/song-tab-s564073' },
  ]) assert.throws(() => state.provider.restoreTrustedResult(chart), { code: 'invalid_result' });
  const restored = state.provider.restoreTrustedResult(result);
  assert.equal(restored.id, result.id); assert.equal(state.windows.length, 0);
  assert.equal((await state.provider.resolve(restored)).revisionId, descriptor.revisionId);
});

test('explicit account retry requires login without attempting anonymous data or creating a copy', async (t) => {
  const root = await temporary(t), state = runtime(root, { signedOut: true }); t.after(() => state.provider.dispose());
  await state.provider.search({ query: 'green lung' }); const pinned = await state.provider.resolve(result);
  const directory = path.join(root, 'job'); await fs.mkdir(directory);
  await assert.rejects(state.provider.acquire(pinned, { directory, allowAccount: true }), { code: 'needs_login' });
  assert.equal(state.actions.includes('create'), false); assert.equal(state.requests.length, 0);
  await state.provider.signIn(); assert.equal(state.windows[1].shown, true); assert.equal(state.windows[0].shown, false);
});

test('account export creates an unpublished copy once, restores original metadata, and reuses it on retry', async (t) => {
  const root = await temporary(t), state = runtime(root); t.after(() => state.provider.dispose());
  await state.provider.search({ query: 'green lung' }); const pinned = await state.provider.resolve(result);
  await state.provider.signIn(); assert.equal(state.windows[1].shown, true);
  for (const name of ['first', 'second']) {
    const directory = path.join(root, name); await fs.mkdir(directory);
    const out = await state.provider.acquire(pinned, { directory, allowAccount: true });
    assert.equal(out.format, 'gpif'); assert.equal(out.metadata.artist, 'Green Lung'); assert.equal(out.metadata.title, 'Woodland Rites');
    assert.equal(out.sourceFilename, 'Green Lung - Woodland Rites.gp');
  }
  assert.equal(state.actions.filter((action) => action === 'create').length, 1);
  assert.equal(state.actions.filter((action) => action === 'export').length, 2);
  assert.equal(state.actions.some((action) => /publish|delete/.test(action)), false);
  assert.equal(state.requests.length, 0); assert.equal(state.windows[1].shown, false);
  const journal = JSON.parse(await fs.readFile(path.join(root, 'profile', 'feedforge-copy-journal.json'), 'utf8'));
  assert.equal(journal.copies['564073:2585330'].copyId, '99999');
});

test('ambiguous copy creation is journaled and never repeated automatically after failure or restart', async (t) => {
  const root = await temporary(t), state = runtime(root, { uncertainCreate: true }); t.after(() => state.provider.dispose());
  await state.provider.search({ query: 'green lung' }); const pinned = await state.provider.resolve(result);
  const directory = path.join(root, 'job'); await fs.mkdir(directory);
  await assert.rejects(state.provider.acquire(pinned, { directory, allowAccount: true }), /lost create response/);
  await assert.rejects(state.provider.acquire(pinned, { directory, allowAccount: true }), { code: 'needs_attention' });
  assert.equal(state.actions.filter((action) => action === 'create').length, 1);
  const restarted = runtime(root); t.after(() => restarted.provider.dispose());
  await restarted.provider.search({ query: 'green lung' }); const pinnedAgain = await restarted.provider.resolve(result);
  await assert.rejects(restarted.provider.acquire(pinnedAgain, { directory, allowAccount: true }), { code: 'needs_attention' });
  assert.equal(restarted.actions.includes('create'), false);
});

test('account GP export opens Download first when the Guitar Pro format control is hidden', async (t) => {
  const root = await temporary(t), state = runtime(root, { exportMenu: true }); t.after(() => state.provider.dispose());
  await state.provider.search({ query: 'green lung' }); const pinned = await state.provider.resolve(result);
  const directory = path.join(root, 'job'); await fs.mkdir(directory);
  const acquired = await state.provider.acquire(pinned, { directory, allowAccount: true });
  assert.equal(acquired.format, 'gpif');
  assert.deepEqual(state.actions.slice(-3), ['export', 'exportMenu', 'export']);
  assert.equal(state.actions.filter((action) => action === 'create').length, 1);
});

test('idle remote pages cannot launch an unowned download or unsafe navigation', async (t) => {
  const root = await temporary(t), state = runtime(root); t.after(() => state.provider.dispose());
  await state.provider.search({ query: 'green lung' });
  for (const session of state.sessions) { let prevented = false; session.emit('will-download', { preventDefault() { prevented = true; } }, {}, {}); assert.equal(prevented, true); }
  let blocked = false; state.windows[0].webContents.emit('will-navigate', { preventDefault() { blocked = true; } }, 'file:///C:/Windows'); assert.equal(blocked, true);
  assert.deepEqual(state.windows[0].webContents.popup({ url: 'https://evil.test' }), { action: 'deny' });
});

test('Open browser chooses the page requiring a challenge even when an account window already exists', async (t) => {
  const root = await temporary(t), state = runtime(root, { challengeOnSearch: true }); t.after(() => state.provider.dispose());
  await state.provider.signIn(); const accountWindow = state.windows[0]; accountWindow.hide();
  await assert.rejects(state.provider.search({ query: 'green lung' }), { code: 'needs_attention' });
  const publicWindow = state.windows[1]; assert.equal(publicWindow.shown, false);
  await state.provider.showBrowser(); assert.equal(publicWindow.shown, true); assert.equal(accountWindow.shown, false);
});

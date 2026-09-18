'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const fsp = fs.promises;
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const { createRequire } = require('node:module');

const sourcePath = path.join(__dirname, '../electron/song-browser/songsterr-service.cjs');
const realRequire = createRequire(sourcePath);
const SONG = { id: '564073', source: 'songsterr', title: 'Woodland Rites', artist: 'Green Lung', url: 'https://www.songsterr.com/a/wsa/green-lung-woodland-rites-tab-s564073', supported: true };
const pause = (ms = 10) => new Promise((resolve) => setTimeout(resolve, ms));

async function fixture(t, options = {}) {
  const tempParent = fs.realpathSync.native(os.tmpdir());
  const root = await fsp.mkdtemp(path.join(tempParent, 'feedforge-songsterr-service-'));
  const outputDir = path.join(root, 'output'); await fsp.mkdir(outputDir);
  const handlers = new Map(), providers = [], acquisitions = [], conversions = [], checks = [], emissions = [], dialogs = [];
  let settings = { outputDir, outputSettings: { outputLayout: 'artist', nameTemplate: '{artist} - {title}' } };
  let dialogResult = { canceled: true, filePaths: [] };
  class FakeProvider {
    constructor(config) { this.config = config; this.connection = { status: 'anonymous' }; this.restored = []; this.disposed = false; providers.push(this); }
    async search(request, context) { this.searchRequest = request; this.searchSignal = context.signal; return { status: 'ready', results: [SONG] }; }
    restoreTrustedResult(chart) { this.restored.push(chart); }
    async acquire(chart, context) {
      acquisitions.push({ chart: structuredClone(chart), allowAccount: context.allowAccount, directory: context.directory });
      if (options.acquire) return options.acquire(chart, context);
      if (options.accountOnly && !context.allowAccount) throw Object.assign(new Error('Anonymous fixture access was denied.'), { code: 'access_denied', canUseAccount: true });
      const destination = path.join(context.directory, 'fixture.songsterr.json');
      await fsp.writeFile(destination, JSON.stringify({ format: 'songsterr', songId: chart.id, fixture: true }), { flag: 'wx' });
      return { path: destination, format: 'songsterr', metadata: { songId: chart.id, revisionId: '2585330', approval: 'approved', title: chart.title, artist: chart.artist } };
    }
    async signIn() { this.signInCalls = (this.signInCalls || 0) + 1; return { ok: true }; }
    async showBrowser() { this.showCalls = (this.showCalls || 0) + 1; return { ok: true }; }
    async dispose() { this.disposed = true; this.disposeCalls = (this.disposeCalls || 0) + 1; return options.dispose?.(); }
  }
  const exports = {};
  const context = { module: { exports }, exports, require: (name) => name === './providers/songsterr/index.cjs' ? { SongsterrProvider: FakeProvider } : realRequire(name),
    URL, AbortController, Buffer, console, __dirname: path.dirname(sourcePath), __filename: sourcePath };
  vm.runInNewContext(fs.readFileSync(sourcePath, 'utf8'), context, { filename: sourcePath });
  const webContents = { mainFrame: {}, send: (name, state) => emissions.push({ name, state }) };
  const win = { webContents, isDestroyed: () => false };
  const event = { sender: webContents, senderFrame: webContents.mainFrame };
  const service = context.module.exports.registerSongsterr({ app: { getPath: (name) => { assert.equal(name, 'userData'); return root; } },
    BrowserWindow: class {}, session: {}, ipcMain: { handle: (name, handler) => handlers.set(name, handler) },
    dialog: { showOpenDialog: async (_win, config) => { dialogs.push(config); return dialogResult; } }, shell: { showItemInFolder() { throw new Error('No completed output is expected.'); } },
    getMainWindow: () => win, getSettings: () => settings,
    validateOutput: (directory) => { checks.push(directory); assert.equal(path.isAbsolute(directory), true); },
    getConverterRecipe: async () => ({ identity: 'fixture-converter' }),
    runConverter: async (args) => {
      assert.equal(args[0], '--song-import-file');
      conversions.push(JSON.parse(await fsp.readFile(args[1], 'utf8')));
      return { code: 1, stdout: JSON.stringify({ ok: false, code: 'needs_audio', error: 'The fixture requests a replacement recording.' }) };
    }, onCompleted() { throw new Error('A fixture should never publish a song.'); } });
  const call = (name, payload, from = event) => handlers.get(`songsterr:${name}`)(from, payload);
  t.after(async () => {
    await service.close();
    assert.equal(path.dirname(root), tempParent); assert.match(path.basename(root), /^feedforge-songsterr-service-/);
    assert.equal(fs.lstatSync(root).isSymbolicLink(), false); assert.equal(fs.realpathSync.native(root), root);
    await fsp.rm(root, { recursive: true, force: true });
  });
  async function waitState(id, expected) {
    for (let attempt = 0; attempt < 150; attempt++) {
      const state = await call('getState');
      const job = state.jobs.find((entry) => entry.id === id);
      if (job?.state === expected) {
        // The real queue releases its active slot after bounded workspace cleanup.
        await pause(15); return job;
      }
      await pause();
    }
    throw new Error(`Fixture did not reach ${expected}: ${JSON.stringify(await call('getState'))}`);
  }
  async function waitingJob() {
    await call('search', { query: 'green lung' });
    const queued = await call('enqueue', { id: SONG.id }); assert.equal(queued.ok, undefined);
    await waitState(queued.id, options.accountOnly ? 'failed' : 'needs_audio'); return queued;
  }
  return { root, outputDir, providers, acquisitions, conversions, checks, emissions, dialogs, service, call, waitState, waitingJob, event,
    setSettings(value) { settings = value; }, setDialog(value) { dialogResult = value; } };
}

test('Songsterr close waits for active acquisition cancellation and delayed provider cleanup once', async (t) => {
  let enter, releaseAcquisition, releaseProvider, signal;
  const entered = new Promise((resolve) => { enter = resolve; });
  const acquisition = new Promise((resolve) => { releaseAcquisition = resolve; });
  const provider = new Promise((resolve) => { releaseProvider = resolve; });
  const f = await fixture(t, {
    acquire: async (_chart, context) => { signal = context.signal; enter(); await acquisition; throw new Error('Fixture acquisition cancelled.'); },
    dispose: () => provider,
  });
  await f.call('search', { query: 'green lung' });
  await f.call('enqueue', { id: SONG.id }); await entered;
  let settled = false;
  const closing = f.service.close();
  closing.then(() => { settled = true; });
  try {
    assert.equal(f.service.close(), closing);
    await new Promise(setImmediate);
    assert.equal(signal.aborted, true);
    assert.equal(f.providers[0].disposeCalls, 1);
    assert.equal(settled, false);
    assert.match((await f.call('enqueue', { id: SONG.id })).error, /closing/);
    releaseProvider(); await new Promise(setImmediate);
    assert.equal(settled, false, 'The active acquisition must settle before close completes.');
    assert.equal(f.service.close(), closing);
  } finally { releaseProvider(); releaseAcquisition(); }
  await closing;
  assert.equal(f.conversions.length, 0);
  assert.equal(f.providers[0].disposeCalls, 1);
  const ledger = JSON.parse(fs.readFileSync(path.join(f.root, 'songsterr', 'jobs', 'jobs.json'), 'utf8'));
  assert.equal(ledger.jobs[0].state, 'cancelled');
});

test('Songsterr IPC rejects another renderer or subframe before initializing a profile or queue', async (t) => {
  const f = await fixture(t);
  for (const from of [{ sender: {}, senderFrame: f.event.senderFrame }, { sender: f.event.sender, senderFrame: {} }, {}]) {
    await assert.rejects(f.call('getState', {}, from), /must come from FeedForge/);
  }
  assert.equal(f.providers.length, 0); assert.equal(f.service.active(), false);
  const state = await f.call('getState'); assert.equal(state.jobs.length, 0); assert.equal(f.providers.length, 1);
});

test('only searched source IDs can enqueue and renderer URL/metadata cannot replace the registry entry', async (t) => {
  const f = await fixture(t);
  const unsearched = await f.call('enqueue', { id: SONG.id, url: SONG.url });
  assert.equal(unsearched.ok, false); assert.match(unsearched.error, /Search for this song/);
  await f.call('search', { query: 'green lung' });
  for (const id of ['99999', 'https://evil.test/file', '../564073', 'customsforge:564073']) {
    const rejected = await f.call('enqueue', { id, url: 'https://evil.test/file' });
    assert.equal(rejected.ok, false);
  }
  const queued = await f.call('enqueue', { id: SONG.id, url: 'https://evil.test/file', title: 'Injected title', artist: 'Injected artist', revisionId: '1', source: 'customsforge' });
  await f.waitState(queued.id, 'needs_audio');
  assert.equal(f.acquisitions.length, 1); assert.deepEqual(f.acquisitions[0].chart, SONG);
  assert.equal((await f.call('getState')).jobs.length, 1);
});

test('enqueued jobs capture shared output folder and naming settings and enter missing-audio without running conversion', async (t) => {
  const f = await fixture(t), queued = await f.waitingJob();
  const state = await f.call('getState'), waiting = state.jobs.find((job) => job.id === queued.id);
  assert.equal(waiting.state, 'needs_audio'); assert.equal(waiting.revisionId, '2585330'); assert.equal(waiting.outputDir, f.outputDir);
  assert.deepEqual(waiting.outputSettings, { outputLayout: 'artist', nameTemplate: '{artist} - {title}' });
  assert.equal(waiting.canRetry, true); assert.equal(waiting.canCancel, true); assert.equal(waiting.outputAvailable, false);
  assert.equal(f.conversions.length, 0); assert.deepEqual(f.checks, [f.outputDir]);
  assert.equal(f.providers[0].config.profilePath, path.join(f.root, 'songsterr', 'browser-profile'));
  const changed = path.join(f.root, 'new-output'); await fsp.mkdir(changed);
  f.setSettings({ outputDir: changed, outputSettings: { outputLayout: 'flat', nameTemplate: '{title}' } });
  const after = await f.call('getState'); assert.equal(after.outputDir, changed); assert.equal(after.jobs[0].outputDir, f.outputDir);
  assert.equal(after.jobs[0].outputSettings.nameTemplate, '{artist} - {title}');
});

test('missing shared output folder rejects enqueue before acquisition', async (t) => {
  const f = await fixture(t); await f.call('search', { query: 'green lung' });
  f.setSettings({ outputDir: '', outputSettings: { outputLayout: 'flat', nameTemplate: '{source}' } });
  const rejected = await f.call('enqueue', { id: SONG.id }); assert.equal(rejected.ok, false); assert.match(rejected.error, /Settings/);
  assert.equal(f.acquisitions.length, 0); assert.equal(f.checks.length, 0);
});

test('account fallback is explicit through retry IPC and login is never triggered automatically', async (t) => {
  const f = await fixture(t, { accountOnly: true }), queued = await f.waitingJob();
  let state = await f.call('getState'); assert.equal(state.jobs[0].canUseAccount, true); assert.equal(f.acquisitions[0].allowAccount, false);
  assert.equal(f.providers[0].signInCalls, undefined);
  const notTrue = await f.call('retry', { id: queued.id, allowAccount: 'true' }); assert.equal(notTrue.ok, undefined);
  await f.waitState(queued.id, 'failed'); assert.equal(f.acquisitions.at(-1).allowAccount, false);
  await f.call('signIn'); assert.equal(f.providers[0].signInCalls, 1);
  const retry = await f.call('retry', { id: queued.id, allowAccount: true }); assert.equal(retry.ok, undefined);
  await f.waitState(queued.id, 'needs_audio');
  assert.equal(f.acquisitions.at(-1).allowAccount, true); assert.equal(f.conversions.length, 0);
  state = await f.call('getState'); assert.equal(state.jobs.length, 1);
});

test('audio URL IPC rejects local/non-HTTPS schemes and embedded credentials before retrying the real job', async (t) => {
  const f = await fixture(t), queued = await f.waitingJob();
  for (const url of ['file:///C:/secret.wav', 'C:\\secret.wav', 'http://example.test/audio.ogg', 'data:audio/wav;base64,AAAA', 'ftp://example.test/song', 'https://user:secret@example.test/song.ogg', 'not a URL']) {
    const rejected = await f.call('useAudioUrl', { id: queued.id, url }); assert.equal(rejected.ok, false, url);
  }
  assert.equal(f.conversions.length, 0); assert.equal((await f.call('getState')).jobs[0].state, 'needs_audio');
  const accepted = await f.call('useAudioUrl', { id: queued.id, url: ' https://www.youtube.com/watch?v=abcdefghijk ' }); assert.equal(accepted.ok, undefined);
  await f.waitState(queued.id, 'needs_audio'); assert.equal(f.acquisitions.length, 1, 'cached tab is reused');
  assert.equal(f.conversions.length, 1); assert.deepEqual(f.conversions[0].audio, { kind: 'url', url: 'https://www.youtube.com/watch?v=abcdefghijk' });
  assert.equal(f.conversions[0].outputSettings.nameTemplate, '{artist} - {title}');
});

test('audio selection uses the native dialog result and never accepts a renderer-provided local path', async (t) => {
  const f = await fixture(t), queued = await f.waitingJob();
  const audio = path.join(f.root, 'selected.wav'); await fsp.writeFile(audio, Buffer.alloc(32));
  f.setDialog({ canceled: false, filePaths: [audio] });
  const retry = await f.call('chooseAudio', { id: queued.id, path: 'C:\\Windows\\secret.wav' }); assert.equal(retry.ok, undefined);
  await f.waitState(queued.id, 'needs_audio'); assert.equal(f.dialogs.length, 1);
  assert.deepEqual(f.conversions[0].audio, { kind: 'file', path: audio });
  assert.equal(f.acquisitions.length, 1);
});

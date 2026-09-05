'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const { createDiagnostics } = require('../electron/song-browser/diagnostics.cjs');
const { normalizeEndpoint } = require('../electron/song-browser/feedback.cjs');

const plain = (value) => JSON.parse(JSON.stringify(value));
const tick = () => new Promise((resolve) => setImmediate(resolve));
function gate() { let resolve; const promise = new Promise((done) => { resolve = done; }); return { promise, resolve }; }
function fixture(t) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'feedforge-ipc-settings-'));
  const libraryDir = path.join(directory, 'fixture-library'); fs.mkdirSync(libraryDir);
  const handlers = new Map(); const created = { browsers: [], queues: [] };
  const mainFrame = {}; const webContents = { mainFrame, send() {} };
  const win = { webContents, isDestroyed: () => false };
  const trusted = { sender: webContents, senderFrame: mainFrame };
  const calls = { inspections: [], refreshes: [], retries: [], cleared: [], saves: [], opens: [] };
  const options = { selectedOutput: path.join(directory, 'selected-output'), savePath: path.join(directory, 'report.json'), failSettings: false };
  const info = (url) => ({ url: normalizeEndpoint(url), libraryDir, version: '0.3.0-alpha.1', running: false });
  const remote = { inspect: async (url) => info(url), refresh: async (url) => info(url) };
  class Browser {
    constructor(value) { this.options = value; this.connection = { status: 'signed_out' }; created.browsers.push(this); }
    signIn() { return { ok: true }; } showBrowser() { return { ok: true }; } dispose() {}
    search() { return { status: 'ready', results: [] }; }
  }
  class Queue {
    constructor(value) { this.options = value; this.outputDir = value.outputDir; this.entries = []; created.queues.push(this); }
    snapshot() { return this.entries.map((item) => ({ ...item })); }
    setOutputDir(value) { this.outputDir = path.resolve(value); fs.mkdirSync(this.outputDir, { recursive: true }); return this.outputDir; }
    retry(id) { calls.retries.push(id); return { ok: true, id }; }
    async clearCache(id) { calls.cleared.push(id); return { ok: true }; }
    async getCachedInput() { return path.join(directory, 'source.psarc'); }
    async dispose() {}
    emit(job) { this.entries = [job]; this.options.emit(job); }
  }
  const fsProxy = { ...fs, renameSync(from, to) {
    if (options.failSettings && path.basename(to) === 'settings.json') throw new Error('Intentional fixture settings failure.');
    return fs.renameSync(from, to);
  } };
  const module = { exports: {} };
  const localRequire = (name) => {
    if (name === 'node:fs') return fsProxy;
    if (name === './browser.cjs') return { CustomsForgeBrowser: Browser };
    if (name === './jobs.cjs') return { SongJobs: Queue };
    if (name === './diagnostics.cjs') return { createDiagnostics };
    if (name === './feedback.cjs') return { normalizeEndpoint,
      inspectFeedback: async (url) => { calls.inspections.push(url); return remote.inspect(url); },
      refreshFeedback: async (url, target) => { calls.refreshes.push({ url, target }); return remote.refresh(url, target); } };
    return require(name);
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../electron/song-browser/index.cjs'), 'utf8'),
    { module, exports: module.exports, require: localRequire, process, console, setTimeout, clearTimeout });
  const service = module.exports.registerSongBrowser({
    app: { getPath: () => directory, getVersion: () => '0.1.40', on() {} },
    BrowserWindow: Browser, session: {}, ipcMain: { handle(name, action) { handlers.set(name, action); } },
    dialog: { showSaveDialog: async (_win, request) => { calls.saves.push(request); return { canceled: false, filePath: options.savePath }; },
      showOpenDialog: async () => { calls.opens.push(true); return { canceled: false, filePaths: [options.selectedOutput] }; } },
    shell: {}, getMainWindow: () => win, runConverter() { throw new Error('No real converter in IPC fixture.'); }
  });
  t.after(async () => { await service.close(); fs.rmSync(directory, { recursive: true, force: true }); });
  const call = (name, payload, event = trusted) => handlers.get('song-browser:' + name)(event, payload);
  const settingsPath = path.join(directory, 'song-browser', 'settings.json');
  return { directory, libraryDir, handlers, options, calls, created, remote, info, call, settingsPath, trusted };
}

test('every recovery, diagnostics and FeedBack IPC rejects foreign senders and subframes before initialization', async (t) => {
  const f = fixture(t);
  for (const name of ['retry', 'clearCache', 'openCached', 'exportDiagnostics', 'connectFeedback', 'setAutoRefresh', 'useFeedbackFolder', 'refreshFeedback']) {
    await assert.rejects(f.call(name, { id: 'fixture', url: 'http://127.0.0.1:8000', enabled: true }, { sender: {}, senderFrame: {} }), /requests must come from FeedForge/);
    await assert.rejects(f.call(name, {}, { sender: f.trusted.sender, senderFrame: {} }), /requests must come from FeedForge/);
  }
  assert.equal(f.created.browsers.length, 0); assert.equal(f.created.queues.length, 0);
  assert.equal(fs.existsSync(path.join(f.directory, 'song-browser')), false);
});

test('diagnostics export contains fixed failure details without account, file, URL or song data and never overwrites', async (t) => {
  const f = fixture(t); await f.call('getState');
  f.created.browsers[0].options.onDiagnostic({ code: 'download_failed', stage: 'download', host: 'dropbox', outcome: 'interrupted', durationMs: 101,
    error: 'PRIVATE ACCOUNT', url: 'https://fixture.invalid?signature=SECRET', cookie: 'PRIVATE COOKIE', path: f.directory, title: 'PRIVATE SONG' });
  assert.deepEqual(plain(await f.call('exportDiagnostics')), { ok: true, exported: true });
  const first = fs.readFileSync(f.options.savePath, 'utf8');
  const report = JSON.parse(first);
  assert.equal(report.appVersion, '0.1.40');
  assert.equal(report.events[0].outcome, 'interrupted'); assert.equal(report.events[0].host, 'dropbox');
  assert.doesNotMatch(first, /PRIVATE|SECRET|signature|cookie|source\.psarc|fixture-library/);
  assert.equal((await f.call('exportDiagnostics')).ok, false);
  assert.equal(fs.readFileSync(f.options.savePath, 'utf8'), first);
});

test('trusted recovery uses a job ID and saves an exclusive PSARC copy selected by the user', async (t) => {
  const f = fixture(t); await f.call('getState');
  const input = path.join(f.directory, 'source.psarc'); fs.writeFileSync(input, 'PSAR fixture');
  f.created.queues[0].entries = [{ id: 'job-1', chartId: '123', hasCachedInput: true, state: 'failed' }];
  assert.equal((await f.call('retry', { id: 'job-1', path: 'untrusted path' })).ok, true);
  assert.equal((await f.call('clearCache', { id: 'job-1' })).ok, true);
  assert.deepEqual(f.calls.retries, ['job-1']); assert.deepEqual(f.calls.cleared, ['job-1']);
  f.options.savePath = path.join(f.directory, 'review.psarc');
  assert.equal((await f.call('openCached', { id: 'job-1' })).inputPath, f.options.savePath);
  assert.equal(fs.readFileSync(f.options.savePath, 'utf8'), 'PSAR fixture');
  assert.match((await f.call('openCached', { id: 'job-1' })).error, /already exists/);
  f.options.savePath = path.join(f.directory, 'review.exe');
  assert.match((await f.call('openCached', { id: 'job-1' })).error, /psarc filename/);
  assert.equal(fs.existsSync(f.options.savePath), false);
});

test('connecting, opting into refresh and selecting a library preserve settings across output changes', async (t) => {
  const f = fixture(t);
  assert.equal((await f.call('connectFeedback', { url: 'http://localhost:8000' })).ok, true);
  assert.equal((await f.call('setAutoRefresh', { enabled: true })).ok, true);
  assert.equal((await f.call('useFeedbackFolder')).outputDir, f.libraryDir);
  let settings = JSON.parse(fs.readFileSync(f.settingsPath));
  assert.deepEqual(settings.feedback, { url: 'http://127.0.0.1:8000', autoRefresh: true });
  assert.equal(settings.outputDir, f.libraryDir);
  await f.call('chooseOutput');
  settings = JSON.parse(fs.readFileSync(f.settingsPath));
  assert.equal(settings.feedback.autoRefresh, true); assert.equal(settings.outputDir, f.options.selectedOutput);
  const state = await f.call('getState'); assert.equal(state.feedback.autoRefresh, true);
});

test('failed settings writes retain the previous connection, refresh choice and queue output folder', async (t) => {
  const f = fixture(t);
  await f.call('connectFeedback', { url: 'http://127.0.0.1:8000' }); await f.call('chooseOutput');
  const previous = fs.readFileSync(f.settingsPath, 'utf8'); const before = await f.call('getState');
  f.options.failSettings = true;
  assert.equal((await f.call('connectFeedback', { url: 'http://127.0.0.1:8001' })).ok, false);
  assert.equal((await f.call('setAutoRefresh', { enabled: true })).ok, false);
  f.options.selectedOutput = path.join(f.directory, 'failed-output');
  assert.equal((await f.call('chooseOutput')).ok, false);
  assert.equal(fs.readFileSync(f.settingsPath, 'utf8'), previous);
  const after = await f.call('getState');
  assert.equal(after.outputDir, before.outputDir); assert.equal(after.feedback.url, before.feedback.url);
  assert.equal(after.feedback.autoRefresh, false); assert.equal(f.created.queues[0].outputDir, before.outputDir);
  assert.deepEqual(fs.readdirSync(path.dirname(f.settingsPath)).filter((name) => name.endsWith('.tmp')), []);
});

test('automatic refresh happens once on completion after opt-in and cannot turn completion into failure', async (t) => {
  const f = fixture(t); await f.call('connectFeedback', { url: 'http://127.0.0.1:8000' });
  const queue = f.created.queues[0];
  queue.emit({ id: 'first', state: 'completed', outputPath: path.join(f.libraryDir, 'first.feedpak') }); await tick();
  assert.equal(f.calls.refreshes.length, 0);
  await f.call('setAutoRefresh', { enabled: true });
  f.remote.refresh = async () => { throw new Error('Fixture refresh failure'); };
  const completed = { id: 'second', state: 'completed', outputPath: path.join(f.libraryDir, 'second.feedpak') };
  queue.emit(completed); queue.emit(completed); await tick();
  assert.equal(f.calls.refreshes.length, 1); assert.equal(queue.snapshot()[0].state, 'completed');
  assert.equal((await f.call('getState')).feedback.status, 'error');
});

test('concurrent refresh requests never overlap mutations after waiting for an earlier request', async (t) => {
  const f = fixture(t); await f.call('connectFeedback', { url: 'http://127.0.0.1:8000' });
  const firstGate = gate(); let active = 0; let maximum = 0; let count = 0;
  f.remote.refresh = async (url) => {
    active++; maximum = Math.max(maximum, active); count++;
    if (count === 1) await firstGate.promise; else await tick();
    active--; return f.info(url);
  };
  const first = f.call('refreshFeedback'); await tick();
  const second = f.call('refreshFeedback'); const third = f.call('refreshFeedback');
  firstGate.resolve(); await Promise.all([first, second, third]);
  assert.equal(maximum, 1, 'parallel waiting requests must remain serialized');
});

test('an older slow connection check cannot replace a newer user-selected connection', async (t) => {
  const f = fixture(t); const old = gate();
  f.remote.inspect = async (url) => { if (url.endsWith(':8000')) await old.promise; return f.info(url); };
  const pending = f.call('connectFeedback', { url: 'http://127.0.0.1:8000' });
  await tick(); await f.call('connectFeedback', { url: 'http://127.0.0.1:8001' });
  old.resolve(); await pending;
  assert.equal((await f.call('getState')).feedback.url, 'http://127.0.0.1:8001');
  assert.equal(JSON.parse(fs.readFileSync(f.settingsPath)).feedback.url, 'http://127.0.0.1:8001');
});

test('a pending library-folder lookup cannot override a newer output choice', async (t) => {
  const f = fixture(t); await f.call('connectFeedback', { url: 'http://127.0.0.1:8000' });
  const lookup = gate(); f.remote.inspect = async (url) => { await lookup.promise; return f.info(url); };
  const pending = f.call('useFeedbackFolder'); await tick();
  await f.call('chooseOutput'); lookup.resolve(); await pending;
  assert.equal((await f.call('getState')).outputDir, f.options.selectedOutput);
});

test('a pending library-folder lookup cannot apply a folder from an older connection', async (t) => {
  const f = fixture(t); await f.call('connectFeedback', { url: 'http://127.0.0.1:8000' });
  const original = (await f.call('getState')).outputDir;
  const lookup = gate(); f.remote.inspect = async (url) => { if (url.endsWith(':8000')) await lookup.promise; return f.info(url); };
  const pending = f.call('useFeedbackFolder'); await tick();
  await f.call('connectFeedback', { url: 'http://127.0.0.1:8001' }); lookup.resolve(); await pending;
  assert.equal((await f.call('getState')).outputDir, original);
});

test('a failed new connection attempt cannot strand the previous refresh in a refreshing state', async (t) => {
  const f = fixture(t); await f.call('connectFeedback', { url: 'http://127.0.0.1:8000' });
  const running = gate(); f.remote.refresh = async (url) => { await running.promise; return f.info(url); };
  const refresh = f.call('refreshFeedback'); await tick();
  f.remote.inspect = async () => { throw new Error('New address unavailable.'); };
  assert.equal((await f.call('connectFeedback', { url: 'http://127.0.0.1:8001' })).ok, false);
  running.resolve(); await refresh;
  assert.equal((await f.call('getState')).feedback.status, 'connected');
});

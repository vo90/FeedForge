const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { EventEmitter } = require('node:events');
const { outputPayload, validateUrl, registerSongsterr } = require('../electron/services/songsterr.cjs');
const { LocalAssetRegistry } = require('../electron/local-assets.cjs');
const { createConcurrencyLimiter } = require('../electron/concurrency-limiter.cjs');

function gate() { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; }
const tick = () => new Promise(setImmediate);
function fixture(t, options = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'feedforge-editor-test-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  for (const name of ['temp', 'userData', 'output']) fs.mkdirSync(path.join(root, name));
  const handlers = new Map(), lifecycle = new Map(), messages = [];
  const assets = new LocalAssetRegistry();
  const sender = new EventEmitter();
  Object.assign(sender, { isDestroyed: () => false, send: (channel, value) => messages.push({ channel, value }) });
  const event = { sender };
  const state = { quits: 0 };
  const service = registerSongsterr({
    app: { getPath: name => path.join(root, name), on: (name, fn) => lifecycle.set(name, fn), quit: () => state.quits++ },
    ipcMain: { handle: (name, fn) => { assert.equal(handlers.has(name), false); handlers.set(name, fn); } },
    dialog: {}, window: () => null, localAssets: assets,
    terminateChildProcessTree: () => {}, logDebug: () => {},
    removeTemporaryDirectory: dir => fs.rmSync(dir, { recursive: true, force: true }),
    ...options
  });
  const payload = { url: 'https://www.songsterr.com/a/wsa/test-s1', selected_parts: [0], output_name: 'Ignored.feedpak',
    outputSettings: { outputDir: path.join(root, 'output'), outputLayout: 'artist', nameTemplate: '{artist} - {title}', generateDifficulty: true } };
  return { root, handlers, lifecycle, messages, assets, event, state, service, payload,
    invoke: (name, value) => handlers.get(`songsterr-editor:${name}`)(event, value) };
}

test('editor namespace coexists with automatic search/cancel and preview survives until shared close', async t => {
  let previewPath;
  const f = fixture(t, { managedLifecycle: true, runConverter: async (args, options) => {
    const request = JSON.parse(fs.readFileSync(args[1]));
    assert.equal(request.action, 'preview');
    assert.equal(options.directory, request.payload.preview_dir);
    assert.notEqual(request.payload.preview_dir, 'untrusted');
    previewPath = path.join(request.payload.preview_dir, 'full.ogg');
    fs.writeFileSync(previewPath, 'fixture');
    return { code: 0, stdout: JSON.stringify({ ok: true, result: { audio_path: previewPath, measures: [] } }) };
  } });
  assert.equal(f.handlers.has('songsterr:search'), false);
  assert.equal(f.handlers.has('songsterr:cancel'), false);
  assert.equal(f.lifecycle.has('before-quit'), false);
  const result = await f.invoke('preview', { preview_dir: 'untrusted' });
  assert.ok(fs.existsSync(previewPath));
  assert.equal(f.assets.resolve(result.audio_url), fs.realpathSync.native(previewPath));
  assert.equal(f.messages[0].channel, 'songsterr-editor:progress');
  const closed = f.service.close();
  assert.equal(f.service.close(), closed);
  await closed;
  assert.equal(f.assets.resolve(result.audio_url), null);
  assert.deepEqual(fs.readdirSync(path.join(f.root, 'temp')), []);
  assert.throws(() => f.invoke('analyze', f.payload.url), /closing/);
});

test('output names validate; editor stages, validates and exclusively publishes with shared settings', async t => {
  let f;
  f = fixture(t, { runConverter: async (args, options) => {
    if (args[0] === '--validate-feedpak') {
      assert.equal(fs.readFileSync(args[1], 'utf8'), 'complete');
      assert.equal(options.admissionSignal instanceof AbortSignal, true);
      fs.mkdirSync(path.join(f.root, 'output', 'Artist'));
      fs.writeFileSync(path.join(f.root, 'output', 'Artist', 'Artist - Song.feedpak'), 'keep');
      return { code: 0, stdout: '{"ok":true}' };
    }
    const request = JSON.parse(fs.readFileSync(args[1]));
    assert.equal(request.payload.outputSettings.nameTemplate, '{artist} - {title}');
    assert.equal(request.payload.generateDifficulty, true);
    assert.equal(options.env.ELECTRON_RUN_AS_NODE, '1');
    assert.equal(options.logOutput, false);
    assert.ok(request.payload.output_path.startsWith(path.join(f.root, 'temp')));
    fs.writeFileSync(request.payload.output_path, 'complete');
    return { code: 0, stdout: JSON.stringify({ ok: true, result: { output_path: request.payload.output_path, relative_path: 'Artist/Artist - Song.feedpak' } }) };
  } });
  const result = await f.invoke('create', f.payload);
  assert.equal(result.output_path, path.join(f.root, 'output', 'Artist', 'Artist - Song (2).feedpak'));
  assert.equal(fs.readFileSync(path.join(f.root, 'output', 'Artist', 'Artist - Song.feedpak'), 'utf8'), 'keep');
  assert.equal(result.sourceVerification, 'not_checked');
  const history = path.join(f.root, 'userData', 'songsterr-editor', 'exports');
  const receipt = JSON.parse(fs.readFileSync(path.join(history, fs.readdirSync(history)[0])));
  assert.equal(receipt.sourceVerification, 'not_checked');
  assert.equal(receipt.validation, 'passed');
  assert.equal(receipt.outputPath, result.output_path);
  assert.deepEqual(fs.readdirSync(path.join(f.root, 'temp')), []);
  for (const name of ['../escape.feedpak', 'C:\\escape.feedpak', 'CON.feedpak']) {
    assert.throws(() => outputPayload({ ...f.payload, output_dir: path.join(f.root, 'output'), output_name: name }));
  }
  assert.throws(() => validateUrl('https://songsterr.com.evil.test/a/wsa/test-s1'));
});

test('validation failure never publishes an editor output', async t => {
  const f = fixture(t, { runConverter: async args => {
    if (args[0] === '--validate-feedpak') return { code: 1, stdout: '{"ok":false}' };
    const request = JSON.parse(fs.readFileSync(args[1]));
    fs.writeFileSync(request.payload.output_path, 'invalid');
    return { code: 0, stdout: JSON.stringify({ ok: true, result: { output_path: request.payload.output_path, relative_path: 'Artist/Song.feedpak' } }) };
  } });
  await assert.rejects(f.invoke('create', f.payload), /validation/);
  assert.deepEqual(fs.readdirSync(path.join(f.root, 'output')), []);
});

test('editor accepts native staging paths through redirected Windows profiles', async t => {
  const f = fixture(t, { runConverter: async args => {
    if (args[0] === '--validate-feedpak') return { code: 0, stdout: '{"ok":true}' };
    const request = JSON.parse(fs.readFileSync(args[1]));
    fs.writeFileSync(request.payload.output_path, 'complete');
    return { code: 0, stdout: JSON.stringify({ ok: true, result: {
      output_path: fs.realpathSync.native(request.payload.output_path), relative_path: 'Artist/Song.feedpak'
    } }) };
  } });
  const logical = path.join(f.root, 'temp'), native = path.join(f.root, 'native-temp');
  fs.renameSync(logical, native);
  fs.symlinkSync(native, logical, process.platform === 'win32' ? 'junction' : 'dir');
  const result = await f.invoke('create', f.payload);
  assert.equal(fs.readFileSync(result.output_path, 'utf8'), 'complete');
});

test('batch Stop finishes the active song and skips the remaining songs', async t => {
  let f;
  f = fixture(t, { runConverter: async (args, options) => {
    if (args[0] === '--validate-feedpak') return { code: 0, stdout: '{"ok":true}' };
    const request = JSON.parse(fs.readFileSync(args[1]));
    options.onSpawn({ pid: 1 });
    options.onStderrLine('FEEDFORGE_PROGRESS {"stage":"Building"}');
    await f.invoke('cancel');
    fs.writeFileSync(request.payload.output_path, 'complete');
    return { code: 0, stdout: JSON.stringify({ ok: true, result: { output_path: request.payload.output_path, relative_path: 'Artist/Song.feedpak' } }) };
  } });
  const result = await f.invoke('batch', [f.payload, f.payload]);
  assert.equal(result.created, 1);
  assert.equal(result.failed, 0);
  assert.equal(result.skipped, 1);
  assert.equal(result.cancelled, true);
  assert.equal(f.messages[1].value.stage, 'Building');
});

test('cancel and timeout remove queued work without spawning it behind an occupied limiter', async t => {
  for (const timeout of [false, true]) {
    const limiter = createConcurrencyLimiter(1), occupied = gate(), entered = gate();
    const holding = limiter.run(() => occupied.promise);
    let starts = 0;
    const f = fixture(t, { operationTimeout: () => timeout ? 20 : 60000,
      runConverter: (args, options) => { entered.resolve(); return limiter.run(() => { starts++; return { code: 0, stdout: '{}' }; }, { signal: options.admissionSignal }); } });
    const pending = f.invoke('analyze', f.payload.url);
    const rejected = assert.rejects(pending, timeout ? /timed out/ : /cancelled|aborted/i);
    await entered.promise;
    if (!timeout) await f.invoke('cancel');
    try { await rejected; assert.equal(starts, 0); } finally { occupied.resolve(); await holding; }
    assert.equal(starts, 0);
    assert.deepEqual(fs.readdirSync(path.join(f.root, 'temp')), []);
  }
});

test('standalone repeated quit waits for an active child and delayed temporary cleanup', async t => {
  const entered = gate(), stopped = gate(), cleanup = gate();
  let kills = 0, cleaned = 0;
  const f = fixture(t, {
    runConverter: async (_args, options) => { options.onSpawn({ pid: 1 }); entered.resolve(); await stopped.promise; return { code: 1, stdout: '' }; },
    terminateChildProcessTree: async () => { kills++; stopped.resolve(); },
    removeTemporaryDirectory: async dir => { cleaned++; await cleanup.promise; fs.rmSync(dir, { recursive: true, force: true }); }
  });
  const pending = f.invoke('analyze', f.payload.url);
  const rejected = assert.rejects(pending, /cancelled/);
  await entered.promise;
  let prevented = 0;
  const quit = () => f.lifecycle.get('before-quit')({ preventDefault() { prevented++; } });
  quit(); quit(); await tick();
  assert.equal(kills, 1); assert.equal(cleaned, 1); assert.equal(f.state.quits, 0);
  cleanup.resolve(); await rejected; await f.service.close(); await tick();
  assert.equal(f.state.quits, 1); assert.equal(prevented, 2);
  quit(); assert.equal(prevented, 2);
});

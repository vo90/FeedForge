"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const vm = require("node:vm");
const { createRequire, wrap } = require("node:module");
const test = require("node:test");

const mainPath = path.resolve(__dirname, "../../electron/main.cjs");
const mainSource = fs.readFileSync(mainPath, "utf8");
const mainRequire = createRequire(mainPath);

function loadMain(t, packaged, options = {}) {
  const base = fs.mkdtempSync(path.join(os.tmpdir(), "feedforge-asset-lifecycle-"));
  t.after(() => fs.rmSync(base, { recursive: true, force: true }));
  const appRoot = path.join(base, "app");
  const resources = path.join(base, "resources");
  const temporary = path.join(base, "temp");
  const profile = path.join(base, "profile");
  for (const directory of [appRoot, resources, temporary, profile]) fs.mkdirSync(directory);
  const assetRoot = packaged ? path.join(resources, "tone-equipment") : path.join(appRoot, "assets", "tone-equipment");
  const tonePath = path.join(assetRoot, "feedback", "mock.png");
  fs.mkdirSync(path.dirname(tonePath), { recursive: true });
  fs.writeFileSync(tonePath, "tone image");
  const catalog = packaged ? path.join(assetRoot, "equipment.json") : path.join(appRoot, "src", "feedback_converter", "data", "equipment.json");
  fs.mkdirSync(path.dirname(catalog), { recursive: true });
  fs.writeFileSync(catalog, JSON.stringify([{ id: "FB_Mock", feedbackAsset: "assets/feedback/mock.png" }]));

  const callbacks = [];
  const events = new Map();
  const ipc = new Map();
  const handlers = new Map();
  const state = { schemes: [], windows: 0, quits: 0, songBrowserRegistrations: 0, browserCloses: 0 };
  const electron = {
    app: {
      isPackaged: packaged, getVersion: () => "test", getAppPath: () => appRoot,
      getPath: (name) => ({ temp: temporary, userData: profile, home: base })[name],
      whenReady: () => ({ then: (callback) => callbacks.push(callback) }),
      on: (name, callback) => events.set(name, callback), quit: () => { state.quits++; }
    },
    BrowserWindow: class {
      constructor() {
        assert.ok(handlers.has("feedforge-local"), "asset protocol must exist before the renderer opens");
        state.windows++;
      }
      once() {}
      loadURL() {}
      loadFile() {}
    },
    Menu: { setApplicationMenu() {} }, dialog: {}, shell: {},
    ipcMain: { handle: (name, callback) => ipc.set(name, callback), on() {} },
    protocol: {
      registerSchemesAsPrivileged: (schemes) => { state.schemes = schemes; },
      handle: (name, callback) => handlers.set(name, callback),
      unhandle: (name) => handlers.delete(name)
    }
  };
  const sandbox = {
    require: (name) => {
      if (name === "electron") return electron;
      // The artwork lifecycle must not initialize browser sessions, import
      // queues, or a real packaged profile as a side effect of this fixture.
      if (name === "./song-browser/index.cjs") return {
        registerSongBrowser: (options) => {
          assert.equal(options.app, electron.app);
          assert.equal(typeof options.runConverter, "function");
          assert.equal(options.managedLifecycle, true);
          state.songBrowserRegistrations++;
          return { close: () => { state.browserCloses++; return closeSongBrowser(); } };
        }
      };
      if (name === path.join(appRoot, "package.json")) return { songBrowserTest: false };
      return mainRequire(name);
    },
    __dirname: path.dirname(mainPath), console, Buffer, Response, URL, AbortController,
    setTimeout: () => 0, clearTimeout() {},
    process: {
      env: {}, platform: process.platform, arch: process.arch, resourcesPath: resources,
      on() {}, constrainedMemory: () => 0
    }
  };
  const closeSongBrowser = options.closeSongBrowser || (() => Promise.resolve());
  vm.createContext(sandbox);
  const fixtureModule = { exports: {} };
  const fixtureHooks = "\nmodule.exports.stubInspection = (result) => { runConverter = async () => result; };\n"
    + "module.exports.stubShutdown = (converters, stems) => { shutdownConverterProcesses = converters; stopStemServer = stems; };\n";
  // Electron loads main.cjs as CommonJS: an early top-level return is valid
  // there. Keep that real scope, and expose only the fixture's converter stub.
  const execute = vm.runInContext(wrap(mainSource + fixtureHooks), sandbox, { filename: mainPath });
  execute(fixtureModule.exports, sandbox.require, fixtureModule, mainPath, path.dirname(mainPath));
  assert.equal(state.schemes[0].scheme, "feedforge-local");
  assert.equal(state.schemes[0].privileges.secure, true);
  assert.equal(state.windows, 0, "scheme is registered before app readiness");
  callbacks.forEach((callback) => callback());
  assert.equal(state.songBrowserRegistrations, 1);
  return { base, temporary, tonePath, stubInspection: fixtureModule.exports.stubInspection, stubShutdown: fixtureModule.exports.stubShutdown, handlers, ipc, events, state };
}

function gate() { let resolve; const promise = new Promise((done) => { resolve = done; }); return { promise, resolve }; }
const tick = () => new Promise(setImmediate);

test('main shutdown waits for song cleanup with no converter child and blocks repeated quit requests', async (t) => {
  const download = gate();
  const app = loadMain(t, false, { closeSongBrowser: () => download.promise });
  let prevented = 0;
  const quit = () => app.events.get('before-quit')({ preventDefault() { prevented++; } });
  try {
    quit(); await tick();
    assert.equal(app.state.browserCloses, 1);
    assert.equal(app.state.quits, 0);
    quit(); quit(); await tick();
    assert.equal(prevented, 3);
    assert.equal(app.state.browserCloses, 1);
    assert.equal(app.state.quits, 0);
  } finally { download.resolve(); }
  await tick();
  assert.equal(app.state.quits, 1);
  quit();
  assert.equal(prevented, 3, 'The final quit is allowed only after every cleanup settles.');
});

test('main shutdown waits for converter cancellation and stem cleanup after song cleanup fails', async (t) => {
  const converter = gate(), stems = gate();
  const app = loadMain(t, false, { closeSongBrowser: () => { throw new Error('Fixture song cleanup failure'); } });
  let converterStops = 0, stemStops = 0;
  app.stubShutdown(() => { converterStops++; return converter.promise; }, () => { stemStops++; return stems.promise; });
  try {
    app.events.get('before-quit')({ preventDefault() {} }); await tick();
    assert.equal(app.state.quits, 0);
    assert.equal(converterStops, 1); assert.equal(stemStops, 1);
    converter.resolve(); await tick();
    assert.equal(app.state.quits, 0, 'The remaining stem cleanup still holds the shutdown barrier.');
  } finally { converter.resolve(); stems.resolve(); }
  await tick();
  assert.equal(app.state.quits, 1);
  assert.equal(app.state.browserCloses, 1);
});

for (const packaged of [false, true]) {
  test(`${packaged ? "packaged" : "development"} inspection serves cover/tone URLs and retires them on shutdown`, async (t) => {
    const app = loadMain(t, packaged);
    const coverPath = path.join(app.temporary, "feedforge-inspect-cache", "cover.png");
    fs.writeFileSync(coverPath, "cover image");
    app.stubInspection({
      stdout: JSON.stringify({ ok: true, preview: {
        cover_path: coverPath, tones: [{ definitions: [{ gear: [{ key: "FB_Mock" }] }] }]
      } }), code: 0
    });
    const result = await app.ipc.get("converter:inspect")({}, path.join(app.base, "song.psarc"));
    const gear = result.preview.tones[0].definitions[0].gear[0];
    assert.equal(result.preview.cover_path, coverPath);
    assert.equal(gear.asset_path, app.tonePath);
    const handler = app.handlers.get("feedforge-local");
    for (const [url, expected] of [[result.preview.cover_url, "cover image"], [gear.asset_url, "tone image"]]) {
      assert.match(url, /^feedforge-local:\/\/asset\/[a-f0-9]{32}$/);
      const response = await handler({ url });
      assert.equal(response.status, 200);
      assert.equal(response.headers.get("content-type"), "image/png");
      assert.equal(response.headers.get("x-content-type-options"), "nosniff");
      assert.equal(await response.text(), expected);
    }
    assert.equal((await handler({ url: "file:///outside.png" })).status, 404);
    app.events.get("before-quit")({ preventDefault() {} });
    await new Promise(setImmediate);
    assert.equal(app.handlers.has("feedforge-local"), false);
    assert.equal(app.state.quits, 1);
    assert.equal((await handler({ url: result.preview.cover_url })).status, 404);
  });
}

test("renderer image bindings consume protocol URLs instead of native file URLs", () => {
  const source = fs.readFileSync(path.resolve(__dirname, "../../ui/src/main.jsx"), "utf8");
  assert.match(source, /const cover = preview\?\.cover_url \|\| null;/);
  assert.match(source, /<img src=\{gear\.asset_url\}/);
  assert.doesNotMatch(source, /file:\/\/\/\$\{(?:preview\.?\??\.cover_path|gear\.asset_path)/);
});

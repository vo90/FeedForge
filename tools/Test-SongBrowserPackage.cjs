'use strict';

// Developer-side artifact verification only. This never launches or automates
// Electron, opens an account page, or reads an existing browser profile.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { spawn } = require('node:child_process');
const asar = require('@electron/asar');
const { NtExecutable, NtExecutableResource, Resource } = require('resedit');

const PRODUCT = 'FeedForge Song Browser Test';
const REQUIRED_MODULES = ['main.cjs', 'preload.cjs', ...['index', 'browser', 'dom', 'host-actions', 'jobs', 'diagnostics', 'feedback'].map((name) => `song-browser/${name}.cjs`)];
const digest = (bytes) => crypto.createHash('sha256').update(bytes).digest('hex');
const cleanKey = (value) => value.replace(/\\/g, '/').replace(/^\/+/, '');
const inside = (root, target) => { const relative = path.relative(root, target); return Boolean(relative) && relative !== '..' && !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative); };

async function hashFile(filename) {
  const hash = crypto.createHash('sha256');
  for await (const bytes of fs.createReadStream(filename)) hash.update(bytes);
  return hash.digest('hex');
}

function argumentsFrom(argv) {
  const result = {};
  const supported = new Set(['--package-root', '--root', '--source-root', '--ui-stage', '--psarc']);
  for (let i = 0; i < argv.length; i += 2) {
    if (!supported.has(argv[i]) || result[argv[i]] || !argv[i + 1] || argv[i + 1].startsWith('--')) {
      throw new Error('Use --package-root <extracted app folder> --root <NEW report folder>; optional --source-root, --ui-stage and --psarc each require a path.');
    }
    result[argv[i]] = path.resolve(argv[i + 1]);
  }
  for (const required of ['--package-root', '--root']) if (!result[required]) throw new Error(`Explicit ${required} is required.`);
  return result;
}

function realDirectory(filename) {
  const resolved = fs.realpathSync(filename);
  assert.ok(fs.statSync(resolved).isDirectory(), `${filename} must be a directory.`);
  return resolved;
}

function regularFile(filename) {
  const stat = fs.lstatSync(filename);
  assert.ok(stat.isFile() && !stat.isSymbolicLink(), `${filename} must be a regular file.`);
  return stat;
}

function fileList(root) {
  return fs.readdirSync(root, { withFileTypes: true }).flatMap((entry) => {
    assert.ok(!entry.isSymbolicLink(), 'Source comparison does not follow symbolic links.');
    const target = path.join(root, entry.name);
    return entry.isDirectory() ? fileList(target) : [target];
  });
}

async function verifyStartupHooks({ source, metadata, archive, converter, reportRoot, portable }) {
  // Execute the packaged main module with in-memory Electron/FS stand-ins.
  // These are assertions about startup logic, not a desktop launch or UI test.
  const virtualRoot = path.join(reportRoot, portable ? 'simulated-portable' : 'simulated-appdata');
  const profile = path.join(virtualRoot, 'FeedForge Song Browser Data');
  const paths = { appData: virtualRoot };
  const ready = [], timers = [], windows = [], filesystem = [], logs = [];
  const forbidden = [], handlers = new Map();
  let locks = 0, registrations = 0, converterRunner;
  const deny = (name) => (..._args) => { forbidden.push(name); throw new Error(`Verification blocked unexpected ${name}.`); };
  const ownedVirtual = (filename) => {
    const resolved = path.resolve(filename);
    assert.ok(resolved === profile || inside(profile, resolved), 'Startup writes must stay inside the isolated test profile.');
    return resolved;
  };
  const fakeFs = {
    mkdirSync: (filename) => filesystem.push({ operation: 'mkdir', path: ownedVirtual(filename) }),
    appendFileSync: (filename, value) => { filesystem.push({ operation: 'append', path: ownedVirtual(filename) }); logs.push(String(value)); },
    existsSync: (filename) => path.resolve(filename) === converter,
    rmSync: (filename) => filesystem.push({ operation: 'remove', path: ownedVirtual(filename) }),
    readdirSync: deny('directory enumeration / stale portable cleanup'),
    statSync: deny('unexpected filesystem inspection'),
  };
  const app = new EventEmitter();
  Object.assign(app, {
    isPackaged: true,
    getAppPath: () => archive,
    getVersion: () => metadata.version,
    getName: () => metadata.name,
    getPath: (name) => { assert.ok(paths[name], `Unexpected app path: ${name}`); return paths[name]; },
    setPath: (name, value) => { paths[name] = value; },
    requestSingleInstanceLock: () => { locks += 1; return true; },
    whenReady: () => ({ then(callback) { ready.push(callback); return this; } }),
    quit: deny('app quit during normal simulated startup'),
  });
  class FakeWindow extends EventEmitter {
    constructor(options) { super(); this.options = options; this.webContents = new EventEmitter(); windows.push(this); }
    loadFile(filename, options) { this.loaded = { filename, options }; return Promise.resolve(); }
    loadURL() { return deny('external window navigation')(); }
    isDestroyed() { return false; }
    show() {}
    focus() {}
    static getAllWindows() { return windows; }
  }
  const fakeProcess = new EventEmitter();
  Object.assign(fakeProcess, { env: portable ? { PORTABLE_EXECUTABLE_DIR: virtualRoot } : {}, platform: 'win32', arch: 'x64', resourcesPath: path.dirname(archive), pid: 1 });
  const electron = {
    app, BrowserWindow: FakeWindow, Menu: { setApplicationMenu() {} },
    dialog: { showOpenDialog: deny('native dialog') }, session: {},
    ipcMain: { handle: (name, handler) => handlers.set(name, handler), on() {} },
    shell: { openExternal: deny('external URL'), openPath: deny('external application') },
  };
  const sandboxRequire = (name) => {
    if (name === 'electron') return electron;
    if (name === 'fs') return fakeFs;
    if (name === 'path') return path;
    if (name === 'http' || name === 'https') return { get: deny('network request'), request: deny('network request') };
    if (name === 'child_process') return { spawn: deny('child process'), execFileSync: deny('child process') };
    if (name === path.join(archive, 'package.json')) return metadata;
    if (name === './song-browser/index.cjs') return { registerSongBrowser(options) { registrations += 1; converterRunner = options.runConverter; } };
    throw new Error(`Unexpected dependency in packaged startup: ${name}`);
  };
  const module = { exports: {} };
  const context = vm.createContext({ require: sandboxRequire, module, exports: module.exports,
    __dirname: path.join(archive, 'electron'), __filename: path.join(archive, 'electron', 'main.cjs'),
    process: fakeProcess, Buffer, URL,
    setTimeout: (callback) => { timers.push(callback); return timers.length; }, clearTimeout() {},
    setInterval: deny('background interval'), clearInterval() {},
  }, { codeGeneration: { strings: false, wasm: false } });
  new vm.Script(`(function () {\n${source}\nmodule.exports.packageVerifierConverterCommand = converterCommand;\n})();`, { filename: 'packaged-electron-main.cjs' }).runInContext(context, { timeout: 2000 });
  for (const callback of ready) await callback();
  assert.equal(locks, 1, 'The test profile must obtain a single-instance lock.');
  assert.equal(paths.userData, profile);
  assert.equal(paths.sessionData, profile);
  assert.equal(paths.temp, path.join(profile, 'temp'));
  assert.equal(windows.length, 1);
  assert.equal(windows[0].options.title, `${PRODUCT} ${metadata.version}`);
  assert.equal(windows[0].loaded.filename, path.join(archive, 'desktop-dist', 'index.html'));
  assert.equal(windows[0].loaded.options.hash, 'songs');
  assert.equal(windows[0].options.webPreferences.nodeIntegration, false);
  assert.equal(windows[0].options.webPreferences.contextIsolation, true);
  let titleProtected = false;
  windows[0].emit('page-title-updated', { preventDefault() { titleProtected = true; } });
  assert.ok(titleProtected, 'The page title must preserve the distinct test window title.');
  assert.equal(registrations, 1);
  assert.equal(typeof converterRunner, 'function');
  for (const timer of timers) await timer();
  assert.ok(handlers.has('updates:check') && handlers.has('updates:openLatest'));
  const update = await handlers.get('updates:check')();
  assert.equal(update.ok, true);
  assert.equal(update.updateAvailable, false);
  assert.equal(update.releaseUrl, '');
  const openUpdate = await handlers.get('updates:openLatest')(null, 'https://github.com/balki97/FeedForge/releases/latest');
  assert.equal(openUpdate.ok, false);
  const command = module.exports.packageVerifierConverterCommand();
  assert.equal(command.command, converter, 'Packaged mode must select the bundled converter.');
  assert.equal(command.cwd, path.dirname(converter));
  assert.equal(command.prefix.length, 0, 'The frozen converter must not depend on a Python module invocation.');
  assert.equal(forbidden.length, 0, 'No network, external process, dialog or global cleanup should be requested.');
  assert.ok(logs.some((line) => line.includes('app.ready')));
  return { mode: 'simulated; desktop application not launched', profileMode: portable ? 'explicit portable directory' : 'separate app-data fallback',
    profileIsolation: true, initialView: 'songs', title: windows[0].options.title, titleProtected,
    registrationHookCalled: true, bundledConverterSelected: true, updaterCheckDisabled: true, updaterOpenDisabled: true,
    ordinaryPortableCleanupSkipped: true, forbiddenActionsRequested: forbidden.length,
    simulatedFilesystemOperations: filesystem.length };
}

async function runConverterSmoke(converter, psarc, reportRoot) {
  const childRoot = path.join(reportRoot, 'converter-smoke');
  const script = path.join(__dirname, 'Test-SongBrowserConverter.cjs');
  await new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [script, '--converter', converter, '--input', psarc, '--root', childRoot], { windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
    let stdout = '', stderr = '', spawnError;
    child.stdout.on('data', (chunk) => { stdout += chunk; });
    child.stderr.on('data', (chunk) => { stderr += chunk; });
    child.once('error', (error) => { spawnError = error; });
    child.once('close', (code) => {
      fs.writeFileSync(path.join(reportRoot, 'converter.stdout.log'), stdout, { flag: 'wx' });
      fs.writeFileSync(path.join(reportRoot, 'converter.stderr.log'), stderr, { flag: 'wx' });
      if (spawnError) reject(spawnError);
      else if (code !== 0) reject(new Error(`Frozen converter smoke failed; see converter.stderr.log (exit ${code}).`));
      else resolve();
    });
  });
  const result = JSON.parse(fs.readFileSync(path.join(childRoot, 'result.json'), 'utf8'));
  assert.equal(result.ok, true);
  return { status: 'passed', report: path.join(childRoot, 'result.json'), sourceUnchanged: true, audio: result.audio, outputBytes: result.output.bytes };
}

async function main() {
  const args = argumentsFrom(process.argv.slice(2));
  const packageRoot = realDirectory(args['--package-root']);
  const sourceRoot = args['--source-root'] ? realDirectory(args['--source-root']) : null;
  const uiStage = args['--ui-stage'] ? realDirectory(args['--ui-stage']) : null;
  const reportParent = realDirectory(path.dirname(args['--root']));
  const reportRoot = path.join(reportParent, path.basename(args['--root']));
  if (fs.existsSync(reportRoot)) throw new Error('--root must be NEW; existing reports, profiles and libraries are never reused.');
  for (const protectedRoot of [packageRoot, sourceRoot, uiStage, path.resolve(__dirname, '..')].filter(Boolean)) {
    assert.ok(reportRoot !== protectedRoot && !inside(protectedRoot, reportRoot), 'Put the new report folder outside package/source/staged UI directories.');
  }
  if (args['--psarc']) regularFile(args['--psarc']);
  fs.mkdirSync(reportRoot);
  fs.writeFileSync(path.join(reportRoot, 'OWNED_PACKAGE_VERIFICATION.txt'), 'Local artifact inspection and simulated startup verification. No desktop app launch, browser automation, account sign-in or game/library import.\n', { flag: 'wx' });
  const report = { ok: false, packageRoot, createdAt: new Date().toISOString(),
    scope: { desktopLaunched: false, browserAutomated: false, externalHostsContacted: false, liveSearchDownloadTested: false, gamePlaybackTested: false },
    sourceComparison: sourceRoot ? { requested: true, sourceRoot } : { requested: false },
    uiComparison: uiStage ? { requested: true, uiStage } : { requested: false },
    converterSmoke: { status: args['--psarc'] ? 'pending' : 'not requested' } };
  try {
    const executable = path.join(packageRoot, `${PRODUCT}.exe`);
    const archive = path.join(packageRoot, 'resources', 'app.asar');
    const converter = path.join(packageRoot, 'resources', 'bin', 'psarc2feedpak', 'psarc2feedpak.exe');
    regularFile(executable); regularFile(archive); regularFile(converter);
    const entries = asar.listPackage(archive).map(cleanKey);
    for (const name of entries) assert.ok(!name.split('/').includes('..'), 'Unexpected traversal segment in ASAR path.');
    const extract = (name) => asar.extractFile(archive, name.split('/').join(path.sep));
    const metadata = JSON.parse(extract('package.json').toString('utf8').replace(/^\uFEFF/, ''));
    assert.equal(metadata.songBrowserTest, true);
    assert.equal(metadata.name, 'feedforge-song-browser-test');
    assert.equal(metadata.main, 'electron/main.cjs');
    const executableResources = NtExecutableResource.from(NtExecutable.from(fs.readFileSync(executable))).entries;
    const versionStrings = Resource.VersionInfo.fromEntries(executableResources).flatMap((info) => info.getAllLanguagesForStringValues().map((language) => info.getStringValues(language)));
    assert.ok(versionStrings.some((strings) => strings.ProductName === PRODUCT && strings.FileDescription === PRODUCT), 'Executable resources must identify the distinct test application.');
    report.identity = { name: metadata.name, version: metadata.version, songBrowserTest: true, executableProduct: PRODUCT };
    for (const name of REQUIRED_MODULES) assert.ok(entries.includes(`electron/${name}`), `Required packaged module missing: ${name}`);
    const runtimeKeys = entries.filter((name) => /^electron\/.*\.cjs$/.test(name));
    const uiKeys = entries.filter((name) => /^desktop-dist\/.*\.(?:html|js|css)$/.test(name));
    assert.ok(uiKeys.includes('desktop-dist/index.html'));
    assert.ok(uiKeys.some((name) => name.endsWith('.js')) && uiKeys.some((name) => name.endsWith('.css')), 'The production UI must include JavaScript and styles.');
    report.files = [...runtimeKeys, ...uiKeys].map((name) => ({ path: name, sha256: digest(extract(name)) }));
    if (sourceRoot) {
      const sourceKeys = fileList(path.join(sourceRoot, 'electron')).filter((filename) => filename.endsWith('.cjs')).map((filename) => cleanKey(path.relative(sourceRoot, filename)));
      assert.deepEqual([...runtimeKeys].sort(), sourceKeys.sort(), 'Source and package must contain the same runtime module set.');
      for (const name of runtimeKeys) assert.equal(digest(extract(name)), digest(fs.readFileSync(path.join(sourceRoot, ...name.split('/')))), `Packaged runtime differs from source: ${name}`);
      report.sourceComparison.passed = true;
    }
    if (uiStage) {
      for (const name of uiKeys) assert.equal(digest(extract(name)), digest(fs.readFileSync(path.join(uiStage, ...name.split('/').slice(1)))), `Packaged UI differs from supplied staged build: ${name}`);
      report.uiComparison.passed = true;
    }
    const html = extract('desktop-dist/index.html').toString('utf8');
    for (const match of html.matchAll(/(?:src|href)=["']([^"']+)["']/g)) {
      assert.ok(match[1].startsWith('./'), 'Production HTML should reference packaged relative assets only.');
      assert.ok(entries.includes(`desktop-dist/${match[1].slice(2)}`), `Referenced UI asset missing: ${match[1]}`);
    }
    const tools = path.join(path.dirname(converter), '_internal', 'feedback_converter', 'tools');
    for (const name of ['vgmstream-cli.exe', 'ffmpeg.exe']) regularFile(path.join(tools, name));
    report.artifacts = await Promise.all([executable, archive, converter].map(async (filename) => ({ path: filename, bytes: regularFile(filename).size, sha256: await hashFile(filename) })));
    const mainSource = extract('electron/main.cjs').toString('utf8');
    report.startupLogic = [];
    for (const portable of [true, false]) report.startupLogic.push(await verifyStartupHooks({ source: mainSource, metadata, archive, converter, reportRoot, portable }));
    if (args['--psarc']) report.converterSmoke = await runConverterSmoke(converter, args['--psarc'], reportRoot);
    report.ok = true;
  } catch (error) { report.error = error.message; throw error; }
  finally {
    fs.writeFileSync(path.join(reportRoot, 'result.json'), JSON.stringify(report, null, 2), { flag: 'wx' });
    process.stdout.write(JSON.stringify({ ok: report.ok, report: path.join(reportRoot, 'result.json'), desktopLaunched: false, sourceComparison: report.sourceComparison, uiComparison: report.uiComparison, converterSmoke: report.converterSmoke, error: report.error }, null, 2) + '\n');
  }
}

main().catch((error) => { process.stderr.write(error.stack + '\n'); process.exitCode = 1; });

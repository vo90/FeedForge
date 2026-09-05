'use strict';

// Explicit installed-backend integration: use --mode plan before --mode run.
// Every run requires a NEW directory; it never imports into a real library.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const fsp = fs.promises;
const path = require('node:path');
const crypto = require('node:crypto');
const net = require('node:net');
const { spawn, spawnSync, execFile } = require('node:child_process');
const { setTimeout: delay } = require('node:timers/promises');
const { inspectFeedback, refreshFeedback } = require('../electron/song-browser/feedback.cjs');

const ROOT_BRANCH = 'feat/customsforge-song-browser';
const BASELINE = '804aa8c91c0cc26809ab8ef97093c0b5d7fa042c';

function parseArgs(argv) {
  const supported = new Set(['source', 'python', 'input', 'root', 'version', 'port', 'mode', 'scenario']);
  const args = {};
  for (let i = 0; i < argv.length; i += 2) {
    const key = argv[i]?.replace(/^--/, '');
    assert.ok(argv[i]?.startsWith('--') && supported.has(key) && argv[i + 1] && !argv[i + 1].startsWith('--') && !args[key], 'Use explicit key/value arguments without duplicates.');
    args[key] = argv[i + 1];
  }
  for (const key of ['source', 'python', 'input', 'root', 'version', 'port', 'mode']) assert.ok(args[key], `--${key} is required.`);
  for (const key of ['source', 'python', 'input', 'root']) assert.ok(path.isAbsolute(args[key]), `--${key} must be absolute.`);
  assert.ok(['plan', 'run'].includes(args.mode));
  args.scenario ||= 'config';
  assert.ok(['config', 'env-override'].includes(args.scenario));
  args.port = Number(args.port);
  assert.ok(Number.isInteger(args.port) && args.port >= 1024 && args.port <= 65535);
  return args;
}

async function hashFile(filename) {
  const hash = crypto.createHash('sha256');
  for await (const chunk of fs.createReadStream(filename)) hash.update(chunk);
  return hash.digest('hex');
}

function command(executable, args, options = {}) {
  const result = spawnSync(executable, args, { windowsHide: true, encoding: 'utf8', timeout: 30000, maxBuffer: 1024 * 1024, ...options });
  assert.equal(result.status, 0, result.error?.message || result.stderr || result.stdout);
  return result.stdout.trim();
}

async function portFree(port) {
  await new Promise((resolve, reject) => {
    const probe = net.createServer();
    probe.once('error', reject);
    probe.listen(port, '127.0.0.1', () => probe.close(resolve));
  });
}

async function sourceManifest(source) {
  const files = {};
  async function visit(directory, relative = '') {
    for (const item of await fsp.readdir(directory, { withFileTypes: true })) {
      if (item.isSymbolicLink()) throw new Error(`Source has an unexpected symlink: ${item.name}`);
      const name = relative ? `${relative}/${item.name}` : item.name;
      if (item.isDirectory() && ['lib', 'plugins'].includes(name.split('/')[0]) && item.name !== '__pycache__') await visit(path.join(directory, item.name), name);
      else if (item.isFile() && (item.name.endsWith('.py') || item.name === 'VERSION' || item.name === 'plugin.json')) files[name] = await hashFile(path.join(directory, item.name));
    }
  }
  await visit(source);
  return files;
}

async function request(base, route, options = {}) {
  assert.ok(route.startsWith('/') && !route.startsWith('//'), 'Only local relative test requests are supported.');
  const response = await fetch(base + route, { redirect: 'error', signal: AbortSignal.timeout(15000), ...options });
  assert.equal(response.status, 200, `${route}: HTTP ${response.status}`);
  return response;
}

async function json(base, route, options) { return (await request(base, route, options)).json(); }

async function highway(base, filename, index) {
  return new Promise((resolve, reject) => {
    const socket = new WebSocket(base.replace('http:', 'ws:') + `/ws/highway/${encodeURIComponent(filename)}?arrangement=${index}`);
    let info, notes = 0, chords = 0, ready = false;
    const messages = [];
    const timer = setTimeout(() => { socket.close(); reject(new Error('Highway API did not become ready within 30 seconds.')); }, 30000);
    const fail = (error) => { clearTimeout(timer); socket.close(); reject(error); };
    socket.addEventListener('error', () => fail(new Error('Highway WebSocket failed.')));
    socket.addEventListener('message', (event) => {
      try {
        const message = JSON.parse(String(event.data));
        if (message.error) throw new Error(message.error);
        messages.push(message.type);
        if (message.type === 'song_info') info = message;
        if (message.type === 'notes') notes += (message.data || []).length;
        if (message.type === 'chords') chords += (message.data || []).length;
        if (message.type === 'ready') {
          assert.ok(info && info.duration > 0 && info.arrangement_index === index, 'The requested real arrangement must load.');
          assert.ok(notes + chords > 0, 'The chart must contain playable events.');
          assert.ok(info.audio_url && !info.audio_error, 'The loaded chart must expose playable audio.');
          ready = true; clearTimeout(timer); socket.close();
          resolve({ info, notes, chords, messages });
        }
      } catch (error) { fail(error); }
    });
    socket.addEventListener('close', () => { if (!ready) fail(new Error('Highway closed before ready.')); });
  });
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const source = await fsp.realpath(args.source), python = await fsp.realpath(args.python), input = await fsp.realpath(args.input);
  assert.equal((await fsp.stat(python)).isFile(), true);
  assert.equal((await fsp.stat(input)).isFile(), true);
  assert.equal(path.extname(input).toLowerCase(), '.feedpak');
  const resources = path.dirname(source);
  assert.equal(path.dirname(path.dirname(python)), resources, 'Use the interpreter shipped beside this installed source.');
  assert.equal((await fsp.readFile(path.join(source, 'VERSION'), 'utf8')).trim(), args.version, 'Installed version changed; inspect before launching.');
  assert.equal(fs.existsSync(path.join(source, '.git')), false, 'This test targets a packaged installed backend, not an editable core checkout.');
  const files = await sourceManifest(source);
  assert.ok(files['server.py'] && files['main.py'] && files['plugins/__init__.py']);
  const serverText = await fsp.readFile(path.join(source, 'server.py'), 'utf8');
  for (const gate of ['FEEDBACK_SKIP_STARTUP_TASKS', 'CONFIG_DIR', 'FEEDBACK_PLUGINS_DIR']) assert.ok(serverText.includes(gate), `Backend lacks isolation gate ${gate}.`);
  const cwd = path.resolve(__dirname, '..');
  const branch = command('git', ['branch', '--show-current'], { cwd });
  assert.equal(branch, ROOT_BRANCH);
  command('git', ['merge-base', '--is-ancestor', BASELINE, 'HEAD'], { cwd });
  const revision = command('git', ['rev-parse', 'HEAD'], { cwd });
  const root = path.resolve(args.root);
  assert.equal(fs.existsSync(root), false, 'Use a NEW disposable runtime; existing or partial state is never repaired.');
  const rootParent = await fsp.realpath(path.dirname(root));
  assert.equal(path.dirname(root), rootParent, 'Runtime parent must be a resolved existing directory.');
  assert.ok(!root.startsWith(source + path.sep) && !root.startsWith(cwd + path.sep));
  await portFree(args.port);
  const configDir = path.join(root, 'profile'), library = path.join(root, 'library');
  const effectiveLibrary = args.scenario === 'env-override' ? path.join(root, 'effective-library') : library;
  const isolation = {
    CONFIG_DIR: configDir, DLC_DIR: args.scenario === 'env-override' ? effectiveLibrary : '',
    FEEDBACK_PLUGINS_DIR: path.join(root, 'user-plugins'), FEEDBACK_SKIP_STARTUP_TASKS: '1', FEEDBACK_ENRICH_OFFLINE: '1', FEEDBACK_MAX_SCAN_WORKERS: '1',
    FEEDBACK_PROGRESSION_DATA: path.join(root, 'progression'), LOG_FILE: path.join(root, 'logs', 'backend.log'), LOG_FORMAT: 'json',
    HOST: '127.0.0.1', PORT: String(args.port), TEMP: path.join(root, 'temp'), TMP: path.join(root, 'temp'), TMPDIR: path.join(root, 'temp'),
    USERPROFILE: path.join(root, 'home'), HOME: path.join(root, 'home'), APPDATA: path.join(root, 'home', 'AppData', 'Roaming'), LOCALAPPDATA: path.join(root, 'home', 'AppData', 'Local'),
    PYTHONDONTWRITEBYTECODE: '1', PYTHONNOUSERSITE: '1',
  };
  const env = { ...process.env };
  for (const key of Object.keys(env)) if (/^(?:FEEDBACK|SLOPSMITH|FEEDFORGE|PYTHON|VIRTUAL_ENV|CONDA|APP_VERSION|APP_SOURCE_URL|APP_LICENSE_URL|DLC_DIR|CONFIG_DIR|LOG_|HOST$|PORT$)/i.test(key) || key.toLowerCase() === 'path') delete env[key];
  const systemRoot = process.env.SystemRoot || process.env.SYSTEMROOT;
  assert.ok(systemRoot && path.isAbsolute(systemRoot));
  env.PATH = [path.join(systemRoot, 'System32'), systemRoot, path.join(resources, 'bin')].join(path.delimiter);
  Object.assign(env, isolation);
  const interpreter = JSON.parse(command(python, ['-I', '-B', '-c', 'import sys,json; print(json.dumps({"executable":sys.executable,"version":sys.version,"paths":sys.path,"dontWrite":sys.dont_write_bytecode}))'], { env, cwd: rootParent }));
  assert.equal(await fsp.realpath(interpreter.executable), python);
  assert.equal(interpreter.dontWrite, true);
  assert.ok(interpreter.paths.every((entry) => path.resolve(entry).startsWith(resources + path.sep)), 'Interpreter search path escaped the installed bundle.');
  const sourceStat = await fsp.stat(input);
  const sourceHash = await hashFile(input);
  const spec = { schema: 1, runtime: root, source, python, version: args.version, interpreter,
    installedRevision: 'Packaged source; immutable file hashes substitute for absent Git metadata.', sourceFiles: files,
    pythonHash: await hashFile(python), branch, revision, sourceBaseline: BASELINE, port: args.port, effectiveLibrary,
    configuredLibrary: library, isolation, pluginComposition: 'No plugins: explicit startup-task skip; empty isolated user-plugin root.',
    scenario: args.scenario, input: { path: input, hash: sourceHash, bytes: sourceStat.size, mtimeMs: sourceStat.mtimeMs }, identityToken: crypto.randomUUID() };
  process.stdout.write(JSON.stringify({ mode: args.mode, runtime: root, source, version: args.version, python, port: args.port,
    profile: configDir, library, effectiveLibrary, sourceFiles: Object.keys(files).length, pluginComposition: spec.pluginComposition, branch, revision }, null, 2) + '\n');
  if (args.mode === 'plan') return;

  await fsp.mkdir(root);
  const directories = [configDir, library, effectiveLibrary, isolation.FEEDBACK_PLUGINS_DIR, isolation.FEEDBACK_PROGRESSION_DATA,
    path.dirname(isolation.LOG_FILE), isolation.TEMP, isolation.USERPROFILE, isolation.APPDATA, isolation.LOCALAPPDATA];
  for (const directory of directories) await fsp.mkdir(directory, { recursive: true });
  await fsp.writeFile(path.join(root, 'runtime.json'), JSON.stringify(spec, null, 2), { flag: 'wx' });
  await fsp.writeFile(path.join(configDir, 'config.json'), JSON.stringify({ dlc_dir: library, enrich_enabled: false, achievements_enabled: false, use_amp_sims: false }), { flag: 'wx' });
  const result = { ok: false, backendApiOnly: true, visualPlaybackTested: false, root, scenario: args.scenario, spec: path.join(root, 'runtime.json') };
  const base = `http://127.0.0.1:${args.port}`;
  const child = spawn(python, ['-I', '-B', path.join(__dirname, 'feedback-integration-server.py'), path.join(root, 'runtime.json')],
    { env, cwd: root, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
  const stdout = fs.createWriteStream(path.join(root, 'server.stdout.log'), { flags: 'wx' });
  const stderr = fs.createWriteStream(path.join(root, 'server.stderr.log'), { flags: 'wx' });
  child.stdout.pipe(stdout); child.stderr.pipe(stderr);
  let closed = false, spawnError;
  child.once('error', (error) => { spawnError = error; });
  const exited = new Promise((resolve) => child.once('close', (code, signal) => { closed = true; result.serverExit = { code, signal }; resolve(); }));
  let identityVerified = false;
  try {
    for (let attempt = 0; attempt < 120; attempt++) {
      if (spawnError || closed) throw spawnError || new Error('The isolated server exited before readiness. Inspect its logs.');
      try {
        result.identity = await json(base, '/__feedforge_smoke_identity/' + spec.identityToken);
        break;
      } catch { await delay(250); }
    }
    assert.equal(result.identity?.pid, child.pid, 'Only the child started by this test may receive requests.');
    assert.equal(path.resolve(result.identity.root), root);
    assert.equal(path.resolve(result.identity.configDir), configDir);
    assert.equal(path.resolve(result.identity.effectiveLibrary), effectiveLibrary);
    assert.equal(result.identity.pluginCount, 0);
    identityVerified = true;
    result.version = await json(base, '/api/version');
    assert.equal(result.version.version, args.version);
    result.before = await json(base, '/api/library?size=100');
    assert.equal(result.before.total, 0);
    const destination = path.join(library, path.basename(input));
    await fsp.copyFile(input, destination, fs.constants.COPYFILE_EXCL);
    assert.equal(await hashFile(destination), sourceHash);
    try {
      result.inspection = await inspectFeedback(base);
      result.refresh = await refreshFeedback(base, destination);
    } catch (error) { result.bridgeError = error.message; }
    if (args.scenario === 'config') assert.ok(!result.bridgeError, result.bridgeError);
    for (let attempt = 0; attempt < 120; attempt++) {
      result.scan = await json(base, '/api/scan-status');
      if (!result.scan.running) break;
      await delay(250);
    }
    assert.equal(result.scan.running, false);
    assert.ok(!result.scan.error, result.scan.error);
    result.after = await json(base, '/api/library?size=100');
    const imported = result.after.songs.filter((song) => song.filename === path.basename(input));
    result.builtinSongs = result.after.songs.filter((song) => /^(?:starter|diagnostics-builtin)\//.test(song.filename)).map((song) => song.filename);
    assert.equal(result.after.total, imported.length + result.builtinSongs.length, 'Only the designated import and installed starter/diagnostic content may appear.');
    if (args.scenario === 'env-override') {
      result.bridgeMismatch = Boolean(result.inspection && path.resolve(result.inspection.libraryDir) !== effectiveLibrary);
      result.falseRefreshSuccess = Boolean(result.bridgeMismatch && result.refresh && imported.length === 0);
      result.ok = Boolean(result.bridgeError) || !result.bridgeMismatch;
      if (!result.ok) result.error = 'Bridge accepted config dlc_dir while the actual scanner used the different DLC_DIR environment override.';
    } else {
      assert.equal(imported.length, 1, 'The real scanner must discover the copied test song exactly once.');
      const song = imported[0];
      assert.equal(song.filename, path.basename(input));
      result.song = await json(base, '/api/song/' + encodeURIComponent(song.filename));
      const first = await highway(base, song.filename, 0);
      const arrangements = [first];
      for (const arrangement of first.info.arrangements) if (arrangement.index !== 0 && arrangement.notes > 0) arrangements.push(await highway(base, song.filename, arrangement.index));
      result.arrangements = arrangements.map(({ info, notes, chords, messages }) => ({ index: info.arrangement_index, name: info.arrangement_smart_name,
        notes, chords, duration: info.duration, audioUrl: info.audio_url, messages }));
      const audioUrl = first.info.audio_url;
      const audioResponse = await request(base, audioUrl);
      assert.match(audioResponse.headers.get('content-type') || '', /^audio\//);
      const audio = Buffer.from(await audioResponse.arrayBuffer());
      assert.ok(audio.length > 1000);
      assert.equal(audio.subarray(0, 4).toString('ascii'), 'OggS', 'Expected the converted Ogg audio stream.');
      result.audio = { url: audioUrl, contentType: audioResponse.headers.get('content-type'), bytes: audio.length, sha256: crypto.createHash('sha256').update(audio).digest('hex') };
      result.ok = true;
    }
    assert.equal(await hashFile(input), sourceHash);
    assert.equal((await fsp.stat(input)).mtimeMs, sourceStat.mtimeMs);
    assert.equal(await hashFile(destination), sourceHash, 'The backend must not modify the copied package.');
    result.sourceUnchanged = true;
  } catch (error) { result.error = error.stack || String(error); }
  finally {
    if (!closed && identityVerified) { try { await json(base, '/__feedforge_smoke_stop/' + spec.identityToken, { method: 'POST' }); } catch { /* Fall back only to our own child. */ } }
    await Promise.race([exited, delay(10000)]);
    if (!closed) {
      await new Promise((resolve) => execFile('taskkill.exe', ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true }, () => resolve()));
      await Promise.race([exited, delay(5000)]);
    }
    result.serverStopped = closed;
    result.sourceTreeUnchanged = JSON.stringify(await sourceManifest(source)) === JSON.stringify(files);
    result.interpreterUnchanged = await hashFile(python) === spec.pythonHash;
    result.ok = result.ok && closed && result.sourceTreeUnchanged && result.interpreterUnchanged;
    await fsp.writeFile(path.join(root, 'result.json'), JSON.stringify(result, null, 2), { flag: 'wx' });
    process.stdout.write(JSON.stringify({ ok: result.ok, result: path.join(root, 'result.json'), bridgeMismatch: result.bridgeMismatch,
      falseRefreshSuccess: result.falseRefreshSuccess, songCount: result.after?.total, arrangements: result.arrangements, audio: result.audio,
      serverStopped: result.serverStopped, error: result.error }, null, 2) + '\n');
    if (!result.ok) process.exitCode = 1;
  }
}

main().catch((error) => { process.stderr.write((error.stack || String(error)) + '\n'); process.exitCode = 1; });

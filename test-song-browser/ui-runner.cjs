'use strict';

// Build and test the real renderer without opening the user's app or a server.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { spawn, execFileSync } = require('node:child_process');

const checkout = fs.realpathSync.native(path.resolve(__dirname, '..'));
const baseline = '804aa8c91c0cc26809ab8ef97093c0b5d7fa042c';
const hash = (file) => crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const inside = (root, file) => {
  const relative = path.relative(root, file);
  return relative !== '' && relative !== '..' && !relative.startsWith('..' + path.sep) && !path.isAbsolute(relative);
};
function inventory(root, entries) {
  const hashes = {};
  function visit(relative) {
    const file = path.join(root, relative);
    const stat = fs.lstatSync(file);
    if (stat.isSymbolicLink()) throw new Error('Source/output links are not permitted: ' + file);
    if (stat.isDirectory()) {
      for (const name of fs.readdirSync(file).sort()) visit(path.join(relative, name));
    } else if (stat.isFile()) hashes[relative.split(path.sep).join('/')] = hash(file);
    else throw new Error('Unsupported source/output entry: ' + file);
  }
  for (const entry of entries) visit(entry);
  return hashes;
}
const git = (...args) => execFileSync('git', args, { cwd: checkout, encoding: 'utf8', windowsHide: true }).trim();
function sourceState(expectedBranch) {
  const branch = git('branch', '--show-current');
  if (branch !== expectedBranch) throw new Error(`Expected branch ${expectedBranch}; found ${branch}.`);
  git('merge-base', '--is-ancestor', baseline, 'HEAD');
  return {
    branch, revision: git('rev-parse', 'HEAD'), baseline,
    gitStatus: git('status', '--porcelain=v1', '--untracked-files=all'),
    sourceHashes: inventory(checkout, ['index.html', 'vite.config.js', 'package.json', 'package-lock.json', 'ui', 'electron', 'test-song-browser'])
  };
}
function same(label, before, after) {
  if (JSON.stringify(before) !== JSON.stringify(after)) throw new Error(label + ' changed during preparation; runtime retained for inspection.');
}
function dependencyIdentity() {
  const packages = ['vite', '@vitejs/plugin-react', 'esbuild', 'react', 'react-dom', 'lucide-react', 'electron'];
  return Object.fromEntries(packages.map((name) => {
    // Some installed packages intentionally do not export their manifest.
    const manifest = path.join(checkout, 'node_modules', name, 'package.json');
    const entry = name === 'electron' ? manifest : require.resolve(name, { paths: [checkout] });
    return [name, { version: JSON.parse(fs.readFileSync(manifest)).version,
      manifest: fs.realpathSync.native(manifest), manifestHash: hash(manifest), entryHash: hash(entry) }];
  }));
}
function options(args) {
  const result = { mode: 'plan', expectedBranch: 'feat/customsforge-song-browser' };
  const names = { '--electron': 'electron', '--runtime': 'runtime', '--mode': 'mode', '--expected-branch': 'expectedBranch' };
  const seen = new Set();
  for (let i = 0; i < args.length; i += 2) {
    const key = Object.hasOwn(names, args[i]) ? names[args[i]] : undefined;
    if (!key || seen.has(key) || !args[i + 1] || args[i + 1].startsWith('--')) throw new Error('Unknown, duplicate or incomplete option: ' + args[i]);
    seen.add(key); result[key] = args[i + 1];
  }
  if (!['plan', 'run'].includes(result.mode) || !path.isAbsolute(result.electron || '') || !path.isAbsolute(result.runtime || '')) {
    throw new Error('Usage: node test-song-browser/ui-runner.cjs --electron ABSOLUTE_ELECTRON_PATH --runtime FRESH_ABSOLUTE_OUTPUT_DIRECTORY [--mode plan|run] [--expected-branch BRANCH]');
  }
  return result;
}
function prepare(args) {
  const opts = options(args);
  const requested = path.resolve(opts.runtime);
  if (requested === path.parse(requested).root || fs.existsSync(requested)) throw new Error('Choose a fresh runtime directory; existing output is never reused.');
  // An existing, canonical parent prevents a hidden junction from redirecting output.
  const parent = fs.realpathSync.native(path.dirname(requested));
  const runtime = path.join(parent, path.basename(requested));
  if (runtime === checkout || inside(checkout, runtime) || inside(runtime, checkout)) throw new Error('Runtime must be outside the source checkout.');
  if (fs.existsSync(runtime)) throw new Error('Canonical runtime already exists.');
  const electronPath = fs.realpathSync.native(opts.electron);
  if (!fs.statSync(electronPath).isFile()) throw new Error('Electron executable is not a file.');
  const dependencies = dependencyIdentity();
  const version = fs.readFileSync(path.join(path.dirname(electronPath), 'version'), 'utf8').trim();
  if (version !== dependencies.electron.version) throw new Error('Electron binary version does not match the installed dependency.');
  const state = sourceState(opts.expectedBranch);
  return { opts, receipt: {
    kind: 'feedforge-song-browser-ui', runtime, sourceRoot: checkout, ...state,
    electron: { path: electronPath, version, sha256: hash(electronPath) },
    node: { path: process.execPath, version: process.version, sha256: hash(process.execPath) }, dependencies,
    profile: path.join(runtime, 'profile'), listeningPorts: [], plugins: [],
    network: 'All renderer and song browser HTTP/S responses are synthetic; unexpected requests fail.',
    conversion: 'Deterministic fixture converter; no real songs, game, account or converter process.',
    rendererHashes: {}
  } };
}
async function loggedProcess(executable, args, env, runtime, prefix, timeoutMs) {
  const stdout = fs.createWriteStream(path.join(runtime, prefix + '-stdout.log'), { flags: 'wx' });
  const stderr = fs.createWriteStream(path.join(runtime, prefix + '-stderr.log'), { flags: 'wx' });
  return new Promise((resolve, reject) => {
    const child = spawn(executable, args, { cwd: checkout, env, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      if (process.platform === 'win32' && child.pid) {
        spawn('taskkill', ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' });
      } else child.kill('SIGKILL');
    }, timeoutMs);
    child.stdout.on('data', (data) => { stdout.write(data); process.stdout.write(data); });
    child.stderr.on('data', (data) => stderr.write(data));
    child.once('error', (error) => { clearTimeout(timer); stdout.end(); stderr.end(); reject(error); });
    child.once('close', (code) => {
      clearTimeout(timer); stdout.end(); stderr.end();
      if (timedOut || code !== 0) reject(new Error(`${prefix} failed (exit ${code}, timedOut ${timedOut}); inspect ${runtime}.`));
      else resolve();
    });
  });
}
async function main(args) {
  const { opts, receipt } = prepare(args);
  process.stdout.write(JSON.stringify({ mode: opts.mode, runtime: receipt.runtime, sourceRoot: checkout,
    branch: receipt.branch, revision: receipt.revision, electron: receipt.electron,
    profile: receipt.profile, listeningPorts: [], plugins: [], network: receipt.network, conversion: receipt.conversion }, null, 2) + '\n');
  if (opts.mode === 'plan') return;
  const runtime = receipt.runtime;
  fs.mkdirSync(runtime);
  fs.writeFileSync(path.join(runtime, 'ownership.json'), JSON.stringify({ kind: receipt.kind, sourceRoot: checkout, created: new Date().toISOString() }, null, 2), { flag: 'wx' });
  for (const dir of ['profile', 'session', 'temp', 'home', 'appdata', 'localappdata']) fs.mkdirSync(path.join(runtime, dir));
  const env = { ...process.env };
  for (const key of Object.keys(env)) {
    if (/^(ELECTRON_|FEEDFORGE_|SONG_BROWSER_|PYTHON|NODE_OPTIONS$|NODE_PATH$)/i.test(key)) delete env[key];
    if (/^(APPDATA|LOCALAPPDATA|USERPROFILE|HOME|TEMP|TMP)$/i.test(key)) delete env[key];
  }
  Object.assign(env, { SONG_BROWSER_UI_RUNTIME: runtime, APPDATA: path.join(runtime, 'appdata'),
    LOCALAPPDATA: path.join(runtime, 'localappdata'), USERPROFILE: path.join(runtime, 'home'), HOME: path.join(runtime, 'home'),
    TEMP: path.join(runtime, 'temp'), TMP: path.join(runtime, 'temp') });
  const vite = path.join(path.dirname(receipt.dependencies.vite.manifest), 'bin', 'vite.js');
  await loggedProcess(process.execPath, [vite, 'build', '--outDir', path.join(runtime, 'ui'), '--emptyOutDir'], env, runtime, 'build', 90000);
  same('Source and Git state', { branch: receipt.branch, revision: receipt.revision, baseline: receipt.baseline, gitStatus: receipt.gitStatus, sourceHashes: receipt.sourceHashes }, sourceState(opts.expectedBranch));
  same('Dependency identity', receipt.dependencies, dependencyIdentity());
  if (hash(receipt.electron.path) !== receipt.electron.sha256 || hash(process.execPath) !== receipt.node.sha256) throw new Error('Runtime binary identity changed.');
  receipt.rendererHashes = inventory(path.join(runtime, 'ui'), fs.readdirSync(path.join(runtime, 'ui')).sort());
  if (!receipt.rendererHashes['index.html']) throw new Error('Production renderer build is missing index.html.');
  fs.writeFileSync(path.join(runtime, 'launch-receipt.json'), JSON.stringify(receipt, null, 2), { flag: 'wx' });
  await loggedProcess(receipt.electron.path, [path.join(__dirname, 'ui-integration-main.cjs')], env, runtime, 'electron', 150000);
  const result = JSON.parse(fs.readFileSync(path.join(runtime, 'result.json'), 'utf8'));
  if (result.status !== 'passed') throw new Error('Renderer integration report did not pass: ' + runtime);
  same('Source and Git state after tests', { branch: receipt.branch, revision: receipt.revision, baseline: receipt.baseline, gitStatus: receipt.gitStatus, sourceHashes: receipt.sourceHashes }, sourceState(opts.expectedBranch));
  process.stdout.write('UI integration passed. Evidence: ' + path.join(runtime, 'result.json') + '\n');
}

if (require.main === module) main(process.argv.slice(2)).catch((error) => { process.stderr.write(error.stack + '\n'); process.exitCode = 1; });
module.exports = { options, inside, inventory, prepare };

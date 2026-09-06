'use strict';

// Runs a disposable offline Chromium fixture. No live provider, account,
// existing browser profile or user output folder is used.
const fs = require('node:fs');
const path = require('node:path');
const { spawn } = require('node:child_process');
const args = process.argv.slice(2);
const options = {};
for (let index = 0; index < args.length; index += 2) {
  if (!['--electron', '--runtime'].includes(args[index]) || options[args[index]] || !args[index + 1]) throw new Error('Use --electron ABSOLUTE_PATH --runtime FRESH_ABSOLUTE_DIRECTORY.');
  options[args[index]] = args[index + 1];
}
const electron = options['--electron'], runtime = options['--runtime'];
if (!path.isAbsolute(electron || '') || !path.isAbsolute(runtime || '') || !fs.statSync(electron).isFile()) throw new Error('Provide an existing Electron executable and a fresh absolute runtime directory.');
if (fs.existsSync(runtime)) throw new Error('The test runtime already exists; choose a fresh directory.');
const checkout = fs.realpathSync.native(path.resolve(__dirname, '..'));
const parent = fs.realpathSync.native(path.dirname(runtime));
const resolved = path.join(parent, path.basename(runtime));
const relative = path.relative(checkout, resolved);
if (!relative || (!relative.startsWith('..' + path.sep) && !path.isAbsolute(relative))) throw new Error('Keep test output outside the source checkout.');
fs.mkdirSync(resolved);
for (const name of ['home', 'appdata', 'localappdata', 'temp']) fs.mkdirSync(path.join(resolved, name));
const env = { ...process.env };
for (const key of Object.keys(env)) if (/^(ELECTRON_|NODE_OPTIONS$|NODE_PATH$|SONG_BROWSER_|FEEDFORGE_|USERPROFILE$|HOME$|APPDATA$|LOCALAPPDATA$|TEMP$|TMP$)/i.test(key)) delete env[key];
Object.assign(env, { SONG_BROWSER_MEGA_RUNTIME: resolved, USERPROFILE: path.join(resolved, 'home'), HOME: path.join(resolved, 'home'), APPDATA: path.join(resolved, 'appdata'), LOCALAPPDATA: path.join(resolved, 'localappdata'), TEMP: path.join(resolved, 'temp'), TMP: path.join(resolved, 'temp') });
const stdout = fs.createWriteStream(path.join(resolved, 'stdout.log'), { flags: 'wx' });
const stderr = fs.createWriteStream(path.join(resolved, 'stderr.log'), { flags: 'wx' });
const child = spawn(electron, [path.join(__dirname, 'mega-electron-integration.cjs')], { env, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
let timedOut = false;
const deadline = setTimeout(() => {
  timedOut = true;
  if (process.platform === 'win32' && child.pid) spawn('taskkill', ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' });
  else child.kill('SIGKILL');
}, 150000);
child.stdout.on('data', (value) => { stdout.write(value); process.stdout.write(value); });
child.stderr.on('data', (value) => stderr.write(value));
child.once('error', (error) => { clearTimeout(deadline); stdout.end(); stderr.end(); process.stderr.write(error.message + '\n'); process.exitCode = 1; });
child.once('close', (code) => {
  clearTimeout(deadline); stdout.end(); stderr.end();
  let report;
  try { report = JSON.parse(fs.readFileSync(path.join(resolved, 'result.json'), 'utf8')); } catch { /* A completed report is mandatory. */ }
  if (timedOut || code !== 0 || report?.status !== 'passed') {
    process.stderr.write(`Offline MEGA lifecycle fixture failed (exit ${code}, timedOut ${timedOut}). Inspect ${resolved}.\n`);
    process.exitCode = 1;
  }
});

'use strict';
const fs = require('node:fs');
const path = require('node:path');
const { spawn } = require('node:child_process');

const args = process.argv.slice(2);
const option = (name) => { const index = args.indexOf(name); return index >= 0 ? args[index + 1] : undefined; };
const electron = option('--electron');
const runtime = option('--runtime');
if (!electron || !runtime || !path.isAbsolute(electron) || !path.isAbsolute(runtime)) {
  throw new Error('Usage: node test-song-browser/electron-runner.cjs --electron ABSOLUTE_ELECTRON_PATH --runtime FRESH_ABSOLUTE_OUTPUT_DIRECTORY');
}
if (!fs.statSync(electron).isFile()) throw new Error('Electron executable is unavailable.');
if (fs.existsSync(runtime)) throw new Error('Choose a fresh runtime directory; existing outputs are not overwritten.');
fs.mkdirSync(runtime, { recursive: true });
const env = { ...process.env, SONG_BROWSER_ELECTRON_RUNTIME: runtime };
delete env.ELECTRON_RUN_AS_NODE;
const child = spawn(electron, [path.join(__dirname, 'electron-integration.cjs')], {
  env, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe']
});
const stdout = fs.createWriteStream(path.join(runtime, 'stdout.log'), { flags: 'wx' });
const stderr = fs.createWriteStream(path.join(runtime, 'stderr.log'), { flags: 'wx' });
let timedOut = false;
const timer = setTimeout(() => {
  timedOut = true;
  if (process.platform === 'win32') spawn('taskkill', ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' });
  else child.kill('SIGKILL');
}, 150000);
child.stdout.on('data', (data) => { stdout.write(data); process.stdout.write(data); });
child.stderr.on('data', (data) => stderr.write(data));
child.once('error', (error) => { clearTimeout(timer); stdout.end(); stderr.end(); process.stderr.write(error.message + '\n'); process.exitCode = 1; });
child.once('close', (code) => {
  clearTimeout(timer); stdout.end(); stderr.end();
  let report;
  try { report = JSON.parse(fs.readFileSync(path.join(runtime, 'result.json'), 'utf8')); } catch { /* Report required below. */ }
  if (timedOut || code !== 0 || report?.status !== 'passed') {
    process.stderr.write(`Offline Electron tests failed (exit ${code}, timedOut ${timedOut}). Inspect ${runtime}.\n`);
    process.exitCode = 1;
  }
});

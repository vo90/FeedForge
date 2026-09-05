'use strict';

// Optional offline integration smoke. This is intentionally not a *.test.cjs
// file: it requires explicit local inputs and invokes the real converter.
// node test-song-browser/real-conversion.cjs --input <song.psarc> --python
// <python.exe> --audio-tools <directory> --root <new smoke directory>

const assert = require('node:assert/strict');
const fs = require('node:fs');
const fsp = fs.promises;
const path = require('node:path');
const crypto = require('node:crypto');
const { spawn } = require('node:child_process');
const { SongJobs } = require('../electron/song-browser/jobs.cjs');

function argumentsFrom(argv) {
  const args = {};
  const supported = new Set(['input', 'python', 'audio-tools', 'root', 'chart-id']);
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index]?.replace(/^--/, '');
    if (!argv[index]?.startsWith('--') || !supported.has(key) || args[key] !== undefined || !argv[index + 1] || argv[index + 1].startsWith('--')) {
      throw new Error('Use --input, --python, --audio-tools and --root, plus optional --chart-id. Every argument requires a value.');
    }
    args[key] = argv[index + 1];
  }
  for (const key of ['input', 'python', 'audio-tools', 'root']) if (!args[key]) throw new Error(`Explicit --${key} is required.`);
  args['chart-id'] ||= '54639';
  if (!/^[1-9]\d{0,11}$/.test(args['chart-id'])) throw new Error('--chart-id must be a CustomsForge numeric chart ID.');
  return args;
}

async function sha256(filename) {
  const hash = crypto.createHash('sha256');
  for await (const chunk of fs.createReadStream(filename)) hash.update(chunk);
  return hash.digest('hex');
}

async function main() {
  const args = argumentsFrom(process.argv.slice(2));
  const input = await fsp.realpath(path.resolve(args.input));
  const python = await fsp.realpath(path.resolve(args.python));
  const audioTools = await fsp.realpath(path.resolve(args['audio-tools']));
  assert.equal((await fsp.stat(input)).isFile(), true, 'Input must be an existing file.');
  assert.equal(path.extname(input).toLowerCase(), '.psarc', 'Choose a PSARC input.');
  assert.equal((await fsp.stat(python)).isFile(), true, 'Python must be an existing executable.');
  assert.equal((await fsp.stat(audioTools)).isDirectory(), true, 'Audio tools must be an existing directory.');

  const requestedRoot = path.resolve(args.root);
  if (fs.existsSync(requestedRoot)) throw new Error('--root must be a NEW dedicated smoke directory, never an existing library or output folder.');
  // Non-recursive creation refuses to silently construct an unexpected tree.
  await fsp.mkdir(requestedRoot);
  const root = await fsp.realpath(requestedRoot);
  await fsp.writeFile(path.join(root, 'OFFLINE_SMOKE.txt'), 'Dedicated FeedForge offline smoke artifacts. No network downloads or game imports.\n', { flag: 'wx' });
  const outputDir = path.join(root, 'output');
  const jobRoot = path.join(root, 'jobs');
  const worktree = path.resolve(__dirname, '..');
  const env = { ...process.env, PYTHONPATH: path.join(worktree, 'src'), PYTHONDONTWRITEBYTECODE: '1', PYTHONIOENCODING: 'utf-8' };
  const pathKey = Object.keys(env).find((key) => key.toLowerCase() === 'path') || 'PATH';
  env[pathKey] = audioTools + path.delimiter + (env[pathKey] || '');
  const states = [];
  const calls = [];
  let downloadCount = 0;
  let jobs;
  const waiters = new Map();
  const sourceHash = await sha256(input);
  const sourceBytes = (await fsp.stat(input)).size;
  const result = { ok: false, offline: true, root, source: { path: input, sha256: sourceHash, bytes: sourceBytes }, chartId: args['chart-id'], states, calls };

  const runConverter = (cliArgs, { onSpawn } = {}) => new Promise((resolve, reject) => {
    const index = String(calls.length + 1).padStart(3, '0');
    const mode = cliArgs.includes('--inspect-json') ? 'inspect' : cliArgs.includes('--validate-feedpak') ? 'validate' : 'convert';
    const call = { mode, args: [...cliArgs], startedAt: new Date().toISOString(), onSpawnCalled: false };
    calls.push(call);
    let stdout = '';
    let stderr = '';
    let spawnError;
    const child = spawn(python, ['-B', '-m', 'feedback_converter.cli', ...cliArgs], { cwd: worktree, env, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
    call.pid = child.pid;
    if (onSpawn) { onSpawn(child); call.onSpawnCalled = true; }
    child.stdout.setEncoding('utf8'); child.stderr.setEncoding('utf8');
    child.stdout.on('data', (chunk) => { stdout += chunk; });
    child.stderr.on('data', (chunk) => { stderr += chunk; });
    child.once('error', (error) => { spawnError = error; });
    child.once('close', async (code, signal) => {
      Object.assign(call, { code, signal, closedAt: new Date().toISOString() });
      try {
        const stdoutLog = path.join(root, `${index}-${mode}.stdout.log`);
        const stderrLog = path.join(root, `${index}-${mode}.stderr.log`);
        await Promise.all([fsp.writeFile(stdoutLog, stdout, { flag: 'wx' }), fsp.writeFile(stderrLog, stderr, { flag: 'wx' })]);
        Object.assign(call, { stdoutLog, stderrLog });
        if (spawnError) reject(spawnError);
        else resolve({ code, stdout, stderr });
      } catch (error) { reject(error); }
    });
  });

  function waitForTerminal(id) {
    const current = jobs.snapshot().find((job) => job.id === id);
    if (current && ['completed', 'failed', 'cancelled'].includes(current.state)) return Promise.resolve(current);
    return new Promise((resolve, reject) => {
      const timer = setTimeout(async () => {
        waiters.delete(id);
        try { await jobs.cancel(id); } catch { /* Preserve evidence of the original timeout. */ }
        reject(new Error('Real conversion smoke exceeded ten minutes.'));
      }, 10 * 60 * 1000);
      waiters.set(id, (job) => { clearTimeout(timer); waiters.delete(id); resolve(job); });
    });
  }

  try {
    // Derive the expected title/artist from the actual source, without online
    // lookup. The optional chart ID is provenance supplied by the caller.
    const inspection = await runConverter(['--inspect-json', input], { onSpawn: () => {} });
    assert.equal(inspection.code, 0, inspection.stderr || inspection.stdout);
    const source = JSON.parse(inspection.stdout);
    assert.equal(source.ok, true); assert.ok(source.preview?.title); assert.ok(source.preview?.artist);
    assert.ok(Array.isArray(source.preview.arrangements) && source.preview.arrangements.length);
    result.chart = { id: args['chart-id'], title: source.preview.title, artist: source.preview.artist };
    jobs = new SongJobs({
      root: jobRoot, outputDir, runConverter,
      download: async (_chart, options) => {
        if (options.signal.aborted) throw new Error('Copy cancelled.');
        downloadCount++;
        await fsp.copyFile(input, options.destination, fs.constants.COPYFILE_EXCL);
        options.onProgress(100);
        return options.destination;
      },
      emit: (job) => {
        const previous = states.at(-1);
        if (!previous || previous.id !== job.id || previous.state !== job.state) {
          states.push({ id: job.id, state: job.state, at: new Date().toISOString() });
          process.stdout.write(`${job.state}: ${job.title}\n`);
        }
        if (['completed', 'failed', 'cancelled'].includes(job.state)) waiters.get(job.id)?.(job);
      },
    });
    const firstQueued = jobs.enqueue(result.chart);
    const first = await waitForTerminal(firstQueued.id);
    assert.equal(first.state, 'completed', first.error || first.message);
    assert.equal(first.sourceHash, sourceHash);
    assert.ok(first.outputPath && first.outputPath.startsWith(outputDir + path.sep));
    assert.equal(path.extname(first.outputPath), '.feedpak');
    const outputHash = await sha256(first.outputPath);
    assert.equal(first.outputHash, outputHash);
    assert.equal(calls.filter((call) => call.mode === 'validate' && call.code === 0).length, 1, 'SongJobs must run independent real FeedPak validation.');
    assert.deepEqual(states.filter((item) => item.id === first.id).map((item) => item.state), ['queued', 'downloading', 'inspecting', 'converting', 'validating', 'completed']);
    const firstStat = await fsp.stat(first.outputPath);
    const conversionCalls = calls.filter((call) => call.mode === 'convert').length;

    const duplicateQueued = jobs.enqueue(result.chart);
    assert.notEqual(duplicateQueued.id, first.id);
    const duplicate = await waitForTerminal(duplicateQueued.id);
    assert.equal(duplicate.state, 'completed', duplicate.error || duplicate.message);
    assert.equal(duplicate.duplicateOf, first.id);
    assert.equal(duplicate.outputPath, first.outputPath);
    assert.equal(duplicate.outputHash, first.outputHash);
    assert.equal(calls.filter((call) => call.mode === 'convert').length, conversionCalls, 'Duplicate must reuse the validated output without conversion.');
    assert.equal((await fsp.stat(first.outputPath)).mtimeMs, firstStat.mtimeMs);
    assert.equal(await sha256(first.outputPath), outputHash);
    assert.equal(downloadCount, 2, 'Both jobs must inspect the user-designated source.');
    assert.ok(calls.every((call) => call.onSpawnCalled && call.closedAt), 'Every converter process must be reported and awaited through close.');
    await jobs.dispose();
    const published = (await fsp.readdir(outputDir)).filter((name) => name.endsWith('.feedpak'));
    assert.equal(published.length, 1, 'Duplicate must not create a second output package.');
    assert.equal(await sha256(input), sourceHash, 'Source PSARC must remain unchanged.');
    Object.assign(result, { ok: true, output: { path: first.outputPath, sha256: outputHash, bytes: firstStat.size }, firstJob: first, duplicateJob: duplicate, outputReused: true, downloadCount });
  } catch (error) {
    result.error = error.stack || error.message || String(error);
    if (jobs) { try { await jobs.dispose(); } catch (disposeError) { result.disposeError = disposeError.message; } result.jobs = jobs.snapshot(); }
    process.exitCode = 1;
  } finally {
    const resultPath = path.join(root, 'result.json');
    await fsp.writeFile(resultPath, JSON.stringify(result, null, 2) + '\n', { flag: 'wx' });
    process.stdout.write(JSON.stringify({ ok: result.ok, resultPath, output: result.output, outputReused: result.outputReused, error: result.error }, null, 2) + '\n');
  }
}

main().catch((error) => { process.stderr.write((error.stack || String(error)) + '\n'); process.exitCode = 1; });

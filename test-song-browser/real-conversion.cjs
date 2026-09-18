'use strict';

// Optional offline integration smoke. This is intentionally not a *.test.cjs
// file: it requires explicit local inputs and invokes the real converter.
// node test-song-browser/real-conversion.cjs --input <song.psarc> --python
// <python.exe> --audio-tools <directory> --root <new smoke directory>
// Or: --input <song.psarc> --converter <frozen converter.exe> --root <new directory>

const assert = require('node:assert/strict');
const fs = require('node:fs');
const fsp = fs.promises;
const path = require('node:path');
const crypto = require('node:crypto');
const { spawn } = require('node:child_process');
const { SongJobs } = require('../electron/song-browser/jobs.cjs');

function argumentsFrom(argv) {
  const args = {};
  const supported = new Set(['input', 'python', 'audio-tools', 'converter', 'root', 'chart-id', 'required-parts', 'required-tuning']);
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index]?.replace(/^--/, '');
    if (!argv[index]?.startsWith('--') || !supported.has(key) || args[key] !== undefined || !argv[index + 1] || argv[index + 1].startsWith('--')) {
      throw new Error('Use --input and --root with either --converter, or --python and --audio-tools, plus optional --chart-id. Every argument requires a value.');
    }
    args[key] = argv[index + 1];
  }
  for (const key of ['input', 'root']) if (!args[key]) throw new Error(`Explicit --${key} is required.`);
  if (args.converter) {
    if (!path.isAbsolute(args.converter)) throw new Error('--converter must be an absolute executable path.');
    if (args.python || args['audio-tools']) throw new Error('Choose either frozen --converter mode or --python/--audio-tools mode.');
  } else {
    for (const key of ['python', 'audio-tools']) if (!args[key]) throw new Error(`Explicit --${key} is required in Python mode.`);
  }
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
  const converter = args.converter ? await fsp.realpath(args.converter) : null;
  const python = args.python ? await fsp.realpath(path.resolve(args.python)) : null;
  const audioTools = args['audio-tools'] ? await fsp.realpath(path.resolve(args['audio-tools'])) : null;
  assert.equal((await fsp.stat(input)).isFile(), true, 'Input must be an existing file.');
  assert.equal(path.extname(input).toLowerCase(), '.psarc', 'Choose a PSARC input.');
  assert.equal((await fsp.stat(converter || python)).isFile(), true, 'Converter/Python must be an existing executable.');
  if (audioTools) assert.equal((await fsp.stat(audioTools)).isDirectory(), true, 'Audio tools must be an existing directory.');

  const requestedRoot = path.resolve(args.root);
  if (fs.existsSync(requestedRoot)) throw new Error('--root must be a NEW dedicated smoke directory, never an existing library or output folder.');
  // Non-recursive creation refuses to silently construct an unexpected tree.
  await fsp.mkdir(requestedRoot);
  const root = await fsp.realpath(requestedRoot);
  await fsp.writeFile(path.join(root, 'OFFLINE_SMOKE.txt'), 'Dedicated FeedForge offline smoke artifacts. No network downloads or game imports.\n', { flag: 'wx' });
  const outputDir = path.join(root, 'output');
  const jobRoot = path.join(root, 'jobs');
  const worktree = path.resolve(__dirname, '..');
  const env = { ...process.env };
  if (converter) {
    for (const key of Object.keys(env)) {
      if (/(?:PYTHON|FEEDFORGE)/i.test(key) || /^(?:PY_|__PYVENV_LAUNCHER__|VIRTUAL_ENV|CONDA|_PYI)/i.test(key) || key.toLowerCase() === 'path') delete env[key];
    }
    const systemRoot = process.env.SystemRoot || process.env.SYSTEMROOT || process.env.WINDIR;
    assert.ok(systemRoot && path.isAbsolute(systemRoot), 'Frozen smoke requires an explicit Windows system directory.');
    env.PATH = [path.join(systemRoot, 'System32'), systemRoot, path.join(systemRoot, 'System32', 'Wbem')].join(path.delimiter);
  } else {
    Object.assign(env, { PYTHONPATH: path.join(worktree, 'src'), PYTHONDONTWRITEBYTECODE: '1', PYTHONIOENCODING: 'utf-8' });
    const pathKey = Object.keys(env).find((key) => key.toLowerCase() === 'path') || 'PATH';
    env[pathKey] = audioTools + path.delimiter + (env[pathKey] || '');
  }
  const states = [];
  const calls = [];
  let downloadCount = 0;
  let jobs;
  const waiters = new Map();
  const sourceHash = await sha256(input);
  const sourceStat = await fsp.stat(input);
  const sourceBytes = sourceStat.size;
  const result = { ok: false, offline: true, root, mode: converter ? 'frozen' : 'python', executable: converter || python,
    isolatedPath: converter ? env.PATH : undefined, source: { path: input, sha256: sourceHash, bytes: sourceBytes, mtimeMs: sourceStat.mtimeMs }, chartId: args['chart-id'], states, calls };

  const runConverter = (cliArgs, { onSpawn, directory = path.join(root, 'inspection-tmp') } = {}) => new Promise((resolve, reject) => {
    const relative = path.relative(root, path.resolve(directory));
    assert.ok(relative && relative !== '..' && !relative.startsWith('..' + path.sep) && !path.isAbsolute(relative), 'Converter temp must be inside the dedicated smoke root.');
    fs.mkdirSync(directory, { recursive: true });
    const childEnv = { ...env, TEMP: directory, TMP: directory, TMPDIR: directory };
    const index = String(calls.length + 1).padStart(3, '0');
    const mode = cliArgs.includes('--version') ? 'version' : cliArgs.includes('--inspect-json') ? 'inspect' : cliArgs.includes('--validate-feedpak') ? 'validate' : 'convert';
    const call = { mode, args: [...cliArgs], directory, startedAt: new Date().toISOString(), onSpawnCalled: false };
    calls.push(call);
    let stdout = '';
    let stderr = '';
    let spawnError;
    const child = spawn(converter || python, converter ? cliArgs : ['-B', '-m', 'feedback_converter.cli', ...cliArgs], {
      cwd: converter ? directory : worktree, env: childEnv, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'],
    });
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
    if (current && ['completed', 'failed', 'cancelled', 'parked'].includes(current.state)) return Promise.resolve(current);
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
    if (args['required-parts'] || args['required-tuning']) {
      result.chart.selection = require('../electron/song-browser/file-selection.cjs').normalizeRequirements({
        parts: args['required-parts'] ? args['required-parts'].split(',') : [], tuning: args['required-tuning'] || null,
        platform: 'pc', strictPlatform: true,
      });
    }
    const version = await runConverter(['--version'], { onSpawn: () => {} });
    assert.equal(version.code, 0);
    const recipe = await require('../electron/song-browser/converter-recipe.cjs').converterRecipe({ command: converter || python, prefix: converter ? [] : ['-m'], cwd: worktree, version: version.stdout.trim() });
    jobs = new SongJobs({ recipe,
      root: jobRoot, outputDir, runConverter,
      download: async (_chart, options) => {
        if (options.signal.aborted) throw new Error('Copy cancelled.');
        downloadCount++;
        await fsp.copyFile(input, options.destination, fs.constants.COPYFILE_EXCL);
        options.onResolvedFile?.({ filename: path.basename(input), platform: source.preview.source_platforms?.includes('pc') ? 'pc' : 'unknown', sizeBytes: (await fsp.stat(input)).size });
        options.onProgress(100);
        return options.destination;
      },
      emit: (job) => {
        const previous = states.at(-1);
        if (!previous || previous.id !== job.id || previous.state !== job.state) {
          states.push({ id: job.id, state: job.state, at: new Date().toISOString() });
          process.stdout.write(`${job.state}: ${job.title}\n`);
        }
        if (['completed', 'failed', 'cancelled', 'parked'].includes(job.state)) waiters.get(job.id)?.(job);
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
    await jobs.jobs.find((job) => job.id === duplicate.id).done;

    const secondOutputDir = path.join(root, 'second-output');
    jobs.setOutputDir(secondOutputDir);
    assert.equal(jobs.snapshot().find((job) => job.id === first.id).inOutputDir, false);
    const copiedQueued = jobs.enqueue(result.chart);
    const copied = await waitForTerminal(copiedQueued.id);
    assert.equal(copied.state, 'completed', copied.error || copied.message);
    assert.notEqual(copied.outputPath, first.outputPath);
    assert.equal(path.dirname(copied.outputPath), secondOutputDir);
    assert.equal(copied.inOutputDir, true);
    assert.ok([first.id, duplicate.id].includes(copied.duplicateOf), 'Cross-folder copy must identify a previous verified package.');
    assert.equal(copied.outputHash, outputHash);
    assert.equal(await sha256(copied.outputPath), outputHash);
    assert.equal((await fsp.stat(first.outputPath)).mtimeMs, firstStat.mtimeMs, 'Copying into another folder must preserve the first output.');
    assert.equal(await sha256(first.outputPath), outputHash);
    assert.equal(calls.filter((call) => call.mode === 'convert').length, conversionCalls, 'Changing output folders must copy the validated package without reconversion.');
    await jobs.jobs.find((job) => job.id === copied.id).done;
    const copiedStat = await fsp.stat(copied.outputPath);

    const reusedQueued = jobs.enqueue(result.chart);
    const reused = await waitForTerminal(reusedQueued.id);
    assert.equal(reused.state, 'completed', reused.error || reused.message);
    assert.equal(reused.duplicateOf, copied.id);
    assert.equal(reused.outputPath, copied.outputPath, 'A subsequent job must reuse the package in the currently selected folder.');
    assert.equal(reused.inOutputDir, true);
    assert.equal((await fsp.stat(copied.outputPath)).mtimeMs, copiedStat.mtimeMs);
    assert.equal(await sha256(copied.outputPath), outputHash);
    assert.equal(calls.filter((call) => call.mode === 'convert').length, conversionCalls);
    assert.equal(downloadCount, 4, 'Each queued job must inspect a fresh local copy of the designated source.');
    assert.ok(calls.every((call) => call.onSpawnCalled && call.closedAt), 'Every converter process must be reported and awaited through close.');
    await jobs.dispose();
    const published = (await fsp.readdir(outputDir)).filter((name) => name.endsWith('.feedpak'));
    assert.equal(published.length, 1, 'Duplicate must not create a second output package.');
    const copiedPublished = (await fsp.readdir(secondOutputDir)).filter((name) => name.endsWith('.feedpak'));
    assert.deepEqual(copiedPublished, [path.basename(copied.outputPath)], 'The second folder must contain exactly its single verified package.');
    assert.equal(await sha256(input), sourceHash, 'Source PSARC must remain unchanged.');
    assert.equal((await fsp.stat(input)).mtimeMs, sourceStat.mtimeMs, 'Source PSARC modification time must remain unchanged.');
    assert.equal((await fsp.stat(input)).size, sourceBytes);
    Object.assign(result, { ok: true, output: { path: first.outputPath, sha256: outputHash, bytes: firstStat.size },
      copiedOutput: { path: copied.outputPath, sha256: outputHash, bytes: copiedStat.size }, firstJob: first, duplicateJob: duplicate,
      copiedJob: copied, reusedJob: reused, outputReused: true, crossFolderCopied: true, secondFolderReused: true, downloadCount,
      callCounts: { inspect: calls.filter((call) => call.mode === 'inspect').length, convert: conversionCalls, validate: calls.filter((call) => call.mode === 'validate').length },
    });
  } catch (error) {
    result.error = error.stack || error.message || String(error);
    if (jobs) { try { await jobs.dispose(); } catch (disposeError) { result.disposeError = disposeError.message; } result.jobs = jobs.snapshot(); }
    process.exitCode = 1;
  } finally {
    const resultPath = path.join(root, 'result.json');
    await fsp.writeFile(resultPath, JSON.stringify(result, null, 2) + '\n', { flag: 'wx' });
    process.stdout.write(JSON.stringify({ ok: result.ok, resultPath, mode: result.mode, output: result.output, copiedOutput: result.copiedOutput,
      outputReused: result.outputReused, crossFolderCopied: result.crossFolderCopied, secondFolderReused: result.secondFolderReused,
      callCounts: result.callCounts, downloadCount: result.downloadCount, error: result.error }, null, 2) + '\n');
  }
}

main().catch((error) => { process.stderr.write((error.stack || String(error)) + '\n'); process.exitCode = 1; });

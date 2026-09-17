'use strict';

// Offline integration smoke test. Runs the real queue, frozen converter and
// publication code against generated tones/tab data in a NEW owned directory.
// It never opens Electron, touches an existing app profile, or contacts a site.
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const { spawn } = require('node:child_process');
const { createRequire } = require('node:module');
const vm = require('node:vm');
const realPath = fs.realpathSync.native;

function argumentsFrom(argv) {
  const result = {};
  const supported = new Set(['--root', '--python', '--converter', '--package-root']);
  for (let index = 0; index < argv.length; index += 2) {
    assert.ok(supported.has(argv[index]) && argv[index + 1] && !result[argv[index]], 'Unknown or repeated argument.');
    result[argv[index]] = path.resolve(argv[index + 1]);
  }
  for (const name of ['--root', '--python']) assert.ok(result[name], `Explicit ${name} is required.`);
  assert.ok(Boolean(result['--converter']) !== Boolean(result['--package-root']), 'Supply exactly one of --converter or --package-root.');
  return result;
}
const digest = (file) => crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const inside = (root, file) => {
  const relative = path.relative(root, file);
  return Boolean(relative) && relative !== '..' && !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative);
};
function run(executable, args, { cwd, env, onSpawn, onStderrLine, timeout = 180000 } = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(executable, args, { cwd, env, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
    let stdout = '', stderr = '', pending = '', settled = false;
    const timer = setTimeout(() => { child.kill(); finish(new Error(`Child timed out: ${path.basename(executable)}`)); }, timeout);
    const finish = (error, result) => { if (settled) return; settled = true; clearTimeout(timer); error ? reject(error) : resolve(result); };
    onSpawn?.(child);
    child.stdout.on('data', (data) => { stdout += data; if (stdout.length > 4 * 1024 * 1024) { child.kill(); finish(new Error('Unexpectedly large converter output.')); } });
    child.stderr.on('data', (data) => {
      stderr += data; pending += data;
      if (stderr.length > 4 * 1024 * 1024) { child.kill(); finish(new Error('Unexpectedly large converter diagnostic output.')); return; }
      const lines = pending.split(/\r?\n/); pending = lines.pop();
      for (const line of lines) onStderrLine?.(line);
    });
    child.on('error', (error) => finish(error));
    child.on('close', (code) => { if (pending) onStderrLine?.(pending); finish(null, { code, stdout, stderr }); });
  });
}
async function settle(queue) {
  await queue.ready;
  while (queue.draining) await queue.draining;
}

async function main() {
  const args = argumentsFrom(process.argv.slice(2));
  const root = args['--root'];
  assert.ok(!fs.existsSync(root), '--root must be NEW; no existing output is deleted or repaired.');
  fs.mkdirSync(root, { recursive: true });
  const physicalRoot = realPath(root);
  const sourceRoot = path.resolve(__dirname, '..');
  const converter = realPath(args['--converter'] || path.join(args['--package-root'], 'resources', 'bin', 'psarc2feedpak', 'psarc2feedpak.exe'));
  assert.ok(fs.statSync(converter).isFile() && path.extname(converter).toLowerCase() === '.exe', 'Expected a frozen Windows converter.');
  const receipt = { owner: 'Test-SongsterrJobs.cjs', version: 1, root, physicalRoot, converter, converterHash: digest(converter), offline: true };
  fs.writeFileSync(path.join(root, 'ownership.json'), JSON.stringify(receipt, null, 2), { flag: 'wx' });
  const report = { ...receipt, ok: false, checks: [], scenarios: [] };
  try {
    let moduleRoot = sourceRoot;
    if (args['--package-root']) {
      const archive = path.join(args['--package-root'], 'resources', 'app.asar');
      report.package = { root: args['--package-root'], archiveHash: digest(archive) };
      moduleRoot = path.join(root, 'packaged-source');
      fs.mkdirSync(moduleRoot);
      // Read the packaged modules with the developer's ASAR reader, then run
      // them unchanged with Node. Native UI/startup is outside this test's scope.
      const asar = require('@electron/asar');
      for (const entry of asar.listPackage(archive)) {
        const key = entry.replaceAll('\\', '/').replace(/^\/+/, '');
        if (!key.startsWith('electron/song-browser/') || !key.endsWith('.cjs')) continue;
        const target = path.resolve(moduleRoot, key);
        assert.ok(inside(moduleRoot, target), 'Invalid archive module path.');
        fs.mkdirSync(path.dirname(target), { recursive: true });
        fs.writeFileSync(target, asar.extractFile(archive, key.split('/').join(path.sep)), { flag: 'wx' });
      }
    }
    const jobsModule = path.join(moduleRoot, 'electron', 'song-browser', 'songsterr-jobs.cjs');
    report.jobsModuleHash = digest(jobsModule);
    const { SongsterrJobs } = require(jobsModule);
    const fixture = path.join(root, 'fixture'); fs.mkdirSync(fixture);
    const generated = await run(args['--python'], ['-B', '-c',
      'import pathlib,runpy,sys; runpy.run_path(sys.argv[1])["synthetic_inputs"](pathlib.Path(sys.argv[2]))',
      path.join(sourceRoot, 'tools', 'Test-SongsterrConverter.py'), fixture], { cwd: root });
    assert.equal(generated.code, 0, generated.stderr);
    const score = path.join(fixture, 'synthetic-score.json'), audio = path.join(fixture, 'synthetic-recording.wav');
    const sourceHashes = { score: digest(score), audio: digest(audio) };
    const environment = { ...process.env };
    for (const key of ['PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV', 'NODE_PATH', 'NODE_OPTIONS']) delete environment[key];
    environment.PATH = path.join(environment.SystemRoot || 'C:\\Windows', 'System32');
    environment.PYTHONNOUSERSITE = '1';
    report.converterPath = environment.PATH;
    const health = await run(converter, ['--song-import-health'], { cwd: root, env: environment });
    assert.equal(health.code, 0, health.stderr);
    assert.equal(JSON.parse(health.stdout).portable, true);
    report.checks.push('Frozen converter health with system-only PATH');

    // Use real Windows package redirection when present. Adding a junction
    // inside an already virtualized directory can itself change Windows I/O
    // virtualization semantics, so the portable control needs one only when
    // the chosen root has no native alias.
    let logicalCase, physicalCase;
    if (physicalRoot !== root) {
      logicalCase = path.join(root, 'native-profile'); fs.mkdirSync(logicalCase);
      physicalCase = realPath(logicalCase);
      report.pathMechanism = 'native-windows-redirection';
    } else {
      physicalCase = path.join(physicalRoot, 'junction-target'); fs.mkdirSync(physicalCase);
      logicalCase = path.join(root, 'logical-profile');
      fs.symlinkSync(physicalCase, logicalCase, process.platform === 'win32' ? 'junction' : 'dir');
      report.pathMechanism = 'directory-junction';
    }
    const scenario = { name: 'redirected-profile-and-output', logicalRoot: logicalCase, physicalRoot: physicalCase,
      acquisitions: 0, converterCalls: [], transitions: [], completionCallbacks: 0 };
    report.scenarios.push(scenario);
    const queueRoot = path.join(logicalCase, 'songsterr', 'jobs'), outputDir = path.join(logicalCase, 'library');
    const provider = {
      restoreTrustedResult() {},
      async acquire(chart, { directory }) {
        scenario.acquisitions++;
        const target = path.join(realPath(directory), 'score.json');
        fs.copyFileSync(score, target, fs.constants.COPYFILE_EXCL);
        // The browser retains the selected path spelling. The independent
        // frozen Python worker resolves it when returning its staged archive.
        return { path: path.join(directory, 'score.json'), metadata: { songId: 12, revisionId: 34, approval: 'approved', approved: true,
          artist: 'Original band', title: 'Portable fixture' }, audio: { kind: 'file', path: audio } };
      },
    };
    const configuration = {
      root: queueRoot, provider,
      getConverterRecipe: async () => receipt.converterHash,
      emit(job) { if (job) scenario.transitions.push({ state: job.state, message: job.message, error: job.error }); },
      onCompleted() { scenario.completionCallbacks++; },
      async runConverter(arguments_, options) {
        const response = await run(converter, arguments_, { ...options, cwd: options.directory, env: environment });
        const call = { args: arguments_, directory: options.directory, code: response.code };
        scenario.converterCalls.push(call);
        fs.writeFileSync(path.join(root, `converter-${scenario.converterCalls.length}.stdout.json`), response.stdout);
        fs.writeFileSync(path.join(root, `converter-${scenario.converterCalls.length}.stderr.txt`), response.stderr);
        if (arguments_[0] === '--song-import-file' && response.code === 0) {
          const converted = JSON.parse(response.stdout);
          call.stagingPath = converted.stagingPath;
          call.mixedPathSpellings = !inside(options.directory, converted.stagingPath)
            && inside(realPath(options.directory), realPath(converted.stagingPath));
          assert.ok(call.mixedPathSpellings, 'The integration must exercise real logical/physical path differences.');
        }
        return response;
      },
    };
    let queue = new SongsterrJobs(configuration);
    let job;
    try {
      await queue.ready;
      job = queue.enqueue({ id: '12', artist: 'Original band', title: 'Portable fixture' }, {
        outputDir, outputSettings: { nameTemplate: '{artist} - {year} - {title}', outputLayout: 'artist' },
      });
      await settle(queue);
      job = queue.snapshot().find((entry) => entry.id === job.id);
      assert.equal(job.state, 'completed', JSON.stringify(job));
      assert.equal(job.outputAvailable, true);
      assert.equal(job.coverage.arrangements, 2); assert.equal(job.coverage.notes, 144);
      assert.equal(path.relative(realPath(outputDir), job.outputPath).replaceAll('\\', '/'), 'Original band/Original band - Portable fixture.feedpak');
      assert.ok(!inside(outputDir, job.outputPath), 'Receipt must use physical spelling of the selected library.');
      assert.equal(job.alignment.status, 'validated');
      scenario.firstOutput = { path: job.outputPath, hash: digest(job.outputPath), coverage: job.coverage };
      report.checks.push('Real converter stages via physical path and queue publishes using configured name/layout');
    } finally { await queue.dispose(); }

    const ledgerPath = path.join(queueRoot, 'jobs.json');
    const ledger = JSON.parse(fs.readFileSync(ledgerPath, 'utf8'));
    const saved = ledger.jobs.find((entry) => entry.id === job.id);
    saved.state = 'saving'; saved.committed = false; delete saved.outputPath;
    fs.writeFileSync(ledgerPath, JSON.stringify(ledger));
    const beforeRecovery = scenario.converterCalls.length;
    queue = new SongsterrJobs(configuration);
    try {
      await queue.ready;
      const recovered = queue.snapshot().find((entry) => entry.id === job.id);
      assert.equal(recovered.state, 'completed', JSON.stringify(recovered));
      assert.equal(recovered.outputPath, scenario.firstOutput.path);
      assert.equal(recovered.outputAvailable, true);
      assert.equal(scenario.converterCalls.length, beforeRecovery);
      report.checks.push('Interrupted-save receipt recovers across logical/physical output paths without reconversion');
    } finally { await queue.dispose(); }

    // The only removal is our own receipt. Preserve the first published file
    // while simulating a cached failure so retry also verifies collision naming.
    const cachedLedger = JSON.parse(fs.readFileSync(ledgerPath, 'utf8'));
    const cachedJob = cachedLedger.jobs.find((entry) => entry.id === job.id);
    Object.assign(cachedJob, { state: 'failed', committed: false, error: 'Synthetic interrupted attempt', scorePath: realPath(cachedJob.scorePath) });
    fs.writeFileSync(ledgerPath, JSON.stringify(cachedLedger));
    fs.unlinkSync(path.join(queueRoot, `${job.id}.receipt.json`));
    queue = new SongsterrJobs(configuration);
    try {
      await queue.ready;
      queue.retry(job.id);
      await settle(queue);
      const retried = queue.snapshot().find((entry) => entry.id === job.id);
      assert.equal(retried.state, 'completed', JSON.stringify(retried));
      assert.equal(scenario.acquisitions, 1, 'Cached retry must not reacquire the tab.');
      assert.equal(path.basename(retried.outputPath), 'Original band - Portable fixture (2).feedpak');
      assert.equal(digest(scenario.firstOutput.path), scenario.firstOutput.hash, 'Retry must not overwrite an existing song.');
      assert.equal(retried.outputAvailable, true);
      scenario.retryOutput = { path: retried.outputPath, hash: digest(retried.outputPath) };
      const validation = await run(converter, ['--validate-feedpak', retried.outputPath], { cwd: root, env: environment });
      assert.equal(validation.code, 0, validation.stderr); assert.equal(JSON.parse(validation.stdout).ok, true);
      report.checks.push('Cached retry survives changed path spelling, preserves first output and validates published FeedPak');
    } finally { await queue.dispose(); }
    // Exercise the actual IPC Show file action against the same saved history.
    // Only the browser provider and shell are stand-ins: no UI/network opens.
    const servicePath = path.join(moduleRoot, 'electron', 'song-browser', 'songsterr-service.cjs');
    const serviceRequire = createRequire(servicePath);
    class OfflineProvider {
      constructor() { this.connection = { status: 'anonymous' }; }
      restoreTrustedResult() {}
      dispose() {}
    }
    const serviceModule = { exports: {} };
    const serviceContext = { module: serviceModule, exports: serviceModule.exports, URL, AbortController, Buffer, console,
      __filename: servicePath, __dirname: path.dirname(servicePath),
      require: (name) => name === './providers/songsterr/index.cjs' ? { SongsterrProvider: OfflineProvider } : serviceRequire(name) };
    vm.runInNewContext(fs.readFileSync(servicePath, 'utf8'), serviceContext, { filename: servicePath });
    const handlers = new Map(), revealed = [];
    const webContents = { mainFrame: {}, send() {} };
    const window = { webContents, isDestroyed: () => false };
    const service = serviceModule.exports.registerSongsterr({
      app: { getPath: (name) => { assert.equal(name, 'userData'); return logicalCase; } },
      ipcMain: { handle: (name, callback) => handlers.set(name, callback) },
      shell: { showItemInFolder: (file) => { assert.ok(fs.statSync(file).isFile()); revealed.push(file); } },
      getMainWindow: () => window, getSettings: () => ({ outputDir }),
      runConverter() { throw new Error('Show file must not reconvert.'); },
    });
    try {
      const response = await handlers.get('songsterr:showOutput')({ sender: webContents, senderFrame: webContents.mainFrame }, { id: job.id });
      assert.equal(response.ok, true, JSON.stringify(response));
      assert.deepEqual(revealed, [scenario.retryOutput.path]);
      scenario.revealedOutput = revealed[0];
      report.checks.push('Actual Show file IPC accepts the completed path and passes it to the OS shell adapter');
    } finally { await service.close(); }
    assert.equal(scenario.completionCallbacks, 2);
    assert.equal(digest(score), sourceHashes.score); assert.equal(digest(audio), sourceHashes.audio);
    assert.equal(digest(converter), receipt.converterHash);
    report.sourceHashes = sourceHashes;
    report.scope = 'Real JS queue + frozen conversion + final publication/recovery with synthetic local inputs. No website, account, live audio download, UI or game playback tested.';
    report.ok = true;
  } catch (error) { report.error = error.message; report.stack = error.stack; }
  const reportPath = path.join(root, 'result.json');
  fs.writeFileSync(reportPath, JSON.stringify(report, null, 2));
  console.log(JSON.stringify({ ok: report.ok, report: reportPath, ...(report.error ? { error: report.error } : {}) }));
  process.exitCode = report.ok ? 0 : 1;
}
main().catch((error) => { console.error(error.stack); process.exitCode = 1; });

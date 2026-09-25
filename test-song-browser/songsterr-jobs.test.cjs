'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');
const { SongsterrJobs } = require('../electron/song-browser/songsterr-jobs.cjs');
const { selectSynchronization, unavailableSynchronization, audioVideo } = require('../electron/song-browser/providers/songsterr/synchronization.cjs');

const { CURRENT_PRESERVATION_CONTRACT: CURRENT } = require('../electron/song-browser/songsterr-evidence.cjs');

const CHART = { id: '123', title: 'Synthetic Song', artist: 'Synthetic Artist' };
const SETTINGS = { outputLayout: 'flat', nameTemplate: '{artist} - {title}' };

test('packaged app and converter use the same preservation contract', () => {
  const { CURRENT_PRESERVATION_CONTRACT } = require('../electron/song-browser/songsterr-evidence.cjs');
  const source = fs.readFileSync(path.join(__dirname, '../src/feedback_converter/song_import/evidence.py'), 'utf8');
  assert.equal(CURRENT_PRESERVATION_CONTRACT, Number(source.match(/^CONTRACT_VERSION = (\d+)$/m)[1]));
});
const RESPONSE = (value) => ({ code: 0, stdout: JSON.stringify({ ok: true, ...value }) });

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

async function settle(jobs) {
  await jobs.ready;
  while (jobs.draining) await jobs.draining;
}

async function fixture(t, options = {}) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'feedforge-songsterr-jobs-'));
  let root = path.join(directory, 'jobs'), outputDir = path.join(directory, 'output');
  let physicalRoot = root, physicalOutputDir = outputDir, restoreRealpath = () => {};
  if (options.redirectedProfile) {
    const physicalProfile = path.join(directory, 'native-profile');
    const logicalProfile = path.join(directory, 'roaming-profile');
    physicalRoot = path.join(physicalProfile, 'jobs'); physicalOutputDir = path.join(physicalProfile, 'output');
    root = path.join(logicalProfile, 'jobs'); outputDir = path.join(logicalProfile, 'output');
    fs.mkdirSync(physicalRoot, { recursive: true });
    fs.symlinkSync(physicalProfile, logicalProfile, 'junction');
    // Reproduce MSIX redirection: the legacy resolver retains AppData's logical
    // spelling, while Python, the native resolver and real files use its target.
    const realpath = fs.realpathSync;
    function logicalRealpath(filename, settings) {
      const resolved = path.resolve(String(filename));
      if (resolved === logicalProfile || resolved.startsWith(logicalProfile + path.sep)) {
        fs.lstatSync(resolved);
        return settings?.encoding === 'buffer' ? Buffer.from(resolved) : resolved;
      }
      return realpath(filename, settings);
    }
    logicalRealpath.native = realpath.native;
    fs.realpathSync = logicalRealpath;
    restoreRealpath = () => { fs.realpathSync = realpath; };
  }
  fs.mkdirSync(outputDir, { recursive: true });
  const calls = [], requests = [], acquisitions = [], completed = [], audioProbes = [], syncProbes = [];
  const provider = { acquire: async (chart, context) => {
    acquisitions.push(chart.id);
    if (options.acquire) return options.acquire(chart, context, acquireNormally);
    return acquireNormally(chart, context);
  } };
  if (options.findAudio) provider.findAudio = async (chart, context) => {
    audioProbes.push({ chart, ...context });
    return options.findAudio(chart, context);
  };
  if (options.findSynchronization) provider.findSynchronization = async (chart, context) => {
    syncProbes.push({ chart, ...context });
    return options.findSynchronization(chart, context);
  };
  function acquireNormally(chart, { directory: work }) {
    const filename = path.join(work, 'source.gp');
    fs.writeFileSync(filename, 'Synthetic score');
    return { path: filename, metadata: { songId: chart.id, revisionId: '456', approval: 'approved',
      artist: chart.artist, title: chart.title }, ...(options.missingAudio ? {} : { audio: options.audio || { kind: 'file', path: path.join(directory, 'recording.wav') } }) };
  }
  async function normalConverter(args, context) {
    if (args[0] === '--validate-feedpak') return RESPONSE({ valid: true });
    assert.equal(args[0], '--song-import-file');
    const request = JSON.parse(fs.readFileSync(args[1], 'utf8'));
    requests.push(request);
    const stagingPath = path.join(context.directory, 'result.feedpak');
    fs.writeFileSync(stagingPath, options.outputBytes ? options.outputBytes(requests.length) : 'Synthetic FeedPak');
    const hash = (data) => crypto.createHash('sha256').update(data).digest('hex');
    const outputHash = hash(fs.readFileSync(stagingPath));
    const sourceHash = hash(fs.readFileSync(request.scorePath));
    const contract = options.contract || CURRENT;
    const report = JSON.stringify({ version: contract, status: 'passed', sourceSha256: sourceHash }), verificationHash = hash(report);
    const record = JSON.stringify({ version: contract, outputHash, objects: { verification: verificationHash, source: sourceHash } });
    const id = hash(record);
    fs.mkdirSync(path.join(request.auditDir, 'objects'), { recursive: true });
    fs.mkdirSync(path.join(request.auditDir, 'records'), { recursive: true });
    fs.writeFileSync(path.join(request.auditDir, 'objects', verificationHash), report);
    fs.writeFileSync(path.join(request.auditDir, 'records', `${id}.json`), record);
    return RESPONSE({ stagingPath, relativePath: options.relativePath || 'Synthetic Artist - Synthetic Song.feedpak',
      scoreHash: sourceHash, audioHash: 'audio-digest', recipe: { version: 3, preservationContract: contract, scoreHash: sourceHash, audioHash: 'audio-digest' },
      verification: { version: contract, status: 'passed', outputHash }, evidence: { version: contract, id, sourceHash, verificationHash, outputHash },
      warnings: [], alignment: { status: 'validated' }, coverage: { arrangements: 1 } });
  }
  const runConverter = async (args, context) => {
    calls.push(args);
    if (options.runConverter) return options.runConverter(args, context, normalConverter);
    return normalConverter(args, context);
  };
  const config = { root, provider, runConverter, getConverterRecipe: async () => ({ version: 'test' }),
    onCompleted: (job) => completed.push(job.id) };
  const jobs = new SongsterrJobs(config);
  await jobs.ready;
  t.after(async () => {
    try { await jobs.dispose(); }
    finally {
      restoreRealpath();
      // Every junction target is inside this fixture's newly allocated folder.
      const resolved = path.resolve(directory);
      assert.equal(path.dirname(resolved), path.resolve(os.tmpdir()));
      assert.ok(path.basename(resolved).startsWith('feedforge-songsterr-jobs-'));
      fs.rmSync(resolved, { recursive: true, force: true });
    }
  });
  return { jobs, directory, root, outputDir, physicalRoot, physicalOutputDir, config, calls, requests, acquisitions, completed, audioProbes, syncProbes,
    enqueue: (chart = CHART) => jobs.enqueue(chart, { outputDir, outputSettings: SETTINGS }) };
}

test('Songsterr cancellation stops waiting for a shared converter recipe without cancelling it for the next song', async (t) => {
  const f = await fixture(t);
  const entered = deferred(), recipe = deferred();
  f.jobs.getConverterRecipe = () => { entered.resolve(); return recipe.promise; };
  const first = f.enqueue();
  await entered.promise;
  let cancelled = false;
  const cancellation = f.jobs.cancel(first.id).then(() => { cancelled = true; });
  try {
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(cancelled, true, 'Cancellation must finish while the shared recipe remains pending.');
    await cancellation;
    assert.equal(f.jobs.snapshot()[0].state, 'cancelled');
    assert.equal(f.acquisitions.length, 0);
    assert.equal(f.calls.length, 0);
  } finally { recipe.resolve({ version: 'test' }); }
  f.enqueue({ ...CHART, id: '124' }); await settle(f.jobs);
  assert.equal(f.jobs.snapshot()[1].state, 'completed');
});

test('generated difficulty remains an explicit importer setting and changes the reuse identity', async t => {
  const f = await fixture(t);
  f.enqueue(); await settle(f.jobs);
  assert.equal(f.requests[0].outputSettings.generateDifficulty, undefined);
  f.jobs.enqueue(CHART, { outputDir: f.outputDir, outputSettings: { ...SETTINGS, generateDifficulty: true } });
  await settle(f.jobs);
  assert.equal(f.requests[1].outputSettings.generateDifficulty, true);
  const jobs = f.jobs.snapshot();
  assert.equal(jobs[0].state, 'completed');
  assert.equal(jobs[1].state, 'completed');
  assert.notEqual(jobs[0].outputPath, jobs[1].outputPath);
});

test('Songsterr pauses for missing audio and reuses the approved score on retry', async (t) => {
  const f = await fixture(t, { missingAudio: true });
  const queued = f.enqueue();
  await settle(f.jobs);
  assert.equal(f.jobs.snapshot()[0].state, 'needs_audio');
  assert.equal(f.calls.length, 0);
  assert.deepEqual(fs.readdirSync(f.outputDir), []);
  f.jobs.retry(queued.id, { audio: { kind: 'file', path: path.join(f.directory, 'chosen.wav') } });
  await settle(f.jobs);
  const saved = f.jobs.snapshot()[0];
  assert.equal(saved.state, 'completed');
  assert.equal(saved.revisionId, '456');
  assert.deepEqual(f.acquisitions, ['123']);
  assert.equal(f.requests[0].metadata.approval, 'approved');
  assert.deepEqual(f.requests[0].outputSettings, SETTINGS);
  assert.ok(fs.existsSync(saved.outputPath));
});

test('unverified converter responses cannot publish even after structural validation', async (t) => {
  const f = await fixture(t, { runConverter: async (args, context, normal) => {
    const response = await normal(args, context);
    if (args[0] === '--song-import-file') {
      const result = JSON.parse(response.stdout); delete result.verification;
      return RESPONSE(result);
    }
    return response;
  } });
  f.enqueue(); await settle(f.jobs);
  assert.equal(f.jobs.snapshot()[0].state, 'failed');
  assert.match(f.jobs.snapshot()[0].error, /source verification/);
  assert.deepEqual(fs.readdirSync(f.outputDir), []);
});

test('verification is bound to the exact staged file before publication', async (t) => {
  const f = await fixture(t, { runConverter: async (args, context, normal) => {
    const response = await normal(args, context);
    if (args[0] === '--song-import-file') fs.appendFileSync(JSON.parse(response.stdout).stagingPath, 'modified');
    return response;
  } });
  f.enqueue(); await settle(f.jobs);
  assert.equal(f.jobs.snapshot()[0].state, 'failed');
  assert.deepEqual(fs.readdirSync(f.outputDir), []);
});

for (const contract of Array.from({ length: CURRENT - 1 }, (_, i) => i + 1)) test(`old preservation contract ${contract} cannot publish as a current verified conversion`, async (t) => {
  const f = await fixture(t, { contract });
  f.enqueue(); await settle(f.jobs);
  assert.equal(f.jobs.snapshot()[0].state, 'failed');
  assert.match(f.jobs.snapshot()[0].error, /current source verification/);
  assert.deepEqual(fs.readdirSync(f.outputDir), []);
});

for (const contract of Array.from({ length: CURRENT - 1 }, (_, i) => i + 1)) test(`old contract ${contract} reports survive recovery without current mapping fidelity or reuse`, async (t) => {
  const f = await fixture(t);
  f.enqueue(); await settle(f.jobs); await f.jobs.dispose();
  const job = f.jobs.jobs[0], hash = (bytes) => crypto.createHash('sha256').update(bytes).digest('hex');
  const oldReference = job.evidence;
  const report = JSON.parse(fs.readFileSync(path.join(f.jobs.auditRoot, 'objects', oldReference.verificationHash), 'utf8'));
  report.version = contract;
  const reportBytes = JSON.stringify(report), verificationHash = hash(reportBytes);
  fs.writeFileSync(path.join(f.jobs.auditRoot, 'objects', verificationHash), reportBytes);
  const record = JSON.parse(fs.readFileSync(path.join(f.jobs.auditRoot, 'records', oldReference.id + '.json'), 'utf8'));
  record.version = contract; record.objects.verification = verificationHash;
  const recordBytes = JSON.stringify(record), id = hash(recordBytes);
  fs.writeFileSync(path.join(f.jobs.auditRoot, 'records', id + '.json'), recordBytes);
  job.recipe.preservationContract = contract; job.verification.version = contract;
  job.evidence = { ...oldReference, version: contract, id, verificationHash };
  f.jobs._save();
  const receiptPath = path.join(f.root, job.id + '.receipt.json');
  const receipt = JSON.parse(fs.readFileSync(receiptPath, 'utf8'));
  fs.writeFileSync(receiptPath, JSON.stringify({ ...receipt, recipe: job.recipe, verification: job.verification, evidence: job.evidence }));
  const restored = new SongsterrJobs(f.config);
  try {
    await restored.ready;
    const old = restored.snapshot()[0];
    assert.equal(old.state, 'completed');
    assert.equal(old.verification.status, 'not_checked');
    assert.equal(old.hasReport, true);
    restored.enqueue(CHART, { outputDir: f.outputDir, outputSettings: SETTINGS });
    await settle(restored);
    const fresh = restored.snapshot()[1];
    assert.equal(fresh.state, 'completed', fresh.error);
    assert.equal(fresh.verification.version, CURRENT);
    assert.equal(fresh.verification.status, 'passed');
    assert.notEqual(fresh.outputPath, old.outputPath);
    assert.equal(fs.readFileSync(old.outputPath, 'utf8'), 'Synthetic FeedPak');
  } finally { await restored.dispose(); }
});

test('later edits retain historical report without claiming current verification', async (t) => {
  const f = await fixture(t);
  f.enqueue(); await settle(f.jobs);
  const initial = f.jobs.snapshot()[0];
  assert.equal(initial.verification.status, 'passed');
  const reference = f.jobs.jobs[0].evidence;
  fs.appendFileSync(initial.outputPath, 'user edit');
  f.jobs.lastOutputCheck = 0;
  await f.jobs.refreshOutputs();
  assert.equal(f.jobs.snapshot()[0].verification.status, 'modified');
  assert.deepEqual(f.jobs.jobs[0].evidence, reference);
  assert.equal(f.jobs.snapshot()[0].hasReport, true);
});

test('an unavailable prior report declines reuse without blocking a freshly checked import', async (t) => {
  const f = await fixture(t, { outputBytes: (n) => `Synthetic FeedPak ${n}` });
  f.enqueue(); await settle(f.jobs);
  const first = f.jobs.jobs[0];
  fs.writeFileSync(path.join(f.jobs.auditRoot, 'records', first.evidence.id + '.json'), 'corrupted historical record');
  f.enqueue(); await settle(f.jobs);
  const current = f.jobs.snapshot()[1];
  assert.equal(current.state, 'completed', current.error);
  assert.equal(current.verification.status, 'passed');
  assert.notEqual(current.outputPath, first.outputPath);
  assert.equal(fs.readFileSync(first.outputPath, 'utf8'), 'Synthetic FeedPak 1');
});

test('source verification evidence lives outside cleaned attempts', async (t) => {
  const f = await fixture(t);
  f.enqueue(); await settle(f.jobs);
  const request = f.requests[0];
  assert.equal(path.dirname(request.auditDir), path.dirname(f.root));
  assert.equal(fs.existsSync(request.workDir), false);
  assert.equal(fs.existsSync(path.join(request.auditDir, 'records', f.jobs.jobs[0].evidence.id + '.json')), true);
});

test('explicit audio detection reuses the cached approved revision without acquiring another tab', async (t) => {
  const audio = { kind: 'url', url: 'https://www.youtube.com/watch?v=abcdefghijk', videoId: 'abcdefghijk' };
  const f = await fixture(t, { missingAudio: true, findAudio: async () => audio });
  const queued = f.enqueue(); await settle(f.jobs);
  const before = f.jobs.jobs[0];
  const scorePath = before.scorePath, scoreHash = before.cachedScoreHash;
  assert.equal(f.audioProbes.length, 0, 'fresh acquisition already searched for audio');
  assert.equal(f.jobs.snapshot()[0].canRetryAudio, true);
  f.jobs.retry(queued.id);
  assert.equal(JSON.parse(fs.readFileSync(path.join(f.root, 'jobs.json'), 'utf8')).jobs[0].retryAudioDetection, undefined,
    'the one-shot user action must not be replayed automatically after restart');
  await settle(f.jobs);
  assert.deepEqual(f.acquisitions, ['123']);
  assert.equal(f.audioProbes.length, 1);
  assert.equal(f.audioProbes[0].revisionId, '456');
  assert.equal(f.audioProbes[0].chart.id, '123');
  assert.ok(f.audioProbes[0].signal instanceof AbortSignal);
  assert.equal(f.jobs.jobs[0].scorePath, scorePath);
  assert.equal(f.jobs.jobs[0].cachedScoreHash, scoreHash);
  assert.equal(f.requests[0].metadata.revisionId, '456');
  assert.deepEqual(f.requests[0].audio, audio);
  assert.equal(f.jobs.snapshot()[0].state, 'completed');
  assert.equal(f.jobs.snapshot()[0].canRetryAudio, false);
});

test('a missing audio retry pauses once without a retry loop or a duplicate tab download', async (t) => {
  const f = await fixture(t, { missingAudio: true, findAudio: async () => null });
  const queued = f.enqueue(); await settle(f.jobs);
  f.jobs.retry(queued.id); await settle(f.jobs);
  assert.equal(f.jobs.snapshot()[0].state, 'needs_audio');
  assert.equal(f.jobs.snapshot()[0].canRetryAudio, true);
  assert.equal(f.audioProbes.length, 1);
  assert.deepEqual(f.acquisitions, ['123']);
  assert.equal(f.calls.length, 0);
  await f.jobs.dispose();
  const restored = new SongsterrJobs(f.config);
  try {
    await settle(restored);
    assert.equal(restored.snapshot()[0].state, 'needs_audio');
    assert.equal(f.audioProbes.length, 1, 'restoring a waiting import must not probe the website');
    restored.retry(queued.id); await settle(restored);
    assert.equal(f.audioProbes.length, 2, 'another explicit retry may check again');
    assert.deepEqual(f.acquisitions, ['123']);
  } finally { await restored.dispose(); }
});

test('cancelling audio discovery prevents conversion and discards a late candidate', async (t) => {
  const entered = deferred(), release = deferred();
  const f = await fixture(t, { missingAudio: true, findAudio: async (_chart, { signal }) => {
    entered.resolve(signal);
    await release.promise;
    return { kind: 'url', url: 'https://www.youtube.com/watch?v=abcdefghijk', videoId: 'abcdefghijk' };
  } });
  const queued = f.enqueue(); await settle(f.jobs);
  f.jobs.retry(queued.id);
  const signal = await entered.promise;
  const cancelling = f.jobs.cancel(queued.id);
  assert.equal(signal.aborted, true);
  release.resolve(); await cancelling; await settle(f.jobs);
  assert.equal(f.jobs.snapshot()[0].state, 'cancelled');
  assert.equal(f.jobs.jobs[0].audio, undefined);
  assert.equal(f.calls.length, 0);
  assert.deepEqual(f.acquisitions, ['123']);
  assert.deepEqual(fs.readdirSync(f.outputDir), []);
});

for (const corruption of ['changed score', 'missing score', 'unapproved revision', 'different song', 'different revision']) {
  test(`audio-only retry rejects ${corruption} before discovery or acquisition`, async (t) => {
    const f = await fixture(t, { missingAudio: true, findAudio: async () => ({ kind: 'url', url: 'https://www.youtube.com/watch?v=abcdefghijk' }) });
    const queued = f.enqueue(); await settle(f.jobs);
    const cached = f.jobs.jobs[0];
    if (corruption === 'changed score') fs.writeFileSync(cached.scorePath, 'Changed score');
    if (corruption === 'missing score') fs.unlinkSync(cached.scorePath);
    if (corruption === 'unapproved revision') cached.metadata.approval = 'pending';
    if (corruption === 'different song') cached.metadata.songId = '124';
    if (corruption === 'different revision') cached.metadata.revisionId = '789';
    f.jobs.retry(queued.id); await settle(f.jobs);
    assert.equal(f.jobs.snapshot()[0].state, 'failed');
    assert.match(f.jobs.snapshot()[0].error, /saved tab/);
    assert.equal(f.audioProbes.length, 0);
    assert.deepEqual(f.acquisitions, ['123']);
    assert.equal(f.calls.length, 0);
  });
}

test('manual audio keeps priority and alignment replacement never starts site discovery', async (t) => {
  const replacements = [
    { kind: 'file', path: 'first.wav' },
    { kind: 'url', url: 'https://example.com/chosen-recording.mp3' },
  ];
  let failAlignment = true;
  const f = await fixture(t, { missingAudio: true, findAudio: async () => { throw new Error('Must not probe'); },
    runConverter: async (args, context, normal) => {
      if (args[0] === '--song-import-file' && failAlignment) {
        failAlignment = false;
        assert.deepEqual(JSON.parse(fs.readFileSync(args[1], 'utf8')).audio, replacements[0]);
        return { code: 1, stdout: JSON.stringify({ ok: false, code: 'alignment_failed', error: 'Choose the matching recording.' }) };
      }
      return normal(args, context);
    } });
  const queued = f.enqueue(); await settle(f.jobs);
  f.jobs.retry(queued.id, { audio: replacements[0] }); await settle(f.jobs);
  assert.equal(f.jobs.snapshot()[0].state, 'alignment_failed');
  assert.equal(f.jobs.snapshot()[0].canRetryAudio, false);
  assert.deepEqual(f.jobs.jobs[0].audio, replacements[0]);
  f.jobs.retry(queued.id, { audio: replacements[1] }); await settle(f.jobs);
  assert.equal(f.jobs.snapshot()[0].state, 'completed');
  assert.deepEqual(f.requests[0].audio, replacements[1]);
  assert.equal(f.audioProbes.length, 0);
  assert.deepEqual(f.acquisitions, ['123']);
});

test('an existing recording is retained on a normal conversion retry', async (t) => {
  let fail = true;
  const f = await fixture(t, { findAudio: async () => { throw new Error('Must not probe'); },
    runConverter: async (args, context, normal) => {
      if (fail) { fail = false; throw new Error('Temporary converter failure'); }
      return normal(args, context);
    } });
  const queued = f.enqueue(); await settle(f.jobs);
  const audio = { ...f.jobs.jobs[0].audio };
  f.jobs.retry(queued.id); await settle(f.jobs);
  assert.equal(f.jobs.snapshot()[0].state, 'completed');
  assert.deepEqual(f.requests[0].audio, audio);
  assert.equal(f.audioProbes.length, 0);
});

test('a general retry still reacquires a missing cached tab and checks its new approval', async (t) => {
  let fail = true;
  const f = await fixture(t, { findAudio: async () => { throw new Error('Must not probe'); },
    runConverter: async (args, context, normal) => {
      if (fail) { fail = false; throw new Error('Temporary converter failure'); }
      return normal(args, context);
    } });
  const queued = f.enqueue(); await settle(f.jobs);
  fs.unlinkSync(f.jobs.jobs[0].scorePath);
  f.jobs.retry(queued.id); await settle(f.jobs);
  assert.equal(f.jobs.snapshot()[0].state, 'completed');
  assert.deepEqual(f.acquisitions, ['123', '123']);
  assert.equal(f.audioProbes.length, 0);
});

test('Songsterr rejects an unapproved revision before conversion', async (t) => {
  const f = await fixture(t, { acquire: (chart, context, normal) => {
    const acquired = normal(chart, context);
    acquired.metadata.approval = 'pending';
    return acquired;
  } });
  f.enqueue(); await settle(f.jobs);
  assert.equal(f.jobs.snapshot()[0].state, 'failed');
  assert.match(f.jobs.snapshot()[0].error, /approved revision/);
  assert.equal(f.calls.length, 0);
});

test('Songsterr rejects a changed cached score when audio is supplied later', async (t) => {
  const f = await fixture(t, { missingAudio: true });
  const queued = f.enqueue(); await settle(f.jobs);
  fs.writeFileSync(f.jobs.jobs.find((job) => job.id === queued.id).scorePath, 'Changed score');
  f.jobs.retry(queued.id, { audio: { kind: 'file', path: 'chosen.wav' } });
  await settle(f.jobs);
  assert.equal(f.jobs.snapshot()[0].state, 'failed');
  assert.match(f.jobs.snapshot()[0].error, /saved tab changed/);
  assert.equal(f.calls.length, 0);
});

test('retry uses a fresh acquisition folder after cancellation leaves a completed download', async (t) => {
  let first = true, f;
  f = await fixture(t, { acquire: (chart, context, normal) => {
    const result = normal(chart, context);
    if (first) { first = false; f.jobs.current.controller.abort(); }
    return result;
  } });
  const job = f.enqueue(); await settle(f.jobs);
  assert.equal(f.jobs.snapshot()[0].state, 'cancelled');
  f.jobs.retry(job.id); await settle(f.jobs);
  assert.equal(f.jobs.snapshot()[0].state, 'completed');
  const sources = fs.readdirSync(path.join(f.root, job.id)).filter((name) => name.startsWith('source-'));
  assert.equal(sources.length, 2);
});

test('Songsterr never publishes after alignment failure', async (t) => {
  const alignment = { trainingSimilarity: 0.6, validationSimilarity: 0.6, regionalSimilarity: [0.56, 0.61, 0.63, 0.62] };
  const f = await fixture(t, { runConverter: async () => ({ code: 1, stdout: JSON.stringify({
    ok: false, code: 'alignment_failed', error: 'The recording does not match this tab.', alignment }) }) });
  const job = f.enqueue(); await settle(f.jobs);
  assert.equal(f.jobs.snapshot()[0].state, 'alignment_failed');
  assert.deepEqual(f.jobs.snapshot()[0].alignment, alignment);
  assert.deepEqual(JSON.parse(fs.readFileSync(path.join(f.root, 'jobs.json'), 'utf8')).jobs[0].alignment, alignment);
  assert.deepEqual(fs.readdirSync(f.outputDir), []);
  assert.equal(f.completed.length, 0);
  assert.equal(f.jobs.retry(job.id).alignment, undefined);
  await settle(f.jobs);
});

for (const relativePath of ['../escaped.feedpak', 'Artist/nested/song.feedpak', 'C:\\escaped.feedpak']) {
  test(`Songsterr rejects unsafe output path ${relativePath}`, async (t) => {
    const f = await fixture(t, { relativePath });
    f.enqueue(); await settle(f.jobs);
    assert.equal(f.jobs.snapshot()[0].state, 'failed');
    assert.deepEqual(fs.readdirSync(f.outputDir), []);
    assert.equal(fs.existsSync(path.join(f.directory, 'escaped.feedpak')), false);
  });
}

test('Songsterr rejects a converter staging file outside the current attempt', async (t) => {
  let outside;
  const f = await fixture(t, { runConverter: async (args, context, normal) => {
    const result = await normal(args, context);
    if (args[0] === '--song-import-file') {
      const parsed = JSON.parse(result.stdout); parsed.stagingPath = outside;
      return RESPONSE(parsed);
    }
    return result;
  } });
  outside = path.join(f.directory, 'outside.feedpak'); fs.writeFileSync(outside, 'outside');
  f.enqueue(); await settle(f.jobs);
  assert.equal(f.jobs.snapshot()[0].state, 'failed');
  assert.match(f.jobs.snapshot()[0].error, /invalid temporary FeedPak file/);
  assert.deepEqual(f.jobs.jobs[0].pathValidation, { stage: 'staged_feedpak', reason: 'outside_folder' });
  assert.equal(f.calls.filter((args) => args[0] === '--validate-feedpak').length, 0);
  assert.equal(fs.readFileSync(outside, 'utf8'), 'outside');
  assert.deepEqual(fs.readdirSync(f.outputDir), []);
});

for (const [kind, reason] of [
  ['junction escape', 'outside_folder'], ['file symlink', 'symbolic_link'],
  ['missing file', 'missing_file'], ['directory', 'not_file'], ['relative path', 'invalid_path'],
]) {
  test(`Songsterr rejects a ${kind} staging path before content validation or publication`, async (t) => {
    let outside;
    const f = await fixture(t, { runConverter: async (args, context, normal) => {
      const result = await normal(args, context);
      if (args[0] !== '--song-import-file') return result;
      const parsed = JSON.parse(result.stdout);
      fs.unlinkSync(parsed.stagingPath);
      if (kind === 'junction escape') {
        const link = path.join(context.directory, 'escaped');
        fs.symlinkSync(path.dirname(outside), link, 'junction');
        parsed.stagingPath = path.join(link, path.basename(outside));
      } else if (kind === 'file symlink') fs.symlinkSync(outside, parsed.stagingPath, 'file');
      else if (kind === 'directory') fs.mkdirSync(parsed.stagingPath);
      else if (kind === 'relative path') parsed.stagingPath = 'result.feedpak';
      return RESPONSE(parsed);
    } });
    const outsideDir = path.join(f.directory, 'outside'); fs.mkdirSync(outsideDir);
    outside = path.join(outsideDir, 'retained.feedpak'); fs.writeFileSync(outside, 'Outside file must survive');
    if (kind === 'file symlink') {
      const probe = path.join(f.directory, 'symlink-probe');
      try { fs.symlinkSync(outside, probe, 'file'); fs.unlinkSync(probe); }
      catch (error) {
        if (process.platform === 'win32' && error.code === 'EPERM') {
          t.skip('File symlinks require Windows Developer Mode or the corresponding privilege.'); return;
        }
        throw error;
      }
    }
    f.enqueue(); await settle(f.jobs);
    const saved = f.jobs.snapshot()[0];
    assert.equal(saved.state, 'failed');
    assert.match(saved.error, /invalid temporary FeedPak file/);
    assert.deepEqual(f.jobs.jobs[0].pathValidation, { stage: 'staged_feedpak', reason });
    const ledger = JSON.parse(fs.readFileSync(path.join(f.root, 'jobs.json'), 'utf8'));
    assert.deepEqual(ledger.jobs[0].pathValidation, { stage: 'staged_feedpak', reason });
    assert.equal(f.calls.filter((args) => args[0] === '--validate-feedpak').length, 0);
    assert.deepEqual(fs.readdirSync(f.outputDir), []);
    assert.equal(fs.readFileSync(outside, 'utf8'), 'Outside file must survive');
    assert.equal(f.completed.length, 0);
  });
}

test('Songsterr accepts Python native staging paths through a redirected Windows profile', { skip: process.platform !== 'win32' }, async (t) => {
  const f = await fixture(t, { redirectedProfile: true, runConverter: async (args, context, normal) => {
    const result = await normal(args, context);
    if (args[0] !== '--song-import-file') return result;
    const parsed = JSON.parse(result.stdout);
    parsed.stagingPath = fs.realpathSync.native(parsed.stagingPath);
    assert.notEqual(path.dirname(parsed.stagingPath), context.directory);
    return RESPONSE(parsed);
  } });
  assert.equal(fs.realpathSync(f.root), f.root);
  assert.equal(fs.realpathSync.native(f.root), f.physicalRoot);
  assert.equal(await fs.promises.realpath(f.root), f.physicalRoot);
  f.enqueue(); await settle(f.jobs);
  const saved = f.jobs.snapshot()[0];
  assert.equal(saved.state, 'completed', saved.error);
  assert.equal(saved.outputAvailable, true);
  assert.equal(f.jobs.root, f.root, 'Existing ledger roots keep their logical path spelling.');
  assert.equal(saved.outputDir, f.outputDir);
  assert.equal(path.dirname(saved.outputPath), f.physicalOutputDir);
  assert.equal(path.basename(saved.outputPath), 'Synthetic Artist - Synthetic Song.feedpak');
  assert.equal(fs.readFileSync(saved.outputPath, 'utf8'), 'Synthetic FeedPak');
  assert.equal(f.calls.filter((args) => args[0] === '--validate-feedpak').length, 1);
  assert.deepEqual(f.completed, [saved.id]);
});

test('restored logical cached scores can retry through redirected Windows paths without another download', { skip: process.platform !== 'win32' }, async (t) => {
  let fail = true;
  const f = await fixture(t, { redirectedProfile: true, runConverter: async (args, context, normal) => {
    const result = await normal(args, context);
    if (args[0] !== '--song-import-file') return result;
    const parsed = JSON.parse(result.stdout);
    // The first attempt recreates the old final-save failure. A real retry
    // returns Python's existing native file in the newly allocated attempt.
    parsed.stagingPath = fail ? path.join(context.directory, 'missing.feedpak') : fs.realpathSync.native(parsed.stagingPath);
    return RESPONSE(parsed);
  } });
  const queued = f.enqueue(); await settle(f.jobs); await f.jobs.dispose();
  assert.equal(f.jobs.snapshot()[0].state, 'failed');
  const cachedPath = f.jobs.jobs[0].scorePath;
  assert.ok(cachedPath.startsWith(f.root + path.sep));
  assert.notEqual(fs.realpathSync.native(cachedPath), cachedPath);
  fail = false;
  const restored = new SongsterrJobs(f.config);
  try {
    await restored.ready; restored.retry(queued.id); await settle(restored);
    const saved = restored.snapshot()[0];
    assert.equal(saved.state, 'completed', saved.error);
    assert.equal(saved.outputAvailable, true);
    assert.equal(restored.jobs[0].scorePath, cachedPath);
    assert.equal(restored.jobs[0].pathValidation, undefined, 'The successful retry clears obsolete path diagnostics.');
    assert.deepEqual(f.acquisitions, ['123']);
    assert.equal(f.requests.length, 2);
    assert.equal(f.requests[1].scorePath, cachedPath);
    assert.equal(path.dirname(saved.outputPath), f.physicalOutputDir);
    assert.deepEqual(fs.readdirSync(f.outputDir), ['Synthetic Artist - Synthetic Song.feedpak']);
  } finally { await restored.dispose(); }
});

test('receipt recovery accepts a physical output inside the selected logical Windows library', { skip: process.platform !== 'win32' }, async (t) => {
  const f = await fixture(t, { redirectedProfile: true });
  f.enqueue(); await settle(f.jobs); await f.jobs.dispose();
  const completed = f.jobs.snapshot()[0];
  assert.equal(completed.state, 'completed', completed.error);
  assert.equal(path.dirname(completed.outputPath), f.physicalOutputDir);
  const ledgerPath = path.join(f.root, 'jobs.json');
  const ledger = JSON.parse(fs.readFileSync(ledgerPath, 'utf8'));
  assert.equal(ledger.jobs[0].outputDir, f.outputDir);
  Object.assign(ledger.jobs[0], { state: 'saving', committed: false });
  delete ledger.jobs[0].outputPath;
  fs.writeFileSync(ledgerPath, JSON.stringify(ledger));
  const callsBeforeRestore = f.calls.length;
  const restored = new SongsterrJobs(f.config);
  try {
    await settle(restored);
    const saved = restored.snapshot()[0];
    assert.equal(saved.state, 'completed', saved.error);
    assert.equal(saved.outputPath, completed.outputPath);
    assert.equal(saved.outputDir, f.outputDir);
    assert.equal(saved.outputAvailable, true);
    assert.equal(saved.canRetry, false);
    assert.equal(f.calls.length, callsBeforeRestore, 'Recovery must verify the existing file without converting again.');
    assert.deepEqual(fs.readdirSync(f.outputDir), ['Synthetic Artist - Synthetic Song.feedpak']);
  } finally { await restored.dispose(); }
});

test('Songsterr rejects an attempt directory replaced with a junction', async (t) => {
  let outside;
  const f = await fixture(t, { runConverter: async (args, context, normal) => {
    const result = await normal(args, context);
    if (args[0] !== '--song-import-file') return result;
    const parsed = JSON.parse(result.stdout);
    // Both locations are inside this test's new temp directory. Moving the
    // request too permits the ordinary cleanup to run before path validation.
    assert.equal(path.dirname(outside), f.directory);
    assert.ok(context.directory.startsWith(f.root + path.sep));
    fs.renameSync(context.directory, outside);
    fs.symlinkSync(outside, context.directory, 'junction');
    parsed.stagingPath = path.join(outside, 'result.feedpak');
    return RESPONSE(parsed);
  } });
  outside = path.join(f.directory, 'outside-attempt');
  f.enqueue(); await settle(f.jobs);
  assert.equal(f.jobs.snapshot()[0].state, 'failed');
  assert.match(f.jobs.snapshot()[0].error, /invalid temporary FeedPak file/);
  assert.deepEqual(f.jobs.jobs[0].pathValidation, { stage: 'staged_feedpak', reason: 'linked_folder' });
  assert.equal(f.calls.filter((args) => args[0] === '--validate-feedpak').length, 0);
  assert.deepEqual(fs.readdirSync(f.outputDir), []);
  assert.equal(fs.readFileSync(path.join(outside, 'result.feedpak'), 'utf8'), 'Synthetic FeedPak');
});

test('Songsterr rejects an acquired score returned through a junction outside its acquisition folder', async (t) => {
  let outside;
  const f = await fixture(t, { acquire: (chart, context, normal) => {
    const result = normal(chart, context);
    const link = path.join(context.directory, 'escaped');
    fs.symlinkSync(path.dirname(outside), link, 'junction');
    return { ...result, path: path.join(link, path.basename(outside)) };
  } });
  fs.mkdirSync(path.join(f.directory, 'outside-score'));
  outside = path.join(f.directory, 'outside-score', 'source.gp'); fs.writeFileSync(outside, 'Outside tab');
  f.enqueue(); await settle(f.jobs);
  assert.equal(f.jobs.snapshot()[0].state, 'failed');
  assert.match(f.jobs.snapshot()[0].error, /invalid tab file/);
  assert.equal(f.calls.length, 0);
  assert.deepEqual(fs.readdirSync(f.outputDir), []);
  assert.equal(fs.readFileSync(outside, 'utf8'), 'Outside tab');
});

test('Songsterr rejects an unchanged cached score moved outside the job through a junction', async (t) => {
  const f = await fixture(t, { missingAudio: true });
  const queued = f.enqueue(); await settle(f.jobs);
  const scorePath = f.jobs.jobs[0].scorePath;
  const sourceDir = path.dirname(scorePath), outside = path.join(f.directory, 'outside-cached-score');
  assert.ok(sourceDir.startsWith(f.root + path.sep));
  fs.renameSync(sourceDir, outside);
  fs.symlinkSync(outside, sourceDir, 'junction');
  f.jobs.retry(queued.id, { audio: { kind: 'file', path: 'chosen.wav' } }); await settle(f.jobs);
  assert.equal(f.jobs.snapshot()[0].state, 'failed');
  assert.match(f.jobs.snapshot()[0].error, /saved tab/);
  assert.deepEqual(f.acquisitions, ['123']);
  assert.equal(f.calls.length, 0);
  assert.deepEqual(fs.readdirSync(f.outputDir), []);
  assert.equal(fs.readFileSync(path.join(outside, path.basename(scorePath)), 'utf8'), 'Synthetic score');
});

test('receipt recovery rejects a matching file outside the output folder through a junction', async (t) => {
  const f = await fixture(t);
  f.enqueue(); await settle(f.jobs); await f.jobs.dispose();
  const completed = f.jobs.snapshot()[0], outsideDir = path.join(f.directory, 'outside-receipt');
  fs.mkdirSync(outsideDir);
  const outsideFile = path.join(outsideDir, path.basename(completed.outputPath));
  fs.renameSync(completed.outputPath, outsideFile);
  const escaped = path.join(f.outputDir, 'escaped'); fs.symlinkSync(outsideDir, escaped, 'junction');
  const receiptPath = path.join(f.root, `${completed.id}.receipt.json`);
  const receipt = JSON.parse(fs.readFileSync(receiptPath, 'utf8'));
  receipt.outputPath = path.join(escaped, path.basename(outsideFile));
  fs.writeFileSync(receiptPath, JSON.stringify(receipt));
  const ledgerPath = path.join(f.root, 'jobs.json'), ledger = JSON.parse(fs.readFileSync(ledgerPath, 'utf8'));
  Object.assign(ledger.jobs[0], { state: 'saving', committed: false });
  delete ledger.jobs[0].outputPath;
  fs.writeFileSync(ledgerPath, JSON.stringify(ledger));
  const callsBeforeRestore = f.calls.length;
  const restored = new SongsterrJobs(f.config);
  try {
    await settle(restored);
    assert.equal(restored.snapshot()[0].state, 'needs_attention');
    assert.equal(restored.snapshot()[0].outputAvailable, false);
    assert.equal(restored.snapshot()[0].canRetry, true);
    assert.equal(f.calls.length, callsBeforeRestore);
    assert.equal(fs.readFileSync(outsideFile, 'utf8'), 'Synthetic FeedPak');
  } finally { await restored.dispose(); }
});

test('receipt recovery allows a selected output folder that is itself a junction', async (t) => {
  const f = await fixture(t);
  f.enqueue(); await settle(f.jobs); await f.jobs.dispose();
  const completed = f.jobs.snapshot()[0], physicalOutput = path.join(f.directory, 'relocated-library');
  assert.equal(path.dirname(f.outputDir), f.directory);
  fs.renameSync(f.outputDir, physicalOutput);
  fs.symlinkSync(physicalOutput, f.outputDir, 'junction');
  const ledgerPath = path.join(f.root, 'jobs.json'), ledger = JSON.parse(fs.readFileSync(ledgerPath, 'utf8'));
  Object.assign(ledger.jobs[0], { state: 'saving', committed: false });
  delete ledger.jobs[0].outputPath;
  fs.writeFileSync(ledgerPath, JSON.stringify(ledger));
  const restored = new SongsterrJobs(f.config);
  try {
    await settle(restored);
    const saved = restored.snapshot()[0];
    assert.equal(saved.state, 'completed', saved.error);
    assert.equal(saved.outputDir, f.outputDir);
    assert.equal(saved.outputPath, path.join(physicalOutput, path.basename(completed.outputPath)));
    assert.equal(saved.outputAvailable, true);
    assert.equal(fs.readFileSync(saved.outputPath, 'utf8'), 'Synthetic FeedPak');
  } finally { await restored.dispose(); }
});

test('Songsterr preserves an existing different file when publishing', async (t) => {
  const f = await fixture(t);
  const original = path.join(f.outputDir, 'Synthetic Artist - Synthetic Song.feedpak');
  fs.writeFileSync(original, 'User existing song');
  f.enqueue(); await settle(f.jobs);
  assert.equal(f.jobs.snapshot()[0].state, 'completed');
  assert.equal(fs.readFileSync(original, 'utf8'), 'User existing song');
  assert.equal(path.basename(f.jobs.snapshot()[0].outputPath), 'Synthetic Artist - Synthetic Song (2).feedpak');
  assert.equal(fs.readdirSync(f.outputDir).length, 2);
});

test('Songsterr reuses an identical saved output instead of publishing a duplicate', async (t) => {
  const f = await fixture(t);
  f.enqueue(); await settle(f.jobs);
  const first = f.jobs.snapshot()[0];
  f.enqueue(); await settle(f.jobs);
  const second = f.jobs.snapshot()[1];
  assert.equal(second.state, 'completed');
  assert.equal(second.outputPath, first.outputPath);
  assert.equal(fs.readdirSync(f.outputDir).length, 1);
});

test('Songsterr deduplication uses the stable recipe even when archive bytes differ', async (t) => {
  const f = await fixture(t, { outputBytes: (attempt) => `Archive with different ZIP timestamp ${attempt}` });
  f.enqueue(); await settle(f.jobs);
  const first = f.jobs.snapshot()[0];
  f.enqueue(); await settle(f.jobs);
  const second = f.jobs.snapshot()[1];
  assert.equal(second.state, 'completed');
  assert.equal(second.outputPath, first.outputPath);
  assert.equal(fs.readdirSync(f.outputDir).length, 1);
});

test('Songsterr cancellation aborts acquisition without converting or publishing', async (t) => {
  const entered = deferred();
  const f = await fixture(t, { acquire: async (_chart, { signal }) => {
    entered.resolve();
    await new Promise((_resolve, reject) => signal.addEventListener('abort', () => reject(new Error('Stopped')), { once: true }));
  } });
  const job = f.enqueue(); await entered.promise;
  await f.jobs.cancel(job.id);
  assert.equal(f.jobs.snapshot()[0].state, 'cancelled');
  assert.equal(f.calls.length, 0);
  assert.deepEqual(fs.readdirSync(f.outputDir), []);
});

test('cancelling one active Songsterr job does not wait for the next queued song', async (t) => {
  const firstEntered = deferred(), secondEntered = deferred(), releaseSecond = deferred();
  const f = await fixture(t, { acquire: async (chart, context, normal) => {
    if (chart.id === '123') {
      firstEntered.resolve();
      await new Promise((_resolve, reject) => context.signal.addEventListener('abort', () => reject(new Error('Stopped')), { once: true }));
    } else {
      secondEntered.resolve();
      await releaseSecond.promise;
      return normal(chart, context);
    }
  } });
  const first = f.enqueue(); f.enqueue({ ...CHART, id: '124' });
  await firstEntered.promise;
  const cancellation = f.jobs.cancel(first.id);
  await secondEntered.promise;
  let timer;
  const promptly = await Promise.race([cancellation.then(() => true), new Promise((resolve) => { timer = setTimeout(() => resolve(false), 150); })]);
  clearTimeout(timer);
  releaseSecond.resolve(); await settle(f.jobs);
  assert.equal(promptly, true, 'Cancel must settle when its own job stops, not when the entire queue drains.');
  assert.deepEqual(f.jobs.snapshot().map((job) => job.state), ['cancelled', 'completed']);
});

test('Songsterr recovers a committed receipt after an interrupted ledger update', async (t) => {
  const f = await fixture(t);
  f.enqueue(); await settle(f.jobs); await f.jobs.dispose();
  const completed = f.jobs.snapshot()[0];
  const ledgerPath = path.join(f.root, 'jobs.json');
  const ledger = JSON.parse(fs.readFileSync(ledgerPath, 'utf8'));
  Object.assign(ledger.jobs[0], { state: 'saving', committed: false });
  delete ledger.jobs[0].outputPath;
  fs.writeFileSync(ledgerPath, JSON.stringify(ledger));
  const restored = new SongsterrJobs(f.config);
  try {
    await restored.ready;
    assert.equal(restored.snapshot()[0].state, 'completed');
    assert.equal(restored.snapshot()[0].outputPath, completed.outputPath);
    assert.equal(fs.readdirSync(f.outputDir).length, 1);
  } finally { await restored.dispose(); }
});

test('a ledger write failure after publication cannot turn a saved song into a retryable failure', async (t) => {
  const f = await fixture(t);
  const originalSave = f.jobs._save.bind(f.jobs);
  let failOnce = true;
  f.jobs._save = () => {
    if (failOnce && f.jobs.jobs.some((job) => job.state === 'completed' && job.committed)) {
      failOnce = false;
      throw new Error('Simulated history disk failure after commit');
    }
    return originalSave();
  };
  f.enqueue(); await settle(f.jobs);
  const saved = f.jobs.snapshot()[0];
  assert.equal(saved.state, 'completed');
  assert.equal(saved.canRetry, false);
  assert.ok(fs.existsSync(saved.outputPath));
  assert.throws(() => f.jobs.retry(saved.id), /cannot be retried/);
  assert.equal(fs.readdirSync(f.outputDir).length, 1);
});

test('cancelling an import waiting for audio prevents any later automatic conversion', async (t) => {
  const f = await fixture(t, { missingAudio: true });
  const queued = f.enqueue(); await settle(f.jobs);
  await f.jobs.cancel(queued.id); await settle(f.jobs);
  assert.equal(f.jobs.snapshot()[0].state, 'cancelled');
  assert.equal(f.calls.length, 0);
  assert.deepEqual(fs.readdirSync(f.outputDir), []);
});

test('Songsterr interrupted work without a valid receipt requires explicit retry', async (t) => {
  const f = await fixture(t, { missingAudio: true });
  f.enqueue(); await settle(f.jobs); await f.jobs.dispose();
  const ledgerPath = path.join(f.root, 'jobs.json');
  const ledger = JSON.parse(fs.readFileSync(ledgerPath, 'utf8'));
  ledger.jobs[0].state = 'converting';
  fs.writeFileSync(ledgerPath, JSON.stringify(ledger));
  const restored = new SongsterrJobs(f.config);
  try {
    await restored.ready;
    assert.equal(restored.snapshot()[0].state, 'needs_attention');
    assert.equal(restored.snapshot()[0].canRetry, true);
    assert.equal(f.calls.length, 0);
  } finally { await restored.dispose(); }
});

const YOUTUBE = { kind: 'url', url: 'https://www.youtube.com/watch?v=abcdefghijk', videoId: 'abcdefghijk' };
function timingMap(chart, context, points = [0, 2, 4]) {
  const identity = { songId: chart.id, revisionId: context.revisionId, videoId: audioVideo(context.audio)?.videoId };
  return identity.videoId ? selectSynchronization([{ ...identity, status: 'done', feature: null, problematic: null, points }], identity)
    : unavailableSynchronization(identity, 'unsupported_audio');
}

test('fresh import sends one exact synchronization envelope and stores only a bounded summary', async (t) => {
  const points = Array.from({ length: 20001 }, (_, i) => i);
  const f = await fixture(t, { audio: YOUTUBE, findSynchronization: (chart, context) => timingMap(chart, context, points) });
  f.enqueue(); await settle(f.jobs);
  assert.equal(f.jobs.snapshot()[0].state, 'completed');
  assert.equal(f.syncProbes.length, 1); assert.equal(f.acquisitions.length, 1);
  assert.equal(f.syncProbes[0].revisionId, '456'); assert.deepEqual(f.syncProbes[0].audio, YOUTUBE);
  const sync = f.requests[0].synchronization;
  assert.equal(sync.status, 'done'); assert.equal(sync.songId, CHART.id); assert.equal(sync.revisionId, '456');
  assert.equal(sync.videoId, YOUTUBE.videoId); assert.deepEqual(sync.points, points);
  const saved = JSON.parse(fs.readFileSync(path.join(f.root, 'jobs.json'), 'utf8')).jobs[0];
  assert.equal(saved.synchronization, undefined);
  assert.equal(saved.synchronizationSummary.pointCount, points.length);
  assert.equal(saved.synchronizationSummary.points, undefined);
  assert.ok(JSON.stringify(saved.synchronizationSummary).length < 256);
  assert.deepEqual(f.jobs.snapshot()[0].synchronization, saved.synchronizationSummary);
});

test('retry of a cached failed import refreshes timing without reacquiring score or audio', async (t) => {
  let attempt = 0, fail = true;
  const f = await fixture(t, { audio: YOUTUBE,
    findSynchronization: (chart, context) => timingMap(chart, context, ++attempt === 1 ? [0, 2, 4] : [0, 2.1, 4.2]),
    runConverter: async (args, context, normal) => {
      const result = await normal(args, context);
      if (args[0] === '--song-import-file' && fail) { fail = false; return { code: 1, stdout: JSON.stringify({ ok: false, code: 'alignment_failed', error: 'Fixture timing failed.' }) }; }
      return result;
    } });
  const queued = f.enqueue(); await settle(f.jobs);
  assert.equal(f.jobs.snapshot()[0].state, 'alignment_failed');
  f.jobs.retry(queued.id); await settle(f.jobs);
  assert.equal(f.jobs.snapshot()[0].state, 'completed');
  assert.equal(f.syncProbes.length, 2); assert.equal(f.acquisitions.length, 1); assert.equal(f.audioProbes.length, 0);
  assert.deepEqual(f.requests[1].synchronization.points, [0, 2.1, 4.2]);
  assert.notEqual(f.requests[0].synchronization.mapHash, f.requests[1].synchronization.mapHash);
});

test('restored legacy cached failure fetches timing on retry even with saved audio', async (t) => {
  let fail = true;
  const f = await fixture(t, { audio: YOUTUBE, findSynchronization: timingMap,
    runConverter: async (args, context, normal) => {
      if (fail) throw new Error('Old converter failure');
      return normal(args, context);
    } });
  const queued = f.enqueue(); await settle(f.jobs); await f.jobs.dispose();
  const ledgerPath = path.join(f.root, 'jobs.json'), ledger = JSON.parse(fs.readFileSync(ledgerPath, 'utf8'));
  delete ledger.jobs[0].synchronizationSummary;
  fs.writeFileSync(ledgerPath, JSON.stringify(ledger));
  fail = false;
  const restored = new SongsterrJobs(f.config);
  try {
    await restored.ready; restored.retry(queued.id); await settle(restored);
    assert.equal(restored.snapshot()[0].state, 'completed');
    assert.equal(f.acquisitions.length, 1); assert.equal(f.syncProbes.length, 2);
    assert.equal(f.requests[0].synchronization.status, 'done');
  } finally { await restored.dispose(); }
});

test('replacement audio invalidates previous timing and receives local-match diagnostics', async (t) => {
  let fail = true;
  const f = await fixture(t, { audio: YOUTUBE, findSynchronization: timingMap,
    runConverter: async (args, context, normal) => {
      const result = await normal(args, context);
      if (args[0] === '--song-import-file' && fail) { fail = false; throw new Error('Fixture conversion failure'); }
      return result;
    } });
  const queued = f.enqueue(); await settle(f.jobs);
  const replacement = { kind: 'file', path: path.join(f.directory, 'replacement.wav') };
  f.jobs.retry(queued.id, { audio: replacement }); await settle(f.jobs);
  assert.equal(f.requests[0].synchronization.status, 'done');
  assert.equal(f.requests[1].synchronization.status, 'unavailable');
  assert.equal(f.requests[1].synchronization.reasonCode, 'unsupported_audio');
  assert.equal(f.requests[1].synchronization.points, undefined);
  assert.deepEqual(f.syncProbes[1].audio, replacement);
});

test('timing transport failure falls back without changing score success into a login prompt', async (t) => {
  const f = await fixture(t, { audio: YOUTUBE, findSynchronization: async () => { throw Object.assign(new Error('Temporary failure'), { code: 'needs_login' }); } });
  f.enqueue(); await settle(f.jobs);
  assert.equal(f.jobs.snapshot()[0].state, 'completed'); assert.equal(f.syncProbes.length, 1);
  assert.equal(f.requests[0].synchronization.status, 'unavailable');
  assert.equal(f.jobs.snapshot()[0].canUseAccount, false);
});

test('cancelling timing retrieval never runs conversion or local fallback', async (t) => {
  const entered = deferred();
  const f = await fixture(t, { audio: YOUTUBE, findSynchronization: async (_chart, { signal }) => {
    entered.resolve(); await new Promise((_resolve, reject) => signal.addEventListener('abort', () => reject(new Error('Stopped')), { once: true }));
  } });
  const queued = f.enqueue(); await entered.promise; await f.jobs.cancel(queued.id); await settle(f.jobs);
  assert.equal(f.jobs.snapshot()[0].state, 'cancelled'); assert.equal(f.calls.length, 0);
  assert.deepEqual(fs.readdirSync(f.outputDir), []);
});

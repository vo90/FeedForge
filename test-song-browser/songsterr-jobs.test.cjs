'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { SongsterrJobs } = require('../electron/song-browser/songsterr-jobs.cjs');

const CHART = { id: '123', title: 'Synthetic Song', artist: 'Synthetic Artist' };
const SETTINGS = { outputLayout: 'flat', nameTemplate: '{artist} - {title}' };
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
  const root = path.join(directory, 'jobs'), outputDir = path.join(directory, 'output');
  fs.mkdirSync(outputDir);
  const calls = [], requests = [], acquisitions = [], completed = [], audioProbes = [];
  const provider = { acquire: async (chart, context) => {
    acquisitions.push(chart.id);
    if (options.acquire) return options.acquire(chart, context, acquireNormally);
    return acquireNormally(chart, context);
  } };
  if (options.findAudio) provider.findAudio = async (chart, context) => {
    audioProbes.push({ chart, ...context });
    return options.findAudio(chart, context);
  };
  function acquireNormally(chart, { directory: work }) {
    const filename = path.join(work, 'source.gp');
    fs.writeFileSync(filename, 'Synthetic score');
    return { path: filename, metadata: { songId: chart.id, revisionId: '456', approval: 'approved',
      artist: chart.artist, title: chart.title }, ...(options.missingAudio ? {} : { audio: { kind: 'file', path: path.join(directory, 'recording.wav') } }) };
  }
  async function normalConverter(args, context) {
    if (args[0] === '--validate-feedpak') return RESPONSE({ valid: true });
    assert.equal(args[0], '--song-import-file');
    const request = JSON.parse(fs.readFileSync(args[1], 'utf8'));
    requests.push(request);
    const stagingPath = path.join(context.directory, 'result.feedpak');
    fs.writeFileSync(stagingPath, options.outputBytes ? options.outputBytes(requests.length) : 'Synthetic FeedPak');
    return RESPONSE({ stagingPath, relativePath: options.relativePath || 'Synthetic Artist - Synthetic Song.feedpak',
      scoreHash: 'score-digest', audioHash: 'audio-digest', recipe: { version: 1, scoreHash: 'score-digest', audioHash: 'audio-digest' },
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
    await jobs.dispose();
    // Only this fixture's freshly allocated temp directory can be removed.
    const resolved = path.resolve(directory);
    assert.equal(path.dirname(resolved), path.resolve(os.tmpdir()));
    assert.ok(path.basename(resolved).startsWith('feedforge-songsterr-jobs-'));
    fs.rmSync(resolved, { recursive: true, force: true });
  });
  return { jobs, directory, root, outputDir, config, calls, requests, acquisitions, completed, audioProbes,
    enqueue: (chart = CHART) => jobs.enqueue(chart, { outputDir, outputSettings: SETTINGS }) };
}

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
  const f = await fixture(t, { runConverter: async () => ({ code: 1, stdout: JSON.stringify({
    ok: false, code: 'alignment_failed', error: 'The recording does not match this tab.' }) }) });
  f.enqueue(); await settle(f.jobs);
  assert.equal(f.jobs.snapshot()[0].state, 'alignment_failed');
  assert.deepEqual(fs.readdirSync(f.outputDir), []);
  assert.equal(f.completed.length, 0);
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
  assert.match(f.jobs.snapshot()[0].error, /invalid staged FeedPak/);
  assert.deepEqual(fs.readdirSync(f.outputDir), []);
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

'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { SongsterrJobs } = require('../electron/song-browser/songsterr-jobs.cjs');
const { HistoryWriter } = require('../electron/song-browser/history-writer.cjs');

const CHART = { id: '123', title: 'Synthetic Song', artist: 'Synthetic Artist' };
const AUDIO = { kind: 'url', videoId: 'abcdefghijk', url: 'https://www.youtube.com/watch?v=abcdefghijk' };
const NOT_FOUND = { version: 1, outcome: 'not_found', reason: 'audio_probe_original_unavailable', startedAt: 1720000000000,
  finishedAt: 1720000000020, events: [{ code: 'audio_probe_original_unavailable', outcome: 'unavailable' }], truncated: false };
const FOUND = { ...NOT_FOUND, outcome: 'found', reason: 'audio_probe_found',
  events: [{ code: 'audio_probe_found', outcome: 'success' }] };

async function settle(jobs) { await jobs.ready; while (jobs.draining) await jobs.draining; }
function discoveries(job) { return (job.retry?.events || job.events || []).filter(event => event.audioDiscovery).map(event => event.audioDiscovery); }
function reportHistory(jobs, id) {
  const bytes = fs.readFileSync(jobs.auditBundle(id));
  let offset = 0;
  while (bytes.readUInt32LE(offset) === 0x04034b50) {
    assert.equal(bytes.readUInt16LE(offset + 8), 0, 'the report wrapper uses stored ZIP entries');
    const size = bytes.readUInt32LE(offset + 18), nameLength = bytes.readUInt16LE(offset + 26), extraLength = bytes.readUInt16LE(offset + 28);
    const name = bytes.subarray(offset + 30, offset + 30 + nameLength).toString('utf8');
    const dataOffset = offset + 30 + nameLength + extraLength;
    if (name === 'attempt-history.json') return JSON.parse(bytes.subarray(dataOffset, dataOffset + size));
    offset = dataOffset + size;
  }
  assert.fail('attempt history must be available without a completed FeedPak');
}
async function fixture(t, options = {}) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'feedforge-discovery-jobs-'));
  const root = path.join(directory, 'jobs'), outputDir = path.join(directory, 'output');
  fs.mkdirSync(outputDir);
  const calls = { acquire: 0, findAudio: 0, converter: 0 }, instances = [];
  function acquireNormally(chart, context) {
    const filename = path.join(context.directory, 'source.gp'); fs.writeFileSync(filename, 'Synthetic score');
    return { path: filename, metadata: { songId: chart.id, revisionId: '456', approval: 'approved', artist: chart.artist, title: chart.title },
      ...(options.audio ? { audio: options.audio } : {}) };
  }
  const provider = {
    async acquire(chart, context) {
      calls.acquire++;
      return options.acquire ? options.acquire(chart, context, acquireNormally) : acquireNormally(chart, context);
    },
    async findAudio(chart, context) { calls.findAudio++; return options.findAudio ? options.findAudio(chart, context) : null; },
  };
  const config = { root, provider, artworkLookup: false, ...(options.historyWriter ? { historyWriter: options.historyWriter } : {}),
    runConverter: async () => { calls.converter++; return { code: 1, stdout: JSON.stringify({ ok: false, code: 'needs_audio', error: 'Synthetic converter boundary.' }) }; } };
  const open = async () => { const jobs = new SongsterrJobs(config); instances.push(jobs); await settle(jobs); return jobs; };
  const jobs = await open();
  t.after(async () => {
    for (const instance of instances) await instance.dispose();
    const resolved = path.resolve(directory);
    assert.equal(path.dirname(resolved), path.resolve(os.tmpdir()));
    assert.ok(path.basename(resolved).startsWith('feedforge-discovery-jobs-'));
    fs.rmSync(resolved, { recursive: true, force: true });
  });
  return { jobs, directory, root, outputDir, config, calls, open,
    enqueue: (chart = CHART) => jobs.enqueue(chart, { outputDir }) };
}

test('unavailable recording reason survives app restart and is exported in the job report', async t => {
  const f = await fixture(t, { acquire: async (chart, context, normal) => {
    await context.onAudioDiscovery(NOT_FOUND);
    const saved = JSON.parse(fs.readFileSync(path.join(f.root, 'jobs.json'))).jobs[0];
    assert.deepEqual(discoveries(saved), [NOT_FOUND], 'the awaited callback durably saves before acquisition continues');
    return normal(chart, context);
  } });
  const first = await f.enqueue(); await settle(f.jobs);
  assert.equal(f.jobs.snapshot()[0].state, 'needs_audio');
  assert.equal(f.calls.converter, 0);
  assert.deepEqual(discoveries(f.jobs.jobs[0]), [NOT_FOUND]);
  assert.deepEqual(f.jobs.snapshot()[0].playerNotices.warnings, [], 'operational discovery evidence is not a chart limitation');
  assert.deepEqual(f.jobs.snapshot()[0].playerNotices.information, []);
  await f.jobs.dispose(); const restored = await f.open();
  assert.deepEqual(discoveries(restored.jobs[0]), [NOT_FOUND]);
  const report = reportHistory(restored, first.id);
  assert.equal(report.jobId, first.id);
  assert.deepEqual(report.retainedCycles.flatMap(discoveries), [NOT_FOUND]);
  assert.equal(f.calls.acquire, 1, 'opening saved history must not rediscover or reacquire');
});

test('retry records its own successful discovery while retaining the original failed cycle', async t => {
  const f = await fixture(t, { acquire: async (chart, context, normal) => {
    await context.onAudioDiscovery(NOT_FOUND); return normal(chart, context);
  }, findAudio: async (_chart, context) => { await context.onAudioDiscovery(FOUND); return AUDIO; } });
  const first = await f.enqueue(); await settle(f.jobs);
  const oldCycle = f.jobs.jobs[0].retry.cycle, scoreHash = f.jobs.jobs[0].cachedScoreHash;
  await f.jobs.dispose(); const restored = await f.open();
  await restored.retry(first.id); await settle(restored);
  assert.equal(f.calls.acquire, 1); assert.equal(f.calls.findAudio, 1);
  assert.equal(restored.jobs[0].cachedScoreHash, scoreHash);
  assert.notEqual(restored.jobs[0].retry.cycle, oldCycle);
  assert.deepEqual(restored.jobs[0].retry.cycles, [oldCycle]);
  assert.deepEqual(discoveries(restored.jobs[0]), [FOUND]);
  const report = reportHistory(restored, first.id);
  assert.deepEqual(report.retainedCycles.flatMap(discoveries), [NOT_FOUND, FOUND]);
});

test('fresh imports and unrelated songs have separate diagnostics and report histories', async t => {
  const f = await fixture(t, { acquire: async (chart, context, normal) => {
    await context.onAudioDiscovery(chart.id === CHART.id ? NOT_FOUND : FOUND); return normal(chart, context);
  } });
  const first = await f.enqueue(); await settle(f.jobs);
  const second = await f.enqueue({ ...CHART, id: '124' }); await settle(f.jobs);
  const fresh = await f.jobs.enqueue(CHART, { outputDir: f.outputDir, freshFrom: first.id }); await settle(f.jobs);
  assert.notEqual(first.id, fresh.id);
  for (const [id, summary] of [[first.id, NOT_FOUND], [second.id, FOUND], [fresh.id, NOT_FOUND]]) {
    const history = reportHistory(f.jobs, id);
    assert.equal(history.jobId, id); assert.equal(history.retainedCycles.length, 1);
    assert.deepEqual(history.retainedCycles.flatMap(discoveries), [summary]);
    assert.ok(history.retainedCycles.every(cycle => cycle.jobId === id));
  }
});

test('manual recording retry and direct known audio do not invent a discovery run', async t => {
  const f = await fixture(t);
  const first = await f.enqueue(); await settle(f.jobs);
  await f.jobs.retry(first.id, { audio: AUDIO }); await settle(f.jobs);
  assert.equal(f.calls.findAudio, 0); assert.equal(f.calls.converter, 1);
  assert.deepEqual(reportHistory(f.jobs, first.id).retainedCycles.flatMap(discoveries), []);
  const known = await fixture(t, { audio: AUDIO });
  const second = await known.enqueue(); await settle(known.jobs);
  assert.equal(known.calls.findAudio, 0); assert.equal(known.calls.converter, 1);
  assert.deepEqual(reportHistory(known.jobs, second.id).retainedCycles.flatMap(discoveries), []);
});

test('explicit replacement recording discovery uses the same durable job callback', async t => {
  const f = await fixture(t, { audio: AUDIO, findAudio: async (_chart, context) => {
    assert.deepEqual(context.excludedVideoIds, [AUDIO.videoId]);
    await context.onAudioDiscovery(NOT_FOUND); return null;
  } });
  const first = await f.enqueue(); await settle(f.jobs);
  await f.jobs.retry(first.id, { rediscoverAudio: true }); await settle(f.jobs);
  assert.equal(f.calls.findAudio, 1); assert.equal(f.calls.converter, 1);
  assert.equal(f.jobs.jobs[0].state, 'needs_audio');
  assert.deepEqual(reportHistory(f.jobs, first.id).retainedCycles.flatMap(discoveries), [NOT_FOUND]);
});

test('failed discovery preserves its classified failure when findAudio rejects', async t => {
  const failure = { ...NOT_FOUND, outcome: 'failed', reason: 'needs_login', events: [] };
  const f = await fixture(t, { findAudio: async (_chart, context) => {
    await context.onAudioDiscovery(failure);
    throw Object.assign(new Error('Synthetic sign-in requirement.'), { code: 'needs_login' });
  } });
  const first = await f.enqueue(); await settle(f.jobs);
  await f.jobs.retry(first.id); await settle(f.jobs);
  assert.equal(f.jobs.jobs[0].state, 'needs_login');
  assert.equal(f.calls.converter, 0);
  assert.deepEqual(reportHistory(f.jobs, first.id).retainedCycles.flatMap(discoveries), [failure]);
});

test('cancelled discovery records cancellation without continuing score acquisition', async t => {
  let entered;
  const probing = new Promise(resolve => { entered = resolve; });
  const cancelled = { ...NOT_FOUND, outcome: 'cancelled', reason: 'cancelled', events: [] };
  const f = await fixture(t, { acquire: async (_chart, context) => {
    const aborted = new Promise(resolve => context.signal.addEventListener('abort', resolve, { once: true }));
    entered(); await aborted;
    await context.onAudioDiscovery(cancelled);
    throw Object.assign(new Error('Synthetic cancellation.'), { code: 'cancelled' });
  } });
  const first = await f.enqueue(); await probing;
  await f.jobs.cancel(first.id); await settle(f.jobs);
  assert.equal(f.jobs.jobs[0].state, 'cancelled'); assert.equal(f.calls.converter, 0);
  assert.equal(f.jobs.jobs[0].scorePath, undefined);
  assert.deepEqual(reportHistory(f.jobs, first.id).retainedCycles.flatMap(discoveries), [cancelled]);
});

test('provider-only fields and arbitrary browser text never enter saved or exported diagnostics', async t => {
  const supplied = { ...NOT_FOUND, cookie: 'SECRET_COOKIE', url: 'https://private.example/SECRET_URL', account: 'SECRET_ACCOUNT',
    events: [{ ...NOT_FOUND.events[0], body: 'SECRET_BROWSER_TEXT' }, { code: 'SECRET_EVENT_CODE', outcome: 'success' }] };
  const expected = { ...NOT_FOUND, truncated: true };
  const f = await fixture(t, { acquire: async (chart, context, normal) => {
    await context.onAudioDiscovery(supplied); return normal(chart, context);
  } });
  const first = await f.enqueue(); await settle(f.jobs);
  assert.deepEqual(discoveries(f.jobs.jobs[0]), [expected]);
  const ledger = fs.readFileSync(path.join(f.root, 'jobs.json'), 'utf8');
  const history = JSON.stringify(reportHistory(f.jobs, first.id));
  assert.doesNotMatch(ledger + history, /SECRET_|private\.example/);
  assert.deepEqual(reportHistory(f.jobs, first.id).retainedCycles.flatMap(discoveries), [expected]);
});

test('malformed or oversized restored diagnostics are safely omitted or bounded in ledger and report', async t => {
  const f = await fixture(t, { acquire: async (chart, context, normal) => {
    await context.onAudioDiscovery(NOT_FOUND); return normal(chart, context);
  } });
  const first = await f.enqueue(); await settle(f.jobs); await f.jobs.dispose();
  const filename = path.join(f.root, 'jobs.json'), ledger = JSON.parse(fs.readFileSync(filename));
  const job = ledger.jobs[0], original = job.retry.events.find(event => event.audioDiscovery);
  const injected = [null, [], { ...NOT_FOUND, reason: 'SECRET_REASON' }, { ...NOT_FOUND, finishedAt: -1 },
    { ...NOT_FOUND, events: Array.from({ length: 100 }, () => ({ ...NOT_FOUND.events[0], text: 'SECRET_TEXT' })), secret: 'SECRET_EXTRA' }];
  job.retry.events = injected.map(audioDiscovery => ({ ...original, audioDiscovery }));
  fs.writeFileSync(filename, JSON.stringify(ledger));
  const cycleFile = path.join(f.jobs.retryHistoryRoot, first.id, job.retry.cycle + '.json');
  fs.writeFileSync(cycleFile, JSON.stringify({ version: 1, jobId: first.id, cycle: job.retry.cycle, events: job.retry.events }));
  const restored = await f.open();
  const summaries = discoveries(restored.jobs[0]);
  assert.equal(summaries.length, 1); assert.equal(summaries[0].events.length, 16); assert.equal(summaries[0].truncated, true);
  const history = reportHistory(restored, first.id);
  assert.deepEqual(history.retainedCycles.flatMap(discoveries), summaries);
  assert.doesNotMatch(fs.readFileSync(filename, 'utf8') + JSON.stringify(history), /SECRET_/);
  assert.equal(restored.jobs[0].state, 'needs_audio'); assert.equal(f.calls.acquire, 1);
});

for (const target of ['cycle', 'ledger']) for (const asynchronous of [false, true]) test(`diagnostic ${target} persistence failure stops acquisition (${asynchronous ? 'async' : 'sync'})`, async t => {
  let continued = false;
  class FailingWriter extends HistoryWriter {
    write(filename, value) {
      const diagnostic = target === 'cycle' ? value.events?.some(event => event.outcome === 'audio_discovery')
        : value.jobs?.some(job => discoveries(job).length);
      if (diagnostic) {
        const error = Object.assign(new Error('Synthetic disk full.'), { code: 'ENOSPC' });
        if (asynchronous) return Promise.reject(error);
        throw error;
      }
      return super.write(filename, value);
    }
  }
  const f = await fixture(t, { historyWriter: new FailingWriter(), acquire: async (chart, context, normal) => {
    await context.onAudioDiscovery(NOT_FOUND); continued = true; return normal(chart, context);
  } });
  await f.enqueue(); await settle(f.jobs);
  assert.equal(continued, false); assert.equal(f.calls.converter, 0);
  assert.equal(f.jobs.persistenceFailed, true); assert.equal(f.jobs.snapshot()[0].state, 'needs_attention');
  assert.equal(f.jobs.persistence.code, 'ENOSPC');
  assert.match(f.jobs.snapshot()[0].error, /history could not be saved/i);
  assert.throws(() => f.jobs.retry(f.jobs.jobs[0].id), /history could not be saved/i);
});

'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { getEventListeners } = require('node:events');
const { createConcurrencyLimiter } = require('../electron/concurrency-limiter.cjs');
const { SongJobs } = require('../electron/song-browser/jobs.cjs');
const { SongsterrJobs } = require('../electron/song-browser/songsterr-jobs.cjs');
const { waitForSharedOperation } = require('../electron/song-browser/shared-operation.cjs');

const RESPONSE = { code: 0, stdout: JSON.stringify({ ok: true }) };
const DEADLINE = 30 * 60 * 1000;

function adapter(source, runConverter) {
  const job = { id: 'test', controller: new AbortController() };
  const jobs = Object.assign(Object.create(source === 'CustomsForge' ? SongJobs.prototype : SongsterrJobs.prototype), {
    root: path.resolve('unused-test-root'), runConverter,
  });
  const run = () => source === 'CustomsForge' ? jobs._converter(job, []) : jobs._run(job, [], jobs.root);
  return { job, run };
}

async function occupiedConverter(t, { rejectAbort = false } = {}) {
  const limiter = createConcurrencyLimiter(1);
  let release;
  const busy = limiter.run(() => new Promise((resolve) => { release = resolve; }));
  await Promise.resolve();
  t.after(async () => { release(); await busy; });
  let starts = 0;
  let signal;
  const runConverter = (_args, options) => {
    signal = options.admissionSignal;
    return limiter.run(() => { starts += 1; return RESPONSE; }, { signal }).catch((error) => {
      // The desktop launcher returns a structured cancellation response. Also
      // cover direct runners that propagate the limiter's AbortError instead.
      if (rejectAbort || error.name !== 'AbortError') throw error;
      return { code: 1, stdout: '', stderr: error.message, diagnostics: { cancelled: true } };
    });
  };
  return { limiter, runConverter, get starts() { return starts; }, get signal() { return signal; } };
}

for (const source of ['CustomsForge', 'Songsterr']) {
  test(`${source} cancellation removes its converter wait without releasing the active conversion`, async (t) => {
    const converter = await occupiedConverter(t);
    const { job, run } = adapter(source, converter.runConverter);
    const rejected = assert.rejects(run(), /Cancelled\./i);
    assert.equal(converter.limiter.waitingCount, 1);
    job.controller.abort();
    assert.equal(converter.limiter.waitingCount, 0);
    await rejected;
    assert.equal(converter.limiter.activeCount, 1);
    assert.equal(converter.starts, 0);
    assert.equal(getEventListeners(job.controller.signal, 'abort').length, 0);
  });
}

for (const rejectAbort of [false, true]) {
  test(`Songsterr timeout cancels queued admission and preserves the timeout error (${rejectAbort ? 'rejected' : 'structured'} runner)`, async (t) => {
    t.mock.timers.enable({ apis: ['setTimeout'] });
    const converter = await occupiedConverter(t, { rejectAbort });
    const { job, run } = adapter('Songsterr', converter.runConverter);
    const rejected = assert.rejects(run(), /Conversion timed out\. Retry with another audio file\./);
    t.mock.timers.tick(DEADLINE - 1);
    assert.equal(converter.limiter.waitingCount, 1);
    assert.equal(converter.signal.aborted, false);
    t.mock.timers.tick(1);
    assert.equal(converter.limiter.waitingCount, 0);
    await rejected;
    assert.equal(job.timedOut, true);
    assert.equal(job.controller.signal.aborted, false, 'A timeout must not turn the job into user cancellation.');
    assert.equal(converter.limiter.activeCount, 1);
    assert.equal(converter.starts, 0);
    assert.equal(getEventListeners(job.controller.signal, 'abort').length, 0);
  });
}

test('Songsterr timeout still terminates an active converter', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  let kills = 0;
  const { job, run } = adapter('Songsterr', (_args, options) => new Promise((resolve) => {
    const child = { exitCode: null, kill() { kills += 1; this.exitCode = 1; resolve({ code: 1, stdout: '' }); } };
    options.onSpawn(child);
  }));
  const rejected = assert.rejects(run(), /Conversion timed out/);
  t.mock.timers.tick(DEADLINE);
  await rejected;
  assert.equal(kills, 1);
  assert.equal(job.controller.signal.aborted, false);
  assert.equal(job.child, null);
});

test('Songsterr successful runs clear their deadline and cancellation listener', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const { job, run } = adapter('Songsterr', async () => RESPONSE);
  assert.deepEqual(await run(), { ok: true });
  assert.equal(getEventListeners(job.controller.signal, 'abort').length, 0);
  t.mock.timers.tick(DEADLINE);
  assert.equal(job.timedOut, undefined);
});

test('cancelling one shared wait leaves other callers and late failures observed', async () => {
  let fail;
  const operation = new Promise((_resolve, reject) => { fail = reject; });
  const controller = new AbortController();
  const first = assert.rejects(waitForSharedOperation(operation, controller.signal), { code: 'cancelled' });
  const otherController = new AbortController();
  const second = assert.rejects(waitForSharedOperation(operation, otherController.signal), /Version check failed/);
  controller.abort();
  await first;
  assert.equal(getEventListeners(controller.signal, 'abort').length, 0);
  fail(new Error('Version check failed'));
  await second;
  assert.equal(getEventListeners(otherController.signal, 'abort').length, 0);
});

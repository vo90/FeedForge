'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const fsp = fs.promises;
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');
const { SongJobs } = require('../electron/song-browser/jobs.cjs');

const CHART = { id: '42', title: 'One', artist: 'Metallica', creator: 'Creator', host: 'dropbox', supported: true, version: '2', updated: '2026-09-01', parts: 'Lead, Bass', tuning: 'E Standard' };
const LEAD = { id: 'lead', name: 'Lead', type: 'guitar', tuning: [0, 0, 0, 0, 0, 0] };
const BASS = { id: 'bass', name: 'Bass', type: 'bass', tuning: [0, 0, 0, 0, 0, 0] };
const preview = (overrides = {}) => ({ title: 'One', artist: 'Metallica', source_platforms: ['pc'], song_count: 1, is_multi_song: false, arrangements: [LEAD, BASS], ...overrides });
const json = (value) => ({ code: 0, stdout: JSON.stringify(value), stderr: '' });
async function fixture(t, options = {}) {
  const directory = await fsp.mkdtemp(path.join(os.tmpdir(), 'feedforge-expansion-jobs-'));
  const root = path.join(directory, 'jobs'), outputDir = path.join(directory, 'output');
  const calls = [], transfers = [], completed = [], managers = [];
  const defaultDownload = async (chart, context) => {
    await fsp.writeFile(context.destination, Buffer.concat([Buffer.from('PSAR'), Buffer.alloc(100, Number(chart.id) % 256)]));
    context.onProgress(100);
    return context.destination;
  };
  const download = async (chart, context) => {
    transfers.push({ chart, parkOnAttention: context.parkOnAttention });
    return options.download ? options.download(chart, context, defaultDownload) : defaultDownload(chart, context);
  };
  const runConverter = async (args) => {
    calls.push(args);
    if (args[0] === '--inspect-json') return json({ ok: true, preview: preview(args[1].endsWith('.feedpak') ? options.converted : options.source) });
    if (args[0] === '--validate-feedpak') return json({ ok: true, results: [{ input_path: args[1], validation: { ok: true, errors: [] } }] });
    assert.equal(args[1], '-o');
    await fsp.writeFile(args[2], 'PK fixture FeedPak validated by the injected converter');
    return { code: 0, stdout: '', stderr: '' };
  };
  const create = () => {
    const manager = new SongJobs({ root, outputDir, download, runConverter, onCompleted: (job) => completed.push(job) });
    managers.push(manager); return manager;
  };
  const manager = create();
  t.after(async () => {
    for (const current of managers) await current.dispose();
    assert.equal(path.dirname(directory), path.resolve(os.tmpdir()));
    assert.match(path.basename(directory), /^feedforge-expansion-jobs-/);
    await fsp.rm(directory, { recursive: true, force: true });
  });
  return { manager, create, root, outputDir, calls, transfers, completed };
}
function batchContext(parts = ['lead']) {
  const batchId = crypto.randomUUID();
  return { batchId, itemId: `${batchId}:42`, selection: { requiredParts: parts, tuning: 'E Standard' } };
}

test('parked batch file choice releases the serial worker so another chart completes', async (t) => {
  const f = await fixture(t, { download: async (chart, context, normal) => {
    if (chart.id === '42') {
      context.onAttention({ message: 'Choose a PSARC version.', candidates: [{ id: 'opaque-one', label: 'One_v2_p.psarc', platform: 'pc' }] });
      const error = new Error('Choose a PSARC version.'); error.code = 'SONG_ATTENTION'; throw error;
    }
    return normal(chart, context);
  } });
  const context = batchContext();
  const [parked, completed] = await Promise.all([
    f.manager.run(CHART, context),
    f.manager.run({ ...CHART, id: '43' }, { ...context, itemId: `${context.batchId}:43` }),
  ]);
  assert.equal(parked.status, 'needs_attention'); assert.equal(parked.state, 'parked');
  assert.equal(parked.batchId, context.batchId); assert.equal(parked.itemId, context.itemId);
  assert.deepEqual(parked.candidates, [{ id: 'opaque-one', label: 'One_v2_p.psarc', platform: 'pc' }]);
  assert.equal(completed.status, 'completed'); assert.ok(fs.existsSync(completed.outputPath));
  assert.deepEqual(f.transfers.map((entry) => entry.chart.id), ['42', '43']);
  assert.ok(f.transfers.every((entry) => entry.parkOnAttention));
  assert.equal(f.manager.current, null);
});

test('cancelling a batch waiting for the same active chart leaves the individual attention job running', async (t) => {
  let started, individualSignal;
  const ready = new Promise((resolve) => { started = resolve; });
  const f = await fixture(t, { download: async (_chart, context) => {
    individualSignal = context.signal;
    return new Promise((_resolve, reject) => {
      context.signal.addEventListener('abort', () => reject(new Error('Individual cancelled during test cleanup.')), { once: true });
      context.onAttention('Sign in to the file host to continue.');
      started();
    });
  } });
  const individual = f.manager.enqueue(CHART);
  await ready;
  assert.equal(f.manager.snapshot().find((job) => job.id === individual.id).state, 'needs_attention');
  const controller = new AbortController();
  const pending = f.manager.run(CHART, { ...batchContext(), signal: controller.signal });
  let timer;
  try {
    controller.abort();
    const outcome = await Promise.race([pending, new Promise((_resolve, reject) => {
      timer = setTimeout(() => reject(new Error('Batch cancellation waited for the unrelated individual job.')), 1000);
    })]);
    assert.equal(outcome.status, 'cancelled');
    assert.equal(individualSignal.aborted, false, 'Cancelling the batch cannot cancel an unrelated individual download.');
    assert.equal(f.manager.snapshot().find((job) => job.id === individual.id).state, 'needs_attention');
    assert.equal(f.manager.snapshot().length, 1, 'The cancelled batch must not create a second job.');
    assert.equal(f.transfers.length, 1);
  } finally {
    clearTimeout(timer);
    await f.manager.cancel(individual.id);
    await pending;
  }
});

test('required arrangements survive conversion and a dropped path is never published', async (t) => {
  const f = await fixture(t, { converted: { arrangements: [LEAD] } });
  const result = await f.manager.run(CHART, batchContext(['bass']));
  assert.equal(result.status, 'needs_attention'); assert.match(result.message, /missing.*bass/);
  assert.deepEqual(result.selection.parts, ['bass']); assert.equal(result.selection.strictPlatform, true);
  assert.equal(result.hasCachedInput, true); assert.deepEqual(await fsp.readdir(f.outputDir), []);
  assert.equal(f.calls.filter((args) => args[0] === '--inspect-json').length, 2);
});

test('arrangement object metadata cannot erase a hard requirement before inspection', async (t) => {
  const f = await fixture(t, { source: { arrangements: [BASS] } });
  const result = await f.manager.run({ ...CHART, parts: [], arrangements: [{ id: 'lead', type: 'lead' }, { id: 'bass', type: 'bass' }] }, batchContext(['lead']));
  assert.equal(result.status, 'needs_attention'); assert.match(result.message, /missing.*lead/);
  assert.deepEqual(result.selection.parts, ['lead']); assert.equal(f.calls.length, 1);
});

test('complementary chart checks its requested path while preserving a separate chart for other paths', async (t) => {
  const f = await fixture(t, { source: { arrangements: [LEAD] }, converted: { arrangements: [LEAD] } });
  const result = await f.manager.run({ ...CHART, parts: ['lead'] }, batchContext(['lead', 'bass']));
  assert.equal(result.status, 'completed'); assert.deepEqual(result.selection.parts, ['lead']);
});

test('batch validates actual PC content and arrangement-specific tuning before conversion', async (t) => {
  for (const [source, expected] of [
    [{ source_platforms: ['mac'] }, /PC PSARC variant|requested PC/],
    [{ source_platforms: [] }, /platform could not be verified/],
    [{ arrangements: [{ ...LEAD, tuning: [-1, -1, -1, -1, -1, -1] }, BASS] }, /tuning could not be verified.*lead/],
  ]) {
    await t.test(String(expected), async (st) => {
      const f = await fixture(st, { source });
      const result = await f.manager.run(CHART, batchContext());
      assert.equal(result.status, 'needs_attention'); assert.match(result.message, expected);
      assert.equal(f.calls.length, 1); assert.deepEqual(await fsp.readdir(f.outputDir), []);
    });
  }
});

test('saved file choice survives queue metadata normalization without opaque browser IDs or links', async (t) => {
  const f = await fixture(t);
  const context = batchContext();
  context.selection.choice = { id: 'old-document-id', label: 'One_v2_p.psarc', platform: 'pc', href: 'https://host.test/?private=token' };
  const result = await f.manager.run(CHART, context);
  assert.equal(result.status, 'completed');
  assert.deepEqual(f.transfers[0].chart.selection.choice, { label: 'One_v2_p.psarc', platform: 'pc' });
  assert.doesNotMatch(await fsp.readFile(path.join(f.root, 'jobs.json'), 'utf8'), /old-document-id|host\.test|private|token/);
});

test('completed receipt restores batch item identity and verified coverage after publication interrupted history save', async (t) => {
  for (const orphan of [false, true]) await t.test(orphan ? 'receipt without ledger job' : 'receipt with interrupted ledger job', async (st) => {
    const f = await fixture(st), context = batchContext();
    const result = await f.manager.run(CHART, context);
    assert.equal(result.status, 'completed'); await f.manager.dispose();
    const ledger = JSON.parse(await fsp.readFile(path.join(f.root, 'jobs.json'), 'utf8'));
    const row = ledger.jobs.find((job) => job.id === result.jobId);
    await fsp.writeFile(path.join(f.root, `${row.id}.receipt.json`), JSON.stringify({ version: 1, job: row }));
    await fsp.writeFile(path.join(f.root, 'jobs.json'), JSON.stringify({ version: 1, jobs: orphan ? [] : [{ ...row, state: 'validating', outputPath: undefined, coverage: undefined, batchId: undefined, itemId: undefined }] }));
    const recovered = f.create().snapshot().find((job) => job.id === row.id);
    assert.equal(recovered.state, 'completed'); assert.equal(recovered.batchId, context.batchId); assert.equal(recovered.itemId, context.itemId);
    assert.equal(recovered.version, '2'); assert.equal(recovered.chartUpdated, '2026-09-01');
    assert.deepEqual(recovered.selection.parts, ['lead']); assert.deepEqual(recovered.coverage.arrangements.map((arr) => arr.id), ['lead', 'bass']);
    assert.equal(recovered.outputPath, result.outputPath); assert.equal(f.transfers.length, 1);
    assert.ok(f.completed.some((entry) => entry.id === row.id && entry.batchId === context.batchId));
  });
});

test('same input hash cannot reuse an earlier FeedPak missing the new requested arrangement', async (t) => {
  const f = await fixture(t, { converted: { arrangements: [LEAD] } });
  const first = await f.manager.run(CHART, batchContext(['lead']));
  assert.equal(first.status, 'completed');
  const second = await f.manager.run(CHART, batchContext(['bass']));
  assert.equal(second.status, 'needs_attention'); assert.match(second.message, /missing.*bass/);
  assert.equal(f.calls.filter((args) => args[1] === '-o').length, 2);
  assert.equal(second.duplicateOf, undefined); assert.ok(fs.existsSync(first.outputPath));
});

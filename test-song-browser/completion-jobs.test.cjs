'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const crypto = require('node:crypto');
const { SongJobs } = require('../electron/song-browser/jobs.cjs');
const { ImportIndex } = require('../electron/song-browser/imports.cjs');
const { normalizeRecipe } = require('../electron/song-browser/provenance.cjs');
const RECIPE = require('./fixture-recipe.cjs');

const CHART = { id: '42', title: 'One', artist: 'Metallica', creator: 'Creator', version: '1', updated: '2026-09-01',
  parts: ['lead', 'bass'], host: 'mediafire', supported: true };
const CHOICE = { label: 'One_p.psarc', platform: 'pc' };
const PSARC = Buffer.concat([Buffer.from('PSAR'), Buffer.alloc(100, 7)]);
const FEEDPAK = Buffer.from('PK synthetic FeedPak: queue boundary tests use an injected converter and validator');
const digest = (bytes) => crypto.createHash('sha256').update(bytes).digest('hex');

function fixture(t, options = {}) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'feedforge-completion-jobs-'));
  const root = path.join(directory, 'jobs'), outputDir = path.join(directory, 'output');
  const downloads = [], calls = [], managers = [], completed = [];
  const state = { failConversion: false };
  let imports = new ImportIndex({ root: path.join(directory, 'imports') });
  const preview = (output) => ({ title: CHART.title, artist: CHART.artist, song_count: 1, is_multi_song: false,
    source_platforms: output ? [] : ['pc'], arrangements: output ? [{ id: 'lead', type: 'guitar' }]
      : [{ id: 'lead', type: 'guitar' }, { id: 'bass', type: 'bass' }] });
  const download = async (chart, context) => {
    downloads.push({ chart, context });
    const choice = chart.selection?.choice || CHOICE;
    context.onResolvedFile?.({ filename: choice.label, platform: choice.platform, sizeBytes: PSARC.length,
      evidence: { filename: 'observed', platform: 'filename_hint', sizeBytes: 'observed' } }, chart.selection?.choice);
    fs.writeFileSync(context.destination, PSARC);
    return context.destination;
  };
  const runConverter = async (args) => {
    calls.push(args);
    if (args[0] === '--inspect-json') return { code: 0, stderr: '', stdout: JSON.stringify({ ok: true, preview: preview(/\.feedpak$/i.test(args[1])) }) };
    if (args[0] === '--validate-feedpak') return { code: 0, stderr: '', stdout: JSON.stringify({ ok: true,
      results: [{ input_path: args[1], validation: { ok: true, errors: [], warnings: [] } }] }) };
    assert.equal(args[1], '-o');
    if (state.failConversion) return { code: 1, stderr: 'Synthetic conversion failure', stdout: '' };
    fs.writeFileSync(args[2], FEEDPAK);
    return { code: 0, stdout: 'Converted', stderr: '' };
  };
  const makeManager = (extra = {}) => {
    const manager = new SongJobs({ root, outputDir, recipe: RECIPE, download, runConverter,
      onCompleted: (job) => { completed.push(job); if (options.onCompleted) options.onCompleted(job); else imports.record(job); },
      ...(options.durable ? { findReusable: async ({ chart, ...request }) => {
        options.onLookup?.(request); return imports.find(chart, request);
      } } : {}), ...extra });
    managers.push(manager);
    return manager;
  };
  const manager = makeManager();
  const finish = async (created, owner = manager) => {
    const job = owner.jobs.find((entry) => entry.id === created.id);
    assert.ok(job, 'The newly enqueued owned job exists');
    await job.done;
    return owner.snapshot().find((entry) => entry.id === created.id);
  };
  t.after(async () => {
    for (const owner of managers) await owner.dispose();
    assert.equal(path.dirname(directory), path.resolve(os.tmpdir()));
    assert.match(path.basename(directory), /^feedforge-completion-jobs-/);
    fs.rmSync(directory, { recursive: true, force: true });
  });
  return { directory, root, outputDir, manager, state, downloads, calls, completed, finish, makeManager,
    index: () => imports, reloadImports: () => { imports = new ImportIndex({ root: path.join(directory, 'imports') }); } };
}

function oldTerminal(id) {
  return { id: crypto.randomUUID(), chartId: String(id), title: 'Old cancelled work', artist: 'Artist',
    state: 'cancelled', createdAt: 1, updatedAt: 1, progress: 0, supported: false };
}

test('same source bytes cannot reuse an output made with another recipe or options', async (t) => {
  const f = fixture(t);
  const first = await f.finish(f.manager.enqueue({ ...CHART, selection: { parts: ['lead'] } }));
  assert.equal(first.state, 'completed');
  const recipe = normalizeRecipe({ ...RECIPE, options: { ...RECIPE.options, pipeline: 2 } });
  f.manager.recipe = recipe;
  const second = await f.finish(f.manager.enqueue({ ...CHART, recipe, selection: { parts: ['lead'] } }));
  assert.equal(second.state, 'completed');
  assert.equal(first.sourceHash, second.sourceHash);
  assert.notEqual(second.recipe.identity, first.recipe.identity);
  assert.equal(second.duplicateOf, undefined);
  assert.notEqual(second.outputPath, first.outputPath);
  assert.equal(f.calls.filter((args) => args[1] === '-o').length, 2);
  assert.deepEqual(fs.readFileSync(first.outputPath), FEEDPAK, 'The prior output remains accessible');
});

test('batch cached retries are bound to the exact intent and selected descriptor', async (t) => {
  for (const scenario of ['same intent and choice', 'different intent', 'different choice', 'review another file']) {
    await t.test(scenario, async (st) => {
      const f = fixture(st), batchId = crypto.randomUUID(), itemId = `${batchId}:${CHART.id}`, intentId = crypto.randomUUID();
      f.state.failConversion = true;
      const context = { batchId, itemId, intentId, recipe: RECIPE, selection: { requiredParts: ['lead'], choice: CHOICE } };
      const first = await f.manager.run(CHART, context);
      assert.equal(first.status, 'failed');
      assert.equal(first.hasCachedInput, true);
      f.state.failConversion = false;
      const next = { ...context, selection: { ...context.selection } };
      if (scenario === 'different intent') next.intentId = crypto.randomUUID();
      if (scenario === 'different choice') next.selection.choice = { label: 'One_v2_p.psarc', platform: 'pc' };
      if (scenario === 'review another file') next.reviewAnother = true;
      const second = await f.manager.run(CHART, next);
      assert.equal(second.status, 'completed');
      assert.equal(second.intentId, next.intentId);
      assert.equal(f.downloads.length, scenario === 'same intent and choice' ? 1 : 2);
      assert.equal(second.resolvedFile.filename, next.selection.choice.label);
      assert.equal(second.sourceHash, digest(PSARC));
    });
  }
});

test('a single cached retry after converter change gets a new intent and uses the actual new recipe', async (t) => {
  const f = fixture(t);
  f.state.failConversion = true;
  const first = await f.finish(f.manager.enqueue({ ...CHART, selection: { parts: ['lead'], choice: CHOICE } }));
  const recipe = normalizeRecipe({ ...RECIPE, build: 'changed-converter-bytes' });
  f.manager.recipe = recipe; f.state.failConversion = false;
  const second = await f.finish(f.manager.retry(first.id));
  assert.equal(second.state, 'completed');
  assert.notEqual(second.intentId, first.intentId);
  assert.equal(second.recipe.identity, recipe.identity);
  assert.equal(f.downloads.length, 1);
  assert.equal(second.resolvedFile.filename, CHOICE.label);
});

test('durable exact-source lookup survives history eviction and persists only the reused output coverage', async (t) => {
  const lookups = [], f = fixture(t, { durable: true, onLookup: (request) => lookups.push(request) });
  const first = await f.finish(f.manager.enqueue({ ...CHART, selection: { parts: ['lead'] } }));
  assert.deepEqual(first.coverage.arrangements.map((arr) => arr.id), ['lead']);
  for (let i = 1000; i < 1100; i++) f.manager.jobs.push(oldTerminal(i));
  assert.equal(f.manager._persist(), true);
  assert.equal(f.manager.snapshot().some((job) => job.id === first.id), false, 'The source job has actually left the bounded history');
  f.reloadImports();
  assert.ok(f.index().snapshot().some((entry) => entry.id === first.id));
  const second = await f.finish(f.manager.enqueue({ ...CHART, id: '43', selection: { parts: ['lead'] } }));
  assert.equal(second.state, 'completed');
  assert.equal(second.duplicateOf, first.id);
  assert.equal(second.outputPath, first.outputPath);
  assert.equal(f.calls.filter((args) => args[1] === '-o').length, 1);
  assert.deepEqual(second.coverage.arrangements.map((arr) => arr.id), ['lead'], 'Source bass coverage must not be attributed to the reused FeedPak');
  assert.equal(lookups.at(-1).sourceHash, digest(PSARC));
  assert.equal(lookups.at(-1).recipe.identity, normalizeRecipe(RECIPE).identity);
  assert.ok(lookups.at(-1).signal instanceof AbortSignal);
  assert.equal(await f.index().find(CHART, { recipe: RECIPE, sourceHash: digest(PSARC), requirements: { parts: ['bass'] } }), null);
});

test('an unindexed published receipt survives history pressure and indexes successfully after restart', async (t) => {
  const f = fixture(t, { onCompleted: () => { throw new Error('Synthetic import disk failure'); } });
  const first = await f.finish(f.manager.enqueue({ ...CHART, selection: { parts: ['lead'] } }));
  const receipt = path.join(f.root, `${first.id}.receipt.json`), original = fs.readFileSync(receipt);
  assert.equal(first.state, 'completed');
  for (let i = 1000; i < 1100; i++) f.manager.jobs.push(oldTerminal(i));
  assert.equal(f.manager._persist(), true);
  assert.equal(f.manager.jobs.length, 100);
  assert.ok(f.manager.snapshot().some((job) => job.id === first.id));
  assert.deepEqual(fs.readFileSync(receipt), original);
  await f.manager.dispose();
  const recovered = f.makeManager({ onCompleted: (job) => f.index().record(job),
    download: async () => { throw new Error('Restart recovery must not download'); },
    runConverter: async () => { throw new Error('Restart recovery must not convert'); } });
  assert.equal(recovered.snapshot().find((job) => job.id === first.id).state, 'completed');
  assert.ok(f.index().snapshot().some((entry) => entry.id === first.id));
  assert.equal(fs.existsSync(receipt), false, 'Receipt is removed only once indexing succeeded');
  assert.deepEqual(fs.readFileSync(first.outputPath), FEEDPAK);
});

test('legacy job and receipt migration preserves published facts without inventing a recipe', async (t) => {
  const f = fixture(t);
  const first = await f.finish(f.manager.enqueue({ ...CHART, selection: { parts: ['lead'] } }));
  await f.manager.dispose();
  const ledger = path.join(f.root, 'jobs.json'), receipt = path.join(f.root, `${first.id}.receipt.json`);
  // Deliberately include modern-looking fields in a v1 envelope: they are not
  // trusted provenance for a schema that did not define those fields.
  const oldLedger = Buffer.from(JSON.stringify({ version: 1, jobs: [{ ...first, state: 'validating' }] }));
  const oldReceipt = Buffer.from(JSON.stringify({ version: 1, job: first }));
  fs.writeFileSync(ledger, oldLedger); fs.writeFileSync(receipt, oldReceipt);
  const recovered = f.makeManager({ onCompleted: () => {} });
  const historical = recovered.snapshot().find((job) => job.id === first.id);
  assert.equal(historical.state, 'completed');
  assert.equal(historical.recipe, null);
  assert.equal(historical.resolvedFile, null);
  assert.equal(historical.intentId, undefined);
  assert.equal(historical.reuseCompatible, false);
  assert.deepEqual(fs.readFileSync(ledger + '.v1.bak'), oldLedger);
  assert.deepEqual(fs.readFileSync(receipt + '.v1.bak'), oldReceipt);
  const second = await f.finish(recovered.enqueue({ ...CHART, selection: { parts: ['lead'] } }), recovered);
  assert.equal(second.state, 'completed');
  assert.equal(second.duplicateOf, undefined);
  assert.notEqual(second.outputPath, historical.outputPath);
  assert.equal(second.recipe.identity, normalizeRecipe(RECIPE).identity);
  assert.equal(f.calls.filter((args) => args[1] === '-o').length, 2);
});

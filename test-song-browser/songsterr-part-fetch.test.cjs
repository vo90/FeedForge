'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const syncFs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const { createRequire } = require('node:module');
const zlib = require('node:zlib');
const adapter = require('../electron/song-browser/providers/songsterr/acquire.cjs');
const policy = require('../electron/song-browser/providers/songsterr/policy.cjs');

const descriptor = { id: '123', revisionId: '456', approval: 'approved', title: 'Synthetic', artist: 'Artist' };
const part = index => ({ marker: index, measures: [{ voices: [{ beats: [{ duration: [1, 1], notes: [] }] }] }] });
const metadata = count => ({ songId: 123, revisionId: 456, image: 'fixture-image', tracks: Array.from({ length: count }, (_, i) => ({ name: `Track ${i}` })) });
const bytes = value => Buffer.from(JSON.stringify(value));
const response = (value, status = 200, headers = {}) => new Response(Buffer.isBuffer(value) ? value : bytes(value), { status, headers });
const indexOf = url => url.includes('/api/meta/') ? -1 : Number(/\/(\d+)\.json$/.exec(url)[1]);
const tick = () => new Promise(resolve => setImmediate(resolve));
function deferred() { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; }
async function directory(t) {
  const folder = await fs.mkdtemp(path.join(os.tmpdir(), 'feedforge-part-fetch-'));
  t.after(() => fs.rm(folder, { recursive: true, force: true }));
  return folder;
}
// Exercise real byte-accounting code with small limits rather than allocating
// hundreds of MiB beside the ordinary test suite.
function limited(overrides) {
  const filename = require.resolve('../electron/song-browser/providers/songsterr/acquire.cjs');
  const originalRequire = createRequire(filename), module = { exports: {} };
  vm.runInThisContext(`(function(require,module,exports){${syncFs.readFileSync(filename, 'utf8')}\n})`, { filename })(
    name => name === './policy.cjs' ? { ...policy, ...overrides } : originalRequire(name), module, module.exports);
  return module.exports;
}

test('read-ahead is bounded to two and reversed completions preserve exact source bytes and progress order', async t => {
  const folder = await directory(t), meta = metadata(5), gates = Array.from({ length: 5 }, deferred), calls = [], progress = [];
  let active = 0, maximum = 0;
  const running = adapter.acquireAnonymous(descriptor, { directory: folder, onProgress: p => progress.push(p.completed), fetch: async (url, options) => {
    const index = indexOf(url); calls.push(index);
    assert.equal(options.credentials, 'omit'); assert.equal(options.redirect, 'error');
    if (index < 0) return response(meta);
    if (index === 0) return response(part(index));
    active++; maximum = Math.max(maximum, active);
    try { return await gates[index].promise; } finally { active--; }
  } });
  await tick(); assert.deepEqual(calls, [-1, 0, 1, 2]); assert.deepEqual(progress, [1]);
  gates[2].resolve(response(part(2))); await tick();
  assert.deepEqual(calls, [-1, 0, 1, 2]); assert.deepEqual(progress, [1]);
  gates[1].resolve(response(part(1))); await tick();
  assert.deepEqual(calls, [-1, 0, 1, 2, 3, 4]);
  gates[4].resolve(response(part(4))); gates[3].resolve(response(part(3)));
  const result = await running;
  assert.equal(maximum, 2); assert.equal(active, 0); assert.deepEqual(progress, [1, 2, 3, 4, 5]);
  assert.equal(await fs.readFile(result.path, 'utf8'), JSON.stringify({ format: 'songsterr', songId: '123', revisionId: '456',
    title: 'Synthetic', artist: 'Artist', tracks: meta.tracks, parts: Array.from({ length: 5 }, (_, index) => part(index)) }));
});

test('a later HTTP failure does not replace the earlier semantic error or discard its observed Retry-After', async t => {
  const folder = await directory(t), gate = deferred(), calls = [];
  const running = adapter.acquireAnonymous(descriptor, { directory: folder, fetch: async url => {
    const index = indexOf(url); calls.push(index);
    if (index < 0) return response(metadata(5));
    if (index === 0) return response(part(0));
    if (index === 1) return gate.promise;
    return response({}, 429, { 'retry-after': '30' });
  } });
  const rejected = assert.rejects(running, error => {
    assert.equal(error.code, 'incomplete_score');
    const facts = error[adapter.ACQUISITION_TRANSPORTS];
    assert.equal(facts.length, 1); assert.equal(facts[0].status, 429); assert.ok(facts[0].retryAfterAt > Date.now());
    return true;
  });
  await tick(); gate.resolve(response({})); await rejected;
  assert.deepEqual(calls, [-1, 0, 1, 2]); assert.deepEqual(await fs.readdir(folder), []);
});

for (const mode of ['invalid-json', 'connection-reset']) test(`an immediate ${mode} keeps its ordered error and the sibling HTTP cooldown despite abort races`, async t => {
  const folder = await directory(t);
  await assert.rejects(adapter.acquireAnonymous(descriptor, { directory: folder, fetch: async url => {
    const index = indexOf(url);
    if (index < 0) return response(metadata(4));
    if (index === 0) return response(part(0));
    if (index === 1) {
      if (mode === 'connection-reset') throw Object.assign(new Error('reset'), { code: 'ECONNRESET' });
      return response(Buffer.from('{invalid'));
    }
    return response({}, 429, { 'retry-after': '60' });
  } }), error => {
    assert.equal(error.code, mode === 'connection-reset' ? 'network_error' : 'invalid_score');
    if (mode === 'connection-reset') assert.equal(error.transport.reason, 'connection_reset');
    assert.equal(error[adapter.ACQUISITION_TRANSPORTS].length, 1);
    assert.equal(error[adapter.ACQUISITION_TRANSPORTS][0].status, 429);
    return true;
  });
  assert.deepEqual(await fs.readdir(folder), []);
});

test('the first-part gate completes validation before any read-ahead starts', async t => {
  const folder = await directory(t), first = deferred(), calls = [];
  const running = adapter.acquireAnonymous(descriptor, { directory: folder, fetch: async url => {
    const index = indexOf(url); calls.push(index);
    return index < 0 ? response(metadata(4)) : index === 0 ? first.promise : response(part(index));
  } });
  const rejected = assert.rejects(running, { code: 'incomplete_score' });
  await tick(); assert.deepEqual(calls, [-1, 0]);
  first.resolve(response({})); await rejected;
  assert.deepEqual(calls, [-1, 0]); assert.deepEqual(await fs.readdir(folder), []);
});

test('cancellation after the last accepted part still prevents the source write', async t => {
  const folder = await directory(t), controller = new AbortController();
  await assert.rejects(adapter.acquireAnonymous(descriptor, { directory: folder, signal: controller.signal,
    onProgress: p => { if (p.completed === 4) controller.abort(); }, fetch: async url => {
      const index = indexOf(url); return response(index < 0 ? metadata(4) : part(index));
    } }), { code: 'cancelled' });
  assert.deepEqual(await fs.readdir(folder), []);
});

test('cancellation during two streamed bodies closes both iterators', async t => {
  const folder = await directory(t), controller = new AbortController(), opened = [], closed = [];
  const running = adapter.acquireAnonymous(descriptor, { directory: folder, signal: controller.signal, fetch: async (url, { signal }) => {
    const index = indexOf(url);
    if (index < 0) return response(metadata(4));
    if (index === 0) return response(part(0));
    return { ok: true, status: 200, headers: { get: () => null }, body: (async function* () {
      opened.push(index);
      try {
        yield Buffer.from('{');
        await new Promise((resolve, reject) => signal.addEventListener('abort', () => reject(new Error('stream aborted')), { once: true }));
      } finally { closed.push(index); }
    })() };
  } });
  const rejected = assert.rejects(running, { code: 'cancelled' });
  await tick(); assert.deepEqual(opened, [1, 2]); controller.abort(); await rejected; await tick();
  assert.deepEqual(closed.sort(), [1, 2]); assert.deepEqual(await fs.readdir(folder), []);
});

for (const mode of ['cancel', 'progress', 'part-error', 'timeout']) test(`${mode} aborts and settles pending siblings without writing a partial source`, async t => {
  const folder = await directory(t), controller = new AbortController(), first = deferred(), started = [], aborted = [];
  const api = mode === 'timeout' ? limited({ bounded: (promise, signal, ignored, abort) => policy.bounded(promise, signal, 20, abort) }) : adapter;
  const running = api.acquireAnonymous(descriptor, { directory: folder, signal: controller.signal,
    onProgress: p => { if (mode === 'progress' && p.completed === 2) throw new Error('progress failed'); },
    fetch: async (url, { signal }) => {
      const index = indexOf(url);
      if (index < 0) return response(metadata(5));
      if (index === 0) return response(part(0));
      started.push(index);
      return new Promise((resolve, reject) => {
        signal.addEventListener('abort', () => { aborted.push(index); reject(new Error('aborted transport')); }, { once: true });
        if (index === 1 && mode !== 'timeout') first.promise.then(resolve);
      });
    } });
  const rejected = assert.rejects(running, error => mode === 'progress' ? error.message === 'progress failed'
    : error.code === (mode === 'cancel' ? 'cancelled' : mode === 'timeout' ? 'timeout' : 'incomplete_score'));
  await tick(); assert.deepEqual(started, [1, 2]);
  if (mode === 'cancel') controller.abort();
  else if (mode !== 'timeout') first.resolve(response(mode === 'part-error' ? {} : part(1)));
  await rejected; assert.ok(aborted.includes(2)); assert.deepEqual(await fs.readdir(folder), []);
});

test('a late cancellation-ignoring response is observed and cannot write a source', async t => {
  const folder = await directory(t), controller = new AbortController(), late = deferred();
  const running = adapter.acquireAnonymous(descriptor, { directory: folder, signal: controller.signal, fetch: async url => {
    const index = indexOf(url);
    return index < 0 ? response(metadata(3)) : index === 0 ? response(part(0)) : late.promise;
  } });
  const rejected = assert.rejects(running, { code: 'cancelled' });
  await tick(); controller.abort(); await rejected;
  late.resolve(response(part(1))); await tick(); await tick();
  assert.deepEqual(await fs.readdir(folder), []);
});

test('gzip read-ahead commits the decoded aggregate budget in source order at its exact boundary', async t => {
  const meta = metadata(5), parts = Array.from({ length: 5 }, (_, index) => ({ ...part(index), padding: 'x'.repeat(200) }));
  const total = bytes(meta).length + parts.reduce((sum, value) => sum + bytes(value).length, 0);
  for (const delta of [0, -1]) {
    const folder = await directory(t), api = limited({ MAX_SCORE_BYTES: 1024, MAX_TOTAL_BYTES: total + delta });
    const running = api.acquireAnonymous(descriptor, { directory: folder, fetch: async url => {
      const index = indexOf(url); return response(index < 0 ? meta : zlib.gzipSync(bytes(parts[index])));
    } });
    if (delta === 0) assert.deepEqual(JSON.parse(await fs.readFile((await running).path, 'utf8')).parts, parts);
    else { await assert.rejects(running, { code: 'invalid_score' }); assert.deepEqual(await fs.readdir(folder), []); }
  }
});

for (const mode of ['header', 'stream', 'inflate', 'truncated-gzip']) test(`${mode} limits reject a speculative part with no partial file`, async t => {
  const folder = await directory(t), api = limited({ MAX_SCORE_BYTES: 1024, MAX_TOTAL_BYTES: 4096 });
  await assert.rejects(api.acquireAnonymous(descriptor, { directory: folder, fetch: async url => {
    const index = indexOf(url);
    if (index < 0) return response(metadata(4));
    if (index !== 1) return response(part(index));
    if (mode === 'header') return response({}, 200, { 'content-length': '1025' });
    if (mode === 'stream') return { ok: true, status: 200, headers: { get: () => null }, body: (async function* () { yield Buffer.alloc(700); yield Buffer.alloc(700); })() };
    const compressed = zlib.gzipSync(bytes({ ...part(1), padding: 'x'.repeat(mode === 'inflate' ? 1500 : 10) }));
    return response(mode === 'truncated-gzip' ? compressed.subarray(0, compressed.length - 5) : compressed);
  } }), { code: ['header', 'stream'].includes(mode) ? 'score_too_large' : 'invalid_score' });
  assert.deepEqual(await fs.readdir(folder), []);
});

test('two overlapping ordinary bodies cannot exceed the aggregate decoded limit', async t => {
  const folder = await directory(t), meta = metadata(3), parts = Array.from({ length: 3 }, (_, index) => ({ ...part(index), padding: 'x'.repeat(200) }));
  const total = bytes(meta).length + parts.reduce((sum, value) => sum + bytes(value).length, 0);
  const api = limited({ MAX_SCORE_BYTES: 1024, MAX_TOTAL_BYTES: total - 1 });
  await assert.rejects(api.acquireAnonymous(descriptor, { directory: folder, fetch: async url => {
    const index = indexOf(url); return response(index < 0 ? meta : parts[index]);
  } }), { code: 'score_too_large' });
  assert.deepEqual(await fs.readdir(folder), []);
});

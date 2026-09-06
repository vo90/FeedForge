'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { createRequire } = require('node:module');

// Exercise the production collector and page reader with no Electron process or
// network. Only this VM's timers are accelerated; requested delays stay visible.
function harness(handler, { timerStarted = () => {} } = {}) {
  const filename = path.join(__dirname, '../electron/song-browser/browser.cjs');
  const source = fs.readFileSync(filename, 'utf8');
  const timers = [], requests = [], progress = [];
  let elapsed = 0;
  class FixtureDate extends Date { static now() { return 1_000_000 + elapsed; } }
  const module = { exports: {} };
  const context = { module, exports: module.exports, require: createRequire(filename), URL, Date: FixtureDate,
    setTimeout(callback, delay) {
      timers.push(delay);
      const handle = setTimeout(() => { elapsed += delay; callback(); }, 1);
      timerStarted(delay);
      return handle;
    }, clearTimeout };
  vm.runInNewContext(source, context, { filename });
  const browser = Object.create(module.exports.CustomsForgeBrowser.prototype);
  browser.disposed = false; browser.searching = false; browser.lastSearch = null; browser.connectionRead = 0;
  browser.diagnostic = () => {}; browser.updateConnection = () => {};
  if (handler) browser._searchPage = async (request) => {
    requests.push(JSON.parse(JSON.stringify(request)));
    return handler(request, requests.length);
  };
  const request = { query: 'Meshuggah', sort: { field: 'title', direction: 'asc' } };
  const collect = (options = {}) => browser._collect(request, { onProgress: (event) => progress.push(event), ...options });
  return { browser, request, requests, progress, timers, collect, advance: (milliseconds) => { elapsed += milliseconds; } };
}
const ready = (page, ids, { total = 3, hasNext = page < 3, sort } = {}) => ({ status: 'ready', page, total, hasNext,
  results: ids.map((id) => ({ id: String(id), artist: 'Meshuggah', title: `Song ${id}`, host: 'dropbox' })), ...(sort ? { sort } : {}) });
const failure = (code) => Object.assign(new Error('Fixture failure'), { code });

test('transient failure re-anchors and verifies every collected prefix page before retrying Next', async () => {
  let currentPage = 0, failed = false;
  const fixture = harness(({ page }) => {
    if (page === 1) currentPage = 1;
    else { assert.equal(page, currentPage + 1, 'never click Next on an unanchored or already advanced page'); currentPage = page; }
    if (page === 3 && !failed) { failed = true; throw failure('ERR_CONNECTION_RESET'); }
    return ready(page, [page]);
  });
  const result = await fixture.collect();
  assert.equal(result.complete, true); assert.deepEqual(Array.from(result.results, (row) => row.id), ['1', '2', '3']);
  assert.deepEqual(fixture.requests.map((request) => request.page), [1, 2, 3, 1, 2, 3]);
  for (const request of fixture.requests) {
    assert.equal(request.query, 'Meshuggah'); assert.deepEqual(request.sort, { field: 'title', direction: 'asc' });
  }
  assert.deepEqual(fixture.timers, [300, 300, 1000]);
  assert.deepEqual(fixture.progress.map((value) => ({ ...value })), [
    { collected: 1, total: 3, page: 1 }, { collected: 2, total: 3, page: 2 },
    { collected: 2, total: 3, page: 3, retrying: true, attempt: 2 }, { collected: 3, total: 3, page: 3 },
  ]);
});

test('a failed page gets at most three attempts with one- and three-second backoffs', async () => {
  let attempts = 0;
  const fixture = harness(({ page }) => {
    if (page === 2) { attempts++; throw failure('SONG_NETWORK'); }
    return ready(1, [1], { total: 2, hasNext: true });
  });
  await assert.rejects(fixture.collect(), (error) => error.code === 'SONG_NETWORK');
  assert.equal(attempts, 3);
  assert.deepEqual(fixture.requests.map((value) => value.page), [1, 2, 1, 2, 1, 2]);
  assert.deepEqual(fixture.timers, [300, 1000, 3000]);
  assert.equal(fixture.progress.filter((value) => value.retrying).length, 2);
});

test('challenge, sign-in, permission, rate limit and malformed responses never enter retry backoff', async (t) => {
  for (const state of ['challenge', 'login_required', 'layout_changed']) {
    await t.test(state, async () => {
      const fixture = harness(() => ({ status: state, results: [], error: 'Review this catalogue page.' }));
      await assert.rejects(fixture.collect(), /Review this catalogue page/);
      assert.equal(fixture.requests.length, 1); assert.deepEqual(fixture.timers, []); assert.deepEqual(fixture.progress, []);
    });
  }
  for (const code of ['ERR_ACCESS_DENIED', 'SONG_RATE_LIMIT', 'SONG_PERMISSION', 'ERR_ABORTED', undefined]) {
    await t.test(String(code), async () => {
      const fixture = harness(() => { throw failure(code); });
      await assert.rejects(fixture.collect(), (error) => error.code === code);
      assert.equal(fixture.requests.length, 1); assert.deepEqual(fixture.timers, []);
    });
  }
});

test('cancelling while a retry backoff is pending rejects promptly without another page read', async () => {
  const controller = new AbortController();
  let enteredBackoff;
  const waiting = new Promise((resolve) => { enteredBackoff = resolve; });
  const fixture = harness(() => { throw failure('ERR_TIMED_OUT'); }, { timerStarted: (delay) => { if (delay === 1000) enteredBackoff(); } });
  const result = fixture.collect({ signal: controller.signal });
  const settled = result.then(() => ({ ok: true }), (error) => ({ error }));
  await waiting;
  controller.abort();
  let guard;
  const outcome = await Promise.race([settled, new Promise((resolve) => { guard = setTimeout(() => resolve({ timeout: true }), 500); })]);
  clearTimeout(guard);
  assert.equal(outcome.timeout, undefined); assert.equal(outcome.error?.name, 'AbortError');
  assert.equal(fixture.requests.length, 1); assert.deepEqual(fixture.timers, [1000]);
});

test('changed prefix contents or totals during recovery stop before the failed page is revisited', async (t) => {
  for (const changed of ['rows', 'total', 'challenge']) {
    await t.test(changed, async () => {
      const fixture = harness(({ page }, call) => {
        if (page === 2) throw failure('ERR_NETWORK_CHANGED');
        if (call > 1 && changed === 'challenge') return { status: 'challenge', results: [], error: 'Complete the browser check.' };
        return ready(1, [call > 1 && changed === 'rows' ? 'replacement' : '1'], { total: call > 1 && changed === 'total' ? 3 : 2, hasNext: true });
      });
      await assert.rejects(fixture.collect(), /changed during retry|browser check/);
      assert.deepEqual(fixture.requests.map((request) => request.page), [1, 2, 1]);
      assert.deepEqual(fixture.timers, [300, 1000]);
    });
  }
});

test('inconsistent totals, repeated pages, overlapping IDs and incomplete final pages never report completion', async (t) => {
  const scenarios = {
    total: [ready(1, [1]), ready(2, [2], { total: 4 })],
    repeated: [ready(1, [1]), ready(2, [1])],
    overlap: [ready(1, [1, 2], { total: 4 }), ready(2, [2, 3], { total: 4, hasNext: false })],
    incomplete: [ready(1, [1], { total: 3, hasNext: false })],
  };
  for (const [label, pages] of Object.entries(scenarios)) {
    await t.test(label, async () => {
      const fixture = harness(({ page }) => pages[page - 1]);
      await assert.rejects(fixture.collect(), /changed|repeated|moved|complete search/);
      assert.equal(fixture.progress.some((event) => event.retrying), false);
      assert.ok(fixture.requests.length <= 2);
    });
  }
});

test('filtered search forwards collection progress and publishes only the complete filtered snapshot', async () => {
  const fixture = harness(({ page }) => ready(page, [page], { total: 2, hasNext: page < 2 }));
  const result = await fixture.browser.search({ query: 'Meshuggah', filters: { exactArtist: 'Meshuggah' } }, { onProgress: (event) => fixture.progress.push(event) });
  assert.equal(result.complete, true); assert.equal(result.sourceTotal, 2); assert.equal(result.total, 2);
  assert.deepEqual(fixture.progress.map((event) => event.collected), [1, 2]);
  assert.equal(fixture.browser.catalogueSignal, null);
  const calls = fixture.requests.length;
  await fixture.browser.search({ query: 'Meshuggah', filters: { exactArtist: 'Meshuggah' } });
  assert.equal(fixture.requests.length, calls, 'same successful filtered snapshot is retained');
});

test('collector rejects an observed page or sort identity that disagrees with the requested page', async (t) => {
  for (const mismatch of ['page', 'sort']) {
    await t.test(mismatch, async () => {
      const fixture = harness(() => ready(mismatch === 'page' ? 2 : 1, ['wrong'], { total: 1, hasNext: false,
        sort: { field: 'title', direction: mismatch === 'sort' ? 'desc' : 'asc' } }));
      await assert.rejects(fixture.collect(), /page|sort|catalogue|identity/i);
      assert.equal(fixture.requests.length, 1);
    });
  }
});

test('matching prefix row IDs cannot hide a changed sort during retry recovery', async () => {
  const fixture = harness(({ page }, call) => {
    if (page === 2) throw failure('SONG_NETWORK');
    return ready(1, [1], { total: 2, hasNext: true, sort: { field: 'title', direction: call > 1 ? 'desc' : 'asc' } });
  });
  await assert.rejects(fixture.collect(), /sort|catalogue|identity/i);
  assert.deepEqual(fixture.requests.map((request) => request.page), [1, 2, 1]);
});

test('page reader cannot rewrite a stale first-page number into a successful requested page', async () => {
  const fixture = harness();
  fixture.browser.ensureSearchWindow = () => ({ isDestroyed: () => false, webContents: { mainFrame: { executeJavaScript: async () => ready(7, ['stale'], { total: 1, hasNext: false }) } } });
  fixture.browser.navigate = async () => {};
  await assert.rejects(fixture.browser._searchPage({ query: 'Meshuggah', page: 1 }), /page|catalogue|identity/i);
  assert.equal(fixture.browser.lastSearch, null);
});

test('catalogue sorting can finish after ten seconds without accepting earlier rows', async () => {
  const fixture = harness();
  let applied = false, sortReads = 0, tableReads = 0;
  fixture.browser.ensureSearchWindow = () => ({ isDestroyed: () => false, webContents: { mainFrame: { executeJavaScript: async (script) => {
    if (script.includes('function requestSearchSort(')) {
      sortReads++;
      applied = fixture.timers.reduce((sum, delay) => sum + delay, 0) >= 10_000;
      return { status: applied ? 'applied' : sortReads === 1 ? 'clicked' : 'waiting', ...(applied ? { sort: fixture.request.sort } : {}) };
    }
    tableReads++;
    return ready(1, applied ? ['1', '2'] : ['2', '1'], { total: 2, hasNext: false,
      sort: applied ? fixture.request.sort : { field: 'title', direction: 'desc' } });
  } } } });
  fixture.browser.navigate = async () => {};
  const result = await fixture.browser.search(fixture.request);
  assert.equal(result.status, 'ready'); assert.deepEqual(Array.from(result.results, (row) => row.id), ['1', '2']);
  assert.deepEqual({ ...result.sort }, fixture.request.sort);
  assert.equal(tableReads, 2, 'Only the initial table and the confirmed sorted table are read.');
  assert.ok(sortReads > 24, 'The old 8.4-second attempt limit must not terminate an active sort.');
  const waited = fixture.timers.reduce((sum, delay) => sum + delay, 0);
  assert.ok(waited >= 10_000 && waited < 11_000);
});

test('a catalogue sort that never finishes is bounded by its thirty-second deadline', async () => {
  const fixture = harness();
  let sortReads = 0, tableReads = 0;
  fixture.browser.ensureSearchWindow = () => ({ isDestroyed: () => false, webContents: { mainFrame: { executeJavaScript: async (script) => {
    if (script.includes('function requestSearchSort(')) { sortReads++; return { status: sortReads === 1 ? 'clicked' : 'waiting' }; }
    tableReads++; return ready(1, ['2', '1'], { total: 2, hasNext: false });
  } } } });
  fixture.browser.navigate = async () => {};
  await assert.rejects(fixture.browser.search(fixture.request), /catalogue sort did not finish/i);
  const waited = fixture.timers.reduce((sum, delay) => sum + delay, 0);
  assert.ok(waited >= 30_000 && waited <= 30_350, 'Waiting must end at the bounded sort deadline.');
  assert.ok(sortReads <= 90); assert.equal(tableReads, 1, 'A timeout cannot publish the original unsorted rows.');
  assert.equal(fixture.browser.lastSearch, null); assert.equal(fixture.browser.searching, false);
});

test('cancelling an in-flight catalogue sort stops promptly without another sort or table read', async () => {
  const controller = new AbortController();
  let enteredWait;
  const waiting = new Promise((resolve) => { enteredWait = resolve; });
  const fixture = harness(undefined, { timerStarted: (delay) => { if (delay === 350) enteredWait(); } });
  let sortReads = 0, tableReads = 0;
  fixture.browser.ensureSearchWindow = () => ({ isDestroyed: () => false, webContents: { mainFrame: { executeJavaScript: async (script) => {
    if (script.includes('function requestSearchSort(')) { sortReads++; return { status: 'clicked' }; }
    tableReads++; return ready(1, ['2', '1'], { total: 2, hasNext: false });
  } } } });
  fixture.browser.navigate = async () => {};
  const settled = fixture.browser.search(fixture.request, { signal: controller.signal }).then(() => ({ ok: true }), (error) => ({ error }));
  await waiting; controller.abort();
  let guard;
  const outcome = await Promise.race([settled, new Promise((resolve) => { guard = setTimeout(() => resolve({ timeout: true }), 500); })]);
  clearTimeout(guard);
  assert.equal(outcome.timeout, undefined); assert.equal(outcome.error?.name, 'AbortError');
  assert.equal(sortReads, 1); assert.equal(tableReads, 1); assert.deepEqual(fixture.timers, [350]);
  assert.equal(fixture.browser.lastSearch, null); assert.equal(fixture.browser.searching, false);
});

test('classified transient errors honor a longer finite service wait without reducing normal backoff', async (t) => {
  for (const [retryAfterMs, expected] of [[5000, 5000], [1500.5, 1501], [200, 1000], [0, 1000], [-1, 1000], [Infinity, 1000], [NaN, 1000], ['5000', 1000]]) {
    await t.test(String(retryAfterMs), async () => {
      const fixture = harness((_request, call) => {
        if (call === 1) throw Object.assign(failure('SONG_NETWORK'), { retryAfterMs });
        return ready(1, [1], { total: 1, hasNext: false });
      });
      const result = await fixture.collect();
      assert.equal(result.complete, true); assert.deepEqual(fixture.timers, [expected]); assert.equal(fixture.requests.length, 2);
    });
  }
  const denied = harness(() => { throw Object.assign(failure('SONG_PERMISSION'), { retryAfterMs: 5000 }); });
  await assert.rejects(denied.collect(), (error) => error.code === 'SONG_PERMISSION');
  assert.deepEqual(denied.timers, [], 'a service wait does not make a nontransient failure retryable');
});

test('a service wait beyond the remaining collection budget stops without scheduling a timer', async () => {
  let fixture;
  fixture = harness(() => { fixture.advance(590_000); throw Object.assign(failure('SONG_NETWORK'), { retryAfterMs: 15_000 }); });
  await assert.rejects(fixture.collect(), /retry wait.*remaining search time/i);
  assert.equal(fixture.requests.length, 1); assert.deepEqual(fixture.timers, []);
});

test('successful prefix replay cannot consume the remaining time and continue to another page', async () => {
  let fixture;
  fixture = harness(({ page }, call) => {
    if (page === 3) throw failure('SONG_NETWORK');
    if (call === 4) fixture.advance(600_000);
    return ready(page, [page]);
  });
  await assert.rejects(fixture.collect(), /timed out/i);
  assert.deepEqual(fixture.requests.map((request) => request.page), [1, 2, 3, 1]);
  assert.equal(fixture.progress.filter((event) => !event.retrying).at(-1).collected, 2);
});

test('cancellation observed during a successful restored page stops the rest of prefix replay', async () => {
  const controller = new AbortController();
  const fixture = harness(({ page }, call) => {
    if (page === 3) throw failure('SONG_NETWORK');
    if (call === 4) controller.abort();
    return ready(page, [page]);
  });
  await assert.rejects(fixture.collect({ signal: controller.signal }), (error) => error.name === 'AbortError');
  assert.deepEqual(fixture.requests.map((request) => request.page), [1, 2, 3, 1]);
});

test('a final successful page cannot publish completion after the collection deadline', async () => {
  let fixture;
  fixture = harness(() => { fixture.advance(600_000); return ready(1, [1], { total: 1, hasNext: false }); });
  await assert.rejects(fixture.collect(), /timed out/i);
  assert.deepEqual(fixture.progress, []); assert.equal(fixture.requests.length, 1);
});

test('final progress cancellation is checked before returning the completed collection', async () => {
  const controller = new AbortController();
  const fixture = harness(() => ready(1, [1], { total: 1, hasNext: false }));
  await assert.rejects(fixture.collect({ signal: controller.signal, onProgress: () => controller.abort() }), (error) => error.name === 'AbortError');
});

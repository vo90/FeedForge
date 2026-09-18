const test = require('node:test');
const assert = require('node:assert/strict');
const moduleReady = import('../ui/src/song-browser/search-session.mjs');

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

test('search state is retained per API without starting a request on subscription', async () => {
  const { getSearchSession } = await moduleReady;
  let calls = 0;
  const api = { search() { calls++; } };
  const session = getSearchSession(api);
  const initial = session.getSnapshot();
  const unsubscribe = session.subscribe(() => {});
  assert.equal(session.getSnapshot(), initial, 'React snapshots must stay stable until an update');
  session.setQuery('Metallica One');
  unsubscribe();
  assert.equal(getSearchSession(api), session);
  assert.equal(getSearchSession(api).getSnapshot().query, 'Metallica One');
  assert.equal(getSearchSession({ search: api.search }).getSnapshot().query, '');
  assert.equal(calls, 0, 'Mounting, returning to a view and editing a draft must not search');
});

test('a search finishes without a mounted view and its result is available on return', async () => {
  const { getSearchSession } = await moduleReady;
  const response = deferred();
  let calls = 0, notifications = 0;
  const api = { search: () => { calls++; return response.promise; } };
  const session = getSearchSession(api);
  const unsubscribe = session.subscribe(() => { notifications++; });
  session.setQuery('BTS Dynamite');
  const pending = session.search(session.getSnapshot().query);
  unsubscribe();
  const beforeUnmount = notifications;
  const returning = getSearchSession(api);
  assert.equal(returning.getSnapshot().pending, true);
  await Promise.resolve();
  assert.equal(calls, 1);
  response.resolve({ status: 'ready', results: [{ id: '54639', title: 'Dynamite' }], hasNext: false });
  await pending;
  assert.equal(notifications, beforeUnmount);
  assert.equal(returning.getSnapshot().pending, false);
  assert.equal(returning.getSnapshot().result.results[0].id, '54639');
  assert.equal(returning.getSnapshot().result.page, 1);
  assert.equal(returning.getSnapshot().searchedQuery, 'BTS Dynamite');
});

test('returning during a search joins its promise instead of issuing another request', async () => {
  const { getSearchSession } = await moduleReady;
  const response = deferred(), calls = [];
  const api = { search: (args) => { calls.push(args); return response.promise; } };
  const session = getSearchSession(api);
  const pending = session.search('  Metallica  ', 2);
  assert.equal(getSearchSession(api).search('Metallica', 2), pending);
  await Promise.resolve();
  assert.deepEqual(calls, [{ query: 'Metallica', page: 2 }]);
  session.setQuery('Unsubmitted next song');
  response.resolve({ status: 'ready', results: [], page: 2 });
  await pending;
  assert.equal(session.getSnapshot().query, 'Unsubmitted next song');
  assert.equal(session.getSnapshot().searchedQuery, 'Metallica');
  assert.equal(session.getSnapshot().result.page, 2);
});

test('a newer query wins even when an older request finishes or fails later', async () => {
  const { getSearchSession } = await moduleReady;
  for (const failOld of [false, true]) {
    const old = deferred(), latest = deferred();
    const session = getSearchSession({ search: ({ query }) => query === 'Old song' ? old.promise : latest.promise });
    const first = session.search('Old song');
    const second = session.search('New song');
    assert.notEqual(first, second);
    latest.resolve({ status: 'ready', results: [{ id: '2' }] });
    await second;
    if (failOld) old.reject(new Error('Obsolete failure')); else old.resolve({ status: 'ready', results: [{ id: '1' }] });
    await first;
    assert.equal(session.getSnapshot().result.results[0].id, '2');
    assert.equal(session.getSnapshot().searchedQuery, 'New song');
    assert.equal(session.getSnapshot().error, '');
    assert.equal(session.getSnapshot().pending, false);
  }
});

test('an old request finishing cannot end the newer pending state', async () => {
  const { getSearchSession } = await moduleReady;
  const old = deferred(), latest = deferred();
  const session = getSearchSession({ search: ({ query }) => query === 'Old song' ? old.promise : latest.promise });
  const first = session.search('Old song');
  const second = session.search('New song');
  old.resolve({ status: 'ready', results: [{ id: '1' }] });
  await first;
  assert.equal(session.getSnapshot().pending, true);
  assert.equal(session.getSnapshot().result, null);
  latest.resolve({ status: 'ready', results: [{ id: '2' }] });
  await second;
  assert.equal(session.getSnapshot().pending, false);
});

test('sort and filter controls persist, reset pages and define selection independently of sorting', async () => {
  const { getSearchSession } = await moduleReady;
  const calls = [];
  const api = { search: async (request) => { calls.push(request); return { status: 'ready', results: [], page: request.page }; } };
  const session = getSearchSession(api);
  session.setSort({ field: 'downloads', direction: 'desc' });
  session.setFilters({ exactArtist: '  Meshuggah ', parts: ['bass', 'lead'] });
  assert.equal(session.getSnapshot().filters.exactArtist, '  Meshuggah ', 'draft spaces remain editable until submission');
  assert.equal(calls.length, 0, 'editing controls must not navigate');
  await session.search('Meshuggah');
  assert.equal(calls[0].filters.exactArtist, 'Meshuggah');
  assert.deepEqual(calls[0].filters.parts, ['lead', 'bass']);
  const scope = session.getSnapshot().selectionScope;
  await session.search('Meshuggah', 2);
  assert.equal(calls[1].page, 2);
  assert.equal(session.getSnapshot().selectionScope, scope);
  session.setSort({ field: 'title', direction: 'asc' });
  await session.search('Meshuggah', 2);
  assert.equal(calls[2].page, 1, 'new sort resets page');
  assert.equal(session.getSnapshot().selectionScope, scope, 'sort does not discard selections');
  session.setFilters({ tuning: 'Bb Standard' });
  await session.search('Meshuggah', 2);
  assert.equal(calls[3].page, 1, 'new filter resets page');
  assert.notEqual(session.getSnapshot().selectionScope, scope);
  assert.equal(getSearchSession(api).getSnapshot().filters.tuning, 'Bb Standard');
});

test('object requests carry validated search settings and identical pending requests join', async () => {
  const { getSearchSession } = await moduleReady;
  const response = deferred(), calls = [];
  const session = getSearchSession({ search: (request) => { calls.push(request); return response.promise; } });
  const request = { query: 'Iron Maiden', sort: { field: 'updated', direction: 'desc' }, filters: { creator: 'Creator', hideConverted: true } };
  const pending = session.search(request);
  assert.equal(session.search(request), pending);
  await Promise.resolve();
  assert.equal(calls.length, 1);
  assert.deepEqual(calls[0].sort, request.sort);
  assert.equal(calls[0].filters.hideConverted, true);
  response.resolve({ status: 'ready', results: [] });
  await pending;
  await session.search({ ...request, sort: { field: 'rating', direction: 'desc' } });
  assert.equal(calls.length, 1);
  assert.match(session.getSnapshot().error, /search order/);
});

test('arrangement matching persists and paging keeps the applied mode until Search is pressed', async () => {
  const { getSearchSession } = await moduleReady;
  const calls = [];
  const api = { search: async (request) => { calls.push(request); return { status: 'ready', results: [], page: request.page, hasNext: true }; } };
  const session = getSearchSession(api);
  assert.equal(session.getSnapshot().filters.partsMatch, 'all', 'existing searches require every checked arrangement');
  session.setFilters({ parts: ['rhythm', 'lead'], partsMatch: 'any' });
  await session.search('Green Lung');
  const anyScope = session.getSnapshot().selectionScope;
  assert.deepEqual(calls[0].filters.parts, ['lead', 'rhythm']);
  assert.equal(calls[0].filters.partsMatch, 'any');
  assert.equal(getSearchSession(api).getSnapshot().filters.partsMatch, 'any');

  session.setFilters({ partsMatch: 'all' });
  session.setSort({ field: 'downloads', direction: 'desc' });
  await session.changePage(2);
  const paged = session.getSnapshot();
  assert.equal(calls[1].page, 2);
  assert.equal(calls[1].filters.partsMatch, 'any', 'paging uses the applied search rather than draft controls');
  assert.deepEqual(calls[1].sort, { field: 'title', direction: 'asc' });
  assert.equal(paged.searchedRequest.filters.partsMatch, 'any', 'downloads still use the mode that produced the results');
  assert.equal(paged.filters.partsMatch, 'all', 'draft controls remain editable until submission');
  assert.deepEqual(paged.sort, { field: 'downloads', direction: 'desc' });
  assert.equal(paged.selectionScope, anyScope);

  await session.search('Green Lung', 2);
  assert.equal(calls[2].page, 1, 'submitting a different match mode starts at page one');
  assert.equal(calls[2].filters.partsMatch, 'all');
  assert.notEqual(session.getSnapshot().selectionScope, anyScope, 'AND selections cannot leak into an OR result set');
});

test('any and all arrangement searches are distinct pending requests and obsolete results cannot replace the mode', async () => {
  const { getSearchSession } = await moduleReady;
  const responses = { all: deferred(), any: deferred() }, calls = [];
  const session = getSearchSession({ search: (request) => { calls.push(request); return responses[request.filters.partsMatch].promise; } });
  const base = { query: 'Green Lung', filters: { parts: ['lead', 'rhythm'], partsMatch: 'all' } };
  const first = session.search(base);
  const second = session.search({ ...base, filters: { ...base.filters, partsMatch: 'any' } });
  assert.notEqual(first, second);
  assert.equal(session.search({ ...base, filters: { ...base.filters, partsMatch: 'any' } }), second);
  await Promise.resolve();
  assert.deepEqual(calls.map((request) => request.filters.partsMatch), ['all', 'any']);
  responses.any.resolve({ status: 'ready', results: [{ id: 'lead-only' }] });
  await second;
  responses.all.resolve({ status: 'ready', results: [{ id: 'both' }] });
  await first;
  assert.equal(session.getSnapshot().result.results[0].id, 'lead-only');
  assert.equal(session.getSnapshot().searchedRequest.filters.partsMatch, 'any');
});

test('arrangement match validation rejects invalid modes and leaves an empty parts filter unrestricted', async () => {
  const { getSearchSession } = await moduleReady;
  const calls = [];
  const session = getSearchSession({ search: async (request) => { calls.push(request); return { status: 'ready', results: [] }; } });
  for (const partsMatch of ['or', '', true, 1, []]) {
    await session.search({ query: 'Green Lung', filters: { partsMatch } });
    assert.match(session.getSnapshot().error, /any or all selected arrangements/);
  }
  assert.equal(calls.length, 0);
  session.setFilters({ partsMatch: 'any' });
  await session.search('Green Lung');
  assert.deepEqual(calls[0].filters.parts, []);
  assert.equal(calls[0].filters.partsMatch, 'any');
});

test('failed requests settle across navigation and can be retried', async () => {
  const { getSearchSession } = await moduleReady;
  let attempt = 0;
  const api = { search: async () => {
    if (++attempt === 1) throw new Error('Connection interrupted');
    if (attempt === 2) return { ok: false, error: 'Browser not ready' };
    return { status: 'ready', results: [] };
  } };
  const session = getSearchSession(api);
  await session.search('One');
  assert.equal(session.getSnapshot().pending, false);
  assert.equal(getSearchSession(api).getSnapshot().error, 'Connection interrupted');
  await session.search('One');
  assert.equal(session.getSnapshot().error, 'Browser not ready');
  await session.search('One');
  assert.equal(session.getSnapshot().error, '');
  assert.equal(session.getSnapshot().result.status, 'ready');
  assert.equal(attempt, 3);
});

test('login and challenge results survive navigation without being mistaken for an empty search', async () => {
  const { getSearchSession } = await moduleReady;
  for (const status of ['login_required', 'challenge', 'layout_changed']) {
    const api = { search: async () => ({ status, error: 'Continue in the browser', results: [] }) };
    await getSearchSession(api).search('One');
    const state = getSearchSession(api).getSnapshot();
    assert.equal(state.pending, false);
    assert.equal(state.result.status, status);
    assert.equal(state.result.error, 'Continue in the browser');
  }
});

test('invalid submissions and absent responses cannot strand a session as searching', async () => {
  const { getSearchSession } = await moduleReady;
  let calls = 0;
  const session = getSearchSession({ search: async () => { calls++; } });
  for (const [query, page] of [['x', 1], ['x'.repeat(161), 1], ['One', 0], ['One', 1.5]]) {
    await session.search(query, page);
    assert.equal(session.getSnapshot().pending, false);
  }
  assert.equal(calls, 0);
  await session.search('One');
  assert.equal(calls, 1);
  assert.match(session.getSnapshot().error, /no response/);
  assert.equal(session.getSnapshot().pending, false);
  assert.equal(session.getSnapshot().result, null);
});


test('scoped collection progress cannot cross cancellation or newer search generations', async () => {
  const { getSearchSession } = await moduleReady;
  const first = deferred(), second = deferred(), calls = [], cancellations = [];
  const session = getSearchSession({ search: (request) => { calls.push(request); return calls.length === 1 ? first.promise : second.promise; }, cancelSearch: async (request) => { cancellations.push(request); } });
  const old = session.search('Old song'); await Promise.resolve();
  const oldId = calls[0].requestId;
  session.setProgress({ requestId: oldId, collected: 50, total: 100, page: 1, pending: true });
  assert.equal(session.getSnapshot().progress.collected, 50);
  const latest = session.search('New song'); await Promise.resolve();
  const newId = calls[1].requestId;
  assert.notEqual(newId, oldId); assert.deepEqual(cancellations, [{ requestId: oldId }]);
  session.setProgress({ requestId: oldId, collected: 100, page: 2 });
  assert.equal(session.getSnapshot().progress, null);
  session.setProgress({ requestId: newId, collected: 20, total: null, page: 1, retrying: true, attempt: 2 });
  assert.equal(session.getSnapshot().progress.attempt, 2);
  await session.cancel();
  assert.deepEqual(cancellations, [{ requestId: oldId }, { requestId: newId }]);
  session.setProgress({ requestId: newId, collected: 30 });
  first.resolve({ status: 'ready', results: [{ id: 'old' }] }); second.resolve({ status: 'ready', results: [{ id: 'new' }] });
  await Promise.all([old, latest]);
  assert.equal(session.getSnapshot().pending, false); assert.equal(session.getSnapshot().progress, null);
  assert.equal(session.getSnapshot().result, null); assert.equal(session.getSnapshot().error, 'Search cancelled.');
});

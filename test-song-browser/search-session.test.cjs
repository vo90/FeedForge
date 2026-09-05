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
  assert.equal(session.search('Another query'), pending);
  await Promise.resolve();
  assert.deepEqual(calls, [{ query: 'Metallica', page: 2 }]);
  session.setQuery('Unsubmitted next song');
  response.resolve({ status: 'ready', results: [], page: 2 });
  await pending;
  assert.equal(session.getSnapshot().query, 'Unsubmitted next song');
  assert.equal(session.getSnapshot().searchedQuery, 'Metallica');
  assert.equal(session.getSnapshot().result.page, 2);
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

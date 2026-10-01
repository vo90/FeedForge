'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const path = require('node:path');
const { SongsterrProvider } = require('../electron/song-browser/providers/songsterr/browser.cjs');

const song = { id: '90203', title: 'R U Mine?', artist: 'Arctic Monkeys', url: 'https://www.songsterr.com/a/wsa/arctic-monkeys-r-u-mine-tab-s90203' };
const popular = { id: '455118', title: 'Master of Puppets', artist: 'Metallica', url: 'https://www.songsterr.com/a/wsa/metallica-master-of-puppets-tab-s455118' };
function setup(options = {}) {
  const session = () => Object.assign(new EventEmitter(), { setPermissionRequestHandler() {}, setPermissionCheckHandler() {} });
  const provider = new SongsterrProvider({ BrowserWindow: function () {}, session: { fromPartition: session, fromPath: session }, profilePath: path.resolve('unused-search-test-profile') });
  let url, page, reads = 0, afterAction = false;
  const actions = [], navigations = [], window = { webContents: { getURL: () => url } };
  provider._window = () => window;
  provider._navigate = async (_window, target) => {
    navigations.push(target); afterAction = false; reads = 0;
    url = options.redirect ? 'https://www.songsterr.com/' : target;
    const query = new URL(url).searchParams.get('pattern');
    page = { status: 'ready', url, searchQuery: query, searchInput: query || '', canSearch: !options.missingControl,
      searchHome: options.redirect, searchReady: !options.redirect, results: options.redirect ? [popular] : [song] };
    if (options.initialStatus) page.status = options.initialStatus;
  };
  provider._act = async (_window, action, _signal, { query }) => {
    actions.push({ action, query }); afterAction = true;
    if (options.actionFails) return { ok: false };
    url = 'https://www.songsterr.com/?' + new URLSearchParams({ pattern: query });
    page = { ...page, url, searchQuery: query, searchInput: query, searchHome: false,
      searchReady: true, results: options.empty ? [] : [song] };
    return { ok: true };
  };
  provider._read = async () => {
    if (!afterAction) return page;
    reads++;
    options.onRead?.();
    if (options.changedQuery) return { ...page, searchQuery: 'another query', searchInput: 'another query' };
    if (options.changedUrl) { url = 'https://www.songsterr.com/'; return page; }
    if (options.recoveryStatus) return { ...page, status: options.recoveryStatus };
    if (options.neverFinishes || reads <= (options.pendingReads || 0)) return { ...page, searchReady: false, results: [popular] };
    return page;
  };
  const wait = provider._wait;
  provider._wait = (win, predicate, signal) => wait.call(provider, win, predicate, signal, options.timeout || 1500);
  return { provider, actions, navigations, reads: () => reads };
}

test('ordinary URL search needs no recovery', async () => {
  const { provider, actions } = setup();
  assert.equal((await provider.search({ query: 'Arctic Monkeys' })).results[0].id, song.id);
  assert.deepEqual(actions, []);
});

for (const query of ['R U Mine?', 'Metallica Am I Evil?', 'Björk — Who Is It?', 'AC/DC & friends?']) {
  test(`lost-query redirect recovers the complete query: ${query}`, async () => {
    const { provider, actions, navigations, reads } = setup({ redirect: true, pendingReads: 1 });
    const result = await provider.search({ query });
    assert.equal(result.results[0].id, song.id);
    assert.deepEqual(actions, [{ action: 'search', query }]);
    assert.equal(navigations.length, 1); assert.ok(reads() >= 2);
    assert.equal(provider.results.has(popular.id), false, 'The default catalogue must never enter the result ledger.');
  });
}

test('a completed empty response is returned as empty, not replaced with popular songs', async () => {
  const { provider } = setup({ redirect: true, empty: true });
  assert.deepEqual((await provider.search({ query: 'nothing?' })).results, []);
});

for (const option of ['neverFinishes', 'changedQuery', 'changedUrl', 'actionFails', 'missingControl']) {
  test(`unsuccessful recovery cannot publish unrelated results: ${option}`, async () => {
    const { provider, actions } = setup({ redirect: true, [option]: true, timeout: 10 });
    await assert.rejects(provider.search({ query: 'R U Mine?' }), error => ['search_unavailable', 'timeout'].includes(error.code));
    assert.equal(provider.results.size, 0); assert.ok(actions.length <= 1); assert.equal(provider.searching, false);
  });
}

test('cancelled recovery publishes nothing and releases the search for a later request', async () => {
  const controller = new AbortController();
  const { provider, navigations } = setup({ redirect: true, onRead: () => controller.abort() });
  await assert.rejects(provider.search({ query: 'R U Mine?' }, { signal: controller.signal }), { name: 'AbortError' });
  assert.equal(provider.results.size, 0); assert.equal(provider.searching, false);
  await provider.search({ query: 'Am I Evil?' });
  assert.equal(navigations.length, 2);
});

for (const stage of ['initialStatus', 'recoveryStatus']) {
  test(`website checks stay visible as a needs-attention error at ${stage}`, async () => {
    const { provider } = setup({ redirect: true, [stage]: 'needs_attention' });
    await assert.rejects(provider.search({ query: 'R U Mine?' }), { code: 'needs_attention' });
    assert.equal(provider.results.size, 0);
  });
  test(`sign-in remains distinct at ${stage}`, async () => {
    const { provider } = setup({ redirect: true, [stage]: 'needs_login' });
    assert.equal((await provider.search({ query: 'R U Mine?' })).status, 'needs_login');
    assert.equal(provider.results.size, 0);
  });
}

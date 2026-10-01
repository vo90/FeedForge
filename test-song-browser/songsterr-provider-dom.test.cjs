'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const { readSongsterrPage, actOnSongsterrPage } = require('../electron/song-browser/providers/songsterr/dom.cjs');

// Rendered-page fixtures only. They exercise the serialized page functions,
// not a second implementation of the acquisition or account flow.
class Element {
  constructor(tag, attrs = {}, children = [], own = '') {
    this.tagName = tag.toUpperCase(); this.attrs = attrs; this.children = children; this.own = own; this.style = {}; this.clicked = 0;
    this.hidden = attrs.hidden === true; this.value = attrs.value || ''; this.events = []; for (const child of children) child.parentElement = this;
  }
  get textContent() { return [this.own, ...this.children.map((child) => child.textContent)].filter(Boolean).join(' '); }
  get innerText() { return [this.own, ...this.children.map((child) => child.innerText)].filter(Boolean).join('\n'); }
  getAttribute(name) { return this.attrs[name] ?? null; }
  getClientRects() { return this.hidden || this.rectCount === 0 ? [] : [{}]; }
  click() { this.clicked++; }
  dispatchEvent(event) { this.events.push(event.type); return true; }
  descendants() { return this.children.flatMap((child) => [child, ...child.descendants()]); }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  querySelectorAll(selector) {
    const matches = (node, part) => {
      if (part === '*') return true;
      if (part.startsWith('#')) return node.getAttribute('id') === part.slice(1);
      const match = /^([a-z]+)?(?:\[([^=*\]]+)(\*?=)?"?([^"\]]*)"?\])?$/i.exec(part);
      if (!match) throw new Error('Unsupported fixture selector: ' + part);
      if (match[1] && node.tagName !== match[1].toUpperCase()) return false;
      if (!match[2]) return true;
      const value = node.getAttribute(match[2]);
      if (!match[3]) return value !== null;
      return match[3] === '*=' ? String(value || '').includes(match[4]) : value === match[4];
    };
    return this.descendants().filter((node) => selector.split(',').some((part) => matches(node, part.trim())));
  }
}
const el = (tag, attrs, children, text) => new Element(tag, attrs, children, text);
const span = (text) => el('span', {}, [], text);
function page(children, url = 'https://www.songsterr.com/?pattern=green+lung') {
  const body = el('body', {}, children), doc = el('document', {}, [body]); doc.body = body; doc.URL = url; doc.title = 'Songsterr'; return doc;
}
function run(fn, document, request) { return JSON.parse(JSON.stringify(vm.runInNewContext(`(${fn.toString()})(request)`, { document, location: { href: document.URL }, request, URL, Event }))); }

test('rendered public search extracts separate titles/artists, deduplicates IDs and ignores unrelated links', () => {
  const href = '/a/wsa/green-lung-woodland-rites-tab-s564073';
  const doc = page([el('main', { id: 'panel-search' }, [
    el('input', { placeholder: 'Search over a million tabs', value: 'green lung' }),
    el('div', { 'data-list': 'songs' }, [
    el('a', { href: '/signin' }, [], 'SIGN IN'),
    el('a', { href }, [el('span', { 'data-testid': 'song-title' }, [], 'Woodland Rites'), el('span', { 'data-testid': 'artist' }, [], 'Green Lung')]),
    el('a', { href }, [span('Woodland Rites'), span('Green Lung')]),
    el('a', { href: 'https://evil.test/a/wsa/copy-tab-s564073' }, [span('Wrong'), span('Wrong')]),
    el('a', { id: 'new-tab-search' }, [], 'Create tab'),
  ])])]);
  const state = run(readSongsterrPage, doc);
  assert.equal(state.status, 'ready'); assert.equal(state.signedOut, true); assert.equal(state.searchReady, true);
  assert.deepEqual(state.results, [{ id: '564073', source: 'songsterr', title: 'Woodland Rites', artist: 'Green Lung', url: 'https://www.songsterr.com' + href }]);
});

function searchFixture(query = 'R U Mine?', { homepage = false, pending = false, input = query, empty = false, emptyQuery = query } = {}) {
  const field = el('input', { type: 'text', placeholder: 'Search over a million tabs', value: homepage ? '' : input });
  const list = el('div', { 'data-list': 'songs' }, [
    el('a', { href: '/a/wsa/arctic-monkeys-r-u-mine-tab-s90203' }, [span('R U Mine?'), span('Arctic Monkeys')]),
    ...(!homepage && !pending ? [el('a', { id: 'new-tab-search' }, [], 'Create tab')] : []),
  ]);
  const panel = el('main', { id: 'panel-search' }, [field, empty ? span(`No tabs found for “${emptyQuery}”`) : list]);
  const doc = page([panel, el('a', { href: '/a/wsa/metallica-master-of-puppets-tab-s455118' }, [span('Master of Puppets'), span('Metallica')])],
    'https://www.songsterr.com/' + (homepage ? '' : '?' + new URLSearchParams({ pattern: query })));
  return { doc, field, panel };
}

test('homepage and in-flight popular list are not completed searches', () => {
  for (const options of [{ homepage: true }, { pending: true }, { input: 'another query' }]) {
    const state = run(readSongsterrPage, searchFixture('R U Mine?', options).doc);
    assert.equal(state.searchReady, false);
    assert.equal(state.searchHome, Boolean(options.homepage));
  }
});

test('completed results belong to the rendered search list and keep punctuation', () => {
  const state = run(readSongsterrPage, searchFixture().doc);
  assert.equal(state.searchReady, true);
  assert.equal(state.searchInput, 'R U Mine?'); assert.equal(state.searchQuery, 'R U Mine?');
  assert.deepEqual(state.results.map(r => r.id), ['90203'], 'Ignore unrelated song links outside the results list.');
});

test('only an empty response explicitly naming this query is ready', () => {
  const correct = run(readSongsterrPage, searchFixture('No such tab?', { empty: true }).doc);
  assert.equal(correct.searchReady, true); assert.equal(correct.noResults, true); assert.deepEqual(correct.results, []);
  const stale = run(readSongsterrPage, searchFixture('No such tab?', { empty: true, emptyQuery: 'old query' }).doc);
  assert.equal(stale.searchReady, false); assert.equal(stale.noResults, false);
});

test('recovery uses the ordinary input with the complete query', () => {
  const { doc, field } = searchFixture('', { homepage: true });
  assert.deepEqual(run(actOnSongsterrPage, doc, { action: 'search', query: 'Metallica Am I Evil?' }), { ok: true });
  assert.equal(field.value, 'Metallica Am I Evil?');
  assert.deepEqual(field.events, ['input', 'change']);
});

test('search control must be unique and enabled; hidden duplicate is harmless', () => {
  const { doc, panel, field } = searchFixture('', { homepage: true });
  const duplicate = el('input', { placeholder: 'Search over a million tabs', value: '', hidden: true });
  duplicate.parentElement = panel; panel.children.push(duplicate);
  assert.equal(run(readSongsterrPage, doc).canSearch, true);
  duplicate.hidden = false;
  assert.equal(run(readSongsterrPage, doc).canSearch, false);
  assert.equal(run(actOnSongsterrPage, doc, { action: 'search', query: 'R U Mine?' }).ok, false);
  duplicate.hidden = true; field.disabled = true;
  assert.equal(run(readSongsterrPage, doc).canSearch, false);
  assert.equal(run(actOnSongsterrPage, doc, { action: 'search', query: 'R U Mine?' }).ok, false);
});

test('search action rejects invalid input and non-search pages', () => {
  for (const query of [null, '', 'x', 'x'.repeat(161)]) {
    assert.equal(run(actOnSongsterrPage, searchFixture().doc, { action: 'search', query }).ok, false);
  }
  const { doc } = searchFixture(); doc.URL = 'https://www.songsterr.com/a/wsa/arctic-monkeys-r-u-mine-tab-s90203';
  assert.equal(run(actOnSongsterrPage, doc, { action: 'search', query: 'R U Mine?' }).ok, false);
});

test('approval reader associates each explicit badge with its own revision and excludes newer unapproved rows', () => {
  const song = '/a/wsa/green-lung-woodland-rites-tab-s564073';
  const doc = page([el('ul', { id: 'revisions-list' }, [
    el('li', { id: 'r3000000' }, [span('9/17/2026'), span('Pending'), el('a', { href: song + '/r3000000' }, [], 'View')]),
    el('li', { id: 'r2585330' }, [span('7/31/2025'), span('APPROVED'), span('NF23'), el('a', { title: 'Show full tab', href: song + '/r2585330' }, [], 'Tab')]),
    el('li', { id: 'r626617' }, [el('a', { 'aria-label': 'View', href: song + '/r123...r626617' }, [span('10/8/2023'), span('APPROVED')])]),
  ])], 'https://www.songsterr.com' + song + '/r626617...r2585330');
  const state = run(readSongsterrPage, doc);
  assert.deepEqual(state.approvedRevisions, [
    { revisionId: '2585330', approval: 'approved', date: '7/31/2025' },
    { revisionId: '626617', approval: 'approved', date: '10/8/2023' },
  ]);
});

test('initial-revision history retains the song and approved full-tab identity', () => {
  const song = '/a/wsa/green-lung-evil-in-this-house-tab-s5197918';
  const row = el('li', { id: 'r6797098' }, [span('5/13/2026'), span('Approved'), span('Initial revision'),
    el('a', { href: '/a/wsa/green-lung-evil-in-this-house-solo-tab-s5197918t0/r6797098' }, [], 'Show full tab')]);
  const state = run(readSongsterrPage, page([el('ul', { id: 'revisions-list' }, [row])],
    'https://www.songsterr.com' + song + '/r...r6797098'));
  assert.equal(state.songId, '5197918');
  assert.equal(state.revisionId, '6797098');
  assert.equal(state.historyReady, true);
  assert.deepEqual(state.revisionRows, [{ revisionId: '6797098', approved: true, excluded: false, moderator: false }]);
});

test('initial comparison links identify older rows without supplying approval by themselves', () => {
  const song = '/a/wsa/example-tab-s42t2';
  const row = (id, badge, href) => el('li', { id: 'r' + id }, [span('1/1/2026'), span(badge), el('a', { href }, [], 'View')]);
  const state = run(readSongsterrPage, page([el('ul', { id: 'revisions-list' }, [
    row('101', 'Approved', song + '/r...r101'),
    row('102', 'Pending', song + '/r...r102'),
    row('103', 'Alternative', song + '/r...r103'),
    row('104', '', song + '/r...r104'),
    row('105', 'Approved', '/a/wsa/other-tab-s99/r...r105'),
    row('106', 'Approved', 'https://other.test' + song + '/r...r106'),
    row('107', 'Approved', song + '/r...r999'),
  ])], 'https://www.songsterr.com' + song + '/r101...r104'));
  assert.deepEqual(state.approvedRevisions, [{ revisionId: '101', approval: 'approved', date: '1/1/2026' }]);
  assert.deepEqual(state.revisionRows, [
    { revisionId: '101', approved: true, excluded: false, moderator: false },
    { revisionId: '102', approved: false, excluded: true, moderator: false },
    { revisionId: '103', approved: false, excluded: true, moderator: false },
    { revisionId: '104', approved: false, excluded: false, moderator: false },
  ]);
});

test('malformed revision paths cannot establish song or approval identity', () => {
  const song = '/a/wsa/example-tab-s42';
  for (const suffix of ['/r', '/r...r', '/r0', '/r...r0', '/r0...r101', '/r..r101', '/r...r101/extra']) {
    const row = el('li', { id: 'r101' }, [span('1/1/2026'), span('Approved'), el('a', { href: song + suffix }, [], 'View')]);
    const state = run(readSongsterrPage, page([el('ul', { id: 'revisions-list' }, [row])], 'https://www.songsterr.com' + song + suffix));
    assert.equal(state.songId, null, suffix);
    assert.equal(state.revisionId, null, suffix);
    assert.deepEqual(state.approvedRevisions, [], suffix);
  }
});

test('observed Ghost Rats revision toggle is ready and clickable despite its tooltip overriding the displayed date', () => {
  const toggle = el('button', { id: 'revisions-toggle-tab', title: 'Show revisions' }, [span('7/8/2026'), el('span', { 'data-visible': 'false', 'aria-hidden': 'true' }, [], 'new')]);
  const doc = page([toggle], 'https://www.songsterr.com/a/wsa/ghost-rats-tab-s441770');
  assert.equal(run(readSongsterrPage, doc).canOpenHistory, true);
  assert.equal(run(actOnSongsterrPage, doc, { action: 'history' }).ok, true);
  assert.equal(toggle.clicked, 1);
});

test('history reader distinguishes a moderator icon from a badge, pending label or author text', () => {
  const song = '/a/wsa/iron-maiden-fear-of-the-dark-tab-s492398';
  const row = (id, children) => el('li', { id: 'r' + id }, [span('6/22/2026'), ...children,
    el('a', { href: song + '/r' + id }, [], 'Show full tab')]);
  const doc = page([el('ul', { id: 'revisions-list' }, [
    row('7509113', [el('img', { alt: 'Moderator' })]),
    row('7509114', [span('Moderator')]),
    row('7509115', [span('On review'), el('img', { alt: 'Moderator' })]),
    row('7509116', [el('img', { alt: 'Moderator', hidden: true })]),
    row('7509117', [span('Alternative'), span('Approved')]),
  ])], 'https://www.songsterr.com' + song);
  assert.deepEqual(run(readSongsterrPage, doc).revisionRows, [
    { revisionId: '7509113', approved: false, excluded: false, moderator: true },
    { revisionId: '7509114', approved: false, excluded: false, moderator: false },
    { revisionId: '7509115', approved: false, excluded: true, moderator: true },
    { revisionId: '7509116', approved: false, excluded: false, moderator: false },
    { revisionId: '7509117', approved: true, excluded: true, moderator: false },
  ]);
});

test('observed Ghost Rats history keeps current and older approved rows separate from alternative and unapproved rows', () => {
  const song = '/a/wsa/ghost-rats-tab-s441770';
  const doc = page([el('ul', { id: 'revisions-list' }, [
    el('li', { id: 'r7788783' }, [el('div', { title: 'Moderator set this revision as default' }, [], 'Approved'), span('7/8/2026'),
      el('a', { title: 'Show full tab', 'aria-label': 'show 7788783 revision', href: song + '/r7788783' }, [], 'Tab')]),
    el('li', { id: 'r6634421' }, [el('a', { href: song + '/r5173121...r6634421', 'aria-label': 'View' }, [
      el('div', { title: 'Moderator set this revision as default' }, [], 'Approved'), span('5/4/2026'), el('a', { href: '/a/u/r12345' }, [], 'Author')])]),
    el('li', { id: 'r5173121' }, [span('Alternative'), span('4/1/2026'), el('a', { href: song + '/r123...r5173121' }, [], 'View')]),
    el('li', { id: 'r123' }, [span('Pending'), span('3/1/2026'), el('a', { href: song + '/r123' }, [], 'View')]),
  ])], 'https://www.songsterr.com' + song + '/r6634421...r7788783');
  const state = run(readSongsterrPage, doc);
  assert.equal(state.songId, '441770'); assert.equal(state.revisionId, '7788783'); assert.equal(state.historyReady, true);
  assert.deepEqual(state.approvedRevisions, [{ revisionId: '7788783', approval: 'approved', date: '7/8/2026' }, { revisionId: '6634421', approval: 'approved', date: '5/4/2026' }]);
});

test('empty history shell and URL-only tab are not considered ready', () => {
  const url = 'https://www.songsterr.com/a/wsa/ghost-rats-tab-s441770/r6634421...r7788783';
  const empty = run(readSongsterrPage, page([el('ul', { id: 'revisions-list' }, [])], url));
  assert.equal(empty.historyVisible, true); assert.equal(empty.historyReady, false);
  const shell = run(readSongsterrPage, page([], url)); assert.equal(shell.songId, '441770'); assert.equal(shell.canOpenHistory, false); assert.equal(shell.historyReady, false);
});

test('a pinned tab becomes ready only after its track mixer loads, even when no linked audio is rendered', () => {
  const mixer = el('button', { id: 'control-mixer', title: 'Show tracks ((T))' }, [], 'Loading'); mixer.disabled = true;
  const doc = page([el('button', { id: 'revisions-toggle-tab', title: 'Show revisions' }, [], '7/8/2026'),
    el('h1', { id: 'song-ttl' }, [], 'Rats'), el('span', { id: 'song-artist' }, [], 'Ghost'), mixer],
  'https://www.songsterr.com/a/wsa/ghost-rats-tab-s441770/r7788783');
  const loading = run(readSongsterrPage, doc);
  assert.equal(loading.canOpenHistory, true); assert.equal(loading.revisionId, '7788783'); assert.equal(loading.tabReady, false);
  mixer.disabled = false; mixer.own = 'Distortion Guitar Fire - Lead';
  const ready = run(readSongsterrPage, doc); assert.equal(ready.tabReady, true); assert.deepEqual(ready.audio, []);
});

test('CSS-hidden sticky copies before the real toolbar do not block pinned readiness', () => {
  const duplicates = (tag, id, text, options = {}) => {
    const sticky = el(tag, { id, ...options }, [], text); sticky.rectCount = 0;
    const actual = el(tag, { id, ...options }, [], text);
    return [sticky, actual];
  };
  const mixer = duplicates('button', 'control-mixer', 'Distortion Guitar Fire - Lead', { title: 'Show tracks ((T))' });
  const doc = page([
    ...duplicates('h1', 'song-ttl', 'Rats'), ...duplicates('span', 'song-artist', 'Ghost'),
    ...duplicates('button', 'revisions-toggle-tab', '7/8/2026', { title: 'Show revisions' }), ...mixer,
  ], 'https://www.songsterr.com/a/wsa/ghost-rats-tab-s441770/r7788783');
  const state = run(readSongsterrPage, doc);
  assert.equal(doc.querySelector('#control-mixer'), mixer[0]); assert.equal(mixer[0].getClientRects().length, 0);
  assert.equal(state.canOpenHistory, true); assert.equal(state.tabReady, true);
  mixer[1].disabled = true; assert.equal(run(readSongsterrPage, doc).tabReady, false, 'a hidden enabled copy cannot replace a still-loading visible control');
});

test('enabled visible duplicate controls win over hidden or disabled first matches for history, editor and exports', () => {
  for (const [id, label, action] of [
    ['revisions-toggle-tab', 'Show revisions', 'history'], ['control-editor', 'Editor', 'editor'],
    ['control-export', 'Download', 'exportMenu'], ['control-export-gp', 'Guitar Pro', 'export'],
  ]) {
    const hidden = el('button', { id, title: label }, [], label); hidden.rectCount = 0;
    const disabled = el('button', { id, title: label }, [], label); disabled.disabled = true;
    const accessibleDisabled = el('button', { id, title: label, 'aria-disabled': 'true' }, [], label);
    const actual = el('button', { id, title: label }, [], label);
    const doc = page([hidden, disabled, accessibleDisabled, actual]);
    assert.equal(run(actOnSongsterrPage, doc, { action }).ok, true, action);
    assert.equal(actual.clicked, 1); assert.equal(hidden.clicked + disabled.clicked + accessibleDisabled.clicked, 0);
    if (action === 'export') assert.equal(run(readSongsterrPage, doc).canExport, true);
    if (action === 'history') assert.equal(run(readSongsterrPage, doc).canOpenHistory, true);
  }
});

test('approved rows require matching same-song revision links and never infer current revision from dates', () => {
  const song = '/a/wsa/ghost-rats-tab-s441770';
  const doc = page([el('ul', { id: 'revisions-list' }, [
    el('li', { id: 'r7788783' }, [span('Approved'), span('7/8/2026')]),
    el('li', { id: 'r6634421' }, [span('Approved'), span('5/4/2026'), el('a', { href: '/a/wsa/other-song-tab-s999/r6634421' }, [], 'Tab')]),
    el('li', { id: 'r5173121' }, [span('Approved'), span('4/1/2026'), el('a', { href: song + '/r111' }, [], 'Tab')]),
  ])], 'https://www.songsterr.com' + song + '/r6634421...r7788783');
  assert.deepEqual(run(readSongsterrPage, doc).approvedRevisions, []);
});

test('hidden approval labels cannot establish approval of an unapproved revision', () => {
  const song = '/a/wsa/ghost-rats-tab-s441770';
  const doc = page([el('ul', { id: 'revisions-list' }, [el('li', { id: 'r123' }, [span('9/17/2026'), el('span', { hidden: true }, [], 'APPROVED'), span('Pending'), el('a', { href: song + '/r123' }, [], 'Tab')])])], 'https://www.songsterr.com' + song + '/r123');
  assert.deepEqual(run(readSongsterrPage, doc).approvedRevisions, []);
});

test('sign-in and copy-account prompts are authoritative, while missing controls are not a login failure', () => {
  assert.equal(run(readSongsterrPage, page([])).status, 'ready');
  assert.equal(run(readSongsterrPage, page([el('input', { type: 'password' })])).status, 'needs_login');
  assert.equal(run(readSongsterrPage, page([span('Please sign up for free to create and edit a copy of the current tab')])).status, 'needs_login');
});

test('only observed editor/copy/create/export actions are supported; publish and arbitrary selectors are impossible', () => {
  const editor = el('button', { id: 'control-editor' }, [], 'Editor');
  const copy = el('button', {}, [], 'Make a copy'), create = el('button', {}, [], 'Create'), publish = el('button', {}, [], 'Publish');
  const exportControl = el('button', { id: 'control-export-gp' }, [], 'Guitar Pro');
  const doc = page([editor, copy, create, publish, exportControl]);
  for (const action of ['editor', 'copy', 'create', 'export']) assert.equal(run(actOnSongsterrPage, doc, { action }).ok, true);
  assert.equal(run(actOnSongsterrPage, doc, { action: 'publish', selector: 'button' }).ok, false);
  assert.equal(publish.clicked, 0); assert.equal(create.clicked, 1); assert.equal(exportControl.clicked, 1);
});

test('only the scoped current-song player supplies audio; unrelated YouTube links and mismatched players are ignored', () => {
  const frame = (video, song = '441770', host = 'www.youtube.com') => el('iframe', { id: `youtube-player-${video}-${song}`, src: `https://${host}/embed/${video}` });
  const doc = page([
    el('a', { href: 'https://www.youtube.com/watch?v=zzzzzzzzzzz' }, [], 'A comment link'),
    frame('zzzzzzzzzzz'),
    el('div', { id: 'youtube-video-container' }, [frame('C_ijc7A5oAc'), frame('abcdefghijk', '999'), frame('abcdefghijk', '441770', 'evil.test'),
      el('iframe', { id: 'youtube-player-abcdefghijk-441770', src: 'https://www.youtube.com/embed/zzzzzzzzzzz' })]),
  ], 'https://www.songsterr.com/a/wsa/ghost-rats-tab-s441770/r7788783');
  assert.deepEqual(run(readSongsterrPage, doc).audio, ['https://www.youtube.com/embed/C_ijc7A5oAc']);
});

test('Original selection is read from the radio checked property, and its lazy player is optional', () => {
  const original = el('input', { type: 'radio', value: 'original', readonly: '' }); original.checked = true; original.rectCount = 0;
  const synth = el('input', { type: 'radio', value: 'synth', readonly: '' }); synth.checked = false;
  const mix = el('select', { id: 'video-select' }, [el('option', { value: 'main' }, [], 'Full mix'), el('option', { value: 'backing' }, [], 'Backing track')]); mix.value = 'main';
  const play = el('button', { id: 'control-play', 'data-can-play': 'true', 'aria-pressed': 'false', title: 'Play' });
  const doc = page([el('div', { id: 'control-source', role: 'radiogroup' }, [el('label', {}, [original], 'Original'), el('label', {}, [synth], 'Synth')]), mix, play],
    'https://www.songsterr.com/a/wsa/ghost-rats-tab-s441770/r7788783');
  const ready = run(readSongsterrPage, doc);
  assert.equal(original.getAttribute('checked'), null); assert.equal(ready.originalSelected, true); assert.equal(ready.originalAvailable, true);
  assert.equal(ready.audioMix, 'main'); assert.equal(ready.fullMixAvailable, true); assert.equal(ready.canPlay, true); assert.equal(ready.playing, false); assert.deepEqual(ready.audio, []);
  original.checked = false; synth.checked = true; play.attrs['aria-pressed'] = 'true';
  const synthState = run(readSongsterrPage, doc); assert.equal(synthState.originalSelected, false); assert.equal(synthState.playing, true);
});

test('audio diagnostics distinguish an enabled Play button from its missing or explicitly false eligibility attribute', () => {
  const play = el('button', { id: 'control-play', 'aria-pressed': 'false' });
  const doc = page([play], 'https://www.songsterr.com/a/wsa/ghost-rats-tab-s441770/r7788783');
  for (const [attribute, expected] of [[undefined, 'missing'], ['false', 'false'], ['true', 'true'], ['unexpected page text', 'other']]) {
    if (attribute === undefined) delete play.attrs['data-can-play']; else play.attrs['data-can-play'] = attribute;
    const read = run(readSongsterrPage, doc);
    assert.equal(read.playEnabled, true); assert.equal(read.playEligibility, expected); assert.equal(read.canPlay, expected === 'true');
  }
  play.disabled = true; assert.equal(run(readSongsterrPage, doc).playEnabled, false);
});

test('audio controls select Original and Full mix, then play and pause idempotently only on the expected revision', () => {
  const original = el('input', { type: 'radio', value: 'original', readonly: '' }); original.checked = false;
  const label = el('label', {}, [original], 'Original');
  const mix = el('select', { id: 'video-select' }, [el('option', { value: 'main' }, [], 'Full mix'), el('option', { value: 'solo' }, [], 'Solo')]); mix.value = 'solo';
  const hidden = el('button', { id: 'control-play', 'data-can-play': 'true', 'aria-pressed': 'false' }); hidden.rectCount = 0;
  const play = el('button', { id: 'control-play', 'data-can-play': 'true', 'aria-pressed': 'false' });
  const doc = page([el('div', { id: 'control-source' }, [label]), mix, hidden, play], 'https://www.songsterr.com/a/wsa/ghost-rats-tab-s441770/r7788783');
  const act = (action, revisionId = '7788783') => run(actOnSongsterrPage, doc, { action, songId: '441770', revisionId });
  assert.equal(act('play').ok, false); assert.equal(act('selectOriginal').ok, true); assert.equal(label.clicked, 1); assert.equal(original.clicked, 0);
  original.checked = true; assert.equal(act('selectOriginal').changed, false); assert.equal(label.clicked, 1);
  assert.equal(act('selectFullMix').ok, true); assert.equal(mix.value, 'main'); assert.deepEqual(mix.events, ['input', 'change']);
  assert.equal(act('selectFullMix').changed, false); assert.equal(mix.events.length, 2);
  assert.equal(act('play').ok, true); assert.equal(play.clicked, 1); assert.equal(hidden.clicked, 0);
  play.attrs['aria-pressed'] = 'true'; assert.equal(act('play').changed, false); assert.equal(play.clicked, 1);
  assert.equal(act('pause', '999').reason, 'revision_changed'); assert.equal(play.clicked, 1);
  assert.equal(act('pause').ok, true); assert.equal(play.clicked, 2);
  play.attrs['aria-pressed'] = 'false'; assert.equal(act('pause').changed, false); assert.equal(play.clicked, 2);
});

test('Download opens the format menu before its hidden Guitar Pro export control is clicked', () => {
  const download = el('button', { id: 'control-export' }, [], 'Download');
  const guitarPro = el('button', { id: 'control-export-gp', hidden: true }, [], 'Guitar Pro');
  const doc = page([download, guitarPro]);
  assert.equal(run(readSongsterrPage, doc).canExport, false);
  assert.equal(run(actOnSongsterrPage, doc, { action: 'export' }).ok, false); assert.equal(guitarPro.clicked, 0);
  assert.equal(run(actOnSongsterrPage, doc, { action: 'exportMenu' }).ok, true); assert.equal(download.clicked, 1);
  guitarPro.hidden = false;
  assert.equal(run(readSongsterrPage, doc).canExport, true);
  assert.equal(run(actOnSongsterrPage, doc, { action: 'export' }).ok, true); assert.equal(guitarPro.clicked, 1);
});

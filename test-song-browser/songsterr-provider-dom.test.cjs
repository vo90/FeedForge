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
    this.hidden = attrs.hidden === true; for (const child of children) child.parentElement = this;
  }
  get textContent() { return [this.own, ...this.children.map((child) => child.textContent)].filter(Boolean).join(' '); }
  get innerText() { return [this.own, ...this.children.map((child) => child.innerText)].filter(Boolean).join('\n'); }
  getAttribute(name) { return this.attrs[name] ?? null; }
  getClientRects() { return this.hidden || this.rectCount === 0 ? [] : [{}]; }
  click() { this.clicked++; }
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
function run(fn, document, request) { return JSON.parse(JSON.stringify(vm.runInNewContext(`(${fn.toString()})(request)`, { document, location: { href: document.URL }, request, URL }))); }

test('rendered public search extracts separate titles/artists, deduplicates IDs and ignores unrelated links', () => {
  const href = '/a/wsa/green-lung-woodland-rites-tab-s564073';
  const doc = page([
    el('a', { href: '/signin' }, [], 'SIGN IN'),
    el('a', { href }, [el('span', { 'data-testid': 'song-title' }, [], 'Woodland Rites'), el('span', { 'data-testid': 'artist' }, [], 'Green Lung')]),
    el('a', { href }, [span('Woodland Rites'), span('Green Lung')]),
    el('a', { href: 'https://evil.test/a/wsa/copy-tab-s564073' }, [span('Wrong'), span('Wrong')]),
  ]);
  const state = run(readSongsterrPage, doc);
  assert.equal(state.status, 'ready'); assert.equal(state.signedOut, true); assert.equal(state.searchReady, true);
  assert.deepEqual(state.results, [{ id: '564073', source: 'songsterr', title: 'Woodland Rites', artist: 'Green Lung', url: 'https://www.songsterr.com' + href }]);
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

test('observed Ghost Rats revision toggle is ready and clickable despite its tooltip overriding the displayed date', () => {
  const toggle = el('button', { id: 'revisions-toggle-tab', title: 'Show revisions' }, [span('7/8/2026'), el('span', { 'data-visible': 'false', 'aria-hidden': 'true' }, [], 'new')]);
  const doc = page([toggle], 'https://www.songsterr.com/a/wsa/ghost-rats-tab-s441770');
  assert.equal(run(readSongsterrPage, doc).canOpenHistory, true);
  assert.equal(run(actOnSongsterrPage, doc, { action: 'history' }).ok, true);
  assert.equal(toggle.clicked, 1);
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

test('rendered player link is captured and no script or page state is needed', () => {
  const doc = page([el('iframe', { src: 'https://www.youtube-nocookie.com/embed/abcdefghijk' }), el('iframe', { src: 'https://evil.test/embed/abcdefghijk' })]);
  assert.deepEqual(run(readSongsterrPage, doc).audio, ['https://www.youtube-nocookie.com/embed/abcdefghijk']);
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

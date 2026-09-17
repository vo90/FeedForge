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
  getClientRects() { return this.hidden ? [] : [{}]; }
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
  const doc = page([el('section', { id: 'revisions-list' }, [
    el('article', { 'data-revision-id': '3000000' }, [span('9/17/2026'), span('Pending'), el('a', { href: song + '/r3000000' }, [], 'View')]),
    el('article', {}, [span('7/31/2025'), span('APPROVED'), span('NF23')]),
    el('article', {}, [span('10/8/2023'), span('APPROVED'), el('a', { href: song + '/r626617' }, [], 'View')]),
  ])], 'https://www.songsterr.com' + song + '/r626617...r2585330');
  const state = run(readSongsterrPage, doc);
  assert.deepEqual(state.approvedRevisions, [
    { revisionId: '2585330', approval: 'approved', date: '7/31/2025' },
    { revisionId: '626617', approval: 'approved', date: '10/8/2023' },
  ]);
});

test('hidden approval labels cannot establish approval of an unapproved revision', () => {
  const doc = page([el('section', { id: 'revisions-list' }, [el('article', { 'data-revision-id': '123' }, [span('9/17/2026'), el('span', { hidden: true }, [], 'APPROVED'), span('Pending')])])]);
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

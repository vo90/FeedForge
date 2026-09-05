'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const { readSearchPage, requestChartDownload, requestSearchPage } = require('../electron/song-browser/dom.cjs');

// A small DOM fixture, not a live site client. Element selection is limited to
// the browser primitives the page functions need; there is no network access.
class Element {
  constructor(tag, attrs = {}, children = [], ownText = '') {
    this.tagName = tag.toUpperCase(); this.attrs = attrs; this.children = children;
    this.ownText = ownText; this.style = {}; this.hidden = !!attrs.hidden;
    this.className = attrs.class || ''; this.clicked = 0;
    for (const child of children) child.parentElement = this;
  }
  get textContent() { return [this.ownText, ...this.children.map((child) => child.textContent)].join(' '); }
  getAttribute(name) { return Object.hasOwn(this.attrs, name) ? this.attrs[name] : null; }
  hasAttribute(name) { return Object.hasOwn(this.attrs, name); }
  getClientRects() { return this.hidden || this.style.display === 'none' ? [] : [{}]; }
  click() { this.clicked++; if (this.throwClick) throw new Error('fixture click failed'); }
  descendants() { return this.children.flatMap((child) => [child, ...child.descendants()]); }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  querySelectorAll(selector) {
    const match = (element, simple) => {
      if (simple.startsWith('#')) return element.getAttribute('id') === simple.slice(1);
      const m = simple.match(/^([a-z][a-z0-9]*)?(?:\[([^=\]]+)(?:="([^"]*)")?\])?$/i);
      if (!m) throw new Error('Unsupported fixture selector: ' + simple);
      return (!m[1] || element.tagName === m[1].toUpperCase()) && (!m[2] || (m[3] === undefined ? element.hasAttribute(m[2]) : element.getAttribute(m[2]) === m[3]));
    };
    const matches = (element, part) => {
      if (part.includes(' > ')) { const [parent, child] = part.split(' > '); return match(element, child) && !!element.parentElement && match(element.parentElement, parent); }
      if (/^[a-z]+ [a-z]+$/i.test(part)) { const [ancestor, leaf] = part.split(' '); if (!match(element, leaf)) return false; for (let p = element.parentElement; p; p = p.parentElement) if (match(p, ancestor)) return true; return false; }
      return match(element, part);
    };
    return this.descendants().filter((element) => selector.split(',').some((part) => matches(element, part.trim())));
  }
}
const el = (tag, attrs, children, text) => new Element(tag, attrs, children, text);
const td = (text) => el('td', {}, [], text);
const link = (href, title) => el('a', { href }, [], title);
const origin = 'https://ignition4.customsforge.com';
const columns = ['Download', 'Artist', 'Title', 'Album', 'Tuning', 'Creator', 'Added', 'Updated', 'Parts', 'Version', 'Year', 'Duration', 'Downloads'];
function row({ id = '6420', title = 'One', artist = 'Metallica', host = 'Google Drive', order = columns, recordHref } = {}) {
  const data = { Download: el('td', {}, [el('span', { 'data-bs-original-title': 'Hosted on ' + host })]), Artist: td(artist), Title: el('td', {}, [link(recordHref || '/cdlc/' + id, title)]), Album: td('...And Justice for All'), Tuning: td('E STANDARD'), Creator: td('Nacholede'), Added: td('2020-01-01'), Updated: td('2026-08-01'), Parts: el('td', {}, [el('i', { 'aria-label': 'Lead' }), el('i', { title: 'Bass' })]), Version: td('1'), Year: td('1988'), Duration: td('7:27'), Downloads: td('100,001'), '': td('') };
  return el('tr', {}, order.map((name) => data[name] || td('')));
}
function page(rows = [row()], { headers = columns, url = origin + '/', text = 'Showing 1 to 1 of 288 results', extras = [] } = {}) {
  const table = el('table', { id: 'cdlc-table' }, [el('thead', {}, [el('tr', {}, headers.map((h) => el('th', {}, [], h)))]), el('tbody', {}, rows)]);
  const body = el('body', {}, [table, ...extras], text);
  const doc = el('document', {}, [body]); doc.body = body; doc.URL = url; doc.title = 'CustomsForge';
  return doc;
}
function run(fn, doc, request) {
  // Serialization proves no hidden module closure is needed in Electron.
  const context = { document: doc, location: { href: doc.URL }, URL, request, Date };
  const value = vm.runInNewContext('(' + fn.toString() + ')(request)', context);
  return JSON.parse(JSON.stringify(value));
}
function downloadPage({ id = '6420', host = 'Google Drive', href, expires = Math.floor(Date.now() / 1000) + 600, platform = 'pc', extraLinks = [] } = {}) {
  const anchor = el('a', { href: href || `/user/collectedcdlcs/toggle/${id}?platform=${platform}&expires=${expires}&signature=fixture-signature`, 'data-bs-original-title': 'Hosted on ' + host }, [], 'Windows');
  const body = el('body', {}, [el('div', {}, [anchor]), ...extraLinks]);
  const doc = el('document', {}, [body]); doc.body = body; doc.URL = origin + '/cdlc/' + id; doc.title = 'CustomsForge';
  return { doc, anchor };
}

test('reads observed table metadata, icon parts and pagination without exporting signed links', () => {
  const doc = page([row()], { extras: [el('button', { 'aria-label': 'Next page' }), el('span', { 'aria-current': 'page' }, [], '2')] });
  const result = run(readSearchPage, doc);
  assert.equal(result.status, 'ready'); assert.equal(result.page, 2); assert.equal(result.total, 288); assert.equal(result.hasNext, true);
  assert.deepEqual(result.results[0], { id: '6420', title: 'One', artist: 'Metallica', album: '...And Justice for All', tuning: 'E STANDARD', creator: 'Nacholede', version: '1', parts: 'Lead, Bass', host: 'google-drive', supported: true, recordUrl: origin + '/cdlc/6420' });
  assert.doesNotMatch(JSON.stringify(result), /expires|signature|toggle/);
});

test('semantic columns survive reordering and added unknown columns', () => {
  const order = ['Album', 'Title', 'Rating', 'Creator', 'Artist', 'Parts', 'Version', 'Tuning', 'Download'];
  const result = run(readSearchPage, page([row({ order })], { headers: order }));
  assert.equal(result.status, 'ready'); assert.equal(result.results[0].artist, 'Metallica'); assert.equal(result.results[0].version, '1');
});

test('duplicate required headers and mismatched record IDs fail closed', () => {
  assert.equal(run(readSearchPage, page([row()], { headers: columns.map((h) => h === 'Album' ? 'Title' : h) })).status, 'layout_changed');
  const fixture = row(); fixture.children[3].children.push(link('/cdlc/999', 'Other chart'));
  assert.equal(run(readSearchPage, page([fixture])).status, 'layout_changed');
});

test('strict fallback recognizes only the observed 13-cell table with title in the right column', () => {
  assert.equal(run(readSearchPage, page([row()], { headers: [] })).status, 'ready');
  const fixture = row(); fixture.children.pop();
  assert.equal(run(readSearchPage, page([fixture], { headers: [] })).status, 'layout_changed');
});

test('record URLs cannot escape CustomsForge and Unicode labels remain text', () => {
  for (const recordHref of ['https://ignition4.customsforge.com.evil.test/cdlc/6420', 'javascript:alert(1)', 'https://user@ignition4.customsforge.com/cdlc/6420', 'https://evil.test/cdlc/6420']) {
    assert.equal(run(readSearchPage, page([row({ recordHref })])).status, 'layout_changed');
  }
  assert.equal(run(readSearchPage, page([row({ title: '<b>Öne & 二</b>' })])).results[0].title, '<b>Öne & 二</b>');
});

test('distinguishes explicit empty results from missing or partial markup', () => {
  const empty = el('tr', {}, [el('td', { colspan: '14' }, [], 'No matching records found')]);
  assert.deepEqual(run(readSearchPage, page([empty], { text: '' })), { status: 'ready', results: [], hasNext: false, page: null, total: 0 });
  assert.equal(run(readSearchPage, page([])).status, 'layout_changed');
  const doc = page(); doc.children[0].children = [];
  assert.equal(run(readSearchPage, doc).status, 'layout_changed');
});

test('a busy search table never publishes old rows or an empty placeholder', () => {
  for (const rows of [[row()], [el('tr', {}, [el('td', { colspan: '14' }, [], 'No matching records found')])]]) {
    const doc = page(rows); const table = doc.querySelector('#cdlc-table');
    table.attrs['aria-busy'] = 'true';
    assert.equal(run(readSearchPage, doc).status, 'layout_changed');
    delete table.attrs['aria-busy'];
    assert.equal(run(readSearchPage, doc).status, 'ready');
  }
});

test('unsupported and conflicting host hints do not become downloadable results', () => {
  const mega = run(readSearchPage, page([row({ host: 'MEGA' })])).results[0];
  assert.equal(mega.host, 'mega'); assert.equal(mega.supported, false);
  const fixture = row(); fixture.children[0].children.push(el('i', { title: 'Hosted on Dropbox' }));
  assert.equal(run(readSearchPage, page([fixture])).results[0].host, 'unknown');
});

test('observed domain host hints work in table and Windows download buttons', () => {
  const names = [['drive.google.com', 'google-drive', true], ['dropbox.com', 'dropbox', true], ['mediafire.com', 'mediafire', true], ['1drv.ms', 'onedrive', false], ['mega.nz', 'mega', false], ['drive.google.com.evil.test', 'unknown', false], ['dropbox.com.evil.test', 'unknown', false]];
  for (const [name, host, supported] of names) {
    const fixture = row({ host: name });
    fixture.children[0].children[0].attrs['data-bs-original-title'] = 'Download Windows - Hosted on ' + name;
    const result = run(readSearchPage, page([fixture])).results[0];
    assert.equal(result.host, host, name); assert.equal(result.supported, supported, name);
    const { doc, anchor } = downloadPage({ host: name });
    anchor.attrs['data-bs-original-title'] = 'Download Windows - Hosted on ' + name;
    const download = run(requestChartDownload, doc, { id: '6420' });
    assert.equal(download.host, host, name); assert.equal(download.status, supported ? 'clicked' : 'unsupported', name);
  }
});

test('disabled pagination never advertises a next page', () => {
  const doc = page([row()], { extras: [el('li', { class: 'page-item disabled' }, [el('a', { 'aria-label': 'Next page' })])] });
  assert.equal(run(readSearchPage, doc).hasNext, false);
});

test('login and security interstitials are recognized without solving them', () => {
  const login = page([], { url: 'https://customsforge.com/oauth/authorize', text: 'Sign in', extras: [el('input', { type: 'password' })] });
  assert.equal(run(readSearchPage, login).status, 'login_required');
  assert.equal(run(requestChartDownload, login, { id: '6420' }).status, 'login_required');
  const challenge = page(); challenge.title = 'Just a moment...';
  assert.equal(run(readSearchPage, challenge).status, 'challenge');
  assert.equal(run(requestChartDownload, challenge, { id: '6420' }).status, 'challenge');
  const ordinary = page([row()], { extras: [el('iframe', { src: 'https://example.test/embed' })] });
  assert.equal(run(readSearchPage, ordinary).status, 'ready');
});

test('observed signed-out Ignition landing page asks for login without a password field', () => {
  const doc = page([], { text: 'Custom songs for Rocksmith 2014', extras: [
    el('h1', {}, [], 'Custom songs for Rocksmith 2014'),
    el('a', { href: '/login' }, [], 'Login to CustomsForge'),
    el('button', {}, [], 'Sign in')
  ] });
  doc.body.children = doc.body.children.filter((node) => node.getAttribute('id') !== 'cdlc-table');
  assert.equal(run(readSearchPage, doc).status, 'login_required');
  doc.body.children = [el('button', {}, [], 'Sign in')];
  assert.equal(run(readSearchPage, doc).status, 'login_required');
  doc.body.ownText = 'A page still rendering';
  assert.equal(run(readSearchPage, doc).status, 'layout_changed', 'an unrelated sign-in control is inconclusive');
  doc.body.children = [el('button', { hidden: true }, [], 'Login to CustomsForge')];
  assert.equal(run(readSearchPage, doc).status, 'layout_changed', 'a hidden login template is inconclusive');
  const signedIn = page([row()], { extras: [el('a', {}, [], 'Login to CustomsForge')] });
  assert.equal(run(readSearchPage, signedIn).status, 'ready', 'the observed search table takes precedence over a lingering login label');
});

test('clicks one current Windows button and never returns its signed URL', () => {
  const { doc, anchor } = downloadPage();
  assert.deepEqual(run(requestChartDownload, doc, { id: '6420' }), { status: 'clicked', host: 'google-drive' });
  assert.equal(anchor.clicked, 1);
  assert.equal(run(requestChartDownload, doc, { id: '6420' }).status, 'already_clicked');
  assert.equal(anchor.clicked, 1);
  anchor.attrs.href = '/user/collectedcdlcs/toggle/6420?platform=pc&expires=1&signature=fixture';
  assert.equal(run(requestChartDownload, doc, { id: '6420' }).status, 'already_clicked');
});

test('does not click expired or nearly expired URLs', () => {
  for (const offset of [-1, 5]) {
    const { doc, anchor } = downloadPage({ expires: Math.floor(Date.now() / 1000) + offset });
    assert.equal(run(requestChartDownload, doc, { id: '6420' }).status, 'expired'); assert.equal(anchor.clicked, 0);
  }
});

test('rejects wrong origins, IDs, ambiguous parameters, missing signatures and Mac links', () => {
  const tail = '?platform=pc&expires=9999999999&signature=fixture';
  const urls = ['https://evil.test/user/collectedcdlcs/toggle/6420' + tail, 'https://ignition4.customsforge.com.evil.test/user/collectedcdlcs/toggle/6420' + tail, '/user/collectedcdlcs/toggle/999' + tail, '/user/collectedcdlcs/toggle/6420' + tail + '&platform=mac', '/user/collectedcdlcs/toggle/6420' + tail + '&expires=1', '/user/collectedcdlcs/toggle/6420?platform=pc&expires=9999999999', '/user/collectedcdlcs/toggle/6420?platform=mac&expires=9999999999&signature=fixture'];
  for (const href of urls) { const { doc, anchor } = downloadPage({ href }); assert.equal(run(requestChartDownload, doc, { id: '6420' }).status, 'layout_changed', href); assert.equal(anchor.clicked, 0); }
});

test('rejects malformed selection and unsupported hosts without clicking', () => {
  const { doc, anchor } = downloadPage({ host: 'MEGA' });
  assert.equal(run(requestChartDownload, doc, { id: '../6420' }).status, 'invalid_request');
  assert.equal(run(requestChartDownload, doc, { id: '6420' }).status, 'unsupported'); assert.equal(anchor.clicked, 0);
  doc.URL = origin + '/cdlc/999';
  assert.equal(run(requestChartDownload, doc, { id: '6420' }).status, 'layout_changed');
});

test('an ambiguous click exception cannot be retried in the same document', () => {
  const { doc, anchor } = downloadPage({ host: 'Dropbox' }); anchor.throwClick = true;
  assert.equal(run(requestChartDownload, doc, { id: '6420' }).status, 'click_failed');
  assert.equal(run(requestChartDownload, doc, { id: '6420' }).status, 'already_clicked');
  assert.equal(anchor.clicked, 1);
});

test('reads the live Go to page input value', () => {
  const input = el('input', { 'aria-label': 'Go to page', value: '1' }); input.value = '3';
  assert.equal(run(readSearchPage, page([row()], { extras: [input] })).page, 3);
});

test('pagination uses observed buttons and clicks one visible match', () => {
  const hidden = el('button', { 'aria-label': 'Next page', hidden: true });
  const next = el('button', { title: 'Next page' });
  const duplicate = el('button', { 'aria-label': 'Next page' });
  const previous = el('button', { 'aria-label': 'Previous page' });
  const doc = page([row()], { extras: [hidden, next, duplicate, previous] });
  assert.equal(run(readSearchPage, page([row()], { extras: [next] })).hasNext, true);
  assert.deepEqual(run(requestSearchPage, doc, { direction: 'next' }), { status: 'clicked' });
  assert.equal(hidden.clicked, 0); assert.equal(next.clicked, 1); assert.equal(duplicate.clicked, 0);
  assert.equal(run(requestSearchPage, doc, { direction: 'previous' }).status, 'clicked'); assert.equal(previous.clicked, 1);
});

test('pagination rejects disabled and external controls, invalid directions and wrong pages', () => {
  const disabled = el('button', { 'aria-label': 'Next page', disabled: '' });
  const external = el('a', { 'aria-label': 'Next page', href: 'https://evil.test' });
  const doc = page([row()], { extras: [disabled, external] });
  assert.equal(run(requestSearchPage, doc, { direction: 'next' }).status, 'layout_changed');
  assert.equal(run(requestSearchPage, doc, { direction: 'sideways' }).status, 'layout_changed');
  assert.equal(disabled.clicked, 0); assert.equal(external.clicked, 0);
  doc.URL = 'https://evil.test';
  assert.equal(run(requestSearchPage, doc, { direction: 'next' }).status, 'layout_changed');
});

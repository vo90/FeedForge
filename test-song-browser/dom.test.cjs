'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const { readSearchPage, requestChartDownload, requestSearchPage, requestSearchSort, prepareSearchUpdates } = require('../electron/song-browser/dom.cjs');

// A small DOM fixture, not a live site client. Element selection is limited to
// the browser primitives the page functions need; there is no network access.
class Element {
  constructor(tag, attrs = {}, children = [], ownText = '') {
    this.nodeType = 1; this.tagName = tag.toUpperCase(); this.attrs = attrs; this.children = children;
    this.ownText = ownText; this.style = {}; this.hidden = !!attrs.hidden;
    this.className = attrs.class || ''; this.clicked = 0;
    for (const child of children) child.parentElement = this;
  }
  get textContent() { return [this.ownText, ...this.children.map((child) => child.textContent)].join(' '); }
  get childNodes() { return [...(this.ownText ? [{ nodeType: 3, textContent: this.ownText }] : []), ...this.children]; }
  getAttribute(name) { return Object.hasOwn(this.attrs, name) ? this.attrs[name] : null; }
  hasAttribute(name) { return Object.hasOwn(this.attrs, name); }
  getClientRects() { return this.hidden || this.style.display === 'none' ? [] : [{}]; }
  click() { this.clicked++; if (this.throwClick) throw new Error('fixture click failed'); }
  descendants() { return this.children.flatMap((child) => [child, ...child.descendants()]); }
  contains(node) { return this === node || this.descendants().includes(node); }
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
// Observed Ignition header shape: textContent includes the arrow even when
// Alpine hides its aria-hidden indicator with display:none. Keep the arrow in
// the fixture so both the metadata reader and sort action exercise that case.
function decorateHeading(heading, { label = heading.ownText, field = label.toLowerCase(), direction = null } = {}) {
  const indicator = el('span', { class: 'header-sort-indicator', 'aria-hidden': 'true' }, [], direction === 'asc' ? '▲' : '▼');
  if (!direction) indicator.style.display = 'none';
  const button = el('button', { type: 'button', class: 'header-sort-button' }, [
    el('i', { class: 'header-icon', 'aria-hidden': 'true' }),
    el('span', { class: 'header-text' }, [], label), indicator,
  ]);
  const content = el('div', { class: 'header-content' }, [
    el('i', { class: 'header-drag-grip', title: 'Drag to reorder', 'aria-hidden': 'true' }), button,
  ]);
  heading.ownText = ''; heading.children = [content]; content.parentElement = heading;
  heading.attrs['data-column-key'] = field;
  heading.attrs['aria-sort'] = direction === 'asc' ? 'ascending' : direction === 'desc' ? 'descending' : 'none';
  return { button, indicator, content };
}
function run(fn, doc, request, globals = {}) {
  // Serialization proves no hidden module closure is needed in Electron.
  const context = { document: doc, location: { href: doc.URL, origin: new URL(doc.URL).origin }, URL, request, Date, ...globals };
  const value = vm.runInNewContext('(' + fn.toString() + ')(request)', context);
  return JSON.parse(JSON.stringify(value));
}
function searchLifecycle() {
  const next = el('button', { 'aria-label': 'Next page' });
  const input = el('input', { 'aria-label': 'Go to page', value: '1' });
  const doc = page([row()], { extras: [next, input] });
  const table = doc.querySelector('#cdlc-table');
  const container = el('section', { 'data-cdlc-table': '' }, [table]);
  doc.body.children[0] = container; container.parentElement = doc.body;
  const heading = doc.querySelectorAll('thead th')[2];
  const { button } = decorateHeading(heading);
  const hooks = new Map();
  const Livewire = { hook(name, callback) { const list = hooks.get(name) || []; list.push(callback); hooks.set(name, list); } };
  const component = { el: container };
  const emit = (name, event) => { for (const callback of hooks.get(name) || []) callback(event); };
  const commit = (target = component) => {
    const callbacks = {};
    emit('commit', { component: target, succeed(callback) { callbacks.succeed = callback; }, fail(callback) { callbacks.fail = callback; } });
    return callbacks;
  };
  return { doc, table, heading, button, next, input, hooks, Livewire, component, emit, commit };
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
  assert.deepEqual(result.results[0], { id: '6420', title: 'One', artist: 'Metallica', album: '...And Justice for All', tuning: 'E STANDARD', creator: 'Nacholede', version: '1', parts: 'Lead, Bass', arrangements: ['lead', 'bass'], downloads: 100001, added: '2020-01-01', updated: '2026-08-01', year: 1988, durationSeconds: 447, reported: null, abandoned: null, host: 'google-drive', supported: true, recordUrl: origin + '/cdlc/6420' });
  assert.doesNotMatch(JSON.stringify(result), /expires|signature|toggle/);
});

test('only rendered arrangement badges count, even when every row contains hidden LRB text and hints', () => {
  const codes = { lead: 'L', rhythm: 'R', bass: 'B' };
  const make = (wanted) => {
    const fixture = row();
    const cell = fixture.children[8]; cell.children = []; cell.attrs.title = 'Lead Rhythm Bass';
    for (const [part, code] of Object.entries(codes)) {
      const badge = el('span', { class: 'badge', title: part }, [], code);
      const wrapper = el('span', { title: part }, [badge]);
      if (!wanted.includes(part)) {
        if (part === 'lead') wrapper.style.visibility = 'hidden';
        else if (part === 'rhythm') wrapper.attrs.class = 'fixture-hidden';
        else badge.attrs['aria-hidden'] = 'true';
      }
      cell.children.push(wrapper); wrapper.parentElement = cell;
    }
    return fixture;
  };
  const getComputedStyle = (node) => node.attrs.class === 'fixture-hidden' ? { display: 'none' } : node.style;
  for (const wanted of [['lead'], ['rhythm'], ['bass'], ['lead', 'bass'], []]) {
    const fixture = make(wanted);
    assert.match(fixture.children[8].textContent, /L\s+R\s+B/);
    const result = run(readSearchPage, page([fixture]), undefined, { getComputedStyle }).results[0];
    assert.deepEqual(result.arrangements, wanted);
    assert.equal(result.parts, wanted.map((part) => part[0].toUpperCase() + part.slice(1)).join(', '));
  }
});

test('visible parent tooltips cannot reintroduce hidden arrangement descendants', () => {
  const fixture = row(); const cell = fixture.children[8];
  cell.children = [
    el('span', { title: 'Lead' }, [el('span', { hidden: true, title: 'Lead' }, [], 'L')]),
    el('span', { title: 'Rhythm' }, [el('span', { class: 'fixture-hidden', title: 'Rhythm' }, [], 'R')]),
    el('span', { title: 'Bass' }, [], 'B'),
  ];
  for (const child of cell.children) child.parentElement = cell;
  const result = run(readSearchPage, page([fixture]), undefined, {
    getComputedStyle: (node) => node.attrs.class === 'fixture-hidden' ? { display: 'none' } : node.style,
  }).results[0];
  assert.deepEqual(result.arrangements, ['bass']); assert.equal(result.parts, 'Bass');
});

test('rendered plain arrangement text and compact labels normalize without raw hidden fallback', () => {
  for (const [label, expected] of [['Lead, Rhythm, Bass, Vocals', ['lead', 'rhythm', 'bass']], ['LRB', ['lead', 'rhythm', 'bass']], ['B', ['bass']], ['Drums', []]]) {
    const fixture = row(); const cell = fixture.children[8]; cell.children = []; cell.ownText = label;
    assert.deepEqual(run(readSearchPage, page([fixture])).results[0].arrangements, expected);
  }
  for (const hiddenBy of ['hidden', 'display', 'visibility', 'contentVisibility', 'opacity']) {
    const fixture = row(); const cell = fixture.children[8]; cell.ownText = 'Lead Rhythm Bass';
    if (hiddenBy === 'hidden') cell.hidden = true;
    else cell.style[hiddenBy] = hiddenBy === 'display' ? 'none' : hiddenBy === 'opacity' ? '0' : 'hidden';
    const result = run(readSearchPage, page([fixture])).results[0];
    assert.deepEqual(result.arrangements, []); assert.equal(result.parts, '');
  }
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

test('official DLC album badges and explicit labels are ODLC, never download hosts', () => {
  const markers = [
    el('span', { class: 'badge bg-primary' }, [], 'OFFICIAL DLC'),
    el('span', { class: 'badge' }, [], 'ODLC'),
    el('i', { 'data-bs-original-title': 'Official DLC' }),
    el('span', { class: 'official-dlc' }, [], 'Official'),
  ];
  for (const marker of markers) {
    const fixture = row({ host: 'Dropbox' });
    fixture.children[3].children.push(marker); marker.parentElement = fixture.children[3];
    const result = run(readSearchPage, page([fixture])).results[0];
    assert.equal(result.host, 'odlc');
    assert.equal(result.supported, false, 'an official badge overrides a lingering host hint');
  }
});

test('ordinary title/creator words, unsupported hosts and hidden badges do not imply ODLC', () => {
  const fixture = row({ title: 'Official DLC', host: 'Unrecognized File Host' });
  fixture.children[2].children[0].attrs.title = 'Official DLC';
  fixture.children[5].ownText = 'Ubisoft';
  const hiddenBadge = el('span', { class: 'badge', hidden: true }, [], 'OFFICIAL DLC');
  fixture.children[3].children.push(hiddenBadge); hiddenBadge.parentElement = fixture.children[3];
  const result = run(readSearchPage, page([fixture])).results[0];
  assert.equal(result.title, 'Official DLC');
  assert.equal(result.host, 'unknown'); assert.equal(result.supported, false);
  const custom = row({ title: 'My ODLC Cover', host: 'Dropbox' });
  custom.children[5].ownText = 'Ubisoft';
  assert.equal(run(readSearchPage, page([custom])).results[0].host, 'dropbox');
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

test('metadata represents missing counts, dates and status as unknown rather than zero', () => {
  const fixture = row();
  fixture.children[6].ownText = 'Yesterday';
  fixture.children[7].ownText = '2026-02-31';
  fixture.children[10].ownText = '?';
  fixture.children[11].ownText = '3:77';
  fixture.children[12].ownText = '1.2K';
  const result = run(readSearchPage, page([fixture])).results[0];
  for (const key of ['added', 'updated', 'year', 'durationSeconds', 'downloads', 'reported', 'abandoned']) assert.equal(result[key], null, key);
  fixture.children[6].children.push(el('time', { datetime: '2026-09-01T12:00:00Z' }, [], 'Yesterday'));
  fixture.children[12].ownText = '0';
  fixture.attrs.title = 'This CDLC has been reported.';
  const observed = run(readSearchPage, page([fixture])).results[0];
  assert.equal(observed.added, '2026-09-01');
  assert.equal(observed.downloads, 0);
  assert.equal(observed.reported, true);
  assert.equal(observed.abandoned, null);
});

test('sort metadata and commands use the same observed whole-table control', () => {
  const doc = page();
  const heading = doc.querySelectorAll('thead th')[2];
  heading.ownText = '';
  const button = el('button', {}, [], 'Title');
  heading.children = [button]; button.parentElement = heading;
  heading.attrs['aria-sort'] = 'ascending';
  assert.deepEqual(run(readSearchPage, doc).sort, { field: 'title', direction: 'asc' });
  assert.deepEqual(run(requestSearchSort, doc, { field: 'title', direction: 'asc' }), { status: 'applied', sort: { field: 'title', direction: 'asc' } });
  assert.equal(button.clicked, 0);
  assert.deepEqual(run(requestSearchSort, doc, { field: 'title', direction: 'desc' }), { status: 'clicked', sort: { field: 'title', direction: 'asc' } });
  assert.equal(button.clicked, 1);
  heading.attrs['aria-sort'] = 'descending';
  assert.deepEqual(run(readSearchPage, doc).sort, { field: 'title', direction: 'desc' });
  assert.equal(run(requestSearchSort, doc, { field: 'title', direction: 'desc' }).status, 'applied');
  assert.equal(button.clicked, 1, 'verified order must not toggle again');
});

test('real Ignition headers retain hidden sort arrows without blocking search', () => {
  const doc = page();
  const expected = run(readSearchPage, doc).results;
  const sortable = new Set(['Artist', 'Title', 'Album', 'Tuning', 'Creator', 'Added', 'Updated', 'Year', 'Duration', 'Downloads']);
  const decorated = new Map();
  for (const heading of doc.querySelectorAll('thead th')) {
    if (sortable.has(heading.ownText)) decorated.set(heading.ownText, decorateHeading(heading));
  }
  const title = decorated.get('Title');
  assert.equal(title.indicator.style.display, 'none');
  assert.match(doc.querySelectorAll('thead th')[2].textContent, /Title\s+▼/);
  const result = run(readSearchPage, doc);
  assert.equal(result.status, 'ready'); assert.deepEqual(result.results, expected);
  assert.equal(result.sort, undefined);
  assert.deepEqual(run(requestSearchSort, doc, { field: 'title', direction: 'asc' }), { status: 'clicked', sort: null });
  assert.equal(title.button.clicked, 1);
  for (const [label, fixture] of decorated) if (label !== 'Title') assert.equal(fixture.button.clicked, 0);
});

test('real Ignition active arrows preserve verified ascending and descending sorting', () => {
  for (const [field, label] of [['title', 'Title'], ['downloads', 'DLs']]) {
    for (const direction of ['asc', 'desc']) {
      const doc = page();
      const heading = doc.querySelectorAll('thead th')[field === 'title' ? 2 : 12];
      const { button, indicator } = decorateHeading(heading, { label, field, direction });
      assert.equal(indicator.getAttribute('aria-hidden'), 'true');
      assert.notEqual(indicator.style.display, 'none');
      assert.deepEqual(run(readSearchPage, doc).sort, { field, direction });
      assert.deepEqual(run(requestSearchSort, doc, { field, direction }), { status: 'applied', sort: { field, direction } });
      assert.equal(button.clicked, 0);
      const opposite = direction === 'asc' ? 'desc' : 'asc';
      assert.deepEqual(run(requestSearchSort, doc, { field, direction: opposite }), { status: 'clicked', sort: { field, direction } });
      assert.equal(button.clicked, 1);
      // aria-sort remains the source of truth while an indicator is stale.
      indicator.ownText = direction === 'asc' ? '▼' : '▲';
      assert.deepEqual(run(readSearchPage, doc).sort, { field, direction });
      assert.equal(run(requestSearchSort, doc, { field, direction }).status, 'applied');
      assert.equal(button.clicked, 1);
    }
  }
});

test('decorated headers still reject duplicate columns and multiple sort buttons', () => {
  const duplicateDoc = page();
  const first = decorateHeading(duplicateDoc.querySelectorAll('thead th')[2]);
  const duplicate = el('th', {}, [], 'Title');
  const second = decorateHeading(duplicate);
  const headerRow = duplicateDoc.querySelectorAll('thead tr')[0];
  duplicate.parentElement = headerRow; headerRow.children.push(duplicate);
  assert.equal(run(readSearchPage, duplicateDoc).status, 'layout_changed');
  assert.equal(run(requestSearchSort, duplicateDoc, { field: 'title', direction: 'asc' }).status, 'layout_changed');
  assert.equal(first.button.clicked, 0); assert.equal(second.button.clicked, 0);

  const buttonsDoc = page();
  const { button, content } = decorateHeading(buttonsDoc.querySelectorAll('thead th')[2]);
  const extra = el('button', { 'aria-label': 'Another action' });
  extra.parentElement = content; content.children.push(extra);
  const result = run(requestSearchSort, buttonsDoc, { field: 'title', direction: 'asc' });
  assert.equal(result.status, 'layout_changed');
  assert.equal(result.error, 'The requested sort button is unavailable.');
  assert.equal(button.clicked, 0); assert.equal(extra.clicked, 0);
});

test('sorting refuses missing, duplicate, disabled and off-site controls', () => {
  const doc = page();
  assert.equal(run(requestSearchSort, doc, { field: 'title', direction: 'asc' }).status, 'layout_changed');
  const heading = doc.querySelectorAll('thead th')[2];
  heading.ownText = '';
  const disabled = el('button', { disabled: '' }, [], 'Title');
  heading.children = [disabled];
  assert.equal(run(requestSearchSort, doc, { field: 'title', direction: 'asc' }).status, 'layout_changed');
  assert.equal(disabled.clicked, 0);
  delete disabled.attrs.disabled;
  doc.URL = 'https://evil.test/';
  assert.equal(run(requestSearchSort, doc, { field: 'title', direction: 'asc' }).status, 'layout_changed');
  doc.URL = origin + '/';
  for (const request of [{ field: 'parts', direction: 'asc' }, { field: 'title', direction: 'sideways' }]) assert.equal(run(requestSearchSort, doc, request).status, 'layout_changed');
  const headings = doc.querySelectorAll('thead tr')[0];
  const duplicate = el('th', {}, [el('button', {}, [], 'Title')]);
  duplicate.parentElement = headings;
  headings.children.push(duplicate);
  assert.equal(run(requestSearchSort, doc, { field: 'title', direction: 'asc' }).status, 'layout_changed');
  assert.equal(disabled.clicked, 0);
});

test('search lifecycle setup waits for Livewire and registers hooks once per document', () => {
  const fixture = searchLifecycle();
  assert.equal(run(prepareSearchUpdates, fixture.doc).status, 'waiting');
  assert.equal(run(prepareSearchUpdates, fixture.doc, undefined, { Livewire: {} }).status, 'waiting');
  for (let i = 0; i < 3; i++) assert.equal(run(prepareSearchUpdates, fixture.doc, undefined, { Livewire: fixture.Livewire }).status, 'ready');
  assert.deepEqual([...fixture.hooks.keys()].sort(), ['commit', 'morphed']);
  for (const callbacks of fixture.hooks.values()) assert.equal(callbacks.length, 1);
  assert.equal(run(prepareSearchUpdates, page()).status, 'ready', 'ordinary fixtures without the Livewire catalogue need no hooks');
  const outside = searchLifecycle(); outside.doc.URL = 'https://evil.test/';
  assert.equal(run(prepareSearchUpdates, outside.doc, undefined, { Livewire: outside.Livewire }).status, 'layout_changed');
  assert.equal(outside.hooks.size, 0);
});

test('optimistic sort metadata stays pending until both commit success and row morph in either order', () => {
  for (const first of ['succeed', 'morphed']) {
    const fixture = searchLifecycle();
    const { doc, heading, button, next, Livewire, component, emit, commit } = fixture;
    run(prepareSearchUpdates, doc, undefined, { Livewire });
    assert.equal(run(requestSearchSort, doc, { field: 'title', direction: 'asc' }).status, 'clicked');
    heading.attrs['aria-sort'] = 'ascending';
    const assertPending = () => {
      assert.equal(run(readSearchPage, doc).status, 'layout_changed');
      assert.equal(run(requestSearchSort, doc, { field: 'title', direction: 'asc' }).status, 'waiting');
      assert.equal(run(requestSearchPage, doc, { direction: 'next' }).status, 'waiting');
      assert.equal(button.clicked, 1); assert.equal(next.clicked, 0);
    };
    assertPending();
    const callbacks = commit();
    if (first === 'succeed') callbacks.succeed();
    else emit('morphed', { component });
    assertPending();
    if (first === 'succeed') emit('morphed', { component });
    else callbacks.succeed();
    assert.equal(run(readSearchPage, doc).status, 'ready');
    assert.deepEqual(run(requestSearchSort, doc, { field: 'title', direction: 'asc' }), { status: 'applied', sort: { field: 'title', direction: 'asc' } });
    assert.equal(button.clicked, 1, 'the confirmed order does not toggle again');
  }
});

test('only the catalogue component can complete an armed search update', () => {
  const { doc, table, heading, Livewire, component, emit, commit } = searchLifecycle();
  run(prepareSearchUpdates, doc, undefined, { Livewire });
  assert.deepEqual(commit(), {}, 'background commits before a search action are ignored');
  run(requestSearchSort, doc, { field: 'title', direction: 'asc' });
  heading.attrs['aria-sort'] = 'ascending';
  const foreign = { el: el('section') };
  assert.deepEqual(commit(foreign), {}, 'an unrelated component is not observed');
  emit('morphed', { component: foreign });
  assert.equal(run(readSearchPage, doc).status, 'layout_changed');
  const callbacks = commit();
  callbacks.succeed();
  emit('morphed', { component: foreign });
  assert.equal(run(readSearchPage, doc).status, 'layout_changed', 'an unrelated morph cannot finish the catalogue commit');
  emit('morphed', { component });
  assert.equal(run(readSearchPage, doc).status, 'ready');

  run(requestSearchSort, doc, { field: 'title', direction: 'desc' });
  const direct = { el: table };
  const nextCommit = commit(direct);
  emit('morphed', { component: direct }); nextCommit.succeed();
  assert.equal(run(readSearchPage, doc).status, 'ready', 'a component rooted directly at the table is also supported');
});

test('pagination waits for the catalogue update despite an optimistic current page value', () => {
  const { doc, input, next, Livewire, component, emit, commit } = searchLifecycle();
  run(prepareSearchUpdates, doc, undefined, { Livewire });
  assert.equal(run(requestSearchPage, doc, { direction: 'next' }).status, 'clicked');
  input.value = '2';
  assert.equal(run(readSearchPage, doc).status, 'layout_changed');
  assert.equal(run(requestSearchPage, doc, { direction: 'next' }).status, 'waiting');
  assert.equal(next.clicked, 1);
  const callbacks = commit(); callbacks.succeed();
  assert.equal(run(readSearchPage, doc).status, 'layout_changed');
  emit('morphed', { component });
  assert.equal(run(readSearchPage, doc).page, 2);
});

test('a failed catalogue commit rejects reading, sorting and paging until a fresh document', () => {
  const { doc, heading, button, next, Livewire, component, emit, commit } = searchLifecycle();
  run(prepareSearchUpdates, doc, undefined, { Livewire });
  run(requestSearchSort, doc, { field: 'title', direction: 'asc' });
  heading.attrs['aria-sort'] = 'ascending';
  const callbacks = commit(); callbacks.fail();
  emit('morphed', { component }); callbacks.succeed();
  run(prepareSearchUpdates, doc, undefined, { Livewire });
  for (const result of [run(readSearchPage, doc), run(requestSearchSort, doc, { field: 'title', direction: 'asc' }), run(requestSearchPage, doc, { direction: 'next' })]) {
    assert.equal(result.status, 'layout_changed');
    assert.match(result.error, /could not update the results/);
  }
  assert.equal(button.clicked, 1); assert.equal(next.clicked, 0);
  const fresh = searchLifecycle();
  run(prepareSearchUpdates, fresh.doc, undefined, { Livewire: fresh.Livewire });
  assert.equal(run(readSearchPage, fresh.doc).status, 'ready');
});

test('a search control click exception leaves the update failed instead of accepting stale rows', () => {
  for (const action of ['sort', 'page']) {
    const { doc, button, next, Livewire } = searchLifecycle();
    run(prepareSearchUpdates, doc, undefined, { Livewire });
    const control = action === 'sort' ? button : next;
    control.throwClick = true;
    const result = action === 'sort' ? run(requestSearchSort, doc, { field: 'title', direction: 'asc' }) : run(requestSearchPage, doc, { direction: 'next' });
    assert.equal(result.status, 'layout_changed');
    assert.equal(control.clicked, 1);
    assert.match(run(readSearchPage, doc).error, /could not update the results/);
  }
});

test('download support is supplied by main-process registry, with a conservative legacy default', () => {
  const { doc, anchor } = downloadPage({ host: 'OneDrive' });
  assert.equal(run(requestChartDownload, doc, { id: '6420' }).status, 'unsupported');
  assert.equal(anchor.clicked, 0);
  assert.equal(run(requestChartDownload, doc, { id: '6420', supportedHosts: ['onedrive'] }).status, 'clicked');
  assert.equal(anchor.clicked, 1);
  const unknown = downloadPage({ host: 'evil.test' });
  assert.equal(run(requestChartDownload, unknown.doc, { id: '6420', supportedHosts: ['unknown'] }).status, 'unsupported');
  assert.equal(unknown.anchor.clicked, 0);
});

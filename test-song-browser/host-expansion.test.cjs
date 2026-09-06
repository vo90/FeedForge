'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const { HOSTS, hostFromUrl, allowedNavigation, allowedDownload, MAX_BYTES } = require('../electron/song-browser/hosts.cjs');
const { selectFileCandidate, normalizeRequirements, validateRequirements } = require('../electron/song-browser/file-selection.cjs');
const { hostActionScript } = require('../electron/song-browser/host-actions.cjs');

test('host registry recognizes approved aliases without accepting lookalikes', () => {
  for (const [url, host] of [
    ['https://docs.google.com/file/d/example', 'google-drive'], ['https://1drv.ms/u/example', 'onedrive'],
    ['https://onedrive.live.com/?id=example', 'onedrive'], ['https://example.sharepoint.com/file', 'onedrive'],
    ['https://public.bn.files.1drv.com/file', 'onedrive'], ['https://u.pcloud.link/publink/show?code=example', 'pcloud'],
    ['https://c42.pcloud.com/file', 'pcloud'], ['https://mega.nz/file/example#key', 'mega'],
  ]) assert.equal(hostFromUrl(url), host, url);
  for (const url of ['https://docs.google.com.evil.test/file', 'https://evilsharepoint.com/file', 'https://1drv.ms.evil.test/file', 'https://pcloud.com@evil.test/file', 'http://u.pcloud.link/file']) {
    assert.equal(hostFromUrl(url), 'unknown'); assert.equal(allowedNavigation(url), false);
  }
  assert.equal(HOSTS.onedrive.status, 'experimental'); assert.equal(HOSTS.pcloud.status, 'experimental');
  assert.equal(HOSTS.mega.supported, true); assert.equal(HOSTS.mega.status, 'experimental'); assert.equal(allowedNavigation('https://mega.nz/file/example'), true);
});

test('new transfer policies check host family, filenames, byte limits and platform', () => {
  for (const url of ['https://public.bn.files.1drv.com/file', 'https://onedrive.live.com/download?id=example', 'https://example.sharepoint.com/download', 'https://c123.pcloud.com/file']) {
    assert.equal(allowedDownload(url, 'Song_p.psarc', MAX_BYTES), true, url);
    assert.equal(allowedDownload(url, 'Song_p.psarc', NaN), false);
    assert.equal(allowedDownload(url, 'Song_p.psarc', -1), false);
    assert.equal(allowedDownload(url, 'Song_p.psarc', MAX_BYTES + 1), false);
    assert.equal(allowedDownload(url, 'Song_m.psarc', 100), false);
    assert.equal(allowedDownload(url, 'Song_m.psarc', 100, { allowMacFallback: true }), true);
    for (const filename of ['../Song_p.psarc', 'x\\Song.psarc', 'Song.psarc.exe', 'Song.psarc\n']) assert.equal(allowedDownload(url, filename, 100), false);
  }
  assert.equal(allowedDownload('https://login.live.com/file', 'Song_p.psarc', 100), false);
  assert.equal(allowedDownload('https://c123.pcloud.com/file', 'Song_p.psarc', 100, { host: 'onedrive' }), false);
});

test('MEGA Blob capability is disabled unless owned active origin is explicitly attested', () => {
  const url = 'blob:https://mega.nz/12345678-abcd-ef12-abcd-123456789abc';
  assert.equal(allowedDownload(url, 'Song_p.psarc', 100), false);
  const context = { enableMegaBlob: true, ownedWindow: true, host: 'mega', documentUrl: 'https://mega.nz/file/example#key' };
  assert.equal(allowedDownload(url, 'Song_p.psarc', 100, context), true);
  for (const override of [{ ownedWindow: false }, { host: 'dropbox' }, { enableMegaBlob: false }, { documentUrl: 'https://evil.test/' }]) {
    assert.equal(allowedDownload(url, 'Song_p.psarc', 100, { ...context, ...override }), false);
  }
  assert.equal(allowedDownload('blob:https://evil.test/abc', 'Song_p.psarc', 100, context), false);
  assert.equal(allowedDownload('https://mega.nz/file/example', 'Song_p.psarc', 100, context), false);
});

const candidates = (...names) => names.map((label, i) => ({ id: `candidate-${i}`, label }));
test('file selection prefers matching PC counterpart without choosing among songs or versions', () => {
  assert.equal(selectFileCandidate(candidates('Song_m.psarc', 'Song_p.psarc')).candidate.label, 'Song_p.psarc');
  for (const names of [['Song_v1_p.psarc', 'Song_v2_p.psarc'], ['Song_p.psarc', 'Other_m.psarc'], ['Song_p.psarc', 'Song.psarc'], ['Song_p.psarc', 'Song_p.psarc']]) {
    assert.equal(selectFileCandidate(candidates(...names)).status, 'choose_file');
  }
  assert.equal(selectFileCandidate(candidates('Song_m.psarc')).status, 'choose_file');
  assert.equal(selectFileCandidate(candidates('Song_m.psarc'), { allowMacFallback: true }).status, 'selected');
  assert.equal(selectFileCandidate(candidates('Song.psarc')).status, 'selected');
});

test('saved file choices require one exact surviving descriptor and never silently fall back', () => {
  const rows = candidates('Song_v1_p.psarc', 'Song_v2_p.psarc');
  assert.equal(selectFileCandidate(rows, { choice: { id: 'candidate-1' } }).candidate.label, 'Song_v2_p.psarc');
  assert.equal(selectFileCandidate(rows, { choice: { id: 'candidate-0', label: 'Song_v2_p.psarc', platform: 'pc' } }).status, 'choose_file');
  assert.equal(selectFileCandidate(candidates('Song_p.psarc', 'Song_p.psarc'), { choice: { id: 'candidate-1', label: 'Song_p.psarc', platform: 'pc' } }).candidate.id, 'candidate-1');
  assert.equal(selectFileCandidate(rows, { choice: { label: 'Song_v2_p.psarc', platform: 'pc' } }).candidate.id, 'candidate-1');
  assert.equal(selectFileCandidate(rows, { choice: { id: 'stale' } }).status, 'choose_file');
  assert.equal(selectFileCandidate(rows, { choice: { label: 'Song_v3_p.psarc', platform: 'pc' } }).status, 'choose_file');
  assert.equal(selectFileCandidate(candidates('Song_p.psarc', 'Song_p.psarc'), { choice: { label: 'Song_p.psarc', platform: 'pc' } }).status, 'choose_file');
  assert.equal(selectFileCandidate(candidates('Song_m.psarc'), { choice: { id: 'candidate-0' } }).status, 'choose_file');
  assert.equal(selectFileCandidate([{ id: 'unsafe', label: '../file_p.psarc' }]).status, 'waiting');
});

test('requirements bind tuning to the requested arrangement and validate source platform evidence', () => {
  const preview = { source_platforms: ['pc'], arrangements: [
    { id: 'lead', type: 'guitar', tuning: [-1, -1, -1, -1, -1, -1] },
    { id: 'bass', type: 'bass', tuning: [0, 0, 0, 0, 0, 0] },
  ] };
  assert.equal(validateRequirements(preview, { parts: ['lead'], tuning: 'Eb Standard', strictPlatform: true }).ok, true);
  assert.equal(validateRequirements(preview, { parts: ['lead'], tuning: 'E Standard' }).ok, false);
  assert.equal(validateRequirements(preview, { parts: ['lead', 'rhythm'] }).ok, false);
  assert.equal(validateRequirements({ ...preview, source_platforms: ['mac'] }, { parts: ['lead'] }).ok, false);
  assert.equal(validateRequirements({ ...preview, source_platforms: ['mac'] }, { parts: ['lead'], allowMacFallback: true }).ok, true);
  assert.equal(validateRequirements({ arrangements: preview.arrangements }, { strictPlatform: true }).ok, false);
  assert.equal(validateRequirements(preview, { parts: ['bass'], tuning: 'Mystery tuning' }).ok, false);
  assert.equal(validateRequirements(preview, { parts: ['lead'], tuning: [-1, -1, -1, -1, -1, -1] }).ok, true);
  assert.equal(validateRequirements(preview, { backingTrack: 'no-guitar', backingStrict: true }).ok, true);
  assert.deepEqual(normalizeRequirements({ parts: ['bass', 'lead', 'bass'] }).parts, ['bass', 'lead']);
  assert.throws(() => normalizeRequirements({ parts: ['vocals'] }));
  assert.throws(() => normalizeRequirements({ tuning: [0, '0', 0, 0] }));
});

class Element {
  constructor(tag, attrs = {}, text = '', children = []) {
    this.tagName = tag.toUpperCase(); this.attrs = { ...attrs }; this.ownText = text; this.children = children;
    this.hidden = !!attrs.hidden; this.style = {}; this.clicked = 0; this.doubleClicked = 0;
    for (const child of children) child.parentElement = this;
  }
  get innerText() { return [this.ownText, ...this.children.map((child) => child.innerText)].filter(Boolean).join('\n'); }
  get textContent() { return this.innerText; }
  get href() { return this.attrs.href; }
  getAttribute(key) { return this.attrs[key] ?? null; }
  hasAttribute(key) { return Object.hasOwn(this.attrs, key); }
  setAttribute(key, value) { this.attrs[key] = value; }
  getClientRects() { return this.hidden ? [] : [{}]; }
  click() { this.clicked++; }
  dispatchEvent(event) { if (event.type === 'dblclick') this.doubleClicked++; }
  contains(other) { return this.descendants().includes(other); }
  descendants() { return this.children.flatMap((child) => [child, ...child.descendants()]); }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  querySelectorAll(selector) {
    return this.descendants().filter((element) => selector.split(',').some((part) => {
      const simple = part.trim();
      if (simple.startsWith('#')) return element.attrs.id === simple.slice(1);
      const match = simple.match(/^([a-z][a-z0-9]*)?(?:\[([^=\]]+)(?:="([^"]*)")?\])?$/i);
      if (!match) throw new Error('Unsupported fixture selector: ' + simple);
      return (!match[1] || element.tagName === match[1].toUpperCase()) && (!match[2] || (match[3] === undefined ? element.hasAttribute(match[2]) : element.getAttribute(match[2]) === match[3]));
    }));
  }
}
const element = (...args) => new Element(...args);
function page(url, children, text = '') {
  const body = element('body', {}, text, children);
  const document = element('document', {}, '', [body]); document.body = body; document.title = '';
  const target = new URL(url), assignments = [];
  const location = { href: url, hostname: target.hostname, assign: (value) => assignments.push(value) };
  const context = { document, location, URL, window: {}, MouseEvent: class { constructor(type) { this.type = type; } } };
  return { document, assignments, run: (request = {}) => JSON.parse(JSON.stringify(vm.runInNewContext(hostActionScript(request), context))) };
}

test('Dropbox folder chooses a PC counterpart using visible links and keeps share keys inside document', () => {
  const mac = element('a', { href: 'https://www.dropbox.com/scl/fi/mac/Song_m.psarc?rlkey=secret-key' }, 'Song_m.psarc');
  const pc = element('a', { href: 'https://www.dropbox.com/scl/fi/pc/Song_p.psarc?rlkey=secret-key' }, 'Song_p.psarc');
  const fixture = page('https://www.dropbox.com/scl/fo/folder?rlkey=secret-key', [mac, pc]);
  const result = fixture.run();
  assert.equal(result.status, 'clicked'); assert.equal(pc.clicked, 1); assert.equal(mac.clicked, 0);
  assert.equal(result.selectedFile.platform, 'pc'); assert.doesNotMatch(JSON.stringify(result), /https|rlkey|secret-key/);
  assert.equal(fixture.run().status, 'already_clicked');
});

test('ambiguous folder returns opaque choices; saved descriptor selects exactly one visible file', () => {
  const a = element('a', { href: 'https://drive.google.com/file/d/v1/view' }, 'Song_v1_p.psarc');
  const b = element('a', { href: 'https://drive.google.com/file/d/v2/view' }, 'Song_v2_p.psarc');
  const all = element('button', {}, 'Download');
  const fixture = page('https://drive.google.com/drive/folders/example', [a, b, all]);
  const result = fixture.run();
  assert.equal(result.status, 'choose_file'); assert.equal(result.candidates.length, 2);
  assert.equal(a.clicked + b.clicked + all.clicked, 0);
  assert.doesNotMatch(JSON.stringify(result), /drive\.google|\/file\/d/);
  assert.equal(fixture.run({ choice: { label: 'Song_v2_p.psarc', platform: 'pc' } }).status, 'clicked');
  assert.equal(b.clicked, 1); assert.equal(all.clicked, 0);
});

test('host chooser carries readable observed metadata without exposing link tokens', () => {
  const first = element('a', { href: 'https://drive.google.com/file/d/provider-secret-1/view' }, 'Song_v1_p.psarc', [
    element('span', {}, '12 MB'), element('span', {}, 'Version: v1.5'), element('span', {}, 'Backing: no-bass'), element('span', {}, 'Edition: live'),
  ]);
  const second = element('a', { href: 'https://drive.google.com/file/d/provider-secret-2/view' }, 'Song_v2_p.psarc');
  const result = page('https://drive.google.com/drive/folders/folder-secret', [first, second]).run();
  assert.equal(result.status, 'choose_file');
  const candidate = result.candidates.find((item) => item.label === 'Song_v1_p.psarc');
  assert.equal(candidate.sizeBytes, 12_000_000); assert.equal(candidate.versionHint, 'v1.5');
  assert.equal(candidate.backingHint, 'no-bass'); assert.equal(candidate.editionHint, 'live');
  assert.equal(candidate.evidence.version, 'observed'); assert.equal(candidate.evidence.backing, 'observed');
  assert.equal(result.candidates.find((item) => item.label === 'Song_v2_p.psarc').sizeBytes, null);
  assert.doesNotMatch(JSON.stringify(result), /secret|https|\/file\/d/);
});

test('virtualized folder never treats its only rendered candidate as a complete file list', () => {
  const file = element('a', { href: 'https://drive.google.com/file/d/v1/view' }, 'Song_p.psarc');
  const grid = element('div', { 'aria-rowcount': '30' }, '', [file]);
  const result = page('https://drive.google.com/drive/folders/example', [grid]).run();
  assert.equal(result.status, 'choose_file'); assert.match(result.error, /part of this folder/); assert.equal(file.clicked, 0);
});

test('OneDrive rows open the individual file and require a file heading before toolbar download', () => {
  const row = element('div', { role: 'row', 'aria-label': 'Song_p.psarc' });
  const downloadAll = element('button', {}, 'Download');
  const folder = page('https://onedrive.live.com/?id=folder', [row, downloadAll]);
  assert.equal(folder.run().status, 'clicked'); assert.equal(row.doubleClicked, 1); assert.equal(downloadAll.clicked, 0);
  const download = element('button', { 'aria-label': 'Download' });
  const file = page('https://onedrive.live.com/?id=file', [element('h1', {}, 'Song_p.psarc'), download]);
  assert.equal(file.run().status, 'clicked'); assert.equal(download.clicked, 1);
  const folderOnly = page('https://onedrive.live.com/?id=folder', [element('h1', {}, 'My songs'), downloadAll]);
  assert.equal(folderOnly.run().status, 'needs_attention'); assert.equal(downloadAll.clicked, 0);
});

test('pCloud handles a visible single-file download without APIs or folder ZIP actions', () => {
  const button = element('button', {}, 'Download');
  const fixture = page('https://u.pcloud.link/publink/show?code=example', [element('h1', {}, 'Song_p.psarc'), button]);
  assert.equal(fixture.run().status, 'clicked'); assert.equal(button.clicked, 1);
  assert.equal(fixture.run().status, 'already_clicked');
});

test('host candidates ignore hidden files, external links and disabled downloads', () => {
  const hidden = element('a', { href: 'https://drive.google.com/file/d/hidden/view', hidden: true }, 'Song_p.psarc');
  const external = element('a', { href: 'https://evil.test/Song_p.psarc' }, 'Song_p.psarc');
  const all = element('button', {}, 'Download');
  assert.equal(page('https://drive.google.com/drive/folders/example', [hidden, external, all]).run().status, 'needs_attention');
  assert.equal(hidden.clicked + external.clicked + all.clicked, 0);
  const disabled = element('button', { 'aria-disabled': 'true' }, 'Download');
  assert.equal(page('https://u.pcloud.link/publink/show?code=example', [element('h1', {}, 'Song_p.psarc'), disabled]).run().status, 'needs_attention');
  assert.equal(disabled.clicked, 0);
});

test('Mac files, expired links and login remain attention states', () => {
  assert.equal(page('https://www.dropbox.com/scl/fi/file/Song_m.psarc', []).run().status, 'needs_attention');
  assert.equal(page('https://login.live.com/', []).run().status, 'login_required');
  const button = element('button', {}, 'Download');
  assert.equal(page('https://onedrive.live.com/?id=file', [element('h1', {}, 'Song_p.psarc'), button], 'This link has expired').run().status, 'needs_attention');
  assert.equal(button.clicked, 0);
});

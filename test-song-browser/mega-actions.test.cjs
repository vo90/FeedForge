'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const { megaDownloadAction, megaCancelAction } = require('../electron/song-browser/mega-actions.cjs');
const { selectFileCandidate } = require('../electron/song-browser/file-selection.cjs');

// Small rendered-DOM fixture, deliberately without MEGA runtime, fetch, cookies,
// storage or even input values. It executes the serialized production functions.
class Element {
  constructor(tag, attrs = {}, value = '', children = []) {
    this.tagName = tag.toUpperCase(); this.attrs = { ...attrs }; this.ownText = value;
    this.children = children; this.style = {}; this.clicked = 0;
    this.hidden = attrs.hidden === true;
    for (const child of children) child.parentElement = this;
    Object.defineProperty(this, 'value', { get() { throw new Error('Do not read input values'); } });
  }
  get innerText() { return [this.ownText, ...this.children.map((child) => child.innerText)].filter(Boolean).join('\n'); }
  get textContent() { return this.innerText; }
  getAttribute(key) { return this.attrs[key] ?? null; }
  setAttribute(key, value) { this.attrs[key] = String(value); }
  hasAttribute(key) { return Object.hasOwn(this.attrs, key); }
  getClientRects() { return this.hidden || this.style.display === 'none' ? [] : [{}]; }
  click() { this.clicked++; this.onClick?.(); }
  descendants() { return this.children.flatMap((child) => [child, ...child.descendants()]); }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  querySelectorAll(selector) {
    const matchesSimple = (element, part) => {
      const tag = part.match(/^[a-z][a-z0-9-]*/i)?.[0];
      if (tag && element.tagName !== tag.toUpperCase()) return false;
      for (const [, cls] of part.matchAll(/\.([a-zA-Z0-9_-]+)/g)) if (!(element.attrs.class || '').split(/\s+/).includes(cls)) return false;
      for (const [, key, value] of part.matchAll(/\[([a-zA-Z0-9_-]+)(?:="([^"]*)")?\]/g)) if (value === undefined ? !element.hasAttribute(key) : element.getAttribute(key) !== value) return false;
      return true;
    };
    return this.descendants().filter((element) => selector.split(',').some((part) => {
      const tokens = part.trim().split(/\s+/);
      if (!matchesSimple(element, tokens.pop())) return false;
      let ancestor = element.parentElement;
      while (tokens.length) {
        const token = tokens.pop();
        while (ancestor && !matchesSimple(ancestor, token)) ancestor = ancestor.parentElement;
        if (!ancestor) return false;
        ancestor = ancestor.parentElement;
      }
      return true;
    }));
  }
}
const el = (...args) => new Element(...args);
const div = (classes, children = [], value = '') => el('div', { class: classes }, value, children);
const name = (label) => div('filename', [div('name', [], label.replace(/\.psarc$/i, '')), el('span', { class: 'ext' }, '.psarc')]);
function fixture(children, url = 'https://mega.nz/file/fixture#never-return-key') {
  const body = el('body', {}, '', children), document = el('document', {}, '', [body]); document.body = body;
  const parsed = new URL(url);
  const context = { document, location: { protocol: parsed.protocol, hostname: parsed.hostname }, getComputedStyle: (element) => element.style };
  const run = (request = {}) => JSON.parse(JSON.stringify(vm.runInNewContext(`(${megaDownloadAction.toString()})(${JSON.stringify(request)}, ${selectFileCandidate.toString()})`, context)));
  const cancel = () => JSON.parse(JSON.stringify(vm.runInNewContext(`(${megaCancelAction.toString()})()`, context)));
  return { body, document, run, cancel };
}
function single(label = 'Song_p.psarc', size = '12 MB', extras = [], buttons = null) {
  const button = el('button', {}, 'Download');
  const info = div('fileinfo', [name(label), div('size', [], size)]);
  const actions = div('actions', buttons || [button]);
  const header = div('dl-header', [info, actions]);
  const page = div('download-page', [div('dl-header-container', [header]), ...extras]);
  return { ...fixture([page]), page, button, info, header, actions };
}
function start(page, request = {}) {
  const result = page.run({ ...request, prepareOnly: true });
  assert.equal(result.status, 'prepared');
  return page.run({ ...request, expectedFile: result.selectedFile });
}
function progress(label = 'Song_p.psarc', statusText = 'Downloading', percent = 25) {
  const status = div('status', [], statusText), bar = div('bar'); bar.style.width = `${percent}%`;
  const close = el('button', { 'data-simpletip': 'Close' }, '', [el('i', { class: 'icon-dialog-close-thin' })]);
  const cancel = el('button', { 'data-simpletip': 'Cancel' }, '', [el('i', { class: 'icon-dialog-close-thin' })]);
  const widget = div('dl-widget progress', [name(label), div('header', [div('actions', [close])]), status, div('progress-bar', [bar]), div('cols', [div('actions', [cancel])])]);
  return { widget, status, bar, close, cancel };
}
function folder(labels, count = labels.length) {
  const rows = labels.map((label) => {
    const arrow = el('button', { class: 'grid-url-arrow' }, 'Options');
    const row = el('tr', { id: 'raw-mega-handle-' + label }, '', [div('tranfer-filetype-txt', [], label), div('file-size', [], '12 MB'), arrow]);
    row.arrow = arrow; return row;
  });
  const table = el('table', { class: 'grid-scrolling-table', ...(count === null ? {} : { 'aria-rowcount': String(count) }) }, '', rows);
  const standard = el('button', { class: 'download-standart-item' }, 'Standard download');
  const zip = el('button', { class: 'zipdownload-item' }, 'Download as ZIP');
  const sync = el('button', { class: 'megasyncdownload-item' }, 'Download with MEGAsync');
  const menu = div('dropdown body', [standard, zip, sync]); menu.hidden = true;
  for (const row of rows) {
    row.onClick = () => { for (const other of rows) other.attrs.class = other === row ? 'ui-selected' : ''; };
    row.arrow.onClick = () => { menu.hidden = false; };
  }
  return { ...fixture([table, menu]), rows, table, menu, standard, zip, sync };
}

test('single file prepares before click, enforces expected filename, and never clicks twice', () => {
  const page = single();
  const prepared = page.run({ prepareOnly: true });
  assert.equal(prepared.status, 'prepared'); assert.equal(page.button.clicked, 0);
  assert.equal(prepared.selectedFile.label, 'Song_p.psarc'); assert.equal(prepared.selectedFile.platform, 'pc');
  assert.equal(prepared.sizeBytes, 12 * 1024 * 1024);
  assert.doesNotMatch(JSON.stringify(prepared), /mega\.nz|never-return-key|raw-mega/);
  assert.equal(page.run().status, 'needs_attention'); assert.equal(page.button.clicked, 0);
  assert.equal(page.run({ expectedFile: { label: 'Other_p.psarc', platform: 'pc' } }).status, 'needs_attention');
  assert.equal(page.run({ expectedFile: prepared.selectedFile }).status, 'clicked'); assert.equal(page.button.clicked, 1);
  assert.equal(page.run({ expectedFile: prepared.selectedFile }).status, 'already_clicked'); assert.equal(page.button.clicked, 1);
});

test('visible file size is required and bounded before preparing MEGA memory downloads', () => {
  for (const size of ['', 'unknown', '0 B', '600 MB', '1 GB']) {
    const page = single('Song_p.psarc', size);
    assert.equal(page.run({ prepareOnly: true }).status, 'needs_attention', size); assert.equal(page.button.clicked, 0);
  }
  assert.equal(single('Song_p.psarc', '512 MiB').run({ prepareOnly: true }).status, 'prepared');
  assert.equal(single('Song_p.psarc', '12,5 MB').run({ prepareOnly: true }).sizeBytes, 12.5 * 1024 * 1024);
  assert.equal(single().run({ prepareOnly: true, maxBytes: 10 }).status, 'needs_attention');
});

test('Mac-only, multiple songs, stale saved choices and non-PSARC files need attention', () => {
  const mac = single('Song_m.psarc');
  assert.equal(mac.run({ prepareOnly: true }).status, 'choose_file'); assert.equal(mac.button.clicked, 0);
  assert.equal(mac.run({ prepareOnly: true, allowMacFallback: true }).status, 'prepared');
  const page = single();
  assert.equal(page.run({ prepareOnly: true, choice: { label: 'Missing_p.psarc', platform: 'pc' } }).status, 'choose_file');
  page.info.querySelector('.ext').ownText = '.exe';
  assert.equal(page.run({ prepareOnly: true }).status, 'needs_attention');
});

test('hidden MEGA template dialogs do not block downloads or expose secret input values', () => {
  const hiddenQuota = div('mega-dialog hidden', [], 'Transfer quota exceeded');
  const hiddenPassword = div('mega-dialog', [el('input', { type: 'password' })], 'Enter decryption key'); hiddenPassword.style.display = 'none';
  const page = single('Song_p.psarc', '12 MB', [hiddenQuota, hiddenPassword]);
  assert.equal(page.run({ prepareOnly: true }).status, 'prepared');
  hiddenQuota.attrs.class = 'mega-dialog';
  assert.match(page.run({ prepareOnly: true }).error, /quota/i); assert.equal(page.button.clicked, 0);
  hiddenQuota.hidden = true; hiddenPassword.style.display = '';
  assert.match(page.run({ prepareOnly: true }).error, /password|key/i);
});

test('visible deleted, quota, decryption and password errors are actionable without returning dialog text', () => {
  for (const message of ['This file is no longer available', 'Transfer quota exceeded', 'Decryption failed', 'Enter your decryption key secret-not-returned']) {
    const page = single('Song_p.psarc', '12 MB', [div('mega-dialog', [], message)]);
    const result = page.run({ prepareOnly: true });
    assert.equal(result.status, 'needs_attention', message); assert.equal(page.button.clicked, 0);
    assert.doesNotMatch(JSON.stringify(result), /secret-not-returned/);
  }
});

test('browser choices take precedence and external-app, ZIP, import and external link actions are excluded', () => {
  const sync = el('button', {}, 'Download with MEGAsync'), zip = el('button', {}, 'Download as ZIP'), save = el('button', {}, 'Save to MEGA');
  const browser = el('button', {}, 'Download through your browser');
  const page = single('Song_p.psarc', '12 MB', [div('mega-dialog', [sync, browser, zip, save])]);
  start(page); assert.equal(browser.clicked, 1); assert.equal(page.button.clicked + sync.clicked + zip.clicked + save.clicked, 0);
  for (const unsafe of ['Download with MEGAsync', 'Install desktop app', 'Download as ZIP', 'Save to MEGA']) {
    const button = el('button', {}, unsafe), fixture = single('Song_p.psarc', '12 MB', [], [button]);
    assert.equal(fixture.run({ prepareOnly: true }).status, 'needs_attention'); assert.equal(button.clicked, 0);
  }
  const unsafeLink = el('a', { href: 'https://evil.test/' }, 'Download through your browser');
  const safe = single('Song_p.psarc', '12 MB', [unsafeLink]); start(safe); assert.equal(unsafeLink.clicked, 0);
});

test('observed transfer progress distinguishes decrypting and saving with stable sanitized activity', () => {
  const transfer = progress(), page = single('Song_p.psarc', '12 MB', [transfer.widget]);
  start(page);
  const initial = page.run(); assert.equal(initial.status, 'transferring'); assert.equal(initial.progress, 25); assert.equal(initial.phase, 'downloading');
  assert.equal(page.run().activity, initial.activity);
  transfer.status.ownText = 'Decrypting (40%)'; transfer.bar.style.width = '40%';
  const decrypting = page.run(); assert.equal(decrypting.phase, 'decrypting'); assert.equal(decrypting.progress, 40); assert.notEqual(decrypting.activity, initial.activity);
  transfer.status.ownText = 'Complete'; transfer.bar.style.width = '100%';
  assert.equal(page.run().phase, 'saving'); assert.equal(page.run().status, 'transferring');
  transfer.status.ownText = 'Paused'; assert.equal(page.run().status, 'needs_attention');
});

test('browser prompt is handled despite an initializing widget and hidden file header', () => {
  const transfer = progress('Song_p.psarc', 'Initializing...', 0);
  const browser = el('button', {}, 'Download in browser');
  const prompt = div('mega-dialog', [browser], 'Get the MEGA desktop app'); prompt.hidden = true;
  const page = single('Song_p.psarc', '12 MB', [transfer.widget, prompt]);
  const prepared = page.run({ prepareOnly: true });
  assert.equal(page.run({ expectedFile: prepared.selectedFile }).status, 'clicked');
  page.header.hidden = true; prompt.hidden = false;
  const next = page.run({ expectedFile: prepared.selectedFile });
  assert.equal(next.status, 'clicked'); assert.equal(browser.clicked, 1); assert.equal(page.button.clicked, 1);
  assert.equal(page.run({ expectedFile: prepared.selectedFile }).status, 'transferring'); assert.equal(browser.clicked, 1);
  for (const value of [25, 40, 100]) {
    transfer.bar.style.width = `${value}%`;
    const state = page.run({ expectedFile: prepared.selectedFile });
    assert.equal(state.status, 'transferring'); assert.equal(state.progress, value); assert.equal(browser.clicked, 1);
  }
  prompt.hidden = true;
  assert.equal(page.run({ expectedFile: prepared.selectedFile }).status, 'transferring');
});

test('wrong transfer filename does not report owned progress or cancel an unrelated transfer', () => {
  const transfer = progress('Different_p.psarc'), page = single('Song_p.psarc', '12 MB', [transfer.widget]);
  start(page); assert.equal(page.run().status, 'already_clicked');
  assert.equal(page.cancel().status, 'unavailable'); assert.equal(transfer.cancel.clicked, 0);
});

test('cancel targets owned active transfer action, never widget close, and is idempotent', () => {
  const transfer = progress(), page = single('Song_p.psarc', '12 MB', [transfer.widget]);
  assert.equal(page.cancel().status, 'unavailable'); start(page);
  assert.equal(page.cancel().status, 'clicked'); assert.equal(transfer.cancel.clicked, 1); assert.equal(transfer.close.clicked, 0);
  assert.equal(page.cancel().status, 'already_clicked'); assert.equal(transfer.cancel.clicked, 1);
});

test('complete PC/Mac folder uses selected individual context menu and never ZIP or MEGAsync', () => {
  const page = folder(['Song_m.psarc', 'Song_p.psarc']);
  const opening = page.run({ prepareOnly: true });
  assert.equal(opening.status, 'clicked'); assert.equal(opening.action, 'select_file'); assert.equal(opening.selectedFile, undefined);
  assert.equal(page.rows[0].clicked, 0); assert.equal(page.rows[1].clicked, 1); assert.equal(page.rows[1].arrow.clicked, 1);
  const prepared = page.run({ prepareOnly: true }); assert.equal(prepared.status, 'prepared'); assert.equal(prepared.selectedFile.platform, 'pc');
  assert.equal(page.standard.clicked + page.zip.clicked + page.sync.clicked, 0);
  assert.equal(page.run({ expectedFile: prepared.selectedFile }).status, 'clicked'); assert.equal(page.standard.clicked, 1);
  assert.equal(page.zip.clicked + page.sync.clicked, 0); assert.doesNotMatch(JSON.stringify(prepared), /raw-mega-handle/);
  assert.equal(page.run().status, 'already_clicked'); assert.equal(page.standard.clicked, 1);
});

test('partial/virtualized folder requires explicit choice and preserves it across DOM reordering', () => {
  for (const count of [null, 20]) {
    const page = folder(['Song_p.psarc'], count);
    const choice = page.run({ prepareOnly: true }); assert.equal(choice.status, 'choose_file'); assert.equal(page.rows[0].clicked, 0);
    assert.equal(page.run({ prepareOnly: true, choice: { id: choice.candidates[0].id } }).action, 'select_file');
  }
  const page = folder(['Song_v1_p.psarc', 'Song_v2_p.psarc']);
  const options = page.run({ prepareOnly: true }); assert.equal(options.status, 'choose_file');
  const wanted = options.candidates[1]; page.table.children.reverse();
  const request = { prepareOnly: true, choice: { id: wanted.id } };
  assert.equal(page.run(request).action, 'select_file');
  assert.equal(page.run(request).selectedFile.label, 'Song_v2_p.psarc');
});

test('folder refuses multiselection, missing ordinary file menu, stale choice and app-only menu', () => {
  const multi = folder(['Song_m.psarc', 'Song_p.psarc']);
  multi.rows[0].attrs.class = 'ui-selected'; multi.rows[1].onClick = () => { multi.rows[1].attrs.class = 'ui-selected'; };
  assert.equal(multi.run({ prepareOnly: true }).status, 'needs_attention'); assert.equal(multi.standard.clicked, 0);
  const appOnly = folder(['Song_p.psarc']); appOnly.standard.hidden = true;
  assert.equal(appOnly.run({ prepareOnly: true }).action, 'select_file');
  assert.equal(appOnly.run({ prepareOnly: true }).status, 'needs_attention'); assert.equal(appOnly.sync.clicked + appOnly.zip.clicked, 0);
  assert.equal(folder(['Song_p.psarc']).run({ prepareOnly: true, choice: { label: 'Other_p.psarc', platform: 'pc' } }).status, 'choose_file');
  const namedFolder = folder(['Song_p.psarc']); namedFolder.rows[0].attrs.class = 'folder';
  assert.equal(namedFolder.run({ prepareOnly: true }).status, 'needs_attention');
  assert.equal(namedFolder.rows[0].clicked + namedFolder.standard.clicked + namedFolder.zip.clicked, 0);
});

test('folder supports ordinary context control selecting a row when row click has no handler', () => {
  const page = folder(['Song_p.psarc']);
  page.rows[0].onClick = () => {};
  page.rows[0].arrow.onClick = () => { page.rows[0].attrs.class = 'ui-selected'; page.menu.hidden = false; };
  assert.equal(page.run({ prepareOnly: true }).action, 'select_file');
  assert.equal(page.run({ prepareOnly: true }).status, 'prepared');
});

test('folder progress and cancellation match only the owned individual download row', () => {
  const page = folder(['Song_p.psarc']);
  page.run({ prepareOnly: true }); start(page);
  const makeTransfer = (label, kind = 'download') => {
    const cancel = el('button', { class: 'cancel', 'data-simpletip': 'Cancel' }, '', [el('i', { class: 'icon-dialog-close-thin' })]);
    const bar = div('transfer-progress-bar-pct'); bar.style.width = '65%';
    const row = div('transfer-task-row ' + kind, [div('transfer-name-block', [div('transfer-filetype-txt', [], label)]), div('transfer-task-status', [], 'Downloading'), div('transfer-progress-bar', [bar]), div('transfer-task-actions', [cancel])]);
    row.parentElement = page.body; page.body.children.push(row); return { row, cancel };
  };
  const other = makeTransfer('Other_p.psarc'), upload = makeTransfer('Song_p.psarc', 'upload'), owned = makeTransfer('Song_p.psarc');
  const state = page.run(); assert.equal(state.status, 'transferring'); assert.equal(state.progress, 65);
  assert.equal(page.cancel().status, 'clicked'); assert.equal(owned.cancel.clicked, 1); assert.equal(other.cancel.clicked + upload.cancel.clicked, 0);
  const duplicate = makeTransfer('Song_p.psarc');
  assert.equal(page.run().status, 'needs_attention'); assert.equal(page.cancel().status, 'unavailable'); assert.equal(duplicate.cancel.clicked, 0);
});

test('ordinary-click exceptions cannot cause a second download attempt', () => {
  const page = single(), prepared = page.run({ prepareOnly: true });
  page.button.onClick = () => { throw new Error('after click dispatch'); };
  assert.throws(() => page.run({ expectedFile: prepared.selectedFile }), /after click dispatch/);
  assert.equal(page.run({ expectedFile: prepared.selectedFile }).status, 'already_clicked'); assert.equal(page.button.clicked, 1);
});

test('adapter and cancellation reject insecure origins and hostname lookalikes', () => {
  for (const url of ['http://mega.nz/file/x', 'https://mega.nz.evil.test/file/x', 'https://evil.test/file/x']) {
    const page = fixture([], url); assert.equal(page.run({ prepareOnly: true }).status, 'needs_attention'); assert.equal(page.cancel().status, 'unavailable');
  }
});

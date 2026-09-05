'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const { CustomsForgeBrowser, allowedNavigation, allowedDownload, searchUrl, MAX_BYTES } = require('../electron/song-browser/browser.cjs');
const { hostDownloadAction } = require('../electron/song-browser/host-actions.cjs');
const { registerSongBrowser } = require('../electron/song-browser/index.cjs');

class FakeContents extends EventEmitter {
  constructor() { super(); this.url = ''; this.mainFrame = {}; this.responses = []; }
  setAudioMuted(value) { this.muted = value; }
  setWindowOpenHandler(handler) { this.openHandler = handler; }
  getURL() { return this.url; }
  executeJavaScript() { return Promise.resolve(this.responses.shift() || { status: 'waiting' }); }
  send() {}
}

class FakeWindow extends EventEmitter {
  static created = [];
  constructor(options) {
    super(); this.options = options; this.webContents = new FakeContents(); this.destroyed = false;
    this.visible = false; this.focused = false; FakeWindow.created.push(this);
  }
  loadURL(url) { this.webContents.url = url; return Promise.resolve(); }
  isDestroyed() { return this.destroyed; }
  show() { this.visible = true; }
  hide() { this.visible = false; }
  focus() { this.focused = true; }
  destroy() { if (!this.destroyed) { this.destroyed = true; this.emit('closed'); } }
}

class FakeDownload extends EventEmitter {
  constructor({ url = 'https://dl.dropboxusercontent.com/s/sample/chart.psarc', filename = 'chart.psarc', total = 200 } = {}) {
    super(); Object.assign(this, { url, filename, total, received: 0, state: 'progressing', cancelled: false });
  }
  getURL() { return this.url; }
  getFilename() { return this.filename; }
  getTotalBytes() { return this.total; }
  getReceivedBytes() { return this.received; }
  getState() { return this.state; }
  setSavePath(value) { this.savePath = value; }
  cancel() {
    assert.notEqual(this.notifyingUpdate, true, 'Chromium must not be reentered from its updated observer');
    this.cancelled = true; this.state = 'cancelled'; this.emit('done', {}, 'cancelled');
  }
  update(state = 'progressing') {
    this.notifyingUpdate = true;
    try { this.emit('updated', {}, state); } finally { this.notifyingUpdate = false; }
  }
  finish(state = 'completed') { this.state = state; this.emit('done', {}, state); }
}

class DeferredCancelDownload extends FakeDownload {
  cancel() {
    // Electron can acknowledge cancellation before the terminal event closes its file handle.
    this.cancelled = true;
    this.state = 'cancelled';
  }
}

function event() { return { prevented: false, preventDefault() { this.prevented = true; } }; }

function fixture(t) {
  const session = new EventEmitter();
  session.setPermissionRequestHandler = (handler) => { session.permissionRequest = handler; };
  session.setPermissionCheckHandler = (handler) => { session.permissionCheck = handler; };
  const connections = [];
  const browser = new CustomsForgeBrowser({ BrowserWindow: FakeWindow, session: { fromPath: () => session },
    profilePath: 'test-owned-profile', onConnection: (value) => connections.push(value) });
  // The polling loop concerns remote DOM changes; lifecycle tests drive Electron events explicitly.
  browser.driveDownload = async () => {};
  t.after(() => browser.dispose());
  return { browser, session, connections };
}

function begin(browser, overrides = {}) {
  const controller = new AbortController();
  const progress = [];
  const attention = [];
  const destination = 'C:\\test-owned\\download.psarc';
  const promise = browser.download({ id: '123', supported: true }, {
    destination, signal: controller.signal, onProgress: (value) => progress.push(value),
    onAttention: (value) => attention.push(value), ...overrides
  });
  const outcome = promise.then((value) => ({ value }), (error) => ({ error }));
  return { controller, progress, attention, destination, outcome, job: browser.active };
}

async function settlesSoon(promise) {
  let timer;
  try { return await Promise.race([promise, new Promise((resolve) => { timer = setTimeout(() => resolve({ stalled: true }), 100); })]); }
  finally { clearTimeout(timer); }
}

test('navigation permits supported HTTPS hosts and rejects lookalikes, credentials, custom schemes and MEGA', () => {
  for (const url of ['https://ignition4.customsforge.com/cdlc/123', 'https://drive.google.com/file/d/test/view',
    'https://drive.usercontent.google.com/download', 'https://accounts.google.com/',
    'https://www.dropbox.com/scl/fi/test/chart.psarc', 'https://dl.dropboxusercontent.com/s/test/chart.psarc',
    'https://www.mediafire.com/file/test/chart.psarc', 'https://download1520.mediafire.com/test/chart.psarc']) {
    assert.equal(allowedNavigation(url), true, url);
  }
  for (const url of ['https://mega.nz/file/test', 'https://www.dropbox.com.evil.test/file',
    'https://evildropboxusercontent.com/file', 'https://download1.mediafire.com.evil.test/file',
    'https://drive.google.com@evil.test/file', 'https://name:secret@drive.google.com/file',
    'https://drive.google.com:444/file', 'http://www.dropbox.com/file', 'file:///C:/private.psarc',
    'javascript:alert(1)', 'data:text/html,hello', 'not a url']) {
    assert.equal(allowedNavigation(url), false, url);
  }
});

test('download guard checks final file host, PSARC extension and byte cap', () => {
  const url = 'https://download1.mediafire.com/abc/chart.psarc';
  assert.equal(allowedDownload(url, 'Song.PSARC', MAX_BYTES), true);
  assert.equal(allowedDownload(url, 'Song.psarc', MAX_BYTES + 1), false);
  for (const name of ['song.exe', 'song.psarc.exe', 'song.html', 'song.psarc ', '']) {
    assert.equal(allowedDownload(url, name, 100), false, name);
  }
  assert.equal(allowedDownload('https://accounts.google.com/file', 'song.psarc', 100), false);
  assert.equal(allowedDownload('https://ignition4.customsforge.com/file', 'song.psarc', 100), false);
  assert.equal(allowedDownload('https://mega.nz/file/test', 'song.psarc', 100), false);
});

test('search construction encodes literal input and rejects malformed pagination', () => {
  const query = 'Title & artist #one?extra=yes';
  const url = new URL(searchUrl(query, 2));
  assert.equal(url.origin, 'https://ignition4.customsforge.com');
  assert.equal(url.searchParams.get('search'), query);
  assert.equal(url.searchParams.has('page'), false, 'pagination uses the visible page control, not an invented endpoint parameter');
  assert.equal(url.searchParams.size, 1);
  assert.equal(url.hash, '');
  for (const bad of ['', 'a', 'a'.repeat(161), {}, null]) assert.throws(() => searchUrl(bad));
  for (const bad of [0, -1, 1.5, 10001, '2']) assert.throws(() => searchUrl('Song', bad));
});

test('search failure clears stale connection and pagination state', async (t) => {
  const { browser } = fixture(t);
  browser.updateConnection('connected', 'Connected to CustomsForge');
  browser.lastSearch = { query: 'fixture', page: 2 };
  const win = browser.ensureSearchWindow();
  win.loadURL = () => Promise.reject(new Error('offline fixture'));
  await assert.rejects(browser.search({ query: 'different' }), /could not be loaded/);
  assert.equal(browser.connection.status, 'error'); assert.equal(browser.lastSearch, null);
});

test('ordinary login completion and later sign-out update connection from the loaded page', async (t) => {
  const { browser } = fixture(t); const win = browser.ensureSearchWindow();
  win.webContents.url = 'https://ignition4.customsforge.com/';
  win.webContents.responses.push({ status: 'ready', results: [], hasNext: false, page: 1 });
  win.webContents.emit('did-finish-load'); await new Promise((resolve) => setImmediate(resolve));
  assert.equal(browser.connection.status, 'connected');
  browser.lastSearch = { query: 'fixture', page: 2 };
  win.webContents.url = 'https://customsforge.com/oauth/authorize';
  win.webContents.responses.push({ status: 'login_required' });
  win.webContents.emit('did-finish-load'); await new Promise((resolve) => setImmediate(resolve));
  assert.equal(browser.connection.status, 'signed_out'); assert.equal(browser.lastSearch, null);
});

test('an old login-page read cannot replace the connection established by a newer search', async (t) => {
  const { browser } = fixture(t); const win = browser.ensureSearchWindow();
  win.webContents.url = searchUrl('fixture');
  let resolve;
  const oldPage = new Promise((done) => { resolve = done; });
  win.webContents.executeJavaScript = () => oldPage;
  const reading = browser.readConnection(win);
  win.webContents.executeJavaScript = async () => ({ status: 'ready', results: [], page: 1 });
  await browser.search({ query: 'fixture' });
  resolve({ status: 'login_required' }); await reading;
  assert.equal(browser.connection.status, 'connected');
  assert.equal(browser.lastSearch.query, 'fixture');
});

test('connection inspection waits for the loaded page to render its table', async (t) => {
  const { browser } = fixture(t); const win = browser.ensureSearchWindow();
  win.webContents.url = 'https://ignition4.customsforge.com/';
  win.webContents.responses.push({ status: 'layout_changed', results: [] }, { status: 'ready', results: [], page: 1 });
  await browser.readConnection(win);
  assert.equal(browser.connection.status, 'connected');
});

test('a navigation timeout cannot become success when stopping the page emits ERR_ABORTED', async (t) => {
  const { browser } = fixture(t); const win = browser.ensureSearchWindow();
  t.mock.timers.enable({ apis: ['setTimeout'] });
  let rejectLoad;
  win.loadURL = () => new Promise((_resolve, reject) => { rejectLoad = reject; });
  win.webContents.stop = () => rejectLoad(Object.assign(new Error('Stopped by the timeout.'), { code: 'ERR_ABORTED', errno: -3 }));
  const navigation = browser.navigate(win, 'https://ignition4.customsforge.com/').then(() => ({ error: null }), (error) => ({ error }));
  t.mock.timers.tick(30000);
  const result = await navigation;
  assert.ok(result.error, 'a timed-out page must fail even when Chromium reports its abort first');
});

test('search waits for an incomplete table and accepts a later explicit empty result', async (t) => {
  const { browser } = fixture(t); const win = browser.ensureSearchWindow();
  win.webContents.responses.push({ status: 'layout_changed', results: [] }, { status: 'ready', results: [], page: 1, total: 0 });
  const result = await browser.search({ query: 'fixture' });
  assert.equal(result.status, 'ready'); assert.equal(result.total, 0);
  assert.equal(browser.connection.status, 'connected');
});

test('browser profile windows deny native permissions, retain sandbox and block unsupported navigation/popups', (t) => {
  const { browser, session } = fixture(t);
  const win = browser.ensureSearchWindow();
  const prefs = win.options.webPreferences;
  assert.equal(prefs.session, session);
  assert.equal(prefs.nodeIntegration, false);
  assert.equal(prefs.contextIsolation, true);
  assert.equal(prefs.sandbox, true);
  assert.equal(prefs.webSecurity, true);
  assert.equal(session.permissionCheck(), false);
  let granted;
  session.permissionRequest({}, 'media', (value) => { granted = value; });
  assert.equal(granted, false);
  const navigation = event();
  win.webContents.emit('will-navigate', navigation, 'https://mega.nz/file/test');
  assert.equal(navigation.prevented, true);
  const redirect = event();
  win.webContents.emit('will-redirect', redirect, 'https://www.dropbox.com.evil.test/');
  assert.equal(redirect.prevented, true);
  assert.equal(win.webContents.openHandler({ url: 'https://www.dropbox.com/s/test/file' }).action, 'deny');
  const webview = event(); win.webContents.emit('will-attach-webview', webview);
  assert.equal(webview.prevented, true);
});

test('only the active download windows can deliver a file; unrelated and second downloads are prevented', async (t) => {
  const { browser, session } = fixture(t);
  const searchWindow = browser.ensureSearchWindow();
  const transfer = begin(browser);
  const jobWindow = [...transfer.job.windows][0];
  const foreign = new FakeDownload(); const foreignEvent = event();
  session.emit('will-download', foreignEvent, foreign, searchWindow.webContents);
  assert.equal(foreignEvent.prevented, true);
  assert.equal(foreign.savePath, undefined);
  assert.equal(transfer.job.finished, false);
  const item = new FakeDownload(); const accepted = event();
  session.emit('will-download', accepted, item, jobWindow.webContents);
  assert.equal(accepted.prevented, false);
  assert.equal(item.savePath, transfer.destination);
  const duplicate = new FakeDownload(); const duplicateEvent = event();
  session.emit('will-download', duplicateEvent, duplicate, jobWindow.webContents);
  assert.equal(duplicateEvent.prevented, true);
  assert.equal(duplicate.savePath, undefined);
  item.received = 100; item.update(); item.received = 200; item.finish();
  assert.deepEqual(await transfer.outcome, { value: transfer.destination });
  assert.deepEqual(transfer.progress, [0, 50, 100]);
  assert.equal(browser.active, null);
  assert.equal(jobWindow.isDestroyed(), true);
  assert.equal(searchWindow.isDestroyed(), false);
});

test('download popups inherit sandbox and are attributed to their active job', async (t) => {
  const { browser, session } = fixture(t);
  const transfer = begin(browser);
  const win = [...transfer.job.windows][0];
  const popup = win.webContents.openHandler({ url: 'https://www.dropbox.com/s/test/chart.psarc' });
  assert.equal(popup.action, 'allow');
  assert.equal(popup.overrideBrowserWindowOptions.webPreferences.nodeIntegration, false);
  assert.equal(popup.overrideBrowserWindowOptions.webPreferences.sandbox, true);
  assert.equal(popup.overrideBrowserWindowOptions.webPreferences.session, session);
  assert.equal(win.webContents.openHandler({ url: 'https://mega.nz/file/test' }).action, 'deny');
  const child = new FakeWindow(popup.overrideBrowserWindowOptions);
  win.webContents.emit('did-create-window', child);
  assert.ok(transfer.job.windows.has(child));
  const item = new FakeDownload(); const downloadEvent = event();
  session.emit('will-download', downloadEvent, item, child.webContents);
  assert.equal(downloadEvent.prevented, false);
  item.received = 200; item.finish();
  assert.equal((await transfer.outcome).value, transfer.destination);
  assert.equal(child.isDestroyed(), true);
  assert.equal(win.isDestroyed(), true);
});

test('unexpected content from an owned window fails cleanly without accepting the file', async (t) => {
  const { browser, session } = fixture(t);
  const transfer = begin(browser);
  const win = [...transfer.job.windows][0];
  const item = new FakeDownload({ filename: 'download-helper.exe' });
  const downloadEvent = event();
  session.emit('will-download', downloadEvent, item, win.webContents);
  assert.equal(downloadEvent.prevented, true);
  assert.equal(item.savePath, undefined);
  assert.match((await transfer.outcome).error.message, /supported PSARC/);
  assert.equal(browser.active, null);
  assert.equal(win.isDestroyed(), true);
});

test('interrupted downloads are cancelled and destroy all job windows', async (t) => {
  const { browser, session } = fixture(t);
  const transfer = begin(browser);
  const win = [...transfer.job.windows][0];
  const item = new FakeDownload();
  session.emit('will-download', event(), item, win.webContents);
  item.update('interrupted');
  assert.match((await transfer.outcome).error.message, /interrupted/i, 'a synchronous cancelled event must not erase the interruption reason');
  assert.equal(item.cancelled, true);
  assert.equal(win.isDestroyed(), true);
  assert.equal(browser.active, null);
  assert.ok(!transfer.progress.includes(100));
});

test('received byte limit stops unknown-length downloads', async (t) => {
  const { browser, session } = fixture(t);
  const transfer = begin(browser);
  const win = [...transfer.job.windows][0];
  const item = new FakeDownload({ total: 0 });
  session.emit('will-download', event(), item, win.webContents);
  item.received = MAX_BYTES + 1; item.update();
  assert.match((await transfer.outcome).error.message, /exceeds.*limit/i, 'a synchronous cancelled event must not erase the size-limit reason');
  assert.equal(item.cancelled, true);
  assert.ok(!transfer.progress.includes(100));
});

test('completed event rechecks final size, including bytes received after the last progress event', async (t) => {
  const { browser, session } = fixture(t);
  const transfer = begin(browser);
  const win = [...transfer.job.windows][0];
  const item = new FakeDownload({ total: 0 });
  session.emit('will-download', event(), item, win.webContents);
  item.received = MAX_BYTES + 1; item.finish();
  const result = await transfer.outcome;
  assert.ok(result.error, 'oversized completion must not enter conversion');
  assert.ok(!transfer.progress.includes(100));
});

test('completed empty downloads never enter conversion', async (t) => {
  const { browser, session } = fixture(t);
  const transfer = begin(browser);
  const win = [...transfer.job.windows][0];
  const item = new FakeDownload({ total: 0 });
  session.emit('will-download', event(), item, win.webContents);
  item.finish();
  assert.ok((await transfer.outcome).error);
  assert.ok(!transfer.progress.includes(100));
});

test('AbortSignal settles active transfers and permits another job', async (t) => {
  const { browser, session } = fixture(t);
  const transfer = begin(browser);
  const win = [...transfer.job.windows][0];
  const item = new FakeDownload();
  session.emit('will-download', event(), item, win.webContents);
  transfer.controller.abort();
  assert.match((await transfer.outcome).error.message, /cancel/i);
  assert.equal(item.cancelled, true);
  assert.equal(win.isDestroyed(), true);
  assert.equal(browser.active, null);
  const next = begin(browser);
  next.controller.abort();
  assert.match((await next.outcome).error.message, /cancel/i);
});

test('cancellation waits for the DownloadItem terminal event before releasing the job or its windows', async (t) => {
  const { browser, session } = fixture(t);
  const transfer = begin(browser);
  const win = [...transfer.job.windows][0];
  const item = new DeferredCancelDownload();
  session.emit('will-download', event(), item, win.webContents);
  transfer.controller.abort();
  try {
    const premature = await settlesSoon(transfer.outcome);
    assert.equal(premature.stalled, true, 'do not release the work directory while the host may still hold its file handle');
    assert.equal(item.cancelled, true);
    assert.equal(browser.active, transfer.job, 'a second job must wait for the terminal event');
    assert.equal(win.isDestroyed(), false, 'keep the owning window until the transfer has terminated');
  } finally {
    item.finish('cancelled');
  }
  const result = await transfer.outcome;
  assert.match(result.error.message, /cancel/i);
  assert.equal(win.isDestroyed(), true);
  assert.equal(browser.active, null);
});

test('a popup delivered after cancellation is destroyed immediately and never added to browser ownership', async (t) => {
  const { browser } = fixture(t);
  const transfer = begin(browser);
  const win = [...transfer.job.windows][0];
  transfer.controller.abort();
  assert.match((await transfer.outcome).error.message, /cancel/i);
  const latePopup = new FakeWindow({});
  win.webContents.emit('did-create-window', latePopup);
  assert.equal(latePopup.isDestroyed(), true);
  assert.equal(transfer.job.windows.has(latePopup), false);
  assert.equal(browser.windows.has(latePopup), false);
  assert.equal(latePopup.visible, false);
});

test('a popup delivered after browser disposal is destroyed even without a download job', (t) => {
  const { browser } = fixture(t);
  const win = browser.ensureSearchWindow();
  browser.dispose();
  const latePopup = new FakeWindow({});
  win.webContents.emit('did-create-window', latePopup);
  assert.equal(latePopup.isDestroyed(), true);
  assert.equal(browser.windows.has(latePopup), false);
});

test('cancellation settles while the initial navigation is still pending', async (t) => {
  const { browser } = fixture(t);
  let finishNavigation;
  browser.navigate = () => new Promise((resolve) => { finishNavigation = resolve; });
  const transfer = begin(browser);
  const win = [...transfer.job.windows][0];
  transfer.controller.abort();
  try {
    const result = await settlesSoon(transfer.outcome);
    assert.equal(result.stalled, undefined, 'cancellation must not wait for a stalled network navigation');
    assert.match(result.error.message, /cancel/i);
    assert.equal(win.isDestroyed(), true);
    assert.equal(browser.active, null);
  } finally { finishNavigation(); await transfer.outcome; }
});

test('dispose rejects a running download and removes the session download listener', async (t) => {
  const { browser, session } = fixture(t);
  const transfer = begin(browser);
  const win = [...transfer.job.windows][0];
  const item = new FakeDownload();
  session.emit('will-download', event(), item, win.webContents);
  browser.dispose();
  assert.ok((await transfer.outcome).error);
  assert.equal(item.cancelled, true);
  assert.equal(win.isDestroyed(), true);
  assert.equal(session.listenerCount('will-download'), 0);
  assert.equal(browser.windows.size, 0);
});

test('a late expired-button reply cannot refresh after cancellation or disposal', async (t) => {
  for (const stop of ['cancel', 'dispose']) {
    await t.test(stop, async (subtest) => {
      const { browser } = fixture(subtest); const transfer = begin(browser);
      const win = [...transfer.job.windows][0]; let refreshes = 0;
      win.loadURL = () => { refreshes++; return Promise.resolve(); };
      win.webContents.executeJavaScript = async () => {
        if (stop === 'cancel') transfer.controller.abort(); else browser.dispose();
        return { status: 'expired' };
      };
      await CustomsForgeBrowser.prototype.driveDownload.call(browser, transfer.job, { id: '123' });
      assert.ok((await transfer.outcome).error);
      assert.equal(refreshes, 0, 'an awaited old-page result must not cause another navigation');
    });
  }
});

test('a download starting during a page action stops later window actions and stale attention', async (t) => {
  const { browser, session } = fixture(t); const transfer = begin(browser);
  const first = [...transfer.job.windows][0]; first.webContents.url = 'https://drive.google.com/file/d/fixture/view';
  const child = new FakeWindow({}); child.webContents.url = 'https://www.dropbox.com/s/fixture/chart.psarc';
  browser.attachWindow(child, transfer.job);
  let laterActions = 0;
  child.webContents.executeJavaScript = async () => { laterActions++; return { status: 'clicked' }; };
  const item = new FakeDownload();
  first.webContents.executeJavaScript = async () => {
    session.emit('will-download', event(), item, first.webContents);
    setImmediate(() => { item.received = item.total; item.finish(); });
    return { status: 'needs_attention', error: 'Reply from the document that just initiated the transfer.' };
  };
  await CustomsForgeBrowser.prototype.driveDownload.call(browser, transfer.job, { id: '123' });
  assert.equal((await transfer.outcome).value, transfer.destination);
  assert.equal(laterActions, 0); assert.deepEqual(transfer.attention, []);
});

test('an attention callback cancelling its job cannot reopen the cancelled browser window', async (t) => {
  const { browser } = fixture(t); const transfer = begin(browser);
  const win = [...transfer.job.windows][0];
  transfer.job.onAttention = () => transfer.controller.abort();
  browser.attention(transfer.job, 'A host step needs attention.');
  assert.equal(win.visible, false);
  assert.ok((await transfer.outcome).error);
});

test('an expired reply from a replaced chart document cannot navigate away from its host page', async (t) => {
  const { browser } = fixture(t); const transfer = begin(browser);
  const win = [...transfer.job.windows][0]; let refreshes = 0;
  win.loadURL = () => { refreshes++; return Promise.resolve(); };
  win.webContents.executeJavaScript = async () => {
    win.webContents.url = 'https://www.dropbox.com/s/fixture/chart.psarc';
    setImmediate(() => transfer.controller.abort());
    return { status: 'expired' };
  };
  await CustomsForgeBrowser.prototype.driveDownload.call(browser, transfer.job, { id: '123' });
  await transfer.outcome;
  assert.equal(refreshes, 0);
});

function hostAction({ url, body = '', title = '', link = null, buttons = [] }) {
  const target = new URL(url);
  const assignments = [];
  const location = { hostname: target.hostname, href: target.href, assign: (value) => assignments.push(value) };
  const document = { title, body: { innerText: body }, querySelector: () => link, querySelectorAll: () => buttons };
  const result = vm.runInNewContext(`(${hostDownloadAction.toString()})()`, { location, document, URL });
  return { result, assignments };
}

function button(label, href) {
  const attributes = new Map();
  return { href, innerText: label, clicked: 0, disabled: false,
    getAttribute(name) { return attributes.get(name) || ''; }, hasAttribute(name) { return attributes.has(name); },
    setAttribute(name, value) { attributes.set(name, value); }, getClientRects() { return [{}]; },
    click() { this.clicked += 1; } };
}

test('Dropbox transforms only single-file share links, preserves resource keys and never rewrites folders', () => {
  const file = hostAction({ url: 'https://www.dropbox.com/scl/fi/test/chart.psarc?rlkey=public-key&dl=0' });
  assert.equal(file.result.status, 'clicked');
  const output = new URL(file.assignments[0]);
  assert.equal(output.searchParams.get('dl'), '1');
  assert.equal(output.searchParams.get('rlkey'), 'public-key');
  const folder = hostAction({ url: 'https://www.dropbox.com/scl/fo/test/folder?rlkey=public-key&dl=0' });
  assert.equal(folder.result.status, 'needs_attention');
  assert.equal(folder.assignments.length, 0);
});

test('host checks and quota messages never trigger automatic download clicks', () => {
  for (const body of ['Verify you are human', 'Download quota exceeded', 'You need access', 'The file has been deleted']) {
    const link = button('Download', 'https://download1.mediafire.com/file/chart.psarc');
    const output = hostAction({ url: 'https://www.mediafire.com/file/test/', body, link });
    assert.ok(['challenge', 'needs_attention'].includes(output.result.status), body);
    assert.equal(link.clicked, 0);
  }
  const google = hostAction({ url: 'https://accounts.google.com/signin' });
  assert.equal(google.result.status, 'login_required');
});

test('MediaFire rejects spoofed download hosts and clicks an approved link only once', () => {
  const fake = button('Download', 'https://download1.mediafire.com.evil.test/file');
  const rejected = hostAction({ url: 'https://www.mediafire.com/file/test/', link: fake });
  assert.equal(rejected.result.status, 'needs_attention');
  assert.equal(fake.clicked, 0);
  const link = button('Download', 'https://download1.mediafire.com/file/chart.psarc');
  assert.equal(hostAction({ url: 'https://www.mediafire.com/file/test/', link }).result.status, 'clicked');
  assert.equal(hostAction({ url: 'https://www.mediafire.com/file/test/', link }).result.status, 'already_clicked');
  assert.equal(link.clicked, 1);
});

test('MediaFire skips hidden and disabled controls and rejects credential-bearing download URLs', () => {
  const states = [(link) => { link.hidden = true; }, (link) => { link.disabled = true; },
    (link) => link.setAttribute('aria-disabled', 'true'), (link) => link.setAttribute('aria-hidden', 'true'),
    (link) => { link.style = { visibility: 'hidden' }; }, (link) => { link.getClientRects = () => []; },
    (link) => { link.href = 'https://private:secret@download1.mediafire.com/file/chart.psarc'; },
    (link) => { link.href = 'https://download1.mediafire.com:8443/file/chart.psarc'; }];
  for (const configure of states) {
    const link = button('Download', 'https://download1.mediafire.com/file/chart.psarc'); configure(link);
    assert.equal(hostAction({ url: 'https://www.mediafire.com/file/test/', link }).result.status, 'needs_attention');
    assert.equal(link.clicked, 0);
  }
});

test('Google Drive ignores aria-disabled or hidden controls and uses the next visible download', () => {
  const disabled = button('Download'); disabled.setAttribute('aria-disabled', 'true');
  const hidden = button('Download'); hidden.hidden = true;
  const parent = button(''); parent.style = { display: 'none' };
  const nested = button('Download'); nested.parentElement = parent;
  const visible = button('Download anyway');
  const response = hostAction({ url: 'https://drive.google.com/file/d/fixture/view', buttons: [disabled, hidden, nested, visible] });
  assert.equal(response.result.status, 'clicked'); assert.equal(visible.clicked, 1);
  assert.equal(disabled.clicked, 0); assert.equal(hidden.clicked, 0); assert.equal(nested.clicked, 0);
});

test('IPC rejects foreign senders and subframes before touching local state', async () => {
  const handlers = new Map();
  const window = new FakeWindow({});
  let initialized = 0;
  const app = new EventEmitter();
  app.getPath = () => { initialized += 1; throw new Error('Trusted request reached initialization.'); };
  registerSongBrowser({ app, BrowserWindow: FakeWindow, session: {}, ipcMain: { handle: (name, handler) => handlers.set(name, handler) },
    dialog: {}, shell: {}, getMainWindow: () => window, runConverter: async () => {} });
  assert.ok(handlers.size >= 8);
  for (const [name, handler] of handlers) {
    await assert.rejects(handler({ sender: new FakeContents(), senderFrame: window.webContents.mainFrame }, {}), /must come from FeedForge/, name);
    await assert.rejects(handler({ sender: window.webContents, senderFrame: {} }, {}), /must come from FeedForge/, name);
  }
  assert.equal(initialized, 0);
  const accepted = await handlers.get('song-browser:getState')({ sender: window.webContents, senderFrame: window.webContents.mainFrame }, {});
  assert.equal(initialized, 1);
  assert.equal(accepted.ok, false);
  assert.match(accepted.error, /Trusted request reached initialization/);
});

test('output folder selection rolls back on settings rename failure and persists a successful retry', async (t) => {
  const tempBase = fs.realpathSync(os.tmpdir());
  const userData = fs.mkdtempSync(path.join(tempBase, 'feedforge-output-settings-'));
  const selected = path.join(userData, 'chosen-library');
  fs.mkdirSync(selected);
  const handlers = new Map();
  const window = new FakeWindow({});
  const session = new EventEmitter();
  session.setPermissionRequestHandler = () => {};
  session.setPermissionCheckHandler = () => {};
  const app = new EventEmitter();
  app.getPath = (name) => { assert.equal(name, 'userData'); return userData; };
  const prompts = [];
  const registration = registerSongBrowser({ app, BrowserWindow: FakeWindow,
    session: { fromPath: () => session }, ipcMain: { handle: (name, handler) => handlers.set(name, handler) },
    dialog: { showOpenDialog: async (_window, options) => {
      prompts.push(options);
      return { canceled: false, filePaths: [selected] };
    } }, shell: {}, getMainWindow: () => window, runConverter: async () => {} });
  t.after(async () => {
    await registration.close();
    const resolved = fs.realpathSync(userData);
    assert.equal(path.dirname(resolved), tempBase);
    assert.match(path.basename(resolved), /^feedforge-output-settings-/);
    fs.rmSync(resolved, { recursive: true, force: true });
  });
  const trustedEvent = { sender: window.webContents, senderFrame: window.webContents.mainFrame };
  const invoke = (name) => handlers.get(`song-browser:${name}`)(trustedEvent, {});
  const original = await invoke('getState');
  assert.ok(original.outputDir);
  assert.notEqual(original.outputDir, selected);
  const settings = path.join(userData, 'song-browser', 'settings.json');

  // A directory occupying the final filename causes a real rename failure without global FS mocks.
  // Create it after initialization so the failure tests persistence, not initial config parsing.
  fs.mkdirSync(settings);
  const failed = await invoke('chooseOutput');
  assert.equal(failed.ok, false);
  assert.equal(typeof failed.error, 'string');
  assert.equal((await invoke('getState')).outputDir, original.outputDir);
  assert.equal(fs.statSync(settings).isDirectory(), true);
  assert.equal(fs.readdirSync(path.dirname(settings)).some((name) => /^settings-.*\.tmp$/.test(name)), false);

  fs.rmdirSync(settings);
  const retried = await invoke('chooseOutput');
  assert.equal(retried.outputDir, selected);
  assert.equal((await invoke('getState')).outputDir, selected);
  assert.equal(JSON.parse(fs.readFileSync(settings, 'utf8')).outputDir, selected);
  assert.equal(prompts[0].defaultPath, original.outputDir);
  assert.equal(prompts[1].defaultPath, original.outputDir, 'retry must still start from the previous saved choice');
});

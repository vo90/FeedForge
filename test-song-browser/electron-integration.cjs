'use strict';

// Run with the real Electron binary, never Node. All HTTPS/HTTP requests in the
// browser session are intercepted and synthesized here; none use net.fetch.
// Production browser origins, sandbox and download guards remain unchanged.
const { app, BrowserWindow, session, ipcMain } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { CustomsForgeBrowser, allowedNavigation } = require('../electron/song-browser/browser.cjs');
const { registerSongBrowser } = require('../electron/song-browser/index.cjs');
const { createDiagnostics } = require('../electron/song-browser/diagnostics.cjs');

const runtime = process.env.SONG_BROWSER_ELECTRON_RUNTIME;
if (!runtime || !path.isAbsolute(runtime)) throw new Error('Provide SONG_BROWSER_ELECTRON_RUNTIME as a fresh absolute test-output directory.');
const checkout = path.resolve(__dirname, '..');
const relative = path.relative(checkout, path.resolve(runtime));
if (!relative || (!relative.startsWith('..' + path.sep) && !path.isAbsolute(relative))) throw new Error('Test runtime must be outside the checkout.');
fs.mkdirSync(runtime, { recursive: true });
const receipt = path.join(runtime, 'electron-test-receipt.json');
fs.writeFileSync(receipt, JSON.stringify({ purpose: 'offline-song-browser-integration', source: checkout }), { flag: 'wx' });
for (const name of ['profile', 'session', 'temp', 'downloads']) fs.mkdirSync(path.join(runtime, name), { recursive: true });
app.setPath('userData', path.join(runtime, 'profile'));
app.setPath('sessionData', path.join(runtime, 'session'));
app.setPath('temp', path.join(runtime, 'temp'));
app.commandLine.appendSwitch('disable-background-networking');
app.commandLine.appendSwitch('disable-component-update');

const results = [];
const visits = new Map();
const transfers = [];
const unexpected = [];
const diagnostics = createDiagnostics({ appVersion: '0.1.40' });
let browser, integration, mainWindow, untrustedWindow;
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const deadline = async (promise, ms = 20000) => {
  let timer;
  try { return await Promise.race([promise, new Promise((_resolve, reject) => { timer = setTimeout(() => reject(new Error('Fixture timed out.')), ms); })]); }
  finally { clearTimeout(timer); }
};
const until = async (condition) => { for (let i = 0; i < 100; i++) { if (condition()) return; await sleep(50); } throw new Error('Fixture condition timed out.'); };
const psarc = Buffer.concat([Buffer.from('PSAR'), Buffer.alloc(65532, 7)]);
const html = (body) => new Response('<!doctype html><html><head><meta charset="UTF-8"><title>Offline fixture</title></head><body>' + body + '</body></html>', { headers: { 'Content-Type': 'text/html' } });
function searchPage(page = 1) {
  return html(`<p>Showing ${page} to ${page} of 2 results</p><table id="cdlc-table"><thead><tr><th>Download</th><th>Artist</th><th>Title</th><th>Album</th><th>Tuning</th><th>Creator</th><th>Parts</th><th>Version</th></tr></thead><tbody><tr><td><span title="Hosted on Dropbox">File</span></td><td>Fixture Artist</td><td><a href="/cdlc/${1000 + page}">Fixture Song ${page}</a></td><td>Fixture Album</td><td>E Standard</td><td>Fixture Creator</td><td>Lead</td><td>1</td></tr></tbody></table><span aria-current="page">${page}</span><button aria-label="Previous page" ${page === 1 ? 'disabled' : ''} onclick="location.href='/?search=fixture&amp;page=1'">Previous</button><button aria-label="Next page" ${page === 2 ? 'disabled' : ''} onclick="location.href='/?search=fixture&amp;page=2'">Next</button>`);
}
function fixtureResponse(request) {
  const url = new URL(request.url);
  const key = url.hostname + url.pathname + (url.searchParams.get('dl') === '1' ? '?dl=1' : '');
  visits.set(key, (visits.get(key) || 0) + 1);
  if (url.hostname === 'ignition4.customsforge.com') {
    if (url.pathname === '/') return searchPage(Number(url.searchParams.get('page') || 1));
    const detail = url.pathname.match(/^\/cdlc\/(100[1-5])$/);
    if (detail) {
      const id = detail[1];
      const expired = id === '1003' && visits.get(key) === 1;
      const expires = expired ? 1 : Math.floor(Date.now() / 1000) + 600;
      return html(`<a href="/user/collectedcdlcs/toggle/${id}?platform=pc&amp;expires=${expires}&amp;signature=fixture-only" ${id === '1002' ? 'target="_blank"' : ''} title="Hosted on ${id === '1002' ? 'MediaFire' : 'Dropbox'}">Windows</a>`);
    }
    const toggle = url.pathname.match(/^\/user\/collectedcdlcs\/toggle\/(100[1-5])$/);
    if (toggle) return Response.redirect(toggle[1] === '1002' ? 'https://www.mediafire.com/file/popup' : `https://www.dropbox.com/scl/fi/fixture/${toggle[1]}.psarc?rlkey=fixture-only&dl=0`, 302);
  }
  if (url.hostname === 'www.mediafire.com' && url.pathname === '/file/popup') return html('<a id="downloadButton" target="_blank" href="https://download1.mediafire.com/offline/popup.psarc">Download</a>');
  if (url.hostname === 'www.dropbox.com' && /^\/scl\/fi\/fixture\/100[1-5]\.psarc$/.test(url.pathname) && url.searchParams.get('dl') !== '1') return html('<p>Controlled Dropbox shared file</p>');
  if ((url.hostname === 'www.dropbox.com' && url.searchParams.get('dl') === '1') || url.hostname === 'download1.mediafire.com') {
    const slow = url.pathname.endsWith('/1004.psarc');
    const interrupted = url.pathname.endsWith('/1005.psarc');
    const headers = { 'Content-Type': 'application/octet-stream', 'Content-Disposition': 'attachment; filename="fixture.psarc"', 'Content-Length': String(psarc.length) };
    if (slow || interrupted) {
      let timer;
      const stream = new ReadableStream({
        start(controller) {
          controller.enqueue(psarc.subarray(0, 8192));
          if (interrupted) timer = setTimeout(() => controller.error(new Error('Intentional offline fixture interruption.')), 120);
          else timer = setTimeout(() => { controller.enqueue(psarc.subarray(8192)); controller.close(); }, 15000);
        },
        cancel() { clearTimeout(timer); }
      });
      return new Response(stream, { headers });
    }
    return new Response(psarc, { headers });
  }
  if (url.pathname === '/favicon.ico') return new Response(null, { status: 204 });
  unexpected.push(url.hostname + url.pathname);
  return new Response('Unexpected request blocked by offline fixture', { status: 403 });
}

async function test(name, run) {
  const started = Date.now();
  fs.appendFileSync(path.join(runtime, 'progress.txt'), 'START ' + name + '\n');
  await deadline(run());
  results.push({ name, status: 'passed', durationMs: Date.now() - started });
  process.stdout.write('PASS ' + name + '\n');
  fs.appendFileSync(path.join(runtime, 'progress.txt'), 'PASS ' + name + '\n');
}
async function download(id, options = {}) {
  const destination = path.join(runtime, 'downloads', id + '.psarc');
  return browser.download({ id, supported: true, host: id === '1002' ? 'mediafire' : 'dropbox' }, { destination, ...options });
}

async function run() {
  await app.whenReady();
  browser = new CustomsForgeBrowser({ BrowserWindow, session, profilePath: path.join(runtime, 'browser-profile'), onDiagnostic: diagnostics.record });
  await browser.session.protocol.handle('https', fixtureResponse);
  await browser.session.protocol.handle('http', () => new Response('HTTP disabled in fixture', { status: 403 }));
  browser.session.on('will-download', (_event, item, contents) => {
    transfers.push({ item, contents, completed: false });
    const transfer = transfers.at(-1);
    item.once('done', (_e, state) => { transfer.completed = true; transfer.state = state; });
  });

  await test('real sandboxed Chromium reads search and navigates ordinary pagination buttons', async () => {
    const first = await browser.search({ query: 'fixture' });
    assert.equal(first.status, 'ready'); assert.equal(first.results[0].id, '1001'); assert.equal(first.hasNext, true);
    const prefs = browser.searchWindow.webContents.getLastWebPreferences();
    assert.equal(prefs.sandbox, true); assert.equal(prefs.contextIsolation, true); assert.equal(prefs.nodeIntegration, false); assert.equal(prefs.webSecurity, true);
    assert.equal(await browser.searchWindow.webContents.executeJavaScript('typeof require'), 'undefined');
    const next = await browser.search({ query: 'fixture', page: 2 });
    assert.equal(next.page, 2); assert.equal(next.results[0].id, '1002'); assert.equal(next.hasNext, false);
    const previous = await browser.search({ query: 'fixture', page: 1 });
    assert.equal(previous.results[0].id, '1001');
    assert.equal(allowedNavigation('http://127.0.0.1:8000'), false);
  });
  await test('real DownloadItem follows signed-button redirect and shared-file download', async () => {
    const file = await download('1001');
    assert.deepEqual(fs.readFileSync(file), psarc);
    assert.equal(transfers.at(-1).state, 'completed');
    assert.equal(visits.get('ignition4.customsforge.com/user/collectedcdlcs/toggle/1001'), 1);
    assert.equal(browser.active, null);
  });
  await test('real nested popup download belongs to its job and is cleaned up', async () => {
    const before = BrowserWindow.getAllWindows().length;
    const file = await download('1002');
    assert.deepEqual(fs.readFileSync(file), psarc);
    assert.equal(transfers.at(-1).item.getURL(), 'https://download1.mediafire.com/offline/popup.psarc');
    assert.equal(BrowserWindow.getAllWindows().length, before);
  });
  await test('expired download button refreshes before exactly one collection click', async () => {
    await download('1003');
    assert.equal(visits.get('ignition4.customsforge.com/cdlc/1003'), 2);
    assert.equal(visits.get('ignition4.customsforge.com/user/collectedcdlcs/toggle/1003'), 1);
  });
  await test('real in-progress download cancellation awaits terminal state and permits another job', async () => {
    const controller = new AbortController();
    const pending = download('1004', { signal: controller.signal }).then(() => ({ error: null }), (error) => ({ error }));
    await until(() => browser.active?.item?.getReceivedBytes() > 0);
    controller.abort();
    const result = await pending;
    assert.match(result.error.message, /cancel/i);
    assert.equal(transfers.at(-1).completed, true); assert.equal(transfers.at(-1).state, 'cancelled');
    assert.equal(browser.active, null);
    await download('1001');
  });
  await test('real interrupted response fails without a completed file or active job', async () => {
    await assert.rejects(download('1005'), /interrupted|did not complete/i);
    assert.equal(transfers.at(-1).completed, true); assert.notEqual(transfers.at(-1).state, 'completed');
    assert.equal(browser.active, null);
  });
  await test('actual ipcMain boundary accepts the local app and rejects another renderer', async () => {
    const options = { show: false, webPreferences: { preload: path.join(__dirname, 'electron-preload.cjs'), sandbox: true, contextIsolation: true, nodeIntegration: false } };
    mainWindow = new BrowserWindow(options); untrustedWindow = new BrowserWindow(options);
    integration = registerSongBrowser({ app, BrowserWindow, session, ipcMain,
      dialog: {}, shell: {}, getMainWindow: () => mainWindow, runConverter: () => { throw new Error('Converter should not run in browser fixture.'); } });
    await Promise.all([mainWindow.loadFile(path.join(__dirname, 'electron-fixture.html')), untrustedWindow.loadFile(path.join(__dirname, 'electron-fixture.html'))]);
    const state = await mainWindow.webContents.executeJavaScript("window.fixture.invoke('song-browser:getState')");
    assert.equal(state.connection.status, 'signed_out'); assert.deepEqual(state.jobs, []);
    await assert.rejects(untrustedWindow.webContents.executeJavaScript("window.fixture.invoke('song-browser:getState')"), /requests must come from FeedForge/);
  });
  assert.deepEqual(unexpected, [], 'Every fixture request must be accounted for.');
}

const watchdog = setTimeout(() => { process.stderr.write('Offline Electron fixture watchdog expired.\n'); app.exit(1); }, 120000);
run().then(async () => {
  browser?.dispose(); await integration?.close();
  const result = { status: 'passed', evidence: 'Actual Electron BrowserWindow, Chromium DOM and DownloadItem with fully synthetic intercepted responses. No live websites, credentials or conversion.', electron: process.versions.electron,
    tests: results, downloadItems: transfers.map(({ state, completed }) => ({ state, terminalEventObserved: completed })), unexpectedRequestCount: unexpected.length, diagnostics: diagnostics.report() };
  fs.writeFileSync(path.join(runtime, 'result.json'), JSON.stringify(result, null, 2));
  process.stdout.write(JSON.stringify({ status: 'passed', tests: results.length, report: path.join(runtime, 'result.json') }) + '\n');
  clearTimeout(watchdog); app.exit(0);
}).catch(async (error) => {
  browser?.dispose(); await integration?.close();
  fs.writeFileSync(path.join(runtime, 'result.json'), JSON.stringify({ status: 'failed', tests: results, error: error.stack, visits: [...visits], transfers: transfers.map(({ state, completed }) => ({ state, completed })), unexpected }, null, 2));
  process.stderr.write(error.stack + '\n'); clearTimeout(watchdog); app.exit(1);
});

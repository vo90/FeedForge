'use strict';

// Offline transfer-lifecycle evidence only. The actual production browser and
// DOM functions run against generated .test documents. Test-only require
// wrappers translate those origins for pure policy checks and lexically bind
// location for DOM recognition; no actual document ever visits MEGA or CF.
const { app, BrowserWindow, session } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const assert = require('node:assert/strict');
const Module = require('node:module');
const runtime = process.env.SONG_BROWSER_MEGA_RUNTIME;
if (!runtime || !path.isAbsolute(runtime)) throw new Error('Provide a fresh absolute SONG_BROWSER_MEGA_RUNTIME.');
const sourceRoot = path.resolve(__dirname, '..');
const relative = path.relative(sourceRoot, runtime);
if (!relative || (!relative.startsWith('..' + path.sep) && !path.isAbsolute(relative))) throw new Error('The fixture runtime must be outside source.');
fs.writeFileSync(path.join(runtime, 'ownership.json'), JSON.stringify({ purpose: 'offline-generated-mega-lifecycle-fixture', sourceRoot }), { flag: 'wx' });
for (const name of ['profile', 'session', 'downloads']) fs.mkdirSync(path.join(runtime, name), { recursive: true });
app.setPath('userData', path.join(runtime, 'profile'));
app.setPath('sessionData', path.join(runtime, 'session'));
app.setPath('temp', path.join(runtime, 'temp'));
app.commandLine.appendSwitch('disable-background-networking');
app.commandLine.appendSwitch('disable-component-update');
app.commandLine.appendSwitch('host-resolver-rules', 'MAP * ~NOTFOUND');
app.on('window-all-closed', () => {});
const TEST_ORIGIN = 'https://mega-proof.test';
const names = { pc: 'Fixture_Generated_p.psarc', mac: 'Fixture_Generated_m.psarc', second: 'Fixture_Generated_v2_p.psarc', wrong: 'Unexpected_p.psarc' };
const evidence = { tests: [], transfers: [], progress: [], blocked: [], requests: [], titles: [] };
const presentations = [];
let browser, foreignWindow, activeMode = 'immediate';
const checksum = (value) => crypto.createHash('sha256').update(value).digest('hex');
const sourceFiles = ['browser.cjs', 'hosts.cjs', 'host-actions.cjs', 'mega-actions.cjs', 'file-selection.cjs'];
const sourceHashes = Object.fromEntries(sourceFiles.map((name) => [name, checksum(fs.readFileSync(path.join(sourceRoot, 'electron', 'song-browser', name)))]));
app.on('browser-window-created', (_event, win) => {
  const item = { initiallyHidden: !win.isVisible(), shows: 0, focuses: 0, visibleEvents: 0 };
  presentations.push(item);
  win.show = win.showInactive = () => { item.shows++; };
  win.focus = () => { item.focuses++; };
  win.on('show', () => { item.visibleEvents++; win.hide(); });
  win.webContents.on('page-title-updated', (_event, title) => evidence.titles.push(title));
});
const pause = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));
async function until(check, label, milliseconds = 12000) {
  const end = Date.now() + milliseconds;
  while (!check()) { if (Date.now() >= end) throw new Error('Timed out: ' + label); await pause(30); }
}
async function bounded(promise, label, milliseconds = 18000) {
  let timer;
  try { return await Promise.race([promise, new Promise((_resolve, reject) => { timer = setTimeout(() => reject(new Error('Timed out: ' + label)), milliseconds); })]); }
  finally { clearTimeout(timer); }
}
const virtualUrl = (value) => typeof value === 'string' ? value.replace(/^https:\/\/mega-proof\.test(?=\/|$)/, 'https://mega.nz').replace(/^blob:https:\/\/mega-proof\.test\//, 'blob:https://mega.nz/') : value;
function virtualContext(context = {}) {
  return { ...context, documentUrl: virtualUrl(context.documentUrl), origin: virtualUrl(context.origin) };
}
function loadProductionBrowser() {
  const filename = path.join(sourceRoot, 'electron', 'song-browser', 'browser.cjs');
  const productionRequire = Module.createRequire(filename);
  const policy = productionRequire('./hosts.cjs');
  const actions = productionRequire('./host-actions.cjs');
  const withLocation = (script) => `(() => { const location = { href: window.location.href.replace('https://mega-proof.test', 'https://mega.nz'), protocol: 'https:', port: '', hostname: 'mega.nz', host: 'mega.nz', origin: 'https://mega.nz', pathname: window.location.pathname, search: window.location.search, hash: window.location.hash }; return ${script}; })()`;
  const aliases = {
    './hosts.cjs': { ...policy, hostFromUrl: (url) => policy.hostFromUrl(virtualUrl(url)),
      allowedNavigation: (url, context) => policy.allowedNavigation(virtualUrl(url), virtualContext(context)),
      allowedDownload: (url, name, bytes, context) => policy.allowedDownload(virtualUrl(url), name, bytes, virtualContext(context)) },
    './host-actions.cjs': { ...actions, hostActionScript: (request) => withLocation(actions.hostActionScript(request)), hostCancelScript: () => withLocation(actions.hostCancelScript()) },
  };
  const loaded = new Module(filename, module);
  loaded.filename = filename;
  loaded.paths = Module._nodeModulePaths(path.dirname(filename));
  loaded.require = (name) => Object.hasOwn(aliases, name) ? aliases[name] : productionRequire(name);
  loaded._compile(fs.readFileSync(filename, 'utf8'), filename);
  return loaded.exports.CustomsForgeBrowser;
}

function expectedBytes(filename) {
  return Buffer.concat([Buffer.from('PSAR'), Buffer.from(filename + '\n'), Buffer.alloc(65536, 37)]);
}
function fixtureDocument(mode) {
  function fixtureScript(mode, names) {
    const escape = (value) => String(value).replace(/[&<>"']/g, (character) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[character]));
    let selected = names.pc, timer, phaseTimer;
    const filenameMarkup = (filename) => `<span class="name">${escape(filename.slice(0, -6))}</span><span class="ext">.psarc</span>`;
    const payload = (filename) => {
      const prefix = new TextEncoder().encode('PSAR' + filename + '\n');
      const bytes = new Uint8Array(prefix.length + 65536); bytes.set(prefix); bytes.fill(37, prefix.length); return bytes;
    };
    function emit(filename) {
      const blob = new Blob([payload(filename)], { type: 'application/octet-stream' });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a'); anchor.href = url; anchor.download = filename;
      document.body.append(anchor); anchor.click(); anchor.remove();
      setTimeout(() => URL.revokeObjectURL(url), 4000);
    }
    const progressMarkup = () => `<div class="dl-widget progress" hidden><div class="filename">${filenameMarkup(selected)}</div><div class="status">Downloading</div><div class="progress-bar"><div class="bar" style="width:25%"></div></div><div class="cols"><div class="actions"><button class="cancel-transfer" title="Cancel transfer"><i class="icon-dialog-close-thin"></i></button></div></div></div>`;
    function begin() {
      const widget = document.querySelector('.dl-widget.progress');
      if (widget) { widget.hidden = false; widget.querySelector('.filename').innerHTML = filenameMarkup(selected); }
      document.title = 'Fixture transfer started';
      if (mode === 'browser-choice') {
        document.querySelector('.dl-header-container').hidden = true;
        widget.querySelector('.status').textContent = 'Initializing';
        const dialog = document.createElement('div'); dialog.setAttribute('role', 'dialog');
        const desktop = document.createElement('button'); desktop.textContent = 'Download with the desktop app'; desktop.onclick = () => { document.title = 'Unexpected desktop action'; };
        const browser = document.createElement('button'); browser.textContent = 'Download through your browser'; browser.onclick = () => {
          document.title = 'Fixture browser choice accepted'; widget.querySelector('.status').textContent = 'Downloading';
          phaseTimer = setTimeout(() => { widget.querySelector('.status').textContent = 'Decrypting'; widget.querySelector('.bar').style.width = '75%'; }, 800);
          timer = setTimeout(() => emit(selected), 1900);
        };
        dialog.append(desktop, browser); document.body.append(dialog); return;
      }
      if (mode === 'hold' || mode === 'foreign-owner') return;
      if (mode === 'progress') {
        phaseTimer = setTimeout(() => { widget.querySelector('.status').textContent = 'Decrypting'; widget.querySelector('.bar').style.width = '75%'; }, 800);
        timer = setTimeout(() => emit(selected), 1900);
      } else emit(mode === 'wrong-name' ? names.wrong : selected);
    }
    function cancel() {
      clearTimeout(timer); clearTimeout(phaseTimer);
      document.title = 'Fixture transfer cancelled';
      const widget = document.querySelector('.dl-widget.progress');
      if (widget) { widget.querySelector('.status').textContent = 'Cancelled'; widget.hidden = true; }
    }
    if (mode === 'folder' || mode === 'multiple') {
      const files = mode === 'folder' ? [names.mac, names.pc] : [names.pc, names.second];
      document.body.innerHTML = `<div class="fmholder"><table class="grid-scrolling-table" aria-rowcount="${files.length}"><tbody>${files.map((filename, index) => `<tr id="row-${index}" data-filename="${escape(filename)}"><td><span class="tranfer-filetype-txt">${escape(filename)}</span><span class="file-size">64 KB</span><button class="grid-url-arrow" aria-label="File actions">Actions</button></td></tr>`).join('')}</tbody></table><div class="context-menu" hidden><button class="download-standart-item">Standard download</button></div></div>${progressMarkup()}`;
      for (const row of document.querySelectorAll('tr')) {
        row.addEventListener('click', () => { for (const item of document.querySelectorAll('tr')) item.classList.remove('ui-selected'); row.classList.add('ui-selected'); selected = row.getAttribute('data-filename'); });
        row.querySelector('.grid-url-arrow').addEventListener('click', () => { selected = row.getAttribute('data-filename'); document.querySelector('.context-menu').hidden = false; });
      }
      document.querySelector('.download-standart-item').addEventListener('click', begin);
    } else {
      document.body.innerHTML = `<div class="download-page"><div class="dl-header-container"><div class="dl-header"><div class="fileinfo"><div class="filename">${filenameMarkup(selected)}</div><div class="size">64 KB</div></div><div class="actions"><button class="download-button">Download</button></div></div></div>${progressMarkup()}</div>`;
      document.querySelector('.download-button').addEventListener('click', begin);
    }
    document.querySelector('.cancel-transfer').addEventListener('click', cancel);
    if (mode === 'unarmed') emit(names.pc);
    if (mode === 'foreign') {
      const button = document.createElement('button'); button.id = 'foreign-download'; button.textContent = 'Fixture foreign Blob'; button.onclick = () => emit(names.pc); document.body.append(button);
    }
  }
  return new Response(`<!doctype html><html><head><meta charset="UTF-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'none'; connect-src 'none'; frame-src 'none'"><title>Offline generated transfer</title></head><body><script>(${fixtureScript.toString()})(${JSON.stringify(mode)}, ${JSON.stringify(names)})</script></body></html>`, { headers: { 'Content-Type': 'text/html' } });
}
async function lockNetwork(target, allowFixtures) {
  target.webRequest.onBeforeRequest({ urls: ['<all_urls>'] }, (details, callback) => {
    const url = details.url;
    const allowed = allowFixtures && (url.startsWith(TEST_ORIGIN + '/') || url.startsWith('blob:' + TEST_ORIGIN + '/'));
    if (!allowed) evidence.blocked.push({ layer: 'webRequest', url });
    callback({ cancel: !allowed });
  });
  for (const scheme of ['http', 'https']) await target.protocol.handle(scheme, (request) => {
    const url = new URL(request.url);
    if (!allowFixtures || url.origin !== TEST_ORIGIN || !['/case/', '/foreign'].some((prefix) => url.pathname.startsWith(prefix))) {
      evidence.blocked.push({ layer: 'protocol', url: request.url }); return new Response('Offline fixture blocked this request.', { status: 403 });
    }
    const previousVisits = evidence.requests.filter((value) => value === request.url).length;
    evidence.requests.push(request.url);
    const mode = url.pathname === '/foreign' ? 'foreign' : url.pathname === '/case/replaced-document' ? previousVisits ? 'unarmed' : 'hold' : url.pathname.slice('/case/'.length);
    return fixtureDocument(mode);
  });
}
function start(mode, options = {}) {
  activeMode = mode;
  const destination = path.join(runtime, 'downloads', mode + '-' + (evidence.tests.length + 1) + '.psarc');
  const progress = [];
  const attention = [];
  const completion = browser.download({ id: String(9000 + evidence.tests.length), supported: true, host: 'mega' }, { destination, ...options,
    onProgress(percent, details) { const event = { mode, percent, phase: details?.phase || null, beforeDownloadItem: !browser.active?.item }; progress.push(event); evidence.progress.push(event); options.onProgress?.(percent, details); },
    onAttention(value) { attention.push(value); options.onAttention?.(value); },
  }).then((value) => ({ value }), (error) => ({ error }));
  return { destination, completion, progress, attention };
}
async function test(name, run) {
  const started = Date.now();
  fs.appendFileSync(path.join(runtime, 'progress.txt'), 'START ' + name + '\n');
  await bounded(run(), name);
  evidence.tests.push({ name, status: 'passed', durationMs: Date.now() - started });
  process.stdout.write('PASS ' + name + '\n');
}
async function run() {
  await app.whenReady();
  assert.equal(process.versions.electron, '43.0.0');
  await lockNetwork(session.defaultSession, false);
  const ProductionBrowser = loadProductionBrowser();
  browser = new ProductionBrowser({ BrowserWindow, session, profilePath: path.join(runtime, 'provider-profile') });
  await lockNetwork(browser.session, true);
  browser.navigate = async (win, sourceUrl) => {
    assert.match(sourceUrl, /^https:\/\/ignition4\.customsforge\.com\/cdlc\/\d+$/);
    await win.loadURL(TEST_ORIGIN + '/case/' + activeMode);
  };
  browser.session.on('will-download', (event, item, contents) => {
    const record = { filename: item.getFilename(), url: item.getURL(), rejected: event.defaultPrevented, ownedWindow: !!browser.active && [...browser.active.windows].some((win) => !win.isDestroyed() && win.webContents === contents) };
    evidence.transfers.push(record);
    if (!event.defaultPrevented) item.once('done', (_event, state) => { record.state = state; });
  });
  await test('prepared filename attestation captures an immediate hidden Blob with exact generated bytes', async () => {
    const job = start('immediate'); const result = await job.completion;
    assert.ifError(result.error); assert.deepEqual(fs.readFileSync(result.value), expectedBytes(names.pc));
    assert.equal(evidence.transfers.at(-1).filename, names.pc); assert.equal(evidence.transfers.at(-1).state, 'completed');
    assert.equal(browser.active, null);
  });
  await test('provider progress and decryption reach FeedForge before a DownloadItem exists', async () => {
    const job = start('progress'); const result = await job.completion;
    assert.ifError(result.error); assert.deepEqual(fs.readFileSync(result.value), expectedBytes(names.pc));
    assert.ok(job.progress.some((entry) => entry.beforeDownloadItem && entry.phase === 'downloading' && entry.percent > 0));
    assert.ok(job.progress.some((entry) => entry.beforeDownloadItem && entry.phase === 'decrypting'));
    assert.ok(job.progress.every((entry, index) => Number.isFinite(entry.percent) && entry.percent >= 0 && entry.percent <= 100 && (!index || entry.percent >= job.progress[index - 1].percent)));
    assert.equal(job.progress.at(-1).percent, 100);
    assert.equal(job.attention.filter(Boolean).length, 0);
  });
  await test('a browser choice after initialization delivers the file without invoking the desktop app', async () => {
    const before = evidence.titles.length; const job = start('browser-choice'); const result = await job.completion;
    assert.ifError(result.error); assert.deepEqual(fs.readFileSync(result.value), expectedBytes(names.pc));
    assert.ok(evidence.titles.slice(before).includes('Fixture browser choice accepted'));
    assert.equal(evidence.titles.includes('Unexpected desktop action'), false);
    assert.ok(job.progress.some((entry) => entry.beforeDownloadItem && entry.phase === 'downloading' && entry.percent > 0), 'The still-visible clicked browser choice cannot suppress transfer progress.');
    assert.ok(job.progress.some((entry) => entry.beforeDownloadItem && entry.phase === 'decrypting'));
  });
  await test('a Blob with a different filename is rejected without publishing a file', async () => {
    const job = start('wrong-name'); const result = await job.completion;
    assert.ok(result.error); assert.equal(fs.existsSync(job.destination), false);
    assert.equal(evidence.transfers.at(-1).filename, names.wrong); assert.equal(evidence.transfers.at(-1).rejected, true);
  });
  await test('an owned document cannot deliver a Blob before its selected file is prepared', async () => {
    const job = start('unarmed'); const result = await job.completion;
    assert.ok(result.error); assert.equal(fs.existsSync(job.destination), false); assert.equal(evidence.transfers.at(-1).rejected, true);
  });
  await test('a same-origin foreign window cannot deliver the active job selected filename', async () => {
    const controller = new AbortController(); const job = start('foreign-owner', { signal: controller.signal });
    try {
      await until(() => browser.active?.megaPrepared && evidence.titles.includes('Fixture transfer started'), 'owned provider is armed');
      foreignWindow = browser.createWindow(); await foreignWindow.loadURL(TEST_ORIGIN + '/foreign');
      await foreignWindow.webContents.mainFrame.executeJavaScript("document.querySelector('#foreign-download').click()", true);
      await until(() => evidence.transfers.at(-1)?.ownedWindow === false, 'foreign Blob rejection');
      assert.equal(evidence.transfers.at(-1).rejected, true); assert.equal(browser.active.finished, false); assert.equal(browser.active.item, null);
    } finally { foreignWindow?.destroy(); foreignWindow = null; controller.abort(); }
    const result = await job.completion; assert.match(result.error?.message || '', /cancel/i); assert.equal(fs.existsSync(job.destination), false);
  });
  await test('cancellation before Blob delivery uses the provider cancel action and releases the job', async () => {
    const controller = new AbortController(); const before = evidence.titles.length; const job = start('hold', { signal: controller.signal });
    await until(() => evidence.titles.slice(before).includes('Fixture transfer started'), 'provider transfer starts');
    controller.abort(); const result = await job.completion;
    assert.match(result.error?.message || '', /cancel/i); assert.equal(fs.existsSync(job.destination), false); assert.equal(browser.active, null);
    assert.ok(evidence.titles.slice(before).includes('Fixture transfer cancelled'), 'The actual provider cancel button must be clicked before closing the document.');
    const recovered = start('immediate'); assert.ifError((await recovered.completion).error);
  });
  await test('a folder containing PC and Mac variants selects and transfers the PC file', async () => {
    const job = start('folder'); const result = await job.completion;
    assert.ifError(result.error); assert.deepEqual(fs.readFileSync(result.value), expectedBytes(names.pc)); assert.equal(evidence.transfers.at(-1).filename, names.pc);
  });
  await test('reloading the same URL invalidates the previous document Blob attestation', async () => {
    const controller = new AbortController(); const before = evidence.titles.length; const job = start('replaced-document', { signal: controller.signal });
    try {
      await until(() => browser.active?.megaPrepared && evidence.titles.slice(before).includes('Fixture transfer started'), 'initial document owns a prepared transfer');
      const oldPrepared = browser.active.megaPrepared;
      const win = [...browser.active.windows][0];
      win.webContents.reload();
      const result = await job.completion;
      assert.ok(result.error); assert.equal(fs.existsSync(job.destination), false); assert.equal(evidence.transfers.at(-1).rejected, true);
      assert.equal(oldPrepared.documentUrl, TEST_ORIGIN + '/case/replaced-document', 'The URL remains identical across replacement.');
    } finally { if (browser.active) controller.abort(); await job.completion; }
  });
  await test('multiple PC variants wait for an in-app choice and then transfer the chosen filename', async () => {
    const controller = new AbortController(); const job = start('multiple', { signal: controller.signal });
    try {
      await until(() => browser.active?.candidates?.length === 2, 'two PC file candidates');
      assert.equal(browser.active.item, null);
      const candidate = browser.active.candidates.find((value) => value.label === names.second); assert.ok(candidate);
      browser.chooseFile({ id: candidate.id });
      const result = await job.completion; assert.ifError(result.error); assert.deepEqual(fs.readFileSync(result.value), expectedBytes(names.second));
    } finally { if (browser.active) controller.abort(); await job.completion; }
  });
  await test('a file choice from an earlier same-URL document cannot activate a replacement row', async () => {
    const controller = new AbortController(); const job = start('multiple', { signal: controller.signal });
    try {
      await until(() => browser.active?.candidates?.length === 2, 'initial PC choices');
      const candidate = browser.active.candidates[0];
      const win = [...browser.active.windows][0];
      const loaded = new Promise((resolve) => win.webContents.once('did-finish-load', resolve));
      win.webContents.reload(); await loaded;
      assert.throws(() => browser.chooseFile({ id: candidate.id }), /expired|changed|refresh|choose|read/i);
      assert.equal(browser.active.item, null); assert.equal(fs.existsSync(job.destination), false);
    } finally { controller.abort(); await job.completion; }
  });
  assert.deepEqual(evidence.blocked, [], 'No external network attempt is expected; every document uses a synthetic .test response.');
  assert.ok(evidence.requests.length > 0 && evidence.requests.every((url) => new URL(url).origin === TEST_ORIGIN));
  assert.ok(evidence.transfers.every((item) => item.url.startsWith('blob:' + TEST_ORIGIN + '/')));
  assert.ok(presentations.every((entry) => entry.initiallyHidden && !entry.shows && !entry.focuses && !entry.visibleEvents));
  assert.deepEqual(Object.fromEntries(sourceFiles.map((name) => [name, checksum(fs.readFileSync(path.join(sourceRoot, 'electron', 'song-browser', name)))])), sourceHashes, 'Production source changed during verification.');
}
const watchdog = setTimeout(() => { process.stderr.write('Offline MEGA fixture watchdog expired.\n'); app.exit(1); }, 125000);
async function finish(error) {
  browser?.dispose(); foreignWindow?.destroy(); clearTimeout(watchdog);
  const result = { status: error ? 'failed' : 'passed', electron: process.versions.electron, evidence: 'Production CustomsForgeBrowser lifecycle and production MEGA DOM actions with test-only origin aliases. Generated Blob bytes and .test HTML only; no live MEGA/CustomsForge requests, accounts, credentials, real song conversion or live-host compatibility claim.', sourceHashes, network: { dnsRule: 'MAP * ~NOTFOUND', protocolsIntercepted: ['http', 'https'], nonFixtureRequestsDenied: true }, presentations, ...evidence, ...(error ? { error: error.stack || String(error) } : {}) };
  fs.writeFileSync(path.join(runtime, 'result.json'), JSON.stringify(result, null, 2));
  process.stdout.write(JSON.stringify({ status: result.status, tests: evidence.tests.length, report: path.join(runtime, 'result.json') }) + '\n');
  if (error) process.stderr.write(error.stack + '\n'); app.exit(error ? 1 : 0);
}
run().then(() => finish(), (error) => finish(error));

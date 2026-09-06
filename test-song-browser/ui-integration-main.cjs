'use strict';

// Test Electron main: production UI/preload/IPC/jobs/browser, synthetic hosts
// and converter only. A root-owned hash-checked launch receipt gates all
// window/session creation. No real account, library, game, server or dialogs.
const { app, BrowserWindow, session, ipcMain } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const assert = require('node:assert/strict');

const charts = Object.freeze({
  // The catalogue advertises Rhythm as well; the inspected fixture contains
  // only Lead, exercising verified arrangement coverage rather than metadata.
  fast: Object.freeze({ id: '1101', title: 'Fixture Success', artist: 'Fixture Artist', host: 'dropbox', parts: 'Lead, Rhythm' }),
  retry: Object.freeze({ id: '1102', title: 'Fixture Recovery', artist: 'Fixture Artist', host: 'google-drive' }),
  slow: Object.freeze({ id: '1103', title: 'Fixture Cancel', artist: 'Fixture Artist', host: 'mediafire' }),
});
// Compact rows reproduce Ignition's adjacent one-letter arrangement
// badges, including absent badges retained as hidden DOM content. Keep these
// separate from the original catalogue and transfer counts. Lead-only also
// has a controlled download to verify OR requirements through conversion.
const compactCharts = Object.freeze({
  all: Object.freeze({ id: '1140', title: 'Leaders Of The Blind', artist: 'Green Lung', host: 'dropbox', parts: 'LRB', compactParts: true }),
  lead: Object.freeze({ id: '1141', title: 'Fixture Lead Only', artist: 'Green Lung', host: 'dropbox', parts: 'L', compactParts: true }),
  rhythm: Object.freeze({ id: '1142', title: 'Fixture Rhythm Only', artist: 'Green Lung', host: 'dropbox', parts: 'R', compactParts: true }),
  bass: Object.freeze({ id: '1143', title: 'Fixture Bass Only', artist: 'Green Lung', host: 'dropbox', parts: 'B', compactParts: true }),
});
const officialCharts = Object.freeze({
  official: Object.freeze({ id: '1150', title: 'Fixture Official Song', artist: 'Fixture Artist', host: 'unknown', official: true }),
  custom: Object.freeze({ id: '1151', title: 'ODLC Custom Tribute', artist: 'Fixture Artist', host: 'dropbox' }),
  unknown: Object.freeze({ id: '1152', title: 'Fixture Unidentified Host', artist: 'Fixture Artist', host: 'unknown' }),
});
const byId = new Map([...Object.values(charts), compactCharts.lead].map((chart) => [chart.id, chart]));
const counters = { search: 0, searchCompleted: 0, downloads: {}, conversions: {}, plans: [], outputSettings: null, outputPicks: [], songBrowserFolderPicks: 0, reveals: 0, browserActions: { signIn: 0, showBrowser: 0 } };
const tests = [], rendererErrors = [], unexpected = [], transfers = [], savedReports = [];
const conversionFailures = new Map();
let runtime, receipt, mainWindow, registration, searchGate, scenarioEvidence, currentOutput = 'first';
let closing = false, watchdog;
const waiters = new Set();
const defer = () => { let resolve; const promise = new Promise((done) => { resolve = done; }); return { promise, resolve }; };
const canonical = (value) => fs.realpathSync.native(value);
const inside = (root, value) => { const relative = path.relative(root, value); return relative === '' || (relative !== '..' && !relative.startsWith('..' + path.sep) && !path.isAbsolute(relative)); };
const sha256 = (filename) => crypto.createHash('sha256').update(fs.readFileSync(filename)).digest('hex');
function fileList(directory, prefix = '') {
  return fs.readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const relative = prefix + entry.name;
    assert.equal(entry.isSymbolicLink(), false, 'Fixture files cannot be symbolic links.');
    if (entry.isDirectory()) return fileList(path.join(directory, entry.name), relative + '/');
    assert.equal(entry.isFile(), true, 'Only regular fixture files are allowed.');
    return [relative];
  });
}
function verifyFiles(root, hashes) {
  assert.ok(hashes && typeof hashes === 'object' && !Array.isArray(hashes), 'The receipt must contain file hashes.');
  for (const [relative, hash] of Object.entries(hashes)) {
    assert.ok(relative && relative.split('/').every((part) => part && part !== '.' && part !== '..') && !relative.includes('\\') && !path.isAbsolute(relative), 'Invalid relative receipt path.');
    assert.match(hash, /^[a-f0-9]{64}$/);
    const filename = path.join(root, ...relative.split('/'));
    assert.ok(inside(root, canonical(filename)), 'A hashed file escapes its receipt root.');
    assert.ok(fs.lstatSync(filename).isFile() && !fs.lstatSync(filename).isSymbolicLink());
    assert.equal(sha256(filename), hash, 'Receipt hash mismatch: ' + relative);
  }
}
function verifyReceipt() {
  const candidate = process.env.SONG_BROWSER_UI_RUNTIME;
  assert.ok(candidate && path.isAbsolute(candidate), 'SONG_BROWSER_UI_RUNTIME must be an absolute path.');
  const resolved = canonical(candidate);
  const source = canonical(path.resolve(__dirname, '..'));
  assert.equal(inside(source, resolved), false, 'The UI runtime must be outside the checkout.');
  const value = JSON.parse(fs.readFileSync(path.join(resolved, 'launch-receipt.json'), 'utf8'));
  assert.equal(value.kind, 'feedforge-song-browser-ui');
  assert.equal(canonical(value.runtime), resolved); assert.equal(canonical(value.sourceRoot), source);
  assert.match(value.revision, /^[a-f0-9]{40}$/);
  assert.equal(canonical(value.electron.path), canonical(process.execPath));
  assert.equal(value.electron.version, process.versions.electron);
  assert.equal(value.electron.sha256, sha256(process.execPath), 'Electron executable hash mismatch.');
  const required = ['electron/preload.cjs', ...['index', 'browser', 'dom', 'host-actions', 'jobs', 'diagnostics', 'feedback'].map((name) => `electron/song-browser/${name}.cjs`),
    'test-song-browser/ui-integration-main.cjs', 'test-song-browser/ui-integration-scenarios.cjs'];
  for (const relative of required) assert.ok(Object.hasOwn(value.sourceHashes || {}, relative), 'Missing required source hash: ' + relative);
  verifyFiles(source, value.sourceHashes);
  const renderer = canonical(path.join(resolved, 'ui'));
  assert.ok(inside(resolved, renderer)); assert.ok(Object.hasOwn(value.rendererHashes || {}, 'index.html'));
  verifyFiles(renderer, value.rendererHashes);
  assert.deepEqual(fileList(renderer).sort(), Object.keys(value.rendererHashes).sort(), 'Renderer contains unverified files.');
  return { runtime: resolved, receipt: value };
}
function signalWaiters() {
  for (const waiter of [...waiters]) if (waiter.ready()) { waiters.delete(waiter); waiter.resolve(); }
}
function waitFor(ready, label) {
  if (ready()) return Promise.resolve();
  let timer;
  return new Promise((resolve, reject) => {
    const waiter = { ready, resolve: () => { clearTimeout(timer); resolve(); } };
    waiters.add(waiter);
    timer = setTimeout(() => { waiters.delete(waiter); reject(new Error('Timed out waiting for ' + label)); }, 15000);
  });
}
function fixturePsarc(chart) {
  return Buffer.concat([Buffer.from('PSAR'), Buffer.from(JSON.stringify({ ...chart, padding: 'offline fixture only '.repeat(500) }))]);
}
function previewFrom(filename) {
  assert.ok(inside(runtime, canonical(filename)), 'Converter input escaped the fixture runtime.');
  const bytes = fs.readFileSync(filename);
  const feedpak = bytes.subarray(0, 19).toString() === 'PK fixture FeedPak ';
  if (!feedpak) assert.equal(bytes.subarray(0, 4).toString(), 'PSAR');
  const data = JSON.parse(bytes.subarray(feedpak ? 19 : 4).toString());
  const chart = byId.get(data.id); assert.ok(chart); assert.equal(data.title, chart.title); assert.equal(data.artist, chart.artist);
  // These controlled source files contain only Lead. That is deliberately
  // insufficient for AND Lead + Rhythm, and sufficient for the OR regression.
  return { chart, preview: { title: chart.title, artist: chart.artist, album: 'Offline Fixture Album', song_count: 1, is_multi_song: false,
    source_platforms: feedpak ? [] : ['pc'], arrangements: [{ id: 'lead', type: 'guitar', name: 'Lead', tuning: [0, 0, 0, 0, 0, 0] }], warnings: [], duration: 120 } };
}
async function runConverter(args) {
  if (args[0] === '--inspect-json') return { code: 0, stderr: '', stdout: JSON.stringify({ ok: true, preview: previewFrom(args[1]).preview }) };
  if (args[0] === '--plan-conversion-file') {
    assert.equal(args.length, 2);
    const request = JSON.parse(fs.readFileSync(ownedFile(args[1]), 'utf8'));
    assert.equal(request.items?.length, 1); assert.equal(request.overwrite, false);
    assert.ok(inside(runtime, canonical(request.outputDir)));
    assert.ok(['flat', 'preserve', 'artist'].includes(request.outputLayout));
    const inputPath = ownedFile(request.items[0].inputPath);
    const { chart, preview } = previewFrom(inputPath);
    const values = { source: path.basename(inputPath, path.extname(inputPath)), artist: preview.artist, title: preview.title, album: preview.album, year: '2020', parts: 'L' };
    assert.ok(typeof request.nameTemplate === 'string' && request.nameTemplate.length > 0);
    const filename = request.nameTemplate.replace(/\{(artist|title|album|year|source|parts)\}/gi, (_match, key) => values[key.toLowerCase()]) + '.feedpak';
    assert.equal(path.basename(filename), filename, 'Fixture templates cannot escape their output folder.');
    const folder = path.join(request.outputDir, ...(request.outputLayout === 'artist' ? [preview.artist] : []));
    let target = path.join(folder, filename), suffix = 2;
    while (fs.existsSync(target)) target = path.join(folder, path.basename(filename, '.feedpak') + ` (${suffix++}).feedpak`);
    const stat = fs.statSync(inputPath, { bigint: true });
    counters.plans.push({ chartId: chart.id, outputLayout: request.outputLayout, nameTemplate: request.nameTemplate, sourceName: path.basename(inputPath), relativePath: path.relative(request.outputDir, target).replace(/\\/g, '/') });
    return { code: 0, stderr: '', stdout: JSON.stringify({ ok: true, total: 1, planned: 1, failed: 0, cached: 0, workers: 1, durationMs: 0,
      items: [{ ok: true, inputPath, sourceSize: Number(stat.size), sourceMtimeNs: String(stat.mtimeNs),
        outputs: [{ key: chart.id, path: target, artist: preview.artist, title: preview.title, album: preview.album, year: values.year, parts: values.parts }] }] }) };
  }
  if (args[0] === '--validate-feedpak') {
    assert.ok(inside(runtime, canonical(args[1])));
    assert.match(fs.readFileSync(args[1], 'utf8'), /^PK fixture FeedPak /);
    return { code: 0, stderr: '', stdout: JSON.stringify({ ok: true, results: [{ input_path: args[1], validation: { ok: true, errors: [], warnings: [] } }] }) };
  }
  assert.equal(args.length, 3); assert.equal(args[1], '-o');
  assert.ok(inside(runtime, canonical(path.dirname(args[2]))));
  const { chart } = previewFrom(args[0]); counters.conversions[chart.id] = (counters.conversions[chart.id] || 0) + 1;
  if (conversionFailures.get(chart.id)) {
    conversionFailures.set(chart.id, conversionFailures.get(chart.id) - 1);
    return { code: 1, stdout: '', stderr: 'Intentional offline fixture conversion failure. Retry the retained PSARC.' };
  }
  fs.writeFileSync(args[2], 'PK fixture FeedPak ' + JSON.stringify(chart), { flag: 'wx' });
  return { code: 0, stdout: 'Synthetic conversion complete.', stderr: '' };
}
const html = (body, title = 'Offline Song Browser fixture') => new Response(`<!doctype html><html><head><meta charset="UTF-8"><title>${title}</title><style>.fixture-absent-part { display: none; }</style></head><body>${body}</body></html>`, { headers: { 'Content-Type': 'text/html' } });
const hostName = (host) => ({ dropbox: 'Dropbox', 'google-drive': 'Google Drive', mediafire: 'MediaFire', mega: 'MEGA' }[host]);
function searchPage(query) {
  if (query.toLowerCase() === 'network failure') return Response.error();
  if (query.toLowerCase() === 'login') return html('<h1>Custom songs for Rocksmith 2014</h1><a href="/">Login to CustomsForge</a><button>Sign in</button>');
  if (query.toLowerCase() === 'challenge') return html('<p>Verify you are human</p>', 'Just a moment...');
  const rows = query.toLowerCase() === 'empty' ? [] : [...Object.values(charts), { id: '1110', title: 'Fixture Unsupported', artist: 'Fixture Artist', host: 'unknown' }];
  if (query.toLowerCase() === 'green lung') rows.splice(0, rows.length, ...Object.values(compactCharts));
  if (query.toLowerCase() === 'official fixture') rows.splice(0, rows.length, ...Object.values(officialCharts));
  if (query.toLowerCase() === 'catalogue') rows.push(
    { id: '1111', title: 'Fixture Beneath', artist: 'Fixture Artist', host: 'dropbox', parts: 'Bass', tuning: 'Bb Standard' },
    { id: '1112', title: 'Fixture Beneath', artist: 'Fixture Artist', host: 'dropbox', parts: 'Lead', tuning: 'Bb Standard' },
    { id: '1113', title: 'Fixture Other Artist', artist: 'Different Artist', host: 'dropbox', parts: 'Lead', tuning: 'E Standard' },
  );
  if (query.toLowerCase() === 'completion') rows.push(
    { id: '1121', title: 'Fixture Success!', artist: 'Fixture Artist', host: 'dropbox', creator: 'Preferred Creator' },
    { id: '1122', title: 'Fixture Success', artist: 'Fixture Artist', host: 'dropbox', creator: 'Other Creator' },
    { id: '1123', title: 'Fixture Success (Live)', artist: 'Fixture Artist', host: 'dropbox', creator: 'Preferred Creator' },
  );
  const catalogue = rows.map((chart, index) => ({ album: 'Offline Fixture Album', tuning: 'E Standard', creator: 'Fixture Creator', parts: 'Lead', version: '1', added: '2026-01-01', updated: '2026-08-01', year: 2020, duration: '2:00', downloads: 100 - index * 10, ...chart, hostName: hostName(chart.host) }));
  // Ordinary buttons sort the full in-page catalogue before slicing a page.
  // Their handlers do not issue hidden requests, preserving the request counter
  // used by the existing navigation/lifecycle regression scenarios.
  function renderCatalogue(data, perPage) {
    let field = 'title', direction = 'asc', page = 1;
    const escape = (value) => String(value ?? '').replace(/[&<>"']/g, (character) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[character]));
    // Preserve Ignition's decorative indicator text even when display:none;
    // simplified plain-label headers previously concealed a live-site failure.
    const heading = (key, label) => `<th data-column-key="${key}" aria-sort="${field === key ? direction === 'asc' ? 'ascending' : 'descending' : 'none'}"><div class="header-content"><i class="header-drag-grip" aria-hidden="true"></i><button type="button" class="header-sort-button" onclick="window.fixtureSort('${key}')"><i class="header-icon" aria-hidden="true"></i><span class="header-text">${label}</span><span class="header-sort-indicator" aria-hidden="true"${field === key ? '' : ' style="display:none"'}>${field === key && direction === 'asc' ? '▲' : '▼'}</span></button></div></th>`;
    function render() {
      const ordered = [...data].sort((a, b) => (field === 'downloads' || field === 'year' ? a[field] - b[field] : String(a[field]).localeCompare(String(b[field]), 'en', { numeric: true })) * (direction === 'desc' ? -1 : 1));
      const offset = (page - 1) * perPage;
      const shown = ordered.slice(offset, offset + perPage);
      const tableRows = shown.map((chart) => {
        // Raw textContent is always LRB here. Only rendered/accessible badges
        // establish an arrangement; an absent badge must not leak back through
        // either its letter or its tooltip. Vary the hidden representation so
        // checking only inline styles or only the badge itself cannot pass.
        const parts = chart.compactParts ? ['L', 'R', 'B'].map((part) => {
          const label = { L: 'Lead', R: 'Rhythm', B: 'Bass' }[part];
          const badge = `<span class="instrument-badge" title="${label}">${part}</span>`;
          if (chart.parts.includes(part)) return badge;
          if (chart.id === '1141' && part === 'R') return `<span style="display:none">${badge}</span>`;
          if (chart.id === '1141' && part === 'B') return `<span class="fixture-absent-part">${badge}</span>`;
          if (chart.id === '1142' && part === 'L') return `<span style="visibility:hidden">${badge}</span>`;
          if (chart.id === '1142' && part === 'B') return `<span aria-hidden="true">${badge}</span>`;
          if (chart.id === '1143' && part === 'L') return `<span title="Lead"><span hidden>${badge}</span></span>`;
          return `<span title="Rhythm"><span class="fixture-absent-part">${badge}</span></span>`;
        }).join('') : escape(chart.parts);
        const album = escape(chart.album) + (chart.official ? ' <span class="badge">OFFICIAL DLC</span>' : '');
        return `<tr><td><span title="Hosted on ${escape(chart.hostName)}">File</span></td><td>${escape(chart.artist)}</td><td><a href="/cdlc/${chart.id}">${escape(chart.title)}</a></td><td>${album}</td><td>${escape(chart.tuning)}</td><td>${escape(chart.creator)}</td><td>${chart.added}</td><td>${chart.updated}</td><td>${parts}</td><td>${chart.version}</td><td>${chart.year}</td><td>${chart.duration}</td><td>${chart.downloads}</td></tr>`;
      }).join('') || '<tr><td colspan="13">No matching records found</td></tr>';
      document.body.innerHTML = `<p>Showing ${data.length ? offset + 1 : 0} to ${offset + shown.length} of ${data.length} results</p> <table id="cdlc-table"><thead><tr><th>Download</th>${heading('artist', 'Artist')}${heading('title', 'Title')}${heading('album', 'Album')}${heading('tuning', 'Tuning')}${heading('creator', 'Creator')}${heading('added', 'Added')}${heading('updated', 'Updated')}<th>Parts</th><th>Version</th>${heading('year', 'Year')}${heading('duration', 'Duration')}${heading('downloads', 'DLs')}</tr></thead><tbody>${tableRows}</tbody></table><span aria-current="page">${page}</span><button aria-label="Previous page" ${page === 1 ? 'disabled' : ''} onclick="window.fixturePage(-1)">Previous</button><button aria-label="Next page" ${offset + shown.length >= data.length ? 'disabled' : ''} onclick="window.fixturePage(1)">Next</button>`;
    }
    window.fixtureSort = (next) => { direction = field === next && direction === 'asc' ? 'desc' : 'asc'; field = next; page = 1; render(); };
    window.fixturePage = (delta) => { page += delta; render(); };
    render();
  }
  return html(`<script>(${renderCatalogue.toString()})(${JSON.stringify(catalogue).replace(/</g, '\\u003c')}, ${query.toLowerCase() === 'catalogue' ? 2 : 50})</script>`);
}
function downloadResponse(chart) {
  const bytes = fixturePsarc(chart);
  const headers = { 'Content-Type': 'application/octet-stream', 'Content-Disposition': `attachment; filename="fixture-${chart.id}.psarc"`, 'Content-Length': String(bytes.length) };
  if (chart.id !== charts.slow.id) return new Response(bytes, { headers });
  let timer;
  return new Response(new ReadableStream({
    start(controller) {
      controller.enqueue(bytes.subarray(0, 1024));
      timer = setTimeout(() => { controller.enqueue(bytes.subarray(1024)); controller.close(); }, 30000);
    }, cancel() { clearTimeout(timer); }
  }), { headers });
}
async function hostResponse(request) {
  const url = new URL(request.url);
  if (url.protocol !== 'https:') return blocked(request, 'Insecure fixture request');
  if (url.hostname === 'ignition4.customsforge.com') {
    if (url.pathname === '/') {
      if (url.searchParams.has('search')) {
        counters.search++; signalWaiters();
        if (searchGate && !searchGate.entered) { const gate = searchGate; gate.entered = true; signalWaiters(); await gate.release.promise; }
      }
      return searchPage(url.searchParams.get('search') || 'fixture');
    }
    const detail = url.pathname.match(/^\/cdlc\/(\d+)$/);
    if (detail && byId.has(detail[1])) {
      const chart = byId.get(detail[1]);
      return html(`<a href="/user/collectedcdlcs/toggle/${chart.id}?platform=pc&amp;expires=${Math.floor(Date.now() / 1000) + 600}&amp;signature=fixture-only" title="Hosted on ${hostName(chart.host)}">Windows</a>`);
    }
    const toggle = url.pathname.match(/^\/user\/collectedcdlcs\/toggle\/(\d+)$/);
    if (toggle && byId.has(toggle[1])) {
      const chart = byId.get(toggle[1]);
      return Response.redirect(chart.host === 'dropbox' ? `https://www.dropbox.com/scl/fi/fixture/${chart.id}.psarc?rlkey=fixture-only&dl=0`
        : chart.host === 'google-drive' ? `https://drive.google.com/file/d/${chart.id}/view` : `https://www.mediafire.com/file/${chart.id}/fixture.psarc`, 302);
    }
  }
  if (url.hostname === 'www.dropbox.com' && url.pathname === '/scl/fi/fixture/1101.psarc') {
    return url.searchParams.get('dl') === '1' ? downloadResponse(charts.fast) : html('<p>Controlled Dropbox shared file</p>');
  }
  if (url.hostname === 'www.dropbox.com' && url.pathname === '/scl/fi/fixture/1141.psarc') {
    return url.searchParams.get('dl') === '1' ? downloadResponse(compactCharts.lead) : html('<p>Controlled Lead-only Dropbox shared file</p>');
  }
  if (url.hostname === 'drive.google.com' && url.pathname === '/file/d/1102/view') return html('<a href="https://drive.usercontent.google.com/download?id=1102">Download</a>');
  if (url.hostname === 'drive.usercontent.google.com' && url.pathname === '/download' && url.searchParams.get('id') === '1102') return downloadResponse(charts.retry);
  if (url.hostname === 'www.mediafire.com' && url.pathname === '/file/1103/fixture.psarc') return html('<a id="downloadButton" href="https://download1.mediafire.com/offline/1103.psarc">Download</a>');
  if (url.hostname === 'download1.mediafire.com' && url.pathname === '/offline/1103.psarc') return downloadResponse(charts.slow);
  if (url.pathname === '/favicon.ico') return new Response(null, { status: 204 });
  return blocked(request, 'Unrecognized fixture request');
}
function blocked(request, reason) {
  const url = new URL(request.url);
  unexpected.push({ reason, origin: url.origin, path: url.pathname });
  return new Response('Blocked by the offline UI fixture', { status: 403 });
}
function trusted(event) {
  if (!mainWindow || event.sender !== mainWindow.webContents || event.senderFrame !== mainWindow.webContents.mainFrame) throw new Error('Fixture IPC requires the trusted main renderer.');
}
function ownedFile(filename) {
  const target = canonical(filename); assert.ok(inside(runtime, target));
  assert.ok(fs.lstatSync(filename).isFile() && !fs.lstatSync(filename).isSymbolicLink()); return target;
}
function fixtureApi() {
  return Object.freeze({
    charts, compactCharts, officialCharts,
    async captureScreenshot(name, selector) {
      assert.match(name, /^[a-zA-Z0-9_-]{1,80}$/);
      if (selector) await mainWindow.webContents.mainFrame.executeJavaScript(`document.querySelector(${JSON.stringify(selector)})?.scrollIntoView({block:'start'})`);
      const directory = path.join(runtime, 'screenshots');
      fs.mkdirSync(directory, { recursive: true });
      await mainWindow.webContents.capturePage();
      await new Promise((resolve) => setTimeout(resolve, 250));
      const screenshot = await mainWindow.webContents.capturePage();
      fs.writeFileSync(path.join(directory, name + '.png'), screenshot.toPNG(), { flag: 'wx' });
      return 'screenshots/' + name + '.png';
    },
    counts: () => JSON.parse(JSON.stringify(counters)),
    waitForOutputSettings: (expected) => waitFor(() => Object.entries(expected).every(([key, value]) => counters.outputSettings?.[key] === value), 'shared output settings synchronization'),
    outputFor(id, state = 'completed') {
      assert.ok(byId.has(String(id)));
      const ledger = JSON.parse(fs.readFileSync(path.join(runtime, 'profile', 'song-browser', 'jobs', 'jobs.json')));
      const job = [...ledger.jobs].reverse().find((item) => item.chartId === String(id) && item.state === state);
      assert.ok(job, 'No fixture job in the requested state exists for ' + id);
      if (job.outputPath && fs.existsSync(job.outputPath)) ownedFile(job.outputPath);
      const outputRoot = job.outputPath ? ['first', 'second'].map((name) => path.join(runtime, 'outputs', name)).find((directory) => inside(directory, job.outputPath)) : null;
      if (job.outputPath) assert.ok(outputRoot, 'Published output must stay in a folder explicitly selected in Settings.');
      return { state: job.state, selection: job.selection, outputSettings: job.outputSettings, outputFolder: outputRoot ? path.basename(outputRoot) : null, relativePath: job.outputPath ? path.relative(outputRoot, job.outputPath).replace(/\\/g, '/') : null };
    },
    holdNextSearch() {
      assert.ok(!searchGate || searchGate.released, 'A fixture search is already held.');
      searchGate = { entered: false, released: false, targetCompleted: counters.searchCompleted + 1, release: defer() };
    },
    waitForSearch: () => waitFor(() => searchGate?.entered === true, 'the held search request'),
    releaseSearch() { assert.ok(searchGate?.entered); searchGate.released = true; searchGate.release.resolve(); },
    waitForSearchSettled: () => { const target = searchGate?.targetCompleted; assert.ok(target); return waitFor(() => counters.searchCompleted >= target, 'search IPC completion'); },
    waitForDownload: (id, count = 1) => waitFor(() => (counters.downloads[String(id)] || 0) >= count, 'download ' + id),
    failConversionOnce(id) { assert.ok(byId.has(String(id))); conversionFailures.set(String(id), 1); },
    chooseOutput(name) { assert.ok(['first', 'second'].includes(name)); currentOutput = name; return path.join(runtime, 'outputs', name); },
    removeOutput(id) {
      const ledger = JSON.parse(fs.readFileSync(path.join(runtime, 'profile', 'song-browser', 'jobs', 'jobs.json')));
      const job = [...ledger.jobs].reverse().find((item) => item.chartId === String(id) && item.state === 'completed' && item.outputPath);
      assert.ok(job, 'No completed fixture output exists for ' + id);
      const target = ownedFile(job.outputPath); assert.equal(path.extname(target), '.feedpak'); fs.unlinkSync(target);
    },
  });
}
async function test(name, run) {
  const start = Date.now(); let timer;
  fs.appendFileSync(path.join(runtime, 'progress.txt'), 'START ' + name + '\n');
  try {
    await Promise.race([run(), new Promise((_resolve, reject) => { timer = setTimeout(() => reject(new Error('UI scenario timed out: ' + name)), 45000); })]);
    assert.deepEqual(rendererErrors, [], 'Production renderer reported an error.');
    tests.push({ name, status: 'passed', durationMs: Date.now() - start });
    fs.appendFileSync(path.join(runtime, 'progress.txt'), 'PASS ' + name + '\n'); process.stdout.write('PASS ' + name + '\n');
  } finally { clearTimeout(timer); }
}
async function main() {
  ({ runtime, receipt } = verifyReceipt());
  fs.writeFileSync(path.join(runtime, 'main-receipt.json'), JSON.stringify({ purpose: 'verified-production-renderer-ui-fixture', revision: receipt.revision }), { flag: 'wx' });
  for (const directory of ['profile', 'session', 'temp', 'outputs/first', 'outputs/second', 'reports']) fs.mkdirSync(path.join(runtime, directory), { recursive: true });
  app.setPath('userData', path.join(runtime, 'profile')); app.setPath('sessionData', path.join(runtime, 'session')); app.setPath('temp', path.join(runtime, 'temp'));
  app.commandLine.appendSwitch('disable-background-networking'); app.commandLine.appendSwitch('disable-component-update');
  // These methods are suppressed only for windows owned by this test process.
  app.on('browser-window-created', (_event, win) => { win.show = () => {}; win.focus = () => {}; win.showInactive = () => {}; });
  app.on('window-all-closed', () => {});
  watchdog = setTimeout(() => { process.stderr.write('UI fixture watchdog expired.\n'); app.exit(1); }, 150000);
  await app.whenReady();
  for (const scheme of ['http', 'https']) await session.defaultSession.protocol.handle(scheme, (request) => blocked(request, 'Local renderer attempted network access'));
  const featureProfile = path.join(runtime, 'profile', 'song-browser', 'browser-profile');
  fs.mkdirSync(featureProfile, { recursive: true });
  const featureSession = session.fromPath(featureProfile);
  for (const scheme of ['http', 'https']) await featureSession.protocol.handle(scheme, hostResponse);
  featureSession.on('will-download', (event, item) => {
    const id = item.getFilename().match(/^fixture-(\d+)\.psarc$/)?.[1];
    assert.ok(byId.has(id), 'Unexpected fixture DownloadItem.');
    const transfer = { id, state: 'progressing' }; transfers.push(transfer);
    item.once('done', (_event, state) => { transfer.state = state; });
    queueMicrotask(() => { if (!event.defaultPrevented) { counters.downloads[id] = (counters.downloads[id] || 0) + 1; signalWaiters(); } });
  });
  mainWindow = new BrowserWindow({ width: 1500, height: 930, show: false, backgroundColor: '#090f18',
    webPreferences: { preload: path.join(receipt.sourceRoot, 'electron', 'preload.cjs'), contextIsolation: true, nodeIntegration: false, sandbox: true } });
  mainWindow.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  ipcMain.on('app:rendererError', (event, payload) => { trusted(event); if (!closing) rendererErrors.push(payload); });
  const inert = {
    'app:version': '0.1.40', 'app:debugLogInfo': null,
    'updates:check': { ok: true, updateAvailable: false, currentVersion: '0.1.40', latestVersion: '0.1.40', releaseUrl: '' },
    'stemServer:status': { url: 'http://127.0.0.1:7865', running: false, processRunning: false, starting: false, healthy: false, accelerators: [] },
  };
  for (const [name, response] of Object.entries(inert)) ipcMain.handle(name, (event) => { trusted(event); return response; });
  ipcMain.handle('dialog:pickOutput', (event, options = {}) => {
    trusted(event);
    const selected = path.join(runtime, 'outputs', currentOutput);
    assert.ok(inside(runtime, canonical(selected)));
    counters.outputPicks.push({ selected, defaultPath: options.defaultPath || null });
    signalWaiters(); return selected;
  });
  ipcMain.handle('converter:inspect', (event, filename) => { trusted(event); return { ok: true, preview: previewFrom(filename).preview }; });
  const observedIpc = { handle(name, action) {
    ipcMain.handle(name, async (...args) => {
      if (name === 'song-browser:signIn') counters.browserActions.signIn++;
      if (name === 'song-browser:showBrowser') counters.browserActions.showBrowser++;
      const requirementKey = ['song-browser:enqueue', 'song-browser:assessResult'].includes(name) ? 'selection'
        : ['song-browser:prepareBatch', 'song-browser:updateBatchPreferences'].includes(name) ? 'preferences' : null;
      if (requirementKey) {
        const request = args[1]?.[requirementKey];
        assert.equal(request?.backingTrack, 'any', name + ' must use the original audio without a backing preference.');
        assert.equal(request.backingStrict, false, name + ' must not require backing verification.');
        assert.deepEqual(request.instrumentRequirements, [], name + ' must not retain removed instrument/string constraints.');
      }
      try {
        const result = await action(...args);
        if (name === 'song-browser:setOutputSettings' && result?.ok !== false) { counters.outputSettings = { ...args[1].settings }; signalWaiters(); }
        return result;
      }
      finally { if (name === 'song-browser:search') { counters.searchCompleted++; signalWaiters(); } }
    });
  } };
  const { registerSongBrowser } = require('../electron/song-browser/index.cjs');
  registration = registerSongBrowser({ getConverterRecipe: async () => require('./fixture-recipe.cjs'), app, BrowserWindow, session, ipcMain: observedIpc, getMainWindow: () => mainWindow, runConverter,
    dialog: {
      showOpenDialog: async () => { counters.songBrowserFolderPicks++; return { canceled: false, filePaths: [path.join(runtime, 'outputs', currentOutput)] }; },
      showSaveDialog: async (_window, options) => {
        const ext = options.filters?.[0]?.extensions?.[0] === 'psarc' ? '.psarc' : '.json';
        const filename = path.join(runtime, 'reports', 'fixture-export-' + (savedReports.length + 1) + ext);
        savedReports.push(filename); return { canceled: false, filePath: filename };
      },
    }, shell: { showItemInFolder(filename) { ownedFile(filename); counters.reveals++; } },
  });
  await mainWindow.loadFile(path.join(runtime, 'ui', 'index.html'), { hash: 'songs' });
  const { runUiScenarios } = require('./ui-integration-scenarios.cjs');
  scenarioEvidence = await runUiScenarios({ win: mainWindow, fixture: fixtureApi(), test });
  assert.deepEqual(unexpected, [], 'The offline renderer attempted an unaccounted request.');
  assert.deepEqual(rendererErrors, [], 'Production renderer reported an error.');
  assert.ok(BrowserWindow.getAllWindows().every((win) => !win.isVisible()), 'A fixture window became visible.');
  assert.deepEqual(transfers.filter((item) => item.id === charts.slow.id).map((item) => item.state), ['cancelled', 'cancelled']);
  assert.deepEqual(transfers.filter((item) => item.id === charts.fast.id).map((item) => item.state), ['completed', 'completed']);
  assert.deepEqual(transfers.filter((item) => item.id === charts.retry.id).map((item) => item.state), ['completed', 'completed']);
  assert.deepEqual(transfers.filter((item) => item.id === compactCharts.lead.id).map((item) => item.state), ['completed']);
}
async function finish(error) {
  closing = true; searchGate?.release.resolve();
  try { await registration?.close(); } catch (cleanup) { error ||= cleanup; }
  clearTimeout(watchdog);
  if (runtime) {
    const result = { status: error ? 'failed' : 'passed', evidence: 'Production renderer, production preload, real feature IPC, queue, browser and file lifecycle. All hosts and converter responses are synthetic; no live account, real FeedPak validation or game playback.',
      revision: receipt.revision, electron: process.versions.electron, tests, scenarioEvidence, counts: counters, transfers, rendererErrors, unexpectedRequests: unexpected,
      ...(error ? { error: error.stack || String(error) } : {}) };
    fs.writeFileSync(path.join(runtime, 'result.json'), JSON.stringify(result, null, 2));
    process.stdout.write(JSON.stringify({ status: result.status, tests: tests.length, report: path.join(runtime, 'result.json') }) + '\n');
  }
  if (error) process.stderr.write((error.stack || String(error)) + '\n');
  app.exit(error ? 1 : 0);
}
main().then(() => finish(), (error) => finish(error));

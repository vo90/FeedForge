const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { normalizeEndpoint, inspectFeedback, refreshFeedback } = require('../electron/song-browser/feedback.cjs');

async function fixture(t, overrides = {}) {
  const base = fs.realpathSync(os.tmpdir());
  const root = fs.mkdtempSync(path.join(base, 'feedforge-feedback-test-'));
  const library = path.join(root, 'library');
  fs.mkdirSync(library);
  const calls = [];
  const routes = {
    '/api/version': { version: '0.3.0-alpha.1', source_url: 'https://github.com/got-feedback/feedBack' },
    '/api/settings': { dlc_dir: library }, '/api/scan-status': { running: false },
    '/api/rescan': { message: 'Rescan started' }, ...overrides
  };
  const server = http.createServer((req, res) => {
    calls.push([req.method, req.url]);
    if (routes[req.url]?.redirect) { res.writeHead(302, { Location: routes[req.url].redirect }); res.end(); return; }
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify(routes[req.url]));
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  t.after(async () => {
    await new Promise((resolve) => server.close(resolve));
    assert.equal(path.dirname(fs.realpathSync(root)), base);
    assert.match(path.basename(root), /^feedforge-feedback-test-/);
    fs.rmSync(root, { recursive: true, force: true });
  });
  return { url: `http://127.0.0.1:${server.address().port}`, library, root, calls, routes };
}

test('FeedBack bridge accepts only bare loopback HTTP endpoints without credentials', () => {
  assert.equal(normalizeEndpoint('http://localhost:8123/'), 'http://127.0.0.1:8123');
  assert.equal(normalizeEndpoint('http://[::1]:8123'), 'http://[::1]:8123');
  for (const url of ['https://example.com', 'http://192.168.1.2:8000', 'http://127.0.0.1.evil.test',
    'http://user:secret@127.0.0.1', 'http://127.0.0.1/path', 'http://127.0.0.1/?token=x', 'http://127.0.0.1/#x', 'file:///C:/x']) {
    assert.throws(() => normalizeEndpoint(url), /local FeedBack address/);
  }
});

test('connecting is read-only and refresh rechecks identity and the current library', async (t) => {
  const f = await fixture(t);
  const info = await inspectFeedback(f.url);
  assert.equal(info.libraryDir, fs.realpathSync(f.library));
  assert.equal(f.calls.filter(([method]) => method === 'POST').length, 0);
  const song = path.join(f.library, 'song.feedpak'); fs.writeFileSync(song, 'test');
  const result = await refreshFeedback(f.url, song);
  assert.match(result.message, /refresh requested/);
  assert.deepEqual(f.calls.slice(-4), [['GET', '/api/version'], ['GET', '/api/settings'], ['GET', '/api/scan-status'], ['POST', '/api/rescan']]);
  const outside = path.join(f.root, 'outside.feedpak'); fs.writeFileSync(outside, 'test');
  await assert.rejects(refreshFeedback(f.url, outside), /output folder/);
  assert.equal(f.calls.filter(([method]) => method === 'POST').length, 1);
});

test('FeedBack refresh rejects changed settings, unavailable files and unrecognized services', async (t) => {
  const f = await fixture(t);
  await assert.rejects(refreshFeedback(f.url, path.join(f.library, 'missing.feedpak')), /no longer available/);
  f.routes['/api/settings'].dlc_dir = path.join(f.root, 'missing-library');
  await assert.rejects(inspectFeedback(f.url), /accessible song-library/);
  f.routes['/api/version'].source_url = 'https://github.com.evil.test/got-feedback/feedBack';
  await assert.rejects(inspectFeedback(f.url), /identified as FeedBack/);
  assert.equal(f.calls.some(([method]) => method === 'POST'), false);
});

test('FeedBack bridge never follows redirects or accepts unsupported scan responses', async (t) => {
  const f = await fixture(t, { '/api/version': { redirect: 'http://example.invalid' } });
  await assert.rejects(inspectFeedback(f.url), /supported FeedBack response/);
  assert.equal(f.calls.length, 1);
  f.routes['/api/version'] = { version: 'test', source_url: 'https://github.com/got-feedback/feedBack' };
  f.routes['/api/scan-status'] = { status: 'unknown' };
  await assert.rejects(inspectFeedback(f.url), /supported library scanner/);
});

test('FeedBack refresh rejects a junction escaping its configured library', async (t) => {
  const f = await fixture(t);
  const outside = path.join(f.root, 'outside'); fs.mkdirSync(outside);
  fs.writeFileSync(path.join(outside, 'song.feedpak'), 'test');
  const link = path.join(f.library, 'shortcut');
  fs.symlinkSync(outside, link, process.platform === 'win32' ? 'junction' : 'dir');
  await assert.rejects(refreshFeedback(f.url, path.join(link, 'song.feedpak')), /output folder/);
  assert.equal(f.calls.some(([method]) => method === 'POST'), false);
});

test('a logical redirected library and native published path compare in the same namespace', async (t) => {
  const f = await fixture(t);
  const logical = path.join(f.root, 'logical-library');
  fs.symlinkSync(f.library, logical, process.platform === 'win32' ? 'junction' : 'dir');
  f.routes['/api/settings'].dlc_dir = logical;
  const published = path.join(fs.realpathSync.native(f.library), 'published.feedpak');
  fs.writeFileSync(published, 'fixture feedpak');
  const outside = path.join(f.root, 'outside.feedpak'); fs.writeFileSync(outside, 'outside fixture');

  // Reproduce observed Windows package virtualization: the legacy sync API
  // retains the logical alias, while .native and async realpath resolve it.
  // The real junction models the same physical directory without accessing
  // any app profile or modifying the actual game library.
  const original = fs.realpathSync;
  function legacyRealpath(filename, options) {
    return path.resolve(filename) === logical ? logical : original(filename, options);
  }
  legacyRealpath.native = original.native;
  fs.realpathSync = legacyRealpath;
  try {
    assert.notEqual(fs.realpathSync(logical), fs.realpathSync.native(logical));
    const info = await inspectFeedback(f.url);
    assert.equal(info.libraryDir, fs.realpathSync.native(f.library));
    await refreshFeedback(f.url, published);
    assert.equal(f.calls.filter(([method]) => method === 'POST').length, 1);
    await assert.rejects(refreshFeedback(f.url, outside), /output folder/);
    assert.equal(f.calls.filter(([method]) => method === 'POST').length, 1, 'canonicalization must preserve the containment guard');
  } finally { fs.realpathSync = original; }
});

'use strict';

// Real sandboxed Chromium DOM/events, with every page and iframe synthesized
// locally. This suite never contacts Songsterr, YouTube, or an account service.
const { app, BrowserWindow, session } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { SongsterrProvider } = require('../electron/song-browser/providers/songsterr/browser.cjs');
const runtime = process.env.SONG_BROWSER_ELECTRON_RUNTIME;
if (!runtime || !path.isAbsolute(runtime)) throw new Error('An absolute external test directory is required.');
const checkout = path.resolve(__dirname, '..'), relative = path.relative(checkout, runtime);
if (!relative || (!relative.startsWith('..' + path.sep) && !path.isAbsolute(relative))) throw new Error('Tests must run outside the checkout.');
fs.writeFileSync(path.join(runtime, 'electron-test-receipt.json'), JSON.stringify({ purpose: 'offline-songsterr-controls', source: checkout }), { flag: 'wx' });
for (const [key, dir] of [['userData', 'profile'], ['sessionData', 'session'], ['temp', 'temp']]) {
  fs.mkdirSync(path.join(runtime, dir), { recursive: true }); app.setPath(key, path.join(runtime, dir));
}
app.commandLine.appendSwitch('disable-background-networking');
app.commandLine.appendSwitch('disable-component-update');
const presentations = [];
app.on('browser-window-created', (_event, win) => {
  assert.equal(win.isVisible(), false);
  win.show = win.showInactive = win.focus = () => { presentations.push('presentation'); };
});

function mount(options) {
  const node = (tag, attrs = {}, text = '', parent = document.body) => {
    const value = document.createElement(tag); for (const [key, attr] of Object.entries(attrs)) value.setAttribute(key, attr);
    value.textContent = text; parent.append(value); return value;
  };
  node('h1', { id: 'song-ttl' }, 'Fixture'); node('span', { id: 'song-artist' }, 'Artist');
  node('button', { id: 'control-mixer' }, 'Guitar');
  const history = node('button', { id: options.legacy ? 'revisions-toggle-tab' : 'control-revision', title: 'Revision from: 7/8/2026' }, '7/8/26');
  history.onclick = () => {
    const list = node('ul', { id: 'revisions-list' }), row = node('li', { id: 'r101' }, '', list);
    node('span', {}, 'Approved', row); node('span', {}, '7/8/2026', row); node('a', { href: '/a/wsa/fixture-tab-s42/r101' }, 'Show full tab', row);
  };
  let source;
  if (options.legacy) {
    const group = node('div', { id: 'control-source' }), label = node('label', {}, 'Original', group);
    source = node('input', { type: 'radio', value: 'original', name: 'source' }, '', label);
    source.checked = !options.synth;
  } else {
    source = node('button', { id: 'control-source', 'data-value': options.synth ? 'synth' : 'original', title: 'Switch audio source ((V))' }, 'Orig. Synth');
    source.onclick = () => { source.dataset.value = source.dataset.value === 'original' ? 'synth' : 'original'; };
  }
  const play = node('button', { id: 'control-play', 'data-can-play': 'true', 'aria-pressed': 'false' }, 'Play');
  const container = node('div', { id: 'youtube-video-container' });
  let mix, selected = options.backing ? 'backing' : 'main';
  const frame = () => {
    container.replaceChildren();
    if (options.noAudio) return;
    const video = selected === 'main' ? 'abcdefghijk' : 'zzzzzzzzzzz';
    node('iframe', { id: 'youtube-player-' + video + '-42', src: 'https://www.youtube.com/embed/' + video }, '', container);
  };
  const renderMix = () => {
    if (mix || options.noMix) return;
    if (options.legacy) {
      mix = node('select', { id: 'video-select' });
      node('option', { value: 'main' }, 'Full mix', mix); node('option', { value: 'backing' }, 'Backing track', mix);
      mix.value = selected; mix.onchange = () => { selected = mix.value; setTimeout(frame, 150); };
      return;
    }
    mix = node('button', { id: 'video-type-side-button', role: 'combobox', 'aria-haspopup': 'listbox', 'aria-expanded': 'false', 'aria-label': 'Synced video' }, selected === 'main' ? 'Full mix' : 'Backing track');
    mix.onclick = () => {
      mix.setAttribute('aria-expanded', 'true');
      setTimeout(() => {
        const list = node('ul', { role: 'listbox', id: 'video-type-side-suggestions', 'aria-labelledby': mix.id });
        for (const [key, label] of [['solo', 'Request solo'], ['backing', 'Backing track'], ['main', 'Full mix']]) {
          const option = node('li', { key, role: 'option', 'aria-selected': String(key === selected) }, label, list);
          option.onclick = () => {
            if (key !== 'main') throw new Error('Fixture: must never request generated mixes');
            selected = key; mix.textContent = label; list.remove(); mix.setAttribute('aria-expanded', 'false');
            setTimeout(frame, 150);
          };
        }
      }, 80);
    };
  };
  if (!options.lazyMix) renderMix();
  play.onclick = () => {
    const playing = play.getAttribute('aria-pressed') !== 'true'; play.setAttribute('aria-pressed', String(playing));
    if (playing) setTimeout(() => { renderMix(); frame(); }, 100);
  };
  const unrelated = node('button', {}, 'Create'); unrelated.onclick = () => { unrelated.textContent = 'WRONG Create clicked'; };
  const copy = node('button', { id: 'control-tabcopy', title: 'Create and edit a copy of the current tab. For example, for personal experiments' });
  copy.onclick = () => {
    const form = node('form', { id: 'copy-tab-form' });
    node('input', { id: 'tab-copy-title-input', type: 'text', value: 'Fixture' }, '', form);
    const create = node('button', { type: 'button', disabled: '' }, 'Create', form);
    setTimeout(() => { create.disabled = false; }, 500);
    create.onclick = () => {
      window.history.replaceState(null, '', '/a/wsa/unpublished-copy-tab-s99?open=editor'); form.remove();
      node('p', {}, 'Not published');
      const download = node('button', { id: 'control-export' }, 'Download');
      download.onclick = () => {
        const gp = node('button', { id: 'control-export-gp' }, 'Guitar Pro');
        gp.onclick = () => {
          const bytes = new Uint8Array(64); bytes.set([80, 75, 3, 4]);
          const a = node('a', { download: 'fixture.gp', href: URL.createObjectURL(new Blob([bytes], { type: 'application/octet-stream' })) }); a.click();
        };
      };
    };
  };
}

const results = [], identity = { songId: '42', revisionId: '101' }, url = 'https://www.songsterr.com/a/wsa/fixture-tab-s42/r101';
let provider, fixture;
async function load(options) {
  fixture = options;
  const win = provider._window(false); await provider._navigate(win, url);
  return { win, page: await provider._readyPinnedPage(win, '42', '101') };
}
async function verify(name, action) { await action(); results.push({ name, status: 'passed' }); }
app.whenReady().then(async () => {
  provider = new SongsterrProvider({ BrowserWindow, session, profilePath: path.join(runtime, 'account') });
  const handler = request => {
    const address = new URL(request.url);
    if (address.pathname === '/api/video-points/42/101/list') {
      return new Response(JSON.stringify(fixture.primary ? [{ songId: 42, revisionId: 101,
        videoId: 'abcdefghijk', status: 'done', feature: null, points: [0, 2, 4] }] : []),
        { headers: { 'content-type': 'application/json' } });
    }
    const html = address.hostname === 'www.songsterr.com'
      ? '<!doctype html><body><script>(' + mount.toString() + ')(' + JSON.stringify(fixture) + ')</script></body>' : '<!doctype html><body>Offline player</body>';
    return new Response(html, { headers: { 'content-type': 'text/html' } });
  };
  for (const owned of [provider.anonymousSession, provider.accountSession]) {
    await owned.protocol.handle('https', handler); await owned.protocol.handle('http', handler);
  }
  try {
    for (const options of [{ legacy: true, synth: true, backing: true }, { synth: true, backing: true }, { lazyMix: true }, { lazyMix: true, backing: true }]) {
      await verify('DOM/event discovery ' + JSON.stringify(options), async () => {
        const { win, page } = await load(options);
        assert.equal((await provider._act(win, 'history')).ok, true);
        assert.equal((await provider._read(win)).revisionRows[0].revisionId, '101');
        const audio = await provider._discoverAudio(win, identity, page, undefined, 5000);
        assert.equal(audio.videoId, 'abcdefghijk'); assert.equal((await provider._read(win)).playing, false);
      });
    }
    await verify('missing mix cannot validate an existing iframe', async () => {
      const { win, page } = await load({ lazyMix: true, noMix: true });
      assert.equal(await provider._discoverAudio(win, identity, page, undefined, 700), null);
      assert.equal((await provider._read(win)).playing, false);
    });
    await verify('absent selector uses exact primary evidence with real DOM and anonymous transport', async () => {
      const { win, page } = await load({ lazyMix: true, noMix: true, primary: true });
      const audio = await provider._discoverAudio(win, identity, page, undefined, 700);
      assert.equal(audio.videoId, 'abcdefghijk');
      assert.equal(audio.primaryRecordingEvidence.policy, 'absent-selector-primary-recording-v1');
      assert.equal(audio.primaryRecordingEvidence.revisionId, '101');
      assert.equal((await provider._read(win)).playing, false);
    });
    await verify('stalled player and cancellation both pause', async () => {
      const { win, page } = await load({ noAudio: true });
      await assert.rejects(provider._discoverAudio(win, identity, page, undefined, 700), error => error.transport?.reason === 'player_timeout');
      assert.equal((await provider._read(win)).playing, false);
      const controller = new AbortController(); setTimeout(() => controller.abort(), 120);
      await assert.rejects(provider._discoverAudio(win, identity, await provider._read(win), controller.signal, 5000), { code: 'cancelled' });
      assert.equal((await provider._read(win)).playing, false);
    });
    await verify('delayed Create is scoped and journaled before one unpublished export', async () => {
      fixture = {};
      const directory = path.join(runtime, 'download'); fs.mkdirSync(directory);
      const value = await provider._acquireAccount({ id: '42', revisionId: '101', pinnedUrl: url, title: 'Fixture', artist: 'Artist', approval: 'approved' }, { directory, onProgress() {} });
      assert.equal(fs.statSync(value.path).size, 64); assert.equal(value.metadata.acquisition, 'account_export');
      const journal = JSON.parse(fs.readFileSync(provider.journalPath)); assert.equal(journal.copies['42:101'].copyId, '99');
    });
    assert.deepEqual(presentations, []);
    fs.writeFileSync(path.join(runtime, 'result.json'), JSON.stringify({ status: 'passed', results }, null, 2));
    process.stdout.write(JSON.stringify({ status: 'passed', tests: results.length }) + '\n');
  } catch (error) {
    fs.writeFileSync(path.join(runtime, 'result.json'), JSON.stringify({ status: 'failed', results, error: error.stack }, null, 2));
    process.exitCode = 1;
  } finally { provider.dispose(); app.exit(process.exitCode || 0); }
}).catch(error => { process.stderr.write(error.stack); app.exit(1); });

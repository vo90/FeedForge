'use strict';
const fs = require('node:fs');
const path = require('node:path');
const { SongsterrProvider } = require('./providers/songsterr/index.cjs');
const { SongsterrJobs } = require('./songsterr-jobs.cjs');

function registerSongsterr({ app, BrowserWindow, session, ipcMain, dialog, shell, getMainWindow, runConverter, getConverterRecipe, getSettings, validateOutput, onCompleted }) {
  let provider, jobs, searching;
  const results = new Map();
  const state = () => ({ jobs: jobs.snapshot(), connection: provider.connection, outputDir: getSettings().outputDir });
  const emit = () => { const win = getMainWindow(); if (jobs && win && !win.isDestroyed()) win.webContents.send('songsterr:state', state()); };
  function initialize() {
    if (jobs) return;
    getSettings();
    const root = path.join(app.getPath('userData'), 'songsterr');
    fs.mkdirSync(root, { recursive: true });
    provider = new SongsterrProvider({ BrowserWindow, session, profilePath: path.join(root, 'browser-profile'), parent: getMainWindow, onConnection: emit });
    jobs = new SongsterrJobs({ root: path.join(root, 'jobs'), provider, runConverter, getConverterRecipe, emit, onCompleted });
  }
  function handler(name, action) {
    ipcMain.handle(`songsterr:${name}`, async (event, payload) => {
      const win = getMainWindow();
      if (!win || event.sender !== win.webContents || event.senderFrame !== win.webContents.mainFrame) throw new Error('Songsterr requests must come from FeedForge.');
      try { initialize(); await jobs.ready; return await action(payload || {}); }
      catch (error) { return { ok: false, code: error.code, error: String(error.message || 'The operation failed.').replace(/https?:\/\/[^\s]+/g, '[link]').slice(0, 1200) }; }
    });
  }
  handler('getState', () => state());
  handler('search', async (request) => {
    const query = typeof request.query === 'string' ? request.query.trim() : '';
    if (query.length < 2 || query.length > 160) throw new Error('Enter an artist or song title (2–160 characters).');
    searching?.abort(); const controller = new AbortController(); searching = controller;
    try {
      const found = await provider.search({ query }, { signal: controller.signal });
      if (controller.signal.aborted) return { status: 'cancelled', results: [] };
      if (found.status === 'ready') {
        for (const song of found.results || []) if (/^\d{1,14}$/.test(String(song.id))) results.set(String(song.id), song);
        while (results.size > 2000) results.delete(results.keys().next().value);
      }
      return found;
    } finally { if (searching === controller) searching = null; }
  });
  handler('cancelSearch', () => { searching?.abort(); return { ok: true }; });
  handler('enqueue', ({ id }) => {
    const song = results.get(String(id)); if (!song) throw new Error('Search for this song again before importing it.');
    const settings = getSettings();
    if (!settings.outputDir) throw new Error('Choose an output folder in FeedForge Settings.');
    validateOutput(settings.outputDir);
    return jobs.enqueue(song, settings);
  });
  handler('retry', ({ id, allowAccount }) => jobs.retry(String(id), { allowAccount: allowAccount === true }));
  handler('cancel', async ({ id }) => { await jobs.cancel(String(id)); return { ok: true }; });
  handler('signIn', () => provider.signIn());
  handler('showBrowser', () => provider.showBrowser());
  handler('chooseAudio', async ({ id }) => {
    if (!jobs.snapshot().some((job) => job.id === id && job.canRetry)) throw new Error('Choose an import waiting for audio.');
    const selection = await dialog.showOpenDialog(getMainWindow(), { title: 'Choose the original song audio', properties: ['openFile'],
      filters: [{ name: 'Audio', extensions: ['mp3', 'ogg', 'wav', 'flac', 'm4a', 'aac', 'opus', 'aiff'] }] });
    if (selection.canceled || !selection.filePaths[0]) return { cancelled: true };
    const filename = selection.filePaths[0], stat = fs.lstatSync(filename);
    if (!stat.isFile() || stat.isSymbolicLink() || !stat.size || stat.size > 2 * 1024 ** 3) throw new Error('Choose a regular audio file smaller than 2 GB.');
    return jobs.retry(String(id), { audio: { kind: 'file', path: filename } });
  });
  handler('useAudioUrl', ({ id, url }) => {
    if (typeof url !== 'string' || url.length > 4096) throw new Error('Paste a valid HTTPS audio or YouTube link.');
    let parsed; try { parsed = new URL(url.trim()); } catch { throw new Error('Paste a valid HTTPS audio or YouTube link.'); }
    if (parsed.protocol !== 'https:' || parsed.username || parsed.password) throw new Error('Use an HTTPS link without credentials.');
    return jobs.retry(String(id), { audio: { kind: 'url', url: parsed.href } });
  });
  handler('showOutput', ({ id }) => {
    const job = jobs.snapshot().find((j) => j.id === id && j.state === 'completed' && j.outputAvailable);
    if (!job) { emit(); throw new Error('The saved FeedPak is no longer available.'); }
    shell.showItemInFolder(job.outputPath); return { ok: true };
  });
  return { active: () => Boolean(jobs), close: async () => { searching?.abort(); await jobs?.dispose(); await provider?.dispose(); } };
}
module.exports = { registerSongsterr };

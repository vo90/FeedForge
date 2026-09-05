const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { CustomsForgeBrowser } = require('./browser.cjs');
const { SongJobs } = require('./jobs.cjs');
const { createDiagnostics } = require('./diagnostics.cjs');
const { normalizeEndpoint, inspectFeedback, refreshFeedback } = require('./feedback.cjs');

function registerSongBrowser({ app, BrowserWindow, session, ipcMain, dialog, shell, getMainWindow, runConverter }) {
  let browser, jobs, outputDir, root;
  let config = {}, diagnostics;
  let feedback = { status: 'disconnected', message: '' };
  let refreshPromise = null;
  let connectionGeneration = 0, connectionRequest = 0, outputGeneration = 0;
  let quitting = false;
  const charts = new Map();
  const stages = new Map();
  const state = () => ({ outputDir, jobs: jobs.snapshot(), connection: browser.connection,
    feedback: { ...feedback, url: config.feedback?.url || '', autoRefresh: config.feedback?.autoRefresh === true } });
  const emit = (job) => {
    if (job?.id && stages.get(job.id)?.stage !== job.state) {
      diagnostics?.record({ code: job.state === 'failed' ? 'job_failed' : 'job_state', stage: job.state, host: job.host });
      stages.set(job.id, { stage: job.state, at: Date.now() });
      while (stages.size > 100) stages.delete(stages.keys().next().value);
      if (job.state === 'completed' && config.feedback?.autoRefresh && !quitting) {
        // Completion is already committed; a refresh failure never changes the conversion result.
        void refresh(job.outputPath).catch(() => {});
      }
    }
    const win = getMainWindow();
    if (jobs && win && !win.isDestroyed()) win.webContents.send('song-browser:state', state());
  };
  function saveSettings(next) {
    const settings = path.join(root, 'settings.json');
    const temporary = path.join(root, `settings-${crypto.randomUUID()}.tmp`);
    let owned = false;
    try {
      fs.writeFileSync(temporary, JSON.stringify(next, null, 2), { flag: 'wx', mode: 0o600 });
      owned = true;
      fs.renameSync(temporary, settings);
      config = next;
    } catch (error) { diagnostics.record({ code: 'settings_failed' }); throw error; }
    finally { if (owned && fs.existsSync(temporary)) fs.unlinkSync(temporary); }
  }
  async function refresh(target) {
    if (!config.feedback?.url) throw new Error('Connect to a running FeedBack first.');
    const previous = refreshPromise;
    const url = config.feedback.url;
    const generation = connectionGeneration;
    let release;
    const gate = new Promise((resolve) => { release = resolve; });
    refreshPromise = gate;
    let start;
    try {
      if (previous) await previous;
      if (quitting || generation !== connectionGeneration) return { ok: true, superseded: true };
      start = Date.now();
      diagnostics.record({ code: 'library_refresh_started', stage: 'library' });
      feedback = { ...feedback, status: 'refreshing', message: 'Requesting library refresh…' }; emit();
      const info = await refreshFeedback(url, target);
      // A response from a previous connection cannot replace newly selected settings.
      if (generation === connectionGeneration) feedback = { ...info, status: 'connected' };
      diagnostics.record({ code: 'library_refresh_finished', stage: 'library', durationMs: Date.now() - start });
      return { ok: true };
    } catch (error) {
      if (generation === connectionGeneration) feedback = { ...feedback, status: 'error', message: error.message };
      diagnostics.record({ code: 'library_refresh_failed', stage: 'library', durationMs: Date.now() - start });
      throw error;
    } finally { release(); if (refreshPromise === gate) refreshPromise = null; emit(); }
  }
  function initialize() {
    if (jobs) return;
    root = path.join(app.getPath('userData'), 'song-browser');
    fs.mkdirSync(root, { recursive: true });
    const configPath = path.join(root, 'settings.json');
    diagnostics = createDiagnostics({ appVersion: app.getVersion?.() || 'unknown' });
    if (fs.existsSync(configPath)) {
      try { config = JSON.parse(fs.readFileSync(configPath, 'utf8')); }
      catch { throw new Error('Song Browser settings could not be read. Keep the file for recovery: ' + configPath); }
    }
    if (!config || typeof config !== 'object' || Array.isArray(config)) throw new Error('Song Browser settings have an unsupported format.');
    if (config.feedback?.url) {
      try { config.feedback.url = normalizeEndpoint(config.feedback.url); }
      catch { config.feedback = { url: '', autoRefresh: false }; }
    }
    outputDir = typeof config.outputDir === 'string' && path.isAbsolute(config.outputDir)
      ? config.outputDir : path.join(root, 'FeedPaks');
    browser = new CustomsForgeBrowser({ BrowserWindow, session,
      profilePath: path.join(root, 'browser-profile'), parent: getMainWindow, onConnection: () => emit(), onDiagnostic: diagnostics.record });
    try {
      jobs = new SongJobs({ root: path.join(root, 'jobs'), outputDir,
        download: (chart, options) => browser.download(chart, options), runConverter, emit });
      for (const job of jobs.snapshot()) stages.set(job.id, { stage: job.state, at: Date.now() });
    } catch (error) { browser.dispose(); browser = null; throw error; }
  }
  function handler(name, action) {
    ipcMain.handle(`song-browser:${name}`, async (event, payload) => {
      const win = getMainWindow();
      if (!win || event.sender !== win.webContents || event.senderFrame !== win.webContents.mainFrame) {
        throw new Error('Song Browser requests must come from FeedForge.');
      }
      try { initialize(); return await action(payload || {}); }
      catch (error) { return { ok: false, error: String(error.message || 'The operation failed.').slice(0, 1200) }; }
    });
  }
  handler('getState', () => state());
  handler('signIn', () => browser.signIn());
  handler('showBrowser', () => browser.showBrowser());
  handler('search', async (request) => {
    const result = await browser.search(request);
    if (result.status === 'ready') {
      // Only visible search results are eligible for enqueue. Never accept a supplied URL.
      for (const chart of result.results) charts.set(String(chart.id), chart);
      while (charts.size > 500) charts.delete(charts.keys().next().value);
    }
    return result;
  });
  handler('enqueue', ({ id }) => {
    const chart = charts.get(String(id));
    if (!chart) throw new Error('Search for the chart again before downloading it.');
    if (!chart.supported) throw new Error('This host is not supported yet.');
    return jobs.enqueue(chart);
  });
  handler('cancel', ({ id }) => jobs.cancel(String(id)));
  handler('retry', ({ id }) => { diagnostics.record({ code: 'retry_requested', stage: 'recovery' }); return jobs.retry(String(id)); });
  handler('clearCache', async ({ id }) => {
    const result = await jobs.clearCache(String(id));
    diagnostics.record({ code: 'cache_cleared', stage: 'recovery' }); return result;
  });
  handler('openCached', async ({ id }) => {
    const job = jobs.snapshot().find((item) => item.id === String(id));
    if (!job?.hasCachedInput) throw new Error('This job has no saved PSARC to review.');
    const result = await dialog.showSaveDialog(getMainWindow(), {
      title: 'Save a PSARC copy for manual review in FeedForge',
      defaultPath: `CF-${job.chartId}-review.psarc`, filters: [{ name: 'PSARC song', extensions: ['psarc'] }]
    });
    if (result.canceled || !result.filePath) return { canceled: true };
    if (path.extname(result.filePath).toLowerCase() !== '.psarc') throw new Error('Choose a .psarc filename.');
    const input = await jobs.getCachedInput(String(id));
    try { fs.copyFileSync(input, result.filePath, fs.constants.COPYFILE_EXCL); }
    catch (error) { if (error.code === 'EEXIST') throw new Error('That file already exists. Choose a new name for the review copy.'); throw error; }
    return { inputPath: result.filePath };
  });
  handler('exportDiagnostics', async () => {
    const result = await dialog.showSaveDialog(getMainWindow(), { title: 'Export Song Browser troubleshooting report',
      defaultPath: `song-browser-report-${new Date().toISOString().slice(0, 10)}.json`, filters: [{ name: 'JSON report', extensions: ['json'] }] });
    if (result.canceled || !result.filePath) return { canceled: true };
    diagnostics.record({ code: 'report_exported', stage: 'diagnostics' });
    fs.writeFileSync(result.filePath, JSON.stringify(diagnostics.report(), null, 2), { flag: 'wx', mode: 0o600 });
    return { ok: true, exported: true };
  });
  function selectOutput(selectedPath) {
    const previous = outputDir;
    let selected;
    try {
      selected = jobs.setOutputDir(selectedPath);
      saveSettings({ ...config, outputDir: selected });
      outputDir = selected;
      diagnostics.record({ code: 'output_checked', stage: 'output' });
    } catch (error) {
      if (selected) jobs.outputDir = previous; // Restore an already accepted setting without a new disk probe.
      diagnostics.record({ code: 'output_failed', stage: 'output' }); throw error;
    }
    emit(); return { outputDir };
  }
  handler('chooseOutput', async () => {
    const generation = ++outputGeneration;
    const result = await dialog.showOpenDialog(getMainWindow(), {
      title: 'Choose where to save converted songs', defaultPath: outputDir,
      properties: ['openDirectory', 'createDirectory']
    });
    if (generation === outputGeneration && !result.canceled && result.filePaths[0]) {
      return selectOutput(result.filePaths[0]);
    }
    return { outputDir };
  });
  handler('connectFeedback', async ({ url }) => {
    const request = ++connectionRequest;
    const info = await inspectFeedback(url);
    if (request !== connectionRequest) return { ok: true, superseded: true };
    const same = config.feedback?.url === info.url;
    saveSettings({ ...config, feedback: { url: info.url, autoRefresh: same && config.feedback?.autoRefresh === true } });
    connectionGeneration++;
    feedback = { ...info, status: 'connected', message: `Connected to FeedBack ${info.version}.` }; emit();
    return { ok: true };
  });
  handler('setAutoRefresh', ({ enabled }) => {
    if (typeof enabled !== 'boolean') throw new Error('Choose whether to refresh after conversion.');
    if (enabled && (!config.feedback?.url || feedback.status !== 'connected')) throw new Error('Connect to FeedBack before enabling automatic refresh.');
    saveSettings({ ...config, feedback: { ...config.feedback, autoRefresh: enabled } }); emit(); return { ok: true };
  });
  handler('useFeedbackFolder', async () => {
    const generation = ++outputGeneration;
    const connection = connectionGeneration;
    const info = await inspectFeedback(config.feedback?.url);
    if (generation !== outputGeneration || connection !== connectionGeneration) return { outputDir };
    return selectOutput(info.libraryDir);
  });
  handler('refreshFeedback', () => refresh(outputDir));
  handler('showOutput', ({ id }) => {
    const job = jobs.snapshot().find((entry) => entry.id === String(id));
    if (!job || job.state !== 'completed' || !job.outputPath || job.outputAvailable !== true || !fs.existsSync(job.outputPath)) {
      // A file may have been moved since the last renderer snapshot. Publish
      // its current availability so the search card can offer a fresh download.
      emit();
      throw new Error('This converted file is no longer available.');
    }
    shell.showItemInFolder(job.outputPath);
    return { ok: true };
  });
  app.on('before-quit', (event) => {
    if (!jobs || quitting) return;
    event.preventDefault();
    quitting = true;
    browser.dispose();
    Promise.resolve(jobs.dispose()).finally(() => app.quit());
  });
  return { close: () => { browser?.dispose(); return jobs?.dispose(); } };
}
module.exports = { registerSongBrowser };

const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { CustomsForgeBrowser } = require('./browser.cjs');
const { SongJobs, selectionForChart } = require('./jobs.cjs');
const { createDiagnostics } = require('./diagnostics.cjs');
const { normalizeEndpoint, inspectFeedback, refreshFeedback } = require('./feedback.cjs');
const { BatchCoordinator } = require('./batch.cjs');
const { ImportIndex } = require('./imports.cjs');
const { normalizeSearchRequest, searchIdentity } = require('./catalogue.cjs');
const { normalizeRequirements } = require('./file-selection.cjs');
const { normalizeRecipe, recipesCompatible } = require('./provenance.cjs');
const { normalizeOutputSettings } = require('./output-settings.cjs');

function registerSongBrowser({ app, BrowserWindow, session, ipcMain, dialog, shell, getMainWindow, runConverter, getConverterRecipe = async () => null }) {
  let browser, jobs, outputDir, root, batches, imports, collecting = null, searching = null, recipePromise;
  let config = {}, diagnostics;
  let feedback = { status: 'disconnected', message: '' };
  let refreshPromise = null;
  let connectionGeneration = 0, connectionRequest = 0, outputGeneration = 0;
  let quitting = false;
  const charts = new Map();
  const stages = new Map();
  const state = () => ({ outputDir, jobs: jobs.snapshot(), connection: browser.connection,
    batches: batches?.snapshot() || [], preparation: collecting ? { ...collecting.progress, pending: true } : null,
    searchProgress: searching ? { ...searching.progress, requestId: searching.id, pending: true } : null,
    feedback: { ...feedback, url: config.feedback?.url || '', autoRefresh: config.feedback?.autoRefresh === true } });
  const emit = (job) => {
    if (job?.id && stages.get(job.id)?.stage !== job.state) {
      diagnostics?.record({ code: job.state === 'failed' ? 'job_failed' : 'job_state', stage: job.state, host: job.host });
      stages.set(job.id, { stage: job.state, at: Date.now() });
      while (stages.size > 100) stages.delete(stages.keys().next().value);
      if (job.state === 'completed' && !job.batchId && config.feedback?.autoRefresh && !quitting) {
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
    if (typeof config.sharedOutputDir === 'string' && path.isAbsolute(config.sharedOutputDir)) outputDir = config.sharedOutputDir;
    browser = new CustomsForgeBrowser({ BrowserWindow, session,
      profilePath: path.join(root, 'browser-profile'), parent: getMainWindow, onConnection: () => emit(), onDiagnostic: diagnostics.record });
    try {
      imports = new ImportIndex({ root: path.join(root, 'imports') });
      jobs = new SongJobs({ root: path.join(root, 'jobs'), outputDir, outputSettings: config.sharedOutputSettings || null,
        download: (chart, options) => browser.download(chart, options), runConverter, emit,
        findReusable: ({ chart, sourceHash, recipe, requirements, signal, outputDir, outputSettings, sourceFilename }) => imports.find(chart, { sourceHash, recipe, requirements, signal, outputDir, outputSettings, sourceFilename }),
        onCompleted: (entry) => { imports.record(entry); browser.filteredSnapshot = null; } });
      browser.decorateCharts = async (charts, request, signal) => {
        if (!request.filters?.hideConverted) return charts;
        const result = [];
        for (const chart of charts) {
          if (signal?.aborted) throw new Error('Collection cancelled.');
          result.push({ ...chart, alreadyConverted: Boolean(await imports.find(chart, { outputDir, outputSettings: jobs.outputSettings, recipe: jobs.recipe, preferences: normalizeRequirements(request.filters || {}), signal })) });
        }
        return result;
      };
      const batchStates = new Map();
      batches = new BatchCoordinator({ root: path.join(root, 'batches'),
        findCompleted: async ({ batchId, itemId, intentId, legacyIntent, selection, jobId }) => {
          const saved = await imports.findByAttempt(batchId, itemId, { intentId: legacyIntent && !selection?.choice ? undefined : intentId, jobId });
          return saved ? { ...saved, status: 'completed', message: 'Previously saved FeedPak recovered.' } : null;
        },
        execute: async (chart, context) => {
          if (!recipesCompatible(context.recipe, jobs.recipe)) throw new Error('The converter changed. Resume the batch to update its conversion settings.');
          const selection = selectionForChart(chart, chart.selection || context.selection);
          const saved = context.reviewAnother || selection.choice ? null : await imports.find(chart, { outputDir: context.outputDir, outputSettings: selection.outputSettings || jobs.outputSettings, preferences: selection, requestedChoice: selection.choice, recipe: context.recipe, signal: context.signal });
          if (saved) return { ...saved, status: 'skipped', skipKind: 'available', message: 'Already available in this output folder.' };
          return jobs.run(chart, { ...context, selection });
        },
        onChange: (snapshots) => {
          for (const batch of snapshots) {
            const stamp = `${batch.state}:${batch.counts?.completed || 0}`;
            if (batchStates.get(batch.id) !== stamp && ['completed', 'paused'].includes(batch.state) && !batch.counts?.running && config.feedback?.autoRefresh && !quitting) {
              if (batch.items?.some((item) => item.state === 'completed')) void refresh(batch.outputDir).catch(() => {});
            }
            batchStates.set(batch.id, stamp);
          }
          emit();
        } });
      for (const job of jobs.snapshot()) stages.set(job.id, { stage: job.state, at: Date.now() });
    } catch (error) { browser.dispose(); browser = null; throw error; }
  }
  async function ensureRecipe() {
    if (!recipePromise) recipePromise = Promise.resolve().then(getConverterRecipe).then(normalizeRecipe).catch((error) => { recipePromise = null; throw error; });
    jobs.recipe = await recipePromise;
    return jobs.recipe;
  }
  async function importDecisions(selected, preferences, directory, signal) {
    const decisions = {};
    for (const chart of selected) {
      if (signal?.aborted) throw new Error('Batch preparation cancelled.');
      const assessment = await imports.assess(chart, { outputDir: directory, outputSettings: preferences.outputSettings || jobs.outputSettings, preferences: selectionForChart(chart, preferences), recipe: jobs.recipe, signal });
      decisions[chart.id] = { status: assessment.status, reason: assessment.reason };
    }
    return decisions;
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
  handler('setOutputSettings', ({ settings }) => {
    const normalized = normalizeOutputSettings(settings);
    const sharedDir = settings?.outputDir || '';
    if (typeof sharedDir !== 'string' || (sharedDir && (!path.isAbsolute(sharedDir) || sharedDir.length > 4096 || sharedDir.includes('\0')))) throw new Error('Choose a valid FeedForge output folder.');
    const target = sharedDir || config.outputDir || path.join(root, 'FeedPaks');
    const previous = jobs.outputDir;
    let selected = previous;
    try {
      if (path.resolve(target) !== previous) selected = jobs.setOutputDir(target);
      saveSettings({ ...config, sharedOutputSettings: normalized, sharedOutputDir: sharedDir });
    } catch (error) { jobs.outputDir = previous; throw error; }
    outputDir = selected;
    jobs.outputSettings = normalized;
    browser.filteredSnapshot = null;
    emit(); return state();
  });
  handler('signIn', () => browser.signIn());
  handler('showBrowser', () => browser.showBrowser());
  handler('search', async (request) => {
    const id = typeof request.requestId === 'string' && /^[a-zA-Z0-9_-]{1,100}$/.test(request.requestId) ? request.requestId : crypto.randomUUID();
    searching?.controller.abort();
    const operation = { id, controller: new AbortController(), progress: { collected: 0, total: null, page: 1 } };
    searching = operation; emit();
    try {
    if (request.filters?.hideConverted) await ensureRecipe();
    const result = await browser.search(request, { signal: operation.controller.signal, onProgress: (progress) => { if (searching === operation) { operation.progress = progress; emit(); } } });
    if (operation.controller.signal.aborted) return { status: 'cancelled', requestId: id };
    if (result.status === 'ready') {
      // Only visible search results are eligible for enqueue. Never accept a supplied URL.
      for (const chart of result.results) charts.set(String(chart.id), chart);
      while (charts.size > 5000) charts.delete(charts.keys().next().value);
    }
    return result;
    } finally { if (searching === operation) { searching = null; emit(); } }
  });
  handler('cancelSearch', ({ requestId }) => { if (searching && (!requestId || searching.id === requestId)) searching.controller.abort(); return { ok: true }; });
  handler('enqueue', async ({ id, selection, reviewAnother = false }) => {
    const chart = charts.get(String(id));
    if (!chart) throw new Error('Search for the chart again before downloading it.');
    if (!chart.supported) throw new Error('This host is not supported yet.');
    await ensureRecipe();
    return jobs.enqueue({ ...chart, recipe: jobs.recipe, reviewAnother: reviewAnother === true, selection: normalizeRequirements(selection || {}) });
  });
  handler('chooseFile', ({ id }) => browser.chooseFile({ id: String(id) }));
  handler('assessResult', async ({ id, jobId, selection }) => {
    const chart = charts.get(String(id));
    if (!chart) throw new Error('Search for the chart again to check the saved song.');
    const job = jobs.snapshot().find((entry) => entry.id === jobId && entry.chartId === chart.id && entry.state === 'completed');
    if (!job) throw new Error('The saved song has changed. Refresh its results.');
    await ensureRecipe();
    const assessment = await imports.assess(chart, { jobId: job.id, outputDir, outputSettings: jobs.outputSettings, preferences: selectionForChart(chart, normalizeRequirements(selection || {})), recipe: jobs.recipe });
    return { reusable: assessment.reusable, code: assessment.code, reason: assessment.reason, status: assessment.status };
  });
  handler('prepareBatch', async ({ request, ids, preferences, scope = 'all' }) => {
    if (collecting) throw new Error('A batch is already being prepared.');
    const query = normalizeSearchRequest(request);
    const target = jobs.validateOutputDir(outputDir);
    preferences = { ...preferences, ...(jobs.outputSettings ? { outputSettings: { ...jobs.outputSettings } } : {}) };
    const operation = { controller: new AbortController(), progress: { collected: 0, total: null } };
    collecting = operation; emit();
    try {
      await ensureRecipe();
      let selected;
      if (scope === 'selected') {
        if (!Array.isArray(ids) || !ids.length || ids.length > 5000 || new Set(ids).size !== ids.length) throw new Error('Select charts to prepare.');
        selected = ids.map((id) => { const chart = charts.get(String(id)); if (!chart) throw new Error('A selected chart has expired. Search for it again.'); return chart; });
      } else if (scope === 'all') {
        const result = await browser.collect({ ...query, sort: request.sort ? query.sort : undefined }, {
          signal: operation.controller.signal,
          onProgress: (progress) => { operation.progress = progress; emit(); },
        });
        selected = result.results;
      } else throw new Error('Choose selected charts or all results.');
      if (operation.controller.signal.aborted) throw new Error('Batch preparation cancelled.');
      const decisions = await importDecisions(selected, preferences || {}, target, operation.controller.signal);
      return batches.prepare({ charts: selected, preferences, recipe: jobs.recipe, importDecisions: decisions, outputDir: target, query: searchIdentity(query), complete: true });
    } finally { if (collecting === operation) collecting = null; emit(); }
  });
  handler('cancelPreparation', () => { collecting?.controller.abort(); return { ok: true }; });
  handler('chooseBatch', ({ id, selectedIds, forceReviewIds }) => batches.choose(String(id), { selectedIds, forceReviewIds }));
  handler('updateBatchPreferences', async ({ id, preferences }) => {
    const batch = batches.snapshot().find((item) => item.id === String(id));
    if (!batch || batch.state !== 'draft') throw new Error('Choose a batch that is still being reviewed.');
    await ensureRecipe();
    preferences = { ...preferences, ...(jobs.outputSettings ? { outputSettings: { ...jobs.outputSettings } } : {}) };
    return batches.updatePreferences(String(id), { preferences, importDecisions: await importDecisions(batch.charts, preferences || {}, batch.outputDir) });
  });
  handler('skipBatchItem', ({ id, itemId }) => batches.skipItem(String(id), String(itemId)));
  handler('retryBatchItem', async ({ id, itemId, relaxRequirements }) => { await ensureRecipe(); await batches.setRecipe(String(id), jobs.recipe); return batches.retryItem(String(id), String(itemId), { relaxRequirements: relaxRequirements === true }); });
  handler('dismissBatchSuggestion', ({ id, suggestionId }) => batches.dismissSuggestion(String(id), String(suggestionId)));
  handler('startBatch', async ({ id }) => { await ensureRecipe(); await batches.setRecipe(String(id), jobs.recipe); return batches.start(String(id)); });
  handler('pauseBatch', ({ id }) => batches.pause(String(id)));
  handler('resumeBatch', async ({ id, retryFailed }) => { await ensureRecipe(); await batches.setRecipe(String(id), jobs.recipe); return batches.resume(String(id), { retryFailed: retryFailed === true }); });
  handler('resolveBatchItem', async ({ id, itemId }) => { await ensureRecipe(); await batches.setRecipe(String(id), jobs.recipe); return batches.resumeItem(String(id), String(itemId)); });
  handler('cancelBatch', ({ id }) => batches.cancel(String(id)));
  handler('chooseBatchFile', ({ id, itemId, choice }) => batches.setItemChoice(String(id), String(itemId), { choice }));
  handler('removeBatch', ({ id }) => { batches.remove(String(id)); return { ok: true }; });
  handler('cancel', ({ id }) => jobs.cancel(String(id)));
  handler('retry', async ({ id, relaxRequirements }) => {
    await ensureRecipe(); diagnostics.record({ code: 'retry_requested', stage: 'recovery' });
    return jobs.retry(String(id), relaxRequirements === true ? { selection: { parts: [], tuning: null, platform: 'pc', strictPlatform: true, backingTrack: 'any', backingStrict: false, instrumentRequirements: [] } } : {});
  });
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
      browser.filteredSnapshot = null;
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
    collecting?.controller.abort();
    searching?.controller.abort();
    browser.dispose();
    Promise.resolve(batches?.dispose?.()).finally(() => jobs.dispose()).finally(() => app.quit()).catch(() => {});
  });
  return { close: async () => { collecting?.controller.abort(); searching?.controller.abort(); await batches?.dispose(); browser?.dispose(); return jobs?.dispose(); } };
}
module.exports = { registerSongBrowser };

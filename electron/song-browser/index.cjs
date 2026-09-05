const fs = require('node:fs');
const path = require('node:path');
const { CustomsForgeBrowser } = require('./browser.cjs');
const { SongJobs } = require('./jobs.cjs');

function registerSongBrowser({ app, BrowserWindow, session, ipcMain, dialog, shell, getMainWindow, runConverter }) {
  let browser, jobs, outputDir, root;
  let quitting = false;
  const charts = new Map();
  const state = () => ({ outputDir, jobs: jobs.snapshot(), connection: browser.connection });
  const emit = () => {
    const win = getMainWindow();
    if (jobs && win && !win.isDestroyed()) win.webContents.send('song-browser:state', state());
  };
  function initialize() {
    if (jobs) return;
    root = path.join(app.getPath('userData'), 'song-browser');
    fs.mkdirSync(root, { recursive: true });
    const configPath = path.join(root, 'settings.json');
    let config = {};
    if (fs.existsSync(configPath)) {
      try { config = JSON.parse(fs.readFileSync(configPath, 'utf8')); }
      catch { throw new Error('Song Browser settings could not be read. Keep the file for recovery: ' + configPath); }
    }
    outputDir = typeof config.outputDir === 'string' && path.isAbsolute(config.outputDir)
      ? config.outputDir : path.join(root, 'FeedPaks');
    browser = new CustomsForgeBrowser({ BrowserWindow, session,
      profilePath: path.join(root, 'browser-profile'), parent: getMainWindow, onConnection: emit });
    try {
      jobs = new SongJobs({ root: path.join(root, 'jobs'), outputDir,
        download: (chart, options) => browser.download(chart, options), runConverter, emit });
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
  handler('chooseOutput', async () => {
    const result = await dialog.showOpenDialog(getMainWindow(), {
      title: 'Choose where to save converted songs', defaultPath: outputDir,
      properties: ['openDirectory', 'createDirectory']
    });
    if (!result.canceled && result.filePaths[0]) {
      const previous = outputDir;
      const selected = jobs.setOutputDir(result.filePaths[0]);
      const settings = path.join(root, 'settings.json');
      const temporary = path.join(root, `settings-${process.pid}-${Date.now()}.tmp`);
      let owned = false;
      try {
        fs.writeFileSync(temporary, JSON.stringify({ outputDir: selected }, null, 2), { flag: 'wx', mode: 0o600 });
        owned = true;
        fs.renameSync(temporary, settings);
        outputDir = selected;
      } catch (error) { jobs.setOutputDir(previous); throw error; }
      finally { if (owned && fs.existsSync(temporary)) fs.unlinkSync(temporary); }
      emit();
    }
    return { outputDir };
  });
  handler('showOutput', ({ id }) => {
    const job = jobs.snapshot().find((entry) => entry.id === String(id));
    if (!job || job.state !== 'completed' || !job.outputPath || !fs.existsSync(job.outputPath)) {
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

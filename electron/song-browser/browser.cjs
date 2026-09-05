// All website interaction stays in this adapter. No cookies or signed URLs leave it.
const { readSearchPage, requestChartDownload, requestSearchPage } = require('./dom.cjs');
const { hostDownloadAction } = require('./host-actions.cjs');

const CF = 'https://ignition4.customsforge.com';
const MAX_BYTES = 512 * 1024 * 1024;
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function allowedNavigation(value) {
  try {
    const u = new URL(value);
    if (u.protocol !== 'https:' || u.username || u.password || (u.port && u.port !== '443')) return false;
    return ['customsforge.com', 'ignition4.customsforge.com', 'drive.google.com',
      'drive.usercontent.google.com', 'accounts.google.com', 'www.dropbox.com', 'dropbox.com',
      'www.mediafire.com', 'mediafire.com'].includes(u.hostname)
      || u.hostname.endsWith('.dropboxusercontent.com')
      || /^download\d+\.mediafire\.com$/.test(u.hostname)
      || u.hostname.endsWith('.googleusercontent.com');
  } catch { return false; }
}

function allowedDownload(value, filename, size) {
  if (!allowedNavigation(value) || !/\.psarc$/i.test(filename) || size > MAX_BYTES) return false;
  const host = new URL(value).hostname;
  return host === 'drive.usercontent.google.com' || host.endsWith('.googleusercontent.com')
    || host === 'dropbox.com' || host === 'www.dropbox.com' || host.endsWith('.dropboxusercontent.com')
    || /^download\d+\.mediafire\.com$/.test(host);
}

function searchUrl(query, page = 1) {
  if (typeof query !== 'string' || query.trim().length < 2 || query.trim().length > 160) {
    throw new Error('Enter between 2 and 160 characters to search.');
  }
  if (!Number.isInteger(page) || page < 1 || page > 10000) throw new Error('Invalid search page.');
  const url = new URL(CF);
  url.searchParams.set('search', query.trim());
  return url.href;
}

class CustomsForgeBrowser {
  constructor({ BrowserWindow, session, profilePath, parent, onConnection = () => {}, onDiagnostic = () => {} }) {
    this.BrowserWindow = BrowserWindow;
    this.parent = parent;
    this.session = session.fromPath(profilePath);
    this.onConnection = onConnection;
    this.onDiagnostic = onDiagnostic;
    this.connection = { status: 'signed_out', message: 'Sign in to CustomsForge to start searching.' };
    this.searchWindow = null;
    this.windows = new Set();
    this.active = null;
    this.searching = false;
    this.lastSearch = null;
    this.connectionRead = 0;
    this.disposed = false;
    this.session.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
    this.session.setPermissionCheckHandler(() => false);
    this.onDownload = (event, item, contents) => this.acceptDownload(event, item, contents);
    this.session.on('will-download', this.onDownload);
  }

  updateConnection(status, message) {
    this.connection = { status, message };
    this.onConnection(this.connection);
  }

  diagnostic(event) {
    // Diagnostic reporting must never interrupt the user's operation.
    try { this.onDiagnostic(event); } catch { /* Nonessential local telemetry. */ }
  }

  createWindow(job = null) {
    const win = new this.BrowserWindow({
      width: 1100, height: 820, show: false, autoHideMenuBar: true,
      title: 'FeedForge — CustomsForge',
      webPreferences: { session: this.session, nodeIntegration: false, contextIsolation: true,
        sandbox: true, webSecurity: true, navigateOnDragDrop: false, backgroundThrottling: false }
    });
    this.attachWindow(win, job);
    return win;
  }

  attachWindow(win, job) {
    if (this.disposed || job?.finished) { win.destroy(); return; }
    this.windows.add(win);
    if (job) job.windows.add(win);
    const wc = win.webContents;
    wc.setAudioMuted(true);
    const guard = (event, url) => {
      if (!allowedNavigation(url)) {
        event.preventDefault();
        this.diagnostic({ code: 'navigation_blocked', stage: 'browser', outcome: 'unsupported' });
        if (job) this.attention(job, 'This destination is not supported in the first version.');
      }
    };
    wc.on('will-navigate', guard);
    wc.on('will-redirect', guard);
    wc.on('will-attach-webview', (event) => event.preventDefault());
    wc.on('did-finish-load', () => {
      if (!job && win === this.searchWindow) void this.readConnection(win);
    });
    wc.setWindowOpenHandler(({ url }) => {
      if (!job || !allowedNavigation(url)) return { action: 'deny' };
      return { action: 'allow', overrideBrowserWindowOptions: { show: false, autoHideMenuBar: true,
        webPreferences: { session: this.session, nodeIntegration: false, contextIsolation: true,
          sandbox: true, webSecurity: true, backgroundThrottling: false } } };
    });
    wc.on('did-create-window', (child) => this.attachWindow(child, job));
    win.on('close', (event) => {
      if (!this.disposed && (!job || !job.finished)) { event.preventDefault(); win.hide(); }
    });
    win.on('closed', () => { this.windows.delete(win); if (job) job.windows.delete(win); });
  }

  ensureSearchWindow() {
    if (!this.searchWindow || this.searchWindow.isDestroyed()) this.searchWindow = this.createWindow();
    return this.searchWindow;
  }

  async readConnection(win) {
    if (this.searching || this.disposed || win.isDestroyed()) return;
    const revision = ++this.connectionRead;
    const url = win.webContents.getURL();
    if (!allowedNavigation(url)) return;
    try {
      for (let i = 0; i < 12; i++) {
        if (revision !== this.connectionRead || this.searching || this.disposed || win.isDestroyed() || win.webContents.getURL() !== url) return;
        const result = await win.webContents.executeJavaScript(`(${readSearchPage.toString()})()`);
        if (revision !== this.connectionRead || this.searching || this.disposed || win.isDestroyed() || win.webContents.getURL() !== url) return;
        // Inspect only the already-loaded page after a normal login/navigation.
        // An incomplete OAuth/table render is inconclusive, never proof of login.
        if (result.status === 'ready') { this.updateConnection('connected', 'Connected to CustomsForge'); return; }
        if (result.status === 'login_required' || result.status === 'challenge') {
          this.lastSearch = null;
          this.updateConnection(result.status === 'challenge' ? 'challenge' : 'signed_out',
            result.status === 'challenge' ? 'Complete the check in the browser, then search again.' : 'Sign in in the browser, then search again.');
          return;
        }
        if (result.status !== 'layout_changed') return;
        await pause(300);
      }
    } catch { /* The page may be replaced while its normal login redirect finishes. */ }
  }

  async signIn() {
    const win = this.ensureSearchWindow();
    win.show(); win.focus();
    if (!win.webContents.getURL()) await this.navigate(win, CF);
    return { ok: true };
  }

  showBrowser() {
    const candidates = this.active ? [...this.active.windows].filter((w) => !w.isDestroyed()) : [];
    const win = candidates.at(-1) || this.ensureSearchWindow();
    win.show(); win.focus();
    return { ok: true };
  }

  async navigate(win, url) {
    if (!allowedNavigation(url)) throw new Error('Unsupported destination.');
    // ERR_ABORTED is expected when a navigation turns into a file download.
    let timer;
    try { await Promise.race([win.loadURL(url), new Promise((_resolve, reject) => {
      timer = setTimeout(() => { if (!win.isDestroyed()) win.webContents.stop(); reject(new Error('Page load timed out.')); }, 30000);
    })]); } catch (error) {
      if (error.code !== 'ERR_ABORTED' && error.errno !== -3) throw new Error('The page could not be loaded. Open the browser and try again.');
    } finally { clearTimeout(timer); }
  }

  async search({ query, page = 1 }) {
    const url = searchUrl(query, page);
    if (this.searching) throw new Error('A search is already running.');
    this.searching = true;
    this.connectionRead++;
    const started = Date.now();
    this.diagnostic({ code: 'search_started', stage: 'search', host: 'customsforge', outcome: 'started' });
    try {
      const win = this.ensureSearchWindow();
      let paging = false;
      if (page > 1 || (this.lastSearch?.query === query.trim() && this.lastSearch.page > 1 && page === this.lastSearch.page - 1)) {
        if (this.lastSearch?.query !== query.trim() || Math.abs(this.lastSearch.page - page) !== 1) {
          throw new Error('Start with the first search page, then use Next or Previous.');
        }
        const changed = await win.webContents.executeJavaScript(`(${requestSearchPage.toString()})(${JSON.stringify({direction: page > this.lastSearch.page ? 'next' : 'previous'})})`, true);
        if (changed.status !== 'clicked') throw new Error(changed.error || 'The next page is unavailable.');
        paging = true;
      } else await this.navigate(win, url);
      let result;
      for (let i = 0; i < 24; i++) {
        if (win.isDestroyed()) throw new Error('The search window closed. Try again.');
        try { result = await win.webContents.executeJavaScript(`(${readSearchPage.toString()})()`); }
        catch { await pause(300); continue; }
        if (result.status !== 'layout_changed' && (!paging || result.status !== 'ready' || result.page === page)) break;
        await pause(300);
      }
      if (!result) throw new Error('Could not read search results. Try again.');
      if (paging && result.status === 'ready' && result.page !== page) throw new Error('The page did not change. Try again.');
      if (result.status === 'ready') {
        this.lastSearch = { query: query.trim(), page: result.page || page };
        this.updateConnection('connected', 'Connected to CustomsForge');
      }
      else if (result.status === 'login_required' || result.status === 'challenge') {
        this.lastSearch = null;
        this.updateConnection(result.status === 'challenge' ? 'challenge' : 'signed_out',
          result.status === 'challenge' ? 'Complete the check in the browser, then search again.' : 'Sign in in the browser, then search again.');
        win.show(); win.focus();
      } else {
        this.lastSearch = null;
        this.updateConnection('error', 'The search page could not be read. Open the browser to check it.');
      }
      this.diagnostic({ code: 'search_finished', stage: 'search', host: 'customsforge', outcome: result.status, durationMs: Date.now() - started });
      return { ...result, page };
    } catch (error) {
      this.lastSearch = null;
      this.updateConnection('error', 'The search could not be completed. Try again or open the browser.');
      this.diagnostic({ code: 'search_failed', stage: 'search', host: 'customsforge', outcome: 'failed', durationMs: Date.now() - started });
      throw error;
    } finally { this.searching = false; }
  }

  attention(job, message) {
    if (job.finished || job.attention === message) return;
    job.attention = message;
    this.diagnostic({ code: 'browser_attention', stage: 'needs_attention', host: job.host, outcome: 'needs_attention' });
    job.onAttention(message);
    this.showBrowser();
  }

  cancelTransfer(job) {
    if (!job.item || job.cancelScheduled) return;
    job.cancelScheduled = true;
    // Chromium forbids reentering its DownloadItem observer list from an
    // updated/done callback. Cancel on the next event-loop turn, then continue
    // waiting for the actual terminal event before touching files/windows.
    setImmediate(() => {
      if (!job.transferFinished && ['progressing', 'interrupted'].includes(job.item.getState())) job.item.cancel();
    });
  }

  acceptDownload(event, item, contents) {
    const job = this.active;
    const ownsWindow = job && [...job.windows].some((win) => !win.isDestroyed() && win.webContents === contents);
    if (!job || job.finished || !ownsWindow || job.item ||
        !allowedDownload(item.getURL(), item.getFilename(), item.getTotalBytes())) {
      event.preventDefault();
      if (job && ownsWindow && !job.item) job.reject(new Error('The host did not return a supported PSARC file.'), 'unsupported');
      return;
    }
    job.item = item;
    job.transferSettled = new Promise((resolve) => { job.onTransferSettled = resolve; });
    item.setSavePath(job.destination);
    job.onProgress(0);
    item.on('updated', (_event, state) => {
      if (job.finished) return;
      if (item.getReceivedBytes() > MAX_BYTES || item.getTotalBytes() > MAX_BYTES) {
        job.reject(new Error('This download exceeds the 512 MB limit.')); this.cancelTransfer(job); return;
      }
      if (state === 'interrupted') {
        job.reject(new Error('The download was interrupted. Retry when the connection is available.'), 'interrupted'); this.cancelTransfer(job); return;
      }
      const total = item.getTotalBytes();
      job.onProgress(total > 0 ? Math.min(99, item.getReceivedBytes() * 100 / total) : 0);
    });
    item.once('done', (_event, state) => {
      job.transferFinished = true;
      job.onTransferSettled();
      if (job.finished) return;
      if (state === 'completed' && item.getReceivedBytes() > 0 && item.getReceivedBytes() <= MAX_BYTES && item.getTotalBytes() <= MAX_BYTES) {
        job.onProgress(100); job.resolve(job.destination);
      } else if (state === 'completed') job.reject(new Error('The downloaded file is empty or exceeds the 512 MB limit.'));
      else job.reject(new Error(state === 'cancelled' ? 'Download cancelled.' : 'The download did not complete.'), state === 'cancelled' ? 'cancelled' : 'interrupted');
    });
  }

  async download(chart, { signal, destination, onProgress = () => {}, onAttention = () => {} }) {
    if (this.active) throw new Error('Another download is already active.');
    if (!/^\d+$/.test(String(chart.id))) throw new Error('Invalid chart.');
    if (!chart.supported) throw new Error('This host is not supported yet.');
    const started = Date.now();
    this.diagnostic({ code: 'download_started', stage: 'download', host: chart.host, outcome: 'started' });
    const job = { windows: new Set(), destination, onProgress, onAttention, clicked: false, host: chart.host,
      finished: false, item: null, attention: null };
    this.active = job;
    const completion = new Promise((resolve, reject) => {
      job.resolve = (value) => { if (!job.finished) { job.finished = true; resolve(value); } };
      job.reject = (error, outcome = 'failed') => { if (!job.finished) { job.finished = true; job.failure = outcome; reject(error); } };
    });
    // Install rejection handling before any asynchronous navigation.
    const guarded = completion.then((value) => ({ value }), (error) => ({ error }));
    const abort = () => {
      job.reject(new Error('Download cancelled.'), 'cancelled');
      this.cancelTransfer(job);
    };
    signal?.addEventListener('abort', abort, { once: true });
    const deadline = setTimeout(() => job.reject(new Error('Download timed out. Retry when you are ready.'), 'timeout'), 10 * 60 * 1000);
    try {
      if (signal?.aborted) abort();
      if (!job.finished) {
        const win = this.createWindow(job);
        // Drive and other login flows share only this app-owned browser session.
        await Promise.race([this.navigate(win, `${CF}/cdlc/${chart.id}`), guarded]);
        if (!job.finished) void this.driveDownload(job, chart).catch(() => job.reject(new Error('Could not follow the download page. Open the browser and retry.')));
      }
      const result = await guarded;
      if (result.error) throw result.error;
      this.diagnostic({ code: 'download_finished', stage: 'download', host: chart.host, outcome: 'success', durationMs: Date.now() - started });
      return result.value;
    } catch (error) {
      this.diagnostic({ code: signal?.aborted ? 'download_cancelled' : 'download_failed', stage: 'download', host: chart.host,
        outcome: signal?.aborted ? 'cancelled' : (job.failure || 'failed'), durationMs: Date.now() - started });
      throw error;
    } finally {
      clearTimeout(deadline);
      signal?.removeEventListener('abort', abort);
      if (!job.finished) job.reject(new Error('Download stopped.'));
      if (job.item && !job.transferFinished) this.cancelTransfer(job);
      // Electron cancellation completes asynchronously. Do not release the job's files yet.
      if (job.transferSettled) await job.transferSettled;
      for (const win of job.windows) if (!win.isDestroyed()) win.destroy();
      this.active = null;
    }
  }

  async driveDownload(job, chart) {
    let refreshed = false;
    let idleTicks = 0;
    while (!job.finished && !this.disposed) {
      if (job.item) { await pause(400); continue; }
      for (const win of [...job.windows]) {
        if (win.isDestroyed()) continue;
        const url = win.webContents.getURL();
        if (!allowedNavigation(url)) continue;
        const host = new URL(url).hostname;
        let result;
        try {
          if (['customsforge.com', 'ignition4.customsforge.com'].includes(host)) {
            if (job.clicked) continue; // Never replay a collection/download click after an ambiguous response.
            result = await win.webContents.executeJavaScript(`(${requestChartDownload.toString()})(${JSON.stringify({ id: String(chart.id) })})`, true);
            if (result.status === 'clicked' || result.status === 'already_clicked') job.clicked = true;
            else if (result.status === 'expired' && !refreshed) {
              refreshed = true; await this.navigate(win, `${CF}/cdlc/${chart.id}`);
            } else if (result.status === 'unsupported' || result.status === 'invalid_request' || result.status === 'click_failed') {
              job.reject(new Error(result.error || 'This chart cannot be downloaded by this version.'), result.status === 'unsupported' ? 'unsupported' : 'failed');
            }
          } else {
            result = await win.webContents.executeJavaScript(`(${hostDownloadAction.toString()})()`, true);
          }
        } catch { continue; } // A document may be replaced while its ordinary navigation completes.
        if (result?.status === 'login_required' || result?.status === 'challenge' || result?.status === 'needs_attention') {
          this.attention(job, result.error || 'Continue in the browser to finish this download.');
        }
      }
      if (++idleTicks === 12 && !job.item) this.attention(job, 'The host needs attention. Continue in the browser; conversion starts after the download.');
      await pause(600);
    }
  }

  dispose() {
    this.disposed = true;
    if (this.active) {
      this.active.reject(new Error('Application closed.'));
      this.cancelTransfer(this.active);
    }
    this.session.removeListener('will-download', this.onDownload);
    for (const win of this.windows) {
      if (!win.isDestroyed() && !this.active?.windows.has(win)) win.destroy();
    }
  }
}

module.exports = { CustomsForgeBrowser, allowedNavigation, allowedDownload, searchUrl, MAX_BYTES };

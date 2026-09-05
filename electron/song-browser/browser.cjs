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
    this.connection = { status: 'unknown', message: '' };
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
    const inspectConnection = () => {
      if (!job && win === this.searchWindow) void this.readConnection(win);
    };
    wc.on('dom-ready', inspectConnection);
    wc.on('did-finish-load', inspectConnection);
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
        const result = await win.webContents.mainFrame.executeJavaScript(`(${readSearchPage.toString()})()`);
        if (revision !== this.connectionRead || this.searching || this.disposed || win.isDestroyed() || win.webContents.getURL() !== url) return;
        // Inspect only the already-loaded page after a normal login/navigation.
        // An incomplete OAuth/table render is inconclusive, never proof of login.
        if (result.status === 'ready') { this.updateConnection('connected', 'Connected to CustomsForge'); return; }
        if (result.status === 'login_required' || result.status === 'challenge') {
          this.lastSearch = null;
          this.updateConnection(result.status === 'challenge' ? 'challenge' : 'signed_out',
            result.status === 'challenge' ? 'Select Open browser to complete the check, then search again.' : 'Select Sign in to reconnect, then search again.');
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
    if (!win.webContents.getURL()) await this.navigate(win, CF, { waitForDocument: true });
    return { ok: true };
  }

  showBrowser() {
    const candidates = this.active ? [...this.active.windows].filter((w) => !w.isDestroyed()) : [];
    const win = candidates.at(-1) || this.ensureSearchWindow();
    win.show(); win.focus();
    if (win === this.searchWindow && !win.webContents.getURL()) {
      return this.navigate(win, CF, { waitForDocument: true }).then(() => ({ ok: true }));
    }
    return { ok: true };
  }

  async navigate(win, url, { waitForDocument = false } = {}) {
    if (!allowedNavigation(url)) throw new Error('Unsupported destination.');
    // Search needs the new document, not every image/ad/frame's load event.
    // Require a fresh main-frame commit so a late event from the previous
    // query cannot make its stale rows look like the requested search.
    let timer;
    const wc = win.webContents;
    let started = false, committed = null, resolveDocument;
    const start = (details, target, inPlace, mainFrame) => {
      if (!(details.isMainFrame ?? mainFrame) || (details.isSameDocument ?? inPlace)) return;
      if ((details.url ?? target) === url) started = true;
      committed = null;
    };
    const commit = (_event, target) => { if (started) committed = target; };
    const ready = () => {
      if (committed && !win.isDestroyed() && wc.getURL() === committed && allowedNavigation(committed)) resolveDocument();
    };
    const documentReady = waitForDocument ? new Promise((resolve) => {
      resolveDocument = resolve;
      wc.on('did-start-navigation', start);
      wc.on('did-navigate', commit);
      wc.on('dom-ready', ready);
    }) : null;
    try { await Promise.race([...(documentReady ? [documentReady] : []), win.loadURL(url), new Promise((_resolve, reject) => {
      timer = setTimeout(() => {
        // Reject first: stop() can synchronously reject loadURL with ERR_ABORTED,
        // which is otherwise a successful navigation-to-download transition.
        reject(new Error('Page load timed out.'));
        try { if (!win.isDestroyed()) win.webContents.stop(); } catch { /* The timed-out window may already be closing. */ }
      }, 30000);
    })]); } catch (error) {
      // A download may abort navigation when Chromium hands off the file.
      // A search abort before document readiness must never accept old rows.
      if (waitForDocument || (error.code !== 'ERR_ABORTED' && error.errno !== -3)) throw new Error('The page could not be loaded. Open the browser and try again.');
    } finally {
      clearTimeout(timer);
      if (waitForDocument) {
        wc.removeListener('did-start-navigation', start);
        wc.removeListener('did-navigate', commit);
        wc.removeListener('dom-ready', ready);
      }
    }
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
        const changed = await win.webContents.mainFrame.executeJavaScript(`(${requestSearchPage.toString()})(${JSON.stringify({direction: page > this.lastSearch.page ? 'next' : 'previous'})})`, true);
        if (changed.status !== 'clicked') throw new Error(changed.error || 'The next page is unavailable.');
        paging = true;
      } else await this.navigate(win, url, { waitForDocument: true });
      let result;
      for (let i = 0; i < 24; i++) {
        if (win.isDestroyed()) throw new Error('The search window closed. Try again.');
        // webContents.executeJavaScript waits for the full window load even
        // after dom-ready. The main frame can read the ready document now.
        try { result = await win.webContents.mainFrame.executeJavaScript(`(${readSearchPage.toString()})()`); }
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
          result.status === 'challenge' ? 'Select Open browser to complete the check, then search again.' : 'Select Sign in to reconnect, then search again.');
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
    if (job.finished || this.disposed || job.item || job.attention === message) return;
    job.attention = message;
    this.diagnostic({ code: 'browser_attention', stage: 'needs_attention', host: job.host, outcome: 'needs_attention' });
    // Surface host steps in FeedForge. Only an explicit Sign in/Open browser
    // action should show or focus a browser, including after transient notices.
    job.onAttention(message);
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
    const canAct = () => !job.finished && !this.disposed && !job.item;
    while (canAct()) {
      for (const win of [...job.windows]) {
        if (!canAct()) return;
        if (win.isDestroyed()) continue;
        const url = win.webContents.getURL();
        if (!allowedNavigation(url)) continue;
        const host = new URL(url).hostname;
        let result;
        try {
          if (['customsforge.com', 'ignition4.customsforge.com'].includes(host)) {
            if (job.clicked) continue; // Never replay a collection/download click after an ambiguous response.
            result = await win.webContents.executeJavaScript(`(${requestChartDownload.toString()})(${JSON.stringify({ id: String(chart.id) })})`, true);
            if (!canAct()) return;
            // A click can legitimately replace its document before the reply.
            // Retain the once-only collection marker, but never act on an
            // expired/error reply belonging to a page the user has left.
            if (result.status === 'clicked' || result.status === 'already_clicked') job.clicked = true;
            else if (win.isDestroyed() || win.webContents.getURL() !== url) continue;
            else if (result.status === 'expired' && !refreshed) {
              refreshed = true; await this.navigate(win, `${CF}/cdlc/${chart.id}`);
            } else if (result.status === 'unsupported' || result.status === 'invalid_request' || result.status === 'click_failed') {
              job.reject(new Error(result.error || 'This chart cannot be downloaded by this version.'), result.status === 'unsupported' ? 'unsupported' : 'failed');
            }
          } else {
            result = await win.webContents.executeJavaScript(`(${hostDownloadAction.toString()})()`, true);
          }
        } catch { continue; } // A document may be replaced while its ordinary navigation completes.
        if (!canAct()) return;
        if (win.isDestroyed() || win.webContents.getURL() !== url) continue;
        if (result?.status === 'login_required' || result?.status === 'challenge' || result?.status === 'needs_attention') {
          this.attention(job, result.error || 'Select Open browser to finish this download.');
        }
      }
      if (!canAct()) return;
      if (++idleTicks === 12 && !job.item) this.attention(job, 'The host may need a step from you. Select Open browser if the download does not start.');
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

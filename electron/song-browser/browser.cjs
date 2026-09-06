// All website interaction stays in this adapter. No cookies or signed URLs leave it.
const { readSearchPage, requestChartDownload, requestSearchPage, prepareSearchUpdates } = require('./dom.cjs');

const CF = 'https://ignition4.customsforge.com';
const MAX_BYTES = 512 * 1024 * 1024;
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
function cancelled(signal) { if (signal?.aborted) { const error = new Error('Search cancelled.'); error.name = 'AbortError'; throw error; } }
function waitCatalogue(ms, signal) {
  cancelled(signal);
  return new Promise((resolve, reject) => {
    const abort = () => { clearTimeout(timer); signal.removeEventListener('abort', abort); const error = new Error('Search cancelled.'); error.name = 'AbortError'; reject(error); };
    const timer = setTimeout(() => { signal?.removeEventListener('abort', abort); resolve(); }, ms);
    signal?.addEventListener('abort', abort, { once: true });
  });
}
const transientCatalogueError = (error) => ['SONG_NETWORK', 'ERR_TIMED_OUT', 'ERR_CONNECTION_RESET', 'ERR_CONNECTION_CLOSED', 'ERR_NETWORK_CHANGED', 'ERR_NAME_NOT_RESOLVED', 'ERR_INTERNET_DISCONNECTED'].includes(error?.code);

function allowedNavigation(value) {
  return require('./hosts.cjs').allowedNavigation(value);
}

function allowedDownload(value, filename, size, context = {}) {
  return require('./hosts.cjs').allowedDownload(value, filename, size, context);
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
    this.documentVersions = new WeakMap();
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
    if (job) wc.on('did-start-navigation', (details, _url, inPlace, mainFrame) => {
      if ((details.isMainFrame ?? mainFrame) && !(details.isSameDocument ?? inPlace)) {
        this.documentVersions.set(wc, (this.documentVersions.get(wc) || 0) + 1);
        if (job?.megaPrepared?.contents === wc) job.megaPrepared = null;
      }
    });
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
        reject(Object.assign(new Error('Page load timed out.'), { code: 'SONG_NETWORK' }));
        try { if (!win.isDestroyed()) win.webContents.stop(); } catch { /* The timed-out window may already be closing. */ }
      }, 30000);
    })]); } catch (error) {
      // A download may abort navigation when Chromium hands off the file.
      // A search abort before document readiness must never accept old rows.
      if (waitForDocument || (error.code !== 'ERR_ABORTED' && error.errno !== -3)) throw Object.assign(new Error('The page could not be loaded. Open the browser and try again.'), { code: error.code });
    } finally {
      clearTimeout(timer);
      if (waitForDocument) {
        wc.removeListener('did-start-navigation', start);
        wc.removeListener('did-navigate', commit);
        wc.removeListener('dom-ready', ready);
      }
    }
  }

  async catalogueOperation(action) {
    const previous = this.catalogueGate || Promise.resolve();
    let release;
    this.catalogueGate = new Promise((resolve) => { release = resolve; });
    await previous;
    try { if (this.disposed) throw new Error('Song browser is closed.'); return await action(); }
    finally { release(); }
  }

  async withCatalogueSignal(signal, action) {
    cancelled(signal); this.catalogueSignal = signal;
    const abort = () => { try { this.searchWindow?.webContents.stop(); } catch { /* Its next checkpoint handles cancellation. */ } };
    signal?.addEventListener('abort', abort, { once: true });
    try { return await action(); }
    finally { signal?.removeEventListener('abort', abort); this.catalogueSignal = null; }
  }

  async search(request, options = {}) {
    const { normalizeSearchRequest, searchIdentity, filterCharts, sortCharts } = require('./catalogue.cjs');
    const normalized = normalizeSearchRequest(request);
    const explicit = { ...normalized, sort: request.sort ? normalized.sort : undefined };
    return this.catalogueOperation(() => this.withCatalogueSignal(options.signal, async () => {
      const filtered = Object.values(normalized.filters).some((value) => Array.isArray(value) ? value.length : Boolean(value));
      if (!filtered) return this._searchPage(explicit);
      const key = searchIdentity({ ...normalized, page: 1 });
      if (normalized.filters.hideConverted || this.filteredSnapshot?.key !== key) {
        const collected = await this._collect({ ...explicit, page: 1 }, options);
        this.filteredSnapshot = { key, ...collected, results: sortCharts(filterCharts(collected.results, normalized.filters), normalized.sort) };
      }
      const snapshot = this.filteredSnapshot;
      const offset = (normalized.page - 1) * 50;
      return { status: 'ready', results: snapshot.results.slice(offset, offset + 50), total: snapshot.results.length,
        sourceTotal: snapshot.sourceTotal, page: normalized.page, hasNext: offset + 50 < snapshot.results.length,
        complete: true, scope: 'complete', request: normalized };
    }));
  }

  async collect(request, options = {}) {
    const { normalizeSearchRequest, filterCharts, sortCharts } = require('./catalogue.cjs');
    const normalized = normalizeSearchRequest({ ...request, page: 1 });
    return this.catalogueOperation(() => this.withCatalogueSignal(options.signal, async () => {
      const result = await this._collect({ ...normalized, sort: request.sort ? normalized.sort : undefined }, options);
      return { ...result, results: sortCharts(filterCharts(result.results, normalized.filters), normalized.sort), request: normalized };
    }));
  }

  async _collect(request, { signal, onProgress = () => {} } = {}) {
    const records = new Map(), signatures = new Set();
    const checkIdentity = (result, page) => {
      if (result.status !== 'ready') return;
      if (result.page != null && result.page !== page) throw new Error('The catalogue returned a different page. Run the search again.');
      if (request.sort && result.sort && (request.sort.field !== result.sort.field || request.sort.direction !== result.sort.direction)) {
        throw new Error('The catalogue sort changed. Run the search again.');
      }
    };
    const started = Date.now();
    const checkpoint = () => {
      cancelled(signal);
      const remaining = 10 * 60 * 1000 - (Date.now() - started);
      if (remaining <= 0) throw new Error('Collecting results timed out. Narrow the search and try again.');
      return remaining;
    };
    let total = null;
    const pageRecords = [];
    for (let page = 1; page <= 100; page++) {
      checkpoint();
      let result;
      for (let attempt = 1; attempt <= 3; attempt++) {
        checkpoint();
        try {
          if (attempt > 1 && page > 1) {
            // Re-anchor ordinary pagination after a failed navigation. Validate
            // the collected prefix before clicking Next again; never skip a page.
            for (let prior = 1; prior < page; prior++) {
              checkpoint();
              const restored = await this._searchPage({ ...request, page: prior });
              checkpoint();
              checkIdentity(restored, prior);
              if (restored.status !== 'ready' || restored.results.map((row) => row.id).join(',') !== pageRecords[prior - 1]
                || (total !== null && restored.total != null && restored.total !== total)) throw new Error('The catalogue changed during retry. Run the search again.');
            }
          }
          checkpoint();
          result = await this._searchPage({ ...request, page });
          checkpoint();
          checkIdentity(result, page); break;
        } catch (error) {
          const remaining = checkpoint();
          if (!transientCatalogueError(error) || attempt === 3) throw error;
          const normalDelay = attempt === 1 ? 1000 : 3000;
          const serviceDelay = Number.isFinite(error.retryAfterMs) && error.retryAfterMs > 0 ? Math.ceil(error.retryAfterMs) : 0;
          const delay = Math.max(normalDelay, serviceDelay);
          if (delay >= remaining) throw new Error(serviceDelay > normalDelay
            ? 'The requested retry wait exceeds the remaining search time. Try again later or narrow the search.'
            : 'Collecting results timed out. Narrow the search and try again.');
          onProgress({ collected: records.size, total, page, retrying: true, attempt: attempt + 1 });
          await waitCatalogue(delay, signal);
        }
      }
      checkpoint();
      if (result.status !== 'ready') throw new Error(result.error || 'Reconnect to CustomsForge before preparing this search.');
      if (total !== null && result.total != null && result.total !== total) throw new Error('The catalogue changed during preparation. Run the search again.');
      if (result.total != null) total = result.total;
      if (total > 5000) throw new Error('This search has more than 5,000 charts. Narrow the artist or title before preparing it.');
      const signature = result.results.map((row) => row.id).join(',');
      if (signatures.has(signature)) throw new Error('A catalogue page repeated. Preparation stopped without downloading songs.');
      signatures.add(signature);
      pageRecords.push(signature);
      for (const row of result.results) {
        if (records.has(row.id)) throw new Error('The catalogue moved between pages. Run the search again.');
        records.set(row.id, row);
      }
      onProgress({ collected: records.size, total, page });
      checkpoint();
      if (!result.hasNext) {
        if (total !== null && records.size !== total) throw new Error('The complete search could not be collected. No batch downloads have started.');
        return { results: [...records.values()], sourceTotal: total ?? records.size, complete: true };
      }
      if (records.size >= 5000) break;
      await waitCatalogue(Math.min(300, checkpoint()), signal);
    }
    throw new Error('The search exceeds the preparation limit. Narrow it and try again.');
  }

  async _searchPage(request) {
    cancelled(this.catalogueSignal);
    const { query, page = 1 } = request;
    const signature = JSON.stringify({ query: query.trim(), sort: request.sort || null });
    const url = searchUrl(query, page);
    if (this.searching) throw new Error('A search is already running.');
    this.searching = true;
    this.connectionRead++;
    const started = Date.now();
    this.diagnostic({ code: 'search_started', stage: 'search', host: 'customsforge', outcome: 'started' });
    try {
      const win = this.ensureSearchWindow();
      let paging = false;
      if (page > 1 || (this.lastSearch?.signature === signature && this.lastSearch.page > 1 && page === this.lastSearch.page - 1)) {
        if (this.lastSearch?.signature !== signature || Math.abs(this.lastSearch.page - page) !== 1) {
          throw new Error('Start with the first search page, then use Next or Previous.');
        }
        const changed = await win.webContents.mainFrame.executeJavaScript(`(() => { const setup = (${prepareSearchUpdates.toString()})(); return setup.status === 'ready' ? (${requestSearchPage.toString()})(${JSON.stringify({direction: page > this.lastSearch.page ? 'next' : 'previous'})}) : setup; })()`, true);
        if (changed.status !== 'clicked') throw new Error(changed.error || 'The next page is unavailable.');
        paging = true;
      } else await this.navigate(win, url, { waitForDocument: true });
      let result;
      for (let i = 0; i < 24; i++) {
        cancelled(this.catalogueSignal);
        if (win.isDestroyed()) throw new Error('The search window closed. Try again.');
        // webContents.executeJavaScript waits for the full window load even
        // after dom-ready. The main frame can read the ready document now.
        try { result = await win.webContents.mainFrame.executeJavaScript(`(${readSearchPage.toString()})()`); }
        catch { await waitCatalogue(300, this.catalogueSignal); continue; }
        if (result.status !== 'layout_changed' && (!paging || result.status !== 'ready' || result.page === page)) break;
        await waitCatalogue(300, this.catalogueSignal);
      }
      if (!result) throw new Error('Could not read search results. Try again.');
      if (paging && result.status === 'ready' && result.page !== page) throw new Error('The page did not change. Try again.');
      // A confirmed, complete zero/one-result search is already in every sort
      // order. Ignition need not re-render that table after a sort click, so
      // waiting for a server-driven row update can time out unnecessarily.
      const trivialOrder = result.status === 'ready' && page === 1 &&
        (!result.page || result.page === 1) && !result.hasNext &&
        result.results.length <= 1 && result.total === result.results.length;
      if (request.sort && trivialOrder) result.sort = { ...request.sort };
      if (result.status === 'ready' && request.sort && !paging && !trivialOrder) {
        const { requestSearchSort } = require('./dom.cjs');
        let applied = false;
        const sortDeadline = Date.now() + 30_000;
        while (Date.now() < sortDeadline) {
          cancelled(this.catalogueSignal);
          const action = await win.webContents.mainFrame.executeJavaScript(`(() => { const setup = (${prepareSearchUpdates.toString()})(); return setup.status === 'ready' ? (${requestSearchSort.toString()})(${JSON.stringify(request.sort)}) : setup; })()`, true);
          if (action.status === 'applied') { applied = true; break; }
          if (!['clicked', 'waiting'].includes(action.status)) throw new Error(action.error || 'The selected catalogue sort is unavailable.');
          await waitCatalogue(350, this.catalogueSignal);
        }
        if (!applied) throw new Error('The catalogue sort did not finish. Try again.');
        for (let attempt = 0; attempt < 24; attempt++) {
          cancelled(this.catalogueSignal);
          result = await win.webContents.mainFrame.executeJavaScript(`(${readSearchPage.toString()})()`);
          if (result.status === 'ready' && (!result.page || result.page === 1)) break;
          await waitCatalogue(300, this.catalogueSignal);
        }
        if (result.status !== 'ready') throw new Error('The sorted catalogue could not be read.');
      }
      if (result.status === 'ready') {
        cancelled(this.catalogueSignal);
        if (result.page != null && result.page !== page) throw new Error('The catalogue returned a different page. Run the search again.');
        if (request.sort && result.sort && (request.sort.field !== result.sort.field || request.sort.direction !== result.sort.direction)) {
          throw new Error('The catalogue sort changed. Run the search again.');
        }
        result.results = result.results.map((row) => ({ ...row, ...require('./hosts.cjs').getHostCapabilities(row.host), id: row.id }));
        if (this.decorateCharts) result.results = await this.decorateCharts(result.results, request, this.catalogueSignal);
        this.lastSearch = { query: query.trim(), signature, page: result.page || page };
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
      cancelled(this.catalogueSignal);
      this.updateConnection('error', 'The search could not be completed. Try again or open the browser.');
      this.diagnostic({ code: 'search_failed', stage: 'search', host: 'customsforge', outcome: 'failed', durationMs: Date.now() - started });
      throw error;
    } finally { this.searching = false; }
  }

  attention(job, message, details = {}) {
    if (job.finished || this.disposed || job.item || job.attention === message) return;
    job.attention = message;
    this.diagnostic({ code: 'browser_attention', stage: 'needs_attention', host: job.host, outcome: 'needs_attention' });
    // Surface host steps in FeedForge. Only an explicit Sign in/Open browser
    // action should show or focus a browser, including after transient notices.
    job.onAttention(details.candidates ? { message, candidates: details.candidates } : message);
    if (job.interactive && !job.shown && !details.candidates) { job.shown = true; this.showBrowser(); }
    if (job.parkOnAttention) {
      const error = new Error(message);
      error.code = 'SONG_ATTENTION'; error.sessionWide = details.sessionWide === true;
      job.reject(error, 'needs_attention');
    }
  }

  chooseFile({ id }) {
    const job = this.active;
    const candidate = job?.candidates?.find((item) => item.id === id);
    if (!candidate || job.finished) throw new Error('This file choice has expired. Retry the song to read its files again.');
    const document = job.candidateDocument;
    if (!document || document.contents.isDestroyed?.() || document.contents.getURL() !== document.url
      || document.version !== (this.documentVersions.get(document.contents) || 0)) throw new Error('This file list has changed. Wait for its current choices.');
    job.choice = { id: candidate.targetId, label: candidate.label, platform: candidate.platform };
    job.requestedChoice = require('./provenance.cjs').sanitizeChoice(candidate);
    job.megaPrepared = null;
    job.attention = null;
    job.onAttention('');
    return { ok: true };
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

  async cancelProvider(job) {
    if (job.host !== 'mega' || job.providerCancelStarted) return;
    job.providerCancelStarted = true;
    this.diagnostic({ code: 'mega_cancel_requested', stage: 'download', host: 'mega', outcome: 'cancelled' });
    let timer;
    const attempts = [...job.windows].filter((win) => !win.isDestroyed()
      && require('./hosts.cjs').hostFromUrl(win.webContents.getURL()) === 'mega').map(async (win) => {
      try { await win.webContents.mainFrame.executeJavaScript(require('./host-actions.cjs').hostCancelScript(), true); }
      catch { /* Window teardown below also stops a replaced/unresponsive page. */ }
    });
    try { await Promise.race([Promise.allSettled(attempts), new Promise((resolve) => { timer = setTimeout(resolve, 2000); })]); }
    finally { clearTimeout(timer); }
  }

  providerProgress(job, result) {
    if (job.host !== 'mega' || job.finished) return false;
    const phase = ['downloading', 'decrypting', 'saving'].includes(result.phase) ? result.phase : 'downloading';
    const observed = Number.isFinite(result.progress) ? Math.min(100, Math.max(0, result.progress)) : null;
    const advanced = (observed !== null && observed > (job.providerPercent ?? -1)) || !job.providerPhases.has(phase);
    if (advanced) job.lastActivity = Date.now();
    job.providerPhases.add(phase);
    if (observed !== null) job.providerPercent = Math.max(job.providerPercent ?? 0, observed);
    const progress = Math.max(job.reportedProgress || 0, phase === 'saving' ? 95 : phase === 'decrypting' ? 94 : Math.min(94, (observed || 0) * 0.94));
    if (progress !== job.reportedProgress || phase !== job.providerPhase) {
      if (phase !== job.providerPhase) this.diagnostic({ code: 'mega_' + phase, stage: 'download', host: 'mega', outcome: 'started' });
      if (phase !== job.providerPhase) job.providerSavingAt = phase === 'saving' ? Date.now() : null;
      job.reportedProgress = progress; job.providerPhase = phase;
      job.onProgress(progress, { phase });
    }
    return advanced;
  }

  checkDownloadDeadline(job, started, now = Date.now()) {
    if (job.finished) return;
    if (now - (job.host === 'mega' ? job.lastActivity : started) >= 10 * 60 * 1000
      || now - started >= (job.host === 'mega' ? 60 : 10) * 60 * 1000) {
      job.reject(new Error('Download timed out. Retry when you are ready.'), 'timeout');
    } else if (job.host === 'mega' && !job.item && job.providerSavingAt != null && now - job.providerSavingAt >= 60000) {
      this.attention(job, 'MEGA has prepared the download, but FeedForge has not received the file. Open the browser to review its download choice.');
    }
  }

  acceptDownload(event, item, contents) {
    const job = this.active;
    const ownsWindow = job && [...job.windows].some((win) => !win.isDestroyed() && win.webContents === contents);
    const prepared = job?.megaPrepared;
    const ownedMegaDocument = job?.host === 'mega' && ownsWindow && prepared?.contents === contents
      && prepared.documentUrl === contents.getURL() && prepared.documentVersion === (this.documentVersions.get(contents) || 0);
    const context = { host: job?.host, allowMacFallback: job?.allowMacFallback === true,
      enableMegaBlob: ownedMegaDocument, ownedWindow: ownedMegaDocument, documentUrl: ownedMegaDocument ? prepared.documentUrl : '' };
    if (job && !job.finished && ownsWindow && !job.item && typeof job.requestedChoice?.label === 'string'
      && job.requestedChoice.label !== item.getFilename()) {
      event.preventDefault();
      const error = new Error('The host returned a different file from the one you chose. Retry and select the intended PSARC version.');
      error.code = 'SONG_ATTENTION';
      job.reject(error, 'needs_attention');
      return;
    }
    if (!job || job.finished || !ownsWindow || job.item ||
        !allowedDownload(item.getURL(), item.getFilename(), item.getTotalBytes(), context)
        || (job.host === 'mega' && (!ownedMegaDocument || item.getFilename() !== prepared.candidate.label))) {
      event.preventDefault();
      if (job && ownsWindow && !job.item) job.reject(new Error('The host did not return a supported PSARC file.'), 'unsupported');
      return;
    }
    if (/_m\.psarc$/i.test(item.getFilename()) && !job.allowMacFallback) {
      event.preventDefault();
      const error = new Error('This is the Mac file. Select the PC PSARC variant.'); error.code = 'SONG_ATTENTION';
      job.reject(error, 'needs_attention'); return;
    }
    job.item = item;
    job.transferSettled = new Promise((resolve) => { job.onTransferSettled = resolve; });
    item.setSavePath(job.destination);
    if (job.host === 'mega') this.providerProgress(job, { phase: 'saving' });
    else job.onProgress(0);
    job.lastActivity = Date.now();
    item.on('updated', (_event, state) => {
      if (job.finished) return;
      if (item.getReceivedBytes() > MAX_BYTES || item.getTotalBytes() > MAX_BYTES) {
        job.reject(new Error('This download exceeds the 512 MB limit.')); this.cancelTransfer(job); return;
      }
      if (state === 'interrupted') {
        job.reject(new Error('The download was interrupted. Retry when the connection is available.'), 'interrupted'); this.cancelTransfer(job); return;
      }
      const total = item.getTotalBytes();
      const received = item.getReceivedBytes();
      if (received > (job.receivedBytes || 0)) job.lastActivity = Date.now();
      job.receivedBytes = received;
      const progress = total > 0 ? Math.min(99, received * 100 / total) : 0;
      job.onProgress(job.host === 'mega' ? 95 + progress * 0.04 : progress, job.host === 'mega' ? { phase: 'saving' } : undefined);
    });
    item.once('done', (_event, state) => {
      job.transferFinished = true;
      job.onTransferSettled();
      if (job.finished) return;
      if (state === 'completed' && item.getReceivedBytes() > 0 && item.getReceivedBytes() <= MAX_BYTES && item.getTotalBytes() <= MAX_BYTES) {
        const descriptor = require('./provenance.cjs').sanitizeFileEvidence({
          ...(job.megaPrepared?.candidate || job.resolvedFile || {}),
          filename: item.getFilename(), label: item.getFilename(), sizeBytes: item.getReceivedBytes(),
          platform: /_p\.psarc$/i.test(item.getFilename()) ? 'pc' : /_m\.psarc$/i.test(item.getFilename()) ? 'mac' : 'unknown',
          evidence: { ...(job.megaPrepared?.candidate || job.resolvedFile)?.evidence, filename: 'observed', platform: 'filename_hint', sizeBytes: 'observed' },
        });
        job.onResolvedFile?.(descriptor, job.requestedChoice);
        job.onProgress(100, job.host === 'mega' ? { phase: 'saving' } : undefined); job.resolve(job.destination);
      } else if (state === 'completed') job.reject(new Error('The downloaded file is empty or exceeds the 512 MB limit.'));
      else job.reject(new Error(state === 'cancelled' ? 'Download cancelled.' : 'The download did not complete.'), state === 'cancelled' ? 'cancelled' : 'interrupted');
    });
  }

  async download(chart, { signal, destination, onProgress = () => {}, onAttention = () => {}, onResolvedFile, parkOnAttention = false, interactive = false }) {
    if (this.active) throw new Error('Another download is already active.');
    if (!/^\d+$/.test(String(chart.id))) throw new Error('Invalid chart.');
    if (!chart.supported) throw new Error('This host is not supported yet.');
    const started = Date.now();
    this.diagnostic({ code: 'download_started', stage: 'download', host: chart.host, outcome: 'started' });
    const job = { windows: new Set(), destination, onProgress, onAttention, onResolvedFile, requestedChoice: chart.selection?.choice, clicked: false, host: chart.host,
      finished: false, item: null, attention: null, parkOnAttention, interactive, choice: chart.selection?.choice,
      allowMacFallback: chart.selection?.allowMacFallback === true, lastActivity: started, providerPhases: new Set() };
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
    const deadline = setInterval(() => {
      // MEGA fetches/decrypts before DownloadItem exists. Advancing progress
      // extends the stall deadline, with a bounded total lifetime for each job.
      this.checkDownloadDeadline(job, started);
    }, 1000);
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
      clearInterval(deadline);
      signal?.removeEventListener('abort', abort);
      if (!job.finished) job.reject(new Error('Download stopped.'));
      if (job.item && !job.transferFinished) this.cancelTransfer(job);
      if (job.failure || signal?.aborted) await this.cancelProvider(job);
      // Electron cancellation completes asynchronously. Do not release the job's files yet.
      if (job.transferSettled) await job.transferSettled;
      for (const win of job.windows) if (!win.isDestroyed()) win.destroy();
      this.active = null;
    }
  }

  async driveDownload(job, chart) {
    let refreshed = false;
    let idleTicks = 0;
    const lastLocations = new WeakMap();
    const canAct = () => !job.finished && !this.disposed && !job.item;
    while (canAct()) {
      for (const win of [...job.windows]) {
        if (!canAct()) return;
        if (win.isDestroyed()) continue;
        const url = win.webContents.getURL();
        const documentVersion = this.documentVersions.get(win.webContents) || 0;
        if (url !== lastLocations.get(win)) { lastLocations.set(win, url); idleTicks = 0; }
        if (!allowedNavigation(url)) continue;
        const host = new URL(url).hostname;
        let result;
        try {
          if (['customsforge.com', 'ignition4.customsforge.com'].includes(host)) {
            if (job.clicked) continue; // Never replay a collection/download click after an ambiguous response.
            const registry = require('./hosts.cjs');
            const supportedHosts = Object.keys(registry.HOSTS).filter((host) => registry.getHostCapabilities(host).supported);
            result = await win.webContents.mainFrame.executeJavaScript(`(${requestChartDownload.toString()})(${JSON.stringify({ id: String(chart.id), supportedHosts })})`, true);
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
            if (job.choice && job.candidateDocument) {
              const owned = job.candidateDocument;
              if (owned.contents !== win.webContents) continue;
              if (owned.url !== url || owned.version !== documentVersion) {
                job.reject(new Error('The file list changed after your choice. Retry to choose from its current files.'), 'needs_attention');
                return;
              }
            }
            const prepared = job.megaPrepared;
            const matches = prepared?.contents === win.webContents && prepared.documentUrl === url && prepared.documentVersion === documentVersion;
            result = await win.webContents.mainFrame.executeJavaScript(require('./host-actions.cjs').hostActionScript({
              ...chart.selection, choice: matches ? { ...prepared.candidate } : job.choice,
              allowMacFallback: job.allowMacFallback, prepareOnly: job.host === 'mega' && !matches,
              expectedFile: matches ? prepared.candidate : undefined,
            }), true);
          }
        } catch { continue; } // A document may be replaced while its ordinary navigation completes.
        if (!canAct()) return;
        if (documentVersion !== (this.documentVersions.get(win.webContents) || 0)) continue;
        if (result?.selectedFile && result.status === 'clicked') { job.selectedFile = result.selectedFile; job.resolvedFile = result.selectedFile; job.choice = undefined; job.candidates = []; job.onAttention(''); }
        if (result?.status === 'clicked') idleTicks = 0;
        if (win.isDestroyed() || win.webContents.getURL() !== url) continue;
        if (job.host === 'mega' && require('./hosts.cjs').hostFromUrl(url) === 'mega') {
          if (result?.status === 'prepared') {
            const selection = require('./file-selection.cjs').selectFileCandidate([result.selectedFile], { allowMacFallback: job.allowMacFallback });
            if (selection.status === 'selected') {
              job.selectedFile = selection.candidate;
              job.megaPrepared = { contents: win.webContents, documentUrl: url, documentVersion, candidate: selection.candidate };
              this.diagnostic({ code: 'mega_file_prepared', stage: 'download', host: 'mega', outcome: 'ready' });
              job.candidates = []; job.choice = undefined; job.attention = null; job.onAttention('');
              job.lastActivity = Date.now(); idleTicks = 0;
            } else this.attention(job, 'Choose a PC PSARC file before starting the MEGA download.');
          }
          if (result?.status === 'transferring') {
            this.providerProgress(job, result);
            // Active transfers use the progress-based stall deadline above.
            idleTicks = 0;
          }
        }
        if (result?.status === 'choose_file') {
          job.megaPrepared = null;
          let candidates = (result.candidates || []).slice(0, 100);
          {
            candidates = candidates.filter((candidate) => candidate && /^[a-zA-Z0-9_-]{1,100}$/.test(candidate.id || '')
              && typeof candidate.label === 'string' && candidate.label.length <= 240 && /\.psarc$/i.test(candidate.label) && !/[\\/\x00-\x1f\x7f]/.test(candidate.label))
              .map((candidate) => require('./file-selection.cjs').sanitizeFileCandidate(candidate)).filter(Boolean);
            const signature = JSON.stringify(candidates);
            const previous = job.candidateDocument;
            if (!previous || previous.contents !== win.webContents || previous.url !== url || previous.version !== documentVersion || previous.signature !== signature) {
              job.candidates = candidates.map((candidate) => ({ ...candidate, targetId: candidate.id, id: require('node:crypto').randomUUID() }));
              job.candidateDocument = { contents: win.webContents, url, version: documentVersion, signature };
              job.attention = null;
            }
          }
          this.attention(job, result.error || 'Choose the PSARC file in FeedForge.', { candidates: job.candidates.map((candidate) => require('./file-selection.cjs').sanitizeFileCandidate(candidate)).filter(Boolean) });
        }
        if (result?.status === 'login_required' || result?.status === 'challenge' || result?.status === 'needs_attention') {
          this.attention(job, result.error || 'Select Open browser to finish this download.', { sessionWide: ['customsforge.com', 'ignition4.customsforge.com'].includes(host) });
        }
      }
      if (!canAct()) return;
      if (++idleTicks === (job.parkOnAttention ? 100 : 12) && !job.item) this.attention(job, 'The host may need a step from you. Select Open browser if the download does not start.');
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

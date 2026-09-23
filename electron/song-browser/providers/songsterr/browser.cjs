'use strict';

const fs = require('node:fs');
const fsp = fs.promises;
const path = require('node:path');
const crypto = require('node:crypto');
const { readSongsterrPage, actOnSongsterrPage } = require('./dom.cjs');
const { ORIGIN, MAX_TOTAL_BYTES, failure, check, clean, numeric, sourceFilename, songUrl, safeResult, allowedNavigation, allowedDownload, publicAudio, delay, bounded } = require('./policy.cjs');
const { acquireAnonymous } = require('./acquire.cjs');
const { retrieveSynchronization, unavailableSynchronization, audioVideo } = require('./synchronization.cjs');

class SongsterrProvider {
  constructor({ BrowserWindow, session, profilePath, onConnection = () => {}, onDiagnostic = () => {}, parent }) {
    if (!BrowserWindow || !session || !path.isAbsolute(profilePath || '')) throw new Error('Songsterr needs a private browser profile.');
    this.BrowserWindow = BrowserWindow; this.parent = parent; this.profilePath = profilePath;
    this.onConnection = onConnection; this.onDiagnostic = onDiagnostic;
    // The anonymous partition is unique per provider lifetime, never persisted
    // and never reused for login. The account profile is separate from CF.
    this.anonymousSession = session.fromPartition(`songsterr-anonymous-${crypto.randomUUID()}`, { cache: false });
    this.accountSession = session.fromPath(profilePath);
    for (const owned of [this.anonymousSession, this.accountSession]) {
      owned.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
      owned.setPermissionCheckHandler(() => false);
    }
    this.connection = { status: 'anonymous', message: 'Public Songsterr search is available. Anonymous tab import is experimental.' };
    this.windows = new Set(); this.searchWindow = null; this.accountWindow = null; this.results = new Map(); this.resolved = new Map();
    this.disposed = false; this.searching = false; this.resolving = false; this.acquiring = false; this.controllers = new Set();
    this.copies = null; this.journalPath = path.join(profilePath, 'feedforge-copy-journal.json');
    this.rejectAnonymousDownload = (event) => event.preventDefault();
    this.guardAccountDownload = (event, _item, contents) => { if (!this.exportOwner || this.exportOwner !== contents) event.preventDefault(); };
    this.anonymousSession.on('will-download', this.rejectAnonymousDownload);
    this.accountSession.on('will-download', this.guardAccountDownload);
  }
  diagnostic(code, outcome) { try { this.onDiagnostic({ code, stage: 'songsterr', host: 'songsterr', outcome }); } catch {} }
  setConnection(status, message) {
    this.connection = { status, message }; try { this.onConnection(this.connection); } catch {}
  }
  _controller(signal) {
    check(signal);
    if (this.disposed) throw failure('unavailable', 'The Songsterr browser is closed.');
    const controller = new AbortController();
    const abort = () => controller.abort(); signal?.addEventListener('abort', abort, { once: true }); this.controllers.add(controller);
    return { signal: controller.signal, release: () => { signal?.removeEventListener('abort', abort); this.controllers.delete(controller); } };
  }
  _window(account = false) {
    const existing = account ? this.accountWindow : this.searchWindow;
    if (existing && !existing.isDestroyed()) return existing;
    if (this.disposed) throw failure('unavailable', 'The Songsterr browser is closed.');
    const win = new this.BrowserWindow({ width: 1100, height: 820, show: false, autoHideMenuBar: true, title: 'FeedForge — Songsterr',
      webPreferences: { session: account ? this.accountSession : this.anonymousSession, nodeIntegration: false,
        contextIsolation: true, sandbox: true, webSecurity: true, navigateOnDragDrop: false, backgroundThrottling: false } });
    this.windows.add(win); if (account) this.accountWindow = win; else this.searchWindow = win;
    const wc = win.webContents;
    wc.setAudioMuted(true);
    const guard = (event, url) => { if (!allowedNavigation(url)) { event.preventDefault(); this.diagnostic('navigation_blocked', 'unsupported'); } };
    wc.on('will-navigate', guard); wc.on('will-redirect', guard);
    wc.on('will-attach-webview', (event) => event.preventDefault());
    wc.setWindowOpenHandler(() => ({ action: 'deny' }));
    win.on('close', (event) => { if (!this.disposed) { event.preventDefault(); win.hide(); } });
    win.on('closed', () => this.windows.delete(win));
    if (account) wc.on('did-finish-load', () => {
      if (!this.acquiring) void this._read(win).then((page) => {
        if (page.status === 'needs_login' || page.signedOut) this.setConnection('signed_out', 'Sign in on Songsterr to use account export.');
        else if (page.status === 'ready') this.setConnection('connected', 'Songsterr account page is available.');
      }).catch(() => {});
    });
    return win;
  }
  async _navigate(win, url, signal) {
    check(signal);
    if (!allowedNavigation(url)) throw failure('invalid_result', 'That Songsterr destination is not allowed.');
    const wc = win.webContents;
    try { await bounded(wc.loadURL(url), signal, 30000, () => wc.stop()); }
    catch (error) {
      check(signal); if (error.source === 'songsterr') throw error;
      throw failure('network_error', 'The Songsterr page did not load. Please retry.');
    }
    check(signal);
    if (!allowedNavigation(wc.getURL())) throw failure('unavailable', 'Songsterr redirected to an unsupported page.');
  }
  async _execute(win, fn, request, signal, timeout = 10000) {
    check(signal);
    if (this.disposed || win.isDestroyed()) throw failure('unavailable', 'The Songsterr page closed.');
    if (!allowedNavigation(win.webContents.getURL())) throw failure('unavailable', 'The Songsterr page is not ready.');
    return bounded(win.webContents.mainFrame.executeJavaScript(`(${fn.toString()})(${JSON.stringify(request || {})})`), signal, timeout, () => win.webContents.stop());
  }
  _read(win, signal) { return this._execute(win, readSongsterrPage, null, signal); }
  _act(win, action, signal, identity = {}, timeout) { return this._execute(win, actOnSongsterrPage, { ...identity, action }, signal, timeout); }
  async _wait(win, predicate, signal, timeout = 15000) {
    const until = Date.now() + timeout;
    while (Date.now() < until) {
      check(signal); const page = await this._read(win, signal);
      if (page.status === 'needs_attention') {
        this.attentionWindow = win;
        throw failure('needs_attention', 'Complete the Songsterr website check in its browser.');
      }
      if (this.attentionWindow === win) this.attentionWindow = null;
      if (predicate(page)) return page;
      await delay(300, signal);
    }
    throw failure('timeout', 'The expected Songsterr page did not become ready. Please retry.');
  }
  async _readyPinnedPage(win, id, revisionId, signal) {
    try {
      const page = await this._wait(win, (value) => value.status === 'needs_login'
        || (value.songId === id && value.revisionId === revisionId && value.tabReady === true), signal);
      if (page.status === 'needs_login') throw failure('needs_login', 'Songsterr is requesting sign-in to load this tab.');
      return page;
    } catch (error) {
      if (error.code === 'timeout') throw failure('revision_unavailable', 'The approved Songsterr tab did not finish loading. Please retry.');
      throw error;
    }
  }
  async _discoverAudio(win, identity, initialPage, signal, timeout = 8000) {
    let page = initialPage, pauseNeeded = false;
    const staleMixVideos = new Set();
    const until = Date.now() + timeout;
    const validate = (value) => {
      check(signal);
      if (value.status === 'needs_attention') {
        this.attentionWindow = win;
        throw failure('needs_attention', 'Complete the Songsterr website check in its browser.');
      }
      if (value.status === 'needs_login') throw failure('needs_login', 'Songsterr is requesting sign-in to load its original audio.');
      if (value.status !== 'ready') throw failure('unavailable', 'The Songsterr audio player is unavailable. Please retry.');
      const parsed = songUrl(value.url), current = songUrl(win.webContents.getURL());
      if (value.songId !== identity.songId || value.revisionId !== identity.revisionId
        || parsed?.id !== identity.songId || parsed.revisionId !== identity.revisionId
        || current?.id !== identity.songId || current.revisionId !== identity.revisionId) {
        throw failure('revision_unavailable', 'The Songsterr tab changed while checking its original audio. Please retry.');
      }
    };
    const audio = () => page.tabReady && page.originalSelected && (page.audioMix === null || page.audioMix === 'main')
      ? (page.audio || []).map(publicAudio).find((value) => value && !staleMixVideos.has(value.videoId)) || null : null;
    const absent = (reason) => {
      this.diagnostic('audio_probe_' + reason, 'unavailable');
      this.diagnostic('audio_probe_original_control', page.originalAvailable ? 'ready' : 'unavailable');
      this.diagnostic('audio_probe_original_selected', page.originalSelected ? 'ready' : 'unavailable');
      this.diagnostic('audio_probe_full_mix', page.audioMix === null || page.audioMix === 'main' ? 'ready' : 'unavailable');
      this.diagnostic('audio_probe_play_control', page.canPlay ? 'ready' : 'unavailable');
      this.diagnostic('audio_probe_play_enabled', page.playEnabled ? 'ready' : 'unavailable');
      const eligibility = ['true', 'false', 'missing', 'other'].includes(page.playEligibility) ? page.playEligibility : 'missing';
      this.diagnostic('audio_probe_play_attribute_' + eligibility, 'ready');
      this.diagnostic('audio_probe_iframe', (page.audio || []).length ? 'ready' : 'unavailable');
      return null;
    };
    const found = () => { this.diagnostic('audio_probe_found', 'success'); return audio(); };
    const act = async (action) => {
      const outcome = await this._act(win, action, signal, identity);
      if (!outcome.ok) { page = await this._read(win, signal); validate(page); }
      return outcome.ok;
    };
    const poll = async (predicate) => {
      while (Date.now() < until) {
        page = await this._read(win, signal); validate(page);
        if (predicate(page)) return true;
        await delay(Math.min(300, Math.max(1, until - Date.now())), signal);
      }
      return false;
    };
    try {
      this.diagnostic('audio_probe_started', 'started');
      validate(page); pauseNeeded = page.playing === true;
      // The track mixer can become ready before the independent audio
      // controls. Give those controls the same bounded discovery window.
      if (!page.originalAvailable && !await poll((value) => value.originalAvailable)) return absent('original_unavailable');
      if (!page.originalSelected) {
        pauseNeeded = true;
        if (!await act('selectOriginal')) return absent('original_action_failed');
        if (!await poll((value) => value.originalSelected)) return absent('original_selection_timeout');
      }
      if (page.audioMix !== null && page.audioMix !== 'main') {
        for (const url of page.audio || []) { const previous = publicAudio(url); if (previous) staleMixVideos.add(previous.videoId); }
        if (!page.fullMixAvailable && !await poll((value) => value.fullMixAvailable || value.audioMix === null || value.audioMix === 'main')) return absent('full_mix_unavailable');
        if (page.audioMix !== null && page.audioMix !== 'main') {
          pauseNeeded = true;
          if (!await act('selectFullMix')) return absent('full_mix_action_failed');
          if (!await poll((value) => value.audioMix === 'main')) return absent('full_mix_selection_timeout');
        }
      }
      if (audio()) return found();
      if (!page.canPlay && !await poll((value) => value.canPlay || Boolean(audio()))) return absent('play_unavailable');
      if (audio()) return found();
      // Play is an ordinary, muted page action that materializes the linked
      // iframe. Mark cleanup before the click, including ambiguous responses.
      pauseNeeded = true;
      this.diagnostic('audio_probe_play_requested', 'started');
      if (!await act('play')) return absent('play_action_failed');
      this.diagnostic('audio_probe_play', 'success');
      return await poll(() => Boolean(audio())) ? found() : absent(staleMixVideos.size ? 'stale_mix_timeout' : 'iframe_timeout');
    } finally {
      if (pauseNeeded && !this.disposed && !win.isDestroyed()) {
        // Cleanup must still run after cancellation; the guarded page action
        // can only pause the exact pinned tab and never starts playback.
        try {
          const paused = await this._act(win, 'pause', undefined, identity, 2000);
          if (!paused.ok) this.diagnostic('audio_probe_pause', 'unavailable');
        } catch { this.diagnostic('audio_probe_pause', 'unavailable'); }
      }
    }
  }
  async signIn() {
    const win = this._window(true); win.show(); win.focus();
    if (!win.webContents.getURL()) await this._navigate(win, ORIGIN + '/signin');
    else if (!this.acquiring) {
      const page = await this._read(win);
      if (page.signedOut && page.status !== 'needs_login') await this._navigate(win, ORIGIN + '/signin');
    }
    return { ok: true };
  }
  async showBrowser() {
    const attention = this.attentionWindow && !this.attentionWindow.isDestroyed() ? this.attentionWindow : null;
    const win = attention || (this.accountWindow && !this.accountWindow.isDestroyed() ? this.accountWindow : this._window(false));
    win.show(); win.focus(); if (!win.webContents.getURL()) await this._navigate(win, ORIGIN + '/'); return { ok: true };
  }
  // Main-process ledger recovery only. Deliberately not exposed as an IPC or
  // renderer action: normal imports still originate from observed searches.
  restoreTrustedResult(chart) {
    if (chart?.source !== 'songsterr') throw failure('invalid_result', 'The saved import is not a Songsterr result.');
    const result = safeResult(chart);
    this.results.set(result.id, result);
    while (this.results.size > 5000) this.results.delete(this.results.keys().next().value);
    return { ...result };
  }
  async search(request = {}, { signal, onProgress = () => {} } = {}) {
    const query = clean(request.query, 161);
    if (query.length < 2 || query.length > 160) throw failure('invalid_query', 'Enter between 2 and 160 characters to search.');
    if (this.searching || this.resolving) throw failure('busy', 'The Songsterr catalogue is already being read.');
    const operation = this._controller(signal); this.searching = true;
    try {
      const win = this._window(false), url = new URL(ORIGIN); url.searchParams.set('pattern', query);
      await this._navigate(win, url.href, operation.signal);
      const page = await this._wait(win, (value) => value.searchReady || value.status === 'needs_login', operation.signal);
      if (page.status === 'needs_login') return { status: 'needs_login', results: [], error: 'Songsterr is requesting sign-in.' };
      const results = [];
      for (const value of (page.results || []).slice(0, 1000)) {
        try { const result = safeResult(value); this.results.set(result.id, result); results.push(result); } catch {}
      }
      while (this.results.size > 5000) this.results.delete(this.results.keys().next().value);
      const field = typeof request.sort === 'string' ? request.sort : request.sort?.field;
      const direction = request.direction || request.sort?.direction || 'asc';
      if (['title', 'artist'].includes(field)) results.sort((a, b) => a[field].localeCompare(b[field], undefined, { sensitivity: 'base', numeric: true }) * (direction === 'desc' ? -1 : 1));
      onProgress({ collected: results.length, total: results.length, page: 1 });
      return { status: 'ready', results, page: 1, hasNext: false, total: results.length, complete: !page.hasMore,
        sortScope: 'loaded_results', ...(page.hasMore ? { message: 'Showing the loaded Songsterr results. Narrow the search for more specific matches.' } : {}) };
    } finally { this.searching = false; operation.release(); }
  }
  async resolve(result, { signal } = {}) {
    if (this.searching || this.resolving) throw failure('busy', 'The Songsterr catalogue is already being read.');
    const id = numeric(result?.id ?? result?.songId);
    const registered = id && this.results.get(id);
    if (!registered) throw failure('invalid_result', 'Search for this Songsterr song before importing it.');
    if (result.url && songUrl(result.url)?.url !== registered.url) throw failure('invalid_result', 'The selected Songsterr song changed. Search again.');
    const operation = this._controller(signal); this.resolving = true;
    try {
      const win = this._window(false);
      await this._navigate(win, registered.url, operation.signal);
      let page;
      try {
        page = await this._wait(win, (value) => value.songId === id && (value.canOpenHistory || value.historyVisible), operation.signal);
      } catch (error) {
        if (error.code === 'timeout') throw failure('revision_controls_unavailable', 'Songsterr loaded, but its revision-history control did not become ready. Please retry.');
        throw error;
      }
      if (!page.historyVisible) {
        const action = await this._act(win, 'history', operation.signal);
        if (!action.ok) throw failure('revision_controls_unavailable', 'Songsterr’s revision-history button could not be opened. Please retry.');
      }
      try {
        page = await this._wait(win, (value) => value.songId === id && value.historyReady === true, operation.signal);
      } catch (error) {
        if (error.code === 'timeout') throw failure('revision_history_unavailable', 'Songsterr opened revision history, but its revision rows did not become ready. Please retry.');
        throw error;
      }
      const revisions = (page.approvedRevisions || []).filter((revision) => numeric(revision.revisionId) && revision.approval === 'approved');
      // History is displayed newest first. Use dates when every row supplies a
      // valid date; otherwise retain the observed order, not numeric ID guesses.
      if (revisions.every((revision) => revision.date && Number.isFinite(Date.parse(revision.date)))) revisions.sort((a, b) => Date.parse(b.date) - Date.parse(a.date));
      if (!revisions.length) throw failure('unapproved_revision', 'Songsterr’s revision history loaded, but no approved revision with a matching tab link could be verified.');
      const revisionId = String(revisions[0].revisionId), url = `${registered.url}/r${revisionId}`;
      await this._navigate(win, url, operation.signal);
      page = await this._readyPinnedPage(win, id, revisionId, operation.signal);
      const audio = await this._discoverAudio(win, { songId: id, revisionId }, page, operation.signal);
      const descriptor = { ...registered, revisionId, approval: 'approved', approvedUrl: url, ...(audio ? { audio } : {}) };
      this.resolved.set(`${id}:${revisionId}`, descriptor);
      return descriptor;
    } finally { this.resolving = false; operation.release(); }
  }
  // A cached, verified score must retain its original revision when retrying
  // linked audio. No history lookup, account copy or score request is needed.
  async findAudio(result, { revisionId, signal } = {}) {
    if (this.searching || this.resolving) throw failure('busy', 'The Songsterr catalogue is already being read.');
    const id = numeric(result?.id ?? result?.songId), revision = numeric(revisionId);
    const registered = id && this.results.get(id);
    if (!registered || !revision || (result.url && songUrl(result.url)?.url !== registered.url)) {
      throw failure('invalid_result', 'The saved Songsterr song or revision could not be verified.');
    }
    const operation = this._controller(signal); this.resolving = true;
    try {
      const win = this._window(false);
      await this._navigate(win, `${registered.url}/r${revision}`, operation.signal);
      const page = await this._readyPinnedPage(win, id, revision, operation.signal);
      return await this._discoverAudio(win, { songId: id, revisionId: revision }, page, operation.signal);
    } finally { this.resolving = false; operation.release(); }
  }
  // The trusted queue supplies the revision from its verified score receipt.
  // This read never opens a browser, creates an account copy or changes audio.
  async findSynchronization(result, { revisionId, audio, signal } = {}) {
    check(signal);
    const id = numeric(result?.id ?? result?.songId), revision = numeric(revisionId);
    const registered = id && this.results.get(id), video = audioVideo(audio);
    const identity = { songId: id, revisionId: revision, videoId: video?.videoId };
    if (!registered || !revision || (result.url && songUrl(result.url)?.url !== registered.url)) {
      return unavailableSynchronization(identity, 'identity_mismatch');
    }
    if (!video) return unavailableSynchronization(identity, 'unsupported_audio');
    const operation = this._controller(signal);
    try {
      const sync = await retrieveSynchronization(identity, { fetch: this.anonymousSession.fetch.bind(this.anonymousSession), signal: operation.signal });
      this.diagnostic('synchronization_' + (sync.reasonCode || 'found'), sync.status === 'done' ? 'success' : 'unavailable');
      return sync;
    } finally { operation.release(); }
  }
  async acquire(result, { directory, signal, onProgress = () => {}, allowAccount = false } = {}) {
    if (this.acquiring) throw failure('busy', 'Another Songsterr tab is being acquired.');
    if (!path.isAbsolute(directory || '')) throw failure('invalid_directory', 'Songsterr needs an absolute job folder.');
    const stat = await fsp.lstat(directory);
    if (!stat.isDirectory() || stat.isSymbolicLink()) throw failure('invalid_directory', 'Songsterr needs a regular job folder.');
    let descriptor = this.resolved.get(`${numeric(result?.id ?? result?.songId)}:${numeric(result?.revisionId)}`);
    if (!descriptor) descriptor = await this.resolve(result, { signal });
    const operation = this._controller(signal); this.acquiring = true;
    try {
      // The UI explicitly chooses the account retry. Never silently create an
      // account copy after a public failure, timeout or changed response shape.
      if (allowAccount === true) return await this._acquireAccount(descriptor, { directory, signal: operation.signal, onProgress });
      try { return await acquireAnonymous(descriptor, { fetch: this.anonymousSession.fetch.bind(this.anonymousSession), directory, signal: operation.signal, onProgress }); }
      catch (error) { error.canUseAccount = ['needs_login', 'access_denied'].includes(error.code); throw error; }
    } finally { this.acquiring = false; operation.release(); }
  }
  _loadCopies() {
    if (this.copies) return this.copies;
    try {
      const stat = fs.lstatSync(this.journalPath);
      if (!stat.isFile() || stat.isSymbolicLink() || stat.size > 1024 * 1024) throw failure('needs_attention', 'The Songsterr copy journal cannot be read safely.');
      const saved = JSON.parse(fs.readFileSync(this.journalPath, 'utf8'));
      if (saved.version !== 1 || !saved.copies || typeof saved.copies !== 'object' || Array.isArray(saved.copies)) throw new Error('Invalid journal');
      for (const [key, entry] of Object.entries(saved.copies)) {
        if (!/^[1-9]\d{0,11}:[1-9]\d{0,11}$/.test(key) || !['creating', 'uncertain', 'created'].includes(entry?.state)
          || (entry.state === 'created' && !songUrl(entry.url))) throw new Error('Invalid copy');
      }
      this.copies = saved.copies;
    } catch (error) {
      if (error.code === 'ENOENT') this.copies = {};
      else throw failure('needs_attention', 'The Songsterr copy journal needs attention before another account copy is created.');
    }
    return this.copies;
  }
  async _saveCopy(key, entry) {
    const copies = this._loadCopies();
    if (!Object.hasOwn(copies, key) && Object.keys(copies).length >= 2000) throw failure('needs_attention', 'The Songsterr account-copy history is full.');
    const next = { ...copies, [key]: entry };
    await fsp.mkdir(this.profilePath, { recursive: true });
    const temp = this.journalPath + '.' + crypto.randomUUID() + '.tmp';
    try { await fsp.writeFile(temp, JSON.stringify({ version: 1, copies: next }), { flag: 'wx', mode: 0o600 }); await fsp.rename(temp, this.journalPath); }
    finally { await fsp.unlink(temp).catch(() => {}); }
    this.copies = next;
  }
  async _acquireAccount(descriptor, { directory, signal, onProgress }) {
    const key = `${descriptor.id}:${descriptor.revisionId}`, journal = this._loadCopies();
    const win = this._window(true); win.hide(); let entry = journal[key];
    if (entry && entry.state !== 'created') {
      throw failure('needs_attention', 'A previous copy request may already have completed. FeedForge will not create another copy automatically.');
    }
    const editorUrl = new URL(entry?.url || descriptor.approvedUrl); editorUrl.searchParams.set('open', 'editor');
    await this._navigate(win, editorUrl.href, signal);
    let page = await this._wait(win, (value) => value.songId || value.status === 'needs_login', signal);
    if (page.status === 'needs_login' || page.signedOut) {
      this.setConnection('signed_out', 'Sign in on Songsterr to use account export.');
      throw failure('needs_login', 'Sign in to Songsterr, then retry using the account export.');
    }
    await this._act(win, 'dismissTutorial', signal);
    if (!entry) {
      if (page.songId !== descriptor.id || page.revisionId !== descriptor.revisionId) throw failure('revision_unavailable', 'The account page did not load the approved revision.');
      const copy = await this._act(win, 'copy', signal);
      if (!copy.ok) {
        const editor = await this._act(win, 'editor', signal);
        if (!editor.ok) throw failure('unavailable', 'The Songsterr editor control could not be found.');
        await delay(400, signal);
        if (!(await this._act(win, 'copy', signal)).ok) throw failure('unavailable', 'The Songsterr copy control could not be found.');
      }
      page = await this._wait(win, (value) => value.copyForm || value.status === 'needs_login', signal);
      if (page.status === 'needs_login') throw failure('needs_login', 'Sign in to Songsterr to create an unpublished copy.');
      // Persist intent before the one non-idempotent action. An ambiguous
      // response remains parked across restart, never blindly clicked twice.
      await this._saveCopy(key, { state: 'creating', createdAt: Date.now() });
      try {
        check(signal);
        if (!(await this._act(win, 'create', signal)).ok) {
          await this._saveCopy(key, { state: 'uncertain', createdAt: Date.now() });
          throw failure('needs_attention', 'The copy could not be confirmed. FeedForge will not create it again automatically.');
        }
        page = await this._wait(win, (value) => value.unpublished && value.songId && value.songId !== descriptor.id, signal, 30000);
        const parsed = songUrl(page.url);
        if (!parsed || parsed.id === descriptor.id) throw failure('needs_attention', 'The unpublished copy could not be identified.');
        entry = { state: 'created', url: parsed.url, copyId: parsed.id, createdAt: Date.now() };
        await this._saveCopy(key, entry);
      } catch (error) {
        if (this.copies?.[key]?.state !== 'created') await this._saveCopy(key, { state: 'uncertain', createdAt: Date.now() }).catch(() => {});
        throw error;
      }
    } else {
      if (page.songId !== entry.copyId || !page.unpublished) throw failure('needs_attention', 'The saved account copy is no longer an identifiable unpublished tab.');
    }
    check(signal); onProgress({ phase: 'export', completed: 0, total: 1 });
    const downloaded = await this._export(win, directory, signal);
    onProgress({ phase: 'export', completed: 1, total: 1 });
    return { path: downloaded.path, format: 'gpif', metadata: { songId: descriptor.id, revisionId: descriptor.revisionId,
      title: descriptor.title, artist: descriptor.artist, approval: 'approved', acquisition: 'account_export' },
      ...(descriptor.audio ? { audio: descriptor.audio } : {}), sourceFilename: sourceFilename(descriptor.artist, descriptor.title) };
  }
  async _export(win, directory, signal) {
    const destination = path.join(directory, 'source.gp');
    try { await fsp.access(destination); throw failure('invalid_directory', 'The tab export destination already exists.'); }
    catch (error) { if (error.code !== 'ENOENT') throw error; }
    let item, done, rejectDownload, settled = false;
    const promise = new Promise((resolve, reject) => { done = resolve; rejectDownload = reject; });
    const listener = (event, candidate, contents) => {
      if (contents !== win.webContents || item || !allowedDownload(candidate.getURL()) || !/\.gp$/i.test(candidate.getFilename() || '') || candidate.getTotalBytes() > MAX_TOTAL_BYTES) { event.preventDefault(); return; }
      item = candidate; candidate.setSavePath(destination);
      candidate.on('updated', () => { if (candidate.getReceivedBytes() > MAX_TOTAL_BYTES) { candidate.cancel(); rejectDownload(failure('score_too_large', 'The Guitar Pro export is too large.')); } });
      candidate.once('done', (_event, state) => {
        if (settled) return;
        if (state === 'completed') done();
        else rejectDownload(failure('download_failed', 'The Guitar Pro export did not finish.'));
      });
    };
    this.accountSession.on('will-download', listener);
    this.exportOwner = win.webContents;
    // Attach a rejection handler before clicking in case a DownloadItem fails
    // synchronously while the normal export action is still resolving.
    promise.catch(() => {});
    try {
      if (!(await this._act(win, 'export', signal)).ok) {
        if (!(await this._act(win, 'exportMenu', signal)).ok) throw failure('unavailable', 'The Songsterr Download control could not be found.');
        const page = await this._wait(win, (value) => value.canExport || value.status === 'needs_login', signal);
        if (page.status === 'needs_login') throw failure('needs_login', 'Sign in to Songsterr to export this unpublished copy.');
        if (!(await this._act(win, 'export', signal)).ok) throw failure('unavailable', 'The Guitar Pro export control could not be found.');
      }
      await bounded(promise, signal, 90000, () => item?.cancel()); settled = true;
      const stat = await fsp.lstat(destination);
      if (!stat.isFile() || stat.isSymbolicLink() || stat.size < 32 || stat.size > MAX_TOTAL_BYTES) throw failure('invalid_score', 'The Guitar Pro export is empty, invalid or too large.');
      const handle = await fsp.open(destination, 'r');
      try { const header = Buffer.alloc(4); await handle.read(header, 0, 4, 0); if (!header.equals(Buffer.from([80, 75, 3, 4]))) throw failure('invalid_score', 'Songsterr did not return a GP8 archive.'); }
      finally { await handle.close(); }
      return { path: destination };
    } catch (error) { item?.cancel(); await fsp.unlink(destination).catch(() => {}); throw error; }
    finally { settled = true; this.exportOwner = null; this.accountSession.removeListener('will-download', listener); }
  }
  dispose() {
    this.disposed = true;
    for (const controller of this.controllers) controller.abort(); this.controllers.clear();
    for (const win of this.windows) if (!win.isDestroyed()) win.destroy(); this.windows.clear();
    this.results.clear(); this.resolved.clear();
    this.attentionWindow = null;
    this.anonymousSession.removeListener('will-download', this.rejectAnonymousDownload);
    this.accountSession.removeListener('will-download', this.guardAccountDownload);
  }
}

module.exports = { SongsterrProvider };

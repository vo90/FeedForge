'use strict';
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { spawn } = require('node:child_process');
const { publishFeedpak } = require('./publication.cjs');
const { normalizeOutputSettings } = require('./output-settings.cjs');
const { unavailableSynchronization, synchronizationSummary, audioVideo } = require('./providers/songsterr/synchronization.cjs');
const WAITING = new Set(['needs_audio', 'needs_login', 'needs_attention', 'alignment_failed']);
const ACTIVE = new Set(['queued', 'resolving', 'downloading', 'converting', 'audio', 'aligning', 'validating', 'saving']);
const UUID = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i;
function clean(value) { return String(value || '').replace(/https?:\/\/[^\s]+/g, '[link]').slice(0, 1200); }
function check(job) { if (job.controller.signal.aborted) throw Object.assign(new Error('Cancelled.'), { code: 'cancelled' }); }
async function hashFile(filename, signal) {
  const hash = crypto.createHash('sha256');
  for await (const chunk of fs.createReadStream(filename)) { if (signal?.aborted) throw new Error('Cancelled.'); hash.update(chunk); }
  return hash.digest('hex');
}
function atomicJson(filename, value) {
  const temporary = `${filename}.${crypto.randomUUID()}.tmp`;
  try { fs.writeFileSync(temporary, JSON.stringify(value), { flag: 'wx', mode: 0o600 }); fs.renameSync(temporary, filename); }
  finally { if (fs.existsSync(temporary)) fs.unlinkSync(temporary); }
}
function record(job) {
  const { controller, child, done, resolveDone, retryAudioDetection, ...saved } = job;
  return saved;
}
function within(root, filename) { const rel = path.relative(root, filename); return rel && !rel.startsWith('..') && !path.isAbsolute(rel); }
function terminate(child) {
  if (!child || child.exitCode != null) return;
  if (process.platform === 'win32' && Number.isInteger(child.pid)) {
    const killer = spawn('taskkill.exe', ['/pid', String(child.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' });
    killer.on('error', () => child.kill());
  } else child.kill('SIGTERM');
}
class SongsterrJobs {
  constructor({ root, provider, runConverter, getConverterRecipe = async () => null, emit = () => {}, onCompleted = () => {}, tools = {} }) {
    Object.assign(this, { root: path.resolve(root), provider, runConverter, getConverterRecipe, emit, onCompleted, tools });
    fs.mkdirSync(this.root, { recursive: true }); this.jobs = []; this.disposed = false;
    const ledger = path.join(this.root, 'jobs.json');
    if (fs.existsSync(ledger)) {
      if (!fs.lstatSync(ledger).isFile() || fs.lstatSync(ledger).isSymbolicLink() || fs.statSync(ledger).size > 8 * 1024 ** 2) throw new Error('Songsterr history is not a supported local ledger.');
      const saved = JSON.parse(fs.readFileSync(ledger, 'utf8'));
      if (saved.version !== 1 || !Array.isArray(saved.jobs)) throw new Error('Songsterr import history could not be read.');
      this.jobs = saved.jobs.filter((j) => UUID.test(j.id) && j.source === 'songsterr' && /^\d+$/.test(j.chart?.id)).slice(-100);
      for (const job of this.jobs) {
        job.controller = new AbortController();
        this.provider.restoreTrustedResult?.(job.chart);
        if (ACTIVE.has(job.state)) { job.state = 'needs_attention'; job.message = 'Import was interrupted. Retry to continue.'; }
      }
    }
    this.ready = this._recover();
  }
  async _recover() {
    for (const job of this.jobs) {
      const receipt = path.join(this.root, `${job.id}.receipt.json`);
      if (!fs.existsSync(receipt)) continue;
      try {
        const saved = JSON.parse(fs.readFileSync(receipt, 'utf8'));
        if (saved.id === job.id && saved.outputHash && path.isAbsolute(saved.outputPath) && within(job.outputDir, saved.outputPath)
            && await hashFile(saved.outputPath) === saved.outputHash) {
          Object.assign(job, saved, { state: 'completed', committed: true, message: 'FeedPak ready.', controller: new AbortController() });
        }
      } catch { /* Preserve interrupted job and files for an explicit retry. */ }
    }
    this._save(); this.emit();
  }
  async _cleanAttempts(job) {
    const directory = path.join(this.root, job.id);
    if (!UUID.test(job.id) || path.dirname(directory) !== this.root || !fs.existsSync(directory) || fs.lstatSync(directory).isSymbolicLink()) return;
    for (const entry of await fs.promises.readdir(directory, { withFileTypes: true })) {
      if (!/^attempt-[a-zA-Z0-9]+$/.test(entry.name) || !entry.isDirectory() || entry.isSymbolicLink()) continue;
      const target = path.resolve(directory, entry.name);
      if (path.dirname(target) === directory) await fs.promises.rm(target, { recursive: true, force: true }).catch(() => {});
    }
  }
  async _pruneCache() {
    const terminal = this.jobs.filter((job) => ['completed', 'failed', 'cancelled'].includes(job.state));
    for (const job of terminal.slice(0, -10)) {
      const directory = path.resolve(this.root, job.id);
      if (!UUID.test(job.id) || path.dirname(directory) !== this.root || !fs.existsSync(directory) || fs.lstatSync(directory).isSymbolicLink()) continue;
      await fs.promises.rm(directory, { recursive: true, force: true }).catch(() => {});
    }
  }
  _save() { atomicJson(path.join(this.root, 'jobs.json'), { version: 1, jobs: this.jobs.map(record) }); }
  _set(job, state, extra = {}) { Object.assign(job, { state, updatedAt: Date.now() }, extra); this._save(); this.emit(this.public(job)); }
  public(job) {
    return { id: job.id, source: 'songsterr', sourceKey: job.sourceKey, songId: job.chart.id, title: job.chart.title, artist: job.chart.artist,
      state: job.state, message: clean(job.message), error: clean(job.error), createdAt: job.createdAt,
      revisionId: job.metadata?.revisionId, outputDir: job.outputDir, outputSettings: job.outputSettings,
      outputPath: job.state === 'completed' ? job.outputPath : undefined,
      outputAvailable: job.state === 'completed' && Boolean(job.outputPath && fs.existsSync(job.outputPath)),
      warnings: job.warnings, alignment: job.alignment, coverage: job.coverage, synchronization: job.synchronizationSummary,
      canRetry: WAITING.has(job.state) || job.state === 'failed' || job.state === 'cancelled',
      canRetryAudio: job.state === 'needs_audio' && !job.audio && Boolean(job.scorePath) && typeof this.provider.findAudio === 'function',
      canUseAccount: job.canUseAccount === true,
      canCancel: ACTIVE.has(job.state) || WAITING.has(job.state) };
  }
  snapshot() { return this.jobs.map((job) => this.public(job)); }
  enqueue(chart, { outputDir, outputSettings }) {
    if (this.disposed) throw new Error('Songsterr is closed.');
    if (!path.isAbsolute(outputDir || '')) throw new Error('Choose an output folder in FeedForge Settings.');
    const existing = this.jobs.find((job) => job.chart.id === String(chart.id) && (ACTIVE.has(job.state) || WAITING.has(job.state)));
    if (existing) return this.public(existing);
    if (this.jobs.filter((job) => ACTIVE.has(job.state) || WAITING.has(job.state)).length >= 30) throw new Error('Finish or cancel some imports before adding more songs.');
    const job = { id: crypto.randomUUID(), source: 'songsterr', sourceKey: `songsterr:${chart.id}`, chart: { ...chart, id: String(chart.id) },
      state: 'queued', createdAt: Date.now(), outputDir: path.resolve(outputDir), outputSettings: normalizeOutputSettings(outputSettings), controller: new AbortController() };
    const previous = this.jobs;
    this.jobs = [...previous, job];
    while (this.jobs.length > 100) {
      const oldest = this.jobs.findIndex((entry) => ['completed', 'failed', 'cancelled'].includes(entry.state));
      if (oldest < 0) break;
      this.jobs.splice(oldest, 1);
    }
    try { this._save(); } catch (error) { this.jobs = previous; throw error; }
    this.emit(this.public(job)); this._start(); return this.public(job);
  }
  retry(id, { audio, allowAccount } = {}) {
    const job = this.jobs.find((j) => j.id === id);
    if (!job || !this.public(job).canRetry || this.current === job) throw new Error('This import cannot be retried yet.');
    if (audio) job.audio = audio;
    if (allowAccount === true) job.allowAccount = true;
    // Audio discovery is an explicit, one-shot retry of the saved revision. A
    // supplied or previously chosen recording always keeps priority.
    job.retryAudioDetection = job.state === 'needs_audio' && !job.audio && Boolean(job.scorePath) && typeof this.provider.findAudio === 'function';
    job.controller = new AbortController(); job.committed = false; job.timedOut = false;
    this._set(job, 'queued', { error: '', alignment: undefined, synchronizationSummary: undefined, message: 'Queued.' }); this._start(); return this.public(job);
  }
  async cancel(id) {
    const job = this.jobs.find((j) => j.id === id);
    if (!job || job.committed) return;
    job.controller.abort(); terminate(job.child);
    if (this.current === job) { await job.done; } else this._set(job, 'cancelled', { message: 'Cancelled.', error: '' });
  }
  _start() {
    if (this.draining || this.disposed) return;
    this.draining = Promise.resolve().then(async () => {
      await this.ready;
      while (!this.disposed) {
        const job = this.jobs.find((j) => j.state === 'queued'); if (!job) break;
        this.current = job;
        job.done = new Promise((resolve) => { job.resolveDone = resolve; });
        try { await this._work(job); }
        catch (error) {
          if (job.committed) {
            // The receipt and atomic link are authoritative even if history persistence failed.
            job.state = 'completed'; job.message = 'FeedPak ready.'; job.error = '';
            this.emit(this.public(job));
            continue;
          }
          const code = job.controller.signal.aborted ? 'cancelled' : error.code;
          job.canUseAccount = error.canUseAccount === true;
          if (code === 'alignment_failed' && error.alignment && typeof error.alignment === 'object' && !Array.isArray(error.alignment)) job.alignment = error.alignment;
          this._set(job, WAITING.has(code) || code === 'cancelled' ? code : 'failed', { error: code === 'cancelled' ? '' : clean(error.message), message: code === 'cancelled' ? 'Cancelled.' : '' });
        } finally {
          job.child = null;
          await this._cleanAttempts(job).catch(() => {});
          await this._pruneCache().catch(() => {});
          this.current = null; job.resolveDone();
        }
      }
    }).finally(() => { this.draining = null; if (!this.disposed && this.jobs.some((j) => j.state === 'queued')) this._start(); });
    this.draining.catch(() => {});
  }
  async _run(job, args, directory) {
    check(job);
    let timer;
    try {
      timer = setTimeout(() => { job.timedOut = true; terminate(job.child); }, 30 * 60 * 1000); timer.unref?.();
      const result = await this.runConverter(args, { directory,
        onSpawn: (child) => { job.child = child; if (job.controller.signal.aborted) terminate(child); },
        onStderrLine: (line) => {
          if (!line.startsWith('FEEDFORGE_PROGRESS ')) return;
          try { const progress = JSON.parse(line.slice(19)); if (['audio', 'aligning', 'converting', 'validating'].includes(progress.stage)) this._set(job, progress.stage, { message: clean(progress.message) }); } catch { /* Ignore unrelated output. */ }
        } });
      job.child = null; check(job);
      if (job.timedOut) throw new Error('Conversion timed out. Retry with another audio file.');
      if (typeof result?.stdout !== 'string' || result.stdout.length > 4 * 1024 * 1024) throw new Error('The converter returned an invalid response.');
      let parsed; try { parsed = JSON.parse(result.stdout); } catch { throw new Error('The converter returned an unreadable response.'); }
      if (result.code !== 0 || parsed.ok !== true) throw Object.assign(new Error(clean(parsed.error) || 'Conversion failed.'), { code: parsed.code, alignment: parsed.alignment });
      return parsed;
    } finally { clearTimeout(timer); }
  }
  async _work(job) {
    const retryAudioDetection = job.retryAudioDetection === true;
    delete job.retryAudioDetection;
    const directory = path.join(this.root, job.id); fs.mkdirSync(directory, { recursive: true });
    const stat = fs.lstatSync(directory); if (!stat.isDirectory() || stat.isSymbolicLink()) throw new Error('The import working folder is invalid.');
    check(job); job.converterRecipe = await this.getConverterRecipe();
    if ((!job.scorePath || !fs.existsSync(job.scorePath)) && !retryAudioDetection) {
      this._set(job, 'resolving', { message: 'Finding the latest approved revision…', error: '' });
      // A cancelled provider may have finished writing before its promise rejects.
      // A fresh acquisition folder makes retry safe without overwriting that file.
      const acquisition = fs.mkdtempSync(path.join(directory, 'source-'));
      const acquired = await this.provider.acquire(job.chart, { directory: acquisition, signal: job.controller.signal, allowAccount: job.allowAccount === true,
        onProgress: (p) => this._set(job, 'downloading', { message: clean(p?.message || 'Retrieving the approved tab…') }) });
      check(job);
      if (!acquired?.path || !within(acquisition, path.resolve(acquired.path)) || !fs.statSync(acquired.path).isFile()) throw new Error('The source returned an invalid tab file.');
      if (String(acquired.metadata?.songId) !== job.chart.id || !/^\d+$/.test(String(acquired.metadata?.revisionId)) || acquired.metadata?.approval !== 'approved') throw new Error('The source did not identify an approved revision.');
      job.scorePath = acquired.path; job.metadata = acquired.metadata;
      job.cachedScoreHash = await hashFile(job.scorePath, job.controller.signal);
      job.sourceKey = `songsterr:${job.chart.id}:${job.metadata.revisionId}`;
      if (!job.audio && acquired.audio) job.audio = acquired.audio;
    }
    if (String(job.metadata?.songId) !== job.chart.id || !/^\d+$/.test(String(job.metadata?.revisionId)) || job.metadata?.approval !== 'approved'
        || job.sourceKey !== `songsterr:${job.chart.id}:${job.metadata.revisionId}`) throw new Error('The saved tab revision is invalid. Search for the song again to create a new import.');
    if (typeof job.scorePath !== 'string' || !path.isAbsolute(job.scorePath) || !within(directory, path.resolve(job.scorePath))
        || !fs.existsSync(job.scorePath) || fs.lstatSync(job.scorePath).isSymbolicLink() || !fs.lstatSync(job.scorePath).isFile()
        || await hashFile(job.scorePath, job.controller.signal) !== job.cachedScoreHash) throw new Error('The saved tab changed. Search for the song again to create a new import.');
    check(job);
    if (retryAudioDetection && !job.audio) {
      this._set(job, 'audio', { message: 'Checking Songsterr for the recording…' });
      const audio = await this.provider.findAudio(job.chart, { revisionId: String(job.metadata.revisionId), signal: job.controller.signal });
      check(job);
      if (audio) job.audio = audio;
    }
    if (!job.audio) throw Object.assign(new Error('No usable original audio was found. Choose an audio file or paste a link.'), { code: 'needs_audio' });
    // Fetch once per conversion attempt, including old cached failed jobs.
    // Never retain the point array in the history ledger or reuse a previous
    // recording's map after the user supplies replacement audio.
    const syncIdentity = { songId: job.chart.id, revisionId: String(job.metadata.revisionId), videoId: audioVideo(job.audio)?.videoId };
    let synchronization = unavailableSynchronization(syncIdentity, 'unavailable');
    if (typeof this.provider.findSynchronization === 'function') {
      this._set(job, 'aligning', { message: 'Checking Songsterr timing for this recording…' });
      try {
        synchronization = await this.provider.findSynchronization(job.chart, { revisionId: syncIdentity.revisionId,
          audio: job.audio, signal: job.controller.signal }) || synchronization;
      } catch (error) {
        check(job);
        if (error.code === 'cancelled') throw error;
        synchronization = unavailableSynchronization(syncIdentity, 'network_error');
      }
    }
    check(job);
    job.synchronizationSummary = synchronizationSummary(synchronization);
    this._set(job, 'converting', { message: 'Preparing the tab and audio…' });
    const attempt = fs.mkdtempSync(path.join(directory, 'attempt-'));
    const requestPath = path.join(attempt, 'request.json');
    atomicJson(requestPath, { scorePath: job.scorePath, metadata: job.metadata, audio: job.audio, synchronization, workDir: attempt,
      outputDir: job.outputDir, outputSettings: job.outputSettings, tools: this.tools });
    let result;
    try { result = await this._run(job, ['--song-import-file', requestPath], attempt); }
    finally { fs.unlinkSync(requestPath); }
    const staging = path.resolve(result.stagingPath || '');
    if (!within(attempt, staging) || fs.lstatSync(staging).isSymbolicLink() || !fs.statSync(staging).isFile()) throw new Error('The converter returned an invalid staged FeedPak.');
    this._set(job, 'validating', { message: 'Checking the completed FeedPak…' });
    await this._run(job, ['--validate-feedpak', staging], attempt);
    Object.assign(job, { outputRelativePath: result.relativePath, outputHash: await hashFile(staging, job.controller.signal),
      scoreHash: result.scoreHash, audioHash: result.audioHash, recipe: result.recipe, alignment: result.alignment, coverage: result.coverage, warnings: result.warnings });
    const identity = (j) => JSON.stringify([j.sourceKey, j.scoreHash, j.audioHash, j.recipe, j.converterRecipe, j.outputDir, j.outputRelativePath, j.outputSettings]);
    const prior = this.jobs.find((j) => j !== job && j.state === 'completed' && identity(j) === identity(job) && j.outputPath);
    if (prior && await hashFile(prior.outputPath, job.controller.signal).catch(() => '') === prior.outputHash) {
      job.outputPath = prior.outputPath; job.outputHash = prior.outputHash; job.committed = true;
    } else {
      this._set(job, 'saving', { message: 'Saving to your FeedForge output folder…' });
      await publishFeedpak({ job, staging, check, hashFile, writeReceipt: (j, target) => atomicJson(path.join(this.root, `${j.id}.receipt.json`), { ...record(j), outputPath: target }) });
    }
    this._set(job, 'completed', { message: 'FeedPak ready.', error: '' });
    await Promise.resolve(this.onCompleted(this.public(job))).catch(() => {});
  }
  async dispose() { this.disposed = true; for (const job of this.jobs) if (ACTIVE.has(job.state)) { job.controller.abort(); terminate(job.child); } await this.draining; }
}
module.exports = { SongsterrJobs, hashFile, atomicJson };

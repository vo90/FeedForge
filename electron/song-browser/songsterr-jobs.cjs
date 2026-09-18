'use strict';
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { spawn } = require('node:child_process');
const { publishFeedpak } = require('./publication.cjs');
const { normalizeOutputSettings } = require('./output-settings.cjs');
const { inspectEvidence, reportBundle } = require('./songsterr-evidence.cjs');
const { waitForSharedOperation } = require('./shared-operation.cjs');
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
function within(root, filename) { const rel = path.relative(root, filename); return Boolean(rel && rel !== '..' && !rel.startsWith(`..${path.sep}`) && !path.isAbsolute(rel)); }
function containedFile(root, filename, { allowRootLink = false } = {}) {
  if (typeof filename !== 'string' || !path.isAbsolute(filename)) return { reason: 'invalid_path' };
  try {
    // Owned source/attempt folders cannot be replaced by a junction. A selected
    // output library may intentionally be linked, so receipt recovery opts in.
    const rootStat = fs.lstatSync(root);
    if (!allowRootLink && rootStat.isSymbolicLink()) return { reason: 'linked_folder' };
    if (!rootStat.isDirectory() && !(allowRootLink && rootStat.isSymbolicLink())) return { reason: 'not_directory' };
    const stat = fs.lstatSync(filename);
    if (stat.isSymbolicLink()) return { reason: 'symbolic_link' };
    if (!stat.isFile()) return { reason: 'not_file' };
    // Python Path.resolve and async realpath use the native Windows backing
    // directory. Legacy realpathSync can retain an MSIX AppData alias instead.
    const realRoot = fs.realpathSync.native(root), realFile = fs.realpathSync.native(filename);
    if (!within(realRoot, realFile)) return { reason: 'outside_folder' };
    return { path: realFile };
  } catch (error) {
    return { reason: error.code === 'ENOENT' || error.code === 'ENOTDIR' ? 'missing_file' : 'unreadable_file' };
  }
}
function fileLocationError(message, stage, reason) {
  const detail = { invalid_path: 'the file path is missing or is not absolute', missing_file: 'the file is missing',
    symbolic_link: 'the file is a symbolic link', not_file: 'the path is not a regular file',
    outside_folder: 'the file is outside the expected folder', linked_folder: 'the working folder is a symbolic link or junction',
    not_directory: 'the working folder is not a directory', unreadable_file: 'the file location could not be read' }[reason];
  return Object.assign(new Error(`${message} (${detail}).`), { code: 'import_file_location', pathValidation: { stage, reason } });
}
function terminate(child) {
  if (!child || child.exitCode != null) return;
  if (process.platform === 'win32' && Number.isInteger(child.pid)) {
    const killer = spawn('taskkill.exe', ['/pid', String(child.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' });
    killer.on('error', () => child.kill());
  } else child.kill('SIGTERM');
}
class SongsterrJobs {
  constructor({ root, provider, runConverter, getConverterRecipe = async () => null, emit = () => {}, onCompleted = () => {}, tools = {}, artworkLookup = true }) {
    Object.assign(this, { root: path.resolve(root), provider, runConverter, getConverterRecipe, emit, onCompleted, tools, artworkLookup });
    this.auditRoot = path.join(path.dirname(this.root), 'evidence');
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
        const output = containedFile(job.outputDir, saved.outputPath, { allowRootLink: true });
        if (saved.id === job.id && saved.outputHash && output.path
            && await hashFile(output.path) === saved.outputHash) {
          if (saved.recipe?.preservationContract) {
            const checked = inspectEvidence(this.auditRoot, saved.evidence, saved.outputHash);
            const contract = saved.recipe.preservationContract;
            if (![1, 2, 3].includes(contract) || saved.verification?.version !== contract || saved.verification.status !== 'passed'
                || checked.verification.version !== contract || checked.verification.status !== 'passed') throw new Error('Unverified recovery receipt.');
          }
          Object.assign(job, saved, { outputPath: output.path, state: 'completed', committed: true, message: 'FeedPak ready.',
            error: '', pathValidation: undefined, outputVerification: saved.verification?.version === 3 && saved.verification.status === 'passed' ? 'passed' : 'not_checked', controller: new AbortController() });
        }
      } catch { /* Preserve interrupted job and files for an explicit retry. */ }
    }
    await this.refreshOutputs(); this._save(); this.emit();
  }
  async refreshOutputs() {
    if (this.refreshing) return this.refreshing;
    if (Date.now() - (this.lastOutputCheck || 0) < 30000) return;
    this.refreshing = (async () => {
      for (const job of this.jobs.filter((j) => j.state === 'completed')) {
        if (!job.verification) { job.outputVerification = 'not_checked'; continue; }
        try {
          const output = containedFile(job.outputDir, job.outputPath, { allowRootLink: true });
          if (!output.path) { job.outputVerification = 'unavailable'; continue; }
          const hash = await hashFile(output.path);
          if (hash !== job.verification.outputHash) { job.outputVerification = 'modified'; continue; }
          const checked = inspectEvidence(this.auditRoot, job.evidence, hash);
          job.outputVerification = job.recipe?.preservationContract === 3 && checked.verification.version === 3 && checked.verification.status === 'passed' ? 'passed' : 'not_checked';
        } catch { job.outputVerification = 'not_checked'; }
      }
      this.lastOutputCheck = Date.now();
    })().finally(() => { this.refreshing = null; });
    return this.refreshing;
  }
  auditBundle(id) {
    const job = this.jobs.find((entry) => entry.id === id);
    if (!job) throw new Error('The import no longer exists.');
    return reportBundle(this.auditRoot, job.evidence);
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
      verification: job.verification ? { ...job.verification, status: job.state === 'completed' ? job.outputVerification || 'not_checked' : job.verification.status } : undefined,
      artwork: job.artwork, hasReport: Boolean(job.evidence),
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
    this._set(job, 'queued', { error: '', alignment: undefined, synchronizationSummary: undefined, pathValidation: undefined, message: 'Queued.' }); this._start(); return this.public(job);
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
          if (error.pathValidation) job.pathValidation = error.pathValidation;
          for (const key of ['verification', 'evidence', 'warnings']) if (error[key]) job[key] = error[key];
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
    // The deadline includes waiting for shared converter capacity. Keep its
    // cancellation separate from the job so a timeout remains a retryable error.
    const admission = new AbortController();
    const cancelAdmission = () => admission.abort();
    job.controller.signal.addEventListener('abort', cancelAdmission, { once: true });
    const timeoutError = () => new Error('Conversion timed out. Retry with another audio file.');
    let timedOut = false;
    let timer;
    try {
      timer = setTimeout(() => { timedOut = true; job.timedOut = true; admission.abort(); terminate(job.child); }, 30 * 60 * 1000); timer.unref?.();
      const result = await this.runConverter(args, { directory, admissionSignal: admission.signal,
        onSpawn: (child) => { job.child = child; if (admission.signal.aborted) terminate(child); },
        onStderrLine: (line) => {
          if (!line.startsWith('FEEDFORGE_PROGRESS ')) return;
          try { const progress = JSON.parse(line.slice(19)); if (['audio', 'aligning', 'converting', 'validating'].includes(progress.stage)) this._set(job, progress.stage, { message: clean(progress.message) }); } catch { /* Ignore unrelated output. */ }
        } });
      job.child = null; check(job);
      if (timedOut) throw timeoutError();
      if (typeof result?.stdout !== 'string' || result.stdout.length > 4 * 1024 * 1024) throw new Error('The converter returned an invalid response.');
      let parsed; try { parsed = JSON.parse(result.stdout); } catch { throw new Error('The converter returned an unreadable response.'); }
      if (result.code !== 0 || parsed.ok !== true) throw Object.assign(new Error(clean(parsed.error) || 'Conversion failed.'), { code: parsed.code, alignment: parsed.alignment, verification: parsed.verification, evidence: parsed.evidence, warnings: parsed.warnings });
      return parsed;
    } catch (error) {
      check(job);
      if (timedOut) throw timeoutError();
      throw error;
    } finally {
      clearTimeout(timer);
      job.controller.signal.removeEventListener('abort', cancelAdmission);
    }
  }
  async _work(job) {
    const retryAudioDetection = job.retryAudioDetection === true;
    delete job.retryAudioDetection;
    const directory = path.join(this.root, job.id); fs.mkdirSync(directory, { recursive: true });
    const stat = fs.lstatSync(directory); if (!stat.isDirectory() || stat.isSymbolicLink()) throw new Error('The import working folder is invalid.');
    check(job); job.converterRecipe = await waitForSharedOperation(this.getConverterRecipe(), job.controller.signal); check(job);
    if ((!job.scorePath || !fs.existsSync(job.scorePath)) && !retryAudioDetection) {
      this._set(job, 'resolving', { message: 'Finding the latest approved revision…', error: '' });
      // A cancelled provider may have finished writing before its promise rejects.
      // A fresh acquisition folder makes retry safe without overwriting that file.
      const acquisition = fs.mkdtempSync(path.join(directory, 'source-'));
      const acquired = await this.provider.acquire(job.chart, { directory: acquisition, signal: job.controller.signal, allowAccount: job.allowAccount === true,
        onProgress: (p) => this._set(job, 'downloading', { message: clean(p?.message || 'Retrieving the approved tab…') }) });
      check(job);
      const source = containedFile(acquisition, acquired?.path);
      if (!source.path) throw fileLocationError('The source returned an invalid tab file', 'acquired_score', source.reason);
      if (String(acquired.metadata?.songId) !== job.chart.id || !/^\d+$/.test(String(acquired.metadata?.revisionId)) || acquired.metadata?.approval !== 'approved') throw new Error('The source did not identify an approved revision.');
      job.scorePath = acquired.path; job.metadata = acquired.metadata;
      job.cachedScoreHash = await hashFile(job.scorePath, job.controller.signal);
      job.sourceKey = `songsterr:${job.chart.id}:${job.metadata.revisionId}`;
      if (!job.audio && acquired.audio) job.audio = acquired.audio;
    }
    if (String(job.metadata?.songId) !== job.chart.id || !/^\d+$/.test(String(job.metadata?.revisionId)) || job.metadata?.approval !== 'approved'
        || job.sourceKey !== `songsterr:${job.chart.id}:${job.metadata.revisionId}`) throw new Error('The saved tab revision is invalid. Search for the song again to create a new import.');
    const score = containedFile(directory, job.scorePath);
    if (!score.path) throw fileLocationError('The saved tab changed. Search for the song again to create a new import', 'cached_score', score.reason);
    if (await hashFile(score.path, job.controller.signal) !== job.cachedScoreHash) throw new Error('The saved tab changed. Search for the song again to create a new import.');
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
      auditDir: this.auditRoot, artworkCacheDir: path.join(path.dirname(this.root), 'artwork-cache'), artworkLookup: this.artworkLookup,
      outputDir: job.outputDir, outputSettings: job.outputSettings, tools: this.tools });
    let result;
    try { result = await this._run(job, ['--song-import-file', requestPath], attempt); }
    finally { fs.unlinkSync(requestPath); }
    const staged = containedFile(attempt, result.stagingPath);
    if (!staged.path) throw fileLocationError('The converter returned an invalid temporary FeedPak file', 'staged_feedpak', staged.reason);
    const staging = staged.path;
    this._set(job, 'validating', { message: 'Checking the completed FeedPak…' });
    await this._run(job, ['--validate-feedpak', staging], attempt);
    const outputHash = await hashFile(staging, job.controller.signal);
    if (result.recipe?.preservationContract !== 3 || result.verification?.version !== 3 || result.verification.status !== 'passed'
        || result.verification.outputHash !== outputHash) throw new Error('The converter did not provide a current source verification. Update the converter and retry.');
    const checked = inspectEvidence(this.auditRoot, result.evidence, outputHash);
    if (checked.verification.version !== 3 || checked.verification.status !== 'passed') throw new Error('Source verification did not pass.');
    if (result.scoreHash !== job.cachedScoreHash || checked.record.objects.source !== job.cachedScoreHash) throw new Error('Source verification describes a different tab.');
    Object.assign(job, { outputRelativePath: result.relativePath, outputHash,
      verification: result.verification, evidence: result.evidence, artwork: result.artwork, outputVerification: 'passed',
      scoreHash: result.scoreHash, audioHash: result.audioHash, recipe: result.recipe, alignment: result.alignment, coverage: result.coverage, warnings: result.warnings });
    const identity = (j) => JSON.stringify([j.sourceKey, j.scoreHash, j.audioHash, j.recipe, j.converterRecipe, j.outputDir, j.outputRelativePath, j.outputSettings]);
    const prior = this.jobs.find((j) => j !== job && j.state === 'completed' && identity(j) === identity(job) && j.outputPath);
    let canReuse = false;
    if (prior && prior.verification?.version === 3 && prior.verification.status === 'passed') {
      try {
        const checkedPrior = inspectEvidence(this.auditRoot, prior.evidence, prior.outputHash);
        canReuse = await hashFile(prior.outputPath, job.controller.signal) === prior.outputHash
          && checkedPrior.verification.version === 3 && checkedPrior.verification.status === 'passed';
      } catch { /* A missing historical report does not invalidate this fresh conversion. */ }
    }
    if (canReuse) {
      job.outputPath = prior.outputPath; job.outputHash = prior.outputHash; job.committed = true;
      job.evidence = prior.evidence; job.verification = prior.verification;
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

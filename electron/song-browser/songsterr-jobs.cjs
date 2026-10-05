'use strict';
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { spawn } = require('node:child_process');
const { publishFeedpak } = require('./publication.cjs');
const { normalizeOutputSettings } = require('./output-settings.cjs');
const { inspectEvidence, reportBundle, compatibilityReport, compatibilityBacklog, CURRENT_PRESERVATION_CONTRACT, KNOWN_PRESERVATION_CONTRACTS,
  CHART_GUIDANCE_POLICY, verifiedChartGuidance } = require('./songsterr-evidence.cjs');
const { waitForSharedOperation } = require('./shared-operation.cjs');
const { unavailableSynchronization, synchronizationSummary, audioVideo } = require('./providers/songsterr/synchronization.cjs');
const { transport, retryPlan } = require('./songsterr-retry.cjs');
const { reportZip } = require('./songsterr-retry-report.cjs');
const { normalizeHybridLead, matchesHybridRequest } = require('./hybrid-lead-options.cjs');
const { selectSiteAudio, isSiteAudio, validRecovery, startRecovery, verifiedRecoveryMap } = require('./songsterr-recording.cjs');
const { validRevisionSelection, revisionLabel } = require('./providers/songsterr/revision-policy.cjs');
const { numeric } = require('./providers/songsterr/policy.cjs');
const WAITING = new Set(['needs_audio', 'needs_login', 'needs_attention', 'alignment_failed', 'awaiting_main_choice']);
const ACTIVE = new Set(['queued', 'resolving', 'downloading', 'converting', 'audio', 'aligning', 'validating', 'saving']);
const PENDING = (job) => ACTIVE.has(job.state) || WAITING.has(job.state) || job.state === 'retry_wait';
const defaultClock = { now: () => Date.now(), setTimeout, clearTimeout, random: Math.random };
function retryCycle(previous) { return { version: 1, cycle: crypto.randomUUID(), used: 0, events: [], cycles: [...(previous?.cycles || []), ...(previous?.cycle ? [previous.cycle] : [])].slice(-20) }; }
function validRetry(retry) { return retry?.version === 1 && UUID.test(retry.cycle) && Number.isInteger(retry.used) && retry.used >= 0 && retry.used <= 2
  && Array.isArray(retry.events) && retry.events.length <= 30 && Array.isArray(retry.cycles) && retry.cycles.length <= 20 && retry.cycles.every(id => UUID.test(id)); }
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
  constructor({ root, provider, runConverter, getConverterRecipe = async () => null, emit = () => {}, onCompleted = () => {}, tools = {}, artworkLookup = true, clock = defaultClock }) {
    Object.assign(this, { root: path.resolve(root), provider, runConverter, getConverterRecipe, emit, onCompleted, tools, artworkLookup });
    this.auditRoot = path.join(path.dirname(this.root), 'evidence');
    this.clock = clock; this.cooldowns = {}; this.retryHistoryRoot = path.join(path.dirname(this.root), 'retry-history');
    fs.mkdirSync(this.root, { recursive: true }); this.jobs = []; this.disposed = false;
    const ledger = path.join(this.root, 'jobs.json');
    if (fs.existsSync(ledger)) {
      if (!fs.lstatSync(ledger).isFile() || fs.lstatSync(ledger).isSymbolicLink() || fs.statSync(ledger).size > 8 * 1024 ** 2) throw new Error('Songsterr history is not a supported local ledger.');
      const saved = JSON.parse(fs.readFileSync(ledger, 'utf8'));
      if (saved.version !== 1 || !Array.isArray(saved.jobs)) throw new Error('Songsterr import history could not be read.');
      for (const service of ['songsterr', 'youtube', 'audio_host']) if (Number.isSafeInteger(saved.cooldowns?.[service]) && saved.cooldowns[service] > clock.now()) this.cooldowns[service] = saved.cooldowns[service];
      this.jobs = saved.jobs.filter((j) => UUID.test(j.id) && j.source === 'songsterr' && /^\d+$/.test(j.chart?.id)).slice(-100);
      for (const job of this.jobs) {
        job.controller = new AbortController();
        this.provider.restoreTrustedResult?.(job.chart);
        if (ACTIVE.has(job.state)) { job.state = 'needs_attention'; job.message = 'Import was interrupted. Retry to continue.'; }
        if (job.state === 'retry_wait' && (!validRetry(job.retry) || !Number.isSafeInteger(job.retry.nextAt) || job.retry.nextAt <= 0)) {
          job.state = 'needs_attention'; job.message = 'Saved retry information is invalid. Retry manually.';
        }
        if (job.retry && !validRetry(job.retry)) delete job.retry;
        if (job.recordingRecovery && !validRecovery(job.recordingRecovery)) {
          delete job.recordingRecovery;
          if (job.state !== 'completed') { job.state = 'needs_attention'; job.message = 'Saved recording recovery is invalid. Check Songsterr audio again.'; }
        }
      }
    }
    this.ready = this._recover();
    this.ready.then(() => this._start()).catch(() => {});
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
          if (!matchesHybridRequest(job.hybridLead, saved.recipe?.hybridLead)) throw new Error('Recovered Hybrid Lead options differ from the job.');
          if (saved.recipe?.preservationContract) {
            const checked = inspectEvidence(this.auditRoot, saved.evidence, saved.outputHash);
            const contract = saved.recipe.preservationContract;
            if (!KNOWN_PRESERVATION_CONTRACTS.includes(contract) || saved.verification?.version !== contract || saved.verification.status !== 'passed'
                || checked.verification.version !== contract || checked.verification.status !== 'passed') throw new Error('Unverified recovery receipt.');
          }
          Object.assign(job, saved, { retry: job.retry || saved.retry, outputPath: output.path, state: 'completed', committed: true, message: 'FeedPak ready.',
            error: '', pathValidation: undefined, outputVerification: saved.verification?.version === CURRENT_PRESERVATION_CONTRACT && saved.verification.status === 'passed' ? 'passed' : 'not_checked', controller: new AbortController() });
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
          job.outputVerification = job.recipe?.preservationContract === CURRENT_PRESERVATION_CONTRACT && checked.verification.version === CURRENT_PRESERVATION_CONTRACT && checked.verification.status === 'passed' ? 'passed' : 'not_checked';
        } catch { job.outputVerification = 'not_checked'; }
      }
      this.lastOutputCheck = Date.now();
    })().finally(() => { this.refreshing = null; });
    return this.refreshing;
  }
  auditBundle(id) {
    const job = this.jobs.find((entry) => entry.id === id);
    if (!job) throw new Error('The import no longer exists.');
    if (!job.retry?.events?.length) return reportBundle(this.auditRoot, job.evidence);
    const entries = [];
    if (job.evidence) entries.push(['conversion-report.zip', fs.readFileSync(reportBundle(this.auditRoot, job.evidence))]);
    const cycles = [...job.retry.cycles, job.retry.cycle], history = [];
    for (const cycle of cycles) {
      const file = containedFile(this.retryHistoryRoot, path.join(this.retryHistoryRoot, job.id, `${cycle}.json`));
      if (!file.path || fs.statSync(file.path).size > 128 * 1024) throw new Error('The saved attempt history is unavailable.');
      history.push(JSON.parse(fs.readFileSync(file.path, 'utf8')));
    }
    entries.push(['attempt-history.json', Buffer.from(JSON.stringify({ version: 1, jobId: job.id, retainedCycles: history }, null, 2))]);
    const output = path.join(this.retryHistoryRoot, job.id, 'report.zip');
    const temporary = output + '.' + crypto.randomUUID() + '.tmp';
    try { fs.writeFileSync(temporary, reportZip(entries), { flag: 'wx' }); fs.renameSync(temporary, output); }
    finally { if (fs.existsSync(temporary)) fs.unlinkSync(temporary); }
    return output;
  }
  compatibilityDetails(id) {
    const job = this.jobs.find(entry => entry.id === id);
    if (!job?.evidence) throw new Error('This import has no saved report.');
    const report = compatibilityReport(this.auditRoot, job.evidence);
    return report ? { ...report, findings: report.findings.slice(0, 200), displayedLimit: 200 } : null;
  }
  compatibilityList() { return compatibilityBacklog(this.auditRoot); }
  async _cleanAttempts(job) {
    if (job.hybridLead?.enabled && ['failed', 'needs_attention'].includes(job.state)) return;
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
  _save() {
    try { atomicJson(path.join(this.root, 'jobs.json'), { version: 1, cooldowns: this.cooldowns, jobs: this.jobs.map(record) }); }
    catch (error) { this.persistenceFailed = true; throw error; }
  }
  _set(job, state, extra = {}) { Object.assign(job, { state, updatedAt: Date.now() }, extra); this._save(); this.emit(this.public(job)); }
  public(job) {
    return { id: job.id, source: 'songsterr', sourceKey: job.sourceKey, songId: job.chart.id, title: job.chart.title, artist: job.chart.artist,
      state: job.state, message: clean(job.message), error: clean(job.error), createdAt: job.createdAt,
      revisionId: job.metadata?.revisionId || job.pinnedDescriptor?.revisionId,
      revisionLabel: revisionLabel(job.metadata || job.pinnedDescriptor), requestedRevisionId: job.requestedRevisionId,
      outputDir: job.outputDir, outputSettings: job.outputSettings,
      hybridLead: job.hybridLead, hybridChoice: job.state === 'awaiting_main_choice' ? job.hybridChoice : undefined,
      originalsOnlyFrom: job.originalsOnlyFrom,
      outputPath: job.state === 'completed' ? job.outputPath : undefined,
      outputAvailable: job.state === 'completed' && Boolean(job.outputPath && fs.existsSync(job.outputPath)),
      warnings: job.warnings, alignment: job.alignment, coverage: job.coverage, synchronization: job.synchronizationSummary,
      verification: job.verification ? { ...job.verification, status: job.state === 'completed' ? job.outputVerification || 'not_checked' : job.verification.status } : undefined,
      artwork: job.artwork, compatibility: job.compatibility, hasReport: Boolean(job.evidence || job.retry?.events?.length),
      retry: job.retry ? { attempt: job.retry.used + 1, maxAttempts: 3, nextAt: job.retry.nextAt, parked: job.retry.parked === true,
        reason: job.retry.reason, failures: job.retry.events.filter(e => e.outcome === 'failed').length } : undefined,
      canRetry: WAITING.has(job.state) || job.state === 'failed' || job.state === 'cancelled',
      canRetryAudio: job.state === 'needs_audio' && !job.audio && Boolean(job.scorePath) && typeof this.provider.findAudio === 'function',
      canRetryRecording: job.state === 'needs_audio' && Boolean(job.audio),
      canRefreshRecording: job.state === 'needs_audio' && Boolean(job.audio && job.scorePath) && typeof this.provider.findAudio === 'function',
      canUseAccount: job.canUseAccount === true,
      canCancel: PENDING(job) };
  }
  snapshot() { return this.jobs.map((job) => this.public(job)); }
  enqueue(chart, { outputDir, outputSettings, hybridLead, originalsOnlyFrom, retainedFrom, requestedRevisionId }) {
    if (this.disposed || this.persistenceFailed) throw new Error('Reopen Songsterr before adding imports.');
    if (!path.isAbsolute(outputDir || '')) throw new Error('Choose an output folder in FeedForge Settings.');
    const options = normalizeHybridLead(hybridLead);
    if (requestedRevisionId != null && !numeric(requestedRevisionId)) throw new Error('Choose a valid revision.');
    requestedRevisionId = requestedRevisionId == null ? undefined : numeric(requestedRevisionId);
    const existing = this.jobs.find((job) => job.chart.id === String(chart.id) && PENDING(job)
      && job.requestedRevisionId === requestedRevisionId
      && JSON.stringify(normalizeHybridLead(job.hybridLead)) === JSON.stringify(options) && job.originalsOnlyFrom === originalsOnlyFrom);
    if (existing) return this.public(existing);
    if (this.jobs.filter(PENDING).length >= 30) throw new Error('Finish or cancel some imports before adding more songs.');
    const job = { id: crypto.randomUUID(), source: 'songsterr', sourceKey: `songsterr:${chart.id}`, chart: { ...chart, id: String(chart.id) },
      state: 'queued', createdAt: this.clock.now(), retry: retryCycle(), outputDir: path.resolve(outputDir), outputSettings: normalizeOutputSettings(outputSettings),
      requestedRevisionId, hybridLead: options, ...(originalsOnlyFrom ? { originalsOnlyFrom } : {}), controller: new AbortController() };
    if (retainedFrom?.scorePath) {
      const oldRoot = path.join(this.root, retainedFrom.id), source = containedFile(oldRoot, retainedFrom.scorePath);
      if (!source.path || fs.statSync(source.path).size > 80 * 1024 ** 2) throw new Error('The retained original tab is unavailable.');
      const bytes = fs.readFileSync(source.path);
      if (crypto.createHash('sha256').update(bytes).digest('hex') !== retainedFrom.cachedScoreHash) throw new Error('The retained original tab changed.');
      const directory = path.join(this.root, job.id);
      fs.mkdirSync(directory);
      const extension = path.extname(source.path).toLowerCase();
      if (!['.json', '.gp', '.gpif', '.xml'].includes(extension)) throw new Error('The retained tab format is unsupported.');
      job.scorePath = path.join(directory, 'source' + extension);
      fs.writeFileSync(job.scorePath, bytes, { flag: 'wx' });
      for (const key of ['metadata', 'pinnedDescriptor', 'audio', 'audioSelection', 'recordingRecovery', 'cachedScoreHash', 'sourceKey', 'allowAccount']) {
        if (retainedFrom[key] !== undefined) job[key] = structuredClone(retainedFrom[key]);
      }
      if (job.audio?.kind === 'file' && within(oldRoot, path.resolve(job.audio.path))) {
        const audio = containedFile(oldRoot, job.audio.path);
        if (!audio.path) throw new Error('The retained recording is unavailable.');
        const target = path.join(directory, 'recording' + path.extname(audio.path));
        fs.copyFileSync(audio.path, target, fs.constants.COPYFILE_EXCL);
        job.audio.path = target;
      }
    }
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
  retry(id, { audio, allowAccount, hybridLead, originalsOnly, rediscoverAudio = false } = {}) {
    if (this.disposed || this.persistenceFailed) throw new Error('Reopen Songsterr before retrying.');
    const job = this.jobs.find((j) => j.id === id);
    if (!job || !this.public(job).canRetry || this.current === job) throw new Error('This import cannot be retried yet.');
    if (rediscoverAudio && (audio || job.state !== 'needs_audio' || !job.scorePath || typeof this.provider.findAudio !== 'function')) {
      throw new Error('This import is not awaiting Songsterr audio recovery.');
    }
    if (originalsOnly === true) {
      if (!job.hybridLead?.enabled) throw new Error('This import already uses original arrangements only.');
      return this.enqueue(job.chart, { outputDir: job.outputDir, outputSettings: job.outputSettings, requestedRevisionId: job.requestedRevisionId, hybridLead: { enabled: false }, originalsOnlyFrom: job.id, retainedFrom: job });
    }
    if (hybridLead !== undefined) {
      if (job.state !== 'awaiting_main_choice') throw new Error('The source choices are not awaiting a selection.');
      const options = normalizeHybridLead(hybridLead), choice = job.hybridChoice;
      if (!options.enabled || options.sourceSha256 !== job.cachedScoreHash || options.sourceSha256 !== choice?.sourceSha256
          || !choice.tracks.some(t => t.id === options.mainTrackId)
          || [...options.excludedTrackIds, ...options.preferredTrackIds, ...Object.keys(options.roles)].some(id => !choice.tracks.some(t => t.id === id))) throw new Error('Choose available guitars for this exact tab revision.');
      delete options.reviewSources;
      job.hybridLead = options;
      delete job.hybridChoice;
    } else if (job.state === 'awaiting_main_choice') throw new Error('Choose a main guitar before continuing.');
    if (audio) { job.audio = audio; job.audioSelection = { version: 1, origin: 'user' }; delete job.recordingRecovery; }
    if (!rediscoverAudio && job.recordingRecovery) job.recordingRecovery.pending = false;
    if (rediscoverAudio) startRecovery(job, { explicit: true });
    if (allowAccount === true) job.allowAccount = true;
    // Audio discovery is an explicit, one-shot retry of the saved revision. A
    // supplied or previously chosen recording always keeps priority.
    job.retryAudioDetection = job.state === 'needs_audio' && !job.audio && Boolean(job.scorePath) && typeof this.provider.findAudio === 'function';
    job.controller = new AbortController(); job.committed = false; job.timedOut = false;
    job.retry = retryCycle(job.retry);
    this._set(job, 'queued', { error: '', alignment: undefined, synchronizationSummary: undefined, pathValidation: undefined, message: 'Queued.' }); this._start(); return this.public(job);
  }
  async cancel(id) {
    const job = this.jobs.find((j) => j.id === id);
    if (!job || job.committed) return;
    job.controller.abort(); terminate(job.child);
    if (this.current === job) await job.done;
    if (!job.committed && job.state !== 'cancelled') { this._event(job, 'cancelled'); this._set(job, 'cancelled', { message: 'Cancelled.', error: '' }); this._arm(); }
  }
  _event(job, outcome, extra = {}) {
    if (!job.retry) job.retry = retryCycle();
    const event = { time: this.clock.now(), attempt: job.retry.used + 1, outcome, stage: job.state,
      songId: job.chart.id, revisionId: job.metadata?.revisionId || job.pinnedDescriptor?.revisionId,
      scoreHash: job.cachedScoreHash, videoId: audioVideo(job.audio)?.videoId, evidence: job.evidence, ...extra };
    // Repeated busy/cooldown admission is still the same attempt, not a new
    // failure. Keep its one start record rather than evicting failed attempts.
    if (outcome === 'started' && job.retry.events.at(-1)?.outcome === 'started' && job.retry.events.at(-1)?.attempt === event.attempt) job.retry.events[job.retry.events.length - 1] = event;
    else job.retry.events = [...job.retry.events, event].slice(-30);
    const directory = path.join(this.retryHistoryRoot, job.id);
    fs.mkdirSync(directory, { recursive: true });
    if (fs.lstatSync(this.retryHistoryRoot).isSymbolicLink() || fs.lstatSync(directory).isSymbolicLink()) throw new Error('Import history folder changed.');
    try { atomicJson(path.join(directory, `${job.retry.cycle}.json`), { version: 1, jobId: job.id, cycle: job.retry.cycle, events: job.retry.events }); }
    catch (error) { this.persistenceFailed = true; throw error; }
  }
  _cooldown(job) {
    const services = ['songsterr'];
    if (job.audio?.kind === 'url') services.push(audioVideo(job.audio) ? 'youtube' : 'audio_host');
    return Math.max(0, ...services.map(s => this.cooldowns[s] || 0));
  }
  _arm() {
    this.clock.clearTimeout(this.retryTimer); this.retryTimer = null;
    if (this.disposed || this.persistenceFailed) return;
    const times = this.jobs.filter(j => j.state === 'retry_wait' || j.state === 'queued')
      .map(j => Math.max(j.state === 'retry_wait' ? j.retry.nextAt : 0, this._cooldown(j)));
    if (!times.length) return;
    this.retryTimer = this.clock.setTimeout(() => { this.retryTimer = null; this._start(); }, Math.min(2147483647, Math.max(1, Math.min(...times) - this.clock.now())));
    this.retryTimer?.unref?.();
  }
  _scheduleFailure(job, error) {
    if (job.controller.signal.aborted || job.committed || this.disposed) return false;
    const terminal = ['cancelled', 'alignment_failed', 'needs_attention', 'needs_login', 'unsupported_score', 'invalid_score', 'revision_unavailable', 'access_denied', 'dependency_missing', 'import_file_location'].includes(error.code);
    const detail = terminal ? null : transport(error.transport);
    if (detail?.retryAfterAt) this.cooldowns[detail.service] = Math.max(this.cooldowns[detail.service] || 0, detail.retryAfterAt);
    const recordingRecovery = error.code === 'needs_audio' && detail?.reason === 'recording_unavailable'
      && isSiteAudio(job) && Boolean(job.scorePath) && typeof this.provider.findAudio === 'function'
      && !job.recordingRecovery?.failedVideoIds?.includes(audioVideo(job.audio)?.videoId);
    const plan = retryPlan(detail, job.retry.used, this.clock.now(), this.clock.random, { recordingRecovery });
    this._event(job, 'failed', { transport: detail, decision: plan ? plan.parked ? 'parked' : recordingRecovery ? 'rediscover_full_mix' : 'retry' : 'stop', nextAt: plan?.at });
    if (!plan) return false;
    if (recordingRecovery) startRecovery(job);
    Object.assign(job.retry, { used: job.retry.used + 1, nextAt: plan.at, reason: detail.reason, parked: plan.parked });
    this._set(job, plan.parked ? 'needs_attention' : 'retry_wait', { error: '', message: plan.parked
      ? 'The service requested a long wait. Retry after the displayed time.' : recordingRecovery
        ? 'The recording is unavailable. Rechecking Songsterr for a working full mix.' : 'Temporary retrieval problem. Waiting to retry.' });
    return true;
  }
  _start() {
    if (this.draining || this.disposed || this.persistenceFailed) return;
    this.clock.clearTimeout(this.retryTimer); this.retryTimer = null;
    this.draining = Promise.resolve().then(async () => {
      await this.ready;
      while (!this.disposed && !this.persistenceFailed) {
        const job = this.jobs.filter(j => !j.controller.signal.aborted && (j.state === 'queued' || j.state === 'retry_wait')
          && (j.state === 'queued' || j.retry.nextAt <= this.clock.now()) && this._cooldown(j) <= this.clock.now())
          .sort((a, b) => (a.retry?.nextAt || a.createdAt) - (b.retry?.nextAt || b.createdAt))[0];
        if (!job) break;
        this.current = job;
        job.done = new Promise((resolve) => { job.resolveDone = resolve; });
        try {
          if (!job.retry) job.retry = retryCycle();
          delete job.retry.nextAt; delete job.retry.parked;
          this._event(job, 'started'); this._set(job, 'queued', { error: '' });
          await this._work(job);
          this._event(job, 'completed'); this._save();
        }
        catch (error) {
          if (job.committed) {
            // The receipt and atomic link are authoritative even if history persistence failed.
            job.state = 'completed'; job.message = 'FeedPak ready.'; job.error = '';
            this.emit(this.public(job));
            continue;
          }
          if (this.persistenceFailed) throw error;
          const code = job.controller.signal.aborted ? 'cancelled' : error.code;
          if (code === 'cancelled') this._event(job, 'cancelled');
          if (!this.disposed && ['busy', 'service_cooldown'].includes(code)) {
            Object.assign(job.retry, { nextAt: code === 'busy' ? this.clock.now() + 1000 : error.nextAt, reason: code === 'busy' ? 'provider_busy' : 'service_cooldown' });
            this._set(job, 'retry_wait', { message: code === 'busy' ? 'Waiting for the Songsterr browser…' : 'Waiting for the service to accept requests…', error: '' });
            continue;
          }
          job.canUseAccount = error.canUseAccount === true;
          if (error.pathValidation) job.pathValidation = error.pathValidation;
          for (const key of ['verification', 'evidence', 'warnings', 'compatibility', 'hybridChoice']) if (error[key]) job[key] = error[key];
          if (code === 'alignment_failed' && error.alignment && typeof error.alignment === 'object' && !Array.isArray(error.alignment)) job.alignment = error.alignment;
          if (this._scheduleFailure(job, error)) continue;
          this._set(job, WAITING.has(code) || code === 'cancelled' ? code : 'failed', { error: code === 'cancelled' ? '' : clean(error.message), message: code === 'cancelled' ? 'Cancelled.' : '' });
        } finally {
          job.child = null;
          await this._cleanAttempts(job).catch(() => {});
          await this._pruneCache().catch(() => {});
          this.current = null; job.resolveDone();
        }
      }
    }).catch((error) => {
      // A ledger/history failure must never dispatch an unrecorded retry or spin.
      this.persistenceFailed = true;
      for (const job of this.jobs.filter(j => j.state === 'queued' || j.state === 'retry_wait' || ACTIVE.has(j.state))) {
        job.state = 'needs_attention'; job.error = 'Import history could not be saved. Reopen FeedForge before retrying.';
        this.emit(this.public(job));
      }
    }).finally(() => { this.draining = null; this._arm(); });
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
          try { const progress = JSON.parse(line.slice(19)); if (['audio', 'aligning', 'converting', 'validating'].includes(progress.stage)) this._set(job, progress.stage, { message: clean(progress.message) }); }
          catch { if (this.persistenceFailed) { admission.abort(); terminate(job.child); } }
        } });
      job.child = null; check(job);
      if (this.persistenceFailed) throw new Error('Import history could not be saved.');
      if (timedOut) throw timeoutError();
      if (typeof result?.stdout !== 'string' || result.stdout.length > 4 * 1024 * 1024) throw new Error('The converter returned an invalid response.');
      let parsed; try { parsed = JSON.parse(result.stdout); } catch { throw new Error('The converter returned an unreadable response.'); }
      if (result.code !== 0 || parsed.ok !== true) throw Object.assign(new Error(clean(parsed.error) || 'Conversion failed.'), { code: parsed.code, transport: transport(parsed.transport), alignment: parsed.alignment, verification: parsed.verification, evidence: parsed.evidence, warnings: parsed.warnings, compatibility: parsed.compatibility, hybridChoice: parsed.hybridChoice });
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
    if ((!job.scorePath || (!fs.existsSync(job.scorePath) && job.retry.used === 0)) && !retryAudioDetection) {
      this._set(job, 'resolving', { message: 'Checking importable revisions…', error: '' });
      // A cancelled provider may have finished writing before its promise rejects.
      // A fresh acquisition folder makes retry safe without overwriting that file.
      const acquisition = fs.mkdtempSync(path.join(directory, 'source-'));
      const acquired = await this.provider.acquire(job.chart, { directory: acquisition, signal: job.controller.signal, allowAccount: job.allowAccount === true,
        requestedRevisionId: job.requestedRevisionId, pinnedDescriptor: job.pinnedDescriptor, onPinned: (descriptor) => {
          check(job);
          if (!validRevisionSelection(descriptor) || (job.requestedRevisionId && descriptor.revisionId !== job.requestedRevisionId)) throw new Error('The revision selection is invalid.');
          if (job.pinnedDescriptor && descriptor.revisionId !== job.pinnedDescriptor.revisionId) throw new Error('The selected revision changed.');
          job.pinnedDescriptor = descriptor; if (!job.audio && descriptor.audio) selectSiteAudio(job, descriptor.audio, descriptor.revisionId);
          this._save();
        },
        onProgress: (p) => this._set(job, 'downloading', { message: clean(p?.message || 'Retrieving the selected tab…') }) });
      check(job);
      const source = containedFile(acquisition, acquired?.path);
      if (!source.path) throw fileLocationError('The source returned an invalid tab file', 'acquired_score', source.reason);
      if (String(acquired.metadata?.songId) !== job.chart.id || !validRevisionSelection(acquired.metadata)) throw new Error('The source did not identify an eligible revision.');
      if (job.pinnedDescriptor && (String(acquired.metadata.revisionId) !== job.pinnedDescriptor.revisionId
          || JSON.stringify(acquired.metadata.revisionEvidence) !== JSON.stringify(job.pinnedDescriptor.revisionEvidence))) throw new Error('The acquired tab differs from the pinned revision.');
      job.scorePath = acquired.path; job.metadata = acquired.metadata;
      job.cachedScoreHash = await hashFile(job.scorePath, job.controller.signal);
      job.sourceKey = `songsterr:${job.chart.id}:${job.metadata.revisionId}`;
      if (!job.audio && acquired.audio) selectSiteAudio(job, acquired.audio);
      this._save();
    }
    if (String(job.metadata?.songId) !== job.chart.id || !validRevisionSelection(job.metadata)
        || (job.requestedRevisionId && String(job.metadata.revisionId) !== job.requestedRevisionId)
        || job.sourceKey !== `songsterr:${job.chart.id}:${job.metadata.revisionId}`) throw new Error('The saved tab revision is invalid. Search for the song again to create a new import.');
    const score = containedFile(directory, job.scorePath);
    if (!score.path) throw fileLocationError('The saved tab changed. Search for the song again to create a new import', 'cached_score', score.reason);
    if (await hashFile(score.path, job.controller.signal) !== job.cachedScoreHash) throw new Error('The saved tab changed. Search for the song again to create a new import.');
    await this._recheckRevision(job);
    check(job);
    if (job.recordingRecovery?.pending) {
      if (!validRecovery(job.recordingRecovery)) throw Object.assign(new Error('Recording recovery is invalid.'), { code: 'needs_attention' });
      this._set(job, 'audio', { message: 'Checking Songsterr for another full-song recording…' });
      const previousVideoId = audioVideo(job.audio)?.videoId;
      const audio = await this.provider.findAudio(job.chart, { revisionId: String(job.metadata.revisionId),
        excludedVideoIds: [...job.recordingRecovery.failedVideoIds], signal: job.controller.signal });
      check(job);
      const candidate = audioVideo(audio);
      if (!candidate || job.recordingRecovery.failedVideoIds.includes(candidate.videoId)) {
        throw Object.assign(new Error('Songsterr did not provide another usable full mix. Choose a matching recording link or audio file.'), { code: 'needs_audio' });
      }
      selectSiteAudio(job, audio); job.recordingRecovery.pending = false;
      for (const key of ['alignment', 'synchronizationSummary', 'verification', 'evidence', 'audioHash', 'recipe', 'outputVerification', 'compatibility', 'warnings']) delete job[key];
      this._event(job, 'recording_selected', { previousVideoId, decision: 'check_replacement_map' }); this._save();
    }
    if ((retryAudioDetection || job.audioDiscoveryPending || job.retry.reason === 'player_timeout') && !job.audio) {
      job.audioDiscoveryPending = true; this._save();
      this._set(job, 'audio', { message: 'Checking Songsterr for the recording…' });
      const audio = await this.provider.findAudio(job.chart, { revisionId: String(job.metadata.revisionId), signal: job.controller.signal });
      check(job);
      if (audio) selectSiteAudio(job, audio);
      job.audioDiscoveryPending = false; this._save();
    }
    if (!job.audio) throw Object.assign(new Error('No usable original audio was found. Choose an audio file or paste a link.'), { code: 'needs_audio' });
    // The recording service may only become known during this acquisition.
    // Recheck its shared cooldown before starting any downloader process.
    if (this._cooldown(job) > this.clock.now()) throw Object.assign(new Error('Service cooldown'), { code: 'service_cooldown', nextAt: this._cooldown(job) });
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
        if (error.code === 'busy') throw error;
        synchronization = { ...unavailableSynchronization(syncIdentity, 'network_error'), transport: error.transport };
      }
    }
    check(job);
    if (transport(synchronization.transport)) {
      if (job.retry.used < 2) throw Object.assign(new Error('Songsterr timing retrieval temporarily failed.'), { code: 'network_error', transport: synchronization.transport });
      this._event(job, 'timing_fallback', { transport: transport(synchronization.transport), decision: 'existing_alignment_fallback' });
      const detail = transport(synchronization.transport);
      if (detail.retryAfterAt) this.cooldowns[detail.service] = Math.max(this.cooldowns[detail.service] || 0, detail.retryAfterAt);
    }
    job.synchronizationSummary = synchronizationSummary(synchronization);
    if (job.recordingRecovery?.requireMap && !verifiedRecoveryMap(synchronization, syncIdentity)) {
      throw Object.assign(new Error('Songsterr could not verify a full-song timing map for the replacement recording. Choose a matching recording link or audio file.'), { code: 'needs_audio' });
    }
    this._set(job, 'converting', { message: 'Preparing the tab and audio…' });
    const attempt = fs.mkdtempSync(path.join(directory, 'attempt-'));
    const requestPath = path.join(attempt, 'request.json');
    atomicJson(requestPath, { managedRetries: true, scorePath: job.scorePath, metadata: job.metadata, audio: job.audio, synchronization, workDir: attempt,
      auditDir: this.auditRoot, artworkCacheDir: path.join(path.dirname(this.root), 'artwork-cache'), artworkLookup: this.artworkLookup,
      outputDir: job.outputDir, outputSettings: job.outputSettings, tools: this.tools, hybridLead: normalizeHybridLead(job.hybridLead) });
    let result;
    try { result = await this._run(job, ['--song-import-file', requestPath], attempt); }
    finally { fs.unlinkSync(requestPath); }
    const staged = containedFile(attempt, result.stagingPath);
    if (!staged.path) throw fileLocationError('The converter returned an invalid temporary FeedPak file', 'staged_feedpak', staged.reason);
    const staging = staged.path;
    this._set(job, 'validating', { message: 'Checking the completed FeedPak…' });
    await this._run(job, ['--validate-feedpak', staging], attempt);
    const outputHash = await hashFile(staging, job.controller.signal);
    if (result.recipe?.preservationContract !== CURRENT_PRESERVATION_CONTRACT || result.verification?.version !== CURRENT_PRESERVATION_CONTRACT || result.verification.status !== 'passed'
        || result.verification.outputHash !== outputHash) throw new Error('The converter did not provide a current source verification. Update the converter and retry.');
    const checked = inspectEvidence(this.auditRoot, result.evidence, outputHash);
    if (checked.verification.version !== CURRENT_PRESERVATION_CONTRACT || checked.verification.status !== 'passed') throw new Error('Source verification did not pass.');
    if (result.recipe?.chartGuidancePolicy !== CHART_GUIDANCE_POLICY
        || !verifiedChartGuidance(checked.verification.chartGuidance, result.coverage?.arrangements)
        || !verifiedChartGuidance(result.verification.chartGuidance, result.coverage?.arrangements))
      throw new Error('The converter did not provide verified arrangement guidance. Update the converter and retry.');
    if (result.scoreHash !== job.cachedScoreHash || checked.record.objects.source !== job.cachedScoreHash) throw new Error('Source verification describes a different tab.');
    if (!matchesHybridRequest(job.hybridLead, result.recipe?.hybridLead)) throw new Error('The completed arrangement does not match the requested Hybrid Lead options.');
    if (job.hybridLead?.enabled) {
      const proof = checked.verification.hybridLead, summary = result.verification.hybridLead, options = result.recipe.hybridLead;
      if (!proof || !['created', 'no_additions', 'not_applicable'].includes(proof.status) || summary?.status !== proof.status
          || proof.status !== 'not_applicable' && (proof.policy !== 'hybrid-lead-v3' || proof.primaryCoverage !== 'checked'
            || summary.policy !== proof.policy || summary.primaryCoverage !== proof.primaryCoverage
            || options.sourceSha256 !== job.cachedScoreHash || !options.mainTrackId
            || proof.mainTrackId !== options.mainTrackId || summary.mainTrackId !== options.mainTrackId)) throw new Error('Hybrid Lead did not provide verified composition evidence.');
    }
    Object.assign(job, { outputRelativePath: result.relativePath, outputHash,
      verification: result.verification, evidence: result.evidence, artwork: result.artwork, compatibility: result.compatibility, outputVerification: 'passed',
      scoreHash: result.scoreHash, audioHash: result.audioHash, recipe: result.recipe, alignment: result.alignment, coverage: result.coverage, warnings: result.warnings });
    const identity = (j) => JSON.stringify([j.sourceKey, j.scoreHash, j.audioHash, j.recipe, j.converterRecipe, j.outputDir, j.outputRelativePath, j.outputSettings]);
    const prior = this.jobs.find((j) => j !== job && j.state === 'completed' && identity(j) === identity(job) && j.outputPath);
    let canReuse = false;
    if (prior && prior.verification?.version === CURRENT_PRESERVATION_CONTRACT && prior.verification.status === 'passed') {
      try {
        const checkedPrior = inspectEvidence(this.auditRoot, prior.evidence, prior.outputHash);
        canReuse = await hashFile(prior.outputPath, job.controller.signal) === prior.outputHash
          && checkedPrior.verification.version === CURRENT_PRESERVATION_CONTRACT && checkedPrior.verification.status === 'passed'
          && verifiedChartGuidance(checkedPrior.verification.chartGuidance, result.coverage?.arrangements);
      } catch { /* A missing historical report does not invalidate this fresh conversion. */ }
    }
    await this._recheckRevision(job);
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
  async dispose() { this.disposed = true; this.clock.clearTimeout(this.retryTimer); for (const job of this.jobs) if (ACTIVE.has(job.state)) { job.controller.abort(); terminate(job.child); } await this.draining; }
  async _recheckRevision(job) {
    if (job.metadata?.revisionEvidence?.version !== 2) return;
    if (typeof this.provider.revalidate !== 'function') throw new Error('The selected revision cannot be rechecked. Update FeedForge and retry.');
    check(job);
    const recheck = await this.provider.revalidate(job.chart, { metadata: job.metadata, signal: job.controller.signal });
    check(job);
    if (recheck?.revisionId !== String(job.metadata.revisionId)) throw new Error('The revision recheck returned a different tab.');
    job.revisionRecheck = recheck; this._save();
  }
}
module.exports = { SongsterrJobs, hashFile, atomicJson };

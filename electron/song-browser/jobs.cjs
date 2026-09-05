"use strict";

const fs = require("node:fs");
const fsp = fs.promises;
const path = require("node:path");
const crypto = require("node:crypto");
const { execFile } = require("node:child_process");

const ACTIVE = new Set(["queued", "downloading", "needs_attention", "inspecting", "converting", "validating"]);
const TERMINAL = new Set(["completed", "failed", "cancelled"]);
const MAX_HISTORY = 100;
const MAX_QUEUE = 30;
const MAX_INPUT_BYTES = 512 * 1024 * 1024;
const MAX_JSON_BYTES = 4 * 1024 * 1024;
const MAX_CACHE_FILES = 3;
const MAX_CACHE_BYTES = 1024 * 1024 * 1024;
const CACHE_LIFETIME_MS = 7 * 24 * 60 * 60 * 1000;
const SUPPORTED_HOSTS = new Set(["dropbox", "google-drive", "mediafire"]);
const UUID = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/;
const HASH = /^[a-f0-9]{64}$/;

function text(value, limit = 200) {
  return String(value ?? "")
    .replace(/\b(?:https?|ftp|file):\/\/[^\s<>"']+/gi, "[link]")
    .replace(/\bwww\.[^\s<>"']+/gi, "[link]")
    .replace(/[\u0000-\u001f\u007f]/g, " ").trim().slice(0, limit);
}

function chartId(chart) {
  const value = chart && (chart.chartId ?? chart.cdlc_id ?? chart.id);
  const id = typeof value === "number" && Number.isSafeInteger(value) ? String(value) : String(value ?? "").trim();
  if (!/^[1-9][0-9]{0,11}$/.test(id)) throw new Error("Choose a chart with a valid CustomsForge record ID.");
  return id;
}

function inside(root, target) {
  const relative = path.relative(root, target);
  return Boolean(relative) && relative !== ".." && !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative);
}

function identity(value) {
  return String(value ?? "").normalize("NFKD").replace(/\p{M}/gu, "")
    .toLowerCase().replace(/&/g, "and").replace(/[^\p{L}\p{N}]/gu, "");
}

function outputName(artist, title, id) {
  const base = `${text(artist) || "Unknown artist"} - ${text(title) || "Untitled"}`
    .replace(/[<>:"/\\|?*\u0000-\u001f]/g, "_").replace(/\s+/g, " ").trim().replace(/[. ]+$/, "").slice(0, 160);
  return `${base} [CF ${id}].feedpak`;
}

function aborted() {
  const error = new Error("Cancelled.");
  error.name = "AbortError";
  return error;
}

function check(job) {
  if (job.controller.signal.aborted) throw aborted();
}

async function hashFile(filename, signal) {
  const hash = crypto.createHash("sha256");
  for await (const chunk of fs.createReadStream(filename, { signal })) hash.update(chunk);
  return hash.digest("hex");
}

function hashFileSync(filename) {
  const hash = crypto.createHash("sha256");
  const buffer = Buffer.alloc(1024 * 1024);
  const descriptor = fs.openSync(filename, "r");
  try {
    let count;
    while ((count = fs.readSync(descriptor, buffer, 0, buffer.length, null))) hash.update(buffer.subarray(0, count));
    return hash.digest("hex");
  } finally { fs.closeSync(descriptor); }
}

function jsonResult(result, label) {
  if (!result || result.code !== 0) {
    throw new Error(`${label} failed: ${text(result?.stderr || result?.stdout || "converter did not finish successfully", 400)}`);
  }
  if (typeof result.stdout !== "string" || Buffer.byteLength(result.stdout) > MAX_JSON_BYTES) {
    throw new Error(`${label} returned an oversized or invalid response.`);
  }
  try { return JSON.parse(result.stdout); }
  catch { throw new Error(`${label} did not return valid JSON.`); }
}

/** A serial, local queue. The downloader owns the browser session, never this class. */
class SongJobs {
  constructor({ root, outputDir, download, runConverter, emit = () => {} }) {
    if (!root || !outputDir || typeof download !== "function" || typeof runConverter !== "function") {
      throw new TypeError("SongJobs needs root, outputDir, download, and runConverter.");
    }
    fs.mkdirSync(path.resolve(root), { recursive: true });
    // Match fs.promises.realpath: Windows app-data redirection can differ from
    // the legacy JavaScript resolver even when no junction is visible.
    this.root = fs.realpathSync.native(path.resolve(root));
    this.outputDir = path.resolve(outputDir);
    this.download = download;
    this.runConverter = runConverter;
    this.emit = emit;
    this.ledger = path.join(this.root, "jobs.json");
    this.cacheDir = path.join(this.root, "cache");
    this.persistenceWarning = "";
    this.jobs = [];
    this.current = null;
    this.disposed = false;
    this.draining = null;
    this._load();
    this._reconcile();
    this._pruneCache();
  }

  _public(job) {
    const result = {};
    for (const key of ["id", "chartId", "title", "artist", "creator", "host", "state", "progress", "message", "error", "outputPath", "createdAt", "updatedAt", "sourceHash", "outputHash", "duplicateOf"]) {
      if (job[key] !== undefined) result[key] = job[key];
    }
    result.hasCachedInput = Boolean(job.cacheHash && job.cacheAt > Date.now() - CACHE_LIFETIME_MS);
    result.canRetry = ["failed", "cancelled"].includes(job.state) && (result.hasCachedInput || job.supported === true);
    result.inOutputDir = false;
    if (job.state === "completed" && job.outputPath) {
      try {
        const stat = fs.lstatSync(job.outputPath);
        result.inOutputDir = stat.isFile() && !stat.isSymbolicLink() && fs.realpathSync.native(path.dirname(job.outputPath)) === fs.realpathSync.native(this.outputDir);
      } catch { /* A missing file is not ready in the selected output. */ }
    }
    if (job.warning || this.persistenceWarning) result.warning = text([job.warning, this.persistenceWarning].filter(Boolean).join(" "), 500);
    return result;
  }

  snapshot() { return [...this.jobs].reverse().map((job) => this._public(job)); }

  _receiptPath(job) { return path.join(this.root, `${job.id}.receipt.json`); }

  _writeReceipt(job, outputPath) {
    const target = this._receiptPath(job);
    const temporary = path.join(this.root, `.receipt-${crypto.randomUUID()}.tmp`);
    try {
      fs.writeFileSync(temporary, JSON.stringify({ version: 1, job: {
        ...this._public(job), outputPath, state: "completed", progress: 100,
        supported: job.supported === true,
      } }), { flag: "wx", mode: 0o600 });
      fs.renameSync(temporary, target);
    } catch (error) {
      throw new Error(`Could not save a recovery receipt before publishing the FeedPak: ${text(error.message, 200)}`);
    } finally { try { fs.unlinkSync(temporary); } catch { /* Only our exact temporary file is eligible. */ } }
  }

  _reconcile() {
    // A receipt is written before the exclusive publication. Its hash proves whether
    // publication finished even if the app stopped before jobs.json was updated.
    const names = fs.readdirSync(this.root).filter((name) => /^[a-f0-9-]+\.receipt\.json$/.test(name)).slice(0, MAX_HISTORY);
    for (const name of names) {
      const filename = path.join(this.root, name);
      try {
        const stat = fs.lstatSync(filename);
        if (!stat.isFile() || stat.isSymbolicLink() || stat.size > 16384) continue;
        const receipt = JSON.parse(fs.readFileSync(filename, "utf8"));
        const row = receipt.job;
        if (receipt.version !== 1 || !row || !UUID.test(row.id) || name !== `${row.id}.receipt.json` || !HASH.test(row.outputHash || "") || !HASH.test(row.sourceHash || "")) continue;
        const id = chartId(row);
        if (typeof row.outputPath !== "string" || row.outputPath.length > 4096 || !path.isAbsolute(row.outputPath)) continue;
        let job = this.jobs.find((item) => item.id === row.id);
        if (job?.state === "completed" && job.outputPath === row.outputPath && job.outputHash === row.outputHash) continue;
        const output = fs.lstatSync(row.outputPath);
        if (!output.isFile() || output.isSymbolicLink() || !output.size || output.size > 2 * 1024 * 1024 * 1024 || hashFileSync(row.outputPath) !== row.outputHash) continue;
        if (!job) {
          job = { id: row.id, chartId: id, title: text(row.title), artist: text(row.artist), creator: text(row.creator),
            host: SUPPORTED_HOSTS.has(row.host) ? row.host : "unknown", supported: row.supported === true && SUPPORTED_HOSTS.has(row.host),
            createdAt: Number(row.createdAt) || Date.now() };
          this.jobs.push(job);
        }
        Object.assign(job, { state: "completed", progress: 100, outputPath: row.outputPath,
          outputHash: row.outputHash, sourceHash: row.sourceHash, message: "Saved FeedPak recovered after restart.", error: "", updatedAt: Date.now() });
        this._deleteCache(job);
      } catch { /* Missing, unrelated or modified outputs must never be marked completed. */ }
    }
    this._persist();
  }

  _cachePath(job) { return path.join(this.cacheDir, `${job.id}.psarc`); }

  _safeCacheDirectory(create = false) {
    if (create) fs.mkdirSync(this.cacheDir, { recursive: true });
    const stat = fs.lstatSync(this.cacheDir);
    if (!stat.isDirectory() || stat.isSymbolicLink() || fs.realpathSync.native(this.cacheDir) !== this.cacheDir) throw new Error("The song cache folder is not a supported local directory.");
  }

  _deleteCache(job) {
    if (!job || !UUID.test(job.id) || !job.cacheHash) return;
    try {
      this._safeCacheDirectory();
      const filename = this._cachePath(job);
      const stat = fs.lstatSync(filename);
      if (!stat.isFile() || stat.isSymbolicLink()) return;
      fs.unlinkSync(filename);
    } catch (error) {
      if (error.code !== "ENOENT") { job.warning = "A cached PSARC could not be removed. Use Clear cache to try again."; return; }
    }
    delete job.cacheHash; delete job.cacheBytes; delete job.cacheAt;
  }

  _pruneCache(incomingBytes = 0) {
    for (const job of this.jobs) if (job.cacheHash && job.cacheAt < Date.now() - CACHE_LIFETIME_MS && !ACTIVE.has(job.state) && !this.jobs.some((item) => ACTIVE.has(item.state) && item.retryOf === job.id)) this._deleteCache(job);
    const cached = this.jobs.filter((job) => job.cacheHash).sort((a, b) => a.cacheAt - b.cacheAt);
    let bytes = cached.reduce((sum, job) => sum + job.cacheBytes, 0);
    let count = cached.length;
    for (const job of cached) {
      if (count + (incomingBytes ? 1 : 0) <= MAX_CACHE_FILES && bytes + incomingBytes <= MAX_CACHE_BYTES) break;
      if (ACTIVE.has(job.state) || this.jobs.some((item) => ACTIVE.has(item.state) && item.retryOf === job.id)) continue;
      const size = job.cacheBytes;
      this._deleteCache(job);
      if (!job.cacheHash) { count--; bytes -= size; this._event(job); }
    }
    if (incomingBytes && (count >= MAX_CACHE_FILES || bytes + incomingBytes > MAX_CACHE_BYTES)) throw new Error("The temporary PSARC cache is full. Clear a cached song before retrying.");
  }

  async _retain(job, input) {
    if (job.cacheHash) return;
    const stat = await fsp.stat(input);
    this._pruneCache(stat.size);
    this._safeCacheDirectory(true);
    const filename = this._cachePath(job);
    try {
      await fsp.copyFile(input, filename, fs.constants.COPYFILE_EXCL);
      job.cacheHash = job.sourceHash; job.cacheBytes = stat.size; job.cacheAt = Date.now();
      this._persist();
    } catch (error) {
      // If the exclusive copy created a partial file, record ownership only long
      // enough to remove it. Never overwrite or remove a pre-existing cache entry.
      if (error.code !== "EEXIST") {
        job.cacheHash = job.sourceHash;
        this._deleteCache(job);
      }
      throw error;
    }
  }

  async getCachedInput(id) {
    const job = this.jobs.find((item) => item.id === id);
    if (!job?.cacheHash || job.cacheAt < Date.now() - CACHE_LIFETIME_MS) throw new Error("This job has no retained PSARC. Retry the download instead.");
    this._safeCacheDirectory();
    const filename = this._cachePath(job);
    const stat = await fsp.lstat(filename);
    if (!stat.isFile() || stat.isSymbolicLink() || stat.size !== job.cacheBytes || stat.size > MAX_INPUT_BYTES || await hashFile(filename) !== job.cacheHash) {
      throw new Error("The retained PSARC changed or is incomplete. Clear its cache and retry the download.");
    }
    return filename;
  }

  async clearCache(id) {
    const job = this.jobs.find((item) => item.id === id);
    if (!job) throw new Error("This song job is no longer available.");
    if (ACTIVE.has(job.state) || this.jobs.some((item) => ACTIVE.has(item.state) && item.retryOf === id)) throw new Error("Finish or cancel the job before clearing its cache.");
    this._deleteCache(job); this._persist(); this._event(job);
    return this._public(job);
  }

  retry(id) {
    const job = this.jobs.find((item) => item.id === id);
    if (!job || !this._public(job).canRetry) throw new Error("This job cannot be retried. Search for the chart again.");
    const existing = this.jobs.find((item) => item.chartId === job.chartId && ACTIVE.has(item.state));
    if (existing) return this._public(existing);
    const retryOf = job.cacheHash && job.cacheAt > Date.now() - CACHE_LIFETIME_MS ? id : undefined;
    return this._enqueue({ id: job.chartId, title: job.title, artist: job.artist, creator: job.creator, host: job.host, supported: job.supported }, retryOf);
  }

  validateOutputDir(directory = this.outputDir) {
    if (typeof directory !== "string" || !directory.trim()) throw new Error("Choose an output folder.");
    const resolved = path.resolve(directory);
    let probe, link, ownsProbe = false, ownsLink = false;
    try {
      fs.mkdirSync(resolved, { recursive: true });
      const real = fs.realpathSync.native(resolved);
      if (real === this.root || inside(this.root, real)) throw new Error("Choose a folder outside the song browser's temporary storage.");
      probe = path.join(real, `.feedforge-preflight-${crypto.randomUUID()}.tmp`);
      link = `${probe}.link`;
      fs.writeFileSync(probe, "FeedForge output check", { flag: "wx", mode: 0o600 }); ownsProbe = true;
      fs.linkSync(probe, link); ownsLink = true;
      return resolved;
    } catch (error) {
      throw new Error(`The output folder cannot safely save FeedPaks. Choose a writable folder on a filesystem with hard-link support (such as NTFS). ${text(error.message, 200)}`);
    } finally {
      if (ownsLink) { try { fs.unlinkSync(link); } catch { /* Only exact owned probe paths are eligible. */ } }
      if (ownsProbe) { try { fs.unlinkSync(probe); } catch { /* A leftover probe is harmless and never a song. */ } }
    }
  }

  _load() {
    if (!fs.existsSync(this.ledger)) return;
    if (fs.lstatSync(this.ledger).isSymbolicLink() || fs.statSync(this.ledger).size > 1024 * 1024) {
      throw new Error("Song job history is not a supported local ledger.");
    }
    let data;
    try { data = JSON.parse(fs.readFileSync(this.ledger, "utf8")); }
    catch { throw new Error("Song job history could not be read. Preserve jobs.json before repairing it."); }
    if (data.version !== 1 || !Array.isArray(data.jobs) || data.jobs.length > MAX_HISTORY) {
      throw new Error("Song job history has an unsupported format.");
    }
    const seen = new Set();
    for (const row of data.jobs) {
      if (!row || !UUID.test(row.id) || seen.has(row.id) || (!ACTIVE.has(row.state) && !TERMINAL.has(row.state))) continue;
      let id;
      try { id = chartId(row); } catch { continue; }
      seen.add(row.id);
      const interrupted = ACTIVE.has(row.state);
      const job = {
        id: row.id, chartId: id, title: text(row.title), artist: text(row.artist), creator: text(row.creator),
        host: SUPPORTED_HOSTS.has(row.host) ? row.host : "unknown", supported: row.supported === true && SUPPORTED_HOSTS.has(row.host),
        state: interrupted ? "failed" : row.state,
        progress: interrupted ? 0 : Math.max(0, Math.min(100, Number(row.progress) || 0)),
        message: interrupted ? "Interrupted when the app closed." : text(row.message, 300),
        error: interrupted ? "The previous job was interrupted. Retry this job or choose the chart again." : text(row.error, 500),
        createdAt: Number(row.createdAt) || Date.now(), updatedAt: interrupted ? Date.now() : Number(row.updatedAt) || Date.now(),
      };
      if (HASH.test(row.sourceHash || "")) job.sourceHash = row.sourceHash;
      if (HASH.test(row.outputHash || "")) job.outputHash = row.outputHash;
      if (typeof row.outputPath === "string" && row.outputPath.length < 4096 && path.isAbsolute(row.outputPath) && !/^[a-z]+:\/\//i.test(row.outputPath)) job.outputPath = row.outputPath;
      if (UUID.test(row.duplicateOf || "")) job.duplicateOf = row.duplicateOf;
      if (HASH.test(row.cacheHash || "") && Number(row.cacheBytes) >= 32 && Number(row.cacheBytes) <= MAX_INPUT_BYTES) {
        job.cacheHash = row.cacheHash; job.cacheBytes = Number(row.cacheBytes); job.cacheAt = Number(row.cacheAt) || 0;
      }
      this.jobs.push(job);
    }
    // Interrupted jobs are recorded as failed, never resumed or deleted automatically.
  }

  _persist() {
    while (this.jobs.length > MAX_HISTORY) {
      const index = this.jobs.findIndex((job) => TERMINAL.has(job.state) && !this.jobs.some((item) => ACTIVE.has(item.state) && item.retryOf === job.id));
      if (index < 0) break;
      this._deleteCache(this.jobs[index]);
      try { fs.unlinkSync(this._receiptPath(this.jobs[index])); } catch { /* Pruned history no longer needs its receipt. */ }
      this.jobs.splice(index, 1);
    }
    const temporary = path.join(this.root, `.jobs-${crypto.randomUUID()}.tmp`);
    try {
      fs.writeFileSync(temporary, JSON.stringify({ version: 1, jobs: this.jobs.map((job) => ({ ...this._public(job),
        supported: job.supported === true, cacheHash: job.cacheHash, cacheBytes: job.cacheBytes, cacheAt: job.cacheAt,
      })) }), { flag: "wx", mode: 0o600 });
      fs.renameSync(temporary, this.ledger);
      this.persistenceWarning = "";
      for (const job of this.jobs) {
        if (job.state === "completed") {
          try { fs.unlinkSync(this._receiptPath(job)); } catch { /* A durable receipt can safely be reconciled again. */ }
        }
      }
      return true;
    } catch (error) {
      this.persistenceWarning = `Song job history could not be saved. Completed files are preserved; restart recovery will use their receipts. ${text(error.message, 200)}`;
      return false;
    } finally {
      try { fs.unlinkSync(temporary); } catch (error) { if (error.code !== "ENOENT") { /* Preserve unrelated state. */ } }
    }
  }

  _event(job) { try { this.emit(this._public(job)); } catch { /* A UI observer cannot interrupt a job. */ } }

  _set(job, state, fields = {}) {
    Object.assign(job, fields, { state, updatedAt: Date.now() });
    if (fields.message !== undefined) job.message = text(fields.message, 300);
    if (fields.error !== undefined) job.error = text(fields.error, 500);
    this._persist();
    this._event(job);
  }

  enqueue(chart) { return this._enqueue(chart); }

  _enqueue(chart, retryOf) {
    if (this.disposed) throw new Error("Song browser is closed.");
    const id = chartId(chart);
    const existing = this.jobs.find((job) => job.chartId === id && ACTIVE.has(job.state));
    if (existing) return this._public(existing);
    if (this.jobs.filter((job) => ACTIVE.has(job.state)).length >= MAX_QUEUE) throw new Error("The song queue is full. Let the current downloads finish first.");
    const now = Date.now();
    const job = {
      id: crypto.randomUUID(), chartId: id, title: text(chart.title), artist: text(chart.artist), creator: text(chart.creator),
      host: SUPPORTED_HOSTS.has(chart.host) ? chart.host : "unknown", supported: chart.supported === true && SUPPORTED_HOSTS.has(chart.host),
      state: "queued", progress: 0, message: "Waiting in queue.", error: "", createdAt: now, updatedAt: now,
      chart: { id, title: text(chart.title), artist: text(chart.artist), creator: text(chart.creator), host: chart.host, supported: chart.supported === true }, controller: new AbortController(), process: null,
      retryOf,
    };
    job.done = new Promise((resolve) => { job.resolveDone = resolve; });
    this.jobs.push(job);
    this._persist();
    this._event(job);
    queueMicrotask(() => this._start());
    return this._public(job);
  }

  setOutputDir(directory) {
    if (this.disposed) throw new Error("Song browser is closed.");
    if (this.jobs.some((job) => ACTIVE.has(job.state))) throw new Error("Finish or cancel queued songs before changing the output folder.");
    this.outputDir = this.validateOutputDir(directory);
    return this.outputDir;
  }

  async cancel(id) {
    const job = this.jobs.find((item) => item.id === id);
    if (!job) return null;
    if (!ACTIVE.has(job.state)) return this._public(job);
    // A fully validated file already published atomically is a completed operation.
    if (job.committed) { await job.done; return this._public(job); }
    job.controller.abort();
    if (job.state === "queued" && this.current !== job) {
      try { this._set(job, "cancelled", { message: "Cancelled.", progress: 0 }); }
      catch { this._event(job); }
      job.resolveDone();
    } else {
      try { this._set(job, job.state, { message: "Cancelling…" }); }
      catch { this._event(job); }
      this._terminate(job.process);
      await job.done;
    }
    return this._public(job);
  }

  _terminate(record) {
    if (!record || record.closed || record.terminating) return;
    record.terminating = true;
    const child = record.child;
    if (process.platform === "win32" && Number.isInteger(child.pid) && child.pid > 0) {
      // Only a child supplied by this job's onSpawn callback is eligible. No shell.
      execFile("taskkill.exe", ["/PID", String(child.pid), "/T", "/F"], { windowsHide: true, timeout: 10000, maxBuffer: 4096 }, (error) => {
        if (error && !record.closed) {
          try { child.kill(); } catch { /* Keep waiting for the actual child exit. */ }
        }
      });
    } else {
      try { child.kill("SIGTERM"); } catch { /* Keep waiting; never advance over a live child. */ }
    }
  }

  async _converter(job, args) {
    check(job);
    let record;
    try {
      const result = await this.runConverter(args, {
        // The adapter scopes Python/tool temporary files without changing global config.
        directory: path.join(this.root, job.id),
        onSpawn: (child) => {
          if (!child || typeof child.once !== "function") throw new Error("Converter did not provide a process handle.");
          record = { child, closed: child.exitCode !== null && child.exitCode !== undefined, terminating: false };
          record.exited = record.closed ? Promise.resolve() : new Promise((resolve) => {
            child.once("close", () => { record.closed = true; resolve(); });
          });
          job.process = record;
          if (job.controller.signal.aborted) this._terminate(record);
        },
      });
      if (record) await record.exited;
      check(job);
      return result;
    } finally {
      // Even a rejected runner must not leave a child using the work directory.
      if (record && !record.closed) {
        this._terminate(record);
        await record.exited;
      }
      if (job.process === record) job.process = null;
    }
  }

  _start() {
    if (this.draining || this.disposed) return;
    this.draining = this._drain().finally(() => {
      this.draining = null;
      if (!this.disposed && this.jobs.some((job) => job.state === "queued")) queueMicrotask(() => this._start());
    });
    // _drain handles every per-job error. Retain a rejection handler for shutdown races.
    this.draining.catch(() => {});
  }

  async _drain() {
    for (;;) {
      const job = this.jobs.find((item) => item.state === "queued");
      if (!job || this.disposed) return;
      this.current = job;
      const directory = path.join(this.root, job.id);
      let owned = false;
      try {
        check(job);
        await fsp.mkdir(directory); // Never adopt an existing directory.
        owned = true;
        check(job);
        await this._work(job, directory);
      } catch (error) {
        const cancelled = job.controller.signal.aborted;
        Object.assign(job, job.committed ? {
          state: "completed", progress: 100, updatedAt: Date.now(),
          message: "FeedPak saved successfully.", error: "", warning: `The FeedPak was saved, but final bookkeeping needs attention. ${text(error.message || error, 200)}`,
        } : {
          state: cancelled ? "cancelled" : "failed", progress: 0, updatedAt: Date.now(),
          message: cancelled ? "Cancelled." : "Could not add this song.",
          error: cancelled ? "" : text(error.message || error, 500),
        });
        if (cancelled || job.committed) this._deleteCache(job);
        if (!job.committed) { try { fs.unlinkSync(this._receiptPath(job)); } catch { /* No output was committed by this attempt. */ } }
        this._persist();
        this._event(job);
      } finally {
        if (owned) await this._cleanup(directory);
        job.chart = null;
        job.process = null;
        this.current = null;
        job.resolveDone();
      }
    }
  }

  async _cleanup(directory) {
    // Recursive removal is confined to a freshly created UUID directory in this root.
    if (!UUID.test(path.basename(directory)) || !inside(this.root, path.resolve(directory))) return;
    try {
      const stat = await fsp.lstat(directory);
      if (stat.isSymbolicLink() || !stat.isDirectory()) return;
      const real = await fsp.realpath(directory);
      if (!inside(this.root, real)) return;
      await fsp.rm(directory, { recursive: true, force: true });
    } catch { /* Failed cleanup leaves only this job's cache for inspection. */ }
  }

  async _work(job, directory) {
    this.validateOutputDir();
    check(job);
    let input;
    if (job.retryOf) {
      this._set(job, "inspecting", { message: "Checking the retained PSARC.", progress: 0 });
      const cached = await this.getCachedInput(job.retryOf);
      check(job);
      input = path.join(directory, "source.psarc");
      // A synchronous exclusive copy closes the gap between lookup and eviction.
      fs.copyFileSync(cached, input, fs.constants.COPYFILE_EXCL);
    } else {
    this._set(job, "downloading", { message: "Downloading selected chart.", progress: 0 });
    const canUpdate = () => !job.controller.signal.aborted && ["downloading", "needs_attention"].includes(job.state);
    input = await this.download(job.chart, {
      jobId: job.id, directory, destination: path.join(directory, "source.psarc"), signal: job.controller.signal,
      onProgress: (value) => {
        if (!canUpdate() || !Number.isFinite(Number(value))) return;
        const progress = Math.floor(Math.max(0, Math.min(100, Number(value))));
        if (job.progress !== progress || job.state === "needs_attention") this._set(job, "downloading", { progress, message: "Downloading selected chart." });
      },
      onAttention: (message) => {
        if (!canUpdate()) return;
        this._set(job, message ? "needs_attention" : "downloading", { message: message ? text(typeof message === "string" ? message : message.message, 300) || "Complete the step in the song browser to continue." : "Downloading selected chart." });
      },
    });
    }
    check(job);
    if (typeof input !== "string" || !inside(directory, path.resolve(input))) throw new Error("The downloaded file was outside its job folder.");
    const stat = await fsp.lstat(input);
    if (stat.isSymbolicLink() || !stat.isFile() || !inside(directory, await fsp.realpath(input))) throw new Error("The download is not a regular file in its job folder.");
    if (stat.size < 32 || stat.size > MAX_INPUT_BYTES) throw new Error("The PSARC download is empty, incomplete, or exceeds the 512 MB limit.");
    const handle = await fsp.open(input, "r");
    try {
      const header = Buffer.alloc(4);
      await handle.read(header, 0, 4, 0);
      if (header.toString("ascii") !== "PSAR") throw new Error("The host returned a page or another file instead of a PSARC song. Review the download in the browser.");
    } finally { await handle.close(); }
    this._set(job, "inspecting", { message: "Checking song identity and arrangements.", progress: 0 });
    job.sourceHash = await hashFile(input, job.controller.signal);
    const inspection = jsonResult(await this._converter(job, ["--inspect-json", input]), "Song inspection");
    const preview = inspection.preview;
    if (!inspection.ok || !preview || !Array.isArray(preview.arrangements) || !preview.arrangements.length) throw new Error("The downloaded PSARC has no readable playable arrangements.");
    try { await this._retain(job, input); }
    catch (error) { job.warning = `A recovery copy of this PSARC could not be retained. ${text(error.message, 200)}`; }
    check(job);
    if (preview.is_multi_song || Number(preview.song_count) > 1) throw new Error("This download contains multiple songs. Open it in FeedForge to review and convert the songs separately.");
    for (const key of ["title", "artist"]) {
      if (!identity(preview[key]) || (identity(job[key]) && identity(job[key]) !== identity(preview[key]))) {
        throw new Error(`Downloaded ${key} does not match the selected chart (${text(preview[key]) || "unknown"}). Review the file manually in FeedForge.`);
      }
    }
    job.title = text(preview.title);
    job.artist = text(preview.artist);
    const duplicate = await this._duplicate(job);
    if (duplicate) {
      check(job);
      job.outputHash = duplicate.outputHash;
      const currentRoot = await fsp.realpath(this.outputDir);
      const previousRoot = await fsp.realpath(path.dirname(duplicate.outputPath));
      let output = duplicate.outputPath;
      if (currentRoot !== previousRoot) output = await this._publish(job, duplicate.outputPath);
      else {
        this._writeReceipt(job, output);
        job.outputPath = output; job.committed = true;
      }
      this._deleteCache(job);
      this._set(job, "completed", { progress: 100, outputPath: output, outputHash: duplicate.outputHash, duplicateOf: duplicate.id, message: currentRoot === previousRoot ? "This file was already converted in the selected folder." : "Existing FeedPak copied into the selected folder.", error: "" });
      return;
    }
    const staging = path.join(directory, "converted.feedpak");
    this._set(job, "converting", { message: "Converting with FeedForge.", progress: 0 });
    const conversion = await this._converter(job, [input, "-o", staging]);
    if (!conversion || conversion.code !== 0) throw new Error(`Conversion failed: ${text(conversion?.stderr || conversion?.stdout || "FeedForge could not convert this chart", 400)}`);
    check(job);
    const outputStat = await fsp.lstat(staging);
    if (outputStat.isSymbolicLink() || !outputStat.isFile() || !outputStat.size) throw new Error("FeedForge did not produce a package file.");
    this._set(job, "validating", { message: "Validating the converted FeedPak.", progress: 0 });
    const validation = jsonResult(await this._converter(job, ["--validate-feedpak", staging]), "FeedPak validation");
    const entry = validation.results?.[0];
    if (validation.ok !== true || validation.results?.length !== 1 || entry?.validation?.ok !== true || (entry.validation.errors || []).length || typeof entry.input_path !== "string" || path.resolve(entry.input_path) !== staging) {
      throw new Error("The converted FeedPak did not pass independent validation.");
    }
    job.outputHash = await hashFile(staging, job.controller.signal);
    const output = await this._publish(job, staging);
    // Publication is the commit point. Cancellation after this point leaves the completed file.
    this._deleteCache(job);
    this._set(job, "completed", { outputPath: output, progress: 100, message: "FeedPak ready.", error: "" });
  }

  async _duplicate(job) {
    const currentRoot = await fsp.realpath(this.outputDir);
    const candidates = [...this.jobs].reverse();
    candidates.sort((a, b) => {
      const selected = (item) => { try { return fs.realpathSync.native(path.dirname(item.outputPath)) === currentRoot ? 1 : 0; } catch { return 0; } };
      return selected(b) - selected(a);
    });
    for (const previous of candidates) {
      if (previous === job || previous.state !== "completed" || previous.sourceHash !== job.sourceHash || !previous.outputPath || !HASH.test(previous.outputHash || "")) continue;
      try {
        const stat = await fsp.lstat(previous.outputPath);
        if (!stat.isFile() || stat.isSymbolicLink()) continue;
        if (await hashFile(previous.outputPath, job.controller.signal) === previous.outputHash) return previous;
      } catch (error) { if (job.controller.signal.aborted) throw error; }
    }
    return null;
  }

  async _publish(job, staging) {
    check(job);
    await fsp.mkdir(this.outputDir, { recursive: true });
    const root = await fsp.realpath(this.outputDir);
    const temporary = path.join(root, `.feedforge-${job.id}-${crypto.randomUUID()}.part`);
    let ownsTemporary = false;
    try {
      // Cross-volume copies stage beside the final file. Only this exact owned .part is cleaned.
      const handle = await fsp.open(temporary, "wx", 0o600);
      ownsTemporary = true;
      await handle.close();
      await fsp.copyFile(staging, temporary);
      check(job);
      if (await hashFile(temporary, job.controller.signal) !== job.outputHash) throw new Error("The FeedPak changed while it was being saved. Retry the job.");
      const name = outputName(job.artist, job.title, job.chartId);
      const stem = name.slice(0, -".feedpak".length);
      for (let index = 0; index < 1000; index++) {
        check(job);
        const target = path.join(root, index ? `${stem} (${index + 1}).feedpak` : name);
        try {
          // link is atomic and fails on an existing destination, unlike rename or copyFile.
          // Keep creation and the commit marker in one turn so cancellation is unambiguous.
          this._writeReceipt(job, target);
          fs.linkSync(temporary, target);
          job.outputPath = target; job.committed = true;
          return target;
        } catch (error) {
          if (error.code === "EEXIST") continue;
          throw new Error(`Could not safely save the FeedPak in the selected folder: ${text(error.message, 250)}`);
        }
      }
      throw new Error("Too many files already use this song name. Choose another output folder.");
    } finally {
      if (ownsTemporary) {
        try { await fsp.unlink(temporary); } catch { /* Do not touch any other output file. */ }
      }
    }
  }

  async dispose() {
    this.disposed = true;
    await Promise.all(this.jobs.filter((job) => ACTIVE.has(job.state)).map((job) => this.cancel(job.id)));
    if (this.draining) await this.draining;
  }
}

module.exports = { SongJobs };

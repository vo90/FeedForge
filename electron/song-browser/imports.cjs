"use strict";

const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const { sanitizeChart, normalizePreferences, partsOf, normalized, cleanText } = require("./batch.cjs");
const { arrangementPart, validateRequirements } = require("./file-selection.cjs");

const HASH = /^[a-f0-9]{64}$/;
const MAX_IMPORTS = 20000;
const MAX_BYTES = 32 * 1024 * 1024;

function clone(value) { return JSON.parse(JSON.stringify(value)); }
function writeAtomic(filename, data) {
  const encoded = JSON.stringify(data);
  if (Buffer.byteLength(encoded) > MAX_BYTES) throw new Error("The imported-song index is too large.");
  const temporary = `${filename}.${crypto.randomUUID()}.tmp`;
  let descriptor;
  try {
    descriptor = fs.openSync(temporary, "wx", 0o600);
    fs.writeFileSync(descriptor, encoded); fs.fsyncSync(descriptor);
    fs.closeSync(descriptor); descriptor = undefined;
    fs.renameSync(temporary, filename);
  } finally {
    if (descriptor !== undefined) fs.closeSync(descriptor);
    if (fs.existsSync(temporary)) fs.unlinkSync(temporary);
  }
}

function coverageOf(value) {
  const source = value?.arrangements ?? value?.parts ?? value ?? [];
  const arrangements = Array.isArray(source) ? source : [];
  const coverage = { arrangements: arrangements.slice(0, 100).flatMap((item) => {
    const source = item && typeof item === "object" ? item : { id: String(item), type: String(item) };
    const part = arrangementPart(source) !== "unknown" ? arrangementPart(source) : partsOf([source])[0];
    if (!part) return [];
    const result = {};
    for (const key of ["id", "name", "path", "type", "tuning_name"]) if (source[key]) result[key] = cleanText(source[key], 160);
    if (!result.id && !result.name && !result.path) result.id = part;
    if (!result.type) result.type = part;
    if (Array.isArray(source.tuning)) {
      if (source.tuning.length >= 4 && source.tuning.length <= 8 && source.tuning.every((note) => Number.isInteger(note) && note >= -24 && note <= 24)) result.tuning = [...source.tuning];
    } else if (typeof source.tuning === "string") result.tuning = cleanText(source.tuning, 160);
    const stringCount = source.string_count ?? source.stringCount;
    if (Number.isInteger(stringCount) && stringCount >= 4 && stringCount <= 8) result.string_count = stringCount;
    return [result];
  }) };
  const platforms = value?.source_platforms ?? value?.platforms;
  if (Array.isArray(platforms)) coverage.source_platforms = [...new Set(platforms.filter((platform) => ["pc", "mac"].includes(platform)))];
  const backingTrack = value?.backing_track ?? value?.backingTrack;
  if (["full", "no-guitar", "no-bass"].includes(backingTrack)) coverage.backing_track = backingTrack;
  return coverage;
}

function sanitizeRecord(input) {
  if (!input || typeof input !== "object" || Array.isArray(input)) throw new Error("Invalid imported-song record.");
  if (input.state !== undefined && input.state !== "completed") throw new Error("Only successfully completed songs belong in the import index.");
  const chart = sanitizeChart(input.chart || input);
  if (typeof input.outputPath !== "string" || !path.isAbsolute(input.outputPath) || !/\.feedpak$/i.test(input.outputPath)
    || input.outputPath.includes("\0") || !HASH.test(input.outputHash || "")) throw new Error("A completed import needs its FeedPak path and SHA-256 hash.");
  const record = {
    id: cleanText(input.id || input.jobId || crypto.randomUUID(), 100), chartId: chart.id, chart,
    outputPath: path.resolve(input.outputPath), outputHash: input.outputHash,
    sourceHash: HASH.test(input.sourceHash || "") ? input.sourceHash : "",
    selection: normalizePreferences(input.selection || input.preferences || {}),
    coverage: coverageOf(input.coverage),
    completedAt: Number.isFinite(input.completedAt) ? input.completedAt : Number.isFinite(input.updatedAt) ? input.updatedAt : Date.now(),
  };
  if (typeof input.batchId === "string" && /^[a-f0-9-]{36}$/.test(input.batchId)) record.batchId = input.batchId;
  if (record.batchId && input.itemId === `${record.batchId}:${chart.id}`) record.itemId = input.itemId;
  return record;
}

function sameDirectory(a, b) {
  try { return fs.realpathSync.native(a) === fs.realpathSync.native(b); }
  catch { return path.resolve(a) === path.resolve(b); }
}

function covers(record, preferences) {
  const requested = normalizePreferences(preferences);
  if (requested.platform !== "any" && record.selection.platform !== requested.platform) return false;
  return validateRequirements(record.coverage, { parts: requested.requiredParts, tuning: requested.tuning || null,
    platform: requested.platform, allowMacFallback: requested.allowMacFallback, backingTrack: requested.backingTrack }).ok;
}

class ImportIndex {
  constructor({ root } = {}) {
    if (!root) throw new TypeError("ImportIndex needs a storage root.");
    fs.mkdirSync(path.resolve(root), { recursive: true });
    this.root = fs.realpathSync.native(path.resolve(root));
    this.filename = path.join(this.root, "imports.json");
    this.records = [];
    if (fs.existsSync(this.filename)) {
      const stat = fs.lstatSync(this.filename);
      if (!stat.isFile() || stat.isSymbolicLink() || stat.size > MAX_BYTES) throw new Error("The imported-song index is not a supported local file.");
      let data;
      try { data = JSON.parse(fs.readFileSync(this.filename, "utf8")); }
      catch { throw new Error("The imported-song index could not be read. Keep it for recovery."); }
      if (data?.version !== 1 || !Array.isArray(data.records) || data.records.length > MAX_IMPORTS) throw new Error("The imported-song index has an unsupported format.");
      this.records = data.records.map(sanitizeRecord);
      if (new Set(this.records.map((item) => item.id)).size !== this.records.length) throw new Error("The imported-song index contains duplicate record IDs.");
    }
  }

  snapshot() { return clone(this.records); }

  // Called only by the trusted completion/receipt path. Expensive file hashing happens on reuse, not on every UI snapshot.
  record(input) {
    const record = sanitizeRecord(input);
    const next = [...this.records];
    const existing = next.findIndex((item) => item.id === record.id);
    if (existing >= 0) {
      // Repeated completion events are common while unrelated jobs update the UI.
      if (input.completedAt === undefined && input.updatedAt === undefined) record.completedAt = next[existing].completedAt;
      if (JSON.stringify(next[existing]) === JSON.stringify(record)) return clone(next[existing]);
      next[existing] = record;
    }
    else {
      if (next.length >= MAX_IMPORTS) throw new Error("The imported-song index is full. Existing duplicate records were preserved.");
      next.push(record);
    }
    writeAtomic(this.filename, { version: 1, records: next });
    this.records = next;
    return clone(record);
  }

  async verify(input) {
    if (!input || !HASH.test(input.outputHash || "") || typeof input.outputPath !== "string" || !path.isAbsolute(input.outputPath)) return false;
    try {
      const before = await fs.promises.lstat(input.outputPath);
      if (!before.isFile() || before.isSymbolicLink()) return false;
      const digest = crypto.createHash("sha256");
      for await (const chunk of fs.createReadStream(input.outputPath)) digest.update(chunk);
      const after = await fs.promises.lstat(input.outputPath);
      return after.isFile() && !after.isSymbolicLink() && before.size === after.size && before.mtimeMs === after.mtimeMs
        && digest.digest("hex") === input.outputHash;
    } catch { return false; }
  }

  async findByAttempt(batchId, itemId) {
    const candidates = this.records.filter((item) => item.batchId === batchId && item.itemId === itemId).sort((a, b) => b.completedAt - a.completedAt);
    for (const record of candidates) if (await this.verify(record)) return clone(record);
    return null;
  }

  async find(input, { outputDir, preferences = {}, sourceHash } = {}) {
    const chart = sanitizeChart(input);
    const exactSource = HASH.test(sourceHash || "");
    const candidates = this.records.filter((record) => {
      if (record.chartId !== chart.id || (outputDir && !sameDirectory(path.dirname(record.outputPath), outputDir)) || !covers(record, preferences)) return false;
      if (exactSource) return record.sourceHash === sourceHash;
      // Artist/title and chart ID alone are not proof of an unchanged chart. Compare every available revision marker.
      if (!chart.version && !chart.updated) return false;
      if (chart.version && record.chart.version !== chart.version) return false;
      if (chart.updated && record.chart.updated !== chart.updated) return false;
      return normalized(record.chart.artist) === normalized(chart.artist) && normalized(record.chart.title) === normalized(chart.title);
    }).sort((a, b) => b.completedAt - a.completedAt);
    for (const record of candidates) if (await this.verify(record)) return clone(record);
    return null;
  }
}

module.exports = { ImportIndex, coverageOf, MAX_IMPORTS };

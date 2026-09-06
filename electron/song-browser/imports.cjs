"use strict";

const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const { sanitizeChart, normalizePreferences, partsOf, cleanText } = require("./batch.cjs");
const { arrangementPart, normalizeRequirements } = require("./file-selection.cjs");
const { sanitizeChoice, sanitizeFileEvidence, normalizeRecipe, reuseDecision, decision, UUID } = require("./provenance.cjs");

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
    if (source.string_count_evidence === "explicit") result.string_count_evidence = "explicit";
    if (["guitar", "bass"].includes(source.instrument_family)) result.instrument_family = source.instrument_family;
    if (source.instrument_family_evidence === "explicit") result.instrument_family_evidence = "explicit";
    if (Number.isInteger(source.minimum_used_strings) && source.minimum_used_strings >= 0 && source.minimum_used_strings <= 8) result.minimum_used_strings = source.minimum_used_strings;
    return [result];
  }) };
  const platforms = value?.source_platforms ?? value?.platforms;
  if (Array.isArray(platforms)) coverage.source_platforms = [...new Set(platforms.filter((platform) => ["pc", "mac"].includes(platform)))];
  const backingTrack = value?.backing_track ?? value?.backingTrack;
  if (["full", "no-guitar", "no-bass"].includes(backingTrack)) coverage.backing_track = backingTrack;
  if (value?.backing_track_evidence === "explicit") coverage.backing_track_evidence = "explicit";
  return coverage;
}

function sanitizeRecord(input, { legacy = false } = {}) {
  if (!input || typeof input !== "object" || Array.isArray(input)) throw new Error("Invalid imported-song record.");
  if (input.state !== undefined && input.state !== "completed") throw new Error("Only successfully completed songs belong in the import index.");
  const chart = sanitizeChart(input.chart || input);
  if (typeof input.outputPath !== "string" || !path.isAbsolute(input.outputPath) || !/\.feedpak$/i.test(input.outputPath)
    || input.outputPath.includes("\0") || !HASH.test(input.outputHash || "")) throw new Error("A completed import needs its FeedPak path and SHA-256 hash.");
  const record = {
    id: cleanText(input.id || input.jobId || crypto.randomUUID(), 100), chartId: chart.id, chart,
    outputPath: path.resolve(input.outputPath), outputHash: input.outputHash,
    sourceHash: HASH.test(input.sourceHash || "") ? input.sourceHash : "",
    selection: { ...normalizePreferences(input.selection || input.preferences || {}),
      ...normalizeRequirements({ ...(input.selection || input.preferences || {}), parts: input.selection?.parts ?? input.selection?.requiredParts ?? input.preferences?.requiredParts ?? [] }) },
    coverage: coverageOf(input.coverage),
    intentId: !legacy && UUID.test(input.intentId || "") ? input.intentId : null,
    requestedChoice: legacy ? null : sanitizeChoice(input.requestedChoice ?? input.selection?.choice),
    resolvedFile: legacy ? null : sanitizeFileEvidence(input.resolvedFile),
    recipe: legacy ? null : normalizeRecipe(input.recipe),
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

function preserveLegacy(filename, original) {
  let backup = filename.replace(/\.json$/, ".v1.backup.json");
  if (fs.existsSync(backup)) {
    const stat = fs.lstatSync(backup);
    if (stat.isFile() && !stat.isSymbolicLink() && fs.readFileSync(backup).equals(original)) return backup;
    backup = filename.replace(/\.json$/, `.v1.backup-${crypto.randomUUID()}.json`);
  }
  const descriptor = fs.openSync(backup, "wx", 0o600);
  try { fs.writeFileSync(descriptor, original); fs.fsyncSync(descriptor); }
  finally { fs.closeSync(descriptor); }
  return backup;
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
      const original = fs.readFileSync(this.filename);
      try { data = JSON.parse(original.toString("utf8")); }
      catch { throw new Error("The imported-song index could not be read. Keep it for recovery."); }
      if (![1, 2].includes(data?.version) || !Array.isArray(data.records) || data.records.length > MAX_IMPORTS) throw new Error("The imported-song index has an unsupported format.");
      this.records = data.records.map((record) => sanitizeRecord(record, { legacy: data.version === 1 }));
      if (new Set(this.records.map((item) => item.id)).size !== this.records.length) throw new Error("The imported-song index contains duplicate record IDs.");
      if (data.version === 1) {
        this.migrationBackup = preserveLegacy(this.filename, original);
        writeAtomic(this.filename, { version: 2, records: this.records });
      }
    }
  }

  snapshot() { return clone(this.records); }

  // Called only by the trusted completion/receipt path. Expensive file hashing happens on reuse, not on every UI snapshot.
  record(input) {
    const record = sanitizeRecord(input);
    const next = [...this.records];
    const existing = next.findIndex((item) => item.id === record.id);
    if (existing >= 0) {
      // Repeated old history events must not erase evidence written by the new
      // completion path. Missing evidence is different from an explicit change.
      if (record.outputHash === next[existing].outputHash && record.sourceHash === next[existing].sourceHash) {
        for (const key of ["intentId", "requestedChoice", "resolvedFile", "recipe"]) {
          if (!record[key] && next[existing][key]) record[key] = clone(next[existing][key]);
        }
      }
      // Repeated completion events are common while unrelated jobs update the UI.
      if (input.completedAt === undefined && input.updatedAt === undefined) record.completedAt = next[existing].completedAt;
      if (JSON.stringify(next[existing]) === JSON.stringify(record)) return clone(next[existing]);
      next[existing] = record;
    }
    else {
      if (next.length >= MAX_IMPORTS) throw new Error("The imported-song index is full. Existing duplicate records were preserved.");
      next.push(record);
    }
    writeAtomic(this.filename, { version: 2, records: next });
    this.records = next;
    return clone(record);
  }

  async verify(input, { signal } = {}) {
    if (signal?.aborted) throw Object.assign(new Error("Import verification cancelled."), { code: "ABORT_ERR" });
    if (!input || !HASH.test(input.outputHash || "") || typeof input.outputPath !== "string" || !path.isAbsolute(input.outputPath)) return false;
    try {
      const before = await fs.promises.lstat(input.outputPath);
      if (!before.isFile() || before.isSymbolicLink() || !before.size || before.size > 2 * 1024 * 1024 * 1024) return false;
      const digest = crypto.createHash("sha256");
      for await (const chunk of fs.createReadStream(input.outputPath, { signal })) digest.update(chunk);
      const after = await fs.promises.lstat(input.outputPath);
      return after.isFile() && !after.isSymbolicLink() && before.size === after.size && before.mtimeMs === after.mtimeMs
        && digest.digest("hex") === input.outputHash;
    } catch (error) { if (signal?.aborted) throw Object.assign(new Error("Import verification cancelled."), { code: "ABORT_ERR" }); return false; }
  }

  async findByAttempt(batchId, itemId, { intentId, jobId, signal } = {}) {
    if (!UUID.test(batchId || "") || typeof itemId !== "string" || (intentId != null && !UUID.test(intentId))) return null;
    // Receipts recover an existing intent, regardless of today's converter.
    // A new intent must never recover a prior variant of the same batch item.
    const candidates = this.records.filter((item) => item.batchId === batchId && item.itemId === itemId
      && (intentId != null ? item.intentId === intentId : !item.intentId)
      && (jobId == null || item.id === jobId)).sort((a, b) => b.completedAt - a.completedAt);
    for (const record of candidates) if (await this.verify(record, { signal })) return clone(record);
    return null;
  }

  async assess(input, { outputDir, preferences = {}, requirements, requestedChoice, recipe, sourceHash, reviewAnother = false, signal, jobId } = {}) {
    if (signal?.aborted) throw Object.assign(new Error("Import verification cancelled."), { code: "ABORT_ERR" });
    const chart = sanitizeChart(input);
    const exactSource = HASH.test(sourceHash || "");
    const candidates = this.records.filter((record) => (jobId == null || record.id === jobId)
      && (record.chartId === chart.id || (exactSource && record.sourceHash === sourceHash))).sort((a, b) => b.completedAt - a.completedAt);
    const describe = (result, record) => ({ ...result,
      status: result.reusable ? "available" : ["different_file", "review_file", "different_requirements"].includes(result.code) ? "choose_file"
        : result.code.startsWith("unknown_") ? "insufficient_evidence" : "new_conversion",
      ...(record ? { record: clone(record) } : {}) });
    let fallback = decision("no_record");
    for (const record of candidates) {
      if (signal?.aborted) throw Object.assign(new Error("Import verification cancelled."), { code: "ABORT_ERR" });
      let result = reuseDecision(record, { chart, requirements: requirements ?? preferences,
        requestedChoice: requestedChoice ?? input.requestedChoice ?? preferences.choice, recipe: recipe ?? input.recipe,
        sourceHash, reviewAnother });
      if (result.reusable) {
        if (!await this.verify(record, { signal })) result = decision("missing_output");
        else if (outputDir && !sameDirectory(path.dirname(record.outputPath), outputDir)) result = decision("other_folder");
        else return describe(result, record);
      }
      // A latest candidate's reason is useful; a known missing output or another
      // folder is more actionable than unrelated older incompatible versions.
      if (fallback.code === "no_record" || ["missing_output", "other_folder"].includes(result.code)) fallback = result;
    }
    return describe(fallback);
  }

  async find(input, options = {}) { const result = await this.assess(input, options); return result.reusable ? result.record : null; }
}

module.exports = { ImportIndex, coverageOf, MAX_IMPORTS };

"use strict";

const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const { arrangementPart, tuningMatches } = require("./file-selection.cjs");
const { possibleDuplicates } = require('./duplicate-suggestions.cjs');
const { normalizeRecipe } = require('./provenance.cjs');

const MAX_BATCH_CHARTS = 5000;
const MAX_BATCHES = 25;
const MAX_TOTAL_CHARTS = 20000;
const MAX_STATE_BYTES = 32 * 1024 * 1024;
const PARTS = ["lead", "rhythm", "bass"];
const BATCH_STATES = new Set(["draft", "running", "paused", "completed", "cancelled"]);
const ITEM_STATES = new Set(["pending", "running", "completed", "skipped", "failed", "needs_attention", "interrupted", "cancelled"]);
const UUID = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/;

function cleanText(value, limit = 300) {
  return String(value ?? "").replace(/\b(?:https?|ftp|file|blob|data):[^\s<>"']+/gi, "[link]")
    .replace(/\bwww\.[^\s<>"']+/gi, "[link]").replace(/[\u0000-\u001f\u007f]/g, " ").trim().slice(0, limit);
}

function recordId(chart) {
  const id = String(chart?.chartId ?? chart?.cdlc_id ?? chart?.id ?? "").trim();
  if (!/^[1-9]\d{0,11}$/.test(id)) throw new Error("Choose a valid CustomsForge chart record.");
  return id;
}

// Deliberately preserve punctuation and qualifiers: live/remix/acoustic titles are separate groups.
function normalized(value) { return cleanText(value).normalize("NFKC").toLowerCase().replace(/\s+/g, " "); }
function songKey(chart) { return JSON.stringify([normalized(chart.artist), normalized(chart.title)]); }
function strings(value, limit = 30) {
  return [...new Set((Array.isArray(value) ? value : typeof value === "string" ? [value] : [])
    .slice(0, limit).map((item) => cleanText(item, 160)).filter(Boolean))];
}

function partsOf(value) {
  const values = Array.isArray(value) ? value : [value];
  const found = new Set();
  for (const item of values) {
    const identified = typeof item === "object" && item ? arrangementPart(item) : "unknown";
    const label = identified !== "unknown" ? identified : typeof item === "object" && item ? item.type ?? item.path ?? item.name : item;
    for (const token of String(label ?? "").toLowerCase().split(/[^a-z]+/)) {
      const part = ({ l: "lead", r: "rhythm", b: "bass" })[token] || token;
      if (PARTS.includes(part)) found.add(part);
    }
  }
  return PARTS.filter((part) => found.has(part));
}

function count(value) {
  const number = typeof value === "string" ? Number(value.replace(/,/g, "")) : value;
  return Number.isFinite(number) && number >= 0 ? Math.min(Math.floor(number), Number.MAX_SAFE_INTEGER) : null;
}

function sanitizeChart(input) {
  if (!input || typeof input !== "object" || Array.isArray(input)) throw new Error("Invalid chart metadata.");
  const chart = { id: recordId(input) };
  for (const key of ["artist", "title", "album", "creator", "version", "tuning", "added", "updated", "year", "duration"]) {
    chart[key] = cleanText(key === "updated" ? input.chartUpdated ?? input.updated : input[key], key === "title" || key === "artist" ? 300 : 160);
  }
  if (!chart.artist || !chart.title) throw new Error("A batch chart needs an artist and song title.");
  chart.parts = partsOf(input.partNames ?? input.parts ?? input.arrangements);
  chart.tunings = strings(input.tunings ?? (chart.tuning ? [chart.tuning] : []));
  chart.arrangements = (Array.isArray(input.arrangements) ? input.arrangements : []).slice(0, 30)
    .filter((item) => item && typeof item === "object" && partsOf([item]).length)
    .map((item) => ({ id: partsOf([item])[0], type: partsOf([item])[0],
      tuning: Array.isArray(item.tuning) && item.tuning.length >= 4 && item.tuning.length <= 8 && item.tuning.every((note) => Number.isInteger(note) && note >= -24 && note <= 24)
        ? [...item.tuning] : cleanText(item.tuning, 160), tuning_name: cleanText(item.tuning_name, 160),
      instrument_family: ['guitar', 'bass'].includes(item.instrument_family) ? item.instrument_family : null,
      string_count: Number.isInteger(item.string_count) && item.string_count >= 4 && item.string_count <= 8 ? item.string_count : null }));
  chart.host = ["google-drive", "dropbox", "mediafire", "onedrive", "mega", "pcloud"].includes(input.host) ? input.host : "unknown";
  chart.supported = input.supported === true;
  chart.downloads = count(input.downloads);
  chart.loves = count(input.loves);
  chart.reported = typeof input.reported === "boolean" ? input.reported : null;
  chart.abandoned = typeof input.abandoned === "boolean" ? input.abandoned : null;
  chart.tags = strings(input.tags);
  chart.requirements = strings(input.requirements);
  return chart;
}

function normalizePreferences(input = {}) {
  if (!input || typeof input !== "object" || Array.isArray(input)) throw new Error("Invalid batch preferences.");
  const rawTuning = input.tuning ?? input.requiredTuning;
  if (Array.isArray(rawTuning) && (rawTuning.length < 4 || rawTuning.length > 8 || rawTuning.some((note) => !Number.isInteger(note) || note < -24 || note > 24))) {
    throw new Error("Choose valid tuning offsets.");
  }
  return {
    requiredParts: partsOf(input.requiredParts ?? input.paths ?? input.parts),
    tuning: Array.isArray(rawTuning) ? [...rawTuning] : cleanText(rawTuning, 160),
    preferredCreators: strings(input.preferredCreators ?? input.creator),
    ranking: ["downloads", "updated", "none"].includes(input.ranking) ? input.ranking : "downloads",
    excludeReported: input.excludeReported === true,
    excludeAbandoned: input.excludeAbandoned === true,
    allowUnsupported: input.allowUnsupported === true,
    platform: ["mac", "any"].includes(input.platform) ? input.platform : "pc",
    backingTrack: ["full", "no-guitar", "no-bass", "any"].includes(input.backingTrack) ? input.backingTrack : "full",
    backingStrict: input.backingStrict === true,
    instrumentRequirements: require('./file-selection.cjs').normalizeRequirements({ instrumentRequirements: input.instrumentRequirements || [] }).instrumentRequirements || [],
    macFallback: input.macFallback === true || input.allowMacFallback === true,
    allowMacFallback: input.macFallback === true || input.allowMacFallback === true,
  };
}

function eligibleParts(chart, preferences) {
  const compatible = chart.parts.filter((part) => {
    const requirement = preferences.instrumentRequirements.find((entry) => entry.part === part && entry.strict !== false);
    if (!requirement) return true;
    // Unknown catalogue evidence stays reviewable, with mandatory inspection later.
    const arrangements = chart.arrangements.filter((entry) => entry.id === part);
    return !arrangements.length || arrangements.some((entry) => (!requirement.family || !entry.instrument_family || entry.instrument_family === requirement.family)
      && (!requirement.stringCount || !entry.string_count || entry.string_count === requirement.stringCount));
  });
  if (!preferences.tuning) return compatible;
  if (chart.arrangements.length) {
    return compatible.filter((part) => chart.arrangements.some((item) => item.type === part && tuningMatches(item, preferences.tuning)));
  }
  // A row advertising multiple tunings cannot establish which requested path uses which tuning.
  return chart.tunings.length === 1 && tuningMatches({ tuning: chart.tunings[0] }, preferences.tuning) ? compatible : [];
}

function optionFor(chart, preferences) {
  const reasons = [];
  const parts = eligibleParts(chart, preferences);
  if (!chart.supported && !preferences.allowUnsupported) reasons.push("Download host needs manual support.");
  if (preferences.excludeReported && chart.reported) reasons.push("Excluded because the chart is reported.");
  if (preferences.excludeAbandoned && chart.abandoned) reasons.push("Excluded because the chart is abandoned.");
  if (preferences.tuning && !parts.length) reasons.push("Required tuning is unavailable or cannot be established.");
  if (preferences.requiredParts.length && !parts.some((part) => preferences.requiredParts.includes(part))) {
    reasons.push("Does not contain a required arrangement.");
  }
  if (preferences.instrumentRequirements.some((requirement) => requirement.strict !== false && chart.parts.includes(requirement.part) && !parts.includes(requirement.part))) reasons.push('The reported instrument is incompatible with the required arrangement.');
  if (preferences.instrumentRequirements.some((requirement) => requirement.strict !== false && chart.parts.length && !chart.parts.includes(requirement.part) && requirement.part !== 'guitar')) reasons.push('The chart does not advertise an arrangement needed for the instrument requirement.');
  const verificationPending = preferences.instrumentRequirements.some((requirement) => requirement.strict !== false && !chart.arrangements.some((arrangement) => arrangement.id === requirement.part
    && (!requirement.family || arrangement.instrument_family === requirement.family) && (!requirement.stringCount || arrangement.string_count === requirement.stringCount)));
  return { id: chart.id, eligible: reasons.length === 0, parts, reasons, verificationPending };
}

function rankCharts(a, b, preferences) {
  const preferred = preferences.preferredCreators.map(normalized);
  const rank = (chart) => { const index = preferred.indexOf(normalized(chart.creator)); return index < 0 ? preferred.length : index; };
  const creator = rank(a) - rank(b);
  if (creator) return creator;
  if (preferences.ranking === "downloads" && (a.downloads ?? -1) !== (b.downloads ?? -1)) return (b.downloads ?? -1) - (a.downloads ?? -1);
  if (preferences.ranking === "updated") {
    const difference = (Date.parse(b.updated) || 0) - (Date.parse(a.updated) || 0);
    if (difference) return difference;
  }
  return Number(a.id) - Number(b.id);
}

function planBatch(inputs, inputPreferences = {}) {
  if (!Array.isArray(inputs) || inputs.length > MAX_BATCH_CHARTS) throw new Error(`A batch can contain at most ${MAX_BATCH_CHARTS} chart records.`);
  const preferences = normalizePreferences(inputPreferences);
  const unique = new Map();
  for (const input of inputs) {
    const chart = sanitizeChart(input);
    const previous = unique.get(chart.id);
    if (previous && JSON.stringify(previous) !== JSON.stringify(chart)) throw new Error("A chart changed while preparing the batch. Refresh the search before continuing.");
    unique.set(chart.id, chart);
  }
  const charts = [...unique.values()];
  const grouped = new Map();
  for (const chart of charts) {
    const key = songKey(chart);
    if (!grouped.has(key)) grouped.set(key, []);
    grouped.get(key).push(chart);
  }
  const groups = [];
  for (const [key, alternatives] of grouped) {
    const options = alternatives.map((chart) => optionFor(chart, preferences));
    const byId = new Map(options.map((option) => [option.id, option]));
    const eligible = alternatives.filter((chart) => byId.get(chart.id).eligible).sort((a, b) => rankCharts(a, b, preferences));
    const recommendedIds = [];
    let missing = [...preferences.requiredParts];
    const complete = eligible.find((chart) => missing.every((part) => byId.get(chart.id).parts.includes(part)));
    if (complete) { recommendedIds.push(complete.id); missing = []; }
    else {
      // At most three required paths. Prefer coverage before chart ranking so complementary charts survive.
      while (missing.length) {
        const candidates = eligible.filter((chart) => !recommendedIds.includes(chart.id)).map((chart) => ({ chart,
          covers: missing.filter((part) => byId.get(chart.id).parts.includes(part)).length }));
        candidates.sort((a, b) => b.covers - a.covers || rankCharts(a.chart, b.chart, preferences));
        if (!candidates[0]?.covers) break;
        const selected = candidates[0].chart;
        recommendedIds.push(selected.id);
        missing = missing.filter((part) => !byId.get(selected.id).parts.includes(part));
      }
    }
    const unresolved = !recommendedIds.length || missing.length > 0;
    const reasons = [];
    if (recommendedIds.length > 1) reasons.push("Multiple charts are needed to cover the requested arrangements.");
    if (missing.length) reasons.push(`Missing required arrangements: ${missing.join(", ")}.`);
    if (!eligible.length) reasons.push("No chart matches the download and arrangement preferences.");
    if (!unresolved && alternatives.length > 1) reasons.push("Recommendation follows required arrangements and tuning, then creator and ranking preferences.");
    groups.push({ key, artist: alternatives[0].artist, title: alternatives[0].title, options,
      recommendedIds: unresolved ? [] : recommendedIds, reasons, unresolved });
  }
  const duplicateReview = possibleDuplicates(groups);
  return { charts, preferences, groups, suggestions: duplicateReview.suggestions, suggestionsLimited: duplicateReview.limited, selectedIds: groups.flatMap((group) => group.recommendedIds),
    unresolvedCount: groups.filter((group) => group.unresolved).length };
}

function atomicWrite(filename, data) {
  const encoded = JSON.stringify(data);
  if (Buffer.byteLength(encoded) > MAX_STATE_BYTES) throw new Error("The saved batch record is too large.");
  const temporary = `${filename}.${crypto.randomUUID()}.tmp`;
  let descriptor;
  try {
    descriptor = fs.openSync(temporary, "wx", 0o600);
    fs.writeFileSync(descriptor, encoded);
    fs.fsyncSync(descriptor);
    fs.closeSync(descriptor); descriptor = undefined;
    fs.renameSync(temporary, filename);
  } finally {
    if (descriptor !== undefined) fs.closeSync(descriptor);
    if (fs.existsSync(temporary)) fs.unlinkSync(temporary);
  }
}

function copy(value) { return JSON.parse(JSON.stringify(value)); }
function outputDirectory(value) {
  if (typeof value !== "string" || !path.isAbsolute(value) || value.includes("\0")) throw new Error("Choose an absolute output folder for the batch.");
  return path.resolve(value);
}
function outcomeOf(input) {
  if (!input || typeof input !== "object") return { status: "failed", message: "The song operation returned no result." };
  const status = input.status ?? input.state;
  const result = { status: ["completed", "skipped", "failed", "needs_attention", "interrupted", "cancelled"].includes(status) ? status : "failed",
    message: cleanText(input.message || input.error, 500), sessionWide: input.sessionWide === true };
  if (result.status === 'skipped') result.skipKind = input.skipKind === 'user' ? 'user' : 'available';
  for (const key of ["sourceHash", "outputHash"]) if (/^[a-f0-9]{64}$/.test(input[key] || "")) result[key] = input[key];
  if (typeof input.outputPath === "string" && path.isAbsolute(input.outputPath) && /\.feedpak$/i.test(input.outputPath)) result.outputPath = path.resolve(input.outputPath);
  if (input.id !== undefined || input.jobId !== undefined) result.jobId = cleanText(input.jobId ?? input.id, 80);
  if (Array.isArray(input.candidates)) result.candidates = input.candidates.slice(0, 100).map((item) => ({
    ...(require('./file-selection.cjs').sanitizeFileCandidate?.(item) || {}),
    id: cleanText(item?.id, 160), label: cleanText(item?.label, 300),
    platform: ["pc", "mac", "unknown"].includes(item?.platform) ? item.platform : "unknown",
  })).filter((item) => item.id && item.label);
  return result;
}

function availabilityOf(value, charts) {
  const result = {};
  for (const chart of charts) {
    const source = value?.[chart.id];
    result[chart.id] = { status: cleanText(source?.status || 'insufficient_evidence', 60), reason: cleanText(source?.reason || 'Local availability will be checked before downloading.', 500) };
  }
  return result;
}

class BatchCoordinator {
  constructor({ root, execute, findCompleted = null, recipe = null, onChange = () => {}, now = Date.now } = {}) {
    if (!root || typeof execute !== "function") throw new TypeError("BatchCoordinator needs root and execute.");
    fs.mkdirSync(path.resolve(root), { recursive: true });
    this.root = fs.realpathSync.native(path.resolve(root));
    this.filename = path.join(this.root, "batches.json");
    this.execute = execute;
    this.findCompleted = findCompleted;
    this.recipe = normalizeRecipe(recipe);
    this.onChange = onChange;
    this.now = now;
    this.batches = [];
    this.worker = null;
    this.current = null;
    this.disposed = false;
    this.persistenceWarning = "";
    this._load();
  }

  _load() {
    if (!fs.existsSync(this.filename)) return;
    if (fs.lstatSync(this.filename).isSymbolicLink() || fs.statSync(this.filename).size > MAX_STATE_BYTES) throw new Error("The batch ledger is not a supported local file.");
    let data;
    try { data = JSON.parse(fs.readFileSync(this.filename, "utf8")); }
    catch { throw new Error("The batch ledger could not be read. Keep it for recovery."); }
    if (![1, 2].includes(data?.version) || !Array.isArray(data.batches) || data.batches.length > MAX_BATCHES) throw new Error("The batch ledger has an unsupported format. Keep the file and update FeedForge before resuming.");
    let total = 0;
    const ids = new Set();
    for (const saved of data.batches) {
      if (!saved || !/^[a-f0-9-]{36}$/.test(saved.id) || ids.has(saved.id) || !BATCH_STATES.has(saved.state)) throw new Error("The batch ledger contains an invalid batch.");
      ids.add(saved.id);
      const plan = planBatch(saved.charts, saved.preferences);
      if ((total += plan.charts.length) > MAX_TOTAL_CHARTS || !Array.isArray(saved.items) || saved.items.length > plan.charts.length) throw new Error("The batch ledger exceeds its record limit.");
      const selectedIds = this._validateSelection(plan, saved.selectedIds, { allowConflicts: true });
      const seenItems = new Set();
      const items = saved.items.map((item) => {
        if (!item || typeof item.id !== "string" || item.id !== `${saved.id}:${item.chartId}` || seenItems.has(item.chartId)
          || !selectedIds.includes(item.chartId) || !ITEM_STATES.has(item.state)) throw new Error("The batch ledger contains an invalid song item.");
        seenItems.add(item.chartId);
        if (data.version === 2 && !UUID.test(item.intentId || '')) throw new Error('The batch ledger contains an invalid request identity. Keep it for recovery.');
        return { id: item.id, chartId: item.chartId, state: item.state === "running" ? "interrupted" : item.state,
          intentId: UUID.test(item.intentId || '') ? item.intentId : crypto.randomUUID(),
          recipe: data.version === 1 ? null : normalizeRecipe(item.recipe || saved.recipe),
          legacyIntent: data.version === 1 || item.legacyIntent === true,
          reviewAnother: item.reviewAnother === true,
          skipRequested: item.skipRequested === true,
          selection: item.selection ? normalizePreferences(item.selection) : null,
          attempts: Number.isInteger(item.attempts) && item.attempts >= 0 ? Math.min(item.attempts, 100000) : 0,
          outcome: item.outcome ? outcomeOf(item.outcome) : null,
          choice: item.choice ? this._savedChoice(item.choiceCandidates, item.choice) : null,
          choiceCandidates: item.choice ? outcomeOf({ candidates: item.choiceCandidates }).candidates : undefined };
      });
      if (saved.state !== "draft" && items.length !== selectedIds.length) throw new Error("The batch ledger has incomplete selected items.");
      this.batches.push({ id: saved.id, state: saved.state === "running" ? "paused" : saved.state,
        ...plan, selectedIds, items, outputDir: outputDirectory(saved.outputDir), query: cleanText(saved.query, 4096),
        recipe: data.version === 1 ? null : normalizeRecipe(saved.recipe),
        availability: availabilityOf(saved.availability, plan.charts), forceReviewIds: (saved.forceReviewIds || []).filter((id) => plan.charts.some((chart) => chart.id === id)),
        manualOverrideIds: (saved.manualOverrideIds || []).filter((id) => plan.charts.some((chart) => chart.id === id)),
        dismissedSuggestions: (saved.dismissedSuggestions || []).filter((id) => plan.suggestions.some((suggestion) => suggestion.id === id)),
        reviewConflicts: this._selectionConflicts(plan, selectedIds),
        complete: saved.complete === true, createdAt: Number(saved.createdAt) || this.now(), updatedAt: Number(saved.updatedAt) || this.now(),
        pauseReason: saved.state === "running" ? "Restarted. Resume this batch when ready." : cleanText(saved.pauseReason, 500) });
    }
    // Loading never starts work. Record recovery before returning an actionable snapshot.
    if (data.version === 1 && !fs.existsSync(`${this.filename}.v1.backup`)) fs.copyFileSync(this.filename, `${this.filename}.v1.backup`, fs.constants.COPYFILE_EXCL);
    atomicWrite(this.filename, { version: 2, batches: this.batches });
  }

  _notify() { try { this.onChange(this.snapshot()); } catch { /* Views cannot interrupt persistence or jobs. */ } }
  _change(action) {
    if (this.disposed) throw new Error("The batch coordinator is closed.");
    const next = copy(this.batches);
    const result = action(next);
    atomicWrite(this.filename, { version: 2, batches: next });
    this.batches = next;
    this.persistenceWarning = "";
    this._notify();
    return result;
  }
  _batch(batches, id) { const batch = batches.find((item) => item.id === String(id)); if (!batch) throw new Error("This batch is unavailable."); return batch; }
  _selectionConflicts(plan, selectedIds) {
    const conflicts = [];
    const selected = new Set(selectedIds);
    for (const group of plan.groups) {
      const chosen = group.options.filter((option) => selected.has(option.id));
      if (chosen.some((option) => !option.eligible)) conflicts.push(`A selected chart for ${group.title} no longer matches the requirements.`);
      if (chosen.length && !plan.preferences.requiredParts.every((part) => chosen.some((option) => option.parts.includes(part)))) conflicts.push(`The selected charts for ${group.title} do not cover every required arrangement.`);
    }
    return conflicts;
  }
  _validateSelection(plan, selectedIds, { allowConflicts = false } = {}) {
    if (!Array.isArray(selectedIds) || selectedIds.length > plan.charts.length) throw new Error("Choose charts from this batch preview.");
    const selected = [...new Set(selectedIds.map(String))];
    const options = new Map(plan.groups.flatMap((group) => group.options.map((option) => [option.id, option])));
    for (const id of selected) if (!options.has(id) || (!allowConflicts && !options.get(id)?.eligible)) throw new Error("A selected chart does not match the batch preferences.");
    for (const group of plan.groups) {
      const chosen = group.options.filter((option) => selected.includes(option.id));
      if (!allowConflicts && chosen.length && !plan.preferences.requiredParts.every((part) => chosen.some((option) => option.parts.includes(part)))) {
        throw new Error(`The selected charts for ${group.title} do not cover every required arrangement.`);
      }
    }
    return selected;
  }
  _choiceFrom(outcome, choice) {
    const candidates = outcomeOf(outcome).candidates || [];
    const matches = candidates.filter((candidate) => choice?.id ? candidate.id === String(choice.id)
      : candidate.label === choice?.label && candidate.platform === choice?.platform);
    if (matches.length !== 1) throw new Error("Choose a file from this song's saved options.");
    const selected = copy(matches[0]);
    delete selected.id; // The durable descriptor is not a current-document chooser token.
    return selected;
  }
  _savedChoice(candidates, choice) {
    const descriptor = require('./file-selection.cjs').sanitizeFileCandidate(choice, { includeId: false });
    if (!descriptor || !outcomeOf({ candidates }).candidates?.some((candidate) => candidate.label === descriptor.label && candidate.platform === descriptor.platform)) throw new Error('The saved file choice is invalid. Keep the batch for recovery.');
    // Identical filenames may become ambiguous after restart. Keep the honest
    // descriptor; the owned browser will require a fresh document-bound choice.
    return descriptor;
  }

  snapshot() {
    return [...this.batches].reverse().map((batch) => ({ ...copy(batch),
      counts: batch.items.reduce((counts, item) => { counts[item.state] = (counts[item.state] || 0) + 1; if (item.state === 'skipped') { const key = item.outcome?.skipKind === 'user' ? 'userSkipped' : 'available'; counts[key] = (counts[key] || 0) + 1; } return counts; }, {}),
      plannedCounts: batch.selectedIds.reduce((counts, chartId) => { const available = batch.availability?.[chartId]?.status === 'available' && !batch.forceReviewIds?.includes(chartId); counts[available ? 'available' : 'downloads']++; return counts; }, { available: 0, downloads: 0 }),
      ...(this.persistenceWarning ? { warning: this.persistenceWarning } : {}) }));
  }
  get(id) { return this.snapshot().find((batch) => batch.id === String(id)) || null; }

  prepare({ charts, preferences, outputDir, query = "", complete = true, importDecisions = {}, recipe = this.recipe } = {}) {
    const plan = planBatch(charts, preferences);
    const batch = { id: crypto.randomUUID(), state: "draft", ...plan, items: [], outputDir: outputDirectory(outputDir),
      query: cleanText(query, 4096), complete: complete === true, createdAt: this.now(), updatedAt: this.now(), pauseReason: "",
      recipe: normalizeRecipe(recipe), availability: availabilityOf(importDecisions, plan.charts), forceReviewIds: [], manualOverrideIds: [], dismissedSuggestions: [], reviewConflicts: [] };
    this._change((batches) => {
      if (batches.length >= MAX_BATCHES || batches.reduce((sum, item) => sum + item.charts.length, plan.charts.length) > MAX_TOTAL_CHARTS) {
        throw new Error("The saved batch limit has been reached. Remove a finished batch before preparing another.");
      }
      batches.push(batch);
    });
    return this.get(batch.id);
  }

  choose(id, { selectedIds, forceReviewIds } = {}) {
    this._change((batches) => {
      const batch = this._batch(batches, id);
      if (batch.state !== "draft") throw new Error("Selections can only be changed in a batch preview.");
      const selected = this._validateSelection(batch, selectedIds);
      batch.manualOverrideIds = [...new Set([...(batch.manualOverrideIds || []), ...batch.charts.filter((chart) => batch.selectedIds.includes(chart.id) !== selected.includes(chart.id)).map((chart) => chart.id)])];
      batch.selectedIds = selected;
      if (forceReviewIds !== undefined) {
        if (!Array.isArray(forceReviewIds) || forceReviewIds.some((chartId) => !batch.charts.some((chart) => chart.id === chartId))) throw new Error('Choose a chart from this batch to review another file.');
        batch.forceReviewIds = [...new Set(forceReviewIds)];
      }
      batch.reviewConflicts = [];
      batch.updatedAt = this.now();
    });
    return this.get(id);
  }

  updatePreferences(id, { preferences, importDecisions } = {}) {
    this._change((batches) => {
      const batch = this._batch(batches, id);
      if (batch.state !== 'draft') throw new Error('Preferences are frozen after a batch starts.');
      const plan = planBatch(batch.charts, preferences);
      const manuallyChosen = new Set(batch.manualOverrideIds || []);
      const selectedIds = plan.charts.filter((chart) => manuallyChosen.has(chart.id) ? batch.selectedIds.includes(chart.id) : plan.selectedIds.includes(chart.id)).map((chart) => chart.id);
      Object.assign(batch, plan, { selectedIds, reviewConflicts: this._selectionConflicts(plan, selectedIds), updatedAt: this.now() });
      batch.availability = availabilityOf(importDecisions, plan.charts);
    });
    return this.get(id);
  }

  dismissSuggestion(id, suggestionId) {
    this._change((batches) => {
      const batch = this._batch(batches, id);
      if (batch.state !== 'draft' || !batch.suggestions.some((suggestion) => suggestion.id === suggestionId)) throw new Error('Choose a possible duplicate from this batch preview.');
      batch.dismissedSuggestions = [...new Set([...(batch.dismissedSuggestions || []), suggestionId])];
      batch.updatedAt = this.now();
    });
    return this.get(id);
  }

  async setRecipe(id, recipe) {
    recipe = normalizeRecipe(recipe);
    const before = this._batch(this.batches, id);
    if (JSON.stringify(before.recipe) === JSON.stringify(recipe)) return this.get(id);
    if (before.state === 'running' || this.current?.batchId === before.id) throw new Error('Pause the batch before accepting changed conversion settings.');
    const recovered = new Map();
    // Reconcile the old immutable request before authorizing a different recipe.
    // A publication receipt remains successful even after a converter upgrade.
    if (typeof this.findCompleted === 'function') for (const item of before.items) {
      if (['completed', 'skipped', 'cancelled'].includes(item.state)) continue;
      const chart = before.charts.find((entry) => entry.id === item.chartId);
      const selection = { ...copy(item.selection || before.preferences), ...(item.choice ? { choice: copy(item.choice) } : {}) };
      const outcome = await this.findCompleted({ batchId: before.id, itemId: item.id, intentId: item.intentId,
        legacyIntent: item.legacyIntent === true, jobId: item.outcome?.jobId, recipe: item.recipe || before.recipe, outputDir: before.outputDir, preferences: selection, selection,
        reviewAnother: item.reviewAnother === true, chart: { ...copy(chart), selection, intentId: item.intentId } });
      if (outcome && ['completed', 'skipped'].includes(outcome.status || outcome.state || 'completed')) recovered.set(item.id, outcomeOf({ ...outcome, status: outcome.status || outcome.state || 'completed' }));
    }
    try {
    this._change((batches) => {
      const batch = this._batch(batches, id);
      if (batch.state === 'running' || this.current?.batchId === batch.id || JSON.stringify(batch) !== JSON.stringify(before)) throw new Error('The batch changed while recovering published songs. Try Resume again.');
      batch.recipe = recipe ? copy(recipe) : null;
      for (const item of batch.items) if (recovered.has(item.id)) { const outcome = recovered.get(item.id); item.state = outcome.status; item.outcome = outcome; }
      else if (!['completed', 'skipped', 'cancelled'].includes(item.state)) { item.intentId = crypto.randomUUID(); item.legacyIntent = false; item.recipe = recipe ? copy(recipe) : null; }
      batch.availability = availabilityOf({}, batch.charts);
      batch.updatedAt = this.now();
    });
    } catch (error) {
      // Receipts have proved these files exist; a failed ledger write must not
      // hide them or permit additional external work in this process.
      if (recovered.size) {
        for (const item of before.items) if (recovered.has(item.id)) { item.state = recovered.get(item.id).status; item.outcome = recovered.get(item.id); }
        this.persistenceWarning = `Recovered output could not be saved in the batch. No more songs will start. ${cleanText(error.message, 300)}`;
        this._notify();
      }
      throw error;
    }
    return this.get(id);
  }

  setItemChoice(id, itemId, { choice } = {}) {
    this._change((batches) => {
      const batch = this._batch(batches, id);
      const item = batch.items.find((entry) => entry.id === String(itemId));
      if (!item || item.state !== "needs_attention" || ["running", "cancelled"].includes(batch.state)) {
        throw new Error("Pause this batch and choose a song that is waiting for a file choice.");
      }
      item.choice = this._choiceFrom(item.outcome, choice);
      item.choiceCandidates = copy(item.outcome.candidates);
      item.intentId = crypto.randomUUID(); item.legacyIntent = false; item.reviewAnother = true;
      batch.updatedAt = this.now();
    });
    return this.get(id);
  }

  start(id) {
    this._change((batches) => {
      const batch = this._batch(batches, id);
      if (batch.state !== "draft") throw new Error("This batch has already started. Use Resume for paused work.");
      if (!batch.complete) throw new Error("This search snapshot is incomplete. Prepare a complete search before starting the batch.");
      if (!batch.selectedIds.length) throw new Error("Select at least one matching chart before starting.");
      this._validateSelection(batch, batch.selectedIds);
      batch.items = batch.selectedIds.map((chartId) => ({ id: `${batch.id}:${chartId}`, chartId, intentId: crypto.randomUUID(), recipe: batch.recipe ? copy(batch.recipe) : null, reviewAnother: batch.forceReviewIds.includes(chartId), state: "pending", attempts: 0, outcome: null }));
      batch.state = "running"; batch.updatedAt = this.now(); batch.pauseReason = "";
    });
    this._schedule();
    return this.get(id);
  }

  pause(id) {
    this._change((batches) => {
      const batch = this._batch(batches, id);
      if (batch.state !== "running") return;
      batch.state = "paused"; batch.pauseReason = "Paused. The active song may finish."; batch.updatedAt = this.now();
    });
    return this.get(id);
  }

  resume(id, { retryFailed = false } = {}) {
    if (this.current?.batchId === String(id) && this.current.interactive) throw new Error("Finish or cancel the active song resolution before resuming the batch.");
    this._change((batches) => {
      const batch = this._batch(batches, id);
      if (batch.state !== "paused" && !(batch.state === "completed" && retryFailed)) throw new Error("Only a paused batch or failed completed items can be resumed.");
      delete batch.interactiveItemId;
      for (const item of batch.items) {
        if (["needs_attention", "interrupted"].includes(item.state) || (retryFailed && item.state === "failed")) item.state = "pending";
      }
      if (!batch.items.some((item) => ["pending", "running"].includes(item.state))) {
        batch.state = "completed";
      } else batch.state = "running";
      batch.pauseReason = ""; batch.updatedAt = this.now();
    });
    this._schedule();
    return this.get(id);
  }

  resumeItem(id, itemId) {
    if (this.current?.batchId === String(id)) throw new Error("Wait for the current song to finish before resolving another song in this batch.");
    this._change((batches) => {
      const batch = this._batch(batches, id);
      if (!["paused", "completed"].includes(batch.state) || batch.interactiveItemId) throw new Error("Pause the batch before resolving one song.");
      const item = batch.items.find((entry) => entry.id === String(itemId));
      if (!item || !["needs_attention", "failed", "interrupted"].includes(item.state)) throw new Error("Choose a song that needs attention or a retry.");
      item.state = "pending";
      batch.interactiveItemId = item.id;
      batch.state = "paused";
      batch.pauseReason = "Resolving one song. Other songs stay paused until you resume the batch.";
      batch.updatedAt = this.now();
    });
    this._schedule();
    return this.get(id);
  }

  async skipItem(id, itemId) {
    let active = null;
    this._change((batches) => {
      const batch = this._batch(batches, id);
      const item = batch.items.find((entry) => entry.id === String(itemId));
      if (!item || ['completed', 'skipped', 'cancelled'].includes(item.state)) throw new Error('Choose an unfinished song to skip.');
      if (item.state === 'running') {
        if (this.current?.itemId !== item.id) throw new Error('The active song is still being recovered.');
        active = this.current;
        item.skipRequested = true;
      } else {
        item.state = 'skipped'; item.outcome = { status: 'skipped', skipKind: 'user', message: 'Skipped by you.' };
      }
      if (batch.interactiveItemId === item.id) delete batch.interactiveItemId;
      if (['running', 'paused'].includes(batch.state) && !batch.items.some((entry) => ['pending', 'running'].includes(entry.state))) {
        const needsAttention = batch.items.some((entry) => entry.state === 'needs_attention');
        batch.state = needsAttention ? 'paused' : 'completed';
        batch.pauseReason = needsAttention ? 'Review or skip the songs that need attention.' : '';
      } else if (batch.state === 'paused' && !active) batch.pauseReason = 'Paused. Resume when ready to continue the remaining songs.';
      batch.updatedAt = this.now();
    });
    if (active) { active.skipRequested = true; active.controller.abort(); await active.done; }
    this._schedule();
    return this.get(id);
  }

  retryItem(id, itemId, { relaxRequirements = false } = {}) {
    this._change((batches) => {
      const batch = this._batch(batches, id);
      const item = batch.items.find((entry) => entry.id === String(itemId));
      if (!item || !['failed', 'needs_attention', 'interrupted', 'skipped', 'cancelled'].includes(item.state) || (item.state === 'skipped' && item.outcome?.skipKind !== 'user')) throw new Error('Choose a song that needs retrying or was skipped by you.');
      if (relaxRequirements) {
        item.selection = normalizePreferences({ ...batch.preferences, requiredParts: [], tuning: '', backingStrict: false, instrumentRequirements: [] });
        item.intentId = crypto.randomUUID(); item.legacyIntent = false;
      }
      if (relaxRequirements || JSON.stringify(item.recipe) !== JSON.stringify(batch.recipe)) {
        item.intentId = crypto.randomUUID(); item.legacyIntent = false;
        item.recipe = batch.recipe ? copy(batch.recipe) : null;
      }
      delete item.skipRequested;
      item.state = 'pending'; item.outcome = null;
      if (batch.state !== 'running') { batch.state = 'paused'; batch.pauseReason = 'The song is ready to retry. Resume when ready.'; }
      batch.updatedAt = this.now();
    });
    this._schedule();
    return this.get(id);
  }

  async cancel(id) {
    this._change((batches) => {
      const batch = this._batch(batches, id);
      if (["completed", "cancelled"].includes(batch.state)) return;
      batch.state = "cancelled"; batch.updatedAt = this.now(); delete batch.interactiveItemId;
      for (const item of batch.items) if (!["running", "completed", "skipped"].includes(item.state)) item.state = "cancelled";
    });
    if (this.current?.batchId === String(id)) {
      this.current.controller.abort();
      await this.current.done;
    }
    return this.get(id);
  }

  remove(id) {
    this._change((batches) => {
      const batch = this._batch(batches, id);
      if (batch.state === "running" || this.current?.batchId === String(id)) throw new Error("Pause or cancel this batch before removing it.");
      batches.splice(batches.indexOf(batch), 1);
    });
  }

  _schedule() {
    if (this.worker || this.disposed || this.closing || this.persistenceWarning) return;
    this.worker = Promise.resolve().then(() => this._drain()).finally(() => {
      this.worker = null;
      if (this._nextBatch()) this._schedule();
    });
    this.worker.catch(() => {});
  }

  _nextBatch() {
    return this.batches.find((batch) => batch.state === "paused" && batch.interactiveItemId
      && batch.items.some((item) => item.id === batch.interactiveItemId && item.state === "pending"))
      || this.batches.find((batch) => batch.state === "running" && batch.items.some((item) => item.state === "pending"));
  }

  async _drain() {
    while (!this.disposed && !this.persistenceWarning) {
      const candidate = this._nextBatch();
      if (!candidate) return;
      const item = candidate.items.find((entry) => entry.state === "pending" && (!candidate.interactiveItemId || entry.id === candidate.interactiveItemId));
      const interactive = candidate.interactiveItemId === item.id;
      const chart = candidate.charts.find((entry) => entry.id === item.chartId);
      const controller = new AbortController();
      let release;
      const current = { batchId: candidate.id, itemId: item.id, controller, interactive, skipRequested: item.skipRequested === true, done: new Promise((resolve) => { release = resolve; }) };
      this.current = current;
      let resolvedOutcome = null;
      try {
        this._change((batches) => {
          const batch = this._batch(batches, candidate.id);
          const selected = batch.items.find((entry) => entry.id === item.id);
          selected.state = "running"; selected.attempts++; batch.updatedAt = this.now();
          // Consume the one-attempt authorization before execution. Restart never opens an interactive host automatically.
          delete batch.interactiveItemId;
        });
        const selection = { ...copy(item.selection || candidate.preferences), ...(item.choice ? { choice: copy(item.choice) } : {}) };
        const context = { signal: controller.signal, batchId: candidate.id, itemId: item.id, outputDir: candidate.outputDir,
          intentId: item.intentId, legacyIntent: item.legacyIntent === true, recipe: item.recipe ? copy(item.recipe) : null, reviewAnother: item.reviewAnother === true,
          preferences: copy(item.selection || candidate.preferences), selection, chart: { ...copy(chart), selection, intentId: item.intentId }, interactive };
        let outcome;
        try {
          if (current.skipRequested) controller.abort();
          const recovered = typeof this.findCompleted === "function" ? await this.findCompleted(context) : null;
          if (recovered) outcome = outcomeOf({ ...recovered, status: recovered.status || "completed" });
          else if (controller.signal.aborted) outcome = { status: "cancelled", message: "Cancelled." };
          else outcome = outcomeOf(await this.execute({ ...copy(chart), selection, intentId: item.intentId, reviewAnother: item.reviewAnother === true }, context));
        } catch (error) {
          outcome = { status: controller.signal.aborted ? "cancelled" : "failed", message: cleanText(error?.message || error, 500) };
        }
        // Once publication succeeded, cancellation must preserve that successful result.
        if (controller.signal.aborted && !["completed", "skipped"].includes(outcome.status)) outcome.status = "cancelled";
        if (controller.signal.aborted && this.closing && outcome.status === "cancelled") outcome.status = "interrupted";
        if (current.skipRequested && !['completed', 'skipped'].includes(outcome.status)) outcome = { status: 'skipped', skipKind: 'user', message: 'Skipped by you.' };
        resolvedOutcome = outcome;
        this._change((batches) => {
          const batch = this._batch(batches, candidate.id);
          const selected = batch.items.find((entry) => entry.id === item.id);
          selected.state = outcome.status; selected.outcome = outcome; batch.updatedAt = this.now();
          if (interactive && batch.state !== "cancelled") {
            batch.state = "paused";
            batch.pauseReason = "The song resolution finished. Resume the batch to continue other songs.";
          }
          if (outcome.status === "needs_attention" && outcome.sessionWide) {
            for (const other of batches) if (other.state === "running") { other.state = "paused"; other.pauseReason = "CustomsForge needs attention. Resume after reconnecting."; }
          }
          if (batch.state === "running" && !batch.items.some((entry) => entry.state === "pending")) {
            batch.state = batch.items.some((entry) => entry.state === "needs_attention") ? "paused" : "completed";
            if (batch.state === "paused") batch.pauseReason = "Some songs need attention. Other available songs have finished.";
          }
        });
      } catch (error) {
        // No further transfers may start without a durable transition. Receipt/import recovery resolves published work on restart.
        this.persistenceWarning = `Batch progress could not be saved. No more songs will start. ${cleanText(error?.message || error, 300)}`;
        if (resolvedOutcome) {
          const selected = this.batches.find((batch) => batch.id === candidate.id)?.items.find((entry) => entry.id === item.id);
          if (selected) { selected.state = resolvedOutcome.status; selected.outcome = resolvedOutcome; }
        }
        for (const batch of this.batches) if (batch.state === "running") { batch.state = "paused"; batch.pauseReason = this.persistenceWarning; }
        this._notify();
      } finally {
        if (this.current === current) this.current = null;
        release();
      }
    }
  }

  async waitForIdle() { while (this.worker) await this.worker; }

  async dispose() {
    if (this.disposed) return;
    // Stop dispatch and persist explicit-resume semantics before interrupting the owned attempt.
    this.closing = true;
    try {
      this._change((batches) => {
        for (const batch of batches) {
          delete batch.interactiveItemId;
          if (batch.state === "running") { batch.state = "paused"; batch.pauseReason = "The app closed. Resume this batch when ready."; }
        }
      });
    } catch (error) {
      this.persistenceWarning = `Batch shutdown progress could not be saved. ${cleanText(error?.message || error, 300)}`;
      for (const batch of this.batches) if (batch.state === "running") batch.state = "paused";
    } finally {
      this.current?.controller.abort();
      await this.waitForIdle();
      this.disposed = true;
    }
  }
}

module.exports = { BatchCoordinator, planBatch, sanitizeChart, normalizePreferences, partsOf, songKey, normalized, cleanText,
  MAX_BATCH_CHARTS, MAX_BATCHES, MAX_TOTAL_CHARTS };

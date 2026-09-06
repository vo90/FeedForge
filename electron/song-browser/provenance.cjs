'use strict';

const crypto = require('node:crypto');
const { validateRequirements } = require('./file-selection.cjs');

const HASH = /^[a-f0-9]{64}$/;
const UUID = /^[a-f0-9]{8}-[a-f0-9]{4}-[1-5][a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/i;
const FORBIDDEN = /(?:\b(?:https?|ftp|file|blob|data):|\bwww\.|[\x00-\x1f\x7f])/i;
const BACKING = new Set(['full', 'no-guitar', 'no-bass']);
const EVIDENCE = new Set(['observed', 'explicit', 'filename_hint', 'user_selected', 'unknown']);
const text = (value, max = 160) => typeof value === 'string' && value.length <= max && !FORBIDDEN.test(value) ? value.trim() : '';
const normalized = (value) => text(value, 500).normalize('NFKC').toLowerCase().replace(/\s+/g, ' ');

function sanitizeChoice(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const label = text(value.label ?? value.filename, 240);
  if (!label || !/\.psarc$/i.test(label) || /[\\/]/.test(label)) return null;
  const inferred = /_p\.psarc$/i.test(label) ? 'pc' : /_m\.psarc$/i.test(label) ? 'mac' : 'unknown';
  const platform = ['pc', 'mac', 'unknown'].includes(value.platform) ? value.platform : inferred;
  if (inferred !== 'unknown' && platform !== inferred) return null;
  // A live chooser token belongs only to its current document, never this descriptor.
  return { label, platform };
}

function sanitizeFileEvidence(value) {
  const choice = sanitizeChoice(value);
  if (!choice) return null;
  const result = { filename: choice.label, platform: choice.platform };
  const size = value.sizeBytes ?? value.bytes;
  if (Number.isSafeInteger(size) && size > 0 && size <= 512 * 1024 * 1024) result.sizeBytes = size;
  for (const key of ['versionHint', 'editionHint']) {
    const hint = text(value[key]);
    if (hint) result[key] = hint;
  }
  if (BACKING.has(value.backingHint)) result.backingHint = value.backingHint;
  const evidence = {};
  const aliases = { filename: 'label', sizeBytes: 'size', versionHint: 'version', editionHint: 'edition', backingHint: 'backing' };
  for (const key of ['filename', 'platform', 'sizeBytes', 'versionHint', 'editionHint', 'backingHint']) {
    const source = value.evidence?.[key] ?? value.evidence?.[aliases[key]];
    if (Object.hasOwn(result, key)) evidence[key] = EVIDENCE.has(source) ? source : 'unknown';
  }
  result.evidence = evidence;
  return result;
}

function canonicalOptions(value, depth = 0, budget = { count: 0 }) {
  if (++budget.count > 150 || depth > 5) throw new Error('Conversion options exceed their supported bounds.');
  if (value === null || typeof value === 'boolean') return value;
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string') {
    if (!text(value, 240) && value !== '') throw new Error('Conversion options contain unsupported text.');
    if (/^(?:[a-z]:[\\/]|[\\/])|(?:^|\s)(?:--?(?:input|output)|(?:token|password|secret)=)/i.test(value)) throw new Error('Conversion options must not contain paths or credentials.');
    return value;
  }
  if (Array.isArray(value)) {
    if (value.length > 40) throw new Error('Too many conversion option values.');
    return value.map((entry) => canonicalOptions(entry, depth + 1, budget));
  }
  if (!value || typeof value !== 'object' || Object.prototype.toString.call(value) !== '[object Object]') throw new Error('Unsupported conversion options.');
  const result = {};
  for (const key of Object.keys(value).sort()) {
    if (!/^[a-zA-Z][a-zA-Z0-9_-]{0,63}$/.test(key) || /(?:url|path|token|password|secret|cookie|authorization)/i.test(key)) throw new Error('Conversion options contain a private or transient field.');
    Object.defineProperty(result, key, { value: canonicalOptions(value[key], depth + 1, budget), enumerable: true });
  }
  return result;
}

function normalizeRecipe(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const version = text(value.version, 100), build = text(value.build, 160);
  if (!version || !build || /[\\/]/.test(version + build)) return null;
  try {
    const options = canonicalOptions(value.options ?? {});
    const recipe = { version, build, options };
    if (Buffer.byteLength(JSON.stringify(recipe)) > 8192) return null;
    return { ...recipe, identity: crypto.createHash('sha256').update(JSON.stringify(recipe)).digest('hex') };
  } catch { return null; }
}

function recipesCompatible(first, second) {
  const a = normalizeRecipe(first), b = normalizeRecipe(second);
  return Boolean(a && b && a.identity === b.identity);
}

const reasons = Object.freeze({
  available: 'A verified FeedPak already satisfies this request.',
  no_record: 'This chart has no recorded completed import.',
  review_file: 'Review the current files before choosing which version to use.',
  different_file: 'The existing import used a different file version.',
  unknown_file: 'The previous file version could not be established.',
  unknown_recipe: 'The conversion version or settings could not be established.',
  different_recipe: 'The conversion version or settings have changed.',
  different_source: 'The selected source file has different contents.',
  different_chart: 'This output belongs to another chart.',
  unknown_revision: 'The chart revision could not be established.',
  different_revision: 'The chart revision has changed.',
  different_identity: 'The recorded song identity does not match this chart.',
  different_requirements: 'The existing import does not satisfy the requested arrangements or other requirements.',
  missing_output: 'The previous FeedPak is missing or has changed.',
  other_folder: 'The verified FeedPak is in another output folder.',
});
function decision(code) { return { reusable: code === 'available', code, reason: reasons[code] || reasons.no_record }; }

function reuseDecision(record, request = {}) {
  if (!record || typeof record !== 'object') return decision('no_record');
  const exactSource = HASH.test(request.sourceHash || '');
  if (!exactSource && request.reviewAnother) return decision('review_file');
  const recipe = normalizeRecipe(request.recipe), previousRecipe = normalizeRecipe(record.recipe);
  if (!recipe || !previousRecipe) return decision('unknown_recipe');
  if (recipe.identity !== previousRecipe.identity) return decision('different_recipe');
  const requirements = request.requirements ?? request.preferences ?? {};
  try {
    if (!validateRequirements(record.coverage || {}, { ...requirements, parts: requirements.parts ?? requirements.requiredParts ?? [] }).ok) return decision('different_requirements');
  } catch { return decision('different_requirements'); }
  if (exactSource) return decision(record.sourceHash === request.sourceHash ? 'available' : 'different_source');

  const chart = request.chart || {}, previous = record.chart || record;
  if (String(previous.chartId ?? previous.id) !== String(chart.chartId ?? chart.id)) return decision('different_chart');
  const version = text(chart.version), updated = text(chart.chartUpdated ?? chart.updated);
  if (!version && !updated) return decision('unknown_revision');
  if ((version && text(previous.version) !== version) || (updated && text(previous.chartUpdated ?? previous.updated) !== updated)) return decision('different_revision');
  if (!normalized(chart.artist) || !normalized(chart.title) || normalized(previous.artist) !== normalized(chart.artist) || normalized(previous.title) !== normalized(chart.title)) return decision('different_identity');
  const file = sanitizeFileEvidence(record.resolvedFile);
  if (!file || !['observed', 'explicit', 'user_selected'].includes(file.evidence.filename)) return decision('unknown_file');
  const requested = request.requestedChoice ?? requirements.choice;
  if (requested) {
    const choice = sanitizeChoice(requested);
    if (!choice) return decision('unknown_file');
    if (choice.label !== file.filename || choice.platform !== file.platform) return decision('different_file');
  }
  return decision('available');
}

module.exports = { sanitizeFileEvidence, sanitizeChoice, normalizeRecipe, recipesCompatible, reuseDecision, decision, UUID };

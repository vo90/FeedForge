'use strict';

const { ORIGIN, numeric, clean, failure } = require('./policy.cjs');
const { readJson, validateMeta } = require('./acquire.cjs');
const { POLICY, LABELS, classifyRevision, approvalFor, validRevisionSelection } = require('./revision-policy.cjs');

const MAX_HISTORY_BYTES = 2 * 1024 * 1024;
const MAX_REVISIONS = 10000;
const unverified = () => failure('revision_history_unverified', 'Songsterr’s revision history could not be verified. Please retry.');
const sameText = (a, b) => typeof a === 'string' && typeof b === 'string'
  && clean(a).normalize('NFKC').toLowerCase() === clean(b).normalize('NFKC').toLowerCase();
const sameSong = (row, descriptor) => numeric(row?.songId) === numeric(descriptor?.id)
  && sameText(row.title, descriptor.title) && sameText(row.artist, descriptor.artist);
const newest = (a, b) => Date.parse(b.createdAt) - Date.parse(a.createdAt) || Number(b.revisionId) - Number(a.revisionId);

function validateHistory(descriptor, history) {
  if (!Array.isArray(history) || !history.length || history.length > MAX_REVISIONS) throw unverified();
  const seen = new Set();
  for (const row of history) {
    const revision = numeric(row?.revisionId);
    if (!row || typeof row !== 'object' || Array.isArray(row) || numeric(row.songId) !== numeric(descriptor.id)
        || !revision || seen.has(revision)) throw unverified();
    seen.add(revision);
  }
}

function visibleMatches(visible, status) {
  if (!visible || visible.ambiguous === true) return false;
  const observed = visible.status ?? (visible.approved ? 'reviewed' : visible.excluded === false ? 'unreviewed' : 'unknown');
  if (status === 'rejected') return ['alternative', 'rejected'].includes(observed);
  if (status === 'unknown') return true; // Display as unavailable, never select.
  return status === observed;
}

function revisionCatalogue(descriptor, metadata, history, page) {
  const id = numeric(descriptor?.id), current = numeric(metadata?.revisionId);
  if (!id || !sameSong(metadata, descriptor) || !current || metadata.isPublished !== true
      || (metadata.isSongDeleted != null && metadata.isSongDeleted !== false)
      || page?.songId !== id || page.historyReady !== true || page.historyHasMore === true
      || !Array.isArray(page.revisionRows)) throw unverified();
  validateHistory(descriptor, history);
  if (!history.some(row => numeric(row.revisionId) === current)) throw unverified();
  const visibleRows = new Map();
  for (const row of page.revisionRows) {
    if (!row || !numeric(row.revisionId) || visibleRows.has(row.revisionId)) throw unverified();
    visibleRows.set(row.revisionId, row);
  }
  return history.map(row => {
    const revisionId = numeric(row.revisionId), state = classifyRevision(row), visible = visibleRows.get(revisionId);
    // Incomplete rendered history is not proof that there is no reviewed revision.
    // Deleted/rejected entries can lack a rendered tab link. They remain
    // unavailable, but cannot prevent importing a separately verified row.
    if (state.eligible && !visibleMatches(visible, state.status)) throw unverified();
    if (!sameSong(row, descriptor) || !numeric(row.author?.personId)
        || typeof row.createdAt !== 'string' || !Number.isFinite(Date.parse(row.createdAt))) {
      return { revisionId, status: 'unknown', eligible: false, automatic: false, label: LABELS.unknown,
        reason: 'The revision identity or creation date could not be verified.' };
    }
    let basis = state.status;
    if (basis === 'unreviewed' && row.moderationType === 'no' && row.author?.isModerator === true
        && revisionId === current && numeric(metadata.latestRevisionId) === current
        && metadata.author?.isModerator === true && numeric(metadata.author.personId) === numeric(row.author.personId)
        && !['isDeleted', 'isBlocked', 'isOnModeration'].some(key => metadata[key] != null && metadata[key] !== false)
        && visible.moderator === true) basis = 'moderator_default';
    return { revisionId, createdAt: row.createdAt, status: basis, label: LABELS[basis], eligible: state.eligible,
      automatic: state.automatic, ...(state.reason ? { reason: state.reason } : {}),
      evidence: { version: 2, policy: POLICY, songId: id, revisionId, basis, defaultRevisionId: current,
        createdAt: row.createdAt, authorId: numeric(row.author.personId), moderationType: row.moderationType ?? null,
        reviewConclusion: row.reviewed?.conclusion ?? null, isDeleted: row.isDeleted, isBlocked: row.isBlocked,
        isOnModeration: row.isOnModeration, ...(basis === 'moderator_default' ? { moderatorVerified: true } : {}) } };
  }).sort(newest);
}

function chooseRevision(catalogue, requestedRevisionId) {
  if (requestedRevisionId != null) {
    if (!numeric(requestedRevisionId)) throw failure('invalid_result', 'Choose a valid tab revision.');
    const selected = catalogue.find(row => row.revisionId === numeric(requestedRevisionId));
    if (!selected?.eligible) throw failure('revision_ineligible', selected?.reason || 'This revision is no longer available for import.');
    return selected;
  }
  const candidates = catalogue.filter(row => row.eligible && row.automatic);
  const preferred = candidates.filter(row => ['reviewed', 'moderator_default'].includes(row.status));
  const selected = (preferred.length ? preferred : candidates)[0];
  if (!selected) throw failure('no_eligible_revision', 'Songsterr has no verified importable revision for this song. Open revision choices for details.');
  return selected;
}

function selectRevision(descriptor, metadata, history, page, { requestedRevisionId } = {}) {
  const selected = chooseRevision(revisionCatalogue(descriptor, metadata, history, page), requestedRevisionId);
  return { revisionId: selected.revisionId, approval: approvalFor(selected.status), revisionEvidence: {
    ...selected.evidence, selection: requestedRevisionId == null ? 'automatic' : 'explicit', checkedAt: new Date().toISOString() } };
}

async function loadCatalogue(descriptor, page, { fetch, signal } = {}) {
  if (!numeric(descriptor?.id) || typeof fetch !== 'function') throw unverified();
  const budget = { remaining: MAX_HISTORY_BYTES };
  const metadata = await readJson(fetch, `${ORIGIN}/api/meta/${descriptor.id}`, signal, budget);
  const history = await readJson(fetch, `${ORIGIN}/api/meta/${descriptor.id}/revisions`, signal, budget, { array: true });
  return { metadata, history, catalogue: revisionCatalogue(descriptor, metadata, history, page) };
}

async function resolveRevision(descriptor, page, options = {}) {
  const { metadata, history } = await loadCatalogue(descriptor, page, options);
  return selectRevision(descriptor, metadata, history, page, options);
}

async function revalidateRevision(descriptor, { fetch, signal } = {}) {
  if (!validRevisionSelection(descriptor)) throw failure('revision_ineligible', 'The saved revision selection is invalid.');
  const budget = { remaining: MAX_HISTORY_BYTES };
  const history = await readJson(fetch, `${ORIGIN}/api/meta/${descriptor.id}/revisions`, signal, budget, { array: true });
  validateHistory(descriptor, history);
  const row = history.find(row => numeric(row.revisionId) === descriptor.revisionId), state = classifyRevision(row);
  const evidence = descriptor.revisionEvidence;
  if (!row || !sameSong(row, descriptor) || !state.eligible || (!state.automatic && evidence?.selection !== 'explicit')
      || (evidence?.version === 2 && (numeric(row.author?.personId) !== evidence.authorId || row.createdAt !== evidence.createdAt))) {
    throw failure('revision_ineligible', state.reason || 'The selected revision is no longer eligible. Start a new import to choose another revision.');
  }
  const metadata = await readJson(fetch, `${ORIGIN}/api/meta/${descriptor.id}/${descriptor.revisionId}`, signal, budget);
  validateMeta(metadata, descriptor);
  return { revisionId: descriptor.revisionId, status: state.status, checkedAt: new Date().toISOString() };
}

module.exports = { selectRevision, resolveRevision, revisionCatalogue, chooseRevision, loadCatalogue, revalidateRevision, MAX_HISTORY_BYTES, MAX_REVISIONS };

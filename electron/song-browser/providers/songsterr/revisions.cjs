'use strict';

const { ORIGIN, numeric, clean, failure } = require('./policy.cjs');
const { readJson } = require('./acquire.cjs');

// Songsterr's public revision-label renderer recognizes these completed
// review conclusions. A published/default revision alone is not approval:
// reputation-based pre-approval can still be awaiting a moderator's review.
const APPROVED = new Set(['approved', 'fair', 'average', 'good', 'excellent']);
const MAX_HISTORY_BYTES = 2 * 1024 * 1024;
const MAX_REVISIONS = 10000;
const unverified = () => failure('unapproved_revision', 'Songsterr’s revision approval could not be verified. No unreviewed revision was selected.');
const sameText = (a, b) => typeof a === 'string' && typeof b === 'string'
  && clean(a).normalize('NFKC').toLowerCase() === clean(b).normalize('NFKC').toLowerCase();
const clear = row => row.isDeleted === false && row.isBlocked === false && row.isOnModeration === false;

function selectRevision(descriptor, metadata, history, page) {
  const id = numeric(descriptor?.id), current = numeric(metadata?.revisionId);
  if (!id || numeric(metadata?.songId) !== id || !current || metadata.isPublished !== true
      || !sameText(metadata.title, descriptor.title) || !sameText(metadata.artist, descriptor.artist)
      || page?.songId !== id || page.historyReady !== true
      || !Array.isArray(history) || !history.length || history.length > MAX_REVISIONS
      || !Array.isArray(page.revisionRows)) throw unverified();
  const seen = new Set();
  const visibleRows = new Map();
  for (const row of page.revisionRows) {
    if (!row || typeof row !== 'object') throw unverified();
    const matches = visibleRows.get(row.revisionId) || [];
    matches.push(row); visibleRows.set(row.revisionId, matches);
  }
  const eligible = [];
  for (const row of history) {
    const revisionId = numeric(row?.revisionId);
    // Conflicting/foreign records make the response itself unreliable.
    if (!row || typeof row !== 'object' || Array.isArray(row) || numeric(row.songId) !== id
        || !revisionId || seen.has(revisionId)) throw unverified();
    seen.add(revisionId);
    const visible = visibleRows.get(revisionId) || [];
    if (visible.length !== 1 || visible[0].excluded !== false || !clear(row)
        || !sameText(row.title, descriptor.title) || !sameText(row.artist, descriptor.artist)) continue;
    const createdAt = typeof row.createdAt === 'string' ? Date.parse(row.createdAt) : NaN;
    if (!Number.isFinite(createdAt) || !numeric(row.author?.personId)) continue;
    const conclusion = row.reviewed?.conclusion;
    let basis;
    if (APPROVED.has(conclusion) && ['pre', 'post', 'no'].includes(row.moderationType)
        && visible[0].approved === true) basis = 'reviewed';
    else if (row.reviewed == null && row.moderationType === 'no' && row.author?.isModerator === true
        && revisionId === current && numeric(metadata.latestRevisionId) === current
        && metadata.author?.isModerator === true && numeric(metadata.author.personId) === numeric(row.author.personId)
        && !['isDeleted', 'isBlocked', 'isOnModeration', 'isSongDeleted'].some(key => metadata[key] != null && metadata[key] !== false)
        && visible[0].moderator === true) basis = 'moderator_default';
    if (!basis) continue;
    eligible.push({ revisionId, createdAt, evidence: { version: 1, policy: 'reviewed-or-current-moderator',
      songId: id, revisionId, basis, defaultRevisionId: current, authorId: numeric(row.author.personId),
      moderationType: row.moderationType, ...(basis === 'reviewed' ? { reviewConclusion: conclusion } : {}),
      isDeleted: false, isBlocked: false, isOnModeration: false } });
  }
  eligible.sort((a, b) => b.createdAt - a.createdAt);
  if (!eligible.length || (eligible.length > 1 && eligible[0].createdAt === eligible[1].createdAt)) throw unverified();
  return { revisionId: eligible[0].revisionId, approval: 'approved',
    revisionEvidence: { ...eligible[0].evidence, checkedAt: new Date().toISOString() } };
}

async function resolveRevision(descriptor, page, { fetch, signal } = {}) {
  if (!numeric(descriptor?.id) || typeof fetch !== 'function') throw unverified();
  const budget = { remaining: MAX_HISTORY_BYTES };
  // No allowOwnUnpublished flag, credentials, or fallback to cached/latest parts.
  const metadata = await readJson(fetch, `${ORIGIN}/api/meta/${descriptor.id}`, signal, budget);
  const history = await readJson(fetch, `${ORIGIN}/api/meta/${descriptor.id}/revisions`, signal, budget, { array: true });
  return selectRevision(descriptor, metadata, history, page);
}

module.exports = { selectRevision, resolveRevision, MAX_HISTORY_BYTES };

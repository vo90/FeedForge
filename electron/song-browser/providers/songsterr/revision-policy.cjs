'use strict';

const { numeric } = require('./policy.cjs');

const APPROVED = new Set(['approved', 'fair', 'average', 'good', 'excellent']);
const REJECTED = new Set(['rejected', '-', '🙅‍♂️']);
const POLICY = 'reviewed-first-with-unreviewed-fallback';
const MODES = new Set([null, 'no', 'pre', 'post']);
const LABELS = { reviewed: 'Approved', moderator_default: 'Moderator revision', unreviewed: 'Unreviewed',
  awaiting_review: 'Awaiting review', awaiting_moderation: 'Awaiting moderation', alternative: 'Alternative',
  rejected: 'Rejected', deleted: 'Deleted', unknown: 'Status could not be verified' };

// Review results, moderation workflow and publication are separate facts.
// isOnModeration is not rejection; isBlocked renders as Alternative even
// for a revision whose review explicitly says rejected.
function classifyRevision(row) {
  const deny = (status, reason) => ({ status, eligible: false, automatic: false, reason });
  if (row?.isDeleted === true) return deny('deleted', 'This revision was deleted.');
  if (REJECTED.has(row?.reviewed?.conclusion)) return deny('rejected', 'A moderator rejected this revision.');
  if (!row || ['isDeleted', 'isBlocked', 'isOnModeration'].some(key => typeof row[key] !== 'boolean')
      || !MODES.has(row.moderationType ?? null)
      || (row.reviewed != null && (!APPROVED.has(row.reviewed.conclusion) || typeof row.reviewed !== 'object'))) {
    return deny('unknown', 'The revision has missing or unfamiliar moderation information.');
  }
  const reviewed = APPROVED.has(row.reviewed?.conclusion);
  if (row.isBlocked) {
    if (row.isOnModeration || !reviewed) return deny('alternative', 'This non-main revision has no verifiable positive review.');
    return { status: 'alternative', eligible: true, automatic: false, reason: 'Positively reviewed, but no longer a main revision. Choose explicitly to import.' };
  }
  if (row.isOnModeration) {
    if (reviewed || !['pre', 'post'].includes(row.moderationType)) return deny('unknown', 'The revision has conflicting review and moderation information.');
    return { status: row.moderationType === 'post' ? 'awaiting_review' : 'awaiting_moderation', eligible: true, automatic: true };
  }
  return { status: reviewed ? 'reviewed' : 'unreviewed', eligible: true, automatic: true };
}

function approvalFor(basis) {
  return ['reviewed', 'moderator_default'].includes(basis) ? 'approved' : basis === 'alternative' ? 'alternative' : 'unreviewed';
}

// Existing approved jobs may predate receipts. New statuses always require
// a complete v2 receipt; an unknown/future receipt never becomes legacy.
function validRevisionSelection(value) {
  const id = numeric(value?.songId ?? value?.id), revisionId = numeric(value?.revisionId), e = value?.revisionEvidence;
  if (!id || !revisionId) return false;
  if (e == null) return value.approval === 'approved';
  if (e.version === 1) return value.approval === 'approved' && e.policy === 'reviewed-or-current-moderator'
    && e.songId === id && e.revisionId === revisionId && ['reviewed', 'moderator_default'].includes(e.basis)
    && e.isDeleted === false && e.isBlocked === false && e.isOnModeration === false;
  if (e.version !== 2 || e.policy !== POLICY || e.songId !== id || e.revisionId !== revisionId
      || !numeric(e.authorId) || !numeric(e.defaultRevisionId) || !['automatic', 'explicit'].includes(e.selection)
      || ![e.createdAt, e.checkedAt].every(date => typeof date === 'string' && Number.isFinite(Date.parse(date)))
      || value.approval !== approvalFor(e.basis)) return false;
  const classification = classifyRevision({ ...e, reviewed: e.reviewConclusion == null ? null : { conclusion: e.reviewConclusion } });
  if (!classification.eligible || (!classification.automatic && e.selection !== 'explicit')) return false;
  if (e.basis === 'moderator_default') return classification.status === 'unreviewed' && e.moderationType === 'no'
    && e.defaultRevisionId === revisionId && e.moderatorVerified === true;
  return classification.status === e.basis;
}

function revisionLabel(value) {
  return LABELS[value?.revisionEvidence?.basis] || (value?.approval === 'approved' ? 'Approved' : LABELS.unknown);
}

module.exports = { APPROVED, POLICY, LABELS, classifyRevision, approvalFor, validRevisionSelection, revisionLabel };

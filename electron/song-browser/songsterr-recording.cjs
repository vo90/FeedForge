'use strict';
const { audioVideo, selectSynchronization } = require('./providers/songsterr/synchronization.cjs');
const videoId = value => typeof value === 'string' && /^[\w-]{11}$/.test(value);

function selectSiteAudio(job, audio, revisionId = job.metadata?.revisionId) {
  job.audio = audio;
  const video = audioVideo(audio);
  job.audioSelection = video && /^\d+$/.test(String(revisionId)) ? {
    version: 1, origin: 'songsterr', songId: job.chart.id, revisionId: String(revisionId), videoId: video.videoId,
  } : { version: 1, origin: 'unknown' };
}
function isSiteAudio(job) {
  const selection = job.audioSelection, video = audioVideo(job.audio);
  return Boolean(video && selection?.version === 1 && selection.origin === 'songsterr'
    && selection.songId === job.chart.id && selection.revisionId === String(job.metadata?.revisionId)
    && selection.videoId === video.videoId);
}
function validRecovery(value) {
  return value?.version === 1 && typeof value.pending === 'boolean' && value.requireMap === true
    && Array.isArray(value.failedVideoIds) && value.failedVideoIds.length <= 3
    && value.failedVideoIds.every(videoId) && new Set(value.failedVideoIds).size === value.failedVideoIds.length;
}
function startRecovery(job, { explicit = false } = {}) {
  const failed = explicit ? [] : validRecovery(job.recordingRecovery) ? job.recordingRecovery.failedVideoIds : [];
  const id = audioVideo(job.audio)?.videoId;
  job.recordingRecovery = { version: 1, pending: true, requireMap: true,
    failedVideoIds: [...new Set([...failed, ...(id ? [id] : [])])].slice(-3) };
}
function verifiedRecoveryMap(sync, identity) {
  if (sync?.selectionEvidence?.version !== 1 || sync.selectionEvidence.policy !== 'primary-before-alternative') return false;
  const selected = selectSynchronization(sync.selectionEvidence.response, identity);
  return selected.status === 'done' && ['version', 'source', 'songId', 'revisionId', 'videoId', 'status', 'feature', 'mapHash']
    .every(key => selected[key] === sync[key]) && Array.isArray(sync.points)
    && selected.points.length === sync.points.length && selected.points.every((p, i) => p === sync.points[i]);
}
module.exports = { selectSiteAudio, isSiteAudio, validRecovery, startRecovery, verifiedRecoveryMap };

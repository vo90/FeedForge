'use strict';

const fs = require('node:fs/promises');
const path = require('node:path');
const zlib = require('node:zlib');
const { httpTransport, networkTransport } = require('../../songsterr-retry.cjs');
const { ORIGIN, MAX_SCORE_BYTES, MAX_TOTAL_BYTES, MAX_TRACKS, MAX_MEASURES, failure, check, clean, numeric, sourceFilename, publicAudio, bounded } = require('./policy.cjs');

// Metadata and parts must use the same approved revision. Tests inject a
// transport; do not fall back to latest metadata or rotate hosts on failure.
function partUrl(songId, revisionId, image, index) {
  if (!numeric(songId) || !numeric(revisionId) || !Number.isInteger(index) || index < 0 || index >= MAX_TRACKS) throw failure('invalid_score', 'The score identity is invalid.');
  if (image == null || image === '') return `https://d3rrfvx08uyjp1.cloudfront.net/part/${revisionId}/${index}`;
  if (typeof image !== 'string' || !/^[a-zA-Z0-9_-]{1,160}$/.test(image)) throw failure('invalid_score', 'The score storage identifier is not supported.');
  const host = image.endsWith('-stage') ? 'd3d3l6a6rcgkaf' : 'dqsljvtekg760';
  return `https://${host}.cloudfront.net/${songId}/${revisionId}/${image}/${index}.json`;
}
function lyricsUrl(songId, revisionId, image) {
  const part = partUrl(songId, revisionId, image, 0);
  return image ? part.replace(/\/0\.json$/, '/lyrics.json') : part.replace(/\/part\/(\d+)\/0$/, '/lyrics/$1');
}
async function readJson(fetch, url, signal, budget, { array = false } = {}) {
  const operation = new URL(url).pathname.startsWith('/api/meta/') ? 'score_metadata' : 'score_part';
  check(signal);
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal?.addEventListener('abort', abort, { once: true });
  try {
    const request = async () => {
      const response = await fetch(url, { method: 'GET', credentials: 'omit', redirect: 'error', signal: controller.signal, headers: { Accept: 'application/json' } });
      if (response.status === 401) throw failure('needs_login', 'Songsterr requires sign-in to retrieve this score.');
      if (response.status === 403) throw failure('access_denied', 'Songsterr did not allow anonymous access to this score.');
      if (!response.ok) throw Object.assign(failure(response.status === 429 ? 'rate_limited' : response.status === 404 ? 'unavailable' : 'network_error', 'The Songsterr score could not be retrieved.'),
        { transport: httpTransport('score', operation, response.status, response.headers) });
      if (response.url && response.url !== url) throw failure('access_denied', 'The score request changed destination.');
      const length = Number(response.headers?.get?.('content-length'));
      if (length > MAX_SCORE_BYTES || length > budget.remaining) throw failure('score_too_large', 'This Songsterr score exceeds the supported size.');
      const chunks = []; let size = 0;
      if (!response.body || !response.body[Symbol.asyncIterator]) throw failure('invalid_response', 'The score response could not be read safely.');
      for await (const value of response.body) {
        check(signal); const chunk = Buffer.from(value); size += chunk.length;
        if (size > MAX_SCORE_BYTES || size > budget.remaining) { controller.abort(); throw failure('score_too_large', 'This Songsterr score exceeds the supported size.'); }
        chunks.push(chunk);
      }
      let bytes = Buffer.concat(chunks);
      if (bytes[0] === 0x1f && bytes[1] === 0x8b) {
        try { bytes = zlib.gunzipSync(bytes, { maxOutputLength: Math.min(MAX_SCORE_BYTES, budget.remaining) }); }
        catch { throw failure('invalid_score', 'The compressed score is invalid or too large.'); }
      }
      if (!bytes.length || bytes.length > MAX_SCORE_BYTES || bytes.length > budget.remaining) throw failure('score_too_large', 'This Songsterr score is empty or too large.');
      budget.remaining -= bytes.length;
      let value;
      try { value = JSON.parse(bytes.toString('utf8')); } catch { throw failure('invalid_score', 'Songsterr returned something other than a complete score.'); }
      if (!value || typeof value !== 'object' || Array.isArray(value) !== array) throw failure('invalid_score', 'The score response has an unsupported format.');
      return value;
    };
    const pending = request();
    // The fetch callback can abort synchronously before bounded attaches its
    // handlers. Observe the pending rejection even in that cancellation race.
    pending.catch(() => {});
    return await bounded(pending, signal, 30000, abort);
  } catch (error) {
    check(signal);
    const detail = error.transport || networkTransport(error, 'score', operation);
    if (error.source === 'songsterr') throw Object.assign(error, { transport: detail });
    throw Object.assign(failure('network_error', 'The public score request failed. This does not establish that sign-in is required.'), { transport: detail });
  } finally { signal?.removeEventListener('abort', abort); controller.abort(); }
}
function validateMeta(meta, descriptor) {
  if (String(meta.revisionId) !== descriptor.revisionId) throw failure('revision_unavailable',
    `Songsterr returned revision ${numeric(meta.revisionId) || 'unknown'} instead of approved revision ${descriptor.revisionId}. Retry the import; signing in is not required by this response.`);
  if (meta.songId != null && String(meta.songId) !== descriptor.id) throw failure('invalid_score', 'The score belongs to a different song.');
  if (!Array.isArray(meta.tracks) || !meta.tracks.length || meta.tracks.length > MAX_TRACKS || meta.tracks.some((track) => !track || typeof track !== 'object' || Array.isArray(track))) throw failure('invalid_score', 'Songsterr did not provide a complete track inventory.');
  for (const key of ['title', 'artist']) {
    if (meta[key] != null && clean(meta[key]).normalize('NFKC').toLowerCase() !== descriptor[key].normalize('NFKC').toLowerCase()) throw failure('invalid_score', 'The score metadata does not match the selected song.');
  }
}
function validatePart(part, track, expectedMeasures) {
  if (!Array.isArray(part.measures) || !part.measures.length || part.measures.length > MAX_MEASURES || part.measures.some((measure) => !measure || typeof measure !== 'object' || Array.isArray(measure))) throw failure('incomplete_score', 'A Songsterr track has missing or invalid measures.');
  if (expectedMeasures && part.measures.length !== expectedMeasures) throw failure('incomplete_score', 'Songsterr tracks have inconsistent measure counts.');
  if (Array.isArray(track.tuning) && (track.tuning.length < 1 || track.tuning.length > 16 || !track.tuning.every(Number.isFinite))) throw failure('invalid_score', 'A track tuning is invalid.');
  return part.measures.length;
}
async function acquireAnonymous(descriptor, { fetch, directory, signal, onProgress = () => {} } = {}) {
  if (typeof fetch !== 'function') throw failure('unavailable', 'Anonymous score retrieval is unavailable.');
  if (!numeric(descriptor?.id) || !numeric(descriptor?.revisionId) || descriptor.approval !== 'approved') throw failure('unapproved_revision', 'A verified approved revision is required.');
  const budget = { remaining: MAX_TOTAL_BYTES };
  const meta = await readJson(fetch, `${ORIGIN}/api/meta/${descriptor.id}/${descriptor.revisionId}`, signal, budget);
  validateMeta(meta, descriptor);
  const parts = []; let measures = Number.isInteger(meta.measureCount) && meta.measureCount > 0 ? meta.measureCount : null;
  for (let index = 0; index < meta.tracks.length; index++) {
    const part = await readJson(fetch, partUrl(descriptor.id, descriptor.revisionId, meta.image, index), signal, budget);
    measures = validatePart(part, meta.tracks[index], measures);
    parts.push(part); onProgress({ phase: 'score', completed: index + 1, total: meta.tracks.length });
  }
  check(signal);
  const payload = { format: 'songsterr', songId: descriptor.id, revisionId: descriptor.revisionId,
    title: descriptor.title, artist: descriptor.artist, tracks: meta.tracks, parts };
  if (meta.lyrics && parts.some((part) => part.withLyrics && !part.newLyrics?.[0]?.text)) {
    try {
      payload.legacyLyrics = await readJson(fetch, lyricsUrl(descriptor.id, descriptor.revisionId, meta.image), signal, budget, { array: true });
      payload.lyricsAcquisition = { status: 'captured', songId: descriptor.id, revisionId: descriptor.revisionId };
    } catch (error) {
      check(signal);
      // Optional lyrics never discard a complete instrument score. Record the
      // outcome so an unavailable legacy sidecar is not reported as no lyrics.
      payload.lyricsAcquisition = { status: 'unavailable', code: error.code || 'network_error',
        songId: descriptor.id, revisionId: descriptor.revisionId };
    }
  }
  const destination = path.join(directory, 'score.songsterr.json');
  const stat = await fs.lstat(directory);
  if (!stat.isDirectory() || stat.isSymbolicLink()) throw failure('invalid_directory', 'Use a regular job folder for this import.');
  await fs.writeFile(destination, JSON.stringify(payload), { flag: 'wx', mode: 0o600 });
  const audio = descriptor.audio || publicAudio(meta.youtubeUrl) || publicAudio(meta.videoUrl);
  return { path: destination, format: 'songsterr', metadata: { songId: descriptor.id, revisionId: descriptor.revisionId,
    title: descriptor.title, artist: descriptor.artist, approval: 'approved', tracks: meta.tracks.length, measures,
    ...(descriptor.revisionEvidence ? { revisionEvidence: descriptor.revisionEvidence } : {}) },
    ...(audio ? { audio } : {}), sourceFilename: sourceFilename(descriptor.artist, descriptor.title) };
}

module.exports = { acquireAnonymous, partUrl, lyricsUrl, validateMeta, validatePart, readJson };

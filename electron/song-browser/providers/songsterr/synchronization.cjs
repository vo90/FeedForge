'use strict';

const crypto = require('node:crypto');
const { httpTransport, networkTransport } = require('../../songsterr-retry.cjs');
const { ORIGIN, MAX_MEASURES, numeric, publicAudio, check, failure, bounded } = require('./policy.cjs');

const MAX_SYNC_BYTES = 2 * 1024 * 1024;
const MAX_ENTRIES = 256;
const SOURCE = 'songsterr-video-points';
const MESSAGES = {
  unsupported_audio: 'This recording has no verified Songsterr video identity; checking its timing locally.',
  identity_mismatch: 'Songsterr timing data did not match the selected song, revision and recording; checking locally.',
  unavailable: 'Songsterr has no usable timing map for this recording; checking locally.',
  restricted: 'Songsterr timing data was unavailable anonymously; checking the recording locally.',
  rate_limited: 'Songsterr timing requests are temporarily limited; checking the recording locally.',
  timeout: 'Songsterr timing data did not arrive in time; checking the recording locally.',
  network_error: 'Songsterr timing data could not be retrieved; checking the recording locally.',
  invalid_response: 'Songsterr timing data had an unsupported response; checking the recording locally.',
  too_large: 'Songsterr timing data exceeded the supported size; checking the recording locally.',
  invalid_points: 'Songsterr timing points were invalid or incomplete; checking the recording locally.',
  ambiguous: 'Songsterr provided conflicting timing maps for this recording; checking locally.',
};

function unavailableSynchronization(identity, reasonCode = 'unavailable') {
  const code = Object.hasOwn(MESSAGES, reasonCode) ? reasonCode : 'unavailable';
  return { version: 1, source: SOURCE, songId: numeric(identity?.songId), revisionId: numeric(identity?.revisionId),
    ...(typeof identity?.videoId === 'string' && /^[\w-]{11}$/.test(identity.videoId) ? { videoId: identity.videoId } : {}),
    status: 'unavailable', reasonCode: code, message: MESSAGES[code] };
}

function audioVideo(audio) {
  if (audio?.kind !== 'url') return null;
  const video = publicAudio(audio.url);
  if (!video || (audio.videoId != null && audio.videoId !== video.videoId)) return null;
  return video;
}

function selectSynchronization(entries, identity) {
  if (!numeric(identity?.songId) || !numeric(identity?.revisionId) || !/^[\w-]{11}$/.test(identity?.videoId || '')) {
    return unavailableSynchronization(identity, 'identity_mismatch');
  }
  if (!Array.isArray(entries) || entries.length > MAX_ENTRIES || entries.some((entry) => !entry || typeof entry !== 'object' || Array.isArray(entry))) {
    return unavailableSynchronization(identity, 'invalid_response');
  }
  const videoEntries = entries.filter((entry) => entry.videoId === identity.videoId);
  const matching = videoEntries.filter((entry) => numeric(entry.songId) === identity.songId && numeric(entry.revisionId) === identity.revisionId);
  if (videoEntries.length && !matching.length) return unavailableSynchronization(identity, 'identity_mismatch');
  // The public player's ordinary video/load and switchType(main) paths select
  // the primary (!feature) entry. An alternative may share its video ID while
  // retaining an older/different map. It must not compete with that primary.
  // Never replace an unusable primary with an alternative implicitly, or pick
  // between conflicting primaries by response order. Keep exact-video binding.
  const primary = matching.filter((entry) => entry.feature === null);
  const candidates = primary.length ? primary : matching.filter((entry) => entry.feature === 'alternative');
  const eligible = candidates.filter((entry) => entry.status === 'done'
    && entry.tracks == null && entry.trackHashes == null && (entry.problematic == null || entry.problematic === false));
  if (primary.length && eligible.length !== primary.length) return unavailableSynchronization(identity);
  if (!eligible.length) return unavailableSynchronization(identity);
  let points;
  for (const entry of eligible) {
    const values = entry.points;
    if (!Array.isArray(values) || values.length < 2 || values.length > MAX_MEASURES + 1
        || values.some((point, index) => typeof point !== 'number' || !Number.isFinite(point) || Math.abs(point) > 86400
          || (index > 0 && (point < values[index - 1] || point === values[index - 1] && point !== values[0])))) return unavailableSynchronization(identity, 'invalid_points');
    if (values.at(-1) === values[0]) return unavailableSynchronization(identity, 'invalid_points');
    if (points && (points.length !== values.length || points.some((point, index) => point !== values[index]))) {
      return unavailableSynchronization(identity, 'ambiguous');
    }
    points = values;
  }
  // Exact duplicate maps are equivalent; selecting a different array order
  // must not alter recording identity or the semantic conversion recipe.
  const result = { version: 1, source: SOURCE, songId: identity.songId, revisionId: identity.revisionId,
    videoId: identity.videoId, status: 'done', feature: eligible.some((entry) => entry.feature === null) ? null : 'alternative', points: [...points] };
  result.mapHash = crypto.createHash('sha256').update(JSON.stringify(result)).digest('hex');
  return result;
}

async function retrieveSynchronization(identity, { fetch, signal, timeoutMs = 15000 } = {}) {
  check(signal);
  if (!numeric(identity?.songId) || !numeric(identity?.revisionId) || !/^[\w-]{11}$/.test(identity?.videoId || '')) {
    return unavailableSynchronization(identity, 'identity_mismatch');
  }
  if (typeof fetch !== 'function') return unavailableSynchronization(identity);
  const url = `${ORIGIN}/api/video-points/${identity.songId}/${identity.revisionId}/list`;
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal?.addEventListener('abort', abort, { once: true });
  try {
    const read = async () => {
      check(signal);
      const response = await fetch(url, { method: 'GET', credentials: 'omit', redirect: 'error', signal: controller.signal,
        headers: { Accept: 'application/json' } });
      check(signal);
      if (response.url && response.url !== url) throw failure('restricted', 'Timing response changed destination.');
      if (response.status === 401 || response.status === 403) throw failure('restricted', 'Timing data is unavailable anonymously.');
      if (!response.ok) throw Object.assign(failure(response.status === 429 ? 'rate_limited' : response.status === 404 ? 'unavailable' : 'network_error', 'Timing request failed.'),
        { transport: httpTransport('synchronization', 'timing_map', response.status, response.headers) });
      if (Number(response.headers?.get?.('content-length')) > MAX_SYNC_BYTES) throw failure('too_large', 'Timing response is too large.');
      const type = response.headers?.get?.('content-type') || '';
      if (type && !/^(?:application\/(?:[a-z0-9.+-]+\+)?json|text\/plain)(?:;|$)/i.test(type)) throw failure('invalid_response', 'Unsupported timing response.');
      if (!response.body?.[Symbol.asyncIterator]) throw failure('invalid_response', 'Timing response cannot be streamed.');
      const chunks = []; let length = 0;
      for await (const chunk of response.body) {
        check(signal);
        const bytes = Buffer.from(chunk); length += bytes.length;
        if (length > MAX_SYNC_BYTES) throw failure('too_large', 'Timing response is too large.');
        chunks.push(bytes);
      }
      check(signal);
      let entries;
      try { entries = JSON.parse(Buffer.concat(chunks).toString('utf8')); }
      catch { throw failure('invalid_response', 'Timing response is not JSON.'); }
      const selected = selectSynchronization(entries, identity);
      // Preserve the bounded anonymous response alongside the selected map so
      // a failed or completed import can reproduce the selection independently.
      // The Python reader hashes/uses only the selected musical map fields.
      return { ...selected, selectionEvidence: { version: 1, policy: 'primary-before-alternative', response: entries } };
    };
    // Attach timeout/cancellation handlers before invoking an injected or
    // synchronous transport, which can itself abort the caller immediately.
    return await bounded(Promise.resolve().then(read), signal, timeoutMs, abort);
  } catch (error) {
    check(signal);
    if (error.code === 'cancelled') throw error;
    const detail = error.transport || networkTransport(error, 'synchronization', 'timing_map');
    return { ...unavailableSynchronization(identity, error.source === 'songsterr' ? error.code : 'network_error'), ...(detail ? { transport: detail } : {}) };
  } finally { signal?.removeEventListener('abort', abort); controller.abort(); }
}

function synchronizationSummary(value) {
  if (!value) return undefined;
  return { source: SOURCE, status: value.status === 'done' ? 'done' : 'unavailable',
    ...(typeof value.videoId === 'string' && /^[\w-]{11}$/.test(value.videoId) ? { videoId: value.videoId } : {}),
    ...(value.status === 'done' ? { pointCount: value.points?.length || 0, mapHash: value.mapHash } : {
      reasonCode: Object.hasOwn(MESSAGES, value.reasonCode) ? value.reasonCode : 'unavailable',
      message: MESSAGES[Object.hasOwn(MESSAGES, value.reasonCode) ? value.reasonCode : 'unavailable'],
    }) };
}

module.exports = { retrieveSynchronization, selectSynchronization, unavailableSynchronization, audioVideo, synchronizationSummary, MAX_SYNC_BYTES, MAX_ENTRIES };

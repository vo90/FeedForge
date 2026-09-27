'use strict';
// Transport facts only. A generic needs_audio/timeout error is not retry evidence.
const STATUSES = new Set([408, 429, 500, 502, 503, 504]);
const REASONS = new Set(['timeout', 'connection_reset', 'temporary_dns', 'interrupted_transfer', 'http', 'media_url_expired', 'player_timeout']);
const OPERATIONS = { score: ['score_metadata', 'score_part', 'page_load', 'revision_history'],
  audio: ['audio_download', 'audio_probe'], synchronization: ['timing_map'] };
const SERVICES = new Set(['songsterr', 'youtube', 'audio_host']);
function transport(value) {
  if (value?.version !== 1 || !OPERATIONS[value.phase]?.includes(value.operation)
      || !SERVICES.has(value.service) || !REASONS.has(value.reason)) return null;
  if (value.reason === 'http' && !STATUSES.has(value.status)) return null;
  if (value.reason === 'media_url_expired' && !(value.service === 'youtube' && value.operation === 'audio_download' && value.status === 403)) return null;
  if (value.reason === 'player_timeout' && value.operation !== 'audio_probe') return null;
  return { version: 1, phase: value.phase, operation: value.operation, service: value.service, reason: value.reason,
    ...(Number.isInteger(value.status) ? { status: value.status } : {}),
    ...(Number.isSafeInteger(value.retryAfterAt) && value.retryAfterAt > 0 ? { retryAfterAt: value.retryAfterAt } : {}) };
}
function retryAfter(value, now = Date.now()) {
  if (typeof value !== 'string' || value.length > 128 || !value.trim()) return undefined;
  if (!/^\d+$/.test(value.trim()) && !/^(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun), \d{2} [A-Z][a-z]{2} \d{4} \d{2}:\d{2}:\d{2} GMT$/.test(value.trim())) return undefined;
  const delay = /^\d+$/.test(value.trim()) ? Number(value) * 1000 + now : Date.parse(value);
  return Number.isSafeInteger(delay) && delay > now ? delay : undefined;
}
function httpTransport(phase, operation, status, headers, now) {
  return transport({ version: 1, phase, operation, service: 'songsterr', reason: 'http', status,
    retryAfterAt: retryAfter(headers?.get?.('retry-after'), now) });
}
function networkTransport(error, phase, operation) {
  const code = error?.cause?.code || error?.code;
  const reason = ({ timeout: 'timeout', ETIMEDOUT: 'timeout', UND_ERR_CONNECT_TIMEOUT: 'timeout',
    UND_ERR_HEADERS_TIMEOUT: 'timeout', UND_ERR_BODY_TIMEOUT: 'timeout', ECONNRESET: 'connection_reset',
    UND_ERR_SOCKET: 'interrupted_transfer', EAI_AGAIN: 'temporary_dns' })[code]
    || (/^net::ERR_(?:TIMED_OUT|CONNECTION_TIMED_OUT)$/.test(error?.message || '') ? 'timeout' : null)
    || (/^net::ERR_CONNECTION_RESET$/.test(error?.message || '') ? 'connection_reset' : null);
  return reason ? transport({ version: 1, phase, operation, service: 'songsterr', reason }) : null;
}
function retryPlan(failure, used, now, random = Math.random) {
  const fact = transport(failure);
  if (!fact || !Number.isInteger(used) || used < 0 || used >= 2) return null;
  const base = [5000, 20000][used], jitter = Math.floor(Math.max(0, Math.min(1, random())) * 1000);
  const at = Math.max(now + base + jitter, fact.retryAfterAt || 0);
  return { at, parked: at - now > 300000, transport: fact };
}
module.exports = { transport, retryAfter, httpTransport, networkTransport, retryPlan };

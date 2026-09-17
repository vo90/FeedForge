'use strict';

const ORIGIN = 'https://www.songsterr.com';
const MAX_SCORE_BYTES = 32 * 1024 * 1024;
const MAX_TOTAL_BYTES = 128 * 1024 * 1024;
const MAX_TRACKS = 64;
const MAX_MEASURES = 20000;
const SCORE_HOSTS = new Set(['dqsljvtekg760.cloudfront.net', 'd3d3l6a6rcgkaf.cloudfront.net', 'd3rrfvx08uyjp1.cloudfront.net']);

function failure(code, message) {
  return Object.assign(new Error(message), { code, source: 'songsterr' });
}
function check(signal) {
  if (signal?.aborted) throw Object.assign(failure('cancelled', 'Songsterr import cancelled.'), { name: 'AbortError' });
}
function clean(value, limit = 300) {
  return String(value ?? '').replace(/[\u0000-\u001f\u007f]/g, ' ').replace(/\s+/g, ' ').trim().slice(0, limit);
}
function numeric(value) { const text = String(value ?? ''); return /^[1-9]\d{0,11}$/.test(text) ? text : null; }
function sourceFilename(artist, title) {
  return `${clean(artist)} - ${clean(title)}`.replace(/[<>:"/\\|?*]/g, '_').slice(0, 180).replace(/[. ]+$/, '') + '.gp';
}
function songUrl(value) {
  try {
    const url = new URL(value);
    if (url.origin !== ORIGIN || url.username || url.password) return null;
    const match = /^\/a\/wsa\/[^/]+-s([1-9]\d{0,11})(?:t\d+)?(?:\/r([1-9]\d{0,11}))?$/.exec(url.pathname);
    if (!match) return null;
    url.search = ''; url.hash = '';
    const base = url.pathname.replace(/t\d+(?=\/|$)/, '').replace(/\/r\d+$/, '');
    return { id: match[1], revisionId: match[2] || null, url: ORIGIN + base, pinnedUrl: url.href };
  } catch { return null; }
}
function allowedNavigation(value) {
  try { const url = new URL(value); return url.origin === ORIGIN && !url.username && !url.password; }
  catch { return false; }
}
function allowedDownload(value) {
  if (String(value).startsWith('blob:' + ORIGIN + '/')) return true;
  try { const url = new URL(value); return url.protocol === 'https:' && !url.username && !url.password && (url.origin === ORIGIN || SCORE_HOSTS.has(url.hostname)); }
  catch { return false; }
}
function publicAudio(value) {
  try {
    const url = new URL(value);
    if (url.protocol !== 'https:' || url.username || url.password) return null;
    let id;
    if (['www.youtube.com', 'youtube.com', 'www.youtube-nocookie.com'].includes(url.hostname)) {
      id = url.pathname === '/watch' ? url.searchParams.get('v') : /^\/(?:embed|shorts)\/([a-zA-Z0-9_-]{11})(?:\/|$)/.exec(url.pathname)?.[1];
    } else if (url.hostname === 'youtu.be') id = url.pathname.slice(1);
    if (!/^[a-zA-Z0-9_-]{11}$/.test(id || '')) return null;
    return { kind: 'url', url: `https://www.youtube.com/watch?v=${id}`, videoId: id };
  } catch { return null; }
}
function safeResult(value) {
  const parsed = songUrl(value?.url);
  const id = numeric(value?.id ?? value?.songId);
  const title = clean(value?.title), artist = clean(value?.artist);
  if (!parsed || parsed.id !== id || !title || !artist) throw failure('invalid_result', 'Search for this Songsterr song again.');
  return { id, source: 'songsterr', title, artist, url: parsed.url,
    ...(numeric(value?.revisionId) ? { revisionId: numeric(value.revisionId) } : {}),
    ...(Array.isArray(value?.parts) ? { parts: value.parts.filter((part) => ['guitar', 'lead', 'rhythm', 'bass'].includes(part)) } : {}),
    supported: true };
}
function delay(ms, signal) {
  check(signal);
  return new Promise((resolve, reject) => {
    const abort = () => { clearTimeout(timer); signal?.removeEventListener('abort', abort); try { check(signal); } catch (error) { reject(error); } };
    const timer = setTimeout(() => { signal?.removeEventListener('abort', abort); resolve(); }, ms);
    signal?.addEventListener('abort', abort, { once: true });
  });
}
async function bounded(promise, signal, timeoutMs = 30000, onAbort = () => {}) {
  check(signal);
  let timer, abort;
  try {
    return await Promise.race([Promise.resolve(promise), new Promise((_, reject) => {
      abort = () => { try { onAbort(); } catch {} reject(Object.assign(failure('cancelled', 'Songsterr import cancelled.'), { name: 'AbortError' })); };
      timer = setTimeout(() => { try { onAbort(); } catch {} reject(failure('timeout', 'Songsterr did not respond in time. Please retry.')); }, timeoutMs);
      signal?.addEventListener('abort', abort, { once: true });
    })]);
  } finally { clearTimeout(timer); signal?.removeEventListener('abort', abort); }
}

module.exports = { ORIGIN, MAX_SCORE_BYTES, MAX_TOTAL_BYTES, MAX_TRACKS, MAX_MEASURES, SCORE_HOSTS,
  failure, check, clean, numeric, sourceFilename, songUrl, safeResult, allowedNavigation, allowedDownload, publicAudio, delay, bounded };

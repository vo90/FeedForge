'use strict';

// Diagnostics deliberately accepts no free text. Do not add URLs, paths,
// chart metadata, exception messages, DOM content, headers or account state.
const CODES = new Set(['search_started', 'search_finished', 'search_failed',
  'download_started', 'download_finished', 'download_failed', 'download_cancelled',
  'browser_attention', 'navigation_blocked', 'job_state', 'job_failed',
  'retry_requested', 'cache_cleared', 'output_checked', 'output_failed',
  'library_refresh_started', 'library_refresh_finished', 'library_refresh_failed',
  'report_exported', 'settings_failed']);
const STAGES = new Set(['search', 'browser', 'download', 'queued', 'downloading',
  'needs_attention', 'inspecting', 'converting', 'validating', 'publishing',
  'completed', 'failed', 'cancelled', 'recovery', 'output', 'library', 'diagnostics']);
const HOSTS = new Set(['customsforge', 'dropbox', 'google-drive', 'mediafire',
  'mega', 'onedrive', 'pcloud', 'unknown']);
const OUTCOMES = new Set(['started', 'ready', 'connected', 'success', 'failed',
  'cancelled', 'login_required', 'challenge', 'needs_attention', 'layout_changed',
  'unsupported', 'interrupted', 'timeout', 'duplicate', 'unavailable']);
const MAX_DURATION = 24 * 60 * 60 * 1000;

function createDiagnostics({ appVersion = 'unknown', maxEvents = 200 } = {}) {
  const version = typeof appVersion === 'string' && /^\d+\.\d+\.\d+(?:[-+][a-zA-Z0-9.-]{1,40})?$/.test(appVersion)
    ? appVersion : 'unknown';
  const limit = Number.isInteger(maxEvents) ? Math.min(500, Math.max(1, maxEvents)) : 200;
  const started = Date.now();
  const events = [];
  let omittedEvents = 0;
  return Object.freeze({
    record(event) {
      if (!event || typeof event !== 'object' || !CODES.has(event.code)) return false;
      const entry = { elapsedMs: Math.max(0, Math.min(MAX_DURATION, Date.now() - started)), code: event.code };
      if (STAGES.has(event.stage)) entry.stage = event.stage;
      if (HOSTS.has(event.host)) entry.host = event.host;
      if (OUTCOMES.has(event.outcome)) entry.outcome = event.outcome;
      if (typeof event.durationMs === 'number' && Number.isFinite(event.durationMs) && event.durationMs >= 0) {
        entry.durationMs = Math.min(MAX_DURATION, Math.round(event.durationMs));
      }
      events.push(entry);
      if (events.length > limit) { events.shift(); omittedEvents++; }
      return true;
    },
    report() {
      return { schemaVersion: 1, adapterVersion: 4, appVersion: version, omittedEvents, events: events.map((event) => ({ ...event })) };
    }
  });
}

module.exports = { createDiagnostics };

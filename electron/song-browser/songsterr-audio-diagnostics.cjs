'use strict';

// Persist only fixed enums and timestamps. Browser text, error messages, URLs,
// account details and recording/song identities never belong in this summary.
const MAX_EVENTS = 16;
const MAX_TIMESTAMP = 8640000000000000;
const ABSENT_REASONS = [
  'original_unavailable', 'original_action_failed', 'original_selection_timeout',
  'full_mix_unavailable', 'full_mix_action_failed', 'full_mix_selection_timeout',
  'play_unavailable', 'play_action_failed', 'stale_mix_timeout', 'iframe_timeout',
  'unsupported_source_control', 'mix_menu_action_failed', 'mix_menu_timeout',
  'unverified_full_mix', 'mix_unavailable', 'ambiguous_iframes', 'primary_unverified', 'primary_page_changed',
].map(reason => 'audio_probe_' + reason);
const EVENT_CODES = new Set([
  ...ABSENT_REASONS,
  'audio_probe_started', 'audio_probe_found', 'audio_probe_play_requested', 'audio_probe_play', 'audio_probe_pause',
  'audio_probe_original_control', 'audio_probe_original_selected', 'audio_probe_full_mix', 'audio_probe_play_control', 'audio_probe_iframe',
  'audio_probe_primary_check', 'audio_probe_primary_verified',
  ...['source', 'mix'].flatMap(control => ['original', 'synth', 'main', 'backing', 'solo', 'absent', 'loading', 'unknown']
    .map(state => `audio_probe_${control}_${state}`)),
  'audio_probe_play_enabled', 'audio_probe_play_attribute_true', 'audio_probe_play_attribute_false',
  'audio_probe_play_attribute_missing', 'audio_probe_play_attribute_other',
]);
const EVENT_OUTCOMES = new Set(['started', 'success', 'ready', 'unavailable']);
const OUTCOMES = new Set(['found', 'not_found', 'failed', 'cancelled']);
const ERROR_REASONS = new Set(['cancelled', 'needs_attention', 'needs_login', 'revision_unavailable',
  'unavailable', 'timeout', 'network_error', 'rate_limited', 'needs_audio', 'failed']);
const REASONS = new Set([...ABSENT_REASONS, ...ERROR_REASONS, 'audio_probe_found', 'audio_probe_primary_verified']);

function sanitizeAudioDiscoveryEvent(value) {
  if (!EVENT_CODES.has(value?.code) || !EVENT_OUTCOMES.has(value?.outcome)) return null;
  return { code: value.code, outcome: value.outcome };
}

function timestamp(value) { return Number.isSafeInteger(value) && value >= 0 && value <= MAX_TIMESTAMP; }

function sanitizeAudioDiscovery(value) {
  if (value?.version !== 1 || !OUTCOMES.has(value.outcome) || !REASONS.has(value.reason)
      || !timestamp(value.startedAt) || !timestamp(value.finishedAt) || value.finishedAt < value.startedAt
      || !Array.isArray(value.events) || typeof value.truncated !== 'boolean') return null;
  const limited = value.events.slice(0, MAX_EVENTS), events = limited.map(sanitizeAudioDiscoveryEvent).filter(Boolean);
  return { version: 1, outcome: value.outcome, reason: value.reason, startedAt: value.startedAt, finishedAt: value.finishedAt,
    events, truncated: value.truncated || value.events.length > MAX_EVENTS || events.length !== limited.length };
}

function audioDiscoveryErrorReason(error) {
  if (error?.name === 'AbortError' || error?.code === 'cancelled') return 'cancelled';
  return ERROR_REASONS.has(error?.code) ? error.code : 'failed';
}

function createAudioDiscoveryTrace() {
  const startedAt = Date.now(), events = [];
  let truncated = false;
  return {
    record(code, outcome) {
      const event = sanitizeAudioDiscoveryEvent({ code, outcome });
      if (!event) return;
      if (events.length < MAX_EVENTS) events.push(event); else truncated = true;
    },
    finish(outcome, reason) {
      return sanitizeAudioDiscovery({ version: 1, outcome, reason, startedAt,
        finishedAt: Math.max(startedAt, Date.now()), events, truncated });
    },
  };
}

module.exports = { sanitizeAudioDiscovery, sanitizeAudioDiscoveryEvent, audioDiscoveryErrorReason, createAudioDiscoveryTrace };

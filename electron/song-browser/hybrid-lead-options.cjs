'use strict';
const POLICY = 'hybrid-lead-v2';

function normalizeHybridLead(value) {
  if (value == null) return { enabled: false };
  if (typeof value !== 'object' || Array.isArray(value) || typeof (value.enabled ?? false) !== 'boolean') throw new Error('Invalid Hybrid Lead options.');
  const allowed = new Set(['enabled', 'policy', 'roles', 'mainTrackId', 'excludedTrackIds', 'preferredTrackIds', 'sourceSha256', 'reviewSources']);
  if (Object.keys(value).some(key => !allowed.has(key))) throw new Error('Unknown Hybrid Lead option.');
  if (!value.enabled) return { enabled: false };
  if (value.policy !== undefined && value.policy !== POLICY) throw new Error('Review sources again for the updated Hybrid Lead policy.');
  const roles = value.roles ?? {};
  if (!roles || typeof roles !== 'object' || Array.isArray(roles) || Object.keys(roles).length > 128 || Object.entries(roles).some(([id, role]) => !id.length || id.length > 160 || !['solo', 'lead', 'accompaniment'].includes(role))) throw new Error('Invalid Hybrid Lead source roles.');
  const out = { enabled: true, policy: POLICY, roles: Object.fromEntries(Object.entries(roles).sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0)) };
  for (const key of ['mainTrackId', 'sourceSha256']) if (value[key] !== undefined) {
    if (typeof value[key] !== 'string' || !value[key].length || value[key].length > 160) throw new Error('Invalid Hybrid Lead source identity.');
    out[key] = value[key];
  }
  for (const key of ['excludedTrackIds', 'preferredTrackIds']) {
    const items = value[key] ?? [];
    if (!Array.isArray(items) || items.length > 128 || items.some(id => typeof id !== 'string' || !id.length || id.length > 160)) throw new Error('Invalid Hybrid Lead source selection.');
    out[key] = [...new Set(items)];
  }
  if (value.reviewSources === true) out.reviewSources = true;
  return out;
}

function matchesHybridRequest(request, result) {
  if (request?.enabled && result?.policy !== POLICY) return false;
  const wanted = normalizeHybridLead(request), actual = normalizeHybridLead(result);
  if (wanted.enabled !== actual.enabled) return false;
  if (!wanted.enabled) return true;
  for (const key of ['mainTrackId', 'sourceSha256']) if (wanted[key] && wanted[key] !== actual[key]) return false;
  return ['excludedTrackIds', 'preferredTrackIds', 'roles'].every(key => JSON.stringify(wanted[key]) === JSON.stringify(actual[key]));
}
module.exports = { normalizeHybridLead, matchesHybridRequest };

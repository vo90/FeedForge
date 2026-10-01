'use strict';
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const CURRENT_PRESERVATION_CONTRACT = 41;
const CHART_GUIDANCE_POLICY = 'feedforge-chart-guidance-v2';
const KNOWN_PRESERVATION_CONTRACTS = Array.from({ length: CURRENT_PRESERVATION_CONTRACT }, (_, i) => i + 1);
const DIGEST = /^[a-f0-9]{64}$/;
const digest = (bytes) => crypto.createHash('sha256').update(bytes).digest('hex');

function checkedFile(root, relative, expected, maxBytes) {
  if (!DIGEST.test(expected || '')) throw new Error('The conversion report reference is invalid.');
  const filename = path.join(root, relative), stat = fs.lstatSync(filename);
  const base = fs.realpathSync.native(root), real = fs.realpathSync.native(filename);
  const rel = path.relative(base, real);
  if (!rel || rel.startsWith('..') || path.isAbsolute(rel) || stat.isSymbolicLink() || !stat.isFile() || stat.size > maxBytes) throw new Error('The conversion report file is invalid.');
  if (digest(fs.readFileSync(real)) !== expected) throw new Error('The saved conversion report has changed.');
  return real;
}

function inspectEvidence(root, reference, outputHash) {
  if (!KNOWN_PRESERVATION_CONTRACTS.includes(reference?.version) || !DIGEST.test(reference.id || '')) throw new Error('The conversion report is unavailable.');
  const filename = checkedFile(root, `records/${reference.id}.json`, reference.id, 1024 * 1024);
  const record = JSON.parse(fs.readFileSync(filename, 'utf8'));
  const report = checkedFile(root, `objects/${record.objects?.verification}`, record.objects?.verification, 16 * 1024 * 1024);
  const verification = JSON.parse(fs.readFileSync(report, 'utf8'));
  if (record.version !== reference.version || record.objects.verification !== reference.verificationHash || record.objects.source !== reference.sourceHash
      || (outputHash && (record.outputHash !== outputHash || reference.outputHash !== outputHash))) throw new Error('The conversion report does not describe this FeedPak.');
  if (verification.status === 'passed' && verification.sourceSha256 !== record.objects.source) throw new Error('The conversion report does not describe this source tab.');
  return { record, verification };
}

function reportBundle(root, reference) {
  inspectEvidence(root, reference);
  return checkedFile(root, `records/${reference.id}.zip`, reference.bundleHash, 256 * 1024 * 1024);
}
function compatibilityReport(root, reference) {
  const { record } = inspectEvidence(root, reference);
  if (!record.objects.compatibility) return null;
  const file = checkedFile(root, `objects/${record.objects.compatibility}`, record.objects.compatibility, 64 * 1024 * 1024);
  const report = JSON.parse(fs.readFileSync(file, 'utf8'));
  if (!Number.isInteger(report.version) || !Array.isArray(report.findings)) throw new Error('The compatibility report is invalid.');
  return report;
}

// Derived from durable evidence, not the bounded job history. The latest
// assessment of a song/revision replaces earlier attempts in the working list.
function compatibilityBacklog(root) {
  const latest = new Map(), history = new Map(); let unreadable = 0;
  const directory = path.join(root, 'records');
  if (!fs.existsSync(directory)) return { version: 1, groups: [], unreadable };
  for (const name of fs.readdirSync(directory)) {
    if (!/^[a-f0-9]{64}\.json$/.test(name)) continue;
    try {
      const id = name.slice(0, -5), file = checkedFile(root, `records/${name}`, id, 1024 * 1024);
      const record = JSON.parse(fs.readFileSync(file, 'utf8'));
      if (!record.objects?.compatibility) continue;
      const reference = { version: record.version, id, verificationHash: record.objects.verification, sourceHash: record.objects.source };
      const report = compatibilityReport(root, reference);
      const identity = record.sourceMetadata || {};
      const key = identity.songId && identity.revisionId ? `${identity.songId}:${identity.revisionId}` : record.objects.source;
      const previous = latest.get(key), time = fs.statSync(file).mtimeMs;
      if (!history.has(key)) history.set(key, new Map());
      for (const finding of report.findings) {
        const featureKey = JSON.stringify([finding.feature, finding.category, finding.impact]);
        history.get(key).set(featureKey, finding);
      }
      if (!previous || report.version > previous.report.version || report.version === previous.report.version && time > previous.time)
        latest.set(key, { report, time, identity, reference });
    } catch { unreadable++; }
  }
  const groups = new Map(), resolved = [];
  for (const [sourceKey, { report, identity, reference }] of latest) {
    for (const finding of report.findings) {
      const key = JSON.stringify([finding.feature, finding.category, finding.impact]);
      if (!groups.has(key)) groups.set(key, { feature: finding.feature, category: finding.category, impact: finding.impact,
        message: finding.message, workStatus: finding.workStatus || (finding.impact === 'display_or_expression' ? 'display_limitation' : 'technical_work'),
        decisionId: finding.decisionId, occurrences: 0, songs: new Map(), examples: [] });
      const group = groups.get(key); group.occurrences++;
      group.songs.set(sourceKey, { ...identity, sourceKey, reportId: reference.id });
      if (group.examples.length < 10) group.examples.push({ ...finding, ...identity, reportId: reference.id, sourceHash: reference.sourceHash, assessmentVersion: report.version });
    }
    // Disappearance from a preflight is not proof of a fixed conversion.
    // Resolve historical gaps only after this exact revision has a passing
    // independent package verification under the current contract.
    let checked;
    try { checked = inspectEvidence(root, reference); } catch { unreadable++; continue; }
    if (checked.verification.version === CURRENT_PRESERVATION_CONTRACT && checked.verification.status === 'passed' && reference.version === CURRENT_PRESERVATION_CONTRACT
        && report.version >= CURRENT_PRESERVATION_CONTRACT && DIGEST.test(checked.record.outputHash || '')) {
      const active = new Set(report.findings.map(f => JSON.stringify([f.feature, f.category, f.impact])));
      for (const [key, finding] of history.get(sourceKey) || []) {
        if (!active.has(key)) resolved.push({ ...finding, ...identity, sourceKey, reportId: reference.id,
          sourceHash: reference.sourceHash, assessmentVersion: report.version, workStatus: 'fixed_verified' });
      }
    }
  }
  return { version: 2, assessedRevisions: latest.size, unreadable, resolved,
    groups: [...groups.values()].map(g => ({ ...g, affectedSongs: g.songs.size, songs: [...g.songs.values()] }))
      .sort((a, b) => b.affectedSongs - a.affectedSongs || a.feature.localeCompare(b.feature)) };
}
function verifiedChartGuidance(proof, count) {
  return proof?.policy === CHART_GUIDANCE_POLICY && proof.status === 'passed' && proof.sourceAuthored === false
    && Number.isSafeInteger(count) && count > 0 && proof.arrangements === count;
}
module.exports = { inspectEvidence, reportBundle, compatibilityReport, compatibilityBacklog, CURRENT_PRESERVATION_CONTRACT, KNOWN_PRESERVATION_CONTRACTS,
  CHART_GUIDANCE_POLICY, verifiedChartGuidance };

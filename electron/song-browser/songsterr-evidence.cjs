'use strict';
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
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
  if (![1, 2, 3].includes(reference?.version) || !DIGEST.test(reference.id || '')) throw new Error('The conversion report is unavailable.');
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
module.exports = { inspectEvidence, reportBundle };

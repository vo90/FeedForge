'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');
const { SongsterrJobs } = require('../electron/song-browser/songsterr-jobs.cjs');
const { CURRENT_PRESERVATION_CONTRACT: CURRENT } = require('../electron/song-browser/songsterr-evidence.cjs');
const digest = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const POLICY_SCOPE = 'note_owned_finger_vibrato';

function vibrato(feature) {
  const wide = feature === 'beat.wideVibrato';
  return { feature, category: 'game_limitation', impact: 'display_or_expression', workStatus: 'display_limitation',
    retained: 'original_source', value: true, valueTruncated: false,
    message: `The beat-level ${wide ? 'wide vibrato' : 'vibrato'} annotation is retained in the source and written beat. Note vibrato follows explicit note instructions; this beat flag does not add a note controller or scored pitch.`,
    location: `parts/0/measures/0/voices/0/beats/${wide ? 1 : 0}/${wide ? 'wideVibrato' : 'vibrato'}`, measure: 1, beat: wide ? 2 : 1 };
}
function report() {
  return { version: CURRENT, status: 'limitations', findingCount: 3, truncated: false,
    findings: [vibrato('beat.vibrato'), vibrato('beat.wideVibrato'),
      { feature: 'note.rightFingering', category: 'game_limitation', impact: 'display_or_expression', message: 'Picking-hand fingering is retained only.' }] };
}
async function loaded(t, { contract = CURRENT, scope = [POLICY_SCOPE], compatibility = report(), proofStatus = 'passed', state = 'completed' } = {}) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'feedforge-vibrato-notices-'));
  const root = path.join(directory, 'jobs'), audit = path.join(directory, 'evidence'), outputDir = path.join(directory, 'songs');
  for (const folder of [root, outputDir, path.join(audit, 'objects'), path.join(audit, 'records')]) fs.mkdirSync(folder, { recursive: true });
  const outputPath = path.join(outputDir, 'test.feedpak'), bytes = Buffer.from('Synthetic retained FeedPak');
  fs.writeFileSync(outputPath, bytes);
  const outputHash = digest(bytes), sourceHash = digest('Synthetic retained source');
  const proof = { version: contract, status: proofStatus, sourceSha256: sourceHash, scope };
  const writeObject = value => {
    const bytes = JSON.stringify(value), hash = digest(bytes);
    fs.writeFileSync(path.join(audit, 'objects', hash), bytes);
    return hash;
  };
  const verificationHash = writeObject(proof), compatibilityHash = writeObject(compatibility);
  const evidence = { version: contract, outputHash, objects: { source: sourceHash, verification: verificationHash, compatibility: compatibilityHash } };
  const recordBytes = JSON.stringify(evidence), id = digest(recordBytes);
  fs.writeFileSync(path.join(audit, 'records', `${id}.json`), recordBytes);
  const job = { id: crypto.randomUUID(), source: 'songsterr', chart: { id: '123', title: 'Example', artist: 'Artist' }, state,
    outputDir, outputPath, recipe: { preservationContract: contract }, compatibility: { status: 'limitations' }, warnings: [],
    // A forged summary scope must never substitute for full saved verification.
    verification: { version: contract, status: 'passed', outputHash, scope: [POLICY_SCOPE] },
    evidence: { version: contract, id, sourceHash, verificationHash, outputHash } };
  fs.writeFileSync(path.join(root, 'jobs.json'), JSON.stringify({ version: 1, jobs: [job] }));
  const jobs = new SongsterrJobs({ root, provider: {}, runConverter: async () => { throw new Error('No conversion expected'); } });
  t.after(async () => {
    await jobs.dispose();
    const resolved = path.resolve(directory);
    assert.equal(path.dirname(resolved), path.resolve(os.tmpdir()));
    assert.ok(path.basename(resolved).startsWith('feedforge-vibrato-notices-'));
    fs.rmSync(resolved, { recursive: true, force: true });
  });
  await jobs.ready;
  return { jobs, job: jobs.jobs[0], outputPath, root, audit, compatibility, files: {
    record: path.join(audit, 'records', `${id}.json`), verification: path.join(audit, 'objects', verificationHash),
    compatibility: path.join(audit, 'objects', compatibilityHash) } };
}
function noticeIds(value, kind) { return value.playerNotices[kind].map(row => row.id); }

test('loaded verified evidence shares the note-owned policy between player summary and prioritized details', async t => {
  const f = await loaded(t), original = fs.readFileSync(f.files.compatibility, 'utf8');
  const summary = f.jobs.snapshot()[0];
  assert.equal(summary.verification.status, 'passed');
  assert.deepEqual(noticeIds(summary, 'warnings'), ['note.rightFingering']);
  assert.deepEqual(noticeIds(summary, 'information'), ['beat.vibrato', 'beat.wideVibrato']);
  const details = f.jobs.compatibilityDetails(f.job.id);
  assert.equal(details.findings[0].feature, 'note.rightFingering');
  assert.equal(details.findingCount, 3);
  assert.equal(fs.readFileSync(f.files.compatibility, 'utf8'), original);
  const backlog = f.jobs.compatibilityList();
  assert.equal(backlog.groups.find(row => row.feature === 'beat.vibrato').impact, 'display_or_expression');
  const saved = JSON.parse(fs.readFileSync(path.join(f.root, 'jobs.json'), 'utf8')).jobs[0];
  assert.equal(saved.noteOwnedFingerVibrato, undefined, 'The derived UI policy is never persisted');
});

for (const [name, options] of [
  ['missing full scope despite summary claim', { scope: [] }],
  ['malformed full scope', { scope: POLICY_SCOPE }],
  ['historical contract despite forged scope', { contract: 93 }],
  ['failed full verification', { proofStatus: 'failed' }],
  ['unfinished job', { state: 'needs_attention' }],
  ['truncated report', { compatibility: { ...report(), truncated: true } }],
  ['mismatched report count', { compatibility: { ...report(), findingCount: 4 } }],
  ['blocked report', { compatibility: { ...report(), status: 'blocked' } }],
]) test(`loaded evidence keeps vibrato warnings: ${name}`, async t => {
  const f = await loaded(t, options);
  assert.ok(noticeIds(f.jobs.snapshot()[0], 'warnings').includes('beat.vibrato'));
  assert.equal(f.jobs.compatibilityDetails(f.job.id).findings[0].feature, 'beat.vibrato');
});

test('a modified FeedPak invalidates cached vibrato classification for summary and details', async t => {
  const f = await loaded(t);
  assert.ok(noticeIds(f.jobs.snapshot()[0], 'information').includes('beat.vibrato'));
  fs.appendFileSync(f.outputPath, 'changed');
  f.jobs.lastOutputCheck = 0; await f.jobs.refreshOutputs();
  const changed = f.jobs.snapshot()[0];
  assert.equal(changed.verification.status, 'modified');
  assert.ok(noticeIds(changed, 'warnings').includes('beat.vibrato'));
  assert.equal(f.jobs.compatibilityDetails(f.job.id).findings[0].feature, 'beat.vibrato');
});

for (const file of ['record', 'verification', 'compatibility']) test(`changed ${file} evidence cannot retain cached informational status`, async t => {
  const f = await loaded(t);
  assert.ok(noticeIds(f.jobs.snapshot()[0], 'information').includes('beat.vibrato'));
  fs.appendFileSync(f.files[file], ' ');
  assert.throws(() => f.jobs.compatibilityDetails(f.job.id), /report has changed/);
  const changed = f.jobs.snapshot()[0];
  assert.equal(noticeIds(changed, 'information').includes('beat.vibrato'), false);
  assert.ok(noticeIds(changed, 'warnings').includes('unavailable_report'));
});

test('snapshots reuse bounded evidence reads and explicit details refresh the shared cache', async t => {
  const f = await loaded(t), original = fs.readFileSync;
  let reads = 0;
  t.mock.method(fs, 'readFileSync', function (filename, ...args) {
    if (Object.values(f.files).includes(String(filename))) reads++;
    return original.call(this, filename, ...args);
  });
  f.jobs.snapshot();
  const first = reads; assert.ok(first > 0);
  assert.equal(Object.hasOwn(f.jobs.noticeCache.get(f.job), 'report'), false, 'Large compatibility reports must not be held in the per-job cache');
  for (let i = 0; i < 10; i++) f.jobs.snapshot();
  assert.equal(reads, first);
  f.jobs.compatibilityDetails(f.job.id);
  const details = reads; assert.ok(details > first);
  f.jobs.snapshot(); assert.equal(reads, details);
  f.job.outputVerification = 'modified';
  assert.ok(noticeIds(f.jobs.snapshot()[0], 'warnings').includes('beat.vibrato'));
  assert.ok(reads > details, 'Output verification changes invalidate the same cache');
});

test('scheduled output refresh rechecks cached compatibility evidence even when the FeedPak is unchanged', async t => {
  const f = await loaded(t);
  assert.ok(noticeIds(f.jobs.snapshot()[0], 'information').includes('beat.vibrato'));
  fs.appendFileSync(f.files.compatibility, ' ');
  f.jobs.lastOutputCheck = 0; await f.jobs.refreshOutputs();
  const refreshed = f.jobs.snapshot()[0];
  assert.equal(refreshed.verification.status, 'passed', 'The unchanged package remains verified');
  assert.ok(noticeIds(refreshed, 'warnings').includes('unavailable_report'));
  assert.equal(noticeIds(refreshed, 'information').includes('beat.vibrato'), false);
});

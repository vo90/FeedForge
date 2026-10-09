'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');
const { playerNotices, classifyFinding, prioritizeFindings } = require('../electron/song-browser/songsterr-notices.cjs');
const { SongsterrJobs } = require('../electron/song-browser/songsterr-jobs.cjs');
const completed = { state: 'completed', verification: { status: 'passed', timing: 'source_map' } };
const keySignature = { feature: 'measure.keySignature', category: 'game_limitation', impact: 'display_or_expression',
  message: 'The written key signature is retained in the source. Explicit pitches are converted unchanged.' };
const letRing = { feature: 'beat.letRing', category: 'game_limitation', impact: 'display_or_expression', message: 'Let-ring instruction is not shown.' };
const findings = rows => ({ version: 109, status: 'limitations', findingCount: rows.length, findings: rows });

test('qualified wah guidance is informational while historical omission remains a warning', () => {
  const f = {feature:'beat.wahwah',category:'game_limitation',impact:'display_or_expression',
    message:'Wah open/closed instructions are preserved as optional timed guidance in the updated highway and tab view. Audio and scoring are unchanged.'};
  assert.equal(classifyFinding(f),'info');
  assert.equal(classifyFinding({...f,message:'The wah pedal marking is retained in the source. Notes and timing are converted; pedal expression is not represented in the game chart.'}),'warning');
  assert.equal(playerNotices(completed,findings([f,f])).warnings.length,0);
});

test('qualified picking rates are information, generic and historical detail remain visible', () => {
  const f={feature:'note.tremolo',category:'game_limitation',impact:'display_or_expression',
    message:'Tremolo picking rates and active spans are preserved for the updated highway and tab view. Trail spacing indicates speed within readable limits; individual picks are not scored separately.'};
  assert.equal(classifyFinding(f),'info');
  for(const message of ['Overlapping tremolo instructions disagree.', 'Exact subdivision and within-tie timing remain in the source.'])
    assert.equal(classifyFinding({...f,message}),'warning');
});

test('supported let-ring spans are information while older missing-display reports remain warnings', () => {
  const supported = {...letRing, message: 'Let-ring instructions and source-derived ringing spans are preserved for the updated game. Written rhythm and scored note durations are unchanged.'};
  assert.equal(classifyFinding(supported), 'info');
  assert.equal(classifyFinding(letRing), 'warning');
  const result = playerNotices(completed, findings([supported, {...supported, measure: 2}]));
  assert.equal(result.warnings.length, 0);
  assert.ok(result.information.some(g => g.id === 'beat.letRing'));
});

test('thousands of routine records do not make an import a warning; source evidence is unchanged', () => {
  const report = findings(Array.from({ length: 2400 }, (_, i) => ({ ...keySignature, measure: i + 1 })));
  const job = { ...completed, warnings: report.findings.map(f => `Key signature in Guitar, measure ${f.measure}, is retained in source evidence only.`)
    .concat('Timing was imported from Songsterr for this tab revision and recording.', 'Excluded non-guitar/bass track: Drums.') };
  const original = JSON.stringify({ report, job });
  const result = playerNotices(job, report);
  assert.equal(result.warnings.length, 0);
  assert.equal(result.diagnosticCount, 2402);
  assert.equal(result.information.filter(g => g.id === keySignature.feature).length, 1);
  assert.ok(JSON.stringify(result).length < 2500);
  assert.equal(JSON.stringify({ report, job }), original);
});

test('real missing instructions are grouped with representative arrangement/bar locations', () => {
  const result = playerNotices(completed, findings(Array.from({ length: 300 }, (_, i) => ({ ...letRing, arrangement: 'Lead', measure: i + 1 }))));
  assert.equal(result.warnings.length, 1);
  assert.equal(result.warnings[0].occurrences, 300);
  assert.equal(result.warnings[0].locations.length, 8);
  assert.match(result.warnings[0].locations[0], /Lead.*Measure 1/);
});

test('unknown findings, missing reports and blocking semantics cannot inherit quiet classifications', () => {
  for (const f of [{ ...keySignature, category: 'unknown_semantics' }, { ...keySignature, impact: 'blocking' },
    { ...keySignature, message: 'This changes a played pitch.' }, { ...keySignature, message: keySignature.message + ' Another effect is lost.' },
    { feature: 'future', message: 'Unknown' }, { feature: 'future', category: 'game_limitation', impact: 'display_or_expression' }]) {
    assert.equal(classifyFinding(f), 'warning');
    assert.equal(playerNotices(completed, findings([f])).warnings.length, 1);
  }
  assert.equal(playerNotices({ ...completed, compatibility: { status: 'limitations' } }, null).warnings[0].id, 'unavailable_report');
  assert.equal(playerNotices(completed, findings([]), { reportUnavailable: true }).warnings[0].id, 'unavailable_report');
  assert.equal(playerNotices(completed, { ...findings([]), truncated: true }).warnings[0].id, 'incomplete_report');
  const unknown = playerNotices({ ...completed, warnings: ['New issue', 'New issue', { message: 'Another issue', location: 'bar 4' }] }, findings([]));
  assert.equal(unknown.warnings.length, 1); assert.equal(unknown.warnings[0].occurrences, 3);
  assert.deepEqual(unknown.warnings[0].examples, ['Another issue']);
});

test('a feature shared by supported and omitted gestures is classified by its actual meaning', () => {
  const supported = { feature: 'note.tied_harmonic', category: 'game_limitation', impact: 'display_or_expression',
    message: 'An artificial-harmonic contact starts during this sustain. Its timing and target are preserved; the updated game displays the contact without another picked attack.' };
  assert.equal(classifyFinding(supported), 'info');
  assert.equal(classifyFinding({ ...supported, message: 'A harmonic change is not represented.' }), 'warning');
});

test('inconclusive timing stays informational; suspected mismatches stay visible and are not duplicated', () => {
  const job = { ...completed, verification: { ...completed.verification, timingAssessment: { version: 'local-recording-clock-v3',
    status: 'inconclusive', supportedWindows: 1, windowCount: 27, suspectedMismatchWindows: 0 } },
    warnings: ['Timing not fully verified. Automatic checks supported timing in 1 of 27 passages. The remaining passages could not be reliably verified; this does not necessarily mean they are wrong. Songsterr\'s timing was preserved.'] };
  assert.equal(playerNotices(job, findings([])).warnings.length, 0);
  assert.match(playerNotices(job, findings([])).information.find(n => n.id === 'timing').message, /1 of 27/);
  job.verification.timingAssessment.suspectedMismatchWindows = 1;
  job.verification.timingAssessment.suspectedMismatchRanges = [{ start: 48, end: 64 }];
  const result = playerNotices(job, findings([]));
  assert.equal(result.warnings.length, 1); assert.match(result.warnings[0].message, /0:48–1:04/);
  assert.equal(result.information.some(n => n.id === 'timing'), false);
});

test('missing notes, uncertain cutoff policy and lyrics/artwork cannot be hidden by a timing pass', () => {
  const high = { feature: 'note.fret_range', category: 'game_limitation', impact: 'gameplay_omission', message: 'Omitted notes', arrangement: 'Bass', measure: 80 };
  const job = { ...completed, artwork: { status: 'unavailable' }, verification: { ...completed.verification,
    omissions: { omittedNotes: 14 }, adjustments: { omittedEndingNotes: 3 }, timingAssessment: { status: 'supported', supportedWindows: 10, windowCount: 10 },
    timingWarning: { status: 'accepted_with_warning', uncertainRanges: [{ start: 100, end: 104 }] } }, warnings: [
    'Completed with omitted notes: 14 unsupported high-fret or connected slide events are not displayed or scored. Original tab retained. Staff notation for affected arrangements is retained in the source only.',
    '1 lyric syllables follow the last available vocal note; retained in source only.'] };
  const result = playerNotices(job, findings([high]));
  assert.deepEqual(result.warnings.map(n => n.id).sort(), ['artwork', 'ending_notes', 'high_frets', 'lyrics', 'timing']);
  assert.match(result.warnings.find(n => n.id === 'high_frets').locations[0], /Bass.*80/);
});

test('modified files never present historic timing as verified', () => {
  const result = playerNotices({ ...completed, verification: { status: 'modified', timingAssessment: { status: 'supported', supportedWindows: 5, windowCount: 5 } } }, findings([]));
  assert.equal(result.information.some(n => n.id === 'timing'), false);
  assert.equal(result.warnings[0].id, 'verification');
});

test('a real Hybrid Lead coverage limitation remains visible outside its collapsed details', () => {
  const result = playerNotices({ ...completed, verification: { ...completed.verification, hybridLead: { coverageStatus: 'limited' } } }, findings([]));
  assert.equal(result.warnings[0].id, 'hybrid');
});

test('playing limitations precede notation noise before the 200-record display limit', () => {
  const records = Array.from({ length: 600 }, () => keySignature).concat(letRing, { feature: 'note.fret_range', impact: 'gameplay_omission' });
  const result = prioritizeFindings(records).slice(0, 200);
  assert.equal(result[0].feature, 'note.fret_range'); assert.equal(result[1].feature, 'beat.letRing');
  assert.equal(records[0], keySignature);
  const manyWarnings = Array.from({ length: 1000 }, () => letRing).concat({ feature: 'note.bend_timing', category: 'conversion_check', message: 'Conflicting bends' });
  assert.ok(prioritizeFindings(manyWarnings).slice(0, 200).some(f => f.feature === 'note.bend_timing'));
});

test('job manager reads full hash-checked evidence, caches it and prioritizes detail results', async t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'songsterr-notices-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const jobs = new SongsterrJobs({ root: path.join(root, 'jobs'), provider: {} });
  await jobs.ready;
  const audit = path.join(root, 'evidence'); fs.mkdirSync(path.join(audit, 'objects'), { recursive: true }); fs.mkdirSync(path.join(audit, 'records'));
  const save = (value, folder, suffix = '') => { const bytes = JSON.stringify(value), id = crypto.createHash('sha256').update(bytes).digest('hex'); fs.writeFileSync(path.join(audit, folder, id + suffix), bytes); return id; };
  const compatibility = save(findings(Array.from({ length: 600 }, () => keySignature).concat(letRing)), 'objects');
  const sourceHash = crypto.createHash('sha256').update('source').digest('hex');
  const verificationHash = save({ status: 'passed', sourceSha256: sourceHash }, 'objects');
  const id = save({ version: 109, objects: { compatibility, source: sourceHash, verification: verificationHash } }, 'records', '.json');
  const job = { ...completed, id: 'test-job', chart: { id: '1' }, outputVerification: 'passed', evidence: { id, version: 109, sourceHash, verificationHash } };
  jobs.jobs.push(job);
  const first = jobs.public(job).playerNotices;
  assert.equal(first.warnings[0].id, 'beat.letRing'); assert.equal(first.warnings.length, 1);
  assert.equal(jobs.public(job).playerNotices, first);
  assert.equal(jobs.compatibilityDetails(job.id).findings[0].feature, 'beat.letRing');
  job.outputVerification = 'modified';
  assert.notEqual(jobs.public(job).playerNotices, first);
  assert.ok(jobs.public(job).playerNotices.warnings.some(n => n.id === 'verification'));
  fs.writeFileSync(path.join(audit, 'objects', compatibility), '{}');
  job.verification = { ...job.verification }; // New assessment invalidates projection cache.
  assert.ok(jobs.public(job).playerNotices.warnings.some(n => n.id === 'unavailable_report'));
});

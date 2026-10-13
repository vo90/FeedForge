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

test('retained octave notation stays diagnostic while invalid octave data warns', () => {
  const finding = { feature: 'beat.octaveClef', category: 'source_metadata', impact: 'source_retained',
    message: 'Explicit tab pitches are preserved; the octave notation remains in the source.' };
  assert.equal(classifyFinding(finding), 'diagnostic');
  assert.deepEqual(playerNotices(completed, { findings: [finding] }).warnings, []);
  assert.equal(classifyFinding({ ...finding, category: 'source_structure', impact: 'blocking' }), 'warning');
});

test('hybrid high-fret omissions are explained once, but unsupported unknowns remain visible', () => {
  const hybridLead = { coverageStatus: 'limited', limitations: [{ reason: 'unsupported_source_gesture' }] };
  const job = { ...completed, verification: { ...completed.verification, omissions: { omittedNotes: 14 }, hybridLead } };
  const result = playerNotices(job, { findings: [] });
  assert.equal(result.warnings.length, 1);
  assert.equal(result.warnings[0].id, 'high_frets');
  assert.match(result.warnings[0].message, /Hybrid Lead/);
  for (const reason of ['future_reason', undefined]) {
    const changed = { ...job, verification: { ...job.verification, hybridLead: { ...hybridLead, limitations: [{ reason }] } } };
    assert.ok(playerNotices(changed, { findings: [] }).warnings.some(w => w.id === 'hybrid'));
  }
  delete job.verification.omissions;
  assert.ok(playerNotices(job, { findings: [] }).warnings.some(w => w.id === 'hybrid_unsupported'));
});

test('hybrid conflict, planning and absent lead have separate honest explanations', () => {
  const job = { ...completed, verification: { ...completed.verification, hybridLead: {
    coverageStatus: 'limited', planningLimited: true,
    limitations: [{ reason: 'conflicting_primary', name: 'Right guitar', missingRanges: [{ start: 153.25, end: 155.775 }] }],
    musicalAudit: { localLeadAudit: { unresolvedCount: 1 } }
  } } };
  const original = JSON.stringify(job);
  const warnings = playerNotices(job, { findings: [] }).warnings;
  assert.deepEqual(warnings.map(w => w.id).sort(), ['hybrid_choices', 'hybrid_missing_lead', 'hybrid_planning']);
  assert.match(warnings.find(w => w.id === 'hybrid_choices').locations[0], /Right guitar.*2:33–2:36/);
  assert.equal(JSON.stringify(job), original);
});

test('limited hybrid reports never silently disappear without an explanation', () => {
  for (const limitations of [[], [{ reason: 'alternate_voice' }]]) {
    const job = { ...completed, verification: { ...completed.verification, hybridLead: { coverageStatus: 'limited', limitations } } };
    assert.ok(playerNotices(job, { findings: [] }).warnings.some(w => w.id === 'hybrid'));
  }
});
const keySignature = { feature: 'measure.keySignature', category: 'game_limitation', impact: 'display_or_expression',
  message: 'The written key signature is retained in the source. Explicit pitches are converted unchanged.' };
const letRing = { feature: 'beat.letRing', category: 'game_limitation', impact: 'display_or_expression', message: 'Let-ring instruction is not shown.' };
const findings = rows => ({ version: 109, status: 'limitations', findingCount: rows.length, findings: rows });

test('qualified harmonic position and tempo appearance are information, with omissions still visible', () => {
  const harmonic = { feature: 'note.harmonicFret', category: 'game_limitation', impact: 'display_or_expression',
    message: 'Natural harmonic position 15 is displayed as 14.7, matching the source playback harmonic; the authored value is retained.' };
  const tempo = { ...harmonic, feature: 'tempo.visible',
    message: 'Tempo-label visibility is retained in the source. The numeric tempo and playback timing are unchanged.' };
  for (const f of [harmonic, tempo]) {
    assert.equal(classifyFinding(f), 'info');
    for (const changed of [{ impact: 'blocking' }, { impact: 'gameplay_omission' },
      ...['unknown_semantics', 'source_structure', 'conversion_check', 'converter_gap'].map(category => ({ category })),
      { message: f.message + ' Some expression is missing.' }, { message: 'Unknown source value.' }]) {
      assert.equal(classifyFinding({ ...f, ...changed }), 'warning');
    }
  }
  const omitted = { ...harmonic, message: 'A harmonic change is not represented.', measure: 8 };
  const report = findings([harmonic, tempo, omitted]);
  const original = JSON.stringify(report);
  const result = playerNotices(completed, report);
  assert.equal(result.warnings.length, 1);
  assert.equal(result.warnings[0].message, omitted.message);
  assert.match(result.warnings[0].locations[0], /Measure 8/);
  assert.equal(result.information.find(g => g.id === harmonic.feature).title, 'Natural-harmonic touch position');
  assert.equal(result.information.find(g => g.id === tempo.feature).title, 'Tempo-label appearance');
  assert.equal(JSON.stringify(report), original);
});

test('only equivalent section label decisions become diagnostics; missing artwork still warns', () => {
  const equivalent = "Equivalent section labels in measure 5 use the authored label 'Verse 1'; all track labels are retained.";
  const job = { ...completed, artwork: { status: 'artist_ambiguous' }, warnings: [equivalent] };
  const before = JSON.stringify(job);
  const result = playerNotices(job, findings([]));
  assert.deepEqual(result.warnings.map(g => g.id), ['artwork']);
  assert.equal(result.diagnosticCount, 1);
  assert.equal(JSON.stringify(job), before);
  for (const message of [equivalent + ' Another issue.', equivalent.replace('Equivalent', 'Conflicting'),
    equivalent.replace('all track labels are retained.', 'track labels were lost.'),
    "Section names differ in measure 5; using 'Verse 1' based on source track order.",
    'Unclassified Songsterr part field voices is retained in source evidence only.']) {
    assert.equal(playerNotices({ ...completed, warnings: [message] }, findings([])).warnings[0].id, 'other');
  }
});

test('validated empty-track warnings are routine while raw source evidence stays unchanged', () => {
  const row = { trackIndex: 1, name: 'Reference. Chords', status: 'skipped_empty', noteCount: 0, blockingFeatures: [] };
  const report = { ...findings([]), status: 'compatible', arrangements: [row] };
  const job = { ...completed, warnings: [`Skipped empty arrangement: ${row.name}.`] };
  const before = JSON.stringify({ report, job });
  const result = playerNotices(job, report);
  assert.equal(result.warnings.length, 0);
  assert.equal(result.diagnosticCount, 1);
  assert.equal(result.rawWarningCount, 1);
  assert.equal(JSON.stringify({ report, job }), before);

  const blocked = { feature: 'arrangement.timeline', impact: 'blocking', message: 'Unresolved tie.' };
  const mixed = playerNotices(job, { ...report, status: 'blocked', findings: [blocked] });
  assert.deepEqual(mixed.warnings.map(item => item.id), ['arrangement.timeline']);
  assert.equal(mixed.diagnosticCount, 1);
});

test('empty-track text cannot quiet unknown, blocked, omitted or ambiguous arrangements', () => {
  const row = { trackIndex: 0, name: 'CHORDS', status: 'skipped_empty', noteCount: 0, blockingFeatures: [] };
  const message = 'Skipped empty arrangement: CHORDS.';
  const job = { ...completed, warnings: [message] };
  for (const report of [null, findings([]), { ...findings([]), arrangements: [] },
    ...[{ name: 'Other' }, { status: 'blocked' }, { status: 'unknown' }, { status: 'score_ready' },
      { noteCount: 1 }, { noteCount: undefined }, { blockingFeatures: ['arrangement.timeline'] }]
      .map(change => ({ ...findings([]), arrangements: [{ ...row, ...change }] })),
    { ...findings([]), arrangements: [row, { ...row, trackIndex: 1, status: 'blocked' }] },
  ]) {
    const result = playerNotices(job, report);
    assert.equal(result.warnings[0].id, 'other');
    assert.equal(result.diagnosticCount, 0);
  }
  const report = { ...findings([]), arrangements: [row] };
  for (const warning of [message + ' Another issue.', 'Skipped empty arrangement: CHORDS', 'Skipped empty arrangement: Lead.']) {
    assert.equal(playerNotices({ ...job, warnings: [warning] }, report).warnings[0].id, 'other');
  }
  const omitted = playerNotices({ ...job, verification: { ...completed.verification,
    omissions: { omittedNotes: 4, excludedTracks: ['lead'] } } }, report);
  assert.deepEqual(omitted.warnings.map(item => item.id), ['high_frets']);
  assert.match(omitted.warnings[0].message, /4 high-fret or connected slide events/);
  assert.equal(omitted.diagnosticCount, 1);
});

test('supported and uncertain timing both preserve the separate recording pitch warning', () => {
  for (const timing of ['Timing is supported', 'Timing remains uncertain']) {
    const message = `${timing}, but the recording's pitch appears about 67 cents lower than the tab's tuning (100 cents is one semitone). Playing along may sound out of tune. No pitch correction was applied to the source tab or recording.`;
    const job = { ...completed, warnings: [message], verification: { ...completed.verification,
      timingAssessment: { version: 'local-recording-clock-v3', status: 'supported', supportedWindows: 10, windowCount: 10 } } };
    const original = JSON.stringify(job);
    const result = playerNotices(job, findings([]));
    assert.deepEqual(result.warnings.map(g => g.id), ['pitch']);
    assert.equal(result.warnings[0].message, message);
    assert.equal(JSON.stringify(job), original);
  }
});

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

test('lyric limits, remaining syllables and export failures share one visible group', () => {
  const warnings = [
    'Lyrics use the public player text limit; text beyond that limit remains in the saved source only.',
    '2530 lyric syllables follow the last available vocal note; retained in source only.',
    'Lyrics were not imported: Lyrics exceed the public player text limit; source retained..',
    'Unknown new problem',
  ];
  const notices = playerNotices({ warnings }, null);
  const group = notices.warnings.find(row => row.id === 'lyrics');
  assert.equal(group.title, 'Lyrics');
  assert.equal(group.occurrences, 3);
  assert.equal(group.examples.length, 2);
  assert.equal(notices.warnings.length, 2);
  assert.equal(notices.rawWarningCount, 4);
});

const boundaryPrefix = 'Some overfilled tab bars use Songsterr playback boundaries: ';
const boundaryLegacy = boundaryPrefix + 'overflowing durations are shortened and attacks outside their bar are omitted. Original tab retained.';
const boundaryRaw = kind => boundaryPrefix + ({
  clip: 'overflowing beat durations are shortened',
  omit: 'beats outside their bar are omitted',
  mixed: 'overflowing beat durations are shortened and beats outside their bar are omitted',
})[kind] + '. Original tab retained.';
const boundaryTail = 'Later bars stay in place; original notation is retained in the source.';
function boundaryFinding(action = 'clip', measure = 13, voice = 1, beat = 8) {
  const value = { trackId: '0', location: `parts/0/measures/${measure - 1}/voices/${voice - 1}/beats/${beat - 1}`,
    measure, voice, beat, action, duration: action === 'duration_cap' ? '8' : '1',
    usedDuration: action === 'omit' ? null : action === 'duration_cap' ? '4' : '1/2' };
  if (action !== 'duration_cap') value.start = action === 'omit' ? '4' : '7/2';
  return { feature: 'beat.measure_boundary', category: 'source_interpretation',
    impact: action === 'omit' ? 'gameplay_omission' : 'display_or_expression',
    workStatus: action === 'omit' ? 'gameplay_omission' : 'display_limitation',
    message: (action === 'omit' ? 'This beat starts outside its written bar and is skipped, as in Songsterr playback. '
      : 'This overflowing beat is bounded to its written bar, as in Songsterr playback. ') + boundaryTail,
    location: `${value.location}/${action}`, trackId: '0', value, valueTruncated: false, retained: 'original_source' };
}
const boundaryReport = rows => ({ version: 122, status: 'limitations', truncated: false,
  findingCount: rows.length, findings: rows,
  arrangements: [{ trackId: '0', name: 'Guitar', status: 'score_ready' }] });

test('verified clip-only boundaries show one truthful warning with written beat counts and readable locations', () => {
  const report = boundaryReport(Array.from({ length: 13 }, (_, i) => boundaryFinding('clip', 13 + i, i % 2 + 1)));
  for (const warning of [boundaryLegacy, boundaryRaw('clip')]) {
    const job = { ...completed, warnings: [warning] };
    const original = JSON.stringify({ job, report });
    const result = playerNotices(job, report);
    assert.equal(result.warnings.length, 1);
    const [group] = result.warnings;
    assert.equal(group.id, 'beat.measure_boundary');
    assert.equal(group.level, 'warning');
    assert.equal(group.title, 'Tab bar boundaries adjusted');
    assert.match(group.message, /^13 beat durations shortened to fit their written bars\./);
    assert.doesNotMatch(group.message, /omit|attack|note count/i);
    assert.equal(group.occurrences, 13);
    assert.equal(group.locations.length, 8);
    assert.equal(group.locations[0], 'Guitar · Measure 13 · voice 1 · beat 8');
    assert.equal(group.locations[1], 'Guitar · Measure 14 · voice 2 · beat 8');
    assert.equal(result.rawWarningCount, 1);
    assert.equal(result.diagnosticCount, 0);
    assert.equal(JSON.stringify({ job, report }), original);
  }
});

test('omitted and shortened beats remain warnings without claiming notes or attacks were removed', () => {
  for (const legacyOmit of [false, true]) {
    const omitted = boundaryFinding('omit');
    if (legacyOmit) omitted.message = 'This attack is outside its written bar and is skipped, as in Songsterr playback. ' + boundaryTail;
    // An omitted source beat may be a rest or a tied continuation. The receipt
    // deliberately makes no assertion about the number of attacks it contains.
    const rows = [omitted, boundaryFinding('omit', 14)];
    const result = playerNotices({ ...completed, warnings: [boundaryRaw('omit')] }, boundaryReport(rows));
    assert.equal(result.warnings.length, 1);
    assert.match(result.warnings[0].message, /^2 beats omitted because they start outside their written bars\./);
    assert.doesNotMatch(result.warnings[0].message, /shortened|attack|notes omitted/);
    assert.equal(result.warnings[0].occurrences, 2);
  }
  const result = playerNotices({ ...completed, warnings: [boundaryRaw('mixed')] },
    boundaryReport([boundaryFinding(), boundaryFinding('omit', 14)]));
  assert.equal(result.warnings.length, 1);
  assert.match(result.warnings[0].message, /^1 beat duration shortened to fit its written bar\. 1 beat omitted because it starts outside its written bar\./);
  assert.equal(result.warnings[0].occurrences, 2);
});

test('duration caps do not double-count a beat that is later clipped or omitted', () => {
  for (const subsequent of [null, 'clip', 'omit']) {
    const rows = [boundaryFinding('duration_cap')];
    if (subsequent) rows.push(boundaryFinding(subsequent));
    const result = playerNotices({ ...completed, warnings: [boundaryRaw(subsequent === 'omit' ? 'mixed' : 'clip')] }, boundaryReport(rows));
    assert.equal(result.warnings.length, 1);
    assert.equal(result.warnings[0].occurrences, 1);
    assert.equal(result.warnings[0].locations.length, 1);
    assert.match(result.warnings[0].message, subsequent === 'omit' ? /^1 beat omitted/ : /^1 beat duration shortened/);
    assert.doesNotMatch(result.warnings[0].message, subsequent === 'omit' ? /shortened/ : /omitted/);
  }
});

test('boundary locations use a track ID when the source name is missing or ambiguous', () => {
  const report = boundaryReport([boundaryFinding()]);
  for (const arrangements of [undefined, [], [{ trackId: '0', name: '' }],
    [{ trackId: '0', name: 'Guitar' }, { trackId: '0', name: 'Other guitar' }]]) {
    const result = playerNotices(completed, { ...report, arrangements });
    assert.equal(result.warnings[0].locations[0], 'Track 0 · Measure 13 · voice 1 · beat 8');
  }
});

test('boundary warnings are never consolidated from missing, truncated or inconsistent reports', () => {
  const report = boundaryReport([boundaryFinding()]);
  const job = { ...completed, warnings: [boundaryLegacy] };
  for (const changed of [null, boundaryReport([]), { ...report, truncated: true }, { ...report, truncated: undefined },
    { ...report, findingCount: 0 }, { ...report, findingCount: 2 }, { ...report, findingCount: '1' },
    { ...report, findingCount: undefined }, { ...report, version: undefined }, { ...report, version: '122' },
    { ...report, status: 'blocked' }]) {
    const result = playerNotices(job, changed);
    assert.ok(result.warnings.some(g => g.id === 'other' && g.message === boundaryLegacy));
    assert.ok(!result.warnings.some(g => g.title === 'Tab bar boundaries adjusted'));
  }
  assert.ok(playerNotices(job, report, { reportUnavailable: true }).warnings.some(g => g.id === 'other'));
});

test('only completed source-verified imports consolidate boundary warnings', () => {
  const report = boundaryReport([boundaryFinding()]);
  for (const job of [{ state: 'running', verification: { status: 'passed' } },
    ...['modified', 'not_checked', 'failed', undefined].map(status => ({ state: 'completed', verification: { status } }))]) {
    const result = playerNotices({ ...job, warnings: [boundaryLegacy] }, report);
    assert.ok(result.warnings.some(g => g.id === 'other'));
    assert.equal(result.warnings.find(g => g.id === 'beat.measure_boundary').message, report.findings[0].message);
  }
});

test('unknown or malformed boundary rows cannot quiet a raw warning or be hidden behind a valid subset', () => {
  const mutations = [
    f => { f.category = 'unknown_semantics'; }, f => { f.impact = 'blocking'; },
    f => { f.workStatus = 'unknown'; }, f => { f.retained = 'partial_source'; },
    f => { f.valueTruncated = true; }, f => { delete f.valueTruncated; },
    f => { f.message += ' Another effect is lost.'; }, f => { f.value = null; },
    f => { f.value = JSON.stringify(f.value); }, f => { f.value = []; },
    f => { f.value.action = 'future_boundary_rule'; }, f => { f.value.trackId = 0; },
    f => { f.trackId = 'different'; }, f => { f.location += '/other'; },
    f => { f.value.location = 'parts/0/measures/999/voices/0/beats/7'; },
    f => { f.value.measure = 0; }, f => { f.value.voice = '1'; }, f => { f.value.beat = 2.5; },
    f => { f.value.duration = 1; }, f => { f.value.duration = '1/0'; },
    f => { f.value.duration = '-1'; }, f => { f.value.duration = '0'; },
    f => { f.value.usedDuration = '1'; }, f => { f.value.usedDuration = '2'; },
    f => { f.value.usedDuration = '0'; }, f => { f.value.usedDuration = null; },
    f => { delete f.value.start; }, f => { f.value.start = NaN; },
    f => { f.value.futureMeaning = true; },
  ];
  for (const change of mutations) {
    const bad = boundaryFinding('clip', 14); change(bad);
    const report = boundaryReport([boundaryFinding(), bad]);
    const result = playerNotices({ ...completed, warnings: [boundaryLegacy] }, report);
    assert.ok(result.warnings.some(g => g.id === 'other'), change.toString());
    assert.ok(!result.warnings.some(g => g.title === 'Tab bar boundaries adjusted'), change.toString());
  }
  const omit = boundaryFinding('omit'); omit.value.usedDuration = '0';
  const cap = boundaryFinding('duration_cap'); cap.value.start = '0';
  for (const row of [omit, cap]) {
    assert.ok(playerNotices({ ...completed, warnings: [boundaryLegacy] }, boundaryReport([row])).warnings.some(g => g.id === 'other'));
  }
});

test('duplicate operations and contradictory final operations remain visible for review', () => {
  for (const rows of [[boundaryFinding(), boundaryFinding()],
    [boundaryFinding('clip'), boundaryFinding('omit')]]) {
    const result = playerNotices({ ...completed, warnings: [boundaryLegacy] }, boundaryReport(rows));
    assert.ok(result.warnings.some(g => g.id === 'other'));
    assert.equal(result.warnings.find(g => g.id === 'beat.measure_boundary').occurrences, 2);
  }
});

test('only the exact supported raw boundary summary is deduplicated and extra messages remain', () => {
  const report = boundaryReport([boundaryFinding()]);
  const unknown = 'Some tab beats use Songsterr playback boundaries. See import details for the recorded timing adjustments. Original tab retained.';
  const warnings = [boundaryRaw('clip'), boundaryLegacy, boundaryLegacy + ' Another issue.',
    boundaryRaw('omit'), boundaryRaw('mixed'), unknown,
    { message: boundaryLegacy, location: 'additional source detail' }, 'Unrelated warning'];
  const result = playerNotices({ ...completed, warnings }, report);
  const other = result.warnings.find(g => g.id === 'other');
  assert.equal(result.warnings.length, 2);
  assert.equal(other.occurrences, warnings.length - 2);
  assert.deepEqual([other.message, ...other.examples], warnings.slice(2).map(w => typeof w === 'string' ? w : w.message));
  assert.deepEqual(other.locations, ['additional source detail']);
  assert.equal(result.rawWarningCount, warnings.length);
});

test('boundary consolidation does not change other playing limitations or their severity', () => {
  const report = boundaryReport([boundaryFinding(), { feature: 'beat.vibrato', category: 'game_limitation',
    impact: 'display_or_expression', message: 'Beat vibrato is not reproduced.' }]);
  const result = playerNotices({ ...completed, warnings: [boundaryRaw('clip'), 'Another issue'], artwork: { status: 'unavailable' } }, report);
  assert.deepEqual(result.warnings.map(g => g.id).sort(), ['artwork', 'beat.measure_boundary', 'beat.vibrato', 'other']);
  assert.ok(result.warnings.every(g => g.level === 'warning'));
  for (const action of ['clip', 'omit', 'duration_cap']) assert.equal(classifyFinding(boundaryFinding(action)), 'warning');
});

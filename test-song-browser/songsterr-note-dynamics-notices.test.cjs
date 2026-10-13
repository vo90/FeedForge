'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { playerNotices, classifyFinding, prioritizeFindings } = require('../electron/song-browser/songsterr-notices.cjs');
const { CURRENT_PRESERVATION_CONTRACT: CURRENT } = require('../electron/song-browser/songsterr-evidence.cjs');

const completed = { state: 'completed', verification: { status: 'passed', timing: 'source_map' } };
const message = 'Per-note volume markings are retained in the source and written-note data. '
  + 'The game does not display or apply individual note loudness; backing audio, '
  + 'note timing and scoring are unchanged.';

function dynamic(index, extra = {}) {
  return { feature: 'note.velocity', value: ['ppp', 'pp', 'p', 'mp', 'mf', 'f', 'ff', 'fff'][index % 8],
    category: 'game_limitation', impact: 'display_or_expression', workStatus: 'display_limitation',
    retained: 'original_source', valueTruncated: false, message,
    arrangement: 'Lead Guitar', measure: index + 1, beat: 1, note: 2,
    location: `parts/0/measures/${index}/voices/0/beats/0/notes/1/velocity`, ...extra };
}

function report(findings) {
  return { version: CURRENT, status: 'limitations', findingCount: findings.length, truncated: false, findings };
}

test('one hundred per-note markings produce one bounded player warning', () => {
  const findings = Array.from({ length: 100 }, (_, index) => dynamic(index));
  const input = report(findings), original = JSON.stringify(input);
  const notices = playerNotices(completed, input);
  assert.equal(notices.warnings.length, 1);
  const [warning] = notices.warnings;
  assert.equal(warning.id, 'note.velocity');
  assert.equal(warning.title, 'Per-note volume markings are not applied');
  assert.equal(warning.message, message);
  assert.equal(warning.occurrences, 100);
  assert.equal(warning.locations.length, 8);
  assert.equal(warning.locations[0], 'Lead Guitar · Measure 1 · beat 1 · note 2');
  assert.equal(JSON.stringify(input), original, 'Presentation must retain the complete immutable report.');
});

test('explicit null, rest and excluded notes remain diagnostics', () => {
  const findings = [
    dynamic(0, { value: null, message: 'No note-level dynamic is specified.' }),
    dynamic(1, { message: 'The dynamic belongs to a rest slot.' }),
    dynamic(2, { arrangement: 'Excluded Piano', message: 'The dynamic belongs to an excluded arrangement.' }),
  ].map(finding => ({ ...finding, category: 'source_metadata', impact: 'source_retained' }));
  assert.ok(findings.every(finding => classifyFinding(finding) === 'diagnostic'));
  assert.deepEqual(playerNotices(completed, report(findings)).warnings, []);
});

test('retained dynamics do not hide active dynamics or unrelated player limitations', () => {
  const active = dynamic(0);
  const inactive = dynamic(1, { value: null, category: 'source_metadata', impact: 'source_retained' });
  const other = { feature: 'note.rightFingering', category: 'game_limitation', impact: 'display_or_expression',
    message: 'The authored picking-hand finger is not displayed.' };
  const notices = playerNotices(completed, report([inactive, active, other]));
  assert.deepEqual(notices.warnings.map(row => row.id).sort(), ['note.rightFingering', 'note.velocity']);
  assert.equal(notices.warnings.find(row => row.id === 'note.velocity').occurrences, 1);
});

test('malformed note volume remains a warning even with familiar feature or inactive value', () => {
  for (const value of [null, false, 0, '', [], {}]) {
    const invalid = dynamic(0, { value, category: 'source_structure', impact: 'blocking',
      message: 'Invalid Songsterr note dynamic marking.' });
    assert.equal(classifyFinding(invalid), 'warning');
    assert.equal(playerNotices(completed, report([invalid])).warnings[0].id, 'note.velocity');
  }
});

test('repeated dynamics cannot bury the next distinct issue in compatibility details', () => {
  const findings = Array.from({ length: 100 }, (_, index) => dynamic(index));
  const other = { feature: 'note.futureTechnique', category: 'unknown_semantics', impact: 'blocking',
    message: 'An unknown technique requires support.' };
  const prioritized = prioritizeFindings([...findings, other]);
  assert.equal(prioritized[0], other);
  assert.equal(prioritized.length, 101);
  assert.deepEqual(prioritized.slice(1), findings);
});

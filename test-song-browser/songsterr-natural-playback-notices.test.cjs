const test = require('node:test');
const assert = require('node:assert/strict');
const { playerNotices } = require('../electron/song-browser/songsterr-notices.cjs');

test('undefined natural harmonics create one warning; resolved shorthand stays diagnostic', () => {
  const finding = {feature: 'note.natural_harmonic_fallback', category: 'game_limitation',
    impact: 'display_or_expression', workStatus: 'display_limitation', value: 'natural',
    message: 'The unresolved harmonic instruction is not shown or scored as a harmonic.'};
  const report = {version: 125, status: 'limitations', truncated: false, findingCount: 3, findings: [
    {...finding, measure: 15, beat: 2}, {...finding, measure: 15, beat: 4},
    {feature: 'note.natural_harmonic_shorthand', category: 'source_interpretation',
      impact: 'source_retained', workStatus: 'source_retained', value: 'natural', measure: 31, beat: 4}]};
  const notices = playerNotices({state: 'completed', verification: {status: 'passed'}}, report);
  assert.equal(notices.warnings.length, 1);
  assert.equal(notices.warnings[0].title, 'Undefined harmonics use ordinary notes');
  assert.equal(notices.warnings[0].occurrences, 2);
  assert.equal(notices.warnings[0].locations.length, 2);
  assert.equal(notices.diagnosticCount, 1);
});

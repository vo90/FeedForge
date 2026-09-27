'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const Module = require('node:module');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const esbuild = require('esbuild');

const filename = path.join(__dirname, '../ui/src/song-browser/SongsterrBrowser.jsx');
const built = esbuild.buildSync({ entryPoints: [filename], bundle: true, platform: 'node', format: 'cjs', write: false,
  external: ['react', 'lucide-react'], loader: { '.css': 'empty' } });
const compiled = new Module(filename, module); compiled.filename = filename;
compiled.paths = Module._nodeModulePaths(path.dirname(filename)); compiled._compile(built.outputFiles[0].text, filename);
const { SongsterrJob, default: SongsterrBrowser } = compiled.exports;
const base = { id: 'job-songsterr-1', songId: '564073', title: 'Woodland Rites', artist: 'Green Lung', state: 'queued', canCancel: true, canRetry: false };
const render = (Component, props) => renderToStaticMarkup(React.createElement(Component, props));
const job = (change) => render(SongsterrJob, { job: { ...base, ...change }, api: {}, action() {}, busy: false });
test('completed partial charts identify omitted gameplay targets, not only display limitations', () => {
  const html = job({ state: 'completed', compatibility: { status: 'limitations' },
    verification: { omissions: { omittedNotes: 14, excludedTracks: ['lead'] } } });
  assert.match(html, /Ready with omitted notes/);
  assert.match(html, /14 high-fret or connected slide events omitted from display and scoring/);
  assert.match(html, /1 arrangements had no supported notes remaining/);
  assert.doesNotMatch(html, /Ready with limitations/);
});
const buttonText = (html) => [...html.matchAll(/<button\b[^>]*>([\s\S]*?)<\/button>/g)].map((match) => match[1].replace(/<[^>]*>/g, '').trim());

test('opening repair, acoustic evidence and preparation are distinguished', () => {
  const html = job({ state: 'completed', verification: { status: 'passed', timing: 'source_map_repaired',
    timingAssessment: { windowCount: 10, supportedWindows: 8, everyNoteVerified: false }, preparationSeconds: 1.83,
    endingSilenceSeconds: .43 } });
  assert.match(html, /opening corrected against the recording/);
  assert.match(html, /8 of 10 passages supported/);
  assert.match(html, /Individual notes may remain uncertain/);
  assert.match(html, /Preparation time added: 1.83 seconds/);
  assert.match(html, /Ending silence added: 0.43 seconds to preserve the final note tails/);
  assert.doesNotMatch(html, /Timing: automatic estimate/);
});

test('retry countdown retains Cancel and delays replacement-audio prompts until exhaustion', () => {
  const html = job({ state: 'retry_wait', retry: { nextAt: Date.now() + 5000, attempt: 2, maxAttempts: 3 } });
  assert.match(html, /attempt 2 of 3/); assert.match(html, /Retrying in/);
  assert.ok(buttonText(html).includes('Cancel')); assert.ok(!buttonText(html).includes('Choose audio file'));
  const exhausted = job({ state: 'needs_audio', canRetry: true, canRetryRecording: true });
  assert.ok(buttonText(exhausted).includes('Retry same recording'));
  assert.ok(buttonText(exhausted).includes('Choose audio file'));
});

test('completed imports distinguish limitations without implying a failed or fully supported chart', () => {
  assert.match(job({ state: 'completed', compatibility: { status: 'limitations' } }), /Ready with limitations/);
  assert.doesNotMatch(job({ state: 'failed', compatibility: { status: 'limitations' } }), /Ready with limitations/);
  const html = render(compiled.exports.CompatibilityDetails, { report: { findingCount: 1, findings: [
    { feature: '<script>future</script>', category: 'converter_gap', arrangement: 'Lead', measure: 5, beat: 2, value: '<img>', message: 'Not rendered.' }
  ] } });
  assert.match(html, /Measure 5/); assert.match(html, /beat 2/);
  assert.match(html, /&lt;script&gt;/); assert.doesNotMatch(html, /<script|<img/);
});

test('conversion fidelity, audio timing and artwork have separate honest outcomes', () => {
  const checked = job({ state: 'completed', verification: { status: 'passed', timing: 'estimated' }, artwork: { status: 'unavailable' } });
  assert.match(checked, /checked against the source tab/);
  assert.match(checked, /Timing: automatic estimate/);
  assert.match(checked, /Album cover unavailable or uncertain/);
  const modified = job({ state: 'completed', verification: { status: 'modified' } });
  assert.match(modified, /changed since conversion/);
  assert.doesNotMatch(modified, /checked against the source tab/);
  assert.doesNotMatch(job({ state: 'completed' }), /checked against the source tab/);
  assert.doesNotMatch(checked + modified, /library doctor|repair.*required/i);
});

test('recorded sustain adjustments are visible without claiming exact source durations', () => {
  const html = job({ state: 'completed', verification: { status: 'passed', adjustments: { terminalSustains: 5 } } });
  assert.match(html, /5 final sustains shortened to the audio ending/);
  assert.match(html, /Original durations are saved with the tab/);
  assert.match(html, /source tab and recorded sustain adjustments/);
  assert.doesNotMatch(job({ state: 'completed', verification: { status: 'passed' } }), /sustains shortened/);
});

test('ending omissions disclose the count, audio check and retained original', () => {
  const verification = { status: 'passed', adjustments: { omittedEndingNotes: 3 } };
  const html = job({ state: 'completed', verification });
  assert.match(html, /3 ending notes omitted at or after the audio ending/);
  assert.match(html, /Earlier timing passed the audio check/);
  assert.match(html, /original tab and omission details are saved/);
  assert.match(html, /recorded ending adjustments/);
  assert.doesNotMatch(job({ state: 'failed', verification }), /Earlier timing passed/);
  assert.doesNotMatch(job({ state: 'completed', verification: { ...verification, status: 'modified' } }), /Earlier timing passed/);
});

test('directional slide cutoffs disclose the adjusted visual interval only after verification', () => {
  const verification = { status: 'passed', adjustments: { terminalSustains: 1, terminalSlideOuts: 1 } };
  assert.match(job({ state: 'completed', verification }), /1 directional slide-out ends with the audio after a timing check/);
  assert.match(job({ state: 'completed', verification }), /original slide intervals and cutoff details are saved/);
  assert.doesNotMatch(job({ state: 'failed', verification }), /directional slide-out ends/);
  assert.doesNotMatch(job({ state: 'completed', verification: { ...verification, status: 'modified' } }), /directional slide-out ends/);
});

test('arrangement diagnostics do not imply a completed or partial import', () => {
  const html = render(compiled.exports.CompatibilityDetails, { report: { findingCount: 0, findings: [],
    arrangements: [{ trackIndex: 3, name: 'Bass', status: 'score_ready' }, { trackIndex: 8, name: 'Lead', status: 'blocked', message: 'Unresolved tie.' }] } });
  assert.match(html, /Score check passed/);
  assert.match(html, /Every requested arrangement is checked/);
  assert.match(html, /Unsupported high-fret events can be omitted with a recorded limitation/);
  assert.match(html, /audio alignment and final file verification/);
  assert.match(html, /Unresolved tie/);
  assert.doesNotMatch(html, /FeedPak ready|Download usable parts/);
});

test('missing or mismatched audio offers exactly the supported link/file replacement flow', () => {
  for (const state of ['needs_audio', 'alignment_failed']) {
    const html = job({ state, canCancel: false, canRetry: true, error: 'Provide a matching recording.' });
    assert.match(html, /Choose audio file/); assert.match(html, /paste an audio or YouTube link/);
    assert.match(html, /Use audio &amp; continue/); assert.match(html, /type="url" required=""/);
    assert.match(html, /Timing is checked before the FeedPak is saved/);
    assert.doesNotMatch(html, /FeedPak ready|Show file|Continue after signing in/);
    assert.equal(buttonText(html).some((label) => /editor|timing editor|export|choose tracks|select tracks/i.test(label)), false);
  }
});

test('only a cached job awaiting automatic audio discovery offers Retry audio detection', () => {
  const missing = job({ state: 'needs_audio', canRetry: true, canRetryAudio: true });
  assert.match(missing, />Retry audio detection<\/button>/);
  assert.match(missing, /Choose audio file/);
  assert.doesNotMatch(missing, />Retry import<\/button>/);
  for (const state of ['alignment_failed', 'audio', 'failed', 'cancelled', 'completed']) {
    assert.doesNotMatch(job({ state, canRetry: true, canRetryAudio: true }), /Retry audio detection/);
  }
  assert.doesNotMatch(job({ state: 'needs_audio', canRetry: true, canRetryAudio: false }), /Retry audio detection/);
  const busy = render(SongsterrJob, { job: { ...base, state: 'needs_audio', canRetryAudio: true }, api: {}, action() {}, busy: true });
  assert.match(busy, /<button[^>]*disabled=""[^>]*>Retry audio detection<\/button>/);
});

test('login/account fallback is optional and explains the unpublished copy without manual export controls', () => {
  const ordinary = job({ state: 'downloading' });
  assert.doesNotMatch(ordinary, /Sign in to Songsterr|Continue after signing in/);
  for (const state of [{ state: 'needs_login', canRetry: true }, { state: 'failed', canUseAccount: true, canRetry: true }]) {
    const html = job(state);
    assert.match(html, /Sign in to Songsterr/); assert.match(html, /Continue after signing in/);
    assert.match(html, /creates an unpublished copy/);
    assert.equal(buttonText(html).some((label) => /export|make a copy|create copy|publish|editor/i.test(label)), false);
  }
});

test('failed alignment can retry Songsterr synchronization with the existing recording', () => {
  const html = job({ state: 'alignment_failed', canRetry: true });
  assert.match(html, />Retry synchronization<\/button>/);
  assert.match(html, /Choose audio file/);
  assert.doesNotMatch(html, /Retry audio detection/);
  for (const state of ['needs_audio', 'aligning', 'completed', 'cancelled']) {
    assert.doesNotMatch(job({ state, canRetry: true }), /Retry synchronization/);
  }
  const busy = render(SongsterrJob, { job: { ...base, state: 'alignment_failed', canRetry: true }, api: {}, action() {}, busy: true });
  assert.match(busy, /<button[^>]*disabled=""[^>]*>Retry synchronization<\/button>/);
});

test('active acquisition/alignment shows progress and cancellation, never premature ready or audio prompts', () => {
  for (const state of ['resolving', 'downloading', 'audio', 'aligning', 'converting', 'validating', 'saving']) {
    const html = job({ state, message: 'Working on this song.' });
    assert.match(html, /<progress/); assert.match(html, />Cancel<\/button>/);
    assert.doesNotMatch(html, /FeedPak ready|Show file|Choose audio file|type="url"/);
  }
});

test('history uses original metadata, approved revision and actual saved-file availability', () => {
  const available = job({ state: 'completed', canCancel: false, outputAvailable: true, revisionId: '2585330', message: 'FeedPak ready.' });
  assert.match(available, /Woodland Rites/); assert.match(available, /Green Lung/); assert.match(available, /Approved revision 2585330/);
  assert.match(available, /Show file/); assert.doesNotMatch(available, /Misc Covers|>Cancel<|Retry import/);
  const missing = job({ state: 'completed', canCancel: false, outputAvailable: false });
  assert.match(missing, /saved file has been moved or removed/); assert.doesNotMatch(missing, /Show file/);
});

test('source panel uses shared Settings and offers only source-supported sorting with no manual conversion wizard', () => {
  const html = render(SongsterrBrowser, { api: {}, outputSettings: { outputDir: 'C:\\Song Library', outputLayout: 'artist', nameTemplate: '{artist} - {title}' } });
  assert.match(html, /C:\\Song Library/); assert.match(html, /Uses the filename and folder layout from Settings/);
  assert.match(html, /Open Settings/); assert.match(html, /Sort these results/);
  assert.match(html, /Song title/); assert.match(html, /Artist/); assert.match(html, /experimental/);
  assert.doesNotMatch(html, /Choose folder|Most downloads|Preferred creator|Backing track|Instrument and string requirements/);
  assert.equal(buttonText(html).some((label) => /export|editor|choose tracks|select tracks|timing/i.test(label)), false);
});

test('remote song metadata is escaped and cannot become markup in job history', () => {
  const html = job({ title: '<script>alert(1)</script>', artist: '<img src=x onerror=alert(2)>', error: '<b>message</b>' });
  assert.match(html, /&lt;script&gt;/); assert.match(html, /&lt;img/); assert.match(html, /&lt;b&gt;message/);
  assert.doesNotMatch(html, /<script|<img|<b>message/);
});

const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const Module = require('node:module');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const esbuild = require('esbuild');

// Render the actual presentational React cards using installed build tooling.
// No DOM, Electron process, app profile or website is involved in this test.
const filename = path.join(__dirname, '../ui/src/song-browser/SongBrowser.jsx');
const built = esbuild.buildSync({ entryPoints: [filename], bundle: true, platform: 'node', format: 'cjs',
  write: false, external: ['react', 'lucide-react'], loader: { '.css': 'empty' } });
const compiled = new Module(filename, module);
compiled.filename = filename;
compiled.paths = Module._nodeModulePaths(path.dirname(filename));
compiled._compile(built.outputFiles[0].text, filename);
const { JobCard, ResultCard } = compiled.exports;
const song = { id: '42', title: 'One', artist: 'Metallica', host: 'google-drive', supported: true };
const completed = { ...song, id: 'job-42', chartId: '42', state: 'completed', message: 'FeedPak ready.' };
const render = (Component, props) => renderToStaticMarkup(React.createElement(Component, props));

test('missing output is shown honestly in history and offers a fresh download in search', () => {
  const job = { ...completed, outputAvailable: false, inOutputDir: false };
  const history = render(JobCard, { job, busy: false });
  assert.match(history, /File unavailable/);
  assert.match(history, /moved or removed/);
  assert.doesNotMatch(history, /FeedPak ready|Show file/);
  const result = render(ResultCard, { job, song, canDownload: true, busy: false });
  assert.match(result, /Download &amp; convert/);
  assert.doesNotMatch(result, /FeedPak ready|disabled=""/);
});

test('a saved output in a previous folder is still revealable while the new folder can receive it', () => {
  const job = { ...completed, outputAvailable: true, inOutputDir: false };
  assert.match(render(JobCard, { job, busy: false }), /Show file/);
  assert.match(render(ResultCard, { job, song, canDownload: true, busy: false }), /Download &amp; convert/);
});

test('an available output in the selected folder keeps its ready action', () => {
  const job = { ...completed, outputAvailable: true, inOutputDir: true };
  assert.match(render(JobCard, { job, busy: false }), /FeedPak ready/);
  const result = render(ResultCard, { job, song, canDownload: true, busy: false });
  assert.match(result, /FeedPak ready/);
  assert.doesNotMatch(result, /Download &amp; convert|File unavailable/);
});


test('completed output access remains while another file can be explicitly reviewed', () => {
  const result = render(ResultCard, { song, job: completed, canDownload: true, busy: false, onReviewAnother: () => {} });
  assert.match(result, /FeedPak ready/); assert.match(result, /Review another file\/version/);
});

test('file choices display unknown evidence and identify unverified hints honestly', () => {
  const result = render(JobCard, { job: { ...song, state: 'needs_attention', fileCandidates: [{ id: 'a', label: 'One_live_no_guitar_v2_p.psarc', platform: 'pc', versionHint: 'v2', editionHint: 'live', backingHint: 'no-guitar', sizeBytes: null }] }, busy: false });
  assert.match(result, /PC/); assert.match(result, /Size unknown/); assert.match(result, /Version hint v2/);
  assert.match(result, /Edition hint: live/); assert.match(result, /no-guitar \(unverified\)/);
});

test('cached attention offers an explicit requirement relaxation without weakening normal retry', () => {
  const result = render(JobCard, { job: { ...song, state: 'parked', hasCachedInput: true, canRetry: true, selection: { backingStrict: true } }, busy: false, onRelax: () => {} });
  assert.match(result, /Retry conversion/); assert.match(result, /Relax requirements and retry cached file/);
});


test('old recipe output remains accessible while offering a fresh conversion', () => {
  const result = render(ResultCard, { song, job: { ...completed, reuseCompatible: false }, canDownload: true, busy: false, onReviewAnother: () => {} });
  assert.match(result, /Show saved FeedPak/); assert.match(result, /Download &amp; convert/); assert.match(result, /older or unknown conversion recipe/);
  assert.doesNotMatch(result, /> FeedPak ready/);
});

const batchFilename = path.join(__dirname, '../ui/src/song-browser/BatchPanel.jsx');
const batchBuilt = esbuild.buildSync({ entryPoints: [batchFilename], bundle: true, platform: 'node', format: 'cjs', write: false, external: ['react'] });
const batchCompiled = new Module(batchFilename, module); batchCompiled.filename = batchFilename; batchCompiled.paths = Module._nodeModulePaths(path.dirname(batchFilename)); batchCompiled._compile(batchBuilt.outputFiles[0].text, batchFilename);
const { BatchPanel } = batchCompiled.exports;
const baseBatch = { id: 'batch-1', state: 'draft', selectedIds: ['1'], charts: [{ id: '1', title: 'One', creator: 'Creator', tuning: 'E Standard' }], outputDir: '/isolated-output', preferences: { requiredParts: [], tuning: '', backingTrack: 'full', backingStrict: false, instrumentRequirements: [], ranking: 'downloads', preferredCreators: ['Creator'] }, groups: [{ key: 'one', title: 'One', artist: 'Metallica', options: [{ id: '1', parts: ['lead'], eligible: true, reasons: [] }], recommendedIds: ['1'] }], suggestions: [], unresolvedCount: 0, complete: true, items: [], availability: { 1: { status: 'available', reason: 'Verified saved output.' } }, plannedCounts: { available: 1, downloads: 0 } };

test('batch review renders imported decisions, creator ordering and explicit variant review', () => {
  const result = render(BatchPanel, { batches: [baseBatch], action: () => {} });
  assert.match(result, /0 planned downloads/); assert.match(result, /1 already available/); assert.match(result, /Verified saved output/);
  assert.match(result, /Draft preferred creators/); assert.match(result, /Review another file\/version instead of keeping/); assert.match(result, /Prefer full backing/);
});

test('batch activity distinguishes user skip from availability and exposes per-item controls', () => {
  const result = render(BatchPanel, { batches: [{ ...baseBatch, state: 'running', counts: { available: 1, userSkipped: 1, pending: 1 }, items: [
    { id: 'pending', chartId: '1', state: 'pending' }, { id: 'skipped', chartId: '1', state: 'skipped', outcome: { skipKind: 'user', message: 'Skipped by you.' } },
  ] }], action: () => {} });
  assert.match(result, /1 already available/); assert.match(result, /1 skipped by you/); assert.match(result, /Skip song/); assert.match(result, /Retry song/);
});


test('shared suitability decides ready state independently of an existing completed job', () => {
  for (const code of ['different_revision', 'different_file', 'insufficient_coverage', 'unknown_recipe']) {
    const result = render(ResultCard, { song, job: completed, canDownload: true, busy: false, suitability: { reusable: false, code, reason: 'The saved file does not satisfy this request.' } });
    assert.match(result, /Download &amp; convert/); assert.match(result, /Show saved FeedPak/); assert.doesNotMatch(result, /> FeedPak ready/);
  }
  const verified = render(ResultCard, { song, job: completed, canDownload: true, suitability: { reusable: true } });
  assert.match(verified, /FeedPak ready/);
  const pending = render(ResultCard, { song, job: completed, canDownload: true, suitability: { reusable: false, pending: true } });
  assert.match(pending, /Checking saved output/); assert.match(pending, /Show saved FeedPak/);
});

test('late completed-result assessments cannot override newer requirements or inspect off-page work', async () => {
  const { createResultAssessmentSession } = await import('../ui/src/song-browser/result-assessment-session.mjs');
  let finishOld;
  const old = new Promise((resolve) => { finishOld = resolve; });
  const calls = [];
  const session = createResultAssessmentSession({ assessResult: async (request) => { calls.push(request); return request.selection.backingStrict ? { reusable: false, reason: 'Unknown strict backing.' } : old; } }, { delayMs: 0 });
  const first = session.assess([{ id: '1' }, { id: '2' }], { backingStrict: false }, 'old');
  const latest = session.assess([{ id: '1' }], { backingStrict: true }, 'new');
  await latest; finishOld({ reusable: true }); await first;
  assert.equal(session.getSnapshot().scope, 'new');
  assert.equal(session.getSnapshot().results['1'].reusable, false);
  assert.deepEqual(calls.map((call) => call.id), ['1', '1'], 'cancelled generation never assesses the next chart');
  session.cancel();
});

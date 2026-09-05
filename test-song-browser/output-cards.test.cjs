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

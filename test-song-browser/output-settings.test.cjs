'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { normalizeOutputSettings, planSongOutput } = require('../electron/song-browser/output-settings.cjs');

function fixture(t) {
  const base = fs.realpathSync(os.tmpdir());
  const root = fs.mkdtempSync(path.join(base, 'feedforge-output-settings-'));
  const directory = path.join(root, 'job');
  const outputDir = path.join(root, 'library');
  fs.mkdirSync(directory); fs.mkdirSync(outputDir);
  const inputPath = path.join(root, 'cache-identity.psarc');
  fs.writeFileSync(inputPath, 'local PSARC fixture');
  t.after(() => {
    assert.equal(path.dirname(fs.realpathSync(root)), base);
    assert.match(path.basename(root), /^feedforge-output-settings-/);
    fs.rmSync(root, { recursive: true, force: true });
  });
  return { root, directory, outputDir, inputPath, sourceFilename: 'Real_Song_v2_p.psarc' };
}

function runner(f, inspect = () => {}, change = (result) => result) {
  return async (args) => {
    assert.equal(args[0], '--plan-conversion-file'); assert.equal(args.length, 2);
    assert.equal(path.dirname(path.dirname(args[1])), f.directory);
    const request = JSON.parse(fs.readFileSync(args[1], 'utf8'));
    assert.equal(request.items.length, 1);
    assert.equal(path.basename(request.items[0].inputPath), f.sourceFilename);
    assert.deepEqual(fs.readFileSync(request.items[0].inputPath), fs.readFileSync(f.inputPath));
    assert.equal(request.items[0].sourceRoot, path.dirname(request.items[0].inputPath));
    assert.equal(request.outputDir, f.outputDir);
    assert.equal(request.overwrite, false);
    inspect(request);
    const filename = path.basename(f.sourceFilename, '.psarc') + '.feedpak';
    const target = request.outputLayout === 'artist' ? path.join(f.outputDir, 'Artist', filename) : path.join(f.outputDir, filename);
    const result = { ok: true, items: [{ ok: true, inputPath: request.items[0].inputPath,
      sourceSize: fs.statSync(request.items[0].inputPath).size, outputs: [{ path: target }] }] };
    return { code: 0, stdout: JSON.stringify(change(result, request)) };
  };
}

test('output settings preserve the normal Source filename default and supported template fields', () => {
  assert.deepEqual(normalizeOutputSettings(), { outputLayout: 'flat', nameTemplate: '{source}' });
  assert.deepEqual(normalizeOutputSettings({ outputLayout: 'preserve', nameTemplate: '', outputDir: 'ignored', backingTrack: 'ignored' }), { outputLayout: 'preserve', nameTemplate: '{source}' });
  for (const outputLayout of ['flat', 'preserve', 'artist']) {
    const settings = { outputLayout, nameTemplate: '{ARTIST} - {title} - {album} - {year} - {parts} - {source}' };
    assert.deepEqual(normalizeOutputSettings(settings), settings);
  }
  for (const value of [null, [], { outputLayout: 'unknown' }, { outputLayout: '../artist' },
    { nameTemplate: '{path}' }, { nameTemplate: '{artist.name}' }, { nameTemplate: '\u0000' }, { nameTemplate: 'x'.repeat(513) }, { nameTemplate: 7 }]) {
    assert.throws(() => normalizeOutputSettings(value));
  }
});

test('planning retains the downloaded source filename and does not create a library output', async (t) => {
  const f = fixture(t);
  const before = fs.readFileSync(f.inputPath);
  for (const outputLayout of ['flat', 'preserve', 'artist']) {
    const plan = await planSongOutput({ ...f, settings: { outputLayout, nameTemplate: '{source}' }, runConverter: runner(f, (request) => {
      assert.equal(request.outputLayout, outputLayout); assert.equal(request.nameTemplate, '{source}');
    }) });
    assert.equal(plan.relativePath, outputLayout === 'artist' ? path.join('Artist', 'Real_Song_v2_p.feedpak') : 'Real_Song_v2_p.feedpak');
    assert.deepEqual(fs.readdirSync(f.outputDir), []);
  }
  assert.deepEqual(fs.readFileSync(f.inputPath), before);
});

test('original filename validation rejects paths and Windows special names before invoking the converter', async (t) => {
  const f = fixture(t); let calls = 0;
  for (const sourceFilename of ['../outside.psarc', '..\\outside.psarc', 'C:\\outside.psarc', '/outside.psarc', 'CON.psarc', 'song:stream.psarc', 'song.psarc ', 'song.feedpak', '']) {
    await assert.rejects(planSongOutput({ ...f, sourceFilename, runConverter: async () => { calls++; } }), /original PSARC filename/);
  }
  assert.equal(calls, 0); assert.deepEqual(fs.readdirSync(f.directory), []);
});

test('planner errors remain visible and invalid or multiple outputs cannot escape publication boundaries', async (t) => {
  const f = fixture(t);
  const unsafe = [path.join(f.root, 'outside.feedpak'), path.join(f.outputDir, 'unexpected', 'song.feedpak'),
    path.join(f.outputDir, 'song.txt'), path.join(f.outputDir, 'song:stream.feedpak'), 'relative.feedpak'];
  for (const target of unsafe) {
    await assert.rejects(planSongOutput({ ...f, runConverter: runner(f, undefined, (result) => {
      result.items[0].outputs[0].path = target; return result;
    }) }), /output planner/);
  }
  for (const change of [
    (result) => { result.items[0].outputs.push(result.items[0].outputs[0]); return result; },
    (result) => { result.items[0].sourceSize++; return result; },
    (result) => { result.items[0].inputPath = f.inputPath; return result; },
  ]) await assert.rejects(planSongOutput({ ...f, runConverter: runner(f, undefined, change) }), /one song/);
  await assert.rejects(planSongOutput({ ...f, runConverter: runner(f, undefined, () => ({ ok: true, items: [{ ok: false, error: 'Source metadata is missing: {album}.' }] })) }), /metadata is missing/);
  for (const response of [{ code: 1, stdout: '{}' }, { code: 0, stdout: 'invalid JSON' }, { code: 0, stdout: JSON.stringify({ ok: true, items: [] }) }]) {
    await assert.rejects(planSongOutput({ ...f, runConverter: async () => response }), /could not plan/);
  }
  const cancellation = Object.assign(new Error('Cancelled.'), { name: 'AbortError' });
  await assert.rejects(planSongOutput({ ...f, runConverter: async () => { throw cancellation; } }), (error) => error === cancellation);
  assert.deepEqual(fs.readdirSync(f.outputDir), []);
});

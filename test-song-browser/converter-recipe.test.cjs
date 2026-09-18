'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { execFile } = require('node:child_process');
const { promisify } = require('node:util');
const { converterRecipe } = require('../electron/song-browser/converter-recipe.cjs');
const run = promisify(execFile);

async function freshProcessRecipe(request) {
  const script = 'require(process.argv[1]).converterRecipe(JSON.parse(process.argv[2])).then(value => process.stdout.write(JSON.stringify(value))).catch(error => { process.stderr.write(error.message); process.exitCode = 1; });';
  const result = await run(process.execPath, ['-e', script, require.resolve('../electron/song-browser/converter-recipe.cjs'), JSON.stringify(request)],
    { windowsHide: true, timeout: 5000, maxBuffer: 8192 });
  return JSON.parse(result.stdout);
}

function fixture(t) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'feedforge-recipe-'));
  const write = (relative, contents) => {
    const filename = path.join(directory, relative);
    fs.mkdirSync(path.dirname(filename), { recursive: true });
    fs.writeFileSync(filename, contents);
    return filename;
  };
  t.after(() => {
    assert.equal(path.dirname(directory), path.resolve(os.tmpdir()));
    assert.match(path.basename(directory), /^feedforge-recipe-/);
    fs.rmSync(directory, { recursive: true, force: true });
  });
  return { directory, write };
}

test('frozen recipe identifies nested payload contents and stays stable when moved unchanged', async (t) => {
  const f = fixture(t), command = f.write('frozen-a/feedforge.exe', 'synthetic exe bytes');
  f.write('frozen-a/_internal/library.dll', 'synthetic dll bytes');
  const data = f.write('frozen-a/_internal/converter/data/table.bin', Buffer.from([1, 2, 3, 4]));
  const first = await converterRecipe({ command, version: '1.2.3' });
  assert.match(first.build, /^[a-f0-9]{64}$/);
  assert.match(first.identity, /^[a-f0-9]{64}$/);
  assert.deepEqual(first.options, { format: 'feedpak', stemSeparation: false, pipeline: 1 });
  fs.cpSync(path.dirname(command), path.join(f.directory, 'frozen-b'), { recursive: true });
  const moved = await converterRecipe({ command: path.join(f.directory, 'frozen-b', 'feedforge.exe'), version: '1.2.3' });
  assert.equal(moved.identity, first.identity);
  assert.equal((await freshProcessRecipe({ command, version: '1.2.3' })).identity, first.identity, 'Frozen identity survives a process restart');
  assert.doesNotMatch(JSON.stringify(first), /feedforge-recipe-|frozen-a|frozen-b|[A-Za-z]:\\/);
  fs.writeFileSync(data, Buffer.from([1, 2, 3, 5]));
  const changed = await converterRecipe({ command, version: '1.2.3' });
  assert.notEqual(changed.identity, first.identity, 'A same-length nested binary content change alters identity');
  assert.equal((await converterRecipe({ command, version: '1.2.3' })).identity, changed.identity);
  assert.notEqual((await converterRecipe({ command, version: '1.2.4' })).identity, changed.identity);
});

test('frozen recipe includes auxiliary files and refuses payload links', async (t) => {
  const f = fixture(t), command = f.write('frozen/feedforge.exe', 'synthetic exe');
  const first = await converterRecipe({ command, version: '1' });
  f.write('frozen/_internal/config/resource.dat', 'output-affecting resource');
  assert.notEqual((await converterRecipe({ command, version: '1' })).identity, first.identity);
  const target = path.join(f.directory, 'outside-payload'); fs.mkdirSync(target);
  fs.writeFileSync(path.join(target, 'library.dll'), 'owned test target');
  try { fs.symlinkSync(target, path.join(path.dirname(command), 'linked-library'), process.platform === 'win32' ? 'junction' : 'dir'); }
  catch (error) { if (['EPERM', 'EACCES'].includes(error.code)) { t.skip('Creating an owned test link requires unavailable local permission.'); return; } throw error; }
  await assert.rejects(converterRecipe({ command, version: '1' }), /unsupported link|unsupported file/);
  assert.equal(fs.readFileSync(path.join(target, 'library.dll'), 'utf8'), 'owned test target');
});

test('development recipe changes with source binaries, Python bytes and project configuration', async (t) => {
  const f = fixture(t), command = f.write('runtime/python.exe', 'synthetic python runtime');
  const cwd = path.join(f.directory, 'project');
  f.write('project/src/feedback_converter/converter.py', 'OUTPUT = 1');
  f.write('project/src/feedback_converter/data/config.json', '{"mode":1}');
  const binary = f.write('project/src/feedback_converter/data/table.bin', Buffer.from([0, 1, 2, 3]));
  const config = f.write('project/pyproject.toml', '[project]\nversion = "1"\n');
  const request = { command, prefix: ['-m', 'feedback_converter'], cwd, version: '1' };
  const first = await converterRecipe(request);
  assert.equal((await converterRecipe(request)).identity, first.identity, 'The same development session is stable');
  fs.writeFileSync(binary, Buffer.from([3, 2, 1, 0]));
  const changedBinary = await converterRecipe(request);
  assert.notEqual(changedBinary.identity, first.identity, 'Packaged source data is part of the development recipe');
  fs.writeFileSync(command, 'different python runtime');
  const changedRuntime = await converterRecipe(request);
  assert.notEqual(changedRuntime.identity, changedBinary.identity);
  fs.writeFileSync(config, '[project]\nversion = "2"\n');
  const changedConfig = await converterRecipe(request);
  assert.notEqual(changedConfig.identity, changedRuntime.identity);
  f.write('project/src/feedback_converter/converter.py', 'OUTPUT = 2');
  const changedSource = await converterRecipe(request);
  assert.notEqual(changedSource.identity, changedConfig.identity);
  assert.notEqual((await freshProcessRecipe(request)).identity, changedSource.identity, 'A fresh unbundled session cannot claim unchanged external dependencies');
  assert.doesNotMatch(JSON.stringify(changedSource), /feedforge-recipe-|python\.exe|[A-Za-z]:\\/);
});

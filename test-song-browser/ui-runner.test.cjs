'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');
const { options, inside, inventory, prepare } = require('./ui-runner.cjs');

const checkout = fs.realpathSync.native(path.resolve(__dirname, '..'));
const tempParent = fs.realpathSync.native(os.tmpdir());
const nonexistentElectron = path.join(tempParent, 'feedforge-ui-guard-no-electron', 'electron.exe');
const argsFor = (runtime) => ['--electron', nonexistentElectron, '--runtime', runtime];

function fixture(t) {
  const directory = fs.mkdtempSync(path.join(tempParent, 'feedforge-ui-guard-'));
  t.after(() => {
    // Only the new directory owned by this test may be recursively removed.
    assert.equal(path.dirname(directory), tempParent);
    assert.match(path.basename(directory), /^feedforge-ui-guard-/);
    assert.equal(fs.lstatSync(directory).isSymbolicLink(), false);
    assert.equal(fs.realpathSync.native(directory), directory);
    fs.rmSync(directory, { recursive: true, force: true });
  });
  return directory;
}

test('UI runner defaults to a read-only plan and preserves explicit run arguments', () => {
  const runtime = path.join(tempParent, 'fresh-ui-runtime');
  assert.deepEqual(options(argsFor(runtime)), {
    mode: 'plan', expectedBranch: 'feat/customsforge-song-browser',
    electron: nonexistentElectron, runtime,
  });
  assert.deepEqual(options([...argsFor(runtime), '--mode', 'run', '--expected-branch', 'feature/test']), {
    mode: 'run', expectedBranch: 'feature/test', electron: nonexistentElectron, runtime,
  });
});

test('UI runner rejects missing, duplicate, unknown and relative command arguments', () => {
  const runtime = path.join(tempParent, 'fresh-ui-runtime');
  const valid = argsFor(runtime);
  for (const args of [
    [], ['--electron', nonexistentElectron],
    [...valid, '--mode'], [...valid, '--mode', '--runtime'],
    [...valid, '--electron', nonexistentElectron], [...valid, '--mode', 'plan', '--mode', 'run'],
    [...valid, '--unknown', 'value'], [...valid, 'toString', 'value'], [...valid, '__proto__', 'value'],
    [...valid, '--mode', 'preview'],
    ['--electron', 'electron.exe', '--runtime', runtime],
    ['--electron', nonexistentElectron, '--runtime', 'relative-runtime'],
  ]) assert.throws(() => options(args), /Unknown, duplicate or incomplete option|Usage:/, JSON.stringify(args));
});

test('UI runtime containment distinguishes children from the root, parent and sibling prefixes', () => {
  const root = path.join(tempParent, 'feedforge-ui-source');
  assert.equal(inside(root, path.join(root, 'runtime')), true);
  assert.equal(inside(root, path.join(root, 'nested', 'runtime')), true);
  assert.equal(inside(root, root), false);
  assert.equal(inside(root, path.dirname(root)), false);
  assert.equal(inside(root, root + '-runtime'), false);
  assert.equal(inside(root, path.join(root + '-runtime', 'child')), false);
  assert.equal(inside(root, path.resolve(root, '..', 'outside')), false);
  if (process.platform === 'win32') {
    assert.equal(inside(root.toUpperCase(), path.join(root.toLowerCase(), 'runtime')), true);
    const otherDrive = path.parse(root).root.toUpperCase().startsWith('Z:') ? 'Y:\\runtime' : 'Z:\\runtime';
    assert.equal(inside(root, otherDrive), false);
  }
});

test('UI inventory hashes regular files and rejects a directory link before following it', (t) => {
  const directory = fixture(t);
  const source = path.join(directory, 'source');
  const outside = path.join(directory, 'outside');
  fs.mkdirSync(source); fs.mkdirSync(outside);
  fs.mkdirSync(path.join(source, 'nested'));
  fs.writeFileSync(path.join(source, 'nested', 'file.txt'), 'fixture bytes');
  fs.writeFileSync(path.join(outside, 'sentinel.txt'), 'must stay outside inventory');
  assert.deepEqual(inventory(source, ['nested']), {
    'nested/file.txt': crypto.createHash('sha256').update('fixture bytes').digest('hex'),
  });
  const link = path.join(source, 'linked');
  fs.symlinkSync(outside, link, process.platform === 'win32' ? 'junction' : 'dir');
  try {
    assert.throws(() => inventory(source, ['linked']), /Source\/output links are not permitted/);
    assert.equal(fs.readFileSync(path.join(outside, 'sentinel.txt'), 'utf8'), 'must stay outside inventory');
  } finally { fs.unlinkSync(link); }
});

test('UI plan rejects existing runtime output without changing its files', (t) => {
  const directory = fixture(t);
  fs.writeFileSync(path.join(directory, 'ownership.json'), '{"unrelated":"existing output"}');
  fs.mkdirSync(path.join(directory, 'nested'));
  fs.writeFileSync(path.join(directory, 'nested', 'sentinel.txt'), 'preserve existing output');
  const before = inventory(directory, fs.readdirSync(directory));
  assert.throws(() => prepare([...argsFor(directory), '--mode', 'plan']), /fresh runtime directory/);
  assert.deepEqual(inventory(directory, fs.readdirSync(directory)), before);
});

test('UI plan rejects source, source children and filesystem roots before any output write', () => {
  const child = path.join(checkout, '.ui-runner-guard-' + crypto.randomUUID());
  assert.equal(fs.existsSync(child), false);
  assert.throws(() => prepare(argsFor(child)), /outside the source checkout/);
  assert.equal(fs.existsSync(child), false);
  for (const runtime of [checkout, path.dirname(checkout), path.parse(checkout).root]) {
    assert.throws(() => prepare(argsFor(runtime)), /fresh runtime directory/);
  }
});

test('UI plan canonicalizes a linked parent before accepting an output location', (t) => {
  const directory = fixture(t);
  const alias = path.join(directory, 'source-alias');
  fs.symlinkSync(checkout, alias, process.platform === 'win32' ? 'junction' : 'dir');
  const name = '.ui-runner-guard-' + crypto.randomUUID();
  try {
    assert.throws(() => prepare(argsFor(path.join(alias, name))), /outside the source checkout/);
    assert.equal(fs.existsSync(path.join(checkout, name)), false);
    assert.deepEqual(fs.readdirSync(directory), ['source-alias']);
  } finally { fs.unlinkSync(alias); }
});

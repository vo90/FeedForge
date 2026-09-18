'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { createRequire } = require('node:module');
const { validateRuntimeStage } = require('../../tools/song-browser-runtime-dependencies.cjs');

const dependencies = path.resolve(process.env.FEEDFORGE_TEST_DEPENDENCIES || path.join(__dirname, '../..'));
const installedRequire = createRequire(path.join(dependencies, 'package.json'));

function fixture() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'feedforge-runtime-inputs-'));
  const write = (name, value) => {
    const filename = path.join(root, name);
    fs.mkdirSync(path.dirname(filename), { recursive: true });
    fs.writeFileSync(filename, typeof value === 'string' ? value : JSON.stringify(value));
  };
  write('package.json', { name: 'fixture-stage', version: '1.0.0', main: 'electron/main.cjs' });
  write('electron/main.cjs', `const { app } = require('electron');
    require('node:fs'); require('./helper.cjs');
    require(path.join(app.getAppPath(), "package.json"));`);
  write('electron/helper.cjs', `module.exports = require('./settings.json');`);
  write('electron/settings.json', {});
  write('desktop-dist/assets/main.js', `import './chunk.js'; import('./chunk.js'); export { answer } from './chunk.js';`);
  write('desktop-dist/assets/chunk.js', 'export const answer = 42;');
  return { root, write };
}

test('runtime stage accepts bundled UI, local modules, built-ins and app metadata without node_modules', () => {
  const f = fixture();
  const result = validateRuntimeStage(f.root, dependencies);
  assert.equal(result.runtimeNodeModulesRequired, false);
  assert.equal(result.metadataPackageName, 'fixture-stage');
  assert.deepEqual(result.checkedModules, [
    'desktop-dist/assets/chunk.js', 'desktop-dist/assets/main.js',
    'electron/helper.cjs', 'electron/main.cjs',
  ]);
  assert.equal(fs.existsSync(path.join(f.root, 'node_modules')), false);
});

for (const [name, file, source, error] of [
  ['unbundled desktop package', 'electron/helper.cjs', `require('lucide-react');`, /Unbundled runtime dependency/],
  ['unbundled renderer package', 'desktop-dist/assets/main.js', `import React from 'react';`, /Unbundled runtime dependency/],
  ['renderer Node dependency', 'desktop-dist/assets/main.js', `import fs from 'node:fs';`, /Unbundled runtime dependency/],
  ['computed require', 'electron/helper.cjs', 'require(process.env.MODULE_NAME);', /Dynamic runtime dependency/],
  ['computed import', 'desktop-dist/assets/main.js', 'import(window.moduleName);', /Dynamic runtime dependency/],
  ['missing local module', 'electron/helper.cjs', `module.require('./absent.cjs');`, /Missing packaged runtime module/],
  ['module path outside stage', 'electron/helper.cjs', `require.resolve('../../outside.json');`, /escapes its package/],
  ['renderer path outside bundle', 'desktop-dist/assets/main.js', `import '../../electron/helper.cjs';`, /escapes its package/],
  ['unrecognized dynamic metadata', 'electron/main.cjs', `require(path.join(app.getAppPath(), filename));`, /Dynamic runtime dependency/],
  ['invalid JavaScript', 'electron/helper.cjs', 'const = 2;', /malformed JavaScript/],
]) {
  test(`runtime stage rejects ${name} before packaging`, () => {
    const f = fixture();
    f.write(file, source);
    assert.throws(() => validateRuntimeStage(f.root, dependencies), error);
  });
}

for (const key of ['dependencies', 'optionalDependencies', 'peerDependencies', 'bundledDependencies']) {
  test(`runtime stage refuses declared ${key} instead of silently omitting packages`, () => {
    const f = fixture();
    f.write('package.json', { name: 'fixture-stage', [key]: { 'lucide-react': '^0.468.0' } });
    assert.throws(() => validateRuntimeStage(f.root, dependencies), /must be empty/);
  });
}

test('real builder traversal succeeds in stage and reproduces the source dependency fallback failure', async () => {
  const { TraversalNodeModulesCollector } = installedRequire('app-builder-lib/out/node-module-collector/traversalNodeModulesCollector');
  const f = fixture();
  f.write('source/package.json', { name: 'fixture-source', version: '1.0.0', dependencies: { 'lucide-react': '^0.468.0' } });
  const sourceCollector = new TraversalNodeModulesCollector(path.join(f.root, 'source'), null);
  await assert.rejects(sourceCollector.getNodeModules({ packageName: 'fixture-source' }), /Production dependency lucide-react not found/);
  const stageCollector = new TraversalNodeModulesCollector(f.root, null);
  const result = await stageCollector.getNodeModules({ packageName: 'fixture-stage' });
  assert.deepEqual(result.nodeModules, []);
  assert.equal(fs.existsSync(path.join(f.root, 'node_modules')), false);
});

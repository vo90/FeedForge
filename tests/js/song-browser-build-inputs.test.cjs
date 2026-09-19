'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { inspectDependencies, viteConfigText } = require('../../tools/song-browser-build-inputs.cjs');

function fixture() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'feedforge-build-inputs-'));
  const source = path.join(root, 'source'), dependencies = path.join(root, 'dependencies');
  const write = (filename, value) => {
    fs.mkdirSync(path.dirname(filename), { recursive: true });
    fs.writeFileSync(filename, typeof value === 'string' ? value : JSON.stringify(value));
  };
  const manifest = { version: '0.1.42', dependencies: { react: '^19.0.0', 'react-dom': '^19.0.0' }, devDependencies: { vite: '^6.0.7', '@vitejs/plugin-react': '^4.3.4', electron: '^43.0.0', 'electron-builder': '^26.15.3' } };
  const versions = { react: '19.0.0', 'react-dom': '19.0.0', vite: '6.4.3', '@vitejs/plugin-react': '4.7.0', electron: '43.0.0', 'electron-builder': '26.15.3' };
  const lock = { packages: { '': manifest, ...Object.fromEntries(Object.entries(versions).map(([name, version]) => [`node_modules/${name}`, { version }])) } };
  write(path.join(source, 'package.json'), manifest);
  for (const folder of [source, dependencies]) write(path.join(folder, 'package-lock.json'), lock);
  for (const [name, version] of Object.entries(versions)) write(path.join(dependencies, 'node_modules', name, 'package.json'), { name, version });
  for (const filename of ['vite/bin/vite.js', 'electron-builder/out/cli/cli.js', '@vitejs/plugin-react/dist/index.js', 'electron/dist/electron.exe']) write(path.join(dependencies, 'node_modules', filename), 'fixture; never executed');
  write(path.join(dependencies, 'node_modules/electron/dist/version'), versions.electron);
  return { root, source, dependencies, electron: path.join(dependencies, 'node_modules/electron/dist/electron.exe'), write };
}

test('external installed dependencies are validated without creating source node_modules', () => {
  const f = fixture();
  const inputs = inspectDependencies(f.source, f.dependencies, f.electron);
  assert.equal(inputs.packages.vite, '6.4.3');
  assert.equal(inputs.dependenciesRoot, fs.realpathSync(f.dependencies));
  const config = viteConfigText(inputs, path.join(f.root, 'build'));
  assert.match(config, /file:\/\//);
  assert.ok(config.includes(JSON.stringify(inputs.aliases.react)));
  assert.ok(config.includes(JSON.stringify(path.join(f.root, 'build', 'vite-cache'))));
  assert.equal(fs.existsSync(path.join(f.source, 'node_modules')), false);
  assert.equal(fs.existsSync(path.join(f.source, 'desktop-dist')), false);
});

test('a different dependency checkout lock is rejected', () => {
  const f = fixture();
  f.write(path.join(f.dependencies, 'package-lock.json'), {});
  assert.throws(() => inspectDependencies(f.source, f.dependencies, f.electron), /exact source lock/);
});

test('release metadata changes can reuse the identical full dependency graph', () => {
  const f = fixture();
  const manifest = JSON.parse(fs.readFileSync(path.join(f.source, 'package.json')));
  const lock = JSON.parse(fs.readFileSync(path.join(f.source, 'package-lock.json')));
  manifest.version = '2.0.1';
  lock.version = '2.0.1';
  lock.packages[''].version = '2.0.1';
  lock.packages[''].license = 'MIT';
  f.write(path.join(f.source, 'package.json'), manifest);
  f.write(path.join(f.source, 'package-lock.json'), lock);
  const inputs = inspectDependencies(f.source, f.dependencies, f.electron);
  assert.notEqual(inputs.lockHash, inputs.dependencyLockHash);
  lock.packages['node_modules/vite/node_modules/example'] = { version: '1.0.0', integrity: 'changed' };
  f.write(path.join(f.source, 'package-lock.json'), lock);
  assert.throws(() => inspectDependencies(f.source, f.dependencies, f.electron), /exact source lock dependency graph/);
});

test('stale installed package versions and a different Electron runtime are rejected', () => {
  const f = fixture();
  f.write(path.join(f.dependencies, 'node_modules/react/package.json'), { name: 'react', version: '18.0.0' });
  assert.throws(() => inspectDependencies(f.source, f.dependencies, f.electron), /Installed react differs/);
  f.write(path.join(f.dependencies, 'node_modules/react/package.json'), { name: 'react', version: '19.0.0' });
  f.write(path.join(path.dirname(f.electron), 'version'), '42.0.0');
  assert.throws(() => inspectDependencies(f.source, f.dependencies, f.electron), /Electron runtime differs/);
});

test('manifest changes absent from the source lock are rejected', () => {
  const f = fixture();
  const filename = path.join(f.source, 'package.json');
  const manifest = JSON.parse(fs.readFileSync(filename));
  manifest.dependencies.react = '^20.0.0';
  f.write(filename, manifest);
  assert.throws(() => inspectDependencies(f.source, f.dependencies, f.electron), /Source dependencies differ/);
});

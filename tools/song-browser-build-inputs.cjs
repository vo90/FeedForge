'use strict';

const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const { pathToFileURL } = require('node:url');

const json = (filename) => JSON.parse(fs.readFileSync(filename, 'utf8').replace(/^\uFEFF/, ''));
const hash = (filename) => crypto.createHash('sha256').update(fs.readFileSync(filename)).digest('hex');
const file = (filename) => { assert.ok(fs.statSync(filename).isFile(), `Required build input is not a file: ${filename}`); return fs.realpathSync(filename); };

function inspectPreservationContract(sourceRoot) {
  const declarations = [
    ['electron/song-browser/songsterr-evidence.cjs', /^const CURRENT_PRESERVATION_CONTRACT = (\d+);\r?$/m],
    ['src/feedback_converter/song_import/evidence.py', /^CONTRACT_VERSION = (\d+)\r?$/m],
    ['src/feedback_converter/song_import/verification.py', /^VERSION = (\d+)\r?$/m],
    ['src/feedback_converter/song_import/compatibility.py', /^VERSION = (\d+)\r?$/m],
  ];
  const versions = declarations.map(([relative, pattern]) => {
    const match = fs.readFileSync(path.join(sourceRoot, relative), 'utf8').match(pattern);
    assert.ok(match, `Missing preservation contract declaration: ${relative}`);
    return Number(match[1]);
  });
  assert.ok(versions[0] > 0 && versions.every(version => version === versions[0]),
    'App, converter evidence, verification and compatibility preservation contracts differ.');
  return versions[0];
}

function inspectDependencies(source, dependencies, electron) {
  const sourceRoot = fs.realpathSync(source);
  const preservationContract = inspectPreservationContract(sourceRoot);
  const dependenciesRoot = fs.realpathSync(dependencies);
  const manifest = json(path.join(sourceRoot, 'package.json'));
  const lockPath = path.join(sourceRoot, 'package-lock.json');
  const lock = json(lockPath);
  assert.equal(lock.packages[''].version, manifest.version, 'Source manifest and lock versions differ.');
  for (const field of ['dependencies', 'devDependencies']) {
    assert.deepEqual(lock.packages[''][field] || {}, manifest[field] || {}, `Source ${field} differ from its lock file.`);
  }
  const lockHash = hash(lockPath);
  const dependencyLockPath = path.join(dependenciesRoot, 'package-lock.json');
  const dependencyLockHash = hash(dependencyLockPath);
  // A release version/license change does not change installed dependencies.
  // Compare the complete locked graph, including integrities and nested packages.
  const dependencyGraph = (value) => {
    const result = structuredClone(value);
    delete result.version;
    if (result.packages?.['']) {
      delete result.packages[''].version;
      delete result.packages[''].license;
    }
    return result;
  };
  assert.deepEqual(dependencyGraph(json(dependencyLockPath)), dependencyGraph(lock), 'Installed dependency checkout must use the exact source lock dependency graph.');
  const nodeModules = fs.realpathSync(path.join(dependenciesRoot, 'node_modules'));
  const packages = {};
  for (const name of Object.keys({ ...manifest.dependencies, ...manifest.devDependencies }).sort()) {
    const installed = json(path.join(nodeModules, ...name.split('/'), 'package.json'));
    assert.equal(installed.name, name, `Unexpected installed package identity for ${name}.`);
    assert.equal(installed.version, lock.packages[`node_modules/${name}`]?.version, `Installed ${name} differs from the locked version.`);
    packages[name] = installed.version;
  }
  const electronExe = file(electron);
  const electronVersion = fs.readFileSync(path.join(path.dirname(electronExe), 'version'), 'utf8').trim();
  assert.equal(electronVersion, packages.electron, 'Selected Electron runtime differs from the locked package.');
  const inputs = {
    sourceRoot, dependenciesRoot, nodeModules, lockHash, dependencyLockHash, packages, electronVersion, preservationContract,
    viteCli: file(path.join(nodeModules, 'vite', 'bin', 'vite.js')),
    builderCli: file(path.join(nodeModules, 'electron-builder', 'out', 'cli', 'cli.js')),
    reactPlugin: file(path.join(nodeModules, '@vitejs', 'plugin-react', 'dist', 'index.js')),
    aliases: Object.fromEntries(Object.keys(manifest.dependencies).map((name) => [name, path.join(nodeModules, ...name.split('/'))])),
  };
  inputs.toolHashes = Object.fromEntries(['viteCli', 'builderCli', 'reactPlugin'].map((key) => [key, hash(inputs[key])]));
  return inputs;
}

function viteConfigText(inputs, buildRoot) {
  const options = {
    root: inputs.sourceRoot,
    base: './',
    cacheDir: path.join(buildRoot, 'vite-cache'),
    resolve: { alias: inputs.aliases },
    build: { outDir: path.join(buildRoot, 'app-stage', 'desktop-dist'), emptyOutDir: true },
  };
  return `import react from ${JSON.stringify(pathToFileURL(inputs.reactPlugin).href)};\nexport default { ...${JSON.stringify(options, null, 2)}, plugins: [react()] };\n`;
}

if (require.main === module) {
  const args = {};
  for (let i = 2; i < process.argv.length; i += 2) {
    const key = process.argv[i];
    assert.ok(['--source', '--dependencies', '--electron', '--vite-config'].includes(key) && !args[key] && process.argv[i + 1], 'Explicit source, dependencies and Electron paths are required.');
    args[key] = process.argv[i + 1];
  }
  const inputs = inspectDependencies(args['--source'], args['--dependencies'], args['--electron']);
  if (args['--vite-config']) {
    const config = path.resolve(args['--vite-config']);
    for (const root of [inputs.sourceRoot, inputs.dependenciesRoot]) {
      const relative = path.relative(root, config);
      assert.ok(relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative), 'Generated config must be outside source and dependency checkouts.');
    }
    fs.writeFileSync(config, viteConfigText(inputs, path.dirname(config)), { flag: 'wx' });
  }
  process.stdout.write(JSON.stringify(inputs));
}

module.exports = { inspectDependencies, viteConfigText };

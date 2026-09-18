'use strict';

// Inspect imports without evaluating application code or launching Electron.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { createRequire, isBuiltin } = require('node:module');

function files(root) {
  return fs.readdirSync(root, { withFileTypes: true }).flatMap((entry) => {
    assert.ok(!entry.isSymbolicLink(), 'Runtime dependency inspection does not follow symbolic links.');
    const filename = path.join(root, entry.name);
    return entry.isDirectory() ? files(filename) : [filename];
  });
}

function validateRuntimeStage(stage, dependenciesRoot) {
  const root = fs.realpathSync(stage);
  const ts = createRequire(path.join(fs.realpathSync(dependenciesRoot), 'package.json'))('typescript');
  const manifest = JSON.parse(fs.readFileSync(path.join(root, 'package.json'), 'utf8').replace(/^\uFEFF/, ''));
  for (const key of ['dependencies', 'optionalDependencies', 'peerDependencies', 'bundledDependencies']) {
    assert.equal(Object.keys(manifest[key] || {}).length, 0, `Staged ${key} must be empty; review runtime packaging before dropping dependencies.`);
  }
  const checked = [];
  const resolveLocal = (specifier, filename, boundary) => {
    assert.ok(specifier.startsWith('./') || specifier.startsWith('../'), `Unbundled runtime dependency ${specifier} in ${filename}`);
    const target = path.resolve(path.dirname(filename), specifier);
    const relative = path.relative(boundary, target);
    assert.ok(relative && relative !== '..' && !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative), `Runtime import escapes its package: ${specifier}`);
    assert.ok([target, `${target}.js`, `${target}.cjs`, `${target}.json`].some((candidate) => fs.existsSync(candidate) && fs.statSync(candidate).isFile()), `Missing packaged runtime module: ${specifier}`);
  };
  for (const [directory, desktop] of [['electron', true], ['desktop-dist', false]]) {
    const boundary = desktop ? root : path.join(root, directory);
    for (const filename of files(path.join(root, directory)).filter((name) => /\.(?:cjs|js|mjs)$/.test(name))) {
      const text = fs.readFileSync(filename, 'utf8');
      const source = ts.createSourceFile(filename, text, ts.ScriptTarget.Latest, true, ts.ScriptKind.JS);
      assert.equal(source.parseDiagnostics.length, 0, `Cannot inspect malformed JavaScript: ${filename}`);
      const check = (argument, metadataAllowed = false) => {
        if (metadataAllowed && argument && path.relative(root, filename).replaceAll('\\', '/') === 'electron/main.cjs'
          && argument.getText(source).replace(/\s/g, '') === 'path.join(app.getAppPath(),"package.json")') return;
        assert.ok(argument && ts.isStringLiteralLike(argument), `Dynamic runtime dependency needs explicit packaging review in ${filename}`);
        if (desktop && (argument.text === 'electron' || isBuiltin(argument.text))) return;
        resolveLocal(argument.text, filename, boundary);
      };
      const visit = (node) => {
        if (ts.isImportDeclaration(node) || ts.isExportDeclaration(node) && node.moduleSpecifier) check(node.moduleSpecifier);
        if (ts.isCallExpression(node)) {
          if (node.expression.kind === ts.SyntaxKind.ImportKeyword) check(node.arguments[0]);
          if (ts.isIdentifier(node.expression) && node.expression.text === 'require') check(node.arguments[0], desktop);
          if (ts.isPropertyAccessExpression(node.expression) && ['require', 'resolve'].includes(node.expression.name.text)
            && ['module', 'require'].includes(node.expression.expression.getText(source))) {
            check(node.arguments[0]);
          }
        }
        ts.forEachChild(node, visit);
      };
      visit(source);
      checked.push(path.relative(root, filename).replaceAll('\\', '/'));
    }
  }
  assert.ok(checked.includes('electron/main.cjs') && checked.some((name) => name.startsWith('desktop-dist/')), 'Staged application and bundled UI are required.');
  return { runtimeNodeModulesRequired: false, checkedModules: checked.sort(), metadataPackageName: manifest.name };
}

if (require.main === module) process.stdout.write(JSON.stringify(validateRuntimeStage(process.argv[2], process.argv[3])));
module.exports = { validateRuntimeStage };

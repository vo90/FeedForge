'use strict';
const expected = require('./reference-manifest.json').qualificationToolchain;

// These development-only tools already exist in FeedForge's locked Babel/Vite
// graph. Do not change the app dependency graph or install anything at runtime.
function verify(readVersion = name => require(name + '/package.json').version) {
  const actual = {node: process.versions.node,
    babelParser: readVersion('@babel/parser'), babelTraverse: readVersion('@babel/traverse')};
  for (const field of ['babelParser', 'babelTraverse']) {
    if (actual[field] !== expected[field]) throw Error('Unqualified reference extraction tool: ' + field);
  }
  return actual;
}
module.exports = {verify};

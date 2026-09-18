'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { verifyStartupHooks } = require('../../tools/Test-SongBrowserPackage.cjs');

// A source-only simulation: no Electron process, actual profile or application
// output is created. The optional path supports reviewing a combined checkout.
const root = path.resolve(process.env.FEEDFORGE_TEST_SOURCE || path.join(__dirname, '../..'));
const source = fs.readFileSync(path.join(root, 'electron/main.cjs'), 'utf8');
const moduleSources = Object.fromEntries(['performance-profile.cjs', 'concurrency-limiter.cjs', 'audio-dependency.cjs', 'conversion-result.cjs', 'converter-args.cjs', 'local-assets.cjs', 'library-audit.cjs']
  .filter((name) => fs.existsSync(path.join(root, 'electron', name)))
  .map((name) => [name, fs.readFileSync(path.join(root, 'electron', name), 'utf8')]));
const options = {
  source, moduleSources,
  metadata: { version: '0.1.42', name: 'feedforge-song-browser-test', songBrowserTest: true },
  archive: path.resolve('virtual-package/resources/app.asar'),
  converter: path.resolve('virtual-package/resources/bin/psarc2feedpak/psarc2feedpak.exe'),
  reportRoot: path.resolve('virtual-report-only'),
};

for (const portable of [true, false]) {
  test(`combined startup retains isolation and disabled updater (portable=${portable})`, async () => {
    const result = await verifyStartupHooks({ ...options, portable });
    assert.equal(result.forbiddenActionsRequested, 0);
    assert.ok(result.verifiedStartupModules.includes('audio-dependency.cjs'));
    assert.ok(result.verifiedStartupModules.includes('performance-profile.cjs'));
    if (source.includes('./local-assets.cjs')) assert.equal(result.localAssetProtocolRegistered, true);
  });
}

test('unrecognized startup dependencies stay blocked', async () => {
  await assert.rejects(verifyStartupHooks({ ...options, portable: true, source: `require('net');\n${source}` }), /Unexpected dependency/);
});

for (const [name, code, error] of [
  ['filesystem', `require('fs').readFileSync('a real profile')`, /filesystem read/],
  ['network', `require('https').get('https:\/\/example.invalid')`, /network request/],
  ['process', `require('child_process').spawn('unexpected')`, /child process/],
]) {
  test(`packaged helper ${name} access stays blocked`, async () => {
    const modified = { ...moduleSources, 'performance-profile.cjs': `${moduleSources['performance-profile.cjs']}\n${code};` };
    await assert.rejects(verifyStartupHooks({ ...options, portable: true, moduleSources: modified }), error);
  });
}

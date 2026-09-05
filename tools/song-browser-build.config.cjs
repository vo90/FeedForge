'use strict';

// Read only explicit build inputs. This config never replaces FeedForge's normal build.
const path = require('node:path');
const fs = require('node:fs');
const root = process.env.FEEDFORGE_PACKAGE_ROOT;
const electronDist = process.env.FEEDFORGE_PACKAGE_ELECTRON_DIST;
if (!root || !electronDist) throw new Error('Use tools/Build-SongBrowser.ps1 to supply owned packaging paths.');
const source = path.resolve(__dirname, '..');
const receipt = JSON.parse(fs.readFileSync(path.join(root, 'song-browser-build.json'), 'utf8').replace(/^\uFEFF/, ''));
if (receipt.kind !== 'feedforge-song-browser-build' || receipt.source !== source || receipt.converter !== 'complete') {
  throw new Error('The build root is not an owned, completed Song Browser converter build.');
}

module.exports = {
  appId: 'com.feedforge.songbrowser.test',
  productName: 'FeedForge Song Browser Test',
  artifactName: 'FeedForge-Song-Browser-Test-${version}-${arch}.${ext}',
  electronDist,
  electronVersion: receipt.electronVersion,
  npmRebuild: false,
  asar: true,
  publish: null,
  extraMetadata: { name: 'feedforge-song-browser-test', songBrowserTest: true },
  directories: { app: path.join(root, 'app-stage'), output: path.join(root, 'release') },
  files: ['desktop-dist/**/*', 'electron/**/*', 'package.json', 'LICENSE'],
  extraResources: [
    { from: path.join(root, 'converter-dist', 'psarc2feedpak'), to: 'bin/psarc2feedpak' },
    { from: path.join(source, 'src'), to: 'demucs-server/src', filter: ['feedback_converter/**/*.py', 'feedback_converter/data/feedpak_schemas/*.json'] },
    { from: path.join(source, 'pyproject.toml'), to: 'demucs-server/pyproject.toml' },
    { from: path.join(source, 'tools', 'start-demucs-server.ps1'), to: 'demucs-server/start-demucs-server.ps1' },
    { from: path.join(source, 'tools', 'start-demucs-server.py'), to: 'demucs-server/start-demucs-server.py' },
    { from: path.join(source, 'README.md'), to: 'demucs-server/README.md' },
    { from: path.join(source, 'assets', 'tone-equipment'), to: 'tone-equipment' },
    { from: path.join(source, 'src', 'feedback_converter', 'data', 'equipment.json'), to: 'tone-equipment/equipment.json' },
    { from: path.join(source, 'src', 'feedback_converter', 'data', 'feedback_equipment.json'), to: 'tone-equipment/feedback_equipment.json' },
  ],
  compression: 'normal',
  electronLanguages: ['en-US'],
  win: { icon: path.join(source, 'assets', 'feedforge.ico'), signExecutable: false, target: ['dir'] },
  portable: { unpackDirName: 'FeedForge-Song-Browser-Test' },
};

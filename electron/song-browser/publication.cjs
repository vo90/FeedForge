'use strict';
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const fsp = fs.promises;

// Both sources use the same exclusive, hash-checked publication operation.
async function publishFeedpak({ job, staging, check, hashFile, writeReceipt }) {
  check(job);
  await fsp.mkdir(job.outputDir, { recursive: true });
  const outputRoot = await fsp.realpath(job.outputDir);
  const relative = job.outputRelativePath;
  if (typeof relative !== 'string' || path.isAbsolute(relative)
      || relative.split(/[\\/]/).some((part) => !part || part === '..' || part === '.' || /[<>:"|?*\x00-\x1f]/.test(part))
      || path.extname(relative).toLowerCase() !== '.feedpak') throw new Error('The planned output filename is invalid.');
  const folder = path.dirname(relative);
  if (folder !== '.' && (job.outputSettings?.outputLayout !== 'artist' || folder.split(/[\\/]/).length !== 1)) throw new Error('The planned output folder is invalid.');
  const destination = path.resolve(outputRoot, folder);
  if (folder !== '.') {
    try { await fsp.mkdir(destination); } catch (error) { if (error.code !== 'EEXIST') throw error; }
    const stat = await fsp.lstat(destination);
    if (!stat.isDirectory() || stat.isSymbolicLink()) throw new Error('The artist output folder must be a regular directory.');
  }
  const root = await fsp.realpath(destination);
  if (root !== outputRoot && path.dirname(root) !== outputRoot) throw new Error('The output folder changed outside the selected library.');
  const temporary = path.join(root, `.feedforge-${job.id}-${crypto.randomUUID()}.part`);
  let owned = false;
  try {
    const handle = await fsp.open(temporary, 'wx', 0o600); owned = true; await handle.close();
    await fsp.copyFile(staging, temporary);
    check(job);
    if (await hashFile(temporary, job.controller.signal) !== job.outputHash) throw new Error('The FeedPak changed while it was being saved. Retry the job.');
    const name = path.basename(relative), stem = name.slice(0, -8);
    for (let index = 0; index < 1000; index++) {
      check(job);
      const target = path.join(root, index ? `${stem} (${index + 1}).feedpak` : name);
      try {
        // Receipt precedes the atomic link so an interrupted commit is recoverable.
        writeReceipt(job, target);
        fs.linkSync(temporary, target);
        job.outputPath = target; job.committed = true;
        return target;
      } catch (error) {
        if (error.code === 'EEXIST') continue;
        throw new Error(`Could not safely save the FeedPak in the selected folder: ${String(error.message).slice(0, 250)}`);
      }
    }
    throw new Error('Too many files already use this song name. Choose another output folder.');
  } finally { if (owned) await fsp.unlink(temporary).catch(() => {}); }
}
module.exports = { publishFeedpak };

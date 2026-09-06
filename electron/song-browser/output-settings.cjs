'use strict';

const fs = require('node:fs');
const path = require('node:path');
const fsp = fs.promises;
const FIELDS = new Set(['artist', 'title', 'album', 'year', 'source', 'parts']);
const LAYOUTS = new Set(['flat', 'preserve', 'artist']);

function normalizeOutputSettings(value = {}) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Output settings must be an object.');
  const outputLayout = value.outputLayout ?? 'flat';
  if (typeof outputLayout !== 'string' || !LAYOUTS.has(outputLayout)) throw new Error('Choose a supported FeedForge output layout.');
  const nameTemplate = value.nameTemplate == null || value.nameTemplate === '' ? '{source}' : value.nameTemplate;
  if (typeof nameTemplate !== 'string' || nameTemplate.length > 512 || /[\u0000-\u001f\u007f]/.test(nameTemplate)) {
    throw new Error('The output naming template contains unsupported text.');
  }
  for (const match of nameTemplate.matchAll(/\{([^{}]+)\}/g)) {
    if (!FIELDS.has(match[1].toLowerCase())) throw new Error(`Unknown output naming field: {${match[1]}}. Choose a template in FeedForge Settings.`);
  }
  return { outputLayout, nameTemplate };
}

function validSegment(value) {
  return typeof value === 'string' && value.length > 0 && value.length <= 240
    && value !== '.' && value !== '..' && !/[<>:"/\\|?*\u0000-\u001f\u007f]/.test(value)
    && !/[. ]$/.test(value) && !/^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)/i.test(value);
}

function plannedRelativePath(outputDir, target, layout) {
  if (typeof target !== 'string' || target.length > 4096 || !path.isAbsolute(target)) throw new Error('The output planner returned an invalid filename.');
  const relative = path.relative(outputDir, target);
  const components = relative.split(path.sep);
  if (path.isAbsolute(relative) || !components.every(validSegment)
    || components.length !== (layout === 'artist' ? 2 : 1)
    || !/\.feedpak$/i.test(components.at(-1))) {
    throw new Error('The output planner returned a filename outside the selected output layout.');
  }
  return relative;
}

// Ask the same Python planner used by Convert. Its input must retain the
// observed download filename because {source} refers to that name, not a cache ID.
// Planning writes only to this job's workspace; publication owns destination writes.
async function planSongOutput({ inputPath, sourceFilename, outputDir, settings, directory, runConverter }) {
  const normalized = normalizeOutputSettings(settings);
  if (!validSegment(sourceFilename) || !/\.psarc$/i.test(sourceFilename)) throw new Error('The original PSARC filename is not valid for output naming.');
  if (typeof directory !== 'string' || !path.isAbsolute(directory) || typeof inputPath !== 'string' || !path.isAbsolute(inputPath)
    || typeof outputDir !== 'string' || !path.isAbsolute(outputDir) || typeof runConverter !== 'function') {
    throw new Error('Output planning needs an input file and local working and output folders.');
  }
  const [directoryStat, sourceStat] = await Promise.all([fsp.lstat(directory), fsp.lstat(inputPath)]);
  if (!directoryStat.isDirectory() || directoryStat.isSymbolicLink() || !sourceStat.isFile() || sourceStat.isSymbolicLink() || !sourceStat.size) {
    throw new Error('The output planning source or working folder is not a regular local file or folder.');
  }
  const workspace = await fsp.realpath(directory);
  const sourceDirectory = await fsp.mkdtemp(path.join(workspace, 'naming-source-'));
  const namedInput = path.join(sourceDirectory, sourceFilename);
  try { await fsp.link(inputPath, namedInput); }
  catch (error) {
    if (!['EXDEV', 'EPERM', 'EACCES', 'ENOTSUP'].includes(error.code)) throw error;
    await fsp.copyFile(inputPath, namedInput, fs.constants.COPYFILE_EXCL);
  }
  const namedStat = await fsp.lstat(namedInput);
  if (!namedStat.isFile() || namedStat.isSymbolicLink() || namedStat.size !== sourceStat.size) throw new Error('The source file changed during output planning.');
  const requestPath = path.join(sourceDirectory, 'output-request.json');
  const request = {
    items: [{ inputPath: namedInput, sourceRoot: sourceDirectory }],
    outputDir: path.resolve(outputDir), ...normalized, overwrite: false, workers: 1,
  };
  await fsp.writeFile(requestPath, JSON.stringify(request), { flag: 'wx', mode: 0o600 });
  const result = await runConverter(['--plan-conversion-file', requestPath]);
  let planned;
  if (typeof result?.stdout === 'string' && result.stdout.length <= 2 * 1024 * 1024) {
    try { planned = JSON.parse(result.stdout); } catch { /* Report a bounded planner error below. */ }
  }
  const failure = (message) => new Error(typeof message === 'string' && message.trim()
    ? message.replace(/[\u0000-\u001f\u007f]/g, ' ').trim().slice(0, 1200) : 'FeedForge could not plan the output filename.');
  if (result?.code !== 0 || planned?.ok !== true || !Array.isArray(planned.items) || planned.items.length !== 1) throw failure(planned?.error);
  const item = planned.items[0];
  if (item?.ok !== true) throw failure(item?.error);
  if (typeof item.inputPath !== 'string' || path.resolve(item.inputPath) !== path.resolve(namedInput)
    || item.sourceSize !== namedStat.size || !Array.isArray(item.outputs) || item.outputs.length !== 1) {
    throw new Error('Song Browser requires one song in the output plan for the downloaded PSARC.');
  }
  return { relativePath: plannedRelativePath(request.outputDir, item.outputs[0]?.path, normalized.outputLayout) };
}

module.exports = { normalizeOutputSettings, planSongOutput };

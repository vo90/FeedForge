'use strict';

// Offline proof that the frozen converter runs without a source checkout,
// Python environment, or external audio programs on PATH.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');

async function sha256(filename) {
  const hash = crypto.createHash('sha256');
  for await (const chunk of fs.createReadStream(filename)) hash.update(chunk);
  return hash.digest('hex');
}

async function main() {
  const args = {};
  for (let i = 2; i < process.argv.length; i += 2) {
    const key = process.argv[i];
    if (!['--converter', '--input', '--root'].includes(key) || args[key] || !process.argv[i + 1]) {
      throw new Error('Use --converter <frozen executable> --input <existing PSARC> --root <new smoke folder>.');
    }
    args[key] = path.resolve(process.argv[i + 1]);
  }
  for (const key of ['--converter', '--input', '--root']) if (!args[key]) throw new Error(`Explicit ${key} is required.`);
  const converter = await fs.promises.realpath(args['--converter']);
  const input = await fs.promises.realpath(args['--input']);
  assert.equal((await fs.promises.stat(converter)).isFile(), true);
  assert.equal(path.extname(input).toLowerCase(), '.psarc');
  const root = args['--root'];
  if (fs.existsSync(root)) throw new Error('The smoke root must be new, never an existing library or output folder.');
  await fs.promises.mkdir(root);
  await fs.promises.writeFile(path.join(root, 'OFFLINE_STANDALONE_SMOKE.txt'), 'Owned standalone converter verification. No downloads or game/library imports.\n', { flag: 'wx' });
  const env = { ...process.env };
  for (const key of Object.keys(env)) {
    if (/^(?:python|feedforge_)/i.test(key) || /^(?:path|temp|tmp|tmpdir)$/i.test(key)) delete env[key];
  }
  env.PATH = [path.join(process.env.SystemRoot || 'C:\\Windows', 'System32'), process.env.SystemRoot || 'C:\\Windows'].join(path.delimiter);
  env.TEMP = root; env.TMP = root; env.TMPDIR = root;
  const report = { ok: false, offline: true, standalone: true, converter, converterSha256: await sha256(converter), input: { path: input, sha256: await sha256(input) }, calls: [], restrictedPath: env.PATH };
  const run = (name, cliArgs) => new Promise((resolve, reject) => {
    const call = { name, startedAt: new Date().toISOString() };
    report.calls.push(call);
    const child = spawn(converter, cliArgs, { cwd: root, env, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
    let stdout = ''; let stderr = ''; let spawnError;
    child.stdout.setEncoding('utf8'); child.stderr.setEncoding('utf8');
    child.stdout.on('data', (chunk) => { stdout += chunk; });
    child.stderr.on('data', (chunk) => { stderr += chunk; });
    child.once('error', (error) => { spawnError = error; });
    child.once('close', async (code, signal) => {
      Object.assign(call, { code, signal, closedAt: new Date().toISOString() });
      try {
        await Promise.all([
          fs.promises.writeFile(path.join(root, `${name}.stdout.log`), stdout, { flag: 'wx' }),
          fs.promises.writeFile(path.join(root, `${name}.stderr.log`), stderr, { flag: 'wx' }),
        ]);
        if (spawnError) throw spawnError;
        assert.equal(code, 0, `${name} failed: ${stderr.slice(-1200)}`);
        resolve(stdout);
      } catch (error) { reject(error); }
    });
  });
  try {
    report.version = (await run('version', ['--version'])).trim();
    const inspection = JSON.parse(await run('inspect', ['--inspect-json', input]));
    assert.equal(inspection.ok, true, 'The frozen executable must inspect the PSARC.');
    assert.ok(inspection.preview?.arrangements?.length, 'Playable arrangements must be present.');
    report.song = { title: inspection.preview.title, artist: inspection.preview.artist, arrangements: inspection.preview.arrangements.length };
    const output = path.join(root, 'standalone-smoke.feedpak');
    await run('convert', [input, '-o', output]);
    const validation = JSON.parse(await run('validate', ['--validate-feedpak', output]));
    assert.equal(validation.ok, true);
    assert.equal(validation.results?.length, 1);
    assert.equal(validation.results[0].validation?.ok, true);
    assert.deepEqual(validation.results[0].validation.errors || [], []);
    const reinspection = JSON.parse(await run('inspect-feedpak', ['--inspect-json', output]));
    assert.equal(reinspection.ok, true);
    assert.ok(reinspection.preview?.stems?.some((stem) => stem.id === 'full' && stem.codec === 'vorbis' && stem.file.endsWith('.ogg') && stem.size > 0), 'Bundled audio tools must produce a playable Ogg/Vorbis full track.');
    report.audio = reinspection.preview.stems.map(({ id, file, codec, size }) => ({ id, file, codec, size }));
    report.output = { path: output, bytes: (await fs.promises.stat(output)).size, sha256: await sha256(output) };
    assert.equal(await sha256(input), report.input.sha256, 'The original PSARC must stay unchanged.');
    report.ok = true;
  } catch (error) {
    report.error = error.message;
    throw error;
  } finally {
    await fs.promises.writeFile(path.join(root, 'result.json'), JSON.stringify(report, null, 2), { flag: 'wx' });
    process.stdout.write(JSON.stringify(report, null, 2) + '\n');
  }
}

main().catch((error) => { process.stderr.write(error.stack + '\n'); process.exitCode = 1; });

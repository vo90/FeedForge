'use strict';
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { normalizeRecipe } = require('./provenance.cjs');
// An unbundled Python environment can change independently of the source tree.
// Reuse it only within this process; frozen distributions have stable identities.
const developmentSession = crypto.randomUUID();

// Hash the actual converter payload once, asynchronously. Paths never become
// persisted identity fields; an installation can move without changing bytes.
async function converterRecipe({ command, prefix = [], cwd, version }) {
  const digest = crypto.createHash('sha256');
  async function add(filename, label) {
    const stat = await fs.promises.lstat(filename);
    if (!stat.isFile() || stat.isSymbolicLink()) throw new Error('The converter contains an unsupported file.');
    digest.update(label + '\0' + stat.size + '\0');
    for await (const chunk of fs.createReadStream(filename)) digest.update(chunk);
  }
  async function walk(directory, relative = '', filter = () => true) {
    const entries = await fs.promises.readdir(directory, { withFileTypes: true });
    for (const entry of entries.sort((a, b) => a.name.localeCompare(b.name, 'en'))) {
      if (entry.name === '__pycache__') continue;
      if (entry.isSymbolicLink()) throw new Error('The converter contains an unsupported link.');
      const next = path.join(directory, entry.name), label = relative + entry.name;
      if (entry.isDirectory()) await walk(next, label + '/', filter);
      else if (entry.isFile() && filter(entry.name)) await add(next, label);
    }
  }
  if (!prefix.length) await walk(path.dirname(command));
  else {
    digest.update('unbundled-session\0' + developmentSession + '\0');
    await add(command, 'python-runtime');
    await walk(path.join(cwd, 'src', 'feedback_converter'), 'source/', (name) => /\.(?:py|json|bin)$/.test(name));
    await add(path.join(cwd, 'pyproject.toml'), 'pyproject.toml');
  }
  return normalizeRecipe({ version, build: digest.digest('hex'), options: { format: 'feedpak', stemSeparation: false, pipeline: 1 } });
}
module.exports = { converterRecipe };

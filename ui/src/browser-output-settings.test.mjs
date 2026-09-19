import test from 'node:test';
import assert from 'node:assert/strict';
import { outputSettingsKey } from './song-browser/output-settings.mjs';

test('changing only generated difficulty requires a new browser settings synchronization', () => {
  const settings = { outputDir: 'C:\\Songs', outputLayout: 'artist', nameTemplate: '{artist} - {title}' };
  const initial = outputSettingsKey(settings);
  const optedIn = outputSettingsKey({ ...settings, generateDifficulty: true });
  assert.notEqual(initial, optedIn);
  assert.deepEqual(JSON.parse(optedIn), { ...settings, generateDifficulty: true });
  assert.equal(JSON.parse(initial).generateDifficulty, false);
  assert.equal(outputSettingsKey({ ...settings, generateDifficulty: false }), initial);
});

test('browser settings distinguish missing preferences and do not invent an output folder', () => {
  assert.equal(outputSettingsKey(undefined), '');
  assert.deepEqual(JSON.parse(outputSettingsKey({})), {
    outputLayout: 'flat', nameTemplate: '{source}', generateDifficulty: false,
  });
});

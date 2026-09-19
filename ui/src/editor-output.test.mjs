import test from 'node:test';
import assert from 'node:assert/strict';
import { editorOutputName, safeOutputSegment } from './editor-output.mjs';
import { resolveStemPreference } from './stem-preference.mjs';

test('editor filename follows saved templates and selected roles without a provider tag', () => {
  const song = { artist: 'Ghost', title: 'Rats', album: 'Prequelle', year: 2018 };
  assert.equal(editorOutputName(song), 'Ghost - Rats.feedpak');
  assert.equal(editorOutputName(song, [
    { selected: true, role: 'rhythm' }, { selected: true, role: 'lead' }, { selected: false, role: 'bass' }
  ], { nameTemplate: '{year} - {title} - {parts}' }), '2018 - Rats - LR.feedpak');
  assert.equal(editorOutputName(song, [], { nameTemplate: '{artist} - {album} - {title}' }), 'Ghost - Prequelle - Rats.feedpak');
});

test('editor preview retains Unicode while keeping output paths safe', () => {
  assert.equal(editorOutputName({ artist: 'Björk', title: 'Jo\u0301ga' }), 'Björk - Jóga.feedpak');
  assert.equal(safeOutputSegment('COM¹.txt'), '_COM¹.txt');
  assert.equal(safeOutputSegment('../a\\b:*? '), '.._a_b___');
  assert.equal(safeOutputSegment(' . '), 'converted');
});

test('saved disabled stems skips readiness entirely; ready stems need no confirmation', async () => {
  const options = { demucsModel: 'selected', demucsStems: ['bass'] };
  assert.deepEqual(await resolveStemPreference(false, options, () => { throw Error('must not check'); }), {
    options: { ...options, separateStems: false }
  });
  assert.deepEqual(await resolveStemPreference(true, options, async () => ({ ready: true })), {
    options: { ...options, separateStems: true }
  });
  assert.deepEqual(options, { demucsModel: 'selected', demucsStems: ['bass'] });
});

test('only an unavailable requested stem service requires a user decision', async () => {
  assert.deepEqual(await resolveStemPreference(true, {}, async () => ({ ready: false, error: 'Wrong model' })), {
    needsDecision: true, error: 'Wrong model'
  });
  assert.deepEqual(await resolveStemPreference(true, {}, async () => { throw Error('Offline'); }), {
    needsDecision: true, error: 'Offline'
  });
});

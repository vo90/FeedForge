import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const dependencies = path.resolve(process.env.FEEDFORGE_TEST_DEPENDENCIES || root);
const installed = createRequire(path.join(dependencies, 'package.json'));
const ts = installed('typescript');
const React = installed('react');
const { renderToStaticMarkup } = installed('react-dom/server');

// Render the actual modules with real React, but no Electron, effects, network,
// browser profile or filesystem writes. A view override models navigation state.
function application(view, saved = {}) {
  const cache = new Map();
  let element, currentSettings = saved;
  const api = { songsterr: {} };
  const context = vm.createContext({
    window: { feedbackConverter: api, location: { hash: '' } },
    document: { getElementById: () => ({}) },
    localStorage: { getItem: () => JSON.stringify(currentSettings), setItem: (_key, value) => { currentSettings = JSON.parse(value); } },
    console, URL, setTimeout, clearTimeout,
  });
  const navigationReact = {
    ...React,
    useState(initial) {
      const value = typeof initial === 'function' ? initial() : initial;
      return React.useState(value === 'home' ? view : value);
    }
  };
  function load(filename) {
    if (cache.has(filename)) return cache.get(filename).exports;
    const module = { exports: {} };
    cache.set(filename, module);
    const text = fs.readFileSync(filename, 'utf8');
    const source = ts.transpileModule(text, { fileName: `${filename}.jsx`, compilerOptions: {
      module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022,
      jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true,
    } }).outputText;
    const require = name => {
      if (name === 'react') return navigationReact;
      if (name === 'react-dom/client') return { createRoot: () => ({ render: value => { element = value; } }) };
      if (!name.startsWith('.')) return installed(name);
      const target = path.resolve(path.dirname(filename), name);
      if (/\.(css|png|svg)$/.test(target)) return target;
      return load(target);
    };
    vm.runInContext(`(function(require,module,exports){${source}\n})`, context, { filename })(require, module, module.exports);
    return module.exports;
  }
  load(path.join(root, 'ui/src/main.jsx'));
  return { html: renderToStaticMarkup(element), load, settings: () => currentSettings };
}

test('combined Home and both Songsterr routes render with the shared shell', () => {
  const home = application('home').html;
  assert.match(home, /Find songs/);
  assert.match(home, /Songsterr editor/);
  assert.match(home, /On your workbench/);
  assert.match(application('songs').html, /CustomsForge/);
  assert.match(application('songsterr').html, /not source-verified imports/);
});

test('settings retain capacity, validation and saved conversion preferences after extraction', () => {
  const { html } = application('settings', { bStandardTo7String: true, generateDifficulty: false, validationPolicy: 'strict' });
  assert.match(html, /Chart validation/);
  assert.match(html, /Strict validation/);
  assert.match(html, /Convert B Standard guitar to 7 strings/);
  const generated = html.match(/<label class="toggle">[^<]*(?:<input[^>]*>)[^<]*Generate easier practice levels[^<]*<\/label>/)?.[0];
  assert.ok(generated);
  assert.doesNotMatch(generated, /checked=/);
  assert.match(html, /Source filename/);
});

test('conversion and library views render their enhanced extracted panels', () => {
  assert.match(application('workspace').html, /No file selected/);
  assert.match(application('feedpak').html, /Library &amp; editor/);
});

test('settings writes preserve unknown values, latest performance version and explicit options', () => {
  const app = application('home', { customFutureOption: 42, performanceSettingsVersion: 3, bStandardTo7String: true });
  const utils = app.load(path.join(root, 'ui/src/workspace-utils.jsx'));
  utils.writeSettings({ generateDifficulty: false, performanceSettingsVersion: 3 });
  assert.deepEqual(app.settings(), {
    customFutureOption: 42, performanceSettingsVersion: 3, bStandardTo7String: true, generateDifficulty: false,
  });
});

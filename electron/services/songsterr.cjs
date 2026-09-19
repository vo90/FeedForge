const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const { parseCoreResponse } = require("./core-response.cjs");
const { normalizeOutputSettings } = require("../song-browser/output-settings.cjs");
const { publishFeedpak } = require("../song-browser/publication.cjs");
const { hashFile, atomicJson } = require("../song-browser/songsterr-jobs.cjs");
const { LocalAssetRegistry } = require("../local-assets.cjs");

function validateUrl(value) {
  if (typeof value !== "string" || value.length > 2048) throw new Error("Paste a Songsterr tab link.");
  const url = new URL(value);
  if (url.protocol !== "https:" || !["songsterr.com", "www.songsterr.com"].includes(url.hostname) ||
      url.username || url.password || (url.port && url.port !== "443") || !url.pathname.startsWith("/a/wsa/")) {
    throw new Error("Paste an HTTPS Songsterr tab link.");
  }
  return value;
}

function outputPayload(payload) {
  if (!payload || typeof payload !== "object") throw new Error("Missing song details.");
  validateUrl(payload.url);
  if (!Array.isArray(payload.selected_parts) || !payload.selected_parts.length ||
      payload.selected_parts.some(id => !Number.isInteger(id) || id < 0)) throw new Error("Select valid arrangements.");
  const name = payload.output_name;
  if (typeof name !== "string" || !name.toLowerCase().endsWith(".feedpak") ||
      /[<>:"/\\|?*\x00-\x1f]/.test(name) || name.length > 220 ||
      /^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i.test(name)) throw new Error("Choose a valid FeedPak filename.");
  if (typeof payload.output_dir !== "string" || !path.isAbsolute(payload.output_dir) ||
      !fs.statSync(payload.output_dir).isDirectory()) throw new Error("Choose an existing output folder.");
  let output = path.join(payload.output_dir, name);
  const stem = name.slice(0, -8);
  for (let index = 2; fs.existsSync(output); index++) output = path.join(payload.output_dir, `${stem} (${index}).feedpak`);
  return { ...payload, output_path: output };
}

function registerSongsterr({ app, ipcMain, dialog, window, runConverter, terminateChildProcessTree, logDebug,
  removeTemporaryDirectory, managedLifecycle = false, localAssets = new LocalAssetRegistry(),
  operationTimeout = (operation, value) => operation === "create" ? (value.separateStems ? 3600000 : 600000) : 180000 }) {
  let active = null;
  let closing = false, closePromise, closeFinished = false, quitRequested = false;
  const previews = new Set();
  const check = state => { if (state.controller.signal.aborted) throw new Error(state.timedOut
    ? "Operation timed out. Retry, or choose local audio if downloading failed." : "Songsterr operation cancelled."); };
  function abort(state) {
    state.cancelled = true;
    state.controller.abort();
    if (state.child && !state.termination) {
      const child = state.child;
      state.termination = Promise.resolve().then(() => terminateChildProcessTree(child));
    }
  }
  function close() {
    if (closePromise) return closePromise;
    closing = true;
    if (active) abort(active);
    closePromise = Promise.resolve(active?.done).then(async () => {
      const results = await Promise.allSettled([...previews].map(async directory => {
        localAssets.revokeDirectory(directory);
        await removeTemporaryDirectory(directory);
      }));
      previews.clear();
      const failure = results.find(result => result.status === "rejected");
      if (failure) throw failure.reason;
    }).finally(() => { closeFinished = true; });
    return closePromise;
  }
  if (!managedLifecycle) app.on?.("before-quit", event => {
    if (closeFinished) return;
    event?.preventDefault?.();
    if (quitRequested) return;
    quitRequested = true;
    void close().finally(() => app.quit?.()).catch(() => {});
  });
  const send = (event, progress) => { if (!event.sender.isDestroyed()) event.sender.send("songsterr-editor:progress", progress); };
  async function invoke(event, action, payload) {
    if (closing) throw new Error("Songsterr editor is closing.");
    if (active) throw new Error("A Songsterr operation is already running.");
    const state = { cancelled: false, child: null, controller: new AbortController(), timedOut: false };
    state.done = new Promise(resolve => { state.resolveDone = resolve; });
    active = state;
    const onDestroyed = () => abort(state);
    event.sender.once?.("destroyed", onDestroyed);
    try {
      const payloads = action === "batch" ? payload : [payload];
      if (!Array.isArray(payloads) || !payloads.length || payloads.length > 200) throw new Error("Use between 1 and 200 songs per batch.");
      const results = [];
      for (let index = 0; index < payloads.length; index++) {
        if (state.cancelled) break;
        const operation = action === "batch" ? "create" : action;
        const title = payloads[index]?.title || "Song";
        let directory;
        let timer;
        try {
          let value = typeof payloads[index] === "object" && payloads[index] !== null ? { ...payloads[index] } : payloads[index];
          if (operation === "analyze") validateUrl(value);
          directory = fs.mkdtempSync(path.join(app.getPath("temp"), "feedforge-songsterr-job-"));
          let outputDir, outputSettings;
          if (operation === "create") {
            outputDir = value.outputSettings?.outputDir || value.output_dir;
            outputSettings = normalizeOutputSettings(value.outputSettings || {});
            // The renderer's filename is only a preview. The shared Python
            // planner owns filename sanitization, template fields and folders.
            const checked = outputPayload({ ...value, output_dir: outputDir, output_name: "Song.feedpak" });
            value = { ...checked, outputSettings: { ...outputSettings, outputDir },
              generateDifficulty: outputSettings.generateDifficulty === true, output_path: path.join(directory, "result.feedpak") };
          }
          if (operation === "preview") value.preview_dir = directory;
          const request = path.join(directory, "request.json");
          fs.writeFileSync(request, JSON.stringify({ action: operation, payload: value }), { flag: "wx", mode: 0o600 });
          send(event, { stage: "Starting", title, index: index + 1, total: payloads.length });
          timer = setTimeout(() => { state.timedOut = true; abort(state); }, operationTimeout(operation, value));
          const result = await runConverter(["songsterr", request], {
            logOutput: false,
            directory, processGroup: true, admissionSignal: state.controller.signal,
            env: { SONGSTERR_NODE_PATH: process.execPath, ELECTRON_RUN_AS_NODE: "1" },
            onSpawn: child => {
              state.child = child;
              if (state.controller.signal.aborted) abort(state);
            },
            onStderrLine: line => {
              if (!line.startsWith("FEEDFORGE_PROGRESS ")) return;
              try { send(event, { ...JSON.parse(line.slice(19)), index: index + 1, total: payloads.length }); } catch { /* diagnostics retain malformed lines */ }
            }
          });
          state.child = null;
          check(state);
          let response;
          try { response = parseCoreResponse(result.stdout); } catch { response = null; }
          if (!response?.ok || result.code !== 0) throw new Error(response?.error || "Songsterr operation failed. Open Settings → Diagnostics for the log.");
          if (operation === "preview") {
            response.result.audio_url = localAssets.registerMedia(directory, response.result.audio_path);
            if (!response.result.audio_url) throw new Error("The audio preview was outside its temporary folder.");
            previews.add(directory);
          }
          if (operation === "create") {
            const staging = value.output_path;
            if (typeof response.result.output_path !== "string" || !path.isAbsolute(response.result.output_path)
                || fs.realpathSync.native(response.result.output_path) !== fs.realpathSync.native(staging)
                || fs.lstatSync(staging).isSymbolicLink() || !fs.statSync(staging).isFile()) throw new Error("The converter returned an invalid FeedPak file.");
            const validation = await runConverter(["--validate-feedpak", staging], {
              directory, logOutput: false, processGroup: true, admissionSignal: state.controller.signal,
              onSpawn: child => { state.child = child; if (state.controller.signal.aborted) abort(state); }
            });
            state.child = null;
            check(state);
            let checked;
            try { checked = JSON.parse(validation.stdout); } catch { /* handled below */ }
            if (validation.code !== 0 || checked?.ok !== true) throw new Error("The completed FeedPak did not pass validation.");
            const job = { id: crypto.randomUUID(), controller: state.controller, outputDir, outputSettings,
              outputRelativePath: response.result.relative_path, outputHash: await hashFile(staging, state.controller.signal) };
            const history = path.join(app.getPath("userData"), "songsterr-editor", "exports");
            await fs.promises.mkdir(history, { recursive: true });
            await publishFeedpak({ job, staging, check: () => check(state), hashFile,
              writeReceipt: (record, outputPath) => atomicJson(path.join(history, `${record.id}.json`), {
                id: record.id, outputPath, outputHash: record.outputHash, outputSettings,
                source: "songsterr-editor", sourceVerification: "not_checked", validation: "passed", createdAt: Date.now()
              }) });
            response.result.output_path = job.outputPath;
            response.result.sourceVerification = "not_checked";
          }
          results.push({ ok: true, ...response.result });
          if (action !== "batch") return response.result;
        } catch (error) {
          if (state.controller.signal.aborted) {
            try { check(state); } catch (cancelled) { error = cancelled; }
          }
          logDebug("songsterr.failed", { action, title, message: error.message });
          if (action !== "batch") throw error;
          results.push({ ok: false, title, error: error.message });
        } finally {
          clearTimeout(timer);
          if (state.termination) await state.termination.catch(error => logDebug("songsterr-editor.terminationFailed", { message: error.message }));
          state.child = null;
          state.termination = null;
          if (directory && !previews.has(directory)) await removeTemporaryDirectory(directory);
        }
      }
      return { results, created: results.filter(r => r.ok).length, failed: results.filter(r => !r.ok).length, cancelled: state.cancelled, skipped: payloads.length - results.length };
    } finally { active = null; event.sender.removeListener?.("destroyed", onDestroyed); state.resolveDone(); }
  }
  const handle = (name, callback) => ipcMain.handle(`songsterr-editor:${name}`, (event, payload) => {
    const win = window();
    if (win && (event.sender !== win.webContents || event.senderFrame !== win.webContents.mainFrame)) throw new Error("Songsterr editor requests must come from FeedForge.");
    if (closing) throw new Error("Songsterr editor is closing.");
    return callback(event, payload);
  });
  for (const action of ["analyze", "create", "batch", "lyrics", "preview", "search"]) {
    handle(action, (event, payload) => invoke(event, action, payload));
  }
  handle("cancel", () => { if (active) { active.cancelled = true; if (!active.child) abort(active); } return { stopping: Boolean(active) }; });
  handle("defaults", () => ({ output_dir: "" }));
  handle("cover", async () => {
    const result = await dialog.showOpenDialog(window(), { properties: ["openFile"], filters: [{ name: "Artwork", extensions: ["png", "jpg", "jpeg", "webp"] }] });
    if (result.canceled || !result.filePaths[0]) return null;
    const file = result.filePaths[0];
    if (fs.statSync(file).size > 20 * 1024 * 1024) throw new Error("Choose an image smaller than 20 MB.");
    const mime = { ".png": "image/png", ".webp": "image/webp" }[path.extname(file).toLowerCase()] || "image/jpeg";
    return { path: file, preview: `data:${mime};base64,${(await fs.promises.readFile(file)).toString("base64")}` };
  });
  handle("lrc", async event => {
    const result = await dialog.showOpenDialog(window(), { properties: ["openFile"], filters: [{ name: "Synchronized lyrics", extensions: ["lrc"] }] });
    return result.canceled || !result.filePaths[0] ? null : invoke(event, "lrc", result.filePaths[0]);
  });
  return { close };
}

module.exports = { registerSongsterr, validateUrl, outputPayload };

import React, { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { AlertTriangle, Check, ChevronLeft, ChevronRight, Download, ExternalLink, FileMusic, FolderOpen, LoaderCircle, Search, X } from "lucide-react";
import "./song-browser.css";
import { getSearchSession } from "./search-session.mjs";
import { BatchPanel } from './BatchPanel.jsx';
import { FileCandidateDetails } from './FileCandidateDetails.jsx';
import { createResultAssessmentSession } from './result-assessment-session.mjs';

const FINISHED = new Set(["completed", "done", "failed", "error", "cancelled", "canceled", "parked"]);
const selectionSessions = new WeakMap();
const COMPLETE = new Set(["completed", "done"]);
const OUTPUT_SETTINGS_ACTIONS = new Set(['enqueue', 'retry', 'prepareBatch', 'updateBatchPreferences', 'chooseBatch', 'startBatch', 'resumeBatch', 'retryBatchItem', 'resolveBatchItem', 'chooseFile', 'chooseBatchFile']);
const STATE_LABELS = {
  queued: "Queued", downloading: "Downloading", inspecting: "Checking song", converting: "Converting",
  validating: "Validating FeedPak", completed: "FeedPak ready", done: "FeedPak ready", failed: "Failed",
  error: "Failed", cancelled: "Cancelled", canceled: "Cancelled", needs_attention: "Needs attention",
  waiting: "Waiting", cancelling: "Cancelling", canceled_pending: "Cancelling", parked: "Needs attention"
};

function text(value, fallback = "") {
  if (typeof value === "string" || typeof value === "number") return String(value);
  return fallback;
}

function errorText(error) {
  return text(error?.message, text(error?.error, text(error, "Something went wrong. Please try again.")));
}

function stateOf(job) {
  return text(job.state || job.status, "queued").toLowerCase().replaceAll("-", "_");
}

function hostInfo(value) {
  const host = text(value).toLowerCase().replace(/[\s_-]/g, "");
  if (host === 'odlc') return { label: 'ODLC', available: false, official: true };
  if (host.includes("dropbox")) return { label: "Dropbox", available: true };
  if (host === "drive" || host.includes("googledrive") || host.includes("drive.google")) return { label: "Google Drive", available: true };
  if (host.includes("mediafire")) return { label: "MediaFire", available: true };
  if (host === 'onedrive') return { label: 'OneDrive · experimental', available: true };
  if (host === 'pcloud') return { label: 'pCloud · experimental', available: true };
  if (host === 'mega') return { label: 'MEGA · experimental', available: true };
  return { label: text(value, "Unknown host"), available: false };
}

function partsText(parts) {
  if (Array.isArray(parts)) return parts.map((part) => text(part, text(part?.name))).filter(Boolean).join(" · ");
  return text(parts);
}

export function ResultCard({ song, job, busy, canDownload, onDownload, onShowOutput, onReviewAnother, suitability, selected, onSelect }) {
  const host = hostInfo(song.host);
  const supported = song.supported === true && host.available;
  const jobState = job ? stateOf(job) : "";
  const savedOutput = COMPLETE.has(jobState) && job?.outputAvailable !== false;
  const completed = savedOutput && job?.inOutputDir !== false && (suitability ? suitability.reusable === true : job?.reuseCompatible !== false);
  const checkingOutput = suitability?.pending === true;
  const pending = job && !FINISHED.has(jobState);
  const parts = partsText(song.parts);
  const label = completed ? "FeedPak ready" : pending ? STATE_LABELS[jobState] || "In queue" : checkingOutput ? 'Checking saved output…' : busy ? "Adding…" : "Download & convert";

  return (
    <article className="sb-result">
      {onSelect ? <input type="checkbox" className="sb-chart-select" aria-label={`Select ${song.title} chart ${song.id}`} checked={selected === true && !host.official} disabled={host.official === true} onChange={(event) => onSelect(String(song.id), event.target.checked)} /> : null}
      <div className="sb-result-icon" aria-hidden="true"><FileMusic size={23} /></div>
      <div className="sb-result-info">
        <h3>{text(song.title, "Untitled song")}</h3>
        <p className="sb-artist">{text(song.artist, "Unknown artist")}{song.album ? <span> / {text(song.album)}</span> : null}</p>
        <div className="sb-metadata">
          <span>Chart by <strong>{text(song.creator, "Unknown creator")}</strong></span>
          {song.version ? <span>Version {text(song.version)}</span> : null}
          {song.tuning ? <span>{text(song.tuning)}</span> : null}
          {parts ? <span>{parts}</span> : null}
          {song.downloads != null ? <span>{song.downloads.toLocaleString()} downloads</span> : null}
        </div>
      </div>
      <div className="sb-result-actions">
        <span className={`sb-host ${supported ? "" : "sb-host-unavailable"}`}>{host.label}</span>
        {completed ? (
          <button type="button" className="sb-button sb-ready" onClick={() => onShowOutput(job.id)} disabled={busy}>
            <Check size={16} aria-hidden="true" /> FeedPak ready <FolderOpen size={14} aria-hidden="true" />
          </button>
        ) : (
          <button type="button" className="sb-button sb-primary" onClick={() => onDownload(song.id)} disabled={!supported || !canDownload || busy || Boolean(pending) || checkingOutput}>
            {busy || pending ? <LoaderCircle size={16} className={jobState === "queued" ? "" : "sb-spin"} aria-hidden="true" /> : <Download size={16} aria-hidden="true" />}
            {supported ? label : host.official ? 'ODLC not downloadable' : "Host not supported"}
          </button>
        )}
        {savedOutput && !completed ? <button type="button" className="sb-text-button" disabled={busy} onClick={() => onShowOutput(job.id)}>Show saved FeedPak</button> : null}
        {savedOutput && supported && onReviewAnother ? <button type="button" className="sb-text-button" disabled={busy || !canDownload} onClick={() => onReviewAnother(song.id)}>Review another file/version</button> : null}
        {savedOutput && !suitability?.reusable && job?.reuseCompatible === false ? <small>The saved file uses an older or unknown conversion recipe.</small> : null}
        {song.availability?.reason && !pending ? <small>{text(song.availability.reason)}</small> : null}
        {suitability?.reason && !pending ? <small>{text(suitability.reason)}</small> : null}
        {!supported ? <small>{host.official ? 'Official DLC (ODLC) is not available for download from CustomsForge.' : 'Automatic download is unavailable.'}</small> : !completed && !pending && !canDownload ? <small>Choose an output folder in Settings.</small> : null}
      </div>
    </article>
  );
}

export function JobCard({ job, busy, canSave = true, onCancel, onShowOutput, onShowBrowser, onRetry, onReview, onClearCache, onChooseFile, onRelax }) {
  const status = stateOf(job);
  const completed = COMPLETE.has(status);
  const missingOutput = completed && job.outputAvailable === false;
  const failed = status === "failed" || status === "error";
  const attention = status === "needs_attention";
  const rawProgress = Number(job.progress);
  const progress = Number.isFinite(rawProgress) ? Math.min(100, Math.max(0, rawProgress)) : null;
  const active = !FINISHED.has(status) && status !== "queued";
  return (
    <li className={`sb-job ${completed && !missingOutput ? "sb-job-completed" : failed ? "sb-job-failed" : ""}`}>
      <div className="sb-job-top">
        <div className="sb-job-heading"><strong>{text(job.title, "Song")}</strong>{job.artist ? <span>{text(job.artist)}</span> : null}</div>
        <span className="sb-job-status">{missingOutput ? <AlertTriangle size={14} aria-hidden="true" /> : completed ? <Check size={14} aria-hidden="true" /> : failed || attention ? <AlertTriangle size={14} aria-hidden="true" /> : active ? <LoaderCircle size={14} className="sb-spin" aria-hidden="true" /> : null}{missingOutput ? "File unavailable" : STATE_LABELS[status] || "In progress"}</span>
      </div>
      {status === 'needs_attention' && job.fileCandidates?.length ? <div className="sb-file-choices">{job.fileCandidates.map((candidate) => <button className="sb-button" key={candidate.id} type="button" disabled={busy || candidate.platform === 'mac'} onClick={() => onChooseFile?.(candidate.id)}><FileCandidateDetails candidate={candidate} /></button>)}</div> : null}
      {active && !attention ? <progress className="sb-progress" max="100" value={progress ?? undefined} aria-label={`${text(job.title, "Song")}: ${STATE_LABELS[status] || "In progress"}`} /> : null}
      <div className="sb-job-bottom">
        <p>{missingOutput ? "The converted file has been moved or removed. Find the chart in search results to download it again." : job.error ? errorText(job.error) : text(job.message, completed ? "Your feedpak is ready in the output folder." : status === "queued" ? "Waiting for the previous song to finish." : "")}</p>
        {job.warning ? <p role="status">{text(job.warning)}</p> : null}
        {completed && !missingOutput ? <button type="button" className="sb-text-button" disabled={busy} onClick={() => onShowOutput(job.id)}><FolderOpen size={14} aria-hidden="true" /> Show file</button> : !FINISHED.has(status) ? <div className="sb-job-controls">{attention ? <button type="button" className="sb-text-button" onClick={onShowBrowser}>Open browser</button> : null}<button type="button" className="sb-text-button" disabled={busy || status === "cancelling"} onClick={() => onCancel(job.id)}><X size={14} aria-hidden="true" />{busy || status === "cancelling" ? "Cancelling…" : "Cancel"}</button></div> : null}
      </div>
      {FINISHED.has(status) && (job.canRetry || job.hasCachedInput) ? <div className="sb-job-controls sb-recovery">
        {job.canRetry ? <button type="button" className="sb-text-button" disabled={busy || !canSave} onClick={() => onRetry(job.id)}>{job.hasCachedInput ? "Retry conversion" : "Retry download"}</button> : null}
        {job.hasCachedInput ? <><button type="button" className="sb-text-button" disabled={busy} onClick={() => onReview(job.id)}>Open in FeedForge</button><button type="button" className="sb-text-button" disabled={busy} onClick={() => onClearCache(job.id)}>Clear cached file</button></> : null}
        {job.hasCachedInput && onRelax && (job.selection?.backingStrict || job.selection?.instrumentRequirements?.some((entry) => entry.strict !== false) || job.selection?.parts?.length || job.selection?.tuning) ? <button type="button" className="sb-text-button" disabled={busy || !canSave} onClick={() => onRelax(job.id)}>Relax requirements and retry cached file</button> : null}
      </div> : null}
    </li>
  );
}

export default function SongBrowser({ api: providedApi, onReview, outputSettings, onOpenOutputSettings, onOutputDirChange }) {
  const api = providedApi ?? (typeof window !== "undefined" ? window.songBrowser : undefined);
  const available = typeof api?.getState === "function" && typeof api?.search === "function";
  const searchSession = useMemo(() => getSearchSession(api), [api]);
  const assessmentSession = useMemo(() => createResultAssessmentSession(api), [api]);
  const assessments = useSyncExternalStore(assessmentSession.subscribe, assessmentSession.getSnapshot, assessmentSession.getSnapshot);
  const searchState = useSyncExternalStore(searchSession.subscribe, searchSession.getSnapshot, searchSession.getSnapshot);
  const { query, searchedQuery, result: searchResult, pending: searching } = searchState;
  const [snapshot, setSnapshot] = useState({ outputDir: "", jobs: [], connection: { status: "unknown" } });
  const selectedOutputDir = outputSettings ? outputSettings.outputDir || '' : snapshot.outputDir;
  const outputReady = Boolean(selectedOutputDir);
  const [loading, setLoading] = useState(available);
  const [error, setError] = useState("");
  const [outputSettingsError, setOutputSettingsError] = useState('');
  const [appliedOutputSettingsKey, setAppliedOutputSettingsKey] = useState('');
  const [notice, setNotice] = useState("");
  const [feedbackAddress, setFeedbackAddress] = useState("");
  const [busy, setBusy] = useState(new Set());
  const [selected, setSelected] = useState(() => new Set(selectionSessions.get(api)?.ids || []));
  const [batchParts, setBatchParts] = useState([]);
  const [batchTuning, setBatchTuning] = useState('');
  const [ranking, setRanking] = useState('downloads');
  const [preferredCreators, setPreferredCreators] = useState('');
  const selectionScope = useRef(selectionSessions.get(api)?.scope || '');
  useEffect(() => {
    if (searchState.selectionScope && searchState.selectionScope !== selectionScope.current) {
      selectionScope.current = searchState.selectionScope; setSelected(new Set());
      if (api) selectionSessions.set(api, { scope: searchState.selectionScope, ids: [] });
    }
  }, [api, searchState.selectionScope]);
  const selectChart = (id, checked) => setSelected((previous) => {
    const next = new Set(previous); checked ? next.add(id) : next.delete(id);
    if (api) selectionSessions.set(api, { scope: selectionScope.current, ids: [...next] });
    return next;
  });
  const busyRef = useRef(new Set());
  const mounted = useRef(false);
  const outputSettingsSync = useRef({ api: null, key: '', failed: false, promise: Promise.resolve() });
  const outputSettingsKey = outputSettings ? JSON.stringify({
    ...(typeof outputSettings.outputDir === 'string' && outputSettings.outputDir ? { outputDir: outputSettings.outputDir } : {}),
    outputLayout: outputSettings.outputLayout || 'flat', nameTemplate: outputSettings.nameTemplate ?? '{source}',
  }) : '';

  function synchronizeOutputSettings(retry = false) {
    const previous = outputSettingsSync.current;
    if (!available || !outputSettingsKey || typeof api?.setOutputSettings !== 'function') {
      if (previous.api !== null) outputSettingsSync.current = { api: null, key: '', failed: false, promise: Promise.resolve() };
      return;
    }
    if (previous.api === api && previous.key === outputSettingsKey && !(retry && previous.failed)) return;
    const synchronization = { api, key: outputSettingsKey, failed: false, promise: null };
    synchronization.promise = previous.promise.catch(() => {}).then(() => api.setOutputSettings({ settings: JSON.parse(outputSettingsKey) })).then((next) => {
      if (next?.ok === false) throw new Error(errorText(next.error));
      if (mounted.current && outputSettingsSync.current === synchronization) {
        setOutputSettingsError('');
        setAppliedOutputSettingsKey(outputSettingsKey);
        if (next) setSnapshot((current) => ({ ...current, ...next }));
      }
      return next;
    }).catch((err) => {
      synchronization.failed = true;
      throw err;
    });
    outputSettingsSync.current = synchronization;
  }

  async function waitForOutputSettings(retry = false) {
    synchronizeOutputSettings(retry);
    // A newer Settings change may arrive while the previous request is in flight.
    // Actions must use the final applied settings, not the first completed call.
    while (true) {
      const synchronization = outputSettingsSync.current;
      try { await synchronization.promise; }
      catch (err) { if (outputSettingsSync.current === synchronization) throw err; }
      if (outputSettingsSync.current === synchronization) return;
    }
  }

  const jobs = Array.isArray(snapshot.jobs) ? snapshot.jobs : [];
  const feedback = snapshot.feedback || {};
  useEffect(() => { if (feedback.url) setFeedbackAddress(feedback.url); }, [feedback.url]);
  const connection = typeof snapshot.connection === "string" ? { status: snapshot.connection } : snapshot.connection || {};
  const connected = connection.status === "connected";
  const signedOut = ["signed_out", "sign_in_required", "login_required", "auth_required"].includes(connection.status);
  const challenge = connection.status === "challenge" || searchResult?.status === "challenge";
  const needsSignIn = ["signed_out", "sign_in_required", "login_required", "auth_required"].includes(searchResult?.status);
  const results = Array.isArray(searchResult?.results) ? searchResult.results : [];
  const page = Number(searchResult?.page) || 1;
  const activeCount = jobs.filter((job) => !FINISHED.has(stateOf(job))).length;
  useEffect(() => {
    let live = true;
    void waitForOutputSettings(activeCount === 0).catch((err) => { if (live) setOutputSettingsError(errorText(err)); });
    return () => { live = false; };
  }, [api, outputSettingsKey, activeCount]);
  const latestJobs = new Map();
  for (const job of jobs) {
    const key = String(job.chartId ?? job.songId ?? job.recordId ?? "");
    const current = latestJobs.get(key);
    if (!current || Number(new Date(job.updatedAt || job.createdAt || 0)) >= Number(new Date(current.updatedAt || current.createdAt || 0))) latestJobs.set(key, job);
  }
  const currentSelection = { backingTrack: 'any', backingStrict: false, instrumentRequirements: [], parts: searchState.searchedRequest?.filters.parts || [], tuning: searchState.searchedRequest?.filters.tuning || null, platform: 'pc', strictPlatform: true };
  const assessmentEntries = results.filter((song) => COMPLETE.has(stateOf(latestJobs.get(String(song.id)) || {}))).map((song) => {
    const job = latestJobs.get(String(song.id));
    return { id: String(song.id), version: song.version, updated: song.updated, jobId: job.id, outputHash: job.outputHash, outputAvailable: job.outputAvailable, recipe: job.recipe, reuseCompatible: job.reuseCompatible };
  });
  const assessmentScope = JSON.stringify({ entries: assessmentEntries, selection: currentSelection, outputDir: snapshot.outputDir, outputSettingsKey, appliedOutputSettingsKey });
  useEffect(() => {
    if (typeof api?.assessResult !== 'function') return;
    let live = true;
    void waitForOutputSettings().then(() => {
      if (live) return assessmentSession.assess(assessmentEntries, currentSelection, assessmentScope);
    }).catch((err) => { if (live) setOutputSettingsError(errorText(err)); });
    return () => { live = false; assessmentSession.cancel(); };
  }, [api, assessmentSession, assessmentScope]);

  useEffect(() => {
    mounted.current = true;
    let live = true;
    let receivedEvent = false;
    let unsubscribe;
    if (!available) { setLoading(false); return () => { mounted.current = false; }; }
    setLoading(true);
    Promise.resolve().then(() => {
      if (!live) return null;
      if (typeof api.onState === "function") unsubscribe = api.onState((next) => {
        if (!live || !next) return;
        receivedEvent = true;
        searchSession.setProgress(next.searchProgress);
        setSnapshot((current) => ({ ...current, ...next }));
      });
      return api.getState();
    }).then((next) => {
      if (next?.ok === false) throw new Error(errorText(next.error));
      if (live && next && !receivedEvent) { setSnapshot((current) => ({ ...current, ...next })); searchSession.setProgress(next.searchProgress); }
    }).catch((err) => { if (live) setError(errorText(err)); }).finally(() => { if (live) setLoading(false); });
    return () => { live = false; mounted.current = false; if (typeof unsubscribe === "function") unsubscribe(); };
  }, [api, available, searchSession]);

  async function action(key, method, args) {
    if (busyRef.current.has(key)) return null;
    busyRef.current.add(key);
    setBusy(new Set(busyRef.current));
    setError("");
    try {
      const handler = api?.[method];
      if (typeof handler !== "function") throw new Error("This action is unavailable in this version of FeedForge.");
      if (OUTPUT_SETTINGS_ACTIONS.has(method)) {
        await waitForOutputSettings(true);
        if (!selectedOutputDir) throw new Error('Choose an output folder in Settings before downloading songs.');
      }
      const result = await handler.call(api, args);
      if (result?.ok === false) throw new Error(errorText(result.error));
      if (mounted.current && ['chooseOutput', 'useFeedbackFolder'].includes(method) && result && Object.prototype.hasOwnProperty.call(result, "outputDir")) {
        setSnapshot((current) => ({ ...current, outputDir: result.outputDir }));
        if (method === 'useFeedbackFolder' && typeof onOutputDirChange === 'function') onOutputDirChange(result.outputDir);
      }
      if (["enqueue", "retry"].includes(method) && result && mounted.current) {
        const job = result.job || result;
        if (job.id) setSnapshot((current) => (current.jobs || []).some((item) => item.id === job.id) ? current : ({ ...current, jobs: [...(current.jobs || []), job] }));
      }
      return result;
    } catch (err) {
      if (mounted.current) setError(errorText(err));
      return null;
    } finally {
      busyRef.current.delete(key);
      if (mounted.current) setBusy(new Set(busyRef.current));
    }
  }

  function search(searchQuery, nextPage = 1) {
    if (!available) return;
    setError("");
    return searchSession.search({ query: searchQuery, page: nextPage, sort: searchState.sort, filters: searchState.filters });
  }

  const prepareBatch = (scope) => action('prepare-batch', 'prepareBatch', {
    scope, ids: [...selected], request: searchState.searchedRequest,
    preferences: { requiredParts: batchParts, tuning: batchTuning, ranking, preferredCreators: preferredCreators.split(/[,\n]/).map((creator) => creator.trim()).filter(Boolean), backingTrack: 'any', backingStrict: false, instrumentRequirements: [] },
  });

  const downloadSong = (id, reviewAnother = false) => action(`enqueue:${id}`, 'enqueue', { id, reviewAnother, selection: currentSelection });

  const showBrowser = () => action("browser", "showBrowser");
  const showOutput = (id) => action(`show:${id}`, "showOutput", { id });
  const reviewCached = async (id) => {
    const result = await action(`review:${id}`, "openCached", { id });
    if (result?.inputPath && onReview) {
      try { await onReview(result.inputPath); } catch (err) { if (mounted.current) setError(errorText(err)); }
    }
  };
  const exportReport = async () => {
    setNotice("");
    const result = await action("report", "exportDiagnostics");
    if (result?.exported && mounted.current) setNotice("Troubleshooting report saved.");
  };
  const searchError = searchState.error || (searchResult?.error ? errorText(searchResult.error) : searchResult?.status === "layout_changed" ? "CustomsForge’s search page could not be read. Open the browser to check it, then try again." : searchResult?.status === "error" ? "Search is temporarily unavailable. Please try again." : "");

  return (
    <section className="song-browser" aria-labelledby="sb-title">
      <header className="sb-header">
        <div><p className="sb-eyebrow">CUSTOMSFORGE SONG LIBRARY</p><h1 id="sb-title">Find songs</h1><p className="sb-description">Find a chart. Download it. Get a feedpak ready for Feedback.</p></div>
        {available ? <div className="sb-account"><span className={`sb-connection ${connected ? "sb-connected" : ""}`}><i aria-hidden="true" />{loading ? "Checking connection…" : connected ? "Connected" : connection.status === "error" ? "Search unavailable" : challenge ? "Browser check needed" : signedOut ? "Signed out" : "Connection not checked"}</span><button type="button" className="sb-button" disabled={busy.has("sign-in") || busy.has("browser")} onClick={signedOut ? () => action("sign-in", "signIn") : showBrowser}><ExternalLink size={15} aria-hidden="true" />{busy.has("sign-in") || busy.has("browser") ? "Opening…" : signedOut ? "Sign in" : "Open browser"}</button></div> : null}
      </header>

      {!available ? (
        <div className="sb-empty sb-desktop-message"><div className="sb-empty-icon"><Download size={28} aria-hidden="true" /></div><h2>Find songs in FeedForge desktop</h2><p>Searching CustomsForge and downloading songs needs the FeedForge desktop app. Open its Find songs tab to connect your account, choose an output folder, and convert songs here.</p></div>
      ) : <>
        <div className="sb-output"><div className="sb-output-label"><FolderOpen size={19} aria-hidden="true" /><div><strong>Output folder from Settings</strong><p title={text(selectedOutputDir)}>{text(selectedOutputDir) || 'Choose an output folder in Settings.'}</p></div></div><button type="button" className="sb-button" disabled={busy.has("folder")} onClick={onOpenOutputSettings || (() => action("folder", "chooseOutput"))}>{onOpenOutputSettings ? outputReady ? 'Change in Settings' : 'Choose in Settings' : busy.has("folder") ? 'Choosing…' : 'Choose folder'}</button></div>
        <p className="sb-library-help">{outputReady ? 'The output folder, file names, and folder layout all come from Settings.' : 'Settings currently uses source folders. Choose a fixed output folder there for downloaded songs.'} Choose your FeedBack song library to save songs there.</p>

        <details className="sb-feedback">
          <summary>FeedBack connection <span>{feedback.autoRefresh ? "Automatic refresh enabled" : "Optional library refresh"}</span></summary>
          <form onSubmit={(event) => { event.preventDefault(); action("feedback-connect", "connectFeedback", { url: feedbackAddress }); }}>
            <label htmlFor="sb-feedback-address">Address of FeedBack running on this computer</label>
            <div className="sb-search-row"><input id="sb-feedback-address" type="url" value={feedbackAddress} onChange={(event) => setFeedbackAddress(event.target.value)} placeholder="http://127.0.0.1:8000" required /><button className="sb-button" disabled={busy.has("feedback-connect")}>{busy.has("feedback-connect") ? "Connecting…" : "Connect"}</button></div>
          </form>
          {feedback.message ? <p role="status">{text(feedback.message)}</p> : <p>Use the address shown by your FeedBack app. You can also use Songs → Refresh directly in the game.</p>}
          {feedback.libraryDir ? <p className="sb-folder-path">FeedBack library: {text(feedback.libraryDir)}</p> : null}
          <div className="sb-feedback-actions">
            <button type="button" className="sb-text-button" disabled={feedback.status !== "connected" || activeCount > 0 || busy.has("feedback-folder")} onClick={() => action("feedback-folder", "useFeedbackFolder")}>Use as Settings output folder</button>
            <button type="button" className="sb-text-button" disabled={!feedback.url || feedback.status === "refreshing" || busy.has("feedback-refresh")} onClick={() => action("feedback-refresh", "refreshFeedback")}>Refresh library now</button>
          </div>
          <label className="sb-auto-refresh"><input type="checkbox" checked={feedback.autoRefresh === true} disabled={busy.has("feedback-auto") || (!feedback.autoRefresh && feedback.status !== "connected")} onChange={(event) => action("feedback-auto", "setAutoRefresh", { enabled: event.target.checked })} /> Refresh FeedBack after each successful conversion</label>
        </details>

        {notice ? <p className="sb-library-help" role="status">{notice}</p> : null}

        {(error || outputSettingsError || searchError) ? <div className="sb-notice sb-error" role="alert"><AlertTriangle size={18} aria-hidden="true" /><p>{error || outputSettingsError || searchError}</p></div> : null}
        {(challenge || needsSignIn || connection.status === "error") && !searchError ? <div className="sb-notice" role="status"><AlertTriangle size={18} aria-hidden="true" /><p>{text(connection.message, challenge ? "Complete the browser check in the CustomsForge window, then search again." : needsSignIn ? "Sign in to CustomsForge in the browser window, then search again." : "The CustomsForge connection needs attention. Open the browser to reconnect.")}</p><button type="button" className="sb-text-button" disabled={busy.has("browser")} onClick={showBrowser}>Open browser <ExternalLink size={14} aria-hidden="true" /></button></div> : null}

        <form className="sb-search-form" onSubmit={(event) => { event.preventDefault(); search(query); }}>
          <label htmlFor="sb-query">Search by artist or song title</label>
          <div className="sb-search-row"><div className="sb-search-input"><Search size={20} aria-hidden="true" /><input id="sb-query" type="search" value={query} onChange={(event) => searchSession.setQuery(event.target.value)} placeholder="Artist or song title" autoComplete="off" minLength={2} maxLength={160} /></div><button type="submit" className="sb-button sb-primary" disabled={query.trim().length < 2 || searching || loading}>{searching ? <LoaderCircle size={17} className="sb-spin" aria-hidden="true" /> : <Search size={17} aria-hidden="true" />}{searching ? "Searching…" : "Search"}</button></div>
          <div className="sb-filter-grid">
            <label>Sort results<select aria-label="Sort results" value={searchState.sort.field} onChange={(event) => searchSession.setSort({ ...searchState.sort, field: event.target.value })}>{[['title','Song title'],['artist','Artist'],['album','Album'],['downloads','Downloads'],['updated','Updated'],['added','Added'],['creator','Creator'],['tuning','Tuning'],['year','Year'],['duration','Duration']].map(([value,label]) => <option key={value} value={value}>{label}</option>)}</select></label>
            <label>Direction<select aria-label="Sort direction" value={searchState.sort.direction} onChange={(event) => searchSession.setSort({ ...searchState.sort, direction: event.target.value })}><option value="asc">Ascending</option><option value="desc">Descending</option></select></label>
            <label>Exact artist<input aria-label="Exact artist" value={searchState.filters.exactArtist} onChange={(event) => searchSession.setFilters({ exactArtist: event.target.value })} placeholder="Optional" /></label>
            <label>Creator<input aria-label="Creator filter" value={searchState.filters.creator} onChange={(event) => searchSession.setFilters({ creator: event.target.value })} placeholder="Optional" /></label>
            <label>Tuning<input aria-label="Tuning filter" value={searchState.filters.tuning} onChange={(event) => searchSession.setFilters({ tuning: event.target.value })} placeholder="For example Eb Standard" /></label>
          </div>
          <div className="sb-filter-checks">{['lead','rhythm','bass'].map((part) => <label key={part}><input type="checkbox" checked={searchState.filters.parts.includes(part)} onChange={(event) => searchSession.setFilters({ parts: event.target.checked ? [...searchState.filters.parts, part] : searchState.filters.parts.filter((value) => value !== part) })} />{part}</label>)}
            <label><input type="checkbox" checked={searchState.filters.availableOnly} onChange={(event) => searchSession.setFilters({ availableOnly: event.target.checked })} />Available hosts</label>
            <label><input type="checkbox" checked={searchState.filters.hideConverted} onChange={(event) => searchSession.setFilters({ hideConverted: event.target.checked })} />Hide converted</label>
            <label><input type="checkbox" checked={searchState.filters.hideReported} onChange={(event) => searchSession.setFilters({ hideReported: event.target.checked })} />Hide reported</label>
            <label><input type="checkbox" checked={searchState.filters.hideAbandoned} onChange={(event) => searchSession.setFilters({ hideAbandoned: event.target.checked })} />Hide abandoned</label>
          </div>
          <p>Press Search to apply sorting and filters to all matching results. Filtering may take longer while all pages are collected.</p>
          {searching && typeof api?.cancelSearch === 'function' ? <div className="sb-notice" role="status"><span>{searchState.progress ? `Collected ${searchState.progress.collected || 0}${searchState.progress.total != null ? ` of ${searchState.progress.total}` : ''} charts · ${searchState.progress.page || 0} pages read${searchState.progress.retrying ? ` · Retrying page, attempt ${searchState.progress.attempt || 1}` : ''}` : 'Waiting for search results…'}</span><button type="button" className="sb-button" onClick={() => searchSession.cancel()}>Cancel search</button></div> : null}
          <p>Dropbox, Google Drive and MediaFire are supported. OneDrive, pCloud and MEGA are experimental.</p>
          <p>CustomsForge’s download action also adds the chart to your collection.</p>
        </form>

        {searchResult?.status === 'ready' ? <section className="sb-batch-prepare" aria-label="Prepare song batch">
          <h2>Download several songs</h2>
          <p>{selected.size} charts selected across pages. Prepare all results to compare versions before starting.</p>
          <div className="sb-filter-checks"><span>Required arrangements:</span>{['lead','rhythm','bass'].map((part) => <label key={part}><input type="checkbox" checked={batchParts.includes(part)} onChange={(event) => setBatchParts((old) => event.target.checked ? [...old,part] : old.filter((value) => value !== part))} />{part}</label>)}</div>
          <div className="sb-filter-grid"><label>Required tuning<input aria-label="Batch required tuning" value={batchTuning} onChange={(event) => setBatchTuning(event.target.value)} placeholder="Any tuning" /></label><label>Prefer between compatible charts<select aria-label="Batch preference" value={ranking} onChange={(event) => setRanking(event.target.value)}><option value="downloads">Most downloads</option><option value="updated">Recently updated</option><option value="none">Review without popularity preference</option></select></label></div>
          <label>Preferred creators, in order<input aria-label="Preferred creators" value={preferredCreators} onChange={(event) => setPreferredCreators(event.target.value)} placeholder="Creator one, Creator two" /></label>
          <p>Listed creators are preferred after required arrangements and tuning. Other creators remain eligible.</p>
          <div className="sb-job-controls"><button type="button" className="sb-button" disabled={!outputReady || !selected.size || searching || busy.has('prepare-batch') || snapshot.preparation?.pending} onClick={() => prepareBatch('selected')}>Prepare selected</button><button type="button" className="sb-button sb-primary" disabled={!outputReady || searching || busy.has('prepare-batch') || snapshot.preparation?.pending || !results.length} onClick={() => prepareBatch('all')}>Prepare all results</button></div>
        </section> : null}
        {snapshot.preparation?.pending ? <div className="sb-notice" role="status"><span>Preparing results: {snapshot.preparation.collected}{snapshot.preparation.total != null ? ` of ${snapshot.preparation.total}` : ''}</span><button type="button" className="sb-button" onClick={() => action('cancel-preparation','cancelPreparation')}>Cancel preparation</button></div> : null}
        <BatchPanel batches={snapshot.batches} action={action} busy={busy} outputReady={outputReady} />

        <div className="sb-content-grid">
          <section className="sb-results" aria-label="Search results" aria-busy={searching}>
            <div className="sb-section-heading"><h2>{searchResult ? "Search results" : "Discover your next song"}</h2><span role="status">{searching ? "Searching CustomsForge…" : searchResult && results.length ? `${typeof searchResult.total === "number" ? searchResult.total.toLocaleString() : results.length} ${searchResult.total === 1 || (searchResult.total == null && results.length === 1) ? "chart" : "charts"}` : ""}</span></div>
            {results.length ? <><div className={`sb-result-list ${searching ? "sb-searching" : ""}`}>{results.map((song) => <ResultCard key={String(song.id)} song={song} job={latestJobs.get(String(song.id))} busy={busy.has(`enqueue:${song.id}`)} canDownload={outputReady && !searching} onDownload={(id) => downloadSong(id)} onReviewAnother={(id) => downloadSong(id, true)} suitability={typeof api?.assessResult === 'function' && assessmentEntries.some((entry) => entry.id === String(song.id)) ? assessments.scope === assessmentScope ? assessments.results[String(song.id)] || { pending: true, reusable: false } : { pending: true, reusable: false } : undefined} onShowOutput={showOutput} selected={selected.has(String(song.id))} onSelect={selectChart} />)}</div><nav className="sb-pagination" aria-label="Search results pages"><button type="button" className="sb-button" disabled={page <= 1 || searching} onClick={() => search(searchedQuery, page - 1)}><ChevronLeft size={16} aria-hidden="true" /> Previous</button><span>Page {page}</span><button type="button" className="sb-button" disabled={!searchResult.hasNext || searching} onClick={() => search(searchedQuery, page + 1)}>Next <ChevronRight size={16} aria-hidden="true" /></button></nav></> : <div className="sb-empty"><div className="sb-empty-icon">{searching ? <LoaderCircle size={28} className="sb-spin" aria-hidden="true" /> : <Search size={28} aria-hidden="true" />}</div><h3>{searching ? "Looking for your song…" : challenge || needsSignIn ? "Complete your connection" : searchError || error ? "Search needs attention" : searchResult ? "No charts found" : "Start with an artist or a song"}</h3><p>{searching ? "Results will appear here." : challenge || needsSignIn ? "Finish the step in the browser window, then run your search again." : searchError || error ? "Check the message above and try again." : searchResult ? `No results for “${searchedQuery}”. Try another title or a shorter artist name.` : "Compare chart versions, instruments and tunings before adding a song."}</p></div>}
          </section>

          <aside className="sb-activity" aria-labelledby="sb-activity-title"><div className="sb-section-heading"><h2 id="sb-activity-title">Song activity</h2>{activeCount ? <span className="sb-count">{activeCount} pending</span> : null}</div><p className="sb-activity-description">Songs are processed one at a time.</p>{jobs.length ? <ol className="sb-job-list">{jobs.map((job) => <JobCard key={job.id} job={job} canSave={outputReady} busy={["cancel", "show", "retry", "review", "clear"].some((key) => busy.has(`${key}:${job.id}`))} onCancel={(id) => action(`cancel:${id}`, "cancel", { id })} onRetry={(id) => action(`retry:${id}`, "retry", { id })} onRelax={(id) => action(`retry:${id}`, "retry", { id, relaxRequirements: true })} onReview={reviewCached} onClearCache={(id) => action(`clear:${id}`, "clearCache", { id })} onShowOutput={showOutput} onShowBrowser={showBrowser} onChooseFile={(id) => action(`file:${id}`, "chooseFile", { id })} />)}</ol> : <div className="sb-activity-empty"><Download size={22} aria-hidden="true" /><p>Your downloads and conversions will appear here.</p></div>}<button className="sb-text-button sb-export" type="button" disabled={busy.has("report")} onClick={exportReport}>Export troubleshooting report</button></aside>
        </div>
      </>}
    </section>
  );
}

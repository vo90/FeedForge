import React, { useEffect, useMemo, useRef, useState } from 'react';
import { AlertTriangle, Check, Download, FolderOpen, LoaderCircle, Search } from 'lucide-react';

const ACTIVE = new Set(['queued', 'resolving', 'downloading', 'converting', 'audio', 'aligning', 'validating', 'saving']);
const searchMemory = new WeakMap();
const LABELS = { queued: 'Queued', resolving: 'Checking revision', downloading: 'Retrieving tab', audio: 'Preparing audio', aligning: 'Aligning audio', converting: 'Creating FeedPak', validating: 'Validating', saving: 'Saving', completed: 'FeedPak ready', needs_audio: 'Audio needed', needs_login: 'Sign in needed', needs_attention: 'Needs attention', alignment_failed: 'Audio could not be aligned', failed: 'Failed', cancelled: 'Cancelled' };
function errorText(value) { return typeof value === 'string' ? value : value?.message || value?.error || 'The operation failed. Please try again.'; }

export function SongsterrJob({ job, api, action, busy }) {
  const [url, setUrl] = useState('');
  const active = ACTIVE.has(job.state);
  const needsAudio = ['needs_audio', 'alignment_failed'].includes(job.state);
  return <li className={`sb-job ${job.state === 'completed' ? 'sb-job-completed' : ''}`}>
    <div className="sb-job-top"><div className="sb-job-heading"><strong>{job.title}</strong><span>{job.artist}</span></div>
      <span className="sb-job-status">{active ? <LoaderCircle className="sb-spin" size={14} /> : job.state === 'completed' ? <Check size={14} /> : job.state !== 'cancelled' ? <AlertTriangle size={14} /> : null}{LABELS[job.state] || job.state}</span></div>
    {active ? <progress className="sb-progress" aria-label={`${job.title}: ${LABELS[job.state]}`} /> : null}
    <p>{job.error || job.message}</p>
    {job.revisionId ? <small>Approved revision {job.revisionId}</small> : null}
    {job.state === 'completed' ? <div className="sb-job-controls">{job.outputAvailable ? <button className="sb-text-button" disabled={busy} onClick={() => action(() => api.showOutput({ id: job.id }))}><FolderOpen size={14} /> Show file</button> : <span>The saved file has been moved or removed.</span>}</div> : null}
    {needsAudio ? <div className="st-audio-input">
      {job.state === 'needs_audio' && job.canRetryAudio ? <button className="sb-button" disabled={busy} onClick={() => action(() => api.retry({ id: job.id }))}>Retry audio detection</button> : null}
      <button className="sb-button" disabled={busy} onClick={() => action(() => api.chooseAudio({ id: job.id }))}>Choose audio file</button>
      <form onSubmit={(event) => { event.preventDefault(); action(() => api.useAudioUrl({ id: job.id, url })); }}>
        <label htmlFor={`audio-${job.id}`}>Or paste an audio or YouTube link</label>
        <input id={`audio-${job.id}`} type="url" required placeholder="https://…" value={url} onChange={(e) => setUrl(e.target.value)} disabled={busy} />
        <button className="sb-button" disabled={busy || !url.trim()}>Use audio & continue</button>
      </form>
      <small>Use the same recording as the tab. Timing is checked before the FeedPak is saved.</small>
    </div> : null}
    {job.state === 'needs_login' || job.canUseAccount ? <div className="sb-job-controls">
      <button className="sb-button" disabled={busy} onClick={() => action(() => api.signIn())}>Sign in to Songsterr</button>
      <button className="sb-button" disabled={busy} onClick={() => action(() => api.retry({ id: job.id, allowAccount: true }))}>Continue after signing in</button>
      <small>The account route creates an unpublished copy so Songsterr can export the tab.</small>
    </div> : null}
    <div className="sb-job-controls">
      {job.canRetry && !needsAudio && job.state !== 'needs_login' ? <button className="sb-text-button" disabled={busy} onClick={() => action(() => api.retry({ id: job.id }))}>Retry import</button> : null}
      {job.canCancel ? <button className="sb-text-button" disabled={busy} onClick={() => action(() => api.cancel({ id: job.id }))}>Cancel</button> : null}
    </div>
    {job.warnings?.length ? <details><summary>Import notes</summary><ul>{job.warnings.map((warning, index) => <li key={index}>{typeof warning === 'string' ? warning : warning.message}</li>)}</ul></details> : null}
  </li>;
}

export default function SongsterrBrowser({ api: providedApi, outputApi: providedOutputApi, outputSettings, onOpenOutputSettings }) {
  const api = providedApi ?? globalThis.window?.songsterrBrowser;
  const outputApi = providedOutputApi ?? globalThis.window?.songBrowser;
  const saved = api ? searchMemory.get(api) : null;
  const [snapshot, setSnapshot] = useState({ jobs: [], connection: {} });
  const [query, setQuery] = useState(saved?.query || ''), [results, setResults] = useState(saved?.results || []), [searched, setSearched] = useState(saved?.searched || false);
  const [sort, setSort] = useState(saved?.sort || 'relevance'), [searching, setSearching] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const [searchNotice, setSearchNotice] = useState(saved?.searchNotice || '');
  const searchGeneration = useRef(0), mounted = useRef(false), settingsSync = useRef(Promise.resolve());
  const settingsKey = JSON.stringify(outputSettings || {});
  useEffect(() => { if (api) searchMemory.set(api, { query, results, searched, sort, searchNotice }); }, [api, query, results, searched, sort, searchNotice]);
  useEffect(() => {
    mounted.current = true;
    if (!api) return () => { mounted.current = false; };
    api.getState().then((state) => { if (mounted.current) { if (state?.ok === false) setError(errorText(state)); else setSnapshot(state); } }).catch((err) => { if (mounted.current) setError(errorText(err)); });
    const unsubscribe = api.onState?.((state) => { if (mounted.current) setSnapshot(state); });
    return () => { mounted.current = false; searchGeneration.current++; unsubscribe?.(); };
  }, [api]);
  useEffect(() => {
    if (!outputSettings || !outputApi?.setOutputSettings) return;
    settingsSync.current = settingsSync.current.catch(() => {}).then(async () => {
      const response = await outputApi.setOutputSettings({ settings: JSON.parse(settingsKey) });
      if (response?.ok === false) throw new Error(errorText(response));
    });
    settingsSync.current.catch((err) => { if (mounted.current) setError(errorText(err)); });
  }, [settingsKey, outputApi]);
  async function action(operation) {
    if (busy) return;
    setBusy(true); setError('');
    try {
      while (true) {
        const pending = settingsSync.current;
        try { await pending; }
        catch (err) {
          if (pending === settingsSync.current && outputSettings && outputApi?.setOutputSettings) {
            settingsSync.current = outputApi.setOutputSettings({ settings: JSON.parse(settingsKey) }).then((response) => { if (response?.ok === false) throw new Error(errorText(response)); });
            await settingsSync.current;
          } else if (pending === settingsSync.current) throw err;
        }
        if (pending === settingsSync.current) break;
      }
      const response = await operation(); if (response?.ok === false) throw new Error(errorText(response));
      const state = await api.getState(); if (mounted.current && state?.ok !== false) setSnapshot(state);
    } catch (err) { if (mounted.current) setError(errorText(err)); }
    finally { if (mounted.current) setBusy(false); }
  }
  async function search(event) {
    event.preventDefault(); if (!api || !query.trim()) return;
    const generation = ++searchGeneration.current;
    setSearching(true); setError('');
    try {
      const response = await api.search({ query: query.trim() });
      if (!mounted.current || generation !== searchGeneration.current) return;
      if (response?.ok === false || !['ready', 'cancelled'].includes(response?.status)) throw new Error(response?.message || errorText(response));
      if (response.status === 'ready') { setResults(response.results || []); setSearched(true); setSearchNotice(response.message || ''); }
    } catch (err) { if (mounted.current && generation === searchGeneration.current) setError(errorText(err)); }
    finally { if (mounted.current && generation === searchGeneration.current) setSearching(false); }
  }
  const ordered = useMemo(() => sort === 'relevance' ? results : [...results].sort((a, b) => String(a[sort] || '').localeCompare(String(b[sort] || ''), undefined, { numeric: true, sensitivity: 'base' })), [results, sort]);
  const outputDir = outputSettings?.outputDir ?? snapshot.outputDir;
  const jobs = snapshot.jobs || [];
  return <section className="song-browser st-browser" aria-label="Songsterr song library">
    <header className="sb-header"><div><p className="sb-eyebrow">SONGSTERR SONG LIBRARY</p><h1>Find songs</h1><p>Choose a tab. Get a FeedPak with guitar, bass and original audio.</p></div>
      <div className="sb-job-controls"><button className="sb-button" disabled={!api || busy} onClick={() => action(() => api.showBrowser())}>Open Songsterr</button><button className="sb-button" disabled={!api || busy} onClick={() => action(() => api.signIn())}>Songsterr sign in</button></div></header>
    <div className="sb-output"><FolderOpen size={20} /><div><strong>FeedForge output folder</strong><p>{outputDir || 'Choose an output folder in Settings.'}</p><small>Uses the filename and folder layout from Settings.</small></div><button className="sb-button" onClick={onOpenOutputSettings}>Open Settings</button></div>
    <p className="st-experimental">Songsterr import is experimental. The latest approved revision and audio timing are checked before saving.</p>
    {!api ? <p role="alert" className="sb-error">Songsterr import is available in the desktop app.</p> : null}
    {error ? <p role="alert" className="sb-error"><AlertTriangle size={18} />{error}</p> : null}
    <form className="st-search" onSubmit={search}><label htmlFor="songsterr-query">Search by artist or song title</label><div className="st-search-row"><input id="songsterr-query" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Artist or song title" minLength={2} maxLength={160} /><button className="sb-button sb-primary" disabled={!api || searching || !query.trim()}>{searching ? <LoaderCircle size={18} className="sb-spin" /> : <Search size={18} />}Search</button>{searching ? <button type="button" className="sb-button" onClick={() => { searchGeneration.current++; api.cancelSearch(); setSearching(false); }}>Cancel</button> : null}</div></form>
    <div className="st-results-layout"><section aria-label="Songsterr search results"><div className="st-results-heading"><h2>{searched ? `${results.length} search results` : 'Discover your next song'}</h2><label>Sort these results <select value={sort} onChange={(e) => setSort(e.target.value)}><option value="relevance">Relevance</option><option value="title">Song title</option><option value="artist">Artist</option></select></label></div>
      {searchNotice ? <p>{searchNotice}</p> : null}
      {!results.length ? <p className="st-empty">{searched ? 'No matching songs. Try another artist or title.' : 'Search Songsterr to get started.'}</p> : ordered.map((song) => {
        const job = [...jobs].reverse().find((entry) => entry.songId === String(song.id) && entry.state !== 'cancelled' && entry.state !== 'failed');
        const pending = job && job.state !== 'completed';
        return <article className="sb-result" key={song.id}><div className="sb-result-info"><h3>{song.title}</h3><p className="sb-artist">{song.artist}</p><small>Songsterr · All playable guitar and bass tracks</small></div><div className="sb-result-actions"><button className="sb-button sb-primary" disabled={!api || busy || !outputDir || Boolean(pending)} onClick={() => action(() => api.enqueue({ id: String(song.id) }))}><Download size={16} />{pending ? LABELS[job.state] : 'Download & convert'}</button>{job?.state === 'completed' && job.outputAvailable ? <button className="sb-text-button" onClick={() => action(() => api.showOutput({ id: job.id }))}>Show saved FeedPak</button> : null}</div></article>;
      })}</section><aside><h2>Song activity</h2><p>Songs are processed one at a time.</p><ul className="st-jobs">{[...jobs].reverse().map((job) => <SongsterrJob key={job.id} job={job} api={api} action={action} busy={busy} />)}</ul></aside></div>
  </section>;
}

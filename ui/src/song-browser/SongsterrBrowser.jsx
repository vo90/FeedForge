import React, { useEffect, useMemo, useRef, useState } from 'react';
import { AlertTriangle, Check, Download, FolderOpen, LoaderCircle, Search } from 'lucide-react';

const ACTIVE = new Set(['queued', 'resolving', 'downloading', 'converting', 'audio', 'aligning', 'validating', 'saving']);
const searchMemory = new WeakMap();
ACTIVE.add('retry_wait');
const LABELS = { queued: 'Queued', resolving: 'Checking revision', downloading: 'Retrieving tab', audio: 'Preparing audio', aligning: 'Aligning audio', converting: 'Creating FeedPak', validating: 'Validating', saving: 'Saving', completed: 'FeedPak ready', needs_audio: 'Audio needed', needs_login: 'Sign in needed', needs_attention: 'Needs attention', alignment_failed: 'Audio could not be aligned', failed: 'Failed', cancelled: 'Cancelled' };
function errorText(value) { return typeof value === 'string' ? value : value?.message || value?.error || 'The operation failed. Please try again.'; }
LABELS.retry_wait = 'Waiting to retry';

function RetryStatus({ job }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => { if (job.state !== 'retry_wait') return; const timer = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(timer); }, [job.state]);
  if (job.state === 'retry_wait') return <p role="status">{job.retry?.reason === 'provider_busy' ? 'Waiting for the Songsterr browser.' : `Temporary retrieval problem. Retrying in ${Math.max(0, Math.ceil((job.retry.nextAt - now) / 1000))} seconds — attempt ${job.retry.attempt} of ${job.retry.maxAttempts}.`}</p>;
  if (job.retry?.parked && job.retry.nextAt) return <p>Next eligible retry: {new Date(job.retry.nextAt).toLocaleString()}.</p>;
  if (job.retry?.failures && job.state === 'completed') return <small>Completed after automatic recovery ({job.retry.attempt} of {job.retry.maxAttempts} attempts).</small>;
  return null;
}

const CATEGORY = { unknown_semantics: 'Needs interpretation', source_interpretation: 'Needs interpretation', converter_gap: 'Converter support', game_representation: 'Game representation', game_limitation: 'Game limitation', source_structure: 'Source structure', conversion_check: 'Conversion check', audio_alignment: 'Audio alignment' };
const WORK = { decision_required: 'Design decision needed', technical_work: 'Conversion work needed', display_limitation: 'Display limitation', gameplay_omission: 'Omitted from gameplay and scoring', fixed_verified: 'Resolved and verified' };
export function CompatibilityDetails({ report }) {
  if (!report) return null;
  return <div className="st-compatibility"><p>{report.findingCount || 0} compatibility findings. Original information is retained; retention does not mean the game displays or scores it.</p>
    {report.arrangements?.length ? <details open><summary>Arrangement checks</summary>
      <p>Every requested arrangement is checked before saving. Unsupported high-fret events can be omitted with a recorded limitation. A passed score check still needs audio alignment and final file verification.</p>
      <ul>{report.arrangements.map(item => <li key={item.trackIndex}><strong>{item.name}</strong> · {item.status === 'score_ready' ? 'Score check passed' : 'Needs attention'}
        {item.message ? <p>{item.message}</p> : item.blockingFeatures?.length ? <p>{item.blockingFeatures.join(', ')}</p> : null}</li>)}</ul>
    </details> : null}
    <ul>{report.findings.map((item, i) => <li key={i}><strong>{item.feature}</strong> · {CATEGORY[item.category] || item.category}
      <p>{[item.arrangement, item.measure && `Measure ${item.measure}`, item.beat && `beat ${item.beat}`, item.note && `note ${item.note}`].filter(Boolean).join(' · ')}</p>
      <p>{item.message}</p>{item.workStatus ? <p>{WORK[item.workStatus] || item.workStatus}</p> : null}<small>Source value: {JSON.stringify(item.value)}{item.valueTruncated ? '… (full value in saved source)' : ''}</small></li>)}</ul>
    {report.findingCount > report.findings.length ? <p>Showing the first {report.findings.length} findings. Save the conversion report for the complete recorded list.</p> : null}
  </div>;
}

export function CompatibilityList({ report }) {
  return <div className="st-compatibility"><p>Latest assessment per song revision. Repeated attempts are counted once.</p>
    {report.unreadable ? <p role="alert">{report.unreadable} saved reports could not be read.</p> : null}
    {!report.groups.length ? <p>No recorded compatibility gaps.</p> : <ul>{report.groups.map((group, i) => <li key={i}>
      <strong>{group.feature}</strong> · {CATEGORY[group.category] || group.category}<p>{WORK[group.workStatus] || group.workStatus}</p><p>{group.affectedSongs} affected song revisions · {group.occurrences} occurrences</p>
      <p>{group.message}</p><details><summary>Examples</summary><ul>{group.examples.map((row, j) => <li key={j}>{row.artist} — {row.title}, {row.arrangement}{row.measure ? `, measure ${row.measure}` : ''}</li>)}</ul></details>
    </li>)}</ul>}
    {report.resolved?.length ? <details><summary>{report.resolved.length} previously recorded gaps resolved and verified</summary><ul>{report.resolved.map((item, i) => <li key={i}>{item.artist} — {item.title}: {item.feature}</li>)}</ul></details> : null}
  </div>;
}

export function SongsterrJob({ job, api, action, busy }) {
  const [url, setUrl] = useState('');
  const [compatibility, setCompatibility] = useState(null);
  useEffect(() => { setCompatibility(null); }, [job.id, job.evidence?.id, job.state]);
  const active = ACTIVE.has(job.state);
  const needsAudio = ['needs_audio', 'alignment_failed'].includes(job.state);
  return <li className={`sb-job ${job.state === 'completed' ? 'sb-job-completed' : ''}`}>
    <div className="sb-job-top"><div className="sb-job-heading"><strong>{job.title}</strong><span>{job.artist}</span></div>
      <span className="sb-job-status">{active ? <LoaderCircle className="sb-spin" size={14} /> : job.state === 'completed' ? <Check size={14} /> : job.state !== 'cancelled' ? <AlertTriangle size={14} /> : null}{job.state === 'completed' && job.verification?.omissions?.omittedNotes ? 'Ready with omitted notes' : job.state === 'completed' && job.compatibility?.status === 'limitations' ? 'Ready with limitations' : LABELS[job.state] || job.state}</span></div>
    {job.verification?.omissions?.omittedNotes ? <p>{job.verification.omissions.omittedNotes} high-fret or connected slide events omitted from display and scoring. The complete original tab is saved inside the FeedPak.{job.verification.omissions.excludedTracks?.length ? ` ${job.verification.omissions.excludedTracks.length} arrangements had no supported notes remaining.` : ''}</p> : null}
    {active ? <progress className="sb-progress" aria-label={`${job.title}: ${LABELS[job.state]}`} /> : null}
    <p>{job.error || job.message}</p>
    <RetryStatus job={job} />
    {job.revisionId ? <small>Approved revision {job.revisionId}</small> : null}
    {job.state === 'completed' ? <p>{job.verification?.status === 'passed' ? job.verification?.adjustments?.omittedEndingNotes ? 'Conversion checked against the source tab and recorded ending adjustments.' : job.verification?.adjustments?.terminalSustains ? 'Conversion checked against the source tab and recorded sustain adjustments.' : 'Conversion checked against the source tab.' : job.verification?.status === 'modified' ? 'This file has changed since conversion. The report describes the original import.' : 'Source verification is unavailable for this file.'}</p> : null}
    {job.state === 'completed' && job.verification?.status === 'passed' && job.verification?.adjustments?.omittedEndingNotes ? <p>{job.verification.adjustments.omittedEndingNotes} ending {job.verification.adjustments.omittedEndingNotes === 1 ? 'note omitted' : 'notes omitted'} at or after the audio ending. Earlier timing passed the audio check. The original tab and omission details are saved in the FeedPak.</p> : null}
    {job.state === 'completed' && job.verification?.adjustments?.terminalSustains ? <p>{job.verification.adjustments.terminalSustains} final {job.verification.adjustments.terminalSustains === 1 ? 'sustain' : 'sustains'} shortened to the audio ending. Original durations are saved with the tab.</p> : null}
    {job.state === 'completed' && job.verification?.status === 'passed' && job.verification?.adjustments?.terminalSlideOuts ? <p>{job.verification.adjustments.terminalSlideOuts} directional {job.verification.adjustments.terminalSlideOuts === 1 ? 'slide-out ends' : 'slide-outs end'} with the audio after a timing check. The original slide intervals and cutoff details are saved in the FeedPak.</p> : null}
    {job.verification?.timing ? <small>{job.verification.timing === 'source_map_repaired' ? 'Timing: opening corrected against the recording; later Songsterr timing retained.' : job.verification.timing === 'source_map' ? 'Timing: Songsterr recording map.' : 'Timing: automatic estimate.'}</small> : null}
    {job.verification?.timingAssessment ? <small>Audio timing: {job.verification.timingAssessment.supportedWindows} of {job.verification.timingAssessment.windowCount} passages supported by automatic checks. Individual notes may remain uncertain.</small> : null}
    {job.verification?.preparationSeconds > 0 ? <small>Preparation time added: {job.verification.preparationSeconds.toFixed(2)} seconds.</small> : null}
    {job.verification?.endingSilenceSeconds > 0 ? <small>Ending silence added: {job.verification.endingSilenceSeconds.toFixed(2)} seconds to preserve the final note tails. This adds no missing music.</small> : null}
    {job.state === 'completed' && job.verification?.status === 'passed' && job.verification?.endingTiming?.status === 'supported' ? <small>Recording timing supported through the ending. This does not verify every transcribed note.</small> : null}
    {job.verification?.compatibility?.status === 'requires_consumer_support' ? <p>Ghost-note or directional-slide symbols need a FeedBack version that supports them. Their original meaning is preserved in this file.</p> : null}
    {job.artwork ? <p>{job.artwork.status === 'matched' ? `Album cover: ${job.artwork.album || 'matched album'}.` : 'Album cover unavailable or uncertain. You can add an image in Edit FeedPaks.'}</p> : null}
    {job.state === 'completed' ? <div className="sb-job-controls">{job.outputAvailable ? <button className="sb-text-button" disabled={busy} onClick={() => action(() => api.showOutput({ id: job.id }))}><FolderOpen size={14} /> Show file</button> : <span>The saved file has been moved or removed.</span>}</div> : null}
    {needsAudio ? <div className="st-audio-input">
      {job.state === 'needs_audio' && job.canRetryAudio ? <button className="sb-button" disabled={busy} onClick={() => action(() => api.retry({ id: job.id }))}>Retry audio detection</button> : null}
      {job.state === 'needs_audio' && job.canRetryRecording ? <button className="sb-button" disabled={busy} onClick={() => action(() => api.retry({ id: job.id }))}>Retry same recording</button> : null}
      {job.state === 'alignment_failed' && job.canRetry ? <button className="sb-button" disabled={busy} onClick={() => action(() => api.retry({ id: job.id }))}>Retry synchronization</button> : null}
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
      {job.compatibility && api.compatibilityDetails ? <button className="sb-text-button" disabled={busy} onClick={() => action(async () => { const result = await api.compatibilityDetails({ id: job.id }); if (result?.ok === false) return result; setCompatibility(result); })}>View compatibility details</button> : null}
      {job.hasReport && api.exportReport ? <button className="sb-text-button" disabled={busy} onClick={() => action(() => api.exportReport({ id: job.id }))}>Save conversion report</button> : null}
      {job.canRetry && !needsAudio && job.state !== 'needs_login' ? <button className="sb-text-button" disabled={busy} onClick={() => action(() => api.retry({ id: job.id }))}>Retry import</button> : null}
      {job.canCancel ? <button className="sb-text-button" disabled={busy} onClick={() => action(() => api.cancel({ id: job.id }))}>Cancel</button> : null}
    </div>
    <CompatibilityDetails report={compatibility} />
    {job.warnings?.length ? <details><summary>Import notes</summary><ul>{job.warnings.map((warning, index) => <li key={index}>{typeof warning === 'string' ? warning : `${warning.location ? warning.location + ': ' : ''}${warning.message}`}</li>)}</ul></details> : null}
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
  const [compatibilityList, setCompatibilityList] = useState(null);
  const searchGeneration = useRef(0), mounted = useRef(false), settingsSync = useRef(Promise.resolve());
  const settingsKey = JSON.stringify(outputSettings || {});
  useEffect(() => { if (api) searchMemory.set(api, { query, results, searched, sort, searchNotice }); }, [api, query, results, searched, sort, searchNotice]);
  useEffect(() => {
    mounted.current = true;
    if (!api) return () => { mounted.current = false; };
    api.getState().then((state) => { if (mounted.current) { if (state?.ok === false) setError(errorText(state)); else setSnapshot(state); } }).catch((err) => { if (mounted.current) setError(errorText(err)); });
    const unsubscribe = api.onState?.((state) => { if (mounted.current) setSnapshot(state); });
    const refresh = () => {
      if (globalThis.document?.visibilityState === 'hidden') return;
      api.getState().then((state) => { if (mounted.current && state?.ok !== false) setSnapshot(state); }).catch(() => {});
    };
    globalThis.window?.addEventListener('focus', refresh);
    globalThis.document?.addEventListener('visibilitychange', refresh);
    const timer = setInterval(refresh, 30000);
    return () => { mounted.current = false; searchGeneration.current++; unsubscribe?.(); clearInterval(timer);
      globalThis.window?.removeEventListener('focus', refresh); globalThis.document?.removeEventListener('visibilitychange', refresh); };
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
    {api?.compatibilityList ? <details className="st-compatibility-list"><summary>Import compatibility</summary><p>Recorded features that need converter improvements or additional game support.</p>
      <button className="sb-button" disabled={busy} onClick={() => action(async () => { const result = await api.compatibilityList(); if (result?.ok === false) return result; setCompatibilityList(result); })}>Load compatibility list</button>
      <button className="sb-button" disabled={busy} onClick={() => action(() => api.exportCompatibility())}>Save compatibility list</button>
      {compatibilityList ? <CompatibilityList report={compatibilityList} /> : null}</details> : null}
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

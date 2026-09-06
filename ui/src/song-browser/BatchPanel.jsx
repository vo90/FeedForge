import React, { useEffect, useState } from 'react';
import { FileCandidateDetails } from './FileCandidateDetails.jsx';

const labels = { draft: 'Review selections', running: 'Running', paused: 'Paused', completed: 'Finished', cancelled: 'Cancelled' };
const availabilityLabels = { available: 'Already available', new_conversion: 'New conversion', choose_file: 'Another file choice needed', insufficient_evidence: 'Needs verification' };

function DraftPreferences({ batch, run, working }) {
  const [draft, setDraft] = useState(batch.preferences);
  const [creators, setCreators] = useState((batch.preferences.preferredCreators || []).join(', '));
  const preferenceKey = JSON.stringify(batch.preferences);
  useEffect(() => { setDraft(batch.preferences); setCreators((batch.preferences.preferredCreators || []).join(', ')); }, [preferenceKey]);
  return <details className="sb-draft-preferences"><summary>Change draft preferences</summary>
    <form onSubmit={(event) => { event.preventDefault(); run('updateBatchPreferences', { preferences: { ...draft, preferredCreators: creators.split(/[,\n]/).map((value) => value.trim()).filter(Boolean), backingTrack: 'any', backingStrict: false, instrumentRequirements: [] } }); }}>
      <label>Preferred creators, in order<input aria-label="Draft preferred creators" value={creators} onChange={(event) => setCreators(event.target.value)} /></label>
      <div className="sb-filter-checks">{['lead','rhythm','bass'].map((part) => <label key={part}><input type="checkbox" checked={draft.requiredParts.includes(part)} onChange={(event) => setDraft({ ...draft, requiredParts: event.target.checked ? [...draft.requiredParts,part] : draft.requiredParts.filter((value) => value !== part) })} />Require {part}</label>)}</div>
      <div className="sb-filter-grid"><label>Required tuning<input aria-label="Draft required tuning" value={Array.isArray(draft.tuning) ? draft.tuning.join(', ') : draft.tuning} onChange={(event) => setDraft({ ...draft, tuning: event.target.value })} /></label>
        <label>Preference<select aria-label="Draft ranking" value={draft.ranking} onChange={(event) => setDraft({ ...draft, ranking: event.target.value })}><option value="downloads">Most downloads</option><option value="updated">Recently updated</option><option value="none">No popularity preference</option></select></label></div>
      <p>Changing recommendations preserves your manual selections. Any new conflicts must be reviewed before Start.</p>
      <button className="sb-button" type="submit" disabled={working}>Update recommendations</button>
    </form>
  </details>;
}

export function BatchPanel({ batches = [], action, busy = new Set(), outputReady = true }) {
  if (!batches.length) return null;
  return <section className="sb-batches" aria-label="Song batches">
    <h2>Song batches</h2>
    {batches.map((batch) => {
      const selected = new Set(batch.selectedIds);
      const charts = new Map(batch.charts.map((chart) => [chart.id, chart]));
      const working = [...busy].some((key) => key.startsWith('batch:' + batch.id + ':'));
      const run = (method, args = {}) => action('batch:' + batch.id + ':' + method, method, { id: batch.id, ...args });
      const counts = batch.counts || {}, planned = batch.plannedCounts || {};
      const select = (id, checked) => { const next = new Set(selected); checked ? next.add(id) : next.delete(id); run('chooseBatch', { selectedIds: [...next] }); };
      const chartInfo = (chart, option) => <span><strong>{chart?.creator || 'Unknown creator'}</strong> · {chart?.tuning || 'Tuning unknown'} · {option.parts.join(', ') || 'Parts unknown'}
        {chart?.version ? ' · v' + chart.version : ''}{chart?.downloads != null ? ' · ' + chart.downloads + ' downloads' : ''}</span>;
      return <details key={batch.id} className="sb-batch" open={batch.state === 'draft' || batch.state === 'running' || batch.state === 'paused'}>
        <summary>{labels[batch.state] || batch.state} · {selected.size} charts · {batch.groups.length} song groups</summary>
        <p className="sb-folder-path">Output: {batch.outputDir}</p>
        {batch.warning || batch.pauseReason ? <p role="status">{batch.warning || batch.pauseReason}</p> : null}
        {batch.state === 'draft' ? <>
          <p>Review alternative charts below. Recommendations apply required arrangements and tuning before creator preferences and ranking. Arrangements and tuning are checked again in the downloaded file.</p>
          <p role="status">{planned.downloads || 0} planned downloads · {planned.available || 0} already available · {batch.unresolvedCount} unresolved groups</p>
          <p>Availability is checked again when running. File choices may appear after the host is opened.</p>
          <DraftPreferences batch={batch} run={run} working={working} />
          {batch.reviewConflicts?.length ? <div className="sb-notice sb-error" role="alert"><p>{batch.reviewConflicts.join(' ')} Review your checked charts before starting.</p></div> : null}
          <div className="sb-batch-groups">
            {batch.groups.map((group) => <details key={group.key} open={group.unresolved}>
              <summary>{group.artist} — {group.title} · {group.options.filter((option) => selected.has(option.id)).length} selected</summary>
              {group.reasons?.length ? <p>{group.reasons.join(' ')}</p> : null}
              {group.options.map((option) => {
                const chart = charts.get(option.id), availability = batch.availability?.[option.id];
                const reviewAnother = batch.forceReviewIds?.includes(option.id);
                return <div className="sb-batch-option-wrap" key={option.id}><label className="sb-batch-option">
                  <input type="checkbox" aria-label={'Select ' + group.title + ' chart ' + option.id} checked={selected.has(option.id)} disabled={working || (!option.eligible && !selected.has(option.id))} onChange={(event) => select(option.id, event.target.checked)} />
                  <span>{chartInfo(chart, option)}<small>{group.recommendedIds.includes(option.id) ? 'Recommended. ' : ''}{option.reasons.join(' ')}{option.verificationPending ? ' Requirements must be checked in the downloaded file.' : ''}</small></span>
                </label>
                  <p className="sb-availability">{reviewAnother ? 'Review another file/version' : availabilityLabels[availability?.status] || 'Needs verification'}{availability?.reason ? ': ' + availability.reason : ''}</p>
                  {option.eligible && (availability?.status === 'available' || reviewAnother) ? <label className="sb-auto-refresh"><input type="checkbox" checked={reviewAnother === true} disabled={working} onChange={(event) => { const ids = new Set(batch.forceReviewIds || []); event.target.checked ? ids.add(option.id) : ids.delete(option.id); run('chooseBatch', { selectedIds: [...selected], forceReviewIds: [...ids] }); }} />Review another file/version instead of keeping the available output</label> : null}
                </div>;
              })}
            </details>)}
          </div>
          {(batch.suggestions || []).filter((suggestion) => !batch.dismissedSuggestions?.includes(suggestion.id)).map((suggestion) => <details className="sb-duplicate-suggestion" key={suggestion.id}>
            <summary>Possible duplicate: {suggestion.groupKeys.map((key) => batch.groups.find((group) => group.key === key)?.title).join(' / ')}</summary>
            <p>{suggestion.reason} Your checked selections have not changed.</p>
            <div className="sb-comparison">{suggestion.groupKeys.map((key) => { const group = batch.groups.find((entry) => entry.key === key); return group ? <div key={key}><strong>{group.title}</strong>{group.options.map((option) => <div key={option.id}>{chartInfo(charts.get(option.id), option)}<button type="button" className="sb-text-button" disabled={working || (!option.eligible && !selected.has(option.id))} onClick={() => select(option.id, !selected.has(option.id))}>{selected.has(option.id) ? 'Deselect chart' : 'Keep chart'}</button></div>)}</div> : null; })}</div>
            <button type="button" className="sb-button" disabled={working} onClick={() => run('dismissBatchSuggestion', { suggestionId: suggestion.id })}>Keep selections and dismiss suggestion</button>
          </details>)}
          {batch.suggestionsLimited ? <p>Possible-duplicate checks reached their review limit. Your complete chart list remains available.</p> : null}
          <button type="button" className="sb-button sb-primary" disabled={!outputReady || working || !selected.size || !batch.complete || !!batch.reviewConflicts?.length} onClick={() => run('startBatch')}>Start batch ({selected.size} charts)</button>
        </> : <>
          <p role="status">{counts.completed || 0} converted · {counts.available || 0} already available · {counts.userSkipped || 0} skipped by you · {counts.pending || 0} waiting · {counts.needs_attention || 0} need attention · {counts.failed || 0} failed</p>
          <progress max={Math.max(1, batch.items.length)} value={(counts.completed || 0) + (counts.skipped || 0) + (counts.failed || 0) + (counts.cancelled || 0)} aria-label="Batch completion" />
          <div className="sb-job-controls">
            {batch.state === 'running' ? <button type="button" className="sb-button" disabled={working} onClick={() => run('pauseBatch')}>Pause after current song</button> : null}
            {batch.state === 'paused' ? <button type="button" className="sb-button" disabled={!outputReady || working} onClick={() => run('resumeBatch')}>Resume batch</button> : null}
            {['paused', 'completed'].includes(batch.state) && counts.failed ? <button type="button" className="sb-button" disabled={!outputReady || working} onClick={() => run('resumeBatch', { retryFailed: true })}>Retry failed songs</button> : null}
            {['paused', 'running'].includes(batch.state) ? <button type="button" className="sb-button" disabled={working} onClick={() => run('cancelBatch')}>Cancel remaining</button> : null}
          </div>
          <div className="sb-batch-groups">
            {batch.items.filter((item) => ['pending', 'running', 'needs_attention', 'failed', 'interrupted'].includes(item.state) || item.outcome?.skipKind === 'user').map((item) => <div className="sb-batch-item" key={item.id}>
              <strong>{charts.get(item.chartId)?.title || item.chartId}</strong> · {item.outcome?.skipKind === 'user' ? 'Skipped by you' : item.state.replaceAll('_', ' ')}
              <p>{item.outcome?.message}</p>
              <div className="sb-job-controls">
                {!['skipped', 'completed', 'cancelled'].includes(item.state) ? <button type="button" className="sb-button" disabled={working || item.skipRequested} onClick={() => run('skipBatchItem', { itemId: item.id })}>{item.skipRequested ? 'Skipping…' : 'Skip song'}</button> : null}
                {['needs_attention', 'failed', 'interrupted', 'skipped'].includes(item.state) ? <button type="button" className="sb-button" disabled={!outputReady || working} onClick={() => run('retryBatchItem', { itemId: item.id })}>Retry song</button> : null}
                {['needs_attention', 'failed', 'interrupted'].includes(item.state) ? <button type="button" className="sb-button" disabled={!outputReady || working || batch.state === 'running'} onClick={() => run('resolveBatchItem', { itemId: item.id })}>Resolve with browser help</button> : null}
                {['needs_attention', 'failed'].includes(item.state) && (batch.preferences.instrumentRequirements?.length || batch.preferences.backingStrict || batch.preferences.requiredParts?.length || batch.preferences.tuning) ? <button type="button" className="sb-text-button" disabled={!outputReady || working} onClick={() => run('retryBatchItem', { itemId: item.id, relaxRequirements: true })}>Relax requirements and retry</button> : null}
              </div>
              {item.outcome?.candidates?.length ? <div className="sb-file-choices">{item.outcome.candidates.map((candidate) => <button type="button" key={candidate.id} className="sb-button"
                disabled={!outputReady || working || batch.state === 'running' || candidate.platform === 'mac'} onClick={() => run('chooseBatchFile', { itemId: item.id, choice: { id: candidate.id } })}><FileCandidateDetails candidate={candidate} /></button>)}</div> : null}
              {item.choice ? <p>Selected file: {item.choice.label}. Resume the batch to continue.</p> : null}
            </div>)}
          </div>
        </>}
        {batch.state !== 'running' ? <button type="button" className="sb-text-button" disabled={working} onClick={() => run('removeBatch')}>Remove batch record</button> : null}
      </details>;
    })}
  </section>;
}

import React from 'react';

const labels = { draft: 'Review selections', running: 'Running', paused: 'Paused', completed: 'Finished', cancelled: 'Cancelled' };

export function BatchPanel({ batches = [], action, busy }) {
  if (!batches.length) return null;
  return <section className="sb-batches" aria-label="Song batches">
    <h2>Song batches</h2>
    {batches.map((batch) => {
      const selected = new Set(batch.selectedIds);
      const charts = new Map(batch.charts.map((chart) => [chart.id, chart]));
      const working = [...busy].some((key) => key.startsWith(`batch:${batch.id}:`));
      const run = (method, args = {}) => action(`batch:${batch.id}:${method}`, method, { id: batch.id, ...args });
      const counts = batch.counts || {};
      return <details key={batch.id} className="sb-batch" open={batch.state === 'draft' || batch.state === 'running' || batch.state === 'paused'}>
        <summary>{labels[batch.state] || batch.state} · {selected.size} charts · {batch.groups.length} song groups</summary>
        <p className="sb-folder-path">Output: {batch.outputDir}</p>
        {batch.warning || batch.pauseReason ? <p role="status">{batch.warning || batch.pauseReason}</p> : null}
        {batch.state === 'draft' ? <>
          <p>Review alternative charts below. Recommendations respect the requested arrangements and tuning. Multiple charts may be needed for separate lead and bass parts.</p>
          <p>{batch.unresolvedCount} groups need a closer look. Starting accepts the checked charts. Host file choices may appear during downloading.</p>
          <div className="sb-batch-groups">
            {batch.groups.map((group) => <details key={group.key} open={group.unresolved}>
              <summary>{group.artist} — {group.title} · {group.options.filter((option) => selected.has(option.id)).length} selected</summary>
              {group.reasons?.length ? <p>{group.reasons.join(' ')}</p> : null}
              {group.options.map((option) => {
                const chart = charts.get(option.id);
                return <label className="sb-batch-option" key={option.id}>
                  <input type="checkbox" aria-label={`Select ${group.title} chart ${option.id}`} checked={selected.has(option.id)} disabled={working || !option.eligible}
                    onChange={(event) => { const next = new Set(selected); event.target.checked ? next.add(option.id) : next.delete(option.id); run('chooseBatch', { selectedIds: [...next] }); }} />
                  <span><strong>{chart?.creator || 'Unknown creator'}</strong> · {chart?.tuning || 'Tuning unknown'} · {option.parts.join(', ') || 'Parts unknown'}
                    {chart?.version ? ` · v${chart.version}` : ''}{chart?.downloads != null ? ` · ${chart.downloads} downloads` : ''}
                    <small>{group.recommendedIds.includes(option.id) ? 'Recommended. ' : ''}{option.reasons.join(' ')}</small></span>
                </label>;
              })}
            </details>)}
          </div>
          <button type="button" className="sb-button sb-primary" disabled={working || !selected.size || !batch.complete} onClick={() => run('startBatch')}>Start batch ({selected.size} charts)</button>
        </> : <>
          <p role="status">{counts.completed || 0} converted · {counts.skipped || 0} already available · {counts.pending || 0} waiting · {counts.needs_attention || 0} need attention · {counts.failed || 0} failed</p>
          <progress max={Math.max(1, batch.items.length)} value={(counts.completed || 0) + (counts.skipped || 0) + (counts.failed || 0) + (counts.cancelled || 0)} aria-label="Batch completion" />
          <div className="sb-job-controls">
            {batch.state === 'running' ? <button type="button" className="sb-button" disabled={working} onClick={() => run('pauseBatch')}>Pause after current song</button> : null}
            {batch.state === 'paused' ? <button type="button" className="sb-button" disabled={working} onClick={() => run('resumeBatch')}>Resume batch</button> : null}
            {['paused', 'completed'].includes(batch.state) && counts.failed ? <button type="button" className="sb-button" disabled={working} onClick={() => run('resumeBatch', { retryFailed: true })}>Retry failed songs</button> : null}
            {['paused', 'running'].includes(batch.state) ? <button type="button" className="sb-button" disabled={working} onClick={() => run('cancelBatch')}>Cancel remaining</button> : null}
          </div>
          <div className="sb-batch-groups">
            {batch.items.filter((item) => ['running', 'needs_attention', 'failed', 'interrupted'].includes(item.state)).map((item) => <div className="sb-batch-item" key={item.id}>
              <strong>{charts.get(item.chartId)?.title || item.chartId}</strong> · {item.state.replaceAll('_', ' ')}
              <p>{item.outcome?.message}</p>
              {['needs_attention', 'failed', 'interrupted'].includes(item.state) ? <button type="button" className="sb-button" disabled={working || batch.state === 'running'} onClick={() => run('resolveBatchItem', { itemId: item.id })}>Resolve with browser help</button> : null}
              {item.outcome?.candidates?.length ? <div className="sb-job-controls">{item.outcome.candidates.map((candidate) => <button type="button" key={candidate.id} className="sb-button"
                disabled={working || batch.state === 'running' || candidate.platform === 'mac'} onClick={() => run('chooseBatchFile', { itemId: item.id, choice: { id: candidate.id } })}>{candidate.label}</button>)}</div> : null}
              {item.choice ? <p>Selected file: {item.choice.label}. Resume the batch to continue.</p> : null}
            </div>)}
          </div>
        </>}
        {batch.state !== 'running' ? <button type="button" className="sb-text-button" disabled={working} onClick={() => run('removeBatch')}>Remove batch record</button> : null}
      </details>;
    })}
  </section>;
}

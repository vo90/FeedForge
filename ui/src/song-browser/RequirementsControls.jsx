import React from 'react';

export const defaultRequirements = () => ({ backingTrack: 'full', backingStrict: false, instrumentRequirements: [] });

export function RequirementsControls({ value = defaultRequirements(), onChange, prefix = 'Song', disabled = false }) {
  const instruments = value.instrumentRequirements || [];
  const setInstrument = (part, patch) => {
    const current = instruments.find((entry) => entry.part === part) || { part, family: null, stringCount: null, strict: true };
    const next = { ...current, ...patch };
    onChange({ ...value, instrumentRequirements: [...instruments.filter((entry) => entry.part !== part), ...(next.family || next.stringCount ? [next] : [])] });
  };
  return <fieldset className="sb-requirements" disabled={disabled}>
    <legend>{prefix} file preferences</legend>
    <div className="sb-filter-grid">
      <label>Backing track<select aria-label={`${prefix} backing track`} value={value.backingTrack || 'full'} onChange={(event) => onChange({ ...value, backingTrack: event.target.value })}>
        <option value="full">Prefer full backing</option><option value="no-guitar">Prefer no guitar</option><option value="no-bass">Prefer no bass</option><option value="any">No preference</option>
      </select></label>
      <label className="sb-auto-refresh"><input type="checkbox" checked={value.backingStrict === true} disabled={value.backingTrack === 'any'} onChange={(event) => onChange({ ...value, backingStrict: event.target.checked })} />Require verified backing type</label>
    </div>
    <p>Filename hints help compare files. Unlabelled backing tracks can continue with a preference; a strict requirement needs verified evidence.</p>
    <details><summary>Instrument and string requirements</summary>
      <p>Leave unrestricted unless needed. Rocksmith tuning slots do not establish an exact string count; missing evidence requires review.</p>
      {['lead', 'rhythm', 'bass'].map((part) => {
        const requirement = instruments.find((entry) => entry.part === part) || {};
        return <div className="sb-instrument-row" key={part}><strong>{part}</strong>
          <label>Instrument<select aria-label={`${prefix} ${part} instrument`} value={requirement.family || ''} onChange={(event) => setInstrument(part, { family: event.target.value || null })}>
            <option value="">Unrestricted</option><option value="guitar">Guitar</option><option value="bass">Bass</option>
          </select></label>
          <label>Strings<select aria-label={`${prefix} ${part} strings`} value={requirement.stringCount || ''} onChange={(event) => setInstrument(part, { stringCount: Number(event.target.value) || null })}>
            <option value="">Unrestricted</option>{[4, 5, 6].map((count) => <option key={count} value={count}>{count}</option>)}
          </select></label>
          <label><input type="checkbox" checked={requirement.strict !== false} disabled={!requirement.family && !requirement.stringCount} onChange={(event) => setInstrument(part, { strict: event.target.checked })} />Required</label>
        </div>;
      })}
    </details>
  </fieldset>;
}

export function FileCandidateDetails({ candidate }) {
  const size = Number.isFinite(candidate.sizeBytes) && candidate.sizeBytes > 0 ? `${(candidate.sizeBytes / (1024 * 1024)).toFixed(1)} MB` : 'Size unknown';
  return <span className="sb-candidate-detail"><strong>{candidate.label}</strong><small>{candidate.platform === 'pc' ? 'PC' : candidate.platform === 'mac' ? 'Mac' : 'Platform unknown'} · {size}</small>
    <small>{candidate.versionHint ? `Version hint ${candidate.versionHint}` : 'Version unknown'} · {candidate.editionHint ? `Edition hint: ${candidate.editionHint}` : 'Edition unknown'}</small>
    <small>{candidate.backingHint ? `Backing hint: ${candidate.backingHint} (unverified)` : 'Backing unknown'}</small></span>;
}

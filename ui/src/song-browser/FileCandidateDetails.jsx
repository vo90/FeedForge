import React from 'react';

export function FileCandidateDetails({ candidate }) {
  const size = Number.isFinite(candidate.sizeBytes) && candidate.sizeBytes > 0 ? `${(candidate.sizeBytes / (1024 * 1024)).toFixed(1)} MB` : 'Size unknown';
  return <span className="sb-candidate-detail"><strong>{candidate.label}</strong><small>{candidate.platform === 'pc' ? 'PC' : candidate.platform === 'mac' ? 'Mac' : 'Platform unknown'} · {size}</small>
    <small>{candidate.versionHint ? `Version hint ${candidate.versionHint}` : 'Version unknown'} · {candidate.editionHint ? `Edition hint: ${candidate.editionHint}` : 'Edition unknown'}</small></span>;
}

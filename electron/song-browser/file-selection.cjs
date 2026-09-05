'use strict';

// Self-contained so the browser can run the same policy on sanitized visible
// filenames without exporting public-share URLs or depending on page scripts.
function selectFileCandidate(candidates, request = {}) {
  const clean = (value) => typeof value === 'string' ? value.replace(/[\x00-\x1f\x7f]/g, '').trim().slice(0, 240) : '';
  const variant = (name) => /_p\.psarc$/i.test(name) ? 'pc' : /_m\.psarc$/i.test(name) ? 'mac' : 'unknown';
  const list = (Array.isArray(candidates) ? candidates : []).slice(0, 500).filter((row) => row && typeof row === 'object').map((row) => ({
    id: clean(row.id), label: clean(row.label), platform: variant(clean(row.label)),
  })).filter((row) => /^[a-zA-Z0-9_-]{1,100}$/.test(row.id) && /\.psarc$/i.test(row.label) && !/[\\/]/.test(row.label));
  if (!list.length) return { status: 'waiting' };
  if (new Set(list.map((row) => row.id)).size !== list.length) return { status: 'needs_attention', error: 'The file list changed. Refresh it before choosing a file.' };
  const choice = request.choice;
  if (choice) {
    let matches = [];
    if (typeof choice === 'object' && typeof choice.id === 'string' && !choice.label) matches = list.filter((row) => row.id === choice.id);
    else if (typeof choice === 'object' && typeof choice.label === 'string' && ['pc', 'mac', 'unknown'].includes(choice.platform)) {
      matches = list.filter((row) => row.label === clean(choice.label) && row.platform === choice.platform);
    }
    if (matches.length !== 1) return { status: 'choose_file', candidates: list, error: 'The selected file is missing or ambiguous. Choose from the current file list.' };
    if (matches[0].platform === 'mac' && request.allowMacFallback !== true) return { status: 'choose_file', candidates: list, error: 'This is a Mac file. PC files are required until Mac fallback has been verified.' };
    return { status: 'selected', candidate: matches[0] };
  }
  const pc = list.filter((row) => row.platform === 'pc');
  // Treat one PC/Mac pair as equivalent only when the base filename matches.
  // A folder containing different songs or versions always needs a choice.
  if (pc.length === 1 && list.every((row) => row.platform !== 'unknown'
    && row.label.replace(/_[pm]\.psarc$/i, '').toLocaleLowerCase() === pc[0].label.replace(/_[pm]\.psarc$/i, '').toLocaleLowerCase())) {
    return { status: 'selected', candidate: pc[0] };
  }
  if (list.length === 1 && list[0].platform === 'unknown') return { status: 'selected', candidate: list[0] };
  if (list.length === 1 && list[0].platform === 'mac' && request.allowMacFallback === true) return { status: 'selected', candidate: list[0] };
  return { status: 'choose_file', candidates: list, error: pc.length ? 'Choose the PSARC version to convert.' : 'Choose a PC PSARC file. No unambiguous PC version was found.' };
}

const PARTS = new Set(['lead', 'rhythm', 'bass', 'guitar']);
function normalizeRequirements(value = {}) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Invalid song requirements.');
  const parts = value.parts === undefined ? [] : value.parts;
  if (!Array.isArray(parts) || parts.length > 4 || parts.some((part) => !PARTS.has(part))) throw new Error('Choose valid lead, rhythm, bass or guitar arrangements.');
  let tuning = value.tuning ?? null;
  if (Array.isArray(tuning)) {
    if (tuning.length < 4 || tuning.length > 8 || tuning.some((note) => !Number.isInteger(note) || note < -24 || note > 24)) throw new Error('Invalid tuning offsets.');
    tuning = [...tuning];
  } else if (typeof tuning === 'string') {
    tuning = tuning.replace(/[\x00-\x1f\x7f]/g, '').trim().slice(0, 80) || null;
  } else if (tuning !== null) throw new Error('Invalid tuning requirement.');
  const platform = value.platform ?? 'pc';
  if (!['pc', 'mac', 'any'].includes(platform)) throw new Error('Invalid PSARC platform.');
  const backingTrack = value.backingTrack ?? 'any';
  if (!['any', 'full', 'no-guitar', 'no-bass'].includes(backingTrack)) throw new Error('Invalid backing track preference.');
  return { parts: [...new Set(parts)].sort(), tuning, platform, allowMacFallback: value.allowMacFallback === true, backingTrack, strictPlatform: value.strictPlatform === true };
}
function arrangementPart(arrangement) {
  for (const key of ['path', 'id', 'name']) {
    const value = String(arrangement?.[key] || '').toLowerCase();
    const found = value.match(/(?:^|[_\s-])(lead|rhythm|bass)(?=$|[_\s-]|\d)/);
    if (found) return found[1];
  }
  return ['bass', 'guitar'].includes(arrangement?.type) ? arrangement.type : 'unknown';
}
function normalizedTuningName(value) {
  return String(value || '').normalize('NFKC').toLowerCase().replace(/♯/g, '#').replace(/♭/g, 'b').replace(/[ _-]+/g, ' ').trim();
}
function tuningMatches(arrangement, requested) {
  const offsets = arrangement?.tuning;
  if (Array.isArray(requested)) return Array.isArray(offsets) && offsets.length === requested.length && requested.every((value, index) => value === offsets[index]);
  const name = normalizedTuningName(requested);
  if (typeof arrangement.tuning_name === 'string' && normalizedTuningName(arrangement.tuning_name) === name) return true;
  if (typeof arrangement.tuning === 'string') return normalizedTuningName(arrangement.tuning) === name;
  if (!Array.isArray(offsets) || offsets.length < 4 || offsets.some((note) => !Number.isInteger(note))) return false;
  const standard = { 'e standard': 0, 'eb standard': -1, 'd# standard': -1, 'd standard': -2, 'db standard': -3, 'c# standard': -3, 'c standard': -4, 'b standard': -5, 'bb standard': -6, 'a# standard': -6, 'a standard': -7 };
  const drop = { 'drop d': 0, 'eb drop db': -1, 'eb drop c#': -1, 'd drop c': -2, 'drop c': -2, 'db drop b': -3, 'c# drop b': -3, 'drop b': -3, 'c drop bb': -4, 'c drop a#': -4, 'drop bb': -4, 'b drop a': -5, 'drop a': -5 };
  const shift = Object.hasOwn(standard, name) ? standard[name] : Object.hasOwn(drop, name) ? drop[name] : null;
  if (shift === null) return false; // No guess for alternate/extended-range tunings.
  // Rocksmith bass data often pads four active strings to six with zeroes.
  const bass = arrangementPart(arrangement) === 'bass';
  const explicitCount = arrangement.string_count ?? arrangement.stringCount;
  let notes = offsets;
  if (bass && Number.isInteger(explicitCount)) notes = offsets.slice(0, explicitCount);
  else if (bass && offsets.length === 6 && offsets.slice(4).every((value) => value === 0)) notes = offsets.slice(0, 4);
  if (notes.length < 4 || notes.length > 6) return false;
  return notes.every((value, index) => value === shift - (Object.hasOwn(drop, name) && index === 0 ? 2 : 0));
}
function validateRequirements(input, requested = {}) {
  const requirements = normalizeRequirements(requested);
  const preview = input?.preview || input?.manifest || input || {};
  const arrangements = Array.isArray(preview.arrangements) ? preview.arrangements.filter((arr) => arr && typeof arr === 'object') : [];
  const errors = [], warnings = [];
  const requestedParts = requirements.parts.length ? requirements.parts : ['any'];
  for (const part of requestedParts) {
    const matches = arrangements.filter((arr) => part === 'any' || arrangementPart(arr) === part || (part === 'guitar' && ['guitar', 'lead', 'rhythm'].includes(arrangementPart(arr))));
    if (!matches.length) errors.push(part === 'any' ? 'The file has no readable arrangements.' : `The file is missing the requested ${part} arrangement.`);
    else if (requirements.tuning !== null && !matches.some((arr) => tuningMatches(arr, requirements.tuning))) errors.push(`The requested tuning could not be verified for ${part === 'any' ? 'any arrangement' : 'the ' + part + ' arrangement'}.`);
  }
  const evidence = preview.source_platforms ?? preview.platforms ?? (preview.platform ? [preview.platform] : []);
  const platforms = Array.isArray(evidence) ? evidence.filter((value) => ['pc', 'mac'].includes(value)) : [];
  if (requirements.platform !== 'any') {
    if (!platforms.length) {
      const message = 'The PSARC platform could not be verified from its contents.';
      (requirements.strictPlatform ? errors : warnings).push(message);
    } else if (!platforms.includes(requirements.platform) && !(requirements.platform === 'pc' && requirements.allowMacFallback && platforms.includes('mac'))) errors.push(`The file does not contain the requested ${requirements.platform === 'pc' ? 'PC' : 'Mac'} arrangements.`);
  }
  if (requirements.backingTrack !== 'any') {
    const actual = preview.backing_track ?? preview.backingTrack;
    if (actual !== requirements.backingTrack) errors.push('The requested backing track variant could not be verified from this file.');
  }
  return { ok: errors.length === 0, errors, warnings, requirements };
}
module.exports = { selectFileCandidate, normalizeRequirements, validateRequirements, arrangementPart, tuningMatches };

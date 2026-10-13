'use strict';

// Presentation only. Never rewrite the immutable compatibility report or use
// these levels to authorize conversion, publication, scoring or timing changes.
const VERSION = 2;
// Exact, qualified converter meanings only. A changed meaning is reviewed again.
const informational = new Map([
  ["note.harmonicFret", "Natural harmonic position 15 is displayed as 14.7, matching the source playback harmonic; the authored value is retained."],
  ["tempo.visible", "Tempo-label visibility is retained in the source. The numeric tempo and playback timing are unchanged."],
  ["beat.wahwah", "Wah open/closed instructions are preserved as optional timed guidance in the updated highway and tab view. Audio and scoring are unchanged."],
  ["beat.tremolo", "Tremolo picking rates and active spans are preserved for the updated highway and tab view. Trail spacing indicates speed within readable limits; individual picks are not scored separately."],
  ["note.tremolo", "Tremolo picking rates and active spans are preserved for the updated highway and tab view. Trail spacing indicates speed within readable limits; individual picks are not scored separately."],
  [
    "beat.letRing",
    "Let-ring instructions and source-derived ringing spans are preserved for the updated game. Written rhythm and scored note durations are unchanged."
  ],
  [
    "measure.keySignature",
    "The written key signature is retained in the source. Explicit pitches are converted unchanged."
  ],
  [
    "note.staccato",
    "Performed duration follows the source's staccato rule. The written staccato marking is retained in the original source; the game chart has no dedicated staccato marker."
  ],
  [
    "beat.chord",
    "Authored labels name simultaneous chord templates. Labels on rests or single notes, and label engraving, remain in the source."
  ],
  [
    "notation.unpitched_mute",
    "Unpitched mutes have no MIDI pitch for standard notation. Its playable tab is converted; complete notation remains in the original source."
  ],
  [
    "notation.written_rhythm",
    "This arrangement's written rhythm is outside the game's notation vocabulary. Its playable tab is converted; complete notation remains in the original source."
  ],
  [
    "measure.doubleBarline",
    "The double barline is retained in the source; the game uses its ordinary measure display. Notes and timing are unchanged."
  ],
  [
    "tempo.text",
    "The descriptive tempo text is retained in the source. The explicit numeric tempo is converted unchanged."
  ]
]);
const policies = new Map([
  [
    "note.harmonicFret",
    "Semi-harmonics use the pinch-harmonic display and permit the fretted sound alongside the selected harmonic."
  ],
  [
    "note.pickScrape",
    "Pick scrapes: visual only, not scored. Direction, strings and timing are preserved; an updated game is required to display them."
  ],
  [
    "beat.tremoloBar",
    "Whammy-bar pitch curves are preserved. Bar expression is optional; an updated game is required for the display and scoring policy."
  ],
  [
    "beat.vibratoWithTremoloBar",
    "Slight/wide bar vibrato is preserved. These notes are visual only in the updated game because the source does not specify an exact pitch curve; they do not reduce accuracy or streaks."
  ],
  [
    "note.muted_slide",
    "Muted slide-outs are visual only, not scored. Direction, strings and timing are preserved; an updated game is required to display them."
  ],
  [
    "note.tied_harmonic",
    "An artificial-harmonic contact starts during this sustain. Its timing and target are preserved; the updated game displays the contact without another picked attack."
  ]
]);
// These meanings require the independently verified note-owned policy. They
// cannot inherit the ordinary message-only informational classification.
const noteOwnedVibrato = new Map([
  ['beat.vibrato', 'The beat-level vibrato annotation is retained in the source and written beat. Note vibrato follows explicit note instructions; this beat flag does not add a note controller or scored pitch.'],
  ['beat.wideVibrato', 'The beat-level wide vibrato annotation is retained in the source and written beat. Note vibrato follows explicit note instructions; this beat flag does not add a note controller or scored pitch.'],
]);
const titles = {
  'beat.letRing': 'Let-ring instructions',
  'beat.wahwah': 'Wah-pedal instructions are not shown',
  'note.bend_timing': 'Some bend timing is uncertain',
  'note.fret_range': 'High-fret notes omitted',
  'note.undefined_slide_to_mute': 'Undefined slide paths omitted',
  'note.muted_slide': 'Some muted slide paths are not shown',
  'note.tied_harmonic': 'Some sustained harmonic changes are not reproduced',
  'note.tied_mute': 'A tied mute continues as a sustain',
  'note.harmonicFret': 'Some harmonic expression is simplified',
  'beat.tremolo': 'Some tremolo detail uses a generic cue',
  'note.tremolo': 'Some tremolo detail uses a generic cue',
  'beat.vibrato': 'Beat-level vibrato is not applied to notes',
  'beat.wideVibrato': 'Beat-level vibrato is not applied to notes',
  'note.rightFingering': 'Picking-hand fingering is not shown',
  'beat.hasRasgueado': 'Rasgueado instructions are not shown',
};

const informationTitles = {
  'beat.tremolo': 'Tremolo picking', 'note.tremolo': 'Tremolo picking',
  'measure.keySignature': 'Key signatures retained', 'measure.doubleBarline': 'Barline appearance',
  'tempo.text': 'Tempo descriptions', 'tempo.visible': 'Tempo-label appearance', 'note.staccato': 'Staccato durations preserved',
  'beat.chord': 'Chord labels', 'notation.unpitched_mute': 'Standard notation for muted notes',
  'notation.written_rhythm': 'Standard notation for written rhythms',
  'note.pickScrape': 'Pick-scrape playing behavior', 'beat.tremoloBar': 'Optional whammy-bar expression',
  'beat.vibratoWithTremoloBar': 'Bar-vibrato playing behavior', 'note.harmonicFret': 'Semi-harmonic playing behavior',
  'note.muted_slide': 'Muted slide-out playing behavior', 'note.tied_harmonic': 'Harmonic contacts during sustains',
  'beat.vibrato': 'Beat-level vibrato annotations', 'beat.wideVibrato': 'Beat-level vibrato annotations',
};

function classifyFinding(f, { noteOwnedFingerVibrato = false } = {}) {
  // Unknown and blocking semantics take precedence over an otherwise familiar
  // feature name. Future converters must not silently inherit a downgrade.
  if (f.impact === 'blocking' || f.impact === 'gameplay_omission'
      || ['unknown_semantics', 'source_structure', 'conversion_check', 'converter_gap'].includes(f.category)) return 'warning';
  if (f.impact === 'source_retained') return 'diagnostic';
  if (noteOwnedFingerVibrato === true && f.category === 'game_limitation'
      && f.impact === 'display_or_expression' && f.workStatus === 'display_limitation'
      && f.retained === 'original_source' && f.value === true && f.valueTruncated === false
      && typeof f.message === 'string' && noteOwnedVibrato.get(f.feature) === f.message) return 'info';
  if (f.category === 'game_limitation' && f.impact === 'display_or_expression'
      && typeof f.message === 'string' && (informational.get(f.feature) === f.message || policies.get(f.feature) === f.message)) return 'info';
  return 'warning';
}
function location(f) {
  return [f.arrangement, f.measure && `Measure ${f.measure}`, f.beat && `beat ${f.beat}`, f.note && `note ${f.note}`]
    .filter(Boolean).join(' · ') || f.location || '';
}
function prioritizeFindings(findings, context = {}) {
  const rank = f => f.impact === 'blocking' || f.impact === 'gameplay_omission' ? 0
    : classifyFinding(f, context) === 'warning' ? 1 : classifyFinding(f, context) === 'info' ? 2 : 3;
  const buckets = Array.from({ length: 4 }, () => ({ seen: new Set(), first: [], rest: [] }));
  for (const finding of findings) {
    const bucket = buckets[rank(finding)];
    const key = JSON.stringify([finding.feature, finding.category, finding.impact, finding.message]);
    (bucket.seen.has(key) ? bucket.rest : bucket.first).push(finding);
    bucket.seen.add(key);
  }
  // Show each distinct issue before repeating its locations. A thousand
  // let-ring markers must not bury the single uncertain bend in the same song.
  return buckets.flatMap(bucket => [...bucket.first, ...bucket.rest]);
}

function isValidatedEmptyWarning(message, report) {
  const matches = (report?.arrangements || []).filter(row => typeof row.name === 'string'
    && message === `Skipped empty arrangement: ${row.name}.`);
  // The warning text alone cannot distinguish authored rests from omissions.
  // Duplicate names must all agree before treating the message as routine.
  return matches.length > 0 && matches.every(row => row.status === 'skipped_empty'
    && row.noteCount === 0 && !row.blockingFeatures?.length);
}

const boundaryPrefix = 'Some overfilled tab bars use Songsterr playback boundaries: ';
const legacyBoundaryWarning = boundaryPrefix + 'overflowing durations are shortened and attacks outside their bar are omitted. Original tab retained.';
const boundarySuffix = 'Later bars stay in place; original notation is retained in the source.';
const boundaryClipMessage = 'This overflowing beat is bounded to its written bar, as in Songsterr playback. ' + boundarySuffix;
const boundaryOmitMessages = new Set([
  'This beat starts outside its written bar and is skipped, as in Songsterr playback. ' + boundarySuffix,
  'This attack is outside its written bar and is skipped, as in Songsterr playback. ' + boundarySuffix,
]);

function boundaryFraction(value) {
  // These are exact quarter-note fractions in the retained evidence. Avoid
  // rounding an invalid or equal duration into an apparent shortening.
  if (typeof value !== 'string' || value.length > 128 || !/^-?\d+(?:\/[1-9]\d*)?$/.test(value)) return null;
  const [numerator, denominator = '1'] = value.split('/');
  return [BigInt(numerator), BigInt(denominator)];
}

function validatedBoundarySummary(report) {
  // Only a full verified report can replace its older, less specific wording.
  // Display-limited, unknown or malformed evidence keeps all original warnings.
  if (!report || report.status !== 'limitations' || report.truncated !== false
      || !Number.isSafeInteger(report.version) || report.version <= 0
      || !Array.isArray(report.findings) || report.findingCount !== report.findings.length) return null;
  const findings = report.findings.filter(f => f?.feature === 'beat.measure_boundary');
  if (!findings.length) return null;
  const beats = new Map(), seen = new Set(), actions = new Set();
  for (const f of findings) {
    const row = f.value;
    if (!row || typeof row !== 'object' || Array.isArray(row)) return null;
    const omit = row.action === 'omit', cap = row.action === 'duration_cap';
    if (!['clip', 'omit', 'duration_cap'].includes(row.action)
        || f.category !== 'source_interpretation' || f.retained !== 'original_source'
        || f.valueTruncated !== false
        || f.impact !== (omit ? 'gameplay_omission' : 'display_or_expression')
        || f.workStatus !== (omit ? 'gameplay_omission' : 'display_limitation')
        || (omit ? !boundaryOmitMessages.has(f.message) : f.message !== boundaryClipMessage)
        || typeof row.trackId !== 'string' || !row.trackId || f.trackId !== row.trackId
        || ![row.measure, row.voice, row.beat].every(n => Number.isSafeInteger(n) && n > 0)
        || typeof row.location !== 'string' || f.location !== `${row.location}/${row.action}`) return null;
    const sourcePath = /^parts\/(\d+)\/measures\/(\d+)\/voices\/(\d+)\/beats\/(\d+)$/.exec(row.location);
    if (!sourcePath || Number(sourcePath[2]) !== row.measure - 1
        || Number(sourcePath[3]) !== row.voice - 1 || Number(sourcePath[4]) !== row.beat - 1) return null;
    const keys = ['trackId', 'location', 'measure', 'voice', 'beat', 'action', 'duration', 'usedDuration'];
    if (!cap) keys.push('start');
    if (Object.keys(row).length !== keys.length || keys.some(key => !Object.hasOwn(row, key))) return null;
    const duration = boundaryFraction(row.duration), used = boundaryFraction(row.usedDuration);
    if (!duration || duration[0] <= 0n || !cap && !boundaryFraction(row.start)
        || (omit ? row.usedDuration !== null : !used || used[0] <= 0n
          || used[0] * duration[1] >= duration[0] * used[1])) return null;
    const key = JSON.stringify([row.trackId, row.location]);
    const operation = JSON.stringify([key, row.action]);
    if (seen.has(operation)) return null;
    seen.add(operation); actions.add(row.action);
    if (!beats.has(key)) beats.set(key, { row, actions: new Set() });
    beats.get(key).actions.add(row.action);
  }
  let shortened = 0, omitted = 0;
  const locations = [];
  for (const { row, actions: beatActions } of beats.values()) {
    // A cap followed by clipping is one shortened beat. A capped beat that is
    // ultimately omitted is counted only as omitted, not as a playable sustain.
    if (beatActions.has('clip') && beatActions.has('omit')) return null;
    if (beatActions.has('omit')) omitted++; else shortened++;
    const names = new Set((Array.isArray(report.arrangements) ? report.arrangements : [])
      .filter(a => a?.trackId === row.trackId && typeof a.name === 'string' && a.name.trim()).map(a => a.name));
    const name = names.size === 1 ? [...names][0] : `Track ${row.trackId}`;
    locations.push(`${name} · Measure ${row.measure} · voice ${row.voice} · beat ${row.beat}`);
  }
  const changes = [];
  if (shortened) changes.push(`${shortened} ${shortened === 1 ? 'beat duration shortened to fit its written bar' : 'beat durations shortened to fit their written bars'}.`);
  if (omitted) changes.push(`${omitted} ${omitted === 1 ? 'beat omitted because it starts' : 'beats omitted because they start'} outside ${omitted === 1 ? 'its written bar' : 'their written bars'}.`);
  const rawChanges = [];
  if (actions.has('clip') || actions.has('duration_cap')) rawChanges.push('overflowing beat durations are shortened');
  if (actions.has('omit')) rawChanges.push('beats outside their bar are omitted');
  return { message: changes.join(' ') + ' ' + boundarySuffix, locations, count: beats.size,
    rawWarnings: new Set([legacyBoundaryWarning, boundaryPrefix + rawChanges.join(' and ') + '. Original tab retained.']) };
}

function playerNotices(job, report, { reportUnavailable = false, noteOwnedFingerVibrato = false } = {}) {
  const groups = new Map();
  function add(id, level, title, message, at = '', count = 1) {
    const key = `${level}:${id}`;
    if (!groups.has(key)) groups.set(key, { id, level, title, message, occurrences: 0, locations: [], examples: [] });
    const group = groups.get(key); group.occurrences += count;
    if (at && !group.locations.includes(at) && group.locations.length < 8) group.locations.push(at);
    if (message && message !== group.message && !group.examples.includes(message) && group.examples.length < 8) group.examples.push(message);
  }
  const v = job.verification || {}, completed = job.state === 'completed', verified = completed && v.status === 'passed';
  // The caller derives policy proof from hash-checked verification evidence;
  // additionally keep partial reports and unverified jobs conservative here.
  const context = { noteOwnedFingerVibrato: noteOwnedFingerVibrato === true && verified && !reportUnavailable
    && ['compatible', 'limitations'].includes(report?.status) && report.truncated === false
    && Number.isSafeInteger(report.version) && report.version > 0
    && Array.isArray(report.findings) && report.findingCount === report.findings.length };
  const boundary = verified && !reportUnavailable ? validatedBoundarySummary(report) : null;
  for (const f of report?.findings || []) {
    if (boundary && f.feature === 'beat.measure_boundary') continue;
    const level = classifyFinding(f, context);
    const fallbackHarmonic = f.feature === 'note.harmonicFret'
      && f.message?.startsWith('Artificial harmonic:');
    const id = fallbackHarmonic ? 'artificial_harmonic_contact' : f.feature === 'note.fret_range' ? 'high_frets' : f.feature || 'unknown_finding';
    add(id, level, fallbackHarmonic ? 'Artificial-harmonic touch position unspecified'
      : level === 'warning' ? titles[f.feature] || 'Source feature needs review'
      : level === 'info' && f.feature === 'note.harmonicFret' && f.message === informational.get(f.feature)
        ? 'Natural-harmonic touch position'
        : informationTitles[f.feature] || 'Retained source information', f.message, location(f));
  }
  if (boundary) {
    add('beat.measure_boundary', 'warning', 'Tab bar boundaries adjusted', boundary.message, '', boundary.count);
    groups.get('warning:beat.measure_boundary').locations = boundary.locations.slice(0, 8);
  }
  if (report?.truncated) add('incomplete_report', 'warning', 'Compatibility report is incomplete', 'Some source findings could not be listed. Review the saved conversion report.');
  if (reportUnavailable || !report && job.compatibility?.status === 'limitations') {
    add('unavailable_report', 'warning', 'Compatibility details need review', 'The saved compatibility details are unavailable. The earlier assessment reported limitations; they have not been dismissed.');
  }
  const timing = v.timingAssessment;
  const warning = v.timingWarning || v.endingTiming?.warning;
  const mismatch = timing?.version === 'local-recording-clock-v3'
    && (timing.suspectedMismatchWindows > 0 || timing.suspectedMismatchRanges?.length > 0);
  const time = seconds => `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`;
  const ranges = items => (items || []).filter(r => Number.isFinite(r.start) && Number.isFinite(r.end)).map(r => `${time(Math.floor(r.start))}–${time(Math.ceil(r.end))}`).join(', ');
  if (verified && (mismatch || warning?.status === 'accepted_with_warning')) {
    const affected = ranges(mismatch ? timing.suspectedMismatchRanges : warning.uncertainRanges);
    add('timing', 'warning', 'Timing needs review', mismatch
      ? `Possible timing mismatch${affected ? ` near ${affected}` : ''}. Some notes may play early or late relative to the recording.`
      : warning.version !== 2 && !v.timingWarning
        ? 'Ending sync not independently confirmed. Songsterr timing retained; short ending silence added.'
        : `Timing could not be confirmed${affected ? ` near ${affected} in the original recording` : ''}. Songsterr timing was retained. Review the recorded adjustments; this does not identify a particular arrangement as faulty.`);
  } else if (verified && timing) {
    add('timing', 'info', 'Audio timing checks', timing.status === 'inconclusive'
      ? `Automatic checks supported timing in ${timing.supportedWindows} of ${timing.windowCount} passages. The remaining passages could not be reliably verified; this does not necessarily mean they are wrong. ${v.timing === 'source_map_repaired' ? 'The recorded opening adjustment was retained; later Songsterr timing was preserved.' : v.timing === 'source_map' ? "Songsterr's timing was preserved." : 'The imported timing was preserved.'}`
      : `${timing.supportedWindows} of ${timing.windowCount} passages supported by automatic checks. This does not verify every individual note.`);
  }
  if (v.timing) add('timing_method', 'info', 'Timing source', v.timing === 'source_map_repaired'
    ? 'Timing: opening corrected against the recording; later Songsterr timing retained.'
    : v.timing === 'source_map' ? 'Timing: Songsterr recording map.' : 'Timing: automatic estimate.');
  if (completed && v.omissions?.omittedNotes) {
    groups.delete('warning:high_frets');
    add('high_frets', 'warning', 'High-fret notes omitted', `${v.omissions.omittedNotes} high-fret or connected slide events omitted from display and scoring. The complete original tab is saved inside the FeedPak.${v.omissions.excludedTracks?.length ? ` ${v.omissions.excludedTracks.length} arrangements had no supported notes remaining.` : ''}`);
    for (const f of report?.findings || []) if (f.feature === 'note.fret_range') {
      const g = groups.get('warning:high_frets'), at = location(f);
      if (at && !g.locations.includes(at) && g.locations.length < 8) g.locations.push(at);
    }
  }
  if (verified && v.adjustments?.omittedEndingNotes) add('ending_notes', 'warning', 'Notes after the recording omitted', `${v.adjustments.omittedEndingNotes} ending notes omitted at or after the original audio ending. The original tab and omission details are saved in the FeedPak.`);
  if (completed && v.adjustments?.terminalSustains) add('ending_sustains', 'warning', 'Final sustains shortened', `${v.adjustments.terminalSustains} final sustains shortened to the audio ending. Original durations are saved with the tab.`);
  if (verified && v.adjustments?.terminalSlideOuts) add('ending_slides', 'warning', 'Final slide-outs shortened', `${v.adjustments.terminalSlideOuts} directional ${v.adjustments.terminalSlideOuts === 1 ? 'slide-out ends' : 'slide-outs end'} with the audio after a timing check. The original slide intervals and cutoff details are saved in the FeedPak.`);
  if (v.preparationSeconds > 0) add('preparation', 'info', 'Preparation time', `Preparation time added: ${v.preparationSeconds.toFixed(2)} seconds.`);
  if (v.endingSilenceSeconds > 0) add('ending_silence', 'info', 'Ending silence', `Ending silence added: ${v.endingSilenceSeconds.toFixed(2)} seconds to preserve the final note tails. This adds no missing music.`);
  if (verified && v.endingTiming?.status === 'supported') add('ending_timing', 'info', 'Ending timing checks', 'Recording timing supported through the ending. This does not verify every transcribed note.');
  if (v.compatibility?.status === 'requires_consumer_support') add('game_version', 'info', 'Game compatibility', 'Ghost-note or directional-slide symbols need a FeedBack version that supports them. This import does not detect the game version.');
  if (completed && job.artwork && job.artwork.status !== 'matched') add('artwork', 'warning', 'Album cover unavailable or uncertain', 'You can add an image in Edit FeedPaks. This does not affect the playable tab.');
  if (verified && job.artwork?.status === 'matched' && job.artwork.reason === 'compilation_album_cover') {
    const explanation = typeof job.artwork.message === 'string' && job.artwork.message.trim()
      ? job.artwork.message.slice(0, 600) : 'Using a compilation cover by the same artist; artwork for this version was not found.';
    add('artwork_fallback', 'info', 'Using compilation artwork', `${explanation} This does not identify the audio recording's album.`);
  }
  if (completed && v.status !== 'passed') add('verification', 'warning', 'Source verification unavailable', v.status === 'modified' ? 'This file has changed since conversion. The report describes the original import.' : 'Source verification is unavailable for this file.');

  const hybrid = v.hybridLead;
  if (completed && hybrid) {
    const reasons = new Set((hybrid.limitations || []).map(row => row.reason));
    if (reasons.has('unsupported_source_gesture')) {
      const omitted = groups.get('warning:high_frets');
      if (verified && v.omissions?.omittedNotes > 0 && omitted) {
        omitted.message += ' These omissions also limit the source phrases available to Hybrid Lead.';
      } else add('hybrid_unsupported', 'warning', 'Hybrid Lead has unsupported source phrases', 'Some source gestures could not be represented in Hybrid Lead. Review the affected arrangements in the details below.');
    }
    if (reasons.has('conflicting_primary')) {
      add('hybrid_choices', 'warning', 'Some overlapping guitar parts were left out of Hybrid Lead', 'Some source passages conflict with the selected lead or solo. The original arrangements remain available; the details show which parts were left out.');
      for (const row of hybrid.limitations || []) if (row.reason === 'conflicting_primary') {
        const at = [row.name || row.trackId, ranges(row.missingRanges)].filter(Boolean).join(' · ');
        const group = groups.get('warning:hybrid_choices');
        if (at && !group.locations.includes(at) && group.locations.length < 8) group.locations.push(at);
      }
    }
    if (hybrid.planningLimited || reasons.has('planning_limit')) add('hybrid_planning', 'warning', 'Hybrid Lead search was incomplete', 'The search reached its limit. The best available arrangement was retained, but additional guitar passages may fit.');
    if (hybrid.musicalAudit?.localLeadAudit?.unresolvedCount > 0 || reasons.has('unresolved_primary')) add('hybrid_missing_lead', 'warning', 'Hybrid Lead has lead passages to review', 'Some identified lead passages were not accounted for. Review the original arrangements and Hybrid Lead details.');
    if (['incompatible_setup', 'excluded_by_user', 'explicit_accompaniment'].some(reason => reasons.has(reason))) add('hybrid_sources', 'warning', 'Some Hybrid Lead sources were unavailable or excluded', 'Some identified passages could not contribute because of tuning, capo or source selection. See the source choices below.');
    const known = new Set(['unsupported_source_gesture', 'conflicting_primary', 'planning_limit', 'unresolved_primary', 'incompatible_setup', 'excluded_by_user', 'explicit_accompaniment', 'alternate_voice']);
    if ([...reasons].some(reason => !known.has(reason)) || hybrid.coverageStatus === 'limited' && (!reasons.size || [...reasons].every(reason => reason === 'alternate_voice'))) {
      add('hybrid', 'warning', 'Hybrid Lead has passages to review', 'Some source passages could not be fully accounted for in Hybrid Lead. The original arrangements are preserved; review the Hybrid Lead details below.');
    }
  }
  for (const item of job.warnings || []) {
    const message = typeof item === 'string' ? item : item?.message || JSON.stringify(item);
    const at = typeof item === 'object' ? item?.location || '' : '';
    // Only recognized routine messages are quieted. New/unclassified messages
    // remain visible, grouped for review, with their complete originals saved.
    if (boundary && typeof item === 'string' && boundary.rawWarnings.has(message)) {
      // This exact warning is already represented by the qualified beat counts.
    } else if (/^Key signature in .+, measure \d+, is retained in source evidence only\.$/.test(message)) {
      add('key_signatures', 'diagnostic', 'Key signatures retained', 'Key signatures were retained in the source; explicit frets and pitches are unchanged.');
    } else if (/^Excluded non-guitar\/bass track:/.test(message)) {
      add('excluded_instruments', 'diagnostic', 'Other instruments excluded', 'The requested guitar and bass arrangements were selected.');
    } else if (isValidatedEmptyWarning(message, report)) {
      add('empty_tracks', 'diagnostic', 'Empty tracks skipped', 'Tracks with no playable notes were skipped; original source annotations are retained.');
    } else if (message === 'Timing was imported from Songsterr for this tab revision and recording.') {
      add('timing_source', 'diagnostic', 'Timing source', message);
    } else if (typeof item === 'string' && /^Excluded arrangement notation: Songsterr part field (clef|grandStaffGroup) is retained in source evidence only\.$/.test(message)) {
      add('excluded_notation', 'diagnostic', 'Notation on excluded arrangements', 'Notation for parts outside the selected guitar and bass arrangements remains in the saved source.');
    } else if (verified && /^Timing not fully verified\. Automatic checks supported timing in \d+ of \d+ passages\./.test(message) && timing) {
      // Already presented once from structured timing evidence above.
    } else if (verified && warning?.status === 'accepted_with_warning' && (message === warning.message || message === 'Ending sync not independently confirmed. Songsterr timing retained; short ending silence added.')) {
      // Structured adjustment warning above includes this finding.
    } else if (/^Notation for .+ is retained in source evidence only:/.test(message) && /The playable tab preserves|outside FeedPak notation v1/.test(message)) {
      if (!report?.findings?.some(f => f.feature?.startsWith('notation.'))) add('notation', 'info', 'Standard notation unavailable for some arrangements', 'The playable guitar/bass tab is retained. Some staff notation remains in the original source.');
    } else if (/^Source arrangement \d+: separated into \d+ selectable voices\.$/.test(message)) {
      add('voices', 'info', 'Simultaneous voices', 'Separate selectable arrangements preserve simultaneous voices.');
    } else if (/^Source arrangement \d+: \d+ equivalent or compatible let-ring attacks combined; original voices retained\.$/.test(message)) {
      add('combined_voices', 'info', 'Compatible voices combined', 'Equivalent or compatible attacks share a playable event; original voices remain in the source.');
    } else if (completed && v.omissions?.omittedNotes && message === `Completed with omitted notes: ${v.omissions.omittedNotes} unsupported high-fret or connected slide events are not displayed or scored. Original tab retained. Staff notation for affected arrangements is retained in the source only.`) {
      // Same omission already shown from the structured verification summary.
    } else if (/^\d+ lyric syllables follow the last available vocal note; retained in source only\.$/.test(message)
        || message === 'Lyrics use the public player text limit; text beyond that limit remains in the saved source only.'
        || /^Lyrics were not imported: .+\.$/.test(message)) {
      add('lyrics', 'warning', 'Lyrics', message);
    } else if (/^Timing (?:is supported|remains uncertain), but the recording's pitch appears about /.test(message)) {
      add('pitch', 'warning', 'Recording pitch differs from the tab tuning', message);
    } else if (/^Equivalent section labels in measure [1-9]\d* use the authored label (?:'[^\r\n]*'|"[^\r\n]*"); all track labels are retained\.$/.test(message)) {
      add('sections', 'diagnostic', 'Section labels', 'Equivalent section labels use an authored label; all original labels are retained.');
    } else if (/^Section label in measure \d+ uses guitar\/bass consensus; all track labels are retained\.$/.test(message)) {
      add('sections', 'diagnostic', 'Section labels', 'Section labels use guitar/bass consensus; all original labels are retained.');
    } else {
      add('other', 'warning', 'Additional import information needs review', message, at);
    }
  }
  const values = [...groups.values()];
  return { version: VERSION, warnings: values.filter(g => g.level === 'warning'), information: values.filter(g => g.level === 'info'),
    diagnosticCount: values.filter(g => g.level === 'diagnostic').reduce((n, g) => n + g.occurrences, 0), rawWarningCount: (job.warnings || []).length };
}

module.exports = { VERSION, classifyFinding, prioritizeFindings, playerNotices };

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
};

function classifyFinding(f) {
  // Unknown and blocking semantics take precedence over an otherwise familiar
  // feature name. Future converters must not silently inherit a downgrade.
  if (f.impact === 'blocking' || f.impact === 'gameplay_omission'
      || ['unknown_semantics', 'source_structure', 'conversion_check', 'converter_gap'].includes(f.category)) return 'warning';
  if (f.impact === 'source_retained') return 'diagnostic';
  if (f.category === 'game_limitation' && f.impact === 'display_or_expression'
      && typeof f.message === 'string' && (informational.get(f.feature) === f.message || policies.get(f.feature) === f.message)) return 'info';
  return 'warning';
}
function location(f) {
  return [f.arrangement, f.measure && `Measure ${f.measure}`, f.beat && `beat ${f.beat}`, f.note && `note ${f.note}`]
    .filter(Boolean).join(' · ') || f.location || '';
}
function prioritizeFindings(findings) {
  const rank = f => f.impact === 'blocking' || f.impact === 'gameplay_omission' ? 0
    : classifyFinding(f) === 'warning' ? 1 : classifyFinding(f) === 'info' ? 2 : 3;
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

function playerNotices(job, report, { reportUnavailable = false } = {}) {
  const groups = new Map();
  function add(id, level, title, message, at = '', count = 1) {
    const key = `${level}:${id}`;
    if (!groups.has(key)) groups.set(key, { id, level, title, message, occurrences: 0, locations: [], examples: [] });
    const group = groups.get(key); group.occurrences += count;
    if (at && !group.locations.includes(at) && group.locations.length < 8) group.locations.push(at);
    if (message && message !== group.message && !group.examples.includes(message) && group.examples.length < 8) group.examples.push(message);
  }
  const v = job.verification || {}, completed = job.state === 'completed', verified = completed && v.status === 'passed';
  for (const f of report?.findings || []) {
    const level = classifyFinding(f);
    const id = f.feature === 'note.fret_range' ? 'high_frets' : f.feature || 'unknown_finding';
    add(id, level, level === 'warning' ? titles[f.feature] || 'Source feature needs review'
      : level === 'info' && f.feature === 'note.harmonicFret' && f.message === informational.get(f.feature)
        ? 'Natural-harmonic touch position'
        : informationTitles[f.feature] || 'Retained source information', f.message, location(f));
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
    if (/^Key signature in .+, measure \d+, is retained in source evidence only\.$/.test(message)) {
      add('key_signatures', 'diagnostic', 'Key signatures retained', 'Key signatures were retained in the source; explicit frets and pitches are unchanged.');
    } else if (/^Excluded non-guitar\/bass track:/.test(message)) {
      add('excluded_instruments', 'diagnostic', 'Other instruments excluded', 'The requested guitar and bass arrangements were selected.');
    } else if (message === 'Timing was imported from Songsterr for this tab revision and recording.') {
      add('timing_source', 'diagnostic', 'Timing source', message);
    } else if (/^Unclassified Songsterr part field (title|clef|grandStaffGroup) is retained in source evidence only\.$/.test(message)) {
      add('staff_metadata', 'diagnostic', 'Staff metadata retained', 'Staff layout and part metadata remain in the saved source.');
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
    } else if (/^\d+ lyric syllables follow the last available vocal note; retained in source only\.$/.test(message)) {
      add('lyrics', 'warning', 'Some lyrics could not be timed', message);
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

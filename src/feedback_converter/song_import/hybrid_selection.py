"""Deterministic source and regional evidence for automatic Hybrid Lead.

This module proposes musical ownership; it never copies or edits chart events.
The phrase planner closes proposed regions over complete source gestures. A
source's name is evidence about a role, never permission to change its setup.
"""
from collections import defaultdict
from hashlib import sha256
import json
import re
from statistics import median
import unicodedata

from .hybrid_context import Clock

EPS = 1e-7
_ROLE_WORDS = re.compile(r'\b(guitar|bass|lead|rhythm|solo|main|clean|acoustic|classical|backing|background|overdubs?|extras?|slide|chords?|harmonics?|tremolo|feedback|delay|echo|effects?|fx)\b', re.I)
_PERSON_STOP = {'guitar', 'lead', 'rhythm', 'solo', 'main', 'clean', 'acoustic', 'electric', 'bass', 'voice', 'track', 'unknown'}


def _text(value):
    return ' '.join(unicodedata.normalize('NFKC', str(value or '')).casefold().split())


def _tokens(value):
    return set(re.findall(r'[^\W\d_]+', _text(value)))


def parse_track(track):
    """Separate performer, function and sound rather than assigning one role."""
    name = str(track.get('name', ''))
    fields = [p.strip() for p in re.split(r'\|', name) if p.strip()]
    role_fields = [p for p in fields if _ROLE_WORDS.search(p)
                   or re.fullmatch(r'harmon(?:y|ies)(?:\s+\d+)?', p, re.I)]
    role_text = ' | '.join(role_fields) if role_fields else name
    tokens = _tokens(role_text)
    harmony = bool(re.search(r'\bharmon(?:y|ies)\b', role_text, re.I))
    # A model such as Harmony Sovereign, in its own field, is not a role.
    if fields and role_fields:
        harmony = any(re.search(r'\bharmon(?:y|ies)\b', p, re.I) for p in role_fields)
    solo = bool(re.search(r'\bsolo\b', role_text, re.I))
    rhythm = bool(re.search(r'\b(rhythm|backing|background|chords?)\b', role_text, re.I))
    # The importer may default a generically named guitar to role=lead. That
    # normalized field is not independent evidence of a foreground melody.
    lead = 'lead' in tokens
    effect = bool(re.search(r'\b(delay|echo|effects?|fx|feedback)\b', role_text, re.I))
    extra = bool(re.search(r'\b(extras?|overdubs?)\b', role_text, re.I))
    main = 'main' in tokens
    dedicated = solo and not (rhythm or harmony or lead or main)
    tuning = track.get('tuning', [])
    bass_layer = bool(track.get('instrument') == 'guitar' and 'bass' in tokens and tuning
                      and min(tuning) < 36 and max(tuning) <= 55)
    # Nashville/high-strung parts are octave textures, not evidence that the
    # normal guitar should adopt their string registers. Require both the name
    # and measured octave displacement; acoustic tone or high pitches alone
    # do not establish this role. Common whole-instrument transposition is fine.
    octave_texture = False
    if (track.get('instrument') == 'guitar' and len(tuning) == 6
            and re.search(r'\b(high[\s-]*strung|nashville)\b', name, re.I)):
        shift = tuning[5] - 64
        offsets = [pitch - standard - shift for pitch, standard in zip(tuning, (40, 45, 50, 55, 59, 64))]
        octave_texture = (offsets[-2:] == [0, 0] and all(value in (0, 12, 24) for value in offsets)
                          and sum(value >= 12 for value in offsets[:4]) >= 2)
    performers = []
    for part in fields:
        words = _tokens(part)
        if (words and not _ROLE_WORDS.search(part) and not words.intersection({'harmony', 'harmonies'})
                and len(words) <= 4 and not re.search(r'\d', part)):
            performers.append(part)
    # Pipe-separated scores normally put the performer first. Restrict aliases
    # to that identity rather than matching instrument/model words anywhere.
    performer = performers[0] if performers else ''
    if effect:
        prior = 'accompaniment'
    elif solo and (rhythm or harmony):
        prior = 'mixed' if harmony else 'accompaniment'
    elif dedicated:
        prior = 'solo'
    elif lead or main:
        prior = 'lead'
    elif effect or rhythm or harmony or extra:
        prior = 'accompaniment'
    else:
        prior = 'unknown'
    return {'name': name, 'performer': performer, 'performerTokens': sorted(_tokens(performer)),
            'sharedPerformer': bool(re.search(r'&|/|\band\b', performer, re.I)),
            'priorRole': prior, 'lead': lead, 'main': main, 'rhythm': rhythm,
            'solo': solo, 'dedicatedSolo': dedicated, 'harmony': harmony,
            'effect': effect, 'extra': extra, 'bassRegisterLayer': bass_layer,
            'octaveTextureLayer': octave_texture,
            'toneTags': sorted(tokens.intersection({'clean', 'acoustic', 'classical', 'electric', 'slide', 'tremolo'}))}


def suggested_role(track):
    """Suggestions for an explicitly opened source review; unknown stays empty."""
    facts = parse_track(track)
    return 'lead' if facts['priorRole'] == 'mixed' else None if facts['priorRole'] == 'unknown' else facts['priorRole']


def automatic_role(track):
    """A missing label does not block a playable automatic arrangement."""
    return suggested_role(track) or 'accompaniment'


def _clock(performance):
    timeline = performance.get('compositionContext', {}).get('timeline') or performance.get('scoreTimeline')
    return Clock(timeline or {'tempoPoints': [{'quarter': 0, 'time': 0, 'bpm': 60}]})


def _notes(track):
    return list(track.get('notes', [])) + [dict(n, t=c['t']) for c in track.get('chords', []) for n in c.get('notes', [])]


def _union(intervals):
    result = []
    for a, b in sorted(intervals):
        if result and a <= result[-1][1] + EPS:
            result[-1][1] = max(result[-1][1], b)
        else:
            result.append([a, b])
    return result


def _span(intervals):
    return sum(b - a for a, b in _union(intervals))


def _activity(track, performance, clock):
    notes = [n for n in _notes(track) if not n.get('ghost') and not n.get('mt')]
    if not notes:
        notes = _notes(track)
    intervals = [(clock.quarter(n['t']), max(clock.quarter(n['t'] + n.get('sus', 0)), clock.quarter(n['t']) + .0625)) for n in notes]
    identities = {sid for n in notes for sid in n.get('source_ids', [])}
    beats = performance.get('compositionContext', {}).get('tracks', {}).get(track['id'], {}).get('beats', [])
    intervals.extend((b['start'], b['end']) for b in beats if not b['rest'] and identities.intersection(b.get('noteIds', [])))
    return _union(intervals)


def _canonical(track):
    # IDs remain a final tie break only; source array order never enters it.
    facts = {k: track.get(k) for k in ('tuning', 'capo', 'notes', 'chords')}
    content = sha256(json.dumps(facts, sort_keys=True, separators=(',', ':'), default=str).encode()).hexdigest()
    return _text(track.get('name')), content, str(track['id'])


def _base_rank(facts, fraction):
    if facts['effect']:
        return 0
    if facts['main']:
        return 6
    if facts['lead'] and not (facts['harmony'] or facts['extra']):
        return 5
    if facts['dedicatedSolo']:
        return 4 if fraction >= .5 else 1
    if facts['harmony'] or facts['extra']:
        return 2
    return 3


def select_base(performance, options=None):
    """Choose dominant tuning, then a suitable base whose capo fixes the setup.

    A brief solo in a different tuning cannot win by virtue of being a solo.
    Capo does not split the dominant-tuning vote; it follows the chosen base.
    """
    from .high_frets import project
    options = options or {}
    playable, _ = project(performance)
    guitars = [t for t in playable['tracks'] if t.get('instrument') == 'guitar' and (t.get('notes') or t.get('chords'))]
    empty = {'mainTrackId': None, 'setup': None, 'eligibleTrackIds': [], 'excluded': [], 'ranking': [], 'setupRanking': [], 'reason': 'no_playable_guitar'}
    if not guitars:
        return empty
    clock = _clock(performance)
    duration = max(EPS, clock.quarter(performance.get('duration', 0)))
    facts = {t['id']: parse_track(t) for t in guitars}
    activity = {t['id']: _activity(t, performance, clock) for t in guitars}
    active = {tid: _span(values) for tid, values in activity.items()}
    excluded = set(options.get('excludedTrackIds', []))
    provided = next((t for t in guitars if t['id'] == options.get('mainTrackId')), None)
    offered = [t for t in guitars if t['id'] not in excluded or t is provided]
    if not offered:
        return {**empty, 'reason': 'all_sources_excluded',
                'excluded': [{'trackId': t['id'], 'reason': 'excluded_by_user'} for t in sorted(guitars, key=lambda t: str(t['id']))]}
    normal_guitars = any(not facts[t['id']]['bassRegisterLayer'] and not facts[t['id']]['effect'] for t in offered)
    voting_excluded = {t['id']: 'bass_register_guitar_layer' for t in offered
                       if normal_guitars and facts[t['id']]['bassRegisterLayer']}
    groups = defaultdict(list)
    for track in offered:
        groups[tuple(track['tuning'])].append(track)
    regular_activity = []
    for members in groups.values():
        anchors = [t for t in members if not any(facts[t['id']][flag] for flag in
                   ('bassRegisterLayer', 'octaveTextureLayer', 'effect', 'dedicatedSolo', 'harmony', 'extra'))]
        regular_activity.append(_span([span for t in anchors for span in activity[t['id']]]))
    strongest_regular = max(regular_activity, default=0)
    for track in offered:
        # A token/brief regular-tuned solo cannot displace a whole texture song.
        # A substantial ordinary part must corroborate the normal setup first.
        if (facts[track['id']]['octaveTextureLayer'] and strongest_regular > EPS
                and strongest_regular + EPS >= active[track['id']] * .5):
            voting_excluded[track['id']] = 'octave_texture_guitar_layer'
    group_rows = []
    for tuning, members in groups.items():
        voting = [t for t in members if t['id'] not in voting_excluded]
        anchors = [t for t in voting if not facts[t['id']]['effect'] and not facts[t['id']]['dedicatedSolo']
                   and not facts[t['id']]['harmony'] and not facts[t['id']]['extra']]
        normal = _span([span for t in anchors for span in activity[t['id']]])
        total = _span([span for t in voting if not facts[t['id']]['effect'] for span in activity[t['id']]])
        best = max((_base_rank(facts[t['id']], active[t['id']] / duration) for t in voting), default=0)
        group_rows.append({'tuning': list(tuning), 'nonSoloActivity': round(normal, 8), 'activity': round(total, 8),
                           'baseRank': best, 'trackIds': sorted(t['id'] for t in members),
                           'votingExcluded': [{'trackId': t['id'], 'reason': voting_excluded[t['id']]}
                                              for t in sorted(members, key=lambda t: str(t['id'])) if t['id'] in voting_excluded]})
    group_rows.sort(key=lambda g: (-g['nonSoloActivity'], -g['baseRank'], -g['activity'], tuple(g['tuning'])))
    dominant = tuple(group_rows[0]['tuning'])
    ranking = sorted(guitars, key=lambda t: (-int(tuple(t['tuning']) == dominant), -int(t['id'] not in excluded),
                     int(t['id'] in voting_excluded),
                     -_base_rank(facts[t['id']], active[t['id']] / duration), -active[t['id']], _canonical(t)))
    main = provided or ranking[0]
    eligible, rejected = [], []
    for t in sorted(guitars, key=lambda t: str(t['id'])):
        reason = ('excluded_by_user' if t['id'] in excluded and t['id'] != main['id'] else
                  'incompatible_setup' if t['tuning'] != main['tuning'] or t.get('capo', 0) != main.get('capo', 0) else None)
        if reason:
            rejected.append({'trackId': t['id'], 'reason': reason})
        else:
            eligible.append(t['id'])
    return {'mainTrackId': main['id'], 'setup': {'tuning': list(main['tuning']), 'capo': main.get('capo', 0)},
            'eligibleTrackIds': eligible, 'excluded': rejected,
            'ranking': [{'trackId': t['id'], 'role': facts[t['id']]['priorRole'],
                         'activeQuarters': round(active[t['id']], 8), 'baseRank': _base_rank(facts[t['id']], active[t['id']] / duration)} for t in ranking],
            'setupRanking': group_rows, 'reason': 'provided_main' if provided else 'dominant_tuning_then_base'}


def _solo_section(name):
    value = _text(name)
    if not re.search(r'\bsolo\b', value):
        return False
    if re.search(r'\b(pre|post|before|after)[\s-]*solo\b', value):
        return False
    if re.search(r'\b(sax(?:ophone)?|tenor|piano|keyboard|organ|drum|bass|vocal|violin|flute|trumpet)\b', value) and 'guitar' not in value:
        return False
    return True


def _named_people(name):
    result = []
    for group in re.findall(r'\(([^)]+)\)', name):
        for part in re.split(r'\s*(?:&|/|,|\band\b)\s*', group, flags=re.I):
            words = _tokens(part) - _PERSON_STOP
            if words and not words.intersection({'part', 'verse', 'chorus', 'intro', 'outro'}):
                result.append(words)
    return result


def _row_core(row):
    return any(not n.get('ghost') and not n.get('mt') for n in row.get('notes', []))


def _local(track, rows, lo, hi, clock):
    notes = [n for r in rows for n in r.get('notes', []) if lo - EPS <= clock.quarter(n['t']) < hi - EPS
             and not n.get('ghost') and not n.get('mt')]
    if not notes:
        return None
    attacks = defaultdict(set)
    voicings = defaultdict(list)
    pitches = []
    for note in notes:
        attacks[round(clock.quarter(note['t']), 8)].add(note['s'])
        if 0 <= note.get('f', -1) <= 24 and 0 <= note['s'] < len(track['tuning']):
            pitch = track['tuning'][note['s']] + track.get('capo', 0) + note['f']
            pitches.append(pitch)
            voicings[round(clock.quarter(note['t']), 8)].append(pitch)
    expressive = sum(bool(n.get('bn') or n.get('bnv') or n.get('vb') or n.get('hp') or n.get('ho') or n.get('po')
                          or n.get('sl') is not None or n.get('slu') is not None) for n in notes) / len(notes)
    single = sum(len(v) == 1 for v in attacks.values()) / len(attacks)
    times = sorted(attacks)
    # Repeated four-attack cells can corroborate accompaniment, but repetition
    # alone is not evidence against a lead motif. Exact pitches are used only
    # for equality: globally transposing a passage leaves this measure intact.
    cells = defaultdict(list)
    for i in range(len(times) - 3):
        key = tuple((tuple(sorted(voicings[t])), round(t - times[i], 5)) for t in times[i:i + 4])
        cells[key].append(i)
    repeated = set()
    for starts in cells.values():
        if len(starts) > 1 and starts[-1] - starts[0] >= 4:
            repeated.update(i for start in starts for i in range(start, start + 4))
    intervals = {round(b - a, 5) for a, b in zip(times, times[1:])}
    active = _span((max(lo, clock.quarter(n['t'])), min(hi, clock.quarter(n['t'] + n.get('sus', 0))))
                   for n in notes)
    return {'attacks': len(attacks), 'single': single, 'expressive': expressive,
            'pitch': median(pitches) if pitches else 0, 'variety': len(set(pitches)),
            'palmMute': sum(bool(n.get('pm')) for n in notes) / len(notes),
            'letRing': sum(bool(n.get('lr')) for n in notes) / len(notes),
            'motifRepeat': len(repeated) / len(attacks),
            'rhythmVariety': min(1, max(0, len(intervals) - 1) / 3),
            'coverage': min(1, active / max(EPS, hi - lo)),
            'first': min(attacks), 'last': max(attacks)}


def _musical_score(data):
    """Bounded local evidence; neither register nor note density is a reward.

    One expressive held note must not outvote a whole melodic phrase solely
    because its expression fraction is 100%. Clean, low and polyphonic solos
    remain possible: tone, absolute pitch and the whole-track role are absent.
    """
    phrase_support = min(1, data['attacks'] / 4)
    backing_pattern = data['motifRepeat'] * max(data['letRing'], data['palmMute'])
    return (2 * data['single'] + 4 * data['expressive'] * phrase_support
            + min(data['variety'], 12) / 12 + .5 * data['rhythmVariety']
            - .8 * data['palmMute'] - 1.2 * backing_pattern)


def _clear_local_advantage(top, other):
    """Require both a margin and a musical explanation, not a label advantage."""
    expression = top['expressive'] - other['expressive']
    corroborated_expression = (expression >= .35 or (expression >= .1 and
        (top['rhythmVariety'] >= other['rhythmVariety'] + .3
         or top['single'] >= other['single'] + .25
         or (other['motifRepeat'] >= .5 and max(other['letRing'], other['palmMute']) >= .35))))
    return (_musical_score(top) >= _musical_score(other) + .6 and top['attacks'] >= 4
            and (corroborated_expression
                 or (other['motifRepeat'] >= .5 and max(other['letRing'], other['palmMute']) >= .35
                     and top['motifRepeat'] <= other['motifRepeat'] - .25)
                 or (top['single'] >= other['single'] + .25 and top['rhythmVariety'] >= .3)))


def _backing_context(data):
    """Corroborated accompaniment, not merely a low score or a rhythm label."""
    return (data['attacks'] >= 4 and data['expressive'] < .12
            and ((data['motifRepeat'] >= .5 and max(data['letRing'], data['palmMute']) >= .35)
                 or (data['single'] <= .25 and data['motifRepeat'] >= .5)))


def _islands(rows):
    # Internal ghost/muted attacks belong to the performed phrase. Discover its
    # activity before trimming a purely ghost fade at the outside boundaries.
    values = _union((r['start'], r['end']) for r in rows)
    islands = []
    for lo, hi in values:
        if islands and lo - islands[-1][1] < 1 - EPS:
            islands[-1][1] = max(hi, islands[-1][1])
        else:
            islands.append([lo, hi])
    result = []
    for lo, hi in islands:
        core = [r for r in rows if lo - EPS <= r['start'] and r['end'] <= hi + EPS and _row_core(r)]
        if core:
            result.append([min(r['start'] for r in core), max(r['end'] for r in core)])
    return result


def regional_candidates(performance, options, main_id, rows=None):
    """Return inferred primary episodes, including documented ineligible ones.

    Bounds are score quarters and are proposals, not permission to cut events.
    The caller closes them over exact source gestures and checks feasibility.
    """
    clock = _clock(performance)
    tracks = {t['id']: t for t in performance['tracks'] if t.get('instrument') == 'guitar'}
    if main_id not in tracks:
        return []
    if rows is None:
        from .hybrid_lead import event_rows
        context = performance.get('compositionContext', {}).get('tracks', {})
        rows = {tid: event_rows(t, context.get(tid, {'beats': []}), clock) for tid, t in tracks.items()}
    facts = {tid: parse_track(t) for tid, t in tracks.items()}
    main = tracks[main_id]
    roles = options.get('roles', {})
    preferred = options.get('preferredTrackIds', [])
    excluded = set(options.get('excludedTrackIds', []))
    end = clock.quarter(performance.get('duration', 0))
    sections = sorted(performance.get('sections', []), key=lambda s: (s['time'], s.get('name', '')))
    if not sections or sections[0]['time'] > EPS:
        sections.insert(0, {'time': 0, 'name': ''})
    result = []
    previous_primary = main_id

    def candidate(tid, lo, hi, priority, evidence, confidence, section='', alternatives=None, score=None,
                  owned_start=None, owned_end=None):
        track = tracks[tid]
        reason = ('excluded_by_user' if tid in excluded else
                  'incompatible_setup' if track['tuning'] != main['tuning'] or track.get('capo', 0) != main.get('capo', 0) else
                  'explicit_accompaniment' if roles.get(tid) == 'accompaniment' else None)
        value = {'trackId': tid, 'start': lo, 'end': hi, 'priority': priority,
                 'ownedStart': lo if owned_start is None else owned_start,
                 'ownedEnd': hi if owned_end is None else owned_end,
                 'evidence': evidence, 'confidence': confidence, 'sectionName': section, 'eligible': reason is None,
                 'score': score or [2 if evidence == 'dedicated_solo' else 1, 0, 0, 0]}
        if reason:
            value['reason'] = reason
        if alternatives:
            value['alternatives'] = sorted(alternatives)
        return value

    # Explicit/dedicated solo sources retain complete activity islands, including
    # pickups before authored labels. Ghost-only fades are not primary material.
    for tid in sorted(tracks):
        if roles.get(tid) == 'solo' or (facts[tid]['dedicatedSolo'] and not facts[tid]['effect']):
            for lo, hi in _islands(rows.get(tid, [])):
                result.append(candidate(tid, lo, hi, 'solo', 'dedicated_solo', 'high'))

    for index, section in enumerate(sections):
        lo = clock.quarter(section['time'])
        hi = clock.quarter(sections[index + 1]['time']) if index + 1 < len(sections) else end
        if hi <= lo + EPS:
            continue
        name = section.get('name', '')
        solo = _solo_section(name)
        people = _named_people(name) if solo else []
        local = {tid: data for tid, t in tracks.items() if (data := _local(t, rows.get(tid, []), lo, hi, clock))}
        usable = [tid for tid in local if not facts[tid]['effect']]
        if not usable:
            continue
        named = [tid for tid in usable if any(words <= set(facts[tid]['performerTokens']) for words in people)]
        # A first name shared by multiple full performer identities is ambiguous.
        # "A & B" is an ensemble containing B, not a second person named B.
        # Genuine distinct single-performer aliases (two Daves) remain unsafe.
        identities = {tuple(facts[tid]['performerTokens']) for tid in named if not facts[tid]['sharedPerformer']}
        if len(people) == 1 and len(identities) > 1:
            named = []

        pool = named or usable
        # Secondary labels normally identify parallel/supporting layers. They
        # cannot be an absolute veto when every ordinary source is demonstrably
        # backing and the featured phrase happens to be stored on an extra tab.
        def promoted_by_content(choices):
            ordinary = [tid for tid in choices if not (facts[tid]['harmony'] or facts[tid]['extra'])]
            if not ordinary or not all(_backing_context(local[tid]) for tid in ordinary):
                return set()
            return {tid for tid in choices if (facts[tid]['harmony'] or facts[tid]['extra'])
                    and not _backing_context(local[tid])
                    and all(_clear_local_advantage(local[tid], local[other]) for other in ordinary)}

        eligible_pool = [tid for tid in pool if candidate(tid, lo, hi, 'solo', 'regional_lead', 'medium')['eligible']]
        # Even a losing unavailable ordinary part cannot veto a playable
        # secondary foreground. The strongest unavailable solo still competes
        # above and remains documented if it wins the semantic comparison.
        promoted_layers = promoted_by_content(pool) | promoted_by_content(eligible_pool)

        def source_tier(tid):
            f = facts[tid]
            return (2 if f['dedicatedSolo'] or roles.get(tid) == 'solo' else
                    0 if (f['harmony'] or f['extra']) and tid not in promoted_layers else 1)

        def role_prior(tid):
            f = facts[tid]
            return .25 if (f['lead'] or f['main'] or roles.get(tid) == 'lead') else 0

        def rank(tid):
            # Ordinary lead/rhythm/clean labels are weak priors, after local
            # evidence. A named guitarist's solo may live on their rhythm tab.
            melody = _musical_score(local[tid])
            preference = preferred.index(tid) if tid in preferred else len(preferred)
            return (-source_tier(tid), -round(melody + role_prior(tid), 4), -round(melody, 4),
                    preference, -int(tid == main_id), -round(local[tid]['coverage'], 6), _canonical(tracks[tid]))

        def choice_evidence(tid, choices, reason):
            rivals = [other for other in choices if other != tid and
                      (source_tier(other) == source_tier(tid)
                       or (source_tier(other) < source_tier(tid) and _clear_local_advantage(local[other], local[tid])))]
            margin = min((_musical_score(local[tid]) - _musical_score(local[other]) for other in rivals), default=None)
            return {'reason': reason, 'confidenceMargin': round(margin, 4) if margin is not None else None,
                    'musicalScore': round(_musical_score(local[tid]), 4), 'rolePrior': role_prior(tid),
                    'considered': [{'trackId': other, 'sourceTier': source_tier(other),
                                    'layerPromotedByContent': other in promoted_layers,
                                    'musicalScore': round(_musical_score(local[other]), 4),
                                    'rolePrior': role_prior(other),
                                    'local': {key: round(value, 6) for key, value in local[other].items()}}
                                   for other in sorted(choices, key=rank)]}

        if solo:
            ordered = sorted(pool, key=rank)
            winner = ordered[0]
            # Parallel performances should not exchange ownership because one
            # happens to contain a few more bends in the next phrase. Retain a
            # coherent voice unless the rival has clear foreground evidence.
            incumbent = previous_primary if previous_primary in pool else main_id if main_id in pool else None
            kept_incumbent = False
            if incumbent is not None and source_tier(incumbent) == source_tier(winner):
                top, prior = local[winner], local[incumbent]
                clearly_ahead = _clear_local_advantage(top, prior)
                preferred_incumbent = not preferred or winner not in preferred or (incumbent in preferred and preferred.index(incumbent) <= preferred.index(winner))
                if not clearly_ahead and prior['attacks'] >= top['attacks'] * .5 and preferred_incumbent:
                    kept_incumbent = winner != incumbent
                    winner = incumbent
            # A section naming multiple players describes parallel voices. Pick
            # a coherent default and preserve the alternatives for the planner.
            evidence = 'named_soloist' if named else 'regional_lead'
            rivals = [tid for tid in ordered if tid != winner and source_tier(tid) == source_tier(winner)]
            secondary_challengers = [tid for tid in ordered if source_tier(tid) < source_tier(winner)
                                     and _clear_local_advantage(local[tid], local[winner])]
            clear = (not secondary_challengers and
                     (not rivals or all(_clear_local_advantage(local[winner], local[tid]) for tid in rivals)))
            confidence = ('high' if facts[winner]['dedicatedSolo'] or (named and clear) else
                          'medium' if clear else 'low')

            def section_candidate(tid, evidence_kind, certainty, choices):
                order_score = rank(tid)
                body = [r for r in rows.get(tid, []) if _row_core(r)
                        and any(lo - EPS <= clock.quarter(n['t']) < hi - EPS for n in r.get('notes', []))]
                body_start, body_end = min(r['start'] for r in body), max(r['end'] for r in body)
                alternatives = [other for other in choices if other != tid and rank(other)[0] == order_score[0]]
                value = candidate(tid, body_start, body_end, 'solo', evidence_kind, certainty, name, alternatives,
                                  [3 if evidence_kind == 'named_soloist' else 1, -order_score[0], -order_score[1], -order_score[2]],
                                  owned_start=min(lo, body_start), owned_end=hi)
                value['labelledSolo'] = True
                reason = ('coherent_incumbent' if kept_incumbent and tid == winner else
                          'foreground_over_secondary_label' if tid in promoted_layers else
                          'secondary_layer_uncertain' if secondary_challengers else
                          'sole_primary_named_candidate' if named and not rivals else
                          'clear_local_foreground' if clear else 'ambiguous_local_default')
                value['selectionEvidence'] = choice_evidence(tid, choices, reason)
                return value

            selected = section_candidate(winner, evidence, confidence, ordered)
            result.append(selected)
            if selected['eligible']:
                previous_primary = winner
            else:
                # An excluded solo remains documented, but it must not hide a
                # compatible alternate lead under the base's backing part.
                compatible = [tid for tid in usable if candidate(tid, lo, hi, 'solo', 'regional_lead', 'medium')['eligible']]
                named_compatible = [tid for tid in named if tid in compatible]
                compatible_ordinary = [tid for tid in compatible if not (facts[tid]['harmony'] or facts[tid]['extra'])]
                promoted_layers.update(promoted_by_content(compatible))
                compatible_foreground = [tid for tid in compatible
                    if tid in promoted_layers or (tid in compatible_ordinary
                    and (facts[tid]['lead'] or facts[tid]['dedicatedSolo'] or roles.get(tid) in {'lead', 'solo'}
                         or any(_clear_local_advantage(local[tid], local[other]) for other in compatible_ordinary
                                if other != tid and (other == main_id or _backing_context(local[other])))))]
                alternate_pool = named_compatible or compatible_foreground
                if named_compatible and all(_backing_context(local[tid]) for tid in named_compatible):
                    # Identity is useful when credible same-player foreground
                    # remains. It must not turn their proven backing into a
                    # solo while another player has the clear playable melody.
                    alternate_pool = [*named_compatible, *(tid for tid in compatible_foreground if tid not in named_compatible
                        and all(_clear_local_advantage(local[tid], local[other]) for other in named_compatible))]
                if alternate_pool:
                    # An excluded or differently tuned foreground cannot veto
                    # the best musical choice among the playable substitutes.
                    promoted_layers.update(promoted_by_content(alternate_pool))
                    alternate = min(alternate_pool, key=rank)
                    alternate_rivals = [tid for tid in alternate_pool if tid != alternate and source_tier(tid) == source_tier(alternate)]
                    alternate_secondary = [tid for tid in alternate_pool if source_tier(tid) < source_tier(alternate)
                                           and _clear_local_advantage(local[tid], local[alternate])]
                    alternate_clear = (not alternate_secondary and
                        (not alternate_rivals or all(_clear_local_advantage(local[alternate], local[tid]) for tid in alternate_rivals)))
                    same_performer = alternate in named_compatible
                    fallback = section_candidate(alternate, 'named_soloist' if same_performer else 'regional_lead',
                                                 'high' if same_performer and alternate_clear else 'medium' if alternate_clear else 'low', alternate_pool)
                    fallback['selectionEvidence'] = choice_evidence(alternate, alternate_pool, 'compatible_alternate')
                    fallback['fallbackForTrackId'] = winner
                    result.append(fallback)
                    previous_primary = alternate
        else:
            # Labels such as Pre-Solo, Post-Solo and Saxophone Solo cannot
            # create guitar-solo obligations. Unlabelled/local foreground still
            # needs several corroborating musical clues before replacing base.
            base = local.get(main_id)
            foreground = []
            for tid in usable:
                if (tid == main_id or facts[tid]['dedicatedSolo']
                        or ((facts[tid]['harmony'] or facts[tid]['extra']) and tid not in promoted_layers)):
                    continue
                data = local[tid]
                # A rhythm tab can carry a featured line outside a labelled
                # solo too. Replacing active base still needs multiple local
                # clues, not merely its name, register, density or one bend.
                plausible = not facts[tid]['bassRegisterLayer']
                distinct = base is not None and (data['single'] > base['single'] + .2 or data['pitch'] >= base['pitch'] + 7
                    or (base['motifRepeat'] >= .5 and max(base['letRing'], base['palmMute']) >= .35
                        and data['motifRepeat'] <= base['motifRepeat'] - .25)
                    or data['rhythmVariety'] >= base['rhythmVariety'] + .3)
                expressive = data['expressive'] >= .12 and (base is None or data['expressive'] >= base['expressive'] + .1)
                clear = base is not None and _clear_local_advantage(data, base)
                if plausible and distinct and expressive and clear and data['attacks'] >= 4 and data['variety'] >= 3:
                    foreground.append(tid)
            if foreground:
                winner = min(foreground, key=rank)
                order_score = rank(winner)
                for a, b in _islands([r for r in rows.get(winner, []) if lo - EPS <= r['start'] < hi - EPS]):
                    value = candidate(winner, a, b, 'solo', 'regional_lead', 'medium', name,
                                      [tid for tid in foreground if tid != winner],
                                      [1, -order_score[0], -order_score[1], -order_score[2]],
                                      owned_start=a, owned_end=min(b, hi))
                    value['selectionEvidence'] = choice_evidence(winner, [main_id, *foreground], 'corroborated_regional_foreground')
                    result.append(value)
    unique = {}
    for item in result:
        key = item['trackId'], item['start'], item['end'], item['ownedStart'], item['ownedEnd'], item['evidence']
        unique[key] = item
    return sorted(unique.values(), key=lambda p: (p['start'], p['end'], p['trackId'], p['evidence']))

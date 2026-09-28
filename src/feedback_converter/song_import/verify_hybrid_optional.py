"""Source-copy checks for retaining old accompaniment around a foreground line.

These checks establish physical provenance and bounds, not the producer's
subjective melody ranking or a claim that its previous optimizer was optimal.
"""
import math

EPS = 1e-5


def _refs(values):
    if not isinstance(values, list) or not values:
        return None
    if any(not isinstance(v, dict) or v.get('kind') not in {'notes', 'chords'}
           or type(v.get('index')) is not int or v['index'] < 0 for v in values):
        return None
    result = {(v['kind'], v['index']) for v in values}
    return result if len(result) == len(values) else None


def audit_retained_boundary(passage, receipt, event_facts, at, check):
    variant = passage['variant']
    parent, episode = variant.get('retainedParent'), variant.get('foregroundEpisode')

    def fail(message):
        check.fail('hybrid_retained_boundary', 'hybrid/passages/variant', message)

    if not isinstance(parent, dict) or not isinstance(episode, dict):
        fail('Retained accompaniment requires original-parent and selected-foreground evidence.')
        return
    for entry in (parent, episode):
        if (not all(type(entry.get(k)) in (int, float) and math.isfinite(entry[k]) for k in ('start', 'end'))
                or entry['start'] >= entry['end'] or _refs(entry.get('events')) is None):
            fail('Malformed retained-parent or foreground episode.')
            return
    tid = passage['trackId']
    parent_refs, episode_refs, refs = _refs(parent['events']), _refs(episode['events']), _refs(passage['events'])
    if parent.get('trackId') != tid or episode.get('trackId') == tid or refs is None or not refs <= parent_refs:
        fail('A retained fragment must copy the same prior accompaniment beside a different foreground.')
        return
    for key in ('start', 'end'):
        if abs(parent[key] - variant.get('parent' + key.title(), float('inf'))) > EPS:
            fail('Retained-parent provenance disagrees with outer source provenance.')
    for key in ('boundaryQuarters', 'boundaries'):
        if parent.get(key) != variant.get('parent' + key[0].upper() + key[1:]):
            fail('Retained-parent boundaries disagree with outer source provenance.')
    old_variant = parent.get('variant')
    if old_variant is not None and (not isinstance(old_variant, dict) or old_variant.get('kind') != 'gap_safe_subphrase'):
        fail('Retained-fragment chains are not permitted.')
    rows = event_facts.get(tid, {})
    # Independently reconstruct the complete prior passage, including long
    # written slots and connected source gestures; a short fabricated parent
    # cannot explain omitted interior events.
    expected = {key for key, row in rows.items()
                if parent['start'] - EPS <= row['onset'] < parent['end'] - EPS}
    if parent_refs != expected:
        fail('Retained-parent event membership is not a complete source passage.')
    if any(key not in rows or rows[key]['start'] < parent['start'] - EPS
           or rows[key]['end'] > parent['end'] + EPS for key in parent_refs):
        fail('Retained-parent bounds cut a connected source gesture.')
    candidates = [p for p in receipt.get('passages', []) if p.get('trackId') == episode.get('trackId')
                  and abs(p.get('start', -1) - episode['start']) <= EPS
                  and abs(p.get('end', -1) - episode['end']) <= EPS
                  and _refs(p.get('events')) == episode_refs and p.get('priority') not in {'solo', 'lead'}]
    if not candidates:
        fail('The claimed optional foreground episode was not actually selected intact.')
    if not parent['start'] < episode['end'] - EPS or not parent['end'] > episode['start'] + EPS:
        fail('The foreground did not replace any of this parent.')
    side = variant.get('retentionSide')
    if side == 'before':
        if abs(passage['start'] - parent['start']) > EPS:
            fail('A retained prefix must reach the original outer start.')
        left, right = passage['end'], episode['start']
    elif side == 'after':
        if abs(passage['end'] - parent['end']) > EPS:
            fail('A retained suffix must reach the original outer end.')
        left, right = episode['end'], passage['start']
    else:
        fail('A retained fragment must be immediately before or after its foreground.')
        return
    if right - left < .25 - EPS or at(right) - at(left) < .125 - EPS:
        fail('Retained accompaniment lacks the required source-change guard.')
    peers = [p for p in receipt.get('passages', []) if p.get('variant', {}).get('kind') == 'retained_optional_boundary'
             and p['variant'].get('foregroundEpisode') == episode]
    if len(peers) > 2 or sum(p['variant'].get('retentionSide') == side for p in peers) != 1:
        fail('An optional foreground can retain at most one prior boundary on each side.')


def audit_retained_parent_source(passage, source, part, order, clock, rows, check):
    """Check inherited evidence on the full original, not its one-sided remnant."""
    from .verify_hybrid import _signature
    from .verify_hybrid_continuity import audit_section_join

    parent = passage.get('variant', {}).get('retainedParent')
    if not isinstance(parent, dict):
        return  # The structural retained-boundary audit reports this.

    def fail(message):
        check.fail('hybrid_retained_boundary', 'hybrid/passages/variant/retainedParent', message)

    def number(value):
        return type(value) in (int, float) and math.isfinite(value)

    bounds, labels = parent.get('boundaryQuarters'), parent.get('boundaries')
    if (not isinstance(bounds, list) or len(bounds) != 2 or not all(number(q) for q in bounds)
            or not isinstance(labels, list) or len(labels) != 2
            or not all(number(parent.get(k)) for k in ('start', 'end'))
            or not bounds[0] - EPS <= parent['start'] < parent['end'] <= bounds[1] + EPS):
        fail('Invalid original retained-parent boundaries.')
        return
    old = parent.get('variant')
    if old is not None:
        outer = old.get('parentBoundaryQuarters', []) if isinstance(old, dict) else []
        outer_labels = old.get('parentBoundaries', []) if isinstance(old, dict) else []
        if (not isinstance(old, dict) or old.get('kind') != 'gap_safe_subphrase'
                or not isinstance(outer, list) or len(outer) != 2 or not all(number(q) for q in outer)
                or not isinstance(outer_labels, list) or len(outer_labels) != 2
                or not all(label in {'song', 'section', 'rest', 'repeat'} for label in outer_labels)
                or not all(number(old.get(k)) for k in ('parentStart', 'parentEnd'))
                or not outer[0] - EPS <= old['parentStart'] <= parent['start'] + EPS
                or not parent['end'] <= old['parentEnd'] + EPS <= outer[1] + 2 * EPS):
            fail('Invalid inherited source-subphrase provenance.')
            return
    starts = [float(q) for q in clock.measure_starts] + [float(clock.quarters)]
    sections = {starts[i] for i, bi in enumerate(order) if source.bars[bi].section}
    intervals = [(starts[i] + float(beat['q']), starts[i] + float(beat['q'] + beat['length']))
                 for i, bi in enumerate(order) for beat in part.beats[bi] if not beat['rest']]
    signatures = [_signature(part, bi) for bi in order]
    for q, label in zip(bounds, labels):
        valid = ((label == 'song' and any(abs(q - edge) <= EPS for edge in (0, starts[-1])))
                 or (label == 'section' and any(abs(q - marker) <= EPS for marker in sections)))
        index = next((i for i, start in enumerate(starts) if abs(q - start) <= EPS), None)
        if label == 'rest':
            left = max((end for start, end in intervals if end <= q + EPS), default=0)
            right = min((start for start, end in intervals if start >= q - EPS), default=starts[-1])
            valid = right - left >= 1 - EPS and not any(start + EPS < q < end - EPS for start, end in intervals)
        if label == 'repeat' and index is not None:
            valid = any(0 <= i and i + 2 * size <= len(order)
                        and signatures[i:i + size] == signatures[i + size:i + 2 * size]
                        for size in (1, 2, 4) for i in (index, index - size, index - 2 * size))
        if label in {'bar', 'gesture'} and old is not None:
            edge = (index is not None if label == 'bar' else
                    any(abs(q - value) <= EPS for row in rows.values() for value in (row['start'], row['end'])))
            valid = (edge and not any(row['start'] + EPS < q < row['end'] - EPS for row in rows.values())
                     and not any(start + EPS < q < end - EPS for start, end in intervals))
        if not valid:
            fail('An original retained-parent boundary is unsupported by the source.')
    if old is not None:
        audit_section_join(parent, source, part, order, clock, rows, check)
    else:
        # Removing the old variant and relabeling its outer edges as natural
        # cannot erase independently cuttable sections inside the claimed parent.
        audit_section_join({**parent, 'variant': {'parentBoundaryQuarters': bounds}},
                           source, part, order, clock, rows, check)


def audit_edge_omissions(receipt, event_facts, parts, source, order, clock, at, quarter_at,
                        primary_protected, main_id, check):
    """Verify the small complete backing edges lost to an intact optional line.

    This verifies source membership and physical limits independently. It does
    not turn the producer's subjective foreground label into a musical oracle.
    """
    from .verify_hybrid_opportunities import pitched

    records = receipt.get('selection', {}).get('optionalForeground', {}).get('allowedEdgeOmissions', [])
    if not isinstance(records, list):
        check.fail('hybrid_edge_omission', 'hybrid/selection/optionalForeground', 'Invalid edge-omission list.')
        return
    selected = {(p.get('trackId'), ref.get('kind'), ref.get('index'))
                for p in receipt.get('passages', []) for ref in p.get('events', [])}
    totals, seen, sides = {}, set(), set()

    def number(value):
        return type(value) in (int, float) and math.isfinite(value)

    def fail(message):
        check.fail('hybrid_edge_omission', 'hybrid/selection/optionalForeground/allowedEdgeOmissions', message)

    for record in records:
        if not isinstance(record, dict):
            fail('Malformed edge-omission entry.')
            continue
        parent, episode = record.get('incumbentParent'), record.get('foregroundEpisode')
        if (not all(isinstance(p, dict) and all(number(p.get(k)) for k in ('start', 'end'))
                    and p['start'] < p['end'] and _refs(p.get('events')) is not None for p in (parent, episode))
                or not all(number(record.get(k)) for k in ('start', 'end', 'quarterBeats'))
                or record['start'] >= record['end'] or _refs(record.get('events')) is None
                or record.get('side') not in {'before', 'after'}):
            fail('Malformed original parent, foreground episode, or edge bounds.')
            continue
        tid, side = record.get('trackId'), record['side']
        if (tid == main_id or tid not in parts or tid not in event_facts or parent.get('trackId') != tid
                or episode.get('trackId') == tid):
            fail('Only an optional donor beside a different foreground can lose an edge.')
            continue
        donor, main = parts[tid]['source'], parts[main_id]['source']
        if donor.instrument != 'guitar' or donor.tuning != main.tuning or donor.capo != main.capo:
            fail('An omitted incumbent must have the same playable guitar setup.')
        refs, parent_refs, episode_refs = _refs(record['events']), _refs(parent['events']), _refs(episode['events'])
        actual_episode = [p for p in receipt.get('passages', []) if p.get('trackId') == episode.get('trackId')
                          and abs(p.get('start', -1) - episode['start']) <= EPS
                          and abs(p.get('end', -1) - episode['end']) <= EPS
                          and _refs(p.get('events')) == episode_refs and p.get('priority') not in {'solo', 'lead'}]
        if not actual_episode:
            fail('The claimed foreground was not selected intact as an optional passage.')
        if not parent['start'] < episode['end'] - EPS or not parent['end'] > episode['start'] + EPS:
            fail('The original parent does not compete with this foreground.')
        rows = event_facts[tid]
        expected_parent = {key for key, row in rows.items() if parent['start'] - EPS <= row['onset'] < parent['end'] - EPS}
        if (parent_refs != expected_parent or not refs <= parent_refs
                or any(key not in rows or rows[key]['start'] < parent['start'] - EPS
                       or rows[key]['end'] > parent['end'] + EPS for key in parent_refs)):
            fail('The incumbent parent is not a complete source passage.')
            continue
        audit_retained_parent_source({'variant': {'retainedParent': parent}}, source, donor, order, clock, rows, check)
        if any(parent['start'] < end + .25 - EPS and parent['end'] > start - .25 + EPS
               or at(parent['start']) < at(end) + .125 - EPS and at(parent['end']) > at(start) - .125 + EPS
               for start, end in primary_protected):
            fail('An omitted incumbent crosses protected primary material or its change guard.')
        members = {key: rows[key] for key in refs}
        expected_edge = {key for key, row in rows.items() if record['start'] - EPS <= row['onset'] < record['end'] - EPS}
        spans = sorted((row['start'], row['end']) for row in members.values())
        edge_start, edge_end = spans[0]
        connected = True
        for start, end in spans[1:]:
            connected &= start <= edge_end + EPS
            edge_end = max(edge_end, end)
        if (refs != expected_edge or not connected or abs(edge_start - record['start']) > EPS
                or abs(edge_end - record['end']) > EPS
                or any(row['start'] < record['start'] - EPS or row['end'] > record['end'] + EPS for row in members.values())
                or any(row['start'] + EPS < edge < row['end'] - EPS for edge in (edge_start, edge_end) for row in rows.values())):
            fail('An omitted edge must be one complete contiguous source component.')
        for ref in record['events']:
            row = rows[(ref['kind'], ref['index'])]
            for field in ('sourceIds', 'occurrences'):
                check.equal('hybrid_event_lineage', 'hybrid/allowedEdgeOmissions/' + field, row[field], ref.get(field))
        identities = {(tid, *key) for key in refs}
        if identities & selected or identities & seen:
            fail('An omitted edge is already selected or counted more than once.')
        seen.update(identities)
        attacks = {round(row['onset'], 5) for row in members.values() if any(pitched(n) for n in row['notes'])}
        if (any(n.get('ghost') for row in members.values() for n in row['notes']) or len(attacks) >= 2
                or type(record.get('pitchedAttackCount')) is not int or record['pitchedAttackCount'] != len(attacks)):
            fail('An omitted edge must contain fewer than two pitched attacks and no ghost notes.')
        boundary = episode['start' if side == 'before' else 'end']
        outer = parent['start' if side == 'before' else 'end']
        guard = max(.25, abs(quarter_at(at(boundary) + (-.125 if side == 'before' else .125)) - boundary))
        left, right = (edge_end, boundary) if side == 'before' else (boundary, edge_start)
        if (abs((edge_start if side == 'before' else edge_end) - outer) > EPS
                or abs(outer - boundary) > 1 + guard + EPS
                or right - left < .25 - EPS or at(right) - at(left) < .125 - EPS):
            fail('An omitted edge must reach the original outer edge immediately beyond the foreground guard.')
        duration = edge_end - edge_start
        if abs(record['quarterBeats'] - duration) > EPS:
            fail('The claimed omitted duration does not match the complete source edge.')
        episode_key = (episode.get('trackId'), episode['start'], episode['end'], tuple(sorted(episode_refs)))
        if (episode_key, side) in sides:
            fail('A foreground episode may omit at most one original edge on each side.')
        sides.add((episode_key, side))
        totals[episode_key] = totals.get(episode_key, 0) + duration
        if totals[episode_key] > min(1.0, .2 * (episode['end'] - episode['start'])) + EPS:
            fail('The combined omitted edges exceed the one-beat or twenty-percent limit.')

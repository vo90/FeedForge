"""Recheck final lyric events against retained source and independent clocks.

Lexical/beat interpretation and phrase policy are shared, qualified separately
against the public player. Repeat traversal, score seconds and recording seconds
come from the independent fidelity checker; no production performance/export
function is used here. This is conversion verification, not acoustic alignment.
"""
import hashlib
import json
import re

from .songsterr_lyrics import POLICY, LEGACY_POLICY, GRACE_POLICY, MAX_EVENTS, written
from .lyric_timeline import phrases
from .tone_timeline import clock_parameters, digest
from .verify_timeline import Clock, RecordingMap, visits


def expected(document, source, alignment, duration, *, policy=POLICY):
    plan = written(document, [b.length for b in source.bars], policy=policy)
    if plan['status'] != 'ready':
        return [], plan, []
    order = visits(source)
    clock, recording = Clock(source, order), RecordingMap(alignment)
    bars = {}
    for row in plan.pop('events'):
        bars.setdefault(row['measure'], []).append(row)
    spans, prior, last_bar = [], None, None
    for visit, bar in enumerate(order):
        if last_bar is not None and bar != last_bar + 1:
            prior = None
        for row in bars.get(bar, []):
            start = clock.measure_starts[visit] + row['q']
            stop = start + row['length']
            if row['rest']:
                prior = None
            elif row['grace']:
                continue
            elif row['extension'] or row['tie'] and not row['text']:
                if prior == start and spans:
                    spans[-1]['end'] = clock.at(stop)
                    prior = stop
                else:
                    prior = None
            elif row['text']:
                spans.append({'time': clock.at(start), 'end': clock.at(stop), 'text': row['text'],
                              'measure': bar, 'beat': row['beat'], 'occurrence': visit + 1})
                prior = stop
            else:
                prior = None
            if len(spans) > MAX_EVENTS:
                return [], {**plan, 'status': 'unsupported'}, []
        last_bar = bar
    if not spans:
        return [], {**plan, 'status': 'unsupported'}, []
    events, ledger = [], []
    try:
        for span in spans:
            lo, hi = recording.at(span['time']), recording.at(span['end'])
            if recording.collapsed_count and span['time'] < recording.opening_end - 1e-8 and span['end'] <= recording.opening_end + 1e-8:
                ledger.append({**span, 'audioStart': lo, 'audioEnd': hi, 'disposition': 'collapsed_opening'})
                continue
            if hi <= lo:
                raise ValueError('Invalid duration')
            start, stop = max(0., lo), min(duration, hi)
            disposition = 'outside_recording' if stop <= start else 'clipped' if (start, stop) != (lo, hi) else 'exported'
            ledger.append({**span, 'audioStart': lo, 'audioEnd': hi, 'disposition': disposition})
            if stop > start:
                event = {'t': round(start, 6), 'd': round(stop - start, 6), 'w': span['text']}
                if event['d'] <= 0 or events and event['t'] < events[-1]['t'] + events[-1]['d'] - 0.0000011:
                    raise ValueError('Overlapping or collapsed syllables')
                events.append(event)
    except ValueError:
        return [], {**plan, 'status': 'unsupported'}, ledger
    return phrases(events), {**plan, 'status': 'imported' if events else 'outside_recording'}, ledger


def text_limit_evidence(text, row_index):
    """Reconstruct the prefix receipt using UTF-16 bytes, not the importer."""
    head = text[:20001].encode('utf-16-le', errors='surrogatepass')[:40000]
    prefix = head.decode('utf-16-le', errors='surrogatepass')
    reason = 'utf16_limit'
    for count, match in enumerate(re.finditer(r'\r\n|\r|\n', prefix), 1):
        if count == 500:
            prefix, reason = prefix[:match.start()], 'line_limit'
            break
    # A cut high surrogate or CR is not a complete boundary.
    units = len(prefix.encode('utf-16-le', errors='surrogatepass')) // 2
    boundary = text[:20001].encode('utf-16-le', errors='surrogatepass')[units * 2:units * 2 + 2]
    after = int.from_bytes(boundary, 'little') if boundary else None
    if prefix and after is not None and (
        0xd800 <= ord(prefix[-1]) <= 0xdbff and 0xdc00 <= after <= 0xdfff
        or prefix[-1] == '\r' and after == 10
    ):
        prefix = prefix[:-1]
    def size(value):
        return sum(len(value[i:i+4096].encode('utf-16-le', errors='surrogatepass')) // 2
                   for i in range(0, len(value), 4096))
    original, retained = size(text), size(prefix)
    if original == retained:
        return None
    return {'policy': POLICY, 'reason': reason, 'rowIndex': row_index,
            'originalUtf16Units': original, 'retainedUtf16Units': retained,
            'originalLineBreaks': sum(1 for _ in re.finditer(r'\r\n|\r|\n', text)),
            'retainedLineBreaks': sum(1 for _ in re.finditer(r'\r\n|\r|\n', prefix))}


def verify(archive, recipe, manifest, source_path, source, alignment, duration, check, read_json):
    policy = recipe.get('lyricsPolicy')
    if policy not in (LEGACY_POLICY, GRACE_POLICY, POLICY):
        check.fail('lyrics_policy', 'song_import', 'Unknown authored lyric policy.')
        return
    if recipe.get('preservationContract', 0) >= 117:
        check.equal('lyrics_policy', 'song_import', POLICY, policy)
    elif recipe.get('preservationContract', 0) >= 109:
        check.equal('lyrics_policy', 'song_import', GRACE_POLICY, policy)
    check.equal('lyrics_report_path', 'song_import', 'import/lyrics.json', recipe.get('lyricsFile'))
    if recipe.get('preservationContract', 0) < 79:
        check.fail('lyrics_contract', 'song_import', 'Lyrics require preservation contract 79.')
    proof = read_json(archive, recipe.get('lyricsFile', ''), check)
    document = json.loads(source_path.read_text(encoding='utf-8-sig'))
    wanted, plan, ledger = expected(document, source, alignment, duration, policy=policy)
    for key, value in {'version': 1, 'policy': policy, 'timeDomain': 'recording_seconds',
                       'acousticAccuracyAssessed': False,
                       'sourceSha256': hashlib.sha256(source_path.read_bytes()).hexdigest(),
                       'audioSha256': hashlib.sha256(archive.read('audio/full.ogg')).hexdigest(),
                       'alignmentSha256': digest(clock_parameters(alignment)),
                       'status': plan['status'], 'exportedEvents': len(wanted)}.items():
        check.equal('lyrics_provenance', f'import/lyrics.json/{key}', value, proof.get(key))
    for key in ('trackIndex', 'trackName', 'sourceKind', 'selection', 'candidates', 'unassignedSyllables'):
        check.equal('lyrics_selection', f'import/lyrics.json/{key}', plan.get(key), proof.get(key))
    limit = plan.get('sourceTextLimit')
    if limit is not None:
        rows = document['parts'][plan['trackIndex']]['newLyrics']
        index = 0 if len(rows) <= 5 else 5
        limit = text_limit_evidence(rows[index]['text'], index)
        check.equal('lyrics_text_limit', 'sourceTextLimit/interpretation', limit, plan.get('sourceTextLimit'))
    check.equal('lyrics_text_limit', 'import/lyrics.json/sourceTextLimit', limit, proof.get('sourceTextLimit'))
    check.equal('lyrics_status', 'song_import', plan['status'], recipe.get('lyricsStatus'))
    if plan['status'] in ('imported', 'outside_recording'):
        actual_ledger = proof.get('events', [])
        check.equal('lyrics_ledger', 'import/lyrics.json', len(ledger), len(actual_ledger))
        for i, (a, b) in enumerate(zip(ledger, actual_ledger)):
            for key in ('time', 'end', 'audioStart', 'audioEnd'):
                check.near('lyrics_timing', f'import/lyrics.json/events/{i}/{key}', float(a[key]), b.get(key))
            for key in ('text', 'measure', 'beat', 'occurrence', 'disposition'):
                check.equal('lyrics_source', f'import/lyrics.json/events/{i}/{key}', a[key], b.get(key))
        for key, disposition in [('clippedEvents', 'clipped'), ('omittedEvents', 'outside_recording')]:
            check.equal('lyrics_bounds', key, sum(r['disposition'] == disposition or key == 'omittedEvents' and r['disposition'] == 'collapsed_opening' for r in ledger), proof.get(key))
    if not wanted:
        check.equal('lyrics_absent', 'manifest/lyrics', None, manifest.get('lyrics'))
        check.equal('lyrics_absent', 'manifest/lyric_tracks', None, manifest.get('lyric_tracks'))
        return
    check.equal('lyrics_path', 'manifest/lyrics', 'lyrics.json', manifest.get('lyrics'))
    check.equal('lyrics_source', 'manifest/lyrics_source', 'authored', manifest.get('lyrics_source'))
    check.equal('lyrics_track', 'manifest/lyric_tracks',
                [{'id': 'original', 'file': 'lyrics.json', 'language': 'und', 'kind': 'original', 'lyrics_source': 'authored',
                  'stem': 'full', 'name': plan['trackName']}], manifest.get('lyric_tracks'))
    actual = read_json(archive, manifest['lyrics'], check)
    check.equal('lyrics_count', 'lyrics.json', len(wanted), len(actual))
    for i, (a, b) in enumerate(zip(wanted, actual)):
        for key in ('t', 'd'):
            check.near('lyrics_timing', f'lyrics.json/{i}/{key}', a[key], b.get(key))
        check.equal('lyrics_text', f'lyrics.json/{i}/w', a['w'], b.get('w'))
    check.equal('lyrics_hash', 'import/lyrics.json', digest(actual), proof.get('lyricsSha256'))

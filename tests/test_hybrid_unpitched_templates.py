"""Unpitched chord shapes need actual retained mute/scrape references."""
from copy import deepcopy
import json
from zipfile import ZipFile

import pytest
import yaml

from test_song_import_score import beat, measure, raw_score
from test_songsterr_hybrid_lead import build, rest


def chord(fret, *, scrape=False, duration=(1, 1)):
    item = beat(fret, duration=duration, **({'dead': True, 'pickScrape': 'down'} if scrape else {}))
    if fret == 127:
        item['notes'][0].pop('fret')  # Songsterr's missing dead-note fret maps to the sentinel.
    item['notes'].append({**deepcopy(item['notes'][0]), 'string': 1})
    return item


@pytest.mark.parametrize('fret', [26, 127])
@pytest.mark.parametrize('position', ['unused_donor', 'selected_donor', 'displaced_base', 'retained_base'])
def test_only_unreferenced_unpitched_templates_are_pruned(tmp_path, fret, position):
    doc = raw_score([measure(beat(3), marker={'text': 'Intro'}),
                     measure(beat(3), marker={'text': 'Solo (Alex)'}),
                     measure(beat(3), marker={'text': 'Verse'}), measure(beat(3))])
    doc['tracks'][0]['name'] = 'Blake | Lead Guitar'
    donor = [measure(chord(9)), measure(beat(12)), measure(rest()), measure(rest())]
    if position == 'unused_donor':
        donor[3] = measure(chord(fret, scrape=True))
    elif position == 'selected_donor':
        donor[1] = measure(beat(12, duration=(1, 4)), chord(fret, scrape=True, duration=(1, 4)),
                           beat(14, duration=(1, 2)))
    elif position == 'displaced_base':
        doc['parts'][0]['measures'][1] = measure(chord(fret, scrape=True), marker={'text': 'Solo (Alex)'})
    else:
        doc['parts'][0]['measures'][0] = measure(chord(fret, scrape=True), marker={'text': 'Intro'})
    doc['tracks'].append({**deepcopy(doc['tracks'][0]), 'id': 1, 'name': 'Alex | Rhythm Guitar'})
    doc['parts'].append({'measures': donor})
    *_, archive, report = build(tmp_path, doc, overrides={'mainTrackId': '0'})
    assert report['status'] == 'passed', report
    with ZipFile(archive) as z:
        manifest = yaml.safe_load(z.read('manifest.yaml'))
        original = {a['id']: json.loads(z.read(a['file'])) for a in manifest['arrangements'] if not a.get('derived')}
        derived = next(a for a in manifest['arrangements'] if a.get('derived'))
        hybrid = json.loads(z.read(derived['file']))
        receipt = json.loads(z.read('import/hybrid-lead.json'))
    source_id = '1' if 'donor' in position else '0'
    assert any(fret in t['frets'] for t in original[source_id]['templates']), 'Original source shapes remain intact.'
    wanted = position in {'selected_donor', 'retained_base'}
    special = {index for index, template in enumerate(hybrid['templates']) if fret in template['frets']}
    assert bool(special) is wanted
    assert special <= {item['id'] for item in hybrid['chords']}
    if wanted:
        copied = [item for item in hybrid['chords'] if item['id'] in special]
        assert copied and all(n.get('mt') and n.get('pick_scrape_marks') for item in copied for n in item['notes'])
        assert all(item in original[source_id]['chords'] or
                   any({**item, 'id': old['id']} == old for old in original[source_id]['chords']) for item in copied)
    # Donor's ordinary unused shape keeps the established template-copy policy.
    assert any(9 in template['frets'] for template in hybrid['templates'])
    assert all(not any(n['f'] == 9 for n in item['notes']) for item in hybrid['chords'])
    assert any(p['trackId'] == '1' for p in receipt['passages'])

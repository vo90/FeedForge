"""A shared solo label does not make plain palm-muted repetition a lead answer."""
from copy import deepcopy
import json
from zipfile import ZipFile

import pytest
import yaml

from test_hybrid_opportunity_oracle import event, fixture
from feedback_converter.song_import.verify_hybrid_opportunities import local_lead_audit


def repeated_case(fret=0, string=0):
    rows, requirements, selected = fixture()
    rows['b'] = {('notes', i): event(2+i*.5, 2.5+i*.5, fret, pm=True) for i in range(2)}
    for row in rows['b'].values():
        row['notes'][0]['s'] = string
    return rows, requirements, selected


@pytest.mark.parametrize('fret,string', [(0, 0), (5, 2), (17, 5)])
def test_plain_repeated_palm_mutes_are_not_compulsory_named_response(fret, string):
    rows, requirements, selected = repeated_case(fret, string)
    before = deepcopy(rows)
    result = local_lead_audit(rows, requirements, selected, set(rows), lambda q: q)
    assert result['unresolvedCount'] == 0
    assert rows == before, 'Source material must remain intact.'


@pytest.mark.parametrize('change', ['pitch', 'string', 'unmuted', 'chord', 'vb', 'bn', 'bnv',
                                   'ho', 'po', 'ln', 'sl', 'slu', 'harmonic', 'unknown'])
def test_musical_or_unknown_differences_keep_named_response_check(change):
    rows, requirements, selected = repeated_case()
    note = rows['b'][('notes', 1)]['notes'][0]
    if change == 'pitch': note['f'] = 2
    elif change == 'string': note['s'] = 1
    elif change == 'unmuted': note.pop('pm')
    elif change == 'chord': rows['b'][('notes', 1)]['notes'].append({**note, 's': 1})
    else: note[change] = 0 if change in {'sl', 'slu'} else True
    result = local_lead_audit(rows, requirements, selected, set(rows), lambda q: q)
    assert result['unresolvedCount'] == 1


def test_plain_repetition_cannot_excuse_another_missing_melodic_peer():
    rows, requirements, selected = repeated_case()
    rows['c'] = {('notes', 0): event(3, 4, 7), ('notes', 1): event(4, 5, 9)}
    requirements[0]['owners']['c'] = set(rows['c'])
    result = local_lead_audit(rows, requirements, selected, set(rows), lambda q: q)
    assert result['unresolved'][0]['candidateTrackIds'] == ['c']


@pytest.mark.parametrize('case', ['continuation', 'same_attack'])
def test_continued_or_simultaneous_events_are_not_plain_repetition(case):
    from feedback_converter.song_import.verify_hybrid_opportunities import _plain_palm_muted_repetition
    rows, _, _ = repeated_case()
    if case == 'continuation':
        rows['b'][('notes', 1)]['onset'] = 2
    else:
        rows['b'][('notes', 1)]['start'] = 2
        rows['b'][('notes', 1)]['onset'] = 2
    assert not _plain_palm_muted_repetition(rows['b'])


@pytest.mark.parametrize('kind', ['plain', 'melodic', 'vibrato', 'single_owner'])
def test_archive_checks_source_not_a_planner_claim(tmp_path, monkeypatch, kind):
    from test_song_import_score import beat, measure, raw_score
    from test_songsterr_hybrid_lead import rest, build
    from feedback_converter.song_import import hybrid_selection, hybrid_handover

    melody = lambda: measure(*[beat(f, duration=(1, 4), vibrato=True) for f in (12, 14, 17, 15)])
    first = melody()
    label = 'Solo (Alex & Blake)' if kind != 'single_owner' else 'Solo (Alex)'
    first['marker'] = {'text': label}
    doc = raw_score([first, measure(rest()), melody()])
    doc['tracks'][0]['name'] = 'Blake | Lead Guitar'
    doc['tracks'].append({**deepcopy(doc['tracks'][0]), 'id': 1, 'name': 'Alex | Lead Guitar'})
    response = [{**beat(0, duration=(1, 8)), 'palmMute': True},
                {**beat(2 if kind == 'melodic' else 0, duration=(1, 8),
                        **({'vibrato': True} if kind == 'vibrato' else {})), 'palmMute': True}, rest((3, 4))]
    doc['parts'].append({'measures': [melody(), measure(*response), melody()]})
    parent = {'id': 'owner-solo', 'trackId': '0', 'start': 0, 'end': 12,
              'ownedStart': 0, 'ownedEnd': 12, 'priority': 'solo', 'evidence': 'named_soloist',
              'confidence': 'high', 'eligible': True, 'score': 10,
              'sectionName': label, 'labelledSolo': True}
    # Keep a self-consistent chart that omits the peer, regardless of producer
    # diagnostics; the independent source audit must decide whether it is valid.
    monkeypatch.setattr(hybrid_selection, 'regional_candidates', lambda *a, **kw: [deepcopy(parent)])
    monkeypatch.setattr(hybrid_handover, 'refine_handovers',
                        lambda selected, *a: (selected, [], [], {'budgetLimited': False}))
    *_, archive, report = build(tmp_path, doc, overrides={'mainTrackId': '0'})
    if kind == 'plain':
        assert report['status'] == 'passed', report['errors']
        with ZipFile(archive) as z:
            manifest = yaml.safe_load(z.read('manifest.yaml'))
            original = json.loads(z.read(manifest['arrangements'][1]['file']))
            assert len([n for n in original['notes'] if n.get('pm')]) == 2
            hybrid = json.loads(z.read(manifest['arrangements'][-1]['file']))
            assert not any(n.get('pm') for n in hybrid['notes'])
    else:
        assert report['status'] == 'failed'
        codes = {e['code'] for e in report['errors']}
        assert ('hybrid_local_lead_coverage' if kind != 'single_owner' else 'hybrid_primary_coverage') in codes

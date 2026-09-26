"""Source-semantic cases; no song identity or measure-specific exceptions."""
from copy import deepcopy
import json
from pathlib import Path
from zipfile import ZipFile

import pytest
import yaml

from test_song_import_score import beat, measure, raw_score, import_json
from feedback_converter.song_import.songsterr import parse
from feedback_converter.song_import.timeline import render
from feedback_converter.song_import.verify_source import songsterr
from feedback_converter.song_import.verify_timeline import expected
from feedback_converter.song_import.verification import Check, _notes, _chords, _flatten, verify_import


def muted(fret, duration=(1, 2), **fields):
    b = beat(fret=0, duration=duration, dead=True, **fields)
    if fret is None:
        b['notes'][0].pop('fret')
    else:
        b['notes'][0]['fret'] = fret
    return b


def source(first=None, second=0):
    return raw_score([measure(muted(first), muted(second, tie=True))])


def check(document):
    original = deepcopy(document)
    p = render(parse(document))
    v = expected(songsterr(document), {'offset': 0, 'scale': 1})
    assert p.get('mutedTieIdentityEvidence', []) == v['muted_tie_identities']
    c = Check()
    for track, part in zip(p['tracks'], v['parts']):
        _notes(part['notes'], _flatten(track), c, part['source'], p['duration'])
        _chords(part['notes'], track, c, part['source'])
    assert not c.errors, c.errors
    assert document == original
    return p


@pytest.mark.parametrize('first,second', [(None, 0), (0, None)])
@pytest.mark.parametrize('program,tuning', [(29, [64,59,55,50,45,40]), (33, [43,38,33,28])])
@pytest.mark.parametrize('prefix', [0, 3])
def test_general_identity_and_timing(first, second, program, tuning, prefix):
    doc = source(first, second)
    doc['songId'] = 9876543
    doc['title'] = 'Unrelated synthetic example'
    doc['tracks'][0].update(name='Arbitrary arrangement', instrumentId=program, tuning=tuning)
    doc['parts'][0]['measures'][:0] = [measure(beat(fret=7)) for _ in range(prefix)]
    p = check(doc)
    notes = p['tracks'][0]['notes']
    assert len(notes) == prefix + 1
    n = notes[-1]
    assert n['mt'] is True and n['f'] == (127 if first is None else first)
    assert n['sus'] == 2 and n['t'] == prefix * 2 and len(n['source_ids']) == 2
    row = p['mutedTieIdentityEvidence'][0]
    assert row['authored'] == {'dead': True, 'fret': second}
    assert row['used'] == {'dead': True, 'fret': first}
    assert row['originSourceId'] == n['source_ids'][0]
    # Existing staff-notation limitation remains explicit; no MIDI pitch is
    # fabricated for the missing-fret source, and the playable tab keeps its X.
    assert 'notation' not in p['tracks'][0]


def test_chains_cross_bars_repeats_and_chords():
    bars = [measure(muted(None, (1,1)), repeatStart=True),
            measure(muted(0, (1,2), tie=True), muted(None, (1,2), tie=True), repeat=2)]
    for m in bars:
        for b in m['voices'][0]['beats']:
            b['notes'].append({'fret': 8, 'string': 1, **({'tie': True} if b['notes'][0].get('tie') else {})})
    p = check(raw_score(bars))
    assert [r['occurrence'] for r in p['mutedTieIdentityEvidence']] == [2, 4]
    assert len(p['tracks'][0]['chords']) == 2
    for chord in p['tracks'][0]['chords']:
        assert len(chord['notes']) == 2 and all(n['sus'] == 4 for n in chord['notes'])
        assert next(n for n in chord['notes'] if n.get('mt'))['f'] == 127


def test_separate_voice_projection_retains_interpretation_for_correct_arrangement():
    doc = source()
    doc['parts'][0]['measures'][0]['voices'].append({'beats': [beat(fret=7)]})
    p = check(doc)
    assert len(p['tracks']) == 2
    assert p['mutedTieIdentityEvidence'][0]['trackId'] == p['tracks'][0]['id']
    assert p['tracks'][1]['notes'][0]['f'] == 7


@pytest.mark.parametrize('first,second,tie', [(None,None,True), (0,0,True), (7,7,True), (None,0,False)])
def test_existing_dead_events_unchanged(first, second, tie):
    doc = source(first, second)
    doc['parts'][0]['measures'][0]['voices'][0]['beats'][1]['notes'][0]['tie'] = tie
    p = check(doc)
    assert not p.get('mutedTieIdentityEvidence')
    assert len(p['tracks'][0]['notes']) == (1 if tie else 2)


@pytest.mark.parametrize('fault', ['pitched', 'nonzero', 'nonzero_origin', 'rest', 'gap', 'missing',
                                  'voice', 'string', 'jump', 'slide', 'bend', 'harmonic', 'vibrato',
                                  'scrape', 'hopo', 'late_gesture'])
def test_ambiguous_or_invalid_ties_still_fail(fault):
    doc = source()
    beats = doc['parts'][0]['measures'][0]['voices'][0]['beats']
    n = beats[1]['notes'][0]
    if fault == 'pitched': n.pop('dead')
    if fault == 'nonzero': n['fret'] = 3
    if fault == 'nonzero_origin': beats[0]['notes'][0]['fret'] = 3
    if fault in ('rest', 'gap'):
        beats[0]['duration'] = beats[1]['duration'] = [1,4]
        beats.insert(1, {'duration':[1,4], 'notes':[{'rest':True}]} if fault == 'rest' else beat(7, string=1, duration=(1,4)))
    if fault == 'missing': beats.pop(0)
    if fault == 'voice':
        doc['parts'][0]['measures'][0]['voices'].append({'beats': [beats.pop(1)]})
    if fault == 'string': n['string'] = 1
    if fault == 'jump':
        beats[0]['notes'][0]['tie'] = True
        doc['parts'][0]['measures'][0]['repeat'] = 2
    if fault == 'slide': n['slide'] = 'upwards'
    if fault == 'bend': n['bend'] = {'points':[{'position':0,'tone':0},{'position':60,'tone':100}]}
    if fault == 'harmonic': n['harmonic'] = 'pinch'
    if fault == 'vibrato': n['vibrato'] = True
    if fault == 'scrape': n['pickScrape'] = 'up'
    if fault == 'hopo': n['hp'] = True
    if fault == 'late_gesture':
        # A zero-origin chain cannot gain a pitch gesture after a missing-fret
        # continuation merely because the last fret equals the origin again.
        beats[:] = [muted(0,(1,4)), muted(None,(1,4),tie=True), muted(0,(1,2),tie=True,slide='upwards')]
    with pytest.raises(ValueError): render(parse(doc))
    with pytest.raises(ValueError): expected(songsterr(doc), {'offset':0, 'scale':1})


def test_overlap_is_not_repaired():
    doc = source()
    score = parse(doc)
    score.tracks[0].bars[0][1].position -= 1
    oracle = songsterr(doc)
    oracle.parts[0].bars[0][1].q -= 1
    with pytest.raises(ValueError): render(score)
    with pytest.raises(ValueError): expected(oracle, {'offset':0,'scale':1})


@pytest.mark.parametrize('reverse', [False, True])
def test_package_provenance_and_tamper_rejection(tmp_path, reverse):
    from test_song_import_builder import inputs
    from feedback_converter.song_import.builder import build_feedpak
    from feedback_converter.feedpak_validator import validate_feedpak
    _, audio, _, job = inputs(tmp_path)
    doc = source(0,None) if reverse else source()
    p = import_json(tmp_path, doc)
    path = tmp_path/'score.json'
    alignment = {'status':'validated','offset':.25,'scale':1.25}
    built = build_feedpak(p, audio, alignment, job, output_dir=tmp_path/'out', source_path=path,
                         compatibility=p['compatibilityReport'], recipe={'preservationContract':30})
    archive = Path(built['stagingPath'])
    assert validate_feedpak(archive).ok
    result = verify_import(path, archive, alignment)
    assert result['status'] == 'passed', result
    assert 'muted_tie_identity' in result['scope']
    with ZipFile(archive) as z: original = {n:z.read(n) for n in z.namelist()}
    manifest = yaml.safe_load(original['manifest.yaml'])
    assert original[manifest['song_import']['sourceFile']] == path.read_bytes()
    for fault in ('missing', 'reference', 'source', 'origin', 'target', 'time', 'boolean',
                  'extra', 'count', 'contract', 'attack', 'fret', 'mute', 'duration'):
        files = dict(original)
        evidence = json.loads(files['import/muted-tie-identity.json'])
        if fault == 'source': evidence['sourceSha256'] = '0'*64
        if fault == 'origin': evidence['continuations'][0]['originSourceId'] = 'invented'
        if fault == 'target': evidence['continuations'][0]['used']['fret'] = 3
        if fault == 'time': evidence['continuations'][0]['start'] += .1
        if fault == 'boolean': evidence['continuations'][0]['authored']['dead'] = 1
        if fault == 'extra': evidence['unexpected'] = True
        if fault == 'count': evidence['continuations'] = []
        files['import/muted-tie-identity.json'] = json.dumps(evidence).encode()
        if fault == 'missing': files.pop('import/muted-tie-identity.json')
        m = deepcopy(manifest)
        if fault == 'reference': m['song_import'].pop('mutedTieIdentityFile')
        if fault == 'contract': m['song_import']['preservationContract'] = 29
        files['manifest.yaml'] = yaml.safe_dump(m).encode()
        chart_path = manifest['arrangements'][0]['file']
        chart = json.loads(files[chart_path])
        if fault == 'attack': chart['notes'].append(deepcopy(chart['notes'][0]))
        if fault == 'fret': chart['notes'][0]['f'] = 3
        if fault == 'mute': chart['notes'][0].pop('mt')
        if fault == 'duration': chart['notes'][0]['sus'] /= 2
        files[chart_path] = json.dumps(chart).encode()
        changed = tmp_path/(fault+'.feedpak')
        with ZipFile(changed,'w') as z:
            for name,data in files.items(): z.writestr(name,data)
        result = verify_import(path, changed, alignment)
        assert result['status'] == 'failed', (fault,result)


def test_old_contract_cannot_publish_identity_mapping(tmp_path):
    from test_song_import_builder import inputs
    from feedback_converter.song_import.builder import build_feedpak
    from feedback_converter.song_import.audio import ImportFailure
    _, audio, _, job = inputs(tmp_path)
    p = import_json(tmp_path, source())
    with pytest.raises(ImportFailure, match='contract 30'):
        build_feedpak(p,audio,{'status':'validated','offset':0,'scale':1},job,
                      output_dir=tmp_path/'out',source_path=tmp_path/'score.json',
                      compatibility=p['compatibilityReport'],recipe={'preservationContract':29})

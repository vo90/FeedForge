"""Partial charts must retain exact source evidence and all supported events."""
from copy import deepcopy
import json
from pathlib import Path
from zipfile import ZipFile

import pytest
import yaml

from test_song_import_score import beat, measure, raw_score
from test_song_import_builder import inputs
from feedback_converter.song_import import load_performance
from feedback_converter.song_import.builder import build_feedpak
from feedback_converter.song_import.high_frets import project
from feedback_converter.song_import.verification import verify_import, Check, _notes, _chords, _flatten
from feedback_converter.song_import.verify_source import songsterr
from feedback_converter.song_import.verify_timeline import expected
from feedback_converter.song_import.verify_high_frets import reconstruct
from test_songsterr_recording_end import recording


def document():
    chord = beat(7, duration=(1, 4))
    chord['notes'] += [{'fret': 25, 'string': 1}, {'fret': 8, 'string': 2}]
    return raw_score([measure(beat(24, duration=(1,4), slide='shift'), beat(26, duration=(1,4)),
                              chord, beat(5, duration=(1,4)))])


def prepare(tmp_path, doc=None):
    source = tmp_path / 'source.json'
    source.write_text(json.dumps(doc or document()), encoding='utf-8')
    perf = load_performance(source)
    return source, perf


def check_projection(doc, tmp_path):
    _, perf = prepare(tmp_path, doc)
    before = deepcopy(perf)
    playable, receipt = project(perf)
    assert perf == before
    raw = expected(songsterr(doc), {'offset':0,'scale':1})
    wanted = deepcopy(raw)
    assert reconstruct(raw, wanted) == receipt
    for track, part in zip(playable['tracks'], wanted['parts']):
        checks = Check()
        _notes(part['notes'], _flatten(track), checks, part['source'], perf['duration'])
        _chords(part['notes'], track, checks, part['source'])
        assert not checks.errors
    return playable, receipt


def test_mixed_chords_and_outgoing_high_slide(tmp_path):
    perf, receipt = check_projection(document(), tmp_path)
    assert [n['fret'] for n in receipt['notes']] == [24, 26, 25]
    assert receipt['notes'][0]['reason'] == 'slide_target_above_24'
    track = perf['tracks'][0]
    assert len(track['chords'][0]['notes']) == 2
    assert all(f <= 24 for t in track['templates'] for f in t['frets'])
    assert 'notation' not in track


@pytest.mark.parametrize('frets', [[7,25], [25,26], [24,25,26]])
def test_single_member_or_empty_chord(tmp_path, frets):
    chord = beat(frets[0], duration=(1,4))
    chord['notes'] += [{'fret':f,'string':i} for i,f in enumerate(frets[1:],1)]
    perf, _ = check_projection(raw_score([measure(chord, beat(3, duration=(1,4)))]), tmp_path)
    assert not perf['tracks'][0]['chords']
    assert len(perf['tracks'][0]['notes']) == 1 + int(frets[0] <= 24)


def test_tie_repeat_and_outgoing_link(tmp_path):
    doc = raw_score([measure(beat(24, duration=(1,4),hp=True), beat(26,duration=(1,4)),
                             beat(26,duration=(1,4),tie=True),beat(7,duration=(1,4)),repeatStart=True,repeat=2)])
    perf, receipt = check_projection(doc, tmp_path)
    assert len(receipt['notes']) == 2 and len(receipt['links']) == 2
    assert all(n['scoreDuration'] == 1 for n in receipt['notes'])
    assert len(perf['tracks'][0]['notes']) == 4
    assert all(not n.get('ln') for n in perf['tracks'][0]['notes'])


def test_incoming_slide_from_high_keeps_supported_destination(tmp_path):
    perf, receipt = check_projection(raw_score([measure(beat(26,duration=(1,4),slide='legato'),
                                        beat(24,duration=(1,4)),beat(7,duration=(1,4)))]), tmp_path)
    assert len(receipt['notes']) == 1
    assert [n['f'] for n in perf['tracks'][0]['notes']] == [24,7]


def test_unaffected_projection_is_exact(tmp_path):
    doc = raw_score([measure(beat(24,duration=(1,4)), beat(7,duration=(1,4)))])
    doc['parts'][0]['measures'][0]['voices'][0]['beats'][1]['notes'].append({'fret':8,'string':1})
    _, perf = prepare(tmp_path, doc)
    actual, receipt = project(perf)
    assert actual == perf and not receipt['notes']


@pytest.mark.parametrize('fault', [None, 'missing_receipt', 'receipt_pitch', 'receipt_hash', 'extra_omission',
                                 'source', 'missing_supported', 'pitch', 'time', 'reintroduced', 'notation', 'summary'])
def test_archive_and_corruption(tmp_path, fault):
    _, audio, _, job = inputs(tmp_path)
    source, perf = prepare(tmp_path)
    alignment = {'status':'validated','mapping':'piecewise-linear','anchors':[
        {'score':0,'audio':.25},{'score':.5,'audio':1.25},{'score':2,'audio':5}],
        'tempos':[{'time':.25,'bpm':60},{'time':1.25,'bpm':48}]}
    built = build_feedpak(perf,audio,alignment,job,output_dir=tmp_path/'out',source_path=source,
                          compatibility=perf['compatibilityReport'],recipe={'preservationContract':24})
    original = Path(built['stagingPath'])
    report = verify_import(source,original,alignment)
    assert report['status']=='passed',report
    assert report['omissions']['omittedNotes']==3
    with ZipFile(original) as z: files = {n:z.read(n) for n in z.namelist()}
    assert files['import/source.json']==source.read_bytes()
    manifest = yaml.safe_load(files['manifest.yaml'])
    entry = manifest['arrangements'][0]; filename = entry['file']
    chart = json.loads(files[filename]); receipt = json.loads(files['import/high-fret-omissions.json'])
    if fault=='missing_receipt': manifest['song_import'].pop('highFretOmissionsFile')
    if fault=='receipt_pitch': receipt['notes'][0]['fret']=23
    if fault=='receipt_hash': receipt['sourceSha256']='0'*64
    if fault=='extra_omission': receipt['notes'].append(deepcopy(receipt['notes'][0]))
    if fault=='source': files['import/source.json']=b'{}'
    if fault=='missing_supported': chart['notes'].pop()
    if fault=='pitch': chart['notes'][0]['f']+=1
    if fault=='time': chart['notes'][0]['t']+=.01
    if fault=='reintroduced': chart['notes'].append({'t':.25,'s':5,'f':24,'sus':1,'sl':26})
    if fault=='notation': entry['notation']='import/source.json'
    if fault=='summary': manifest['song_import']['omissions']['omittedNotes']=0
    files[filename]=json.dumps(chart).encode()
    files['import/high-fret-omissions.json']=json.dumps(receipt).encode()
    files['manifest.yaml']=yaml.safe_dump(manifest).encode()
    mutated=tmp_path/'mutated.feedpak'
    with ZipFile(mutated,'w') as z:
        for name,data in files.items(): z.writestr(name,data)
    checked=verify_import(source,mutated,alignment)
    assert checked['status']==('passed' if fault is None else 'failed'),checked


def test_all_high_chart_does_not_publish_empty_package(tmp_path):
    from feedback_converter.song_import.audio import ImportFailure
    _, audio, alignment, job = inputs(tmp_path)
    source, perf = prepare(tmp_path,raw_score([measure(beat(26))]))
    with pytest.raises(ImportFailure,match='no supported playable'):
        build_feedpak(perf,audio,alignment,job,output_dir=tmp_path/'out',source_path=source,
                      compatibility=perf['compatibilityReport'],recipe={'preservationContract':24})
    assert not (job/'result.feedpak').exists()


@pytest.mark.parametrize('fret', [25, 48])
def test_range_boundaries_are_omitted(tmp_path, fret):
    _, receipt = check_projection(raw_score([measure(beat(fret,duration=(1,4)),beat(24,duration=(1,4)))]),tmp_path)
    assert len(receipt['notes'])==1 and receipt['notes'][0]['fret']==fret


@pytest.mark.parametrize('note', [{'fret':49}, {'fret':-1}, {'fret':25.5}, {'fret':True},
                                {'fret':26, 'unrecognizedTechnique':True}])
def test_invalid_or_unknown_source_still_fails(tmp_path,note):
    from feedback_converter.song_import.model import ScoreImportError
    doc=raw_score([measure(beat(7),beat(26))])
    doc['parts'][0]['measures'][0]['voices'][0]['beats'][1]['notes'][0].update(note)
    with pytest.raises(ScoreImportError):prepare(tmp_path,doc)


def test_visual_scrape_and_unpitched_mute_are_not_high_frets(tmp_path):
    doc=raw_score([measure(beat(30,duration=(1,4),dead=True,pickScrape='up'),
                           beat(None,duration=(1,4),dead=True),beat(7,duration=(1,4)))])
    _, perf=prepare(tmp_path,doc)
    result,receipt=project(perf)
    assert result==perf and not receipt['notes']


def test_fully_omitted_arrangement_with_other_playable_track_is_reported(tmp_path):
    _,audio,alignment,job=inputs(tmp_path)
    doc=raw_score([measure(beat(26))])
    doc['tracks'].append({**doc['tracks'][0], 'id':1, 'name':'Rhythm'})
    doc['parts'].append(deepcopy(doc['parts'][0]))
    doc['parts'][1]['measures'][0]['voices'][0]['beats'][0]['notes'][0]['fret']=7
    source,perf=prepare(tmp_path,doc)
    built=build_feedpak(perf,audio,alignment,job,output_dir=tmp_path/'out',source_path=source,
                        compatibility=perf['compatibilityReport'],recipe={'preservationContract':24})
    checked=verify_import(source,Path(built['stagingPath']),alignment)
    assert checked['status']=='passed',checked
    assert checked['omissions']['excludedTracks']==['0']
    assert checked['counts']['archivedNotes']==1


def test_high_fret_at_recording_end_keeps_original_audio_sync_evidence(recording,tmp_path,monkeypatch):
    from feedback_converter.song_import.ending_cutoff import authorize
    from feedback_converter.song_import.synchronization import align_from_songsterr
    from feedback_converter.song_import import recording_sync
    from test_songsterr_recording_end import META
    _,old_source,_,audio,sync,_=recording
    doc=json.loads(old_source.read_text(encoding='utf-8'))
    doc['parts'][0]['measures'][-1]['voices'][0]['beats'][-1]['notes'][0]['fret']=26
    source,perf=prepare(tmp_path,doc)
    alignment=align_from_songsterr(perf,audio,sync,META,allow_ending_candidate=True)
    authorized=authorize(perf,audio,alignment)
    job=tmp_path/'job';job.mkdir()
    built=build_feedpak(perf,audio,authorized,job,output_dir=tmp_path/'out',source_path=source,
                        compatibility=perf['compatibilityReport'],recipe={'preservationContract':24,
                        'audioSource':audio['source'],'alignment':{'provenance':authorized['provenance']}})
    assessed=[];original=recording_sync.assess
    def capture(tracks,*args,**kwargs):
        assessed.append(deepcopy(tracks))
        return original(tracks,*args,**kwargs)
    monkeypatch.setattr(recording_sync,'assess',capture)
    checked=verify_import(source,Path(built['stagingPath']),authorized,META)
    assert checked['status']=='passed',checked
    assert checked['omissions']['omittedNotes']==1
    assert checked['adjustments']['omittedEndingNotes']==2
    assert len(assessed)==1 and len(assessed[0][0]['events'])==208
    assert assessed[0][0]['events'][-1]['midi']==66

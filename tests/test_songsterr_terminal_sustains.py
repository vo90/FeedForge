from copy import deepcopy
import json
from pathlib import Path
import zipfile

import numpy as np
import pytest
import soundfile as sf
import yaml

from feedback_converter.song_import.audio import ImportFailure, prepare_audio
from feedback_converter.song_import.builder import _retime_note, build_feedpak
from feedback_converter.song_import.score import load_performance
from feedback_converter.song_import.synchronization import align_from_songsterr
from feedback_converter.song_import.terminal_sustains import policy_for, trim_held_note
from feedback_converter.song_import.verification import verify_import

META = {'songId': '12', 'revisionId': '34', 'approval': 'approved'}
VIDEO = 'abcdefghijk'
SYNC = {'version': 1, 'source': 'songsterr-video-points', **META, 'videoId': VIDEO,
        'status': 'done', 'feature': None, 'points': [0, 2, 4]}


def fixture(tmp_path, *, chord=False, tied=False, held_bend=False):
    notes = [{'string': 0, 'fret': 5, 'vibrato': True}]
    if chord:
        notes.append({'string': 1, 'fret': 7, 'ghost': True})
    raw = {'format': 'songsterr', 'songId': 12, 'revisionId': 34, 'title': 'Tail', 'artist': 'Artist',
           'tracks': [{'id': 0, 'name': 'Lead', 'instrumentId': 29, 'tuning': [64,59,55,50,45,40]}],
           'parts': [{'measures': [{'signature':[4,4], 'voices':[{'beats':[{'duration':[1,1], 'notes':deepcopy(notes)}]}]}
                                   for _ in range(2)],
                      'automations': {'tempo':[{'measure':0,'position':[0,1],'bpm':120,'type':4}]}}]}
    if tied:
        for n in raw['parts'][0]['measures'][1]['voices'][0]['beats'][0]['notes']:
            n['tie'] = True
    if held_bend:
        raw['parts'][0]['measures'][1]['voices'][0]['beats'][0]['notes'][0]['bend'] = {
            'points': [{'position':0,'tone':0}, {'position':15,'tone':100}, {'position':60,'tone':100}]}
    path = tmp_path/'score.json'; path.write_text(json.dumps(raw),encoding='utf-8')
    return path, load_performance(path, META)


def align(performance, duration=3.5):
    return align_from_songsterr(performance, {'duration':duration,'source':{'kind':'youtube','videoId':VIDEO}}, SYNC, META)


@pytest.mark.parametrize('chord', [False, True])
@pytest.mark.parametrize('tied', [False, True])
def test_plain_sustains_and_tied_chord_members_are_trimmed_with_an_independent_ledger(tmp_path, chord, tied):
    source, performance, alignment, archive = package(tmp_path, chord=chord, tied=tied)
    result = verify_import(source,archive,alignment,META)
    assert result['status']=='passed',result
    assert result['adjustments']['terminalSustains']==(2 if chord else 1)
    assert result['adjustments']['maxShorteningSeconds']==.5
    with zipfile.ZipFile(archive) as z:
        manifest=yaml.safe_load(z.read('manifest.yaml'))
        assert z.read(manifest['song_import']['sourceFile'])==source.read_bytes()
        details=json.loads(z.read(manifest['song_import']['adjustmentsFile']))
        assert details['policy']=='trim-final-sustain-v1'
        assert all(n['originalDuration']-n['exportedDuration']==.5 for n in details['notes'])
        chart=json.loads(z.read(manifest['arrangements'][0]['file']))
        notes=chart['notes']+[{'t':c['t'],**n} for c in chart['chords'] for n in c['notes']]
        assert max(n['t']+n['sus'] for n in notes)==3.5
        assert len(notes)==(1 if tied else 2)*(2 if chord else 1)
        assert any(n.get('vb') for n in notes)
        if chord:assert any(n.get('ghost') for n in notes)


def package(tmp_path, **kw):
    source,performance=fixture(tmp_path,**kw)
    wav=tmp_path/'recording.wav';sf.write(wav,.1*np.sin(np.arange(77175)*.15),22050)
    media=tmp_path/'media';media.mkdir()
    audio=prepare_audio({'kind':'file','path':str(wav)},media)
    audio['source'].update(kind='youtube',videoId=VIDEO)
    alignment=align_from_songsterr(performance,audio,SYNC,META)
    job=tmp_path/'job';job.mkdir()
    built=build_feedpak(performance,audio,alignment,job,output_dir=tmp_path/'out',source_path=source,
                       compatibility=performance['compatibilityReport'],recipe={'preservationContract':10})
    return source,performance,alignment,Path(built['stagingPath'])


@pytest.mark.parametrize('start', [3.5, 3.6])
def test_a_new_attack_at_or_after_the_audio_end_still_rejects_the_whole_song(tmp_path,start):
    _,performance=fixture(tmp_path)
    performance['tracks'].append({**deepcopy(performance['tracks'][0]),'id':'extra',
                                  'notes':[{'t':start,'sus':.1,'s':0,'f':3}], 'chords':[]})
    with pytest.raises(ImportFailure) as exc:align(performance)
    assert exc.value.diagnostics['sourceSyncReason']=='note_outside_recording'


@pytest.mark.parametrize('effect', [
    {'sl':0}, {'slu':5}, {'slide_out':'down'}, {'bn':1},
    {'bn':1,'bnv':[{'t':0,'v':0},{'t':1.8,'v':1}]},
    {'slide_out_marks':[{'direction':'down','start':1,'end':2}]},
    {'slide_in_marks':[{'direction':'up','time':1.8}]},
])
def test_pitch_gestures_cannot_be_removed_or_accelerated_to_make_a_trim_work(tmp_path,effect):
    _,performance=fixture(tmp_path)
    performance['tracks'][0]['notes'][-1].update(effect)
    with pytest.raises(ImportFailure) as exc:align(performance)
    assert exc.value.diagnostics['sourceSyncReason']=='terminal_technique_outside_recording'


def test_a_finished_bend_with_a_held_tail_keeps_the_exact_bend(tmp_path):
    _,performance=fixture(tmp_path)
    note=performance['tracks'][0]['notes'][-1]
    note.update(bn=2,bnv=[{'t':0,'v':0},{'t':.5,'v':2}])
    alignment=align(performance)
    before=deepcopy(note);actual=_retime_note(note,alignment,3.5)
    assert actual['sus']==1.5 and actual['bnv']==before['bnv'] and actual['bn']==2
    assert note==before


def test_constant_bend_tail_endpoint_can_follow_the_shortened_sustain():
    note={'t':1.,'sus':3.,'s':0,'f':5,'bn':2,
          'bnv':[{'t':0,'v':0},{'t':.5,'v':2},{'t':3,'v':2}]}
    before=deepcopy(note)
    actual,_=trim_held_note(note,3.5)
    assert actual['sus']==2.5
    assert actual['bnv']==[{'t':0,'v':0},{'t':.5,'v':2},{'t':2.5,'v':2}]
    assert note==before


def test_packaged_held_bend_tail_is_independently_verified(tmp_path):
    source,_,alignment,archive=package(tmp_path,held_bend=True)
    report=verify_import(source,archive,alignment,META)
    assert report['status']=='passed',report
    with zipfile.ZipFile(archive) as z:
        manifest=yaml.safe_load(z.read('manifest.yaml'))
        note=json.loads(z.read(manifest['arrangements'][0]['file']))['notes'][-1]
        assert note['sus']==1.5
        assert note['bnv']==[{'t':0,'v':0},{'t':.5,'v':2},{'t':1.5,'v':2}]
    def change(m,c,d):
        c['notes'][-1]['bnv'][1]['t']+=.1
    assert verify_import(source,rewrite(archive,change),alignment,META)['status']=='failed'


def test_only_the_validated_recording_duration_and_source_mapping_allow_trimming(tmp_path):
    _,performance=fixture(tmp_path);note=performance['tracks'][0]['notes'][-1]
    original=align(performance,4)
    with pytest.raises(ImportFailure):_retime_note(note,original,3.5)
    approved=align(performance)
    with pytest.raises(ImportFailure):_retime_note(note,approved,3.4)
    with pytest.raises(ImportFailure):_retime_note(note,{'offset':0,'scale':1,'terminalSustains':policy_for(3.5)},3.5)


def rewrite(archive,change):
    with zipfile.ZipFile(archive) as z:files={n:z.read(n) for n in z.namelist()}
    manifest=yaml.safe_load(files['manifest.yaml'])
    chart_path=manifest['arrangements'][0]['file'];chart=json.loads(files[chart_path])
    detail_path=manifest['song_import']['adjustmentsFile'];detail=json.loads(files[detail_path])
    change(manifest,chart,detail)
    files['manifest.yaml']=yaml.safe_dump(manifest).encode();files[chart_path]=json.dumps(chart).encode();files[detail_path]=json.dumps(detail).encode()
    target=archive.with_name('tampered.feedpak')
    with zipfile.ZipFile(target,'w') as z:
        for name,data in files.items():z.writestr(name,data)
    return target


@pytest.mark.parametrize('what', ['shorten_more','shift_attack','wrong_fret','false_ledger','missing_ledger','unapproved','false_audio_duration'])
def test_independent_verification_rejects_changes_outside_the_exact_policy(tmp_path,what):
    source,_,alignment,archive=package(tmp_path)
    def change(m,c,d):
        if what=='shorten_more':c['notes'][-1]['sus']-=.1
        elif what=='shift_attack':c['notes'][-1]['t']-=.1
        elif what=='wrong_fret':c['notes'][-1]['f']+=1
        elif what=='false_ledger':d['notes'][0]['originalDuration']+=.1
        elif what=='missing_ledger':m['song_import']['adjustmentsFile']='missing.json'
        elif what=='unapproved':m['song_import'].pop('terminalSustains')
        elif what=='false_audio_duration':
            # Forge every claimed boundary consistently, but leave real audio.
            m['duration']=3.25
            m['song_import']['terminalSustains']['audioDuration']=3.25
            alignment['terminalSustains']['audioDuration']=3.25
            c['notes'][-1]['sus']=1.25
            d['audioDuration']=3.25
            d['notes'][0].update(exportedDuration=1.25,trimmedSeconds=.75)
    result=verify_import(source,rewrite(archive,change),alignment,META)
    assert result['status']=='failed',result


def test_serialized_cutoff_does_not_extend_the_audio_or_create_a_zero_length_attack():
    result,detail=trim_held_note({'t':1.,'sus':3.,'s':0,'f':5},3.12345678)
    assert result['t']+result['sus']<=3.12345678
    assert detail['trimmedSeconds']==pytest.approx(.876544)
    with pytest.raises(ImportFailure):trim_held_note({'t':3.123456,'sus':1,'s':0,'f':5},3.12345678)

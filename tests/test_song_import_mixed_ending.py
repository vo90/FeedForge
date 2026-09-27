"""Combined endings must preserve attacks and shorten only declared long tails."""
from copy import deepcopy
import json
from zipfile import ZipFile

import pytest
import yaml

from feedback_converter.song_import import ending_padding as ep
from feedback_converter.song_import.builder import build_feedpak
from feedback_converter.song_import.preparation import finalize
from feedback_converter.song_import.score import load_performance
from feedback_converter.song_import.synchronization import align_from_songsterr
from feedback_converter.song_import.verification import verify_import
from feedback_converter.song_import.audio import ImportFailure
from feedback_converter.song_import.worker import _failure_message
from test_song_import_ending_padding import source_audio
from test_songsterr_recording_end import META


@pytest.mark.parametrize('dead', [False, True])
def test_mixed_ending_real_package(source_audio, tmp_path, dead):
    _, original, _, audio, sync = source_audio
    raw = json.loads(original.read_text())
    track = deepcopy(raw['tracks'][0]); track.update(id=1, name='Held tail')
    raw['tracks'].append(track)
    part = deepcopy(raw['parts'][0])
    for bar in part['measures']:
        bar['voices'][0]['beats'] = [{'duration':[1,1], 'rest':True, 'notes':[]}]
    note = {'string':5, 'fret':5, **({'dead':True} if dead else {})}
    part['measures'][25]['voices'][0]['beats'] = [
        {'duration':[7,8], 'rest':True, 'notes':[]},
        {'duration':[1,8], 'notes':[note]}]
    part['measures'][26]['voices'][0]['beats'] = [{'duration':[1,1], 'notes':[{**note,'tie':True}]}]
    raw['parts'].append(part)
    source = tmp_path/'source.json'; source.write_text(json.dumps(raw))
    perf = load_performance(source, META)
    alignment = align_from_songsterr(perf, audio, sync, META, allow_padding_candidate=True)
    assert alignment['paddingCandidate']['trimLongHeldTails'] is True
    assert alignment['paddingCandidate']['sourceLastNoteEnd'] == 54
    alignment = ep.authorize(perf, audio, alignment)
    job=tmp_path/'job'; job.mkdir()
    prepared, alignment = finalize(perf, audio, alignment, job)
    assert alignment['terminalSustains']['audioDuration'] == pytest.approx(53.9)
    assert prepared['duration'] == pytest.approx(54)
    recipe = {'preservationContract':37, 'audioSource':audio['source'], 'preparation':alignment['preparation'],
              'alignment':{'provenance':alignment['provenance'], 'endingPadding':alignment['endingPadding']}}
    built = build_feedpak(perf, prepared, alignment, job, output_dir=tmp_path/'out', source_path=source,
                         recipe=recipe, compatibility=perf['compatibilityReport'])
    path=built['stagingPath']
    result=verify_import(source, path, alignment, META)
    assert result['status']=='passed', result
    assert result['adjustments']['terminalSustains']==1
    with ZipFile(path) as z:
        manifest=yaml.safe_load(z.read('manifest.yaml'))
        held=json.loads(z.read(manifest['arrangements'][1]['file']))
        assert len(held['notes'])==1
        assert held['notes'][0]['t']==53.75
        assert held['notes'][0]['sus']==pytest.approx(.15)
        lead=json.loads(z.read(manifest['arrangements'][0]['file']))
        final=[n for c in lead['chords'] if c['t']==53.75 for n in c['notes']]
        assert len(final)==3 and all(n['sus']==.25 for n in final)
    damaged=deepcopy(alignment)
    damaged['terminalSustains']['audioDuration'] += .1
    assert verify_import(source,path,damaged,META)['status']=='failed'


@pytest.mark.parametrize('effects, start', [({'bnv':[{'t':3,'v':1}]},9), ({'slide_out_marks':[]},9), ({},10.1)])
def test_mixed_policy_cannot_shorten_gestures_or_create_late_attacks(effects,start):
    tracks=[{'events':[{'t':9,'end':10.4,'effects':{'slide_out_marks':[]}},
                       {'t':start,'end':13,'effects':effects}]}]
    assert ep.plan_bounds(tracks,10) is None


def test_mixed_policy_keeps_exact_two_second_tail():
    tracks=[{'events':[{'t':9,'end':12,'effects':{'slide_out_marks':[]}},
                       {'t':9,'end':12.01,'effects':{'mt':True}}]}]
    assert ep.plan_bounds(tracks,10)['lastNoteEnd']==12


@pytest.mark.parametrize('status, expected', [('inconclusive','could not be confirmed'), ('suspected_mismatch','appears to differ')])
def test_failure_distinguishes_uncertainty_from_mismatch(status,expected):
    failure=ImportFailure('alignment_failed','Generic matcher error',
        {'endingPaddingDeclined':{'endingPaddingSync':{'status':status}}})
    assert expected in _failure_message(failure)

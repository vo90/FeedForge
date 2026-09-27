"""Musical requirements missing from the first Hybrid Lead implementation."""
from copy import deepcopy
import json
from zipfile import ZipFile

import pytest
import yaml

from test_song_import_score import beat, measure
from test_songsterr_hybrid_lead import song, rest, prepared, build
from feedback_converter.song_import.hybrid_lead import event_rows
from feedback_converter.song_import.hybrid_context import Clock


def test_forward_link_after_rest_does_not_reserve_the_rest(tmp_path):
    _, p, _ = prepared(tmp_path)
    track = deepcopy(p['tracks'][1])
    track['chords'] = []
    track['notes'] = [dict(t=0, s=0, f=3, sus=.5),
                      dict(t=4, s=0, f=5, sus=.25, ln=True),
                      dict(t=4.25, s=0, f=7, sus=.25, ho=True)]
    context = {'beats': []}
    rows = event_rows(track, context, Clock(p['scoreTimeline']))
    assert rows[0]['end'] == 1
    assert all(row['start'] == 8 and row['end'] == 9 for row in rows[1:])


def solo_song():
    doc = song()
    doc['tracks'][1]['name'] = 'Electric Guitar | Solo'
    doc['parts'][0]['measures'] = [measure(beat(3)), measure(rest()),
                                  measure(beat(5)), measure(beat(7))]
    doc['parts'][1]['measures'] = [measure(rest()), measure(beat(12)),
                                  measure(beat(14)), measure(rest())]
    doc['parts'][2]['measures'] = [measure(rest()), measure(beat(8)),
                                  measure(beat(8)), measure(rest())]
    return doc


def test_solo_is_primary_even_when_background_fills_the_same_gap(tmp_path):
    *_, archive, report = build(tmp_path, solo_song())
    assert report['status'] == 'passed', report
    with ZipFile(archive) as z:
        m = yaml.safe_load(z.read('manifest.yaml'))
        receipt = json.loads(z.read('import/hybrid-lead.json'))
        chart = json.loads(z.read(m['arrangements'][-1]['file']))
        solo = json.loads(z.read('arrangements/1.json'))
        assert all(n in chart['notes'] for n in solo['notes'])
        assert not any(n['f'] == 8 for n in chart['notes'])
        assert any(p['trackId'] == '1' and p['priority'] == 'solo' for p in receipt['passages'])
        assert receipt['coverage']['status'] == 'complete'
        assert receipt['removedMain'], 'The solo must continue across the lower-priority main return.'


@pytest.mark.parametrize('name',['Solo Chords','Solo and Harmonies'])
def test_solo_chords_is_not_silently_classified_as_primary(tmp_path,name):
    from feedback_converter.song_import.hybrid_primary import resolve_roles
    _, p, options = prepared(tmp_path, solo_song())
    p['tracks'][1]['name'] = name
    assert resolve_roles(p, options, '0')['1'] != 'solo'


def test_source_order_does_not_change_primary_plan(tmp_path):
    from feedback_converter.song_import.hybrid_lead import plan
    _, p, options = prepared(tmp_path, solo_song())
    alignment = {'status': 'validated', 'offset': 0, 'scale': 1}
    first = plan(p, options, '0', alignment, 8)
    p['tracks'].reverse()
    assert plan(p, options, '0', alignment, 8) == first


@pytest.mark.parametrize('fault', ['silence', 'rhythm', 'main_lineage', 'coverage_time', 'coverage_reason'])
def test_independent_gate_rejects_self_consistent_but_musically_wrong_plan(tmp_path, monkeypatch, fault):
    import feedback_converter.song_import.hybrid_lead as composer
    real_plan = composer.plan
    def corrupted(performance, *args, **kwargs):
        result = real_plan(performance, *args, **kwargs)
        if fault in {'silence','rhythm'}:
            solo = next(p for p in result['passages'] if p['priority'] == 'solo')
            if fault == 'silence':
                result['passages'].remove(solo)
                result['status'] = 'no_additions'
            else:
                track = next(t for t in performance['tracks'] if t['id'] == '2')
                rows = event_rows(track,performance['compositionContext']['tracks']['2'],Clock(performance['scoreTimeline']))
                solo.update(trackId='2',priority='accompaniment',events=[{k:r[k] for k in ('kind','index','sourceIds','occurrences')} for r in rows])
        if fault == 'main_lineage': result['mainEvents'][0]['sourceIds'] = ['invented']
        if fault == 'coverage_time': result['coverage']['events'][0]['recordingStart'] += .5
        if fault == 'coverage_reason': result['coverage']['events'][0]['reason'] = 'unknown'
        return result
    monkeypatch.setattr(composer,'plan',corrupted)
    *_, report = build(tmp_path,solo_song())
    assert report['status'] == 'failed'
    codes = {e['code'] for e in report['errors']}
    assert 'hybrid_chart' not in codes, 'The forged chart deliberately remains a faithful copy of the wrong selected sources.'
    assert ('hybrid_primary_coverage' if fault in {'silence','rhythm'} else 'hybrid_event_lineage' if fault == 'main_lineage' else 'hybrid_coverage') in codes


@pytest.mark.parametrize('role', ['solo','lead'])
@pytest.mark.parametrize('duplicate', [False,True])
def test_parallel_primary_parts_choose_coherent_default_and_allow_priority(tmp_path,role,duplicate):
    from feedback_converter.song_import.hybrid_lead import plan
    from feedback_converter.song_import.audio import ImportFailure
    doc = solo_song()
    if duplicate: doc['parts'][2] = deepcopy(doc['parts'][1])
    _, p, options = prepared(tmp_path,doc,{'roles':{'1':role,'2':role}})
    first = plan(p,options,'0',{'status':'validated','offset':0,'scale':1},8)
    assert first == plan(p,options,'0',{'status':'validated','offset':0,'scale':1},8)
    out = tmp_path/'resolved';out.mkdir()
    *_, report = build(out,doc,overrides={'roles':{'1':role,'2':role},'preferredTrackIds':['1','2']})
    assert report['status'] == 'passed', report


@pytest.mark.parametrize('fault', ['capo','unsupported_fret'])
def test_unrepresentable_solo_material_keeps_a_valid_limited_result(tmp_path,fault):
    from feedback_converter.song_import.audio import ImportFailure
    doc = solo_song()
    if fault == 'capo': doc['tracks'][1]['capo'] = 2
    else: doc['parts'][1]['measures'][1] = measure(beat(30))
    *_, archive, report = build(tmp_path,doc)
    assert report['status'] == 'passed', report
    with ZipFile(archive) as z:
        receipt = json.loads(z.read('import/hybrid-lead.json'))
    assert receipt['coverage']['status'] == 'limited'
    if fault == 'capo':
        assert all(p['trackId'] != '1' for p in receipt['passages'])
    else:
        assert any(p['trackId'] == '1' for p in receipt['passages']), 'The supported later solo must remain.'


def test_ghost_only_solo_tail_returns_to_main_without_cutting_primary_body(tmp_path):
    doc = solo_song()
    doc['parts'][1]['measures'][3] = measure(beat(14,ghost=True))
    *_, archive, report = build(tmp_path,doc)
    assert report['status'] == 'passed',report
    with ZipFile(archive) as z:
        receipt = json.loads(z.read('import/hybrid-lead.json'))
    solo = next(p for p in receipt['passages'] if p['priority'] == 'solo')
    assert solo['start'] == 4 and solo['end'] == 12
    assert any(e['trackId'] == '1' and e['reason'] == 'outside_solo_body' for e in receipt['coverage']['events'])
    assert any(e['trackId'] == '0' and e['start'] == 12 and e['status'] == 'included' for e in receipt['coverage']['events'])


def test_explicit_role_or_exclusion_resolves_ambiguous_solo_chords(tmp_path):
    doc = solo_song();doc['tracks'][1]['name'] = 'Solo Chords'
    for name,overrides in [('role',{'roles':{'1':'solo'}}),('excluded',{'excludedTrackIds':['1']})]:
        out=tmp_path/name;out.mkdir()
        *_,report=build(out,doc,overrides=overrides)
        assert report['status']=='passed',report


def test_consecutive_graces_form_one_complete_group(tmp_path):
    doc=solo_song()
    doc['parts'][1]['measures'][1]=measure({**beat(7,duration=(1,32)), 'graceNote':'onBeat'},
                                        {**beat(9,duration=(1,32)), 'graceNote':'onBeat'},
                                        beat(12,duration=(1,2)),rest((1,2)))
    _,p,_=prepared(tmp_path,doc)
    rows=event_rows(p['tracks'][1],p['compositionContext']['tracks']['1'],Clock(p['scoreTimeline']))
    assert len({(r['start'],r['end']) for r in rows[:3]})==1
    target=tmp_path/'build';target.mkdir()
    *_,report=build(target,doc)
    assert report['status']=='passed',report


def test_silent_grace_offset_and_unused_muted_chord_template(tmp_path):
    doc=song()
    doc['parts'][0]['measures'][0]=measure({**rest((1,64)),'graceNote':'onBeat'},beat(3,duration=(1,4)),rest((3,4)))
    doc['parts'][1]['measures'][0]=measure({'duration':[1,4],'notes':[{'string':0,'dead':True},{'string':1,'fret':5}]},rest((3,4)))
    *_,archive,report=build(tmp_path,doc)
    assert report['status']=='passed',report
    with ZipFile(archive) as z:
        m=yaml.safe_load(z.read('manifest.yaml'))
        original=json.loads(z.read('arrangements/1.json'))
        derived=json.loads(z.read(m['arrangements'][-1]['file']))
    assert any(127 in t['frets'] for t in original['templates'])
    assert all(127 not in t['frets'] for t in derived['templates'])


def test_non_ghost_solo_pickup_precedes_the_authored_section(tmp_path):
    doc=solo_song()
    for part in doc['parts']:
        part['measures'][2]['marker']={'text':'Solo','width':40}
    *_,archive,report=build(tmp_path,doc)
    assert report['status']=='passed',report
    with ZipFile(archive) as z:
        receipt=json.loads(z.read('import/hybrid-lead.json'))
    assert receipt['passages'][0]['start']==4
    assert receipt['passages'][0]['end']==12


def test_main_review_retains_solo_suggestions_and_unknowns_require_a_choice(tmp_path):
    from feedback_converter.song_import.hybrid_lead import choose_main
    from feedback_converter.song_import.audio import ImportFailure
    _,p,options=prepared(tmp_path,solo_song())
    p['tracks'][2]['name']='Guitar 3'
    with pytest.raises(ImportFailure) as error:
        choose_main(p,{'enabled':True,'reviewSources':True},options['sourceSha256'])
    assert error.value.diagnostics['suggestedRoles']['1']=='solo'
    assert error.value.diagnostics['suggestedRoles']['2']==''


@pytest.mark.parametrize('tempo',[127,137,173,201])
def test_exact_primary_handover_survives_recording_rounding(tmp_path,tempo):
    doc=solo_song()
    doc['parts'][0]['automations']['tempo'][0]['bpm']=tempo
    for part in doc['parts']:
        part['measures'][2]['marker']='Solo'
    *_,report=build(tmp_path,doc)
    assert report['status']=='passed',report


def test_planning_budget_retains_a_valid_base(tmp_path,monkeypatch):
    import feedback_converter.song_import.hybrid_lead as composer
    from feedback_converter.song_import.audio import ImportFailure
    _,p,options=prepared(tmp_path)
    monkeypatch.setattr(composer,'MAX_CANDIDATES',0)
    result = composer.plan(p,options,'0',{'status':'validated','offset':0,'scale':1},8)
    assert result['mainEvents']
    assert result['selection']['budgetLimited']


def test_repeated_chord_boundary_ignores_source_note_array_order(tmp_path):
    doc=song()
    notes=[{'string':0,'fret':3},{'string':1,'fret':5}]
    doc['parts'][1]['measures']=[measure({'duration':[1,1],'notes':deepcopy(notes if i != 2 else list(reversed(notes)))}) for i in range(4)]
    *_,archive,report=build(tmp_path,doc)
    assert report['status']=='passed',report
    with ZipFile(archive) as z:
        receipt=json.loads(z.read('import/hybrid-lead.json'))
    assert any(p['trackId']=='1' and 'repeat' in p['boundaries'] for p in receipt['passages'])


def test_fully_unsupported_solo_remains_available_for_explicit_exclusion(tmp_path):
    from feedback_converter.song_import.audio import ImportFailure
    doc=solo_song()
    for bar in (1,2): doc['parts'][1]['measures'][bar]=measure(beat(30))
    *_, archive, report = build(tmp_path,doc)
    assert report['status'] == 'passed', report
    with ZipFile(archive) as z:
        receipt = json.loads(z.read('import/hybrid-lead.json'))
    assert receipt['coverage']['status'] == 'limited'
    assert any(x['trackId'] == '1' and x['reason'] == 'unsupported_source_gesture' for x in receipt['limitations'])
    out=tmp_path/'excluded';out.mkdir()
    *_,report=build(out,doc,overrides={'excludedTrackIds':['1']})
    assert report['status']=='passed',report

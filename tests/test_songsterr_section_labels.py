from copy import deepcopy
import json
from itertools import permutations
from zipfile import ZipFile
import pytest
import yaml

from test_song_import_verification import example, verify
from test_song_import_score import raw_score, measure, beat
from feedback_converter.song_import.songsterr import parse
from feedback_converter.song_import.timeline import render
from feedback_converter.song_import.verify_source import songsterr
from feedback_converter.song_import.model import ScoreImportError


def source(labels, programs=(68, 30, 34)):
    doc = raw_score([measure(beat(), marker=labels[0])])
    part = deepcopy(doc['parts'][0])
    meta = deepcopy(doc['tracks'][0])
    doc['parts'], doc['tracks'] = [], []
    for i, (label, program) in enumerate(zip(labels, programs)):
        m, p = deepcopy(meta), deepcopy(part)
        m.update(id=str(i), name=f'Track {i}', instrumentId=program)
        p['measures'][0]['marker'] = label
        doc['tracks'].append(m); doc['parts'].append(p)
    return doc


@pytest.mark.parametrize('vocal,playable', [('Main Riff','Interlude'), ('Bridge 2','Bridge Part II'), ('Chorus II','Bridge')])
def test_frozen_conflict_patterns_choose_playable_consensus_without_altering_source(vocal, playable):
    doc = source([{'text':vocal,'width':70}, playable, playable]); original=deepcopy(doc)
    score=parse(doc); reference=songsterr(doc)
    assert score.measures[0].section == reference.bars[0].section == playable
    assert score.source['sectionLabels'] == reference.section_labels
    assert score.source['sectionLabels'][0] == {
        'measure':1,'label':playable,'basis':'guitar_bass_consensus',
        'labels':[{'trackIndex':i,'trackId':str(i),'eligible':i>0,'text':text,
                   'location':f'parts/{i}/measures/0/marker'} for i,text in enumerate((vocal,playable,playable))]}
    assert doc == original
    assert score.source_document['document'] == original
    selected=parse(doc,track_indices={2})
    assert selected.source['sectionLabels'] == score.source['sectionLabels']
    assert selected.measures[0].section == playable
    independent_selected = songsterr(doc, track_indices={2})
    assert independent_selected.section_labels == reference.section_labels
    assert len(independent_selected.parts) == 1
    assert any('consensus' in w for w in score.warnings)


def test_metadata_order_does_not_pick_the_vocal_label():
    doc=source(['Vocal','Guitar','Guitar']); expected='Guitar'
    for order in ([0,1,2],[2,0,1],[1,2,0]):
        d={**doc,'tracks':[doc['tracks'][i] for i in order],'parts':[doc['parts'][i] for i in order]}
        assert parse(d).measures[0].section == songsterr(d).bars[0].section == expected


@pytest.mark.parametrize('labels,expected', [
    (['Verse', 'Verse 1'], 'Verse 1'), (['Verse 2', 'Verse'], 'Verse 2'),
    (['Chorus', 'Chorus 3'], 'Chorus 3'), (['Pre-Chorus', 'Pre-Chorus 2'], 'Pre-Chorus 2'),
    (['Solo', 'Solo 2'], 'Solo 2'), (['Intro', 'Intro 12'], 'Intro 12'),
    (['Verse 1', ' verse  1 '], 'Verse 1'),
    (['Solo (Adrian Smith)', 'solo (adrian smith)'], 'Solo (Adrian Smith)'),
])
def test_equivalent_labels_choose_an_authored_label_without_changing_events(labels, expected):
    doc = source(['Vocal section', *labels]); original = deepcopy(doc)
    score, reference = parse(doc), songsterr(doc)
    assert score.measures[0].section == reference.bars[0].section == expected
    assert score.source['sectionLabels'] == reference.section_labels
    assert reference.section_labels[0]['basis'] == 'guitar_bass_equivalent_labels'
    assert [entry['text'] for entry in reference.section_labels[0]['labels']] == ['Vocal section', *labels]
    assert score.source_document['document'] == doc == original
    consensus = deepcopy(doc)
    for part in consensus['parts']:
        part['measures'][0]['marker'] = expected
    assert render(score)['tracks'] == render(parse(consensus))['tracks']
    assert reference.parts == songsterr(consensus).parts
    assert parse(doc, track_indices={2}).source['sectionLabels'] == reference.section_labels
    assert songsterr(doc, track_indices={2}).section_labels == reference.section_labels
    for order in permutations(range(3)):
        reordered = {**doc, 'tracks': [doc['tracks'][i] for i in order],
                     'parts': [doc['parts'][i] for i in order]}
        assert parse(reordered).measures[0].section == songsterr(reordered).bars[0].section == expected


def test_unambiguous_number_must_be_shared_by_every_numbered_label():
    doc = source(['Verse', 'Verse 2', ' verse  2 '], (30, 30, 34))
    assert parse(doc).measures[0].section == songsterr(doc).bars[0].section == 'Verse 2'


@pytest.mark.parametrize('labels', [
    ['Verse 1', 'Verse 2'], ['Verse', 'Verse 1', 'Verse 2'], ['Verse', 'Chorus 1'],
    ['Verse', 'Verse 1a'], ['Verse', 'Verse II'], ['Verse', 'Verse 0'],
    ['Verse', 'Verse -1'], ['Verse', 'Verse 1.5'], ['Verse', 'Verse 01'],
    ['Part', 'Part 1'], ['Kurt', 'Kurt 1'], ['Solo', 'Solo (Kurt)'],
    ['Solo (Kurt)', 'Solo (Krist)'], ['Solo (Part 1)', 'Solo (Part 2)'],
    ['Bass Solo', 'Guitar Solo'], ['Chorus', 'Post-Chorus'],
])
def test_meaningful_or_unrecognized_differences_remain_located(labels):
    doc = source(labels, (30,) * len(labels))
    with pytest.raises(ScoreImportError, match='measure 1') as error:
        parse(doc)
    assert error.value.source_feature == 'arrangement.section_labels'
    assert [x['text'] for x in error.value.source_value] == labels
    with pytest.raises(ValueError, match='ambiguous'):
        songsterr(doc)


def test_equivalent_fallback_only_when_playable_tracks_have_no_label():
    doc = source(['Verse', 'Verse 2', ''], (68, 70, 30))
    assert parse(doc).measures[0].section == songsterr(doc).bars[0].section == 'Verse 2'
    assert songsterr(doc).section_labels[0]['basis'] == 'other_tracks_equivalent_labels'
    doc['parts'][2]['measures'][0]['marker'] = 'Intro'
    assert parse(doc).measures[0].section == songsterr(doc).bars[0].section == 'Intro'


def test_numbered_labels_do_not_invent_or_move_section_boundaries():
    doc = source(['Verse 1', 'Verse'], (30, 34))
    for part in doc['parts']:
        part['measures'] += [measure(beat()), measure(beat(), marker='Chorus')]
    doc['parts'][0]['measures'][2]['marker'] = 'Chorus 2'
    score, reference = parse(doc), songsterr(doc)
    assert [bar.section for bar in score.measures] == [bar.section for bar in reference.bars] == ['Verse 1', '', 'Chorus 2']
    assert [(s['time'], s['name']) for s in render(score)['sections']] == [(0.0, 'Verse 1'), (4.0, 'Chorus 2')]


@pytest.mark.parametrize('labels,expected,basis', [(['Intro','',''], 'Intro','other_tracks_consensus'),
    (['','',''], '',None), (['Same','Same','Same'],'Same','guitar_bass_consensus')])
def test_absent_duplicate_and_fallback_labels(labels,expected,basis):
    doc=source(labels); score=parse(doc); reference=songsterr(doc)
    assert score.measures[0].section == reference.bars[0].section == expected
    assert score.source['sectionLabels'] == reference.section_labels
    if basis: assert reference.section_labels[0]['basis'] == basis
    else: assert reference.section_labels == []


@pytest.mark.parametrize('labels,programs', [(['Vocal','Verse','Chorus'],(68,30,34)),
    (['Verse','Chorus',''],(68,70,30))])
def test_real_playable_or_fallback_ambiguity_stays_located(labels,programs):
    doc=source(labels,programs)
    with pytest.raises(ScoreImportError,match='measure 1') as error: parse(doc,track_indices={2})
    assert error.value.source_feature == 'arrangement.section_labels'
    with pytest.raises(ValueError,match='ambiguous'): songsterr(doc)


@pytest.mark.parametrize('invalid', [12, True, [], {'text':5}, {'text':'Intro','navigation':'jump'}])
def test_malformed_markers_are_not_stringified(invalid):
    doc=source([invalid,'Intro','Intro'])
    with pytest.raises(ValueError): parse(doc)
    with pytest.raises(ValueError): songsterr(doc)


@pytest.mark.parametrize('field,value', [('signature',[3,4]),('repeat',3),('alternateEnding',[2])])
def test_musical_navigation_conflicts_still_reject(field,value):
    doc=source(['Different','Intro','Intro'])
    for p in doc['parts']: p['measures'][0].update(repeat=2,alternateEnding=[1])
    doc['parts'][0]['measures'][0][field]=value
    with pytest.raises(ValueError): parse(doc)
    with pytest.raises(ValueError): songsterr(doc)


def test_archive_checks_selected_section_and_provenance_not_just_note_counts(tmp_path):
    doc, package=example()
    doc['tracks'].insert(0, {**doc['tracks'][0], 'id':'v','name':'Vocals','instrumentId':68})
    doc['parts'].insert(0,deepcopy(doc['parts'][0])); doc['parts'][0]['measures'][0]['marker']='Vocal section'
    # This legacy-contract fixture isolates actual section checking.
    assert verify(tmp_path,doc,package)['status']=='passed'
    package['timeline.json']['sections'][0]['name']='Vocal section'
    result=verify(tmp_path,doc,package)
    assert result['status']=='failed'
    assert any(e['location'].startswith('song_timeline/sections') for e in result['errors'])


@pytest.mark.parametrize('labels', [['Voice', 'Intro', 'Intro'], ['Voice', 'Verse 1', 'Verse']])
@pytest.mark.parametrize('tamper', ['provenance', 'chosen_label', 'basis', 'timeline'])
def test_builder_embeds_and_verifier_checks_label_decisions(tmp_path, labels, tamper):
    from test_song_import_builder import inputs
    from feedback_converter.song_import.builder import build_feedpak
    from feedback_converter.song_import.compatibility import inspect_songsterr
    from feedback_converter.song_import.verification import verify_import
    doc=source(labels,(68,30,30))
    performance=render(parse(doc)); _,audio,_,job=inputs(tmp_path)
    score_path=tmp_path/'source.json'; score_path.write_text(json.dumps(doc),encoding='utf-8')
    alignment={'status':'validated','offset':0,'scale':1}
    result=build_feedpak(performance,audio,alignment,job,output_dir=tmp_path/'out',source_path=score_path,
                         compatibility=inspect_songsterr(doc),recipe={'preservationContract':18})
    archive=result['stagingPath']
    assert verify_import(score_path,archive,alignment)['status']=='passed'
    with ZipFile(archive) as z:
        entries={name:z.read(name) for name in z.namelist()}
    manifest=yaml.safe_load(entries['manifest.yaml'])
    assert manifest['song_import']['sourceMetadata']['sectionLabels']==performance['source']['sectionLabels']
    decisions = manifest['song_import']['sourceMetadata']['sectionLabels']
    if tamper == 'provenance':
        decisions[0]['labels'].pop(0)
    elif tamper == 'chosen_label':
        decisions[0]['label'] = 'Wrong label'
    elif tamper == 'basis':
        decisions[0]['basis'] = 'unverified_guess'
    else:
        timeline_name = next(name for name in entries if name.endswith('timeline.json'))
        timeline = json.loads(entries[timeline_name])
        timeline['sections'][0]['name'] = 'Wrong label'
        entries[timeline_name] = json.dumps(timeline).encode()
    entries['manifest.yaml']=yaml.safe_dump(manifest).encode()
    corrupt=tmp_path/'changed.feedpak'
    with ZipFile(corrupt,'w') as z:
        for name,data in entries.items(): z.writestr(name,data)
    report=verify_import(score_path,corrupt,alignment)
    assert report['status']=='failed'
    assert any(e['code']=='section_provenance' if tamper != 'timeline' else
               e['location'].startswith('song_timeline/sections') for e in report['errors'])

from copy import deepcopy
import json
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
    assert any('consensus' in w for w in score.warnings)


def test_metadata_order_does_not_pick_the_vocal_label():
    doc=source(['Vocal','Guitar','Guitar']); expected='Guitar'
    for order in ([0,1,2],[2,0,1],[1,2,0]):
        d={**doc,'tracks':[doc['tracks'][i] for i in order],'parts':[doc['parts'][i] for i in order]}
        assert parse(d).measures[0].section == songsterr(d).bars[0].section == expected


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


def test_builder_embeds_and_verifier_checks_label_decisions(tmp_path):
    from test_song_import_builder import inputs
    from feedback_converter.song_import.builder import build_feedpak
    from feedback_converter.song_import.compatibility import inspect_songsterr
    from feedback_converter.song_import.verification import verify_import
    doc=source(['Voice','Intro','Intro'],(68,30,30))
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
    manifest['song_import']['sourceMetadata']['sectionLabels'][0]['labels'].pop(0)
    entries['manifest.yaml']=yaml.safe_dump(manifest).encode()
    corrupt=tmp_path/'changed.feedpak'
    with ZipFile(corrupt,'w') as z:
        for name,data in entries.items(): z.writestr(name,data)
    report=verify_import(score_path,corrupt,alignment)
    assert report['status']=='failed'
    assert any(e['code']=='section_provenance' for e in report['errors'])

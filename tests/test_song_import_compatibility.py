import json
from pathlib import Path
from zipfile import ZipFile

from feedback_converter.song_import.compatibility import inspect_songsterr
from feedback_converter.song_import.worker import run_import
from test_song_import_score import beat, measure, raw_score


def test_inventory_collects_unknowns_without_blocking_an_explicit_unpitched_mute():
    source = raw_score([measure(beat(futureOne=True), beat(futureTwo=0), beat(dead=True))])
    del source['parts'][0]['measures'][0]['voices'][0]['beats'][2]['notes'][0]['fret']
    report = inspect_songsterr(source)
    assert report['status'] == 'blocked'
    assert {r['feature'] for r in report['findings']} == {'note.futureOne', 'note.futureTwo'}
    assert all(r['measure'] == 1 for r in report['findings'])


def test_blocked_import_saves_source_and_detailed_report_before_audio(tmp_path):
    source = raw_score([measure(beat(futureOne=True), beat(futureTwo=True))])
    score = tmp_path / 'tab.json'; score.write_text(json.dumps(source))
    result = run_import({'scorePath': str(score), 'workDir': str(tmp_path / 'work'),
                         'outputDir': str(tmp_path / 'output'), 'auditDir': str(tmp_path / 'evidence')})
    assert not result['ok'] and result['code'] == 'needs_attention'
    assert result['compatibility']['findingCount'] == 2
    with ZipFile(tmp_path / 'evidence' / 'records' / (result['evidence']['id'] + '.zip')) as z:
        record = json.loads(z.read('report.json'))
        assert z.read('objects/' + record['objects']['source']) == score.read_bytes()
        report = json.loads(z.read('objects/' + record['objects']['compatibility']))
        assert len(report['findings']) == 2
    assert not (tmp_path / 'output').exists()

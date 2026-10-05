import copy
import json
from pathlib import Path

import pytest
from feedback_converter.song_import.revision_policy import valid_revision_selection

FIXTURE = json.loads((Path(__file__).parent / 'fixtures/songsterr-revision-selection.json').read_text())


@pytest.mark.parametrize('case', FIXTURE['cases'], ids=lambda case: case['name'])
def test_shared_revision_selection_contract(case):
    metadata = copy.deepcopy(FIXTURE['base'])
    metadata.update(case.get('metadata', {}))
    if 'evidence' in case:
        metadata['revisionEvidence'].update(case['evidence'])
    assert valid_revision_selection(metadata) is case['valid']


def test_evidence_report_preserves_unreviewed_receipt(tmp_path):
    from feedback_converter.song_import.evidence import save_evidence
    score = tmp_path / 'score.json'
    score.write_text('{"fixture":true}')
    metadata = copy.deepcopy(FIXTURE['base'])
    result = save_evidence(tmp_path / 'audit', score=score, metadata=metadata, performance=None,
                           synchronization=None, alignment=None, verification={'status': 'incomplete'})
    record = json.loads((tmp_path / 'audit/records' / (result['id'] + '.json')).read_text())
    assert record['sourceMetadata'] == metadata


def test_worker_cannot_bypass_revision_check_with_local_audio():
    from feedback_converter.song_import.worker import run_import
    result = run_import({'metadata': {'songId': '123', 'revisionId': '456', 'approval': 'unreviewed'},
                         'audio': {'kind': 'file', 'path': 'local.wav'}})
    assert result['ok'] is False
    assert result['code'] == 'revision_ineligible'

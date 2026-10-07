"""Auditable Songsterr measure-boundary playback adjustments."""
from copy import deepcopy
import hashlib

POLICY = 'songsterr-measure-boundaries-v1'


def report_findings(performance, report):
    from .compatibility import add_finding
    for row in performance.get('source', {}).get('measureBoundaryAdjustments', []):
        add_finding(report, feature='beat.measure_boundary', category='source_interpretation',
                    impact='gameplay_omission' if row['action'] == 'omit' else 'display_or_expression',
                    message=('This attack is outside its written bar and is skipped, as in Songsterr playback. '
                             if row['action'] == 'omit' else
                             'This overflowing beat is bounded to its written bar, as in Songsterr playback. ')
                            + 'Later bars stay in place; original notation is retained in the source.',
                    location=row['location'] + '/' + row['action'], value=deepcopy(row), trackId=row['trackId'])


def receipt(rows, source_path):
    return {'version': 1, 'policy': POLICY, 'timeDomain': 'measure_quarters',
            'sourceSha256': hashlib.sha256(source_path.read_bytes()).hexdigest(),
            'adjustments': deepcopy(rows)}
